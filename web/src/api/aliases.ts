import { api } from './client';
import type { Alias, AnalyzeResponse } from '../types';

export const aliasesApi = {
  list: () => api.get<Alias[]>('/aliases'),

  create: async (data: {
    name: string;
    item_name: string;
    meal_description?: string;
    calories: number;
    protein: number;
    carbs: number;
    fat: number;
    items_json?: string;
  }) => {
    const result = await api.post<Alias>('/aliases', data);
    window.dispatchEvent(new Event('quota-used'));
    return result;
  },

  delete: (name: string) => api.delete<{ ok: boolean }>(`/aliases/${encodeURIComponent(name)}`),

  reorder: (names: string[]) => api.put<{ ok: boolean }>('/aliases/reorder', { names }),

  createEditSession: (name: string) =>
    api.post<AnalyzeResponse>(`/aliases/${encodeURIComponent(name)}/edit-session`),

  log: (name: string, loggedAt?: string) =>
    api.post<{ ok: boolean; meal_id: number; progress: Record<string, unknown>; new_badges?: import('../types').NewBadge[] }>(
      `/aliases/${encodeURIComponent(name)}/log`,
      loggedAt ? { logged_at: loggedAt } : undefined,
    ),
};
