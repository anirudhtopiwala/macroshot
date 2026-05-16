import { api } from './client';
import type { SubscriptionStatus } from '../types';

export const subscriptionApi = {
  status: () => api.get<SubscriptionStatus>('/subscription'),
  startTrial: () =>
    api.post<{ message: string; plan: string; is_premium: boolean }>('/subscription/trial'),
  checkout: (plan: string) =>
    api.post<{ url?: string; error?: string }>('/subscription/checkout', { plan }),
  portal: () => api.post<{ url?: string; error?: string }>('/subscription/portal'),
};
