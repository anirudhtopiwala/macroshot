/**
 * Two-tier cache for API responses:
 * 1. In-memory Map - fastest, survives component unmount/remount
 * 2. localStorage fallback - survives app close/reload (instant launch)
 *
 * On getCached(): checks memory first, then localStorage.
 * On setCache(): writes to both memory and localStorage.
 * TTL: 5 minutes for in-session freshness; on cold launch, stale
 * localStorage data is returned immediately (better than a spinner)
 * and the caller fetches fresh data in the background.
 */

const store = new Map<string, { data: unknown; ts: number }>();

const MAX_AGE = 5 * 60_000; // 5 minutes
const LS_PREFIX = 'macro_cache:';

// On cold launch, allow stale localStorage data (up to 24h) so the
// user sees *something* instantly instead of a loading spinner.
const LS_MAX_AGE = 24 * 60 * 60_000; // 24 hours

function lsGet<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(LS_PREFIX + key);
    if (!raw) return null;
    const entry = JSON.parse(raw) as { data: T; ts: number };
    if (Date.now() - entry.ts > LS_MAX_AGE) {
      localStorage.removeItem(LS_PREFIX + key);
      return null;
    }
    return entry.data;
  } catch {
    return null;
  }
}

function lsSet(key: string, data: unknown, ts: number): void {
  try {
    localStorage.setItem(LS_PREFIX + key, JSON.stringify({ data, ts }));
  } catch {
    // localStorage full - silently ignore (in-memory cache still works)
  }
}

export function getCached<T>(key: string): T | null {
  // Tier 1: in-memory (with strict 5-min TTL)
  const entry = store.get(key);
  if (entry) {
    if (Date.now() - entry.ts <= MAX_AGE) {
      return entry.data as T;
    }
    store.delete(key);
  }
  // Tier 2: localStorage (with relaxed 24h TTL for cold launch)
  const lsData = lsGet<T>(key);
  if (lsData !== null) {
    // Promote to in-memory so subsequent reads are instant
    store.set(key, { data: lsData, ts: 0 }); // ts=0 so it won't pass MAX_AGE check → next getCached triggers background refresh
    return lsData;
  }
  return null;
}

export function setCache(key: string, data: unknown): void {
  const ts = Date.now();
  store.set(key, { data, ts });
  lsSet(key, data, ts);
}

export function clearCache(prefix?: string): void {
  if (!prefix) {
    store.clear();
    // Clear all localStorage cache entries
    try {
      const keys = Object.keys(localStorage).filter(k => k.startsWith(LS_PREFIX));
      keys.forEach(k => localStorage.removeItem(k));
    } catch { /* ignore */ }
    return;
  }
  for (const key of store.keys()) {
    if (key.startsWith(prefix)) store.delete(key);
  }
  // Also clear matching localStorage entries
  try {
    const keys = Object.keys(localStorage).filter(k => k.startsWith(LS_PREFIX + prefix));
    keys.forEach(k => localStorage.removeItem(k));
  } catch { /* ignore */ }
  // Also tell the SW to clear its runtime API cache so stale responses aren't served
  if (navigator.serviceWorker?.controller) {
    navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_API_CACHE' });
  }
}
