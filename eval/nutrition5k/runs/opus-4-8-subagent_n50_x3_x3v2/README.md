# Opus 4.8 sub-agent run (n=50), X3 vs X3v2

Predictions produced by the Workflow sub-agent harness (Opus 4.8), GT-free input,
one agent per (condition,dish). Each prediction file is {dish_id, calories,
mass_g, fat_g, carb_g, protein_g}. Score against `ground_truth.json` (keyed by
dish_id). NOT produced by run.py (which can only call Gemini), so there is no
metrics.json/report.md here — score with a small join script.

Headline (n=50, paired): X3 AvgMAE 43.2 -> X3v2 41.2 (a wash; the caption fix is
a small-model phenomenon, see BLOG_HANDOFF.md).
