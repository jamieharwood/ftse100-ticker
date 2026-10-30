# FTSE 100 Price Loader

Downloads daily price history for current FTSE 100 constituents and stores it in MongoDB. It can also print recent prices without writing price data.

## How it works

1. Reads the current constituent list from the [FTSE 100 Wikipedia page](https://en.wikipedia.org/wiki/FTSE_100_Index). If the scrape fails or returns too few rows, it uses the previously stored current constituents; if none are stored, the load fails.
2. Converts each EPIC to Yahoo Finance format (for example, `BT.A` becomes `BT-A.L`).
3. Downloads OHLCV history using `yfinance` and upserts constituent and price documents into MongoDB.

The constituent list is refreshed each time the script runs. Companies leaving the index remain in the database with `in_index: false`. Price writes are idempotent: a rerun updates an existing symbol/date record, including the current day's bar.

## Requirements

- Python 3
- A reachable MongoDB instance
- Packages pinned in `requirements.txt` (`pytest` is included for tests)

Create a virtual environment and install the pinned packages:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

Run with the default period of five days:

```bash
python ticker.py
```

Choose a different [yfinance period](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html):

```bash
python ticker.py --period 1mo
python ticker.py --period max
```

Print recent prices without writing price history:

```bash
python ticker.py --quote HSBA BT.A
python ticker.py --quote
```

Provide EPICs (`BT.A`) or Yahoo symbols (`BT-A.L`). With no symbols, `--quote` uses the stored current constituents. It requests the latest available one-minute price bar, which may be delayed by about 15 minutes. A MongoDB connection is still required; this mode does not update the database.

The script defaults to `mongodb://storenator.bluelarma.com:27017`. Set `MONGO_URI` to use another MongoDB instance:

```bash
MONGO_URI=mongodb://localhost:27017 python ticker.py
```

The database name is `stocks`.

Use the virtual environment's interpreter for commands if `python` is not available on your `PATH`:

```bash
.venv/bin/python ticker.py
.venv/bin/python ticker.py --quote HSBA
```

Run the offline test suite with:

```bash
.venv/bin/python -m pytest tests
```

## MongoDB collections

`constituents` stores one document per symbol, uniquely indexed by `symbol`. Current members have `in_index: true`; former members remain with `in_index: false`:

```text
symbol, epic, company, isin, sector, in_index
```

`prices` stores one document per symbol and trading date, uniquely indexed by `(symbol, date)`. Dates are stored as naive midnight timestamps in the exchange-local day, so reruns update the same daily record:

```text
symbol, date, open, high, low, close, adj_close, volume
```

Prices from the London Stock Exchange are reported in pence (GBp), not pounds. Yahoo Finance history may contain missing dates; gaps can originate from the data source.