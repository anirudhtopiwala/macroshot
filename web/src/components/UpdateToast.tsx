import { useEffect, useRef, useState } from 'react';
import { Download, X } from 'lucide-react';
import { hapticLight } from '../utils/haptics';
import { useToast } from './Toast';
import LoadingSpinner from './LoadingSpinner';

export default function UpdateToast() {
  const { toast } = useToast();
  const [updateReady, setUpdateReady] = useState(false);
  const [reloading, setReloading] = useState(false);
  const watchdogRef = useRef<number | null>(null);

  useEffect(() => {
    if (localStorage.getItem('app-just-updated')) {
      localStorage.removeItem('app-just-updated');
      toast('App updated successfully!', 'success');
    }
  }, [toast]);
  // Only prompt to reload when we're sure the reload will succeed. Tapping
  // the toast causes the new SW to claim → full reload → fresh API fetches.
  // If offline at that moment, the reload is fine (precache serves the
  // shell) but the API-cache clear is skipped (see swManager.ts) and the
  // user may still render against stale data. Safer to wait for reconnect.
  const [online, setOnline] = useState(() =>
    typeof navigator === 'undefined' ? true : navigator.onLine
  );

  useEffect(() => {
    let mounted = true;

    (async () => {
      const { isUpdateAvailable, onUpdateAvailable } = await import('../utils/swManager');
      if (!mounted) return;

      if (isUpdateAvailable()) setUpdateReady(true);

      const unsub = onUpdateAvailable(() => {
        if (mounted) setUpdateReady(true);
      });

      return unsub;
    })();

    const onEvent = () => setUpdateReady(true);
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener('sw-update-available', onEvent);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);

    return () => {
      mounted = false;
      window.removeEventListener('sw-update-available', onEvent);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      if (watchdogRef.current !== null) {
        clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
    };
  }, []);

  const visible = updateReady && online;
  if (!visible) return null;

  const handleReload = async () => {
    if (reloading) return;
    hapticLight();
    setReloading(true);
    const { applyUpdate } = await import('../utils/swManager');
    applyUpdate();
    // Watchdog: if controllerchange / reload doesn't happen in 10s, force a
    // hard reload. Covers edge cases where the new SW never reaches `waiting`
    // (e.g. precache stalled mid-download, browser dropped the message).
    watchdogRef.current = window.setTimeout(() => {
      window.location.reload();
    }, 10_000);
  };

  const handleDismiss = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (reloading) return;
    hapticLight();
    setUpdateReady(false);
  };

  return (
    <div
      className="fixed left-3 right-3 z-[60] flex justify-center pointer-events-none"
      style={{ bottom: 'calc(5.5rem + env(safe-area-inset-bottom, 0px))' }}
    >
      <button
        onClick={handleReload}
        disabled={reloading}
        aria-busy={reloading}
        className="max-w-lg w-full flex items-center gap-3 px-4 py-3 rounded-2xl backdrop-blur-[24px] border pointer-events-auto text-left active:scale-[0.98] transition-transform disabled:active:scale-100"
        style={{
          background: 'rgba(16, 185, 129, 0.15)',
          borderColor: 'rgba(16, 185, 129, 0.35)',
        }}
      >
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgba(16, 185, 129, 0.2)' }}
        >
          {reloading ? (
            <LoadingSpinner size="sm" />
          ) : (
            <Download className="w-4 h-4" style={{ color: '#10b981' }} />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            {reloading ? 'Updating…' : 'New version available'}
          </p>
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            {reloading ? 'Reloading shortly' : 'Tap to reload'}
          </p>
        </div>
        {!reloading && (
          <span
            onClick={handleDismiss}
            role="button"
            aria-label="Dismiss"
            className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 active:scale-90 transition-transform"
            style={{ background: 'rgba(0, 0, 0, 0.08)' }}
          >
            <X className="w-4 h-4" style={{ color: 'var(--text-secondary)' }} />
          </span>
        )}
      </button>
    </div>
  );
}
