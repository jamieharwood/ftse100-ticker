import threading
import time
from datetime import datetime

import pytest

import webapp


class FakeCursor(list):
    def sort(self, field, direction):
        return FakeCursor(sorted(self, key=lambda d: d[field], reverse=direction < 0))

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find(self, query, projection):
        self.queries.append(query)
        return FakeCursor(self.docs)


class FakeDB:
    def __init__(self, constituents=(), prices=()):
        self.constituents = FakeCollection(list(constituents))
        self.prices = FakeCollection(list(prices))


def bar(day, close):
    return {"date": datetime(2026, 10, day), "open": 1.0, "high": 2.0, "low": 0.5, "close": close, "volume": 10}


def client(db):
    return webapp.create_app(db).test_client()


def test_stocks_lists_current_members_sorted_by_company():
    db = FakeDB(constituents=[{"symbol": "BP.L", "company": "BP"}, {"symbol": "AAL.L", "company": "Anglo American"}])
    resp = client(db).get("/api/stocks")
    assert [s["symbol"] for s in resp.get_json()] == ["AAL.L", "BP.L"]
    assert db.constituents.queries == [{"in_index": True}]


def test_prices_returns_chart_bars_in_date_order():
    db = FakeDB(prices=[bar(30, 3.0), bar(28, 1.5)])
    resp = client(db).get("/api/prices/HSBA.L")
    assert resp.get_json() == [
        {"time": "2026-10-28", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"time": "2026-10-30", "open": 1.0, "high": 2.0, "low": 0.5, "close": 3.0, "volume": 10},
    ]
    assert db.prices.queries == [{"symbol": "HSBA.L"}]


def test_prices_start_filters_by_date():
    db = FakeDB()
    client(db).get("/api/prices/BT-A.L?start=2026-01-15")
    assert db.prices.queries == [{"symbol": "BT-A.L", "date": {"$gte": datetime(2026, 1, 15)}}]


@pytest.mark.parametrize("url", ["/api/prices/hsba", "/api/prices/HSBA.L?start=15-01-2026"])
def test_prices_rejects_bad_input(url):
    db = FakeDB()
    assert client(db).get(url).status_code == 400
    assert db.prices.queries == []


def test_simulate_runs_each_symbol_with_its_balance():
    db = FakeDB(prices=[bar(d, c) for d, c in [(26, 100.0), (27, 100.0), (28, 90.0), (29, 99.0)]])
    resp = client(db).post("/api/simulate", json={
        "balances": {"HSBA.L": 1000, "BP.L": 500},
        "params": {"sma_days": 3, "dip_pct": 5, "plan": "tiered", "trade_size": None},
    })
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["params"]["plan"] == "tiered" and body["params"]["trade_size"] is None
    assert body["results"]["HSBA.L"]["start_balance"] == 1000
    assert body["results"]["BP.L"]["start_balance"] == 500
    assert [t["side"] for t in body["results"]["HSBA.L"]["trades"]] == ["buy", "sell"]


@pytest.mark.parametrize("body", [
    {},
    {"balances": {"hsba": 1000}},
    {"balances": {"HSBA.L": 0}},
    {"balances": {"HSBA.L": "1000"}},
    {"balances": {"HSBA.L": 1000}, "params": {"dip_pct": -1}},
    {"balances": {"HSBA.L": 1000}, "params": {"plan": "free"}},
    {"balances": {"HSBA.L": 1000}, "start": "30/10/2026"},
])
def test_simulate_rejects_bad_input(body):
    assert client(FakeDB()).post("/api/simulate", json=body).status_code == 400


def wait_for_refresh(api):
    for _ in range(200):
        status = api.get("/api/refresh-history").get_json()
        if status["status"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("refresh still running")


def test_refresh_history_loads_max_period_once_while_running(monkeypatch):
    release = threading.Event()
    calls = []

    def fake_load(db, period):
        calls.append(period)
        release.wait(5)
        return {"symbols": 100, "rows": 5, "inserted": 1, "updated": 2, "missing": []}

    monkeypatch.setattr(webapp, "load_prices", fake_load)
    api = client(FakeDB())
    assert api.get("/api/refresh-history").get_json() == {"status": "idle"}
    assert api.post("/api/refresh-history").status_code == 202
    assert api.post("/api/refresh-history").get_json() == {"status": "running"}
    release.set()

    status = wait_for_refresh(api)
    assert status["status"] == "done" and status["rows"] == 5
    assert calls == ["max"]


def test_refresh_history_reports_failure_and_can_retry(monkeypatch):
    def fake_load(db, period):
        raise RuntimeError("Yahoo is down")

    monkeypatch.setattr(webapp, "load_prices", fake_load)
    api = client(FakeDB())
    api.post("/api/refresh-history")
    assert wait_for_refresh(api) == {"status": "failed", "error": "Yahoo is down"}

    monkeypatch.setattr(webapp, "load_prices", lambda db, period: {"rows": 1})
    api.post("/api/refresh-history")
    assert wait_for_refresh(api) == {"status": "done", "rows": 1}
