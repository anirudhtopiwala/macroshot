import { describe, it, expect, vi, beforeEach } from 'vitest';
import { aliasesApi } from './aliases';

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

describe('aliasesApi.log', () => {
  it('POSTs to correct URL with encoded alias name', async () => {
    mockOk({ ok: true, meal_id: 1, progress: {} });

    await aliasesApi.log('Chicken Rice');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/aliases/Chicken%20Rice/log');
  });

  it('sends no body when loggedAt is not provided', async () => {
    mockOk({ ok: true, meal_id: 1, progress: {} });

    await aliasesApi.log('Oatmeal');

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.body).toBeUndefined();
  });

  it('sends logged_at in body when loggedAt is provided', async () => {
    mockOk({ ok: true, meal_id: 2, progress: {} });

    await aliasesApi.log('Chicken Rice', '2026-03-15 14:30');

    const [, opts] = mockFetch.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ logged_at: '2026-03-15 14:30' });
  });

  it('encodes special characters in alias name', async () => {
    mockOk({ ok: true, meal_id: 3, progress: {} });

    await aliasesApi.log('Eggs & Toast');

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/aliases/Eggs%20%26%20Toast/log');
  });
});

describe('aliasesApi.list', () => {
  it('GETs /aliases', async () => {
    mockOk([]);

    await aliasesApi.list();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/aliases');
  });
});

describe('aliasesApi.create', () => {
  it('POSTs alias data to /aliases', async () => {
    const alias = {
      name: 'Oatmeal',
      item_name: 'Oatmeal',
      calories: 300,
      protein: 10,
      carbs: 50,
      fat: 5,
    };
    mockOk({ ...alias, id: 1 });

    await aliasesApi.create(alias);

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/aliases');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual(alias);
  });
});

describe('aliasesApi.delete', () => {
  it('DELETEs with encoded alias name', async () => {
    mockOk({ ok: true });

    await aliasesApi.delete('Chicken Rice');

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe('/macro_app/api/v1/aliases/Chicken%20Rice');
    expect(opts.method).toBe('DELETE');
  });
});
