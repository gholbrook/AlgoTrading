"""Trailing volume-at-price histogram for strategy chart_indicators(ctx).

Usage::

    from indicators.volume_profile import volume_profile

    def chart_indicators(ctx):
        return {"Monthly volume profile": volume_profile(ctx, days=30, bins=48)}

Uses only context candles through the current candle. Candle volume is spread
uniformly over its high-low range, an OHLCV approximation of traded volume at
price. Zero-range candles put all volume into their price bin. Volumes retain
source units. The window is a trailing number of 24-hour days, not a calendar
month. Set candles=N to use exactly the last N available candles instead.
Short or interrupted history is explicitly marked as partial.

Strategies can also inspect the returned poc, value_low, and value_high.
Imported helper code is loaded from disk; archived strategy source does not
snapshot this module. Reruns use the installed helper version.
"""
import math


def volume_profile(ctx, *, days=30, bins=48, value_area=0.70, candles=None):
    """Return a JSON-compatible volume_profile overlay or None for empty history."""
    if type(days) not in (int, float) or not math.isfinite(days) or not 0 < days <= 365:
        raise ValueError("days must be greater than zero and at most 365")
    if type(bins) is not int or not 2 <= bins <= 100:
        raise ValueError("bins must be an integer from 2 to 100")
    if type(value_area) not in (int, float) or not math.isfinite(value_area) or not 0 < value_area <= 1:
        raise ValueError("value_area must be greater than zero and at most one")
    if candles is not None and (type(candles) is not int or candles < 2):
        raise ValueError("candles must be an integer of at least 2")
    history = ctx["history"]
    if not history:
        return None
    end = ctx["bar"]["ts"]
    cutoff = end - days * 86400
    # Slice only the needed suffix, including support for read-only context views.
    start = len(history)
    while start and (candles is not None or history[start - 1]["ts"] > cutoff):
        start -= 1
    bars = [bar for bar in history[start:] if bar["ts"] <= end]
    if candles is not None:
        bars = bars[-candles:]
    if not bars:
        return None
    low = min(bar["low"] for bar in bars)
    high = max(bar["high"] for bar in bars)
    if high == low:
        padding = max(abs(low) * 0.000001, 1e-12)
        low, high = low - padding, high + padding
    width = (high - low) / bins
    volumes = [0.0] * bins
    # Interior bins have equal density. A difference array keeps work O(candles + bins).
    difference = [0.0] * (bins + 1)
    for bar in bars:
        bottom, top, volume = bar["low"], bar["high"], bar["volume"]
        first = min(bins - 1, max(0, int((bottom - low) / width)))
        last = min(bins - 1, max(0, int((top - low) / width)))
        if first == last or top == bottom:
            volumes[first] += volume
            continue
        density = volume / (top - bottom)
        volumes[first] += density * (low + (first + 1) * width - bottom)
        volumes[last] += density * (top - (low + last * width))
        difference[first + 1] += density * width
        difference[last] -= density * width
    running = 0.0
    for index in range(bins):
        running += difference[index]
        volumes[index] = max(0.0, volumes[index] + running)
    total = sum(volumes)
    poc_index = max(range(bins), key=volumes.__getitem__)
    left = right = poc_index
    included = volumes[poc_index]
    while included < total * value_area and (left > 0 or right < bins - 1):
        below = volumes[left - 1] if left > 0 else -1
        above = volumes[right + 1] if right < bins - 1 else -1
        if below >= above:
            left -= 1
            included += volumes[left]
        else:
            right += 1
            included += volumes[right]
    intervals = [b["ts"] - a["ts"] for a, b in zip(bars, bars[1:])]
    interval = min(intervals) if intervals else 0
    covers_window = len(bars) == candles if candles is not None else bars[0]["ts"] <= cutoff + interval
    complete = bool(interval and covers_window and all(gap == interval for gap in intervals))
    return {"type": "volume_profile", "low": low, "high": high, "volumes": volumes,
            "poc": low + (poc_index + 0.5) * width if total else None,
            "value_low": low + left * width if total else None,
            "value_high": low + (right + 1) * width if total else None,
            "start": bars[0]["ts"], "end": end, "days": days, "complete": complete,
            "total_volume": total, "candles": len(bars), "lookback_candles": candles}


def validate_profile(value):
    """Validate structured chart output before it enters a persisted engine point."""
    if value.get("type") != "volume_profile":
        raise ValueError("unsupported chart overlay type")
    for field in ("low", "high", "start", "end", "days", "total_volume", "candles"):
        number = value.get(field)
        if type(number) not in (float, int) or not math.isfinite(number):
            raise ValueError(f"volume profile {field} must be finite")
    if value["low"] >= value["high"] or value["days"] <= 0 or value["total_volume"] < 0:
        raise ValueError("invalid volume profile range")
    volumes = value.get("volumes")
    if not isinstance(volumes, list) or not 2 <= len(volumes) <= 100:
        raise ValueError("volume profile needs 2–100 volume bins")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in volumes):
        raise ValueError("volume profile bins must be finite and nonnegative")
    for field in ("poc", "value_low", "value_high"):
        number = value.get(field)
        if number is not None and (type(number) not in (float, int) or not math.isfinite(number)
                                   or not value["low"] <= number <= value["high"]):
            raise ValueError(f"volume profile {field} must be within its price range")
    if type(value.get("complete")) is not bool:
        raise ValueError("volume profile complete must be a boolean")
