"""Buy a fixed ladder at and below EMA 200 when EMA 9 crosses above EMA 200 at candle close.
Use the highest wick of the previous above-EMA upswing before price closed back
at or below the EMA. Freeze both the EMA and the rebound target on the signal.
Each buy level has equal funding and is divided into tranches with individual
sell limits. Default exits sell 85% in the three lines just below the cap.
Orders work from the next candle. Cancel unused buys when the basket closes or
the cap is reached. When price closes across EMA 50 from above to below,
cancel pending buys and remaining targets and sell open lots at the next open.
"""

NAME = "EMA 200 rebound grid"
WARMUP = 201
SETTINGS = {
    "buy_levels": 5,
    "buy_spacing_pct": 0.25,
    "basket_cash_pct": 25.0,
    "rebound_pct": 75.0,
    "sell_levels_pct_of_target_move": [80, 87, 94, 100],
    "sell_weights_pct": [20, 30, 35, 15],
}


def advance(memory, bar):
    previous = memory["ema"]
    ema = previous + 2 / 201 * (bar["close"] - previous)
    above = bar["close"] > ema
    close = bar["close"]
    fast = memory["ema9"] + 2 / 10 * (close - memory["ema9"])
    exit_ema = memory["ema50"] + 2 / 51 * (close - memory["ema50"])
    entry_cross = memory["ema9"] <= previous and fast > ema
    exit_cross = memory["close"] >= memory["ema50"] and close < exit_ema
    if above:
        memory["peak"] = max(memory.get("peak", bar["high"]), bar["high"])
    elif memory["above"]:
        # Include the high of the candle that closed the upswing.
        memory["prior_high"] = max(memory.pop("peak"), bar["high"])
    memory.update(ema=ema, ema9=fast, ema50=exit_ema, close=close, above=above)
    return entry_cross, exit_cross


def make_grid(ema, prior_high, ctx):
    settings = ctx["settings"]
    count = settings["buy_levels"]
    spacing = settings["buy_spacing_pct"] / 100
    allocation = settings["basket_cash_pct"] / 100
    rebound = settings["rebound_pct"] / 100
    levels = settings["sell_levels_pct_of_target_move"]
    weights = settings["sell_weights_pct"]
    if type(count) is not int or not 3 <= count <= 21:
        raise ValueError("Choose 3 to 21 buy levels.")
    if not 0 < spacing * (count - 1) < 1 or not 0 < allocation <= 1 or not 0 < rebound <= 1:
        raise ValueError("Check buy spacing, basket allocation and rebound percentage.")
    if (not levels or len(weights) != len(levels) or levels[-1] != 100 or
            levels[0] <= 0 or any(a >= b for a, b in zip(levels, levels[1:])) or
            min(weights) <= 0 or abs(sum(weights) - 100) > 1e-8):
        raise ValueError("Sell levels must increase through 100 and positive weights must total 100.")
    move = (prior_high - ema) * rebound
    targets = [ema + move * fraction / 100 for fraction in levels]
    buys = [ema * (1 - i * spacing) for i in range(count)]
    if targets[0] <= buys[0] or ctx["bar"]["close"] >= targets[-1]:
        return None
    per_line = ctx["cash"] * allocation / (count * (1 + ctx["fee_bps"]/10000))
    working = []
    for i, price in enumerate(buys, 1):
        for j, (target, weight) in enumerate(zip(targets, weights), 1):
            working.append(dict(type="limit", direction="long", price=price,
                                notional=per_line*weight/100, target_price=target,
                                label=f"EMA buy {i} / sell {j}"))
    return dict(working=working, cap=targets[-1], filled=False)


def on_bar(ctx):
    memory = ctx["memory"]
    if "ema" not in memory:
        previous = ctx["history"][:-1]
        seed = sum(bar["close"] for bar in previous[:200]) / 200
        last = previous[199]
        closes = [bar["close"] for bar in previous[:200]]
        ema = ctx["helpers"]["ema"]
        memory.update(ema=seed, ema9=ema(closes, 9), ema50=ema(closes, 50),
                      close=last["close"], above=last["close"] > seed)
        if memory["above"]:
            memory["peak"] = last["high"]
        for bar in previous[200:]:
            advance(memory, bar)
    crossed, exit_cross = advance(memory, ctx["bar"])
    if exit_cross:
        memory.pop("grid", None)
        return [dict(action="exit", type="market", lot_id=lot["lot_id"],
                     cancel_targets=True, label="Close below EMA 50")
                for lot in ctx["positions"]]
    grid = memory.get("grid")
    if grid:
        entries = {e["label"] for e in ctx["events"] if e["kind"] == "entry"}
        if entries:
            grid["filled"] = True
            grid["working"] = [o for o in grid["working"] if o["label"] not in entries]
        if ctx["bar"]["high"] >= grid["cap"]:
            grid["working"] = []
        if ctx["positions"] or (grid["working"] and not grid["filled"] and not crossed):
            return grid["working"]
        del memory["grid"]
    if crossed and memory.get("prior_high") and ctx["cash"] > 0:
        grid = make_grid(memory["ema"], memory["prior_high"], ctx)
        if grid:
            memory["grid"] = grid
            return grid["working"]
    return []
