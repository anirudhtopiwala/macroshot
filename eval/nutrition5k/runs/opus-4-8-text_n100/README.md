# Opus 4.8 TEXT-ONLY (n=100)

E_terse/E_detailed = current TEXT_ONLY prompt; Etx_* = fixed (composite-meal) prompt.
Predictions {dish_id,5 macros}; score vs ground_truth.json (keyed by dish_id).
Result: fix helps clearly on Opus text — E_terse 92.2->Etx_terse 78.8 (-15%), E_detailed 80.7->Etx_detailed 69.7 (-14%). Detailed beats terse. GT-free sub-agent harness.
