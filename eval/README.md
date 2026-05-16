# Meal-analysis eval

Offline benchmark for the meal-analysis model. Run it whenever a new
candidate model lands; flip `GEMINI_MEAL_MODEL` in your `.env` only
when the report says it wins.

## Two-step loop

```
# 1. Build the ground-truth set
#    Copy meals_groundtruth.example.json to meals_groundtruth.json,
#    then add your verified meals (one entry per real meal whose
#    macros you know). Each case needs verified_by set — unverified
#    cases are silently skipped at runtime. Schema below.

# 2. Run
.venv/bin/python scripts/eval_run.py \
  --models gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro \
  --configs raw,with_fatsecret
```

Output lands in `eval/runs/<timestamp>/` - see `report.md` (terminal-friendly)
and `report.html` (color-coded drill-down).

## Files

| Path | Tracked? | Purpose |
|---|---|---|
| `eval/meals_groundtruth.json` | no | Your verified ground-truth cases (gitignored) |
| `eval/meals_groundtruth.example.json` | yes | Sanitized example schema |
| `eval/images/` | no | Source images for image-mode cases |
| `eval/runs/` | no | Time-stamped run outputs |
| `scripts/eval_run.py` | yes | Runs all configured models, writes report |
| `scripts/eval_lib/` | yes | Dataset, runners, metrics, report code |

## Schema (`meals_groundtruth.json`)

```jsonc
{
  "version": 1,
  "cases": [
    {
      "id": "burrito-2026-04-12",
      "input_mode": "image_text",        // image | text | image_text
      "image_path": "images/abc.jpg",    // null when mode == text
      "text": "chicken burrito bowl",    // null/empty when mode == image
      "ground_truth": {
        "items": [{"name": "...", "qty": 1, "unit": "bowl",
                   "calories": 705, "protein": 52, "carbs": 78, "fat": 22}],
        "totals": {"calories": 705, "protein": 52, "carbs": 78, "fat": 22},
        "source_of_truth": "chipotle.com",
        "notes": "...",
        "verified_by": "yourname",       // null = unverified, runner skips
        "verified_at": "2026-04-15T19:30:00Z"
      },
      "tags": ["restaurant", "single_item"],
      "skip": false,
      "skip_reason": null
    }
  ]
}
```

## Defaults

- **Composite score weights:** macro accuracy 0.50 · item recall 0.20 · hallucination penalty 0.15 · cost 0.10 · latency 0.05
- **Tolerance bands per macro:** ±15 % green, 15-30 % yellow, > 30 % red
- **Configs:** `raw` (model alone) and `with_fatsecret` (matches the shipped backend pipeline)
- **Caching:** model responses cached per `(case, model, config, prompt_hash)` - re-running after editing ground truth is free for unchanged inputs

## Adding a new candidate model

1. Append it to `--models`.
2. Make sure `eval_lib/runners.py` has its price entry in `PRICE_TABLE`
   (input + output $ per million tokens). Eval will fail loudly if missing.
3. Re-run.

## Top up the dataset

Append new entries to `meals_groundtruth.json`. Existing verified cases
are untouched and their results are reused from cache.
