"""Open a long ladder when the MACD line crosses above its signal line at candle close.
The crossover can occur in any regime and on either side of the MACD zero line.
Use one next-open buy and stagger equal-sized limits below the signal close.
Keep limits working until the basket closes or the histogram falls twice in a row.
Each fill gets its own stop and partial sell limit. At that limit the net sale
proceeds recover the full purchase plus entry fee. When three successive histogram values
strictly decrease, above, below, or across zero, cancel outstanding buy and recovery limits and sell all
remaining units at the next open, even if no recovery target filled.
"""

NAME = "MACD contraction and capital recovery"
WARMUP = 100  # Seed the 12/26/9 MACD from close history.
SETTINGS = {"buy_count": 3, "basket_cash_pct": 25.0, "buy_spacing_pct": 0.5,
            "stop_pct": 2.0, "recovery_gain_pct": 2.0}


def update_indicator(memory, close):
    # Standard MACD line = EMA(12) - EMA(26); histogram = line - EMA(9) of line.
    for key, period in (("fast", 12), ("slow", 26)):
        previous = memory.get(key, close)
        memory[key] = previous + 2 / (period + 1) * (close - previous)
    line = memory["fast"] - memory["slow"]
    previous = memory.get("signal", line)
    memory["signal"] = previous + 0.2 * (line - previous)
    hist = memory.setdefault("hist", [])
    hist.append(line - memory["signal"])
    del hist[:-4]


def on_bar(ctx):
    s = ctx["settings"]
    count = s["buy_count"]
    spacing = s["buy_spacing_pct"] / 100
    if not isinstance(count, int) or not 1 <= count <= 20 or spacing <= 0 or (count-1)*spacing >= 1:
        raise ValueError("Use 1–20 buys with positive spacing and prices above zero.")
    if not 0 < s["basket_cash_pct"] <= 100 or not 0 < s["stop_pct"] < 100:
        raise ValueError("Check basket allocation and stop percentage.")
    fee = ctx["fee_bps"]/10000
    if (1+s["recovery_gain_pct"]/100)*(1-fee) <= 1+fee:
        raise ValueError("Increase recovery gain to cover both fees and leave a remainder.")

    memory = ctx["memory"]
    if "fast" not in memory:
        for bar in ctx["history"][:-1]:
            update_indicator(memory, bar["close"])
    update_indicator(memory, ctx["bar"]["close"])
    h = memory["hist"]
    entry = h[-2] <= 0 < h[-1]  # Histogram is MACD minus signal.
    fading = h[-3] > h[-2] > h[-1]
    lots = ctx["positions"]
    basket = memory.get("basket")
    if basket and not lots:
        memory.pop("basket")
        basket = None
    orders = []
    if basket:
        filled = {e["label"] for e in ctx["events"] if e["kind"] == "entry"}
        basket["working"] = [k for k in basket["working"] if str(k+1) not in filled]
        if fading:
            basket["working"] = []
            return [{"action": "exit", "lot_id": lot["lot_id"], "cancel_targets": True,
                     "label": "MACD reversal: sell remaining position"} for lot in lots]
    elif entry and ctx["cash"] > 0:
        basket = {"anchor": ctx["bar"]["close"], "working": list(range(count)),
                  "notional": ctx["cash"] * s["basket_cash_pct"]/100 / count / (1+fee)}
        memory["basket"] = basket
    if basket:
        for k in basket["working"]:
            order = {"direction": "long", "type": "limit" if k else "market", "label": str(k+1),
                     "notional": basket["notional"], "stop_pct": s["stop_pct"],
                     "recover_cost_pct": s["recovery_gain_pct"]}
            if k:
                order["price"] = basket["anchor"]*(1-k*spacing)
            orders.append(order)
    return orders
