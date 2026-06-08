#!/usr/bin/env python3
"""Build data/prompts.json for the selected dish set, from the Nutrition5K CSVs.

Dishes already present in the current prompts.json are carried over verbatim
(preserves their captions and the exact ground truth that prior runs were
scored against). New dishes are reconstructed from the metadata CSV:
totals, per-ingredient breakdown, and ingredient_list_paper1_format (the unique
ingredient names, the only ingredient field any eval condition consumes). New
dishes get empty text_descriptions to be filled by a caption-generation pass.

Usage:  python3 tools/build_prompts.py 500
"""
import csv,json,os,sys
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
N=int(sys.argv[1]) if len(sys.argv)>1 else 500
CSV_DIR="/home/anirudh/Nutrition5k/sample/metadata"; MAC=["calories","mass_g","fat_g","carb_g","protein_g"]
def parse_csv():
    out={}
    for cafe,fn in [("cafe1","dish_metadata_cafe1.csv"),("cafe2","dish_metadata_cafe2.csv")]:
        for line in open(os.path.join(CSV_DIR,fn)):
            f=line.rstrip("\n").split(",")
            if len(f)<6 or not f[0].startswith("dish_"): continue
            try: totals={MAC[i]:float(f[1+i]) for i in range(5)}
            except: continue
            ings=[]; j=6
            while j+6<len(f):
                try:
                    ings.append({"id":f[j],"name":f[j+1].strip(),"grams":float(f[j+2]),
                                 "calories":float(f[j+3]),"fat_g":float(f[j+4]),
                                 "carb_g":float(f[j+5]),"protein_g":float(f[j+6])})
                except: break
                j+=7
            out[f[0]]={"cafe":cafe,"totals":totals,"ingredients":ings}
    return out
RAW=parse_csv()
existing={x["dish_id"]:x for x in json.load(open("data/prompts.json"))}
sel=[l.strip() for l in open(f"data/selected_{N}.txt") if l.strip()]
out=[]; carried=new=no_cap=0
for d in sel:
    if d in existing:
        out.append(existing[d]); carried+=1; continue
    r=RAW.get(d)
    if not r: print("  WARN no metadata for",d); continue
    names=list(dict.fromkeys(i["name"] for i in r["ingredients"] if i["name"]))
    out.append({"dish_id":d,"cafe":r["cafe"],"image_view_c":f"data/images/{d}_view_c.jpg",
        "ground_truth":{"totals":r["totals"],"ingredients":r["ingredients"]},
        "ingredient_list_paper1_format":"; ".join(names),
        "real_ingredient_list":"; ".join(names),
        "text_descriptions":{"terse":"","detailed":""}}); new+=1; no_cap+=1
if not os.path.exists("data/prompts_100.json"):
    json.dump(list(existing.values()),open("data/prompts_100.json","w"))  # backup
json.dump(out,open("data/prompts.json","w"))
print(f"wrote data/prompts.json: {len(out)} dishes  (carried {carried}, new {new})")
print(f"new dishes missing captions: {no_cap}  (text_descriptions empty until caption-gen pass)")
