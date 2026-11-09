/* Click-driven historical simulator. No strategy callbacks run on candle advance. */
(function (root) {
  'use strict';
  function number(value, label, min = 0, max = Infinity) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max)
      throw new Error(`${label} must be a number between ${min} and ${max}.`);
    return value;
  }
  function create(capital, feeBps, slipBps) {
    number(capital, 'Capital', Number.MIN_VALUE);
    number(feeBps, 'Fee bps', 0, 1000);
    number(slipBps, 'Slippage bps', 0, 1000);
    return {capital, cash: capital, held: 0, fees: 0, fee: feeBps / 10000,
      slip: slipBps / 10000, orders: [], fills: [], nextId: 1};
  }
  function available(s) {
    return {cash: Math.max(0, s.cash - s.orders.filter(o => o.side === 'buy').reduce((n, o) => n + o.budget, 0)),
      held: Math.max(0, s.held - s.orders.filter(o => o.side === 'sell').reduce((n, o) => n + o.qty, 0))};
  }
  function targets(value = []) {
    if (!Array.isArray(value)) throw new Error('targets must be an array.');
    let total = 0;
    const result = value.map(t => {
      const pct = number(t.qtyPct, 'Target quantity %', Number.MIN_VALUE, 100);
      total += pct;
      return {gainPct: number(t.gainPct, 'Target gain %', Number.MIN_VALUE), qtyPct: pct};
    });
    if (total > 100 + 1e-9) throw new Error('Target quantities must total at most 100%.');
    return result;
  }
  function submit(s, side, pct, price = null, attached = []) {
    if (!['buy', 'sell'].includes(side)) throw new Error('Choose buy or sell.');
    number(pct, 'Order size %', Number.MIN_VALUE, 100);
    if (price !== null) number(price, 'Limit price', Number.MIN_VALUE);
    const exits = targets(attached);
    const free = available(s);
    const amount = (side === 'buy' ? free.cash : free.held) * pct / 100;
    if (!(amount > 1e-14)) throw new Error(`No available ${side === 'buy' ? 'cash' : 'holdings'}. Cancel reserved orders first.`);
    const order = {id: s.nextId++, side, price, targets: exits};
    if (side === 'buy') order.budget = amount;
    else order.qty = amount;
    s.orders.push(order);
    return order;
  }
  // All ladder allocations use the same available-cash snapshot. Validate atomically.
  function preset(s, config, bars) {
    if (!config || !Array.isArray(config.buys) || !config.buys.length || config.buys.length > 100)
      throw new Error('Preset needs 1–100 buy legs.');
    const period = number(config.rangePeriod ?? 14, 'Range period', 1, 500);
    if (!Number.isInteger(period)) throw new Error('Range period must be an integer.');
    const recent = bars.slice(-period);
    const width = recent.reduce((sum, b) => sum + b.high - b.low, 0) / recent.length;
    const bar = bars[bars.length - 1];
    const cash = available(s).cash;
    let total = 0;
    const orders = config.buys.map(leg => {
      const pct = number(leg.cashPct, 'Cash %', Number.MIN_VALUE, 100);
      total += pct;
      const ref = leg.reference ?? 'close';
      if (!['open', 'high', 'low', 'close'].includes(ref)) throw new Error('reference must be open, high, low or close.');
      const offset = number(leg.offsetPct ?? 0, 'Offset %', -99, 1000);
      const multiple = number(leg.rangeMultiple ?? 0, 'Range multiple', -1000, 1000);
      if (!['market', 'limit'].includes(leg.type ?? 'limit')) throw new Error('type must be market or limit.');
      const price = leg.type === 'market' ? null : bar[ref] * (1 + offset / 100) + multiple * width;
      if (price !== null) number(price, 'Calculated limit price', Number.MIN_VALUE);
      return {side: 'buy', budget: cash * pct / 100, price, targets: targets(leg.targets)};
    });
    if (total > 100 + 1e-9) throw new Error('Buy allocations must total at most 100% of available cash.');
    if (!(cash > 1e-14)) throw new Error('No available cash.');
    for (const order of orders) s.orders.push({...order, id: s.nextId++});
  }
  function advance(s, bar) {
    // Snapshot prevents targets created inside this candle filling on unknown intrabar paths.
    const eligible = [...s.orders];
    for (const o of eligible) {
      // A sell limit executable at the open precedes intrabar moves.
      // If both exits are touched later, resolve the ambiguous path stop-first.
      const stopped = o.side === 'sell' && o.stopPrice > 0 &&
        (o.stopOnly || bar.open < o.price) && bar.low <= o.stopPrice;
      if (o.stopOnly && !stopped) continue;
      if (!stopped && o.price !== null && (o.side === 'buy' ? bar.low > o.price : bar.high < o.price)) continue;
      let price;
      if (stopped) price = Math.min(bar.open, o.stopPrice) * (1 - s.slip);
      else if (o.price === null) price = bar.open * (1 + (o.side === 'buy' ? s.slip : -s.slip));
      else price = o.side === 'buy' ? Math.min(bar.open, o.price) : Math.max(bar.open, o.price);
      const qty = o.side === 'buy' ? o.budget / (price * (1 + s.fee)) : o.qty;
      const fee = qty * price * s.fee;
      if (o.side === 'buy') { s.cash -= o.budget; s.held += qty; }
      else { s.cash += qty * price - fee; s.held -= qty; }
      s.fees += fee;
      s.orders = s.orders.filter(order => order.id !== o.id);
      s.fills.push({id: o.id, side: o.side, qty, price, fee, ts: bar.ts, parent: o.parent ?? null, ...(stopped ? {reason:o.stopReason || "stop"} : {})});
      if (o.side === 'buy') for (const t of o.targets) s.orders.push({id: s.nextId++, side: 'sell',
        qty: qty * t.qtyPct / 100, price: price * (1 + t.gainPct / 100), targets: [], parent: o.id});
    }
    if (Math.abs(s.held) < 1e-14) s.held = 0;
    if (Math.abs(s.cash) < 1e-14) s.cash = 0;
  }
  const api = {create, available, submit, preset, advance};
  if (typeof module !== 'undefined') module.exports = api;
  else root.ManualEngine = api;
})(typeof window === 'undefined' ? globalThis : window);
