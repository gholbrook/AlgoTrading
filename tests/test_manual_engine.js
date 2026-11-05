// Run with node tests/test_manual_engine.js
const assert = require('node:assert/strict');
const E = require('../static/manual-engine.js');
const bar = (open, high, low, close = open, ts = 1) => ({open, high, low, close, ts});
let s = E.create(1000, 20, 2);
E.submit(s, 'buy', 100);
assert.equal(s.fills.length, 0, 'Clicks queue orders without filling the current candle');
assert.equal(E.available(s).cash, 0);
assert.throws(() => E.submit(s, 'buy', 10));
E.advance(s, bar(100, 110, 90));
assert.equal(s.fills[0].price, 100.02);
assert.equal(s.cash, 0);
assert.ok(Math.abs(s.held * 100.02 * 1.002 - 1000) < 1e-9);
E.submit(s, 'sell', 100);
assert.throws(() => E.submit(s, 'sell', 10));
E.advance(s, bar(110, 115, 100));
assert.equal(s.held, 0);
assert.equal(s.fills.length, 2);
assert.ok(s.cash > 1090);

s = E.create(1000, 0, 0);
E.preset(s, {buys: [0, -2, -4, -6].map(offsetPct => ({cashPct: 25, offsetPct,
  targets: [{gainPct: 2, qtyPct: 50}, {gainPct: 4, qtyPct: 50}]}))}, [bar(100, 101, 99)]);
assert.equal(E.available(s).cash, 0);
E.advance(s, bar(99, 110, 99));
assert.equal(s.fills.length, 1, 'Only the touched buy fills; new exits wait for next candle');
assert.equal(s.fills[0].price, 99, 'Limit receives opening price improvement');
assert.equal(s.orders.filter(o => o.side === 'sell').length, 2);
assert.ok(E.available(s).held < 1e-12);
E.advance(s, bar(105, 110, 105));
assert.equal(s.fills.length, 3);
assert.equal(s.held, 0);
assert.equal(s.orders.length, 3);

s = E.create(100, 0, 0);
assert.throws(() => E.preset(s, {buys: [{cashPct: 60}, {cashPct: 60}]}, [bar(100, 101, 99)]));
assert.equal(s.orders.length, 0, 'Invalid ladders must be atomic');
assert.throws(() => E.submit(s, 'buy', 10, null, [{gainPct: 1, qtyPct: 110}]));
assert.equal(s.orders.length, 0);
E.preset(s, {rangePeriod: 2, buys: [{cashPct: 50, rangeMultiple: -2}]}, [bar(100, 103, 97)]);
assert.equal(s.orders[0].price, 88);
s.orders = [];
assert.equal(E.available(s).cash, 100, 'Cancel frees reserves');
assert.deepEqual(JSON.parse(JSON.stringify(s)), s, 'State round-trips through persistence');
console.log('Manual replay engine checks passed.');
