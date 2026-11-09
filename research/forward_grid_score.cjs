// Run after python3 -m tools.grid_forward_history. Re-running scores is idempotent.
const fs=require('node:fs');
const crypto=require('node:crypto');
const dir='var/walk-forward';
const lock=JSON.parse(fs.readFileSync(dir+'/forward-lock.json'));
for(const [file,expected] of Object.entries(lock.engine_sha256)){
  if(crypto.createHash('sha256').update(fs.readFileSync(dir+'/frozen/'+file)).digest('hex')!==expected)
    throw Error('Frozen replay engine changed: '+file);
}
const {market,windows,run}=require('../var/walk-forward/frozen/research/replay_channel_sweep.cjs');
const results={};
const end=new Date().toISOString().slice(0,10);
if(end>lock.first_start){
  for(const [name,group] of Object.entries(lock.markets)){
    const [venue,pair]=name.split('-'), m=market(pair,venue,['var/replay-prehistory','var/history']);
    const sessions=[1,4].flatMap(d=>windows(lock.first_start,end,d,[m]));
    if(!sessions.length)continue;
    results[name]={group,configs:{}};
    const keys=group==='control'?['reference','early_usdt']:['reference','early_crypto','challenger'];
    if(pair.endsWith('_USDT')) keys.push('challenger','usdt_range_width');
    for(const key of keys){
      const c=lock.configs[key];
      results[name].configs[key]={base:run(c,sessions,true),stress:run({...c,feeBps:4,slipBps:3},sessions,true)};
    }
  }
}
const dest=dir+'/forward-scores.json';
fs.writeFileSync(dest+'.tmp',JSON.stringify(results,null,2));fs.renameSync(dest+'.tmp',dest);
console.log(Object.keys(results).length,'markets with fully observed prospective sessions');
