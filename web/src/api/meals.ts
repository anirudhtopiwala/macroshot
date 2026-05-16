import { api } from './client';
import { formatLocalDateTime } from '../utils/date';
import type { AnalyzeResponse, CorrectionResponse, AcceptResponse, Meal, Nutrition } from '../types';

export interface RecentMeal {
  id: number;
  item_name: string;
  meal_description: string;
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
  meal_type: string;
  logged_at: string;
}

export const mealsApi = {
  analyze: (formData: FormData) =>
    api.post<AnalyzeResponse>('/meals/analyze', formData),

  correct: (sessionId: string, text: string) =>
    api.post<CorrectionResponse>(`/meals/sessions/${sessionId}/correct`, { text }, { timeoutMs: 45_000 }),

  itemRemoved: (sessionId: string, itemName: string) =>
    api.post<{ ok: boolean; skipped?: string }>(`/meals/sessions/${sessionId}/item-removed`, { item_name: itemName }),

  accept: (sessionId: string, nutrition?: Nutrition, loggedAt?: string, servings?: number) => {
    const body: Record<string, unknown> = {};
    if (nutrition) body.nutrition = nutrition;
    if (loggedAt) body.logged_at = loggedAt;
    if (servings !== undefined && servings !== 1) body.servings = servings;
    return api.post<AcceptResponse>(
      `/meals/sessions/${sessionId}/accept`,
      Object.keys(body).length > 0 ? body : undefined,
    );
  },

  cancel: (sessionId: string) =>
    api.post<{ ok: boolean }>(`/meals/sessions/${sessionId}/cancel`),

  list: (params?: { date?: string; type?: string; limit?: number; offset?: number; refresh?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.date) qs.set('date', params.date);
    if (params?.type) qs.set('type', params.type);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    return api.get<Meal[]>(`/meals?${qs}`, { refresh: params?.refresh });
  },

  get: (id: number) => api.get<Meal>(`/meals/${id}`),

  update: (id: number, data: Partial<Meal>) =>
    api.put<Meal>(`/meals/${id}`, data),

  delete: (id: number) => api.delete<{ ok: boolean }>(`/meals/${id}`),

  createEditSession: (mealId: number) =>
    api.post<AnalyzeResponse>(`/meals/${mealId}/edit-session`),

  relog: (mealId: number, loggedAt?: string) =>
    api.post<{ ok: boolean; meal_id: number; new_badges?: import('../types').NewBadge[] }>(`/meals/${mealId}/relog`, { logged_at: loggedAt || formatLocalDateTime() }),

  barcodeLookup: (barcode: string, servings?: number, mealType?: string) =>
    api.post<AnalyzeResponse>('/meals/barcode', { barcode, servings, meal_type: mealType }),

  qrScan: (payload: string, mealType?: string) =>
    api.post<AnalyzeResponse>('/meals/qr', { payload, meal_type: mealType }, { timeoutMs: 45_000 }),

  resetBarcodeCorrection: (barcode: string) =>
    api.delete<{ ok: boolean; removed: boolean }>(`/meals/barcode/${encodeURIComponent(barcode)}/correction`),

  copyDay: (sourceDate: string, targetDate?: string) =>
    api.post<{ ok: boolean; copied_count: number; meal_ids: number[]; new_badges?: import('../types').NewBadge[] }>(
      '/meals/copy-day',
      { source_date: sourceDate, ...(targetDate ? { target_date: targetDate } : {}) },
    ),

  recentUnique: () => api.get<RecentMeal[]>('/meals/recent-unique'),

  search: (q: string, limit = 20) => {
    const qs = new URLSearchParams({ q, limit: String(limit) });
    return api.get<Meal[]>(`/meals/search?${qs}`);
  },
};
