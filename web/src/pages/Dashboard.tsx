import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { dashboardApi } from '../api/dashboard';
import ProgressRing, { getTargetColor } from '../components/ProgressRings';
import MealCard from '../components/MealCard';
import WeekStrip from '../components/WeekStrip';
import { Camera, Search } from '../components/icons';
// EmptyState removed - custom welcome card used instead
import SwipeActions from '../components/SwipeActions';
import { DashboardSkeleton, OfflineAwareSkeleton } from '../components/Skeleton';
import { mealsApi } from '../api/meals';
import { workoutsApi, type WorkoutResponse } from '../api/workouts';
import AddWorkout from '../components/AddWorkout';
import type { ExerciseAdjustment } from '../api/dashboard';
import { useToast } from '../components/Toast';
import { useRegisterRefresh } from '../context/PullToRefreshContext';
import { achievementsApi } from '../api/achievements';
import BadgeCelebration from '../components/BadgeCelebration';
import NotificationPrompt from '../components/NotificationPrompt';
import Tooltip from '../components/Tooltip';
import { isEarlyUser } from '../utils/onboarding';
import ProBlur from '../components/ProBlur';
import type { NewBadge } from '../types';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import { schedulePendingDelete, cancelPendingDelete, isDeletePending } from '../utils/pendingMealDelete';
import { signalCriticalDone } from '../utils/requestScheduler';
import type { Meal, MacroTotals, TrendDay, ChallengesResponse } from '../types';

const DEFAULT_TARGET: MacroTotals = { calories: 2000, protein: 150, carbs: 200, fat: 70 };

import { formatLocalDate } from '../utils/date';
import { hapticLight } from '../utils/haptics';

/* ── Weekly Recap helpers ──────────────────────────────────────────── */

/** Get the ISO week string (Monday date) for a given date, used as localStorage key */
/** Get last week's Mon-Sun date range as [startStr, endStr] */
function lastWeekRange(): [string, string] {
  const now = new Date();
  const day = now.getDay(); // 0=Sun
  // Days since this Monday (if Sun, treat as 6 days after Mon)
  const daysSinceThisMonday = day === 0 ? 6 : day - 1;
  const thisMonday = new Date(now);
  thisMonday.setDate(now.getDate() - daysSinceThisMonday);
  const lastMonday = new Date(thisMonday);
  lastMonday.setDate(thisMonday.getDate() - 7);
  const lastSunday = new Date(lastMonday);
  lastSunday.setDate(lastMonday.getDate() + 6);
  return [formatLocalDate(lastMonday), formatLocalDate(lastSunday)];
}

/** Hook: animate a number counting up from 0 to target over `duration` ms */
function useAnimatedCount(target: number, duration: number = 800, enabled: boolean = true): number {
  const [display, setDisplay] = useState(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (!enabled || target === 0) { setDisplay(target); return; }
    const start = performance.now();
    const animate = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(eased * target));
      if (progress < 1) rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration, enabled]);

  return display;
}

const DAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

interface RecapData {
  daysLogged: number;
  totalDays: number;
  avgCalories: number;
  proteinHitDays: number;
  dayDots: boolean[];
  streakNudge: boolean;
  streakDays: number;
}

function WeeklyRecapCard({ data, onDismiss }: { data: RecapData; onDismiss: () => void }) {
  const [visible, setVisible] = useState(false);
  const [dotsReady, setDotsReady] = useState(false);

  useEffect(() => {
    const t1 = setTimeout(() => setVisible(true), 50);
    const t2 = setTimeout(() => setDotsReady(true), 400);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, []);

  const animDays = useAnimatedCount(data.daysLogged, 600, visible);
  const animCal = useAnimatedCount(data.avgCalories, 800, visible);
  const animProt = useAnimatedCount(data.proteinHitDays, 600, visible);
  const pct = Math.round((data.daysLogged / data.totalDays) * 100);

  return (
    <div
      className="glass-card overflow-hidden"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(-12px)',
        transition: 'opacity 0.4s ease, transform 0.4s ease',
      }}
    >
      {/* Accent bar */}
      <div style={{
        height: 2,
        background: 'linear-gradient(90deg, #10b981, #3b82f6, #2dd4bf)',
      }} />

      <div className="px-3 py-2.5">
        {/* Header row: title + dots + dismiss */}
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[11px] font-bold uppercase tracking-wider shrink-0" style={{ color: 'var(--text-muted)' }}>
            Last Week
          </span>

          {/* Day dots inline */}
          <div className="flex items-center gap-1.5 flex-1 justify-center">
            {data.dayDots.map((logged, i) => (
              <div key={i} className="flex flex-col items-center" style={{ gap: 1 }}>
                <div style={{
                  width: 12, height: 12, borderRadius: '50%',
                  background: dotsReady && logged ? '#10b981' : 'var(--track-bg)',
                  boxShadow: dotsReady && logged ? '0 0 6px #10b98160' : 'none',
                  transition: 'all 0.4s ease',
                  transitionDelay: dotsReady ? `${i * 80}ms` : '0ms',
                  transform: dotsReady && logged ? 'scale(1)' : 'scale(0.7)',
                }} />
                <span className="text-[10px] font-semibold" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
                  {DAY_LABELS[i]}
                </span>
              </div>
            ))}
          </div>

          <button onClick={onDismiss} className="p-0.5 shrink-0" style={{ color: 'var(--text-muted)' }} aria-label="Dismiss">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M2 2l8 8M10 2L2 10" />
            </svg>
          </button>
        </div>

        {/* Stats row - compact */}
        <div className="flex items-baseline justify-between">
          <div className="flex items-baseline gap-1">
            <span className="text-base font-black tabular-nums" style={{ color: '#3b82f6' }}>{animDays}/{data.totalDays}</span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>days</span>
          </div>
          <div className="flex items-baseline gap-1">
            <span className="text-base font-black tabular-nums" style={{ color: 'var(--text-primary)' }}>{animCal.toLocaleString()}</span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>kcal/day</span>
          </div>
          <div className="flex items-baseline gap-1">
            <span className="text-base font-black tabular-nums" style={{ color: '#2dd4bf' }}>{animProt}/{data.totalDays}</span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>protein</span>
          </div>
          <div className="flex items-baseline gap-1">
            <span className="text-base font-black tabular-nums" style={{ color: pct >= 80 ? '#10b981' : '#f59e0b' }}>{pct}%</span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>score</span>
          </div>
        </div>

        {/* Streak nudge */}
        {data.streakNudge && (
          <div className="mt-2 flex items-center gap-1.5 text-[11px] font-medium" style={{ color: '#f59e0b' }}>
            <span style={{ fontSize: 14 }}>🔥</span>
            <span>{data.streakDays > 0 ? `${data.streakDays}-day streak - log a meal to keep it!` : 'Log a meal to start your streak!'}</span>
          </div>
        )}
      </div>

    </div>
  );
}

function todayStr() {
  return formatLocalDate();
}

function addDays(dateStr: string, n: number): string {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return formatLocalDate(d);
}

/** Horizontal progress bar used inside macro cards */
function MacroProgress({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="w-full h-1.5 rounded-full mt-2 overflow-hidden" style={{ background: 'var(--track-bg)' }}>
      <div
        className="h-full rounded-full transition-all duration-700"
        style={{
          width: `${pct}%`,
          background: `linear-gradient(90deg, ${color}, ${color}aa)`,
          boxShadow: `0 0 8px ${color}60`,
        }}
      />
    </div>
  );
}

const MACROS = [
  { key: 'protein' as const, label: 'Protein', cssVar: '--color-protein', fallback: '#2dd4bf', bg: 'from-teal-500/20 to-teal-900/10', borderColor: 'border-teal-500/20' },
  { key: 'carbs' as const, label: 'Carbs', cssVar: '--color-carbs', fallback: '#f97316', bg: 'from-orange-500/20 to-orange-900/10', borderColor: 'border-orange-500/20' },
  { key: 'fat' as const, label: 'Fat', cssVar: '--color-fat', fallback: '#22c55e', bg: 'from-green-500/20 to-green-900/10', borderColor: 'border-green-500/20' },
];


export default function Dashboard() {
  const { user: authUser } = useAuth();
  const uid = authUser?.user_id || '';

  // Restore from in-memory cache for instant re-mount after navigation
  // Persist selected date in URL so navigating back restores the viewed day
  const [searchParams, setSearchParams] = useSearchParams();
  const dateFromUrl = searchParams.get('date');
  const today = todayStr();
  const initialDate = (() => {
    if (dateFromUrl && /^\d{4}-\d{2}-\d{2}$/.test(dateFromUrl) && dateFromUrl <= today) return dateFromUrl;
    return today;
  })();
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;

  // Cache keyed by date - instant re-mount for any previously viewed day
  const cachedDay = getCached<{ totals: MacroTotals & { meal_count: number }; target: MacroTotals; adjusted_target: MacroTotals | null; exercise_adjustment: ExerciseAdjustment | null; remaining: MacroTotals | null; recent_meals: Meal[] }>(`dash_day_${initialDate}`);
  const cachedTrend = getCached<TrendDay[]>('dash_trend');

  const [selectedDate, setSelectedDate] = useState(initialDate);
  const [totals, setTotals] = useState<MacroTotals & { meal_count: number }>(cachedDay?.totals || { calories: 0, protein: 0, carbs: 0, fat: 0, meal_count: 0 });
  const [target, setTarget] = useState<MacroTotals>(cachedDay?.target || DEFAULT_TARGET);
  const [remaining, setRemaining] = useState<MacroTotals | null>(cachedDay?.remaining ?? null);
  const [meals, setMeals] = useState<Meal[]>(cachedDay?.recent_meals || []);
  const [weekDays, setWeekDays] = useState<TrendDay[]>(cachedTrend || []);
  const [loading, setLoading] = useState(!cachedDay);
  const [showRemaining, setShowRemaining] = useState(false);
  const [fetchError, setFetchError] = useState('');
  const [relogBadges, setRelogBadges] = useState<NewBadge[]>([]);
  const [copyingDay, setCopyingDay] = useState(false);
  const [workoutData, setWorkoutData] = useState<WorkoutResponse | null>(getCached<WorkoutResponse>('dash_workouts') ?? null);
  const [challengeData, setChallengeData] = useState<ChallengesResponse | null>(getCached<ChallengesResponse>('dash_challenges') ?? null);
  const [adjustedTarget, setAdjustedTarget] = useState<MacroTotals | null>(cachedDay?.adjusted_target ?? null);
  const [exerciseAdjustment, setExerciseAdjustment] = useState<ExerciseAdjustment | null>(cachedDay?.exercise_adjustment ?? null);
  const { toast } = useToast();

  // Weekly recap state
  const [recapTrendData, setRecapTrendData] = useState<TrendDay[] | null>(null);
  const [recapDismissed, setRecapDismissed] = useState(() => {
    // Dismiss key includes the day - dismissed on Monday can reappear on Tuesday
    const key = `recap_dismissed_${todayStr()}`;
    return localStorage.getItem(key) === '1';
  });

  // Tooltip refs
  const heroCardRef = useRef<HTMLButtonElement>(null);
  const firstMealRef = useRef<HTMLDivElement>(null);

  // Undo delete
  // Strip meals that the user just deleted-with-undo from any server-derived
  // list. Without this, a refresh during the 5s undo window (loadDay,
  // refreshAll, copy-yesterday reload, etc.) repopulates state with the
  // pre-delete view and the just-deleted card flashes back into the UI.
  const dropPending = useCallback((list: Meal[]): Meal[] =>
    list.filter((m) => !isDeletePending(m.id)), []);

  // When the server kicks off a background Fitbit/Oura sync (sync_pending),
  // schedule one follow-up refresh so the activity card and exercise-
  // adjusted totals pick up fresh numbers without the user pulling to
  // refresh. Re-uses the existing 'workouts-synced' event so the workout
  // card refetches alongside dashboard totals.
  const scheduleSyncFollowup = useCallback((data: { sync_pending?: boolean }) => {
    if (!data.sync_pending) return;
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('workouts-synced'));
    }, 2500);
  }, []);

  // Re-fetch workout data when a sync completes (e.g., user just connected Fitbit/Strava)
  useEffect(() => {
    const handler = () => {
      const date = selectedDate;
      workoutsApi.get(date).then((wd) => {
        setWorkoutData(wd);
        if (date === todayStr()) setCache('dash_workouts', wd);
      }).catch(() => {});
      // Also refresh dashboard totals for exercise adjustment
      dashboardApi.today(date, { refresh: true }).then((data) => {
        setTotals(data.totals);
        setAdjustedTarget(data.adjusted_target ?? null);
        setExerciseAdjustment(data.exercise_adjustment ?? null);
        setRemaining(data.remaining ?? null);
      }).catch(() => {});
    };
    window.addEventListener('workouts-synced', handler);
    return () => window.removeEventListener('workouts-synced', handler);
  }, [selectedDate]);

  const subtractMacros = useCallback((meal: Meal) => {
    setTotals((prev) => ({
      ...prev,
      calories: prev.calories - meal.calories,
      protein: prev.protein - meal.protein,
      carbs: prev.carbs - meal.carbs,
      fat: prev.fat - meal.fat,
      meal_count: prev.meal_count - 1,
    }));
    setRemaining((prev) => prev ? ({
      ...prev,
      calories: prev.calories + meal.calories,
      protein: prev.protein + meal.protein,
      carbs: prev.carbs + meal.carbs,
      fat: prev.fat + meal.fat,
    }) : null);
  }, []);

  const addMacros = useCallback((meal: Meal) => {
    setTotals((prev) => ({
      ...prev,
      calories: prev.calories + meal.calories,
      protein: prev.protein + meal.protein,
      carbs: prev.carbs + meal.carbs,
      fat: prev.fat + meal.fat,
      meal_count: prev.meal_count + 1,
    }));
    setRemaining((prev) => prev ? ({
      ...prev,
      calories: prev.calories - meal.calories,
      protein: prev.protein - meal.protein,
      carbs: prev.carbs - meal.carbs,
      fat: prev.fat - meal.fat,
    }) : null);
  }, []);

  const mealsRef = useRef<Meal[]>(meals);
  mealsRef.current = meals;

  const deleteMeal = useCallback((id: number) => {
    const meal = mealsRef.current.find((m) => m.id === id);
    if (!meal) return;

    setMeals((prev) => prev.filter((m) => m.id !== id));
    subtractMacros(meal);

    schedulePendingDelete(id, {
      cachesToClear: [
        `dash_day_${selectedDate}`,
        `dash_today_${selectedDate}`,
        'trends',
        'achievements',
        'achievement_summary',
        'challenges',
      ],
    });

    toast('Meal deleted', 'success', {
      label: 'Undo',
      onClick: () => {
        if (cancelPendingDelete(id)) {
          setMeals((prev) => [...prev, meal].sort((a, b) => b.id - a.id));
          addMacros(meal);
        }
      },
    });
  }, [toast, subtractMacros, addMacros, selectedDate]);

  const loadDay = useCallback((date: string) => {
    setLoading(true);
    setSelectedDate(date);
    // Sync date to URL so navigating back restores this day
    const today = todayStr();
    if (date === today) {
      setSearchParamsRef.current({}, { replace: true });
    } else {
      setSearchParamsRef.current({ date }, { replace: true });
    }
    window.dispatchEvent(new CustomEvent('viewingDate', { detail: date }));
    setFetchError('');
    dashboardApi.today(date, { refresh: true }).then((data) => {
      setTotals(data.totals);
      if (data.target) setTarget(data.target);
      setAdjustedTarget(data.adjusted_target ?? null);
      setExerciseAdjustment(data.exercise_adjustment ?? null);
      setRemaining(data.remaining ?? null);
      setMeals(dropPending(data.recent_meals));
      setCache(`dash_day_${date}`, data);
      scheduleSyncFollowup(data);
    }).catch((err) => {
      console.error(err);
      setFetchError('Could not load data. Pull down to refresh.');
    }).finally(() => setLoading(false));
    // Reload week strip data for the week containing the selected date
    // Compute Sunday of this week as the trend endpoint "today" param
    const d = new Date(date + 'T00:00:00');
    const dow = d.getDay(); // 0=Sun
    const sundayOffset = dow === 0 ? 0 : 7 - dow;
    const sunday = new Date(d);
    sunday.setDate(d.getDate() + sundayOffset);
    // Don't request future dates - cap at actual today
    const sundayStr = formatLocalDate(sunday) <= today ? formatLocalDate(sunday) : today;
    dashboardApi.trend(7, sundayStr, { refresh: true, calendar: true }).then((trendData) => {
      setWeekDays(trendData.days);
    }).catch(() => {});
    // Fetch workouts for this day (non-blocking)
    workoutsApi.get(date).then((wd) => {
      setWorkoutData(wd);
      if (date === todayStr()) setCache('dash_workouts', wd);
    }).catch(() => setWorkoutData(null));
  }, [dropPending, scheduleSyncFollowup]);

  const relogMeal = useCallback(async (id: number) => {
    try {
      const res = await mealsApi.relog(id);
      toast('Meal re-logged!');
      if (res.new_badges?.length) {
        setRelogBadges(res.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      }
      // Always reload today since relog uses current timestamp
      const today = todayStr();
      loadDay(today);
    } catch {
      toast('Failed to re-log', 'error');
    }
  }, [toast, loadDay]);

  const copyYesterday = useCallback(async () => {
    setCopyingDay(true);
    try {
      const yesterday = addDays(selectedDate, -1);
      const res = await mealsApi.copyDay(yesterday, selectedDate);
      toast(`Copied ${res.copied_count} meal${res.copied_count === 1 ? '' : 's'}!`);
      if (res.new_badges?.length) {
        setRelogBadges(res.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      }
      // Clear caches and reload the current day
      clearCache(`dash_day_${selectedDate}`);
      clearCache(`dash_today_${selectedDate}`);
      loadDay(selectedDate);
    } catch {
      toast('No meals to copy from yesterday', 'error');
    } finally {
      setCopyingDay(false);
    }
  }, [selectedDate, toast, loadDay]);

  // refreshAll: dropPending dependency; same rationale as loadDay above.
  const refreshAll = useCallback(async () => {
    setFetchError('');
    const [trendResult, todayResult, workoutsResult] = await Promise.allSettled([
      dashboardApi.trend(undefined, undefined, { refresh: true }),
      dashboardApi.today(selectedDate, { refresh: true }),
      workoutsApi.get(selectedDate),
    ]);
    if (todayResult.status === 'fulfilled') {
      const data = todayResult.value;
      setTotals(data.totals);
      if (data.target) setTarget(data.target);
      setAdjustedTarget(data.adjusted_target ?? null);
      setExerciseAdjustment(data.exercise_adjustment ?? null);
      setRemaining(data.remaining ?? null);
      setMeals(dropPending(data.recent_meals));
      if (selectedDate === todayStr()) {
        setCache(`dash_today_${todayStr()}`, data);
      }
      scheduleSyncFollowup(data);
    }
    if (workoutsResult.status === 'fulfilled') {
      setWorkoutData(workoutsResult.value);
      if (selectedDate === todayStr()) setCache('dash_workouts', workoutsResult.value);
    } else {
      setWorkoutData(null);
    }
    if (trendResult.status === 'fulfilled') {
      setWeekDays(trendResult.value.days);
      setCache('dash_trend', trendResult.value.days);
    }
  }, [selectedDate, dropPending, scheduleSyncFollowup]);

  // Daily target celebration - after 7 PM, if close to target with 3+ meals
  useEffect(() => {
    if (loading) return;
    if (selectedDate !== todayStr()) return;
    const hour = new Date().getHours();
    if (hour < 19) return; // Only trigger in the evening
    if (totals.meal_count < 3) return;
    const effectiveCals = (adjustedTarget || target).calories;
    if (effectiveCals <= 0) return;

    const ratio = totals.calories / effectiveCals;
    if (ratio < 0.9 || ratio > 1.1) return; // Not within 10% of target

    const targetKey = `daily_target_toast_${todayStr()}`;
    if (localStorage.getItem(targetKey)) return;
    localStorage.setItem(targetKey, '1');
    toast('On target today! \uD83C\uDFAF');
  }, [loading, totals, target, adjustedTarget, selectedDate, toast]);

  useRegisterRefresh(refreshAll);

  useEffect(() => {
    // Respect URL date param when navigating back to a past day
    const mountDate = initialDate;
    if (!cachedDay) setLoading(true);
    setSelectedDate(mountDate);
    window.dispatchEvent(new CustomEvent('viewingDate', { detail: mountDate }));
    // Always use refresh: true so Dashboard gets fresh data after mutations
    // (e.g. user just logged a meal). The in-memory cache above provides
    // instant display while we wait for the network response.
    // Critical requests first - trend + today drive the main UI.
    // Non-critical requests (workouts, challenges) fire AFTER to avoid
    // overwhelming the single-vCPU VM with 8+ concurrent requests.
    //
    // Use allSettled (NOT all) so a single transient failure - e.g. trend
    // timing out on a cold-started VM while today succeeds - doesn't throw
    // away the request that DID succeed. With Promise.all, any rejection
    // skipped every setter below, leaving the dashboard at its initial
    // empty state (0 kcal, dotted week strip) until the user navigated to
    // another day and back.
    Promise.allSettled([
      dashboardApi.trend(undefined, undefined, { refresh: true }),
      dashboardApi.today(mountDate, { refresh: true }),
    ]).then(([trendResult, dayResult]) => {
      if (trendResult.status === 'fulfilled') {
        setWeekDays(trendResult.value.days);
        setCache('dash_trend', trendResult.value.days);
      }
      if (dayResult.status === 'fulfilled') {
        const dayData = dayResult.value;
        setTotals(dayData.totals);
        if (dayData.target) setTarget(dayData.target);
        setAdjustedTarget(dayData.adjusted_target ?? null);
        setExerciseAdjustment(dayData.exercise_adjustment ?? null);
        setRemaining(dayData.remaining ?? null);
        setMeals(dropPending(dayData.recent_meals));
        setCache(`dash_day_${mountDate}`, dayData);
        scheduleSyncFollowup(dayData);
      } else if (import.meta.env.DEV) {
        console.error(dayResult.reason);
      }
      // Only show the error banner when the today request itself failed.
      // A missing week strip is visually obvious; a missing today card is
      // what triggers the "did I lose my data?" panic.
      if (dayResult.status === 'rejected') {
        setFetchError('Could not load data. Pull down to refresh.');
      }
      signalCriticalDone();

      // Non-critical requests fire regardless of trend/today outcome so a
      // transient trend timeout doesn't also kill workouts + challenges.
      workoutsApi.get(mountDate).then((wd) => {
        setWorkoutData(wd);
        if (mountDate === todayStr()) setCache('dash_workouts', wd);
      }).catch(() => {});

      achievementsApi.getChallenges().then((challenges: ChallengesResponse) => {
        setChallengeData(challenges);
        setCache('dash_challenges', challenges);
        // Use localStorage (persists across app reopens) not sessionStorage
        const challengeKey = `challenge_seen_${today}`;
        const shown = localStorage.getItem(challengeKey);
        let shownIds: string[] = [];
        try { shownIds = shown ? JSON.parse(shown) : []; } catch { /* corrupted */ }

        const checkChallenge = (c: ChallengesResponse['daily'] | ChallengesResponse['weekly']) => {
          if (c && c.completed && !shownIds.includes(c.id)) {
            shownIds.push(c.id);
            localStorage.setItem(challengeKey, JSON.stringify(shownIds));
            toast(`Challenge Complete: ${c.name} \uD83C\uDFAF`);
          }
        };
        checkChallenge(challenges.daily);
        checkChallenge(challenges.weekly);
      }).catch(() => {});

      // Weekly recap: fetch 14-day trend on Mon/Tue to get full last week
      const localDay = new Date().getDay(); // 0=Sun, 1=Mon, ...
      if (localDay === 1 || localDay === 2) {
        dashboardApi.trend(14).then((data) => {
          setRecapTrendData(data.days);
        }).catch(() => {});
      }

      // If trend failed but today succeeded, retry trend once in the
      // background so the strip backfills without user action. The day
      // card is already populated, so no error UI is needed.
      if (trendResult.status === 'rejected' && dayResult.status === 'fulfilled') {
        if (import.meta.env.DEV) console.error(trendResult.reason);
        dashboardApi.trend(undefined, undefined, { refresh: true }).then((td) => {
          setWeekDays(td.days);
          setCache('dash_trend', td.days);
        }).catch(() => {});
      }
    }).finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Swipe between days on hero card
  const heroStartXRef = useRef(0);
  const heroStartYRef = useRef(0);
  const heroSwipingRef = useRef(false);

  const onHeroTouchStart = useCallback((e: React.TouchEvent) => {
    heroStartXRef.current = e.touches[0].clientX;
    heroStartYRef.current = e.touches[0].clientY;
    heroSwipingRef.current = false;
  }, []);

  const heroDxRef = useRef(0);

  const onHeroMove = useCallback((e: React.TouchEvent) => {
    const dx = e.touches[0].clientX - heroStartXRef.current;
    const dy = e.touches[0].clientY - heroStartYRef.current;
    heroDxRef.current = dx;
    if (!heroSwipingRef.current && Math.abs(dx) > 15 && Math.abs(dx) > Math.abs(dy)) {
      heroSwipingRef.current = true;
    }
  }, []);

  const onHeroEnd = useCallback(() => {
    if (!heroSwipingRef.current) return;
    heroSwipingRef.current = false;
    const dx = heroDxRef.current;
    heroDxRef.current = 0;
    const today = todayStr();

    if (dx > 50) {
      // Swipe right → previous day
      hapticLight();
      const prev = addDays(selectedDate, -1);
      loadDay(prev);
    } else if (dx < -50) {
      // Swipe left → next day (only if not future)
      const next = addDays(selectedDate, 1);
      if (next <= today) { hapticLight(); loadDay(next); }
    }
  }, [selectedDate, loadDay]);

  const [showTargetBanner, setShowTargetBanner] = useState(() => localStorage.getItem(`targets_skipped_${uid}`) === 'true' && !localStorage.getItem(`targets_banner_dismissed_${uid}`));

  // Weekly Recap: compute last week's data from 14-day trend
  const recapData = useMemo((): RecapData | null => {
    if (!recapTrendData || recapTrendData.length === 0) return null;
    const [lastMon, lastSun] = lastWeekRange();
    const lastWeekDays = recapTrendData.filter(
      (d) => d.date >= lastMon && d.date <= lastSun
    );
    if (lastWeekDays.length === 0) return null;

    lastWeekDays.sort((a, b) => a.date.localeCompare(b.date));

    const daysLogged = lastWeekDays.filter((d) => d.meal_count > 0).length;
    const daysWithCals = lastWeekDays.filter((d) => d.calories > 0);
    const avgCalories = daysWithCals.length > 0
      ? Math.round(daysWithCals.reduce((s, d) => s + d.calories, 0) / daysWithCals.length)
      : 0;
    const proteinTarget = target.protein * 0.9;
    const proteinHitDays = lastWeekDays.filter((d) => d.protein >= proteinTarget).length;

    const dayDots: boolean[] = [];
    const mon = new Date(lastMon + 'T00:00:00');
    for (let i = 0; i < 7; i++) {
      const d = new Date(mon);
      d.setDate(mon.getDate() + i);
      const dateStr = formatLocalDate(d);
      const found = lastWeekDays.find((td) => td.date === dateStr);
      dayDots.push(found ? found.meal_count > 0 : false);
    }

    // Streak nudge: check if yesterday had no meals
    const yesterday = addDays(todayStr(), -1);
    const yesterdayData = recapTrendData.find((d) => d.date === yesterday);
    const streakNudge = !yesterdayData || yesterdayData.meal_count === 0;

    // Check today too - if today has meals, no nudge needed
    const todayData = weekDays.find((d) => d.date === todayStr());
    const todayHasMeals = todayData && todayData.meal_count > 0;

    // Compute streak from trend data (consecutive days with meals, backwards from yesterday)
    const sorted = [...recapTrendData].sort((a, b) => b.date.localeCompare(a.date));
    let streakCount = 0;
    for (const d of sorted) {
      if (d.date >= todayStr()) continue;
      if (d.meal_count > 0) streakCount++;
      else break;
    }

    return {
      daysLogged, totalDays: 7, avgCalories, proteinHitDays, dayDots,
      streakNudge: streakNudge && !todayHasMeals,
      streakDays: streakCount,
    };
  }, [recapTrendData, target.protein, weekDays]);

  // Show recap: Mon/Tue always, other days only if yesterday had no meals (streak nudge)
  // Skip if user logged 0 days last week (new user, first week)
  const showRecap = useMemo(() => {
    if (recapDismissed || !recapData) return false;
    if (recapData.daysLogged === 0) return false; // New user - no data to recap
    const localDay = new Date().getDay(); // 0=Sun
    // Mon/Tue: always show (last week recap)
    if (localDay === 1 || localDay === 2) return true;
    // Other days: show if streak nudge (yesterday had no meals)
    return recapData.streakNudge;
  }, [recapDismissed, recapData]);

  const dismissRecap = useCallback(() => {
    setRecapDismissed(true);
    localStorage.setItem(`recap_dismissed_${todayStr()}`, '1');
  }, []);

  const isInitialLoad = loading && meals.length === 0 && weekDays.length === 0;

  if (isInitialLoad) {
    return <OfflineAwareSkeleton skeleton={<DashboardSkeleton />} />;
  }

  const toggleView = () => { hapticLight(); setShowRemaining((v) => !v); };
  const effectiveTarget = adjustedTarget || target;

  return (
    <div className="space-y-4">
      {relogBadges.length > 0 && (
        <BadgeCelebration badges={relogBadges} onDone={() => setRelogBadges([])} />
      )}
      {/* Skipped targets banner */}
      {showTargetBanner && (
        <div className="rounded-2xl p-3 flex items-center gap-3" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)' }}>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>Your targets are set to general defaults</p>
            <Link to="/settings/targets?mode=settings" className="text-xs font-semibold" style={{ color: 'var(--color-calories)' }}>Set personalized targets →</Link>
          </div>
          <button
            onClick={() => { setShowTargetBanner(false); localStorage.setItem(`targets_banner_dismissed_${uid}`, 'true'); }}
            className="p-1 rounded-lg shrink-0"
            style={{ color: 'var(--text-muted)' }}
          >
            ✕
          </button>
        </div>
      )}
      {/* Week Strip */}
      <div className="glass-card p-2.5">
        <WeekStrip days={weekDays} selectedDate={selectedDate} onSelect={loadDay} target={target.calories} />
      </div>

      {/* Weekly Recap Card - shown Mon/Tue at start of week */}
      {showRecap && recapData && (
        <ProBlur message="Weekly recap - Pro">
          <WeeklyRecapCard data={recapData} onDismiss={dismissRecap} />
        </ProBlur>
      )}

      {/* Hero Calorie Card - tap to toggle, swipe to change day */}
      <button
        ref={heroCardRef}
        data-swipe-handler
        onClick={toggleView}
        onTouchStart={onHeroTouchStart}
        onTouchMove={onHeroMove}
        onTouchEnd={onHeroEnd}
        className="relative overflow-hidden rounded-2xl p-5 flex flex-col items-center border border-blue-500/15 w-full transition-all duration-300 active:scale-[0.98]"
        style={{
          background: 'linear-gradient(135deg, rgba(59,130,246,0.1) 0%, var(--bg-card) 50%, rgba(59,130,246,0.05) 100%)',
          boxShadow: '0 16px 48px rgba(0,0,0,0.15), inset 0 1px 0 var(--border-glass), 0 0 40px rgba(59,130,246,0.08)',
        }}
      >
        <div className="text-[11px] uppercase tracking-widest font-bold mb-3" style={{ color: 'var(--text-muted)' }}>
          {showRemaining ? 'Remaining' : 'Calories'}
        </div>
        <div className="relative">
          <ProgressRing
            eaten={totals.calories}
            target={effectiveTarget.calories}
            label=""
            unit=""
            color="#3b82f6"
            autoColor
            size={150}
            strokeWidth={10}
            gradient
            glow
          />
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span
              className="text-4xl font-black tabular-nums transition-all duration-300"
              style={{ color: showRemaining && remaining && remaining.calories < 0 ? '#f87171' : 'var(--text-primary)' }}
            >
              {showRemaining && remaining
                ? `${remaining.calories < 0 ? '+' : ''}${Math.abs(Math.round(remaining.calories)).toLocaleString()}`
                : Math.round(totals.calories).toLocaleString()
              }
            </span>
            <span className="text-[11px] uppercase tracking-widest mt-0.5 font-semibold" style={{ color: 'var(--text-secondary)' }}>
              {showRemaining
                ? (remaining && remaining.calories < 0 ? 'over target' : 'kcal left')
                : `/ ${Math.round(effectiveTarget.calories).toLocaleString()} kcal`
              }
            </span>
            {exerciseAdjustment && exerciseAdjustment.active_calories > 0 && !showRemaining && (
              <div className="flex flex-col items-center mt-0.5">
                <span className="text-[11px] font-bold" style={{ color: 'var(--color-carbs, #f97316)' }}>
                  +{exerciseAdjustment.calories} from workouts
                </span>
                <span className="text-[11px] font-semibold" style={{ color: getTargetColor(totals.calories, effectiveTarget.calories, '#3b82f6') }}>
                  net {Math.round(totals.calories - exerciseAdjustment.active_calories).toLocaleString()} kcal
                </span>
              </div>
            )}
          </div>
        </div>
        <div className="text-[11px] mt-2 font-medium" style={{ color: 'var(--text-muted)' }}>tap for {showRemaining ? 'eaten' : 'remaining'}</div>
      </button>

      {fetchError && (
        <p className="text-xs text-center py-2" style={{ color: 'var(--text-secondary)' }}>{fetchError}</p>
      )}

      {/* Macro Cards - tappable to toggle eaten/remaining */}
      <div className="grid grid-cols-3 gap-3">
        {MACROS.map((m) => {
          const eaten = totals[m.key];
          const mTarget = effectiveTarget[m.key];
          const rem = remaining ? remaining[m.key] : mTarget - eaten;
          const isOver = rem < 0;
          const displayVal = showRemaining ? Math.abs(Math.round(rem)) : Math.round(eaten);
          const pct = mTarget > 0 ? Math.round((eaten / mTarget) * 100) : 0;

          const color = `var(${m.cssVar}, ${m.fallback})`;
          return (
            <button
              key={m.key}
              onClick={toggleView}
              className={`relative overflow-hidden rounded-2xl p-3.5 border ${m.borderColor} bg-gradient-to-br ${m.bg} text-left transition-all duration-300 active:scale-[0.97]`}
              style={{
                boxShadow: `0 8px 24px rgba(0,0,0,0.1), inset 0 1px 0 var(--border-glass)`,
                backdropFilter: 'blur(16px)',
              }}
            >
              <p className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-secondary)' }}>
                {showRemaining ? `${m.label} left` : m.label}
              </p>
              <div className="flex items-baseline gap-0.5 mt-1">
                <span
                  className="text-2xl font-black tabular-nums transition-all duration-300"
                  style={{ color: showRemaining && isOver ? '#f87171' : color }}
                >
                  {showRemaining && isOver ? '+' : ''}{displayVal}
                </span>
                <span className="text-[11px] font-semibold" style={{ color: 'var(--text-secondary)' }}>g</span>
              </div>
              <MacroProgress value={eaten} max={mTarget} color={getTargetColor(eaten, mTarget, m.fallback)} />
              <p className="text-[11px] mt-1.5 font-bold tabular-nums" style={{ color }}>
                {showRemaining && isOver ? 'over' : `${pct}%`}
              </p>
            </button>
          );
        })}
      </div>

      {/* Activity — single unified card. Empty state, stats, workout list, and
          add-workout entry point all live here. */}
      {workoutData && (() => {
        const isEmpty = workoutData.totals.workout_count === 0 && workoutData.totals.steps === 0;
        const isConnected = workoutData.fitbit_connected || workoutData.strava_connected;
        const refreshAfterSave = () => {
          workoutsApi.get(selectedDate, { refresh: true }).then((wd) => {
            setWorkoutData(wd);
            if (selectedDate === todayStr()) setCache('dash_workouts', wd);
          }).catch(() => {});
          dashboardApi.today(selectedDate, { refresh: true }).then((data) => {
            setTotals(data.totals);
            setAdjustedTarget(data.adjusted_target ?? null);
            setExerciseAdjustment(data.exercise_adjustment ?? null);
            setRemaining(data.remaining ?? null);
            scheduleSyncFollowup(data);
          }).catch(() => {});
        };
        return (
          <div className="glass-card p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="section-heading">Activity</h2>
              <div className="flex items-center gap-3">
                {exerciseAdjustment && exerciseAdjustment.calories > 0 && (
                  <span className="text-[11px] font-bold tabular-nums px-2 py-0.5 rounded-full"
                    style={{ color: 'var(--color-carbs, #f97316)', background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.18)' }}>
                    +{exerciseAdjustment.calories} kcal
                  </span>
                )}
                <Link to="/workouts" className="text-[11px] font-semibold" style={{ color: '#3b82f6' }}>History →</Link>
              </div>
            </div>

            {/* Stats row — only when there's activity */}
            {!isEmpty && (
              <div className="grid grid-cols-3 gap-2 mb-3">
                {workoutData.totals.active_calories > 0 && (
                  <div className="rounded-lg px-2.5 py-2" style={{ background: 'rgba(249,115,22,0.06)', border: '1px solid rgba(249,115,22,0.12)' }}>
                    <div className="text-base font-black tabular-nums leading-none" style={{ color: 'var(--color-carbs)' }}>
                      {Math.round(workoutData.totals.active_calories)}
                    </div>
                    <div className="text-[10px] mt-1 font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>cal burned</div>
                  </div>
                )}
                {workoutData.totals.steps > 0 && (
                  <div className="rounded-lg px-2.5 py-2" style={{ background: 'rgba(0,176,185,0.06)', border: '1px solid rgba(0,176,185,0.14)' }}>
                    <div className="text-base font-black tabular-nums leading-none" style={{ color: '#00B0B9' }}>
                      {workoutData.totals.steps.toLocaleString()}
                    </div>
                    <div className="text-[10px] mt-1 font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>steps</div>
                  </div>
                )}
                {workoutData.totals.active_minutes > 0 ? (
                  <div className="rounded-lg px-2.5 py-2" style={{ background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.14)' }}>
                    <div className="text-base font-black tabular-nums leading-none" style={{ color: 'var(--color-fat)' }}>
                      {workoutData.totals.active_minutes}
                    </div>
                    <div className="text-[10px] mt-1 font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>active min</div>
                  </div>
                ) : workoutData.totals.total_duration_sec > 0 ? (
                  <div className="rounded-lg px-2.5 py-2" style={{ background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.14)' }}>
                    <div className="text-base font-black tabular-nums leading-none" style={{ color: 'var(--color-fat)' }}>
                      {Math.round(workoutData.totals.total_duration_sec / 60)}
                    </div>
                    <div className="text-[10px] mt-1 font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>minutes</div>
                  </div>
                ) : null}
              </div>
            )}

            {/* Empty state — sync hint for today, simple message for past dates */}
            {isEmpty && selectedDate === today && (
              <Link
                to="/settings/connected-apps"
                className="flex items-center gap-3 rounded-lg p-3 mb-3 transition-all active:scale-[0.99]"
                style={{ background: 'linear-gradient(135deg, rgba(59,130,246,0.08), rgba(59,130,246,0.02))', border: '1px solid rgba(59,130,246,0.15)' }}
              >
                <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.25)' }}>
                  <span className="text-base">🏃</span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {isConnected ? 'No activity yet today' : 'Track your workouts'}
                  </p>
                  <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    {isConnected ? 'Waiting for sync — pull to refresh or check your watch' : 'Connect Strava or Fitbit to sync automatically'}
                  </p>
                </div>
                <span className="text-sm font-semibold shrink-0" style={{ color: '#3b82f6' }}>→</span>
              </Link>
            )}
            {isEmpty && selectedDate !== today && (
              <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
                No activity logged for this day.
              </p>
            )}

            {/* Individual workouts */}
            {workoutData.workouts.length > 0 && (
              <div className="space-y-1 mb-3">
                {workoutData.workouts.map((w, i) => (
                  <div key={w.id} className="flex items-center justify-between py-2"
                    style={{ borderTop: i === 0 ? 'none' : '1px solid var(--border-glass)' }}>
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="w-7 h-7 rounded-full flex items-center justify-center text-[12px] shrink-0"
                        style={{ background: w.source === 'strava' ? 'rgba(252,76,2,0.15)' : 'rgba(0,176,185,0.15)' }}>
                        {w.source === 'strava' ? '⚡' : '🏃'}
                      </span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <p className="text-xs font-semibold truncate">{w.name || w.activity_type}</p>
                          {w.started_at && (
                            <span className="text-[11px] shrink-0" style={{ color: 'var(--text-muted)' }}>
                              {new Date(w.started_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                            </span>
                          )}
                        </div>
                        <div className="flex gap-1.5 mt-0.5">
                          {w.duration_sec > 0 && (
                            <span className="text-[11px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                              {Math.round(w.duration_sec / 60)}min
                            </span>
                          )}
                          {w.distance_m > 0 && (
                            <span className="text-[11px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                              {(w.distance_m / 1000).toFixed(1)}km
                            </span>
                          )}
                          {w.avg_heart_rate > 0 && (
                            <span className="text-[11px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: '#ef4444' }}>
                              ♥ {Math.round(w.avg_heart_rate)}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    {w.calories_burned > 0 && (
                      <div className="text-right shrink-0">
                        <span className="text-xs font-bold tabular-nums" style={{ color: 'var(--color-carbs)' }}>
                          {Math.round(w.calories_burned)}
                        </span>
                        <span className="text-[11px] ml-0.5" style={{ color: 'var(--text-muted)' }}>cal</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <AddWorkout date={selectedDate} onSaved={refreshAfterSave} />
          </div>
        );
      })()}

      {/* Notification permission prompt - shown after first meal accept */}
      <NotificationPrompt />

      {/* Challenges - compact card linking to Achievements */}
      {challengeData && (challengeData.daily || challengeData.weekly) && (
        <div>
          <div className="flex justify-between items-center mb-2">
            <h2 className="section-heading">Challenges</h2>
            <Link to="/settings/achievements" className="text-xs font-semibold" style={{ color: 'var(--color-calories)' }}>See all →</Link>
          </div>
          <Link to="/settings/achievements" className="glass-card p-3 block active:scale-[0.98] transition-transform">
            <div className="space-y-2.5">
              {[challengeData.daily, challengeData.weekly].filter(Boolean).map((c) => {
                if (!c) return null;
                const pct = c.target > 0 ? Math.min(c.progress / c.target, 1) : 0;
                const isDaily = c.type === 'daily';
                const color = c.completed ? '#10b981' : (isDaily ? '#3b82f6' : '#a855f7');
                return (
                  <div key={c.id} className="flex items-center gap-2.5">
                    <span className="text-lg shrink-0">{c.completed ? '✅' : c.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-0.5">
                        <p className="text-xs font-semibold" style={{ color: c.completed ? 'var(--text-muted)' : 'var(--text-primary)', textDecoration: c.completed ? 'line-through' : undefined }}>
                          {c.name}
                        </p>
                        <span className="text-[11px] font-bold tabular-nums shrink-0" style={{ color: 'var(--text-muted)' }}>
                          {c.progress}/{c.target}
                        </span>
                      </div>
                      <div className="h-1 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${pct * 100}%`, background: color }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Link>
        </div>
      )}

      {/* My Meals */}
      <div>
        <div className="flex justify-between items-center mb-3">
          <h2 className="section-heading">My Meals</h2>
          <Link to="/journal" className="text-xs font-semibold" style={{ color: 'var(--color-calories)' }}>See all →</Link>
        </div>
        {meals.length === 0 ? (
          <div className="glass-card p-5 text-center">
            <p className="text-lg font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
              {localStorage.getItem('has_logged_meal') ? "Nothing logged yet today." : "Welcome to MacroShot!"}
            </p>
            <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
              {localStorage.getItem('has_logged_meal')
                ? "Tap below to log your next meal."
                : "Snap a photo, type a description, or scan a barcode \u2014 AI does the rest in seconds."}
            </p>
            <div className="flex justify-center gap-6 mb-4">
              <div className="flex flex-col items-center gap-1">
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: 'rgba(16,185,129,0.1)' }}>
                  <Camera className="w-5 h-5" style={{ color: '#10b981' }} />
                </div>
                <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Photo</span>
              </div>
              <div className="flex flex-col items-center gap-1">
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: 'rgba(59,130,246,0.1)' }}>
                  <Search className="w-5 h-5" style={{ color: '#3b82f6' }} />
                </div>
                <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Text</span>
              </div>
              <div className="flex flex-col items-center gap-1">
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: 'rgba(168,85,247,0.1)' }}>
                  <span className="text-lg">📷</span>
                </div>
                <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Barcode</span>
              </div>
            </div>
            <Link to="/log" className="glass-btn glass-btn-primary inline-flex items-center justify-center text-sm px-5 py-2.5 font-semibold rounded-xl">
              Log Your First Meal
            </Link>
            {localStorage.getItem('has_logged_meal') && (() => {
              const yesterdayStr = addDays(selectedDate, -1);
              const yesterdayHadMeals = (weekDays.find((d) => d.date === yesterdayStr)?.meal_count ?? 0) > 0;
              if (!yesterdayHadMeals) return null;
              return (
                <button
                  onClick={copyYesterday}
                  disabled={copyingDay}
                  className="glass-btn glass-btn-secondary text-sm px-5 py-2 font-semibold rounded-xl mt-3 inline-flex items-center justify-center gap-1.5"
                >
                  {copyingDay ? 'Copying...' : "Copy yesterday\u2019s meals"}
                </button>
              );
            })()}
          </div>
        ) : (
          <div className="space-y-2">
            {meals.map((m, i) => (
              <div key={m.id} ref={i === 0 ? firstMealRef : undefined}>
                <SwipeActions onDelete={() => deleteMeal(m.id)} onRelog={() => relogMeal(m.id)}>
                  <MealCard meal={m} />
                </SwipeActions>
              </div>
            ))}
          </div>
        )}

        {/* Add meal to past day */}
        {selectedDate < todayStr() && (
          <Link
            to={`/log?date=${selectedDate}`}
            className="w-full glass-card-hover py-3 text-sm text-center font-semibold flex items-center justify-center gap-2 mt-2"
            style={{ color: '#3b82f6' }}
          >
            + Add meal to {selectedDate === todayStr() ? 'today' : new Date(selectedDate + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
          </Link>
        )}
      </div>

      {/* Contextual tooltips - each shows once */}
      <Tooltip id="calorie_ring" show={!loading && meals.length > 0 && isEarlyUser()} targetRef={heroCardRef}>
        Tap any ring or card to switch between eaten and remaining.
      </Tooltip>
      <Tooltip id="swipe_hint" show={meals.length >= 2 && isEarlyUser()} targetRef={firstMealRef} position="top">
        Swipe left to delete, right to re-log.
      </Tooltip>
    </div>
  );
}
