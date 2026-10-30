"""Backtest a commission-aware dip-buying strategy over daily bars.

Prices in bars are pence (as stored by ticker.py); all money here is pounds.
"""
import math
from collections import deque

# Interactive Brokers UK (GBP) stock commissions, IB SmartRouting
COMMISSION_PLANS = {
    "fixed": {"rate": 0.0005, "minimum": 3.00},
    # Tiered also passes through exchange/clearing fees, which aren't modelled
    "tiered": {"rate": 0.0005, "minimum": 1.00},
}
STAMP_DUTY_RATE = 0.005  # UK stamp duty reserve tax, charged on purchases only

DEFAULT_PARAMS = {
    "sma_days": 20,      # moving-average window
    "dip_pct": 3.0,      # buy when the close is this far below the average
    "target_pct": 2.0,   # sell once the sale clears all costs by this much
    "stop_pct": 8.0,     # sell if the close falls this far below the buy price; 0 = off
    "trade_size": None,  # max pounds per buy; None = all available cash
    "plan": "fixed",
    "stamp_duty": True,
}


def commission(value, plan):
    p = COMMISSION_PLANS[plan]
    return round(max(value * p["rate"], p["minimum"]), 2)


def buy_costs(shares, price, plan, stamp_duty):
    value = shares * price
    duty = round(value * STAMP_DUTY_RATE, 2) if stamp_duty else 0.0
    return value, commission(value, plan), duty


def affordable_shares(budget, price, plan, stamp_duty):
    """Most whole shares whose price plus fees fits in budget."""
    rate = COMMISSION_PLANS[plan]["rate"] + (STAMP_DUTY_RATE if stamp_duty else 0)
    shares = math.floor(budget / (price * (1 + rate)))
    # The minimum commission (and penny rounding) can push the estimate over budget
    while shares > 0 and sum(buy_costs(shares, price, plan, stamp_duty)) > budget:
        shares -= 1
    return shares


def sale_proceeds(shares, price, plan):
    value = shares * price
    fee = commission(value, plan)
    return value - fee, fee


def simulate(bars, balance, params=None, warmup=()):
    """Run the strategy over bars (dicts with "time" and "close" in pence, oldest first).

    warmup bars precede the range and only seed the moving average, so trading
    can start on the first day of the range.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    plan, stamp_duty = p["plan"], p["stamp_duty"]
    window = deque((b["close"] / 100 for b in warmup), maxlen=p["sma_days"])
    cash = balance
    position = None  # {"shares", "price", "cost"} where cost includes buy fees
    trades = []
    fees = {"commission": 0.0, "stamp_duty": 0.0}

    for bar in bars:
        price = bar["close"] / 100
        window.append(price)

        if position:
            proceeds, fee = sale_proceeds(position["shares"], price, plan)
            take_profit = proceeds >= position["cost"] * (1 + p["target_pct"] / 100)
            stop_loss = p["stop_pct"] > 0 and price <= position["price"] * (1 - p["stop_pct"] / 100)
            if take_profit or stop_loss:
                cash += proceeds
                fees["commission"] += fee
                trades.append({
                    "time": bar["time"], "side": "sell", "reason": "target" if take_profit else "stop",
                    "shares": position["shares"], "price": bar["close"], "value": round(proceeds + fee, 2),
                    "commission": fee, "stamp_duty": 0.0, "pnl": round(proceeds - position["cost"], 2),
                    "cash": round(cash, 2),
                })
                position = None
            continue

        if len(window) < p["sma_days"]:
            continue
        average = sum(window) / len(window)
        if price > average * (1 - p["dip_pct"] / 100):
            continue
        budget = min(cash, p["trade_size"]) if p["trade_size"] else cash
        shares = affordable_shares(budget, price, plan, stamp_duty)
        if not shares:
            continue
        value, fee, duty = buy_costs(shares, price, plan, stamp_duty)
        cost = value + fee + duty
        cash -= cost
        fees["commission"] += fee
        fees["stamp_duty"] += duty
        position = {"shares": shares, "price": price, "cost": cost}
        trades.append({
            "time": bar["time"], "side": "buy", "reason": "dip", "shares": shares, "price": bar["close"],
            "value": round(value, 2), "commission": fee, "stamp_duty": duty, "pnl": None, "cash": round(cash, 2),
        })

    final_value = cash
    open_position = None
    if position and bars:
        # Value an open position at what selling it on the last day would raise
        last = bars[-1]["close"]
        proceeds, _ = sale_proceeds(position["shares"], last / 100, plan)
        final_value += proceeds
        open_position = {"shares": position["shares"], "buy_price": round(position["price"] * 100, 2),
                         "last_price": last, "unrealised_pnl": round(proceeds - position["cost"], 2)}

    sells = [t for t in trades if t["side"] == "sell"]
    return {
        "start_balance": round(balance, 2),
        "final_value": round(final_value, 2),
        "profit": round(final_value - balance, 2),
        "return_pct": round((final_value / balance - 1) * 100, 2) if balance else 0.0,
        "trades": trades,
        "trade_count": len(trades),
        "wins": sum(t["pnl"] > 0 for t in sells),
        "losses": sum(t["pnl"] <= 0 for t in sells),
        "commission": round(fees["commission"], 2),
        "stamp_duty": round(fees["stamp_duty"], 2),
        "open_position": open_position,
        "buy_and_hold": buy_and_hold(bars, balance, plan, stamp_duty),
    }


def buy_and_hold(bars, balance, plan, stamp_duty):
    """Benchmark: buy on the first day, value on the last, same fees."""
    if not bars:
        return None
    first, last = bars[0]["close"] / 100, bars[-1]["close"] / 100
    shares = affordable_shares(balance, first, plan, stamp_duty)
    if not shares:
        return None
    spent = sum(buy_costs(shares, first, plan, stamp_duty))
    proceeds, _ = sale_proceeds(shares, last, plan)
    final_value = balance - spent + proceeds
    return {"final_value": round(final_value, 2), "return_pct": round((final_value / balance - 1) * 100, 2)}
