"""Two moving-average cross example with lot-keyed, non-grid exits.

All decisions happen after a candle closes. Market orders fill at the following
bar's open. Protective stops and targets are intentionally omitted here so the
example demonstrates explicit strategy exits by lot id.
"""

NAME = "Trend Cross"
WARMUP = 31
SETTINGS = {
    "fast_period": 10,
    "slow_period": 30,
    "notional": 1000.0,
    "allow_short": True,
}


def on_bar(ctx):
    settings = ctx["settings"]
    fast_period = settings["fast_period"]
    slow_period = settings["slow_period"]
    if type(fast_period) is not int or type(slow_period) is not int or not 1 <= fast_period < slow_period < 10000:
        raise ValueError("Periods must be whole numbers with fast < slow and slow below 10,000")
    closes = [bar["close"] for bar in ctx["history"][-(slow_period + 1):]]
    if len(closes) < slow_period + 1:
        return []

    fast_now = sma(closes, fast_period)
    slow_now = sma(closes, slow_period)
    fast_before = sma(closes[:-1], fast_period)
    slow_before = sma(closes[:-1], slow_period)
    if None in (fast_now, slow_now, fast_before, slow_before):
        return []

    crossed_up = fast_before <= slow_before and fast_now > slow_now
    crossed_down = fast_before >= slow_before and fast_now < slow_now
    if not crossed_up and not crossed_down:
        return []

    target_direction = "long" if crossed_up else ("short" if settings["allow_short"] else None)
    orders = []
    for lot in ctx["positions"]:
        if target_direction is None or lot["direction"] != target_direction:
            orders.append({"type": "market", "action": "exit", "lot_id": lot["lot_id"]})

    if target_direction and not any(lot["direction"] == target_direction for lot in ctx["positions"]):
        orders.append({"type": "market", "direction": target_direction,
                       "notional": float(settings["notional"])})
    return orders
