#!/usr/bin/env python3
"""Rotate flagged upside-down images 180 deg, idempotently.

Camera-C frames are captured upside-down for a large fraction of dishes. During
manual orientation review, flagged dish ids are rotated here. A log
(data/rotated_ids.txt) guards against double-rotation: an id already in the log
is skipped, so re-listing it (across overlapping review batches) is safe.

Usage:
  python3 tools/rotate_flagged.py --from-file ids.txt
  python3 tools/rotate_flagged.py dish_123 dish_456
"""
import os,sys,argparse
from PIL import Image
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
IMG="data/images/{d}_view_c.jpg"; LOG="data/rotated_ids.txt"
ap=argparse.ArgumentParser(); ap.add_argument("ids",nargs="*"); ap.add_argument("--from-file")
a=ap.parse_args()
ids=list(a.ids)
if a.from_file: ids+=[l.strip() for l in open(a.from_file) if l.strip()]
# accept full paths or bare ids
ids=[os.path.basename(x).replace("_view_c.jpg","").replace(".jpg","") for x in ids]
ids=list(dict.fromkeys(ids))
already=set(l.strip() for l in open(LOG)) if os.path.exists(LOG) else set()
rot=[]; skip=[]; miss=[]
for d in ids:
    if d in already: skip.append(d); continue
    p=IMG.format(d=d)
    if not os.path.exists(p): miss.append(d); continue
    Image.open(p).rotate(180,expand=False).save(p,"JPEG",quality=90); rot.append(d)
with open(LOG,"a") as fh:
    for d in rot: fh.write(d+"\n")
print(f"input {len(ids)} | rotated {len(rot)} | already-done {len(skip)} | missing {len(miss)}")
if miss: print("  MISSING (check id):", miss)
