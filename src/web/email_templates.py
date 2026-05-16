"""Branded HTML + plain-text templates for onboarding & engagement emails.

Design: matches the PIN email style from auth.py - table-based layout,
inline styles only, dark-on-light, 560px max width, emerald accent.
From address uses "{OPERATOR_FIRST_NAME} from MacroShot" for the solo-dev
personal touch when the operator has set OPERATOR_FIRST_NAME.

Email keys (stored in email_engagement table):
  welcome        - immediate on signup
  onboarding_d2  - day 2  (activity-branched)
  onboarding_d5  - day 5  (feature highlight, only if < 5 meals)
  onboarding_d7  - day 7  (activity-branched)
  reengage_d5    - 5 days inactive (post-onboarding only)
  sunset_d14     - 14 days inactive (graceful goodbye)

A10: every interpolation of a user-controlled string (`name`, etc.) is
HTML-escaped via _esc(). Adds defense-in-depth alongside the schema-level
character class enforced in src/web/schemas.py.
"""

import html
import os


def _esc(value) -> str:
    """HTML-escape a value for safe interpolation into an email template."""
    return html.escape(str(value), quote=True)

APP_URL = os.environ.get("APP_URL", "")
ICON_URL = f"{APP_URL}icons/icon-192.png" if APP_URL else ""
UNSUBSCRIBE_URL = f"{APP_URL}settings" if APP_URL else ""
_APP_DOMAIN_TEXT = (
    APP_URL.replace("https://", "").replace("http://", "").split("/")[0]
    if APP_URL else "MacroShot"
)
# Name used in email sign-offs ("Cheers,\n{SIGNOFF_NAME}"). Falls back to
# the brand so unconfigured instances don't sign with an empty string.
SIGNOFF_NAME = os.environ.get("OPERATOR_FIRST_NAME", "").strip() or "the MacroShot team"

# ── Shared layout ──────────────────────────────────────────────────────


def _wrap_html(preheader: str, body_rows: str) -> str:
    """Wrap email body rows in the branded outer shell."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MacroShot</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}{"&zwnj;&nbsp;" * 80}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5f7;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:16px;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(15,23,42,0.04);overflow:hidden;">
<!-- Logo header -->
<tr><td style="padding:32px 40px 8px;text-align:center;">
<a href="{APP_URL}" style="text-decoration:none;color:#0f172a;">
<img src="{ICON_URL}" width="64" height="64" alt="MacroShot"
     style="display:block;margin:0 auto 12px;width:64px;height:64px;border:0;border-radius:14px;outline:none;">
<div style="font-size:22px;font-weight:700;letter-spacing:0.3px;color:#0f172a;">MacroShot</div>
</a>
</td></tr>
{body_rows}
<!-- Footer -->
<tr><td style="padding:20px 40px;background:#f8fafc;border-top:1px solid #e5e7eb;text-align:center;">
<p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;">
MacroShot &middot; AI-powered meal tracking<br>
<a href="{APP_URL}" style="color:#10b981;text-decoration:none;">{_APP_DOMAIN_TEXT}</a><br>
<a href="{UNSUBSCRIBE_URL}" style="color:#94a3b8;text-decoration:underline;font-size:11px;">Unsubscribe</a>
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _cta_button(text: str, url: str = APP_URL) -> str:
    """Render a centered emerald CTA button.

    Wrapped in a <td> with background-color so Outlook desktop (which
    strips padding from <a> tags) still shows a colored button.
    """
    return f"""\
<tr><td style="padding:8px 40px 24px;text-align:center;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
<tr><td style="background:#10b981;border-radius:10px;text-align:center;">
<a href="{url}" style="display:inline-block;background:#10b981;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;padding:14px 32px;border-radius:10px;letter-spacing:0.3px;">
{text}
</a>
</td></tr></table>
</td></tr>"""


def _heading(text: str) -> str:
    return f"""\
<tr><td style="padding:24px 40px 4px;text-align:center;">
<h1 style="margin:0;font-size:20px;font-weight:600;color:#0f172a;line-height:1.3;">{text}</h1>
</td></tr>"""


def _paragraph(text: str) -> str:
    return f"""\
<tr><td style="padding:8px 40px;text-align:left;">
<p style="margin:0;font-size:15px;line-height:1.6;color:#475569;">{text}</p>
</td></tr>"""


def _step_list(steps: list[tuple[str, str]]) -> str:
    """Render numbered steps with emoji icons."""
    rows = []
    for emoji, text in steps:
        rows.append(
            f'<tr><td style="padding:6px 40px;text-align:left;">'
            f'<p style="margin:0;font-size:15px;line-height:1.6;color:#475569;">'
            f'<span style="font-size:18px;margin-right:8px;">{emoji}</span>{text}</p>'
            f'</td></tr>'
        )
    return "\n".join(rows)


def _spacer(px: int = 16) -> str:
    return f'<tr><td style="padding:{px}px 0 0;"></td></tr>'


def _feature_card(emoji: str, title: str, desc: str) -> str:
    """A highlighted feature card with emerald left border."""
    return f"""\
<tr><td style="padding:8px 40px;">
<div style="background:#f0fdf4;border-left:4px solid #10b981;border-radius:8px;padding:16px 20px;">
<p style="margin:0 0 4px;font-size:16px;font-weight:600;color:#0f172a;">
<span style="margin-right:6px;">{emoji}</span>{title}</p>
<p style="margin:0;font-size:14px;line-height:1.5;color:#475569;">{desc}</p>
</div>
</td></tr>"""


# ── Email 1: Welcome (immediate) ──────────────────────────────────────


def welcome_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"Welcome to MacroShot, {greeting}!"),
        _paragraph(
            "You're all set to start tracking your meals with AI. "
            "No food databases to search, no manual entry - just snap a photo "
            "and we'll handle the rest."
        ),
        _spacer(8),
        _step_list([
            ("\U0001f4f8", "<strong>Snap a photo</strong> of your meal"),
            ("\u2728", "<strong>AI analyzes</strong> calories, protein, carbs & fat"),
            ("\u2705", "<strong>Review & accept</strong> - done in seconds"),
        ]),
        _spacer(8),
        _cta_button("Log your first meal"),
        _paragraph(
            "<em style='color:#94a3b8;font-size:13px;'>"
            "We'll send a few tips this week to help you get the most out of MacroShot, "
            "then we'll back off. You can unsubscribe anytime from Settings.</em>"
        ),
        _spacer(8),
        _paragraph(
            "Cheers,<br>"
            f"<strong>{SIGNOFF_NAME}</strong>"
        ),
    ])
    return _wrap_html(
        f"Welcome to MacroShot, {greeting}! Log your first meal in seconds.",
        body,
    )


def welcome_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"Welcome to MacroShot, {greeting}!\n\n"
        "You're all set to start tracking your meals with AI.\n\n"
        "Here's how it works:\n"
        "1. Snap a photo of your meal\n"
        "2. AI analyzes calories, protein, carbs & fat\n"
        "3. Review & accept - done in seconds\n\n"
        f"Log your first meal: {APP_URL}\n\n"
        "We'll send a few tips this week, then we'll back off.\n"
        "You can unsubscribe anytime from Settings.\n\n"
        f"Cheers,\n{SIGNOFF_NAME}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 2: Day 2 - active variant ───────────────────────────────────


def day2_active_html(name: str, meal_count: int) -> str:
    greeting = _esc(name) if name else "there"
    meal_count = int(meal_count)  # A10: numeric coercion - defense-in-depth.
    body = "\n".join([
        _heading(f"Nice start, {greeting}!"),
        _paragraph(
            f"You've already logged <strong>{meal_count} meal{'s' if meal_count != 1 else ''}</strong> "
            "- that's the hardest part done. Here's a tip to make things even faster:"
        ),
        _feature_card(
            "\u2328\ufe0f",
            "Text-only logging",
            "No photo? No problem. Just type what you ate - "
            "\"chicken rice bowl\" or \"2 eggs and toast\" - and the AI figures out the rest.",
        ),
        _spacer(4),
        _cta_button("Log a meal"),
        _paragraph(
            "<em style='color:#94a3b8;font-size:13px;'>"
            "Tip: consistency beats perfection. Even logging one meal a day "
            "gives you useful data over time.</em>"
        ),
    ])
    return _wrap_html(
        f"You've logged {meal_count} meals already - here's a tip to go faster.",
        body,
    )


def day2_active_text(name: str, meal_count: int) -> str:
    greeting = name or "there"
    return (
        f"Nice start, {greeting}!\n\n"
        f"You've already logged {meal_count} meal{'s' if meal_count != 1 else ''} "
        "- that's the hardest part done.\n\n"
        "Tip: Text-only logging\n"
        "No photo? No problem. Just type what you ate - "
        "\"chicken rice bowl\" or \"2 eggs and toast\" - "
        "and the AI figures out the rest.\n\n"
        f"Log a meal: {APP_URL}\n\n"
        "Consistency beats perfection. Even logging one meal a day "
        "gives you useful data.\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 2: Day 2 - inactive variant ─────────────────────────────────


def day2_inactive_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"Your first meal is waiting, {greeting}"),
        _paragraph(
            "You signed up for MacroShot but haven't logged a meal yet. "
            "That's okay - here's how quick it is:"
        ),
        _spacer(8),
        _step_list([
            ("\U0001f4f8", "Open the app and tap <strong>Log Meal</strong>"),
            ("\U0001f4f1", "Take a photo of whatever you're eating right now"),
            ("\U0001f389", "See your calories & macros in seconds"),
        ]),
        _spacer(4),
        _paragraph(
            "The whole thing takes about 10 seconds. No searching through food databases, "
            "no guessing portion sizes - just point and shoot."
        ),
        _cta_button("Try it now"),
    ])
    return _wrap_html(
        "Your first meal is just one photo away.",
        body,
    )


def day2_inactive_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"Your first meal is waiting, {greeting}\n\n"
        "You signed up for MacroShot but haven't logged a meal yet.\n"
        "That's okay - here's how quick it is:\n\n"
        "1. Open the app and tap Log Meal\n"
        "2. Take a photo of whatever you're eating\n"
        "3. See your calories & macros in seconds\n\n"
        "The whole thing takes about 10 seconds. No food databases, "
        "no guessing portions.\n\n"
        f"Try it now: {APP_URL}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 3: Day 5 - feature highlight ────────────────────────────────


def day5_feature_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"The feature most people miss, {greeting}"),
        _paragraph(
            "Most MacroShot users stick to photo logging - but there are "
            "two more features that can save you serious time:"
        ),
        _spacer(4),
        _feature_card(
            "\U0001f516",
            "Saved Meals",
            "Eat the same breakfast often? Save it as a shortcut and log it "
            "in one tap next time. No photo needed.",
        ),
        _feature_card(
            "\U0001f4ca",
            "Daily Dashboard",
            "See your calories, protein, carbs & fat at a glance - plus how much "
            "room you have left for the day.",
        ),
        _spacer(4),
        _cta_button("Open MacroShot"),
        _paragraph(
            "<em style='color:#94a3b8;font-size:13px;'>"
            "Reply to this email if you have questions or feedback - "
            "we read every reply and act fast.</em>"
        ),
    ])
    return _wrap_html(
        "Two features that save you time - saved meals & your daily dashboard.",
        body,
    )


def day5_feature_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"The feature most people miss, {greeting}\n\n"
        "Most users stick to photo logging - but there's more:\n\n"
        "1. Saved Meals\n"
        "Eat the same breakfast often? Save it as a shortcut "
        "and log it in one tap. No photo needed.\n\n"
        "2. Daily Dashboard\n"
        "See your calories, protein, carbs & fat at a glance - "
        "plus how much room you have left.\n\n"
        f"Open MacroShot: {APP_URL}\n\n"
        "Reply to this email if you have questions - we read every reply and act fast.\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 4: Day 7 - active variant ───────────────────────────────────


def day7_active_html(name: str, meal_count: int, streak: int) -> str:
    greeting = _esc(name) if name else "there"
    meal_count = int(meal_count)  # A10
    streak = int(streak)
    streak_line = (
        f"a <strong>{streak}-day streak</strong>"
        if streak > 1
        else "a solid first week"
    )
    body = "\n".join([
        _heading(f"One week in - nice work, {greeting}!"),
        _paragraph(
            f"You've logged <strong>{meal_count} meals</strong> and built {streak_line}. "
            "That puts you ahead of most people who try meal tracking."
        ),
        _spacer(4),
        _paragraph(
            "Most people who make it past the first week end up sticking around "
            "&mdash; and you're already there."
        ),
        _spacer(4),
        _paragraph(
            "<strong>Quick ask:</strong> Hit reply and tell me one thing you'd "
            "improve about MacroShot. We act fast on feedback."
        ),
        _cta_button("Keep tracking"),
    ])
    return _wrap_html(
        f"One week in - {meal_count} meals logged! You're building a real habit.",
        body,
    )


def day7_active_text(name: str, meal_count: int, streak: int) -> str:
    greeting = name or "there"
    streak_line = (
        f"a {streak}-day streak" if streak > 1 else "a solid first week"
    )
    return (
        f"One week in - nice work, {greeting}!\n\n"
        f"You've logged {meal_count} meals and built {streak_line}. "
        "That puts you ahead of most people who try meal tracking.\n\n"
        "Most people who make it past the first week end up sticking around "
        "- and you're already there.\n\n"
        "Quick ask: Reply to this email and tell me one thing you'd "
        "improve about MacroShot. We act fast on feedback.\n\n"
        f"Keep tracking: {APP_URL}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 4: Day 7 - inactive variant ─────────────────────────────────


def day7_inactive_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"Still here for you, {greeting}"),
        _paragraph(
            "It's been a week since you signed up, and we haven't seen any meals "
            "logged yet. No judgment - life gets busy."
        ),
        _spacer(4),
        _paragraph(
            "Whenever you're ready, logging your next meal is one photo away. "
            "Even tracking just one meal a day gives you useful data."
        ),
        _spacer(4),
        _paragraph(
            "<strong>Is something not working?</strong> Reply to this email and "
            "let us know - we move fast and can usually fix things same-day."
        ),
        _cta_button("Log a meal"),
    ])
    return _wrap_html(
        "Your next meal is one photo away - whenever you're ready.",
        body,
    )


def day7_inactive_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"Still here for you, {greeting}\n\n"
        "It's been a week since you signed up and we haven't seen "
        "any meals logged yet. No judgment - life gets busy.\n\n"
        "Whenever you're ready, logging your next meal is one photo away. "
        "Even tracking just one meal a day gives you useful data.\n\n"
        "Is something not working? Reply to this email and let us know "
        "- we move fast and can usually fix things same-day.\n\n"
        f"Log a meal: {APP_URL}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 5: Re-engagement (5 days inactive, post-onboarding) ─────────


def reengage_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"Ready to pick back up, {greeting}?"),
        _paragraph(
            "It's been a few days since your last meal log. "
            "That's totally normal - tracking doesn't have to be all-or-nothing."
        ),
        _spacer(4),
        _paragraph(
            "Even logging <strong>one meal today</strong> keeps the habit alive and "
            "gives you data you can actually use."
        ),
        _feature_card(
            "\U0001f4a1",
            "Quick tip",
            "Save your go-to meals as shortcuts - one tap to log them next time, "
            "no photo or typing needed.",
        ),
        _spacer(4),
        _cta_button("Log a meal"),
    ])
    return _wrap_html(
        "Ready to pick back up? One meal is all it takes.",
        body,
    )


def reengage_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"Ready to pick back up, {greeting}?\n\n"
        "It's been a few days since your last meal log. "
        "That's totally normal - tracking doesn't have to be all-or-nothing.\n\n"
        "Even logging one meal today keeps the habit alive.\n\n"
        "Quick tip: Save your go-to meals as shortcuts - one tap to log "
        "them next time, no photo or typing needed.\n\n"
        f"Log a meal: {APP_URL}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Email 6: Sunset (14 days inactive - graceful goodbye) ─────────────


def sunset_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"We'll stop emailing, {greeting}"),
        _paragraph(
            "It's been a couple of weeks and it seems like now isn't the right time "
            "for meal tracking. That's completely okay."
        ),
        _spacer(4),
        _paragraph(
            "This is the last email we'll send. Your account is still here "
            "whenever you want to come back - just open the app and pick up "
            "where you left off."
        ),
        _spacer(4),
        _paragraph(
            "If you have a moment, I'd love to know what held you back. "
            "Was it the app, the timing, or something else? A quick reply "
            "helps me make MacroShot better for everyone."
        ),
        _cta_button("Come back anytime"),
        _paragraph(
            "Wishing you well,<br>"
            f"<strong>{SIGNOFF_NAME}</strong>"
        ),
    ])
    return _wrap_html(
        "This is our last email - your account is here whenever you're ready.",
        body,
    )


def sunset_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"We'll stop emailing, {greeting}\n\n"
        "It's been a couple of weeks and it seems like now isn't "
        "the right time for meal tracking. That's completely okay.\n\n"
        "This is the last email we'll send. Your account is still "
        "here whenever you want to come back.\n\n"
        "If you have a moment, I'd love to know what held you back. "
        "A quick reply helps me make MacroShot better for everyone.\n\n"
        f"Come back anytime: {APP_URL}\n\n"
        f"Wishing you well,\n{SIGNOFF_NAME}\n\n"
        f"- MacroShot · {APP_URL}\n"
        f"Unsubscribe: {UNSUBSCRIBE_URL}\n"
    )


# ── Waitlist confirmation (sent when beta cap is hit) ─────────────────


def waitlist_confirmation_html(name: str) -> str:
    greeting = _esc(name) if name else "there"
    body = "\n".join([
        _heading(f"You're on the waitlist, {greeting}"),
        _paragraph(
            "Thanks for signing up for MacroShot! We're in limited beta right now "
            "and every spot is taken."
        ),
        _paragraph(
            "You've been added to the waitlist - we'll email you the moment we "
            "open up the monthly plan and you can get in."
        ),
        _spacer(8),
        _paragraph(
            "No action needed from you right now. Sit tight and we'll be in touch."
        ),
        _spacer(8),
        _paragraph(
            "Cheers,<br>"
            f"<strong>{SIGNOFF_NAME}</strong>"
        ),
    ])
    return _wrap_html(
        "You're on the MacroShot waitlist - we'll email you when a spot opens.",
        body,
    )


def waitlist_confirmation_text(name: str) -> str:
    greeting = name or "there"
    return (
        f"You're on the waitlist, {greeting}\n\n"
        "Thanks for signing up for MacroShot! We're in limited beta right now "
        "and every spot is taken.\n\n"
        "You've been added to the waitlist - we'll email you the moment we "
        "open up the monthly plan and you can get in.\n\n"
        "No action needed from you right now. Sit tight and we'll be in touch.\n\n"
        f"Cheers,\n{SIGNOFF_NAME}\n\n"
        f"- MacroShot · {APP_URL}\n"
    )


# ── Subject lines ──────────────────────────────────────────────────────

SUBJECTS: dict[str, str] = {
    "welcome": "Welcome to MacroShot - log your first meal",
    "onboarding_d2_active": "A faster way to log (no photo needed)",
    "onboarding_d2_inactive": "Your first meal is one photo away",
    "onboarding_d5": "The feature most people miss",
    "onboarding_d7_active": "One week in - nice work!",
    "onboarding_d7_inactive": "Quick question about MacroShot",
    "reengage_d5": "Ready to pick back up?",
    "sunset_d14": "We'll stop emailing - come back anytime",
    "waitlist_confirmation": "You're on the MacroShot waitlist",
}
