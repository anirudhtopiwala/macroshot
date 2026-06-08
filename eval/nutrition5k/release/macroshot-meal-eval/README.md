# MacroShot — meal-macro accuracy eval

How accurately can an LLM read **calories & macros** from a meal photo (and/or a
typed description)? This bundle benchmarks several prompting options across four
models on **Nutrition5K**, against the published baseline of **Wang et al. 2026**.

## View it

Open **`index.html`** in any browser — a self-contained dashboard with the metric
glossary, the prompting options, example prompts, and color-coded results
(headline + per-macro). No server needed.

## What's in here

| file | what it is |
|---|---|
| `index.html` | The visual dashboard (start here). |
| `metrics_summary.csv` | Tidy results: one row per `model · option · nutrient` with **MAE / RelErr% / MedPE%** + an "Average (5 nutrients)" row. |
| `per_dish_predictions.csv` | Every scored dish: `model, option, dish_id`, predicted macros, ground-truth macros, absolute errors. For full reproducibility. |
| `ground_truth.csv` | The 100 evaluation dishes: difficulty (ingredient count), and ground-truth calories/mass/fat/carbs/protein. |
| `captions.csv` | The simulated user captions per dish (terse + detailed). |
| `prompts/` | The exact prompts: generic baseline, MacroShot photo & text-only system prompts, and the caption-generation meta-prompt. |

## The options

- **Baseline** — generic minimal prompt, photo only (reproduces the paper's baseline).
- **Baseline + GT ingredients** — generic prompt given the dish's true ingredient names (a best-case reference, not a real user flow).
- **MacroShot** — MacroShot's production prompt, photo only.
- **MacroShot + user caption (terse)** — MacroShot prompt with the photo *and* a casual user caption.
- **Text-only (terse / detailed)** — the typed-description flow, no photo.

## Metrics

- **MAE** — mean |prediction − truth| in native units (kcal / grams). Primary.
- **RelErr** — mean(|p−t|/t)×100% (MAPE). Scale-free but inflated by tiny denominators.
- **MedPE** — median(|p−t|/t)×100%. Robust; "half of dishes are within X%".
- **Avg\*** — averaged over the 5 nutrients (calories, mass, fat, carbs, protein).

Wang et al. 2026 published baseline (Gemini 2.5 Flash): image-only **AvgMAE 45.55 / RelErr 161%**.

## Method & caveats

Nutrition5K (Thames et al. 2021), camera-C frame 10, **n=100** dishes stratified by
complexity (40 simple / 35 medium / 25 complex), fixed seed. Captions were
LLM-generated from each dish's ground-truth ingredient list (no exact grams/macros
leaked) and verified faithful. Frontier-model (Opus) runs use one isolated,
ground-truth-free agent per dish. Baseline is our reconstruction of Wang et al.
2026's prompt (their exact prompt isn't published).

**Caveats:** Nutrition5K is cafeteria/single-cuisine heavy; RelErr is noisy (prefer
MAE / MedPE); the "Flash-full" model runs with chain-of-thought on, which
over-estimates portions; one Flash-full Baseline dish is excluded (n=99) due to a
parse failure.

## Links
- Wang et al. 2026 — https://doi.org/10.1016/j.crfs.2026.101405
- Nutrition5K (Thames et al. 2021) — https://arxiv.org/abs/2103.03375
- MacroShot prompts — https://github.com/anirudhtopiwala/macroshot/blob/main/src/gemini.py
