'use strict';
const $ = id => document.getElementById(id);
let bars = [], start = 0, span = 500;
let geometry = null, drag = null, frame = null, loadVersion = 0;
const embedded = window.parent !== window && new URLSearchParams(location.search).get('view') === 'backtest';
let replayCursor = -1, seekReplay, describeFill, tradeTrace;
const emaLines = [
  {period: 200, control: $('ema'), color: '--ema-color', values: []},
  {period: 21, control: $('ema21'), color: '--ema21-color', values: []},
  {period: 50, control: $('ema50'), color: '--ema50-color', values: []},
];
function closingEma(candles, period) {
  const alpha = 2 / (period + 1);
  let sum = 0, value;
  return candles.map(({close}, index) => {
    if (index < period) {
      sum += close;
      if (index + 1 < period) return null;
      value = sum / period;
    } else {
      value += alpha * (close - value);
    }
    return value;
  });
}
let macdValues = [];
function calculateMacd(points) {
  if (!points.length) return [];
    let fast = points[0].close, slow = fast, signal = 0;
    return points.map(({close}) => {
      fast += (2 / 13) * (close - fast);
      slow += (2 / 27) * (close - slow);
      const line = fast - slow;
      signal += 0.2 * (line - signal);
      return [line, signal, line - signal];
    });
}

function drawMacd(ctx, first, last, x, top, bottom, left, right, style) {
  let extent = 0;
  for (let i = first; i <= last; i++) {
    for (const value of macdValues[i]) extent = Math.max(extent, Math.abs(value));
  }
  const middle = (top + bottom) / 2;
  const y = value => middle - value / (extent || 1) * (bottom - top) * 0.47;
  const color = name => style.getPropertyValue(name).trim();
  ctx.save();
  ctx.fillStyle = color('--macd-line');
  ctx.fillText('MACD (12, 26)', left, top - 4);
  ctx.fillStyle = color('--macd-signal');
  ctx.fillText('Signal (9)', left + 102, top - 4);
  ctx.fillStyle = color('--muted');
  ctx.fillText('0', right + 8, middle + 4);
  ctx.fillText(number(extent), right + 8, y(extent) + 4);
  ctx.strokeStyle = color('--line');
  ctx.beginPath();
  ctx.moveTo(left, middle);
  ctx.lineTo(right, middle);
  ctx.stroke();
  ctx.beginPath();
  ctx.rect(left, top, right - left, bottom - top);
  ctx.clip();

  const step = Math.max(1, Math.ceil(span / (right - left)));
  const barWidth = Math.max(1, Math.min(14, (right - left) * step / span * .75));
  const positive = color('--green'), negative = color('--rust');
  for (let i = first; i <= last; i += step) {
    const histogram = macdValues[i][2];
    ctx.fillStyle = histogram >= 0 ? positive : negative;
    ctx.fillRect(x(i) - barWidth / 2, middle, barWidth, y(histogram) - middle);
  }
  for (const [column, ink] of [[0, '--macd-line'], [1, '--macd-signal']]) {
    ctx.beginPath();
    ctx.moveTo(x(first), y(macdValues[first][column]));
    for (let i = first + step; i < last; i += step) ctx.lineTo(x(i), y(macdValues[i][column]));
    ctx.lineTo(x(last), y(macdValues[last][column]));
    ctx.strokeStyle = color(ink);
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  ctx.restore();
}

const number = value => Number(value).toLocaleString('en-US', {maximumFractionDigits: 8});
const date = ts => new Date(ts * 1000).toISOString().replace('T',' ').slice(0,16)+' UTC';
const colors = () => getComputedStyle(document.documentElement);
function message(text, error=false) { $('status').textContent=text; $('status').style.color=error?'var(--rust)':'var(--green)'; }
async function api(path, body) {
  const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Request failed.');return data;
}
function clampView() { span=Math.min(bars.length,Math.max(20,span));start=Math.max(0,Math.min(start,bars.length-span)); }
function redraw() { if(frame!==null)return;frame=requestAnimationFrame(()=>{frame=null;draw();}); }
function canvasContext(id) {
  const canvas=$(id),rect=$('chart-area').getBoundingClientRect(),ratio=devicePixelRatio||1;
  canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);return {ctx,width:rect.width,height:rect.height};
}
function draw() {
  const {ctx,width,height}=canvasContext('price-chart');canvasContext('crosshair');
  if(!bars.length)return;
  if (!embedded) clampView();
  const first=Math.floor(start),last=embedded ? replayCursor : Math.min(bars.length-1,Math.ceil(start+span));
  let low=Infinity,high=-Infinity;
  for(let i=first;i<=last;i++){low=Math.min(low,bars[i].low);high=Math.max(high,bars[i].high);}
  if (embedded) {
    for (let i = first; i <= last; i++) for (const fill of bars[i].events || []) {
      low = Math.min(low, fill.price);
      high = Math.max(high, fill.price);
    }
  }
  const visibleEmas = emaLines.filter(line => line.control.checked);
  for (const {values} of visibleEmas) {
    for (let i = first; i <= last; i++) {
      if (values[i] === null) continue;
      low = Math.min(low, values[i]);
      high = Math.max(high, values[i]);
    }
  }
  const pad=(high-low)*.07||high*.001;low-=pad;high+=pad;
  const left=16,right=width-100,top=26,plotBottom=height-36;
  const bottom = $('macd').checked ? plotBottom - Math.round(height * .24) : plotBottom;
  const x=i=>left+(i-start)/span*(right-left),y=p=>bottom-(p-low)/(high-low)*(bottom-top);
  const style=colors(),green=style.getPropertyValue('--green').trim(),red=style.getPropertyValue('--rust').trim(),muted=style.getPropertyValue('--muted').trim();
  geometry={left,right,top,bottom,plotBottom,x,y,low,high,width,height};
  ctx.font='11px monospace';ctx.lineWidth=1;
  for(let i=0;i<=6;i++) {const price=low+(high-low)*i/6,py=y(price);ctx.strokeStyle=style.getPropertyValue('--line');ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(right,py);ctx.stroke();ctx.fillStyle=muted;ctx.fillText(price.toLocaleString('en-US',{maximumFractionDigits:high>=100?2:8}),right+8,py+4);}
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  const bucket=Math.max(1,Math.ceil(span/Math.max(1,(right-left)/3)));
  for(let i=first;i<=last;i+=bucket){const end=Math.min(last,i+bucket-1);let hi=-Infinity,lo=Infinity;
    for(let j=i;j<=end;j++){hi=Math.max(hi,bars[j].high);lo=Math.min(lo,bars[j].low);}
    const open=bars[i].open,close=bars[end].close,cx=x((i+end)/2);
    ctx.strokeStyle=ctx.fillStyle=close>=open?green:red;ctx.beginPath();ctx.moveTo(cx,y(hi));ctx.lineTo(cx,y(lo));ctx.stroke();
    const bw=Math.max(1,Math.min(14,(right-left)*bucket/span*.7));ctx.fillRect(cx-bw/2,Math.min(y(open),y(close)),bw,Math.max(1,Math.abs(y(open)-y(close))));
  }
  for (const {values, period, color} of visibleEmas) {
    if (values[last] === null) continue;
    ctx.save();
    ctx.strokeStyle = getComputedStyle($('chart-area')).getPropertyValue(color).trim();
    ctx.lineWidth = 1.7;
    ctx.beginPath();
    const begin = Math.max(first, period - 1);
    ctx.moveTo(x(begin), y(values[begin]));
    const step = Math.max(1, Math.floor(span / (right - left)));
    for (let i = begin + step; i < last; i += step) ctx.lineTo(x(i), y(values[i]));
    ctx.lineTo(x(last), y(values[last]));
    ctx.stroke();
    ctx.restore();
  }
  if (embedded) {
    for (let i = first; i <= last; i++) for (const fill of bars[i].events || []) {
      const buy = fill.side === 'buy';
      const cx = x(i), cy = y(fill.price), base = buy ? 8 : -8;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx - 5, cy + base);
      ctx.lineTo(cx + 5, cy + base);
      ctx.closePath();
      ctx.fillStyle = buy ? green : red;
      ctx.fill();
      ctx.strokeStyle = style.getPropertyValue('--ink');
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }
  if (tradeTrace && tradeTrace.start >= first && tradeTrace.end <= last) {
    const entryX = x(tradeTrace.start), entryY = y(tradeTrace.entry.price);
    const exitX = x(tradeTrace.end), exitY = y(tradeTrace.exit.price);
    ctx.save();
    ctx.strokeStyle = style.getPropertyValue('--ink');
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(entryX, entryY);
    ctx.lineTo(exitX, exitY);
    ctx.stroke();
    ctx.setLineDash([]);
    for (const [cx, cy] of [[entryX, entryY], [exitX, exitY]]) {
      ctx.beginPath();
      ctx.arc(cx, cy, 8, 0, 2 * Math.PI);
      ctx.stroke();
    }
    ctx.restore();
  }
  ctx.restore();
  if ($('macd').checked) drawMacd(ctx, first, last, x, bottom + 24, plotBottom, left, right, style);
  ctx.fillStyle=muted;
  const ticks=Math.max(2,Math.floor((right-left)/190));
  for(let t=0;t<ticks;t++){const i=Math.min(last,Math.round(start+span*t/(ticks-1)));ctx.textAlign=t===ticks-1?'right':'left';ctx.fillText(date(bars[i].ts).slice(0,16),x(i),height-12);}ctx.textAlign='left';
  $('view-info').textContent=`${date(bars[first].ts)} — ${date(bars[last].ts)} · ${bucket===1?'Individual candles':bucket+' source candles per drawn candle'}`;
}
function zoom(factor,anchor=.5) {const pivot=start+span*anchor;span*=factor;start=pivot-span*anchor;clampView();redraw();}
function hover(event) {
  if(!geometry)return;
  const rect=$('crosshair').getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top;
  const {left,right,top,plotBottom,x,width,height}=geometry;
  const canvas=$('crosshair'),ctx=canvas.getContext('2d'),ratio=devicePixelRatio||1;ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  if(px<left||px>right||py<top||py>plotBottom)return;
  const i=Math.max(0,Math.min(embedded ? replayCursor : bars.length-1,Math.round(start+(px-left)/(right-left)*span))),b=bars[i];
  ctx.strokeStyle=colors().getPropertyValue('--muted');ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(x(i),top);ctx.lineTo(x(i),plotBottom);ctx.moveTo(left,py);ctx.lineTo(right,py);ctx.stroke();ctx.setLineDash([]);
  const emaReadout = emaLines.filter(line => line.control.checked)
    .map(({period, values}) => ` · EMA ${period} ${values[i] === null ? 'warming up' : number(values[i])}`).join('');
  const macdReadout = $('macd').checked
    ? macdValues[i].map((value, k) => ' · '+['MACD', 'Signal', 'Histogram'][k]+' '+number(value)).join('')
    : '';
  $('hover').textContent=`${date(b.ts)} · O ${number(b.open)} H ${number(b.high)} L ${number(b.low)} C ${number(b.close)} · Volume ${number(b.volume)}${emaReadout}${macdReadout}${embedded ? (b.events || []).map(fill => " · "+describeFill(fill)+" @ "+number(fill.price)).join("") : ""}`;
}
async function load(data) {
  if(!data?.dataset?.id)throw Error('Choose a value-area experiment JSON export.');
  const version=++loadVersion;message('Loading historical candles…');
  const response=await api('/api/manual/candles',{dataset:data.dataset.id,start:data.start||'',end:data.end||''});
  if(version!==loadVersion)return;
  if(data.dataset.sha256!==response.fingerprint)throw Error('Dataset fingerprint changed. Re-run the experiment before comparing.');
  bars = response.bars;
  macdValues = calculateMacd(bars);
  for (const line of emaLines) line.values = closingEma(bars, line.period);
  start = 0;
  span = bars.length;
  message(`${response.market} · ${response.timeframe} · ${bars.length.toLocaleString()} candles loaded`);
  redraw();
}
$('overview').onclick=()=>{start=0;span=bars.length;redraw();};$('zoom-in').onclick=()=>zoom(.6);$('zoom-out').onclick=()=>zoom(1/.6);
for (const line of emaLines) line.control.onchange = redraw;
$('macd').onchange=redraw;
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('chart-area').requestFullscreen();}catch(error){message(error.message,true);}};
$('import').onchange=async event=>{try{const file=event.target.files[0];if(file)await load(JSON.parse(await file.text()));}catch(error){message(error.message,true);}};
const overlay=$('crosshair');
overlay.addEventListener('wheel',event=>{if(!bars.length||!geometry)return;event.preventDefault();const rect=overlay.getBoundingClientRect();const anchor=Math.max(0,Math.min(1,(event.clientX-rect.left-geometry.left)/(geometry.right-geometry.left)));
  if (embedded) {
    const factor = Math.exp(Math.max(-.5, Math.min(.5, event.deltaY*.002)));
    if (event.shiftKey) seekReplay(replayCursor + Math.sign(event.deltaY)*span*.12, span);
    else seekReplay(replayCursor, span * factor);
    return;
  }
  if(event.shiftKey){start+=Math.sign(event.deltaY)*span*.12;clampView();redraw();}else zoom(Math.exp(Math.max(-.5,Math.min(.5,event.deltaY*.002))),anchor);
},{passive:false});
overlay.addEventListener('pointerdown',event=>{if(!geometry)return;drag={x:event.clientX,start,cursor:replayCursor};overlay.setPointerCapture(event.pointerId);});
overlay.addEventListener('pointermove',event=>{if(drag){const delta=event.clientX-drag.x;if (embedded) {seekReplay(drag.cursor-delta/(geometry.right-geometry.left)*span,span);return;}start=drag.start-delta/(geometry.right-geometry.left)*span;clampView();redraw();}else hover(event);});
overlay.addEventListener('pointerup',event=>{
  if (embedded && drag && Math.abs(event.clientX-drag.x)<3) {
    const px=event.clientX-overlay.getBoundingClientRect().left;
    if(px>=geometry.left && px<=geometry.right) seekReplay(Math.min(replayCursor,start+(px-geometry.left)*span/(geometry.right-geometry.left)),span);
  }
  drag=null;
});
overlay.addEventListener('pointercancel',()=>drag=null);
overlay.addEventListener('pointerleave',()=>{if(geometry){const ctx=overlay.getContext('2d');ctx.clearRect(0,0,overlay.width,overlay.height);}});
$('chart-area').addEventListener('keydown',event=>{if(!bars.length)return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();if(embedded){seekReplay(replayCursor+(event.key==='ArrowLeft'?-1:1)*span*.2,span);return;}start+=(event.key==='ArrowLeft'?-1:1)*span*.2;clampView();redraw();}});
new ResizeObserver(redraw).observe($('chart-area'));
const area = new URLSearchParams(location.search).get('area');
const suffix = ['balanced50','adaptive1','adaptive2','adaptive3','adaptive4'].includes(area) ? '-'+area : '';
if (embedded) {
  document.body.classList.add('embedded-chart');
  $('view-info').nextElementSibling.textContent = '▲ Buy · ▼ Sell · Click a candle to inspect';
  window.updateBacktest = (points, cursor, windowSize, theme, label, seek, trace) => {
    document.documentElement.dataset.theme = theme || 'light';
    if (points !== bars) {
      bars = points;
      macdValues = calculateMacd(bars);
      for (const line of emaLines) line.values = closingEma(bars, line.period);
    }
    tradeTrace = trace;
    replayCursor = cursor;
    seekReplay = seek;
    describeFill = label;
    span = Math.max(1, Math.min(windowSize, cursor + 1));
    start = Math.max(0, cursor - span + 1);
    $('hover').textContent = 'Hover over a candle or fill to inspect it.';
    redraw();
  };
} else api('/api/research/range-explorer'+suffix).then(load).catch(error=>message(error.message,true));
