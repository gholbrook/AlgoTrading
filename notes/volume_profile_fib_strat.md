# Volume Profile Entry Grid w/ Fibonacci Exit

Copy this file for each trade decision. Fill in the strategy rules before the session. At decision time, follow one regime branch and record the action, including a decision to wait.

## Strategy rules

- Strategy and version: Volume Profile Entry Grid w/ Fibonacci Exit version 1
- Signal timeframe: 1 Hour · Regime timeframe: 200 Day
- Regime indicator, source pair, and units: [BTC 200 EMA Slope Indicator]((../pinescript/btc_200_ema_slope_indicator))
- Maximum loss per trade in quote currency: ___ · Maximum total exposure: ___
- Regime indicator scales directly with the level of reservation when placing grid lines.

Take moment to consider the three values in the regime indicator. We extrapolate five days into the future by assuming a continuation of the last five days. If our extrapolated value is less than the current, we can assume at least a sideways move in price action.

Use Fix Range Volume Profile indicator on a consolidation range of at least one week in a one-hour chart(how to codify this is an open question). This will produce two section, split across the price with the most volume. If this line is not sufficiently close to the middle of the upper and lower bounds of the two ranges, consider a different asset pair or time period. Upon finding a valid range, draw three horizontal lines on the chart to extend the levels indicated by the volume profile indefinitely. Draw a red line at the volume area high, an orange line on the point of control, and a green line on the volume area low. Favor the range that corresponds to the overall regime (upper sector for bullish regime, lower for bearish). Don't be afraid to draw the line at another support if it looks better than the volume profile. The 250 period moving average can serve as a replacement for the point of control line.

Use the Fib Retracement indicator to establish position and spacing of grid lines (This is to be refined. Codifying is open question). See [fibonacci_placement.md](fibonacci_placement.md) for guide on placing the Fibonacci retracement on your charts.

| Branch | Observable condition | Permitted setup and order action |
| --- | --- | --- |
| A · Bullish | ___ | ___ |
| B · Sideways | ___ | ___ |
| C · Bearish | ___ | ___ |
| D · Unclear, conflicting, or missing data | No single branch qualifies | Wait. Recheck when ___ |

## Bearish Regime

- Buy signal on the red line
  - Probably just disregard these.

- Buy signal on the orange line
  - Use smaller position and sell out early and don't anticipate adding additional grid lines, unless another signal is given

- Buy signal on the green line
  - Place buys lower with an increased distance between grid lines to reduce risk. Sell all at 50% retracement to the orange line. Do not hold surplus stock for additional gains. Can replace grid lines as they close.

## Bullish Regime

- Buy signal on the red line

- Buy signal on the orange line

- Buy signal on the green line

## Neutral Regime

- Buy signal on the red line

- Buy signal on the orange line

- Buy signal on the green line


## Before entry

- [ ] Record decision time (UTC): ___ · Venue: ___ · Pair: ___ · Mode: replay / paper / live
- [ ] Record the regime reading and its timestamp: ___
- [ ] Select one branch: A / B / C / D. Planned action from that row: ___
- [ ] Verify the pair-specific entry trigger using only information available at decision time. Required: ___ · Observed: ___

**If the branch calls for waiting, or the trigger is absent**, skip the order ticket. Record the reason and next review condition below. Otherwise continue.

## Order ticket

- [ ] Entry side and order type: ___ · Price or trigger: ___ · Quantity: ___ (base units)
- [ ] If using a ladder, list each level as price × quantity: ___. Total quote committed if all entries fill, including fees: ___
- [ ] Exit plan: ___. For attached sells, specify price or offset from actual fill and the percentage of each fill to sell: ___
- [ ] Invalidation price or condition: ___ · Exit action: ___ · Pending orders to cancel: ___
- [ ] Estimate loss at invalidation if every entry fills, including fees and assumed slippage: ___ (quote units). Check against the trade limit and existing exposure.
- [ ] Check available balance, working orders, and the actual ticket against the plan. Submit once and record order IDs: ___

## After the decision

- Decision: placed / waited / skipped · Reason: ___ · Next review time or condition: ___
- On a partial fill: verify filled quantity, remaining entries, and exit coverage. Action taken: ___
- If the regime changes while orders are working: apply the predefined rule for pending entries ___ and open positions ___.
- On exit: record actual fills and fees, cancel obsolete orders, and reconcile remaining holdings. Net P&L and currency: ___
- One observation for the next review: ___
