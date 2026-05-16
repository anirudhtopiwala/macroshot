// Module-scoped pending-delete scheduler. Lives outside React so the
// 5s grace timer keeps running across SPA navigation / unmount — the
// previous in-component setTimeout was being cleared on unmount, which
// silently swallowed delete requests when users left Journal/Dashboard
// during the undo window.

import { mealsApi } from '../api/meals';
import { clearCache } from './apiCache';

const UNDO_DELAY_MS = 5000;

interface Pending {
  timer: ReturnType<typeof setTimeout>;
  fire: () => Promise<void>;
}

const pending = new Map<number, Pending>();

interface ScheduleOptions {
  cachesToClear?: string[];
}

export function schedulePendingDelete(id: number, opts: ScheduleOptions = {}): void {
  // If another schedule comes in for the same id, cancel the prior timer
  // so we don't end up with two DELETEs queued.
  const prior = pending.get(id);
  if (prior) clearTimeout(prior.timer);

  const fire = async () => {
    pending.delete(id);
    try {
      await mealsApi.delete(id);
      (opts.cachesToClear ?? []).forEach((k) => clearCache(k));
    } catch (e: unknown) {
      // 404 = already deleted (e.g., from another tab). Treat as success.
      if (e && typeof e === 'object' && 'status' in e && (e as { status: number }).status === 404) return;
      window.dispatchEvent(new CustomEvent('app-toast', {
        detail: { message: 'Failed to delete meal', type: 'error' },
      }));
    }
  };

  const timer = setTimeout(fire, UNDO_DELAY_MS);
  pending.set(id, { timer, fire });
}

export function cancelPendingDelete(id: number): boolean {
  const p = pending.get(id);
  if (!p) return false;
  clearTimeout(p.timer);
  pending.delete(id);
  return true;
}

export function isDeletePending(id: number): boolean {
  return pending.has(id);
}

// Best-effort flush when the page is being hidden / closed. Cancels the
// timers and fires the API calls immediately so they don't get stranded.
function flushAll(): void {
  for (const [, p] of pending) {
    clearTimeout(p.timer);
    void p.fire();
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', flushAll);
}
