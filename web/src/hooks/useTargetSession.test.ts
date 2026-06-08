import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTargetSession } from './useTargetSession';

vi.mock('../api/targets', () => ({
  targetsApi: {
    suggest: vi.fn(),
    refine: vi.fn(),
    accept: vi.fn(),
  },
}));

import { targetsApi } from '../api/targets';

beforeEach(() => {
  vi.mocked(targetsApi.suggest).mockReset();
  vi.mocked(targetsApi.refine).mockReset();
  vi.mocked(targetsApi.accept).mockReset();
});

describe('useTargetSession', () => {
  // ── suggest ──

  it('sets targets, sessionId, and explanation on success', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-1',
      targets: { calories: 2100, protein: 160, carbs: 230, fat: 65 },
      explanation: 'Good targets for you.',
      reply_text: 'raw',
      error: null,
    });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    expect(result.current.sessionId).toBe('sess-1');
    expect(result.current.targets?.calories).toBe(2100);
    expect(result.current.explanation).toBe('Good targets for you.');
    expect(result.current.error).toBeNull();
    expect(result.current.suggesting).toBe(false);
  });

  it('clears previous state before a new suggest call', async () => {
    vi.mocked(targetsApi.suggest)
      .mockResolvedValueOnce({
        session_id: 'sess-1', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
        explanation: 'first', reply_text: '', error: null,
      })
      .mockResolvedValueOnce({
        session_id: 'sess-2', targets: { calories: 2500, protein: 180, carbs: 280, fat: 80 },
        explanation: 'second', reply_text: '', error: null,
      });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });
    expect(result.current.sessionId).toBe('sess-1');

    await act(async () => {
      await result.current.suggest({ goal: 'gain_weight', activity_level: 'very_active' });
    });
    expect(result.current.sessionId).toBe('sess-2');
    expect(result.current.targets?.calories).toBe(2500);
    expect(result.current.messages).toHaveLength(0);
  });

  it('sets error from API response (soft error)', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-err', targets: null, explanation: '', reply_text: '',
      error: 'Model overloaded',
    });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    expect(result.current.error).toBe('Model overloaded');
    expect(result.current.targets).toBeNull();
  });

  it('sets error on network failure', async () => {
    vi.mocked(targetsApi.suggest).mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useTargetSession());

    const res = await act(async () => {
      return result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    expect(res).toBeNull();
    expect(result.current.error).toBe('Network error');
    expect(result.current.suggesting).toBe(false);
  });

  // ── refine ──

  it('appends user message immediately, then assistant reply', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-r', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
      explanation: '', reply_text: '', error: null,
    });

    vi.mocked(targetsApi.refine).mockResolvedValue({
      session_id: 'sess-r',
      targets: { calories: 2000, protein: 200, carbs: 180, fat: 70 },
      explanation: 'More protein.',
      reply_text: 'I increased protein to 200g.',
      error: null,
    });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    await act(async () => {
      await result.current.refine('more protein please');
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0]).toEqual({ role: 'user', text: 'more protein please' });
    expect(result.current.messages[1]).toEqual({
      role: 'assistant',
      text: 'I increased protein to 200g.',
      macrosUpdated: true,    // refine returned new targets
      noUpdateHint: false,    // no user_requested_change in mock, so hint stays suppressed
    });
    expect(result.current.targets?.protein).toBe(200);
  });

  it('is a no-op when sessionId is null', async () => {
    const { result } = renderHook(() => useTargetSession());

    const res = await act(async () => {
      return result.current.refine('hello');
    });

    expect(res).toBeNull();
    expect(targetsApi.refine).not.toHaveBeenCalled();
  });

  it('accumulates messages across multiple refine rounds', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-m', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
      explanation: '', reply_text: '', error: null,
    });

    vi.mocked(targetsApi.refine)
      .mockResolvedValueOnce({
        session_id: 'sess-m', targets: { calories: 2000, protein: 180, carbs: 200, fat: 70 },
        explanation: '', reply_text: 'Done, more protein.', error: null,
      })
      .mockResolvedValueOnce({
        session_id: 'sess-m', targets: { calories: 1800, protein: 180, carbs: 180, fat: 60 },
        explanation: '', reply_text: 'Lowered calories too.', error: null,
      });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    await act(async () => { await result.current.refine('more protein'); });
    await act(async () => { await result.current.refine('lower calories'); });

    expect(result.current.messages).toHaveLength(4);
    expect(result.current.messages[0].role).toBe('user');
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[2].role).toBe('user');
    expect(result.current.messages[3].role).toBe('assistant');
    expect(result.current.targets?.calories).toBe(1800);
  });

  // ── accept ──

  it('calls API with the provided targets', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-a', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
      explanation: '', reply_text: '', error: null,
    });
    vi.mocked(targetsApi.accept).mockResolvedValue({ message: 'Targets saved' });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    const userEdited = { calories: 1900, protein: 160, carbs: 190, fat: 65 };
    let res: Awaited<ReturnType<typeof result.current.accept>>;
    await act(async () => {
      res = await result.current.accept(userEdited);
    });

    expect(res).toEqual({ message: 'Targets saved' });
    expect(targetsApi.accept).toHaveBeenCalledWith('sess-a', userEdited);
  });

  it('returns null on accept failure', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-f', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
      explanation: '', reply_text: '', error: null,
    });
    vi.mocked(targetsApi.accept).mockRejectedValue(new Error('Server error'));

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });

    let res: Awaited<ReturnType<typeof result.current.accept>>;
    await act(async () => {
      res = await result.current.accept({ calories: 2000, protein: 150, carbs: 200, fat: 70 });
    });

    expect(res).toBeNull();
    expect(result.current.error).toBe('Server error');
  });

  // ── reset ──

  it('clears all state', async () => {
    vi.mocked(targetsApi.suggest).mockResolvedValue({
      session_id: 'sess-reset', targets: { calories: 2000, protein: 150, carbs: 200, fat: 70 },
      explanation: 'test', reply_text: '', error: null,
    });

    const { result } = renderHook(() => useTargetSession());

    await act(async () => {
      await result.current.suggest({ goal: 'maintain', activity_level: 'active' });
    });
    expect(result.current.sessionId).toBe('sess-reset');

    act(() => { result.current.reset(); });

    expect(result.current.sessionId).toBeNull();
    expect(result.current.targets).toBeNull();
    expect(result.current.explanation).toBe('');
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.error).toBeNull();
  });
});
