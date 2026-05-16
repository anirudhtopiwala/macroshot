import { useEffect, useState, useCallback, useRef } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { dashboardApi, type ActivityTrendDay } from '../api/dashboard';
import { workoutsApi } from '../api/workouts';
import CaloriesChart from '../components/CaloriesChart';
import ActivityChart from '../components/ActivityChart';
import { api } from '../api/client';
import { weightApi, type WeightEntry } from '../api/weight';
import { formatTimeAgo } from '../utils/date';
import WeightChart from '../components/WeightChart';
import { Flame, Scale } from '../components/icons';
import EmptyState from '../components/EmptyState';

import { OfflineAwareSkeleton } from '../components/Skeleton';
import { useRegisterRefresh } from '../context/PullToRefreshContext';
import { getCached, setCache } from '../utils/apiCache';
import { track } from '../api/analytics';
import { useSubscription } from '../context/SubscriptionContext';
import UpgradeCard from '../components/UpgradeCard';
import ProBlur from '../components/ProBlur';
import type { TrendDay, MacroTotals, UserStats } from '../types';

const KG_TO_LBS = 2.20462;

/** Animate a number counting up from 0 */
function useAnimatedCount(target: number, duration = 800): number {
  const [display, setDisplay] = useState(0);
  const rafRef = useRef<number>(0);
  useEffect(() => {
    if (target === 0) { setDisplay(0); return; }
    const start = performance.now();
    const animate = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      setDisplay(Math.round((1 - Math.pow(1 - progress, 3)) * target));
      if (progress < 1) rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);
  return display;
}

function TrendSummaryCard({ avgCal, weekDelta, activeDays, totalDays, adherence }: {
  avgCal: number;
  weekDelta: number | null;
  activeDays: number;
  totalDays: number;
  adherence: { calories: number; protein: number; total: number } | null;
}) {
  const animCal = useAnimatedCount(Math.round(avgCal));
  const animLogged = useAnimatedCount(activeDays, 600);
  const animCalTarget = useAnimatedCount(adherence?.calories ?? 0, 600);
  const animProtTarget = useAnimatedCount(adherence?.protein ?? 0, 600);

  return (
    <div className="glass-card overflow-hidden">
      <div style={{ height: 2, background: 'linear-gradient(90deg, #10b981, #3b82f6, #2dd4bf)' }} />
      <div className="p-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-2xl font-black tabular-nums" style={{ color: 'var(--text-primary)' }}>
              {animCal.toLocaleString()}
            </span>
            <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>kcal/day</span>
          </div>
          {weekDelta !== null && (
            <span className={`text-xs font-bold tabular-nums ${weekDelta < 0 ? 'text-blue-400' : weekDelta > 0 ? 'text-amber-400' : ''}`}
              style={weekDelta === 0 ? { color: 'var(--text-muted)' } : undefined}
            >
              {weekDelta > 0 ? '↑' : weekDelta < 0 ? '↓' : ''}
              {Math.abs(weekDelta)} vs last week
            </span>
          )}
        </div>
        <div className="flex gap-3 mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          <span>{animLogged}/{totalDays} days logged</span>
          {adherence && <span>· {animCalTarget}/{adherence.total} on calorie target</span>}
          {adherence && <span>· {animProtTarget}/{adherence.total} on protein</span>}
        </div>
      </div>
    </div>
  );
}

const PERIODS = [
  { label: '7D', days: 7 },
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
] as const;

function DonutChart({ data, size = 110 }: { data: { value: number; color: string; label: string }[]; size?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0) || 1;
  const r = (size - 10) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--track-bg)" strokeWidth={10} />
      {data.map((d) => {
        const pct = d.value / total;
        const dash = circumference * pct;
        const gap = circumference - dash;
        const currentOffset = offset;
        offset += dash;
        return (
          <circle
            key={d.label}
            cx={cx} cy={cy} r={r}
            fill="none"
            stroke={d.color}
            strokeWidth={10}
            strokeDasharray={`${dash} ${gap}`}
            strokeDashoffset={-currentOffset}
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 6px ${d.color}50)` }}
          />
        );
      })}
    </svg>
  );
}

export default function Trends() {
  const { isPremium } = useSubscription();
  const [searchParams, setSearchParams] = useSearchParams();
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;

  // Restore view state from URL params on back-navigation
  const initPeriod = Number(searchParams.get('period')) || 7;
  const initChart = (searchParams.get('chart') || 'calories') as 'calories' | 'weight' | 'activity';
  const initMacro = (searchParams.get('macro') || 'calories') as 'calories' | 'protein' | 'carbs' | 'fat';

  const [period, setPeriod] = useState(initPeriod);
  const [showGate, setShowGate] = useState(false);

  // Read cache once to avoid TOCTOU race at the 5-minute expiry boundary
  const initCache = getCached<{ days: TrendDay[]; target: MacroTotals | null }>('trends_7');
  const [days, setDays] = useState<TrendDay[]>(initCache?.days || []);
  const [target, setTarget] = useState<MacroTotals | null>(initCache?.target || null);
  const [stats, setStats] = useState<UserStats | null>(() => getCached<UserStats>('trends_stats') || null);
  const [loading, setLoading] = useState(!initCache);
  const [fetchError, setFetchError] = useState('');
  const [weightEntries, setWeightEntries] = useState<WeightEntry[]>([]);
  const [weightUnit, setWeightUnit] = useState<'kg' | 'lbs'>(() =>
    (localStorage.getItem('weight_unit') as 'kg' | 'lbs') || 'kg'
  );
  const [chartTab, setChartTab] = useState<'calories' | 'weight' | 'activity'>(initChart);
  const [macroTab, setMacroTab] = useState<'calories' | 'protein' | 'carbs' | 'fat'>(initMacro);
  const [heightCm, setHeightCm] = useState<number | null>(null);
  const [weightGoal, setWeightGoal] = useState<number | null>(null);
  const [activityDays, setActivityDays] = useState<ActivityTrendDay[]>([]);
  const [lastSynced, setLastSynced] = useState<string | null>(null);

  // Sync view state to URL so back-navigation restores it
  const syncUrl = useCallback((p: number, c: string, m: string) => {
    const params: Record<string, string> = {};
    if (p !== 7) params.period = String(p);
    if (c !== 'calories') params.chart = c;
    if (m !== 'calories') params.macro = m;
    setSearchParamsRef.current(params, { replace: true });
  }, []);

  // Sync weight unit when changed elsewhere
  useEffect(() => {
    const handler = (e: Event) => setWeightUnit((e as CustomEvent).detail);
    window.addEventListener('weight-unit-changed', handler);
    return () => window.removeEventListener('weight-unit-changed', handler);
  }, []);

  // Fetch profile once for BMI + weight goal
  useEffect(() => {
    api.get<{ height_cm: number | null; weight_goal_kg: number | null }>('/settings/profile')
      .then((p) => { setHeightCm(p.height_cm); setWeightGoal(p.weight_goal_kg); })
      .catch(() => {});
  }, []);

  const refreshTrends = useCallback(async (refresh = false) => {
    const opts = refresh ? { refresh: true } : undefined;
    const [trendRes, sRes, wRes, actRes, syncRes] = await Promise.allSettled([
      dashboardApi.trend(period, undefined, opts),
      dashboardApi.stats(opts),
      weightApi.history(period),
      dashboardApi.activityTrend(period),
      workoutsApi.syncStatus(),
    ]);
    const allFailed = [trendRes, sRes, wRes, actRes].every(r => r.status === 'rejected');
    if (allFailed) {
      throw (trendRes as PromiseRejectedResult).reason;
    }
    if (trendRes.status === 'fulfilled') {
      setDays(trendRes.value.days);
      setTarget(trendRes.value.target);
      setCache(`trends_${period}`, trendRes.value);
    }
    if (sRes.status === 'fulfilled') {
      setStats(sRes.value);
      setCache('trends_stats', sRes.value);
    }
    if (wRes.status === 'fulfilled') {
      setWeightEntries(wRes.value.entries);
    }
    if (actRes.status === 'fulfilled') {
      setActivityDays(actRes.value.days);
      setCache(`trends_activity_${period}`, actRes.value);
    }
    if (syncRes.status === 'fulfilled') {
      setLastSynced(syncRes.value.last_synced);
    }
    if (trendRes.status === 'rejected') {
      setFetchError('Could not load trends. Pull down to refresh.');
    }
  }, [period]);

  useRegisterRefresh(useCallback(() => refreshTrends(true), [refreshTrends]));

  // Refresh weight data when logged/deleted from WeightTracker
  useEffect(() => {
    const handler = () => { refreshTrends(true); };
    window.addEventListener('weight-changed', handler);
    return () => window.removeEventListener('weight-changed', handler);
  }, [refreshTrends]);

  // When period changes: show cached data for new period if available, else clear and show loading
  useEffect(() => {
    const cached = getCached<{ days: TrendDay[]; target: MacroTotals | null }>(`trends_${period}`);
    if (cached) {
      setDays(cached.days);
      setTarget(cached.target);
    } else {
      setDays([]);
      setLoading(true);
    }
    setFetchError('');
    refreshTrends().catch((err) => {
      console.error(err);
      setFetchError('Could not load trends. Pull down to refresh.');
    }).finally(() => {
      setLoading(false);
      // Pre-warm caches for other periods so switching is instant
      const otherPeriods = [7, 30, 90].filter((p) => p !== period);
      for (const p of otherPeriods) {
        if (!getCached(`trends_${p}`)) {
          dashboardApi.trend(p).then((t) => setCache(`trends_${p}`, t)).catch(() => {});
        }
      }
    });
  }, [refreshTrends]); // eslint-disable-line react-hooks/exhaustive-deps

  // Restore activity data from cache on period change (fresh fetch happens in refreshTrends)
  useEffect(() => {
    const cached = getCached<{ days: ActivityTrendDay[] }>(`trends_activity_${period}`);
    if (cached) setActivityDays(cached.days);
  }, [period]);

  if (loading && days.length === 0) {
    const shimmer = 'rounded-xl bg-[length:200%_100%] animate-shimmer dark:bg-gradient-to-r dark:from-white/[0.04] dark:via-white/[0.08] dark:to-white/[0.04] bg-gradient-to-r from-black/[0.03] via-black/[0.06] to-black/[0.03]';
    return (
      <OfflineAwareSkeleton skeleton={
        <div className="space-y-4">
          <div className={`${shimmer} h-8 w-32`} />
          <div className={`${shimmer} h-48 !rounded-2xl`} />
          <div className={`${shimmer} h-40 !rounded-2xl`} />
          <div className={`${shimmer} h-32 !rounded-2xl`} />
          <div className={`${shimmer} h-32 !rounded-2xl`} />
        </div>
      } />
    );
  }

  if (fetchError && days.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>Trends</h1>
        <div className="glass-card p-8 text-center">
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{fetchError}</p>
          <button
            onClick={() => { setFetchError(''); setLoading(true); refreshTrends(true).catch((err) => { console.error(err); setFetchError('Could not load trends. Pull down to refresh.'); }).finally(() => setLoading(false)); }}
            className="mt-3 text-xs font-semibold text-emerald-400"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const activeDays = days.filter((d) => d.meal_count > 0);

  // Averages + Median
  const avgCal = activeDays.length ? activeDays.reduce((s, d) => s + d.calories, 0) / activeDays.length : 0;
  const avgP = activeDays.length ? activeDays.reduce((s, d) => s + d.protein, 0) / activeDays.length : 0;
  const avgC = activeDays.length ? activeDays.reduce((s, d) => s + d.carbs, 0) / activeDays.length : 0;
  const avgF = activeDays.length ? activeDays.reduce((s, d) => s + d.fat, 0) / activeDays.length : 0;
  const macroTotal = avgP + avgC + avgF || 1;
  const pctP = Math.round((avgP / macroTotal) * 100);
  const pctC = Math.round((avgC / macroTotal) * 100);
  const pctF = 100 - pctP - pctC;

  // Week-over-week delta (when we have >= 14 days of data)
  const recentWeek = days.slice(-7);
  const prevWeek = days.length >= 14 ? days.slice(-14, -7) : [];
  const recentActive = recentWeek.filter(d => d.meal_count > 0);
  const prevActive = prevWeek.filter(d => d.meal_count > 0);
  const recentAvg = recentActive.length ? recentActive.reduce((s, d) => s + d.calories, 0) / recentActive.length : 0;
  const prevAvg = prevActive.length ? prevActive.reduce((s, d) => s + d.calories, 0) / prevActive.length : 0;
  const weekDelta = prevActive.length > 0 ? Math.round(recentAvg - prevAvg) : null;

  // Day-of-week averages
  const dowAvgs = (() => {
    const sums = Array.from({ length: 7 }, () => ({ total: 0, count: 0 }));
    for (const d of activeDays) {
      const dow = new Date(d.date + 'T00:00:00').getDay();
      sums[dow].total += d.calories;
      sums[dow].count += 1;
    }
    const labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    return labels.map((label, i) => ({
      label,
      avg: sums[i].count > 0 ? Math.round(sums[i].total / sums[i].count) : 0,
    }));
  })();
  const dowMax = Math.max(...dowAvgs.map(d => d.avg), 1);

  // Consistency
  const consistencyPct = days.length > 0 ? Math.round((activeDays.length / days.length) * 100) : 0;

  // Best/worst
  const bestDay = activeDays.length ? activeDays.reduce((a, b) => a.protein > b.protein ? a : b) : null;
  // worstDay removed - consolidated into highlights

  // Goal adherence
  const adherence = target && activeDays.length > 0 ? {
    calories: activeDays.filter((d) => d.calories >= target.calories * 0.9 && d.calories <= target.calories * 1.1).length,
    protein: activeDays.filter((d) => d.protein >= target.protein * 0.9).length,
    total: activeDays.length,
  } : null;

  // Macro-specific chart data
  const macroKey = macroTab;
  const macroTarget = target ? target[macroKey] : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>Trends</h1>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
          {PERIODS.map((p) => {
            const isGated = !isPremium && p.days > 7;
            return (
              <button
                key={p.days}
                onClick={() => {
                  // Always change the period; gate overlay shows on top of blurred chart
                  setPeriod(p.days);
                  syncUrl(p.days, chartTab, macroTab);
                  setShowGate(isGated);
                  track('ui_trend_range_selected', { days: p.days, gated: isGated });
                }}
                className={`px-3 py-1 rounded-md text-xs font-bold transition-all ${
                  period === p.days
                    ? 'bg-emerald-500/20 text-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.15)]'
                    : ''
                }`}
                style={period === p.days ? undefined : { color: 'var(--text-muted)' }}
              >
                {p.label}
                {isGated && (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="inline-block ml-0.5 -mt-0.5">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Weekly Summary Card - only show if enough data for a meaningful average */}
      {activeDays.length >= 3 && (
        <ProBlur message="Pro"><TrendSummaryCard
          avgCal={avgCal}
          weekDelta={period === 7 ? weekDelta : null}
          activeDays={activeDays.length}
          totalDays={days.length}
          adherence={adherence}
        /></ProBlur>
      )}

      {/* Chart Card - swipable between Calories and Weight */}
      <div className="glass-card p-5 overflow-hidden" data-swipe-handler style={{ position: 'relative' }}
        onTouchStart={(e) => { (e.currentTarget as HTMLElement).dataset.sx = String(e.touches[0].clientX); (e.currentTarget as HTMLElement).dataset.sy = String(e.touches[0].clientY); }}
        onTouchEnd={(e) => {
          const sx = parseFloat((e.currentTarget as HTMLElement).dataset.sx || '0');
          const sy = parseFloat((e.currentTarget as HTMLElement).dataset.sy || '0');
          const dx = e.changedTouches[0].clientX - sx;
          const dy = e.changedTouches[0].clientY - sy;
          if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {
            const tabs: ('calories' | 'weight' | 'activity')[] = ['calories', 'activity', 'weight'];
            const idx = tabs.indexOf(chartTab);
            if (dx < 0 && idx < tabs.length - 1) { setChartTab(tabs[idx + 1]); syncUrl(period, tabs[idx + 1], macroTab); }
            else if (dx > 0 && idx > 0) { setChartTab(tabs[idx - 1]); syncUrl(period, tabs[idx - 1], macroTab); }
          }
        }}
      >
        {/* Tab toggle */}
        <div className="flex items-center justify-between mb-4 gap-2">
          <div className="flex gap-0.5 p-0.5 rounded-lg flex-1 min-w-0" style={{ background: 'var(--bg-elevated)' }}>
            {(['calories', 'protein', 'carbs', 'fat'] as const).map((tab) => {
              const colors: Record<string, string> = { calories: '#10b981', protein: '#2dd4bf', carbs: '#f97316', fat: '#22c55e' };
              const labels: Record<string, string> = { calories: 'Cal', protein: 'P', carbs: 'C', fat: 'F' };
              const isActive = chartTab === 'calories' && macroTab === tab;
              const isMacroLocked = !isPremium && tab !== 'calories';
              return (
                <button
                  key={tab}
                  onClick={() => {
                    if (isMacroLocked) { setShowGate(true); return; }
                    setChartTab('calories'); setMacroTab(tab); syncUrl(period, 'calories', tab);
                  }}
                  className={`px-2 py-1 rounded-md text-xs font-bold transition-all relative ${isActive ? '' : ''}`}
                  style={isActive ? { background: `${colors[tab]}20`, color: colors[tab] } : { color: 'var(--text-muted)', opacity: isMacroLocked ? 0.6 : 1 }}
                >
                  {labels[tab]}
                </button>
              );
            })}
            <button
              onClick={() => { setChartTab('activity'); syncUrl(period, 'activity', macroTab); }}
              className="px-2 py-1 rounded-md text-xs font-bold transition-all"
              style={chartTab === 'activity' ? { background: 'rgba(59,130,246,0.2)', color: '#3b82f6' } : { color: 'var(--text-muted)' }}
            >
              Activity
            </button>
            <button
              onClick={() => { setChartTab('weight'); syncUrl(period, 'weight', macroTab); }}
              className={`px-2 py-1 rounded-md text-xs font-bold transition-all ${
                chartTab === 'weight'
                  ? 'bg-amber-500/20 text-amber-400'
                  : ''
              }`}
              style={chartTab !== 'weight' ? { color: 'var(--text-muted)' } : undefined}
            >
              Weight
            </button>
          </div>
          {chartTab === 'weight' ? (
            <button
              onClick={() => { const next = weightUnit === 'kg' ? 'lbs' : 'kg'; setWeightUnit(next); localStorage.setItem('weight_unit', next); window.dispatchEvent(new CustomEvent('weight-unit-changed', { detail: next })); }}
              className="text-xs tabular-nums font-semibold px-2 py-1 rounded-md active:scale-95 transition-all shrink-0"
              style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.1)' }}
            >
              {weightEntries.length > 0
                ? `${(weightUnit === 'lbs' ? weightEntries[0].weight_kg * KG_TO_LBS : weightEntries[0].weight_kg).toFixed(1)} ${weightUnit}`
                : weightUnit}
            </button>
          ) : null}
        </div>

        {/* Panels - fixed height so tab switches don't cause layout jumps */}
        {chartTab === 'calories' ? (
          <div style={{ minHeight: '240px' }}>
            <CaloriesChart
              days={days}
              activityDays={activityDays}
              target={macroTarget}
              period={period}
              valueKey={macroKey as 'calories' | 'protein' | 'carbs' | 'fat'}
            />
          </div>
        ) : chartTab === 'weight' ? (
          <div style={{ minHeight: '240px' }}>
              {weightEntries.length === 0 ? (
                <EmptyState
                  icon={<Scale className="w-8 h-8" />}
                  title="No weight entries yet"
                  subtitle="Track your progress over time - log from the + button"
                  card={false}
                />
              ) : (
                <>
                  {weightEntries.length > 1 && (() => {
                    const weights = weightEntries.map(e => weightUnit === 'lbs' ? e.weight_kg * KG_TO_LBS : e.weight_kg);
                    const delta = weights[0] - weights[weights.length - 1];
                    // Weekly rate
                    const oldest = weightEntries[weightEntries.length - 1];
                    const newest = weightEntries[0];
                    const d1 = new Date(oldest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
                    const d2 = new Date(newest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
                    const daySpan = (d2.getTime() - d1.getTime()) / 86400000;
                    const weeklyRate = daySpan >= 7 ? (delta / daySpan) * 7 : null;
                    return (
                      <div className="flex items-center gap-3 mb-3 flex-wrap">
                        <span className={`text-sm font-bold tabular-nums ${delta < 0 ? 'text-emerald-400' : delta > 0 ? 'text-red-400' : ''}`} style={delta === 0 ? { color: 'var(--text-secondary)' } : undefined}>
                          {delta > 0 ? '+' : ''}{delta.toFixed(1)} {weightUnit}
                        </span>
                        <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>over {weightEntries.length} entries</span>
                        {weeklyRate !== null && (
                          <span className={`text-xs font-semibold tabular-nums ${weeklyRate < -0.05 ? 'text-emerald-400' : weeklyRate > 0.05 ? 'text-red-400' : ''}`} style={Math.abs(weeklyRate) <= 0.05 ? { color: 'var(--text-muted)' } : undefined}>
                            ({weeklyRate > 0 ? '+' : ''}{weeklyRate.toFixed(1)} {weightUnit}/wk)
                          </span>
                        )}
                      </div>
                    );
                  })()}
                  <WeightChart entries={weightEntries} unit={weightUnit} gradientId="wg-trends" />
                  {weightGoal && weightEntries.length > 0 && (() => {
                    const current = weightEntries[0].weight_kg;
                    const start = weightEntries[weightEntries.length - 1].weight_kg;
                    const totalDelta = weightGoal - start;
                    const currentDelta = current - start;
                    // Use signed math: only show progress if moving in correct direction
                    const progress = totalDelta !== 0 ? Math.max(0, Math.min(1, currentDelta / totalDelta)) : 0;
                    const displayCurrent = weightUnit === 'lbs' ? current * KG_TO_LBS : current;
                    const displayGoal = weightUnit === 'lbs' ? weightGoal * KG_TO_LBS : weightGoal;
                    return (
                      <div className="mt-3 p-3 rounded-xl" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] font-semibold" style={{ color: 'var(--text-muted)' }}>Goal progress</span>
                          <span className="text-xs font-bold tabular-nums" style={{ color: '#f59e0b' }}>
                            {displayCurrent.toFixed(1)} → {displayGoal.toFixed(1)} {weightUnit}
                          </span>
                        </div>
                        <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                          <div
                            className="h-full rounded-full transition-all duration-700"
                            style={{ width: `${progress * 100}%`, background: 'linear-gradient(90deg, #f59e0b, #f97316)', boxShadow: '0 0 8px rgba(245,158,11,0.4)' }}
                          />
                        </div>
                        <p className="text-[11px] mt-1 tabular-nums" style={{ color: 'var(--text-muted)' }}>
                          {Math.round(progress * 100)}% there
                        </p>
                      </div>
                    );
                  })()}
                  <button
                    onClick={() => window.dispatchEvent(new CustomEvent('open-weight-tracker'))}
                    className="block w-full mt-3 py-2 rounded-lg text-xs font-semibold text-center transition-all active:scale-[0.98]"
                    style={{ background: 'rgba(245,158,11,0.1)', color: '#f59e0b' }}
                  >
                    View Weight History
                  </button>
                </>
              )}
          </div>
        ) : chartTab === 'activity' ? (
          <div style={{ minHeight: '240px' }}>
            <ActivityChart days={activityDays} period={period} />
            {lastSynced && (
              <p className="text-[11px] mt-2 text-center" style={{ color: 'var(--text-muted)' }}>
                Last synced {formatTimeAgo(lastSynced)}
              </p>
            )}
            {/* Summary stats - daily averages */}
            {activityDays.length > 0 && (() => {
              const n = activityDays.length;
              const totalBurned = activityDays.reduce((s, d) => s + d.burned_calories, 0);
              const totalSteps = activityDays.reduce((s, d) => s + d.steps, 0);
              const totalActiveMin = activityDays.reduce((s, d) => s + d.active_minutes, 0);
              const activeDayCount = activityDays.filter(d => d.burned_calories > 0 || d.workout_count > 0).length;
              const avgBurned = Math.round(totalBurned / n);
              const avgSteps = Math.round(totalSteps / n);
              return (
                <div className="grid grid-cols-3 gap-3 mt-3">
                  {avgBurned > 0 && (
                    <div className="text-center">
                      <p className="text-lg font-black tabular-nums" style={{ color: '#f97316' }}>{avgBurned.toLocaleString()}</p>
                      <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>active cal/day</p>
                      <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>above resting</p>
                    </div>
                  )}
                  {avgSteps > 0 && (
                    <div className="text-center">
                      <p className="text-lg font-black tabular-nums" style={{ color: '#00B0B9' }}>{avgSteps.toLocaleString()}</p>
                      <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>avg steps/day</p>
                    </div>
                  )}
                  <div className="text-center">
                    <p className="text-lg font-black tabular-nums" style={{ color: 'var(--text-primary)' }}>{activeDayCount}/{n}</p>
                    <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>active days</p>
                  </div>
                  {totalActiveMin > 0 && (
                    <div className="text-center">
                      <p className="text-lg font-black tabular-nums" style={{ color: 'var(--color-fat)' }}>{Math.round(totalActiveMin / n)}</p>
                      <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>avg active min</p>
                    </div>
                  )}
                </div>
              );
            })()}
            <Link
              to="/workouts"
              className="block w-full mt-3 py-2 rounded-lg text-xs font-semibold text-center transition-all active:scale-[0.98]"
              style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}
            >
              View Workout History
            </Link>
          </div>
        ) : null}

        {/* Dot indicators */}
        <div className="flex justify-center gap-1.5 mt-3">
          <div className="w-1.5 h-1.5 rounded-full transition-all duration-300" style={{ background: chartTab === 'calories' ? '#10b981' : 'var(--track-bg)' }} />
          <div className="w-1.5 h-1.5 rounded-full transition-all duration-300" style={{ background: chartTab === 'activity' ? '#3b82f6' : 'var(--track-bg)' }} />
          <div className="w-1.5 h-1.5 rounded-full transition-all duration-300" style={{ background: chartTab === 'weight' ? '#f59e0b' : 'var(--track-bg)' }} />
        </div>

        {/* Frosted gate overlay for 30D/90D */}
        {(showGate || (!isPremium && period > 7)) && (() => {
          const dismissGate = () => {
            setShowGate(false);
            if (!isPremium && period > 7) {
              // Snap back to 7D so the chart becomes usable
              setPeriod(7);
              syncUrl(7, chartTab, macroTab);
            }
          };
          return (
            <div
              onClick={dismissGate}
              style={{
                position: 'absolute',
                inset: 0,
                backdropFilter: 'blur(8px)',
                WebkitBackdropFilter: 'blur(8px)',
                background: 'var(--bg-frosted-overlay, rgba(0,0,0,0.3))',
                borderRadius: 'inherit',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '1rem',
                zIndex: 20,
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{ maxWidth: '280px', width: '100%', position: 'relative' }}
              >
                {/* Close button */}
                <button
                  onClick={dismissGate}
                  className="absolute -top-2 -right-2 w-7 h-7 rounded-full flex items-center justify-center z-10 active:scale-95 transition-transform"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)', boxShadow: '0 2px 8px rgba(0,0,0,0.3)' }}
                  aria-label="Dismiss"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-primary)' }}>
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
                <UpgradeCard
                  feature="Extended trends"
                  message="Macro breakdowns and 30/90-day trends are Pro features. Upgrade to unlock your full progress."
                />
              </div>
            </div>
          );
        })()}
      </div>

      {/* Consistency + Stats Row */}
      <ProBlur message="Pro">
      <div className="grid grid-cols-3 gap-3">
        {/* Consistency Ring */}
        <div
          className="glass-card p-4 flex flex-col items-center justify-center"
        >
          <div className="relative w-14 h-14">
            <svg width={56} height={56} className="-rotate-90">
              <circle cx={28} cy={28} r={23} fill="none" stroke="var(--track-bg)" strokeWidth={5} />
              <circle
                cx={28} cy={28} r={23} fill="none"
                stroke="#10b981"
                strokeWidth={5}
                strokeLinecap="round"
                strokeDasharray={`${(consistencyPct / 100) * 2 * Math.PI * 23} ${2 * Math.PI * 23}`}
                style={{ filter: 'drop-shadow(0 0 6px rgba(16,185,129,0.4))' }}
              />
            </svg>
            <span className="absolute inset-0 flex items-center justify-center text-sm font-black text-emerald-400">
              {consistencyPct}%
            </span>
          </div>
          <span className="text-[11px] font-semibold mt-1.5 uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Logged</span>
        </div>

        {/* Streak */}
        {stats && (
          <div
            className="glass-card p-4 flex flex-col items-center justify-center"
            style={stats.streak_days > 0 ? { background: 'rgba(249,115,22,0.04)', border: '1px solid rgba(249,115,22,0.12)' } : undefined}
          >
            <Flame
              className="w-6 h-6 text-streak mb-1 animate-flame-flicker"
              style={{ filter: 'drop-shadow(0 0 6px rgba(249,115,22,0.4))' }}
            />
            <span className="text-2xl font-black text-streak" style={{ textShadow: '0 0 10px rgba(249,115,22,0.3)' }}>{stats.streak_days}</span>
            <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Streak</span>
          </div>
        )}

        {/* Adherence */}
        {adherence && adherence.total > 0 ? (
          <div className="glass-card p-4 flex flex-col items-center justify-center">
            <span className="text-2xl font-black text-blue-400">{Math.round((adherence.calories / adherence.total) * 100)}%</span>
            <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>On Target</span>
          </div>
        ) : stats ? (
          <div className="glass-card p-4 flex flex-col items-center justify-center">
            <span className="text-2xl font-black text-emerald-400">{stats.total_meals}</span>
            <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Meals</span>
          </div>
        ) : null}
      </div>
      </ProBlur>

      {/* Day-of-Week Patterns */}
      {activeDays.length >= 3 && (
        <ProBlur message="Pro"><div className="glass-card p-5">
          <h2 className="section-heading mb-3">Daily Patterns</h2>
          <div className="space-y-2">
            {dowAvgs.filter(d => d.avg > 0).map((d) => {
              const isMax = d.avg === dowMax;
              return (
                <div key={d.label} className="flex items-center gap-3">
                  <span className="text-[11px] font-bold w-7 shrink-0" style={{ color: 'var(--text-muted)' }}>{d.label}</span>
                  <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${(d.avg / dowMax) * 100}%`,
                        background: isMax ? 'linear-gradient(90deg, #f97316, #f59e0b)' : 'linear-gradient(90deg, #10b981, #10b981aa)',
                        boxShadow: isMax ? '0 0 8px rgba(249,115,22,0.3)' : undefined,
                      }}
                    />
                  </div>
                  <span className="text-[11px] font-bold tabular-nums w-10 text-right" style={{ color: isMax ? '#f97316' : 'var(--text-secondary)' }}>
                    {d.avg}
                  </span>
                </div>
              );
            })}
          </div>
          {(() => {
            const sorted = dowAvgs.filter(d => d.avg > 0).sort((a, b) => b.avg - a.avg);
            if (sorted.length >= 2) {
              const diff = sorted[0].avg - sorted[sorted.length - 1].avg;
              return diff > 100 ? (
                <p className="text-[11px] mt-2" style={{ color: 'var(--text-muted)' }}>
                  You eat ~{diff} kcal more on {sorted[0].label}s than {sorted[sorted.length - 1].label}s
                </p>
              ) : null;
            }
            return null;
          })()}
        </div></ProBlur>
      )}

      {/* Macro Split */}
      {activeDays.length > 0 && (
        <ProBlur message="Pro"><div className="glass-card p-5">
          <h2 className="section-heading mb-4">Macro Split</h2>
          <div className="flex items-center gap-5">
            <div className="relative shrink-0">
              <DonutChart
                data={[
                  { value: avgP, color: '#2dd4bf', label: 'Protein' },
                  { value: avgC, color: '#f97316', label: 'Carbs' },
                  { value: avgF, color: '#22c55e', label: 'Fat' },
                ]}
                size={110}
              />
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-lg font-black" style={{ color: 'var(--text-primary)' }}>{Math.round(avgP + avgC + avgF)}</span>
                <span className="text-[11px] font-semibold" style={{ color: 'var(--text-muted)' }}>g/day</span>
              </div>
            </div>
            <div className="flex-1 space-y-3">
              {[
                { label: 'Protein', color: '#2dd4bf', pct: pctP, avg: avgP },
                { label: 'Carbs', color: '#f97316', pct: pctC, avg: avgC },
                { label: 'Fat', color: '#22c55e', pct: pctF, avg: avgF },
              ].map((m) => (
                <div key={m.label}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full" style={{ backgroundColor: m.color, boxShadow: `0 0 6px ${m.color}50` }} />
                      <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{m.label}</span>
                    </div>
                    <div className="flex items-baseline gap-1">
                      <span className="text-xs font-black tabular-nums" style={{ color: m.color }}>{Math.round(m.avg)}g</span>
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{m.pct}%</span>
                    </div>
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${m.pct}%`, backgroundColor: m.color, boxShadow: `0 0 8px ${m.color}40` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div></ProBlur>
      )}

      {/* Highlights - best day + most logged + BMI */}
      {(bestDay || (stats && stats.most_logged_meal)) && (
        <ProBlur message="Pro"><div className="glass-card p-5 space-y-3">
          <h2 className="section-heading">Highlights</h2>
          <div className="grid grid-cols-2 gap-3">
            {bestDay && (
              <div className="p-3 rounded-xl" style={{ background: 'rgba(45,212,191,0.06)', border: '1px solid rgba(45,212,191,0.12)' }}>
                <p className="text-[11px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Best Protein</p>
                <p className="text-lg font-black" style={{ color: '#2dd4bf' }}>{Math.round(bestDay.protein)}g</p>
                <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                  {new Date(bestDay.date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </p>
              </div>
            )}
            {stats && stats.most_logged_meal && (
              <div className="p-3 rounded-xl" style={{ background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.12)' }}>
                <p className="text-[11px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Most Logged</p>
                <p className="text-sm font-bold truncate text-purple-400">{stats.most_logged_meal[0]}</p>
                <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{stats.most_logged_meal[1]}x</p>
              </div>
            )}
          </div>
        </div></ProBlur>
      )}

      {/* Weekly Macro Bars */}
      {period <= 7 && (
        <ProBlur message="Pro"><div className="glass-card p-5">
          <h2 className="section-heading mb-4">Daily Macro Breakdown</h2>
          <div className="flex items-end gap-1.5" style={{ height: '112px' }}>
            {days.map((d) => {
              const total = d.protein + d.carbs + d.fat || 1;
              const hasData = total > 1;
              const barH = hasData ? 112 : 4;
              const dayLabel = new Date(d.date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' });
              return (
                <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                  <div className="w-full flex flex-col rounded-lg overflow-hidden" style={{ height: `${barH}px` }}>
                    <div className="transition-all duration-500" style={{ flex: d.protein / total, backgroundColor: '#2dd4bf' }} />
                    <div className="transition-all duration-500" style={{ flex: d.carbs / total, backgroundColor: '#f97316' }} />
                    <div className="transition-all duration-500" style={{ flex: d.fat / total, backgroundColor: '#22c55e' }} />
                  </div>
                  <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>{dayLabel}</span>
                </div>
              );
            })}
          </div>
          <div className="flex gap-4 mt-3 text-[11px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            {[
              { label: 'Protein', color: '#2dd4bf' },
              { label: 'Carbs', color: '#f97316' },
              { label: 'Fat', color: '#22c55e' },
            ].map((m) => (
              <span key={m.label} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: m.color, boxShadow: `0 0 4px ${m.color}40` }} />
                {m.label}
              </span>
            ))}
          </div>
        </div></ProBlur>
      )}

      {/* BMI Indicator */}
      <ProBlur message="Pro">
      {heightCm && heightCm > 0 && weightEntries.length > 0 && (() => {
        const weightKg = weightEntries[0].weight_kg;
        const heightM = heightCm / 100;
        const bmi = weightKg / (heightM * heightM);
        const bmiRounded = bmi.toFixed(1);

        // BMI categories and colors
        const categories = [
          { label: 'Underweight', max: 18.5, color: '#3b82f6' },
          { label: 'Normal', max: 25, color: '#10b981' },
          { label: 'Overweight', max: 30, color: '#f59e0b' },
          { label: 'Obese', max: 40, color: '#ef4444' },
        ];

        const category = categories.find((c) => bmi < c.max) || categories[categories.length - 1];

        // Position on bar (BMI 15–40 range mapped to 0–100%)
        const barMin = 15, barMax = 40;
        const pct = Math.max(0, Math.min(100, ((bmi - barMin) / (barMax - barMin)) * 100));

        // Category boundary positions
        const boundaries = categories.map((c) => ({
          ...c,
          pct: ((c.max - barMin) / (barMax - barMin)) * 100,
        }));

        return (
          <div className="glass-card p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Scale className="w-4 h-4" style={{ color: category.color }} />
                <h2 className="section-heading">BMI</h2>
              </div>
              <div className="flex items-baseline gap-1.5">
                <span className="text-lg font-black tabular-nums" style={{ color: category.color }}>{bmiRounded}</span>
                <span className="text-[11px] font-semibold" style={{ color: category.color }}>{category.label}</span>
              </div>
            </div>

            {/* BMI bar */}
            <div className="relative h-3 rounded-full overflow-hidden flex">
              {boundaries.map((b, i) => {
                const prevPct = i === 0 ? 0 : boundaries[i - 1].pct;
                const width = Math.min(b.pct, 100) - prevPct;
                return (
                  <div
                    key={b.label}
                    style={{
                      width: `${width}%`,
                      background: `${b.color}30`,
                      borderRight: i < boundaries.length - 1 ? '1px solid rgba(0,0,0,0.3)' : 'none',
                    }}
                  />
                );
              })}
              {/* Indicator dot */}
              <div
                className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full"
                style={{
                  left: `${pct}%`,
                  transform: `translate(-50%, -50%)`,
                  background: category.color,
                  boxShadow: `0 0 8px ${category.color}80`,
                  borderWidth: '2px',
                  borderStyle: 'solid',
                  borderColor: 'var(--bg-card)',
                }}
              />
            </div>

            {/* Labels */}
            <div className="flex justify-between mt-2 text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
              <span>15</span>
              <span>18.5</span>
              <span>25</span>
              <span>30</span>
              <span>40</span>
            </div>
          </div>
        );
      })()}
      </ProBlur>
    </div>
  );
}
