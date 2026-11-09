'use strict';
const $ = id => document.getElementById(id);
const canvas = $('chart');
const key = 'gridlab.channel-replay.v1';
const fmt = n => Number(n).toLocaleString('en-US', {maximumFractionDigits: 8});
function stamp(ts) {
  const minutes = Math.round((ts - bars[session.blindOrigin].ts) / 60);
  if (!minutes) return 'T+0m';
  const magnitude = Math.abs(minutes);
  const hours = Math.floor(magnitude / 60);
  return 'T' + (minutes < 0 ? '−' : '+') + (hours ? hours+'h ' : '') + (magnitude%60)+'m';
}
function relativePrice(price) {
  const change = 100 * (price / bars[session.blindOrigin].close - 1);
  return (change > 0 ? '+' : '') + change.toFixed(3) + '%';
}
let datasets = [], bars = [], session, timer, pending = false;
let anchors = [], drawing = false, geometry, pointer, drag, viewEnd, span = 100;
let pickingProfile = false, profileStart = null, profile = null, emaValues = [];
function measures() {
  return session.indicators ??= {bins:48, area:70, period:200, ema:false, range:null, visible:true};
}
function updateProfile() {
  profile = null;
  const {range,bins,area} = measures();
  if (!range || range[1] > session.cursor) return;
  const selected = bars.slice(range[0],range[1]+1);
  let lo = Infinity, hi = -Infinity;
  for (const b of selected) {lo=Math.min(lo,b.low);hi=Math.max(hi,b.high);}
  if (lo===hi) {const pad=Math.max(lo*1e-6,1e-12);lo-=pad;hi+=pad;}
  const width=(hi-lo)/bins, volume=Array(bins).fill(0);
  const bin=p=>Math.min(bins-1,Math.max(0,Math.floor((p-lo)/width)));
  for(const b of selected){
    if(b.high===b.low){volume[bin(b.close)]+=b.volume;continue;}
    for(let j=bin(b.low);j<=bin(b.high);j++){
      const overlap=Math.min(b.high,lo+(j+1)*width)-Math.max(b.low,lo+j*width);
      volume[j]+=b.volume*Math.max(0,overlap)/(b.high-b.low);
    }
  }
  const peak=Math.max(...volume);
  if(!peak)return;
  const center=volume.indexOf(peak), target=volume.reduce((a,b)=>a+b,0)*area/100;
  let left=center,right=center,sum=peak;
  while(sum<target && (left>0 || right+1<bins)){
    if((volume[left-1]??-1)>=(volume[right+1]??-1))sum+=volume[--left];
    else sum+=volume[++right];
  }
  profile={lo,width,volume,peak,left,right,poc:lo+(center+.5)*width,val:lo+left*width,vah:lo+(right+1)*width};
}
function extendEma(){
  const m=measures();
  if(!m.ema)return;
  for(let i=emaValues.length;i<=session.cursor;i++){
    if(i<m.period-1)emaValues.push(null);
    else if(i===m.period-1)emaValues.push(bars.slice(0,m.period).reduce((s,b)=>s+b.close,0)/m.period);
    else emaValues.push(emaValues[i-1]+2/(m.period+1)*(bars[i].close-emaValues[i-1]));
  }
}
function indicatorControls(){
  const m=measures();
  $('daily-ema-visible').checked=!!m.dailyEma;
  $('ema-visible').checked=m.ema;$('ema-period').value=m.period;
  $('vp-bins').value=m.bins;$('vp-area').value=m.area;
  $('vp-toggle').hidden=$('vp-remove').hidden=!m.range;
  $('vp-toggle').textContent=m.visible?'Hide profile':'Show profile';
  $('vp-values').textContent=m.range ? stamp(bars[m.range[0]].ts)+' — '+stamp(bars[m.range[1]].ts)+(profile?' · VAL '+relativePrice(profile.val)+' · POC '+relativePrice(profile.poc)+' · VAH '+relativePrice(profile.vah):' · No traded volume') : '';
}
function notice(text) { $('status').textContent = text; }
function pause() { clearInterval(timer); timer = null; $('play').textContent = '▶ Play'; }
function persist() {
  try { localStorage.setItem(key, JSON.stringify(session)); }
  catch { notice('Replay is running in memory. Browser storage is unavailable.'); }
}
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed.');
  return result;
}
// Build the original 48 price slots in ascending order before trimming by side.
const edgeWeights = [20,11.5,6,3.5,2.5,2,2,1.5,1];
const gridWeights = [...edgeWeights, ...edgeWeights.slice().reverse()];
const topLine = 47;
const edgeLines = 6;
const buyLines = [];
const sellLines = [];
for (let line = 0; line <= topLine; line++) {
  const inMiddle = line >= edgeLines && line <= topLine - edgeLines;
  const isBuy = line < edgeLines || (inMiddle && (line - edgeLines) % 2 === 1);
  (isBuy ? buyLines : sellLines).push(line);
}
buyLines.splice(-6);       // Remove the six highest bids.
sellLines.splice(0, 6);    // Remove the six lowest asks.
// Pair in ascending price order so each buy owns one higher sell.
const weightedLevels = gridWeights.map((weight, index) => ({
  weight,
  fraction: buyLines[index] / topLine,
  sellFraction: sellLines[index] / topLine
}));
function gridLevels(grid) {
  if (!grid) return weightedLevels;
  return grid.sleeves.map((s, k) => {
    const fraction = s.fraction ?? .2*k/3;
    return {fraction, sellFraction:s.sellFraction ?? 1-fraction};
  });
}
// One-time conversion of four-point saves to two fixed prices at the saved candle.
function restoreHorizontalChannels(replay) {
  for (const item of [replay, replay.grid, ...(replay.decisions || [])]) {
    if (item?.points?.length !== 4) continue;
    const [upper, upperEnd, lower, lowerEnd] = item.points;
    function freeze(start, end) {
      const elapsed = replay.cursor - start.i;
      const p = start.i === end.i ? start.p : start.p + elapsed*(end.p-start.p)/(end.i-start.i);
      return {i:start.i, p};
    }
    item.points = [freeze(upper,upperEnd), freeze(lower,lowerEnd)];
  }
}
function workingChannel(grid) {
  const offset = grid.shifts?.at(-1)?.offset ?? 0;
  return [grid.points[1].p + offset, grid.points[0].p + offset];
}
function recordDecision(action, details = {}) {
  session.decisions ??= [];
  session.decisions.push({action, candle: session.cursor, ...details});
}
function canceledChannel() {
  if (session.grid || anchors.length !== 2) return null;
  const actions = session.decisions || [];
  const last = actions.at(-1);
  if (!['cancel','complete'].includes(last?.action)) return null;
  const arm = actions.findLast(d => d.action === 'arm');
  if (!arm || !arm.points.every((p,i)=>p.i===anchors[i].i && p.p===anchors[i].p)) return null;
  return {arm, last};
}
function cancel(reason, candle = session.cursor) {
  recordDecision('cancel', {reason, candle, shifts:session.grid?.shifts});
  const a = session.account;
  session.grid = null;
  a.orders = [];
  if (a.held > 0) ManualEngine.submit(a,'sell',100);
  notice(reason + (a.orders.length ? ' · Holdings sell at the next open.' : ''));
}
let dailyCloses = [];
const dailyEmaCache = new Map();
const minuteSlopeCache = new Map();
function prepareDailyHistory(history) {
  const closes = new Map(history);
  const stride = session.timeframe === '1h' ? 3600 : 300;
  const count = 86400 / stride;
  for(let i=0;i+count<=bars.length;i++){
    if(bars[i].ts%86400!==0) continue;
    const last=bars[i+count-1];
    if(last.ts===bars[i].ts+86400-stride) {
      closes.set(bars[i].ts,last.close);
      i+=count-1;
    }
  }
  dailyCloses=[...closes].sort((a,b)=>a[0]-b[0]);
  dailyEmaCache.clear();
  minuteSlopeCache.clear();
}
function dailyEmaAt(period, ts) {
  let readings=dailyEmaCache.get(period);
  if (!readings) {
    readings=new Map();
    let count=0, sum=0, ema=0, previous;
    for(const [day,close] of dailyCloses){
      if(day!==previous+86400){ count=0; sum=0; }
      previous=day;
      count++;
      let slope;
      if(count<=period){
        sum+=close;
        if(count<period) continue;
        ema=sum/period;
      } else {
        const updated=ema+2/(period+1)*(close-ema);
        slope=100*(updated/ema-1);
        ema=updated;
      }
      // Only a completed UTC day's EMA is available when a candle opens.
      readings.set(day+86400,{value:ema,slope});
    }
    dailyEmaCache.set(period,readings);
  }
  return readings.get(Math.floor(ts/86400)*86400);
}
function slopeFor(period, ts) {
  return dailyEmaAt(period,ts)?.slope;
}
function minuteSlope(minutes,ts){
  const stride=session.timeframe==='1h'?60:5;
  const period=minutes/stride;
  if(!Number.isInteger(period) || period<2) return undefined;
  let readings=minuteSlopeCache.get(minutes);
  if(!readings){
    readings=new Map();
    let count=0,sum=0,ema=0,previous;
    for(const b of bars){
      if(previous+stride*60!==b.ts){count=0;sum=0;}
      previous=b.ts;
      if(++count<=period){sum+=b.close;ema=sum/count;continue;}
      const updated=ema+2/(period+1)*(b.close-ema);
      readings.set(b.ts+stride*60,100*(updated/ema-1));
      ema=updated;
    }
    if(minuteSlopeCache.size>=4) minuteSlopeCache.delete(minuteSlopeCache.keys().next().value);
    minuteSlopeCache.set(minutes,readings);
  }
  return readings.get(ts);
}
function ruleSlope(rule,ts){
  return rule.mode==='minutes' ? minuteSlope(rule.minutes,ts) : slopeFor(rule.period,ts);
}
function ruleOf(grid) {
  return grid.replenishment ?? {mode:grid.replenish===false?'off':'on',period:200,threshold:0};
}
function ruleEnabled(rule,ts) {
  if(rule.mode!=='ema' && rule.mode!=='minutes') return rule.mode==='on';
  const slope=ruleSlope(rule,ts);
  return slope!==undefined && slope>=rule.threshold;
}
const legacyUpperDefault = {mode:'off',period:200,threshold:0};
const edgeDefault = {mode:'on',period:200,threshold:0};
const ruleInputs = {
  trailingExit:['trail-mode','trail-period','trail-threshold'],
  replenishment:['replenish-active','rule-period','rule-threshold'],
  lowerShift:['lower-mode','lower-period','lower-threshold','lower-lines'],
  upperShift:['upper-mode','upper-period','upper-threshold','upper-lines']
};
const minuteInputs={replenishment:'rule-minutes',trailingExit:'trail-minutes',lowerShift:'lower-minutes',upperShift:'upper-minutes'};
function setRuleControls(rule, name='replenishment') {
  const [mode,period,threshold,lines]=ruleInputs[name];
  $(minuteInputs[name]).value=rule.minutes ?? 720;
  if(name==='trailingExit') $('trail-distance').value=rule.distance ?? 1;
  if(lines) $(lines).value=rule.lines ?? 8;
  $(mode).value=rule.mode;
  $(period).value=rule.period;
  $(threshold).value=rule.threshold;
}
// Preferences seed the next grid; execution always reads the armed grid's copy.
const preferencesKey = 'gridlab.replay.grid-settings.v1';
const settingIds = [...Object.values(minuteInputs),'trail-distance','grid-mode','stop-pct','shift-cap','rule',...Object.values(ruleInputs).flat()];
function saveField(input) {
  if (!settingIds.includes(input.id) || input.value === '' || !input.validity.valid) return;
  try {
    const saved=JSON.parse(localStorage.getItem(preferencesKey) || '{}');
    saved[input.id]=input.value;
    localStorage.setItem(preferencesKey,JSON.stringify(saved));
  } catch { notice('Grid settings could not be saved in this browser.'); }
}
$('grid-panel').addEventListener('input',event=>saveField(event.target));
$('grid-panel').addEventListener('change',event=>saveField(event.target));
function restoreGridSettings(replay) {
  let saved={};
  try { saved=JSON.parse(localStorage.getItem(preferencesKey) || '{}') || {}; } catch {}
  // Recover settings from older sessions that stored them only on arm or edit.
  const decisions=replay.decisions || [];
  const armIndex=decisions.findLastIndex(d=>d.action==='arm');
  const last=armIndex<0 ? null : {...decisions[armIndex]};
  if(last) for(const d of decisions.slice(armIndex+1)){
    for(const name of Object.keys(ruleInputs)){
      if(d.action==='update '+name && d.rule) last[name]=d.rule;
    }
  }
  const grid=replay.grid || last;
  $('rule').value=replay.rule ?? 'manual';
  if(grid){
    $('grid-mode').value=grid.shiftOnClose?'shift':'fixed';
    $('stop-pct').value=grid.stopPct ?? 0;
    $('shift-cap').value=grid.maxShift ?? 1;
    setRuleControls(grid.trailingExit ?? legacyUpperDefault,'trailingExit');
    setRuleControls(ruleOf(grid));
    setRuleControls(grid.lowerShift ?? edgeDefault,'lowerShift');
    setRuleControls(grid.upperShift ?? legacyUpperDefault,'upperShift');
  }
  // Apply the requested new-grid default once, without rewriting an armed grid.
  if(!saved.minuteDefaultApplied){
    Object.assign(saved,{'replenish-active':'minutes','rule-minutes':'720','rule-threshold':'0.02',minuteDefaultApplied:true});
    try {localStorage.setItem(preferencesKey,JSON.stringify(saved));} catch {}
  }
  const stride=replay.timeframe==='1h'?60:5;
  for(const id of Object.values(minuteInputs)){
    $(id).min=2*stride;
    $(id).step=stride;
  }
  for(const id of settingIds){
    const input=$(id);
    // Active grids own their settings. Hidden edge rules on a fixed grid
    // can still retain their draft values for the next shifting grid.
    const inactiveEdge=replay.grid && !replay.grid.shiftOnClose &&
      [...ruleInputs.lowerShift,...ruleInputs.upperShift].includes(id);
    if((!replay.grid || inactiveEdge) && saved[id]!=null){
      const previous=input.value;
      input.value=saved[id];
      if(input.value==='' || !input.validity.valid) input.value=previous;
    }
    // Restoring the active grid must not overwrite the new-grid preference.
    const activeReplenishment=replay.grid && [...ruleInputs.replenishment,minuteInputs.replenishment].includes(id);
    if(!activeReplenishment) saveField(input);
  }
  for(const id of Object.values(minuteInputs)){
    const field=$(id);
    if(!field.validity.valid) field.value=Math.min(10080,Math.max(2*stride,Math.ceil((field.valueAsNumber || 720)/stride)*stride));
  }
  replay.rule=$('rule').value;
}
function readRuleControls(name='replenishment') {
  const [mode,period,threshold,lines]=ruleInputs[name];
  const rule={mode:$(mode).value,period:$(period).valueAsNumber,threshold:$(threshold).valueAsNumber};
  if(rule.mode==='minutes'){
    const stride=session.timeframe==='1h'?60:5;
    rule.minutes=$(minuteInputs[name]).valueAsNumber;
    if(!$(minuteInputs[name]).validity.valid || !Number.isFinite(rule.minutes) || rule.minutes<2*stride || rule.minutes%stride!==0){
      notice('Use at least two source candles: choose a multiple of '+stride+' minutes, starting at '+2*stride+'.');
      return null;
    }
  }
  if(name==='trailingExit'){
    const distance=$('trail-distance');
    if(rule.mode!=='off' && !distance.validity.valid){ distance.reportValidity(); return null; }
    rule.distance=distance.validity.valid ? distance.valueAsNumber : 1;
  }
  if(lines){
    rule.lines=$(lines).valueAsNumber;
    if(!Number.isInteger(rule.lines)||rule.lines<1||rule.lines>47){
      $(lines).reportValidity();
      return null;
    }
  }
  if(!Number.isInteger(rule.period)||rule.period<2||rule.period>500||!Number.isFinite(rule.threshold)){
    notice('Enter an EMA period from 2 to 500 days and a numeric slope threshold.');
    return null;
  }
  return rule;
}
function orderState(line, replacementAllowed) {
  if (line.retired) return 'retired';
  if (line.qty > 0) return 'sell';
  if (line.buyId != null && !replacementAllowed) return 'paused';
  return 'buy';
}
function fillGridOrders(grid, b) {
  const a = session.account;
  const rule=ruleOf(grid);
  const replace=ruleEnabled(rule,b.ts);
  const previousCount = a.fills.length;
  const [low,high] = workingChannel(grid);
  // Each sleeve spends only its own proceeds. Limits use the channel levels
  // known before this candle, with at most one fill per sleeve on this bar.
  const levels = gridLevels(grid);
  const slots = new Map();
  a.orders = [];
  const trail=grid.trailingExit;
  const bullish=(trail?.mode==='ema' || trail?.mode==='minutes') && ruleSlope(trail,b.ts)>trail.threshold;
  const trailUpdates=[];
  let blockedTouches=0;
  grid.sleeves.forEach((s,k) => {
    const side=orderState(s,replace);
    if (side==='paused' || side==='retired') {
      if (b.low<=low+levels[k].fraction*(high-low)) blockedTouches++;
      return;
    }
    const width = high-low;
    const order = s.qty > 0
      ? {id:a.nextId++,side:'sell',price:low+levels[k].sellFraction*width,stopPrice:s.stopPrice,qty:s.qty,targets:[],parent:s.buyId}
      : {id:a.nextId++,side:'buy',price:low+levels[k].fraction*width,budget:s.cash,targets:[]};
    if(side==='sell' && (s.trailDistance || bullish)){
      // Only a stop known before the candle can execute during it.
      const target=order.price;
      order.stopOnly=true;
      order.price=null;
      if(s.trailStop>0 && s.trailStop>=(s.stopPrice || 0)){
        order.stopPrice=s.trailStop;
        order.stopReason='trailing_stop';
      }
      if(s.trailDistance || b.high>=target) trailUpdates.push(s);
    }
    a.orders.push(order);
    slots.set(order.id,k);
  });
  ManualEngine.advance(a,b);
  const freshStops = [];
  function finishSell(s, fill) {
    Object.assign(s,{cash:fill.qty*fill.price-fill.fee,qty:0,stopPrice:null,trailStop:null,trailDistance:null,retired:rule.mode==='off'});
  }
  for (const f of a.fills.slice(previousCount)) {
    const index = slots.get(f.id), s = grid.sleeves[index];
    if (f.side==='sell') { finishSell(s,f); continue; }
    s.cash=0; s.qty=f.qty; s.buyId=f.id;
    s.stopPrice=grid.stopPct ? f.price*(1-grid.stopPct/100) : null;
    if (s.stopPrice && b.low<=s.stopPrice) {
      // A buy limit crossed on the way down must fill before its lower stop.
      const id=a.nextId++;
      slots.set(id,index);
      freshStops.push({id,side:'sell',price:null,qty:s.qty,parent:f.id});
    }
  }
  a.orders=[];
  for (const order of freshStops) {
    const s=grid.sleeves[slots.get(order.id)];
    a.orders=[order];
    ManualEngine.advance(a,{...b,open:s.stopPrice});
    const fill=a.fills.at(-1);
    fill.reason='stop';
    finishSell(s,fill);
  }
  for(const s of trailUpdates){
    if(!s.qty) continue;
    s.trailDistance ??= trail.distance;
    s.trailStop=Math.max(s.trailStop || 0,b.high*(1-s.trailDistance/100));
  }
  grid.lastCheck={ts:b.ts,blockedTouches};
  return a.fills.length - previousCount;
}
function completeGrid(grid,candle) {
  recordDecision('complete',{candle,shifts:grid.shifts,reason:'All lines completed their buy–sell cycle'});
  session.grid = null;
  notice('Grid complete · All sale proceeds are in cash.');
}
function shiftAfterClose(grid, candle, close, low, high) {
  const spacing = (high-low) * (gridLevels(grid)[1]?.fraction ?? 1);
  let steps;
  if (close >= low && close <= high) {
    const lower=grid.lowerShift ?? edgeDefault, upper=grid.upperShift ?? legacyUpperDefault;
    const lowerLines=lower.lines ?? 8, upperLines=upper.lines ?? 8;
    const closeTime=bars[candle].ts+(session.timeframe==='1h'?3600:300);
    const down=close<=low+lowerLines*spacing && ruleEnabled(lower,closeTime);
    const up=close>=high-upperLines*spacing && ruleEnabled(upper,closeTime);
    if (!up && !down) return;
    // Overlapping eligible zones shift once, toward the nearer edge (lower on a tie).
    steps = up && (!down || high-close < close-low) ? upperLines : -lowerLines;
  } else {
    const distance = close > high ? close-high : low-close;
    const catchUp = Math.min(grid.maxShift ?? 1, Math.max(1, Math.ceil(distance/spacing)));
    steps = close > high ? catchUp : -catchUp;
  }
  // Keep the shifted lower boundary positive, even on exceptionally wide grids.
  steps = Math.max(steps, 1-Math.ceil(low/spacing));
  if (!steps) return;
  const offset = (grid.shifts.at(-1)?.offset ?? 0) + steps*spacing;
  grid.shifts.push({from:candle+1,offset});
  notice(`Channel shifted ${Math.abs(steps)} ${Math.abs(steps)===1?'line':'lines'} ${steps>0?'up':'down'} for the next candle.`);
}
function next() {
  if (!session || pending || drawing || pickingProfile || session.cursor >= bars.length-1) { pause(); return; }
  const a = session.account, i = session.cursor+1, b = bars[i];
  let grid = session.grid;
  if (grid && (workingChannel(grid)[0] <= 0 || grid.points[0].p <= grid.points[1].p)) {
    cancel('Invalid channel price');
    grid = null;
  }
  if (grid) {
    const [low,high] = workingChannel(grid);
    fillGridOrders(grid,b);
    if (grid.sleeves.every(s => s.retired)) {
      completeGrid(grid,i);
    } else if (grid.shiftOnClose) {
      shiftAfterClose(grid,i,b.close,low,high);
    } else if (grid.rule==='outside' && (b.close<low || b.close>high)) cancel('Close outside the channel', i);
  } else ManualEngine.advance(a,b);
  session.cursor = viewEnd = i;
  if (i===bars.length-1) { pause(); notice('End of data · Holdings valued at the last close.'); }
  pointer = null; $('readout').textContent='';
  persist(); render();
}
function play() {
  if (timer) return pause();
  if (!session || drawing || pickingProfile || pending || session.cursor>=bars.length-1) return;
  timer = setInterval(next,Number($('speed').value)); $('play').textContent='Ⅱ Pause';
}
function marketLabel(d){
  return [d.market,d.timeframe,d.venue || 'Local'].join(' · ');
}
function showHistory(selectedId){
  const picker=$('dataset');
  const parts=datasets.filter(d=>marketLabel(d)===$('replay-market').value)
    .sort((a,b)=>a.start.localeCompare(b.start) || a.end.localeCompare(b.end) || a.id.localeCompare(b.id));
  picker.replaceChildren();
  if(!parts.length)return;
  const longest=parts.reduce((best,d)=>d.bars>=best.bars?d:best);
  parts.forEach((d,i)=>{
    const label=['Part '+(i+1)+' of '+parts.length,d.bars.toLocaleString('en-US')+' candles'];
    if(parts.length>1 && d===longest) label.push('longest');
    if($('show-history-dates').checked) label.push(d.start.slice(0,10)+' – '+d.end.slice(0,10));
    picker.add(new Option(label.join(' · '),d.id));
  });
  picker.value=parts.some(d=>d.id===selectedId)?selectedId:longest.id;
}
function showMarket() {
  const option = datasets.find(d=>d.id===$('dataset').value);
  if(option) $('capital').value = ['BTC','ETH'].includes(option.market.split('/')[1])?'1':'10000';
}
// Old saves use original dataset indices. Offset them once, preserving drawings,
// decision times, shift history and indicator endpoints. Fill timestamps are absolute.
function attachPrefix(replay,count) {
  replay.cursor += count;
  replay.blindOrigin += count;
  const points = new Set(replay.points || []);
  const shifts = new Set(replay.grid?.shifts || []);
  if (replay.grid) {
    replay.grid.armed += count;
    for (const point of replay.grid.points) points.add(point);
  }
  for (const decision of replay.decisions || []) {
    decision.candle += count;
    for (const p of decision.points || []) points.add(p);
    for (const shift of decision.shifts || []) shifts.add(shift);
  }
  for (const p of points) p.i += count;
  for (const shift of shifts) shift.from += count;
  if (replay.indicators?.range) replay.indicators.range = replay.indicators.range.map(i=>i+count);
  replay.prefixCount = count;
}
async function load(saved) {
  pause(); pending=true; render();
  try {
    const id = saved ? saved.dataset : $('dataset').value;
    const data = await api('/api/manual/candles',{dataset:id,prehistory:saved?.prefixCount ?? 'random'});
    if (saved && saved.fingerprint!==data.fingerprint) throw Error('Saved dataset changed. Start a new replay.');
    if (data.bars.length<3) throw Error('Choose a dataset with more candles.');
    const originalLength = data.bars.length - data.prefix_count;
    const start = Math.min(99,Math.floor(originalLength/3));
    const headroom = Math.max(1,originalLength-start-Math.min(288,start));
    const fresh = saved || {dataset:id,fingerprint:data.fingerprint,market:data.market,timeframe:data.timeframe,
      cursor:start+Math.floor(Math.random()*headroom),account:ManualEngine.create(Number($('capital').value),2,1),grid:null,points:[],rule:$('rule').value};
    session=fresh; bars=data.bars; fillLimit=25;
    // Old sessions did not record their initial cursor. Anchor them once at migration.
    session.blindOrigin ??= session.cursor;
    if (session.prefixCount == null) attachPrefix(session,data.prefix_count);
    restoreHorizontalChannels(session);
    prepareDailyHistory(data.daily_history || []);
    restoreGridSettings(session);
    $('readout').textContent='';
    $('show-usd').checked=false;
    pickingProfile=false;profileStart=null;emaValues=[];updateProfile();indicatorControls(); viewEnd=session.cursor; span=100;
    anchors=session.points||[]; drawing=false; pointer=null;
    const market=datasets.find(d=>d.id===id) ?? datasets.find(d=>d.market===data.market && d.timeframe===data.timeframe);
    if(market) $('replay-market').value=marketLabel(market);
    showHistory(id);
    $('capital').value=session.account.capital;
    $('session-menu').hidePopover(); notice(session.grid?'Grid active':'Draw a channel when you see a range.');
    persist();
  } catch(e) { notice(e.message); }
  finally { pending=false; render(); }
}
// Rebuild from the persisted fill ledger, including fills from older sessions.
let rateCache;
let fillLimit=25;
function renderVolume() {
  const quote = session.market.split('/')[1];
  if (!rateCache || rateCache.quote !== quote) {
    rateCache = {quote, rates:new Map(), retry:new Map(), busy:false};
  }
  const cache = rateCache, missing = new Set();
  let total = 0, usd = 0, incomplete = false;
  for (const fill of session.account.fills) {
    const notional = fill.qty * fill.price;
    total += notional;
    const rate = quote === 'USD' ? 1 : cache.rates.get(fill.ts);
    if (rate !== undefined) usd += notional * rate;
    else {
      incomplete = true;
      if (!(cache.retry.get(fill.ts) > Date.now())) missing.add(fill.ts);
    }
  }
  const usdText = incomplete ? 'USD pending' : usd.toLocaleString('en-US',{style:'currency',currency:'USD'});
  $('traded-volume').textContent = ' · Traded volume '+fmt(total)+' '+quote+(quote==='USD'||!$('show-usd').checked?'':' ('+usdText+')');
  if (!$('show-usd').checked || cache.busy || !missing.size) return;
  const timestamps = Array.from(missing).slice(0,100);
  cache.busy = true;
  for (const ts of timestamps) cache.retry.set(ts, Date.now()+60000);
  api('/api/manual/usd-rates',{quote,timestamps}).then(rates=>{
    for (const [ts,rate] of Object.entries(rates)) cache.rates.set(Number(ts),rate);
  }).catch(()=>{}).finally(()=>{
    cache.busy = false;
    if (cache===rateCache) renderVolume();
  });
}
function render() {
  const ended=!session || session.cursor>=bars.length-1;
  $('arm').disabled=pending||ended||drawing||pickingProfile||anchors.length!==2||!!session.grid||!!session.account.orders.length;
  $('arm').hidden=!!session?.grid;
  $('rule').disabled=!!session?.grid?.shiftOnClose;
  $('grid-replenishment').hidden=$('arm').disabled;
  $('grid-exits').hidden=$('arm').disabled;
  const shifting=session?.grid ? session.grid.shiftOnClose : $('grid-mode').value==='shift';
  $('grid-settings').hidden=!session?.grid && $('arm').disabled;
  $('fixed-exit').hidden=shifting;
  $('catchup-row').hidden=!shifting;
  if ($('grid-settings').hidden && $('grid-panel').matches(':popover-open')) $('grid-panel').hidePopover();
  $('shift-cap').disabled=!$('grid-mode').value.startsWith('shift');
  $('live-replenishment').hidden=!session?.grid && $('arm').disabled;
  $('lower-rule-editor').hidden=$('upper-rule-editor').hidden=!shifting;
  for(const [name,[mode,period]] of Object.entries(ruleInputs)){
    const minute=$(mode).value==='minutes', active=minute || $(mode).value==='ema';
    const fields=$(period).closest('.rule-params');
    fields.hidden=!active;
    $(period).parentElement.hidden=minute;
    $(minuteInputs[name]).parentElement.hidden=!minute;
    if(name!=='trailingExit') fields.querySelector('small').textContent=minute?
      'EMA change per completed source candle. Chart zoom does not affect this rule.':
      'Compares the last two completed daily EMA values. At 0%, a flat or rising EMA enables the rule.';
  }
  $('replenish-active').disabled=pending || ended;
  $('cancel').hidden=!session?.grid;
  $('draw').disabled=pending||ended||pickingProfile||!!session?.grid;
  $('draw').textContent=drawing?'Discard drawing':anchors.length?'Redraw channel':'Draw channel';
  $('play').disabled=$('step').disabled=pending||ended||drawing||pickingProfile;
  $('new').disabled=pending;
  $('indicators').disabled=!session||pending||drawing;
  $('vp-select').textContent=pickingProfile?'Cancel selection':'Select candle range';
  if (!session) return;
  extendEma();
  const a=session.account, bar=bars[session.cursor], quote=session.market.split('/')[1];
  $('chart-interval').value=session.chartInterval||session.timeframe;
  $('chart-interval').querySelector('[value="5m"]').disabled=session.timeframe==='1h';
  const canceled=canceledChannel();
  $('grid-state').textContent=session.grid ? (session.grid.replenish===false ? 'Grid active · One cycle per line' : 'Grid active · Replenishing buys') : canceled
    ? (canceled.last.action==='complete'?'Grid complete · ':'Grid canceled · ')+canceled.last.reason+' · Candle '+stamp(bars[canceled.last.candle].ts)+(a.orders.length?' · Sale pending':'')
    : 'Not armed';
  if (!session.grid) {
    const reason=pending?'Loading candles':ended?'Replay ended':a.orders.length?'Sale pending at next open':
      pickingProfile?'Finish selecting the volume profile':drawing?(anchors.length?'Click the lower price':'Click the upper price'):
      anchors.length===2?'Ready to arm · Click Arm grid':'Draw a channel to place orders';
    $('grid-state').textContent += ' · '+reason;
  }
  $('grid-state').dataset.active=String(!!session.grid && !ended);
  $('rule-readout').hidden=!session.grid;
  if (session.grid) {
    const rule=ruleOf(session.grid);
    const nextTime=bar.ts+(session.timeframe==='1h'?3600:300);
    const replace=ruleEnabled(rule,nextTime);
    const emaLabel=rule.mode==='minutes'?rule.minutes+'-minute EMA':rule.period+'-day EMA';
    if (rule.mode==='ema' || rule.mode==='minutes') {
      const slope=ruleSlope(rule,nextTime);
      $('grid-state').textContent=`Grid active · Replacement buys ${replace?'ON':'OFF'} · ${emaLabel}: ${slope===undefined?'warming up':slope.toFixed(4)+'%'} (minimum ${rule.threshold}%)`;
    }
    const counts={buy:0,sell:0,paused:0,retired:0};
    for (const line of session.grid.sleeves) counts[orderState(line,replace)]++;
    $('rule-readout').textContent=$('grid-state').textContent;
    const status=ended?'Replay ended': 'Grid active';
    $('grid-state').textContent=`${status} · ${counts.buy} working buys / ${counts.sell} working sells`;
    const trailing=session.grid.sleeves.filter(s=>s.qty && s.trailDistance).length;
    if(trailing) $('grid-state').textContent+=` · ${trailing} trailing`;
    const trail=session.grid.trailingExit;
    if(trail?.mode==='ema' || trail?.mode==='minutes'){
      const slope=ruleSlope(trail,nextTime);
      $('rule-readout').textContent+=slope>trail.threshold?' · New sell-line touches start trails':
        slope===undefined?' · Limit sells (EMA warming up)':' · Limit sells (EMA below or at threshold)';
    }
    if(counts.paused){
      const slope=ruleSlope(rule,nextTime);
      const reason=slope===undefined?emaLabel+' warming up':`${emaLabel} ${slope.toFixed(4)}% below ${rule.threshold}%`;
      $('grid-state').textContent+=` · ${counts.paused} replacement buys paused: ${reason}`;
    }
    if(counts.retired) $('grid-state').textContent+=` · ${counts.retired} lines finished (replenishment off)`;
    const check=session.grid.lastCheck;
    if(check?.ts===bar.ts && check.blockedTouches)
      $('grid-state').textContent+=` · This candle reached ${check.blockedTouches} disabled buy level(s)`;
  }
  if (session.grid?.stopPct) $('rule-readout').textContent += ' · Stop '+session.grid.stopPct+'%';
  if (session.grid?.shiftOnClose) {
    const atClose=bar.ts+(session.timeframe==='1h'?3600:300);
    for (const [edge, rule] of [['Lower', session.grid.lowerShift ?? edgeDefault], ['Upper', session.grid.upperShift ?? legacyUpperDefault]]) {
      const state=ruleEnabled(rule,atClose)?'ON':'OFF';
      const warming=(rule.mode==='ema' || rule.mode==='minutes') && ruleSlope(rule,atClose)===undefined;
      $('rule-readout').textContent += ` · ${edge}-${rule.lines ?? 8} shift ${state}${warming?' (warming up)':''}`;
    }
    $('rule-readout').textContent += ` · Outside: up to ${session.grid.maxShift ?? 1} lines/close`;
  }
  $('market').textContent=session.market+' · '+session.timeframe+' replay';
  $('time').textContent=stamp(bar.ts)+' · Price change from reference close';
  const equity=a.cash+a.held*bar.close;
  $('account').textContent='Equity '+fmt(equity)+' '+quote+'   ·   P&L '+fmt(equity-a.capital)+' '+quote+'   ·   Fees '+fmt(a.fees)+' '+quote;
  renderVolume();
  const latest=a.fills.at(-1);
  $('fillcount').textContent='Trade log · '+a.fills.length+' fills'+
    (latest ? ' · Last '+latest.side+' '+stamp(latest.ts)+' @ '+relativePrice(latest.price) : '');
  $('older-fills').hidden=a.fills.length<=fillLimit;
  $('fills').replaceChildren(...a.fills.slice(-fillLimit).reverse().map(f=>{
    const row=document.createElement('button');
    row.type='button'; row.title='Locate this fill on the chart';
    row.onclick=()=>{
      pause();
      const index=bars.findIndex(b=>b.ts===f.ts);
      if(index<0)return;
      const factor=hourlyView()?12:1;
      viewEnd=Math.min(session.cursor,index+Math.floor(span*factor/2));
      chart();
      $('readout').textContent='Fill #'+f.id+' · '+f.side+' · '+stamp(f.ts)+' @ '+relativePrice(f.price);
      canvas.scrollIntoView({block:'nearest'});
    };
    row.textContent='#'+f.id+' · '+stamp(f.ts)+' · '+(f.reason==='stop'?'stop-loss sell':f.reason==='trailing_stop'?'trailing-stop sell':f.side)+' '+fmt(f.qty*f.price)+' '+quote+' @ '+relativePrice(f.price);return row;
  }));
  chart();
}
function hourlyView() {
  return session.timeframe === '5m' && session.chartInterval === '1h';
}
// Keep drawing coordinates in source-candle indices on both chart intervals.
function candleGroups(first, last, hourly) {
  const groups = [];
  for (let i=first; i<=last; i++) {
    const b=bars[i], ts=hourly ? Math.floor(b.ts/3600)*3600 : b.ts;
    const previous=groups.at(-1);
    if (previous && previous.ts===ts) {
      previous.high=Math.max(previous.high,b.high);
      previous.low=Math.min(previous.low,b.low);
      previous.close=b.close;
      previous.last=i;
    } else groups.push({ts,open:b.open,high:b.high,low:b.low,close:b.close,first:i,last:i});
  }
  return groups;
}
function chart() {
  if(!session) return;
  const w=canvas.clientWidth,h=canvas.clientHeight,r=devicePixelRatio||1,ctx=canvas.getContext('2d');
  canvas.width=w*r;canvas.height=h*r;ctx.scale(r,r);
  const hourly=hourlyView(), factor=hourly?12:1;
  let first=Math.max(0,viewEnd-span*factor+1);
  while(hourly && first>0 && Math.floor(bars[first-1].ts/3600)===Math.floor(bars[first].ts/3600)) first--;
  const left=10,right=w-84,top=34,bottom=h-30;
  const dx=(right-left)/(viewEnd-first+1+9*factor),x=i=>left+(i-first+.5)*dx;
  const visible=bars.slice(first,viewEnd+1);
  let low=Math.min(...visible.map(b=>b.low)),high=Math.max(...visible.map(b=>b.high));
  if(measures().ema){
    for(let i=first;i<=viewEnd;i++) if(emaValues[i]!=null){low=Math.min(emaValues[i],low);high=Math.max(emaValues[i],high);}
  }
  const dailyLevels=measures().dailyEma ? visible.map(b=>dailyEmaAt(200,b.ts)?.value) : [];
  for(const value of dailyLevels){
    if(value!=null){low=Math.min(low,value);high=Math.max(high,value);}
  }
  const pad=(high-low)*.12||high*.001;low-=pad;high+=pad;
  const y=p=>bottom-(p-low)/(high-low)*(bottom-top);
  const groups=candleGroups(first,viewEnd,hourly);
  geometry={first,left,right,top,bottom,dx,low,high,groups};
  function stroke(x1,y1,x2,y2,color,width=1) {
    ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();
  }
  ctx.font='11px monospace';ctx.fillStyle='#a0b1ac';
  const priceIntervals = Math.max(8, Math.floor((bottom-top)/32));
  for(let j=0; j<=priceIntervals; j++){
    const price=low+j*(high-low)/priceIntervals;
    stroke(left,y(price),right,y(price),'#30403e');
    ctx.fillText(relativePrice(price),right+5,y(price)+4);
  }
  const candleSeconds = session.timeframe === '1h' ? 3600 : 300;
  const desiredSeconds = candleSeconds * 72 / dx;
  const timeSteps = [300,600,900,1800,3600,7200,10800,21600,43200,86400,172800,604800];
  const step = timeSteps.find(seconds=>seconds>=Math.max(candleSeconds,desiredSeconds)) ?? timeSteps.at(-1);
  let lastLabelRight = -Infinity;
  const originTs=bars[session.blindOrigin].ts;
  for (let i=first;i<=viewEnd;i++){
    const ts=bars[i].ts;
    if (i>first && Math.floor((ts-originTs)/step)===Math.floor((bars[i-1].ts-originTs)/step)) continue;
    const px=x(i);
    stroke(px,top,px,bottom,'#293937');
    const label=stamp(ts);
    const labelWidth=ctx.measureText(label).width;
    const labelX=Math.max(left,px-labelWidth/2);
    if(labelX>lastLabelRight+6 && labelX+labelWidth<=right){
      ctx.fillText(label,labelX,h-9);
      lastLabelRight=labelX+labelWidth;
    }
  }
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  const m=measures();
  if(m.range && m.visible && profile){
    const [a,b]=m.range;
    const x1=Math.max(left,x(a)-dx/2),x2=Math.min(right,x(b)+dx/2);
    if(x2>x1){
      const histogramWidth=Math.min(160,x2-x1);
      profile.volume.forEach((v,j)=>{
        ctx.fillStyle=j>=profile.left && j<=profile.right?'#5294ac99':'#627b825c';
        const price=profile.lo+j*profile.width;
        ctx.fillRect(x1,y(price+profile.width),histogramWidth*v/profile.peak,Math.max(1,y(price)-y(price+profile.width)));
      });
      for(const idx of [a,b])stroke(x(idx),top,x(idx),bottom,'#698890');
    }
    if (viewEnd>=a){
      for(const [name,price] of [['VAL',profile.val],['POC',profile.poc],['VAH',profile.vah]]){
        const py=y(price),col=name==='POC'?'#f2ce81':'#8bc2d2';
        ctx.setLineDash(name==='POC'?[]:[4,4]);stroke(Math.max(left,x(a)),py,right,py,col);ctx.setLineDash([]);
        ctx.fillStyle=col;ctx.fillText(name,right-26,py-3);
      }
    }
  }
  if(pickingProfile && profileStart!==null)stroke(x(profileStart),top,x(profileStart),bottom,'#f2ce81',2);
  for(const b of groups){
    const cx=x((b.first+b.last)/2),bodyWidth=dx*(b.last-b.first+1);
    const col=b.close>=b.open?'#7edaba':'#ef987c';
    stroke(cx,y(b.low),cx,y(b.high),col);
    ctx.fillStyle=col;
    ctx.fillRect(cx-bodyWidth*.32,Math.min(y(b.open),y(b.close)),Math.max(1,bodyWidth*.64),Math.max(1,Math.abs(y(b.open)-y(b.close))));
  }
  if(m.ema){
    for(let i=Math.max(first+1,m.period);i<=viewEnd;i++)
      stroke(x(i-1),y(emaValues[i-1]),x(i),y(emaValues[i]),'#b79ded',1.6);
  }
  dailyLevels.forEach((value,k)=>{
    if(value==null)return;
    const left=x(first+k)-dx/2;
    const previous=dailyLevels[k-1];
    if(previous!=null && previous!==value) stroke(left,y(previous),left,y(value),'#f5c76e',2);
    stroke(left,y(value),left+dx,y(value),'#f5c76e',2);
  });
  const points=anchors.slice();
  if(drawing && pointer) points.push(pointer);
  const widthLabel=$('channel-width');
  const upper=points[0]?.p, lower=points[1]?.p;
  widthLabel.hidden=!(lower>0 && upper>lower);
  if(!widthLabel.hidden){
    const widthPct=100*(upper-lower)/((upper+lower)/2);
    widthLabel.textContent='Starting width '+widthPct.toFixed(2)+'%';
  }
  const far=viewEnd+8*factor;
  const shifts=session.grid?.shifts ?? canceledChannel()?.last.shifts ?? [];
  function drawShifted(from,to,price,color,width){
    let begin=Math.max(first,from),offset=0;
    if(begin>to)return;
    for(const change of shifts){
      if(change.from>to)break;
      if(change.from>begin){
        stroke(x(begin),y(price+offset),x(change.from),y(price+offset),color,width);
        stroke(x(change.from),y(price+offset),x(change.from),y(price+change.offset),color,width);
        begin=change.from;
      }
      offset=change.offset;
    }
    stroke(x(begin),y(price+offset),x(to),y(price+offset),color,width);
  }
  points.forEach((point,index)=>{
    const color=index===0?'#e7bb75':'#87bee6';
    drawShifted(first,far,point.p,color,2);
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(x(point.i),y(point.p),4,0,2*Math.PI);ctx.fill();
  });
  if(anchors.length===2){
    const canceled=canceledChannel();
    const from=session.grid?.armed??canceled?.arm.candle??Math.max(...anchors.map(p=>p.i));
    const lineEnd=canceled?Math.min(far,canceled.last.candle):far;
    ctx.setLineDash([3,5]);
    const levels = session.grid ? gridLevels(session.grid) : canceled
      ? (canceled.arm.levels ?? Array.from({length:10},(_,k)=>({fraction:k/19,sellFraction:1-k/19})))
      : weightedLevels;
    levels.forEach(({fraction,sellFraction},k)=>{
      if (session.grid?.sleeves[k].retired || lineEnd<from) return;
      for (const [f,color] of [[fraction,'#668da7'],[sellFraction,'#ac936a']]) {
        if (f===0 || f===1) continue;
        const price=anchors[1].p+(anchors[0].p-anchors[1].p)*f;
        drawShifted(from,lineEnd,price,color);
      }
    });
    ctx.setLineDash([]);
  }
  // Keep every ledger fill on its execution candle, including those that share
  // a price. Badges sit outside the wick so candle bodies cannot obscure them.
  const fillsByCandle = new Map();
  for (let k=session.account.fills.length-1;k>=0;k--) {
    const f=session.account.fills[k];
    if (f.ts<bars[first].ts) break;
    if (f.ts>bars[viewEnd].ts) continue;
    const ts=hourly ? Math.floor(f.ts/3600)*3600 : f.ts;
    if (!fillsByCandle.has(ts)) fillsByCandle.set(ts,{buy:[],sell:[]});
    fillsByCandle.get(ts)[f.side].push(f);
  }
  for (const candle of groups) {
    const fills=fillsByCandle.get(candle.ts);
    if (!fills) continue;
    const cx=x((candle.first+candle.last)/2);
    for (const side of ['buy','sell']) {
      if (!fills[side].length) continue;
      const buy=side==='buy', ink=buy?'#98eaff':'#ffce74';
      for (const fill of fills[side]) {
        ctx.beginPath();ctx.arc(cx,y(fill.price),2.8,0,2*Math.PI);
        ctx.fillStyle=ink;ctx.fill();ctx.strokeStyle='#101818';ctx.lineWidth=1.3;ctx.stroke();
      }
      const label=(buy?'B':'S')+(fills[side].length>1?'×'+fills[side].length:'');
      ctx.font='bold 11px monospace';
      const width=ctx.measureText(label).width+6;
      const cy=Math.max(top+8,Math.min(bottom-8,buy?y(candle.low)+12:y(candle.high)-12));
      const bx=Math.max(left,Math.min(right-width,cx-width/2));
      ctx.fillStyle='#101818';ctx.fillRect(bx,cy-7,width,14);
      ctx.strokeStyle=ink;ctx.lineWidth=1;ctx.strokeRect(bx,cy-7,width,14);
      ctx.fillStyle=ink;ctx.fillText(label,bx+3,cy+4);
    }
  }
  geometry.fillsByCandle=fillsByCandle;
  ctx.restore();
}
function position(e){
  const rect=canvas.getBoundingClientRect(),px=e.clientX-rect.left,py=e.clientY-rect.top;
  const g=geometry;
  if(!g||px<g.left||px>g.right||py<g.top||py>g.bottom)return null;
  const i=g.first+Math.floor((px-g.left)/g.dx);
  return i>viewEnd?null:{i,p:g.low+(g.bottom-py)/(g.bottom-g.top)*(g.high-g.low)};
}
$('draw').onclick=()=>{
  pause();drawing=!drawing;anchors=[];session.points=[];
  recordDecision('clear drawing');pointer=null;viewEnd=session.cursor;
  notice(drawing?'Click the upper channel price.':'Drawing cleared.');persist();render();
};
canvas.onpointerdown=e=>{
  const pt=position(e);if(!pt||pending)return;
  if(pickingProfile){
    if(profileStart===null){profileStart=pt.i;notice('Click the other end of the volume profile. Escape cancels.');chart();return;}
    const m=measures();m.range=[Math.min(profileStart,pt.i),Math.max(profileStart,pt.i)];m.visible=true;
    pickingProfile=false;profileStart=null;updateProfile();indicatorControls();persist();render();
    notice(profile?'Profile fixed · POC '+relativePrice(profile.poc)+' · VAL '+relativePrice(profile.val)+' · VAH '+relativePrice(profile.vah):'Selected candles have no traded volume.');
    return;
  }
  if(!drawing){drag={x:e.clientX,end:viewEnd};canvas.setPointerCapture(e.pointerId);return;}
  if(pt.p<=0 || (anchors.length && pt.p>=anchors[0].p)){
    notice(anchors.length?'Click a positive price below the upper boundary.':'Click a positive upper price.');
    return;
  }
  anchors.push(pt);
  pointer=null;
  if(anchors.length===2){
    drawing=false;session.points=anchors;persist();notice('Channel ready · Dashed lines preview the grid.');
  }else notice('Click the lower channel price.');
  render();
};
canvas.onpointermove=e=>{
  if(!geometry)return;
  if(drag){viewEnd=Math.max(Math.min(span-1,session.cursor),Math.min(session.cursor,drag.end-Math.round((e.clientX-drag.x)/geometry.dx)));chart();return;}
  pointer=position(e);
  const b=pointer&&geometry.groups.find(group=>group.first<=pointer.i && pointer.i<=group.last);
  $('readout').textContent=b?stamp(b.ts)+(hourlyView() && b.last-b.first+1<12?' · partial hour':'')+'   O '+relativePrice(b.open)+'  H '+relativePrice(b.high)+'  L '+relativePrice(b.low)+'  C '+relativePrice(b.close):'';
  if(b){
    const ema=dailyEmaAt(200,b.ts);
    $('readout').textContent+=' · 200-day EMA';
    if(ema?.slope!=null) $('readout').textContent+=' daily change '+(ema.slope>0?'+':'')+ema.slope.toFixed(4)+'%';
    $('readout').textContent+=ema ? ' · EMA level vs replay start '+relativePrice(ema.value) : ' unavailable';
  }
  const fills=b && geometry.fillsByCandle?.get(b.ts);
  if(fills) $('readout').textContent += ' · '+fills.buy.length+' buys / '+fills.sell.length+' sells';
  if(drawing)chart();
};
canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
canvas.onpointerleave=()=>{pointer=null;$('readout').textContent='';if(drawing)chart();};
$('chart-interval').onchange=()=>{
  if (!session) return;
  pause();
  session.chartInterval=$('chart-interval').value;
  pointer=null;drag=null;$('readout').textContent='';
  persist();render();
};
function zoom(f){span=Math.max(20,Math.min(500,Math.round(span*f)));chart();}
canvas.onwheel=e=>{e.preventDefault();zoom(e.deltaY<0?.8:1.25);};
$('plus').onclick=()=>zoom(.8);$('minus').onclick=()=>zoom(1.25);
$('latest').onclick=()=>{if(session){viewEnd=session.cursor;chart();}};
$('grid-mode').onchange=render;
$('arm').onclick=()=>{
  pause();
  if(anchors.length!==2 || anchors[1].p<=0 || anchors[0].p<=anchors[1].p){notice('Redraw the upper and lower prices.');return;}
  session.rule=$('rule').value;
  const mode = $('grid-mode').value;
  const shiftOnClose = mode === 'shift' || mode === 'shift-repeat';
  const replenishment=readRuleControls();
  const lowerShift=shiftOnClose ? readRuleControls('lowerShift') : edgeDefault;
  const upperShift=shiftOnClose ? readRuleControls('upperShift') : edgeDefault;
  const trailingExit=readRuleControls('trailingExit');
  if(!trailingExit || !replenishment || !lowerShift || !upperShift)return;
  const replenish=replenishment.mode!=='off';
  const stopPct=$('stop-pct').valueAsNumber, maxShift=$('shift-cap').valueAsNumber;
  if(!Number.isFinite(stopPct)||stopPct<0||stopPct>=100||!Number.isInteger(maxShift)||maxShift<1||maxShift>47){
    notice('Use a stop from 0 to below 100%, and a catch-up limit of 1–47 lines.');return;
  }
  recordDecision('arm', {points:anchors.map(p=>({...p})),rule:session.rule,replenishment,replenish,lowerShift,upperShift,trailingExit,shiftOnClose,stopPct,maxShift,levels:weightedLevels});
  session.grid={armed:session.cursor,points:anchors,rule:session.rule,replenishment,replenish,lowerShift,upperShift,trailingExit,shiftOnClose,stopPct,maxShift,shifts:[],sleeves:weightedLevels.map(({weight,fraction,sellFraction})=>({cash:session.account.cash*(weight/100),qty:0,fraction,sellFraction}))};
  const {ts,close} = bars[session.cursor];
  // New orders can take the visible close, but cannot fill against an earlier wick.
  const filled = fillGridOrders(session.grid, {ts,open:close,high:close,low:close,close});
  notice(filled ? `Grid active · ${filled} buy ${filled===1?'line filled':'lines filled'} at ${relativePrice(close)}.` : 'Grid active · Buy limits are below the current price.');
  persist();render();
};
function changeRule(name) {
  const rule=readRuleControls(name);
  if(!rule){render();return;}
  const grid=session?.grid;
  if(grid){
    grid[name]=rule;
    recordDecision('update '+name,{rule});
    if(name==='replenishment'){
      grid.replenish=rule.mode!=='off';
      for(const line of grid.sleeves) line.retired=!grid.replenish && line.qty===0 && line.buyId!=null;
      if(grid.sleeves.every(line=>line.retired)) completeGrid(grid,session.cursor);
    }
    persist();
  }
  render();
}
for(const [name,id] of Object.entries(minuteInputs)) $(id).onchange=()=>changeRule(name);
$('trail-distance').onchange=()=>changeRule('trailingExit');
for(const [name,ids] of Object.entries(ruleInputs)){
  for(const id of ids) $(id).onchange=()=>changeRule(name);
}
$('cancel').onclick=()=>{pause();cancel('Grid canceled');persist();render();};
$('step').onclick=()=>{pause();next();};$('play').onclick=play;
$('speed').onchange=()=>{if(timer){pause();play();}};
$('indicators').onclick=pause;
$('vp-select').onclick=()=>{
  pause();pickingProfile=!pickingProfile;profileStart=null;$('indicator-menu').hidePopover();
  notice(pickingProfile?'Click the first candle of the volume profile. Escape cancels.':'Selection canceled.');render();
};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&pickingProfile){pickingProfile=false;profileStart=null;notice('Selection canceled.');render();}});
$('vp-toggle').onclick=()=>{const m=measures();m.visible=!m.visible;indicatorControls();persist();render();};
$('vp-remove').onclick=()=>{measures().range=null;profile=null;indicatorControls();persist();render();};
for(const [id,field] of [['vp-bins','bins'],['vp-area','area'],['ema-period','period']]){
  $(id).onchange=()=>{
    const value=$(id).valueAsNumber;
    if(!Number.isInteger(value)||!$(id).checkValidity()){indicatorControls();return;}
    measures()[field]=value;
    if(field==='period')emaValues=[];else updateProfile();
    indicatorControls();persist();render();
  };
}
$('daily-ema-visible').onchange=()=>{measures().dailyEma=$('daily-ema-visible').checked;persist();render();};
$('ema-visible').onchange=()=>{measures().ema=$('ema-visible').checked;persist();render();};
$('show-usd').onchange=()=>{if(session)renderVolume();};
$('older-fills').onclick=()=>{fillLimit+=50; render();};
$('setup').onclick=pause;
$('grid-settings').onclick=pause;
$('new').onclick=()=>{if(!session||confirm('Replace this replay and its fills with a new session?'))load();};
$('replay-market').onchange=()=>{showHistory();showMarket();};
$('show-history-dates').onchange=()=>showHistory($('dataset').value);
$('rule').onchange=()=>{if(session){session.rule=$('rule').value;if(session.grid){session.grid.rule=session.rule;recordDecision('update cancel rule',{rule:session.rule});}persist();}};
new ResizeObserver(chart).observe(canvas);
(async()=>{
  render();
  try{
    datasets=(await api('/api/context')).datasets.filter(d=>d.bars>=3);
    const markets=[...new Set(datasets.map(marketLabel))].sort();
    for(const market of markets) $('replay-market').add(new Option(market,market));
    const preferred=datasets.find(d=>d.market==='ETH/BTC' && d.timeframe==='5m');
    if(preferred) $('replay-market').value=marketLabel(preferred);
    showHistory();
    showMarket();
    if(!datasets.length)throw Error('No local datasets available.');
    const saved=JSON.parse(localStorage.getItem(key)||'null');
    await load(saved);
  }catch(e){notice(e.message);}
})();
