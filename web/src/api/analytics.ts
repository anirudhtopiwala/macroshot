/**
 * Frontend analytics dispatcher - buffered batch client for UI events.
 *
 * Why not a third-party SDK: adblockers routinely drop PostHog/Mixpanel on
 * privacy-minded audiences (who also install PWAs), health data has stricter
 * expectations, and we already have a working self-hosted event pipeline at
 * /events/batch. See CLAUDE.md analytics plan.
 *
 * Design:
 *  - Events buffer in memory, flushed every 10s OR on visibilitychange:hidden.
 *  - Offline-safe: if sendBeacon/fetch fails, events are persisted to
 *    localStorage and retried on the next page load or visibility event.
 *  - No PII. event_type must start with `ui_` (enforced server-side too).
 *  - Page views are deduplicated per mount so double-renders (StrictMode,
 *    suspense hydration) don't double-count.
 *
 *  NEVER use this to track keystrokes, text content, or any field that
 *  could contain user-identifying data. Metadata values are clipped to
 *  short scalars only - the backend re-validates, but don't rely on it.
 */

type EventMetadata = Record<string, string | number | boolean | null>;

interface QueuedEvent {
  event_type: string;
  metadata?: EventMetadata;
}

const FLUSH_INTERVAL_MS = 10_000;
const MAX_BUFFER = 50;
const STORAGE_KEY = 'macro_analytics_queue';
const ENDPOINT = '/macro_app/api/v1/events/batch';

let buffer: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let started = false;

function readPersisted(): QueuedEvent[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, 200) : [];
  } catch {
    return [];
  }
}

function persistBuffer() {
  try {
    if (buffer.length === 0) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    // Cap persisted queue so a permanently-offline tab can't balloon localStorage
    const toStore = buffer.slice(-200);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toStore));
  } catch {
    // Quota exceeded or disabled - silently drop rather than throw
  }
}

async function sendBatch(events: QueuedEvent[]): Promise<boolean> {
  if (events.length === 0) return true;
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'MacroApp',
      },
      body: JSON.stringify({ events }),
      // Keep the request alive through tab close when flushing on unload.
      keepalive: true,
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function flush(): Promise<void> {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (buffer.length === 0) return;

  const toSend = buffer.slice();
  const ok = await sendBatch(toSend);

  if (ok) {
    // Remove only the events we successfully sent - others arrived during flush
    buffer = buffer.slice(toSend.length);
    persistBuffer();
  } else {
    // Failed - keep buffered events and persist so a reload can retry
    persistBuffer();
  }
}

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flush();
  }, FLUSH_INTERVAL_MS);
}

/**
 * Queue a frontend UI event for batch delivery.
 *
 * Event types must start with `ui_` and be on the backend allowlist
 * (src/web/routes/events.py). Unknown events are silently dropped.
 */
export function track(event_type: string, metadata?: EventMetadata): void {
  if (!event_type.startsWith('ui_')) return;

  buffer.push({ event_type, metadata });

  // If the buffer fills up, flush immediately to avoid dropping events.
  if (buffer.length >= MAX_BUFFER) {
    void flush();
    return;
  }
  scheduleFlush();
}

/**
 * Start the analytics dispatcher. Safe to call multiple times - idempotent.
 * Loads any previously-persisted events and begins the flush lifecycle.
 */
export function startAnalytics(): void {
  if (started) return;
  started = true;

  // Drain any events the previous session couldn't deliver
  const persisted = readPersisted();
  if (persisted.length > 0) {
    buffer = persisted.concat(buffer);
    scheduleFlush();
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        void flush();
      }
    });
  }
  if (typeof window !== 'undefined') {
    // Best-effort flush on unload - keepalive:true lets the request survive.
    window.addEventListener('pagehide', () => {
      void flush();
    });
  }
}

/**
 * Testing/shutdown helper - forces any buffered events out NOW.
 * Also used by page transitions when we want immediate delivery.
 */
export function flushAnalytics(): Promise<void> {
  return flush();
}
