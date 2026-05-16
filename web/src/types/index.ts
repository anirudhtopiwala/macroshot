export interface FoodItem {
  name: string;
  description: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  weight_g: number | null;
  source: string | null;
}

export interface Nutrition {
  item_name: string;
  meal_description: string;
  items: FoodItem[];
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  source: string;
}

export interface Meal {
  id: number;
  logged_at: string;
  item_name: string;
  meal_description: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  meal_type: string;
  items_json: string;
  image_path: string;
  // Set by GET /meals/search on semantic hits (cosine similarity, 0-1).
  // Null/undefined on substring fallback or rows from list endpoints.
  score?: number | null;
}

export interface AnalyzeResponse {
  session_id: string;
  nutrition: Nutrition | null;
  questions: string[];
  raw_text: string;
  error: string | null;
  image_url?: string | null;
  // Barcode-specific fields
  serving_label?: string | null;
  serving_size_g?: number | null;
  /** 'g' (mass) or 'ml' (volume). Pairs with serving_size_g to label + warn correctly for liquids. */
  serving_size_unit?: 'g' | 'ml';
  cached?: boolean;
  cached_days_ago?: number | null;
  // User-correction metadata
  correction_applied?: boolean;
  corrected_at?: string | null;
}

export interface CorrectionResponse {
  nutrition: Nutrition | null;
  reply_text: string;
  error: string | null;
  new_badges?: NewBadge[];
  questions?: string[];
}

export interface AcceptResponse {
  meal_id: number | null;
  nutrition: Nutrition | null;
  progress: Progress;
  new_badges: NewBadge[];
  error: string | null;
}

export interface Progress {
  totals: MacroTotals;
  target: MacroTotals | null;
  remaining: MacroTotals | null;
}

export interface MacroTotals {
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  meal_count?: number;
}

export interface TrendDay {
  date: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  meal_count: number;
  /** Per-day calorie target adjusted for workout calories (when available). */
  effective_target_calories?: number;
}

export interface UserStats {
  total_meals: number;
  streak_days: number;
  avg_daily_calories_week: number;
  avg_protein_week: number;
  avg_carbs_week: number;
  avg_fat_week: number;
  most_logged_meal: [string, number] | null;
  macro_split: { pct_protein: number; pct_carbs: number; pct_fat: number } | null;
}

export interface Alias {
  name: string;
  item_name: string;
  meal_description: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  items: FoodItem[];
  new_badges?: NewBadge[];
}

export interface UserMe {
  user_id: number;
  email: string;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  avatar_url: string | null;
  google_linked: boolean;
  has_targets: boolean;
  tos_accepted: boolean;
}

export interface Targets {
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  set_by?: string;
}

export interface Prefs {
  timezone: string;
  breakfast_hour: number;
  lunch_hour: number;
  snack_hour: number;
  dinner_hour: number;
  streak_alert_hour: number;
  reminders_on: number;
  units_system: string;
  gamification: string;
  exercise_adjustment_on: number;
  exercise_eat_back_pct: number;
}

export interface Profile {
  age: number | null;
  height_cm: number | null;
  weight_kg: number | null;
  sex: string | null;
  weight_goal_kg: number | null;
  activity_level: string | null;
  workouts_per_week: number | null;
  weight_change_rate_kg: number | null;
  goal: string | null;
}

export interface TargetSuggestion {
  session_id: string;
  targets: Targets | null;
  explanation: string;
  reply_text: string;
  error: string | null;
}

// Chat with AI
export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: 'user' | 'model';
  text: string;
  /** Relative paths under data/images/. Served via /api/v1/images/{path}.
   *  Only present on user turns that attached photos. */
  image_paths?: string[];
}

export interface ChatCreateResponse {
  session_id: string;
  title: string;
  new_badges?: NewBadge[];
}

export interface ChatMessageResponse {
  reply: string;
  title: string;
  error: string | null;
}

export interface ChatHistoryResponse {
  session_id: string;
  title: string;
  messages: ChatMessage[];
  at_limit?: boolean;
}

// Badges / Achievements
export interface NewBadge {
  badge_id: string;
  name: string;
  tier: number;
  tier_name: string;
  is_new: boolean;
  icon: string;
}

export interface Badge {
  badge_id: string;
  name: string;
  category: string;
  icon: string;
  description: string;
  current_value: number;
  tier: number; // -1 = locked, 0-3 = bronze-diamond
  tier_name: string;
  thresholds: number[];
  next_threshold: number | null;
  earned_at: string | null;
  seen: boolean;
}

export interface AchievementsResponse {
  badges: Badge[];
  shields_available: number;
  shields_total_earned: number;
  gamification: string;
}

export interface ShieldProgress {
  on_target_days: number;
  shields_earned_total: number;
  days_until_next: number;
}

export interface AchievementSummaryResponse {
  total_earned: number;
  total_badges: number;
  unseen_count: number;
  shields_available: number;
  shield_used_today: boolean;
  shield_used_dates: string[];
  shield_progress: ShieldProgress;
  shields_at_max: boolean;
}

export interface Challenge {
  id: string;
  name: string;
  description: string;
  icon: string;
  target: number;
  progress: number;
  completed: boolean;
  period: string;
  type: string;
}

export interface ChallengesResponse {
  daily: Challenge | null;
  weekly: Challenge | null;
}

// Subscription
export interface SubscriptionStatus {
  plan: string;
  status: string;
  is_premium: boolean;
  is_og?: boolean;
  /** Legacy alias of is_og - kept so old cached bundles still decode. */
  founding_member?: boolean;
  /** 'self' or 'hosted'. */
  app_mode?: string;
  /** True while the app is in limited-beta mode (everyone is Pro, Stripe disabled). */
  beta_mode?: boolean;
  trial_available: boolean;
  trial_ends_at: string | null;
  usage_image_used: number;
  usage_image_limit: number; // daily cap: FREE_IMAGE_LIMIT (free) or PRO_IMAGE_LIMIT (pro). Never -1 - even pro is capped.
  usage_text_meal_used?: number;
  usage_text_meal_limit?: number; // daily cap, -1 for unlimited
  usage_chat_used: number;
  usage_chat_limit: number; // -1 for unlimited (in beta: daily cap, not monthly)
  /** Per-meal correction cap (NOT daily) - no `used` companion. */
  usage_meal_edit_limit?: number;
  usage_saved_meals: number;
  usage_saved_meals_limit: number; // -1 for unlimited
  current_period_start: string | null;
  current_period_end: string | null;
  cancelled_at: string | null;
  stripe_customer_id?: string;
}
