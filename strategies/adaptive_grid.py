"""Adaptive buy and sell limit levels around a moving-average anchor.

The anchor is a moving simple average. Spacing expands and contracts with the
one-standard-deviation Bollinger width from completed closes, bounded by
``min_spacing_pct`` and ``max_spacing_pct``. Orders are submitted after each
completed candle and fill no earlier than the following candle. Every filled
line targets the adjacent favorable line; unoccupied lines are resubmitted.

Editable settings:

* ``anchor_period`` and ``volatility_period``: close windows for recentering
  and width measurement.
* ``volatility_multiple``: scale applied to the measured band width.
* ``min_spacing_pct`` / ``max_spacing_pct``: spacing bounds.
* ``lines_per_side``: number of buy and sell lines, from 1 to 20 (default 4).
* ``notional_per_order``: cash notional for each line.
"""

NAME = "Adaptive volatility grid"
WARMUP = 20
SETTINGS = {
    "anchor_period": 20,
    "volatility_period": 20,
    "volatility_multiple": 0.5,
    "min_spacing_pct": 0.25,
    "max_spacing_pct": 5.0,
    "lines_per_side": 4,
    "notional_per_order": 100.0,
}


def _positive_number(settings, key):
    value = settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)


def _period(settings, key):
    value = settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 719:
        raise ValueError(f"{key} must be an integer from 1 to 719")
    return value


def on_bar(ctx):
    settings = ctx["settings"]
    anchor_period = _period(settings, "anchor_period")
    volatility_period = _period(settings, "volatility_period")
    multiple = _positive_number(settings, "volatility_multiple")
    min_spacing = _positive_number(settings, "min_spacing_pct")
    max_spacing = _positive_number(settings, "max_spacing_pct")
    notional = _positive_number(settings, "notional_per_order")
    lines = settings.get("lines_per_side", 4)
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 20:
        raise ValueError("lines_per_side must be an integer from 1 to 20")
    cap = 2 * lines
    if min_spacing > max_spacing or max_spacing * lines >= 100:
        raise ValueError("spacing bounds must be ordered and keep all buy lines positive")

    closes = [float(row["close"]) for row in ctx["history"][-max(anchor_period, volatility_period):]]
    if len(closes) < max(anchor_period, volatility_period):
        return []
    anchor = ctx["helpers"]["sma"](closes, anchor_period)
    middle, upper, lower = ctx["helpers"]["bollinger"](closes, volatility_period, 1.0)
    if anchor is None or middle is None or middle <= 0:
        return []
    measured_pct = (upper - middle) / middle * 100.0
    spacing_pct = min(max_spacing, max(min_spacing, measured_pct * multiple))
    step = spacing_pct / 100.0

    # Keep the latest completed-bar inputs explicit so a resumed run uses the
    # same recentering and spacing state as an uninterrupted run.
    ctx["memory"]["anchor"] = float(anchor)
    ctx["memory"]["spacing_pct"] = float(spacing_pct)
    ctx["memory"]["volatility_pct"] = float(measured_pct)

    positions = ctx["positions"]
    if len(positions) >= cap:
        return []
    occupied = {"long": [], "short": []}
    for lot in positions:
        target = lot.get("target_price")
        if target is not None:
            occupied[lot["direction"]].append(float(target))

    orders = []
    for index in range(1, lines + 1):
        buy_level = anchor * (1.0 - step * index)
        buy_target = anchor * (1.0 - step * (index - 1))
        if not any(abs(target - buy_target) <= anchor * step * 0.25
                   for target in occupied["long"]):
            orders.append({"type": "limit", "direction": "long", "price": buy_level,
                           "notional": notional, "target_price": buy_target})

        sell_level = anchor * (1.0 + step * index)
        sell_target = anchor * (1.0 + step * (index - 1))
        if not any(abs(target - sell_target) <= anchor * step * 0.25
                   for target in occupied["short"]):
            orders.append({"type": "limit", "direction": "short", "price": sell_level,
                           "notional": notional, "target_price": sell_target})

    return orders[:cap - len(positions)]
