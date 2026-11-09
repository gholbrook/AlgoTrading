// node research/ema_grid_rules.cjs from the repository root
const fs=require('node:fs');
const {market,windows,run}=require('./replay_channel_sweep.cjs');
const dir='var/replay-channel-sweep/';
const previous=JSON.parse(fs.readFileSync(dir+'all-pairs.json'));
const grid={...previous.shared.chosen,replenish:'minutes',minutes:720,threshold:0.02};
const preset={mode:'minutes',minutes:720,threshold:0.02};
const daily={mode:'ema',period:200,threshold:0};
const gates=['trailingExit','lowerShift','upperShift'];
const alternatives=[daily];
for(const minutes of [10,15,30,60,120,240,480,720,1440])
  for(const threshold of [0,0.005,0.02]) alternatives.push({mode:'minutes',minutes,threshold});
const scores={},individual={};
for(const name of Object.keys(previous.markets)){
  const [venue,pair]=name.split(' ');
  const m=market(pair.replace('/','_'),venue,['var/replay-prehistory','var/history']);
  const phases=[['2026-01-01','2026-09-01',[1]],['2026-09-01','2026-10-01',[1,4]],['2026-10-01','2026-11-01',[1,4]]]
    .map(([start,end,days])=>days.flatMap(d=>windows(start,end,d,[m])));
  const check=c=>phases.map(w=>run(c,w));
  scores[name]={};
  for(const gate of gates){
    scores[name][gate]=alternatives.map(rule=>check({...grid,rules:{[gate]:rule}}));
  }
  const prior=previous.markets[name].local.chosen, rules={};
  if(prior.trail) rules.trailingExit=preset;
  if(prior.replenish==='ema') rules.replenishment=preset;
  individual[name]={before:check(prior),after:check({...prior,rules})};
  console.log(name);
}
const mean=xs=>xs.reduce((a,b)=>a+b,0)/xs.length;
const summary={};
for(const gate of gates){
  const ranked=alternatives.map((rule,i)=>({rule,phases:[0,1,2].map(j=>{
    const pool=Object.values(scores).map(s=>s[gate][i][j]);
    return {ret:mean(pool.map(x=>x.ret)),vol:mean(pool.map(x=>x.vol))};
  })}));
  const finalists=ranked.slice(1).sort((a,b)=>b.phases[0].ret-a.phases[0].ret).slice(0,6);
  summary[gate]={daily:ranked[0],preset:ranked.find(x=>x.rule.minutes===720 && x.rule.threshold===0.02),
    selected:finalists.sort((a,b)=>b.phases[1].ret-a.phases[1].ret)[0],ranked};
}
fs.writeFileSync(dir+'ema-grid-rules.json',JSON.stringify({grid,summary,individual,scores}));
