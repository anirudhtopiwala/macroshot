import { api } from './client';
import type { Nutrition } from '../types';

/** Subset of /meals/analyze AnalyzeResponse - no session_id (no DB row). */
export interface GuestAnalyzeResponse {
  nutrition: Nutrition | null;
  raw_text: string;
  error: string | null;
}

export interface ImportGuestMealItem {
  nutrition: Nutrition;
  /** "YYYY-MM-DD HH:MM[:SS]" - same pattern as AcceptRequest.logged_at. */
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

/** Subset of /meals/barcode AnalyzeResponse - no session_id (no DB row). */
export interface GuestBarcodeResponse {
  nutrition: Nutrition | null;
  error: string | null;
  image_url: string | null;
  serving_label: string | null;
  serving_size_g: number | null;
  serving_size_unit: 'g' | 'ml';
}

export interface ImportGuestWeightItem {
  weight_kg: number;
  logged_at: string;
}

export interface ImportGuestWeightsResponse {
  imported: number;
  already_imported: boolean;
}

export const guestApi = {
  analyze: (formData: FormData) =>
    api.post<GuestAnalyzeResponse>('/guest/analyze', formData),

  barcodeLookup: (barcode: string, servings = 1) =>
    api.post<GuestBarcodeResponse>('/guest/barcode', { barcode, servings }),

  importGuestMeals: (meals: ImportGuestMealItem[]) =>
    api.post<ImportGuestMealsResponse>('/meals/import-guest', { meals }),

  importGuestWeights: (entries: ImportGuestWeightItem[]) =>
    api.post<ImportGuestWeightsResponse>('/weight/import-guest', { entries }),

  importStatus: () => api.get<ImportGuestStatus>('/meals/import-guest/status'),
};
