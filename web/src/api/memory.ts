import { api } from './client';

export type MemoryKind = 'allergy' | 'restriction' | 'preference' | 'note';

export interface MemoryEntry {
  id: number;
  kind: MemoryKind;
  text: string;
  source: 'user' | 'coach_suggested';
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  entries: MemoryEntry[];
}

export const memoryApi = {
  list: () => api.get<MemoryListResponse>('/memory'),

  create: (kind: MemoryKind, text: string) =>
    api.post<{ id: number; kind: MemoryKind; text: string }>('/memory', { kind, text }),

  update: (id: number, fields: { kind?: MemoryKind; text?: string }) =>
    api.put<{ ok: boolean; id: number }>(`/memory/${id}`, fields),

  delete: (id: number) =>
    api.delete<{ ok: boolean }>(`/memory/${id}`),
};
