# FTSE 100 Price Loader

Downloads daily price history for the current FTSE 100 constituents from Yahoo Finance (via [`yfinance`](https://pypi.org/project/yfinance/)) and stores it in MongoDB. It can also print current prices without writing to the database.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Scheduling](#scheduling)
- [Data model](#data-model)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

## Features

- Loads daily open/high/low/close/adjusted close/volume for all FTSE 100 constituents in one batch download.
- Backfills full history (back to 1968 for the oldest constituents) with `--period max`.
- Idempotent: re-running never creates duplicate rows; existing days are updated in place.
- Keeps the constituent list current from Wikipedia and flags companies that leave the index.
- Keeps working if Wikipedia is unavailable by falling back to the last stored constituent list.
- Prints current (≈15 minute delayed) prices with `--quote`.
- Timestamped logging and a non-zero exit code on failure, suitable for cron.

## How it works

1. Reads the current constituent list from the [FTSE 100 Wikipedia page](https://en.wikipedia.org/wiki/FTSE_100_Index). If the scrape fails or returns too few rows (fewer than 90), it uses the previously stored current constituents; if none are stored, the load fails.
2. Converts each EPIC to Yahoo Finance format (for example, `BT.A` becomes `BT-A.L`).
3. Downloads OHLCV history using `yfinance` and upserts constituent and price documents into MongoDB.

The constituent list is refreshed on every successful scrape. Companies leaving the index remain in the database with `in_index: false`. Price writes are idempotent: a rerun updates an existing symbol/date record, including the current day's bar.

## Requirements

- Python 3 (developed on 3.12)
- A reachable MongoDB instance (developed against MongoDB 8)
- Internet access to Wikipedia and Yahoo Finance
- Packages pinned in `requirements.txt`: `yfinance`, `pymongo`, `pandas`, `lxml`, `requests`, and `pytest` for tests

## Installation

```bash
git clone https://github.com/jamieharwood/ftse100-ticker.git
cd ftse100-ticker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The MongoDB database (`stocks`) and its collections and indexes are created automatically on the first run.

## Configuration

| Setting | Default | How to change |
|---|---|---|
| MongoDB connection | `mongodb://storenator.bluelarma.com:27017` | `MONGO_URI` environment variable |
| Database name | `stocks` | `DB_NAME` constant in `ticker.py` |
| History period | `5d` | `--period` option |

```bash
MONGO_URI=mongodb://localhost:27017 .venv/bin/python ticker.py
```

`MONGO_URI` accepts any standard [MongoDB connection string](https://www.mongodb.com/docs/manual/reference/connection-string/), including credentials if your server requires them.

## Usage

Load the last five days (the default):

```bash
.venv/bin/python ticker.py
```

```text
2026-10-30 08:39:27,960 INFO 100 symbols, 500 rows: 100 inserted, 100 updated
```

Choose a different [yfinance period](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html), for example to backfill full history:

```bash
.venv/bin/python ticker.py --period 1mo
.venv/bin/python ticker.py --period max
```

Print current prices without writing price history:

```bash
.venv/bin/python ticker.py --quote HSBA BT.A
.venv/bin/python ticker.py --quote
```

```text
HSBA.L      1283.40p  2026-10-30 08:24
BT-A.L       224.40p  2026-10-30 08:17
```

Provide EPICs (`BT.A`) or Yahoo symbols (`BT-A.L`), in any case. With no symbols, `--quote` uses the stored current constituents, so a MongoDB connection is still required. It shows the latest available one-minute price bar and its time; this is typically delayed by about 15 minutes, and outside trading hours it is the last close.

Run `.venv/bin/python ticker.py --help` for all options.

## Scheduling

To load prices every weekday after the London Stock Exchange closes (16:30 UK time), add a crontab entry with `crontab -e`:

```cron
30 17 * * 1-5 cd /path/to/ftse100-ticker && .venv/bin/python ticker.py >> ticker.log 2>&1
```

The time is in the machine's timezone, so this assumes `Europe/London`. Cron only runs while the machine is on; the default five-day window fills in up to four missed trading days, and anything older needs a manual `--period 1mo` run.

Each run logs timestamped lines. Search the log for `WARNING` (constituent fallback, symbols with no data) or `ERROR` (failed run):

```bash
grep -E 'WARNING|ERROR' ticker.log
```

## Data model

`constituents` stores one document per symbol, uniquely indexed by `symbol`. Current members have `in_index: true`; former members remain with `in_index: false`:

```text
symbol, epic, company, isin, sector, in_index
```

`prices` stores one document per symbol and trading date, uniquely indexed by `(symbol, date)`. Dates are stored as naive midnight timestamps in the exchange-local day, so reruns update the same daily record:

```text
symbol, date, open, high, low, close, adj_close, volume
```

The full schemas, as MongoDB `$jsonSchema` validators plus index definitions, are in [`schema/`](schema). The live collections don't enforce them. To turn on validation for a collection, apply its validator in `mongosh`:

```js
use stocks
db.runCommand({ collMod: "prices", validator: <the "validator" object from schema/prices.json>, validationLevel: "strict" })
```

Example query in `mongosh`:

```js
use stocks
db.prices.find({ symbol: "HSBA.L" }).sort({ date: -1 }).limit(5)
```

## Testing

The tests run offline and need no MongoDB or internet access:

```bash
.venv/bin/python -m pytest tests                # all tests
.venv/bin/python -m pytest tests -k price_docs  # tests matching a name
```

They cover symbol conversion, date normalisation (which the duplicate protection depends on), skipping days without prices, and the constituent fallback.

## Project structure

```text
ticker.py            # loader and --quote command
tests/test_ticker.py # offline unit tests
requirements.txt     # pinned dependencies
schema/              # MongoDB $jsonSchema and indexes for each collection
CLAUDE.md            # notes for Claude Code
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR Failed` with a `ServerSelectionTimeoutError` | MongoDB isn't reachable. Check the host is up and `MONGO_URI` is correct. |
| `WARNING Constituent scrape failed ...` | Wikipedia was unreachable or its table layout changed. The load still ran using the stored list. If it persists, update `get_ftse100_constituents()`. |
| `WARNING No data for: ...` | Yahoo returned nothing for those symbols, usually a temporary Yahoo problem or a symbol format Yahoo doesn't recognise. |
| `ERROR ... No price data returned for any of 100 symbols` | Yahoo is unreachable or rate-limiting. Try again later. |
| A date is missing for every stock | Yahoo hasn't published that day yet. A later run with the default period fills it in. |
| `ModuleNotFoundError` | Run with `.venv/bin/python`, or install the requirements into the virtual environment. |

## Limitations

- Prices from the London Stock Exchange are in pence (GBp), not pounds.
- Yahoo Finance data is unofficial, delayed, and may contain gaps; `yfinance` is not affiliated with Yahoo.
- History covers today's constituents only. Companies that left the index before they were first recorded aren't included, so historical index membership can't be reconstructed from this data.
- `yfinance` changes often; versions are pinned, so upgrade deliberately and re-run the tests.

## Contributing

1. Create a branch from `main`.
2. Make your change and add or update tests in `tests/`.
3. Run `.venv/bin/python -m pytest tests`.
4. Open a pull request.

## License

No license has been chosen yet. Until one is added, all rights are reserved.
