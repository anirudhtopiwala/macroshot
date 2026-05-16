import { describe, it, expect, vi, beforeEach } from 'vitest';
import { targetsApi } from './targets';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function mockOk(data: unknown) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve(data),
  });
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe('targetsApi', () => {
  it('suggest POSTs to /settings/targets/suggest', async () => {
    const body = { goal: 'lose_weight', activity_level: 'active', age: 30 };
    mockOk({ session_id: 'abc', targets: null, explanation: '', reply_text: '', error: null });

    await targetsApi.suggest(body);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/settings/targets/suggest');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual(body);
  });

  it('refine POSTs to correct URL with session ID', async () => {
    mockOk({ session_id: 'xyz', targets: null, explanation: '', reply_text: 'ok', error: null });

    await targetsApi.refine('my-session-id', 'more protein');

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/settings/targets/suggest/my-session-id/refine');
    expect(JSON.parse(opts.body)).toEqual({ text: 'more protein' });
  });

  it('accept POSTs targets to correct URL', async () => {
    mockOk({ message: 'Targets saved' });

    const targets = { calories: 2000, protein: 150, carbs: 200, fat: 70 };
    await targetsApi.accept('sess-123', targets);

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/settings/targets/suggest/sess-123/accept');
    expect(JSON.parse(opts.body)).toEqual(targets);
  });
});
