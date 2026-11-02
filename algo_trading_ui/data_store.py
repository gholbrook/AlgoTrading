"""In-memory store for asset data and backtest results."""

from typing import Optional
import pandas as pd
import vectorbt as vbt
from .models import AssetPair, TradeRecord, BacktestSummary


class DataStore:
    """Thread-safe in-memory store mapping asset symbols to OHLCV data and trades."""

    def __init__(self) -> None:
        self._assets: dict[str, dict] = {}  # symbol -> {df, timeframe, start, end}
        self._backtests: dict[str, vbt.Portfolio] = {}  # symbol -> Portfolio

    # -- Asset data management --

    def add_asset(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str,
        start_date: str,
        end_date: str,
    ) -> None:
        """Register a new asset pair's OHLCV data."""
        self._assets[symbol] = {
            "df": df,
            "timeframe": timeframe,
            "start": start_date,
            "end": end_date,
        }

    def get_asset_df(self, symbol: str) -> Optional[pd.DataFrame]:
        """Return the OHLCV DataFrame for *symbol*, or None."""
        entry = self._assets.get(symbol)
        return entry["df"] if entry else None

    def list_symbols(self) -> list[str]:
        """Return all registered asset symbols, sorted."""
        return sorted(self._assets.keys())

    def get_asset_info(self, symbol: str) -> Optional[dict]:
        """Return metadata dict for *symbol*."""
        entry = self._assets.get(symbol)
        if entry is None:
            return None
        return {
            "symbol": symbol,
            "timeframe": entry["timeframe"],
            "start_date": entry["start"],
            "end_date": entry["end"],
        }

    # -- Backtest / portfolio management --

    def register_backtest(self, symbol: str, portfolio: vbt.Portfolio) -> None:
        """Associate a backtest Portfolio with an asset symbol."""
        self._backtests[symbol] = portfolio

    def get_portfolio(self, symbol: str) -> Optional[vbt.Portfolio]:
        """Return the registered Portfolio for *symbol*, or None."""
        return self._backtests.get(symbol)

    def has_backtest_for(self, symbol: str) -> bool:
        return symbol in self._backtests

    # -- Trade extraction helpers --

    @staticmethod
    def extract_trades(portfolio: vbt.Portfolio) -> list[TradeRecord]:
        """Pull individual trade records from a Portfolio object.

        Uses the vectorbt trades.records DataFrame which contains raw columns:
        entry_idx, exit_idx, entry_price, exit_price, pnl, direction, status.
        """
        rec = portfolio.trades.records
        records: list[TradeRecord] = []

        # Build a lookup from index position to timestamp using the portfolio's close_prices index
        price_index = portfolio.close.index
        idx_map: dict[int, str] = {}
        for pos in range(len(price_index)):
            idx_map[pos] = str(price_index[pos])

        for i in range(len(rec)):
            row = rec.iloc[i]
            entry_idx_val = row["entry_idx"] if "entry_idx" in rec.columns else None
            exit_idx_val = row["exit_idx"] if "exit_idx" in rec.columns else None
            entry_price = float(row["entry_price"]) if pd.notna(row.get("entry_price")) else 0.0
            exit_price = float(row["exit_price"]) if pd.notna(row.get("exit_price")) else None

            entry_time = idx_map.get(int(entry_idx_val), str(entry_idx_val)) if pd.notna(entry_idx_val) else None
            exit_time = idx_map.get(int(exit_idx_val), str(exit_idx_val)) if pd.notna(exit_idx_val) else None

            pnl_val = float(row["pnl"]) if pd.notna(row.get("pnl")) else None
            direction_val = int(row["direction"]) if pd.notna(row.get("direction")) else 1

            records.append(TradeRecord(
                entry_idx=None if pd.isna(entry_idx_val) else int(entry_idx_val),
                exit_idx=None if pd.isna(exit_idx_val) else int(exit_idx_val),
                entry_price=round(entry_price, 8),
                exit_price=round(exit_price, 8) if exit_price is not None else None,
                entry_time=entry_time or "",
                exit_time=exit_time,
                pnl=round(pnl_val, 4) if pnl_val is not None else None,
                is_long=direction_val == 1,
            ))

        return records

    @staticmethod
    def build_summary(portfolio: vbt.Portfolio) -> BacktestSummary:
        """Build a BacktestSummary from a Portfolio."""
        trades = portfolio.trades
        rec = trades.records
        total = len(rec)

        pnl_col = rec["pnl"] if "pnl" in rec.columns else pd.Series([], dtype=float)
        winning = int((pnl_col > 0).sum()) if len(pnl_col) > 0 else 0
        losing = total - winning

        total_return = float(portfolio.total_return())
        max_dd = float(portfolio.max_drawdown())

        sharpe = None
        try:
            sr = portfolio.sharpe_ratio()
            if sr is not None and not pd.isna(sr):
                sharpe = round(float(sr), 4)
        except Exception:
            pass

        return BacktestSummary(
            total_trades=total,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=round(winning / total * 100, 2) if total > 0 else 0.0,
            total_return_pct=round(total_return * 100, 4),
            max_drawdown_pct=round(max_dd * 100, 4),
            sharpe_ratio=sharpe,
        )
