import json,math,numpy as np,pandas as pd
from scipy import stats
RUN="/workspace/llm-loan-bias/runs/20261012T143812-ff54324d-4745d6-batch/raw/"
df=pd.DataFrame([json.loads(l) for l in open(RUN+"calls.jsonl")]); df=df[df.cost_usd.notna()]
# implied batch prices per 1M tokens (least squares in,out)
for v,g in df.groupby("vendor"):
    X=np.c_[g.input_tokens,g.output_tokens]; b=np.linalg.lstsq(X,g.cost_usd,rcond=None)[0]*1e6; print(v,"implied $/1M in,out",b.round(4))
c=df.groupby(["vendor","setup"]).cost_usd.mean()
J=1.804e-5; per_unit={(v,s):24*(c[(v,s)]+J) for v,s in c.index}
# google fix: failures truncated at 800; assume their visible output ~ median ok visible (approx +300 tokens) -> apply to 11.5% of setup1
gp=df[df.vendor=="google"]; X=np.c_[gp.input_tokens,gp.output_tokens]; pin,pout=np.linalg.lstsq(X,gp.cost_usd,rcond=None)[0]
add=0.115*300*pout; per_unit[("google",1)]+=24*add; print("google setup1 fix add per call",add)
setup_prof={s:sum(per_unit[(v,s)] for v in ["openai","anthropic","google"]) for s in (1,2,3)}
print("cost per profile (3 vendors, 8 variants x3 reps):",{k:round(x,4) for k,x in setup_prof.items()},"all",round(sum(setup_prof.values()),4))
anth_full=c[("anthropic",1)]*600+c[("anthropic",2)]*600+c[("anthropic",3)]*600; print("anthropic full-pilot cost re-est",round(anth_full,3),"vs projected 5.134")
za=stats.norm.ppf(1-0.05/8/2); zb=stats.norm.ppf(.8); print("z",za)
for lab,sd,n in [("name_b",0.1952,36),("zip_b",0.1866,36),("control",0.1688,36)]:
    up=sd*math.sqrt((n-1)/stats.chi2.ppf(0.025,n-1))
    for s in (sd,up):
        N=math.ceil(((za+zb)*s/0.05)**2); print(lab,"sd",round(s,4),"units",N,"deny profiles",math.ceil(N/3),"profiles@48%deny",math.ceil(N/3/0.48))
# MDE at given units
for units in (200,330,450,600):
    print("MDE units",units,round((za+zb)*0.2537/math.sqrt(units),4))
REM=200-10.581; print("remaining",REM)
for prox,pt,lo,hi in [("name",0.0667,0.0090,0.1455),("zip",0.0811,0.0194,0.1645)]:
    for lab,r in [("optimistic",hi),("point",pt),("pessimistic",lo)]:
        units=math.ceil(20/r); prof=math.ceil(units/3); print(prox,lab,r,"units",units,"profiles",prof,"setup1 cost",round(prof*setup_prof[1],2))
    # openai-only (all name flips from openai)
for P in (150,230,300,400):
    tot=P*sum(setup_prof.values()); print("profiles",P,"cost all setups",round(tot,2),"setup1 units/proxy",3*P,"exp net flips name",round(3*P*0.0667,1),"[",round(3*P*0.009,1),round(3*P*0.1455,1),"] zip",round(3*P*0.0811,1),"[",round(3*P*0.0194,1),round(3*P*0.1645,1),"]")
