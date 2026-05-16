"""Badge/achievement definitions for gamification system.

24 badges across 8 categories, each with up to 4 tiers (Bronze/Silver/Gold/Diamond).
Designed as a 6-week program. Rewards consistency, accuracy, variety, and feature
exploration - never restriction.
"""

TIERS = ("bronze", "silver", "gold", "diamond")

# Badge definitions keyed by badge_id
# thresholds: [bronze, silver, gold, diamond] - cumulative values
# triggers: which events cause this badge to be re-evaluated
# icon: emoji used in celebrations and badge cards
BADGES: dict[str, dict] = {
    # ── Logging Streaks ──
    "streak_on_a_roll": {
        "name": "On a Roll",
        "category": "Logging Streaks",
        "icon": "\U0001f525",  # fire
        "descriptions": ["Log meals for 3 consecutive days", "Keep a 7-day logging streak going", "Maintain a 21-day streak - habit formed!", "42-day streak - you're unstoppable!"],
        "thresholds": [3, 7, 21, 42],
        "triggers": ["meal_accept"],
    },
    "streak_early_bird": {
        "name": "Early Bird",
        "category": "Logging Streaks",
        "icon": "\U0001f305",  # sunrise
        "descriptions": ["Log breakfast before 9 AM 3 times", "10 early breakfasts - morning routine locked in", "25 early breakfasts - true morning person", "42 early breakfasts - early bird legend"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["meal_accept"],
    },
    "streak_night_owl": {
        "name": "Night Owl",
        "category": "Logging Streaks",
        "icon": "\U0001f319",  # crescent moon
        "descriptions": ["Log dinner after 7 PM 3 times", "10 late dinners - night owl confirmed", "25 late dinners - you own the evenings", "42 late dinners - night owl legend"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["meal_accept"],
    },
    "streak_comeback": {
        "name": "Comeback King",
        "category": "Logging Streaks",
        "icon": "\U0001f451",  # crown
        "descriptions": ["Restart your streak after a gap - resilience!", "2 comebacks - you bounce right back", "3 comebacks - you never give up", "5 comebacks - unbreakable spirit"],
        "thresholds": [1, 2, 3, 5],
        "triggers": ["meal_accept"],
    },
    "streak_full_day": {
        "name": "Full Day",
        "category": "Logging Streaks",
        "icon": "\U0001f4c5",  # calendar
        "descriptions": ["Log all 4 meal types in one day", "7 complete tracking days", "21 complete days - total awareness", "42 full days tracked - nutrition master"],
        "thresholds": [1, 7, 21, 42],
        "triggers": ["meal_accept"],
    },

    # ── Milestones (New User Onboarding) ──
    "milestone_first_meal": {
        "name": "First Bite",
        "category": "Milestones",
        "icon": "\U0001f37d\ufe0f",  # fork and knife with plate
        "descriptions": ["Log your very first meal - welcome to MacroShot!"],
        "thresholds": [1],
        "triggers": ["meal_accept"],
    },
    "milestone_first_day": {
        "name": "Day One",
        "category": "Milestones",
        "icon": "\U0001f31f",  # glowing star
        "descriptions": ["Log 3 meals in a single day - you've got this!"],
        "thresholds": [1],
        "triggers": ["meal_accept"],
    },
    "milestone_first_week": {
        "name": "Week One",
        "category": "Milestones",
        "icon": "\U0001f4c6",  # tear-off calendar
        "descriptions": ["Log meals on 7 different days - a full week of tracking!"],
        "thresholds": [7],
        "triggers": ["meal_accept"],
    },

    # ── Meals Logged ──
    "meals_meal_machine": {
        "name": "Meal Machine",
        "category": "Meals Logged",
        "icon": "\U0001f374",  # fork and knife
        "descriptions": ["Log 10 meals", "50 meals - you're a regular!", "100 meals - tracking is second nature", "168 meals - legendary dedication"],
        "thresholds": [10, 50, 100, 168],
        "triggers": ["meal_accept"],
    },
    "meals_snap_happy": {
        "name": "Paparazzi",
        "category": "Meals Logged",
        "icon": "\U0001f4f8",  # camera
        "descriptions": ["Log 5 meals with a photo", "20 photo meals - building a food diary", "60 photo meals - visual tracking pro", "150 photo meals - you could write a cookbook!"],
        "thresholds": [5, 20, 60, 150],
        "triggers": ["meal_accept"],
    },

    # ── Target Hits ──
    "target_bullseye": {
        "name": "Bullseye",
        "category": "Target Hits",
        "icon": "\U0001f3af",  # target
        "descriptions": ["Hit your calorie target (within 10%) for 3 days", "10 days on target - precision pays off", "25 days on target - master of portions", "42 days - calorie sniper"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["meal_accept"],
    },
    "target_protein": {
        "name": "Protein Machine",
        "category": "Target Hits",
        "icon": "\U0001f4aa",  # flexed bicep
        "descriptions": ["Hit your protein target (within 10%) for 3 days", "10 days of protein goals met", "25 days - your muscles thank you", "42 days - protein champion"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["meal_accept"],
    },
    "target_carbs_master": {
        "name": "Carb Conscious",
        "category": "Target Hits",
        "icon": "\U0001f35e",  # bread
        "descriptions": ["Hit your carb target (within 10%) for 3 days", "10 days of carb control", "25 days - carb management pro", "42 days - carb conscious champion"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["meal_accept"],
    },
    "target_macro_master": {
        "name": "Macro Master",
        "category": "Target Hits",
        "icon": "\U0001f48e",  # gem (was crown, now unique)
        "descriptions": ["Hit ALL 4 macro targets in a single day", "5 days with perfect macros", "15 days - balanced nutrition royalty", "30 days of macro perfection"],
        "thresholds": [1, 5, 15, 30],
        "triggers": ["meal_accept"],
    },
    "target_perfect_week": {
        "name": "Perfect Week",
        "category": "Target Hits",
        "icon": "\u2b50",  # star
        "descriptions": ["Hit your calorie target every day for a full week", "3 perfect weeks - monthly consistency", "5 perfect weeks - incredible discipline", "6 perfect weeks - flawless program!"],
        "thresholds": [1, 3, 5, 6],
        "triggers": ["meal_accept"],
    },

    # ── Variety ──
    "variety_world_plate": {
        "name": "World Plate",
        "category": "Variety",
        "icon": "\U0001f30d",  # globe
        "descriptions": ["Log 10 different meals", "25 unique meals - adventurous eater!", "50 unique meals - diverse palate", "100 unique meals - culinary explorer"],
        "thresholds": [10, 25, 50, 100],
        "triggers": ["meal_accept"],
    },
    "variety_quick_draw": {
        "name": "Quick Draw",
        "category": "Variety",
        "icon": "\u26a1",  # lightning
        "descriptions": ["Use Quick Log 3 times", "10 quick logs - speed tracker", "30 quick logs - logging in seconds", "75 quick logs - the fastest logger"],
        "thresholds": [3, 10, 30, 75],
        "triggers": ["alias_log"],
    },
    "variety_saved_chef": {
        "name": "Saved Meals Chef",
        "category": "Variety",
        "icon": "\U0001f4d6",  # book
        "descriptions": ["Save 2 meals as Quick Log shortcuts", "5 saved meals - building your recipe book", "10 saved meals - your personal menu", "20 saved meals - a full cookbook!"],
        "thresholds": [2, 5, 10, 20],
        "triggers": ["alias_create"],
    },

    # ── Weight ──
    "weight_scale_warrior": {
        "name": "Scale Warrior",
        "category": "Weight",
        "icon": "\u2696\ufe0f",  # scales
        "descriptions": ["Log your weight 3 times", "10 weigh-ins - tracking trends", "25 weigh-ins - data-driven progress", "42 weigh-ins - scale master"],
        "thresholds": [3, 10, 25, 42],
        "triggers": ["weight_log"],
    },
    "weight_trend_setter": {
        "name": "Trend Setter",
        "category": "Weight",
        "icon": "\U0001f4c8",  # chart
        "descriptions": ["Log weight in 2 different weeks", "4 weeks of weight tracking", "5 weeks of consistent tracking", "6 weeks - full program tracked!"],
        "thresholds": [2, 4, 5, 6],
        "triggers": ["weight_log"],
    },

    # ── Explorer ──
    "explorer_coach_fav": {
        "name": "Coach's Favorite",
        "category": "Explorer",
        "icon": "\U0001f4ac",  # speech bubble
        "descriptions": ["Start your first AI coaching chat", "5 coaching sessions - regular check-ins", "15 sessions - the AI knows you well", "40 sessions - nutrition coaching veteran"],
        "thresholds": [1, 5, 15, 40],
        "triggers": ["chat_create"],
    },
    "explorer_perfectionist": {
        "name": "Perfectionist",
        "category": "Explorer",
        "icon": "\u270f\ufe0f",  # pencil
        "descriptions": ["Make 3 meal corrections for better accuracy", "10 corrections - precision matters to you", "25 corrections - every gram counts", "50 corrections - the ultimate perfectionist"],
        "thresholds": [3, 10, 25, 50],
        "triggers": ["meal_correct"],
    },
    "explorer_goal_setter": {
        "name": "Goal Setter",
        "category": "Explorer",
        "icon": "\U0001f6a9",  # flag
        "descriptions": ["Set your macro targets - now you're dialed in!"],
        "thresholds": [1],
        "triggers": ["target_set"],
    },

    # ── Meta ──
    "meta_completionist": {
        "name": "Completionist",
        "category": "Meta",
        "icon": "\U0001f3c6",  # trophy
        "descriptions": ["Earn any 5 badges", "12 badges collected - halfway there!", "18 badges - almost a full set", "Earn all other 23 badges - ultimate completionist!"],
        "thresholds": [5, 12, 18, 23],
        "triggers": ["any_badge_earn"],
    },
}


def get_tier(value: int, thresholds: list[int]) -> int:
    """Return tier index (0-3) for a value, or -1 if below bronze."""
    tier = -1
    for i, threshold in enumerate(thresholds):
        if value >= threshold:
            tier = i
    return tier


def get_tier_name(tier: int) -> str:
    """Return tier name string, or 'locked' for -1."""
    if 0 <= tier < len(TIERS):
        return TIERS[tier]
    return "locked"


def badges_for_trigger(trigger: str) -> list[str]:
    """Return badge IDs that should be evaluated for a given trigger."""
    return [bid for bid, b in BADGES.items() if trigger in b["triggers"]]
