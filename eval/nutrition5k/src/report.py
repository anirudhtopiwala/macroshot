"""Human-readable markdown report for a run directory.

Produces report.md alongside metrics.json. For each (dish, condition):
  - the prompt sent (system snippet + full user msg)
  - the raw model response
  - parsed totals vs GT
  - per-metric abs/pct error

Also includes a side-by-side summary table comparing conditions for each
dish so you can scan "which condition got closest" at a glance.
"""
from __future__ import annotations

import json
from pathlib import Path


_METRIC_LABELS = {
    "calories":  ("cal",   "kcal"),
    "mass_g":    ("mass",  "g"),
    "fat_g":     ("fat",   "g"),
    "carb_g":    ("carb",  "g"),
    "protein_g": ("prot",  "g"),
}

# Reference numbers from Wang et al. 2026 (Curr. Res. Food Sci. 12:101405),
# Table 4 — Gemini 2.5 Flash on Nutrition5K test set (n=3466).
# See REFERENCES.md for full citation.
WANG2026_REF = {
    "BASELINE_unlabeled": {"avg_mae": 45.55, "avg_rel_err": 161.19, "n": 3466,
                           "label": "Wang et al. 2026 — Gemini 2.5 Flash, image only (n=3466)"},
    "BASELINE_labeled":   {"avg_mae": 44.12, "avg_rel_err": 138.95, "n": 3466,
                           "label": "Wang et al. 2026 — Gemini 2.5 Flash, image+ingredients (n=3466)"},
}


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.1f}"


def render(run_dir: Path) -> Path:
    config = json.loads((run_dir / "config.json").read_text())
    summary = json.loads((run_dir / "metrics.json").read_text())
    pred_root = run_dir / "predictions"

    lines: list[str] = []
    lines.append(f"# Run report: {run_dir.name}\n")
    lines.append(f"- Model: `{config['model']}`")
    lines.append(f"- Temperature: {config['temperature']} · max_tokens: {config['max_tokens']}")
    lines.append(f"- Dishes (n={len(config['dish_ids'])}): {', '.join(config['dish_ids'])}")
    lines.append(f"- Conditions: {', '.join(config['conditions'])}\n")

    # ── Headline summary across conditions ───────────────────────────────
    lines.append("## Headline summary\n")
    lines.append("| Condition | AvgMAE | MedAE | AvgRelErr | MedPE | n |")
    lines.append("|-----------|-------:|------:|----------:|------:|--:|")
    for c in config["conditions"]:
        s = summary.get(c)
        if not s:
            continue
        lines.append(
            f"| {c} | {_fmt(s['avg_mae'])} | {_fmt(s['avg_median_ae'])} | "
            f"{_fmt(s['avg_rel_err'])}% | {_fmt(s['avg_median_pe'])}% | {s['n_dishes']} |"
        )
        if c in WANG2026_REF:
            ref = WANG2026_REF[c]
            lines.append(
                f"| └ *{ref['label']}* | *{ref['avg_mae']}* | — | "
                f"*{ref['avg_rel_err']}%* | — | *{ref['n']}* |"
            )
    lines.append("")

    # ── Per-dish side-by-side ────────────────────────────────────────────
    for dish_id in config["dish_ids"]:
        lines.append(f"## Dish `{dish_id}`\n")
        # Header row: each metric labeled with units
        cols = "| Condition | " + " | ".join(
            f"{lbl} ({u})" for lbl, u in _METRIC_LABELS.values()
        ) + " |"
        sep = "|---|" + "|".join("---:" for _ in _METRIC_LABELS) + "|"
        lines.append(cols)
        lines.append(sep)

        # GT row
        gt_row = None
        for c in config["conditions"]:
            p = pred_root / c / f"{dish_id}.json"
            if p.exists():
                rec = json.loads(p.read_text())
                gt = rec["ground_truth"]
                gt_row = "| **GT** | " + " | ".join(
                    _fmt(gt[k]) for k in _METRIC_LABELS
                ) + " |"
                break
        if gt_row:
            lines.append(gt_row)

        # Per-condition rows: predicted | (abs err)
        for c in config["conditions"]:
            p = pred_root / c / f"{dish_id}.json"
            if not p.exists():
                lines.append(f"| {c} | _missing_ |" + " |" * (len(_METRIC_LABELS) - 1))
                continue
            rec = json.loads(p.read_text())
            cells = []
            for k in _METRIC_LABELS:
                pred = rec["parsed"].get(k)
                err = rec["abs_err"].get(k)
                pct = rec["pct_err"].get(k)
                if pred is None:
                    cells.append("—")
                else:
                    err_str = f"Δ{_fmt(err)} ({_fmt(pct)}%)"
                    cells.append(f"{_fmt(pred)} <br>_{err_str}_")
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
        lines.append("")

        # Per-dish prompts & responses, collapsed
        for c in config["conditions"]:
            p = pred_root / c / f"{dish_id}.json"
            if not p.exists():
                continue
            rec = json.loads(p.read_text())
            lines.append(f"<details><summary><b>{c}</b> — prompt & response</summary>\n")
            sys_text = rec["prompt"].get("system")
            sys_sha  = rec["prompt"].get("system_sha256")
            if sys_text:
                lines.append(f"**System** (sha256 `{sys_sha[:12] if sys_sha else '?'}`, "
                             f"{len(sys_text)} chars):\n")
                lines.append("<details><summary>show full system prompt</summary>\n")
                lines.append(f"```\n{sys_text}\n```\n")
                lines.append("</details>\n")
            else:
                lines.append("**System:** _(none)_\n")
            user = rec["prompt"].get("user")
            lines.append(f"**User:** `{user}`\n" if user else "**User:** _(image only)_\n")
            img = rec["prompt"].get("image")
            if img:
                lines.append(f"**Image:** `{img}`\n")
            lines.append(f"**Latency:** {rec.get('latency_s', 0):.2f}s · "
                         f"in_tokens={rec.get('input_tokens')} · "
                         f"out_tokens={rec.get('output_tokens')}\n")
            lines.append(f"**Raw response:**\n\n```json\n{rec['response_text']}\n```\n")
            lines.append("</details>\n")

    out_path = run_dir / "report.md"
    out_path.write_text("\n".join(lines))
    return out_path
