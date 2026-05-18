/**
 * Service Worker lifecycle manager.
 *
 * Silent auto-update model: a newly installed SW stays in the waiting
 * state and activates naturally the next time all clients of the old
 * SW are closed (e.g. user closes and reopens the PWA). We do NOT
 * force a reload or auto-send SKIP_WAITING - that's what caused the
 * March 2026 reload loop (old page still referencing old chunk
 * hashes).
 *
 * The exported applyUpdate() + update-available callbacks remain so
 * the Settings "Check for Updates" button can offer a manual path.
 */

import { clearCache } from './apiCache';

type UpdateCallback = () => void;
export type PrecacheProgress = { done: number; total: number };
type ProgressCallback = (p: PrecacheProgress) => void;

let _registration: ServiceWorkerRegistration | null = null;
let _updateAvailable = false;
let _onUpdateCallbacks: UpdateCallback[] = [];
let _precacheProgress: PrecacheProgress = { done: 0, total: 0 };
let _onProgressCallbacks: ProgressCallback[] = [];

/**
 * Narrow clear: wipe only stale API-response caches.
 *   - In-memory apiCache Map + `macro_cache:*` localStorage entries (clearCache)
 *   - `macro-api-v1` SW cache (directly via Cache API, not via SW message,
 *     so the deletion completes before we hand control to the caller)
 *
 * Deliberately does NOT touch: auth cookies, tooltip/onboarded localStorage
 * keys, offline queue (IndexedDB), `macro-images-v1` cache (meal photos -
 * critical for offline), or the Workbox precache (app shell).
 */
export async function narrowClearApiCaches(): Promise<void> {
  clearCache();
  try {
    await caches.delete('macro-api-v1');
  } catch {
    // Best effort - nothing catastrophic if this fails.
  }
}

/** Whether a SW update is fully precached and ready to apply */
export function isUpdateAvailable(): boolean {
  return _updateAvailable;
}

/** Subscribe to update availability changes */
export function onUpdateAvailable(cb: UpdateCallback): () => void {
  _onUpdateCallbacks.push(cb);
  // If already available, fire immediately
  if (_updateAvailable) cb();
  return () => {
    _onUpdateCallbacks = _onUpdateCallbacks.filter((c) => c !== cb);
  };
}

function notifyUpdateAvailable() {
  _updateAvailable = true;
  _onUpdateCallbacks.forEach((cb) => cb());
  window.dispatchEvent(new CustomEvent('sw-update-available'));
}

/** Live precache progress (done / total files cached so far for the
 * current install). `total` is the manifest size; `done` only counts fresh
 * fetches, so unchanged files don't increment it. */
export function getPrecacheProgress(): PrecacheProgress {
  return _precacheProgress;
}

/** Subscribe to precache progress updates during a SW install. */
export function onPrecacheProgress(cb: ProgressCallback): () => void {
  _onProgressCallbacks.push(cb);
  return () => {
    _onProgressCallbacks = _onProgressCallbacks.filter((c) => c !== cb);
  };
}

function notifyPrecacheProgress(p: PrecacheProgress) {
  _precacheProgress = p;
  _onProgressCallbacks.forEach((cb) => {
    try { cb(p); } catch { /* ignore */ }
  });
}

/**
 * Check for a new SW version. Returns true once an update is found and fully
 * precached (i.e. reload will be instant).
 *
 * `onDownloading` fires once a new SW enters `installing` state, so callers
 * can swap a "Checking..." UI for "Downloading update...".
 *
 * Observes the SW lifecycle through the same notifyUpdateAvailable signal
 * that the long-lived updatefound listener (initServiceWorker) and the
 * UpdateToast rely on. The previous approach polled reg.installing /
 * reg.waiting for 3s after reg.update() resolved and bailed if neither was
 * set — a structural blind spot, because the browser (especially iOS Safari)
 * routinely resolves update() before transitioning the new worker into
 * `installing`. The install would complete seconds later, the long-lived
 * listener would fire notifyUpdateAvailable, and the user would see the
 * "New version available" toast appear moments after dismissing our
 * "App is on latest version" modal. Subscribing here means the tap path,
 * the popstate path, and the reload path all converge on one signal.
 *
 * Budget: 60s. Precache is ~10 MB; slow mobile networks regularly take 30-60s.
 */
export async function checkForUpdate(onDownloading?: () => void): Promise<boolean> {
  if (!_registration) return false;
  const reg = _registration;
  if (reg.waiting) return true;

  try {
    await reg.update();
  } catch {
    return false;
  }
  if (reg.waiting) return true;

  return new Promise<boolean>((resolve) => {
    let settled = false;
    let downloadingFired = false;

    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      clearInterval(installingWatcher);
      unsubAvailable();
      resolve(value);
    };

    // Primary signal: fires when any install (ours, popstate's, or
    // visibilitychange's) transitions a worker to `installed` (waiting).
    const unsubAvailable = onUpdateAvailable(() => finish(true));

    // Secondary: switch the UI from "Checking…" to "Downloading…" the
    // moment the browser actually assigns an installing worker. Polled
    // because there's no DOM event that fires on the installing→null
    // transition into installing, and we don't want to attach a one-shot
    // updatefound listener (the long-lived one in initServiceWorker
    // already handles the install→waiting bookkeeping).
    const installingWatcher = setInterval(() => {
      if (settled) return;
      if (reg.waiting) {
        finish(true);
        return;
      }
      if (reg.installing && !downloadingFired) {
        downloadingFired = true;
        try { onDownloading?.(); } catch { /* best effort */ }
      }
    }, 100);

    const timeoutId = setTimeout(() => finish(!!reg.waiting), 60_000);
  });
}

/**
 * Tell the new SW to skipWaiting and take over.
 * The page will reload automatically via the controllerchange listener.
 *
 * If the new SW is still `installing` (precache not yet finished), the
 * SKIP_WAITING is queued and sent the moment it transitions to `installed`.
 * Without this, an early tap silently dropped the message and the user
 * would see "Updating…" stuck until the SW finished installing on its own.
 */
// sessionStorage key that signals "we deliberately asked a new SW to take
// over; the controllerchange that follows is OURS, please reload." Without
// this gate, iOS Safari has been seen to fire spurious controllerchange
// events on PWA reloads (likely a race between SW takeover bookkeeping and
// the new page's controller binding) — the legacy handler honored them and
// triggered a second window.location.reload() mid-mount, which tore down
// the Suspense subtree and left the user with header + bottom nav but a
// blank route content area. Only honoring our own intent eliminates that.
const SW_APPLY_INTENT_KEY = 'sw-apply-intent';

export function applyUpdate(): void {
  const waiting = _registration?.waiting;
  if (waiting) {
    try { sessionStorage.setItem(SW_APPLY_INTENT_KEY, '1'); } catch { /* ignore */ }
    waiting.postMessage({ type: 'SKIP_WAITING' });
    return;
  }
  const installing = _registration?.installing;
  if (installing) {
    const onState = () => {
      if (installing.state === 'installed') {
        installing.removeEventListener('statechange', onState);
        // Now in waiting state — re-read from registration to be safe.
        const w = _registration?.waiting ?? installing;
        try { sessionStorage.setItem(SW_APPLY_INTENT_KEY, '1'); } catch { /* ignore */ }
        w.postMessage({ type: 'SKIP_WAITING' });
      } else if (installing.state === 'redundant') {
        installing.removeEventListener('statechange', onState);
      }
    };
    installing.addEventListener('statechange', onState);
  }
}

/**
 * Nuclear reset: unregister all SWs, clear all caches, reload.
 * This is the escape hatch when everything else fails.
 */
export async function nuclearReset(): Promise<void> {
  try {
    // Try to tell the active SW to nuke itself
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg?.active) {
      reg.active.postMessage({ type: 'NUCLEAR_RESET' });
    }
    // Also do it from the client side in case the SW is unresponsive
    const regs = await navigator.serviceWorker?.getRegistrations();
    if (regs) {
      await Promise.all(regs.map((r) => r.unregister()));
    }
    const names = await caches.keys();
    await Promise.all(names.map((n) => caches.delete(n)));
  } catch {
    // Best effort
  }
  // Clear all session/local storage keys related to SW state
  sessionStorage.clear();
  // Don't reload immediately - let the caller decide, or let the page settle.
  // If called from reload loop detector, another reload would just restart the loop.
  // Instead, set a flag so the next page load knows to skip SW registration.
  localStorage.setItem('sw-nuked', '1');
}

/**
 * Register the service worker and set up all lifecycle listeners.
 * Call once from main.tsx on page load.
 */
export function initServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return;

  // If we just ran a nuclear reset, skip SW registration for this load
  // to prevent the controllerchange → reload loop
  if (localStorage.getItem('sw-nuked')) {
    localStorage.removeItem('sw-nuked');
    return;
  }

  // ── Register on load ──
  window.addEventListener('load', async () => {
    try {
      // `updateViaCache: 'none'` forces the browser to bypass the HTTP
      // cache when fetching sw.js (both at register and on every
      // reg.update() call). Default ('imports') only bypasses cache for
      // import scripts, not the main worker — and iOS Safari has been
      // seen to serve cached sw.js to update checks despite the spec,
      // which silently masked real updates as "no new version".
      _registration = await navigator.serviceWorker.register('/macro_app/sw.js', {
        updateViaCache: 'none',
      });
    } catch {
      return; // SW registration failed - not critical
    }

    // Request persistent storage to prevent browser from evicting caches
    navigator.storage?.persist?.().catch(() => {});

    // Check if there's already a waiting worker (e.g. from a previous visit)
    if (_registration.waiting) {
      notifyUpdateAvailable();
    }

    // Listen for new workers that finish installing
    _registration.addEventListener('updatefound', () => {
      const newWorker = _registration!.installing;
      if (!newWorker) return;

      newWorker.addEventListener('statechange', () => {
        // The new SW installed and is now waiting — precache complete,
        // reload would be instant.
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          notifyUpdateAvailable();
        }
      });
    });
  });

  // ── Listen for messages from SW ──
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data?.type === 'PRECACHE_START') {
      notifyPrecacheProgress({ done: 0, total: e.data.total ?? 0 });
      return;
    }
    if (e.data?.type === 'PRECACHE_PROGRESS') {
      notifyPrecacheProgress({
        done: e.data.done ?? 0,
        total: e.data.total ?? _precacheProgress.total,
      });
      return;
    }
    // SW_ACTIVATED is informational - the controllerchange handler below
    // takes care of the reload.
    if (e.data?.type === 'SW_NUKED') {
      // Nuclear reset completed by SW - don't reload (nuclearReset deliberately
      // avoids reloading to prevent loops). The sw-nuked localStorage flag will
      // handle recovery on the next navigation/page load.
    }
  });

  // ── Reload when the new SW takes control (user-initiated only) ──
  // controllerchange fires in several cases:
  //   a) First visit: no controller → SW activates → controllerchange (DON'T reload)
  //   b) Update: applyUpdate() → SKIP_WAITING → new SW activates → controllerchange (DO reload)
  //   c) iOS spurious: post-reload controller binding races without a real SW takeover
  //
  // The old heuristic `hadController` flipped on the moment any controller
  // existed at module init — which is true for EVERY warm reload after the
  // first install. That let case (c) through, triggering a second
  // window.location.reload() mid-mount that left header + bottom nav painted
  // but the lazy Suspense subtree torn down — the user saw a "blank middle"
  // until they exited and reopened the PWA process.
  //
  // The new gate: only honor controllerchange when WE explicitly asked for a
  // takeover via applyUpdate(), which sets `sw-apply-intent` in sessionStorage
  // before posting SKIP_WAITING. Consume the flag on first read so a
  // subsequent spurious event can't re-trigger the reload.
  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', async () => {
    if (refreshing) return;
    let intentSet = false;
    try {
      intentSet = sessionStorage.getItem(SW_APPLY_INTENT_KEY) === '1';
      if (intentSet) sessionStorage.removeItem(SW_APPLY_INTENT_KEY);
    } catch { /* sessionStorage disabled - treat as no intent */ }
    if (!intentSet) return; // Spurious / first-install controllerchange
    refreshing = true;
    localStorage.setItem('app-just-updated', '1');
    if (navigator.onLine) {
      try {
        await narrowClearApiCaches();
      } catch { /* best effort - still reload */ }
    }
    window.location.reload();
  });

  // ── Periodic update checks ──
  // Check for SW updates when the tab becomes visible after being backgrounded.
  // Handles: rapid deploys, iOS PWA suspension, long-idle tabs.
  let lastVisibleTime = Date.now();

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      const elapsed = Date.now() - lastVisibleTime;
      // If backgrounded for >2 minutes, check for updates
      if (elapsed > 2 * 60 * 1000 && _registration) {
        _registration.update().catch(() => {});
      }
    } else {
      lastVisibleTime = Date.now();
    }
  });

  // ── Check for updates on SPA navigation ──
  // Catches rapid deploys for users who stay in-app without backgrounding.
  // Throttled to at most once per 5 minutes.
  let lastNavCheck = 0;
  window.addEventListener('popstate', () => {
    const now = Date.now();
    if (now - lastNavCheck > 5 * 60 * 1000 && _registration) {
      lastNavCheck = now;
      _registration.update().catch(() => {});
    }
  });
}
