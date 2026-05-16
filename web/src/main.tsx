import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
// Self-host Inter font (variable: weights 100-900) so we don't send EU
// user IPs to Google Fonts on first load.  Vite bundles the woff2 files.
import '@fontsource-variable/inter'
import './index.css'
import App from './App.tsx'
import { initServiceWorker, nuclearReset } from './utils/swManager'
import { clearChunkReloadFlags } from './utils/lazyWithRetry'
import { startAnalytics, track } from './api/analytics'

// Sentry error monitoring - only active when VITE_SENTRY_DSN is set
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN;
if (SENTRY_DSN) {
  // __APP_VERSION__ can contain spaces on staging (e.g., "abc1234 · 3 ahead
  // of v1.0.0"). Sentry release IDs must be sluggy, so take the first token.
  const rawVersion = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev';
  const sentryRelease = `macroshot@${rawVersion.split(' ')[0]}`;
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.MODE,
    release: sentryRelease,
    integrations: [
      Sentry.browserTracingIntegration(),
      // PII safety: mask all on-screen text and block all media in session replays.
      // Users' meal photos, chat messages, weight, and other health data must NOT
      // be transmitted to Sentry as part of replay capture.
      Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true }),
    ],
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 1.0,
    // Do not send PII (IP, cookies, headers) by default.
    sendDefaultPii: false,
  });
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Dismiss splash screen: wait for BOTH min display time AND app ready
const splash = document.getElementById('splash');
if (splash) {
  const SPLASH_MIN_MS = 1200; // Minimum time to show splash for branding
  const splashStart = Date.now();

  const dismissSplash = () => {
    const elapsed = Date.now() - splashStart;
    const remaining = Math.max(0, SPLASH_MIN_MS - elapsed);
    setTimeout(() => {
      splash.classList.add('hide');
      setTimeout(() => splash.remove(), 400);
    }, remaining);
  };

  // Listen for app-ready signal from the home page / auth check
  window.addEventListener('app-ready', dismissSplash, { once: true });

  // Safety fallback: dismiss after 4s max even if app-ready never fires
  setTimeout(() => {
    if (splash.parentNode) dismissSplash();
  }, 4000);
}

// Umami analytics - currently not deployed.  Cloudflare Web Analytics
// covers basic page-view tracking and that's enough for now.  To re-enable
// Umami: (1) restore the conditional script-injection block here, (2) add
// cloud.umami.is to script-src and api-gateway.umami.dev to connect-src
// in src/web/app.py CSP, (3) re-add the Umami disclosure to Privacy.tsx §4.
// The trackEvent() helper in utils/analytics.ts is a no-op when Umami is
// not loaded - existing call sites do not need to change.

// Capture beforeinstallprompt early so the InstallPrompt component can use it
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  (window as any).__pwaInstallPrompt = e;
});

// ── Service Worker lifecycle ──
// Silent auto-update model: new SW waits, activates when all old
// clients are closed. See swManager.ts for full details.
initServiceWorker();

// ── Self-hosted analytics dispatcher ──
// Starts the batching loop that ships UI events to /events/batch.
// Idempotent - safe to call on every boot. Handles offline persistence.
startAnalytics();

// ── Reload loop detection (nuclear reset) ──
// If we detect 3+ reloads within 15 seconds, something is badly broken
// (stale chunks, SW conflict, etc). Nuke everything to break the loop.
const RELOAD_TS_KEY = 'reload-timestamps';
const reloadTimestamps: number[] = JSON.parse(sessionStorage.getItem(RELOAD_TS_KEY) || '[]');
reloadTimestamps.push(Date.now());
const recent = reloadTimestamps.slice(-5);
sessionStorage.setItem(RELOAD_TS_KEY, JSON.stringify(recent));

if (recent.length >= 3 && recent[recent.length - 1] - recent[0] < 15000) {
  sessionStorage.removeItem(RELOAD_TS_KEY);
  console.warn('[SW] Reload loop detected - running nuclear reset');
  nuclearReset();
}

// ── Chunk error handler (global fallback) ──
// lazyWithRetry handles most chunk errors at the component level with
// retries + single reload. This global handler is a safety net for
// any chunk errors that slip through (e.g. from non-lazy dynamic imports).
function isChunkError(msg: string): boolean {
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    msg.includes('Importing a module script failed') ||
    msg.includes('Loading chunk') ||
    msg.includes('error loading dynamically imported module')
  );
}

const GLOBAL_CHUNK_RELOAD = 'global-chunk-reload';
function handleGlobalChunkError() {
  const attempts = parseInt(sessionStorage.getItem(GLOBAL_CHUNK_RELOAD) || '0');
  if (attempts >= 1) {
    // Already tried once - escalate to nuclear reset
    sessionStorage.removeItem(GLOBAL_CHUNK_RELOAD);
    nuclearReset();
    return;
  }
  sessionStorage.setItem(GLOBAL_CHUNK_RELOAD, '1');
  window.location.reload();
}

// Mirror uncaught JS errors and unhandled promise rejections into the
// `ui_client_error` analytics event. Surfaces blank-page / render bugs in
// service journalctl (events.py logs them at WARN) when Sentry isn't
// configured for the frontend. Best-effort: never throw from a handler.
function reportClientError(kind: 'error' | 'unhandledrejection', msg: string, src?: string, ln?: number, stack?: string) {
  try {
    track('ui_client_error', {
      kind,
      msg: msg.slice(0, 200),
      src: (src || '').slice(0, 160),
      ln: ln ?? null,
      stack: (stack || '').slice(0, 400),
      path: window.location.pathname.slice(0, 80),
      sw_controlled: !!navigator.serviceWorker?.controller,
    });
  } catch { /* swallow */ }
}

window.addEventListener('error', (e) => {
  if (e.message && isChunkError(e.message)) {
    if (Sentry.isInitialized()) Sentry.captureException(new Error(e.message), { tags: { type: 'chunk-error' } });
    handleGlobalChunkError();
    return;
  }
  reportClientError('error', e.message || '', e.filename, e.lineno, e.error?.stack);
});
window.addEventListener('unhandledrejection', (e) => {
  const msg = String(e.reason?.message || e.reason || '');
  if (isChunkError(msg)) {
    if (Sentry.isInitialized()) Sentry.captureException(new Error(msg), { tags: { type: 'chunk-error' } });
    handleGlobalChunkError();
    return;
  }
  reportClientError('unhandledrejection', msg, undefined, undefined, (e.reason as Error)?.stack);
});

// ── Clear all chunk-reload guards on successful load ──
window.addEventListener('load', () => {
  clearChunkReloadFlags();
  sessionStorage.removeItem(GLOBAL_CHUNK_RELOAD);
});

// ── iOS PWA recovery ──
// iOS kills the WebView process when the PWA is backgrounded for a while,
// but keeps the app "open" visually. When the user returns, React's root
// is empty. Also handles camera permission revocation quirks on iOS.
let lastActiveTimestamp = Date.now();

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    const elapsed = Date.now() - lastActiveTimestamp;

    // If backgrounded for >5s AND root is empty, React was killed - reload.
    // The 5s guard prevents false positives from React StrictMode unmount cycles.
    if (elapsed > 5000) {
      const root = document.getElementById('root');
      if (root && root.children.length === 0) {
        const key = 'ios-pwa-recovery-reload';
        if (sessionStorage.getItem(key)) {
          sessionStorage.removeItem(key);
          return; // already tried once - avoid loop
        }
        sessionStorage.setItem(key, '1');
        window.location.reload();
        return;
      }
    }

    // Long idle: force SW update check (belt-and-suspenders with swManager's
    // 2-minute check - this covers the >30 min case explicitly)
    if (elapsed > 30 * 60 * 1000) {
      navigator.serviceWorker?.getRegistration()?.then((reg) => reg?.update());
    }
  } else {
    lastActiveTimestamp = Date.now();
  }
});
