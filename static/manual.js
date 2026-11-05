'use strict';
const $ = id => document.getElementById(id);
const E = ManualEngine;
const sessionKey = 'gridlab.manual.session.v1';
const presetKey = 'gridlab.manual.presets.v1';
const defaultPreset = {rangePeriod: 14, buys: [0, -1, -2, -3].map((offset, i) => ({
  type: i === 0 ? 'market' : 'limit', cashPct: 25, reference: 'close', offsetPct: offset,
  targets: [{gainPct: 1, qtyPct: 50}, {gainPct: 2, qtyPct: 50}]
}))};
let datasets = [], bars = [], session = null, busy = false, zoom = 80;
let presets = {'Four buys with profit targets': defaultPreset,
  'Volatility-spaced buys': {rangePeriod: 14, buys: [0, -1, -2].map(rangeMultiple => ({
    type: 'limit', cashPct: 25, reference: 'close', rangeMultiple,
    targets: [{gainPct: 1, qtyPct: 100}]
  }))}};
function message(text, error = false) { $('message').textContent = text; $('message').classList.toggle('error', error); }
function guard(fn) { return async () => { try { await fn(); } catch (e) { message(e.message, true); } }; }
function persist() {
  try { localStorage.setItem(sessionKey, JSON.stringify(session)); }
  catch { message('Browser storage is full. This replay will be lost if you close the page.', true); }
}
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
function fmt(n) { return Number(n).toLocaleString('en-US', {maximumFractionDigits: 8}); }
function date(ts) { return new Date(ts * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC'); }
function option(value, text) { const o = document.createElement('option'); o.value = value; o.textContent = text; return o; }
function filterDatasets() {
  const previous = $('dataset').value;
  $('dataset').replaceChildren(...datasets.filter(d => !$('quote').value || d.market.endsWith('/' + $('quote').value))
    .map(d => option(d.id, `${d.market} · ${d.timeframe} · ${d.venue || 'Local'} · ${d.bars.toLocaleString()} candles`)));
  if ([...$('dataset').options].some(o => o.value === previous)) $('dataset').value = previous;
  coverage();
}
function coverage() {
  const d = datasets.find(d => d.id === $('dataset').value);
  $('coverage').textContent = d ? `${d.start.slice(0, 10)} — ${d.end.slice(0, 10)} · UTC` : 'No datasets for this quote currency.';
}
function active() {
  if (!session || busy) throw new Error('Start a replay first.');
  if (session.cursor >= bars.length - 1) throw new Error('End of dataset. Start another replay to place more orders.');
}
function refreshPresets(selected) {
  $('preset-select').replaceChildren(...Object.keys(presets).map(name => option(name, name)));
  if (selected) $('preset-select').value = selected;
  const name = $('preset-select').value;
  $('preset-name').value = name;
  $('preset-code').value = JSON.stringify(presets[name] || defaultPreset, null, 2);
}
function parsePreset() {
  const value = JSON.parse($('preset-code').value);
  // Validate using a throwaway account, including calculated-price rules.
  E.preset(E.create(100, 0, 0), value, [{open: 100, high: 101, low: 99, close: 100}]);
  return value;
}
function place(side) {
  active();
  E.submit(session.account, side, Number($('size').value), $('type').value === 'market' ? null : Number($('limit').value));
  persist(); render(); message('Order queued. Advance a candle to evaluate its fill.');
}
function step(count) {
  active();
  for (let i = 0; i < count && session.cursor < bars.length - 1; i++) E.advance(session.account, bars[++session.cursor]);
  persist(); render();
  message(session.cursor === bars.length - 1 ? 'End of dataset reached. Pending orders remain unfilled.' : 'Replay advanced.');
}
function row(text, detail, buttonText, callback) {
  const el = document.createElement('div'); el.className = 'manual-row';
  const label = document.createElement('div'); label.textContent = text;
  const small = document.createElement('small'); small.textContent = detail;
  label.append(document.createElement('br'), small); el.append(label);
  if (buttonText) { const b = document.createElement('button'); b.textContent = buttonText; b.onclick = guard(callback); el.append(b); }
  return el;
}
function render() {
  const ended = !session || session.cursor >= bars.length - 1;
  document.querySelectorAll('[data-trading]').forEach(b => b.disabled = busy || ended);
  if (!session) return;
  const a = session.account, bar = bars[session.cursor], free = E.available(a);
  const [base, quote] = session.market.split('/');
  $('pair').textContent = `${session.market} · ${session.timeframe}`;
  $('cursor').textContent = `${date(bar.ts)} · ${session.cursor - session.first + 1} replay candles revealed${ended ? ' · End of dataset' : ''}`;
  const values = [['Equity', a.cash + a.held * bar.close, quote], ['Cash / available', `${fmt(a.cash)} / ${fmt(free.cash)}`, quote],
    ['Holdings / available', `${fmt(a.held)} / ${fmt(free.held)}`, base], ['P&L · marked to close', a.cash + a.held * bar.close - a.capital, quote],
    ['Fees paid', a.fees, quote], ['Executed fills', a.fills.length, '']];
  $('stats').replaceChildren(...values.map(([name, value, unit]) => {
    const div = document.createElement('div'), small = document.createElement('small'), strong = document.createElement('strong');
    small.textContent = name; strong.textContent = `${typeof value === 'number' ? fmt(value) : value} ${unit}`;
    div.append(small, strong); return div;
  }));
  $('orders').replaceChildren(...a.orders.map(o => row(`#${o.id} ${o.side.toUpperCase()} · ${o.price === null ? 'Market' : fmt(o.price) + ' ' + quote}`,
    o.side === 'buy' ? `${fmt(o.budget)} ${quote} reserved including fees · ${o.targets.length} attached exits` : `${fmt(o.qty)} ${base} reserved${o.parent ? ' · target for buy #' + o.parent : ''}`,
    'Cancel', () => { a.orders = a.orders.filter(x => x.id !== o.id); persist(); render(); })));
  if (!a.orders.length) $('orders').textContent = 'No pending orders.';
  $('fills').replaceChildren(...a.fills.slice(-30).reverse().map(f => row(
    `#${f.id} ${f.side.toUpperCase()} ${fmt(f.qty)} ${base} @ ${fmt(f.price)} ${quote}`,
    `${date(f.ts)} · Fee ${fmt(f.fee)} ${quote}${f.parent ? ' · target for buy #' + f.parent : ''}`)));
  if (!a.fills.length) $('fills').textContent = 'Place an order and advance a candle to see its execution.';
  draw();
}
function draw() {
  const canvas = $('chart'), width = canvas.clientWidth, height = 350, ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  if (!session) return;
  const visible = bars.slice(Math.max(0, session.cursor - zoom + 1), session.cursor + 1);
  const style = getComputedStyle(document.documentElement);
  const low = Math.min(...visible.map(b => b.low)), high = Math.max(...visible.map(b => b.high));
  const pad = (high - low) * .08 || high * .001;
  const lo = low - pad, hi = high + pad;
  const plotWidth = Math.max(10, width - 90), dx = plotWidth / visible.length;
  const y = price => 18 + (hi - price) / (hi - lo) * (height - 50);
  ctx.font = '11px monospace';
  for (let i = 0; i <= 4; i++) {
    const value = lo + (hi - lo) * i / 4;
    ctx.strokeStyle = style.getPropertyValue('--line'); ctx.beginPath(); ctx.moveTo(0, y(value)); ctx.lineTo(plotWidth, y(value)); ctx.stroke();
    ctx.fillStyle = style.getPropertyValue('--muted'); ctx.fillText(fmt(value), plotWidth + 6, y(value) + 4);
  }
  visible.forEach((b, i) => {
    const x = (i + .5) * dx;
    ctx.strokeStyle = ctx.fillStyle = b.close >= b.open ? style.getPropertyValue('--green') : style.getPropertyValue('--rust');
    ctx.beginPath(); ctx.moveTo(x, y(b.high)); ctx.lineTo(x, y(b.low)); ctx.stroke();
    ctx.fillRect(x - Math.max(1, dx * .3), Math.min(y(b.open), y(b.close)), Math.max(2, dx * .6), Math.max(1, Math.abs(y(b.close) - y(b.open))));
  });
  const byTime = new Map(visible.map((b, i) => [b.ts, i]));
  for (const f of session.account.fills) {
    const i = byTime.get(f.ts); if (i === undefined) continue;
    const x = (i + .5) * dx, py = y(f.price);
    ctx.fillStyle = f.side === 'buy' ? style.getPropertyValue('--green') : style.getPropertyValue('--rust');
    ctx.beginPath(); ctx.arc(x, py, 4, 0, Math.PI * 2); ctx.fill();
    ctx.fillText(f.side === 'buy' ? 'B' : 'S', x + 4, py - 5);
  }
  ctx.fillStyle = style.getPropertyValue('--muted');
  ctx.fillText(date(visible[0].ts).slice(5, 16), 0, height - 4);
  ctx.fillText(date(visible.at(-1).ts).slice(5, 16), Math.max(0, plotWidth - 115), height - 4);
  canvas.onmousemove = event => {
    const i = Math.max(0, Math.min(visible.length - 1, Math.floor(event.offsetX / dx)));
    const b = visible[i], fills = session.account.fills.filter(f => f.ts === b.ts);
    $('hover').textContent = `${date(b.ts)} · O ${fmt(b.open)} H ${fmt(b.high)} L ${fmt(b.low)} C ${fmt(b.close)}${fills.map(f => ` · #${f.id} ${f.side} ${fmt(f.qty)} @ ${fmt(f.price)}`).join('')}`;
  };
}
$('start-session').onclick = guard(async () => {
  if (session && !confirm('Replace the current manual replay? Its orders and fills will be cleared.')) return;
  const account = E.create(Number($('capital').value), Number($('fee').value), Number($('slip').value));
  const query = {dataset: $('dataset').value, start: $('start').value, end: $('end').value};
  busy = true; $('start-session').disabled = true; render();
  try {
    const data = await request('/api/manual/candles', query);
    bars = data.bars;
    session = {query, market: data.market, timeframe: data.timeframe, fingerprint: data.fingerprint, first: data.index, cursor: data.index, account};
    persist(); message('Replay ready. The displayed candle has closed. Place your first order.');
  } finally { busy = false; $('start-session').disabled = false; render(); }
});
$('buy').onclick = guard(() => place('buy')); $('sell').onclick = guard(() => place('sell'));
$('step').onclick = guard(() => step(1)); $('step10').onclick = guard(() => step(10));
$('close-all').onclick = guard(() => {
  active();
  if (!(session.account.held > 1e-14)) throw new Error('There are no holdings to sell.');
  session.account.orders = session.account.orders.filter(o => o.side !== 'sell');
  E.submit(session.account, 'sell', 100); persist(); render(); message('Sell orders replaced with a full market exit for the next open. Pending buys remain active.');
});
$('cancel-all').onclick = guard(() => { session.account.orders = []; persist(); render(); message('Pending orders canceled.'); });
$('type').onchange = () => $('limit-label').hidden = $('type').value !== 'limit';
$('quote').onchange = filterDatasets; $('dataset').onchange = coverage;
$('preset-select').onchange = () => refreshPresets($('preset-select').value);
$('save-preset').onclick = guard(() => {
  const name = $('preset-name').value.trim(); if (!name) throw new Error('Give the preset a name.');
  const value = parsePreset();
  Object.defineProperty(presets, name, {value, writable: true, enumerable: true, configurable: true});
  localStorage.setItem(presetKey, JSON.stringify(presets)); refreshPresets(name); message('Preset saved in this browser.');
});
$('delete-preset').onclick = guard(() => { delete presets[$('preset-select').value]; localStorage.setItem(presetKey, JSON.stringify(presets)); refreshPresets(); });
$('apply-preset').onclick = guard(() => { active(); E.preset(session.account, JSON.parse($('preset-code').value), bars.slice(0, session.cursor + 1)); persist(); render(); message('Preset buys queued using the currently revealed candles.'); });
$('theme').onclick = () => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = next; localStorage.setItem('gridline.theme', next); draw(); };
$('chart').addEventListener('wheel', e => { e.preventDefault(); zoom = Math.max(15, Math.min(500, zoom + (e.deltaY > 0 ? 10 : -10))); draw(); }, {passive: false});
window.addEventListener('resize', draw);
guard(async () => {
  render();
  try { const saved = localStorage.getItem(presetKey); if (saved) presets = JSON.parse(saved); } catch { message('Saved presets could not be loaded.', true); }
  refreshPresets();
  const context = await request('/api/context'); datasets = context.datasets; filterDatasets();
  const stored = localStorage.getItem(sessionKey);
  if (stored) {
    const saved = JSON.parse(stored);
    const data = await request('/api/manual/candles', saved.query);
    if (data.fingerprint !== saved.fingerprint || !data.bars[saved.cursor]) throw new Error('Saved replay data changed. Start a new replay.');
    bars = data.bars; session = saved;
    $('quote').value = ''; filterDatasets(); $('dataset').value = saved.query.dataset; coverage();
    $('start').value = saved.query.start; $('end').value = saved.query.end;
    $('capital').value = saved.account.capital; $('fee').value = saved.account.fee * 10000; $('slip').value = saved.account.slip * 10000;
    render(); message('Manual replay restored.');
  }
})();
