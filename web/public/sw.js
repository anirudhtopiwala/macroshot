import { precacheAndRoute, cleanupOutdatedCaches, addPlugins } from 'workbox-precaching';

// ── Workbox precaching (manifest injected at build time) ──
// We hook a plugin into the precache controller so we can stream progress
// back to the page during the (~10 MB) install. The plugin's cacheDidUpdate
// fires per fresh fetch — files already cached from the previous version
// don't trigger it. We post the manifest size as `total` at install start
// so the UI can show "downloaded N of M" with a sensible upper bound; the
// page jumps to 100% when statechange === 'installed' regardless.
// Capture the manifest exactly once — workbox-build's injectManifest step
// requires the manifest token to appear in this file at most once. The
// comment intentionally avoids spelling the token literally so that
// requirement isn't tripped by this very comment.
const PRECACHE_MANIFEST = self.__WB_MANIFEST;
const PRECACHE_TOTAL = (PRECACHE_MANIFEST || []).length;
let precacheDone = 0;

async function postToAllClients(message) {
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const c of all) c.postMessage(message);
}

addPlugins([
  {
    cacheDidUpdate: async () => {
      precacheDone += 1;
      await postToAllClients({
        type: 'PRECACHE_PROGRESS',
        done: precacheDone,
        total: PRECACHE_TOTAL,
      });
    },
  },
]);

precacheAndRoute(PRECACHE_MANIFEST);
cleanupOutdatedCaches();

// ── DO NOT call self.skipWaiting() at top level ──
// Immediate skipWaiting() causes the new SW to take control while the page
// still references old chunk hashes from the previous build. Those old chunks
// are no longer in the new precache, causing dynamic import failures and
// reload loops. Instead, we wait for an explicit SKIP_WAITING message from
// the client after the user taps "Update now".

// ── Constants ──
const API_CACHE = 'macro-api-v1';
const IMAGES_CACHE = 'macro-images-v1';
const FONTS_CACHE = 'macro-fonts-v1';
const KNOWN_CACHES = [API_CACHE, IMAGES_CACHE, FONTS_CACHE];
const API_CACHE_TTL = 5 * 60 * 1000; // 5 minutes
const MAX_IMAGE_ENTRIES = 200;

// ── Install ──
// Workbox's precacheAndRoute() above adds its own install listener with
// waitUntil(precache-all-assets). We deliberately do NOT post a SW_WAITING
// message here: install fires when precaching *starts*, not when it finishes.
// Posting from here would surface "Tap to reload" before the new bundle is
// actually cached, so a tap would send SKIP_WAITING to a still-installing SW
// (no waiting worker yet) and the message would be dropped — the user would
// see "Updating…" stuck for ~precache-duration. The client side waits for
// statechange === 'installed' instead, which fires after Workbox finishes.
self.addEventListener('install', () => {
  precacheDone = 0;
  postToAllClients({ type: 'PRECACHE_START', total: PRECACHE_TOTAL });
});

// ── Activate: claim clients, clean up old caches ──
self.addEventListener('activate', (e) => {
  e.waitUntil(
    (async () => {
      // Delete any unknown runtime caches (old versions, orphaned caches)
      const cacheNames = await caches.keys();
      await Promise.all(
        cacheNames
          .filter((name) => !name.startsWith('workbox-') && !KNOWN_CACHES.includes(name))
          .map((name) => caches.delete(name))
      );

      await self.clients.claim();
      // Notify all open tabs that the new SW is now active
      const allClients = await self.clients.matchAll({ type: 'window' });
      for (const client of allClients) {
        client.postMessage({ type: 'SW_ACTIVATED' });
      }
    })()
  );
});

// ── Single message handler for all message types ──
self.addEventListener('message', (e) => {
  const type = e.data?.type;
  if (type === 'SKIP_WAITING') {
    // User accepted the update - now safe to take over
    self.skipWaiting();
  } else if (type === 'CLEAR_CACHE') {
    // Full clear on logout - wipe all user data
    caches.delete(API_CACHE);
    caches.delete(IMAGES_CACHE);
  } else if (type === 'CLEAR_API_CACHE') {
    // Partial clear after mutations - only wipe API data cache
    caches.delete(API_CACHE);
  } else if (type === 'NUCLEAR_RESET') {
    // Emergency recovery: nuke everything and unregister
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.map((n) => caches.delete(n)));
      await self.registration.unregister();
      const allClients = await self.clients.matchAll({ type: 'window' });
      for (const client of allClients) {
        client.postMessage({ type: 'SW_NUKED' });
      }
    })();
  }
});

// ── Cache helpers ──

function isCacheFresh(response) {
  const dateHeader = response.headers.get('sw-cached-at');
  if (!dateHeader) return false;
  return (Date.now() - new Date(dateHeader).getTime()) < API_CACHE_TTL;
}

async function stampAndCache(cache, key, response) {
  const headers = new Headers(response.headers);
  headers.set('sw-cached-at', new Date().toISOString());
  const stamped = new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
  try {
    await cache.put(key, stamped);
  } catch {
    // QuotaExceededError - silently ignore, in-memory data still served
  }
}

/** Evict oldest entries if cache exceeds maxEntries */
async function enforceLimit(cache, maxEntries) {
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  const excess = keys.length - maxEntries;
  for (let i = 0; i < excess; i++) {
    await cache.delete(keys[i]);
  }
}

// ── Fetch handler ──
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Skip non-GET requests
  if (e.request.method !== 'GET') return;

  // --- Google Fonts: cache-first (separate cache) ---
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(
      caches.open(FONTS_CACHE).then((c) =>
        c.match(e.request).then((cached) => {
          if (cached) return cached;
          return fetch(e.request).then((res) => {
            if (res.ok) {
              try { c.put(e.request, res.clone()); } catch { /* quota */ }
            }
            return res;
          });
        })
      )
    );
    return;
  }

  // --- API GET requests ---
  if (url.pathname.includes('/api/')) {
    // Never cache auth endpoints
    if (url.pathname.includes('/auth/')) return;

    // Never cache fuzzy/semantic meal search. Queries are user-typed and
    // rarely repeat exactly, so the SW's stale-while-revalidate would
    // serve a stale empty result for days when an early failed embedding
    // call (or a network blip) cached `[]` against that query string.
    // Always go straight to the network.
    if (url.pathname.endsWith('/meals/search')) return;

    // Cache-first for meal images (immutable content, separate cache with limit)
    if (url.pathname.includes('/images/')) {
      e.respondWith(
        caches.open(IMAGES_CACHE).then((c) =>
          c.match(e.request).then((cached) => {
            if (cached) return cached;
            return fetch(e.request).then((res) => {
              if (res.ok) {
                try {
                  c.put(e.request, res.clone());
                  enforceLimit(c, MAX_IMAGE_ENTRIES);
                } catch { /* quota */ }
              }
              return res;
            });
          })
        )
      );
      return;
    }

    const cacheKey = new Request(url.href);
    const isRefresh = e.request.headers.get('X-Refresh') === '1';

    if (isRefresh) {
      // Network-first for explicit refreshes (pull-to-refresh)
      e.respondWith(
        fetch(e.request)
          .then((res) => {
            if (res.ok) {
              const clone = res.clone();
              caches.open(API_CACHE).then((c) => stampAndCache(c, cacheKey, clone));
            }
            return res;
          })
          .catch(() =>
            caches.open(API_CACHE).then((c) => c.match(cacheKey)).then((cached) =>
              cached || new Response('{"detail":"You are offline"}', {
                status: 503,
                headers: { 'Content-Type': 'application/json' },
              })
            )
          )
      );
    } else {
      // Stale-while-revalidate with TTL
      const networkFetch = fetch(e.request)
        .then((res) => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(API_CACHE).then((c) => stampAndCache(c, cacheKey, clone));
          }
          return res;
        })
        .catch(() => null);

      e.respondWith(
        caches.open(API_CACHE).then((c) => c.match(cacheKey)).then((cached) => {
          if (cached && isCacheFresh(cached)) {
            return cached; // Fresh: serve immediately
          }
          return networkFetch.then((res) => {
            if (res) return res;
            if (cached) return cached;
            return new Response('{"detail":"You are offline"}', {
              status: 503,
              headers: { 'Content-Type': 'application/json' },
            });
          });
        })
      );
      e.waitUntil(networkFetch);
    }
    return;
  }

  // Navigation requests are handled by Workbox's precacheAndRoute above
  // (serves index.html from the precache, auto-versioned per deploy)
});

// ── Push notification handler ──
self.addEventListener('push', (e) => {
  let data;
  try {
    data = e.data?.json() ?? {};
  } catch {
    data = { title: 'MacroShot', body: e.data?.text() || '' };
  }

  e.waitUntil(
    self.registration.showNotification(data.title || 'MacroShot', {
      body: data.body || '',
      icon: data.icon || '/macro_app/icons/icon-192.png',
      badge: '/macro_app/icons/icon-192.png',
      tag: data.tag || 'default',
      data: { url: data.url || '/macro_app/' },
      vibrate: [100, 50, 100],
      renotify: true,
    })
  );
});

// ── Notification click: open/focus the app ──
// SECURITY: clamp the navigation URL to same-origin under /macro_app/. The
// push payload is signed by our backend, but defense-in-depth: if the push
// service or a leaked VAPID key ever lets an attacker inject a `data.url`,
// they can't use it to open https://phish.example or javascript: URLs.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const FALLBACK = '/macro_app/';
  let url = FALLBACK;
  const raw = e.notification.data?.url;
  if (typeof raw === 'string') {
    try {
      const u = new URL(raw, self.location.origin);
      if (u.origin === self.location.origin && u.pathname.startsWith('/macro_app/')) {
        url = u.pathname + u.search + u.hash;
      }
    } catch {
      // ignore - fall back to /macro_app/
    }
  }

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes('/macro_app') && 'focus' in client) {
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
