# Meal-eval + prompt-fix — agent handoff

> Working/context doc for whoever continues this thread. Goal: an **engineering
> blog post** about MacroShot's meal-macro accuracy, built on the Nutrition5K
> eval. This is an internal working doc — **trim/remove before publishing the
> harness publicly** (it references PR numbers, costs, and `/tmp` paths).
> Last updated by the session that shipped PR #29 / #30.

---

## 1. Where everything lives

| What | Path |
|---|---|
| Product repo (public) | `/home/anirudh/macro/macroshot/` (GitHub `anirudhtopiwala/macroshot`) |
| Ops repo (private) | `/home/anirudh/macro/macro_app/` |
| Python venv (use for everything) | `/home/anirudh/macro/macroshot/.venv/bin/python` |
| **Eval harness + all experiments** | `…/macroshot/.claude/worktrees/nutrition5k-eval/eval/nutrition5k/` on branch `eval/nutrition5k-benchmark` |
| Production prompts | `macroshot/src/gemini.py` → `CONVERSATIONAL_INITIAL_PROMPT` (~L72), `TEXT_ONLY_INITIAL_PROMPT` (~L165) |

The eval is a **standalone harness** that imports the production prompts from
`src.gemini` so it tracks prod. Runner: `python -m eval.nutrition5k.src.run`.
Conditions are defined in `eval/nutrition5k/src/conditions.py`. Scoring/parsers
in `src/parsers.py` (mass_g = sum of items[].weight_g — Macroshot schema has no
top-level mass). Dataset: `data/prompts.json` (per-dish GT + 2 captions),
`data/selected_100.txt` (the 100 dish ids), `data/images/` (gitignored).

Run it:
```
cd …/nutrition5k-eval
.venv/bin/python -m eval.nutrition5k.src.run --models <model> --conditions <c1,c2>
# writes runs/<model>_<ts>/predictions/<cond>/<dish>.json + metrics + report.md
```

---

## 2. What shipped (both MERGED to public main)

- **PR #29** `fix(gemini): anchor caption portions to the plate, not standard servings`
  — the validated caption fix. Appends a block to `CONVERSATIONAL_INITIAL_PROMPT`
  **gated to the photo+text flow** ("IF THE USER ALSO DESCRIBED THE MEAL IN
  TEXT…"): caption = identity not quantity; named items SHARE the plate;
  estimate whole-plate mass top-down then divide. Photo-only is untouched.
- **PR #30** `fix(test): use asyncio.run instead of deprecated get_event_loop`
  — pre-existing CI infra bug (207 tests errored with "no current event loop"
  under the unpinned pytest-asyncio). Unrelated to the prompt work; unblocked CI.

---

## 3. The core results (Gemini 2.5 Flash-Lite = the SHIPPED model, n=100)

Run dirs under `eval/nutrition5k/runs/`:
- `gemini-2.5-flash-lite_20260607_222113/` → **X1, X3, BASELINE_unlabeled**
- `gemini-2.5-flash-lite_20260607_224646/` → **X3v2** (the fix)
- `gemini-2.5-flash-lite_20260607_230419/` → X1v3, X3v3 (iteration 2)
- `gemini-2.5-flash-lite_20260607_231430/` → X3v4 (iteration 3)
- `gemini-2.5-flash-lite_20260608_000713/` → E_terse, Etx_terse, X1b, X3q (cross-mode experiments)

AvgMAE (mean abs err over calories/mass_g/fat_g/carb_g/protein_g; lower better):

| Condition | AvgMAE | note |
|---|---|---|
| BASELINE_unlabeled (generic Wang-style, photo) | 54.5 | |
| X1 (our prompt, photo only) | 65.3 | our rich prompt is WORSE than minimal on this small model |
| X3 (our prompt + caption, pre-fix) | 83.0 | caption-inflation disaster |
| **X3v2 (our prompt + caption, FIX = shipped)** | **56.6** | recovers to ~baseline, beats photo-only |
| X3v3 / X3v4 (more changes) | 63.1 / 65.3 | iterations that REGRESSED — see §5 |

**Per-macro, BASELINE vs X3v2 (the key nuance — AvgMAE hides it):**
X3v2 WINS on the nutrition macros — calories 161→140 MAE, protein 12.3→9.3,
fat 9.6→7.4 (and median %err: cal 48→37, protein 55→40, fat 66→44). It LOSES
on mass (75→110) and carbs (14.5→16.4). The unweighted AvgMAE "tie" is because
mass is in raw grams and dominates the flat average. **Honest claim: our fixed
prompt beats the generic baseline on the macros that matter (cal/protein/fat).**

---

## 3b. Gemini 2.5 Flash (FULL) — the model Wang used (n=100)

Run dir: `runs/gemini-2.5-flash_20260608_004059/` (BASELINE_unlabeled, X1, X3v2). Cost $2.02.

| Condition | AvgMAE |
|---|---|
| BASELINE_unlabeled | 89.0 |
| X1 (our prompt, photo) | 85.6 |
| **X3v2 (our fix, photo+caption)** | **67.3** (−24% vs baseline — cleanest "our fix beats generic" on any Gemini) |

**KEY: Flash-full does NOT reproduce Wang's 45.55** (BASELINE 89 ≈ 2×). Cause:
Flash-full runs chain-of-thought (~1,800 thinking tokens) and **over-estimates
portions** (BASELINE signed bias +203 kcal / +146 g). Flash-Lite (≈0 thinking)
was conservative. So **thinking made the cheap model LESS accurate at portions**
— a real, blog-worthy finding ("more reasoning isn't free accuracy"). The
whole-plate budget in X3v2 counteracts the inflation, which is why X3v2 wins big
here. **Anchor the paper-repro on Opus (46.9 ≈ 45.55), NOT Flash-full.**
(To make Flash-full match Wang you'd re-run with thinking DISABLED — a one-line
`thinking_config` tweak in `src/client.py`; deemed unnecessary, Opus already matches.)

## 4. Frontier vs small model (Opus)

- **Opus 4.7 historical** (sub-agent harness, n=100, all 7 conditions):
  `runs/claude-opus-4-7-subagent_n100_20260527_060817/` (per_dish.csv +
  raw_outputs). Key: X1 42.3 < BASELINE 46.9 < Wang 45.55 → **on a frontier
  model our prompt beats the generic prompt AND the published paper.**
- **Opus 4.8 new** (sub-agent, n=50, X3 vs X3v2):
  `runs/opus-4-8-subagent_n50_x3_x3v2/` (predictions + ground_truth.json;
  score with a join script — NOT run.py output). Paired: X3 43.2 → X3v2 41.2
  (a wash). **The caption fix barely helps Opus** — it's a small-model failure.
  Contrast: Flash-Lite 83→56.6 (−32%) vs Opus 43→41.
- Cost gap: Flash-Lite ≈ $0.0004/analysis (measured) vs Opus ≈ $0.04–0.05 (est).

---

## 5. Conclusions (the blog's spine)

1. **Counterintuitive hook:** adding an *accurate* user caption made the small
   model ~2× worse (it read each named item as a standard serving and summed).
   Captions verified faithful (76% ingredient coverage, ~0 hallucinations).
2. **The fix:** "text identifies, image sizes" + whole-plate budget → −32% on
   Flash-Lite; now beats photo-only and wins cal/protein/fat vs the baseline.
3. **Small models punish prompt bloat.** Every attempt to add more (density
   tiers, looser caps, multi-branch rules, an explicit-quantity clause that
   never even fires) REGRESSED Flash-Lite. The minimal fix (X3v2) won. The
   explicit-quantity feature ("0.5 lb fish") regressed by 14% in-prompt →
   **must be done in code (parse + inject grams), not the prompt.**
4. **The fix helps both models (bigger on the small one).** UPDATED at n=100:
   Opus 4.8 image X3 50.8 -> X3v2 41.1 (-19%); Opus 4.8 text -14-15% too. Flash-Lite
   -32%. The n=50 "wash" was a favorable first-half artifact. So NOT small-model-only
   — but the effect is larger on the cheaper model we ship.
4b. **Chain-of-thought hurt portion accuracy.** Flash-full (thinks) over-estimated
   far more than Flash-Lite (doesn't) — BASELINE 89 vs 54.5 on identical dishes,
   +203 kcal bias. Reasoning is not free accuracy for portion estimation.
5. **Cross-mode (run `…000713`):** photo-only doesn't need the fix (budget
   no-ops without a caption, X1b 65.0 ≈ X1 65.3); text-only composite-meal fix
   is marginal/within noise at n=100 (E_terse 119.8 → Etx_terse 111.9, paired
   40/34) — revisit at larger n.

---

## 6. Narrative + decisions (locked)

Angle: **engineering**. Eval stays **n=100**. New prompt = baseline (freeze the
legacy prompt for the before/after). Publish the harness. Archive outputs.
**Narrative order:** paper repro → our prompt beats the baseline (per-macro) →
caption fix (small-model win) → Gemini vs Opus.

---

## 7. IN FLIGHT / TODO

- [x] **Gemini 2.5 Flash (full) n=100** — DONE → `runs/gemini-2.5-flash_20260608_004059/`
  (BASELINE 89.0 / X1 85.6 / X3v2 67.3; cost $2.02). See §3b. Conclusion: thinking
  inflates → doesn't repro Wang; anchor repro on Opus.
- [x] **Opus 4.8 image X3/X3v2 → n=100** (`runs/opus-4-8-subagent_n50_x3_x3v2/`, now 100 each): X3 50.8, X3v2 41.1.
- [x] **Opus 4.8 text n=100** (`runs/opus-4-8-text_n100/`): E_terse 92.2/Etx_terse 78.8/E_detailed 80.7/Etx_detailed 69.7 (fix helps).
- [x] **Consolidated dashboard** regenerated: `runs/FINAL_RESULTS.html` (all 4 models, image+text+oracle preview).
- [ ] **7-variant × 400-dish Opus-4.8 rollout (IN PROGRESS / PAUSED for go).** Variants (7):
  BASELINE_unlabeled, BASELINE_labeled, X1, X2, X3v2, Etx_terse, Etx_detailed (use FIXED prompts;
  oracle = +GT ingredient names, no macro GT). Batch 1 (10 dishes) done → `runs/opus-4-8-fullset/`.
  300 more dishes sampled (`data/selected_300.txt` + `selected_300_meta.json`, stratified 120/105/75 seed 42);
  **150/300 images fetched** (`/tmp/fetch_300.sh` stopped early — re-run for the rest; idempotent).
  Plan: 10 dishes/10min idempotent loop; each new-dish batch does Opus rotation-check+fix + Opus caption-gen
  (NO Gemini — see [[feedback_no_gemini_without_approval]]) + 7-variant eval. **Checkpoint at +100 new dishes:**
  compare to original-100 metrics; stop if converged (saves ~$380). Total ~$1k approved. Auto-pause if rate-limited;
  check `/usage` before resuming.
- [ ] ~~Expanded Opus 4.8 run (n=100)~~ (done above). Old plan note:
  BASELINE_unlabeled, X1, X3, X3v2 at n=100 → full current-frontier ladder +
  paper-repro on the CURRENT model. Use the Workflow sub-agent harness
  (one agent/dish, model:'opus'=4.8). **Slim the per-agent input** (inline the one
  system prompt + caption + image path; do NOT have agents read the fat 30KB
  dataset file — that's what made the n=50 run ~34k tokens/agent). GT-free input
  is `runs/opus-4-8-subagent_n50_x3_x3v2/agent_input_gtfree.json` (100 dishes);
  GT for scoring is `ground_truth.json` next to it. Est. cost ~$15-25 (Opus).
- [ ] **Freeze the legacy (pre-PR-#29) prompt** as a pinned eval condition — the
  eval imports the live prompt, so the before/after needs the old string frozen.
- [ ] **Cross-run figure generator** (matplotlib) for the 4 blog figures —
  no new runs needed, build against existing run dirs.
- [ ] Generalize the example-card HTML generator (was `runs/caption_examples.html`,
  regenerate via script) → render to images.
- [ ] **Archive:** commit curated `published_runs/<run>/` (per_dish.csv +
  metrics.json + report.md + prompt SHAs), GT-free, no images.
- [ ] **Publish PR:** merge `eval/nutrition5k` → public main. Before that, TIDY
  `conditions.py`: keep X1/BASELINE_unlabeled/X3/X3v2 (+ a frozen legacy);
  drop or shelve the experimental X3v3, X3v4, X1v3, X1b, Etx_terse, X3q.
- [ ] (Optional) explicit-quantity feature as a CODE change (not prompt).
- [ ] (Optional) grow Opus n=50→100; grow text-only set to confirm Etx.

---

## 8. GOTCHAS (learned the hard way)

- **GT-leak contamination:** the first Opus sub-agent run let agents read a
  dataset file that included `gt` → AvgMAE collapsed to ~14 (fake). ALWAYS feed
  agents a GT-free input (`agent_input_gtfree.json`); keep GT separate for
  scoring. Sanity-check: if many preds are within ~2 kcal of GT, you leaked.
- **run.py only calls Gemini** (google-genai client). Opus runs go through the
  **Workflow sub-agent harness** (one agent per dish, model:'opus' = 4.8, NOT
  4.7). Each agent reads the GT-free file + its image, writes a prediction JSON.
- **Workflow `args` was flaky** — inline params as consts in the script; don't
  rely on `args.x`. Top-level structured-output schema must be `object` (not
  `array`). agents wrote to a relative dir when `outDir` failed to interpolate.
- **AvgMAE over 5 macros over-weights mass_g** (raw grams). For a macro tracker,
  lead with calories + protein/fat, not the flat average.
- **Model-version confound:** Opus 4.8 ≠ 4.7 (4.8 X3 43 vs 4.7 X3 54). Always
  run the same-model baseline before claiming a prompt delta.
- **Image fetch/rotation** (for growing the set): `gsutil cp` camera_C video →
  ffmpeg frame 10 → ~48% need manual 180° rotation (`tools/rotate_images.py`).
  This is the scaling bottleneck past n=100.
- **`/tmp` is ephemeral.** Opus n=50 + datasets were persisted into
  `runs/opus-4-8-subagent_n50_x3_x3v2/`. Anything still only in `/tmp/opus_run2`,
  `/tmp/opus_*.json`, or `/tmp/*.log` will vanish on reboot.
