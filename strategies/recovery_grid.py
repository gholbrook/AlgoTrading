"""Buy a seed, then buy dips at fixed intervals. Each lot has its own stop and profit target.
When realized grid gains cover the estimated loss on all remaining stops, spread the
remaining sells along a favorable move. Entry spacing stays fixed. The ladder resets
when the basket is flat. All percentages are measured in percentage points."""
NAME = "Grid with risk recovery"
WARMUP = 20
SETTINGS = {
    "max_lines": 4,
    "order_pct_of_cash": 5.0,
    "entry_spacing_pct": 0.4,
    "stop_pct": 2.0,
    "initial_profit_pct": 0.8,
    "recovery_spacing": "geometric",  # fixed, geometric, or volatility
    "recovery_step_pct": 1.0,
    "recovery_growth": 1.5,
    "volatility_lookback": 20,
    "volatility_multiplier": 1.0,
}


def remaining_stop_loss(ctx):
    loss = 0.0
    for lot in ctx["positions"]:
        fill = lot["stop_price"] * (1 - ctx["slip_bps"] / 10000)
        net = (fill - lot["entry_price"]) * lot["qty"] - lot["entry_fee"]
        net -= fill * lot["qty"] * ctx["fee_bps"] / 10000
        loss += max(0.0, -net)
    return loss


def recovery_step(ctx):
    settings = ctx["settings"]
    if settings["recovery_spacing"] != "volatility":
        return settings["recovery_step_pct"] / 100
    closes = [bar["close"] for bar in ctx["history"][-settings["volatility_lookback"]:]]
    middle, upper, _ = ctx["helpers"]["bollinger"](closes, settings["volatility_lookback"])
    if middle is None:
        return settings["recovery_step_pct"] / 100
    return max((upper - middle) / middle, 0.000001) * settings["volatility_multiplier"]


def on_bar(ctx):
    cfg, memory = ctx["settings"], ctx["memory"]
    if cfg["recovery_spacing"] not in ("fixed", "geometric", "volatility"):
        raise ValueError("Choose fixed, geometric or volatility recovery spacing")
    if type(cfg["max_lines"]) is not int or cfg["max_lines"] < 1:
        raise ValueError("max_lines must be a positive integer")
    if not 0 < cfg["order_pct_of_cash"] < 100 or not 0 < cfg["entry_spacing_pct"] < 100:
        raise ValueError("Order size and entry spacing must be between 0 and 100 percent")
    if not 0 < cfg["stop_pct"] < 100 or cfg["initial_profit_pct"] <= 0:
        raise ValueError("Choose a positive target and a stop strictly between 0 and 100 percent")
    if min(cfg["recovery_step_pct"], cfg["recovery_growth"], cfg["volatility_multiplier"]) <= 0:
        raise ValueError("Recovery step, growth, and volatility multiplier must be positive")
    if type(cfg["volatility_lookback"]) is not int or not 2 <= cfg["volatility_lookback"] <= 10000:
        raise ValueError("Volatility lookback must be between 2 and 10,000 candles")
    buy = {"direction": "long", "notional": ctx["cash"] * cfg["order_pct_of_cash"] / 100,
           "stop_pct": cfg["stop_pct"], "label": "Fixed grid buy"}
    if not ctx["positions"]:
        memory.clear()
        memory.update(start_pnl=ctx["realized_pnl"], lines=0, targets={})
        return [dict(buy, type="market")] if buy["notional"] > 0 else []

    for fill in ctx["events"]:
        if fill["kind"] == "entry":
            memory["lines"] += 1
            memory["last_buy"] = fill["price"]

    realized = ctx["realized_pnl"] - memory["start_pnl"]
    if "anchor" not in memory and realized > 0 and realized >= remaining_stop_loss(ctx):
        memory.update(anchor=ctx["bar"]["close"], next_sell=ctx["bar"]["close"],
                      step=recovery_step(ctx), rank=0)

    orders = []
    for lot in ctx["positions"]:
        target = lot["entry_price"] * (1 + cfg["initial_profit_pct"] / 100)
        if "anchor" in memory:
            key = str(lot["lot_id"])
            if key not in memory["targets"]:
                growth = cfg["recovery_growth"] if cfg["recovery_spacing"] == "geometric" else 1
                memory["next_sell"] += memory["anchor"] * memory["step"] * growth ** memory["rank"]
                memory["targets"][key] = max(target, memory["next_sell"])
                memory["rank"] += 1
            target = memory["targets"][key]
        orders.append({"type": "limit", "action": "exit", "lot_id": lot["lot_id"], "price": target,
                       "label": "Recovery profit target" if "anchor" in memory else "Initial profit target"})
    if memory["lines"] < cfg["max_lines"] and buy["notional"] > 0:
        next_buy = memory["last_buy"] * (1 - cfg["entry_spacing_pct"] / 100)
        orders.append(dict(buy, type="limit", price=next_buy))
    return orders
