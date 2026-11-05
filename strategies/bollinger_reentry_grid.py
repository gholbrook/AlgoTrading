"""Buy a lower-band re-entry in a rising market. On completed candles, the
previous close must be below its lower Bollinger band and the current close
inside its bands. The 200-candle SMA must exceed its value five candles ago.

Budget one quarter of starting free cash per buy, including its entry fee.
Submit the market buy for the next open and three buy limits together, 2%, 4%,
and 6% below the signal close. Keep each unfilled line working while the grid
holds inventory. Every buy immediately attaches four equal-quantity sell limits
above its actual fill. Once flat, cancel unused lines and await a fresh signal.
"""

NAME = "Bollinger re-entry grid"
BAND_PERIOD = 20
REGIME_PERIOD = 200
REGIME_LOOKBACK = 5
WARMUP = 205  # 200 closes plus the five-candle slope lookback
SETTINGS = {
    "band_deviations": 2.0,
    "cash_per_buy_pct": 25.0,
    "buy_step_pct": 2.0,
    "sell_step_pct": 2.0,
}
BUY_LABELS = ["Re-entry buy", "Grid buy 2", "Grid buy 3", "Grid buy 4"]


def chart_indicators(ctx):
    closes = [bar["close"] for bar in ctx["history"][-REGIME_PERIOD:]]
    middle, upper, lower = ctx["helpers"]["bollinger"](
        closes, BAND_PERIOD, ctx["settings"]["band_deviations"])
    return {"Upper band": upper, "Middle band": middle, "Lower band": lower,
            "Regime SMA": ctx["helpers"]["sma"](closes, REGIME_PERIOD)}


def entry_signal(ctx, deviations):
    closes = [bar["close"] for bar in ctx["history"][-WARMUP:]]
    if len(closes) < WARMUP:
        return False
    sma = ctx["helpers"]["sma"]
    if ctx["plots"]["Regime SMA"] <= sma(closes[:-REGIME_LOOKBACK], REGIME_PERIOD):
        return False
    bands = ctx["helpers"]["bollinger"]
    _, _, previous_lower = bands(closes[:-1], BAND_PERIOD, deviations)
    return (closes[-2] < previous_lower and
            ctx["plots"]["Lower band"] <= closes[-1] <= ctx["plots"]["Upper band"])


def on_bar(ctx):
    settings = ctx["settings"]
    fraction = settings["cash_per_buy_pct"] / 100
    buy_step = settings["buy_step_pct"] / 100
    sell_step = settings["sell_step_pct"]
    deviations = settings["band_deviations"]
    if not (0 < fraction <= 0.25 and 0 < buy_step < 1 / 3 and sell_step > 0 and deviations > 0):
        raise ValueError("Use up to 25% cash per buy, buy spacing below 33⅓%, and positive sell spacing and band deviations.")

    memory = ctx["memory"]
    grid = memory.get("grid")
    if grid:
        if not ctx["positions"]:
            del memory["grid"]
            grid = None
        else:
            filled = {e["label"] for e in ctx["events"] if e["kind"] == "entry"}
            grid["working"] = [i for i in grid["working"] if BUY_LABELS[i] not in filled]

    if grid is None:
        if not entry_signal(ctx, deviations) or ctx["cash"] <= 0:
            return []
        grid = {"anchor": ctx["bar"]["close"], "working": [0, 1, 2, 3],
                "notional": ctx["cash"] * fraction / (1 + ctx["fee_bps"] / 10000)}
        memory["grid"] = grid

    orders = []
    for index in grid["working"]:
        order = {"type": "market" if index == 0 else "limit", "direction": "long",
                 "notional": grid["notional"], "label": BUY_LABELS[index],
                 "target_steps_pct": [sell_step * n for n in range(1, 5)]}
        if index:
            order["price"] = grid["anchor"] * (1 - index * buy_step)
        orders.append(order)
    return orders
