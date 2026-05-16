"""Validation dataset I/O.

Loads / saves eval/meals_groundtruth.json. The schema is documented in
eval/README.md and exemplified in eval/meals_groundtruth.example.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

InputMode = Literal["image", "text", "image_text"]

EVAL_ROOT = Path(__file__).resolve().parents[2] / "eval"
DATASET_PATH = EVAL_ROOT / "meals_groundtruth.json"
IMAGES_DIR = EVAL_ROOT / "images"


@dataclass
class GTItem:
    """Per-item ground truth. Schema mirrors src.models.FoodItem so the
    seed script can drop the prod meal_logs.items_json straight in as a
    starting draft."""
    name: str
    calories: float
    protein: float
    carbs: float
    fat: float
    description: str = ""
    weight_g: float | None = None


@dataclass
class GroundTruth:
    items: list[GTItem]
    totals: dict[str, float]
    source_of_truth: str = ""
    notes: str = ""
    verified_by: str | None = None
    verified_at: str | None = None


@dataclass
class Case:
    id: str
    input_mode: InputMode
    image_path: str | None
    text: str | None
    ground_truth: GroundTruth
    tags: list[str] = field(default_factory=list)
    skip: bool = False
    skip_reason: str | None = None

    @property
    def is_verified(self) -> bool:
        return bool(self.ground_truth.verified_by) and not self.skip

    @property
    def absolute_image_path(self) -> Path | None:
        if not self.image_path:
            return None
        # Stored relative to eval/ so the dataset is portable.
        return EVAL_ROOT / self.image_path


_GT_ITEM_KEYS = {"name", "calories", "protein", "carbs", "fat", "description", "weight_g"}


def _gt_from_dict(d: dict[str, Any]) -> GroundTruth:
    items = []
    for raw in d.get("items", []):
        # Drop unknown keys silently so prod items_json (which has more
        # fields like brand, source, *_per_100g) can be loaded directly.
        filtered = {k: v for k, v in raw.items() if k in _GT_ITEM_KEYS}
        items.append(GTItem(**filtered))
    return GroundTruth(
        items=items,
        totals=d.get("totals", {}),
        source_of_truth=d.get("source_of_truth", ""),
        notes=d.get("notes", ""),
        verified_by=d.get("verified_by"),
        verified_at=d.get("verified_at"),
    )


def _gt_to_dict(gt: GroundTruth) -> dict[str, Any]:
    return {
        "items": [vars(i) for i in gt.items],
        "totals": gt.totals,
        "source_of_truth": gt.source_of_truth,
        "notes": gt.notes,
        "verified_by": gt.verified_by,
        "verified_at": gt.verified_at,
    }


def _case_from_dict(d: dict[str, Any]) -> Case:
    return Case(
        id=d["id"],
        input_mode=d["input_mode"],
        image_path=d.get("image_path"),
        text=d.get("text"),
        ground_truth=_gt_from_dict(d.get("ground_truth", {})),
        tags=list(d.get("tags", [])),
        skip=bool(d.get("skip", False)),
        skip_reason=d.get("skip_reason"),
    )


def _case_to_dict(c: Case) -> dict[str, Any]:
    return {
        "id": c.id,
        "input_mode": c.input_mode,
        "image_path": c.image_path,
        "text": c.text,
        "ground_truth": _gt_to_dict(c.ground_truth),
        "tags": c.tags,
        "skip": c.skip,
        "skip_reason": c.skip_reason,
    }


def load(path: Path = DATASET_PATH) -> list[Case]:
    if not path.exists():
        return []
    with path.open() as f:
        raw = json.load(f)
    return [_case_from_dict(c) for c in raw.get("cases", [])]


def save(cases: list[Case], path: Path = DATASET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(
            {"version": 1, "cases": [_case_to_dict(c) for c in cases]},
            f,
            indent=2,
        )


def load_image_bytes(case: Case) -> bytes | None:
    """Read the case's image off disk, or return None for text-only cases."""
    p = case.absolute_image_path
    if not p or not p.exists():
        return None
    return p.read_bytes()
