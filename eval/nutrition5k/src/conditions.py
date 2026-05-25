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
