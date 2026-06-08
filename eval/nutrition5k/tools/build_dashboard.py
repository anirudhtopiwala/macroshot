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
GLOB={"Flash-Lite":"runs/gemini-2.5-flash-lite_*","Flash-full":"runs/gemini-2.5-flash_2*","Opus 4.8":"runs/opus-4-8-*"}
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
        rows = rows_csv(O47,c) if model=="Opus 4.7" else best_dir(GLOB[model],c)
        F[model][c]=metrics(rows)
def band(v): return "na" if v is None else ("g" if v<=50 else ("y" if v<=70 else "r"))
def hcell(m,c):
    x=F.get(m,{}).get(c)
    if not x: return '<td class="na">&mdash;</td>'
    nt=f'<span class="n"> n{x["n"]}</span>' if x["n"]<100 else ''
    return f'<td class="{band(x["avgmae"])}">{x["avgmae"]}{nt}</td>'
hrows="".join("<tr><td class=l>"+lab+"</td>"+"".join(hcell(m,c) for m in models)+"</tr>" for lab,c,_ in FOCUS)
def permodel(model):
    pres=[(lab,c) for lab,c,_ in FOCUS if F.get(model,{}).get(c)]
    if not pres: return ""
    head="".join(f"<th>{LBL[m]}</th>" for m in M); body=""
    for lab,c in pres:
        x=F[model][c]; n=f" <span class=n>n{x['n']}</span>" if x['n']<100 else ""
        cells="".join(f"<td>{x['macros'][m]['mae']}<span class=pct><br>{x['macros'][m]['rel']}% &middot; {x['macros'][m]['med']}%</span></td>" for m in M)
        body+=f"<tr><td class=l>{lab}{n}</td>{cells}<td class=avg>{x['avgmae']}<span class=pct><br>{x['avgrel']}% &middot; {x['avgmed']}%</span></td></tr>"
    return f"<h3>{model}</h3><table><thead><tr><th>option</th>{head}<th>Avg</th></tr></thead><tbody>{body}</tbody></table>"
permodel_html="".join(permodel(m) for m in models)
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
td.g{color:var(--g);font-weight:600}td.y{color:var(--y)}td.r{color:var(--r);font-weight:600}td.na{color:#3a4150}
.pct{color:var(--mut);font-size:11px}.n{color:var(--mut);font-size:10px}.avg{color:#cdd6ea;font-weight:600}
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
 "<h2>Metrics</h2><dl class=glossary><dt>MAE &mdash; Mean Absolute Error</dt><dd>Average |prediction &minus; ground&nbsp;truth| in native units (kcal / grams). Primary, most interpretable.</dd>",
 "<dt>RelErr &mdash; Relative Error (MAPE)</dt><dd>mean(|pred &minus; truth| / truth) &times; 100%. Scale-free but inflated by tiny denominators.</dd>",
 "<dt>MedPE &mdash; Median Percent Error</dt><dd>median(|pred &minus; truth| / truth) &times; 100%. Robust counterpart &mdash; 'half of dishes within X%'. Best single UX number.</dd>",
 "<dt>Avg*</dt><dd>Averaged across the five nutrients (calories, mass, fat, carbs, protein).</dd></dl>",
 f"<div class=key><b><a href='{WANG}'>Wang et&nbsp;al. 2026</a> baseline</b> (Gemini 2.5 Flash, n=3466): image-only <b>AvgMAE 45.55 / RelErr 161%</b>; image+ingredients <b>44.12 / 139%</b>. Per-macro cells read <code>MAE / RelErr% &middot; MedPE%</code>.</div>",
 "<h2>What each option is</h2><table><thead><tr><th>option</th><th>input</th><th style='text-align:left'>description</th></tr></thead><tbody>",
 "".join(f"<tr><td class=l>{lab}</td><td>{'text only' if 'Text' in lab else 'photo'}</td><td style='text-align:left'>{d}</td></tr>" for lab,c,d in FOCUS),"</tbody></table>",
 "<h3>Prompts</h3><table><thead><tr><th>prompt</th><th>used by</th><th>view</th></tr></thead><tbody>"
 "<tr><td class=l>Generic baseline <span class=or>(our Wang reconstruction)</span></td><td>Baseline &middot; + GT ingredients</td><td><a href='#bp'>expand &darr;</a></td></tr>"
 f"<tr><td class=l>MacroShot System Prompt &mdash; photo</td><td>MacroShot &middot; + user caption</td><td><a href='{GH}#L72'>GitHub &nearr;</a></td></tr>"
 f"<tr><td class=l>MacroShot System Prompt &mdash; text-only</td><td>Text-only &middot; terse + detailed</td><td><a href='{GH}#L165'>GitHub &nearr;</a></td></tr></tbody></table>",
 f"<details id=bp><summary>Generic baseline prompt</summary><pre>{esc(WANGP)}\n\n[+ GT ingredients prepends: \"Ingredients on this plate: &lt;names&gt;.\"]</pre></details>",
 "<h2>How the user captions were generated</h2>",
 "<p class=sub>Each caption was generated by <b>Gemini</b> from the dish&rsquo;s <b>ground-truth ingredient list</b>: a casual log entry, <b>no exact grams/macros leaked</b>, literal, sub-1&nbsp;g seasonings skipped. The generator <b>does see each ingredient&rsquo;s gram weight</b> and uses it for the <b>detailed</b> caption&rsquo;s vague portion cues ('a good portion') &mdash; never a number &mdash; so 'detailed' carries a mild GT-derived portion hint 'terse' does not. Verified faithful (&asymp;76% coverage, ~0 hallucinations).</p>",
 f"<div class=ex><div class=lab>Ground-truth ingredients (input to Gemini)</div><div class=v>{esc(EX['gt'])}</div><div class=lab>&rarr; Terse caption</div><div class=v>&ldquo;{esc(EX['terse'])}&rdquo;</div><div class=lab>&rarr; Detailed caption</div><div class=v>&ldquo;{esc(EX['detailed'])}&rdquo;</div></div>",
 "<div class=key><b>Same caption, with vs without the photo.</b> The identical <b>terse</b> caption feeds BOTH <b>MacroShot + user caption (terse)</b> (photo prompt + image + caption) and <b>Text-only (terse)</b> (text prompt + caption, no image) &mdash; so comparing them isolates what the <b>photo</b> adds, holding the user&rsquo;s words constant.</div>",
 "<h2>Results &mdash; headline (AvgMAE)</h2>",
 "<table><thead><tr><th>option</th><th>Flash-Lite<span class=n> shipped</span></th><th>Flash-full</th><th>Opus 4.7</th><th>Opus 4.8</th></tr></thead><tbody>"+hrows+"</tbody></table>",
 "<p class=sub>Color: AvgMAE green &le;50 &middot; yellow &le;70 &middot; red &gt;70. <code>nNN</code> = sample &lt;100. Blank = not run on that model.</p>",
 "<h2>Results &mdash; per-macro detail</h2><p class=sub>Each cell: <b>MAE</b> with <span class=pct>RelErr% &middot; MedPE%</span> beneath. AvgMAE over five nutrients over-weights Mass; for a nutrition app, Calories / Protein / Fat matter most.</p>",
 permodel_html,
 f"<div class=foot><b>Method:</b> Nutrition5K (<a href='{N5K}'>Thames et&nbsp;al. 2021</a>) camera-C frame 10, n=100 stratified (seed 42). Frontier-model runs use one isolated, ground-truth-free sub-agent per dish. Baseline = our reconstruction of <a href='{WANG}'>Wang et&nbsp;al. 2026</a>&rsquo;s prompt. <b>Caveats:</b> cafeteria/single-cuisine heavy; RelErr noisy (prefer MAE / MedPE); Flash-full runs with chain-of-thought on; some Opus-4.8 cells may be small-n previews.</div>",
 "</div></body></html>"]
open("runs/FINAL_RESULTS.html","w").write("\n".join(H))
print("regenerated runs/FINAL_RESULTS.html — discovered cells:")
for m in models:
    for lab,c,_ in FOCUS:
        x=F[m].get(c)
        if x: print(f"  {m:<12} {lab:<34} AvgMAE={x['avgmae']} n={x['n']}")
