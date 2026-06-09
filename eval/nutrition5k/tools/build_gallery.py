#!/usr/bin/env python3
"""Per-dish gallery: the meals every model reads well, and the ones they all miss.

Ranks dishes by the mean absolute calorie error across models on the shipped
flow (macroshot_cam_text_terse = photo + terse user caption), then renders the
best 5 and worst 5 - the View C photo, the user caption, ground truth, and each
model's predicted macros. Images are embedded as base64 thumbnails (the
Nutrition5K frames are public). Writes runs/gallery.html.

Run:  .venv/bin/python -m eval.nutrition5k.tools.build_gallery
"""
import json, os, glob, base64, io, statistics as s, html
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../nutrition5k
os.chdir(HERE)

COND = "macroshot_cam_text_terse"
M = ["calories", "mass_g", "fat_g", "carb_g", "protein_g"]
LBL = {"calories": "Calories", "mass_g": "Mass", "fat_g": "Fat", "carb_g": "Carbs", "protein_g": "Protein"}
UNIT = {"calories": "kcal", "mass_g": "g", "fat_g": "g", "carb_g": "g", "protein_g": "g"}
GLOB = {"Gemini 2.5 Flash-Lite": "runs/gemini-2.5-flash-lite_*",
        "Gemini 2.5 Flash": "runs/gemini-2.5-flash_2*",
        "Claude Opus 4.7": "runs/opus-4-7-*",
        "Claude Opus 4.8": "runs/opus-4-8-*"}
MODELS = list(GLOB)
esc = lambda t: html.escape(str(t))

P = json.load(open("data/prompts.json"))
ds = P["dishes"] if isinstance(P, dict) and "dishes" in P else P
Dm = ds if isinstance(ds, dict) else {x["dish_id"]: x for x in ds}
GT = {d: Dm[d]["ground_truth"]["totals"] for d in Dm}


def num(x):
    return isinstance(x, (int, float))


def preds_for(globpat):
    """dish_id -> parsed prediction, from the run dir with the most scoreable preds."""
    best = {}
    for run in glob.glob(globpat):
        cur = {}
        for f in glob.glob(f"{run}/predictions/{COND}/*.json"):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            did = os.path.basename(f)[:-5]
            p = d.get("parsed") if isinstance(d.get("parsed"), dict) else d
            if did in GT and all(num(p.get(m)) for m in M):
                cur[did] = {m: p[m] for m in M}
        if len(cur) > len(best):
            best = cur
    return best


MP = {model: preds_for(g) for model, g in GLOB.items()}

# Rank dishes that ALL models predicted, by mean |cal% err| across models.
ERRM = ["calories", "fat_g", "carb_g", "protein_g"]  # macros shown with signed % error (mass shown plain)
common = set.intersection(*[set(MP[m]) for m in MODELS]) if all(MP.values()) else set()
def macro_err(did, model):
    return s.mean(abs(MP[model][did][m] - GT[did][m]) / GT[did][m] * 100 for m in ERRM if GT[did][m])
ranked = sorted(common, key=lambda d: s.mean(macro_err(d, m) for m in MODELS))
best5, worst5 = ranked[:5], ranked[-5:][::-1]


def thumb(did, w=320):
    path = f"data/images/{did}_view_c.jpg"
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGB")
    im.thumbnail((w, w * 2))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78)
    return base64.b64encode(buf.getvalue()).decode()


def signed_pct(pred, gt):
    return None if not gt else (pred - gt) / gt * 100


def errcls(p):
    if p is None:
        return "na"
    a = abs(p)
    return "g" if a <= 15 else ("y" if a <= 35 else "r")


def cell(m, val, gt, is_gt):
    """GT and Mass show only the value; the four macros also show signed % error."""
    if is_gt or m == "mass_g":
        return f"<td>{round(val)}<span class=u> {UNIT[m]}</span></td>"
    e = signed_pct(val, gt[m])
    es = "" if e is None else f" <span class='e {errcls(e)}'>{e:+.0f}%</span>"
    return f"<td>{round(val)}{es}</td>"


def row(label, vals, gt, cls=""):
    cells = "".join(cell(m, vals[m], gt, cls == "gt") for m in M)
    return f"<tr class='{cls}'><td class=l>{label}</td>{cells}</tr>"


def card(did):
    gt = GT[did]
    cap = Dm[did].get("text_descriptions", {}).get("terse", "")
    b64 = thumb(did)
    img = f"<img alt='{did}' src='data:image/jpeg;base64,{b64}'>" if b64 else "<div class=noimg>no image</div>"
    rows = row("Ground truth", gt, gt, cls="gt")
    for m in MODELS:
        if did in MP[m]:
            rows += row(m, MP[m][did], gt)
    head = "".join(f"<th>{LBL[m]}</th>" for m in M)
    cap_html = f"<div class=cap>&ldquo;{esc(cap)}&rdquo;</div>" if cap else ""
    return (f"<div class=card>{img}<div class=meta><div class=did>{esc(did)}</div>{cap_html}"
            f"<table><thead><tr><th>source</th>{head}</tr></thead><tbody>{rows}</tbody></table></div></div>")


def section(title, sub, dishes):
    cards = "".join(card(d) for d in dishes)
    return f"<h2>{title}</h2><p class=sub>{sub}</p>{cards}"


CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#272c36;--mut:#8b93a7;--fg:#e7eaf0;--g:#34d399;--y:#fbbf24;--r:#f87171;--blue:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1020px;margin:0 auto;padding:36px 22px 100px}h1{font-size:28px;margin:0 0 6px}
h2{font-size:19px;margin:40px 0 6px;border-bottom:1px solid var(--line);padding-bottom:7px}
.sub{color:var(--mut);max-width:860px}a{color:var(--blue)}
.card{display:flex;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0}
.card img{width:240px;height:auto;border-radius:7px;object-fit:cover;align-self:flex-start}
.noimg{width:240px;height:160px;display:flex;align-items:center;justify-content:center;color:var(--mut);background:#0c0e12;border-radius:7px}
.meta{flex:1;min-width:0}.did{font:12px ui-monospace,Menlo,monospace;color:var(--mut)}
.cap{font-style:italic;color:#cdd6ea;margin:2px 0 8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;text-align:right;border-bottom:1px solid #20242d;font-size:13px}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px}
td.l{color:#cdd6ea}.u{color:var(--mut);font-size:10px}tr.gt td{color:#fff;font-weight:600;background:rgba(122,162,255,.07)}
.e{font-size:11px;font-weight:600}.e.g{color:var(--g)}.e.y{color:var(--y)}.e.r{color:var(--r)}.e.na{color:var(--mut)}
@media(max-width:680px){.card{flex-direction:column}.card img,.noimg{width:100%}table{display:block;overflow-x:auto}}
"""

H = [
    "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
    "<title>MacroShot - per-dish gallery</title><style>" + CSS + "</style></head><body><div class=wrap>",
    "<h1>Per-dish gallery - best &amp; worst</h1>",
    "<p class=sub><a href='index.html'>&larr; back to the accuracy dashboard</a></p>",
    f"<p class=sub>Each model's read on the <b>shipped flow</b> (photo + a terse user caption) vs ground truth, "
    f"for the dishes the models collectively get <b>closest</b> and <b>furthest</b>. "
    f"Each macro cell shows the value and its <b>signed % error</b> (+ = over-estimate): "
    f"<span style='color:var(--g)'>&le;15%</span> / <span style='color:var(--y)'>&le;35%</span> / "
    f"<span style='color:var(--r)'>&gt;35%</span>. Ranked by mean error across calories, fat, carbs &amp; protein "
    f"over {len(common)} dishes all {len(MODELS)} models scored; mass shown as grams.</p>",
    section("Best 5 - models nail these", "Simple, well-separated plates where the photo + caption pin the portions.", best5),
    section("Worst 5 - every model misses", "Dense, mixed, or visually ambiguous plates where portion size is hard to read.", worst5),
    f"<p class=sub style='margin-top:34px'>Nutrition5K camera-C frame 10. Condition <code>{COND}</code>. "
    f"GT from <code>data/prompts.json</code>.</p>",
    "</div></body></html>",
]
out = "\n".join(H)
out = out.replace(" &mdash; ", " - ").replace("&mdash;", " - ").replace("&ndash;", "-")
open("runs/gallery.html", "w").write(out)
print(f"wrote runs/gallery.html - best5={best5} worst5={worst5} (n_common={len(common)})")
