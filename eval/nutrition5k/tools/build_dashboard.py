#!/usr/bin/env python3
"""Regenerate runs/FINAL_RESULTS.html from whatever run dirs exist.

Auto-discovers, per (model, condition), the run dir with the most scoreable
predictions, scores against ground truth from data/prompts.json, computes
per-macro MAE / RelErr / MedPE + Avg composites, and renders the public page.
Run:  .venv/bin/python -m eval.nutrition5k.tools.build_dashboard
"""
import json,os,glob,csv,html,statistics as s
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../nutrition5k
os.chdir(HERE)
M=["calories","mass_g","fat_g","carb_g","protein_g"]; LBL={"calories":"Calories","mass_g":"Mass","fat_g":"Fat","carb_g":"Carbs","protein_g":"Protein"}
GH="https://github.com/anirudhtopiwala/macroshot/blob/main/src/gemini.py"; WANG="https://doi.org/10.1016/j.crfs.2026.101405"; N5K="https://arxiv.org/abs/2103.03375"
esc=lambda t: html.escape(t)
P=json.load(open('data/prompts.json')); ds=P['dishes'] if isinstance(P,dict) and 'dishes' in P else P
Dm=ds if isinstance(ds,dict) else {x['dish_id']:x for x in ds}
GT={d:Dm[d]['ground_truth']['totals'] for d in Dm}
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
def metrics(rows):
    if not rows: return None
    mac={}
    for m in M:
        ae=[abs(p[m]-g[m]) for p,g in rows]; pe=[abs(p[m]-g[m])/g[m] for p,g in rows if g[m]]
        mac[m]={"mae":round(s.mean(ae),1),"rel":round(s.mean(pe)*100) if pe else None,"med":round(s.median(pe)*100) if pe else None}
    return {"avgmae":round(s.mean(mac[m]["mae"] for m in M),1),"avgrel":round(s.mean(mac[m]["rel"] for m in M)),"avgmed":round(s.mean(mac[m]["med"] for m in M)),"macros":mac,"n":len(rows)}
O47="runs/claude-opus-4-7-subagent_n100_20260527_060817/per_dish.csv"
GLOB={"Flash-Lite":"runs/gemini-2.5-flash-lite_*","Flash-full":"runs/gemini-2.5-flash_2*","Opus 4.7":"runs/opus-4-7-*","Opus 4.8":"runs/opus-4-8-*"}
models=["Flash-Lite","Flash-full","Opus 4.7","Opus 4.8"]
FOCUS=[("Baseline","BASELINE_unlabeled","Generic Wang-style prompt &middot; photo only"),
 ("Baseline + GT ingredients","BASELINE_labeled","Generic prompt &middot; photo + the dish&rsquo;s <b>true ingredient names</b> &mdash; a best-case reference, not a real user flow"),
 ("MacroShot","X1","MacroShot system prompt &middot; photo only"),
 ("MacroShot + user caption (terse)","X3v2","MacroShot system prompt &middot; photo + a terse user caption &middot; <i>shipped flow</i>"),
 ("Text-only (terse)","E_terse","Text-only prompt &middot; terse description, no photo"),
 ("Text-only (detailed)","E_detailed","Text-only prompt &middot; detailed description, no photo")]
F={}
for model in models:
    F[model]={}
    for lab,c,_ in FOCUS:
        if model=="Opus 4.7":
            rows = rows_csv(O47,c) or best_dir(GLOB[model],c)
        else:
            rows = best_dir(GLOB[model],c)
        F[model][c]=metrics(rows)
def band(v): return "na" if v is None else ("g" if v<=50 else ("y" if v<=70 else "r"))
HMAX=max([F[m][c]["avgmae"] for m in models for _,c,_ in FOCUS if F.get(m,{}).get(c)] or [1])
def hcell(m,c):
    x=F.get(m,{}).get(c)
    if not x: return '<td class="na">&mdash;</td>'
    nt=f'<span class="n"> n{x["n"]}</span>' if x["n"]<100 else ''
    w=min(100,round(x["avgmae"]/HMAX*100))
    return f'<td class="hc {band(x["avgmae"])}"><span class=bar style="width:{w}%"></span><span class=v>{x["avgmae"]}{nt}<span class=pct><br>{x["avgmed"]}% med</span></span></td>'
WANG_REF='<tr class=ref><td class=l>Wang et al. 2026 &middot; Gemini Flash, image-only (n=3466)</td><td colspan=4 style=text-align:left>AvgMAE 45.55 &middot; the published baseline this reconstructs</td></tr>'
hrows=WANG_REF+"".join("<tr><td class=l>"+lab+"</td>"+"".join(hcell(m,c) for m in models)+"</tr>" for lab,c,_ in FOCUS)
def permodel(model):
    pres=[(lab,c) for lab,c,_ in FOCUS if F.get(model,{}).get(c)]
    if not pres: return ""
    head="".join(f"<th>{LBL[m]}</th>" for m in M); body=""
    for lab,c in pres:
        x=F[model][c]; n=f" <span class=n>n{x['n']}</span>" if x['n']<100 else ""
        mb=lambda v:'na' if v is None else ('g' if v<=30 else ('y' if v<=50 else 'r'))
        cells="".join(f"<td class={mb(x['macros'][m]['med'])}>{x['macros'][m]['mae']}<span class=pct><br>{x['macros'][m]['rel']}% &middot; {x['macros'][m]['med']}%</span></td>" for m in M)
        body+=f"<tr><td class=l>{lab}{n}</td>{cells}<td class=avg>{x['avgmae']}<span class=pct><br>{x['avgrel']}% &middot; {x['avgmed']}%</span></td></tr>"
    return f"<h3>{model}</h3><table><thead><tr><th>option</th>{head}<th>Avg</th></tr></thead><tbody>{body}</tbody></table>"
permodel_html="".join(permodel(m) for m in models)
# --- deltas / "what moves the needle" ---
def dpct(model,fr,to):
    a=F.get(model,{}).get(fr); b=F.get(model,{}).get(to)
    if not a or not b: return None
    return a["avgmae"],b["avgmae"],(b["avgmae"]-a["avgmae"])/a["avgmae"]*100
def dcell(model,fr,to):
    r=dpct(model,fr,to)
    if not r: return '<td class=na>&mdash;</td>'
    a,b,p=r; return f'<td class={"g" if p<0 else "r"}>{"&#9660;" if p<0 else "&#9650;"} {p:+.0f}%<span class=pct><br>{a:.0f}&rarr;{b:.0f}</span></td>'
COMPS=[("Baseline &rarr; MacroShot &middot; photo only","BASELINE_unlabeled","X1"),
 ("Baseline &rarr; Baseline + GT ingredients","BASELINE_unlabeled","BASELINE_labeled"),
 ("MacroShot &rarr; MacroShot + user caption","X1","X3v2"),
 ("Text-only (terse) &rarr; MacroShot + user caption &middot; adds photo","E_terse","X3v2"),
 ("Text-only (terse) &rarr; Text-only (detailed)","E_terse","E_detailed")]
comprows="".join(f"<tr><td class=l>{lab}</td>{dcell('Flash-Lite',fr,to)}{dcell('Opus 4.8',fr,to)}</tr>" for lab,fr,to in COMPS)
def _p(model,fr,to):
    r=dpct(model,fr,to); return abs(round(r[2])) if r else None
photo_fl,photo_op=_p('Flash-Lite','E_terse','X3v2'),_p('Opus 4.8','E_terse','X3v2')
ingr_fl,ingr_op=_p('Flash-Lite','BASELINE_unlabeled','BASELINE_labeled'),_p('Opus 4.8','BASELINE_unlabeled','BASELINE_labeled')
cap_fl,cap_op=_p('Flash-Lite','X1','X3v2'),_p('Opus 4.8','X1','X3v2')
det_fl=F.get('Flash-Lite',{}).get('E_detailed',{}).get('avgmed'); det_op=F.get('Opus 4.8',{}).get('E_detailed',{}).get('avgmed')
EX={"gt":"corn; garlic; caesar salad; nopales; olive oil; pepper; green beans; lime; sour cream; jicama; arugula; fish; carrot",
 "terse":"Had fish with caesar salad, green beans, corn, and some other veggies.",
 "detailed":"Had a good portion of fish, a side of caesar salad, and green beans. Also a small mix of corn and other veggies, with olive oil, lime, and sour cream."}
try: WANGP=json.load(open("/tmp/_prompts.json"))["wang"]
except: WANGP="Calculate the total calories (kcal), total weight (g), fat content (g), carbohydrate content (g), and protein content (g) for the food in this image. Reply with JSON only: {\"calories\":<kcal>,\"mass_g\":<g>,\"fat_g\":<g>,\"carb_g\":<g>,\"protein_g\":<g>}."
CSS="""
:root{--bg:#0f1115;--card:#171a21;--line:#272c36;--mut:#8b93a7;--fg:#e7eaf0;--g:#34d399;--y:#fbbf24;--r:#f87171;--blue:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1020px;margin:0 auto;padding:36px 22px 100px}h1{font-size:28px;margin:0 0 6px}h2{font-size:19px;margin:38px 0 10px;border-bottom:1px solid var(--line);padding-bottom:7px}h3{font-size:14px;color:var(--blue);margin:20px 0 4px}
.sub{color:var(--mut);max-width:860px}a{color:var(--blue)}
table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-variant-numeric:tabular-nums}th,td{padding:7px 9px;text-align:right;border-bottom:1px solid #20242d;font-size:13px}th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px}td.l{color:#cdd6ea}.or{color:var(--mut);font-style:italic}
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
H=["<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
 "<title>MacroShot &mdash; meal-macro accuracy eval</title><style>"+CSS+"</style></head><body><div class=wrap>",
 "<h1>MacroShot &mdash; meal-macro accuracy eval</h1>",
 f"<p class=sub>How accurately can an LLM read calories &amp; macros from a meal photo (and/or a typed description)? Benchmarked on <a href='{N5K}'>Nutrition5K</a> against the published baseline of <a href='{WANG}'>Wang et&nbsp;al. 2026</a>, n=100 dishes stratified by complexity. Lower error is better.</p>",
 f"<div class=key style='border-left-color:var(--g)'><b>Key takeaways</b><ul style='margin:8px 0 0;padding-left:18px;color:#cdd6ea'>"
 f"<li><b>The photo is the single biggest lever.</b> With the <i>same</i> user caption, adding the image cut error by ~{photo_fl}% (Flash-Lite) / ~{photo_op}% (Opus&nbsp;4.8).</li>"
 f"<li><b>Extra information only helps if the prompt knows what to do with it.</b> Handing the <i>generic</i> prompt the true ingredient list made it <span style='color:var(--r)'>worse</span> (+{ingr_fl}% / +{ingr_op}%) &mdash; it stacks standard servings. Giving <i>MacroShot</i> the user&rsquo;s caption made it <span style='color:var(--g)'>better</span> (&minus;{cap_fl}% / &minus;{cap_op}%).</li>"
 f"<li><b>Photo-free text logging still works.</b> With no image, detailed typed descriptions land within ~{det_op}% (Opus) / ~{det_fl}% (Flash-Lite) median error &mdash; rough, but far better than nothing.</li>"
 f"<li><b>The frontier model (Opus) is more accurate, but the same patterns hold</b> on the cheap shipped model.</li>"
 "</ul></div>",
 "<h2>Results &mdash; headline</h2>",
 "<table><thead><tr><th>option</th><th>Flash-Lite<span class=n> shipped</span></th><th>Flash-full</th><th>Opus 4.7</th><th>Opus 4.8</th></tr></thead><tbody>"+hrows+"</tbody></table>",
 "<p class=leg>Cells show <b>AvgMAE</b> (color) with <b>AvgMedPE%</b> beneath; bar length is relative AvgMAE (shorter = better). Color: <span class='chip g'></span>&le;50 <span class='chip y'></span>&le;70 <span class='chip r'></span>&gt;70. <code>nNN</code> = sample &lt;100; blank = not run.</p>",
 "<h2>What moves the needle</h2>",
 "<p class=sub>Each row applies one <i>change</i> to a prompt; the cell is the change in AvgMAE. <b style='color:var(--g)'>&#9660; Green = error reduced (better)</b>, <b style='color:var(--r)'>&#9650; red = error increased (worse)</b>; small numbers are AvgMAE before&rarr;after.</p>",
 "<table><thead><tr><th>change</th><th>Flash-Lite</th><th>Opus 4.8</th></tr></thead><tbody>"+comprows+"</tbody></table>",
 "<div class=key>Same move, opposite result: <b>adding the ground-truth ingredient list to the generic prompt makes it worse</b>, but <b>adding the user&rsquo;s caption to MacroShot makes it better</b> &mdash; the structured prompt knows to treat the text as identity and size portions from the image, instead of stacking a standard serving per named item. The <b>photo</b> is the largest single improvement (same caption, +image roughly halves the error). And <b>text-only logging</b>, while the weakest, still recovers usable macros.</div>",
 "<div class=key><b>Same caption, with vs without the photo.</b> The identical <b>terse</b> caption feeds BOTH <b>MacroShot + user caption (terse)</b> (photo prompt + image + caption) and <b>Text-only (terse)</b> (text prompt + caption, no image) &mdash; so comparing them isolates what the <b>photo</b> adds, holding the user&rsquo;s words constant.</div>",
 "<h2>Results &mdash; per-macro detail</h2><p class=sub>Each cell: <b>MAE</b> with <span class=pct>RelErr% &middot; MedPE%</span> beneath, colored by MedPE: <span class='chip g'></span>&le;30% <span class='chip y'></span>&le;50% <span class='chip r'></span>&gt;50%. AvgMAE over five nutrients over-weights Mass; for a nutrition app, Calories / Protein / Fat matter most.</p>",
 permodel_html,
 "<h2>Methodology &amp; definitions</h2>",
 "<h3>Metrics</h3><dl class=glossary><dt>MAE &mdash; Mean Absolute Error</dt><dd>Average |prediction &minus; ground&nbsp;truth| in native units (kcal / grams). Primary, most interpretable.</dd>",
 "<dt>RelErr &mdash; Relative Error (MAPE)</dt><dd>mean(|pred &minus; truth| / truth) &times; 100%. Scale-free but inflated by tiny denominators.</dd>",
 "<dt>MedPE &mdash; Median Percent Error</dt><dd>median(|pred &minus; truth| / truth) &times; 100%. Robust counterpart &mdash; 'half of dishes within X%'. Best single UX number.</dd>",
 "<dt>Avg*</dt><dd>Averaged across the five nutrients (calories, mass, fat, carbs, protein).</dd></dl>",
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
 f"<div class=foot><b>Method:</b> Nutrition5K (<a href='{N5K}'>Thames et&nbsp;al. 2021</a>) camera-C frame 10, n=100 stratified (seed 42). Frontier-model runs use one isolated, ground-truth-free sub-agent per dish. Baseline = our reconstruction of <a href='{WANG}'>Wang et&nbsp;al. 2026</a>&rsquo;s prompt. <b>Caveats:</b> cafeteria/single-cuisine heavy; RelErr noisy (prefer MAE / MedPE); Flash-full runs with chain-of-thought on; some Opus-4.8 cells may be small-n previews.</div>",
 "</div></body></html>"]
_html="\n".join(H)
# no em dashes anywhere (user preference): collapse spaced/unspaced em dashes to a hyphen
_html=_html.replace(" &mdash; "," - ").replace("&mdash;"," - ").replace(" — "," - ").replace("—"," - ")
open("runs/FINAL_RESULTS.html","w").write(_html)
print("regenerated runs/FINAL_RESULTS.html — discovered cells:")
for m in models:
    for lab,c,_ in FOCUS:
        x=F[m].get(c)
        if x: print(f"  {m:<12} {lab:<34} AvgMAE={x['avgmae']} n={x['n']}")
