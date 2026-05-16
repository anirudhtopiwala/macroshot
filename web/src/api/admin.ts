import { api } from './client';

// NOTE: these endpoints are gated server-side by the ADMIN_EMAIL env var.
// Non-admin callers get a 404 (not 403) to avoid advertising the endpoint.
// This client is used only by the /admin/metrics page.

export interface AdminUserRow {
  user_id: number;
  email: string;
  first_name: string;
  events: Record<string, number>;
  total: number;
}

export interface AdminOverviewResponse {
  window_days: number;
  since: string;
  totals: Record<string, number>;
  users: AdminUserRow[];
}

export interface AdminUserTimeseriesRow {
  day: string;
  event_type: string;
  cnt: number;
}

export interface AdminUserDetailResponse {
  user_id: number;
  email: string;
  first_name: string;
  window_days: number;
  since: string;
  totals: Record<string, number>;
  timeseries: AdminUserTimeseriesRow[];
}

export interface FunnelCounts {
  analyze_total: number;
  analyze_image: number;
  analyze_text: number;
  analyze_combined: number;
  correct: number;
  accept: number;
  cancel: number;
  delete: number;
  quick_log: number;
  relog: number;
  copy_day: number;
  barcode_scan: number;
}

export interface AcquisitionMix {
  signup_google: number;
  signup_email: number;
  signup_total: number;
  waitlist_pending: number;
  waitlist_total: number;
  total_users: number;
}

export interface FeatureAdoption {
  gamification_on: number;
  gamification_off: number;
  chat_users: number;
  strava_connected: number;
  fitbit_connected: number;
  push_subscribed: number;
  has_targets: number;
  reminders_on: number;
  logged_weight_recent: number;
  barcode_users_recent: number;
  quick_log_users_recent: number;
  premium_users: number;
  trial_users: number;
}

export interface LimitHitEntry {
  count: number;
  unique_users: number;
}

export interface CostMetrics {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  image_calls: number;
  web_searches: number;
  unique_users: number;
  estimated_cost_usd: number;
}

export interface BudgetStatus {
  monthly_cost_usd: number;
  monthly_budget_usd: number;
  pct_used: number;
  gate_tripped: boolean;
}

export interface RetentionDay {
  day: string;
  active_users: number;
  events: number;
}

export interface AdminDashboardResponse {
  window_days: number;
  since: string;
  funnel: FunnelCounts;
  acquisition: AcquisitionMix;
  adoption: FeatureAdoption;
  limits: Record<string, LimitHitEntry>;
  cost: CostMetrics;
  budget: BudgetStatus;
  retention: RetentionDay[];
}

export interface FeedbackTotals {
  thumbs_up: number;
  thumbs_down: number;
  total: number;
  meals_logged: number;
  submission_rate: number;
}

export interface FeedbackThumbsDown {
  feedback_id: number;
  user_id: number;
  meal_id: number;
  rating: number;
  comment: string;
  created_at: string;
  item_name: string;
  meal_description: string;
  source: string;
  image_path: string;
  snapshot: unknown | null;
}

export interface AdminFeedbackResponse {
  window_days: number;
  since: string;
  totals: FeedbackTotals;
  thumbs_downs: FeedbackThumbsDown[];
}

export interface TodayMeal {
  meal_id: number;
  user_id: number;
  first_name: string;
  email: string;
  logged_at: string;
  meal_description: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  source: string;
  meal_type: string;
}

export interface TodayWeight {
  weight_id: number;
  user_id: number;
  first_name: string;
  email: string;
  logged_at: string;
  weight_kg: number;
}

export interface TodayActivityResponse {
  hours: number;
  meals: TodayMeal[];
  weights: TodayWeight[];
}

export const adminApi = {
  whoami: () => api.get<{ user_id: number; email: string; is_admin: boolean }>('/admin/whoami'),
  overview: (days = 30) => api.get<AdminOverviewResponse>(`/admin/metrics/overview?days=${days}`),
  user: (userId: number, days = 30) =>
    api.get<AdminUserDetailResponse>(`/admin/metrics/user/${userId}?days=${days}`),
  dashboard: (days = 30) =>
    api.get<AdminDashboardResponse>(`/admin/metrics/dashboard?days=${days}`),
  feedback: (days = 30, limit = 50) =>
    api.get<AdminFeedbackResponse>(`/admin/metrics/feedback?days=${days}&limit=${limit}`),
  today: (hours = 24) =>
    api.get<TodayActivityResponse>(`/admin/metrics/today?hours=${hours}`),
};
