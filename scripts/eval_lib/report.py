"""Render eval results as markdown + HTML.

The markdown is what you read in the terminal after a run. The HTML is
where you drill into specific failures with color-coded macro bands and
side-by-side ground-truth comparisons.
"""

from __future__ import annotations

import html
from pathlib import Path

from .metrics import AggregateMetric, CaseMetric

BAND_COLOR = {"green": "#10b981", "yellow": "#f59e0b", "red": "#ef4444"}


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"


def _fmt_ms(x: float) -> str:
    return f"{x / 1000:.2f}s" if x >= 1000 else f"{x:.0f}ms"


def render_markdown(
    aggs: list[AggregateMetric],
    cases_count: int,
    timestamp: str,
    cache_hits: int = 0,
    cache_misses: int = 0,
) -> str:
    """Top-line summary table sorted by composite score."""
    sorted_aggs = sorted(aggs, key=lambda a: a.composite_score, reverse=True)
    if not sorted_aggs:
        return f"# Eval run {timestamp}\n\nNo results.\n"

    winner = sorted_aggs[0]

    lines: list[str] = []
    lines.append(f"# Eval run {timestamp}")
    lines.append("")
    lines.append(f"**{cases_count} verified cases · {len(sorted_aggs)} configs · "
                 f"cache: {cache_hits} hits / {cache_misses} misses**")
    lines.append("")
    lines.append("| Rank | Model · Config | Composite | KcalMAPE | Recall | Halluc/case | $/case | p95 |")
    lines.append("|---:|:---|---:|---:|---:|---:|---:|---:|")
    for i, a in enumerate(sorted_aggs, 1):
        marker = " 🥇" if i == 1 else (" ← current" if a.config == "with_fatsecret" and a.model == "gemini-2.5-flash-lite" else "")
        fail_note = f" ({a.failed_count} failed)" if a.failed_count else ""
        lines.append(
            f"| {i} | `{a.model}` · `{a.config}`{marker} | "
            f"{a.composite_score:.2f} | {_fmt_pct(a.kcal_mape)} | "
            f"{_fmt_pct(a.item_recall)} | {a.halluc_per_case:.2f} | "
            f"{_fmt_usd(a.cost_per_case)} | {_fmt_ms(a.latency_p95_ms)}{fail_note} |"
        )
    lines.append("")
    lines.append(f"**Recommendation:** `{winner.model}` · `{winner.config}` "
                 f"(composite {winner.composite_score:.2f}).")
    if winner.config == "with_fatsecret":
        lines.append("")
        lines.append(f"To switch in production: set `GEMINI_MEAL_MODEL={winner.model}` "
                     f"in `.env` and `sudo systemctl restart macro_web.service`.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### Per-band breakdown")
    lines.append("")
    lines.append("| Model · Config | 🟢 green | 🟡 yellow | 🔴 red |")
    lines.append("|:---|---:|---:|---:|")
    for a in sorted_aggs:
        lines.append(f"| `{a.model}` · `{a.config}` | "
                     f"{_fmt_pct(a.green_pct)} | {_fmt_pct(a.yellow_pct)} | {_fmt_pct(a.red_pct)} |")
    lines.append("")
    return "\n".join(lines)


def render_html(
    aggs: list[AggregateMetric],
    cases_metrics: list[CaseMetric],
    timestamp: str,
) -> str:
    """Per-case drill-down with color-coded macro bands."""
    sorted_aggs = sorted(aggs, key=lambda a: a.composite_score, reverse=True)
    by_case: dict[str, list[CaseMetric]] = {}
    for cm in cases_metrics:
        by_case.setdefault(cm.case_id, []).append(cm)

    out: list[str] = []
    out.append(f"<!doctype html><html><head><title>Eval {html.escape(timestamp)}</title>")
    out.append("<style>")
    out.append("body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#111;line-height:1.4;}")
    out.append("h1,h2{margin-top:24px;}")
    out.append("table{border-collapse:collapse;width:100%;margin:8px 0;}")
    out.append("th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #eee;font-size:13px;}")
    out.append("th{background:#f5f5f7;font-weight:600;}")
    out.append("td.num{text-align:right;font-variant-numeric:tabular-nums;}")
    out.append(".pill{display:inline-block;padding:1px 6px;border-radius:8px;color:#fff;font-size:11px;font-weight:600;}")
    out.append(".case{margin:18px 0;padding:12px;background:#fafafa;border-radius:8px;}")
    out.append(".case h3{margin:0 0 8px 0;font-size:14px;}")
    out.append(".gt{color:#666;font-style:italic;}")
    out.append(".fail{color:#ef4444;font-weight:600;}")
    out.append(".tagline{color:#666;font-size:12px;}")
    out.append("</style></head><body>")

    out.append(f"<h1>Eval run {html.escape(timestamp)}</h1>")
    out.append(f"<p class='tagline'>{len(by_case)} verified cases · {len(sorted_aggs)} configs.</p>")

    out.append("<h2>Summary</h2><table>")
    out.append("<tr><th>Rank</th><th>Model · Config</th><th class='num'>Composite</th>"
               "<th class='num'>Kcal MAPE</th><th class='num'>Recall</th>"
               "<th class='num'>Halluc/case</th><th class='num'>$/case</th>"
               "<th class='num'>p95</th><th class='num'>Failed</th></tr>")
    for i, a in enumerate(sorted_aggs, 1):
        out.append(
            f"<tr><td>{i}</td><td><code>{html.escape(a.model)}</code> · <code>{html.escape(a.config)}</code></td>"
            f"<td class='num'>{a.composite_score:.2f}</td>"
            f"<td class='num'>{_fmt_pct(a.kcal_mape)}</td>"
            f"<td class='num'>{_fmt_pct(a.item_recall)}</td>"
            f"<td class='num'>{a.halluc_per_case:.2f}</td>"
            f"<td class='num'>{_fmt_usd(a.cost_per_case)}</td>"
            f"<td class='num'>{_fmt_ms(a.latency_p95_ms)}</td>"
            f"<td class='num'>{a.failed_count}</td></tr>"
        )
    out.append("</table>")

    out.append("<h2>Per-case drill-down</h2>")
    for case_id in sorted(by_case.keys()):
        out.append(f"<div class='case'><h3>{html.escape(case_id)}</h3>")
        out.append("<table><tr><th>Source</th>"
                   "<th class='num'>kcal</th><th class='num'>P</th>"
                   "<th class='num'>C</th><th class='num'>F</th>"
                   "<th>Items</th></tr>")
        # Find any non-failed metric to grab GT row from side_by_side
        metrics_for_case = by_case[case_id]
        gt_row = None
        for cm in metrics_for_case:
            if cm.side_by_side:
                gt_row = cm.side_by_side[0]
                break
        if gt_row:
            out.append(f"<tr class='gt'><td>ground truth</td>"
                       f"<td class='num'>{gt_row['calories']:.0f}</td>"
                       f"<td class='num'>{gt_row['protein']:.0f}</td>"
                       f"<td class='num'>{gt_row['carbs']:.0f}</td>"
                       f"<td class='num'>{gt_row['fat']:.0f}</td>"
                       f"<td>{', '.join(html.escape(s) for s in gt_row['items'])}</td></tr>")

        for cm in sorted(metrics_for_case, key=lambda m: (m.model, m.config)):
            if cm.failed:
                out.append(f"<tr><td><code>{html.escape(cm.model)}</code> · <code>{html.escape(cm.config)}</code></td>"
                           f"<td colspan='5' class='fail'>FAILED - {html.escape(cm.error or '')}</td></tr>")
                continue
            row = cm.side_by_side[1] if len(cm.side_by_side) > 1 else None
            if not row:
                continue
            out.append(f"<tr><td><code>{html.escape(cm.model)}</code> · <code>{html.escape(cm.config)}</code></td>")
            for k in ("calories", "protein", "carbs", "fat"):
                color = BAND_COLOR[row[f"{ {'calories':'kcal'}.get(k,k) }_band"]]
                err = row[f"{ {'calories':'kcal'}.get(k,k) }_err"] * 100
                out.append(f"<td class='num'><span class='pill' style='background:{color}'>{row[k]:.0f}</span> "
                           f"<span class='tagline'>±{err:.0f}%</span></td>")
            out.append(f"<td>{', '.join(html.escape(s) for s in row['items'])}</td></tr>")
        out.append("</table></div>")

    out.append("</body></html>")
    return "\n".join(out)


def write_reports(
    out_dir: Path,
    aggs: list[AggregateMetric],
    cases_metrics: list[CaseMetric],
    cases_count: int,
    timestamp: str,
    cache_hits: int = 0,
    cache_misses: int = 0,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(aggs, cases_count, timestamp, cache_hits, cache_misses)
    htm = render_html(aggs, cases_metrics, timestamp)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    md_path.write_text(md)
    html_path.write_text(htm)
    return md_path, html_path
