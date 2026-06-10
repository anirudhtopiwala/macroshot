#!/usr/bin/env python3
"""Regenerate runs/FINAL_RESULTS.html from whatever run dirs exist.

Auto-discovers, per (model, condition), the run dir with the most scoreable
predictions, scores against ground truth from data/prompts.json, computes
per-macro MAE / RelErr / MedPE + Avg composites, and renders the public page.
Run:  .venv/bin/python -m eval.nutrition5k.tools.build_dashboard
"""
import json,os,glob,csv,html,sys,statistics as s
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../nutrition5k
os.chdir(HERE)
# Single dashboard, headlined on AvgMedPE (robust median % error); AvgMAE shown beneath.
METRIC, PRIMARY, SECOND = "medpe", "avgmed", "avgmae"
PSUF, SSUF, SLAB = "%", "", "MAE"
PRLBL, SCLBL = "AvgMedPE", "AvgMAE"
BAND1, BAND2 = 30, 50
OUTNAME = "runs/FINAL_RESULTS_MEDPE.html"
M=["calories","mass_g","fat_g","carb_g","protein_g"]; LBL={"calories":"Calories","mass_g":"Mass","fat_g":"Fat","carb_g":"Carbs","protein_g":"Protein"}
GH="https://github.com/anirudhtopiwala/macroshot/blob/main/src/gemini.py"; WANG="https://doi.org/10.1016/j.crfs.2026.101405"; N5K="https://arxiv.org/abs/2103.03375"
esc=lambda t: html.escape(t)
P=json.load(open('data/prompts.json')); ds=P['dishes'] if isinstance(P,dict) and 'dishes' in P else P
Dm=ds if isinstance(ds,dict) else {x['dish_id']:x for x in ds}
GT={d:Dm[d]['ground_truth']['totals'] for d in Dm}
Meta=json.load(open('data/selected_500_meta.json')) if os.path.exists('data/selected_500_meta.json') else {}
def get_complexity(dish_id):
    n=Meta.get(dish_id,{}).get('n_real_ingr',0)
    if isinstance(n,int): return 'simple' if n<=5 else ('medium' if n<=9 else 'complex')
    return None
def rows_from_dir(run,cond):
    out=[]
    for f in glob.glob(f"{run}/predictions/{cond}/*.json"):
        try:d=json.load(open(f))
        except:continue
        did=os.path.basename(f)[:-5]; p=d.get('parsed') if isinstance(d.get('parsed'),dict) else d
        if did in GT and all(isinstance(p.get(m),(int,float)) for m in M): out.append((p,GT[did]))
    return out
def best_dir(globpat,cond):
    best=[]; 
    for run in glob.glob(globpat):
        r=rows_from_dir(run,cond)
        if len(r)>len(best): best=r
    return best
def rows_csv(path,cond):
    out=[]
    for r in csv.DictReader(open(path)):
        if r["condition"]==cond: out.append(({m:float(r[f"pred_{m}"]) for m in M},{m:float(r[f"gt_{m}"]) for m in M}))
    return out
def metrics_for_complexity(globpat,cond):
    """Get metrics by complexity level for a given model/condition"""
    out={'simple':{},'medium':{},'complex':{}}
    for run in glob.glob(globpat):
        for f in glob.glob(f"{run}/predictions/{cond}/*.json"):
            try:d=json.load(open(f))
            except:continue
            did=os.path.basename(f)[:-5]; p=d.get('parsed') if isinstance(d.get('parsed'),dict) else d
            if did in GT and all(isinstance(p.get(m),(int,float)) for m in M):
                comp=get_complexity(did)
                if comp: out[comp][did]=(p,GT[did])
    result={}
    for comp in ['simple','medium','complex']:
        result[comp]=metrics(list(out[comp].values())) if out[comp] else None
    return result
def metrics(rows):
    if not rows: return None
    mac={}
    for m in M:
        ae=[abs(p[m]-g[m]) for p,g in rows]; pe=[abs(p[m]-g[m])/g[m] for p,g in rows if g[m]]
        mac[m]={"mae":round(s.mean(ae),1),"rel":round(s.mean(pe)*100) if pe else None,"med":round(s.median(pe)*100) if pe else None}
    return {"avgmae":round(s.mean(mac[m]["mae"] for m in M),1),"avgrel":round(s.mean(mac[m]["rel"] for m in M)),"avgmed":round(s.mean(mac[m]["med"] for m in M)),"macros":mac,"n":len(rows)}
def metrics_by_complexity(rows_with_ids):
    out={}
    for comp in ['simple','medium','complex']:
        rows=[r for d,r in rows_with_ids if get_complexity(d)==comp]
        out[comp]=metrics(rows) if rows else None
    return out
O47="runs/claude-opus-4-7-subagent_n100_20260527_060817/per_dish.csv"
GLOB={"Flash-Lite":"runs/gemini-2.5-flash-lite_*","Flash-full":"runs/gemini-2.5-flash_2*","Opus 4.7":"runs/opus-4-7-*","Opus 4.8":"runs/opus-4-8-*"}
models=["Flash-Lite","Flash-full","Opus 4.7","Opus 4.8"]
# Internal keys above map to run dirs / pricing; DISP = full model name shown in the page.
DISP={"Flash-Lite":"Gemini 2.5 Flash-Lite","Flash-full":"Gemini 2.5 Flash","Opus 4.7":"Claude Opus 4.7","Opus 4.8":"Claude Opus 4.8"}
def dn(m): return DISP.get(m,m)
FOCUS=[("Generic Cam","generic_cam","Generic Wang-style prompt &middot; photo only"),
 ("Generic Cam Ingredients","generic_cam_ingredients","Generic prompt &middot; photo + the dish&rsquo;s <b>true ingredient names</b> &mdash; a best-case reference, not a real user flow"),
 ("MacroShot Cam","macroshot_cam","MacroShot system prompt &middot; photo only"),
 ("MacroShot Cam Text Terse","macroshot_cam_text_terse","MacroShot system prompt &middot; photo + a terse user caption &middot; <i>shipped flow</i>"),
 ("MacroShot Text Terse","macroshot_text_terse","MacroShot text-only prompt &middot; terse description, no photo"),
 ("MacroShot Text Detailed","macroshot_text_detailed","MacroShot text-only prompt &middot; detailed description, no photo")]
F={}
COMPLEXITY={}  # complexity breakdown: [model][cond][complexity] = metrics
for model in models:
    F[model]={}
    COMPLEXITY[model]={}
    for lab,c,_ in FOCUS:
        if model=="Opus 4.7":
            rows = rows_csv(O47,c) or best_dir(GLOB[model],c)
        else:
            rows = best_dir(GLOB[model],c)
        F[model][c]=metrics(rows)
        COMPLEXITY[model][c]=metrics_for_complexity(GLOB[model],c)
def band(v): return "na" if v is None else ("g" if v<=BAND1 else ("y" if v<=BAND2 else "r"))
def mb(v): return "na" if v is None else ("g" if v<=30 else ("y" if v<=50 else "r"))  # MedPE bands
HMAX=max([F[m][c][PRIMARY] for m in models for _,c,_ in FOCUS if F.get(m,{}).get(c)] or [1])
# Per-model sample size (max scored dishes across that model's conditions) - models differ now.
NMODEL={m: max([F[m][c]["n"] for _,c,_ in FOCUS if F.get(m,{}).get(c)] or [0]) for m in models}
def hcell(m,c):
    x=F.get(m,{}).get(c)
    if not x: return '<td class="na">&mdash;</td>'
    nt=f'<span class="n"> n{x["n"]}</span>' if x["n"]<NMODEL[m] else ''
    prim=x[PRIMARY]; sec=x[SECOND]; w=min(100,round(prim/HMAX*100))
    return f'<td class="hc {band(prim)}"><span class=bar style="width:{w}%"></span><span class=v>{prim}{PSUF}{nt}<span class=pct><br>{sec}{SSUF} {SLAB}</span></span></td>'
WANG_REF=('<tr class=ref><td class=l>Wang et al. 2026 &middot; Gemini Flash, image-only (n=3466)</td><td colspan=4 style=text-align:left>RelErr 161% &middot; AvgMAE 45.55 &middot; the published baseline (median PE not reported)</td></tr>' if METRIC=="medpe" else '<tr class=ref><td class=l>Wang et al. 2026 &middot; Gemini Flash, image-only (n=3466)</td><td colspan=4 style=text-align:left>AvgMAE 45.55 &middot; the published baseline this reconstructs</td></tr>')
hrows=WANG_REF+"".join("<tr><td class=l>"+lab+"</td>"+"".join(hcell(m,c) for m in models)+"</tr>" for lab,c,_ in FOCUS)
def permodel(model):
    pres=[(lab,c) for lab,c,_ in FOCUS if F.get(model,{}).get(c)]
    if not pres: return ""
    head="".join(f"<th>{LBL[m]}<br><span class=pct>MAE<br>RelErr%<br>MedPE%</span></th>" for m in M); body=""
    for lab,c in pres:
        x=F[model][c]; n=f" <span class=n>n{x['n']}</span>" if x['n']<NMODEL[model] else ""
        mb=lambda v:'na' if v is None else ('g' if v<=30 else ('y' if v<=50 else 'r'))
        cells="".join(f"<td class={mb(x['macros'][m]['med'])}>{x['macros'][m]['mae']}<span class=pct><br>{x['macros'][m]['rel']}% &middot; {x['macros'][m]['med']}%</span></td>" for m in M)
        body+=f"<tr><td class=l>{lab}{n}</td>{cells}<td class=avg>{x['avgmae']}<span class=pct><br>{x['avgrel']}% &middot; {x['avgmed']}%</span></td></tr>"
    return f"<h3>{dn(model)} <span class=n>n={NMODEL[model]}</span></h3><table><thead><tr><th>option</th>{head}<th>Avg<br><span class=pct>AvgMAE<br>AvgRelErr%<br>AvgMedPE%</span></th></tr></thead><tbody>{body}</tbody></table>"
permodel_html="".join(permodel(m) for m in models)
# --- deltas / "what moves the needle" ---
def dpct(model,fr,to):
    a=F.get(model,{}).get(fr); b=F.get(model,{}).get(to)
    if not a or not b: return None
    return a[PRIMARY],b[PRIMARY],(b[PRIMARY]-a[PRIMARY])/a[PRIMARY]*100
def dcell(model,fr,to):
    r=dpct(model,fr,to)
    if not r: return '<td class=na>&mdash;</td>'
    a,b,p=r; return f'<td class={"g" if p<0 else "r"}>{"&#9660;" if p<0 else "&#9650;"} {p:+.0f}%<span class=pct><br>{a:.0f}{PSUF}&rarr;{b:.0f}{PSUF}</span></td>'
COMPS=[("Generic Cam &rarr; MacroShot Cam &middot; same photo, our prompt","generic_cam","macroshot_cam"),
 ("Generic Cam &rarr; Generic Cam Ingredients &middot; add GT ingredients","generic_cam","generic_cam_ingredients"),
 ("MacroShot Cam &rarr; MacroShot Cam Text Terse &middot; add user caption","macroshot_cam","macroshot_cam_text_terse"),
 ("MacroShot Text Terse &rarr; MacroShot Cam Text Terse &middot; add the photo","macroshot_text_terse","macroshot_cam_text_terse"),
 ("MacroShot Text Terse &rarr; MacroShot Text Detailed","macroshot_text_terse","macroshot_text_detailed")]
comprows="".join(f"<tr><td class=l>{lab}</td>{dcell('Flash-Lite',fr,to)}{dcell('Opus 4.8',fr,to)}</tr>" for lab,fr,to in COMPS)
# per-macro deltas (the nutrients an app cares about; mass/grams excluded)
M4=["calories","protein_g","carb_g","fat_g"]; PMAC="med" if METRIC=="medpe" else "mae"
def dmac(model,fr,to,macro):
    a=F.get(model,{}).get(fr); b=F.get(model,{}).get(to)
    if not a or not b: return '<td class=na>&mdash;</td>'
    av=a["macros"][macro][PMAC]; bv=b["macros"][macro][PMAC]
    if not av: return '<td class=na>&mdash;</td>'
    p=(bv-av)/av*100
    return f'<td class={"g" if p<0 else "r"}>{"&#9660;" if p<0 else "&#9650;"} {p:+.0f}%<span class=pct><br>{av:.0f}{PSUF}&rarr;{bv:.0f}{PSUF}</span></td>'
def dmac_avg(model,fr,to):
    a=F.get(model,{}).get(fr); b=F.get(model,{}).get(to)
    if not a or not b: return '<td class=na>&mdash;</td>'
    av=s.mean(a["macros"][m][PMAC] for m in M4); bv=s.mean(b["macros"][m][PMAC] for m in M4)
    if not av: return '<td class=na>&mdash;</td>'
    p=(bv-av)/av*100
    return f'<td class={"g" if p<0 else "r"}>{"&#9660;" if p<0 else "&#9650;"} {p:+.0f}%<span class=pct><br>{av:.0f}{PSUF}&rarr;{bv:.0f}{PSUF}</span></td>'
def needle_macro(model):
    metric_name = "MedPE%" if METRIC=="medpe" else "MAE"
    head="".join(f"<th>{LBL[m]}<br><span class=pct>% Δ<br>{metric_name}</span></th>" for m in M4)+f"<th>Avg (4)<br><span class=pct>% Δ<br>{metric_name}</span></th>"
    body="".join(f"<tr><td class=l>{lab}</td>"+"".join(dmac(model,fr,to,m) for m in M4)+dmac_avg(model,fr,to)+"</tr>" for lab,fr,to in COMPS)
    return f"<h3>{dn(model)}</h3><table><thead><tr><th>change</th>{head}</tr></thead><tbody>{body}</tbody></table>"
needle_macro_html="".join(needle_macro(m) for m in ["Flash-Lite","Opus 4.8"])
# --- cost model: list prices per 1M tokens (June 2026); Gemini tokens MEASURED from our runs, Opus ESTIMATED ---
GEM_PRICE_URL="https://ai.google.dev/gemini-api/docs/pricing"; CLA_PRICE_URL="https://platform.claude.com/docs/en/about-claude/pricing"
PRICE={"Flash-Lite":(0.10,0.40),"Flash-full":(0.30,2.50),"Opus 4.7":(5.0,25.0),"Opus 4.8":(5.0,25.0)}
TOK={"Flash-Lite":(2418,753,"measured"),"Flash-full":(2000,548,"measured"),"Opus 4.7":(3900,700,"estimated"),"Opus 4.8":(3900,700,"estimated")}
MEALS_MONTH=3*30  # 3 meals/day x 30 days
def costmeal(model):  # $ per single meal
    pi,po=PRICE[model]; ti,to,_=TOK[model]; return (ti*pi+to*po)/1e6
def costmonth(model): return costmeal(model)*MEALS_MONTH  # $ per active user / month
def costrow(model):
    x=F.get(model,{}).get("macroshot_cam_text_terse")
    macs="".join((f"<td class={mb(x['macros'][mm]['med'])}>{x['macros'][mm]['med']}%</td>" if x else "<td class=na>&mdash;</td>") for mm in M4)
    ti,to,kind=TOK[model]; m=costmonth(model); mult=m/costmonth("Flash-Lite"); star="*" if kind=="estimated" else ""
    ms=f"{mult:.1f}" if mult<10 else f"{mult:.0f}"
    return f"<tr><td class=l>{dn(model)}</td>{macs}<td>{ti} / {to}{star}</td><td>${m:.2f}</td><td>{ms}&times;</td></tr>"
costrows="".join(costrow(m) for m in models)
opus_mult=round(costmonth("Opus 4.8")/costmonth("Flash-Lite")); ff_mult=round(costmonth("Flash-full")/costmonth("Flash-Lite"),1)
fl_month=costmonth("Flash-Lite"); op_month=costmonth("Opus 4.8")
def _p(model,fr,to):
    r=dpct(model,fr,to); return abs(round(r[2])) if r else None
photo_fl,photo_op=_p('Flash-Lite','macroshot_text_terse','macroshot_cam_text_terse'),_p('Opus 4.8','macroshot_text_terse','macroshot_cam_text_terse')
ingr_fl,ingr_op=_p('Flash-Lite','generic_cam','generic_cam_ingredients'),_p('Opus 4.8','generic_cam','generic_cam_ingredients')
cap_fl,cap_op=_p('Flash-Lite','macroshot_cam','macroshot_cam_text_terse'),_p('Opus 4.8','macroshot_cam','macroshot_cam_text_terse')
det_fl=F.get('Flash-Lite',{}).get('macroshot_text_detailed',{}).get('avgmed'); det_op=F.get('Opus 4.8',{}).get('macroshot_text_detailed',{}).get('avgmed')
EX={"gt":"corn; garlic; caesar salad; nopales; olive oil; pepper; green beans; lime; sour cream; jicama; arugula; fish; carrot",
 "terse":"Had fish with caesar salad, green beans, corn, and some other veggies.",
 "detailed":"Had a good portion of fish, a side of caesar salad, and green beans. Also a small mix of corn and other veggies, with olive oil, lime, and sour cream."}
WANGP="Calculate the total calories (kcal), total weight (g), fat content (g), carbohydrate content (g), and protein content (g) for the food in this image. Reply with JSON only: {\"calories\":<kcal>,\"mass_g\":<g>,\"fat_g\":<g>,\"carb_g\":<g>,\"protein_g\":<g>}."
CSS="""
:root{--bg:#0f1115;--card:#171a21;--line:#272c36;--mut:#8b93a7;--fg:#e7eaf0;--g:#34d399;--y:#fbbf24;--r:#f87171;--blue:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1020px;margin:0 auto;padding:36px 22px 100px}h1{font-size:28px;margin:0 0 6px}h2{font-size:19px;margin:38px 0 10px;border-bottom:1px solid var(--line);padding-bottom:7px}h3{font-size:14px;color:var(--blue);margin:20px 0 4px}
.sub{color:var(--mut);max-width:860px}a{color:var(--blue)}
table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-variant-numeric:tabular-nums}th,td{padding:7px 9px;text-align:right;border-bottom:1px solid #20242d;font-size:13px}th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px}td.l{color:#cdd6ea}.or{color:var(--mut);font-style:italic}
tr.win td{background:rgba(52,211,153,.10)}tr.win td.l{box-shadow:inset 3px 0 0 var(--g);font-weight:600;color:#eafff5}.star{color:var(--g);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap;margin-left:5px}
td.g{color:var(--g);font-weight:600;background:rgba(52,211,153,.09)}td.y{color:var(--y);background:rgba(251,191,36,.08)}td.r{color:var(--r);font-weight:600;background:rgba(248,113,113,.10)}td.na{color:#3a4150}
.pct{color:var(--mut);font-size:11px}.n{color:var(--mut);font-size:10px}.avg{color:#cdd6ea;font-weight:600}
td.hc{position:relative}.bar{position:absolute;left:0;top:50%;transform:translateY(-50%);height:60%;border-radius:2px;opacity:.22;z-index:0}.v{position:relative;z-index:1}
td.g .bar{background:var(--g)}td.y .bar{background:var(--y)}td.r .bar{background:var(--r)}
.leg{color:var(--mut);font-size:12px;margin:2px 0 14px}.chip{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 12px;vertical-align:-1px}.chip.g{background:var(--g)}.chip.y{background:var(--y)}.chip.r{background:var(--r)}
.ref td{color:var(--mut);font-style:italic;border-top:1px solid var(--line)}.ref td.l{color:var(--mut)}
@media(max-width:680px){table{display:block;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch}th:first-child,td:first-child{position:sticky;left:0;background:var(--bg);white-space:normal}}
.glossary dt{color:#cdd6ea;font-weight:600;margin-top:8px}.glossary dd{margin:0 0 4px;color:var(--mut)}
.key{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:8px;padding:12px 16px;margin:12px 0}.key b{color:#cdd6ea}
.ex{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:10px 0}.ex .lab{color:var(--mut);font-size:11px;text-transform:uppercase}.ex .v{color:#cdd6ea;margin:2px 0 10px}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:0 14px}summary{cursor:pointer;padding:11px 0;color:#cdd6ea;font-weight:600;font-size:13.5px}
pre{white-space:pre-wrap;color:#aeb6c8;font:12px/1.5 ui-monospace,Menlo,monospace;background:#0c0e12;border:1px solid var(--line);border-radius:6px;padding:12px;margin:0 0 12px}
code{background:#0c0e12;border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:12px}.foot{color:var(--mut);font-size:12px;border-top:1px solid var(--line);margin-top:36px;padding-top:16px}
"""
# ---- Versus published baselines: Wang et al. 2026 Tables 4-5 (image-only / "w/o ingredients"), n=3466 ----
PAPER_N="3,466"
PAPER={  # avgmae, avgrel% from Table 4 (image-only / w/o ingredients); mac = per-nutrient RelErr% from Table 5
 "Doubao-1.5-vision-pro":{"avgmae":38.0,"avgrel":99, "mac":{"calories":66,"mass_g":44,"fat_g":223,"carb_g":90, "protein_g":74}},
 "GPT-4.1 mini":         {"avgmae":39.2,"avgrel":119,"mac":{"calories":77,"mass_g":43,"fat_g":288,"carb_g":102,"protein_g":86}},
 "Gemini 2.5 Flash":     {"avgmae":45.55,"avgrel":161,"mac":{"calories":93,"mass_g":47,"fat_g":482,"carb_g":90, "protein_g":94}},
}
PAPER_ORDER=["Doubao-1.5-vision-pro","GPT-4.1 mini","Gemini 2.5 Flash"]
OUR_IMG=[("Flash-Lite","generic_cam"),("Flash-full","generic_cam"),
 ("Opus 4.7","generic_cam"),("Opus 4.8","generic_cam")]
OUR_CAP=[("Flash-Lite","macroshot_cam_text_terse"),("Opus 4.7","macroshot_cam_text_terse"),("Opus 4.8","macroshot_cam_text_terse")]
PN=["calories","mass_g","fat_g","carb_g","protein_g"]
PN_ORDERED=["calories","protein_g","carb_g","fat_g","mass_g"]
# best MacroShot result (lowest AvgMAE among our comparison rows) - highlighted in both tables
_avail=[(m,c) for m,c in OUR_IMG+OUR_CAP if F.get(m,{}).get(c)]
WIN=min(_avail,key=lambda t:F[t[0]][t[1]]["avgmae"]) if _avail else None
def _maec(v): return "g" if v<=45 else ("y" if v<=60 else "r")
def _relc(v): return "g" if v<=100 else ("y" if v<=160 else "r")
def _pnc(v):  return "g" if v<=100 else ("y" if v<=200 else "r")   # per-nutrient rel; fat denominators blow up
def _grp(txt,span): return f"<tr><td colspan={span} style='text-align:left;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:15px 9px 5px;border-bottom:1px solid var(--line)'>{txt}</td></tr>"
def _mae_avg(mac_dict): return round(s.mean(mac_dict[m]["mae"] for m in M),1)
def _hl_paper_mae(n):
    d=PAPER[n]
    avg_mae=d["avgmae"]; avg_rel=d["avgrel"]
    avg_disp=f"<b>{avg_mae}</b><span class=pct><br>({avg_rel}%)</span>"
    cells="".join(f"<td class={_pnc(d['mac'][m])}>&mdash;<span class=pct><br>({d['mac'][m]}%)</span></td>" if m in d['mac'] else "<td class=na>&mdash;</td>" for m in PN_ORDERED)
    return f"<tr><td class=l>{n}</td><td>photo only</td><td class={_maec(avg_mae)}>{avg_disp}</td>{cells}<td>{PAPER_N}</td></tr>"
def _hl_our_mae(model,cond,inp):
    x=F.get(model,{}).get(cond)
    if not x: return ""
    win=WIN==(model,cond); tr=" class=win" if win else ""; star=" <span class=star>&#9733; best</span>" if win else ""
    avg_val=x["avgmae"]; avg_rel=x["avgrel"]; avg_disp=f"<b>{avg_val}</b>" if win else f"{avg_val}"
    avg_disp+=f"<span class=pct><br>({avg_rel}%)</span>"
    cells="".join(f"<td class={_maec(x['macros'][m]['mae'])}>{x['macros'][m]['mae']}<span class=pct><br>({x['macros'][m]['rel']}%)</span></td>" for m in PN_ORDERED)
    return f"<tr{tr}><td class=l>{dn(model)}{star}</td><td>{inp}</td><td class={_maec(avg_val)}>{avg_disp}</td>{cells}<td>{x['n']}</td></tr>"
cmp_tbl=("<table><thead><tr><th>model</th><th>input</th><th>Avg MAE<br><span class=pct>error (kcal/g)<br>RelErr%</span></th>"+"".join(f"<th>{LBL[m]}<br><span class=pct>MAE<br>RelErr%</span></th>" for m in PN_ORDERED)+"<th>n<br><span class=pct>dishes<br>scored</span></th></tr></thead><tbody>"
 +_grp(f"Published &middot; Wang et al. 2026 (image only, n&asymp;{PAPER_N})",len(PN_ORDERED)+3)+"".join(_hl_paper_mae(n) for n in PAPER_ORDER)
 +_grp("MacroShot &middot; our harness, photo only",len(PN_ORDERED)+3)+"".join(_hl_our_mae(*r,"photo") for r in OUR_IMG)
 +_grp("MacroShot &middot; our harness, photo + user caption (shipped flow)",len(PN_ORDERED)+3)+"".join(_hl_our_mae(*r,"photo + caption") for r in OUR_CAP)
 +"</tbody></table>")
_pbn,_pb=min(PAPER.items(),key=lambda kv:kv[1]["avgmae"])  # strongest published model
if WIN:
    _wx=F[WIN[0]][WIN[1]]; _wn=dn(WIN[0]); _wi="with a user caption" if WIN[1]=="macroshot_cam_text_terse" else "from the photo alone"
    _d=_wx["avgmae"]-_pb["avgmae"]; _rel="beats" if _d<=-1.5 else ("matches" if abs(_d)<=1.5 else "comes close to")
    _gf=PAPER["Gemini 2.5 Flash"]["avgmae"]; _gftxt=f", and lands ahead of their Gemini&nbsp;2.5&nbsp;Flash ({_gf})" if _wx["avgmae"]<_gf else ""
    win_callout=(f"<div class=key style='border-left-color:var(--g)'><b>Best result.</b> MacroShot&rsquo;s strongest configuration &mdash; <b>{_wn} {_wi}</b> &mdash; reaches <b>AvgMAE&nbsp;{_wx['avgmae']}</b>, which <b>{_rel} the best of the 17 models</b> benchmarked by Wang et&nbsp;al. (<b>{_pbn}, {_pb['avgmae']}</b>){_gftxt}. And the cheap, <b>shipped Gemini&nbsp;2.5&nbsp;Flash-Lite</b> sits inside that published pack at a fraction of the per-meal cost.</div>")
else:
    win_callout=""
paper_block="\n".join([
 "<h2>Versus published baselines</h2>",
 f"<p class=sub>The strongest vision models from <a href='{WANG}'>Wang et&nbsp;al. 2026</a> (image-only, n&asymp;{PAPER_N}) next to MacroShot&rsquo;s eval. MAE (Mean Absolute Error) is the average gap between the estimate and ground truth in native units (kcal or grams) &mdash; the most direct read of accuracy. Color: <span class='chip g'></span>&le;45 <span class='chip y'></span>&le;60 <span class='chip r'></span>&gt;60.</p>",
 win_callout,
 cmp_tbl,
 "<div class=key><b>How to read this.</b> Each cell shows <b>MAE</b> with <b>RelErr%</b> in parentheses below. <b>Avg MAE</b> (leftmost data column) averages error across all five nutrients. The nutrient columns show per-macro error: for published baselines, MAE is unavailable (shown as &mdash;) and only RelErr% is shown; for MacroShot, both MAE and RelErr% are shown. MAE is in native units (kcal for Calories; grams for the rest). A smaller value is better. Published rows are from Wang et&nbsp;al. 2026 Table 4&ndash;5; MacroShot rows are from this eval &mdash; different runs, so treat the published baseline as a reference point rather than direct comparison.</div>",
])
H=["<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
 "<title>MacroShot &mdash; meal-macro accuracy eval</title><style>"+CSS+"</style></head><body><div class=wrap>",
 "<h1>MacroShot &mdash; meal-macro accuracy eval</h1>",
 f"<p class=sub>How accurately can an LLM read calories &amp; macros from a meal photo (and/or a typed description)? Benchmarked on <a href='{N5K}'>Nutrition5K</a> against the published baseline of <a href='{WANG}'>Wang et&nbsp;al. 2026</a>, dishes stratified by complexity (seed 42). <b>Gemini&nbsp;2.5&nbsp;Flash-Lite (the shipped model) is evaluated on the full n={NMODEL['Flash-Lite']}</b>; the other models are at n&asymp;100 previews. Each model column shows its n. Lower error is better.</p>",
 "<p class=sub style='margin-top:-2px'>&rarr; <a href='gallery.html'><b>Per-dish gallery</b></a>: the meals every model nails, and the ones they all miss (best 5 / worst 5, with the photo and each model&rsquo;s read). &nbsp;&middot;&nbsp; <a href='https://github.com/anirudhtopiwala/macroshot/releases/latest'><b>Download the full results bundle</b></a>: every prediction file, ground truth, scoring scripts, and source images (~100&nbsp;MB zip).</p>",
 f"<div class=key style='border-left-color:var(--g)'><b>Key takeaways</b><ul style='margin:8px 0 0;padding-left:18px;color:#cdd6ea'>"
 f"<li><b>The photo is the single biggest lever.</b> With the <i>same</i> user caption, adding the image cut error by ~{photo_fl}% (Gemini&nbsp;2.5&nbsp;Flash-Lite) / ~{photo_op}% (Claude&nbsp;Opus&nbsp;4.8).</li>"
 f"<li><b>Extra information only helps if the prompt knows what to do with it.</b> Handing the <i>generic</i> prompt the true ingredient list made it <span style='color:var(--r)'>worse</span> (+{ingr_fl}% / +{ingr_op}%) &mdash; it stacks standard servings. Giving <i>MacroShot</i> the user&rsquo;s caption made it <span style='color:var(--g)'>better</span> (&minus;{cap_fl}% / &minus;{cap_op}%).</li>"
 f"<li><b>Photo-free text logging still works.</b> With no image, detailed typed descriptions land within ~{det_op}% (Claude&nbsp;Opus&nbsp;4.8) / ~{det_fl}% (Gemini&nbsp;2.5&nbsp;Flash-Lite) median error &mdash; rough, but far better than nothing.</li>"
 f"<li><b>The frontier model (Claude&nbsp;Opus) is more accurate, but the same patterns hold</b> on the cheap shipped model &mdash; at <b>~{opus_mult}&times; the cost per meal</b>, which is why the cheap model ships.</li>"
 "</ul></div>",
 paper_block,
 f"<h2>Results &mdash; headline ({PRLBL})</h2>",
 "<table><thead><tr><th>option</th>"+"".join(f"<th>{dn(m)}{' <span class=n>shipped</span>' if m=='Flash-Lite' else ''}<br><span class=pct>n={NMODEL[m]}</span></th>" for m in models)+"</tr></thead><tbody>"+hrows+"</tbody></table>",
 f"<p class=leg>Cells show <b>{PRLBL}{PSUF}</b> (color) with <b>{SCLBL}{SSUF}</b> beneath; bar length is relative {PRLBL} (shorter = better). Color: <span class='chip g'></span>&le;{BAND1}{PSUF} <span class='chip y'></span>&le;{BAND2}{PSUF} <span class='chip r'></span>&gt;{BAND2}{PSUF}. Each model column lists its sample size n; a <code>nNN</code> tag on a cell flags a condition scored on fewer dishes than that model&rsquo;s headline n; blank = not run.</p>",
 "<h2>Cost vs accuracy</h2>",
 f"<p class=sub>What each model costs <b>per active user per month</b> (assuming <b>3 meals/day, {MEALS_MONTH} meals/month</b>), against accuracy on the shipped flow (<b>MacroShot Cam Text Terse</b>). The four nutrient columns are the <b>median percent error</b> per macro &mdash; <span class='chip g'></span>&le;30% <span class='chip y'></span>&le;50% <span class='chip r'></span>&gt;50%. Prices are <b>list rates per 1M tokens, June 2026</b> (<a href='{GEM_PRICE_URL}'>Gemini</a> $0.10/$0.40 Gemini&nbsp;2.5&nbsp;Flash-Lite, $0.30/$2.50 Gemini&nbsp;2.5&nbsp;Flash; <a href='{CLA_PRICE_URL}'>Claude</a> Opus $5/$25). Gemini tokens are <b>measured</b> from our runs; Opus tokens are <b>estimated</b> for an equivalent single-shot call (image (w&times;h)/750 &asymp; 1844 + prompt &asymp; 2050; output comparable to the same task on Gemini), marked <b>*</b>. Monthly cost = {MEALS_MONTH} &times; (in&times;price_in + out&times;price_out).</p>",
 "<table><thead><tr><th>model</th>"+"".join(f"<th>{LBL[m]}<br><span class=pct>median % err</span></th>" for m in M4)+"<th>tokens in / out<br><span class=pct>per meal</span></th><th>$ / user / month<br><span class=pct>3 meals/day</span></th><th>relative cost</th></tr></thead><tbody>"+costrows+"</tbody></table>",
 f"<div class=key>A daily user (3 meals/day, {MEALS_MONTH}/month) costs <b>~${fl_month:.2f}/month</b> on Gemini&nbsp;2.5&nbsp;Flash-Lite vs <b>~${op_month:.2f}/month</b> on Claude&nbsp;Opus &mdash; <b>~{opus_mult}&times;</b> for roughly a dozen points better median error. <b>Gemini&nbsp;2.5&nbsp;Flash costs ~{ff_mult}&times;</b> Gemini&nbsp;2.5&nbsp;Flash-Lite while scoring <i>worse</i> on the shipped flow (it over-estimates portions). For a free consumer app the cheap model + the right prompt is the rational ship; the frontier model is a quality ceiling, not a cost-effective default. Prompt caching / batch can cut Opus by up to ~90% / 50%.</div>",
 "<h2>What moves the needle</h2>",
 (f"<p class=sub>Each row applies one <i>change</i> to a prompt, broken out by the nutrients an app cares about &mdash; <b>mass/grams excluded</b>. Each cell is the % change in that nutrient&rsquo;s {'MedPE' if METRIC=='medpe' else 'MAE'} (<b style='color:var(--g)'>&#9660; green = better</b>, <b style='color:var(--r)'>&#9650; red = worse</b>); small numbers are {'MedPE% before&rarr;after' if METRIC=='medpe' else 'MAE before&rarr;after in native units (kcal for calories, g for the rest)'}. <b>Avg (4)</b> is the grams-free average across the four.</p>"),
 needle_macro_html,
 "<div class=key>Same move, opposite result: <b>adding the ground-truth ingredient list to the generic prompt makes it worse</b>, but <b>adding the user&rsquo;s caption to MacroShot makes it better</b> &mdash; the structured prompt knows to treat the text as identity and size portions from the image, instead of stacking a standard serving per named item. The <b>photo</b> is the largest single improvement (same caption, +image roughly halves the error). And <b>text-only logging</b>, while the weakest, still recovers usable macros.</div>",
 "<div class=key><b>Same caption, with vs without the photo.</b> The identical <b>terse</b> caption feeds BOTH <b>MacroShot Cam Text Terse</b> (photo prompt + image + caption) and <b>MacroShot Text Terse</b> (text prompt + caption, no image) &mdash; so comparing them isolates what the <b>photo</b> adds, holding the user&rsquo;s words constant.</div>",
 "<h2>Results &mdash; per-macro detail</h2><p class=sub>Each cell: <b>MAE</b> with <span class=pct>RelErr% &middot; MedPE%</span> beneath, colored by MedPE: <span class='chip g'></span>&le;30% <span class='chip y'></span>&le;50% <span class='chip r'></span>&gt;50%. AvgMAE over five nutrients over-weights Mass; for a nutrition app, Calories / Protein / Fat matter most.</p>",
 permodel_html,
 "<h2>Accuracy by dish complexity</h2>",
 "<p class=sub>How do Flash-Lite and Opus 4.8 perform on simple vs. complex dishes? Dishes are stratified by ingredient count: simple &le;5, medium 6-9, complex &ge;10. MAE is in native units (kcal for Calories, grams for the rest).</p>",
 "<table><thead><tr><th>model</th><th>variant</th><th>complexity</th><th>Avg MAE<br><span class=pct>error (kcal/g)<br>RelErr%</span></th><th>Calories<br><span class=pct>MAE<br>RelErr%</span></th><th>Protein<br><span class=pct>MAE<br>RelErr%</span></th><th>Carbs<br><span class=pct>MAE<br>RelErr%</span></th><th>Fat<br><span class=pct>MAE<br>RelErr%</span></th><th>Mass<br><span class=pct>MAE<br>RelErr%</span></th><th>n</th></tr></thead><tbody>" +
 "".join(
   f"<tr><td class=l>{dn(m)}</td><td class=l>{lab}</td><td class=l><b>{comp.capitalize()}</b></td>" +
   (f"<td class={_maec(x['avgmae'])}>{x['avgmae']}<span class=pct><br>({x['avgrel']}%)</span></td>" if x else "<td class=na>&mdash;</td>") +
   ("".join(f"<td class={_maec(x['macros'][nu]['mae'])}>{x['macros'][nu]['mae']}<span class=pct><br>({x['macros'][nu]['rel']}%)</span></td>" for nu in PN_ORDERED) if x else "".join("<td class=na>&mdash;</td>" for _ in PN_ORDERED)) +
   (f"<td>{x['n']}</td></tr>" if x else "<td>&mdash;</td></tr>")
   for m in ["Flash-Lite","Opus 4.8"] for lab,c,_ in FOCUS for comp,x in [(k,COMPLEXITY[m][c].get(k)) for k in ['simple','medium','complex']] if x
 ) +
 "</tbody></table>",
 "<h2>Methodology &amp; definitions</h2>",
 "<h3>Metrics</h3><dl class=glossary><dt>MAE &mdash; Mean Absolute Error</dt><dd>Average gap between the estimate and the truth, in native units (kcal or grams). The most direct read, though it counts a 50&nbsp;kcal miss the same whether the meal is 200 or 900&nbsp;kcal.</dd>",
 "<dt>RelErr &mdash; Relative Error (MAPE)</dt><dd>That gap as a percent of the true value, averaged over dishes: mean(|pred &minus; truth| / truth) &times; 100%. Comparable across dishes of any size, but a few tiny-value items (say, 2&nbsp;g of fat) can inflate it.</dd>",
 "<dt>MedPE &mdash; Median Percent Error</dt><dd>The median of those same percentages: half the dishes come in under this number, half over. We lead with it because, unlike the average above, a handful of extreme dishes can&rsquo;t drag it around.</dd>",
 "<dt>Avg*</dt><dd>Any figure prefixed &lsquo;Avg&rsquo; is that metric averaged across the five nutrients (calories, mass, fat, carbs, protein).</dd></dl>",
 "<h3>What each option is</h3><table><thead><tr><th>option</th><th>input</th><th style='text-align:left'>description</th></tr></thead><tbody>",
 "".join(f"<tr><td class=l>{lab}</td><td>{'text only' if 'Text' in lab else 'photo'}</td><td style='text-align:left'>{d}</td></tr>" for lab,c,d in FOCUS),"</tbody></table>",
 "<h3>Prompts</h3><table><thead><tr><th>prompt</th><th>used by</th><th>view</th></tr></thead><tbody>"
 "<tr><td class=l>Generic baseline <span class=or>(our Wang reconstruction)</span></td><td>Baseline &middot; + GT ingredients</td><td><a href='#bp'>expand &darr;</a></td></tr>"
 f"<tr><td class=l>MacroShot System Prompt &mdash; photo</td><td>MacroShot &middot; + user caption</td><td><a href='{GH}#L72'>GitHub &nearr;</a></td></tr>"
 f"<tr><td class=l>MacroShot System Prompt &mdash; text-only</td><td>Text-only &middot; terse + detailed</td><td><a href='{GH}#L165'>GitHub &nearr;</a></td></tr></tbody></table>",
 f"<details id=bp><summary>Generic baseline prompt</summary><pre>{esc(WANGP)}\n\n[+ GT ingredients prepends: \"Ingredients on this plate: &lt;names&gt;.\"]</pre></details>",
 "<details><summary>How the user captions were generated</summary>"
 "<p class=sub>Each caption was generated by <b>Gemini</b> from the dish&rsquo;s <b>ground-truth ingredient list</b>: a casual log entry, <b>no exact grams/macros leaked</b>, literal, sub-1&nbsp;g seasonings skipped. The generator <b>does see each ingredient&rsquo;s gram weight</b> and uses it for the <b>detailed</b> caption&rsquo;s vague portion cues ('a good portion') &mdash; never a number &mdash; so 'detailed' carries a mild GT-derived portion hint 'terse' does not. Verified faithful (&asymp;76% coverage, ~0 hallucinations).</p>"
 f"<div class=ex><div class=lab>Ground-truth ingredients (input to Gemini)</div><div class=v>{esc(EX['gt'])}</div><div class=lab>&rarr; Terse caption</div><div class=v>&ldquo;{esc(EX['terse'])}&rdquo;</div><div class=lab>&rarr; Detailed caption</div><div class=v>&ldquo;{esc(EX['detailed'])}&rdquo;</div></div></details>",
 f"<div class=foot><b>Method:</b> Nutrition5K (<a href='{N5K}'>Thames et&nbsp;al. 2021</a>) camera-C frame 10, stratified by complexity (seed 42). <b>Gemini&nbsp;2.5&nbsp;Flash-Lite is scored on the full n={NMODEL['Flash-Lite']} set; the other models are n&asymp;100 previews</b> (see each model column / section for its exact n). Frontier-model runs use one isolated, ground-truth-free sub-agent per dish. Baseline = our reconstruction of <a href='{WANG}'>Wang et&nbsp;al. 2026</a>&rsquo;s prompt. <b>Caveats:</b> cafeteria/single-cuisine heavy; RelErr noisy (prefer MAE / MedPE).</div>",
 "</div></body></html>"]
_html="\n".join(H)
# no em dashes anywhere (user preference): collapse spaced/unspaced em dashes to a hyphen
_html=_html.replace(" &mdash; "," - ").replace("&mdash;"," - ").replace("&ndash;","-")
open(OUTNAME,"w").write(_html)
print(f"regenerated {OUTNAME} (headline metric: {PRLBL}) - discovered cells:")
for m in models:
    for lab,c,_ in FOCUS:
        x=F[m].get(c)
        if x: print(f"  {m:<12} {lab:<34} {PRLBL}={x[PRIMARY]} n={x['n']}")
