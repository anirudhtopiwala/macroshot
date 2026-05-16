import { api } from './client';
import { formatLocalDate } from '../utils/date';
import type { Meal, MacroTotals, TrendDay, UserStats } from '../types';

export interface ExerciseAdjustment {
  calories: number;
  protein: number;
  active_calories: number;
}

interface TodayData {
  totals: MacroTotals & { meal_count: number };
  target: MacroTotals | null;
  adjusted_target: MacroTotals | null;
  exercise_adjustment: ExerciseAdjustment | null;
  remaining: MacroTotals | null;
  recent_meals: Meal[];
  // Server kicked off a Fitbit/Oura background sync for this request.
  // Refetch shortly to pick up updated exercise adjustment / activity data.
  sync_pending?: boolean;
}

interface TrendData {
  days: TrendDay[];
  target: MacroTotals | null;
}

interface FetchOpts { refresh?: boolean }

export const dashboardApi = {
  today: (date?: string, opts?: FetchOpts) =>
    api.get<TodayData>(date ? `/dashboard/today?date=${date}` : '/dashboard/today', opts),

  totals: (period: string = 'day') =>
    api.get<Record<string, unknown>>(`/dashboard/totals?period=${period}`),

  remaining: () =>
    api.get<{ remaining: MacroTotals | null; totals: MacroTotals; target: MacroTotals | null }>('/dashboard/remaining'),

  trend: (days?: number, todayOverride?: string, opts?: FetchOpts & { calendar?: boolean }) => {
    const qs = new URLSearchParams({ today: todayOverride || formatLocalDate() });
    if (days) qs.set('days', String(days));
    if (opts?.calendar) qs.set('calendar', 'true');
    return api.get<TrendData>(`/dashboard/trend?${qs}`, opts);
  },

  stats: (opts?: FetchOpts) => api.get<UserStats>('/dashboard/stats', opts),

  activityTrend: (days?: number) =>
    api.get<ActivityTrendData>(`/dashboard/activity-trend${days ? `?days=${days}` : ''}`),
};

export interface ActivityTrendDay {
  date: string;
  burned_calories: number;
  intake_calories: number;
  steps: number;
  active_minutes: number;
  workout_count: number;
  duration_min: number;
}

export interface ActivityTrendData {
  days: ActivityTrendDay[];
  truncated?: boolean;
  upgrade_message?: string;
}
