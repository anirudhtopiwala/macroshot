"""Main runner. Loops over (condition x dish), calls Gemini, parses, scores.

Usage:
  cd <macroshot-root>
  .venv/bin/python -m eval.nutrition5k.src.run \
      --model gemini-2.5-flash \
      --conditions X1,X2,E_terse,E_detailed \
      --dishes dish_1562871537
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project root (macroshot/) on path for `from src.gemini import ...`
_BENCH_ROOT = Path(__file__).resolve().parents[1]
_MACROSHOT_ROOT = _BENCH_ROOT.parents[1]
if str(_MACROSHOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MACROSHOT_ROOT))

# Load .env so GEMINI_API_KEY is available when invoked standalone.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_MACROSHOT_ROOT / ".env")

from .client import call as gemini_call  # noqa: E402
from .conditions import CONDITIONS, build_inputs  # noqa: E402
from .metrics import aggregate, per_dish  # noqa: E402
from .parsers import parse  # noqa: E402
from .report import render as render_report  # noqa: E402

# Capture provenance for the imported Macroshot prompts so each run records
# exactly which source file + sha256 was used. If the prompt is edited later,
# the run is still reproducible from the recorded hash.
import hashlib  # noqa: E402
import src.gemini as macroshot_gemini  # noqa: E402


def _prompt_provenance() -> dict:
    src_path = Path(macroshot_gemini.__file__)
    src_text = src_path.read_text()
    return {
        "source_file": str(src_path),
        "source_sha256": hashlib.sha256(src_text.encode()).hexdigest(),
        "prompts": {
            "CONVERSATIONAL_INITIAL_PROMPT": {
                "length_chars": len(macroshot_gemini.CONVERSATIONAL_INITIAL_PROMPT),
                "sha256":       hashlib.sha256(
                    macroshot_gemini.CONVERSATIONAL_INITIAL_PROMPT.encode()
                ).hexdigest(),
            },
            "TEXT_ONLY_INITIAL_PROMPT": {
                "length_chars": len(macroshot_gemini.TEXT_ONLY_INITIAL_PROMPT),
                "sha256":       hashlib.sha256(
                    macroshot_gemini.TEXT_ONLY_INITIAL_PROMPT.encode()
                ).hexdigest(),
            },
        },
    }


def _load_dishes() -> list[dict]:
    return json.loads((_BENCH_ROOT / "data" / "prompts.json").read_text())


def _resolve_image(image_rel: str | None) -> str | None:
    if not image_rel:
        return None
    return str(_BENCH_ROOT / image_rel)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Single model. Use --models for comma-separated list.")
    parser.add_argument("--models", default=None,
                        help="Comma-separated list of models. If set, runs each "
                             "model sequentially (one run dir per model).")
    parser.add_argument("--conditions", default=",".join(c.id for c in CONDITIONS),
                        help="Comma-separated condition IDs")
    parser.add_argument("--dishes", default="",
                        help="Comma-separated dish IDs (default: all in prompts.json)")
    parser.add_argument("--temperature", type=float, default=0.1,
                        help="Macroshot prod default")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="Higher than prod's 3072 because we have no retry-on-truncation loop")
    args = parser.parse_args()

    all_dishes = _load_dishes()
    if args.dishes:
        wanted = set(args.dishes.split(","))
        dishes = [d for d in all_dishes if d["dish_id"] in wanted]
    else:
        dishes = all_dishes
    conditions = args.conditions.split(",")
    models = args.models.split(",") if args.models else [args.model]

    for model in models:
        _run_one_model(model, dishes, conditions, args)
    return 0


def _run_one_model(model: str, dishes: list[dict], conditions: list[str], args) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = _BENCH_ROOT / "runs" / f"{model}_{ts}"
    (run_dir / "predictions").mkdir(parents=True, exist_ok=True)
    print(f"\n========= MODEL: {model}  ({len(dishes)} dishes × {len(conditions)} conditions) =========")

    config = {
        "model": model, "temperature": args.temperature,
        "max_tokens": args.max_tokens, "conditions": conditions,
        "dish_ids": [d["dish_id"] for d in dishes],
        "started_at": ts,
        "prompt_provenance": _prompt_provenance(),
    }
    (run_dir / "config.json").write_text(json.dumps(config, indent=2))

    per_condition_errors: dict[str, list] = {c: [] for c in conditions}

    for cond_id in conditions:
        cond_dir = run_dir / "predictions" / cond_id
        cond_dir.mkdir(parents=True, exist_ok=True)
        for dish in dishes:
            inp = build_inputs(cond_id, dish)
            print(f"[{cond_id}] {dish['dish_id']}", flush=True)
            try:
                result = gemini_call(
                    model=model,
                    system=inp["system"],
                    user=inp["user"],
                    image_path=_resolve_image(inp["image"]),
                    temperature=args.temperature,
                    max_output_tokens=args.max_tokens,
                )
                parsed = parse(result.text)
                err = per_dish(parsed, dish["ground_truth"]["totals"], dish["dish_id"])
                per_condition_errors[cond_id].append(err)
                record = {
                    "dish_id": dish["dish_id"],
                    "condition": cond_id,
                    "prompt": {
                        "system":        inp["system"],         # full text, not truncated
                        "system_sha256": hashlib.sha256(inp["system"].encode()).hexdigest()
                                          if inp["system"] else None,
                        "user":          inp["user"],
                        "image":         inp["image"],
                    },
                    "response_text": result.text,
                    "parsed": parsed,
                    "ground_truth": dish["ground_truth"]["totals"],
                    "abs_err": err.abs_err,
                    "pct_err": err.pct_err,
                    "latency_s": result.latency_s,
                    "input_tokens":    result.input_tokens,
                    "output_tokens":   result.output_tokens,
                    "thinking_tokens": result.thinking_tokens,
                    "total_tokens":    result.total_tokens,
                }
                (cond_dir / f"{dish['dish_id']}.json").write_text(
                    json.dumps(record, indent=2))
                print(f"  abs_err={ {k: round(v,1) for k,v in err.abs_err.items()} }")
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)

    summary = {c: aggregate(errs) for c, errs in per_condition_errors.items() if errs}
    (run_dir / "metrics.json").write_text(json.dumps(summary, indent=2))

    report_path = render_report(run_dir)
    print(f"\nReport: {report_path}")
    print("\n=== Summary (mean | median across dishes, averaged across 5 metrics) ===")
    print(f"{'condition':14s}  {'AvgMAE':>8s}  {'MedAE':>8s}  {'AvgRelErr':>10s}  {'MedPE':>8s}  n")
    for c, m in summary.items():
        print(f"{c:14s}  {m['avg_mae']:8.2f}  {m['avg_median_ae']:8.2f}  "
              f"{m['avg_rel_err']:9.1f}%  {m['avg_median_pe']:7.1f}%  {m['n_dishes']}")
    print(f"\nRun dir: {run_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
