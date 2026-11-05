"""Fixed-distance buy and sell limit levels around a fixed reference price.

The anchor is the first close seen by this run and never moves. Each filled
line exits one spacing step back toward the anchor. The strategy resubmits a
one-bar limit order for every currently unoccupied line, so a line can be used
again after its position closes.

Settings can be overridden in ``strategy_settings``:

* ``spacing_pct``: positive percentage distance between adjacent lines.
* ``lines_per_side``: number of buy and sell lines, from 1 to 20 (default 4).
* ``notional_per_order``: cash notional allocated to each line.
"""

NAME = "Fixed distance grid"
WARMUP = 1
SETTINGS = {
    "spacing_pct": 1.0,
    "lines_per_side": 4,
    "notional_per_order": 100.0,
}


def _positive_number(settings, key):
    value = settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)


def on_bar(ctx):
    settings = ctx["settings"]
    spacing_pct = _positive_number(settings, "spacing_pct")
    notional = _positive_number(settings, "notional_per_order")
    lines = settings.get("lines_per_side", 4)
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 20:
        raise ValueError("lines_per_side must be an integer from 1 to 20")
    cap = 2 * lines
    if spacing_pct * lines >= 100:
        raise ValueError("spacing_pct must keep all buy lines above zero")

    memory = ctx["memory"]
    close = float(ctx["bar"]["close"])
    if "anchor" not in memory:
        memory["anchor"] = close
    anchor = float(memory["anchor"])
    step = spacing_pct / 100.0

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
        if not any(abs(target - buy_target) <= anchor * step * 1e-6
                   for target in occupied["long"]):
            orders.append({"type": "limit", "direction": "long", "price": buy_level,
                           "notional": notional, "target_price": buy_target})

        sell_level = anchor * (1.0 + step * index)
        sell_target = anchor * (1.0 + step * (index - 1))
        if not any(abs(target - sell_target) <= anchor * step * 1e-6
                   for target in occupied["short"]):
            orders.append({"type": "limit", "direction": "short", "price": sell_level,
                           "notional": notional, "target_price": sell_target})

    remaining = cap - len(positions)
    return orders[:remaining]
