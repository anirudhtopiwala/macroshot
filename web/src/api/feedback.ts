import { api } from './client';

export const feedbackApi = {
  submit: (data: {
    type: 'bug' | 'feature' | 'other';
    message: string;
    page?: string;
  }) => api.post<{ ok: boolean }>('/settings/feedback', data),

  mealFeedback: (mealId: number, rating: 0 | 1, comment?: string) =>
    api.post<{ ok: boolean }>(`/meals/${mealId}/feedback`, {
      rating,
      ...(comment ? { comment } : {}),
    }),
};
