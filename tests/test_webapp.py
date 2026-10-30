from datetime import datetime

import pytest

import webapp


class FakeCursor(list):
    def sort(self, field, direction):
        return FakeCursor(sorted(self, key=lambda d: d[field], reverse=direction < 0))


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
