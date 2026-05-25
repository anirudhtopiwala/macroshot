"""Pydantic request/response schemas for the web API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# Eating-disorder safeguard: refuse manually-entered calorie targets below
# this floor.  Mirrors the same floor that the AI target-setting flow
# enforces in src/targets.py and src/gemini.py.  Users with a documented
# clinical need below this should not be using a consumer macro tracker.
MIN_SAFE_CALORIES = 1200


# Strict character class for human first/last names. Allows letters,
# digits (some folks have numeric handles), spaces, hyphens, apostrophes,
# and dots. Rejects HTML metacharacters (<, >, &, "). Length cap 80 to
# avoid abuse via 4 KB names that bloat email templates.
_NAME_PATTERN = r"^[\w\s\-'.]{0,80}$"


# --- Auth ---

class GoogleAuthRequest(BaseModel):
    id_token: str


class EmailSendPinRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        # Lowercase + strip at the schema boundary so every downstream
        # code path sees a single canonical form. Prevents case-aliasing
        # bypass of per-email rate limits and PIN lockouts.
        return v.strip().lower()


class EmailVerifyPinRequest(BaseModel):
    email: EmailStr
    pin: str = Field(min_length=4, max_length=12)
    first_name: str | None = Field(default=None, max_length=80, pattern=_NAME_PATTERN)
    last_name: str | None = Field(default=None, max_length=80, pattern=_NAME_PATTERN)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class WaitlistJoinRequest(BaseModel):
    email: EmailStr
    first_name: str | None = Field(default=None, max_length=80, pattern=_NAME_PATTERN)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class WaitlistJoinResponse(BaseModel):
    ok: bool = True
    already_subscribed: bool = False
    message: str


class AuthResponse(BaseModel):
    user_id: int
    email: str
    username: str | None = None
    message: str = "ok"


class UserMeResponse(BaseModel):
    user_id: int
    email: str
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    avatar_url: str | None = None
    google_linked: bool = False
    has_targets: bool = False
    tos_accepted: bool = False


# --- Meals ---

class CorrectionRequest(BaseModel):
    text: str = Field(max_length=2000)


class ItemRemovedRequest(BaseModel):
    """Tells the session that the user manually removed an item from the
    meal. The server appends a synthetic user/model exchange to the
    conversation so any future Gemini correction sees the removal and
    stops re-adding the item to its responses."""
    item_name: str = Field(min_length=1, max_length=200)


class FoodItemOut(BaseModel):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=2000)
    brand: str | None = Field(default=None, max_length=200)
    has_label: bool = False
    calories: float = Field(ge=0, le=100000)
    protein: float = Field(ge=0, le=10000)
    carbs: float = Field(ge=0, le=10000)
    fat: float = Field(ge=0, le=10000)
    weight_g: float | None = Field(default=None, ge=0, le=100000)
    source: str | None = Field(default=None, max_length=50)


class NutritionOut(BaseModel):
    item_name: str = Field(max_length=200)
    meal_description: str = Field(default="", max_length=2000)
    items: list[FoodItemOut] = Field(default_factory=list, max_length=50)
    calories: float = Field(ge=0, le=100000)
    protein: float = Field(ge=0, le=10000)
    carbs: float = Field(ge=0, le=10000)
    fat: float = Field(ge=0, le=10000)
    source: str = Field(default="Gemini", max_length=50)


class AnalyzeResponse(BaseModel):
    session_id: str
    nutrition: NutritionOut | None = None
    questions: list[str] = []
    raw_text: str = ""
    error: str | None = None
    image_url: str | None = None
    # Barcode-specific fields
    serving_label: str | None = None
    serving_size_g: float | None = None
    # 'g' (mass) or 'ml' (volume). Paired with serving_size_g - frontend
    # reads this to render the correct unit and pick the right threshold
    # for the "large serving" warning (liquids are often 330–500 ml per
    # serving, which would trip a grams-only threshold).
    serving_size_unit: str = "g"
    cached: bool = False
    cached_days_ago: int | None = None
    # User-correction metadata (populated when a barcode scan used the
    # user's saved correction as the source of truth)
    correction_applied: bool = False
    corrected_at: str | None = None


class CorrectionResponse(BaseModel):
    nutrition: NutritionOut | None = None
    reply_text: str = ""
    error: str | None = None
    new_badges: list[NewBadge] = []
    questions: list[str] = []


class AcceptRequest(BaseModel):
    nutrition: NutritionOut | None = None
    # A25: client-provided "YYYY-MM-DD" or "YYYY-MM-DD HH:MM[:SS]" for backdating.
    logged_at: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$",
    )
    # For barcode sessions: the servings multiplier applied to the per-serving
    # values. Used to back out the per-serving nutrition when saving a
    # barcode correction, so "I ate 2 servings" doesn't double the saved value.
    servings: float | None = Field(default=None, gt=0, le=1000)


class NewBadge(BaseModel):
    badge_id: str
    name: str
    tier: int
    tier_name: str
    is_new: bool
    icon: str = ""


class AcceptResponse(BaseModel):
    meal_id: int | None = None
    nutrition: NutritionOut | None = None
    progress: dict = {}
    new_badges: list[NewBadge] = []
    error: str | None = None


class MealOut(BaseModel):
    id: int
    logged_at: str
    item_name: str
    meal_description: str = ""
    calories: float
    protein: float
    carbs: float
    fat: float
    meal_type: str = ""
    items_json: str = ""
    image_path: str = ""
    # Set by /meals/search on semantic hits (cosine similarity, 0-1).
    # Null on substring fallback or when the row came from list endpoints.
    score: float | None = None


class CopyDayRequest(BaseModel):
    source_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD
    target_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # defaults to today in user's tz


class MealUpdateRequest(BaseModel):
    item_name: str | None = Field(default=None, max_length=200)
    meal_description: str | None = Field(default=None, max_length=2000)
    calories: float | None = Field(default=None, ge=0, le=100000)
    protein: float | None = Field(default=None, ge=0, le=10000)
    carbs: float | None = Field(default=None, ge=0, le=10000)
    fat: float | None = Field(default=None, ge=0, le=10000)
    # A26: tightened from 50k to 20k. 20k bytes is enough for ~50 fully
    # expanded FoodItem rows; legitimate meals never come close.
    items_json: str | None = Field(default=None, max_length=20_000)
    meal_type: str | None = Field(default=None, max_length=50)


# --- Dashboard ---

class TodayResponse(BaseModel):
    totals: dict
    target: dict | None = None
    adjusted_target: dict | None = None
    exercise_adjustment: dict | None = None
    remaining: dict | None = None
    recent_meals: list[MealOut] = []
    # True when a Fitbit/Oura background sync was kicked off for this
    # request. Client should refetch shortly to pick up fresh workout
    # numbers (eat-back adjustment, activity card).
    sync_pending: bool = False


class TrendResponse(BaseModel):
    days: list[dict]
    target: dict | None = None


class StatsResponse(BaseModel):
    total_meals: int = 0
    streak_days: int = 0
    avg_daily_calories_week: float = 0.0
    avg_protein_week: float = 0.0
    avg_carbs_week: float = 0.0
    avg_fat_week: float = 0.0
    most_logged_meal: list | None = None
    macro_split: dict | None = None
    locked: bool = False


# --- Aliases ---

class AliasCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    item_name: str = Field(max_length=200)
    meal_description: str = Field(default="", max_length=2000)
    calories: float = Field(ge=0, le=100000)
    protein: float = Field(ge=0, le=10000)
    carbs: float = Field(ge=0, le=10000)
    fat: float = Field(ge=0, le=10000)
    # A26: tightened from 50k to 20k. See MealUpdateRequest.
    items_json: str = Field(default="[]", max_length=20_000)


class AliasOut(BaseModel):
    name: str
    item_name: str
    meal_description: str = ""
    calories: float
    protein: float
    carbs: float
    fat: float
    items: list[FoodItemOut] = []
    new_badges: list[NewBadge] = []


# --- Settings ---

class TargetsRequest(BaseModel):
    calories: float = Field(ge=0, le=20000)
    protein: float = Field(ge=0, le=2000)
    carbs: float = Field(ge=0, le=2000)
    fat: float = Field(ge=0, le=2000)
    skip: bool = False  # True when saving defaults during onboarding skip

    @field_validator("calories")
    @classmethod
    def _enforce_safe_calorie_floor(cls, v: float, info) -> float:
        # Skip the floor only when saving onboarding defaults (skip=True),
        # which the frontend sets when a user dismisses the wizard without
        # entering numbers - those defaults are server-side safe values.
        skip = info.data.get("skip", False) if info.data else False
        if not skip and 0 < v < MIN_SAFE_CALORIES:
            raise ValueError(
                f"Calorie target must be at least {MIN_SAFE_CALORIES} kcal. "
                "Lower targets can be unsafe - please consult a registered "
                "dietitian if you have a clinical need to eat less."
            )
        return v


class TargetSuggestRequest(BaseModel):
    age: int | None = Field(default=None, ge=16, le=150)
    sex: str | None = None
    weight_kg: float | None = Field(default=None, ge=0, le=500)
    height_cm: float | None = Field(default=None, ge=0, le=300)
    goal: str = "maintain"
    activity_level: str = "lightly_active"
    workouts_per_week: int | None = None
    weight_change_rate_kg: float | None = None
    # Optional first-turn user message folded into the Gemini prompt so the
    # AI's initial suggestion already reflects the user's stated intent
    # (e.g. "I'm training for a marathon, give me more carbs"). Used by the
    # refine flow to avoid the legacy two-step suggest→refine round-trip.
    seed_message: str | None = Field(default=None, max_length=2000)


class TargetRefineRequest(BaseModel):
    text: str = Field(max_length=2000)


class TargetSuggestResponse(BaseModel):
    session_id: str
    targets: TargetsRequest | None = None
    explanation: str = ""
    reply_text: str = ""
    # True iff the model judged the user's last turn to be asking for a target
    # change. Used by the UI to decide whether to show a "no update" hint when
    # `targets` is null - confirmations and informational questions should stay
    # silent, only change-requests-that-didn't-take should get nudged.
    user_requested_change: bool = False
    error: str | None = None


class TargetAcceptRequest(BaseModel):
    calories: float = Field(ge=0, le=20000)
    protein: float = Field(ge=0, le=2000)
    carbs: float = Field(ge=0, le=2000)
    fat: float = Field(ge=0, le=2000)

    @field_validator("calories")
    @classmethod
    def _enforce_safe_calorie_floor(cls, v: float) -> float:
        if 0 < v < MIN_SAFE_CALORIES:
            raise ValueError(
                f"Calorie target must be at least {MIN_SAFE_CALORIES} kcal."
            )
        return v


class PrefsRequest(BaseModel):
    timezone: str | None = None
    breakfast_hour: int | None = None
    lunch_hour: int | None = None
    snack_hour: int | None = None
    dinner_hour: int | None = None
    streak_alert_hour: int | None = None
    reminders_on: int | None = None
    units_system: str | None = None
    gamification: str | None = None
    exercise_adjustment_on: Literal[0, 1] | None = None
    exercise_eat_back_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    ai_web_search_enabled: Literal[0, 1] | None = None
    notif_show_macros: Literal[0, 1] | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)


class PrefsResponse(BaseModel):
    timezone: str
    breakfast_hour: int
    lunch_hour: int
    snack_hour: int
    dinner_hour: int
    streak_alert_hour: int
    reminders_on: int
    meals_public: int
    units_system: str
    gamification: str
    exercise_adjustment_on: int
    exercise_eat_back_pct: float
    ai_web_search_enabled: int = 0
    notif_show_macros: int = 0
    quiet_hours_start: int = 23
    quiet_hours_end: int = 7


class ProfileRequest(BaseModel):
    first_name: str | None = Field(default=None, max_length=80, pattern=_NAME_PATTERN)
    last_name: str | None = Field(default=None, max_length=80, pattern=_NAME_PATTERN)
    age: int | None = Field(default=None, ge=16, le=150)
    weight_kg: float | None = Field(default=None, ge=0, le=500)
    height_cm: float | None = Field(default=None, ge=0, le=300)
    sex: str | None = Field(default=None, max_length=20)
    weight_goal_kg: float | None = Field(default=None, ge=0, le=500)
    activity_level: str | None = Field(default=None, max_length=50)
    workouts_per_week: int | None = Field(default=None, ge=0, le=30)
    weight_change_rate_kg: float | None = Field(default=None, ge=-5, le=5)
    goal: str | None = Field(default=None, max_length=50)


class ProfileResponse(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    sex: str | None = None
    weight_goal_kg: float | None = None
    activity_level: str | None = None
    workouts_per_week: int | None = None
    weight_change_rate_kg: float | None = None
    goal: str | None = None


# --- Push Notifications ---

class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=200)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushSubscriptionKeys

class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(max_length=2000)


# --- Weight ---

class WeightLogRequest(BaseModel):
    weight_kg: float = Field(gt=0, le=700)
    # A25: pattern-restrict to YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS].
    logged_at: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$",
    )


class WeightEntryOut(BaseModel):
    id: int
    logged_at: str
    weight_kg: float


class WeightHistoryResponse(BaseModel):
    entries: list[WeightEntryOut] = []
    latest: float | None = None
    start: float | None = None
    goal: float | None = None
    last_logged_at: str | None = None


# --- Chat with AI ---

class ChatCreateResponse(BaseModel):
    session_id: str
    title: str = ""
    new_badges: list[NewBadge] = []


class ChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatMessageResponse(BaseModel):
    reply: str
    title: str = ""
    error: str | None = None


class ChatSessionOut(BaseModel):
    id: str
    title: str = ""
    created_at: str
    updated_at: str


class ChatHistoryMessage(BaseModel):
    role: str
    text: str
    # Relative paths under data/images/, served via /api/v1/images/{path}.
    # Only present on user turns that attached photos.
    image_paths: list[str] | None = None


class ChatHistoryResponse(BaseModel):
    session_id: str
    title: str = ""
    messages: list[ChatHistoryMessage] = []
    at_limit: bool = False


# --- Subscription ---

class CheckoutRequest(BaseModel):
    plan: str = "pro_monthly"  # monthly only


class SubscriptionStatusResponse(BaseModel):
    plan: str = "free"
    status: str = "active"
    is_premium: bool = False
    is_og: bool = False
    # Legacy alias for is_og - kept so older frontend bundles keep working
    # through the deploy. Remove once every client has refreshed.
    founding_member: bool = False
    app_mode: str = "self"   # "self" | "hosted"
    beta_mode: bool = False
    trial_available: bool = True
    trial_ends_at: str | None = None

    # Daily counters. Limit == -1 means "no cap"; frontend renders
    # unlimited instead of a progress bar.
    usage_image_used: int = 0
    usage_image_limit: int = 5
    usage_text_meal_used: int = 0
    usage_text_meal_limit: int = -1
    usage_chat_used: int = 0
    usage_chat_limit: int = 1
    # Per-meal cap on AI edits - not a daily counter, no "used" field
    # (each meal session tracks its own via its conversation history).
    usage_meal_edit_limit: int = 10

    usage_saved_meals: int = 0
    usage_saved_meals_limit: int = 10  # -1 for unlimited (premium)
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancelled_at: str | None = None
    stripe_customer_id: str = ""


class TrialResponse(BaseModel):
    message: str
    plan: str
    status: str
    is_premium: bool
    trial_ends_at: str | None = None


# --- Achievements / Badges ---

class BadgeOut(BaseModel):
    badge_id: str
    name: str
    category: str
    icon: str
    description: str
    current_value: int
    tier: int  # -1 = locked, 0-3 = bronze-diamond
    tier_name: str
    thresholds: list[int]
    next_threshold: int | None = None
    earned_at: str | None = None
    seen: bool = True


class AchievementsResponse(BaseModel):
    badges: list[BadgeOut]
    shields_available: int = 0
    shields_total_earned: int = 0
    gamification: str = "full"


class ShieldProgressOut(BaseModel):
    on_target_days: int = 0
    shields_earned_total: int = 0
    days_until_next: int = -1


class AchievementSummaryResponse(BaseModel):
    total_earned: int = 0
    total_badges: int = 24
    unseen_count: int = 0
    shields_available: int = 0
    shield_used_today: bool = False  # True if a shield was consumed today
    shield_used_dates: list[str] = []  # bridged_dates from shields used in last 7 days
    shield_progress: ShieldProgressOut = ShieldProgressOut()
    shields_at_max: bool = False  # True when available >= 3


class ChallengeOut(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    target: int
    progress: int = 0
    completed: bool = False
    period: str = ""
    type: str = ""  # "daily" or "weekly"


class ChallengesResponse(BaseModel):
    daily: ChallengeOut | None = None
    weekly: ChallengeOut | None = None


class BadgeSeenRequest(BaseModel):
    # A24: bound list length + per-id length.
    badge_ids: list[str] = Field(max_length=200)

    @field_validator("badge_ids")
    @classmethod
    def _bound_entries(cls, v: list[str]) -> list[str]:
        return [b[:100] for b in v]


# --- Feedback ---

class FeedbackRequest(BaseModel):
    type: Literal["bug", "feature", "other"]
    message: str = Field(min_length=1, max_length=2000)
    page: str | None = Field(default=None, max_length=200)
