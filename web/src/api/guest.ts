import { api } from './client';
import type { Nutrition } from '../types';

/** Subset of /meals/analyze AnalyzeResponse — no session_id (no DB row). */
export interface GuestAnalyzeResponse {
  nutrition: Nutrition | null;
  raw_text: string;
  error: string | null;
}

export interface ImportGuestMealItem {
  nutrition: Nutrition;
  /** "YYYY-MM-DD HH:MM[:SS]" — same pattern as AcceptRequest.logged_at. */
  logged_at: string;
  meal_type?: string;
  user_input?: string;
}

export interface ImportGuestMealsResponse {
  imported: number;
  already_imported: boolean;
}

export interface ImportGuestStatus {
  already_imported: boolean;
  imported_at: string | null;
}

export const guestApi = {
  analyze: (formData: FormData) =>
    api.post<GuestAnalyzeResponse>('/guest/analyze', formData),

  importGuestMeals: (meals: ImportGuestMealItem[]) =>
    api.post<ImportGuestMealsResponse>('/meals/import-guest', { meals }),

  importStatus: () => api.get<ImportGuestStatus>('/meals/import-guest/status'),
};
