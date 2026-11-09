'use strict';
const $ = id => document.getElementById(id);
let catalog = [], result = null, chartVersion = 0;
const configFields = ['candles', 'bins', 'value_area', 'grace', 'horizon', 'stride'];
const fmt = v => v === null ? '—' : Number(v).toLocaleString(undefined, {maximumFractionDigits: 2});
const utc = ts => new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
function option(value, text) { const el = document.createElement('option'); el.value = value; el.textContent = text; return el; }
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed.');
  return data;
}
function coverage() {
  const d = catalog.find(d => d.id === $('dataset').value);
  $('coverage').textContent = d ? `${d.start.slice(0,10)} — ${d.end.slice(0,10)} · ${d.bars.toLocaleString()} candles` : 'No matching datasets.';
  const hours = d?.timeframe === '1h' ? 1 : 1 / 12;
  $('time-help').textContent = `Formation ${fmt(Number($('candles').value)*hours)} hours · Observation up to ${fmt(Number($('horizon').value)*hours)} hours.`;
}
function datasets() {
  $('dataset').replaceChildren(...catalog.filter(d => !$('quote').value || d.market.endsWith('/'+$('quote').value))
    .map(d => option(d.id, `${d.market} · ${d.timeframe} · ${d.venue || 'Local'} · ${d.bars.toLocaleString()}`)));
  coverage();
}
function metric(name, value) {
  const div = document.createElement('div'), label = document.createElement('small'), strong = document.createElement('strong');
  label.textContent = name; strong.textContent = value; div.append(label, strong); return div;
}
function render() {
  const s = result.summary;
  $('results').hidden = false;
  $('result-title').textContent = `${result.dataset.market} · ${result.dataset.timeframe} · ${result.config.candles} profile candles · ${result.config.allocation==='adaptive' ? '70% → 50% above '+result.config.width_threshold+'% width' : result.config.value_area+'% volume'} · allowance ${result.config.grace}`;
  $('stats').replaceChildren(
    metric('Average elapsed stay · confirmed exits', fmt(s.mean_duration_candles)+' candles'),
    metric('Average actual inside · confirmed exits', fmt(s.mean_inside_candles)+' candles'),
    metric('Median elapsed stay · confirmed exits', fmt(s.median_duration_candles)+' candles'),
    metric('Average elapsed stay · hours', fmt(s.mean_duration_hours)),
    metric('Average strict stay · first exits observed', fmt(s.mean_strict_duration_candles)+' candles'),
    metric('Instances / confirmed exits', `${s.instances} / ${s.completed_exits}`),
    metric('Unfinished instances', fmt(s.censored)),
    metric('Average lower bound · all instances', fmt(s.mean_observed_lower_bound_candles)+' candles'),
    metric('Average ignored outside · confirmed exits', fmt(s.mean_ignored_candles)+' candles'));
  $('outcomes').textContent = `Unfinished at horizon ${s.outcomes.horizon} · dataset end ${s.outcomes.data_end} · data gap ${s.outcomes.gap}. Skipped candidates outside ${result.skipped.outside}, zero volume ${result.skipped.zero_volume}, formation gap ${result.skipped.formation_gap}, insufficient volume on one side ${result.skipped.insufficient_side_volume ?? 0}.`;
  $('inspection').hidden = !result.episodes.length;
  $('episode').replaceChildren(...result.episodes.map((e,i)=>option(i, `#${e.id} · ${utc(e.anchor)} · ${e.duration_candles} candles${e.censored ? '+' : ''} · ${e.outcome}`)));
  if (result.episodes.length) inspect();
}
function svg(name, attrs, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,value);
  if (text !== undefined) node.textContent = text;
  return node;
}
async function inspect() {
  const version = ++chartVersion, r = result, e = r.episodes[Number($('episode').value)];
  const canvas = $('episode-chart'); canvas.replaceChildren();
  $('episode-detail').textContent = 'Loading instance candles…';
  try {
    const data = await api('/api/manual/candles', {dataset:r.dataset.id, start:utc(e.formation_start).slice(0,10), end:utc(e.observed_end).slice(0,10)});
    if (version !== chartVersion) return;
    let bars = data.bars.filter(b => b.ts >= e.formation_start && b.ts <= e.observed_end);
    if (!$('full-formation').checked) {
      const anchorIndex = bars.findIndex(b => b.ts === e.anchor);
      const context = Math.max(20, Math.ceil(e.observed_candles * 0.25));
      bars = bars.slice(Math.max(0, anchorIndex - context));
    }
    $('episode-detail').textContent = `${e.inside_candles} inside · ${e.ignored_candles} ignored outside in ${e.ignored_excursions} excursions · ${e.terminal_outside_candles} terminal outside · ${e.censored ? 'Unfinished — duration is a lower bound' : 'Exit confirmed '+utc(e.confirmed_at)}. VAL ${e.value_low.toPrecision(7)} · POC ${e.poc.toPrecision(7)} · VAH ${e.value_high.toPrecision(7)}.`;
    let low=e.value_low, high=e.value_high;
    for(const b of bars) { low=Math.min(low,b.low); high=Math.max(high,b.high); }
    const pad=(high-low)*.08 || high*.01; low-=pad; high+=pad;
    const x=ts=>55+(ts-bars[0].ts)/Math.max(1,bars.at(-1).ts-bars[0].ts)*820;
    const y=price=>285-(price-low)/(high-low)*255;
    canvas.append(svg('rect',{x:x(e.anchor),y:y(e.value_high),width:Math.max(1,x(e.observed_end)-x(e.anchor)),height:y(e.value_low)-y(e.value_high),fill:'var(--green)',opacity:.12}));
    for(const [value,label] of [[e.value_low,'VAL'],[e.poc,'POC'],[e.value_high,'VAH']]) {
      canvas.append(svg('line',{x1:x(e.anchor),x2:875,y1:y(value),y2:y(value),stroke:'var(--green)','stroke-dasharray':'4 3'}));
      canvas.append(svg('text',{x:55,y:y(value)-5,fill:'var(--ink)','font-size':11},`${label} ${value.toPrecision(6)}`));
    }
    const path=bars.map((b,i)=>`${i?'L':'M'}${x(b.ts)},${y(b.close)}`).join(' ');
    canvas.append(svg('path',{d:path,stroke:'var(--muted)','stroke-width':1.3,fill:'none'}));
    // Limit markers on very long observations while retaining every candle in the line.
    const sample=Math.max(1,Math.ceil(bars.length/2000));
    bars.forEach((b,i)=>{
      if(b.ts<=e.anchor || i%sample) return;
      const inside=r.config.inside==='close' ? e.value_low<=b.close && b.close<=e.value_high : e.value_low<=b.low && b.high<=e.value_high;
      const dot=svg('circle',{cx:x(b.ts),cy:y(b.close),r:2.3,fill:inside?'var(--green)':'var(--rust)'});
      dot.append(svg('title',{},`${utc(b.ts)} · O ${b.open} H ${b.high} L ${b.low} C ${b.close} · ${inside?'inside':'outside'}`)); canvas.append(dot);
    });
    canvas.append(svg('text',{x:55,y:315,fill:'var(--muted)','font-size':11},utc(bars[0].ts)));
    canvas.append(svg('text',{x:875,y:315,fill:'var(--muted)','font-size':11,'text-anchor':'end'},utc(bars.at(-1).ts)));
  } catch(error) { if(version===chartVersion) $('episode-detail').textContent=error.message; }
}
function allocationControls() {
  const adaptive = $('allocation').value === 'adaptive';
  $('value_area').readOnly = adaptive;
  if (adaptive) $('value_area').value = 70;
  $('threshold-label').hidden = !adaptive;
  $('width_threshold').disabled = !adaptive;
}
$('allocation').onchange = allocationControls;
allocationControls();
$('experiment').onsubmit = async event => {
  event.preventDefault(); const button=$('run'); button.disabled=true; button.textContent='Calculating…'; $('status').textContent='Evaluating historical instances…';
  const config=Object.fromEntries(configFields.map(key=>[key,Number($(key).value)]));config.inside=$('inside').value;config.allocation=$('allocation').value;
  if(config.allocation==='adaptive') config.width_threshold=Number($('width_threshold').value);
  try { result=await api('/api/research/value-area',{dataset:$('dataset').value,start:$('start').value,end:$('end').value,config}); render(); $('status').textContent=result.episodes.length ? 'Experiment complete.' : 'No eligible instances. Try a longer date window or adjust the profile settings.'; }
  catch(error) { $('status').textContent=error.message; }
  finally { button.disabled=false; button.textContent='Run experiment'; }
};
function download(contents, extension, mime) {
  const url=URL.createObjectURL(new Blob([contents],{type:mime})), link=document.createElement('a');
  link.href=url; link.download=`value-area-${result.dataset.market.replace('/','-')}.${extension}`; link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('json').onclick=()=>download(JSON.stringify(result,null,2),'json','application/json');
$('csv').onclick=()=>{const keys=Object.keys(result.episodes[0]||{}); const cell=value=>JSON.stringify(String(value??'')); download([keys.join(','),...result.episodes.map(e=>keys.map(k=>cell(e[k])).join(','))].join('\n'),'csv','text/csv');};
$('quote').onchange=datasets; $('dataset').onchange=coverage; $('candles').oninput=coverage; $('horizon').oninput=coverage; $('episode').onchange=inspect; $('full-formation').onchange=inspect;
api('/api/context').then(data=>{catalog=data.datasets;datasets();}).catch(error=>$('status').textContent=error.message);
