# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`ticker.py` loads daily prices for the FTSE 100 into MongoDB:

1. `sync_constituents()` scrapes the current constituent list from the Wikipedia "FTSE 100 Index" page (the table with `Company`/`Ticker` columns) and flags members with `in_index`. If the scrape fails, or returns fewer than `MIN_CONSTITUENTS` rows, it logs a warning and falls back to the stored `in_index: true` symbols, so a Wikipedia change never blocks the daily load.
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

There is no linter config. Git remote: `github.com/jamieharwood/ftse100-ticker` (private), branch `main`.

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
