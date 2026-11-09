// Run from the root: node research/grid_walk_forward.cjs ETH_BTC [Binance]
const fs=require('node:fs');
const {market,windows,run}=require('./replay_channel_sweep.cjs');
const dir='var/walk-forward';
const preset={width:12,move:'both8',cap:1,stop:2,replenish:'minutes',minutes:720,threshold:.02,trail:.5};
const configs=[];
for(const width of [2,4,8,12]) for(const move of ['fixed','outside','both8']) for(const replenish of ['off','on','ema','minutes']){
  configs.push({...preset,width,move,replenish,threshold:replenish==='minutes'?.02:0});
}
for(const width of [4,12]){
  const basic={...preset,width};
  configs.push({...basic,stop:0},{...basic,stop:1},{...basic,trail:0},
    {...basic,rules:{lowerShift:{mode:'minutes',minutes:15,threshold:.02},trailingExit:{mode:'minutes',minutes:720,threshold:.02}}});
}
const challenge=[];
for(const width of [4,12,'R2']) for(const move of ['fixed','both8']) for(const entry of ['always','quiet','contained','both']){
  challenge.push({...preset,width,move,entry});
}
function condition(bars,i){
  const ranges=[];
  for(let n=0;n<7;n++){
    const end=i-n*288, start=end-287, chunk=bars.slice(start,end+1);
    const distances=Math.max(...chunk.map(b=>b.high))-Math.min(...chunk.map(b=>b.low));
    ranges.push(distances/bars[end].close);
  }
  const prior=ranges.slice(1).sort((a,b)=>a-b);
  const quiet=ranges[0]<=(prior[2]+prior[3])/2;
  const contained=Math.abs(bars[i].close-bars[i-287].open)<=0.25*ranges[0]*bars[i].close;
  return {always:true,quiet,contained,both:quiet&&contained};
}
const [pair,venue='Binance',mode='sweep']=process.argv.slice(2);
const m=market(pair,venue,['var/replay-prehistory','var/history']);
const usdtCheck=['usdt-check','usdt-stress'].includes(mode);
const all=[1,4].flatMap(d=>windows(usdtCheck?'2025-01-01':'2020-01-01',mode==='audit'||usdtCheck?'2026-11-09':'2026-11-01',d,[m])).sort((a,b)=>a.i-b.i);
const samples={};
for(const w of all){
  const stamp=i=>new Date(m.bars[i].ts*1000).toISOString().slice(0,7);
  const month=stamp(w.i);
  if((mode.startsWith('challenge') || usdtCheck) && (w.i<2015 || m.bars[w.i].ts-m.bars[w.i-2015].ts!==2015*300))continue;
  if(mode==='audit' && !['2021-10','2021-11','2021-12','2026-11'].includes(month))continue;
  if(month===stamp(w.i+865)) (samples[month]??=[]).push(w);
}
let choices=mode.startsWith('challenge')?challenge:configs;
if(['transfer','transfer-stress','challenge-transfer','challenge-stress','audit'].includes(mode)){
  const locked=JSON.parse(fs.readFileSync(dir+'/locked.json'));
  choices=mode.startsWith('transfer')?[configs[locked.crypto],preset]:[challenge[locked.challenger],preset];
  if(mode==='audit') choices=[configs[locked.crypto],preset,challenge[locked.challenger]];
  if(mode.endsWith('-stress')) choices=choices.map(c=>({...c,feeBps:4,slipBps:3}));
}
if(mode==='stress' || mode==='locked-control'){
  const picks=JSON.parse(fs.readFileSync(dir+'/picks.json'))[venue+'-'+pair];
  choices=picks.map(id=>mode==='stress'?{...configs[id],feeBps:4,slipBps:3}:configs[id]);
}
if(usdtCheck){
  const {configs:locked}=JSON.parse(fs.readFileSync(dir+'/forward-lock.json'));
  const selected=JSON.parse(fs.readFileSync(dir+'/usdt-selection.json')).config;
  choices=[locked.early_usdt,locked.reference,locked.challenger,selected];
  if(mode==='usdt-stress') choices=choices.map(c=>({...c,feeBps:4,slipBps:3}));
}
const result={choices,months:{}};
for(const [month,ws] of Object.entries(samples)){
  if(ws.length<(mode==='audit'||usdtCheck?1:4))continue;
  const indicators=mode.startsWith('challenge') || usdtCheck ? ws.map(({i})=>condition(m.bars,i)) : [];
  result.months[month]=choices.map(c=>{
    const s=run(c,ws,true);
    // Keep the start date and fill metrics so timing comparisons stay paired.
    return s.rows.map(({date,ret,dd,vol,hold,fills},j)=>
      c.entry && c.entry!=='always' && !indicators[j][c.entry]?[date,0,0,0,hold,0]:[date,ret,dd,vol,hold,fills]);
  });
}
fs.mkdirSync(dir+'/months',{recursive:true});
const path=dir+'/months/'+venue+'-'+pair+'-'+mode+'.json';
fs.writeFileSync(path+'.tmp',JSON.stringify(result));fs.renameSync(path+'.tmp',path);
console.log(pair,mode,Object.keys(result.months).length);
