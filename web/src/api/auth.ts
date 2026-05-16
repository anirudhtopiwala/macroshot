import { api } from './client';
import type { UserMe } from '../types';

export interface WaitlistJoinResponse {
  ok: boolean;
  already_subscribed: boolean;
  message: string;
}

export const authApi = {
  googleAuth: (id_token: string) =>
    api.post<{ user_id: number; email: string }>('/auth/google', { id_token }),

  sendPin: (email: string) =>
    api.post<{ message: string }>('/auth/email/send-pin', { email }),

  verifyPin: (email: string, pin: string, first_name?: string, last_name?: string) =>
    api.post<{ user_id: number; email: string }>('/auth/email/verify-pin', {
      email, pin,
      ...(first_name ? { first_name } : {}),
      ...(last_name ? { last_name } : {}),
    }),

  joinWaitlist: (email: string, first_name?: string) =>
    api.post<WaitlistJoinResponse>('/auth/waitlist', {
      email,
      ...(first_name ? { first_name } : {}),
    }),

  refresh: () => api.post<{ message: string }>('/auth/refresh'),

  logout: () => api.post<{ message: string }>('/auth/logout'),

  me: () => api.get<UserMe>('/auth/me'),

  acceptTos: () => api.post<{ accepted: boolean }>('/auth/accept-tos'),
};
