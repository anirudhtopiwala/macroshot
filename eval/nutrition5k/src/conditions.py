"""Experiment condition definitions.

Each condition is a recipe for building (system_prompt, user_text, image_path)
from a dish record in prompts.json.

System prompts are imported VERBATIM from macroshot.src.gemini so that this
eval tracks production prompts. Do not redefine them here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

# Add macroshot project root to sys.path so we can import src.gemini.
_MACROSHOT_ROOT = Path(__file__).resolve().parents[3]
if str(_MACROSHOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MACROSHOT_ROOT))

from src.gemini import (  # noqa: E402
    CONVERSATIONAL_INITIAL_PROMPT,
    TEXT_ONLY_INITIAL_PROMPT,
)


# Wang et al. 2026 (Curr. Res. Food Sci. 12:101405) doesn't publish prompts
# verbatim — Fig 3 is an image, the body text only says "the models were
# required to calculate the calories (kcal), total weight (g), fat content
# (g), carbohydrate content (g), and protein content (g) for each sample."
# (§2.2.2). This is the closest text reconstruction. We request JSON so the
# parser can extract numbers reliably.
WANG2026_USER_BASE = (
    "Calculate the total calories (kcal), total weight (g), fat content (g), "
    "carbohydrate content (g), and protein content (g) for the food in this "
    "image. Reply with JSON only: "
    "{\"calories\": <kcal>, \"mass_g\": <g>, \"fat_g\": <g>, "
    "\"carb_g\": <g>, \"protein_g\": <g>}."
)


# --- Experimental prompt addendum for X3v2 (eval-only; not in production yet) ---
# Hypothesis: X3 (image + casual caption) regresses vs X1 (image only) because
# the model treats each named ingredient as a STANDARD SERVING and sums them.
# Fix: tell it the caption is for IDENTITY only, and force a top-down
# whole-plate mass budget so portions stay anchored to what's visible.
X3V2_ADDENDUM = """

==============================================================
ACCOMPANYING USER TEXT — READ CAREFULLY
==============================================================
A short caption from the user accompanies this image. The caption tells you
WHAT is on the plate, NOT how much. Use it ONLY to identify and disambiguate
items you can already see (e.g. confirm a protein is salmon, a grain is wheat
berry, a dressing is caesar). It is an identity hint, never a quantity signal.

- NEVER size an item from its name. A named ingredient is NOT a standard
  serving. Every weight_g MUST come from the visual portion in the image.
- Items the user lists SHARE the food visible on the plate — they do not each
  add a full serving. A caption listing "chicken, beef, potatoes, broccoli,
  pizza" describes ONE normal plate divided among those items, not five full
  servings stacked together. The more items named, the SMALLER each one's
  share of the same plate — listing more food does not mean more total food.
- If the user names an item you cannot actually find in the image, give it a
  near-zero weight and add a clarifying question. Do NOT assume a default
  serving for something you can't see.

WHOLE-PLATE MASS BUDGET (do this BEFORE assigning per-item weights):
1. From the IMAGE ALONE, estimate the TOTAL mass of food physically on the
   plate/container. A single home or cafeteria plate typically holds ~250-500 g
   of food in total, even when it contains many different items.
2. Allocate that total across the identified items in proportion to the visual
   area/volume each one occupies.
3. The sum of all weight_g MUST stay within your total-plate estimate. If your
   per-item weights add up to more food than you can see on the plate, scale
   them ALL down until they fit. Do not let the count of named items inflate
   the total mass.
"""

# Build the X3v2 system prompt by appending the addendum to the production one.
CONVERSATIONAL_PROMPT_X3V2 = CONVERSATIONAL_INITIAL_PROMPT + X3V2_ADDENDUM


# --- X3v3: iteration 2 (eval-only) -------------------------------------------
# Synthesis of two critical reviews of the X3v2 results:
#   * Prompt review: cut the per-item COMMON FOOD WEIGHTS table (the serving-size
#     inflation source), move the whole-plate budget into the base PORTION step,
#     loosen the mass range, and add explicit-quantity handling.
#   * Data review: X3v2 still over-predicts (+107 kcal mean); the hard 250-500 g
#     cap caused 8 under-prediction regressions on large/dense plates; composite
#     "list-y" captions still inflate. Fixes: soft cap w/ large-plate escape
#     hatch, calorie-density tiers, a high-end calorie sanity check.
#
# We replace the production PORTION step (step 2) with a simpler, top-down block
# and append a caption/explicit-quantity addendum.

_NEW_PORTION_BLOCK = """2. PORTION (size from the IMAGE, never from an item's name):
   First estimate the WHOLE-PLATE mass, then divide it among items - do NOT size each item independently and sum.
   a. WHOLE-PLATE BUDGET: From the image alone, estimate the total mass of food physically present. Priors (a sanity guide, NOT a hard cap - judge from what you see):
        - light / small plate or side ~ 150-300 g
        - typical single plate or bowl ~ 300-450 g
        - heaped, overflowing, large or multi-component plate / big pasta-rice bowl / full tray ~ up to 650-900 g
      A plate holding many items is still ONE plate's worth of food - more items means each item's share is SMALLER, not more total food.
   b. ALLOCATE: split the total across items by the visual area/volume each occupies. Sides, garnishes, dressings, and "a bit of X" are often <30 g - shrink minor items rather than adding mass for each named one.
   c. CHECK: the sum of all weight_g must match the food visible on the plate. If your per-item weights add up to more than you can see, scale them ALL down.
   CALORIE DENSITY (pick from what you see, not from the item count): watery / veg / salad plates ~ 0.4-0.7 kcal/g; mixed plates ~ 0.8-1.3 kcal/g; fried / cheesy / oily / egg-and-meat plates ~ 1.4-2.0 kcal/g. Leafy greens and raw vegetables are low-mass and low-calorie - a long vegetable list should NOT pull calories up.
   SIZE REFERENCES (read volumes off the image): dinner plate ~ 25 cm; bowl ~ 14-16 cm / ~300 ml; fist ~ 1 cup ~ 150 g rice/pasta/veg; palm (no fingers) ~ 85 g cooked meat/fish; thumb tip ~ 1 tsp ~ 5 g oil/butter; cupped hand ~ 30 g nuts; tbsp ~ 15 g sauce. Unit objects for counting only: egg ~ 50 g; bread slice ~ 28 g; cheese slice ~ 22 g.
   When uncertain, prefer the LOWER end. Report specific numbers (e.g. 143 g, 87 g); do not round to multiples of 25/50 without reason.
"""

def _simplify_base(p: str) -> str:
    """Replace the verbose per-item PORTION step with the top-down block above."""
    start = p.index("2. PORTION")
    end = p.index("3. CALCULATE")
    return p[:start] + _NEW_PORTION_BLOCK + p[end:]

CONVERSATIONAL_BASE_V3 = _simplify_base(CONVERSATIONAL_INITIAL_PROMPT)

X3V3_ADDENDUM = """

==============================================================
ACCOMPANYING USER TEXT
==============================================================
A short caption accompanies this image. For each food the user names, decide whether they stated an AMOUNT.

A) NAMED, NO AMOUNT ("chicken", "had rice and broccoli"): the caption says WHAT is there, not how much. Use it only to identify/confirm items you can see. NEVER size these from the name - weight_g comes from the visual portion under the whole-plate budget (step 2). Listed items SHARE the plate; they do NOT each add a serving. Do not assume every named item is substantial - minor ones are often <30 g.

B) NAMED WITH AN EXPLICIT AMOUNT ("0.5 lb fish", "200g rice", "two eggs", "a cup of oats", "1 tbsp olive oil", "3 slices"): the stated amount is AUTHORITATIVE for that item. Convert to grams, set weight_g directly, do NOT re-size it from the image, and EXCLUDE it from the whole-plate budget (the budget and visual allocation cover only the unquantified items). Conversions: 1 oz ~ 28 g; 1 lb ~ 454 g (so 0.5 lb ~ 227 g); 1 cup ~ 240 ml liquid, dry varies (oats ~80 g, cooked rice ~160 g, flour ~120 g, berries ~150 g); 1 tbsp ~ 15 g; 1 tsp ~ 5 g; counts = per-unit weight x count (egg ~50 g, roti ~38 g, bread slice ~28 g). CONFLICT: if the stated amount clearly cannot match the image, still honor the user's number as the estimate, but add a short clarifying question noting the mismatch.

C) NAMED BUT NOT VISIBLE and no amount given: set weight near zero and add a clarifying question. Do not invent a default serving for something you can't see.

FINAL SANITY: most single plates here are ~150-450 kcal. If your total exceeds ~600 kcal, re-check that the image truly shows that much food / oil / cheese / meat - only large or very dense plates should exceed 600 kcal.
"""

CONVERSATIONAL_PROMPT_X3V3 = CONVERSATIONAL_BASE_V3 + X3V3_ADDENDUM


# --- X3v4: iteration 3 (eval-only) -------------------------------------------
# Ablation from the X3v3 run isolated the cause of its regression:
#   * Removing the per-item weight table HELPED photo-only (X1 65.3 -> 61.1) -> keep it.
#   * X3v3 still regressed vs X3v2 (56.6 -> 63.1) and over-predicted MORE
#     (+107 -> +128 signed). The only remaining difference was the loosened mass
#     ceiling (~900 g), which re-inflated the dominant over-prediction population.
# Fix: keep the simplified base (table removed) + density tiers + explicit
# quantities, but restore X3v2's TIGHT default budget with a NARROW, strongly
# image-gated large-plate escape instead of a blanket high ceiling.
_NEW_PORTION_BLOCK_V4 = """2. PORTION (size from the IMAGE, never from an item's name):
   First estimate the WHOLE-PLATE mass, then divide it among items - do NOT size each item independently and sum.
   a. WHOLE-PLATE BUDGET: From the image alone, estimate the total mass of food physically present. Most single plates in this setting are SMALL:
        - default range for a normal plate or bowl ~ 250-450 g
        - small plate / side / light salad ~ 150-280 g
      Treat ~450 g as the normal ceiling. ONLY exceed it (up to ~650 g) if the image CLEARLY shows a heaped, overflowing, or stacked plate / a large full bowl - never because the caption names many items.
      A plate holding many items is still ONE plate's worth of food - more items means each item's share is SMALLER, not more total food. When unsure, choose the LOWER end.
   b. ALLOCATE: split the total across items by the visual area/volume each occupies. Sides, garnishes, dressings, and "a bit of X" are often <30 g - shrink minor items rather than adding mass for each named one.
   c. CHECK: the sum of all weight_g must match the food visible on the plate. If your per-item weights add up to more than you can see, scale them ALL down.
   CALORIE DENSITY (pick from what you see, not from the item count): watery / veg / salad plates ~ 0.4-0.7 kcal/g; mixed plates ~ 0.8-1.3 kcal/g; fried / cheesy / oily / egg-and-meat plates ~ 1.4-2.0 kcal/g. Leafy greens and raw vegetables are low-mass and low-calorie - a long vegetable list should NOT pull calories up.
   SIZE REFERENCES (read volumes off the image): dinner plate ~ 25 cm; bowl ~ 14-16 cm / ~300 ml; fist ~ 1 cup ~ 150 g rice/pasta/veg; palm (no fingers) ~ 85 g cooked meat/fish; thumb tip ~ 1 tsp ~ 5 g oil/butter; cupped hand ~ 30 g nuts; tbsp ~ 15 g sauce. Unit objects for counting only: egg ~ 50 g; bread slice ~ 28 g; cheese slice ~ 22 g.
   Report specific numbers (e.g. 143 g, 87 g); do not round to multiples of 25/50 without reason.
"""

def _simplify_base_v4(p: str) -> str:
    start = p.index("2. PORTION"); end = p.index("3. CALCULATE")
    return p[:start] + _NEW_PORTION_BLOCK_V4 + p[end:]

CONVERSATIONAL_BASE_V4 = _simplify_base_v4(CONVERSATIONAL_INITIAL_PROMPT)
CONVERSATIONAL_PROMPT_X3V4 = CONVERSATIONAL_BASE_V4 + X3V3_ADDENDUM  # reuse the explicit-quantity addendum


# --- Cross-mode experiments (eval-only): does photo-only / text-only need fixing too? ---
# X1b: photo-only with the whole-plate budget applied UNGATED (does the budget help photo-only?)
BUDGET_UNGATED = """

WHOLE-PLATE PORTION DISCIPLINE: Before assigning per-item weights, estimate the TOTAL mass of food physically on the plate from the image (a single home/restaurant plate is typically ~250-500 g even when it holds many items), then divide that total across the items by their visible size. The per-item weights must sum to within what you can actually see; if they exceed it, scale them all down."""
CONVERSATIONAL_PROMPT_X1B = CONVERSATIONAL_INITIAL_PROMPT + BUDGET_UNGATED

# Etx: text-only with a composite-meal realism rule (the text prompt lacks the
# "items share one meal, don't stack full servings" rule that fixed the photo flow).
TEXT_COMPOSITE_FIX = """

COMPOSITE-MEAL REALISM: When the description lists several foods eaten together as one meal, they form ONE plate, not a stack of separate full servings. Do NOT assume a full standard serving of every item and sum them - that over-counts composite meals. Estimate a realistic total for the whole meal and divide it among the items by likely proportion. Honor any explicit quantity the user states for a specific item; apply this realism only to the unquantified items."""
TEXT_ONLY_PROMPT_FIXED = TEXT_ONLY_INITIAL_PROMPT + TEXT_COMPOSITE_FIX

# X3q: the shipped caption fix (X3v2) PLUS an explicit-quantity override, so a
# stated amount ("0.5 lb fish") is honored rather than ignored. Should match X3v2
# on quantity-free captions (the clause never fires) while adding the feature.
EXPLICIT_QTY_OVERRIDE = """

EXPLICIT AMOUNTS OVERRIDE THE IMAGE: the "caption = identity only" rule applies only to items the user did NOT quantify. If the user states an explicit amount for an item (e.g. "0.5 lb fish", "200g rice", "two eggs", "1 cup oats", "1 tbsp olive oil"), that amount is AUTHORITATIVE for that item: convert to grams (1 lb~454 g, 1 oz~28 g, 1 tbsp~15 g, 1 tsp~5 g, count x unit weight; 1 cup oats~80 g / cooked rice~160 g / berries~150 g) and use it directly as weight_g, excluding that item from the whole-plate budget. The budget and visual sizing apply only to the unquantified items."""
CONVERSATIONAL_PROMPT_X3Q = CONVERSATIONAL_PROMPT_X3V2 + EXPLICIT_QTY_OVERRIDE


@dataclass(frozen=True)
class Condition:
    id: str
    description: str
    comparison_ref: str  # which Paper 1 number this maps to (informational)


CONDITIONS: list[Condition] = [
    Condition(
        id="X1",
        description="Macroshot system + View C image, no user text",
        comparison_ref="Wang et al. 2026 — Gemini 2.5 Flash, image only (AvgMAE 45.55)",
    ),
    Condition(
        id="X2",
        description="Macroshot system + View C image + GT ingredient list",
        comparison_ref="Wang et al. 2026 — Gemini 2.5 Flash, image+ingredients (AvgMAE 44.12)",
    ),
    Condition(
        id="X3",
        description="Macroshot system + View C image + natural-language terse user description (no exact grams)",
        comparison_ref="(realistic Macroshot product flow — user types a casual description while logging)",
    ),
    Condition(
        id="X3v2",
        description="Experimental: Macroshot system + X3V2 addendum (text=identity, image=portion, whole-plate mass budget) + View C image + terse user description",
        comparison_ref="(experimental — improve on X3 by stopping standard-serving inflation from the caption)",
    ),
    Condition(
        id="X3v3",
        description="Experimental v2: simplified base (no per-item weight table, top-down mass budget, density tiers) + explicit-quantity handling + View C image + terse caption",
        comparison_ref="(experimental — iterate on X3v2: fix large-plate under-prediction + list inflation, honor explicit user amounts)",
    ),
    Condition(
        id="X3v4",
        description="Experimental v3: simplified base (no weight table) + TIGHT budget w/ narrow image-gated large-plate escape + density tiers + explicit quantities + caption",
        comparison_ref="(experimental — X3v2's tight budget + X3v3's table removal/density/explicit-qty, minus the loose ceiling that re-inflated)",
    ),
    Condition(
        id="X1v3",
        description="Control: simplified base (X3v3 base) + View C image, NO caption — checks the base simplification doesn't regress photo-only",
        comparison_ref="(control for X3v3 base change vs X1)",
    ),
    Condition(
        id="X1b",
        description="Experiment: photo-only + ungated whole-plate budget (does the budget help photo-only?)",
        comparison_ref="(experiment — compare to X1=65.3 Flash-Lite)",
    ),
    Condition(
        id="Etx_terse",
        description="Experiment: text-only FIXED prompt (composite-meal realism) + terse description",
        comparison_ref="(experiment — compare to E_terse baseline)",
    ),
    Condition(
        id="X3q",
        description="Experiment: shipped caption fix (X3v2) + explicit-quantity override; check it matches X3v2 on quantity-free captions",
        comparison_ref="(experiment — compare to X3v2=56.6 Flash-Lite)",
    ),
    Condition(
        id="E_terse",
        description="Macroshot text-only system + terse user description",
        comparison_ref="(no published equivalent — text-only floor)",
    ),
    Condition(
        id="E_detailed",
        description="Macroshot text-only system + detailed user description",
        comparison_ref="(no published equivalent — text-only ceiling)",
    ),
    Condition(
        id="BASELINE_unlabeled",
        description="Wang et al. 2026 reproduction: minimal prompt + View C image, no ingredients",
        comparison_ref="Wang et al. 2026 — Gemini 2.5 Flash, image only (AvgMAE 45.55)",
    ),
    Condition(
        id="BASELINE_labeled",
        description="Wang et al. 2026 reproduction: minimal prompt + View C image + GT ingredients",
        comparison_ref="Wang et al. 2026 — Gemini 2.5 Flash, image+ingredients (AvgMAE 44.12)",
    ),
]


def build_inputs(condition_id: str, dish: dict) -> dict:
    """Return {system, user, image_path} for the given condition + dish."""
    if condition_id == "X1":
        return {
            "system": CONVERSATIONAL_INITIAL_PROMPT,
            "user":   None,
            "image":  dish["image_view_c"],
        }
    if condition_id == "X2":
        return {
            "system": CONVERSATIONAL_INITIAL_PROMPT,
            "user":   f"Ingredients on this plate: {dish['ingredient_list_paper1_format']}.",
            "image":  dish["image_view_c"],
        }
    if condition_id == "X3":
        return {
            "system": CONVERSATIONAL_INITIAL_PROMPT,
            "user":   dish["text_descriptions"]["terse"],
            "image":  dish["image_view_c"],
        }
    if condition_id == "X3v2":
        return {
            "system": CONVERSATIONAL_PROMPT_X3V2,
            "user":   dish["text_descriptions"]["terse"],
            "image":  dish["image_view_c"],
        }
    if condition_id == "X3v3":
        return {
            "system": CONVERSATIONAL_PROMPT_X3V3,
            "user":   dish["text_descriptions"]["terse"],
            "image":  dish["image_view_c"],
        }
    if condition_id == "X3v4":
        return {
            "system": CONVERSATIONAL_PROMPT_X3V4,
            "user":   dish["text_descriptions"]["terse"],
            "image":  dish["image_view_c"],
        }
    if condition_id == "X1v3":
        return {
            "system": CONVERSATIONAL_BASE_V3,
            "user":   None,
            "image":  dish["image_view_c"],
        }
    if condition_id == "X1b":
        return {
            "system": CONVERSATIONAL_PROMPT_X1B,
            "user":   None,
            "image":  dish["image_view_c"],
        }
    if condition_id == "Etx_terse":
        return {
            "system": TEXT_ONLY_PROMPT_FIXED,
            "user":   dish["text_descriptions"]["terse"],
            "image":  None,
        }
    if condition_id == "X3q":
        return {
            "system": CONVERSATIONAL_PROMPT_X3Q,
            "user":   dish["text_descriptions"]["terse"],
            "image":  dish["image_view_c"],
        }
    if condition_id == "E_terse":
        return {
            "system": TEXT_ONLY_INITIAL_PROMPT,
            "user":   dish["text_descriptions"]["terse"],
            "image":  None,
        }
    if condition_id == "E_detailed":
        return {
            "system": TEXT_ONLY_INITIAL_PROMPT,
            "user":   dish["text_descriptions"]["detailed"],
            "image":  None,
        }
    if condition_id == "BASELINE_unlabeled":
        return {
            "system": None,
            "user":   WANG2026_USER_BASE,
            "image":  dish["image_view_c"],
        }
    if condition_id == "BASELINE_labeled":
        return {
            "system": None,
            "user":   (
                f"Ingredients on this plate: {dish['ingredient_list_paper1_format']}. "
                + WANG2026_USER_BASE
            ),
            "image":  dish["image_view_c"],
        }
    raise ValueError(f"Unknown condition: {condition_id}")
