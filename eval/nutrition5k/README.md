# Nutrition5k benchmark

Offline benchmark of Macroshot's meal-analysis prompt vs the published
baseline from Wang et al. 2026 (see [REFERENCES.md](./REFERENCES.md)),
running on the Nutrition5K dataset.

The eval is intentionally standalone: it imports the Macroshot **system
prompts** from `src.gemini` (so it always tracks production), but uses its
own thin google-genai client — no Macroshot pipeline dependencies. This
lets us run the same eval after any prompt edit without untangling the
production code.

## Quick start

```bash
# from macroshot project root
.venv/bin/python -m eval.nutrition5k.src.run \
    --models gemini-2.5-flash,gemini-3-flash-preview,gemini-3.5-flash \
    --dishes dish_1562871537,dish_1566931620
```

Each invocation writes a timestamped run dir to `eval/nutrition5k/runs/`
containing the per-prediction JSON, aggregate metrics, and a markdown
report you can open directly.

## Conditions

| ID | What it tests |
|----|---------------|
| `X1` | Macroshot system prompt + side-angle image, no user text |
| `X2` | Macroshot system prompt + side-angle image + GT ingredient list |
| `X3` | Macroshot system prompt + image + natural-language user caption (no exact grams) |
| `X3v2` | `X3` + the caption fix (text=identity, image=portion, whole-plate mass budget) |
| `E_terse` | Macroshot text-only prompt + short user-style description |
| `E_detailed` | Macroshot text-only prompt + longer user-style description |
| `BASELINE_unlabeled` | Wang et al. 2026 reproduction (minimal prompt + image, no ingredients) |
| `BASELINE_labeled` | Wang et al. 2026 reproduction (minimal prompt + image + GT ingredients) |

Apples-to-apples: `X1` vs `BASELINE_unlabeled`, and `X2` vs `BASELINE_labeled`,
isolate **prompt style** as the only variable; `X3` vs `X3v2` isolates the
caption fix. `src/conditions.py` also defines a handful of experimental
iterations (`X3v3`, `X3v4`, `X1b`, `Etx_*`, `X3q`) used during prompt
development — see their descriptions there.

## Metrics

From Wang et al. 2026 eqs 10–13, with median variants added for robustness:

- `MAE_x` = mean absolute error per nutrient
- `MedianAE_x` = median absolute error (added; not in Wang 2026)
- `RelErr_x` = mean of `|y - ŷ| / |y|` × 100% (same as MAPE)
- `MedianPE_x` = median of same (added)
- `AvgMAE`, `AvgRelErr`, `AvgMedianAE`, `AvgMedianPE` = mean across the 5 metrics

## Reproducing Wang 2026's setup

`BASELINE_unlabeled` and `BASELINE_labeled` use:
- View C (side-angle camera) frame 10 — same as Wang 2026 §2.2
- Temperature 0.1, max_tokens 8192 (Wang 2026 used 0.2/4096; minor deviation
  — see `client.py` docstring)
- Minimal user prompt asking for the 5 nutrient values in JSON

Wang 2026's published headline (Gemini 2.5 Flash, n=3466):
- Image only: AvgMAE 45.55, AvgRelErr 161.19%
- Image + ingredients: AvgMAE 44.12, AvgRelErr 138.95%

## Provenance & reproducibility

Each run dir stores:
- `config.json` — model, settings, dish IDs, **sha256 of every system
  prompt used** (so a future prompt edit doesn't silently invalidate old
  results)
- `predictions/<condition>/<dish>.json` — full system prompt sent,
  user text, image path, raw model response, parsed values, errors,
  latency, token counts (including Gemini's `thoughts_token_count`)
- `metrics.json` — aggregated MAE/RelErr/MedianAE/MedianPE per condition
- `report.md` — human-readable summary with collapsible per-(dish, condition)
  drill-downs

## Data

`data/prompts.json` is tracked: per-dish ground truth from Nutrition5K
metadata CSVs, plus two human-written text descriptions per dish (terse
and detailed) for the text-only conditions.

`data/images/` is gitignored. The whole dataset regenerates from three
scripts under `tools/` (run from this directory):

```bash
python3 tools/select_dishes.py 500       # stratified dish selection -> data/selected_500.txt
python3 tools/fetch_extract_images.py    # download View C video from the public bucket, extract frame 10
python3 tools/build_prompts.py 500       # rebuild data/prompts.json (ground truth + ingredients)
```

`select_dishes.py` and `build_prompts.py` read the Nutrition5K
`dish_metadata_cafe{1,2}.csv` files; point them at your local copy with
`NUTRITION5K_METADATA_DIR` (defaults to `data/metadata/`). Images come
from the public GCS bucket over plain HTTPS — no auth, no cost. To pull a
single frame by hand instead:

```bash
gsutil cp "gs://nutrition5k_dataset/nutrition5k_dataset/imagery/side_angles/<dish>/camera_C.h264" /tmp/
ffmpeg -loglevel error -framerate 30 -i /tmp/camera_C.h264 \
    -vf "select=eq(n\,9)" -vframes 1 -q:v 2 \
    data/images/<dish>_view_c.jpg
```
