/**
 * Coordinates request timing so non-critical API calls wait for the
 * critical dashboard requests (today + trend) to finish first.
 * This prevents overwhelming the single-vCPU VM with 8+ concurrent requests.
 *
 * NOTE: Module-level state - staggering only applies on the FIRST Dashboard
 * mount per app session. Subsequent SPA navigations back to Dashboard resolve
 * immediately since _done stays true. This is intentional: the cold-start burst
 * is the only time all 8+ requests fire simultaneously.
 */

let _resolve: (() => void) | null = null;
let _done = false;

// Reset on every fresh page load (module-level state)
const _promise = new Promise<void>((r) => { _resolve = r; });

/** Called by Dashboard after trend + today complete (or fail). */
export function signalCriticalDone() {
  if (_done) return;
  _done = true;
  _resolve?.();
}

/**
 * Returns a promise that resolves when critical requests are done,
 * or after a 3s safety timeout (for non-Dashboard deep links where
 * signal never fires).
 */
export function waitForCritical(): Promise<void> {
  if (_done) return Promise.resolve();
  return Promise.race([
    _promise,
    new Promise<void>((r) => setTimeout(r, 3000)),
  ]);
}
