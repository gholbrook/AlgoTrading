"""Run after generating Binance monthly results with grid_walk_forward.cjs."""
import json
from pathlib import Path
from statistics import mean
import numpy as np

ROOT = Path('var/walk-forward')
PRIMARY = ['BNB_BTC', 'BNB_ETH', 'ETH_BTC', 'SOL_BTC', 'SOL_ETH']
CONTROL = [s + '_USDT' for s in ('ADA','BNB','BTC','DOGE','ETH','LINK','SOL','XRP')]


def read(name, mode='sweep'):
    return json.loads((ROOT / 'months' / f'{name}-{mode}.json').read_text())


def interval(values):
    # Preserve contemporaneous pair correlation by sampling portfolio months.
    values = np.asarray(values)
    rng = np.random.default_rng(20261109)
    starts = rng.integers(len(values), size=(10000, (len(values)+2)//3))
    ix = ((starts[:,:,None] + np.arange(3)) % len(values)).reshape(10000,-1)
    return np.quantile(values[ix[:,:len(values)]].mean(axis=1), [.025,.975]).tolist()


def packed(matrix):
    return dict(zip(('ret','volume','hold','dd','win','fills'),matrix.tolist()))


def score(pairs):
    keys=['Binance-'+pair for pair in pairs]
    files={k:read(k) for k in keys}
    configs=files[keys[0]]['choices']
    assert all(f['choices']==configs for f in files.values())
    months=sorted(set.intersection(*(set(f['months']) for f in files.values())))
    # Compare precisely the same start times across every market.
    aggregate=[]; individual=[]; counts=[]
    for m in months:
        dates=set.intersection(*({r[0] for r in f['months'][m][0]} for f in files.values()))
        assert dates
        per_pair=[]
        for f in files.values():
            rows=np.array([[r[1:] for r in sample if r[0] in dates] for sample in f['months'][m]])
            ret,dd,vol,hold,fills=rows.transpose(2,0,1)
            per_pair.append(np.stack([ret.mean(1),vol.mean(1),hold.mean(1),dd.max(1),(ret>0).mean(1),fills.mean(1)],axis=1))
        counts.append(len(dates)*len(pairs))
        panel=np.array(per_pair);individual.append(panel)
        metrics=panel.mean(0);metrics[:,3]=panel[:,:,3].max(0);aggregate.append(metrics)
    matrix=np.array(aggregate)
    eligible=[i for i,m in enumerate(months) if '2022-07'<=m<'2025']
    train=matrix[eligible,:,0].mean(0)
    ranking=np.argsort(-train,kind='stable')
    winner,runner=map(int,ranking[:2])
    ref=configs.index(dict(width=12,move='both8',cap=1,stop=2,replenish='minutes',minutes=720,threshold=.02,trail=.5))
    rolled={}
    for i,m in enumerate(months):
        if m<'2023' or i<6:continue
        # A missing calendar month must not quietly become an older training month.
        dates=np.array(months[i-6:i+1],dtype='datetime64[M]').astype(int)
        if not np.all(np.diff(dates)==1):continue
        rolled[m]=int(matrix[i-6:i,:,0].mean(0).argmax())
    output={}
    for label,start,end in [('rolling-history','2023','2027'),('confirmation','2025','2026'),('previously-studied','2026','2027')]:
        ix=[i for i,m in enumerate(months) if start<=m<end and m in rolled]
        idx=[rolled[months[i]] for i in ix]
        selected=matrix[ix,idx];fixed=matrix[ix,winner];reference=matrix[ix,ref]
        report={}
        for key,series in [('rolling',selected),('fixed',fixed),('reference',reference)]:
            stats=packed(series.mean(0)); stats['dd']=float(series[:,3].max())
            stats.update(ci=interval(series[:,0]),against_reference=interval(series[:,0]-reference[:,0]),
                         against_hold=interval(series[:,0]-series[:,2]),positive_months=int((series[:,0]>0).sum()))
            report[key]=stats
        output[label]=dict(months=len(ix),sessions=sum(counts[i] for i in ix),stats=report,
            pair_returns={p:float(np.mean([individual[i][j,winner,0] for i in ix])) for j,p in enumerate(pairs)},
            fixed_minus_runner=interval(fixed[:,0]-matrix[ix,runner,0]))
    monthly={m:dict(rolling_id=idx,rolling_net=matrix[i,idx,0],fixed_net=matrix[i,winner,0],reference_net=matrix[i,ref,0],hold=matrix[i,ref,2],sessions=counts[i]) for i,m in enumerate(months) if (idx:=rolled.get(m)) is not None}
    best=dict(id=winner,config=configs[winner],training=float(train[winner]),runner=configs[runner],runner_id=runner,reference_id=ref)
    return dict(best=best,periods=output,monthly=monthly),{k:sorted({winner,ref,runner}) for k in keys}


if __name__=='__main__':
    output={};picks={}
    for name,group in [('crypto',PRIMARY),('usdt',CONTROL)]:
        output[name],selection=score(group);picks.update(selection)
    for key in ['Binance-LTC_BTC','Binance-XRP_BTC']:
        picks[key]=picks['Binance-ETH_BTC']
    picks['Coinbase-ETH_USD']=picks['Binance-ETH_USDT']
    (ROOT/'scores.json').write_text(json.dumps(output,indent=2))
    (ROOT/'picks.json').write_text(json.dumps(picks,indent=2))
    for group,r in output.items():print(group,r['best'],r['periods']['confirmation'])
