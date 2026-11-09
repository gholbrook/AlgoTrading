// From the repository root: node research/replay_channel_sweep.cjs
const fs = require('node:fs');
const assert = require('node:assert/strict');
const engine = require('../static/manual-engine.js');
const pairs = ['ETH_BTC','BNB_BTC','SOL_BTC','BNB_ETH','SOL_ETH'];
const day = 86400, horizon = 3 * 288;
const average = xs => xs.reduce((a,b)=>a+b,0)/xs.length;
const date = ts => new Date(ts*1000).toISOString().slice(0,10);
const root = 'var/history';
const source = fs.readFileSync(require.resolve('../static/grid-replay.js'),'utf8');
// Load the actual order, channel and EMA routines without mounting the chart.
const boot = source.indexOf('new ResizeObserver(chart)');
assert.ok(boot > 0);
const element = {addEventListener(){}};
const documentStub = {getElementById:()=>element, addEventListener(){}};
function market(pair, venue='Binance', directories=[root]) {
  const rows = new Map();
  for (const dir of directories) for (const file of fs.readdirSync(dir).filter(x=>x.startsWith(venue.toLowerCase()+'_') && x.includes('_'+pair+'_5m_') && x.endsWith('.csv'))) {
    const lines = fs.readFileSync(dir+'/'+file,'utf8').trim().split('\n');
    for (const row of lines.slice(1)) {
      const [stamp,...values]=row.split(',');
      const ts=Date.parse(stamp)/1000, [open,high,low,close]=values.map(Number);
      assert.ok(low>0 && high>=low && Number.isFinite(close));
      rows.set(ts,{ts,open,high,low,close});
    }
  }
  const bars=[...rows.values()].sort((a,b)=>a.ts-b.ts);
  const history=JSON.parse(fs.readFileSync('var/replay-daily/'+venue+'-'+pair+'.json'));
  const setup = new Function('document','ManualEngine','candles','history',source.slice(0,boot)+`
    bars=candles; session={timeframe:'5m'}; prepareDailyHistory(history);
    return {weightedLevels,advance:fillGridOrders,channel:workingChannel,shift:shiftAfterClose,
      start(account){session.account=account;}};
  `);
  return {pair,bars,replay:setup(documentStub,engine,bars,history)};
}

const base={width:2,move:'both8',cap:8,stop:2,replenish:'on',trail:0};
function simulate({bars,replay},i,c){
  const first=bars[i], price=first.close, past=bars.slice(i-287,i+1);
  const width=typeof c.width==='number' ? price*c.width/100 :
    Number(c.width.slice(1))*(Math.max(...past.map(b=>b.high))-Math.min(...past.map(b=>b.low)));
  assert.ok(width>0 && width<2*price);
  const account=engine.create(1,c.feeBps ?? 2,c.slipBps ?? 1);
  replay.start(account);
  const rule=mode=>({mode,period:200,threshold:0});
  const lines=c.move==='both4'?4:8;
  const grid={points:[{p:price+width/2},{p:price-width/2}],shifts:[],maxShift:c.cap,
    replenishment:{...rule(c.replenish),minutes:c.minutes,threshold:c.threshold ?? 0},stopPct:c.stop,
    lowerShift:{...rule(c.move.startsWith('both')||c.move==='lower'?'on':'off'),lines},
    upperShift:{...rule(c.move.startsWith('both')?'on':'off'),lines},
    trailingExit:{...rule(c.trail?'ema':'off'),distance:c.trail},
    sleeves:replay.weightedLevels.map(l=>({...l,cash:l.weight/100,qty:0}))};
  for(const [name,rule] of Object.entries(c.rules ?? {})) Object.assign(grid[name],rule);
  replay.advance(grid,{...first,open:price,high:price,low:price});
  let peak=1,dd=0;
  for(let j=i+1;j<=i+horizon;j++){
    const b=bars[j], [low,high]=replay.channel(grid);
    replay.advance(grid,b);
    const equity=account.cash+account.held*b.close;
    peak=Math.max(peak,equity);dd=Math.max(dd,1-equity/peak);
    if(grid.sleeves.every(s=>s.retired)) break;
    if(c.move!=='fixed') replay.shift(grid,j,b.close,low,high);
  }
  const last=bars[i+horizon+1];
  if(account.held>1e-14){engine.submit(account,'sell',100);engine.advance(account,last);}
  const ret=account.cash-1;
  dd=Math.max(dd,1-account.cash/peak);
  return {ret,dd,vol:account.fills.reduce((n,f)=>n+f.price*f.qty,0),fills:account.fills.length,
    hold:last.open*(1-account.slip)*(1-account.fee)/(price*(1+account.slip)*(1+account.fee))-1};
}
function windows(begin,end,weekday,markets){
  const all=[];
  for(const m of markets){
    for(let i=287;i+horizon+1<m.bars.length;i++){
      const ts=m.bars[i].ts;
      if(ts%day || new Date(ts*1000).getUTCDay()!==weekday || date(ts)<begin || date(m.bars[i+horizon+1].ts)>=end)continue;
      if(m.bars[i+horizon+1].ts-m.bars[i-287].ts!==(horizon+288)*300)continue;
      all.push({m,i});
    }
  }
  return all;
}
function summary(rows){
  const metric=k=>average(rows.map(r=>r[k]));
  const sorted=rows.map(r=>r.ret).sort((a,b)=>a-b);
  return {n:rows.length,ret:metric('ret'),median:(sorted[Math.floor((rows.length-1)/2)]+sorted[Math.floor(rows.length/2)])/2,
    win:average(rows.map(r=>Number(r.ret>0))),dd:Math.max(...rows.map(r=>r.dd)),vol:metric('vol'),fills:metric('fills'),hold:metric('hold')};
}
function run(c,windows,detail=false){
  const rows=windows.map(({m,i})=>({pair:m.pair,date:date(m.bars[i].ts),...simulate(m,i,c)}));
  return {c,...summary(rows),...(detail?{rows}:{})};
}
function rank(configs,windows){return configs.map(c=>run(c,windows)).sort((a,b)=>b.ret-a.ret);}
module.exports={market,windows,base,run,summary};
if(require.main===module){
  const markets=pairs.map(pair=>market(pair));
  const train=windows('2026-01-01','2026-07-01',1,markets), valid=windows('2026-07-01','2026-09-01',1,markets);
  const test=windows('2026-09-01','2026-11-01',1,markets), alternate=windows('2026-09-01','2026-11-01',4,markets);
  const widths=rank([0.25,0.5,1,2,4,8,12,'R0.5','R1','R2'].map(width=>({...base,width})),train);
  const candidates=[];
  for(const w of widths.slice(0,2)) for(const move of ['fixed','outside','both8','lower','both4']) {
    for(const stop of [0,1,2]) for(const replenish of ['off','on','ema']){
      candidates.push({...base,width:w.c.width,move,stop,replenish});
    }
  }
  const settings=rank(candidates,train);
  const finalists=new Map();
  for(const row of settings.slice(0,5)) for(const cap of (row.c.move==='fixed'?[8]:[1,8,20])) for(const trail of [0,0.5,1]) {
    const c={...row.c,cap,trail};
    finalists.set(JSON.stringify(c),c);
  }
  const validation=rank([...finalists.values()],valid);
  const chosen=validation[0].c;
  const results={widths,settings,validation,holdout:[chosen,{...base,width:chosen.width},base,{...base,replenish:'off'}].map(c=>run(c,test,true)),
    alternate:run(chosen,alternate,true)};
  fs.mkdirSync('var/replay-channel-sweep',{recursive:true});
  fs.writeFileSync('var/replay-channel-sweep/results.json',JSON.stringify(results,null,2));
  console.log('Selected',chosen,'holdout',summary(results.holdout[0].rows));
}
