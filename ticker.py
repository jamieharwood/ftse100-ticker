import argparse
import io
import logging
import os
import sys
import warnings

import pandas as pd
import requests
import yfinance as yf
from pymongo import ASCENDING, MongoClient, UpdateOne

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://storenator.bluelarma.com:27017")
DB_NAME = "stocks"
# yfinance triggers pandas inplace deprecation warnings on every download
warnings.filterwarnings("ignore", message="The inplace keyword")

FTSE_100_URL = "https://en.wikipedia.org/wiki/FTSE_100_Index"
# A scrape returning far fewer rows means the page changed; don't let it mark the rest as delisted
MIN_CONSTITUENTS = 90

log = logging.getLogger("ticker")


def to_yahoo_symbol(epic):
    # Yahoo uses "-" for share classes (BT.A -> BT-A.L) and ".L" for LSE
    return epic.upper().replace(".", "-") + ".L"


def get_ftse100_constituents():
    # Wikipedia rejects requests without a User-Agent
    resp = requests.get(FTSE_100_URL, headers={"User-Agent": "ftse-ticker/1.0"}, timeout=15)
    resp.raise_for_status()
    for table in pd.read_html(io.StringIO(resp.text)):
        if "Ticker" in table.columns and "Company" in table.columns:
            break
    else:
        raise RuntimeError("FTSE 100 constituents table not found")

    isin_col = next(c for c in table.columns if c.startswith("ISIN"))
    sector_col = next(c for c in table.columns if "sector" in c.lower())
    constituents = [
        {
            "symbol": to_yahoo_symbol(row["Ticker"]),
            "epic": row["Ticker"],
            "company": row["Company"],
            "isin": row[isin_col],
            "sector": row[sector_col],
        }
        for _, row in table.iterrows()
    ]
    if len(constituents) < MIN_CONSTITUENTS:
        raise RuntimeError(f"Only {len(constituents)} constituents parsed, expected ~100")
    return constituents


def stored_index_symbols(db):
    return sorted(db.constituents.distinct("symbol", {"in_index": True}))


def sync_constituents(db):
    """Refresh constituents from Wikipedia and return current index symbols.

    Falls back to the last stored list if the scrape fails, so the daily load
    doesn't depend on Wikipedia being up or unchanged.
    """
    try:
        constituents = get_ftse100_constituents()
    except Exception as exc:
        symbols = stored_index_symbols(db)
        if not symbols:
            raise RuntimeError("Constituent scrape failed and none are stored") from exc
        log.warning("Constituent scrape failed (%s); using %d stored symbols", exc, len(symbols))
        return symbols

    symbols = [c["symbol"] for c in constituents]
    db.constituents.bulk_write(
        [UpdateOne({"symbol": c["symbol"]}, {"$set": {**c, "in_index": True}}, upsert=True)
         for c in constituents]
    )
    left = db.constituents.update_many(
        {"symbol": {"$nin": symbols}, "in_index": {"$ne": False}}, {"$set": {"in_index": False}}
    )
    if left.modified_count:
        log.info("%d symbols left the index", left.modified_count)
    return symbols


def price_docs(symbol, history):
    # Skip incomplete bars: NaN would be stored as-is and break JSON consumers like webapp.py
    for date, row in history.dropna(subset=["Open", "High", "Low", "Close"]).iterrows():
        # The (symbol, date) unique index only dedupes if every bar for a day has the
        # same key, so drop any timezone (keeping exchange-local wall time) and time of day
        if date.tzinfo is not None:
            date = date.tz_localize(None)
        yield {
            "symbol": symbol,
            "date": date.normalize().to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "adj_close": float(row["Adj Close"]),
            "volume": int(row["Volume"]),
        }


def get_current_prices(symbols):
    """Latest traded price (pence, ~15 min delayed) and its timestamp for each Yahoo symbol."""
    data = yf.download(symbols, period="1d", interval="1m", group_by="ticker", progress=False)
    prices = {}
    for symbol in symbols:
        if symbol not in data.columns.get_level_values(0):
            continue
        close = data[symbol]["Close"].dropna()
        if not close.empty:
            prices[symbol] = (float(close.iloc[-1]), close.index[-1])
    return prices


def print_quotes(db, symbols):
    if symbols:
        symbols = [s.upper() if s.upper().endswith(".L") else to_yahoo_symbol(s) for s in symbols]
    else:
        symbols = stored_index_symbols(db)
    prices = get_current_prices(symbols)
    for symbol in symbols:
        if symbol in prices:
            price, at = prices[symbol]
            print(f"{symbol:<8} {price:>10.2f}p  {at:%Y-%m-%d %H:%M}")
        else:
            print(f"{symbol:<8} {'no data':>11}")


def load_prices(db, period):
    db.prices.create_index([("symbol", ASCENDING), ("date", ASCENDING)], unique=True)
    db.constituents.create_index("symbol", unique=True)

    symbols = sync_constituents(db)
    data = yf.download(symbols, period=period, group_by="ticker", auto_adjust=False, progress=False)

    ops, missing = [], []
    for symbol in symbols:
        if symbol not in data.columns.get_level_values(0):
            missing.append(symbol)
            continue
        docs = list(price_docs(symbol, data[symbol]))
        if not docs:
            missing.append(symbol)
        ops += [UpdateOne({"symbol": d["symbol"], "date": d["date"]}, {"$set": d}, upsert=True) for d in docs]

    if not ops:
        raise RuntimeError(f"No price data returned for any of {len(symbols)} symbols")
    result = db.prices.bulk_write(ops, ordered=False)
    log.info("%d symbols, %d rows: %d inserted, %d updated", len(symbols) - len(missing), len(ops),
             result.upserted_count, result.modified_count)
    if missing:
        log.warning("No data for: %s", ", ".join(missing))
    return {"symbols": len(symbols) - len(missing), "rows": len(ops), "inserted": result.upserted_count,
            "updated": result.modified_count, "missing": missing}


def main():
    parser = argparse.ArgumentParser(description="Store FTSE 100 daily prices in MongoDB")
    parser.add_argument("--period", default="5d", help="yfinance period, e.g. 5d, 1mo, 1y, max")
    parser.add_argument("--quote", nargs="*", metavar="SYMBOL",
                        help="print current prices instead of loading (e.g. HSBA BT.A); no symbols = all FTSE 100")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = MongoClient(MONGO_URI)[DB_NAME]
    try:
        if args.quote is not None:
            print_quotes(db, args.quote)
        else:
            load_prices(db, args.period)
    except Exception:
        log.exception("Failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
