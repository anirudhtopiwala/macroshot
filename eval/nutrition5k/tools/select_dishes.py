#!/usr/bin/env python3
"""Stratified dish selection for the Nutrition5K macro eval.

Pool = dishes that have a camera-C video (data/has_camera_c_dishes.txt) AND a
metadata row with valid totals. Stratified by complexity (real-ingredient
count): simple <=5, medium 6-9, complex >=10, in the same 40/35/25 ratio as the
original n=100. The existing selected_100 / selected_300 are forced into the
set so prior results stay valid and the sample only grows. Seed 42.

Writes data/selected_<N>.txt and data/selected_<N>_meta.json.
Usage:  python3 tools/select_dishes.py 500
"""
import csv,json,os,sys,random
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
N=int(sys.argv[1]) if len(sys.argv)>1 else 500
RATIO=(0.40,0.35,0.25)  # simple / medium / complex, from the original n=100
CSV_DIR=os.environ.get("NUTRITION5K_METADATA_DIR","data/metadata")  # dish_metadata_cafe{1,2}.csv from the public Nutrition5K release
MAC=["calories","mass_g","fat_g","carb_g","protein_g"]
def parse_meta():
    """dish_id -> {cafe, totals, ingredients(real, deduped, >=1g), n_real_ingr}"""
    out={}
    for cafe,fn in [("cafe1","dish_metadata_cafe1.csv"),("cafe2","dish_metadata_cafe2.csv")]:
        for line in open(os.path.join(CSV_DIR,fn)):
            f=line.rstrip("\n").split(",")
            if len(f)<6 or not f[0].startswith("dish_"): continue
            try: totals={MAC[i]:float(f[1+i]) for i in range(5)}
            except: continue
            ings=[]  # (name, grams)
            j=6
            while j+6<len(f):
                try: nm=f[j+1].strip(); g=float(f[j+2])
                except: break
                if nm: ings.append((nm,g))
                j+=7
            seen=set(); real=[]
            for nm,g in ings:
                if nm not in seen: seen.add(nm); real.append(nm)  # all unique names (matches original)
            out[f[0]]={"cafe":cafe,"totals":totals,"ingredients":real,"n_real_ingr":len(real)}
    return out
def bin_of(n): return 0 if n<=5 else (1 if n<=9 else 2)
META=parse_meta()
cam=set(l.strip() for l in open("data/has_camera_c_dishes.txt") if l.strip())
pool=[d for d in META if d in cam and META[d]["totals"]["mass_g"]>0 and META[d]["totals"]["calories"]>0]
# validate parsing rule against the existing 300-meta (n_real_ingr agreement)
if os.path.exists("data/selected_300_meta.json"):
    old=json.load(open("data/selected_300_meta.json")); agree=tot=0
    for d,v in old.items():
        if d in META: tot+=1; agree+= (META[d]["n_real_ingr"]==v["n_real_ingr"])
    print(f"[validate] n_real_ingr matches existing 300-meta on {agree}/{tot} dishes")
forced=[]
for fn in ["data/selected_100.txt","data/selected_300.txt"]:
    if os.path.exists(fn): forced+=[l.strip() for l in open(fn) if l.strip()]
forced=[d for d in dict.fromkeys(forced) if d in META]  # dedupe, keep order, must have meta
bins={0:[],1:[],2:[]}
for d in pool: bins[bin_of(META[d]["n_real_ingr"])].append(d)
target=[round(N*r) for r in RATIO]; target[1]=N-target[0]-target[2]  # exact sum
rng=random.Random(42)
chosen=list(forced)
fset=set(forced); fbin={0:0,1:0,2:0}
for d in forced: fbin[bin_of(META[d]["n_real_ingr"])]+=1
for b in (0,1,2):
    need=target[b]-fbin[b]
    cand=[d for d in bins[b] if d not in fset]; rng.shuffle(cand)
    chosen+= cand[:max(0,need)]
chosen=list(dict.fromkeys(chosen))[:N]
random.Random(42).shuffle(chosen)
got={0:0,1:0,2:0}
for d in chosen: got[bin_of(META[d]["n_real_ingr"])]+=1
open(f"data/selected_{N}.txt","w").write("\n".join(chosen)+"\n")
json.dump({d:META[d] for d in chosen}, open(f"data/selected_{N}_meta.json","w"))
print(f"pool={len(pool)} forced={len(forced)} chosen={len(chosen)}")
print(f"bins simple/medium/complex: got {got[0]}/{got[1]}/{got[2]}  target {target[0]}/{target[1]}/{target[2]}")
miss100=[d for d in (l.strip() for l in open('data/selected_100.txt') if l.strip()) if d not in set(chosen)]
print(f"existing-100 preserved: {100-len(miss100)}/100")
