#!/usr/bin/env python3
"""Stratified dish selection for the Nutrition5K macro eval.

Pool = dishes that have a camera-C video (data/has_camera_c_dishes.txt) AND a
metadata row with valid totals. Stratified by complexity (real-ingredient
count): simple <=5, medium 6-9, complex >=10, in a 40/35/25 ratio. Seed 42, so
the selection is deterministic.

The committed data/selected_500.txt is the canonical benchmark set; re-running
this reproduces a comparable stratified sample (it will not byte-match the
committed file, which was grown incrementally - use the committed file to score).

Writes data/selected_<N>.txt and data/selected_<N>_meta.json.
Usage:  python3 tools/select_dishes.py 500
"""
import csv,json,os,sys,random
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
N=int(sys.argv[1]) if len(sys.argv)>1 else 500
RATIO=(0.40,0.35,0.25)  # simple / medium / complex
CSV_DIR=os.environ.get("NUTRITION5K_METADATA_DIR","data/metadata")  # dish_metadata_cafe{1,2}.csv from the public Nutrition5K release
MAC=["calories","mass_g","fat_g","carb_g","protein_g"]
def parse_meta():
    """dish_id -> {cafe, totals, ingredients(real, deduped), n_real_ingr}"""
    out={}
    for cafe,fn in [("cafe1","dish_metadata_cafe1.csv"),("cafe2","dish_metadata_cafe2.csv")]:
        for line in open(os.path.join(CSV_DIR,fn)):
            f=line.rstrip("\n").split(",")
            if len(f)<6 or not f[0].startswith("dish_"): continue
            try: totals={MAC[i]:float(f[1+i]) for i in range(5)}
            except: continue
            ings=[]
            j=6
            while j+6<len(f):
                try: nm=f[j+1].strip()
                except: break
                if nm: ings.append(nm)
                j+=7
            real=list(dict.fromkeys(ings))  # unique names, preserve order
            out[f[0]]={"cafe":cafe,"totals":totals,"ingredients":real,"n_real_ingr":len(real)}
    return out
def bin_of(n): return 0 if n<=5 else (1 if n<=9 else 2)
META=parse_meta()
cam=set(l.strip() for l in open("data/has_camera_c_dishes.txt") if l.strip())
pool=[d for d in META if d in cam and META[d]["totals"]["mass_g"]>0 and META[d]["totals"]["calories"]>0]
bins={0:[],1:[],2:[]}
for d in pool: bins[bin_of(META[d]["n_real_ingr"])].append(d)
target=[round(N*r) for r in RATIO]; target[1]=N-target[0]-target[2]  # exact sum
rng=random.Random(42)
chosen=[]
for b in (0,1,2):
    cand=list(bins[b]); rng.shuffle(cand)
    chosen+=cand[:target[b]]
chosen=list(dict.fromkeys(chosen))[:N]
random.Random(42).shuffle(chosen)
got={0:0,1:0,2:0}
for d in chosen: got[bin_of(META[d]["n_real_ingr"])]+=1
open(f"data/selected_{N}.txt","w").write("\n".join(chosen)+"\n")
json.dump({d:META[d] for d in chosen}, open(f"data/selected_{N}_meta.json","w"))
print(f"pool={len(pool)} chosen={len(chosen)}")
print(f"bins simple/medium/complex: got {got[0]}/{got[1]}/{got[2]}  target {target[0]}/{target[1]}/{target[2]}")
