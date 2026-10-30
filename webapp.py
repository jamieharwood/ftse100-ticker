import argparse
import re
from datetime import datetime

from flask import Flask, abort, jsonify, request
from pymongo import MongoClient

from ticker import DB_NAME, MONGO_URI

SYMBOL_RE = re.compile(r"^[A-Z0-9-]+\.L$")


def create_app(db):
    app = Flask(__name__, static_folder="static", static_url_path="")

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
        query = {"symbol": symbol}
        if start := request.args.get("start"):
            try:
                query["date"] = {"$gte": datetime.strptime(start, "%Y-%m-%d")}
            except ValueError:
                abort(400, "start must be YYYY-MM-DD")
        # Served by the (symbol, date) unique index
        docs = db.prices.find(
            query, {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        ).sort("date", 1)
        # Lightweight Charts expects daily bars keyed by "time" as YYYY-MM-DD
        return jsonify([
            {"time": d["date"].strftime("%Y-%m-%d"), **{k: d[k] for k in ("open", "high", "low", "close", "volume")}}
            for d in docs
        ])

    return app


def main():
    parser = argparse.ArgumentParser(description="Candlestick chart UI for FTSE 100 prices")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other machines on the LAN")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = create_app(MongoClient(MONGO_URI)[DB_NAME])
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
