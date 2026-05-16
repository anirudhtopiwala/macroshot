"""PDF nutrition report generator using Jinja2 + WeasyPrint.

Generates a multi-page branded PDF report for Pro subscribers.
Charts rendered with matplotlib, embedded as base64 in an HTML template,
converted to PDF by WeasyPrint.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("macro_app.pdf")

# ── Brand colors (hex for CSS, tuples for matplotlib) ──────────
EMERALD = "#10b981"
BLUE = "#3b82f6"
ORANGE = "#f97316"
PURPLE = "#a855f7"
RED = "#ef4444"
AMBER = "#f59e0b"

MACRO_COLORS = {
    "calories": {"hex": EMERALD, "dot": "emerald"},
    "protein": {"hex": BLUE, "dot": "blue"},
    "carbs": {"hex": ORANGE, "dot": "orange"},
    "fat": {"hex": PURPLE, "dot": "purple"},
}


# ── Chart helpers (matplotlib -> base64 PNG) ───────────────────

def _chart_to_b64(fig) -> str:
    """Render matplotlib figure to base64 PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    buf.seek(0)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.read()).decode("ascii")


def _make_calorie_chart(days: list[dict], target_cal: float) -> str:
    """Daily calorie bar chart. Returns base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dates = [d["date"][-5:] for d in days]
    cals = [d["calories"] for d in days]

    fig, ax = plt.subplots(figsize=(7.5, 3.5))
    colors = []
    for c in cals:
        if c == 0:
            colors.append("#e2e8f0")
        elif target_cal > 0 and abs(c - target_cal) <= 200:
            colors.append(EMERALD)
        elif target_cal > 0 and c > target_cal + 200:
            colors.append(RED)
        else:
            colors.append(BLUE)

    ax.bar(dates, cals, color=colors, width=0.6, zorder=2)
    if target_cal > 0:
        ax.axhline(y=target_cal, color=AMBER, linewidth=1.5,
                    linestyle="--", label=f"Target ({int(target_cal)} kcal)", zorder=3)
        ax.legend(fontsize=7, frameon=False, loc="upper right")

    ax.tick_params(colors="#475569", labelsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.set_ylabel("kcal", fontsize=8, color="#475569")
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.5, zorder=0)

    # Rotate x labels for readability
    if len(dates) > 14:
        plt.xticks(rotation=45, ha="right")

    return _chart_to_b64(fig)


def _make_macro_pie(pct_pro: float, pct_carb: float, pct_fat: float) -> str:
    """Macro split pie chart. Returns base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sizes = [pct_pro, pct_carb, pct_fat]
    if sum(sizes) == 0:
        return ""
    labels = [f"Protein\n{pct_pro:.0f}%", f"Carbs\n{pct_carb:.0f}%", f"Fat\n{pct_fat:.0f}%"]
    colors = [BLUE, ORANGE, PURPLE]

    fig, ax = plt.subplots(figsize=(2.5, 2.5))
    ax.pie(sizes, labels=labels, colors=colors, startangle=90,
           wedgeprops={"linewidth": 2, "edgecolor": "white"},
           textprops={"fontsize": 8, "color": "#1e293b", "fontweight": "bold"})
    ax.set_aspect("equal")
    return _chart_to_b64(fig)


def _make_donut(pct: float, color: str) -> str:
    """Small donut ring chart. Returns base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    filled = min(max(pct, 0.01), 1.0)
    remaining = 1.0 - filled
    fig, ax = plt.subplots(figsize=(1.5, 1.5))
    fig.patch.set_alpha(0)
    ax.pie([filled, remaining], colors=[color, "#475569"],
           startangle=90, counterclock=False,
           wedgeprops={"width": 0.3, "edgecolor": "none", "linewidth": 0})
    ax.set_aspect("equal")

    # Percentage text in center (white for dark cover background)
    ax.text(0, 0, f"{pct*100:.0f}%", ha="center", va="center",
            fontsize=12, fontweight="bold", color="white")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    buf.seek(0)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.read()).decode("ascii")


def _make_weight_chart(weights: list[dict], goal_kg: float | None = None) -> str:
    """Weight trend line chart. Returns base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    dates = [w["logged_at"][:10] for w in weights]
    vals = [float(w["weight_kg"]) for w in weights]

    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    x = list(range(len(vals)))
    ax.plot(x, vals, color=EMERALD, linewidth=1.5, marker="o", markersize=3, zorder=3)

    if len(vals) >= 7:
        kernel = np.ones(7) / 7
        smoothed = np.convolve(vals, kernel, mode="valid")
        offset = len(vals) - len(smoothed)
        ax.plot(range(offset, len(vals)), smoothed, color=BLUE,
                linewidth=2, alpha=0.7, label="7-day avg", zorder=4)

    if goal_kg and goal_kg > 0:
        ax.axhline(y=goal_kg, color=AMBER, linewidth=1.5,
                    linestyle="--", label=f"Goal ({goal_kg:.1f} kg)", zorder=2)

    step = max(1, len(dates) // 6)
    ax.set_xticks(list(range(0, len(dates), step)))
    ax.set_xticklabels([dates[i][-5:] for i in range(0, len(dates), step)], fontsize=7)
    ax.tick_params(colors="#475569", labelsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.set_ylabel("kg", fontsize=8, color="#475569")
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.5, zorder=0)
    if goal_kg:
        ax.legend(fontsize=7, frameon=False)

    return _chart_to_b64(fig)


def _make_dow_chart(daily: dict[str, dict], target_cal: float) -> str:
    """Day-of-week calorie heatmap. Returns base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dow_totals: dict[int, list[float]] = defaultdict(list)
    for d_str, v in daily.items():
        try:
            dow_totals[date.fromisoformat(d_str).weekday()].append(v["cal"])
        except ValueError:
            continue

    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    avgs = [sum(dow_totals.get(i, [])) / max(len(dow_totals.get(i, [])), 1) for i in range(7)]

    fig, ax = plt.subplots(figsize=(7.5, 1.8))
    colors = []
    for a in avgs:
        if a == 0:
            colors.append("#e2e8f0")
        elif target_cal > 0 and abs(a - target_cal) <= 200:
            colors.append(EMERALD)
        elif target_cal > 0 and a > target_cal + 200:
            colors.append(RED)
        else:
            colors.append(BLUE)

    bars = ax.barh(labels, avgs, color=colors, height=0.5)
    if target_cal > 0:
        ax.axvline(x=target_cal, color=AMBER, linewidth=1, linestyle="--")

    for bar, val in zip(bars, avgs):
        if val > 0:
            ax.text(bar.get_width() + 20, bar.get_y() + bar.get_height() / 2,
                    f"{int(val)}", va="center", fontsize=7, color="#475569")

    ax.tick_params(colors="#475569", labelsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.invert_yaxis()
    ax.set_xlabel("avg kcal", fontsize=7, color="#475569")

    return _chart_to_b64(fig)


def _image_to_b64(path: str) -> str:
    """Read a file and return base64 string, or empty string on failure."""
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return ""


def _format_period(start_str: str, end_str: str) -> str:
    """Format date range nicely: 'Mar 5 - Apr 3, 2026'."""
    try:
        s = date.fromisoformat(start_str)
        e = date.fromisoformat(end_str)
        if s.year == e.year:
            return f"{s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}"
        return f"{s.strftime('%b %d, %Y')} - {e.strftime('%b %d, %Y')}"
    except ValueError:
        return f"{start_str} to {end_str}"


# ── Main entry point ───────────────────────────────────────────

async def generate_report(
    user: dict,
    db_path: str,
    num_days: int = 30,
) -> bytes:
    """Generate a full PDF nutrition report. Returns PDF bytes."""
    import aiosqlite
    from src.db import (
        get_daily_totals_7days,
        get_user_profile,
        get_user_prefs,
        get_user_stats,
        get_user_target,
        export_user_weights,
    )
    from src.db_pool import get_db
    from src.services import user_today_str

    today_str = await user_today_str(db_path, user["user_id"])
    today = date.fromisoformat(today_str)
    start = today - timedelta(days=num_days - 1)
    start_str = start.isoformat()

    # Fetch all data in parallel
    target, stats, profile, prefs, weights, daily_totals = await asyncio.gather(
        get_user_target(db_path, user["user_id"]),
        get_user_stats(db_path, user["user_id"], today_str=today_str),
        get_user_profile(db_path, user["user_id"]),
        get_user_prefs(db_path, user["user_id"]),
        export_user_weights(db_path, user["user_id"]),
        get_daily_totals_7days(db_path, user["user_id"], num_days=num_days, today_str=today_str),
    )

    # Fetch meals with image_path
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT logged_at, item_name, meal_description, calories, protein,
                      carbs, fat, meal_type, source, image_path
               FROM meal_logs WHERE user_id = ? AND logged_at >= ?
               ORDER BY logged_at DESC""",
            (user["user_id"], start_str),
        )).fetchall()
    meals = [dict(r) for r in rows]

    # Aggregate daily data
    daily: dict[str, dict] = defaultdict(lambda: {"cal": 0.0, "pro": 0.0, "carb": 0.0, "fat": 0.0, "count": 0})
    for m in meals:
        d = (m.get("logged_at") or "")[:10]
        daily[d]["cal"] += float(m.get("calories") or 0)
        daily[d]["pro"] += float(m.get("protein") or 0)
        daily[d]["carb"] += float(m.get("carbs") or 0)
        daily[d]["fat"] += float(m.get("fat") or 0)
        daily[d]["count"] += 1

    user_name = " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")])) or ""
    email = user.get("email", "")
    period_label = _format_period(start_str, today_str)
    units = prefs.get("units_system", "metric")
    target_cal = target["calories"] if target else 0

    # Compute period-specific averages (not 7-day from get_user_stats)
    logged_days_data = [v for v in daily.values() if v["count"] > 0]
    days_logged = len(logged_days_data)
    n_days = max(days_logged, 1)
    streak = stats.get("streak_days", 0)
    total_meals = sum(v["count"] for v in daily.values())
    avg_cal = sum(v["cal"] for v in logged_days_data) / n_days
    avg_pro = sum(v["pro"] for v in logged_days_data) / n_days
    avg_carb = sum(v["carb"] for v in logged_days_data) / n_days
    avg_fat = sum(v["fat"] for v in logged_days_data) / n_days

    # Compute period-specific macro split
    total_pro_cal = sum(v["pro"] for v in logged_days_data) * 4
    total_carb_cal = sum(v["carb"] for v in logged_days_data) * 4
    total_fat_cal = sum(v["fat"] for v in logged_days_data) * 9
    total_macro_cal = total_pro_cal + total_carb_cal + total_fat_cal
    if total_macro_cal > 0:
        period_macro_split = {
            "pct_protein": total_pro_cal / total_macro_cal * 100,
            "pct_carbs": total_carb_cal / total_macro_cal * 100,
            "pct_fat": total_fat_cal / total_macro_cal * 100,
        }
    else:
        period_macro_split = None

    project_root = str(Path(__file__).resolve().parent.parent)
    image_dir = os.path.join(project_root, "data", "images")

    # Build all template data in a thread (matplotlib is CPU-bound)
    def _build_context() -> dict:
        # Logo
        logo_path = os.path.join(project_root, "web", "public", "logo-login.png")
        logo_b64 = _image_to_b64(logo_path)

        # Charts
        cal_chart_b64 = _make_calorie_chart(daily_totals, target_cal)

        macro_split = period_macro_split
        pie_chart_b64 = ""
        if macro_split and sum(macro_split.get(k, 0) for k in ("pct_protein", "pct_carbs", "pct_fat")) > 0:
            pie_chart_b64 = _make_macro_pie(
                macro_split["pct_protein"], macro_split["pct_carbs"], macro_split["pct_fat"])

        # Donut charts for cover
        donut_charts = []
        if target:
            for label, avg, tgt_key, color in [
                ("Protein", avg_pro, "protein", BLUE),
                ("Carbs", avg_carb, "carbs", ORANGE),
                ("Fat", avg_fat, "fat", PURPLE),
            ]:
                tgt = target.get(tgt_key, 1)
                pct = avg / max(tgt, 1)
                donut_charts.append({
                    "label": label,
                    "pct_text": f"{pct*100:.0f}%",
                    "img_b64": _make_donut(pct, color),
                })

        # Weight
        weight_in_range = sorted(
            [w for w in weights if (w.get("logged_at") or "")[:10] >= start_str],
            key=lambda w: w["logged_at"],
        )
        weight_chart_b64 = ""
        weight_stats = []
        if len(weight_in_range) >= 2:
            weight_chart_b64 = _make_weight_chart(weight_in_range, profile.get("weight_goal_kg"))
            first_w = float(weight_in_range[0]["weight_kg"])
            last_w = float(weight_in_range[-1]["weight_kg"])
            delta = last_w - first_w
            weight_stats = [
                {"label": "Start", "value": f"{first_w:.1f} kg", "sub": weight_in_range[0]["logged_at"][:10], "color": "#0f172a"},
                {"label": "Current", "value": f"{last_w:.1f} kg", "sub": weight_in_range[-1]["logged_at"][:10], "color": EMERALD},
                {"label": "Change", "value": f"{delta:+.1f} kg", "sub": "", "color": EMERALD if delta <= 0 else RED},
            ]
            goal_kg = profile.get("weight_goal_kg")
            if goal_kg:
                remaining = goal_kg - last_w
                weight_stats.append({"label": "Goal", "value": f"{goal_kg:.1f} kg", "sub": f"{remaining:+.1f} kg to go", "color": AMBER})

        dow_chart_b64 = _make_dow_chart(daily, target_cal)

        # Macro rows for table
        macro_rows = []
        if target:
            for key, label, dot_class in [("calories", "Calories", "emerald"), ("protein", "Protein", "blue"), ("carbs", "Carbs", "orange"), ("fat", "Fat", "purple")]:
                avg_v = {"calories": avg_cal, "protein": avg_pro, "carbs": avg_carb, "fat": avg_fat}[key]
                tgt_v = target.get(key, 0)
                pct = avg_v / max(tgt_v, 1) * 100
                unit = " kcal" if key == "calories" else "g"
                bar_color = EMERALD if abs(pct - 100) <= 10 else (RED if pct > 110 else MACRO_COLORS[key]["hex"])
                macro_rows.append({
                    "label": label, "dot": dot_class,
                    "avg": f"{avg_v:.0f}{unit}", "target": f"{tgt_v:.0f}{unit}",
                    "pct": pct, "color": bar_color,
                })

        # Meal type distribution
        type_counts: dict[str, int] = defaultdict(int)
        type_cals: dict[str, float] = defaultdict(float)
        type_colors = {"Breakfast": EMERALD, "Lunch": BLUE, "Dinner": PURPLE, "Snack": AMBER}
        for m in meals:
            mt = (m.get("meal_type") or "other").capitalize()
            type_counts[mt] += 1
            type_cals[mt] += float(m.get("calories") or 0)
        total_count = max(sum(type_counts.values()), 1)
        meal_types = []
        all_types = ["Breakfast", "Lunch", "Dinner", "Snack"]
        # Include Other if any meals are categorized as such
        if type_counts.get("Other", 0) > 0:
            all_types.append("Other")
            type_colors["Other"] = "#64748b"
        for mt in all_types:
            cnt = type_counts.get(mt, 0)
            meal_types.append({
                "name": mt, "count": cnt,
                "avg_cal": type_cals.get(mt, 0) / max(cnt, 1),
                "pct": cnt / total_count * 100,
                "color": type_colors.get(mt, "#64748b"),
            })

        # Consistency stats
        compliance = days_logged / max(num_days, 1) * 100
        avg_meals_day = sum(v["count"] for v in daily.values()) / max(days_logged, 1)
        consistency_stats = [
            {"value": f"{days_logged}/{num_days}", "label": "Days Logged", "color": EMERALD},
            {"value": f"{compliance:.0f}%", "label": "Compliance", "color": EMERALD if compliance >= 80 else AMBER},
            {"value": str(streak), "label": "Streak", "color": PURPLE},
            {"value": f"{avg_meals_day:.1f}", "label": "Meals/Day", "color": BLUE},
        ]

        # Top foods
        food_freq: dict[str, dict] = defaultdict(lambda: {"count": 0, "cals": [], "pros": []})
        for m in meals:
            name = m.get("item_name", "Unknown")
            food_freq[name]["count"] += 1
            food_freq[name]["cals"].append(float(m.get("calories") or 0))
            food_freq[name]["pros"].append(float(m.get("protein") or 0))

        top_foods = sorted(food_freq.items(), key=lambda x: x[1]["count"], reverse=True)[:6]
        top_foods_data = [{
            "name": n, "count": d["count"],
            "avg_cal": sum(d["cals"]) / d["count"],
            "avg_pro": sum(d["pros"]) / d["count"],
        } for n, d in top_foods]

        highest_cal = sorted(meals, key=lambda m: float(m.get("calories") or 0), reverse=True)[:5]
        highest_cal_data = [{"name": m.get("item_name", ""), "cal": float(m.get("calories") or 0), "date": (m.get("logged_at") or "")[:10]} for m in highest_cal]

        highest_pro = sorted(meals, key=lambda m: float(m.get("protein") or 0), reverse=True)[:5]
        highest_pro_data = [{"name": m.get("item_name", ""), "pro": float(m.get("protein") or 0), "date": (m.get("logged_at") or "")[:10]} for m in highest_pro]

        # Food log (all days in the period - compact table format)
        by_day: dict[str, list] = defaultdict(list)
        for m in meals:
            d = (m.get("logged_at") or "")[:10]
            by_day[d].append(m)

        food_log_days = []
        for day_str in sorted(by_day.keys(), reverse=True):
            day_meals = by_day[day_str]
            day_data = {
                "date": day_str,
                "total_cal": sum(float(m.get("calories") or 0) for m in day_meals),
                "total_pro": sum(float(m.get("protein") or 0) for m in day_meals),
                "meals": [],
            }
            for m in day_meals:
                day_data["meals"].append({
                    "name": m.get("item_name") or m.get("meal_description") or "Meal",
                    "type": (m.get("meal_type") or "").capitalize(),
                    "cal": float(m.get("calories") or 0),
                    "pro": float(m.get("protein") or 0),
                    "carb": float(m.get("carbs") or 0),
                    "fat": float(m.get("fat") or 0),
                })
            food_log_days.append(day_data)

        # Calorie range stats (for filling space on calorie overview page)
        logged_day_cals = [v["cal"] for v in daily.values() if v["count"] > 0]
        cal_stats = []
        if logged_day_cals:
            min_cal = min(logged_day_cals)
            max_cal = max(logged_day_cals)
            min_day = next(d for d, v in daily.items() if v["cal"] == min_cal and v["count"] > 0)
            max_day = next(d for d, v in daily.items() if v["cal"] == max_cal)
            on_target_days = sum(1 for c in logged_day_cals if target_cal > 0 and abs(c - target_cal) <= target_cal * 0.1)
            cal_stats = [
                {"label": "Lowest Day", "value": f"{min_cal:.0f} kcal", "sub": min_day, "color": BLUE},
                {"label": "Highest Day", "value": f"{max_cal:.0f} kcal", "sub": max_day, "color": RED},
                {"label": "On Target", "value": f"{on_target_days} days", "sub": "within 10%", "color": EMERALD},
                {"label": "Total kcal", "value": f"{sum(logged_day_cals):,.0f}", "sub": f"over {len(logged_day_cals)} days", "color": AMBER},
            ]

        # Cover insight
        insight = ""
        if target_cal > 0:
            insight = f"You logged {total_meals} meals over {num_days} days, averaging {avg_cal:.0f} kcal/day ({avg_cal/target_cal*100:.0f}% of your {target_cal:.0f} target)."

        return {
            "user_name": user_name,
            "email": email,
            "period_label": period_label,
            "logo_b64": logo_b64,
            "total_meals": total_meals,
            "avg_cal": avg_cal,
            "streak": streak,
            "days_logged": days_logged,
            "num_days": num_days,
            "donut_charts": donut_charts,
            "insight": insight,
            "cal_chart_b64": cal_chart_b64,
            "macro_rows": macro_rows,
            "pie_chart_b64": pie_chart_b64,
            "weight_chart_b64": weight_chart_b64,
            "weight_stats": weight_stats,
            "dow_chart_b64": dow_chart_b64,
            "meal_types": meal_types,
            "consistency_stats": consistency_stats,
            "top_foods": top_foods_data,
            "highest_cal": highest_cal_data,
            "highest_pro": highest_pro_data,
            "food_log_days": food_log_days,
            "cal_stats": cal_stats,
        }

    ctx = await asyncio.to_thread(_build_context)

    # Render HTML template.
    # SECURITY: autoescape=True is REQUIRED. Meal names, descriptions,
    # first_name, and email are user-controlled. Without autoescape, an
    # attacker could inject arbitrary HTML or CSS into the PDF - combined
    # with WeasyPrint's URL fetcher, this becomes a local-file disclosure
    # primitive (img src="file:///etc/passwd", etc).
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(['html', 'htm', 'xml']),
    )
    template = env.get_template("report.html")
    html = template.render(**ctx)

    # Convert to PDF with WeasyPrint (in thread - CPU-bound).
    # SECURITY: A restricted URLFetcher refuses every external protocol -
    # only inline data: URIs (used for embedded base64 chart PNGs) are
    # allowed.  Without this guard, WeasyPrint would happily resolve
    # file:// URIs against a base URL, leaking .env, credentials.json,
    # or the SQLite database.  We also drop base_url entirely so relative
    # paths cannot resolve into the filesystem at all.
    def _render_pdf(html_str: str) -> bytes:
        from weasyprint import HTML, URLFetcher

        class DataOnlyURLFetcher(URLFetcher):
            """Allow only data: URIs.  Any other scheme raises."""
            def __init__(self):
                super().__init__(allowed_protocols={"data"}, allow_redirects=False)

            def fetch(self, url, headers=None):
                if not url.startswith("data:"):
                    logger.warning("PDF render blocked external URL: %s", url[:200])
                    raise ValueError("External URLs are not permitted in PDF reports")
                return super().fetch(url, headers=headers)

        return HTML(string=html_str, url_fetcher=DataOnlyURLFetcher()).write_pdf()

    return await asyncio.to_thread(_render_pdf, html)
