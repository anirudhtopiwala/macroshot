import { useEffect, useState, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Flame, Shield } from './icons';
import Tooltip from './Tooltip';
import ShieldCelebration from './ShieldCelebration';
import ShieldEarnedCelebration from './ShieldEarnedCelebration';
import { isEarlyUser } from '../utils/onboarding';
import { useAuth } from '../context/AuthContext';
import { dashboardApi } from '../api/dashboard';
import { achievementsApi } from '../api/achievements';
import { getCached } from '../utils/apiCache';
import { waitForCritical } from '../utils/requestScheduler';
import type { AchievementSummaryResponse } from '../types';

// Evolving flame styles based on streak tier
function getStreakStyle(streak: number) {
  if (streak >= 100) return {
    bg: 'rgba(234,179,8,0.15)', border: '1px solid rgba(234,179,8,0.35)',
    shadow: '0 0 16px rgba(234,179,8,0.25)', color: '#eab308',
    filter: 'drop-shadow(0 0 6px rgba(234,179,8,0.7))', textShadow: '0 0 10px rgba(234,179,8,0.5)',
  };
  if (streak >= 30) return {
    bg: 'rgba(168,85,247,0.12)', border: '1px solid rgba(168,85,247,0.3)',
    shadow: '0 0 14px rgba(168,85,247,0.2)', color: '#a855f7',
    filter: 'drop-shadow(0 0 5px rgba(168,85,247,0.6))', textShadow: '0 0 8px rgba(168,85,247,0.4)',
  };
  if (streak >= 7) return {
    bg: 'rgba(249,115,22,0.12)', border: '1px solid rgba(249,115,22,0.3)',
    shadow: '0 0 14px rgba(249,115,22,0.2)', color: '#f97316',
    filter: 'drop-shadow(0 0 5px rgba(249,115,22,0.6))', textShadow: '0 0 8px rgba(249,115,22,0.4)',
  };
  return {
    bg: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.25)',
    shadow: '0 0 12px rgba(249,115,22,0.15)', color: '#f97316',
    filter: 'drop-shadow(0 0 4px rgba(249,115,22,0.5))', textShadow: '0 0 8px rgba(249,115,22,0.4)',
  };
}

export default function Header() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [streak, setStreak] = useState(0);
  const [shields, setShields] = useState(() => getCached<AchievementSummaryResponse>('achievement_summary')?.shields_available ?? 0);
  const [gamification, setGamification] = useState(() => {
    const cached = getCached<{ prefs?: { gamification?: string } }>('settings_main');
    return cached?.prefs?.gamification ?? 'full';
  });
  const [comebackToast, setComebackToast] = useState(false);
  // Full-screen celebration when a shield was just consumed. `before` is the
  // shield count we last observed; `after` is the fresh count from the API,
  // so the popup can animate the countdown.
  const [shieldCelebration, setShieldCelebration] = useState<
    { shieldsBefore: number; shieldsAfter: number; bridgedDate?: string } | null
  >(null);
  // Full-screen celebration when a shield was just earned (count went up).
  const [shieldEarnedCelebration, setShieldEarnedCelebration] = useState<
    { shieldsBefore: number; shieldsAfter: number } | null
  >(null);
  const streakRef = useRef<HTMLButtonElement>(null);
  const shieldCelebrationShownRef = useRef(false);
  const shieldEarnedShownRef = useRef(false);
  const prevShieldsRef = useRef<number | null>(null);
  const prevStreakRef = useRef<number | null>(null);
  // Animated header shield count: tweens from old → new on decrement so the
  // user sees the consume happen, not just a number snap.
  const [displayShields, setDisplayShields] = useState(() => getCached<AchievementSummaryResponse>('achievement_summary')?.shields_available ?? 0);
  const initial = ((user?.first_name?.[0] || '') + (user?.last_name?.[0] || '')) || user?.email?.[0] || '?';

  // Listen for gamification toggle changes from Settings (instant, works offline)
  useEffect(() => {
    const handler = (e: Event) => setGamification((e as CustomEvent).detail);
    window.addEventListener('gamification-changed', handler);
    return () => window.removeEventListener('gamification-changed', handler);
  }, []);

  // Fetch streak + shields on mount and when navigating to home
  const lastFetchRef = useRef(0);
  const prevPathRef = useRef(pathname);
  const mountedRef = useRef(false);
  const toastTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => {
    const prevPath = prevPathRef.current;
    prevPathRef.current = pathname;
    const isMount = !mountedRef.current;
    mountedRef.current = true;

    const shouldFetch = isMount || pathname === '/';
    if (!shouldFetch) return;

    const now = Date.now();
    const comingFromLog = prevPath === '/log';
    if (!isMount && !comingFromLog && now - lastFetchRef.current <= 2 * 60 * 1000) return;

    let cancelled = false;
    const addTimer = (fn: () => void, ms: number) => {
      if (cancelled) return;
      const id = setTimeout(fn, ms);
      toastTimersRef.current.push(id);
    };

    lastFetchRef.current = now;
    // Wait for Dashboard's critical requests to finish first so we don't
    // compete for the VM's single vCPU during the initial burst.
    waitForCritical().then(() => dashboardApi.stats()).then((s) => {
      if (cancelled) return;
      const newStreak = s.streak_days ?? 0;
      // Comeback detection: streak is 1 and previous was 0 (gap without shield)
      const comebackKey = `comeback_toast_${new Date().toISOString().slice(0, 10)}`;
      if (
        newStreak === 1 &&
        prevStreakRef.current !== null &&
        prevStreakRef.current === 0 &&
        !localStorage.getItem(comebackKey)
      ) {
        localStorage.setItem(comebackKey, '1');
        setComebackToast(true);
        addTimer(() => setComebackToast(false), 4000);
      }
      prevStreakRef.current = newStreak;
      setStreak(newStreak);
    }).catch(() => {});
    waitForCritical().then(() => achievementsApi.getSummary()).then((r) => {
      if (cancelled) return;
      // Shield earned detection: shield count increased between fetches.
      // Surface as a full-screen celebration (not a toast) so the user actually
      // notices banking the reward - a 4s chip in the header was easy to miss
      // and didn't match the consume-side ShieldCelebration treatment.
      const shieldEarnedKey = `shield_earned_celebration_${new Date().toISOString().slice(0, 10)}`;
      if (
        prevShieldsRef.current !== null &&
        r.shields_available > prevShieldsRef.current &&
        gamification === 'full' &&
        !shieldEarnedShownRef.current &&
        !localStorage.getItem(shieldEarnedKey)
      ) {
        localStorage.setItem(shieldEarnedKey, '1');
        shieldEarnedShownRef.current = true;
        setShieldEarnedCelebration({
          shieldsBefore: prevShieldsRef.current,
          shieldsAfter: r.shields_available,
        });
      }
      const prevCount = prevShieldsRef.current;
      prevShieldsRef.current = r.shields_available;
      setShields(r.shields_available);
      // Show shield-used celebration ONCE per unique shield event. The
      // backend's shield_used_today flag stays true for ~24h after a bridge,
      // so a per-day key would re-fire the popup tomorrow. Keying on the
      // actual bridged_date (one per shield consumption) means each event
      // gets exactly one celebration ever, regardless of how many times
      // the user opens the app.
      const bridged = r.shield_used_dates?.[0];
      const shieldCelebKey = bridged ? `shield_celebration_seen:${bridged}` : null;
      if (
        r.shield_used_today &&
        bridged &&
        shieldCelebKey &&
        gamification === 'full' &&
        !shieldCelebrationShownRef.current &&
        !localStorage.getItem(shieldCelebKey)
      ) {
        shieldCelebrationShownRef.current = true;
        localStorage.setItem(shieldCelebKey, '1');
        // If we have a prior count locally, use it; otherwise infer one
        // shield more than current so the popup still has something to
        // animate down from.
        const before = prevCount !== null && prevCount > r.shields_available
          ? prevCount
          : r.shields_available + 1;
        setShieldCelebration({
          shieldsBefore: before,
          shieldsAfter: r.shields_available,
          bridgedDate: bridged,
        });
      }
    }).catch(() => {});

    return () => {
      cancelled = true;
      toastTimersRef.current.forEach(clearTimeout);
      toastTimersRef.current = [];
    };
  }, [pathname]);

  // Tween the header shield count when it changes so a consume reads as a
  // visible decrement, not a silent number swap. ~700ms ease-out is short
  // enough to read alongside the celebration popup if both fire.
  useEffect(() => {
    if (displayShields === shields) return;
    const startedAt = performance.now();
    const startVal = displayShields;
    const delta = shields - startVal;
    const duration = 700;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplayShields(Math.round(startVal + delta * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // displayShields intentionally not in deps - we only retrigger when the
    // *target* (shields) changes; the rAF loop drives displayShields itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shields]);

  const streakStyle = getStreakStyle(streak);

  return (
    <header className="fixed top-0 left-0 right-0 z-50 backdrop-blur-[24px] border-b" style={{ background: 'var(--header-bg)', borderColor: 'var(--border-glass)', paddingTop: 'env(safe-area-inset-top, 0px)' }}>
      <div className="max-w-lg mx-auto px-4 h-14 flex items-center justify-between relative">
        {/* Left: avatar */}
        <div className="flex items-center z-10">
          <button onClick={() => navigate('/settings/personal')} className="shrink-0">
            {user?.avatar_url ? (
              <img
                src={user.avatar_url}
                alt=""
                referrerPolicy="no-referrer"
                className="w-8 h-8 rounded-full object-cover ring-1 ring-emerald-500/40"
              />
            ) : (
              <div className="w-8 h-8 rounded-full bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center text-xs font-bold text-emerald-400 uppercase">
                {initial}
              </div>
            )}
          </button>
        </div>
        {/* Center: brand name */}
        <button onClick={() => navigate('/about')} className="absolute left-1/2 -translate-x-1/2 active:scale-95 transition-transform z-10">
          <span className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>MacroShot</span>
        </button>
        <div className="flex items-center gap-1.5 z-10">
          {/* Shield count - only visible when gamification is full */}
          {gamification === 'full' && (
            <button
              onClick={() => navigate('/settings/achievements?section=shields')}
              className="flex items-center gap-1 px-2.5 py-1 rounded-full active:scale-95 transition-transform"
              style={{
                background: shields === 0 ? 'rgba(59,130,246,0.05)'
                  : shields === 1 ? 'rgba(59,130,246,0.1)'
                  : shields === 2 ? 'rgba(59,130,246,0.15)'
                  : 'rgba(59,130,246,0.2)',
                border: `1px solid rgba(59,130,246,${shields === 0 ? '0.1' : shields === 1 ? '0.2' : shields === 2 ? '0.3' : '0.4'})`,
                boxShadow: shields >= 3 ? '0 0 12px rgba(59,130,246,0.35), 0 0 4px rgba(59,130,246,0.2)' : 'none',
              }}
            >
              <Shield className="w-3.5 h-3.5" style={{
                color: '#3b82f6',
                filter: shields >= 3 ? 'drop-shadow(0 0 4px rgba(59,130,246,0.6))' : 'none',
              }} />
              <span className="text-xs font-bold tabular-nums" style={{ color: '#3b82f6' }}>
                {displayShields}
              </span>
            </button>
          )}
          {/* Streak flame - also hidden when gamification=off, for parity
              with the shield chip and the celebration popups. The flame is
              gamification UI, even though pre-fix it was rendered always. */}
          {gamification === 'full' && (
            <button
              ref={streakRef}
              onClick={() => navigate('/settings/achievements')}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full active:scale-95 transition-transform"
              style={{
                background: streakStyle.bg,
                border: streakStyle.border,
                boxShadow: streakStyle.shadow,
              }}
            >
              <Flame
                className={`w-3.5 h-3.5 animate-flame-flicker`}
                style={{ color: streakStyle.color, filter: streakStyle.filter }}
                aria-hidden
              />
              <span
                className="text-xs font-bold tabular-nums"
                style={{ color: streakStyle.color, textShadow: streakStyle.textShadow }}
              >
                {streak}
              </span>
            </button>
          )}
        </div>
      </div>
      {/* Shield earned/used both show full-screen celebrations (rendered below).
          Comeback still uses a header toast - it's a softer event without a
          banked reward to dramatize. */}
      {comebackToast && (
        <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 px-4 py-2 rounded-xl text-xs font-semibold animate-fade-in"
          style={{ background: 'rgba(249,115,22,0.15)', border: '1px solid rgba(249,115,22,0.3)', color: '#f97316', whiteSpace: 'nowrap' }}
        >
          <Flame className="w-3.5 h-3.5 inline mr-1.5" style={{ color: '#f97316' }} />
          Welcome back! New streak started
        </div>
      )}
      <Tooltip id="streak_flame" show={streak >= 1 && isEarlyUser()} targetRef={streakRef}>
        Tap your streak to view achievements.
      </Tooltip>
      {shieldCelebration && (
        <ShieldCelebration
          streakDays={streak}
          shieldsBefore={shieldCelebration.shieldsBefore}
          shieldsAfter={shieldCelebration.shieldsAfter}
          bridgedDate={shieldCelebration.bridgedDate}
          onDone={() => setShieldCelebration(null)}
        />
      )}
      {shieldEarnedCelebration && (
        <ShieldEarnedCelebration
          shieldsBefore={shieldEarnedCelebration.shieldsBefore}
          shieldsAfter={shieldEarnedCelebration.shieldsAfter}
          onDone={() => setShieldEarnedCelebration(null)}
        />
      )}
    </header>
  );
}
