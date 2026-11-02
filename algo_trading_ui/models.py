"""Pydantic models for the algo trading UI API."""

from pydantic import BaseModel
from typing import Optional


class AssetInfo(BaseModel):
    """Metadata for a single asset pair."""
    symbol: str
    timeframe: str
    start_date: str
    end_date: str


class TradeRecord(BaseModel):
    """A single trade entry/exit from a backtest."""
    entry_idx: Optional[int] = None
    exit_idx: Optional[int] = None
    entry_price: float
    exit_price: Optional[float] = None
    entry_time: str
    exit_time: Optional[str] = None
    pnl: Optional[float] = None
    is_long: bool


class BacktestSummary(BaseModel):
    """Aggregate stats from a portfolio backtest."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float] = None


class AssetPair(BaseModel):
    """An asset pair with its data and optional backtest results."""
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    trades: list[TradeRecord]
    summary: Optional[BacktestSummary] = None
