# References

## Primary comparison

**Wang et al. 2026** — comparative VLM study on Nutrition5K. Our `BASELINE_*`
conditions reproduce their nutrient-estimation methodology on the same dish set.

> Wang S, Yin J, Liu G, Yang L, Ren K, Tang R, Ge J, Yang Z, Zhao Y, Wang W.
> A comparative study of vision–language models for food ingredient
> recognition and nutrient estimation.
> *Current Research in Food Science* 12 (2026) 101405.
> DOI: [10.1016/j.crfs.2026.101405](https://doi.org/10.1016/j.crfs.2026.101405)
> PMC: [PMC13092701](https://pmc.ncbi.nlm.nih.gov/articles/PMC13092701/)

Headline numbers we compare against (Gemini 2.5 Flash, n=3466 Nutrition5K dishes):

| Setting | AvgMAE | AvgRelErr |
|---------|-------:|----------:|
| Image only, no ingredient labels (Fig 3a, Table 4) | 45.55 | 161.19% |
| Image + ground-truth ingredient labels (Fig 3b, Table 4) | 44.12 | 138.95% |

Per-nutrient RelErr (Table 5, Gemini 2.5 Flash):

| Nutrient | w/o Ingre. | w/ Ingre. |
|----------|-----------:|----------:|
| Mass | 47.12% | 49.31% |
| Calories | 92.57% | 89.04% |
| Carb | 90.07% | 76.87% |
| Fat | 482.32% | 417.05% |
| Protein | 93.85% | 62.51% |

Settings reported in Wang et al. 2026 §2.2.3: temperature 0.2, max tokens
4096 (8192 reasoning budget specifically for Gemini Flash), OpenAI-compatible
API. Frame #10 of camera C side-angle video used per dish.

## Underlying dataset

**Thames et al. 2021** — Nutrition5K.

> Thames Q, Karpur A, Norris W, Xia F, Panait L, Weyand T, Sim J.
> Nutrition5k: Towards Automatic Nutritional Understanding of Generic Food.
> *CVPR 2021*. arXiv:[2103.03375](https://arxiv.org/abs/2103.03375).

5,006 plates from Google cafeterias; per-dish ground-truth ingredient masses,
total calories, total mass, fat/carb/protein. 4 side-angle videos + overhead
RGB-D imagery (when available). Dataset bucket:
`gs://nutrition5k_dataset/nutrition5k_dataset/`.
