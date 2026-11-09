"""Lock the second-round choice before checking the four transfer pairs."""
import json
from statistics import mean
from research.score_walk_forward import PRIMARY,ROOT,read
files=[read('Binance-'+p,'challenge') for p in PRIMARY]
configs=files[0]['choices']
eligible=[]
for i,c in enumerate(configs):
    monthly=[]
    for month in sorted(files[0]['months']):
        if not '2022-07'<=month<'2025':continue
        shared=set.intersection(*({v[0] for v in f['months'][month][i]} for f in files))
        rows=[v for f in files for v in f['months'][month][i] if v[0] in shared]
        monthly.append([mean(r[1] for r in rows),mean(r[3] for r in rows),mean(r[5]>0 for r in rows)])
    ret,vol,active=map(mean,zip(*monthly))
    if vol>=1 and active>=.25:eligible.append((ret,i,vol,active))
ret,i,vol,active=max(eligible)
previous=json.loads((ROOT/'scores.json').read_text())
locked={'crypto':previous['crypto']['best']['id'],'challenger':i,'config':configs[i],
        'training_net':ret,'training_turnover':vol,'training_active':active}
(ROOT/'locked.json').write_text(json.dumps(locked,indent=2))
print(locked)
