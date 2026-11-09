"""Display the trailing 30-day volume profile. Select this file to inspect it.

The profile follows the replay candle. Load at least a month of history before
that candle for a full window; earlier readings are labeled partial history.
Use ctx['plots']['Monthly volume profile']['poc'] in on_bar to access its POC.
Add explicit entry and exit rules to on_bar when ready to test a strategy.
"""
from indicators.volume_profile import volume_profile

NAME = "Volume Profile"
SETTINGS = {"profile_days": 1, "profile_bins": 96, "value_area": 0.60}
WARMUP = 0


def chart_indicators(ctx):
    settings = ctx["settings"]
    return {"Monthly volume profile": volume_profile(
        ctx, days=settings["profile_days"], bins=settings["profile_bins"],
        value_area=settings["value_area"])}


def on_bar(ctx):
    return []
