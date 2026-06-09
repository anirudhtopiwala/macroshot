"""Apply 180° rotation to listed images.

Use this in tandem with the visual orientation review pass - list inverted
dish IDs (one per line) in a file and run:

    .venv/bin/python -m eval.nutrition5k.tools.rotate_images --from-file inverted_ids.txt

Or pass IDs directly:

    .venv/bin/python -m eval.nutrition5k.tools.rotate_images dish_1550874191 dish_1551324010

NOT idempotent: running twice on the same dish double-rotates back to the
original. Re-extract from camera_C.h264 if you need to undo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

_BENCH = Path(__file__).resolve().parents[1]
_IMG_DIR = _BENCH / "data" / "images"


def rotate_one(dish_id: str) -> None:
    p = _IMG_DIR / f"{dish_id}_view_c.jpg"
    if not p.exists():
        print(f"  {dish_id}  MISSING ({p})", file=sys.stderr)
        return
    img = Image.open(p)
    img.rotate(180, expand=False).save(p, "JPEG", quality=90)
    print(f"  {dish_id}  rotated 180°")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dish_ids", nargs="*", help="Dish IDs to rotate")
    parser.add_argument("--from-file", help="Path to file with one dish_id per line")
    args = parser.parse_args()

    ids: list[str] = list(args.dish_ids)
    if args.from_file:
        ids.extend(line.strip() for line in open(args.from_file)
                   if line.strip() and not line.startswith("#"))
    if not ids:
        print("No dish IDs provided", file=sys.stderr)
        return 1
    for d in ids:
        rotate_one(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
