import argparse
import logging
import re
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, request
from pymongo import MongoClient

import simulator
from ticker import DB_NAME, MONGO_URI, load_prices

log = logging.getLogger("webapp")

SYMBOL_RE = re.compile(r"^[A-Z0-9-]+\.L$")
BAR_FIELDS = ("open", "high", "low", "close", "volume")
PROJECTION = {"_id": 0, "date": 1, **{f: 1 for f in BAR_FIELDS}}
# (min, max) for numeric strategy parameters accepted from the UI
PARAM_LIMITS = {"sma_days": (2, 250), "dip_pct": (0, 50), "target_pct": (0, 100), "stop_pct": (0, 100),
                "trade_size": (1, 10_000_000)}
MAX_BALANCE = 10_000_000


def to_bar(doc):
    # Lightweight Charts expects daily bars keyed by "time" as YYYY-MM-DD
    return {"time": doc["date"].strftime("%Y-%m-%d"), **{k: doc[k] for k in BAR_FIELDS}}


def parse_start(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d") if value else None
    except ValueError:
        abort(400, "start must be YYYY-MM-DD")


def parse_params(raw):
    params = {}
    for name, (low, high) in PARAM_LIMITS.items():
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            abort(400, f"{name} must be a number from {low} to {high}")
        params[name] = int(value) if name == "sma_days" else float(value)
    if "plan" in raw:
        if raw["plan"] not in simulator.COMMISSION_PLANS:
            abort(400, "plan must be one of " + ", ".join(simulator.COMMISSION_PLANS))
        params["plan"] = raw["plan"]
    if "stamp_duty" in raw:
        params["stamp_duty"] = bool(raw["stamp_duty"])
    return params


def create_app(db):
    app = Flask(__name__, static_folder="static", static_url_path="")
    refresh = {"status": "idle"}
    refresh_lock = threading.Lock()

    def run_refresh():
        nonlocal refresh
        try:
            outcome = {"status": "done", **load_prices(db, "max")}
        except Exception as exc:
            log.exception("History refresh failed")
            outcome = {"status": "failed", "error": str(exc)}
        with refresh_lock:
            refresh = outcome

    @app.get("/api/refresh-history")
    def refresh_status():
        with refresh_lock:
            return jsonify(refresh)

    @app.post("/api/refresh-history")
    def start_refresh():
        # A full download takes minutes, so it runs in the background and the page polls for
        # the outcome. A second request while one is running just joins it.
        nonlocal refresh
        with refresh_lock:
            if refresh["status"] != "running":
                refresh = {"status": "running"}
                threading.Thread(target=run_refresh, daemon=True).start()
            return jsonify(refresh), 202

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/stocks")
    def stocks():
        docs = db.constituents.find(
            {"in_index": True}, {"_id": 0, "symbol": 1, "company": 1, "sector": 1}
        ).sort("company", 1)
        return jsonify(list(docs))

    @app.get("/api/prices/<symbol>")
    def prices(symbol):
        if not SYMBOL_RE.match(symbol):
            abort(400, "Invalid symbol")
        return jsonify(load_bars(symbol, parse_start(request.args.get("start"))))

    @app.post("/api/simulate")
    def simulate():
        body = request.get_json(silent=True) or {}
        balances = body.get("balances")
        if not isinstance(balances, dict) or not balances:
            abort(400, "balances must map symbols to starting balances")
        for symbol, balance in balances.items():
            if not SYMBOL_RE.match(symbol):
                abort(400, f"Invalid symbol {symbol}")
            if not isinstance(balance, (int, float)) or isinstance(balance, bool) or not 0 < balance <= MAX_BALANCE:
                abort(400, f"Balance for {symbol} must be between 0 and {MAX_BALANCE}")
        start = parse_start(body.get("start"))
        params = {**simulator.DEFAULT_PARAMS, **parse_params(body.get("params") or {})}

        results = {}
        for symbol, balance in balances.items():
            warmup = []
            if start:
                # Bars just before the range seed the moving average
                docs = db.prices.find({"symbol": symbol, "date": {"$lt": start}}, PROJECTION)
                warmup = [to_bar(d) for d in docs.sort("date", -1).limit(params["sma_days"])][::-1]
            results[symbol] = simulator.simulate(load_bars(symbol, start), balance, params, warmup)
        return jsonify({"params": params, "results": results})

    def load_bars(symbol, start):
        query = {"symbol": symbol}
        if start:
            query["date"] = {"$gte": start}
        # Served by the (symbol, date) unique index
        return [to_bar(d) for d in db.prices.find(query, PROJECTION).sort("date", 1)]

    return app


def main():
    parser = argparse.ArgumentParser(description="Candlestick chart UI for FTSE 100 prices")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other machines on the LAN")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app(MongoClient(MONGO_URI)[DB_NAME])
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
