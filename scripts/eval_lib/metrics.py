"""Metric computation: per-case and aggregate.

Score philosophy: macro accuracy dominates because that's what users
actually see; item recall and hallucination penalty catch identification
bugs; cost and latency are tiebreakers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

from rapidfuzz import fuzz

from .dataset import Case, GroundTruth
from .runners import CallResult


# Tolerance bands per macro (% deviation). Override at the top of report
# generation if a model has different tolerance needs.
TOL_GREEN = 0.15
TOL_YELLOW = 0.30


def _band(err_frac: float) -> str:
    if err_frac <= TOL_GREEN:
        return "green"
    if err_frac <= TOL_YELLOW:
        return "yellow"
    return "red"


def _safe_pct_err(model_v: float, truth_v: float) -> float:
    """|model - truth| / truth. Returns 0.0 when both are 0; large when truth is 0 but model isn't."""
    if truth_v == 0 and model_v == 0:
        return 0.0
    if truth_v == 0:
        return 1.0  # 100% error - model invented nonzero where there should be zero
    return abs(model_v - truth_v) / abs(truth_v)


@dataclass
class CaseMetric:
    case_id: str
    model: str
    config: str
    failed: bool = False
    error: str | None = None

    # Macro errors - fraction (0.20 = 20%)
    kcal_err: float = 0.0
    protein_err: float = 0.0
    carbs_err: float = 0.0
    fat_err: float = 0.0
    macro_band: str = "red"  # worst band across the four

    # Items
    item_recall: float = 0.0      # matched_gt_items / gt_count
    item_precision: float = 1.0   # matched_model_items / model_count
    halluc_count: int = 0          # model items with no GT match

    # Cost & latency
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    # Pre-rendered side-by-side rows for the per-case report
    side_by_side: list[dict[str, Any]] = field(default_factory=list)


def _match_items(gt_items: list, model_items: list, threshold: int = 80) -> tuple[int, int]:
    """Greedy fuzzy matching by name. Returns (matched_gt_count, halluc_count)."""
    if not gt_items:
        return 0, len(model_items)
    if not model_items:
        return 0, 0

    used_model: set[int] = set()
    matched_gt = 0
    for gt in gt_items:
        best_idx, best_score = -1, 0
        for i, m in enumerate(model_items):
            if i in used_model:
                continue
            score = fuzz.token_set_ratio(gt.name.lower(), m["name"].lower())
            if score > best_score:
                best_idx, best_score = i, score
        if best_score >= threshold:
            used_model.add(best_idx)
            matched_gt += 1

    halluc = len(model_items) - len(used_model)
    return matched_gt, halluc


def compute(case: Case, result: CallResult) -> CaseMetric:
    m = CaseMetric(
        case_id=case.id, model=result.model, config=result.config,
        failed=result.failed, error=result.error,
        latency_ms=result.latency_ms, cost_usd=result.cost_usd,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
    )
    if result.failed:
        m.macro_band = "red"
        return m

    gt = case.ground_truth
    model_totals = result.totals or {}
    m.kcal_err = _safe_pct_err(model_totals.get("calories", 0), gt.totals.get("calories", 0))
    m.protein_err = _safe_pct_err(model_totals.get("protein", 0), gt.totals.get("protein", 0))
    m.carbs_err = _safe_pct_err(model_totals.get("carbs", 0), gt.totals.get("carbs", 0))
    m.fat_err = _safe_pct_err(model_totals.get("fat", 0), gt.totals.get("fat", 0))

    bands = [_band(e) for e in (m.kcal_err, m.protein_err, m.carbs_err, m.fat_err)]
    if "red" in bands:
        m.macro_band = "red"
    elif "yellow" in bands:
        m.macro_band = "yellow"
    else:
        m.macro_band = "green"

    matched, halluc = _match_items(gt.items, result.items)
    m.item_recall = matched / len(gt.items) if gt.items else 0.0
    m.item_precision = matched / len(result.items) if result.items else 1.0
    m.halluc_count = halluc

    # Side-by-side: show truth row + model row aligned for the HTML report
    m.side_by_side = [
        {"label": "ground truth",
         **{k: gt.totals.get(k, 0) for k in ("calories", "protein", "carbs", "fat")},
         "items": [i.name for i in gt.items]},
        {"label": f"{result.model} · {result.config}",
         **{k: model_totals.get(k, 0) for k in ("calories", "protein", "carbs", "fat")},
         "items": [i["name"] for i in result.items],
         "kcal_err": m.kcal_err, "protein_err": m.protein_err,
         "carbs_err": m.carbs_err, "fat_err": m.fat_err,
         "kcal_band": _band(m.kcal_err), "protein_band": _band(m.protein_err),
         "carbs_band": _band(m.carbs_err), "fat_band": _band(m.fat_err)},
    ]
    return m


# ── Aggregate ─────────────────────────────────────────────────────────────
@dataclass
class AggregateMetric:
    model: str
    config: str
    case_count: int = 0
    failed_count: int = 0

    # Macro errors averaged across cases (MAPE)
    kcal_mape: float = 0.0
    protein_mape: float = 0.0
    carbs_mape: float = 0.0
    fat_mape: float = 0.0

    green_pct: float = 0.0
    yellow_pct: float = 0.0
    red_pct: float = 0.0

    item_recall: float = 0.0
    item_precision: float = 0.0
    halluc_per_case: float = 0.0

    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    cost_per_case: float = 0.0

    composite_score: float = 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(round((pct / 100) * (len(s) - 1)))
    return s[max(0, min(idx, len(s) - 1))]


# Composite score weights (sum to 1.0). Higher is better, all components
# normalized to [0, 1] before weighting.
WEIGHTS = {"macro": 0.50, "recall": 0.20, "halluc": 0.15, "cost": 0.10, "latency": 0.05}

# Cost / latency reference points for [0,1] normalization. Anything cheaper
# or faster than these is treated as 1.0; anything 10x worse is 0.0.
COST_REF_USD = 0.005     # $/case is "perfect"
COST_FLOOR_USD = 0.05    # $/case is "0.0" - adjust if pro models distort the curve
LATENCY_REF_MS = 1000    # 1s is "perfect"
LATENCY_FLOOR_MS = 10000 # 10s is "0.0"


def _norm(v: float, good: float, bad: float) -> float:
    """Linear normalization: <= good → 1.0, >= bad → 0.0."""
    if v <= good:
        return 1.0
    if v >= bad:
        return 0.0
    return 1.0 - (v - good) / (bad - good)


def aggregate(metrics: list[CaseMetric]) -> AggregateMetric:
    if not metrics:
        return AggregateMetric(model="", config="")
    a = AggregateMetric(model=metrics[0].model, config=metrics[0].config,
                        case_count=len(metrics))
    a.failed_count = sum(1 for m in metrics if m.failed)
    ok = [m for m in metrics if not m.failed]
    if ok:
        a.kcal_mape = mean(m.kcal_err for m in ok)
        a.protein_mape = mean(m.protein_err for m in ok)
        a.carbs_mape = mean(m.carbs_err for m in ok)
        a.fat_mape = mean(m.fat_err for m in ok)
        bands = [m.macro_band for m in ok]
        a.green_pct = bands.count("green") / len(bands)
        a.yellow_pct = bands.count("yellow") / len(bands)
        a.red_pct = bands.count("red") / len(bands)
        a.item_recall = mean(m.item_recall for m in ok)
        a.item_precision = mean(m.item_precision for m in ok)
        a.halluc_per_case = mean(m.halluc_count for m in ok)
        a.latency_p50_ms = median(m.latency_ms for m in ok)
        a.latency_p95_ms = _percentile([m.latency_ms for m in ok], 95)
        a.cost_per_case = mean(m.cost_usd for m in ok)

        # Composite - bounded to [0,1] per axis
        macro_score = max(0.0, 1.0 - mean([a.kcal_mape, a.protein_mape, a.carbs_mape, a.fat_mape]))
        recall_score = a.item_recall
        halluc_score = max(0.0, 1.0 - min(1.0, a.halluc_per_case / 2))  # 2 hallucs/case = 0
        cost_score = _norm(a.cost_per_case, COST_REF_USD, COST_FLOOR_USD)
        lat_score = _norm(a.latency_p95_ms, LATENCY_REF_MS, LATENCY_FLOOR_MS)
        a.composite_score = (
            WEIGHTS["macro"] * macro_score
            + WEIGHTS["recall"] * recall_score
            + WEIGHTS["halluc"] * halluc_score
            + WEIGHTS["cost"] * cost_score
            + WEIGHTS["latency"] * lat_score
        )
    return a
