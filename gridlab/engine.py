"""Small, deterministic, incremental Bollinger grid backtest engine.

Signals are calculated from a completed candle and queued for a market fill at
the following candle's open. Stops and targets use that candle's OHLC, with
gap-aware open fills and the stop taking priority when both levels are crossed.
"""
from __future__ import annotations

import json
from math import isfinite, sqrt

DEFAULTS = {
    "capital": 50000.0,
    "size_pct": 2.0,
    "fee_bps": 20.0,
    "slip_bps": 2.0,
    "tp_pct": 0.45,
    "sl_pct": 2.0,
    "direction": "legacy-long",
    "bb_period": 20,
    "bb_mult": 2.0,
    "trend_period": 200,
    "trend_lookback": 5,
    "tier2_mult": 1.5,
    "signal_model": "wick-grid",
    "bull_price": "low",
    "bull_op": "<=",
    "bull_level": "lower",
    "bull_trend": "rising",
    "bear_price": "high",
    "bear_op": ">=",
    "bear_level": "upper",
    "bear_trend": "non-rising",
    "execution_model": "signal-layers",
    "grid_lines": 3,
    "grid_spacing": "fixed",
    "grid_step_pct": 1.0,
    "grid_growth": 1.5,
    "grid_after_step_pct": 1.0,
    "grid_after_growth": 1.5,
    "grid_after_exits": "unchanged",
}
_ALLOWED = set(DEFAULTS) | {"strategy_file", "strategy_settings"}


def validate_config(partial_dict: dict | None) -> dict:
    """Merge config with defaults and reject unknown or invalid values."""
    if partial_dict is None:
        partial_dict = {}
    if not isinstance(partial_dict, dict):
        raise TypeError("config must be a dict")
    unknown = set(partial_dict) - _ALLOWED
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
    cfg = {**DEFAULTS, **partial_dict}
    if "strategy_file" in cfg:
        spec = cfg["strategy_file"]
        if not isinstance(spec, dict) or not isinstance(spec.get("filename"), str) or not isinstance(spec.get("source"), str):
            raise ValueError("strategy_file must contain string filename and source")
    if "strategy_settings" in cfg:
        if "strategy_file" not in cfg:
            raise ValueError("strategy_settings require a strategy_file")
        if not isinstance(cfg["strategy_settings"], dict):
            raise ValueError("strategy_settings must be a dictionary")
        try:
            json.dumps(cfg["strategy_settings"], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("strategy_settings must contain JSON-compatible values") from exc
    if cfg["signal_model"] not in ("wick-grid", "sma-cross", "custom"):
        raise ValueError("signal_model must be 'wick-grid', 'sma-cross', or 'custom'")
    for side in ("bull", "bear"):
        for key, choices in ((f"{side}_price", ("open", "high", "low", "close")),
                             (f"{side}_op", ("<=", ">=")),
                             (f"{side}_level", ("lower", "upper", "sma")),
                             (f"{side}_trend", ("any", "rising", "non-rising", "disabled"))):
            if cfg[key] not in choices:
                raise ValueError(f"{key} must be one of {', '.join(choices)}")
    for key in ("bb_period", "trend_period", "trend_lookback"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or cfg[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("bb_mult", "tier2_mult"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], (int, float)):
            raise ValueError(f"{key} must be numeric")
        cfg[key] = float(cfg[key])
        if not isfinite(cfg[key]) or cfg[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
    for key in ("capital", "size_pct", "fee_bps", "slip_bps", "tp_pct", "sl_pct"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], (int, float)):
            raise ValueError(f"{key} must be numeric")
        cfg[key] = float(cfg[key])
        if not isfinite(cfg[key]):
            raise ValueError(f"{key} must be finite")
    if cfg["capital"] <= 0 or not 0 < cfg["size_pct"] <= 100:
        raise ValueError("capital must be positive and size_pct in (0, 100]")
    if cfg["fee_bps"] < 0 or not 0 <= cfg["slip_bps"] < 10000:
        raise ValueError("fee_bps must be nonnegative and slip_bps in [0, 10000)")
    if not 0 < cfg["tp_pct"] < 100 or not 0 < cfg["sl_pct"] < 100:
        raise ValueError("tp_pct and sl_pct must be in (0, 100)")
    if max(cfg["bb_period"], cfg["trend_period"] + cfg["trend_lookback"]) > 719:
        raise ValueError("configured indicator warmup must not exceed 719 candles")
    if cfg["direction"] not in ("legacy-long", "long-short"):
        raise ValueError("direction must be 'legacy-long' or 'long-short'")
    if cfg["execution_model"] not in ("signal-layers", "single-position", "price-grid"):
        raise ValueError("invalid execution_model")
    if isinstance(cfg["grid_lines"], bool) or not isinstance(cfg["grid_lines"], int) or not 1 <= cfg["grid_lines"] <= 100:
        raise ValueError("grid_lines must be an integer in [1, 100]")
    if cfg["grid_spacing"] not in ("fixed", "geometric", "band"):
        raise ValueError("grid_spacing must be fixed, geometric, or band")
    if cfg["grid_after_exits"] not in ("unchanged", "fixed", "geometric", "band"):
        raise ValueError("grid_after_exits must be unchanged, fixed, geometric, or band")
    for key in ("grid_step_pct", "grid_growth", "grid_after_step_pct", "grid_after_growth"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], (int, float)):
            raise ValueError(f"{key} must be numeric")
        cfg[key] = float(cfg[key])
        if not isfinite(cfg[key]) or cfg[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
        if key in ("grid_growth", "grid_after_growth") and cfg[key] > 10:
            raise ValueError(f"{key} must not exceed 10")
        if key in ("grid_step_pct", "grid_after_step_pct") and cfg[key] > 1000:
            raise ValueError(f"{key} must not exceed 1000")
    return cfg


def new_state(config: dict | None = None) -> dict:
    """Return a JSON-serializable engine state."""
    cfg = validate_config(config)
    state = {
        "config": cfg,
        "history": [],  # bounded to the configured indicator warmup
        "positions": [],
        "pending": [],
        "cash": cfg["capital"],
        "fees": 0.0,
        "realized_pnl": 0.0,
        "closed_trades": 0,
        "wins": 0,
        "peak_equity": cfg["capital"],
        "max_drawdown": 0.0,
        "signal_streak": 0,
        "last_ts": None,
    }
    if cfg["execution_model"] == "price-grid":
        state.update({"basket": None, "next_position_id": 1, "next_basket_id": 1})
    return state


def warmup_count(config: dict | None = None) -> int:
    """Return closed-candle warmup required for the configured indicators."""
    cfg = validate_config(config)
    if "strategy_file" in cfg:
        from gridlab.file_engine import file_warmup
        return file_warmup(cfg)
    return _warmup(cfg)


def _warmup(cfg: dict) -> int:
    return max(cfg["bb_period"], cfg["trend_period"] + cfg["trend_lookback"])


def _indicators(history: list[float], cfg: dict) -> tuple[float | None, float | None, float | None, float | None]:
    sma = None
    if len(history) >= cfg["trend_period"]:
        sma = sum(history[-cfg["trend_period"]:]) / cfg["trend_period"]
    middle = upper = lower = band_pct = None
    if len(history) >= cfg["bb_period"]:
        sample = history[-cfg["bb_period"]:]
        middle = sum(sample) / cfg["bb_period"]
        variance = sum((x - middle) ** 2 for x in sample) / cfg["bb_period"]
        dev = cfg["bb_mult"] * sqrt(variance)
        upper, lower = middle + dev, middle - dev
        band_pct = ((upper - middle) / middle * 100.0) if middle else 0.0
    return sma, upper, lower, band_pct


def _levels(pos: dict, cfg: dict) -> tuple[float, float | None]:
    if "stop_price" in pos and "target_price" in pos:
        target = pos["target_price"]
        return pos["stop_price"], target
    sign, entry = pos["direction_sign"], pos["entry_price"]
    stop = entry * (1.0 - cfg["sl_pct"] / 100.0 if sign > 0 else 1.0 + cfg["sl_pct"] / 100.0)
    target = entry * (1.0 + pos["tp_pct"] / 100.0 if sign > 0 else 1.0 - pos["tp_pct"] / 100.0)
    return stop, target if target > 0 else None


def _unrealized(state: dict, mark: float) -> float:
    return sum(p["direction_sign"] * (mark - p["entry_price"]) * p["qty"] - p["entry_fee"]
               for p in state["positions"])


def _equity(state: dict, mark: float) -> tuple[float, float]:
    unrealized = _unrealized(state, mark)
    return state["config"]["capital"] + state["realized_pnl"] + unrealized, unrealized


def _grid_spacing(cfg: dict, basket: dict, next_layer: int) -> float:
    mode = cfg["grid_spacing"]
    step_key = "grid_step_pct"
    growth_key = "grid_growth"
    if mode == "band":
        return max(float(basket.get("band_pct") or 0.0), 1e-9) * cfg[step_key]
    step = cfg[step_key]
    return step if mode == "fixed" else step * cfg[growth_key] ** max(0, next_layer - 2)


def _covered_target(cfg: dict, basket: dict, sign: int, index: int) -> float | None:
    mode = cfg["grid_after_exits"]
    if mode == "unchanged" or not basket.get("coverage_close"):
        return None
    if mode == "band":
        interval = max(float(basket.get("band_pct") or 0.0), 1e-9) * cfg["grid_after_step_pct"]
    elif mode == "geometric":
        growth = cfg["grid_after_growth"]
        interval = (cfg["grid_after_step_pct"] * index if growth == 1 else
                    cfg["grid_after_step_pct"] * (growth ** index - 1) / (growth - 1))
    else:
        interval = cfg["grid_after_step_pct"]
    proposed = basket["coverage_close"] * (1.0 + sign * interval * index / 100.0)
    return proposed if proposed > 0 else None


def _basket_risk(state: dict) -> float:
    """Estimated net loss at each remaining lot's stop, including costs."""
    cfg = state["config"]
    slip = cfg["slip_bps"] / 10000.0
    fee_rate = cfg["fee_bps"] / 10000.0
    risk = 0.0
    basket = state.get("basket")
    for pos in state["positions"]:
        if basket is not None and pos.get("basket_id") != basket.get("id"):
            continue
        stop, _ = _levels(pos, cfg)
        sign = pos["direction_sign"]
        exit_fill = stop * (1.0 - slip if sign > 0 else 1.0 + slip)
        gross = sign * (exit_fill - pos["entry_price"]) * pos["qty"]
        exit_fee = exit_fill * pos["qty"] * fee_rate
        risk += max(0.0, -(gross - pos["entry_fee"] - exit_fee))
    return risk


def step(state: dict, bar: dict, *, trade: bool = True) -> dict:
    """Consume one completed OHLCV bar and mutate state, returning its point."""
    required = ("ts", "open", "high", "low", "close", "volume")
    if any(k not in bar for k in required):
        raise ValueError("bar requires ts, open, high, low, close, volume")
    ts = bar["ts"]
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not isfinite(ts):
        raise ValueError("ts must be a finite Unix timestamp")
    if state["last_ts"] is not None and ts <= state["last_ts"]:
        raise ValueError("bar timestamps must increase")
    values = [bar[k] for k in required[1:]]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values):
        raise ValueError("OHLCV values must be finite numbers")
    o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    if min(o, h, l, c) <= 0 or float(bar["volume"]) < 0 or h < max(o, c, l) or l > min(o, c, h):
        raise ValueError("invalid OHLC values")

    cfg = state["config"]
    if any(key not in cfg for key in DEFAULTS):
        # Persisted paper sessions from older engine versions have a partial
        # config. Merge the current defaults once, when that state is resumed.
        cfg = validate_config(cfg)
        state["config"] = cfg
    if "strategy_file" in cfg:
        from gridlab.file_engine import step_file
        return step_file(state, bar, trade=trade)
    state["history"].append(c)
    warmup = _warmup(cfg)
    if len(state["history"]) > warmup:
        del state["history"][:-warmup]
    sma, upper, lower, band_pct = _indicators(state["history"], cfg)
    events: list[dict] = []
    slip = cfg["slip_bps"] / 10000.0
    fee_rate = cfg["fee_bps"] / 10000.0
    def exit_position(pos: dict, raw_price: float, reason: str) -> None:
        side = "sell" if pos["direction_sign"] == 1 else "buy"
        fill = raw_price * (1.0 - slip if side == "sell" else 1.0 + slip)
        gross = pos["direction_sign"] * (fill - pos["entry_price"]) * pos["qty"]
        fee = fill * pos["qty"] * fee_rate
        net = gross - pos["entry_fee"] - fee
        state["cash"] += pos["notional"] + gross - fee
        state["fees"] += fee
        state["realized_pnl"] += net
        basket = state.get("basket")
        if basket is not None and pos.get("basket_id") == basket.get("id"):
            basket["realized_net"] += net
        state["closed_trades"] += 1
        state["wins"] += int(net > 0)
        event = {"ts": ts, "side": side, "kind": "exit", "direction": pos["direction"],
                 "entry_ts": pos.get("entry_ts"), "price": fill, "qty": pos["qty"],
                 "fee": fee, "pnl": net, "reason": reason}
        if pos.get("basket_id") is not None:
            event.update({"position_id": pos.get("position_id"), "basket_id": pos["basket_id"],
                          "grid_layer": pos.get("grid_layer")})
        events.append(event)

    def enter_position(direction: str, raw_price: float, reason: str, *,
                       basket: dict | None = None, layer: int | None = None,
                       tp_pct: float | None = None) -> dict | None:
        sign = 1 if direction == "long" else -1
        fill = raw_price * (1.0 + slip if sign > 0 else 1.0 - slip)
        notional = min(state["cash"] / (1.0 + fee_rate), state["cash"] * cfg["size_pct"] / 100.0)
        if notional <= 0:
            return None
        qty = notional / fill
        fee = notional * fee_rate
        state["cash"] -= notional + fee
        state["fees"] += fee
        pos = {"direction": direction, "direction_sign": sign, "entry_price": fill,
               "entry_ts": ts, "qty": qty, "notional": notional,
               "tp_pct": cfg["tp_pct"] if tp_pct is None else tp_pct,
               "entry_fee": fee, "reason": reason}
        if basket is not None:
            pos["position_id"] = state.setdefault("next_position_id", 1)
            state["next_position_id"] += 1
            pos["basket_id"] = basket["id"]
            pos["grid_layer"] = layer
        pos["stop_price"], pos["target_price"] = _levels(pos, cfg)
        if basket is not None and basket.get("coverage_active") and layer and layer > 1:
            proposed = _covered_target(cfg, basket, sign, layer - 1)
            prior = pos.get("target_price")
            if proposed is not None and (prior is None or
                    (sign > 0 and proposed > prior) or (sign < 0 and proposed < prior)):
                pos["target_price"] = proposed
        state["positions"].append(pos)
        entry_reason = reason if cfg["execution_model"] == "signal-layers" else reason
        event = {"ts": ts, "side": "buy" if sign > 0 else "sell", "kind": "entry",
                 "direction": direction, "price": fill, "qty": qty, "fee": fee,
                 "entry_ts": ts, "pnl": None, "reason": entry_reason}
        if basket is not None:
            event.update({"position_id": pos["position_id"], "basket_id": basket["id"],
                          "grid_layer": layer})
        events.append(event)
        return pos

    if trade:
        # Only open-gap exits are known before orders sized at this open.
        survivors = []
        for pos in state["positions"]:
            sign = pos["direction_sign"]
            stop, target = _levels(pos, cfg)
            if (sign > 0 and o <= stop) or (sign < 0 and o >= stop):
                exit_position(pos, o, "sl_gap")
            elif target is not None and ((sign > 0 and o >= target) or (sign < 0 and o <= target)):
                exit_position(pos, o, "tp_gap")
            else:
                survivors.append(pos)
        state["positions"] = survivors

        if cfg["execution_model"] == "price-grid" and state.get("basket") is not None:
            basket = state["basket"]
            if not state["positions"]:
                state["basket"] = None
            elif basket.get("next_grid_price") is not None and basket.get("layers", 1) < cfg["grid_lines"]:
                sign = 1 if basket["direction"] == "long" else -1
                level = basket["next_grid_price"]
                raw_add = None
                if sign > 0:
                    if o <= level:
                        raw_add = o
                    elif l <= level:
                        raw_add = level
                else:
                    if o >= level:
                        raw_add = o
                    elif h >= level:
                        raw_add = level
                if raw_add is not None:
                    layer = basket["layers"] + 1
                    pos = enter_position(basket["direction"], raw_add,
                                         f"grid_add_layer_{layer}", basket=basket,
                                         layer=layer, tp_pct=cfg["tp_pct"])
                    if pos is not None:
                        pos["grid_fill_ts"] = ts
                        basket["layers"] = layer
                        if layer < cfg["grid_lines"]:
                            interval = _grid_spacing(cfg, basket, layer + 1) / 100.0
                            basket["next_grid_price"] = pos["entry_price"] * (1.0 - interval if sign > 0 else 1.0 + interval)
                        else:
                            basket["next_grid_price"] = None

        # Execute only signals queued by a previous completed bar.
        pending, state["pending"] = state["pending"], []
        filled_single_position = bool(state["positions"])
        for order in pending:
            if cfg["execution_model"] == "single-position" and (state["positions"] or filled_single_position):
                continue
            if cfg["execution_model"] == "price-grid" and state.get("basket") is not None:
                continue
            direction = order["direction"]
            # Tier 2 uses a configurable multiple of the signal candle's BB half-width.
            tp = cfg["tp_pct"] if order["streak"] <= 1 else order["band_pct"] * cfg["tier2_mult"]
            reason = f"{order['reason']}_tier{2 if order['streak'] > 1 else 1}"
            basket = None
            layer = None
            if cfg["execution_model"] == "price-grid":
                basket = {"id": state.setdefault("next_basket_id", 1), "direction": direction,
                          "layers": 1, "realized_net": 0.0, "band_pct": order.get("band_pct", 0.0),
                          "spacing_phase": "initial", "coverage_active": False}
                state["next_basket_id"] += 1
                state["basket"] = basket
                layer = 1
                reason = f"grid_seed_{order['reason']}"
            pos = enter_position(direction, o, reason, basket=basket, layer=layer, tp_pct=tp)
            if pos is not None and basket is None:
                # Legacy checkpoints retain the unsuffixed source reason on lots.
                pos["reason"] = order["reason"]
            if basket is not None and pos is not None and cfg["grid_lines"] > 1:
                interval = _grid_spacing(cfg, basket, 2) / 100.0
                sign = pos["direction_sign"]
                basket["next_grid_price"] = pos["entry_price"] * (1.0 - interval if sign > 0 else 1.0 + interval)
            if cfg["execution_model"] == "single-position":
                filled_single_position = True

        # All lots, old and new, now face intrabar ranges. Exit proceeds cannot
        # affect this candle's already-completed open sizing.
        survivors = []
        for pos in state["positions"]:
            sign = pos["direction_sign"]
            stop, target = _levels(pos, cfg)
            if (sign > 0 and l <= stop) or (sign < 0 and h >= stop):
                exit_position(pos, stop, "sl")
            elif (target is not None and pos.get("grid_fill_ts") != ts and
                  ((sign > 0 and h >= target) or (sign < 0 and l <= target))):
                exit_position(pos, target, "tp")
            else:
                survivors.append(pos)
        state["positions"] = survivors

        # Coverage can only switch an active basket. Targets are repriced after
        # this candle's exits so the new levels cannot fill on the same candle.
        basket = state.get("basket")
        if cfg["execution_model"] == "price-grid" and basket is not None:
            if not state["positions"]:
                state["basket"] = None
            else:
                basket["band_pct"] = band_pct or basket.get("band_pct", 0.0)
                risk = _basket_risk(state)
                if (not basket.get("coverage_active") and basket["realized_net"] > 0 and
                        basket["realized_net"] >= risk):
                    basket["coverage_active"] = True
                    basket["coverage_ts"] = ts
                    basket["coverage_close"] = c
                    mode = cfg["grid_after_exits"]
                    if mode != "unchanged":
                        active = sorted(state["positions"], key=lambda p: p.get("grid_layer", 1))
                        sign = active[0]["direction_sign"]
                        for idx, pos in enumerate(active, start=1):
                            proposed = _covered_target(cfg, basket, sign, idx)
                            prior = pos.get("target_price")
                            if proposed is not None and (prior is None or
                                    (sign > 0 and proposed > prior) or (sign < 0 and proposed < prior)):
                                pos["target_price"] = proposed
                                pos["stop_price"] = _levels(pos, cfg)[0]

    signal = None
    regime = None
    if sma is not None and len(state["history"]) >= warmup:
        lookback = cfg["trend_lookback"]
        period = cfg["trend_period"]
        rising = sma > sum(state["history"][-(period + lookback):-lookback]) / period
        regime = "rising" if rising else "non-rising"
        if cfg["signal_model"] == "sma-cross":
            previous_sma = sum(state["history"][-(period + 1):-1]) / period
            long_signal = state["history"][-2] <= previous_sma and c > sma
            short_signal = state["history"][-2] >= previous_sma and c < sma
            long_reason, short_reason = "sma_cross_up", "sma_cross_down"
        elif cfg["signal_model"] == "custom":
            prices = {"open": o, "high": h, "low": l, "close": c}

            def matches(side: str) -> bool:
                level_name = cfg[f"{side}_level"]
                level = {"lower": lower, "upper": upper, "sma": sma}[level_name]
                if level is None:
                    return False
                op = cfg[f"{side}_op"]
                price = prices[cfg[f"{side}_price"]]
                price_match = price <= level if op == "<=" else price >= level
                trend = cfg[f"{side}_trend"]
                trend_match = (trend == "any" or
                               (trend == "rising" and rising) or
                               (trend == "non-rising" and not rising))
                return price_match and trend_match

            long_signal = matches("bull")
            short_signal = matches("bear")
            long_reason, short_reason = "custom_bull", "custom_bear"
        else:
            # Wick-grid also requires the Bollinger window to be warm.
            long_signal = upper is not None and lower is not None and l <= lower and rising
            short_signal = upper is not None and lower is not None and h >= upper and not rising
            long_reason, short_reason = "lower_wick", "upper_wick"
        eligible = long_signal or short_signal
        signal = "long" if long_signal else ("short" if short_signal else None)
        if trade:
            state["signal_streak"] = state["signal_streak"] + 1 if eligible else 0
        can_signal_entry = (cfg["execution_model"] == "signal-layers" or
                            (cfg["execution_model"] == "single-position" and not state["positions"]) or
                            (cfg["execution_model"] == "price-grid" and not state["positions"] and
                             state.get("basket") is None))
        if trade and eligible and can_signal_entry:
            direction = "long" if cfg["direction"] == "legacy-long" else signal
            state["pending"].append({"direction": direction, "streak": state["signal_streak"],
                                     "band_pct": band_pct or 0.0,
                                     "reason": long_reason if long_signal else short_reason})
    elif trade:
        state["signal_streak"] = 0

    equity, unrealized = _equity(state, c)
    state["peak_equity"] = max(state["peak_equity"], equity)
    dd = (state["peak_equity"] - equity) / state["peak_equity"] if state["peak_equity"] else 0.0
    state["max_drawdown"] = max(state["max_drawdown"], dd)
    state["last_ts"] = ts
    point = {"ts": ts, "close": c, "equity": equity, "sma": sma, "upper": upper,
             "lower": lower, "regime": regime, "signal": signal, "events": events}
    if cfg["execution_model"] != "signal-layers":
        basket = state.get("basket")
        if basket is not None:
            risk = _basket_risk(state)
            point["grid"] = {"basket_id": basket["id"], "direction": basket["direction"],
                             "layers": basket["layers"], "max_layers": cfg["grid_lines"],
                             "realized_net": basket["realized_net"], "stop_risk": risk,
                             "risk_covered": bool(basket.get("coverage_active")),
                             "next_add_price": basket.get("next_grid_price"),
                             "spacing_mode": cfg["grid_spacing"],
                             "spacing_phase": "covered" if basket.get("coverage_active") else "initial",
                             "next_add_fill_rule": "open if gapped through level; otherwise level on touch; adverse slippage applies",
                             "targets": [{"position_id": p.get("position_id"), "price": p.get("target_price")}
                                         for p in state["positions"] if p.get("basket_id") == basket["id"]]}
        else:
            point["grid"] = None
    return point


def summary(state: dict) -> dict:
    """Return current account, trade, fee, and drawdown statistics."""
    mark = state["history"][-1] if state["history"] else state["config"]["capital"]
    equity, unrealized = _equity(state, mark)
    trades = state["closed_trades"]
    return {
        "equity": equity,
        "return_pct": (equity / state["config"]["capital"] - 1.0) * 100.0,
        "drawdown_pct": state["max_drawdown"] * 100.0,
        "closed_trades": trades,
        "win_rate": state["wins"] / trades * 100.0 if trades else 0.0,
        "open_positions": len(state["positions"]),
        "fees": state["fees"],
        "cash": state["cash"],
        "realized_pnl": state["realized_pnl"],
        "unrealized_pnl": unrealized,
    }


def open_lots(state: dict) -> list[dict]:
    """Describe open lots using the most recent close as their mark."""
    mark = state["history"][-1] if state["history"] else state["config"]["capital"]
    # File strategies can replace explicit next-bar limit exits at each close.
    exit_limits = {order.get("lot_id", order.get("position_id")): order["price"]
                   for order in state.get("pending", [])
                   if order.get("action") == "exit" and order.get("type") == "limit" and "price" in order}
    result = []
    for pos in state["positions"]:
        sign = pos["direction_sign"]
        stop, target = _levels(pos, state["config"])
        lot = {
            "direction": pos["direction"],
            "entry_ts": pos.get("entry_ts"),
            "entry_price": pos["entry_price"],
            "qty": pos["qty"],
            "entry_fee": pos["entry_fee"],
            "stop": stop,
            "target": target if target is not None else exit_limits.get(pos.get("lot_id")),
            "unrealized_pnl": sign * (mark - pos["entry_price"]) * pos["qty"] - pos["entry_fee"],
        }
        if pos.get("targets"):
            lot["targets"] = [dict(level) for level in pos["targets"] if not level.get("filled")]
            if lot["targets"]:
                lot["target"] = lot["targets"][0]["price"]
        if pos.get("lot_id") is not None or pos.get("position_id") is not None:
            lot.update({"lot_id": pos.get("lot_id", pos.get("position_id")),
                        "position_id": pos.get("position_id", pos.get("lot_id"))})
        if pos.get("basket_id") is not None:
            lot.update({"basket_id": pos["basket_id"], "grid_layer": pos.get("grid_layer"),
                        "reason": pos.get("reason")})
        result.append(lot)
    return result
