"""Parse Gemini responses into a unified prediction dict.

Two formats appear in our eval:
  1. Macroshot's nested schema (the macroshot_* conditions all use it because
     they import CONVERSATIONAL_INITIAL_PROMPT / TEXT_ONLY_INITIAL_PROMPT,
     both of which specify the same JSON shape with items[] + totals).
  2. Loose / unstructured responses — fallback in case the model deviates.

Unified output:
  {"calories": float, "mass_g": float, "fat_g": float,
   "carb_g": float, "protein_g": float}

Returns None for any field that can't be extracted.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _extract_json(text: str) -> dict | None:
    """Find the largest top-level JSON object in `text`."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Greedy match for embedded JSON object
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


# For calories/fat/carb/protein, taking the LAST match works because in both
# Macroshot's and Wang et al. 2026's schemas the top-level total appears
# AFTER any nested item-level values.
_NUM_PATTERNS = {
    "calories":  re.compile(r'"calories"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
    "fat_g":     re.compile(r'"fat(?:_g)?"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
    "carb_g":    re.compile(r'"carbs?(?:_g)?"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
    "protein_g": re.compile(r'"protein(?:_g)?"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
}

# Mass handling is special: Macroshot's schema has NO top-level mass field —
# total mass = sum(items[].weight_g). Wang et al. 2026's schema has a single
# top-level mass_g. So we try explicit top-level keys first; only if none
# found do we fall back to summing weight_g matches (assumed to be per-item).
_TOPLEVEL_MASS_KEYS = re.compile(
    r'"(?:mass_g|mass|portion_g|portion|total_weight)"\s*:\s*([-+]?\d+(?:\.\d+)?)'
)
_WEIGHT_G_KEYS = re.compile(r'"weight_g"\s*:\s*([-+]?\d+(?:\.\d+)?)')


def _regex_fallback(text: str) -> dict[str, float | None]:
    """Last-resort field extraction for malformed JSON."""
    out: dict[str, float | None] = {}
    for key, pat in _NUM_PATTERNS.items():
        matches = pat.findall(text)
        out[key] = float(matches[-1]) if matches else None

    mass_matches = _TOPLEVEL_MASS_KEYS.findall(text)
    if mass_matches:
        out["mass_g"] = float(mass_matches[-1])
    else:
        weights = _WEIGHT_G_KEYS.findall(text)
        out["mass_g"] = sum(float(w) for w in weights) if weights else None

    return out


def _num(x: Any) -> float | None:
    if isinstance(x, (int, float)):
        return float(x)
    return None


def parse(text: str) -> dict[str, float | None]:
    """Extract macro totals from a model response."""
    out: dict[str, float | None] = {
        "calories": None, "mass_g": None, "fat_g": None,
        "carb_g": None, "protein_g": None,
    }
    obj = _extract_json(text)
    if obj is None:
        # Malformed JSON — fall back to regex extraction of top-level totals.
        return _regex_fallback(text)

    # Pull top-level totals. Accept both Macroshot's ("fat", "carbs",
    # "protein") and Wang 2026-style ("fat_g", "carb_g", "protein_g") keys.
    def _first(keys: tuple[str, ...]) -> float | None:
        for k in keys:
            v = _num(obj.get(k))
            if v is not None:
                return v
        return None

    out["calories"]  = _first(("calories",))
    out["fat_g"]     = _first(("fat_g", "fat"))
    out["carb_g"]    = _first(("carb_g", "carbs", "carb"))
    out["protein_g"] = _first(("protein_g", "protein"))

    items = obj.get("items")
    if isinstance(items, list):
        mass = 0.0
        any_weight = False
        for it in items:
            if isinstance(it, dict):
                w = _num(it.get("weight_g"))
                if w is not None:
                    mass += w
                    any_weight = True
        if any_weight:
            out["mass_g"] = mass

    # Fallback: paper2-style flat schema with explicit mass/portion fields.
    if out["mass_g"] is None:
        for key in ("mass_g", "mass", "portion_g", "portion", "total_weight", "weight_g"):
            v = _num(obj.get(key))
            if v is not None:
                out["mass_g"] = v
                break

    return out
