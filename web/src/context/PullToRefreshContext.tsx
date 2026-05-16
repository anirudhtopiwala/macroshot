import { createContext, useContext, useRef, useState, useCallback, useEffect, type ReactNode } from 'react';
import { hapticLight } from '../utils/haptics';
import LoadingSpinner from '../components/LoadingSpinner';

interface PullToRefreshCtx {
  registerRefresh: (fn: () => Promise<void>) => void;
  unregisterRefresh: () => void;
  setDisabled: (v: boolean) => void;
}

const Ctx = createContext<PullToRefreshCtx>({
  registerRefresh: () => {},
  unregisterRefresh: () => {},
  setDisabled: () => {},
});

/** Pages call this to register their refresh function */
export function useRegisterRefresh(fn: () => Promise<void>) {
  const { registerRefresh, unregisterRefresh } = useContext(Ctx);
  useEffect(() => {
    registerRefresh(fn);
    return unregisterRefresh;
  }, [fn, registerRefresh, unregisterRefresh]);
}

const THRESHOLD = 70;
const MAX_PULL = 130;
const REFRESH_TIMEOUT = 10_000; // 10s max before force-releasing the UI

/** Disable pull-to-refresh while a modal/picker is open */
export function useDisablePullToRefresh() {
  const { setDisabled } = useContext(Ctx);
  useEffect(() => {
    setDisabled(true);
    return () => setDisabled(false);
  }, [setDisabled]);
}

export function PullToRefreshProvider({ children }: { children: ReactNode }) {
  const refreshFnRef = useRef<(() => Promise<void>) | null>(null);
  const disabledRef = useRef(false);
  const startYRef = useRef(0);
  const pullYRef = useRef(0);
  const indicatorRef = useRef<HTMLDivElement>(null);
  const arrowRef = useRef<SVGSVGElement>(null);
  const [refreshing, setRefreshing] = useState(false);

  const registerRefresh = useCallback((fn: () => Promise<void>) => {
    refreshFnRef.current = fn;
  }, []);

  const unregisterRefresh = useCallback(() => {
    refreshFnRef.current = null;
  }, []);

  const setDisabled = useCallback((v: boolean) => {
    disabledRef.current = v;
  }, []);

  // Direct DOM update for the pull indicator - avoids re-rendering the entire tree
  const updateIndicatorDOM = useCallback((py: number, animate: boolean) => {
    const el = indicatorRef.current;
    if (!el) return;
    const visible = py > 8;
    el.style.transform = `translateX(-50%) translateY(${visible ? py + 48 : -50}px)`;
    el.style.opacity = visible ? '1' : '0';
    el.style.transition = animate ? 'transform 0.3s ease, opacity 0.3s ease' : 'opacity 0.15s ease';
    // Rotate the arrow based on progress
    const arrow = arrowRef.current;
    if (arrow) {
      const progress = Math.min(py / THRESHOLD, 1);
      arrow.style.transform = `rotate(${progress * 180}deg)`;
    }
  }, []);

  useEffect(() => {
    const onTouchStart = (e: TouchEvent) => {
      if (refreshing || disabledRef.current) return;
      if (window.scrollY <= 0) {
        startYRef.current = e.touches[0].clientY;
        pullYRef.current = 0;
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (!startYRef.current || refreshing || disabledRef.current) return;
      const dy = e.touches[0].clientY - startYRef.current;

      if (dy > 0 && window.scrollY <= 0) {
        const dampened = Math.min(dy * 0.45, MAX_PULL);
        pullYRef.current = dampened;
        // Direct DOM update - no React re-render
        updateIndicatorDOM(dampened, false);
      } else {
        if (pullYRef.current > 0) {
          pullYRef.current = 0;
          updateIndicatorDOM(0, false);
        }
        startYRef.current = 0;
      }
    };

    const onTouchEnd = async () => {
      if (refreshing) return;
      const py = pullYRef.current;
      startYRef.current = 0;
      pullYRef.current = 0;

      if (py >= THRESHOLD && refreshFnRef.current) {
        hapticLight();
        updateIndicatorDOM(50, true);
        setRefreshing(true);
        try {
          await Promise.race([
            refreshFnRef.current(),
            new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), REFRESH_TIMEOUT)),
          ]);
        } catch {
          // Timeout or refresh error - release UI either way
        } finally {
          setRefreshing(false);
          updateIndicatorDOM(0, true);
        }
      } else {
        updateIndicatorDOM(0, true);
      }
    };

    window.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchmove', onTouchMove, { passive: true });
    window.addEventListener('touchend', onTouchEnd);

    return () => {
      window.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
    };
  }, [refreshing, updateIndicatorDOM]);

  return (
    <Ctx.Provider value={{ registerRefresh, unregisterRefresh, setDisabled }}>
      {/* Pull indicator - positioned via ref, no state-driven re-renders */}
      <div
        ref={indicatorRef}
        className="fixed left-1/2 z-[60] pointer-events-none"
        style={{
          transform: 'translateX(-50%) translateY(-50px)',
          opacity: 0,
          top: 0,
        }}
      >
        <div
          className="w-9 h-9 rounded-full backdrop-blur-xl border flex items-center justify-center"
          style={{
            background: 'var(--nav-bg)',
            borderColor: 'var(--border-glass)',
            boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
          }}
        >
          {refreshing ? (
            <LoadingSpinner size="sm" />
          ) : (
            <svg
              ref={arrowRef}
              className="w-4 h-4 text-emerald-400"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              viewBox="0 0 24 24"
              style={{ transition: 'transform 0.1s ease' }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
            </svg>
          )}
        </div>
      </div>
      {children}
    </Ctx.Provider>
  );
}
