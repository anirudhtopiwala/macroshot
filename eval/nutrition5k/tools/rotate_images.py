"""Rotate flagged upside-down View C images 180 deg, idempotently.

Camera-C frames are captured upside-down for a large fraction of dishes. After a
visual orientation review, list the inverted dish IDs (one per line) and run:

    .venv/bin/python -m eval.nutrition5k.tools.rotate_images --from-file inverted_ids.txt

Or pass IDs directly:

    .venv/bin/python -m eval.nutrition5k.tools.rotate_images dish_1550874191 dish_1551324010

A log (data/rotated_ids.txt) guards against double-rotation: an ID already in the
log is skipped, so re-listing it across overlapping review batches is safe.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image

_BENCH = Path(__file__).resolve().parents[1]
_IMG = _BENCH / "data" / "images"
_LOG = _BENCH / "data" / "rotated_ids.txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dish_ids", nargs="*", help="Dish IDs (or image paths) to rotate")
    ap.add_argument("--from-file", help="File with one dish_id per line (# comments ok)")
    args = ap.parse_args()

    ids = list(args.dish_ids)
    if args.from_file:
        ids += [l.strip() for l in open(args.from_file) if l.strip() and not l.startswith("#")]
    # accept bare ids or full image paths
    ids = [os.path.basename(x).replace("_view_c.jpg", "").replace(".jpg", "") for x in ids]
    ids = list(dict.fromkeys(ids))
    if not ids:
        print("No dish IDs provided", file=sys.stderr)
        return 1

    already = set(l.strip() for l in open(_LOG)) if _LOG.exists() else set()
    rot, skip, miss = [], [], []
    for d in ids:
        if d in already:
            skip.append(d); continue
        p = _IMG / f"{d}_view_c.jpg"
        if not p.exists():
            miss.append(d); continue
        Image.open(p).rotate(180, expand=False).save(p, "JPEG", quality=90)
        rot.append(d)
    with open(_LOG, "a") as fh:
        for d in rot:
            fh.write(d + "\n")
    print(f"input {len(ids)} | rotated {len(rot)} | already-done {len(skip)} | missing {len(miss)}")
    if miss:
        print("  MISSING (check id):", miss[:20], "..." if len(miss) > 20 else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
