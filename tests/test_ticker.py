import math

import pandas as pd
import pytest

import ticker

COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def history(index, close=1.0):
    return pd.DataFrame({c: [close] * len(index) for c in COLUMNS}, index=pd.DatetimeIndex(index))


@pytest.mark.parametrize("epic, expected", [("HSBA", "HSBA.L"), ("BT.A", "BT-A.L"), ("bt.a", "BT-A.L")])
def test_to_yahoo_symbol(epic, expected):
    assert ticker.to_yahoo_symbol(epic) == expected


@pytest.mark.parametrize("ts", [
    pd.Timestamp("2026-07-01"),
    pd.Timestamp("2026-07-01 14:37"),
    pd.Timestamp("2026-07-01", tz="Europe/London"),  # BST midnight is 23:00 UTC the day before
    pd.Timestamp("2026-07-01 16:35", tz="Europe/London"),
])
def test_price_docs_normalises_date_to_naive_midnight(ts):
    # Same trading day must always produce the same key, or the unique index won't dedupe
    (doc,) = ticker.price_docs("X.L", history([ts]))
    assert doc["date"] == pd.Timestamp("2026-07-01").to_pydatetime()
    assert doc["date"].tzinfo is None


def test_price_docs_skips_rows_without_close():
    docs = list(ticker.price_docs("X.L", history(["2026-07-01", "2026-07-02"], close=math.nan)))
    assert docs == []


class FakeConstituents:
    def __init__(self, symbols):
        self.symbols = symbols

    def distinct(self, field, query):
        assert (field, query) == ("symbol", {"in_index": True})
        return self.symbols


class FakeDB:
    def __init__(self, symbols):
        self.constituents = FakeConstituents(symbols)


def failing_scrape():
    raise RuntimeError("table not found")


def test_sync_constituents_falls_back_to_stored_list(monkeypatch):
    monkeypatch.setattr(ticker, "get_ftse100_constituents", failing_scrape)
    assert ticker.sync_constituents(FakeDB(["BP.L", "HSBA.L"])) == ["BP.L", "HSBA.L"]


def test_sync_constituents_fails_when_nothing_stored(monkeypatch):
    monkeypatch.setattr(ticker, "get_ftse100_constituents", failing_scrape)
    with pytest.raises(RuntimeError, match="none are stored"):
        ticker.sync_constituents(FakeDB([]))
