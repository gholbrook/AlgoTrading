"""Plotly candlestick chart builder with trade markers and layout controls."""

from typing import Optional
import numpy as np
import plotly.graph_objects as go
import pandas as pd


def build_candlestick_chart(
    df: pd.DataFrame,
    symbol: str,
    trades: Optional[list] = None,
    show_trades: bool = True,
) -> go.Figure:
    """Build an interactive candlestick figure with optional buy/sell trade markers.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with columns: open, high, low, close, volume.
        Index should be datetime.
    symbol : str
        Asset pair label (e.g. 'ETH/BTC').
    trades : list[TradeRecord] | None
        List of TradeRecord objects with entry_idx / exit_idx positions.
    show_trades : bool
        Whether to overlay buy/sell markers on the chart.

    Returns
    -------
    go.Figure
        A fully-configured Plotly figure with zoom/pan enabled.
    """
    fig = go.Figure()

    # -- Candlestick --
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="OHLC",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a",
        decreasing_fillcolor="#ef5350",
    ))

    # -- Volume bars (if present) --
    # if "volume" in df.columns:
    #     colors = ["#26a69a" if c >= o else "#ef5350"
    #               for o, c in zip(df["open"], df["close"])]
    #     fig.add_trace(go.Bar(
    #         x=df.index,
    #         y=df["volume"],
    #         name="Volume",
    #         marker_color=colors,
    #         opacity=0.3,
    #         yaxis="y2",
    #     ))

    # -- Moving Average (200-period SMA) --
    if len(df) >= 200:
        sma = df["close"].rolling(window=200).mean()
        fig.add_trace(go.Scatter(
            x=df.index,
            y=sma,
            mode="lines",
            name="SMA (200)",
            line=dict(color="#f59e0b", width=1.5),
        ))

    # -- Trade markers --
    if show_trades and trades:
        entry_times = []
        exit_times = []
        entry_prices = []
        exit_prices = []

        for t in trades:
            entry_idx = t.entry_idx if hasattr(t, 'entry_idx') else t.get('entry_idx')
            exit_idx = t.exit_idx if hasattr(t, 'exit_idx') else t.get('exit_idx')
            entry_price = t.entry_price if hasattr(t, 'entry_price') else t.get('entry_price')
            exit_price = t.exit_price if hasattr(t, 'exit_price') else t.get('exit_price')

            # Try timestamp-based lookup first (for JSON-loaded trades with time data)
            entry_time = getattr(t, 'entry_time', None) or (t.get('entry_time') if isinstance(t, dict) else None)
            exit_time = getattr(t, 'exit_time', None) or (t.get('exit_time') if isinstance(t, dict) else None)

            # Use timestamp matching when available and index-based as fallback
            def find_bar_index(time_str):
                """Find the closest bar index by timestamp."""
                if time_str:
                    try:
                        target = pd.Timestamp(time_str)
                        csv_index = df.index
                        if target.tzinfo is not None:
                            target = target.tz_localize(None)
                        if csv_index.tzinfo is not None:
                            csv_index = csv_index.tz_localize(None)
                        # Use total_seconds() for correct diff calculation
                        diffs_s = np.abs((csv_index - target).total_seconds())
                        min_pos = int(np.argmin(diffs_s))
                        if diffs_s[min_pos] < 1800:  # 30 minutes in seconds
                            return min_pos
                    except Exception:
                        pass
                return None

            entry_pos = find_bar_index(entry_time) if entry_time else (int(entry_idx) if entry_idx is not None and entry_idx < len(df) else None)
            exit_pos = find_bar_index(exit_time) if exit_time else (int(exit_idx) if exit_idx is not None and exit_idx < len(df) else None)

            if entry_pos is not None:
                entry_times.append(entry_pos)
                entry_prices.append(entry_price)

            if exit_pos is not None:
                exit_times.append(exit_pos)
                exit_prices.append(exit_price or 0)

        # Entry markers (green upward triangles for longs, red downward for shorts)
        if entry_times:
            entry_x = df.index[entry_times]
            entry_y = df.iloc[entry_times]["low"].values.copy().astype(float)
            # Place entries slightly below the low for visibility
            entry_y = [max(0, y - (y * 0.002)) for y in entry_y]

            fig.add_trace(go.Scatter(
                x=entry_x,
                y=entry_y,
                mode="markers",
                name="Long Entry",
                marker=dict(symbol="arrow-up", size=10, color="#26a69a", line_width=1),
                hovertemplate="<b>Entry</b><br>Time: %{x}<br>Price: %{y:.8f}<extra></extra>",
            ))

        # Exit markers
        if exit_times:
            exit_x = df.index[exit_times]
            exit_y = df.iloc[exit_times]["high"].values.copy().astype(float)
            # Place exits slightly above the high for visibility
            exit_y = [y + (y * 0.002) for y in exit_y]

            fig.add_trace(go.Scatter(
                x=exit_x,
                y=exit_y,
                mode="markers",
                name="Exit",
                marker=dict(symbol="arrow-down", size=10, color="#ef5350", line_width=1),
                hovertemplate="<b>Exit</b><br>Time: %{x}<br>Price: %{y:.8f}<extra></extra>",
            ))

    # -- Layout configuration --
    fig.update_layout(
        title=f"{symbol} — Candlestick Chart",
        title_x=0.5,
        xaxis_rangeslider_visible=False,
        xaxis=dict(
            type="date",
            gridcolor="rgba(128,128,128,0.15)",
            zerolinecolor="rgba(128,128,128,0.3)",
        ),
        yaxis=dict(
            title="Price",
            gridcolor="rgba(128,128,128,0.15)",
            zerolinecolor="rgba(128,128,128,0.3)",
            tickprefix="$" if symbol.startswith("$") else "",
            fixedrange=False,
        ),
        yaxis2=dict(
            title="Volume",
            overlaying="y",
            side="right",
            range=[0, 1],
            showgrid=False,
            zeroline=False,
            fixedrange=True,
            anchor="x",
            matches=None,
        ),
        dragmode="zoom",
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=60, r=30, t=60, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )

    return fig


def build_empty_chart(symbol: str) -> go.Figure:
    """Return a placeholder chart when no data is loaded for *symbol*."""
    fig = go.Figure()
    fig.add_annotation(
        text=f"No data loaded for <b>{symbol}</b>",
        xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=20),
    )
    fig.update_layout(
        title=f"{symbol} — No Data",
        title_x=0.5,
        dragmode="zoom",
        template="plotly_white",
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig
