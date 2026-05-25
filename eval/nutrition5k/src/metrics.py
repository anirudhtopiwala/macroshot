"""Nutrient-estimation metrics from Wang et al. 2026 (Curr. Res. Food Sci.
12:101405, eqs 10–13). See REFERENCES.md for full citation.

MAE_x        = mean_k |y_k - y_hat_k|                       for x in {cal, mass, fat, carb, prot}
MedianAE_x   = median_k |y_k - y_hat_k|                     (we add this; not in Wang 2026)
RelErr_x     = mean_k |y_k - y_hat_k| / |y_k|  * 100%       ( = MAPE)
MedianPE_x   = median_k |y_k - y_hat_k| / |y_k|  * 100%     (we add this)
AvgMAE       = mean of 5 MAEs
AvgRelErr    = mean of 5 RelErrs
AvgMedianAE  = mean of 5 MedianAEs
AvgMedianPE  = mean of 5 MedianPEs

Mean vs median: mean MAE/RelErr matches Wang 2026; median is the robust
companion because nutrient errors are heavy-tailed — Wang 2026 Table 5
shows mean RelErr_fat hitting 482% for Gemini 2.5 Flash because many dishes
have near-zero fat denominators. Median collapses that tail. Report both.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


METRIC_KEYS = ("calories", "mass_g", "fat_g", "carb_g", "protein_g")


@dataclass
class PerDishError:
    dish_id: str
    abs_err: dict[str, float]
    pct_err: dict[str, float]


def per_dish(prediction: dict, truth: dict, dish_id: str) -> PerDishError:
    abs_err: dict[str, float] = {}
    pct_err: dict[str, float] = {}
    for k in METRIC_KEYS:
        p = prediction.get(k)
        t = truth.get(k)
        if p is None or t is None:
            abs_err[k] = float("nan")
            pct_err[k] = float("nan")
            continue
        err = abs(p - t)
        abs_err[k] = err
        pct_err[k] = (err / abs(t) * 100.0) if t != 0 else float("nan")
    return PerDishError(dish_id=dish_id, abs_err=abs_err, pct_err=pct_err)


def aggregate(errors: list[PerDishError]) -> dict:
    """Per-metric mean + median over dishes, then averaged across 5 metrics."""
    def _safe_mean(vals: list[float]) -> float:
        good = [v for v in vals if v == v]
        return sum(good) / len(good) if good else float("nan")

    def _safe_median(vals: list[float]) -> float:
        good = [v for v in vals if v == v]
        return median(good) if good else float("nan")

    mae: dict[str, float] = {}
    rel: dict[str, float] = {}
    med_ae: dict[str, float] = {}
    med_pe: dict[str, float] = {}
    for k in METRIC_KEYS:
        a_vals = [e.abs_err[k] for e in errors]
        p_vals = [e.pct_err[k] for e in errors]
        mae[k]    = _safe_mean(a_vals)
        rel[k]    = _safe_mean(p_vals)
        med_ae[k] = _safe_median(a_vals)
        med_pe[k] = _safe_median(p_vals)

    return {
        "mae_per_metric":       mae,
        "rel_per_metric":       rel,
        "median_ae_per_metric": med_ae,
        "median_pe_per_metric": med_pe,
        "avg_mae":              _safe_mean(list(mae.values())),
        "avg_rel_err":          _safe_mean(list(rel.values())),
        "avg_median_ae":        _safe_mean(list(med_ae.values())),
        "avg_median_pe":        _safe_mean(list(med_pe.values())),
        "n_dishes":             len(errors),
    }
