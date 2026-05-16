import { lazy, type ComponentType } from 'react';
import * as Sentry from '@sentry/react';

/**
 * Wrapper around React.lazy() that retries failed dynamic imports before
 * giving up. Handles the common post-deploy scenario where old chunk hashes
 * no longer exist on the server.
 *
 * Strategy:
 *  1. First attempt: normal import
 *  2. One retry after a short delay - helps for transient network errors
 *     (DNS blip, flaky cell signal). Does NOT help for stale-chunk 404s:
 *     Vite statically analyzes the import specifier so we can't append a
 *     cache-bust query string without breaking bundling, and the browser's
 *     module cache returns the same failed resolution on repeat.
 *  3. If the retry fails: reload the page ONCE (guarded by sessionStorage).
 *     This is what actually recovers stale-chunk cases - fresh HTML brings
 *     the new chunk manifest.
 *  4. If we already reloaded: let the error propagate to ErrorBoundary.
 *
 * This avoids the infinite reload loop that plain "reload on chunk error" causes.
 */

const CHUNK_RELOAD_KEY = 'chunk-reload-attempted';
const MAX_RETRIES = 1;
const RETRY_DELAY_MS = 1000;

function isChunkError(error: unknown): boolean {
  const msg = String((error as Error)?.message || error || '');
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    msg.includes('Importing a module script failed') ||
    msg.includes('Loading chunk') ||
    msg.includes('error loading dynamically imported module') ||
    // Safari-specific
    msg.includes('Unexpected token') ||
    msg.includes('is not a valid JavaScript MIME type')
  );
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function lazyWithRetry<T extends ComponentType<any>>(
  importFn: () => Promise<{ default: T }>,
  componentName?: string,
) {
  return lazy(async () => {
    // Try the original import first
    try {
      return await importFn();
    } catch (firstError) {
      if (!isChunkError(firstError)) throw firstError;

      // Retry the same import after a short delay. Only transient network
      // errors benefit here; stale-chunk 404s will re-hit the cached failed
      // resolution and fall through to the reload fallback below.
      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        await wait(RETRY_DELAY_MS * attempt);
        try {
          return await importFn();
        } catch (retryError) {
          if (!isChunkError(retryError)) throw retryError;
          // Continue to next retry
        }
      }

      // All retries failed. Try a single page reload to get fresh HTML
      // with the new chunk manifest.
      const reloadKey = componentName
        ? `${CHUNK_RELOAD_KEY}:${componentName}`
        : CHUNK_RELOAD_KEY;

      if (!sessionStorage.getItem(reloadKey)) {
        sessionStorage.setItem(reloadKey, '1');
        if (Sentry.isInitialized()) Sentry.captureException(firstError, { tags: { type: 'chunk-error', component: componentName || 'unknown' } });
        window.location.reload();
        // Return a never-resolving promise so React doesn't try to render
        // while the page is reloading
        return new Promise(() => {});
      }

      // Already tried reloading - clear the flag and let it propagate
      // to ErrorBoundary, which will show a recovery UI
      sessionStorage.removeItem(reloadKey);
      if (Sentry.isInitialized()) Sentry.captureException(firstError, { tags: { type: 'chunk-error', component: componentName || 'unknown' } });
      throw firstError;
    }
  });
}

/**
 * Clear all chunk-reload flags. Call this on successful page load
 * to reset the guards for next time.
 */
export function clearChunkReloadFlags(): void {
  const keysToRemove: string[] = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (key?.startsWith(CHUNK_RELOAD_KEY)) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((k) => sessionStorage.removeItem(k));
}
