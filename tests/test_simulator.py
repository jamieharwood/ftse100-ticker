import pytest

import simulator


def bars(*closes):
    return [{"time": f"2026-01-{i + 1:02d}", "close": c} for i, c in enumerate(closes)]


PARAMS = {"sma_days": 3, "dip_pct": 5, "target_pct": 1, "stop_pct": 10}


@pytest.mark.parametrize("value, plan, expected", [
    (1000, "fixed", 3.00),     # 0.05% = £0.50, below the £3 minimum
    (10000, "fixed", 5.00),    # 0.05% above the minimum
    (1000, "tiered", 1.00),
])
def test_commission(value, plan, expected):
    assert simulator.commission(value, plan) == expected


def test_affordable_shares_never_exceeds_budget():
    for price in (0.97, 2.24, 12.84, 134.28):
        shares = simulator.affordable_shares(1000, price, "fixed", True)
        assert sum(simulator.buy_costs(shares, price, "fixed", True)) <= 1000
        assert sum(simulator.buy_costs(shares + 1, price, "fixed", True)) > 1000


def test_buys_on_dip_and_sells_only_after_covering_all_fees():
    # Average of 100,100,90 = 96.67; 90p is 6.9% below it, so buy
    result = simulator.simulate(bars(100, 100, 90, 91, 95), 1000, PARAMS)
    buy, sell = result["trades"]
    assert (buy["side"], buy["time"], buy["price"]) == ("buy", "2026-01-03", 90)
    # 91p wouldn't clear buy fees, stamp duty and sell commission; 95p does
    assert (sell["side"], sell["time"], sell["reason"]) == ("sell", "2026-01-05", "target")
    assert sell["pnl"] > 0
    assert result["final_value"] == pytest.approx(1000 + sell["pnl"])
    assert result["wins"] == 1 and result["losses"] == 0


def test_stop_loss_sells_at_a_loss():
    result = simulator.simulate(bars(100, 100, 90, 85, 80), 1000, PARAMS)
    sell = result["trades"][-1]
    assert (sell["reason"], sell["time"]) == ("stop", "2026-01-05")  # 80 <= 90 * 0.9
    assert sell["pnl"] < 0 and result["losses"] == 1


def test_no_trades_until_average_has_enough_days():
    assert simulator.simulate(bars(100, 50), 1000, PARAMS)["trades"] == []


def test_warmup_seeds_average_so_first_day_can_trade():
    result = simulator.simulate(bars(90), 1000, PARAMS, warmup=bars(100, 100))
    assert result["trades"][0]["time"] == "2026-01-01"


def test_open_position_valued_at_net_sale_proceeds():
    result = simulator.simulate(bars(100, 100, 90), 1000, PARAMS)
    pos = result["open_position"]
    proceeds, _ = simulator.sale_proceeds(pos["shares"], 0.90, "fixed")
    assert result["final_value"] == pytest.approx(result["trades"][0]["cash"] + proceeds)


def test_stamp_duty_and_trade_size_options():
    with_duty = simulator.simulate(bars(100, 100, 90), 1000, PARAMS)
    without = simulator.simulate(bars(100, 100, 90), 1000, {**PARAMS, "stamp_duty": False})
    small = simulator.simulate(bars(100, 100, 90), 1000, {**PARAMS, "trade_size": 100})
    assert with_duty["stamp_duty"] > 0 and without["stamp_duty"] == 0
    assert small["trades"][0]["value"] <= 100


def test_buy_and_hold_benchmark():
    bh = simulator.simulate(bars(100, 110), 1000, PARAMS)["buy_and_hold"]
    shares = simulator.affordable_shares(1000, 1.00, "fixed", True)
    spent = sum(simulator.buy_costs(shares, 1.00, "fixed", True))
    proceeds, _ = simulator.sale_proceeds(shares, 1.10, "fixed")
    assert bh["final_value"] == pytest.approx(1000 - spent + proceeds, abs=0.01)
