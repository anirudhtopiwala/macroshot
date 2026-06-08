import { api } from './client';
import type { NewBadge, TargetSuggestion } from '../types';

export interface SuggestRequest {
  age?: number | null;
  sex?: string | null;
  weight_kg?: number | null;
  height_cm?: number | null;
  goal: string;
  activity_level: string;
  workouts_per_week?: number | null;
  weight_change_rate_kg?: number;
  // First-turn user message folded into the prompt so the initial AI
  // suggestion reflects user intent. Used by the refine flow to skip a
  // throwaway suggest call before the user has even spoken.
  seed_message?: string;
}

export const targetsApi = {
  suggest: (data: SuggestRequest) =>
    api.post<TargetSuggestion>('/settings/targets/suggest', data),

  refine: (sessionId: string, text: string) =>
    api.post<TargetSuggestion>(`/settings/targets/suggest/${sessionId}/refine`, { text }),

  accept: (sessionId: string, targets: { calories: number; protein: number; carbs: number; fat: number }) =>
    api.post<{ message: string; new_badges?: NewBadge[] }>(`/settings/targets/suggest/${sessionId}/accept`, {
      calories: targets.calories,
      protein: targets.protein,
      carbs: targets.carbs,
      fat: targets.fat,
    }),
};
