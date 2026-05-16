import { api } from './client';

export interface Workout {
  id: number;
  source: string;
  activity_type: string;
  name: string;
  started_at: string;
  duration_sec: number;
  calories_burned: number;
  distance_m: number;
  avg_heart_rate: number;
}

export interface FitbitSummary {
  calories_out: number;
  activity_calories: number;
  calories_bmr: number;
  active_calories: number;
  steps: number;
  fairly_active_min: number;
  very_active_min: number;
  resting_heart_rate: number;
  fetched_at: string;
}

export interface WorkoutResponse {
  date: string;
  workouts: Workout[];
  fitbit_summary: FitbitSummary | null;
  totals: {
    active_calories: number;
    workout_count: number;
    total_duration_sec: number;
    steps: number;
    active_minutes: number;
  };
  fitbit_connected?: boolean;
  strava_connected?: boolean;
}

export interface ManualWorkoutRequest {
  activity_type: string;
  name?: string;
  duration_min: number;
  calories_burned?: number;
  date?: string;
  time?: string;
}

export interface WorkoutHistoryFitbit {
  activity_calories: number;
  calories_out: number;
  calories_bmr: number;
  active_calories: number;
  steps: number;
  fairly_active_min: number;
  very_active_min: number;
  resting_heart_rate: number;
}

export interface WorkoutHistoryDay {
  date: string;
  workouts: Workout[];
  fitbit_summary: WorkoutHistoryFitbit | null;
  total_calories: number;
  total_duration_sec: number;
  total_steps: number;
}

export interface WorkoutHistoryResponse {
  days: WorkoutHistoryDay[];
  totals: {
    active_days: number;
    workout_count: number;
    total_calories: number;
    total_duration_sec: number;
    total_steps: number;
  };
}

export interface WorkoutSyncStatus {
  last_synced: string | null;
}

export const workoutsApi = {
  get: (date?: string, opts?: { refresh?: boolean }) =>
    api.get<WorkoutResponse>(`/workouts${date ? `?date=${date}` : ''}`, opts),
  history: (days?: number, opts?: { refresh?: boolean }) =>
    api.get<WorkoutHistoryResponse>(`/workouts/history${days ? `?days=${days}` : ''}`, opts),
  syncStatus: () =>
    api.get<WorkoutSyncStatus>('/workouts/sync-status'),
  addManual: (data: ManualWorkoutRequest) =>
    api.post<{ id: number; status: string }>('/workouts', data),
  delete: (id: number) =>
    api.delete(`/workouts/${id}`),
};
