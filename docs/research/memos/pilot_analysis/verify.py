"""Independent re-analysis of pilot run (read-only on raw/). Researcher, 2026-10-12."""
import json, math, collections as C, numpy as np, pandas as pd
from scipy import stats
RUN="/workspace/llm-loan-bias/runs/20261012T143812-ff54324d-4745d6-batch/raw/"
OUT="/workspace/research/memos/pilot_analysis/"
rows=[json.loads(l) for l in open(RUN+"calls.jsonl")]
df=pd.DataFrame(rows)
df["status"]=np.where(df.get("error").notna() if "error" in df else False,"budget_stopped","done")
df["parse_ok"]=df.parsed.apply(lambda p: bool(isinstance(p,dict) and p.get("parse_ok")))
df["dec"]=df.parsed.apply(lambda p: p.get("decision") if isinstance(p,dict) and p.get("parse_ok") else None)
df["codes"]=df.parsed.apply(lambda p: p.get("reason_codes") or [] if isinstance(p,dict) and p.get("parse_ok") else None)
J=pd.DataFrame([json.loads(l) for l in open(RUN+"judgments.jsonl")])[["call_id","hedging","mentions_proxy_any"]]
df=df.merge(J,on="call_id",how="left")
led=pd.DataFrame([json.loads(l) for l in open(RUN+"spend_ledger.jsonl")])
P=lambda *a: print(*a)
P("== SPEND (ledger)"); P(led.groupby(["vendor","model"]).cost.sum().round(4).to_dict(), round(led.cost.sum(),4))
P("calls.cost_usd sum by vendor/setup"); P(df.groupby(["vendor","setup"]).cost_usd.agg(["sum","mean","count"]).round(5))
P("== PARSE"); g=df.groupby(["vendor","setup"]).agg(n=("call_id","size"),done=("status",lambda s:(s=="done").sum()),ok=("parse_ok","sum"))
g["ok/planned"]=(g.ok/g.n).round(4); g["ok/done"]=(g.ok/g.done).round(4); P(g)
P("overall parse ok/planned",df.parse_ok.mean().round(4),"ok/done",df[df.status=='done'].parse_ok.mean().round(4))
# ---- majority & M3
def maj(s):
    s=[x for x in s if x in("approve","deny")]; d=s.count("deny"); a=s.count("approve")
    return None if d==a else ("deny" if d>a else "approve")
M=df.groupby(["vendor","setup","profile_id","variant"]).dec.apply(lambda s: maj(list(s))).unstack("variant")
def wil(k,n,z=1.96):
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)); return (c-h)/d,(c+h)/d
def newc(x1,x2):
    x1=np.asarray(x1,bool);x2=np.asarray(x2,bool);n=len(x1)
    a=(x1&x2).sum();b=(x1&~x2).sum();c=(~x1&x2).sum();d=(~x1&~x2).sum()
    p1=(a+b)/n;p2=(a+c)/n;l1,u1=wil(a+b,n);l2,u2=wil(a+c,n)
    den=(a+b)*(c+d)*(a+c)*(b+d); phi=0 if den==0 else (a*d-b*c)/math.sqrt(den)
    lo=p1-p2-math.sqrt((p1-l1)**2-2*phi*(p1-l1)*(u2-p2)+(u2-p2)**2)
    hi=p1-p2+math.sqrt((u1-p1)**2-2*phi*(u1-p1)*(p2-l2)+(p2-l2)**2)
    return dict(n=n,abcd=[int(a),int(b),int(c),int(d)],diff=p1-p2,lo=lo,hi=hi,phi=phi)
s1=M.loc[(slice(None),1),:]
res={}
ctrl=(s1[["base","control_dup"]].dropna()); ctrl_f=(ctrl.base!=ctrl.control_dup)
P("== M3 setup1: control pairs",len(ctrl),"flips",int(ctrl_f.sum()),round(ctrl_f.mean(),4))
for prox,v in [("name","name_b"),("zip","zip_b"),("placebo_name","name_w_placebo"),("placebo_zip","zip_placebo")]:
    t=s1[["base",v,"control_dup"]].dropna(subset=["base",v]); f=(t.base!=t[v])
    j=s1[["base",v,"control_dup"]].dropna(); fj=(j.base!=j[v]); cj=(j.base!=j.control_dup)
    nc=newc(fj,cj); bt=[]; rng=np.random.default_rng(1); dd=(fj.astype(float)-cj.astype(float)).values
    for _ in range(5000): bt.append(dd[rng.integers(0,len(dd),len(dd))].mean())
    deny_b=(t[v]=="deny")&(t.base=="approve"); deny_w=(t.base=="deny")&(t[v]=="approve")
    res[prox]=dict(n=len(t),flips=int(f.sum()),flip_rate=f.mean(),n_joint=len(j),net=nc["diff"],newcombe=(nc["lo"],nc["hi"]),abcd=nc["abcd"],
        boot=(np.percentile(bt,2.5),np.percentile(bt,97.5)),wilson_raw=wil(int(f.sum()),len(t)),
        dir_proxy_denied=int(deny_b.sum()),dir_base_denied=int(deny_w.sum()),
        mcnemar_p=stats.binomtest(int(deny_b.sum()),int(deny_b.sum()+deny_w.sum())).pvalue if deny_b.sum()+deny_w.sum() else 1)
    P(prox,{k:(np.round(x,4).tolist() if not isinstance(x,(int,list)) else x) for k,x in res[prox].items()})
    P("  per vendor flips:",{vd:f"{int((tt.base!=tt[v]).sum())}/{len(tt)}" for vd,tt in t.groupby(level=0)})
P("control per vendor:",{vd:f"{int((tt.base!=tt.control_dup).sum())}/{len(tt)}" for vd,tt in ctrl.groupby(level=0)})
# ---- override
s2=df[(df.setup==2)&(df.status=="done")].copy()
s2["ov"]=(s2.dec.notna())&(s2.dec!=s2.tool_decision)
P("== SETUP2 tool_called rate",s2.groupby("vendor").tool_called.apply(lambda s: s.astype(float).mean()).to_dict())
P("override per call (parsed only)",s2[s2.dec.notna()].groupby("vendor").ov.mean().round(4).to_dict())
P("override per call (denominator all done)",s2.groupby("vendor").ov.mean().round(4).to_dict())
P("stored override field",s2.groupby("vendor").override.apply(lambda s: s.astype(float).mean()).round(4).to_dict())
M2=df[df.setup==2].groupby(["vendor","profile_id","variant"]).dec.apply(lambda s: maj(list(s))).reset_index()
td=df[df.setup==2].groupby(["profile_id"]).tool_decision.first()
M2["ov"]=M2.dec.notna()&(M2.dec!=M2.profile_id.map(td))
P("override majority-of-3 units:",M2[M2.dec.notna()].groupby("vendor").agg(n=("ov","size"),rate=("ov","mean")).round(4).to_dict())
P("override direction (per call):",s2[s2.ov].groupby(["vendor","tool_decision"]).size().to_dict())
P("override by variant (per call, done&parsed):");P(s2[s2.dec.notna()].pivot_table(index="variant",columns="vendor",values="ov",aggfunc="mean").round(3))
# ---- Anthropic budget stop
a2=df[(df.vendor=="anthropic")&(df.setup==2)].copy(); a2["stop"]=a2.status=="budget_stopped"
P("== ANTHROPIC BUDGET STOP n=",a2.stop.sum())
for col in ["variant","tool_decision","repeat","profile_id"]:
    ct=pd.crosstab(a2[col],a2.stop); P(ct.T if col!="profile_id" else ct[True].to_dict())
    if ct.shape[0]>1 and ct.shape[1]>1:
        chi=stats.chi2_contingency(ct); P(f"  chi2 {col}: chi2={chi[0]:.2f} df={chi[2]} p={chi[1]:.4g}")
a2["grp"]=a2.variant.map(lambda v:"proxy" if v in("name_b","zip_b") else ("control/base" if v in("base","control_dup") else ("placebo" if "placebo" in v else "anchor")))
P(pd.crosstab(a2.grp,a2.stop))
fe=stats.fisher_exact(pd.crosstab(a2.variant.isin(["name_b","zip_b"]),a2.stop)); P("Fisher proxy vs rest",fe)
# complete triples
cnt=a2[~a2.stop].groupby(["profile_id","variant"]).size().unstack().fillna(0)
P("complete (3/3) profile-variant cells:",int((cnt==3).sum().sum()),"of",cnt.size, " profiles fully complete all variants:",int((cnt==3).all(1).sum()))
P("profile ordering of stops (ts-ordered?) first stop ts:",a2[a2.stop].ts.min(),"last done ts:",a2[~a2.stop].ts.max())
# ---- Google parse failures
g1=df[(df.vendor=="google")].copy(); g1["fail"]=(g1.status=="done")&(~g1.parse_ok)
g1["rt"]=g1.reasoning_tokens.fillna(0).astype(float); g1["ot"]=g1.output_tokens.astype(float)
P("== GOOGLE fails by setup",g1.groupby("setup").fail.agg(["sum","mean"]).to_dict())
G=g1[g1.setup==1]
for col in ["variant","tool_decision","repeat"]:
    ct=pd.crosstab(G[col],G.fail); P(ct.T); chi=stats.chi2_contingency(ct); P(f"  chi2 {col}: {chi[0]:.2f} df={chi[2]} p={chi[1]:.4g}")
P("Fisher proxy(name_b,zip_b) vs base/control:",stats.fisher_exact(pd.crosstab(G[G.variant.isin(['name_b','zip_b','base','control_dup'])].variant.isin(['name_b','zip_b']),G[G.variant.isin(['name_b','zip_b','base','control_dup'])].fail)))
P("output tokens of failures:",G[G.fail].ot.describe().round(1).to_dict())
P("reasoning tokens: fail",G[G.fail].rt.describe().round(0).to_dict()); P("reasoning tokens: ok",G[~G.fail].rt.describe().round(0).to_dict())
G["rbin"]=pd.cut(G.rt,[-1,0,200,400,600,700,800]); P(G.groupby("rbin",observed=True).fail.agg(["size","sum","mean"]))
P("reasoning tokens by variant (setup1, mean):",G.groupby("variant").rt.mean().round(0).to_dict())
P("KW reasoning tokens across variants p=",stats.kruskal(*[x.rt.values for _,x in G.groupby("variant")]).pvalue)
P("reasoning tokens by tool_decision:",G.groupby("tool_decision").rt.mean().round(0).to_dict())
P("fails by profile (top):",G[G.fail].profile_id.value_counts().head(8).to_dict(), "n profiles with >=1 fail",G[G.fail].profile_id.nunique())
gm=M.loc[("google",1)]; P("google setup1 majority NaN cells by variant:",gm.isna().sum().to_dict())
P("google setup2/3 fails by variant:",g1[g1.setup>1].groupby(["setup","variant"]).fail.sum().unstack().to_dict("index"))
# deny-rate among parsed google vs others (selection check)
P("setup1 deny rate parsed by vendor:",df[(df.setup==1)&df.dec.notna()].groupby("vendor").dec.apply(lambda s:(s=='deny').mean()).round(3).to_dict())
# ---- setup 3 descriptive M1
s3=df[(df.setup==3)&(df.tool_decision=="deny")&df.parse_ok].copy()
P("== SETUP3 fixed denials parsed calls",len(s3),"profiles",s3.profile_id.nunique())
P("letters whose decision != deny:",int((s3.dec!="deny").sum()))
ex=s3.explode("codes").reset_index(drop=True); tab=pd.crosstab(ex.codes,ex.variant,normalize="columns").round(3); P(tab)
tab.to_csv(OUT+"m1a_code_share_by_variant.csv")
P("codes per letter by variant:",s3.groupby("variant").codes.apply(lambda s: np.mean([len(x) for x in s])).round(2).to_dict())
P("letters citing exactly tool codes (set equality) by vendor:",s3.assign(eq=s3.apply(lambda r:set(r.codes)==set(r.tool_reason_codes),axis=1)).groupby("vendor").eq.mean().round(3).to_dict())
P("codes outside tool codes share by vendor/variant:");P(s3.assign(o=s3.apply(lambda r:len(set(r.codes)-set(r.tool_reason_codes))>0,axis=1)).pivot_table(index="variant",columns="vendor",values="o",aggfunc="mean").round(3))
def jsd(a,b):
    ks=sorted(set(a)|set(b)); p=np.array([a.get(k,0) for k in ks],float)+1e-12; q=np.array([b.get(k,0) for k in ks],float)+1e-12
    p/=p.sum();q/=q.sum();m=(p+q)/2;return 0.5*(p*np.log2(p/m)).sum()+0.5*(q*np.log2(q/m)).sum()
cc=lambda v: C.Counter(c for x in s3[s3.variant==v].codes for c in x)
P("pooled JSD vs base:",{v:round(jsd(cc("base"),cc(v)),5) for v in ["control_dup","name_b","zip_b","name_w_placebo","zip_placebo","anchor"]})
P("hedging rate by variant x vendor (fixed denials):");P(s3.pivot_table(index="variant",columns="vendor",values="hedging",aggfunc="mean").round(3))
P("hedging pooled by variant:",s3.groupby("variant").hedging.mean().round(3).to_dict())
P("hedging fixed approvals pooled:",df[(df.setup==3)&(df.tool_decision=="approve")&df.parse_ok].groupby("variant").hedging.mean().round(3).to_dict())
U=s3.groupby(["vendor","profile_id","variant"]).hedging.mean().unstack()
pw={}
for v in ["control_dup","name_b","zip_b","name_w_placebo","zip_placebo"]:
    d=(U[v]-U["base"]).dropna(); pw[v]=dict(n=len(d),mean=d.mean(),sd=d.std(ddof=1),nonzero=int((d!=0).sum()))
    # per-letter: within cell variance
P("within-pair diff of unit hedging rate (unit=vendor x profile, 3 reps):",{k:{a:round(b,4) for a,b in x.items()} for k,x in pw.items()})
wv=s3.groupby(["vendor","profile_id","variant"]).hedging.var(ddof=1).mean(); P("mean within-cell (repeat) variance of hedging:",round(wv,4))
P("between-profile SD of unit hedging rate:",round(U.stack().groupby(level=[0,1]).mean().std(),4))
# per-code paired diffs SD
for v in ["name_b","zip_b"]:
    out={}
    for code in sorted(set(c for x in s3.codes for c in x)):
        f=s3.assign(h=s3.codes.apply(lambda x: code in x)).groupby(["vendor","profile_id","variant"]).h.mean().unstack()
        d=(f[v]-f["base"]).dropna(); out[code]=(round(d.mean(),3),round(d.std(),3))
    P("M1a per-code paired diff (mean,sd)",v,out)
json.dump({"m3":{k:{a:(list(map(float,b)) if isinstance(b,tuple) else (float(b) if not isinstance(b,list) else b)) for a,b in r.items()} for k,r in res.items()},"m1b_pw":{k:{a:float(b) for a,b in x.items()} for k,x in pw.items()}},open(OUT+"verify_out.json","w"),indent=1)
# per call costs
cst=df[df.status=="done"].groupby(["vendor","setup"]).agg(mean_cost=("cost_usd","mean"),mean_in=("input_tokens","mean"),mean_out=("output_tokens","mean"),mean_reason=("reasoning_tokens",lambda s:s.fillna(0).mean()))
P(cst.round(6)); cst.to_csv(OUT+"per_call_cost.csv")
P("judge cost per judged call:",led[led.model=="gpt-5-nano"].cost.sum()/len(J), "judge rows",(led.model=="gpt-5-nano").sum())
