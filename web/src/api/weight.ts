import { api } from './client';
import type { NewBadge } from '../types';

export interface WeightEntry {
  id: number;
  logged_at: string;
  weight_kg: number;
}

export interface WeightLogResponse extends WeightEntry {
  new_badges?: NewBadge[];
}

export interface WeightHistoryResponse {
  entries: WeightEntry[];
  latest: number | null;
  start: number | null;
  goal: number | null;
  last_logged_at: string | null;
}

export const weightApi = {
  log: (weight_kg: number, logged_at?: string) =>
    api.post<WeightLogResponse>('/weight', { weight_kg, logged_at }),

  history: (limit = 90) =>
    api.get<WeightHistoryResponse>(`/weight?limit=${limit}`),

  delete: (id: number) =>
    api.delete<{ ok: boolean }>(`/weight/${id}`),
};
