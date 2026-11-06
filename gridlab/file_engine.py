"""Execution contract for trusted local Python strategy files.

Strategies receive a plain ``ctx`` dictionary and return next-bar orders. A
strategy file is arbitrary Python and is therefore intended only for code the
local operator trusts; there is deliberately no sandbox.

Order forms:

* ``{"type":"market", "direction":"long", "notional":500}``
* ``{"type":"limit", "direction":"short", "price":101, "notional":500}``
* ``{"type":"market", "action":"exit", "lot_id":2}``

Entries may optionally set absolute ``stop_price`` / ``target_price`` or
``stop_pct`` / ``target_pct``. Alternatively ``target_steps_pct=[1,2,3,4]``
attaches equal quantity profit targets relative to the actual entry fill. Its
strictly ascending percentages cannot be combined with a single target. Open
lots retain a ``targets`` list, with remaining target quantities and stable
``target_id`` / ``label`` values. An explicit exit may set ``qty`` to close part
of the lot; its remaining targets shrink proportionally. Every filled lot receives a unique integer
``lot_id``. Entry and exit fills are carried on the returned engine point's
``events``; strategy exits use reason ``strategy_exit``, protective exits use
``stop`` and ``target``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import pstdev
from types import MappingProxyType
from typing import Any

MAX_CONTEXT_BARS = 10_000


@dataclass(frozen=True)
class Program:
    filename: str
    name: str
    source: str
    source_hash: str
    settings: dict
    warmup: int
    on_bar: Any
    chart_indicators: Any


def sma(values, period):
    """Simple moving average of the last *period* values, or None if unready."""
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer")
    if len(values) < period:
        return None
    return sum(float(x) for x in values[-period:]) / period


def bollinger(values, period=20, deviations=2.0):
    """Return ``(middle, upper, lower)`` with population standard deviation."""
    middle = sma(values, period)
    if middle is None:
        return None, None, None
    sample = [float(x) for x in values[-period:]]
    width = float(deviations) * pstdev(sample)
    return middle, middle + width, middle - width


def ema(values, period):
    """Return the last exponential moving average, or None if unready."""
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer")
    if len(values) < period:
        return None
    out = sum(float(x) for x in values[:period]) / period
    alpha = 2.0 / (period + 1)
    for value in values[period:]:
        out += alpha * (float(value) - out)
    return out


def _read_source(source: str | Path) -> tuple[str, str]:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8"), source.name
    if not isinstance(source, str):
        raise TypeError("strategy source must be Python text or a file path")
    # A simple existing path is accepted for local callers; API configs normally
    # persist the exact source text alongside its display filename.
    if "\n" not in source and "\r" not in source:
        try:
            candidate = Path(source)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8"), candidate.name
        except OSError:
            pass
    return source, "<strategy>"


@lru_cache(maxsize=64)
def _compiled_code(source: str, filename: str):
    return compile(source, filename, "exec")


def _instantiate(source: str, filename: str) -> Program:
    namespace = {
        "__name__": "gridlab_local_strategy",
        "__file__": filename,
        "sma": sma,
        "ema": ema,
        "bollinger": bollinger,
    }
    try:
        code = _compiled_code(source, filename)
        exec(code, namespace, namespace)  # Trusted local code.
    except Exception as exc:
        raise ValueError(f"{filename} could not load: {exc}") from exc
    callback = namespace.get("on_bar")
    if not callable(callback):
        raise ValueError("strategy file must define callable on_bar(ctx)")
    name = namespace.get("NAME", Path(filename).stem if filename != "<strategy>" else "Local strategy")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("NAME must be a non-empty string")
    settings = namespace.get("SETTINGS", {})
    if not isinstance(settings, dict):
        raise ValueError("SETTINGS must be a dictionary")
    try:
        settings = json.loads(json.dumps(settings, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SETTINGS must contain JSON-compatible values: {exc}") from exc
    warmup = namespace.get("WARMUP", 0)
    if isinstance(warmup, bool) or not isinstance(warmup, int) or not 0 <= warmup <= 719:
        raise ValueError("WARMUP must be an integer from 0 to 719")
    plot_callback = namespace.get("chart_indicators")
    if plot_callback is not None and not callable(plot_callback):
        raise ValueError("chart_indicators must be a function")
    return Program(filename, name.strip(), source, hashlib.sha256(source.encode()).hexdigest(),
                   settings, warmup, callback, plot_callback)


def load_program(source: str | Path) -> Program:
    """Compile trusted source and return its metadata and ``on_bar`` callback."""
    code, filename = _read_source(source)
    return _instantiate(code, filename)


class _ReadOnlyRecords(Sequence):
    """Read-only lazy view over candle and lot dictionaries."""
    def __init__(self, records):
        self._records = records

    def __len__(self):
        return len(self._records)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(MappingProxyType(record) for record in self._records[index])
        return MappingProxyType(self._records[index])

    def __iter__(self):
        for record in self._records:
            yield MappingProxyType(record)


def file_warmup(config: dict) -> int:
    """Return the strategy-declared warmup, if a program is configured."""
    spec = config.get("strategy_file") if isinstance(config, dict) else None
    if not spec:
        return 0
    if not isinstance(spec, dict):
        raise ValueError("strategy_file must contain filename and source")
    program = _instantiate(spec["source"], spec.get("filename", "<strategy>"))
    return program.warmup


def _program_for(state: dict) -> tuple[Program, dict]:
    config = state["config"]
    spec = config.get("strategy_file")
    if not isinstance(spec, dict):
        raise ValueError("strategy_file must contain filename and source")
    source = spec.get("source")
    if not isinstance(source, str):
        raise ValueError("strategy_file.source must be Python source text")
    filename = spec.get("filename", "<strategy>")
    if not isinstance(filename, str) or not filename:
        raise ValueError("strategy_file.filename must be a non-empty string")
    # Re-execute cached bytecode into a fresh namespace. Only ctx.memory is
    # persistent state; mutable globals cannot bleed across runs or audits.
    program = _instantiate(source, filename)
    overrides = config.get("strategy_settings", {})
    if not isinstance(overrides, dict):
        raise ValueError("strategy_settings must be a dictionary")
    settings = {**program.settings, **overrides}
    try:
        settings = json.loads(json.dumps(settings, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"strategy_settings must contain JSON-compatible values: {exc}") from exc
    return program, settings


def _number(value, label, *, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _side(direction):
    if direction not in ("long", "short"):
        raise ValueError("entry direction must be 'long' or 'short'")
    return 1 if direction == "long" else -1


def _exit(state, position, raw_price, ts, reason, events, *, limit_price=None, label=None, qty=None, target_id=None):
    cfg = state["config"]
    sign = position["direction_sign"]
    # Exit is the opposite side of the lot's entry and pays adverse slippage.
    fill = raw_price * (1.0 - cfg["slip_bps"] / 10000.0 if sign > 0
                       else 1.0 + cfg["slip_bps"] / 10000.0)
    if limit_price is not None:
        fill = max(fill, limit_price) if sign > 0 else min(fill, limit_price)
    old_qty = position["qty"]
    quantity = old_qty if qty is None else min(_number(qty, "exit qty"), old_qty)
    fraction = quantity / old_qty
    principal = position["notional"] * fraction
    entry_fee = position["entry_fee"] * fraction
    fee = fill * quantity * cfg["fee_bps"] / 10000.0
    gross = sign * (fill - position["entry_price"]) * quantity
    net = gross - entry_fee - fee
    state["cash"] += principal + gross - fee
    state["fees"] += fee
    state["realized_pnl"] += net
    state["closed_trades"] += 1
    state["wins"] += int(net > 0)
    position["exit_count"] = position.get("exit_count", 0) + 1
    events.append({"ts": ts, "kind": "exit", "side": "sell" if sign > 0 else "buy",
                   "direction": position["direction"], "lot_id": position["lot_id"],
                   "position_id": position["lot_id"], "entry_ts": position["entry_ts"],
                   "price": fill, "qty": quantity, "fee": fee, "pnl": net,
                   "reason": reason, "label": label})
    if position.get("targets") or qty is not None:
        events[-1].update(target_id=target_id,
                          event_id=f"{position['lot_id']}:exit:{position['exit_count']}")
    position["qty"] = max(0.0, old_qty - quantity)
    position["notional"] -= principal
    position["entry_fee"] -= entry_fee
    if target_id is None:
        active = [t for t in position.get("targets", []) if t["qty"] > 0]
        remaining = position["qty"]
        for i, target in enumerate(active):
            target["qty"] = remaining if i == len(active) - 1 and not position.get("has_runner") else target["qty"] * (1 - fraction)
            remaining -= target["qty"]


def _protect(state, position, bar, events, *, at_open=False):
    trigger = _stop_target(state, position, bar, events, at_open=at_open)
    if trigger:
        reason, price = trigger
        _exit(state, position, price, bar["ts"], reason, events,
              limit_price=price if reason == "target" else None)
        return False
    if not at_open and position.get("intrabar_limit_ts") == bar["ts"]:
        return True
    sign = position["direction_sign"]
    for target in position.get("targets", []):
        price = target["price"]
        touched = (bar["open"] >= price if sign > 0 else bar["open"] <= price) if at_open else (
            bar["high"] >= price if sign > 0 else bar["low"] <= price)
        if target["qty"] > 0 and touched:
            quantity = min(position["qty"], target["qty"])
            target["qty"] = 0.0
            target["filled"] = True
            _exit(state, position, bar["open"] if at_open else price,
                  bar["ts"], "target", events, limit_price=price,
                  label=target["label"], qty=quantity, target_id=target["target_id"])
    return position["qty"] > 0


def _stop_target(state, position, bar, events, *, at_open=False):
    sign = position["direction_sign"]
    o, h, l = bar["open"], bar["high"], bar["low"]
    stop, target = position.get("stop_price"), position.get("target_price")
    if sign > 0:
        if stop is not None and (o <= stop if at_open else l <= stop):
            return "stop", o if at_open and o <= stop else stop
        if target is not None and (o >= target if at_open else h >= target):
            return "target", o if at_open and o >= target else target
    else:
        if stop is not None and (o >= stop if at_open else h >= stop):
            return "stop", o if at_open and o >= stop else stop
        if target is not None and (o <= target if at_open else l <= target):
            return "target", o if at_open and o <= target else target
    return None


def _entry_levels(order, direction, fill):
    sign = _side(direction)
    stop = order.get("stop_price")
    target = order.get("target_price")
    if stop is not None:
        stop = _number(stop, "stop_price")
    if target is not None:
        target = _number(target, "target_price")
    if order.get("stop_pct") is not None:
        pct = _number(order["stop_pct"], "stop_pct")
        if pct >= 100:
            raise ValueError("stop_pct must be below 100")
        stop = fill * (1.0 - pct / 100.0 if sign > 0 else 1.0 + pct / 100.0)
    if order.get("target_pct") is not None:
        pct = _number(order["target_pct"], "target_pct")
        if pct >= 100:
            raise ValueError("target_pct must be below 100")
        target = fill * (1.0 + pct / 100.0 if sign > 0 else 1.0 - pct / 100.0)
    if stop is not None and (stop >= fill if sign > 0 else stop <= fill):
        raise ValueError("stop must be below a long entry or above a short entry")
    if target is not None and (target <= fill if sign > 0 else target >= fill):
        raise ValueError("target must be above a long entry or below a short entry")
    return stop, target


def _fill_entry(state, order, bar, events):
    order_type = order.get("type", order.get("order_type", "market"))
    direction = order.get("direction")
    sign = _side(direction)
    o, h, l = bar["open"], bar["high"], bar["low"]
    intrabar_limit = False
    if order_type == "market":
        raw_price = o
    elif order_type == "limit":
        limit = _number(order.get("price"), "limit price")
        if sign > 0:
            if o <= limit:
                raw_price = o
            elif l <= limit:
                raw_price = limit
                intrabar_limit = True
            else:
                return
        else:
            if o >= limit:
                raw_price = o
            elif h >= limit:
                raw_price = limit
                intrabar_limit = True
            else:
                return
    else:
        raise ValueError("entry type must be 'market' or 'limit'")

    cfg = state["config"]
    slip = cfg["slip_bps"] / 10000.0
    fill = raw_price * (1.0 + slip if sign > 0 else 1.0 - slip) if order_type == "market" else raw_price
    if "qty" in order and "notional" in order:
        raise ValueError("entry cannot specify both qty and notional")
    if "qty" in order:
        qty = _number(order["qty"], "qty")
        notional = qty * fill
    else:
        notional = _number(order["notional"], "notional") if "notional" in order else (
            state["cash"] * cfg["size_pct"] / 100.0)
        qty = notional / fill
    entry_fee = notional * cfg["fee_bps"] / 10000.0
    if notional + entry_fee > state["cash"] + max(1e-12, abs(state["cash"]) * 1e-12):
        return
    stop, target = _entry_levels(order, direction, fill)
    steps = order.get("target_steps_pct")
    targets = []
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise ValueError("target_steps_pct must be a non-empty ascending list")
        if target is not None:
            raise ValueError("target_steps_pct cannot be combined with target_price or target_pct")
        values = [_number(value, "target_steps_pct") for value in steps]
        if any(value >= 100 for value in values) or any(a >= b for a, b in zip(values, values[1:])):
            raise ValueError("target_steps_pct must be strictly ascending and below 100")
        remainder = qty
        for i, pct in enumerate(values):
            quantity = remainder if i == len(values) - 1 else qty / len(values)
            targets.append({"target_id": i + 1, "label": f"TP{i + 1}", "pct": pct,
                            "price": fill * (1 + sign * pct / 100),
                            "qty": quantity, "filled": False})
            remainder -= quantity
    if "recover_cost_pct" in order:
        if sign < 0 or steps is not None or target is not None:
            raise ValueError("recover_cost_pct requires a long entry with no other profit targets")
        pct = _number(order["recover_cost_pct"], "recover_cost_pct")
        price = fill * (1 + pct / 100)
        proceeds_per_unit = price * (1 - cfg["fee_bps"] / 10000)
        if qty * proceeds_per_unit <= notional + entry_fee:
            raise ValueError("Recovery price must cover entry and exit fees and leave a remainder")
        targets.append({"target_id": 1, "label": "Recover capital and fees", "pct": pct,
                        "price": price, "qty": (notional + entry_fee) / proceeds_per_unit,
                        "filled": False})
    state["cash"] -= notional + entry_fee
    state["fees"] += entry_fee
    lot_id = state.setdefault("next_position_id", 1)
    state["next_position_id"] += 1
    position = {
        "lot_id": lot_id, "position_id": lot_id, "direction": direction,
        "direction_sign": sign, "entry_price": fill, "entry_ts": bar["ts"],
        "qty": qty, "notional": notional, "entry_fee": entry_fee,
        "stop_price": stop, "target_price": target,
        # Keep legacy fields present for engine helpers that expect these keys.
        "tp_pct": 0.0,
        "intrabar_limit_ts": bar["ts"] if intrabar_limit else None,
    }
    if "recover_cost_pct" in order:
        position["has_runner"] = True
    if targets:
        position["targets"] = targets
    state["positions"].append(position)
    events.append({"ts": bar["ts"], "kind": "entry", "side": "buy" if sign > 0 else "sell",
                   "direction": direction, "lot_id": lot_id, "position_id": lot_id,
                   "entry_ts": bar["ts"], "price": fill, "qty": qty,
                   "fee": entry_fee, "pnl": None, "reason": "strategy_entry",
                   "label": order.get("label")})
    if targets:
        events[-1]["event_id"] = f"{lot_id}:entry"


def _validate_orders(orders):
    if not isinstance(orders, list):
        raise ValueError("on_bar(ctx) must return a list of orders")
    try:
        return json.loads(json.dumps(orders, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"strategy orders must be JSON-compatible: {exc}") from exc


def _apply_order(state, order, bar, events):
    if not isinstance(order, dict):
        raise ValueError("each strategy order must be a dictionary")
    action = order.get("action", "entry")
    order_type = order.get("type", order.get("order_type", "market"))
    if action == "exit":
        if order_type not in ("market", "limit"):
            raise ValueError("explicit exits must use type='market' or 'limit'")
        lot_id = order.get("lot_id", order.get("position_id"))
        if lot_id is None:
            raise ValueError("explicit exit requires lot_id")
        position = next((p for p in state["positions"] if p["lot_id"] == lot_id), None)
        if position is not None:
            raw_price = bar["open"]
            if order_type == "limit":
                limit = _number(order.get("price"), "exit limit price")
                if position["direction_sign"] > 0:
                    if bar["open"] >= limit:
                        raw_price = bar["open"]
                    elif bar["high"] >= limit:
                        raw_price = limit
                    else:
                        return  # one-bar limit order expires without a fill
                else:
                    if bar["open"] <= limit:
                        raw_price = bar["open"]
                    elif bar["low"] <= limit:
                        raw_price = limit
                    else:
                        return
            _exit(state, position, raw_price, bar["ts"], "strategy_exit", events,
                  limit_price=limit if order_type == "limit" else None,
                  label=order.get("label"), qty=order.get("qty"))
            if position["qty"] == 0:
                state["positions"].remove(position)
        return
    if action != "entry":
        raise ValueError("order action must be 'entry' or 'exit'")
    _fill_entry(state, order, bar, events)


def _mark(state, close, ts, events):
    unrealized = sum(p["direction_sign"] * (close - p["entry_price"]) * p["qty"] - p["entry_fee"]
                     for p in state["positions"])
    equity = state["config"]["capital"] + state["realized_pnl"] + unrealized
    peak = max(state.get("peak_equity", state["config"]["capital"]), equity)
    state["peak_equity"] = peak
    state["max_drawdown"] = max(state.get("max_drawdown", 0.0),
                                (peak - equity) / peak if peak else 0.0)
    return equity


def step_file(state: dict, bar: dict, trade: bool = True):
    """Advance a configured file-strategy run; return None if none is selected.

    The host engine should call this after validating OHLCV and before running
    built-in signal logic. This routine owns close history, queued orders,
    position accounting, and ``last_ts`` for file-backed runs.
    """
    config = state.get("config", {})
    if "strategy_file" not in config:
        return None
    required = ("ts", "open", "high", "low", "close", "volume")
    if any(key not in bar for key in required):
        raise ValueError("bar requires ts, open, high, low, close, volume")
    ts = bar["ts"]
    if state.get("last_ts") is not None and ts <= state["last_ts"]:
        raise ValueError("bar timestamps must increase")
    for key in required[1:]:
        _number(bar[key], key, positive=(key != "volume"))
    if bar["volume"] < 0 or bar["high"] < max(bar["open"], bar["close"], bar["low"]) or bar["low"] > min(bar["open"], bar["close"], bar["high"]):
        raise ValueError("invalid OHLCV bar")

    program, file_settings = _program_for(state)
    settings = file_settings
    closes = state.setdefault("history", [])
    closes.append(float(bar["close"]))
    warmup = max(program.warmup, int(config.get("bb_period", 20)),
                 int(config.get("trend_period", 200)) + int(config.get("trend_lookback", 5)))
    if len(closes) > max(1, warmup):
        del closes[:-max(1, warmup)]
    candle_history = state.setdefault("strategy_history", [])
    candle_history.append({key: (int(bar[key]) if key == "ts" else float(bar[key])) for key in required})
    if len(candle_history) > MAX_CONTEXT_BARS:
        del candle_history[:-MAX_CONTEXT_BARS]
    state.setdefault("strategy_memory", {})
    state.setdefault("positions", [])
    state.setdefault("pending", [])
    state.setdefault("fees", 0.0)
    state.setdefault("realized_pnl", 0.0)
    state.setdefault("closed_trades", 0)
    state.setdefault("wins", 0)
    state.setdefault("peak_equity", config.get("capital", 50000.0))
    state.setdefault("max_drawdown", 0.0)
    events = []
    strategy_ready = len(candle_history) >= program.warmup

    if trade:
        # Gap-through protective orders execute at the next bar's open.
        remaining = []
        for position in state["positions"]:
            if _protect(state, position, bar, events, at_open=True):
                remaining.append(position)
        state["positions"] = remaining

        pending, state["pending"] = state["pending"], []
        limit_exits = []
        for order in pending:
            if (isinstance(order, dict) and order.get("action") == "exit"
                    and order.get("type", order.get("order_type", "market")) == "limit"):
                lot_id = order.get("lot_id", order.get("position_id"))
                position = next((p for p in state["positions"] if p["lot_id"] == lot_id), None)
                limit = _number(order.get("price"), "exit limit price")
                marketable = (position is not None and
                              (bar["open"] >= limit if position["direction_sign"] > 0
                               else bar["open"] <= limit))
                if marketable:
                    _apply_order(state, order, bar, events)
                else:
                    limit_exits.append(order)
                continue
            _apply_order(state, order, bar, events)

        # Stops/targets crossed intrabar execute at their level. Stop wins if a
        # wide candle crosses both, matching the native engine's conservative rule.
        remaining = []
        for position in state["positions"]:
            if position.get("intrabar_limit_ts") == ts and not position.get("targets"):
                trigger = _stop_target(state, position, bar, events)
                if trigger and trigger[0] == "stop":
                    _exit(state, position, trigger[1], ts, "stop", events)
                else:
                    remaining.append(position)
            elif _protect(state, position, bar, events):
                remaining.append(position)
        state["positions"] = remaining
        # Protective stops/targets win any candle that also reaches a strategy
        # limit exit. Limit exits expire after this next candle if still unfilled.
        for order in limit_exits:
            _apply_order(state, order, bar, events)

    sma_value = sma(closes, int(config.get("trend_period", 200)))
    middle, upper, lower = bollinger(closes, int(config.get("bb_period", 20)),
                                     float(config.get("bb_mult", 2.0)))
    equity = _mark(state, float(bar["close"]), ts, events)
    state["last_ts"] = ts
    signal = None
    chart_ctx = {
        "bar": MappingProxyType(candle_history[-1]),
        "history": _ReadOnlyRecords(candle_history),
        "settings": MappingProxyType(settings),
        "indicators": {"sma": sma_value, "bb_middle": middle, "bb_upper": upper, "bb_lower": lower},
        "helpers": {"sma": sma, "ema": ema, "bollinger": bollinger},
    }
    plots = None
    if program.chart_indicators is not None:
        try:
            plots = program.chart_indicators(chart_ctx)
            if not isinstance(plots, dict):
                raise ValueError("expected a dictionary of line names and values")
            for name, value in plots.items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("each line needs a non-empty name")
                if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
                    raise ValueError(f"{name} must be a finite price or None")
        except Exception as exc:
            raise ValueError(f"{program.filename} chart_indicators failed at candle {ts}: {exc}") from exc
    if trade:
        ctx = {
            **chart_ctx,
            "positions": _ReadOnlyRecords(state["positions"]),
            "pending": _ReadOnlyRecords(state["pending"]),
            "cash": float(state["cash"]), "equity": equity,
            "realized_pnl": float(state["realized_pnl"]),
            "fee_bps": float(config["fee_bps"]), "slip_bps": float(config["slip_bps"]),
            "memory": state["strategy_memory"],
            "events": _ReadOnlyRecords(events),
            "plots": MappingProxyType(plots or {}),
        }
        if strategy_ready:
            try:
                orders = _validate_orders(program.on_bar(ctx))
            except Exception as exc:
                raise ValueError(f"{program.filename} on_bar failed at candle {ts}: {exc}") from exc
        else:
            orders = []
        # Copy memory through JSON to guarantee restart-safe persistent state.
        state["strategy_memory"] = json.loads(json.dumps(ctx["memory"], allow_nan=False))
        for order in orders:
            if isinstance(order, dict) and order.get("action", "entry") == "entry":
                signal = order.get("direction", signal)
        # Cancel resting profit limits at the signal close, before next-open gap checks.
        cancel_ids = {order["lot_id"] for order in orders if isinstance(order, dict)
                      and order.get("action") == "exit" and order.get("cancel_targets") is True
                      and order.get("type", "market") == "market" and "lot_id" in order}
        for lot in state["positions"]:
            if lot["lot_id"] in cancel_ids:
                lot["targets"] = []
                lot["target_price"] = None
        state["pending"] = orders
        submitted_orders = orders
    else:
        submitted_orders = []

    # Recalculate after the strategy call, which may only enqueue future orders.
    equity = _mark(state, float(bar["close"]), ts, events)
    return {"ts": ts, "close": float(bar["close"]), "equity": equity,
            "sma": sma_value, "upper": upper, "lower": lower,
            "regime": None, "signal": signal, "events": events,
            "strategy": program.name, "strategy_hash": program.source_hash,
            "strategy_warmup": program.warmup, "strategy_ready": strategy_ready,
            "orders": submitted_orders, **({"plots": plots} if plots is not None else {})}
