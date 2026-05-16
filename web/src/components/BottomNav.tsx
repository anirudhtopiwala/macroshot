import { useState, useRef, useEffect, useCallback, startTransition } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { Home, ClipboardList, Settings, Plus, TrendingUp } from './icons';
import { hapticLight } from '../utils/haptics';
import FABMenu from './FABMenu';
import WeightTracker from './WeightTracker';
import { hasLoggedAnyMeal, hasClickedFab, markFabClicked } from '../utils/onboarding';
import type { LucideIcon } from 'lucide-react';

const tabs: { path: string; Icon: LucideIcon; label: string }[] = [
  { path: '/', Icon: Home, label: 'Home' },
  { path: '/journal', Icon: ClipboardList, label: 'Journal' },
  // Plus button goes here (rendered separately)
  { path: '/trends', Icon: TrendingUp, label: 'Trends' },
  { path: '/settings', Icon: Settings, label: 'Settings' },
];

// Prefetch page chunks on touch to eliminate lazy-load flash
const prefetchMap: Record<string, () => void> = {
  '/journal': () => { import('../pages/Journal').catch(() => {}); },
  '/trends': () => { import('../pages/Trends').catch(() => {}); },
  '/settings': () => { import('../pages/Settings').catch(() => {}); },
};

function getActiveTabIndex(pathname: string): number {
  if (pathname === '/') return 0;
  if (pathname.startsWith('/journal') || pathname.startsWith('/meals')) return 1;
  if (pathname.startsWith('/trends')) return 2;
  if (pathname.startsWith('/settings')) return 3;
  return -1;
}

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export default function BottomNav() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const logActive = pathname.startsWith('/log');
  const [fabOpen, setFabOpen] = useState(false);
  const [fabClicked, setFabClicked] = useState<boolean>(() => hasClickedFab());
  const [weightOpen, setWeightOpen] = useState(false);

  // Close weight tracker on navigation (e.g., badge celebration → achievements)
  useEffect(() => {
    setWeightOpen(false);
  }, [pathname]);

  // Allow other components (e.g., WeightSummaryCard) to open the weight tracker
  useEffect(() => {
    const handler = () => setWeightOpen(true);
    window.addEventListener('open-weight-tracker', handler);
    return () => window.removeEventListener('open-weight-tracker', handler);
  }, []);

  // Hide the nav while the on-screen keyboard is up. Without this, iOS
  // resizes the visual viewport above the keyboard and our position:fixed
  // bottom nav abruptly jumps up to sit just above the keyboard — which
  // looks like the nav is "moving around" as the user types.
  //
  // We combine two signals:
  //   1. visualViewport shrink vs. a captured baseline. We snapshot the
  //      baseline at mount and recompute on orientation change rather than
  //      comparing against window.innerHeight, because on Android Chrome
  //      innerHeight *also* shrinks with the keyboard (so the diff stays
  //      ~zero and the threshold never trips).
  //   2. A focused editable element on touch devices. Some pages (notably
  //      /chat with its 100dvh layout) don't reliably fire visualViewport
  //      resize on iOS PWA, so focus-within is a backstop. Gated to
  //      pointer:coarse so desktop input focus doesn't hide the nav.
  const [kbUp, setKbUp] = useState(false);
  useEffect(() => {
    const vv = window.visualViewport;
    const isTouch = typeof window.matchMedia === 'function'
      && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
    let baseline = vv?.height ?? window.innerHeight;

    const isEditableFocused = () => {
      const ae = document.activeElement as HTMLElement | null;
      if (!ae) return false;
      const tag = ae.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || ae.isContentEditable;
    };

    const update = () => {
      // 150px threshold absorbs iOS Safari address-bar collapse without
      // false-positiving the much-larger keyboard window.
      const vvShrunk = !!vv && vv.height < baseline - 150;
      const focusSignal = isTouch && isEditableFocused();
      setKbUp(vvShrunk || focusSignal);
    };

    const onOrientation = () => {
      // Recapture baseline after rotation; the keyboard is dismissed during
      // rotation so vv.height represents the un-keyboarded viewport.
      baseline = vv?.height ?? window.innerHeight;
      update();
    };

    vv?.addEventListener('resize', update);
    window.addEventListener('orientationchange', onOrientation);
    document.addEventListener('focusin', update);
    document.addEventListener('focusout', update);
    update();
    return () => {
      vv?.removeEventListener('resize', update);
      window.removeEventListener('orientationchange', onOrientation);
      document.removeEventListener('focusin', update);
      document.removeEventListener('focusout', update);
    };
  }, []);

  // Track which date the user is viewing on Dashboard/Journal
  const [viewingDate, setViewingDate] = useState('');
  useEffect(() => {
    const handler = (e: Event) => setViewingDate((e as CustomEvent).detail || '');
    window.addEventListener('viewingDate', handler);
    return () => window.removeEventListener('viewingDate', handler);
  }, []);
  // Clear viewing date when navigating away from Dashboard/Journal
  useEffect(() => {
    if (pathname !== '/' && !pathname.startsWith('/journal')) {
      setViewingDate('');
    }
  }, [pathname]);
  // Build date query param for FAB navigation (only for past dates)
  const dateParam = viewingDate && viewingDate < todayStr() ? `&date=${viewingDate}` : '';

  const activeIdx = getActiveTabIndex(pathname);
  const prevIdxRef = useRef(activeIdx);
  const [traveling, setTraveling] = useState(false);

  // Refs for measuring actual button positions
  const containerRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [indicatorX, setIndicatorX] = useState<number | null>(null);
  const renderCountRef = useRef(0);

  // Measure position of active tab button relative to container
  const measurePosition = useCallback(() => {
    if (activeIdx < 0 || !containerRef.current || !tabRefs.current[activeIdx]) return;
    const container = containerRef.current.getBoundingClientRect();
    const btn = tabRefs.current[activeIdx]!.getBoundingClientRect();
    setIndicatorX(btn.left - container.left + btn.width / 2);
    renderCountRef.current++;
  }, [activeIdx]);

  // Re-measure on active tab change and on mount
  useEffect(() => {
    if (activeIdx < 0) {
      setIndicatorX(null);
      return;
    }
    measurePosition();
  }, [activeIdx, measurePosition]);

  // Also measure after layout settles (fonts loaded, etc.)
  useEffect(() => {
    const timer = setTimeout(measurePosition, 50);
    return () => clearTimeout(timer);
  }, [measurePosition]);

  // Detect tab changes and trigger morph animation
  useEffect(() => {
    if (prevIdxRef.current !== activeIdx && activeIdx >= 0 && prevIdxRef.current >= 0) {
      setTraveling(true);
      const timer = setTimeout(() => setTraveling(false), 350);
      prevIdxRef.current = activeIdx;
      return () => clearTimeout(timer);
    }
    prevIdxRef.current = activeIdx;
  }, [activeIdx]);

  const goTo = useCallback((path: string, targetTabIdx: number) => {
    hapticLight();
    // If already on the target path, tapping the tab resets state
    // (e.g., Home tab while on /?date=X clears the date → go to today)
    const hasParams = searchParams.toString() !== '';
    if (pathname === path && !hasParams) return;
    const currentTabIdx = getActiveTabIndex(pathname);
    startTransition(() => {
      // Same path but with params (e.g., /?date=X → /) - replace to reset
      // Same tab sub-page (e.g., /meals/5 → /journal) - replace
      if (pathname === path || (currentTabIdx === targetTabIdx && currentTabIdx >= 0)) {
        navigate(path, { replace: true });
      } else {
        navigate(path);
      }
    });
  }, [navigate, pathname, searchParams]);

  const prefetch = (path: string) => {
    prefetchMap[path]?.();
  };

  const setTabRef = (idx: number) => (el: HTMLButtonElement | null) => {
    tabRefs.current[idx] = el;
  };

  return (
    <>
      {fabOpen && (
        <FABMenu
          onClose={() => setFabOpen(false)}
          onCamera={() => { setFabOpen(false); navigate(`/log?mode=camera${dateParam}`, { replace: true }); }}
          onBarcode={() => { setFabOpen(false); navigate(`/log?mode=barcode${dateParam}`, { replace: true }); }}
          onText={() => { setFabOpen(false); navigate(`/log?mode=text&scroll=text${dateParam}`, { replace: true }); }}
          onWeight={() => { setFabOpen(false); setWeightOpen(true); }}
          onChat={() => { setFabOpen(false); navigate('/chat', { replace: true }); }}
          onCopyDay={() => { setFabOpen(false); navigate(`/log?scroll=quicklog${dateParam}`, { replace: true }); }}
        />
      )}
      {weightOpen && <WeightTracker onClose={() => setWeightOpen(false)} />}
      <nav
        className="fixed left-3 right-3 z-50 pointer-events-none transition-opacity duration-150"
        style={{
          bottom: 'calc(0.5rem + env(safe-area-inset-bottom, 0px))',
          transform: 'translate3d(0, 0, 0)',
          willChange: 'transform',
          opacity: kbUp ? 0 : 1,
          visibility: kbUp ? 'hidden' : 'visible',
        }}
      >
        <div
          className="max-w-lg mx-auto rounded-2xl backdrop-blur-[24px] border pointer-events-auto"
          style={{
            background: 'var(--nav-bg)',
            borderColor: 'var(--border-glass)',
            boxShadow: '0 4px 24px rgba(0, 0, 0, 0.15), inset 0 1px 0 var(--border-glass)',
          }}
        >
          <div ref={containerRef} className="flex items-center justify-around py-2 px-1 relative">
            {/* Traveling bubble indicator */}
            {activeIdx >= 0 && indicatorX !== null && (
              <div
                className="absolute pointer-events-none"
                style={{
                  left: indicatorX,
                  top: '50%',
                  transform: `translate(-50%, -60%) ${traveling ? 'scaleX(1.8) scaleY(0.55)' : 'scaleX(1) scaleY(1)'}`,
                  width: 40,
                  height: 40,
                  borderRadius: traveling ? '10px' : '12px',
                  background: 'rgba(16,185,129,0.15)',
                  boxShadow: '0 0 16px rgba(16,185,129,0.1)',
                  transition: renderCountRef.current > 1
                    ? 'left 0.35s cubic-bezier(0.4, 0, 0.2, 1), transform 0.35s cubic-bezier(0.4, 0, 0.2, 1)'
                    : 'none',
                }}
              />
            )}

            {/* First two tabs */}
            {tabs.slice(0, 2).map((t, i) => {
              const active = i === activeIdx;
              return (
                <button
                  key={t.path}
                  ref={setTabRef(i)}
                  onClick={() => goTo(t.path, i)}
                  onTouchStart={() => prefetch(t.path)}
                  aria-label={t.label}
                  aria-current={active ? 'page' : undefined}
                  className="flex flex-col items-center py-1 px-4 text-[11px] font-medium rounded-xl transition-all duration-200 relative z-10"
                  style={{ color: active ? 'var(--text-primary)' : 'var(--text-muted)' }}
                >
                  <div className={`p-1.5 rounded-xl transition-all duration-200 ${active ? 'scale-110' : 'scale-100'}`}>
                    <t.Icon className={`w-6 h-6 transition-colors duration-200 ${active ? 'text-emerald-400' : ''}`} />
                  </div>
                  <span className="mt-0.5 transition-all duration-200" style={{ opacity: active ? 1 : 0.7 }}>{t.label}</span>
                </button>
              );
            })}

            {/* Center plus button */}
            <button
              aria-label={fabOpen ? 'Close quick add menu' : 'Open quick add menu'}
              aria-expanded={fabOpen}
              className="flex flex-col items-center -mt-7 group relative z-10"
              onClick={() => {
                hapticLight();
                if (!fabClicked) { markFabClicked(); setFabClicked(true); }
                setFabOpen(!fabOpen);
              }}
            >
              <div className="relative">
                {!fabOpen && !fabClicked && !hasLoggedAnyMeal() && (
                  <>
                    <span
                      className="absolute top-1/2 left-1/2 w-14 h-14 rounded-full pointer-events-none animate-fab-pulse-ring"
                      style={{ border: '2px solid rgba(16,185,129,0.6)' }}
                      aria-hidden="true"
                    />
                    <span
                      className="absolute top-1/2 left-1/2 w-14 h-14 rounded-full pointer-events-none animate-fab-pulse-ring-delayed"
                      style={{ border: '2px solid rgba(16,185,129,0.6)' }}
                      aria-hidden="true"
                    />
                  </>
                )}
                <div
                  className={`p-3.5 rounded-full transition-all duration-300 border relative ${
                    fabOpen
                      ? 'bg-emerald-500 border-emerald-400/50 shadow-[0_0_24px_rgba(16,185,129,0.5)]'
                      : logActive
                        ? 'bg-emerald-500 border-emerald-400/50 shadow-[0_0_24px_rgba(16,185,129,0.4)]'
                        : 'bg-emerald-600 border-emerald-500/30 shadow-[0_4px_20px_rgba(16,185,129,0.3)] group-hover:bg-emerald-500 group-hover:shadow-[0_0_24px_rgba(16,185,129,0.4)]'
                  }`}
                >
                  <Plus
                    className="w-7 h-7 text-white transition-transform duration-300"
                    style={{ transform: fabOpen ? 'rotate(45deg)' : 'rotate(0deg)' }}
                    strokeWidth={2.5}
                  />
                </div>
              </div>
            </button>

            {/* Last two tabs */}
            {tabs.slice(2).map((t, i) => {
              const tabIdx = i + 2;
              const active = tabIdx === activeIdx;
              return (
                <button
                  key={t.path}
                  ref={setTabRef(tabIdx)}
                  onClick={() => goTo(t.path, tabIdx)}
                  onTouchStart={() => prefetch(t.path)}
                  aria-label={t.label}
                  aria-current={active ? 'page' : undefined}
                  className="flex flex-col items-center py-1 px-4 text-[11px] font-medium rounded-xl transition-all duration-200 relative z-10"
                  style={{ color: active ? 'var(--text-primary)' : 'var(--text-muted)' }}
                >
                  <div className={`p-1.5 rounded-xl transition-all duration-200 ${active ? 'scale-110' : 'scale-100'}`}>
                    <t.Icon className={`w-6 h-6 transition-colors duration-200 ${active ? 'text-emerald-400' : ''}`} />
                  </div>
                  <span className="mt-0.5 transition-all duration-200" style={{ opacity: active ? 1 : 0.7 }}>{t.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      </nav>
    </>
  );
}
