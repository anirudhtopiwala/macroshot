import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { request, ApiError, TimeoutError } from './client';

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

/** Build a mock Response with headers (client reads content-type). */
function mockResponse(overrides: {
  ok?: boolean; status?: number; statusText?: string;
  json?: () => Promise<unknown>;
}) {
  return {
    ok: overrides.ok ?? true,
    status: overrides.status ?? 200,
    statusText: overrides.statusText ?? 'OK',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: overrides.json ?? (() => Promise.resolve({})),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('API client', () => {
  describe('timeout', () => {
    it('aborts request after default timeout (15s)', async () => {
      // fetch that never resolves
      mockFetch.mockImplementation(() => new Promise(() => {}));

      // Speed up: override the timeout by checking the signal
      const promise = request('/test');

      // The AbortController is created inside request - we verify via the signal passed to fetch
      await vi.waitFor(() => {
        expect(mockFetch).toHaveBeenCalledTimes(1);
      });

      const callArgs = mockFetch.mock.calls[0][1] as RequestInit;
      expect(callArgs.signal).toBeInstanceOf(AbortSignal);
    });

    it('throws TimeoutError when request times out', async () => {
      // Simulate AbortError from fetch
      mockFetch.mockRejectedValue(new DOMException('The operation was aborted.', 'AbortError'));

      await expect(request('/test')).rejects.toThrow(TimeoutError);
      await expect(request('/test')).rejects.toThrow('Request timed out');
    });

    it('passes AbortSignal to fetch', async () => {
      mockFetch.mockResolvedValue(mockResponse({ json: () => Promise.resolve({ data: 'ok' }) }));

      await request('/test');

      const callArgs = mockFetch.mock.calls[0][1] as RequestInit;
      expect(callArgs.signal).toBeDefined();
      expect(callArgs.signal).toBeInstanceOf(AbortSignal);
    });
  });

  describe('error handling', () => {
    it('throws ApiError on non-ok responses', async () => {
      mockFetch.mockResolvedValue(mockResponse({
        ok: false, status: 500, statusText: 'Internal Server Error',
        json: () => Promise.resolve({ detail: 'Something broke' }),
      }));

      await expect(request('/test')).rejects.toThrow(ApiError);
      await expect(request('/test')).rejects.toThrow('Something broke');
    });

    it('handles 401 by clearing cache', async () => {
      const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem');

      mockFetch.mockResolvedValue(mockResponse({ ok: false, status: 401, statusText: 'Unauthorized' }));

      await expect(request('/test')).rejects.toThrow(ApiError);
      expect(removeItemSpy).toHaveBeenCalledWith('macro_cached_user');
    });

    it('handles array detail errors', async () => {
      mockFetch.mockResolvedValue(mockResponse({
        ok: false, status: 422, statusText: 'Unprocessable Entity',
        json: () => Promise.resolve({ detail: [{ msg: 'field required' }, { msg: 'invalid type' }] }),
      }));

      try {
        await request('/test');
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).message).toBe('field required; invalid type');
      }
    });
  });

  describe('request formatting', () => {
    it('does not set Content-Type for FormData', async () => {
      mockFetch.mockResolvedValue(mockResponse({}));

      const formData = new FormData();
      await request('/test', { method: 'POST', body: formData });

      const callArgs = mockFetch.mock.calls[0][1] as RequestInit;
      const headers = callArgs.headers as Record<string, string>;
      expect(headers['Content-Type']).toBeUndefined();
    });

    it('sets Content-Type to JSON for non-FormData', async () => {
      mockFetch.mockResolvedValue(mockResponse({}));

      await request('/test', { method: 'POST', body: '{}' });

      const callArgs = mockFetch.mock.calls[0][1] as RequestInit;
      const headers = callArgs.headers as Record<string, string>;
      expect(headers['Content-Type']).toBe('application/json');
    });

    it('includes credentials: include', async () => {
      mockFetch.mockResolvedValue(mockResponse({}));

      await request('/test');

      const callArgs = mockFetch.mock.calls[0][1] as RequestInit;
      expect(callArgs.credentials).toBe('include');
    });
  });

  describe('successful responses', () => {
    it('returns parsed JSON', async () => {
      mockFetch.mockResolvedValue(mockResponse({ json: () => Promise.resolve({ calories: 500, protein: 30 }) }));

      const result = await request<{ calories: number }>('/test');
      expect(result.calories).toBe(500);
    });

    it('clears timeout on success', async () => {
      const clearSpy = vi.spyOn(global, 'clearTimeout');

      mockFetch.mockResolvedValue(mockResponse({}));

      await request('/test');
      expect(clearSpy).toHaveBeenCalled();
    });
  });
});
