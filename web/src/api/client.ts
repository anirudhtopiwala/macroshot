import * as Sentry from '@sentry/react';

const BASE = '/macro_app/api/v1';

const DEFAULT_TIMEOUT = 15_000;   // 15s for normal reads/writes
const UPLOAD_TIMEOUT = 120_000;   // 2min for image uploads (Gemini analysis)
const CHAT_TIMEOUT = 90_000;      // 90s for AI chat (MCP + Gemini tool calls)

export class ApiError extends Error {
  /**
   * Raw `detail` payload from the backend. Backends like FastAPI return
   * structured error bodies (e.g. `{detail: {error: 'beta_full', ...}}`) that
   * callers want to branch on - we stash the parsed object here so they can,
   * while `message` stays a human-readable string for fallback rendering.
   */
  public detail: unknown;
  constructor(public status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.detail = detail;
  }
}

export class TimeoutError extends ApiError {
  constructor() {
    super(0, 'Request timed out');
    this.name = 'TimeoutError';
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

const SLOW_THRESHOLD = 3000; // Log requests slower than 3s

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = `${BASE}${path}`;
  const { timeoutMs, ...fetchOptions } = options;
  const isUpload = fetchOptions.body instanceof FormData;
  const timeout = timeoutMs ?? (isUpload ? UPLOAD_TIMEOUT : DEFAULT_TIMEOUT);
  const t0 = performance.now();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const { headers: optHeaders, ...restOptions } = fetchOptions;
    const res = await fetch(url, {
      credentials: 'include',
      ...restOptions,
      headers: {
        'X-Requested-With': 'MacroApp',
        'X-App-Version': typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown',
        ...(isUpload ? {} : { 'Content-Type': 'application/json' }),
        ...(optHeaders instanceof Headers ? Object.fromEntries(optHeaders.entries()) : optHeaders),
      },
      signal: controller.signal,
    });

    if (res.status === 401) {
      // Guest mode: the visitor has no cookie by design. Any 401 here
      // is expected (Dashboard/today, /events/batch, /subscription/me,
      // etc.). Throw a normal ApiError so callers can decide how to
      // degrade — but DO NOT wipe cached state and DO NOT redirect to
      // /login, which would yank them off the app shell.
      let isGuest = false;
      try { isGuest = localStorage.getItem('macro_guest_mode') === '1'; } catch { /* ignore */ }
      if (isGuest) {
        throw new ApiError(401, 'Unauthorized');
      }
      localStorage.removeItem('macro_cached_user');
      // Also wipe the cached subscription snapshot so a user signing in
      // next on this browser doesn't briefly see the prior user's plan
      // state. Server is authoritative, but the cache drives the first
      // paint while /subscription refetches.
      localStorage.removeItem('macro_cached_subscription');
      // Don't redirect when the user is already on a public page - otherwise
      // the initial /auth/me probe from AuthProvider bounces unauth visitors
      // off /about, /terms, /privacy (all of which are meant to be public).
      const path = window.location.pathname;
      const PUBLIC_PREFIXES = ['/macro_app/login', '/macro_app/about', '/macro_app/terms', '/macro_app/privacy'];
      const onPublicPage = PUBLIC_PREFIXES.some(
        (p) => path === p || path.startsWith(p + '/')
      );
      if (!onPublicPage && !(window as unknown as Record<string, unknown>).__401_redirect_pending) {
        (window as unknown as Record<string, unknown>).__401_redirect_pending = true;
        window.location.href = '/macro_app/login';
      }
      throw new ApiError(401, 'Unauthorized');
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = body.detail;
      const message =
        (typeof detail === 'string' && detail) ? detail
        : Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg || String(e)).join('; ')
        : detail?.message || detail?.error || res.statusText || `Error ${res.status}`;
      throw new ApiError(res.status, message || `Error ${res.status}`, detail);
    }

    // Handle empty responses (204, etc.)
    const contentType = res.headers.get('content-type');
    if (res.status === 204 || !contentType?.includes('application/json')) {
      return {} as T;
    }

    const elapsed = Math.round(performance.now() - t0);
    if (elapsed > SLOW_THRESHOLD && Sentry.isInitialized()) {
      Sentry.addBreadcrumb({
        category: 'perf',
        message: `Slow API: ${fetchOptions.method || 'GET'} ${path} ${res.status} ${elapsed}ms`,
        level: 'warning',
        data: { path, method: fetchOptions.method || 'GET', status: res.status, elapsed },
      });
    }

    return res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      const timeoutErr = new TimeoutError();
      if (Sentry.isInitialized()) Sentry.captureException(timeoutErr, { tags: { type: 'timeout' } });
      throw timeoutErr;
    }
    // Report server errors (5xx) and unexpected failures to Sentry
    if (Sentry.isInitialized() && err instanceof ApiError && err.status >= 500) {
      Sentry.captureException(err);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: <T>(path: string, opts?: { refresh?: boolean }) =>
    request<T>(path, opts?.refresh ? { headers: { 'X-Refresh': '1' } } : {}),
  post: <T>(path: string, body?: unknown, opts?: { timeoutMs?: number }) =>
    request<T>(path, {
      method: 'POST',
      timeoutMs: opts?.timeoutMs,
      ...(body !== undefined
        ? { body: body instanceof FormData ? body : JSON.stringify(body) }
        : {}),
    }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** POST with extended timeout for AI chat endpoints */
  postChat: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      timeoutMs: CHAT_TIMEOUT,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
};
