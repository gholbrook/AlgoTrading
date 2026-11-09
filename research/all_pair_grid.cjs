// Run from the repository root with node research/all_pair_grid.cjs.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {market,windows,base,run}=require('./replay_channel_sweep.cjs');
const dirs=['var/replay-prehistory','var/history'];
const instruments=new Map();
for(const file of fs.readdirSync(dirs[1]).filter(f=>f.endsWith('.json') && f.includes('_5m_'))){
  const {market:pair,venue}=JSON.parse(fs.readFileSync(dirs[1]+'/'+file));
  instruments.set(venue+' '+pair,{pair:pair.replace('/','_'),venue});
}
const samples=[...instruments].sort(([a],[b])=>a.localeCompare(b)).map(([name,{pair,venue}])=>{
  const m=market(pair,venue,dirs);
  const period=(start,end,weekdays)=>weekdays.flatMap(d=>windows(start,end,d,[m]));
  const train=period('2026-01-01','2026-09-01',[1]);
  const validation=period('2026-09-01','2026-10-01',[1,4]);
  const test=period('2026-10-01','2026-11-01',[1,4]);
  assert.ok(train.length && validation.length && test.length,name);
  return {name,train,validation,test,cache:new Map()};
});
function evaluate(sample,phase,c){
  const key=phase+JSON.stringify(c);
  if(!sample.cache.has(key)) sample.cache.set(key,run(c,sample[phase]));
  return sample.cache.get(key);
}
function rank(group,phase,configs){
  return configs.map(c=>({c,ret:group.reduce((sum,s)=>sum+evaluate(s,phase,c).ret,0)/group.length}))
    .sort((a,b)=>b.ret-a.ret);
}
function select(group){
  const widths=rank(group,'train',[0.25,0.5,1,2,4,8,12,'R0.5','R1','R2'].map(width=>({...base,width})));
  const configs=[];
  for(const {c} of widths.slice(0,2)) for(const move of ['fixed','outside','both8','lower','both4'])
    for(const stop of [0,1,2]) for(const replenish of ['off','on','ema']) configs.push({...c,move,stop,replenish});
  const settings=rank(group,'train',configs);
  const candidates=new Map();
  for(const {c} of settings.slice(0,5)) for(const cap of c.move==='fixed'?[8]:[1,8,20]) for(const trail of [0,0.5,1]){
    const v={...c,cap,trail};candidates.set(JSON.stringify(v),v);
  }
  const finalists=rank(group,'validation',[...candidates.values()]);
  return {chosen:finalists[0].c,widths,settings,finalists};
}
// The earlier five-pair sweep selected this before the all-market study.
const prior={...base,width:4,move:'outside',cap:1,replenish:'off',trail:1};
const shared=select(samples);
const results={shared,markets:{}};
for(const s of samples){
  const local=select([s]);
  const checked=c=>run(c,s.test,true);
  results.markets[s.name]={trainingSessions:s.train.length,validationSessions:s.validation.length,local,
    sharedTest:checked(shared.chosen),localTest:checked(local.chosen),baseline:checked(base),priorTest:checked(prior),
    trailingComparison:checked({...shared.chosen,trail:shared.chosen.trail?0:1})};
  console.log(s.name,'complete');
}
const out='var/replay-channel-sweep';
fs.mkdirSync(out,{recursive:true});
fs.writeFileSync(out+'/all-pairs.json',JSON.stringify(results,null,2));
