import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mealsApi } from './meals';

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

describe('mealsApi.accept', () => {
  it('POSTs to correct URL with session ID', async () => {
    mockOk({ meal_id: 1, error: null });

    await mealsApi.accept('sess-abc');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/meals/sessions/sess-abc/accept');
    expect(opts.method).toBe('POST');
  });

  it('sends no body when no nutrition or loggedAt', async () => {
    mockOk({ meal_id: 1, error: null });

    await mealsApi.accept('sess-abc');

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.body).toBeUndefined();
  });

  it('sends logged_at in body when loggedAt is provided', async () => {
    mockOk({ meal_id: 2, error: null });

    await mealsApi.accept('sess-abc', undefined, '2026-03-20 14:00');

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.logged_at).toBe('2026-03-20 14:00');
    expect(body.nutrition).toBeUndefined();
  });

  it('sends both nutrition and logged_at when both provided', async () => {
    mockOk({ meal_id: 3, error: null });

    const nutrition = {
      item_name: 'Test',
      calories: 500,
      protein: 30,
      carbs: 60,
      fat: 15,
      items: [],
    };
    await mealsApi.accept('sess-abc', nutrition as any, '2026-03-20 08:00');

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.logged_at).toBe('2026-03-20 08:00');
    expect(body.nutrition).toEqual(nutrition);
  });

  it('sends only nutrition when loggedAt is not provided', async () => {
    mockOk({ meal_id: 4, error: null });

    const nutrition = {
      item_name: 'Test',
      calories: 300,
      protein: 20,
      carbs: 40,
      fat: 10,
      items: [],
    };
    await mealsApi.accept('sess-abc', nutrition as any);

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.nutrition).toEqual(nutrition);
    expect(body.logged_at).toBeUndefined();
  });
});

describe('mealsApi.cancel', () => {
  it('POSTs to cancel URL', async () => {
    mockOk({ ok: true });

    await mealsApi.cancel('sess-xyz');

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/meals/sessions/sess-xyz/cancel');
    expect(opts.method).toBe('POST');
  });
});

describe('mealsApi.list', () => {
  it('includes date param in query string', async () => {
    mockOk([]);

    await mealsApi.list({ date: '2026-03-20' });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('date=2026-03-20');
  });

  it('includes type param in query string', async () => {
    mockOk([]);

    await mealsApi.list({ type: 'breakfast' });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('type=breakfast');
  });
});
