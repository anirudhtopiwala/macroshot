"""Generate natural-language meal descriptions for the text-only E conditions.

Usage:
  .venv/bin/python -m eval.nutrition5k.tools.generate_text_descriptions \
      --start 0 --count 10

Produces two descriptions per dish:
  terse:    ~10-15 words, casual ("had X with Y")
  detailed: ~25-35 words, casual but with rough portion cues
            ("had a palm-sized portion of X, a side of Y")

No exact grams or macro numbers leak - these descriptions simulate user
typed input.

Skips ingredients <1 g (seasonings the user wouldn't mention) and the
"deprecated" placeholder. Reads dishes from data/prompts.json and fills in
captions for any whose text_descriptions are still empty.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parents[1]
_MACROSHOT = _BENCH.parents[1]
if str(_MACROSHOT) not in sys.path:
    sys.path.insert(0, str(_MACROSHOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_MACROSHOT / ".env")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402


_META_PROMPT = """You write meal-log entries for a nutrition-tracking app, simulating what a real user would type after eating.

Given a list of ingredients with their gram amounts, write TWO descriptions of the meal:
  - "terse": ~10-15 words, casual, e.g. "had chicken with rice and broccoli"
  - "detailed": ~25-35 words, casual but with rough portion cues, e.g. "lunch was a palm-sized portion of grilled chicken, about a cup of rice, and a side of broccoli"

Rules:
- DO NOT include exact grams, calories, or macro numbers.
- DO NOT include ingredients under 1 gram (seasonings the user wouldn't mention).
- DO NOT include any ingredient named "deprecated" - it's a placeholder.
- Use casual food-logging phrasing ("had ...", "lunch was ...", "grabbed ...").
- Portion cues should be vague: "a small bowl", "a few strips", "a side of", "about a cup", "a palm-sized portion", "a handful".

PRECISION (important):
- BE LITERAL. Name the ingredients the user has, do not combine them into compound dishes or invent preparation styles that aren't stated.
  - BAD: "creamy lime dressing" (invented from sour cream + lime + olive oil)
  - GOOD: "with a bit of sour cream, lime, and olive oil"
  - BAD: "pan-fried tofu" if cooking method isn't stated
  - GOOD: "some tofu"
- It's fine to use a single grouping word like "salad" or "mixed greens" if the GT names already imply one (e.g., "mixed greens", "caesar salad" are GT items themselves). Otherwise list ingredients directly.
- If 3+ vegetables appear together with no explicit GT salad name, you may say "a mix of [X, Y, Z]" but do not invent a name like "mediterranean bowl" or "rice pilaf".
- Do not infer cooking methods (grilled / roasted / pan-fried / sauteed) unless the GT name contains it (e.g., "grilled chicken", "fried rice", "roasted potatoes" are already-cooked GT names - keep those literal).

Return JSON only: {"terse": "...", "detailed": "..."}
"""


def _ingr_line(ingredients: list[dict]) -> str:
    parts = []
    for i in ingredients:
        if i["name"] == "deprecated" or i["grams"] < 1.0:
            continue
        parts.append(f"{i['name']} ({i['grams']:.0f}g)")
    return ", ".join(parts)


def generate_for_dish(client: genai.Client, ingredients: list[dict]) -> dict:
    user_line = f"Ingredients: {_ingr_line(ingredients)}"
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_line,
        config=types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=4096,
            response_mime_type="application/json",
            system_instruction=_META_PROMPT,
        ),
    )
    return json.loads(resp.text)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=int, default=0,
                   help="0-indexed start position in prompts.json")
    p.add_argument("--count", type=int, default=10,
                   help="Number of dishes to process")
    args = p.parse_args()

    prompts_path = _BENCH / "data" / "prompts.json"
    dishes = json.loads(prompts_path.read_text())
    chunk = dishes[args.start:args.start + args.count]
    print(f"Scanning {len(chunk)} dishes for missing captions (offset {args.start})")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    filled = 0
    for s in chunk:
        if s.get("text_descriptions", {}).get("terse"):
            continue  # already has a caption
        try:
            desc = generate_for_dish(client, s["ground_truth"]["ingredients"])
            s["text_descriptions"] = desc
            filled += 1
            print(f"  {s['dish_id']}  terse: {desc['terse'][:60]}...")
        except Exception as e:
            print(f"  {s['dish_id']}  ERROR: {e}", file=sys.stderr)

    prompts_path.write_text(json.dumps(dishes, indent=2))
    print(f"\nFilled {filled} captions; wrote {len(dishes)} dishes to {prompts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
