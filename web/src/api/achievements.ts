import { api } from './client';
import type { AchievementsResponse, AchievementSummaryResponse, ChallengesResponse } from '../types';

export interface ShieldHistoryEntry {
  earned_at: string;
  used_at: string | null;
  bridged_date?: string | null;
}

export const achievementsApi = {
  getAll: () => api.get<AchievementsResponse>('/achievements'),
  getChallenges: () => api.get<ChallengesResponse>('/achievements/challenges'),
  getSummary: () => api.get<AchievementSummaryResponse>('/achievements/summary'),
  getShieldHistory: () => api.get<{ history: ShieldHistoryEntry[] }>('/achievements/shields/history'),
  markSeen: (badgeIds: string[]) => api.post('/achievements/seen', { badge_ids: badgeIds }),
};
