#!/usr/bin/env python3
"""Run the meal-analysis benchmark.

For every verified case in eval/meals_groundtruth.json, calls each
candidate model with each config, computes per-case + aggregate
metrics, and writes a markdown + HTML report to eval/runs/<ts>/.

Usage:
  .venv/bin/python scripts/eval_run.py
  .venv/bin/python scripts/eval_run.py --models gemini-2.5-flash,gemini-2.5-pro
  .venv/bin/python scripts/eval_run.py --no-cache --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))

# Load .env so GEMINI_API_KEY etc. are available - eval runs as a standalone
# script, outside the FastAPI app's startup that normally does this.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_PROJ / ".env")

from scripts.eval_lib.dataset import EVAL_ROOT, load  # noqa: E402
from scripts.eval_lib.metrics import aggregate, compute  # noqa: E402
from scripts.eval_lib.report import write_reports  # noqa: E402
from scripts.eval_lib.runners import CACHE_DIR, run_all  # noqa: E402


DEFAULT_MODELS = "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro"
DEFAULT_CONFIGS = "raw,with_fatsecret"


async def _amain(args) -> int:
    cases_all = load()
    cases = [c for c in cases_all if c.is_verified]
    if not cases:
        print(f"No verified cases in {EVAL_ROOT / 'meals_groundtruth.json'}.")
        print("Seed first: .venv/bin/python scripts/eval_seed.py --count 30")
        print("Then edit the JSON and set ground_truth.verified_by=\"yourname\" on each entry.")
        return 1
    skipped = len(cases_all) - len(cases)
    if skipped:
        print(f"Skipping {skipped} unverified case(s).")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]

    # Count cache hits/misses by checking before run_all populates it.
    pre_existing = {p.name for p in CACHE_DIR.glob("*.json")} if CACHE_DIR.exists() else set()

    print(f"Running {len(cases)} cases × {len(models)} models × {len(configs)} configs "
          f"= {len(cases) * len(models) * len(configs)} calls "
          f"(concurrency={args.concurrency}, cache={'on' if not args.no_cache else 'off'})")

    results = await run_all(
        cases, models, configs,
        use_cache=not args.no_cache,
        concurrency=args.concurrency,
    )

    cases_by_id = {c.id: c for c in cases}
    metrics = [compute(cases_by_id[r.case_id], r) for r in results]

    # Aggregate per (model, config)
    aggs_by_key: dict[tuple[str, str], list] = {}
    for m in metrics:
        aggs_by_key.setdefault((m.model, m.config), []).append(m)
    aggs = [aggregate(group) for group in aggs_by_key.values()]

    # Cache stats
    post_existing = {p.name for p in CACHE_DIR.glob("*.json")} if CACHE_DIR.exists() else set()
    cache_hits = sum(1 for r in results if not r.failed) - (len(post_existing) - len(pre_existing))
    cache_hits = max(0, cache_hits)
    cache_misses = len(post_existing) - len(pre_existing)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = EVAL_ROOT / "runs" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    # Raw results.jsonl for downstream tooling
    with (out_dir / "results.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")

    md_path, html_path = write_reports(
        out_dir, aggs, metrics, cases_count=len(cases),
        timestamp=timestamp, cache_hits=cache_hits, cache_misses=cache_misses,
    )

    print()
    print(md_path.read_text())
    print()
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", default=DEFAULT_MODELS,
                   help=f"Comma-separated model IDs (default: {DEFAULT_MODELS})")
    p.add_argument("--configs", default=DEFAULT_CONFIGS,
                   help=f"Comma-separated configs (default: {DEFAULT_CONFIGS})")
    p.add_argument("--no-cache", action="store_true", help="Bypass response cache")
    p.add_argument("--concurrency", type=int, default=4,
                   help="Max concurrent model calls (default 4)")
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
