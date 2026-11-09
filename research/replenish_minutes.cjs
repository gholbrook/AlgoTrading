// Run from the repository root with node research/replenish_minutes.cjs.
const fs=require('node:fs');
const {market,windows,run}=require('./replay_channel_sweep.cjs');
const previous=JSON.parse(fs.readFileSync('var/replay-channel-sweep/all-pairs.json'));
const grid=previous.shared.chosen;
const configs=['off','on','ema'].map(replenish=>({...grid,replenish}));
for(const minutes of [10,15,30,60,120,240,480,720,1440])
  for(const threshold of [0,0.005,0.02]) configs.push({...grid,replenish:'minutes',minutes,threshold});
const phases=[['2026-01-01','2026-09-01',[1]],['2026-09-01','2026-10-01',[1,4]],['2026-10-01','2026-11-01',[1,4]]];
const markets={};
for(const name of Object.keys(previous.markets)){
  const [venue,pair]=name.split(' ');
  const m=market(pair.replace('/','_'),venue,['var/replay-prehistory','var/history']);
  const samples=phases.map(([start,end,days])=>days.flatMap(d=>windows(start,end,d,[m])));
  markets[name]=configs.map(c=>samples.map(s=>run(c,s)));
}
const metrics=['ret','vol','dd','win'];
function aggregate(idx,phase){
  const stats=Object.values(markets).map(rows=>rows[idx][phase]);
  return Object.fromEntries(metrics.map(k=>[k,k==='dd'?Math.max(...stats.map(s=>s[k])):stats.reduce((n,s)=>n+s[k],0)/stats.length]));
}
const scores=configs.map((c,i)=>({c,i,train:aggregate(i,0),validation:aggregate(i,1),test:aggregate(i,2)}));
const finalists=scores.slice(3).sort((a,b)=>b.train.ret-a.train.ret).slice(0,6);
const winner=finalists.sort((a,b)=>b.validation.ret-a.validation.ret)[0];
const out='var/replay-channel-sweep/minute-replenishment.json';
fs.writeFileSync(out,JSON.stringify({winner,finalists,scores,markets},null,2));
console.log(JSON.stringify([winner,...scores.slice(0,3)],null,2));
