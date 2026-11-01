# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`ticker.py` loads daily prices for the FTSE 100 into MongoDB:

1. `sync_constituents()` scrapes the current constituent list from the Wikipedia "FTSE 100 Index" page (the table with `Company`/`Ticker` columns) and flags members with `in_index`. If the scrape fails, or returns fewer than `MIN_CONSTITUENTS` rows, it logs a warning and falls back to the stored `in_index: true` symbols, so a Wikipedia change doesn't block the daily load. The load fails only if no symbols are stored either.
2. Batch-downloads OHLCV history for all symbols with `yf.download(..., group_by="ticker", auto_adjust=False)`.
3. Upserts into the `stocks` database on `storenator.bluelarma.com:27017` (LAN host, no auth).

## Running

Dependencies are pinned in `requirements.txt` and installed in a project venv at `.venv` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`). yfinance changes its output format often, so bump versions deliberately and re-run the tests.

```bash
.venv/bin/python ticker.py               # last 5 days
.venv/bin/python ticker.py --period max  # backfill full history
MONGO_URI=mongodb://other:27017 .venv/bin/python ticker.py
.venv/bin/python ticker.py --quote HSBA BT.A  # print current prices (no DB write)
.venv/bin/python ticker.py --quote            # current prices for all stored constituents
```

`--quote` uses `get_current_prices()` (last 1-minute bar from a batch `yf.download`) and accepts EPICs or Yahoo symbols. With no arguments, `ticker.py` runs `load_prices()`; the cron job depends on that default.

Loader output goes through `logging` with timestamps. Any exception is logged and the process exits with code 1, and getting no price rows at all counts as a failure.

```bash
.venv/bin/python -m pytest tests                          # all tests (offline, no DB needed)
.venv/bin/python -m pytest tests -k price_docs            # single test
```

Use `python -m pytest`: there is no `conftest.py` or pytest config, so bare `pytest` can't import the top-level modules and fails at collection.

Tests use hand-written fakes defined separately in each test file, not mongomock (`FakeDB`/`FakeCollection`/`FakeCursor` in `test_webapp.py`, `FakeDB`/`FakeConstituents` in `test_ticker.py`), so a new query method in `webapp.py` or `ticker.py` needs a matching fake method. There are no frontend tests; check UI changes by running `webapp.py`.

`README.md` is the user-facing documentation (UI behaviour, strategy rules, troubleshooting), so user-visible changes usually need a matching update there.

There is no linter config. Git remote: `github.com/jamieharwood/ftse100-ticker` (private), branch `main`.

## Web UI

`webapp.py` is a Flask app built by `create_app(db)` (tests inject a fake db). It imports `MONGO_URI`/`DB_NAME` from `ticker.py`. It serves `static/index.html`, a single page with no build step that loads Lightweight Charts 4.2.0 from unpkg (v4 API: `addCandlestickSeries`; v5 renamed it). The page fetches `/api/stocks` and `/api/prices/<symbol>?start=`. Bars must be `{time: "YYYY-MM-DD", open, high, low, close}` in ascending date order, and must not contain NaN, which is why `price_docs` drops incomplete bars. Each selected stock gets its own chart, and the charts keep their visible time range in sync. The main area has two tabs, Stock graphs and Trading simulator, chosen with `?view=charts|sim`. Charts aren't built while their tab is hidden, because a zero-width chart fits its data wrongly. `loadChart` stores the fetched bars in `entry.pendingBars`, and `setView` builds them when the tab is shown. The URL holds the page state (`?symbols=&range=&view=`, written by `saveUrl`), so a link reproduces the view. Per-stock starting balances live only in `state.balances` (not in the URL) and are edited in the simulator tab's Starting balances list. Fetched bars are cached per `symbol|range`. When the colour scheme changes, the page re-applies chart options and simulator markers, so new colours need to come from `chartOptions`/`seriesColors` (CSS variables), not be hard-coded.

```bash
.venv/bin/python webapp.py [--host 0.0.0.0] [--port 5000]
```

`Dockerfile` and `compose.yaml` build the public image `jamieharwood/ftse100-ticker` (`latest` and a version tag set in `compose.yaml`), which runs the web UI by default. It's built with `docker compose build`, and `.dockerignore` is an allowlist, so a new top-level file must be added there and to the `COPY` lines. Anyone can pull this image, so don't add secrets to it. The cron loader still runs from the host venv, not the image.

The server runs without a reloader, so restart it after changing Python code; a stale process shows up as "Not Found" on new endpoints. Only `load_prices()` creates the indexes. It's also the only way `webapp.py` writes to MongoDB: the header's Refresh full history button calls `POST /api/refresh-history`, which runs `load_prices(db, "max")` in a background thread while the page polls `GET` on the same route. That status is held in memory, so it assumes a single server process and resets on restart.

## Trading simulator

`simulator.py` is pure Python with no I/O. `simulate(bars, balance, params, warmup)` backtests one stock: bars are dicts with `time` and `close` in pence, and all money is in pounds. The strategy buys whole shares when the close is `dip_pct` below the `sma_days` average. It sells when net sale proceeds beat the position's full cost (value + commission + stamp duty) by `target_pct`, or on `stop_pct`. Commission rates (IBKR UK GBP fixed/tiered) and 0.5% stamp duty on buys are module constants. `POST /api/simulate` in `webapp.py` validates input against `PARAM_LIMITS`, loads `sma_days` warmup bars before `start`, and runs each symbol. In each trade dict, `price` is pence (the raw close) and every other money field is pounds, rounded to whole pence so per-trade P&L adds up exactly to the total (a test checks this). Open positions are valued at net sale proceeds on the last bar, and `buy_and_hold` uses the same fees as a benchmark. The UI shows the results as series markers (`setMarkers`, v4 API), per-card tables, and a sortable summary with pinned total rows. Clicking a row opens a `<dialog>` that lists the stock's trades paired into round trips. Results are cleared when the date range changes.

Modelling details that are easy to miss:

- Each stock trades independently from its own balance, holding at most one position, with no capital shared across stocks.
- Trades fill at the close, and a stock can't buy again on the day it sells.
- With no `start` (the Max range) there are no warmup bars, so trading can't begin until `sma_days` bars have passed.
- API bars omit `adj_close`, so charts and backtests use `close`, and dividends aren't modelled.

A new strategy parameter has to be added in three places: `DEFAULT_PARAMS` in `simulator.py`, `PARAM_LIMITS` in `webapp.py` (or a dedicated check in `parse_params`, as for `plan` and `stamp_duty`), and the request built in `static/index.html`.

## Data model (`stocks` database)

- `constituents`: one doc per symbol (`symbol`, `epic`, `company`, `isin`, `sector`, `in_index`), unique on `symbol`. Companies that leave the index keep their doc with `in_index: false`. `--quote` with no symbols and the scrape fallback both use only `in_index: true`.
- `prices`: one doc per symbol per trading day (`symbol`, `date`, `open`, `high`, `low`, `close`, `adj_close`, `volume`), unique on `(symbol, date)`. Writes are upserts, so re-running is idempotent; today's bar is overwritten as it changes intraday. `date` is always stored as naive midnight (exchange-local day); `price_docs` normalises it, because a tz-aware or intraday timestamp would bypass the unique index and create a same-day duplicate.

`schema/<collection>.json` holds a `$jsonSchema` validator and the index definitions for each collection. They document the live data but aren't applied to MongoDB. If you change the fields `ticker.py` writes, update the matching schema file, then check it against the live data with `db.<coll>.count_documents({"$nor": [{"$jsonSchema": schema}]}) == 0`.

## Notes

- Symbol mapping: the Wikipedia EPIC gets `.` replaced with `-` and `.L` appended (`BT.A` → `BT-A.L`). Yahoo needs this format.
- LSE prices are in pence (GBp), not pounds.
- yfinance emits pandas `inplace` deprecation warnings internally; `ticker.py` filters them.
- Yahoo daily history can have gaps (missing days across all symbols). This is a data-source issue, not a script bug.

## Scheduling

User crontab (`crontab -l`) runs `ticker.py` at 17:30 Mon–Fri (system TZ is Europe/London; LSE closes 16:30), appending output to `ticker.log` in the project directory. Grep it for `WARNING`/`ERROR` to find fallbacks or failed runs.
