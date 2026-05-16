import { useState, useEffect, useCallback } from 'react';
import { Scale } from './icons';
import { weightApi, type WeightHistoryResponse } from '../api/weight';
import { hapticLight } from '../utils/haptics';

const KG_TO_LBS = 2.20462;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso: string): string {
  const parts = iso.split(/[T ]/)[0].split('-');
  const day = parseInt(parts[2], 10);
  const month = MONTHS[parseInt(parts[1], 10) - 1];
  const year = parts[0];
  return `${month} ${day}, ${year}`;
}

/** Human-friendly "Logged X" string based on days since last weigh-in. */
function formatLoggedAgo(daysSince: number, lastLoggedAt: string): string {
  if (daysSince === 0) return 'Logged today';
  if (daysSince === 1) return 'Logged yesterday';
  if (daysSince <= 6) return `Logged ${daysSince} days ago`;
  // 7+ days: use absolute date
  const parts = lastLoggedAt.split(/[T ]/)[0].split('-');
  const month = MONTHS[parseInt(parts[1], 10) - 1];
  const day = parseInt(parts[2], 10);
  const year = parts[0];
  const currentYear = String(new Date().getFullYear());
  if (year === currentYear) return `Logged on ${month} ${day}`;
  return `Logged on ${month} ${day}, ${year}`;
}

function daysBetween(a: string, b: Date): number {
  const d1 = new Date(a.split(/[T ]/)[0] + 'T00:00:00');
  const d2 = new Date(b.toISOString().split('T')[0] + 'T00:00:00');
  return Math.floor((d2.getTime() - d1.getTime()) / 86400000);
}

export default function WeightSummaryCard() {
  const [data, setData] = useState<WeightHistoryResponse | null>(null);
  const [unit, setUnit] = useState<'kg' | 'lbs'>(() =>
    (localStorage.getItem('weight_unit') as 'kg' | 'lbs') || 'kg'
  );

  const fetchData = useCallback(() => {
    weightApi.history(90).then(setData).catch(() => {});
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Refresh when weight is logged/deleted from WeightTracker
  useEffect(() => {
    const handler = () => fetchData();
    window.addEventListener('weight-changed', handler);
    return () => window.removeEventListener('weight-changed', handler);
  }, [fetchData]);

  // Sync unit when changed elsewhere
  useEffect(() => {
    const handler = (e: Event) => setUnit((e as CustomEvent).detail);
    window.addEventListener('weight-unit-changed', handler);
    return () => window.removeEventListener('weight-unit-changed', handler);
  }, []);


  // Don't render if no data or no entries
  if (!data || !data.latest) return null;

  const convert = (kg: number) => unit === 'lbs' ? kg * KG_TO_LBS : kg;
  const display = (kg: number) => convert(kg).toFixed(1);

  const current = data.latest;
  const start = data.start ?? current;
  const goal = data.goal;
  const daysSinceLast = data.last_logged_at ? daysBetween(data.last_logged_at, new Date()) : null;

  // Weekly rate of change
  let weeklyRate: number | null = null;
  if (data.entries.length > 1) {
    const oldest = data.entries[data.entries.length - 1];
    const newest = data.entries[0];
    const d1 = new Date(oldest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
    const d2 = new Date(newest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
    const days = (d2.getTime() - d1.getTime()) / 86400000;
    if (days >= 7) {
      weeklyRate = ((newest.weight_kg - oldest.weight_kg) / days) * 7;
    }
  }

  // Progress toward goal
  let progressPct = 0;
  if (goal && start !== goal) {
    const totalDelta = goal - start;
    const currentDelta = current - start;
    progressPct = Math.max(0, Math.min(100, (currentDelta / totalDelta) * 100));
  }

  // Projection
  let projectedDate: string | null = null;
  if (goal && data.entries.length > 1) {
    const oldest = data.entries[data.entries.length - 1];
    const newest = data.entries[0];
    const daysSpan = daysBetween(oldest.logged_at, new Date(newest.logged_at.split(/[T ]/)[0] + 'T00:00:00'));
    const weightDelta = newest.weight_kg - oldest.weight_kg;
    if (daysSpan > 0 && weightDelta !== 0) {
      const ratePerDay = weightDelta / daysSpan;
      const remaining = goal - current;
      if ((remaining > 0 && ratePerDay > 0) || (remaining < 0 && ratePerDay < 0)) {
        const daysToGoal = Math.ceil(Math.abs(remaining / ratePerDay));
        const target = new Date();
        target.setDate(target.getDate() + daysToGoal);
        projectedDate = formatDate(target.toISOString());
      }
    }
  }

  const progressColor = '#f59e0b';
  const displayRate = weeklyRate !== null ? convert(weeklyRate) : null;

  return (
      <div
        className="rounded-2xl p-4 space-y-3"
        style={{
          background: 'linear-gradient(135deg, rgba(245,158,11,0.06) 0%, var(--bg-elevated) 100%)',
          border: '1px solid rgba(245,158,11,0.15)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.2), inset 0 1px 0 var(--border-glass)',
          backdropFilter: 'blur(16px)',
        }}
      >
        {/* Top row: current weight (left) + stacked buttons (right) */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <Scale className="w-3.5 h-3.5 text-amber-500/60" />
              <span className="text-[11px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Weight</span>
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-3xl font-black tabular-nums" style={{ color: '#f59e0b' }}>{display(current)}</span>
              <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>{unit}</span>
            </div>
            {/* Weekly rate */}
            {displayRate !== null && (
              <span className={`text-xs font-semibold tabular-nums ${displayRate < -0.05 ? 'text-emerald-400' : displayRate > 0.05 ? 'text-red-400' : ''}`} style={Math.abs(displayRate) <= 0.05 ? { color: 'var(--text-muted)' } : undefined}>
                {displayRate > 0 ? '+' : ''}{displayRate.toFixed(1)} {unit}/wk
              </span>
            )}
          </div>
          <button
            onClick={() => {
              hapticLight();
              window.dispatchEvent(new CustomEvent('open-weight-tracker'));
            }}
            className="min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl active:scale-95 transition-transform"
            style={{
              background: 'rgba(245,158,11,0.12)',
              border: '1px solid rgba(245,158,11,0.25)',
            }}
          >
            <Scale className="w-5 h-5" style={{ color: '#f59e0b' }} />
          </button>
        </div>

        {/* Logged date - own line below weight and buttons */}
        {daysSinceLast !== null && data.last_logged_at && (
          <p className="text-[11px] font-medium" style={{ color: daysSinceLast >= 7 ? '#f87171' : 'var(--text-muted)' }}>
            {formatLoggedAgo(daysSinceLast, data.last_logged_at)}
          </p>
        )}

        {/* Progress bar (only if goal is set) */}
        {goal && (
          <>
            <div className="w-full h-2 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{
                  width: `${progressPct}%`,
                  background: `linear-gradient(90deg, ${progressColor}, ${progressColor}cc)`,
                  boxShadow: `0 0 8px ${progressColor}40`,
                }}
              />
            </div>

            <div className="flex items-center justify-between text-xs">
              <span style={{ color: 'var(--text-muted)' }}>
                Start: <span className="font-bold" style={{ color: 'var(--text-secondary)' }}>{display(start)} {unit}</span>
              </span>
              <span style={{ color: 'var(--text-muted)' }}>
                Goal: <span className="font-bold" style={{ color: '#f59e0b' }}>{display(goal)} {unit}</span>
              </span>
            </div>

            {projectedDate && (
              <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                At your goal by <span className="font-bold" style={{ color: 'var(--text-secondary)' }}>{projectedDate}</span>
              </p>
            )}
          </>
        )}
      </div>
  );
}
