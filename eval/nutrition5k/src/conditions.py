"""Experiment condition definitions.

Each condition is a recipe for building (system_prompt, user_text, image_path)
from a dish record in prompts.json.

Naming: ``<prompt>_<input-mode>[_variant]``
  prompt      'generic'  - our reconstruction of the Wang et al. 2026 minimal
                           prompt; 'macroshot' - the production MacroShot prompt.
  input-mode  'cam'              - photo only
              'cam_ingredients'  - photo + the ground-truth ingredient list
              'cam_text'         - photo + a free-text user caption
              'text'             - no photo, user description only
  variant     'terse' / 'detailed' for the user-text conditions.

System prompts are imported VERBATIM from macroshot.src.gemini so this eval
tracks production. Do not redefine them here.
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
# verbatim - Fig 3 is an image, the body text only says "the models were
# required to calculate the calories (kcal), total weight (g), fat content (g),
# carbohydrate content (g), and protein content (g) for each sample" (§2.2.2).
# This is the closest text reconstruction. We request JSON so the parser can
# extract numbers reliably.
WANG2026_USER_BASE = (
    "Calculate the total calories (kcal), total weight (g), fat content (g), "
    "carbohydrate content (g), and protein content (g) for the food in this "
    "image. Reply with JSON only: "
    "{\"calories\": <kcal>, \"mass_g\": <g>, \"fat_g\": <g>, "
    "\"carb_g\": <g>, \"protein_g\": <g>}."
)


# --- Caption addendum for the photo + user-caption flow ----------------------
# Feeding the production prompt a casual user caption regresses vs photo-only
# because the model reads each named ingredient as a STANDARD SERVING and sums
# them. Fix: tell it the caption is for IDENTITY only, and force a top-down
# whole-plate mass budget so portions stay anchored to what's visible. Kept here
# so the eval pins the exact string it scores for `macroshot_cam_text_terse`.
CAPTION_ADDENDUM = """

==============================================================
ACCOMPANYING USER TEXT - READ CAREFULLY
==============================================================
A short caption from the user accompanies this image. The caption tells you
WHAT is on the plate, NOT how much. Use it ONLY to identify and disambiguate
items you can already see (e.g. confirm a protein is salmon, a grain is wheat
berry, a dressing is caesar). It is an identity hint, never a quantity signal.

- NEVER size an item from its name. A named ingredient is NOT a standard
  serving. Every weight_g MUST come from the visual portion in the image.
- Items the user lists SHARE the food visible on the plate - they do not each
  add a full serving. A caption listing "chicken, beef, potatoes, broccoli,
  pizza" describes ONE normal plate divided among those items, not five full
  servings stacked together. The more items named, the SMALLER each one's
  share of the same plate - listing more food does not mean more total food.
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

# Photo + user-caption system prompt = production prompt + the caption addendum.
MACROSHOT_CAM_TEXT_PROMPT = CONVERSATIONAL_INITIAL_PROMPT + CAPTION_ADDENDUM


@dataclass(frozen=True)
class Condition:
    id: str
    description: str
    comparison_ref: str  # which Wang et al. 2026 number this maps to (informational)


CONDITIONS: list[Condition] = [
    Condition(
        id="generic_cam",
        description="Generic baseline prompt + View C image, no user text",
        comparison_ref="Wang et al. 2026 - Gemini 2.5 Flash, image only (AvgMAE 45.55)",
    ),
    Condition(
        id="generic_cam_ingredients",
        description="Generic baseline prompt + View C image + ground-truth ingredient list",
        comparison_ref="Wang et al. 2026 - Gemini 2.5 Flash, image + ingredients (AvgMAE 44.12)",
    ),
    Condition(
        id="macroshot_cam",
        description="MacroShot system prompt + View C image, no user text",
        comparison_ref="vs generic_cam - isolates prompt style (image only)",
    ),
    Condition(
        id="macroshot_cam_ingredients",
        description="MacroShot system prompt + View C image + ground-truth ingredient list",
        comparison_ref="vs generic_cam_ingredients - isolates prompt style (image + ingredients)",
    ),
    Condition(
        id="macroshot_cam_text_terse",
        description="MacroShot system prompt + caption addendum + View C image + terse user caption (shipped flow)",
        comparison_ref="vs macroshot_cam - isolates the user caption + caption fix",
    ),
    Condition(
        id="macroshot_text_terse",
        description="MacroShot text-only prompt + terse user description, no photo",
        comparison_ref="(no published equivalent - text-only floor)",
    ),
    Condition(
        id="macroshot_text_detailed",
        description="MacroShot text-only prompt + detailed user description, no photo",
        comparison_ref="(no published equivalent - text-only ceiling)",
    ),
]


def build_inputs(condition_id: str, dish: dict) -> dict:
    """Return {system, user, image} for the given condition + dish."""
    img = dish["image_view_c"]
    terse = dish["text_descriptions"]["terse"]
    detailed = dish["text_descriptions"]["detailed"]
    ingredients = f"Ingredients on this plate: {dish['ingredient_list_paper1_format']}."

    if condition_id == "generic_cam":
        return {"system": None, "user": WANG2026_USER_BASE, "image": img}
    if condition_id == "generic_cam_ingredients":
        return {"system": None, "user": f"{ingredients} {WANG2026_USER_BASE}", "image": img}
    if condition_id == "macroshot_cam":
        return {"system": CONVERSATIONAL_INITIAL_PROMPT, "user": None, "image": img}
    if condition_id == "macroshot_cam_ingredients":
        return {"system": CONVERSATIONAL_INITIAL_PROMPT, "user": ingredients, "image": img}
    if condition_id == "macroshot_cam_text_terse":
        return {"system": MACROSHOT_CAM_TEXT_PROMPT, "user": terse, "image": img}
    if condition_id == "macroshot_text_terse":
        return {"system": TEXT_ONLY_INITIAL_PROMPT, "user": terse, "image": None}
    if condition_id == "macroshot_text_detailed":
        return {"system": TEXT_ONLY_INITIAL_PROMPT, "user": detailed, "image": None}
    raise ValueError(f"Unknown condition: {condition_id}")
