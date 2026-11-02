import vectorbt as vbt
import pandas as pd
import numpy as np

# ==========================================
# 1. DOWNLOAD HIGH-RESOLUTION DATA FROM KRAKEN
# ==========================================
print("Pulling high-resolution historical data via CCXT wrapper...")
market_symbol = 'ETH/BTC'

# FIX 1: Swapped to capital 'min' to prevent the Pandas 3.1.0 token parser crash
timeframe = '5min'
start_time = "2026-10-01 UTC"
end_time = "2026-11-01 UTC"

try:
    # FIX 2: Corrected VectorBT component name to CCXTData mapping Kraken parameters
    data = vbt.CCXTData.download(
        market_symbol,
        start=start_time,
        end=end_time,
        timeframe=timeframe,
        exchange='kraken'
    )
    df = data.get()
except Exception as e:
    print(f"Direct download failed: {e}. Falling back to clean offline high-frequency simulation...")
    # FIX 3: Updated string frequency marker '5m' to '5min' to satisfy Pandas 3.1.0 rules
    index = pd.date_range(start="2026-10-01", end="2026-11-01", freq="5min")
    df = pd.DataFrame(index=index)
    df['close'] = np.sin(np.linspace(0, 500, len(index))) * 0.003 + 0.07
    df['open'] = df['close'].shift(1).fillna(0.07)
    df['high'] = df['close'] + 0.0002
    df['low'] = df['close'] - 0.0002

close_prices = df['close']
high_prices = df['high']
low_prices = df['low']
open_prices = df['open']

# ==========================================
# 2. CALIBRATE HIGH-FREQUENCY INDICATORS
# ==========================================
regime_sma = vbt.MA.run(close_prices, window=200).ma
sma_is_rising = regime_sma > regime_sma.shift(5)

bb = vbt.BBANDS.run(close_prices, window=20, alpha=2.0)
bb_lower = bb.lower
bb_basis = bb.middle
bb_upper = bb.upper

# Relaxed trigger logic: Fires when wicks actively touch range limits
wick_touch_entry = (low_prices <= bb_lower) & sma_is_rising
wick_touch_short = (high_prices >= bb_upper) & (~sma_is_rising)

all_strategy_entries = wick_touch_entry | wick_touch_short

# ==========================================
# 3. EXPANDING ASYMMETRIC GRID MATH
# ==========================================
base_band_height = ((bb_upper - bb_basis) / bb_basis)

tp_tier_1 = 0.0045 # Micro-churn layer to clear fees quickly
tp_tier_2 = base_band_height * 1.5 # Expansion layer to let winners run

consecutive_entries = all_strategy_entries.groupby((~all_strategy_entries).cumsum()).cumsum()
tp_target_matrix = np.where(consecutive_entries <= 1, tp_tier_1, tp_tier_2)
tp_target_matrix = np.where(all_strategy_entries, tp_target_matrix, 0.015)

# Hard stop loss protection matrix
sl_target_matrix = np.where(all_strategy_entries, 0.020, np.nan)

# ==========================================
# 4. PORTFOLIO MATRIX EXECUTION
# ==========================================
print(f"Simulating high-turnover grid matrix across {len(close_prices)} historical bars...")
portfolio = vbt.Portfolio.from_signals(
    close=close_prices,
    entries=all_strategy_entries,
    exits=None,
    tp_stop=tp_target_matrix,
    sl_stop=sl_target_matrix,
    init_cash=50000,
    fees=0.0020,               # Kraken Tier 4 Maker Fee
    size=0.02,                 # Safe 2% capital layer allocation
    size_type='percent',
    accumulate=True
)

print("\n" + "="*40)
print("     HIGH-FREQUENCY ASYMMETRIC GRID RESULTS     ")
print("="*40)
print(portfolio.stats())
