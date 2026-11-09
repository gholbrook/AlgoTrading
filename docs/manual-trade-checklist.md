# Manual trade checklist

Copy this file for each trade decision. Fill in the strategy rules before the session. At decision time, follow one regime branch and record the action, including a decision to wait.

## Strategy rules

- Strategy and version: ___
- Signal timeframe: ___ · Regime timeframe: ___
- Regime indicator, source pair, and units: ___
- Regime reading to use: [ ] Last completed regime candle  [ ] As of the decision candle
- Maximum loss per trade in quote currency: ___ · Maximum total exposure: ___

Define the thresholds and action for each branch. For example, a BTC daily 200 EMA slope rule could use the percentage change from the prior daily EMA, with a neutral band of your choosing. If you trade grids, define how you also confirm that the traded pair is moving sideways inside its boundaries.

| Branch | Observable condition | Permitted setup and order action |
| --- | --- | --- |
| A · Bullish | ___ | ___ |
| B · Sideways | ___ | ___ |
| C · Bearish | ___ | ___ |
| D · Unclear, conflicting, or missing data | No single branch qualifies | Wait. Recheck when ___ |

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
