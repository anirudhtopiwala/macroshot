import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { workoutsApi, type Workout, type WorkoutHistoryDay } from '../api/workouts';
import { dashboardApi, type ActivityTrendDay } from '../api/dashboard';
import BackButton from '../components/BackButton';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ActivityChart from '../components/ActivityChart';
import SwipeActions from '../components/SwipeActions';
import { useToast } from '../components/Toast';
import { getCached, setCache } from '../utils/apiCache';
import { formatTimeAgo } from '../utils/date';
import ProBlur from '../components/ProBlur';

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = (today.getTime() - d.getTime()) / 86400000;
  if (diff < 1) return 'Today';
  if (diff < 2) return 'Yesterday';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function sourceIcon(source: string) {
  if (source === 'strava') return { emoji: '\u26A1', bg: 'rgba(252,76,2,0.15)' };
  if (source === 'fitbit') return { emoji: '\uD83C\uDFC3', bg: 'rgba(0,176,185,0.15)' };
  return { emoji: '\uD83D\uDCAA', bg: 'rgba(59,130,246,0.15)' };
}

function StatPill({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: color || 'var(--text-secondary)' }}>
      {value} {label}
    </span>
  );
}

function WorkoutRow({ w }: { w: Workout }) {
  const icon = sourceIcon(w.source);
  return (
    <div
      className="flex items-center justify-between py-1.5 px-1"
      style={{ background: 'var(--bg-card)' }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] shrink-0" style={{ background: icon.bg }}>
          {icon.emoji}
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
          <div className="flex gap-1.5 mt-0.5 flex-wrap">
            {w.duration_sec > 0 && <StatPill value={String(Math.round(w.duration_sec / 60))} label="min" />}
            {w.distance_m > 0 && <StatPill value={(w.distance_m / 1000).toFixed(1)} label="km" />}
            {w.avg_heart_rate > 0 && <StatPill value={`\u2665 ${Math.round(w.avg_heart_rate)}`} label="" color="#ef4444" />}
          </div>
        </div>
      </div>
      {w.calories_burned > 0 && (
        <div className="text-right shrink-0 ml-2">
          <span className="text-xs font-bold tabular-nums" style={{ color: 'var(--color-carbs)' }}>
            {Math.round(w.calories_burned)}
          </span>
          <span className="text-[11px] ml-0.5" style={{ color: 'var(--text-muted)' }}>cal</span>
        </div>
      )}
    </div>
  );
}

function DayCard({ day, onDelete }: { day: WorkoutHistoryDay; onDelete: (id: number) => Promise<void> }) {
  const fb = day.fitbit_summary;
  const hasWorkouts = day.workouts.length > 0;
  const activeMin = fb ? fb.fairly_active_min + fb.very_active_min : 0;

  return (
    <div className="glass-card p-4">
      {/* Day header */}
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>{formatDate(day.date)}</p>
        <div className="flex items-center gap-3 text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {day.total_calories > 0 && (
            <span><span className="font-bold" style={{ color: 'var(--color-carbs)' }}>{Math.round(day.total_calories).toLocaleString()}</span> cal</span>
          )}
          {day.total_steps > 0 && (
            <span><span className="font-bold" style={{ color: 'var(--color-protein)' }}>{day.total_steps.toLocaleString()}</span> steps</span>
          )}
        </div>
      </div>

      {/* Fitbit daily summary row */}
      {fb && (fb.steps > 0 || fb.active_calories > 0 || fb.activity_calories > 0) && (
        <div className="flex items-center justify-between py-1.5" style={{ borderTop: '1px solid var(--border-glass)' }}>
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] shrink-0" style={{ background: 'rgba(0,176,185,0.15)' }}>
              {'\uD83D\uDCF1'}
            </span>
            <div>
              <p className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Daily Activity</p>
              <div className="flex gap-1.5 mt-0.5 flex-wrap">
                {fb.steps > 0 && <StatPill value={fb.steps.toLocaleString()} label="steps" />}
                {(fb.active_calories || fb.activity_calories) > 0 && (
                  <StatPill
                    value={String(Math.round(fb.active_calories || fb.activity_calories))}
                    label="active cal"
                    color="var(--color-carbs)"
                  />
                )}
                {activeMin > 0 && <StatPill value={String(activeMin)} label="active min" />}
                {fb.resting_heart_rate > 0 && <StatPill value={`\u2665 ${fb.resting_heart_rate}`} label="" color="#ef4444" />}
              </div>
            </div>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0" style={{ background: 'rgba(0,176,185,0.12)', color: '#00b0b9' }}>
            Fitbit
          </span>
        </div>
      )}

      {/* Individual workouts */}
      {hasWorkouts && day.workouts.map((w) => (
        <div key={w.id} className="pt-1.5 mt-1.5" style={{ borderTop: '1px solid var(--border-glass)' }}>
          {w.source === 'manual' ? (
            <SwipeActions onDelete={() => onDelete(w.id)}>
              <WorkoutRow w={w} />
            </SwipeActions>
          ) : (
            <WorkoutRow w={w} />
          )}
        </div>
      ))}
    </div>
  );
}

export default function WorkoutHistory() {
  const { toast } = useToast();
  const [days, setDays] = useState<WorkoutHistoryDay[]>([]);
  // Share the same cache keys as Trends page so data is always consistent
  const cached7 = getCached<{ days: ActivityTrendDay[] }>('trends_activity_7');
  const [activityDays, setActivityDays] = useState<ActivityTrendDay[]>(cached7?.days || []);
  const [chartPeriod, setChartPeriod] = useState<7 | 30>(7);
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      workoutsApi.history(30),
      dashboardApi.activityTrend(7).catch(() => ({ days: [] as ActivityTrendDay[] })),
      workoutsApi.syncStatus().catch(() => ({ last_synced: null })),
    ]).then(([histData, trendData, syncData]) => {
      setDays(histData.days);
      setActivityDays(trendData.days);
      setCache('trends_activity_7', trendData);
      setLastSynced(syncData.last_synced);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const totals = activityDays.reduce(
    (acc, d) => {
      const isActive = d.steps > 0 || d.burned_calories > 0 || d.workout_count > 0;
      return {
        active_days: acc.active_days + (isActive ? 1 : 0),
        total_steps: acc.total_steps + d.steps,
        total_calories: acc.total_calories + d.burned_calories,
        workout_count: acc.workout_count + d.workout_count,
      };
    },
    { active_days: 0, total_steps: 0, total_calories: 0, workout_count: 0 },
  );

  const switchChartPeriod = (p: 7 | 30) => {
    setChartPeriod(p);
    // Check shared Trends cache first
    const cached = getCached<{ days: ActivityTrendDay[] }>(`trends_activity_${p}`);
    if (cached?.days) {
      setActivityDays(cached.days);
    } else {
      dashboardApi.activityTrend(p).then((data) => {
        setActivityDays(data.days);
        setCache(`trends_activity_${p}`, data);
      }).catch(() => {});
    }
  };

  const handleDelete = async (id: number) => {
    const prev = days;
    setDays((prevDays) =>
      prevDays
        .map((d) => ({ ...d, workouts: d.workouts.filter((w) => w.id !== id) }))
        .filter((d) => d.workouts.length > 0 || d.fitbit_summary),
    );
    try {
      await workoutsApi.delete(id);
      toast('Workout deleted');
    } catch {
      setDays(prev);
      toast('Failed to delete workout', 'error');
    }
  };

  if (loading) return <LoadingSpinner fullPage />;

  return (
    <div className="space-y-4">
      <BackButton fallbackPath="/" />
      <h1 className="text-xl font-bold">Workout History</h1>

      {/* Activity trend chart */}
      {activityDays.length > 0 && (
        <div className="glass-card p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <p className="text-[11px] uppercase tracking-widest font-bold" style={{ color: 'var(--text-muted)' }}>Trend</p>
              <div className="flex gap-0.5 p-0.5 rounded-md" style={{ background: 'var(--bg-elevated)' }}>
                {([7, 30] as const).map((p) => (
                  <button
                    key={p}
                    onClick={() => switchChartPeriod(p)}
                    className="px-2 py-0.5 rounded text-[11px] font-bold transition-all"
                    style={chartPeriod === p ? { background: 'rgba(59,130,246,0.2)', color: '#3b82f6' } : { color: 'var(--text-muted)' }}
                  >
                    {p}D
                  </button>
                ))}
              </div>
            </div>
            <Link
              to="/trends?chart=activity"
              className="text-[11px] font-semibold"
              style={{ color: '#3b82f6' }}
            >
              View Trends
            </Link>
          </div>
          <ActivityChart days={activityDays} period={chartPeriod} />
          {lastSynced && (
            <p className="text-[11px] mt-2 text-center" style={{ color: 'var(--text-muted)' }}>
              Last synced {formatTimeAgo(lastSynced)}
            </p>
          )}
        </div>
      )}

      {/* Summary stats */}
      <ProBlur message="Pro">
      {totals.active_days > 0 && (
        <div className="glass-card p-4">
          <p className="text-[11px] uppercase tracking-widest font-bold mb-2" style={{ color: 'var(--text-muted)' }}>Last {chartPeriod} days</p>
          <div className="flex items-center gap-5 text-sm flex-wrap">
            <div>
              <span className="text-xl font-black" style={{ color: 'var(--text-primary)' }}>{totals.active_days}</span>
              <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>active days</span>
            </div>
            {totals.total_steps > 0 && (
              <div>
                <span className="text-xl font-black" style={{ color: 'var(--color-protein)' }}>{totals.total_steps.toLocaleString()}</span>
                <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>steps</span>
              </div>
            )}
            {totals.total_calories > 0 && (
              <div>
                <span className="text-xl font-black" style={{ color: 'var(--color-carbs, #f97316)' }}>{Math.round(totals.total_calories).toLocaleString()}</span>
                <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>cal</span>
              </div>
            )}
            {totals.workout_count > 0 && (
              <div>
                <span className="text-xl font-black" style={{ color: 'var(--color-fat, #22c55e)' }}>{totals.workout_count}</span>
                <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>workouts</span>
              </div>
            )}
          </div>
        </div>
      )}

      {days.length === 0 ? (
        <EmptyState
          icon={<span className="text-3xl">{'\uD83C\uDFC3'}</span>}
          title="No activity yet"
          subtitle="Connect Strava or Fitbit in Settings, or log manually from the + button on the dashboard"
        />
      ) : (
        <div className="space-y-3">
          {days.map((day) => <DayCard key={day.date} day={day} onDelete={handleDelete} />)}
        </div>
      )}
      </ProBlur>
    </div>
  );
}
