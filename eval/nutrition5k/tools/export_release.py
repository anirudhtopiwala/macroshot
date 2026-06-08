#!/usr/bin/env python3
"""Pack the eval results into a clean, navigable release bundle + zip."""
import json,os,glob,csv,shutil,statistics as s,sys
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
sys.path.insert(0,os.path.dirname(os.path.dirname(HERE)))  # for src.gemini
M=["calories","mass_g","fat_g","carb_g","protein_g"]; LBL={"calories":"Calories","mass_g":"Mass","fat_g":"Fat","carb_g":"Carbs","protein_g":"Protein"}
P=json.load(open('data/prompts.json')); ds=P['dishes'] if isinstance(P,dict) and 'dishes' in P else P
Dm=ds if isinstance(ds,dict) else {x['dish_id']:x for x in ds}
GT={d:Dm[d]['ground_truth']['totals'] for d in Dm}
meta={x['dish_id']:x for x in json.load(open('data/sample100_meta.json'))}
O47="runs/claude-opus-4-7-subagent_n100_20260527_060817/per_dish.csv"
GLOB={"Flash-Lite":"runs/gemini-2.5-flash-lite_*","Flash-full":"runs/gemini-2.5-flash_2*","Opus 4.8":"runs/opus-4-8-*"}
FOCUS=[("Baseline","BASELINE_unlabeled"),("Baseline + GT ingredients","BASELINE_labeled"),("MacroShot","X1"),
 ("MacroShot + user caption (terse)","X3v2"),("Text-only (terse)","E_terse"),("Text-only (detailed)","E_detailed")]
models=["Flash-Lite","Flash-full","Opus 4.7","Opus 4.8"]
def rows_dir(run,cond):
    o=[]
    for f in glob.glob(f"{run}/predictions/{cond}/*.json"):
        try:d=json.load(open(f))
        except:continue
        did=os.path.basename(f)[:-5]; p=d.get('parsed') if isinstance(d.get('parsed'),dict) else d
        if did in GT and all(isinstance(p.get(m),(int,float)) for m in M): o.append((did,p))
    return o
def best(globpat,cond):
    bb=[]
    for r in glob.glob(globpat):
        rr=rows_dir(r,cond)
        if len(rr)>len(bb): bb=rr
    return bb
def rows_csv(cond):
    o=[]
    for r in csv.DictReader(open(O47)):
        if r["condition"]==cond: o.append((r["dish_id"],{m:float(r[f"pred_{m}"]) for m in M}))
    return o
OUT="release/macroshot-meal-eval"; 
if os.path.exists("release"): shutil.rmtree("release")
os.makedirs(OUT+"/prompts")
# gather predictions per (model,option)
allpred={}
for model in models:
    for lab,c in FOCUS:
        allpred[(model,lab)] = rows_csv(c) if model=="Opus 4.7" else best(GLOB[model],c)
# metrics_summary.csv (tidy)
with open(OUT+"/metrics_summary.csv","w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["model","option","scope","MAE","RelErr_pct","MedPE_pct","n"])
    for model in models:
        for lab,c in FOCUS:
            rows=allpred[(model,lab)]
            if not rows: continue
            for m in M:
                ae=[abs(p[m]-GT[d][m]) for d,p in rows]; pe=[abs(p[m]-GT[d][m])/GT[d][m] for d,p in rows if GT[d][m]]
                w.writerow([model,lab,LBL[m],round(s.mean(ae),2),round(s.mean(pe)*100) if pe else "",round(s.median(pe)*100) if pe else "",len(rows)])
            # average row
            mae=s.mean(s.mean(abs(p[m]-GT[d][m]) for m in M) for d,p in rows)
            rel=s.mean(s.mean(abs(p[m]-GT[d][m])/GT[d][m] for m in M if GT[d][m])*100 for d,p in rows)
            med=s.mean([s.median([abs(p[m]-GT[d][m])/GT[d][m] for d,p in rows if GT[d][m]])*100 for m in M])
            w.writerow([model,lab,"Average (5 nutrients)",round(mae,2),round(rel),round(med),len(rows)])
# per_dish_predictions.csv
with open(OUT+"/per_dish_predictions.csv","w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["model","option","dish_id"]+[f"pred_{m}" for m in M]+[f"gt_{m}" for m in M]+[f"abserr_{m}" for m in M])
    for model in models:
        for lab,c in FOCUS:
            for did,p in allpred[(model,lab)]:
                g=GT[did]; w.writerow([model,lab,did]+[round(p[m],2) for m in M]+[round(g[m],2) for m in M]+[round(abs(p[m]-g[m]),2) for m in M])
# ground_truth.csv
with open(OUT+"/ground_truth.csv","w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["dish_id","cafe","n_ingredients","difficulty"]+M)
    sel=[l.strip() for l in open('data/selected_100.txt') if l.strip()]
    for d in sel:
        n=meta.get(d,{}).get("n_real_ingr",""); diff="simple" if isinstance(n,int) and n<=5 else ("medium" if isinstance(n,int) and n<=9 else "complex")
        w.writerow([d,meta.get(d,{}).get("cafe",""),n,diff]+[round(GT[d][m],2) for m in M])
# captions.csv
with open(OUT+"/captions.csv","w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["dish_id","terse","detailed"])
    for d in [l.strip() for l in open('data/selected_100.txt') if l.strip()]:
        td=Dm[d]["text_descriptions"]; w.writerow([d,td["terse"],td["detailed"]])
# prompts
from src.gemini import CONVERSATIONAL_INITIAL_PROMPT as CONV, TEXT_ONLY_INITIAL_PROMPT as TXT
from eval.nutrition5k.src.conditions import WANG2026_USER_BASE as WANG
open(OUT+"/prompts/baseline.txt","w").write("Generic baseline prompt (our reconstruction of Wang et al. 2026; sent as the user message, no system prompt).\nThe 'Baseline + GT ingredients' option prepends: \"Ingredients on this plate: <names>.\"\n\n---\n\n"+WANG)
open(OUT+"/prompts/macroshot_photo.txt","w").write(CONV)
open(OUT+"/prompts/macroshot_text_only.txt","w").write(TXT)
try:
    from eval.nutrition5k.tools.generate_text_descriptions import _META_PROMPT
    open(OUT+"/prompts/caption_generation.txt","w").write(_META_PROMPT)
except Exception as e:
    open(OUT+"/prompts/caption_generation.txt","w").write("Captions were generated by Gemini from each dish's ground-truth ingredient list (names + grams), with rules: casual log entry, no exact grams/macros leaked, literal, skip <1g seasonings. Terse ~10-15 words (no portions); detailed ~25-35 words with vague portion cues derived from the grams.")
# copy dashboard as index.html
shutil.copy("runs/FINAL_RESULTS.html", OUT+"/index.html")
print("bundle built at",OUT)
