import { useState, useRef, useEffect, useCallback } from 'react';
import type { ActivityTrendDay } from '../api/dashboard';

interface Props {
  days: ActivityTrendDay[];
  period: number;
}

interface BarData {
  label: string;
  burned: number;
  steps: number;
  activeMin: number;
  dayCount: number;
}

const CHART_W = 320;
const CHART_H = 154;
const PAD_L = 36;
const PAD_R = 8;
const PAD_Y = 8;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - 2 * PAD_Y - 14;  // reserve 14px for labels
const LABEL_Y = CHART_H - 3;

function aggregateToBars(days: ActivityTrendDay[], period: number): BarData[] {
  if (days.length === 0) return [];

  if (period <= 7) {
    return days.map(d => {
      const dt = new Date(d.date + 'T00:00:00');
      return {
        label: dt.toLocaleDateString('en-US', { weekday: 'short' }),
        burned: d.burned_calories,
        steps: d.steps,
        activeMin: d.active_minutes,
        dayCount: 1,
      };
    });
  }

  // Weekly aggregation for 30D/90D
  const weeks: Map<string, ActivityTrendDay[]> = new Map();
  for (const d of days) {
    const dt = new Date(d.date + 'T00:00:00');
    const day = dt.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const monday = new Date(dt);
    monday.setDate(dt.getDate() + mondayOffset);
    const key = monday.toISOString().slice(0, 10);
    if (!weeks.has(key)) weeks.set(key, []);
    weeks.get(key)!.push(d);
  }

  return Array.from(weeks.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, weekDays]) => {
      const n = weekDays.length;
      const first = new Date(weekDays[0].date + 'T00:00:00');
      const fmtShort = (dt: Date) => dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      return {
        label: fmtShort(first),
        burned: Math.round(weekDays.reduce((s, d) => s + d.burned_calories, 0) / n),
        steps: Math.round(weekDays.reduce((s, d) => s + d.steps, 0) / n),
        activeMin: Math.round(weekDays.reduce((s, d) => s + d.active_minutes, 0) / n),
        dayCount: n,
      };
    });
}

export default function ActivityChart({ days, period }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const bars = aggregateToBars(days, period);

  useEffect(() => { setSelectedIdx(null); }, [days.length, period]);
  useEffect(() => () => { clearTimeout(fadeTimerRef.current); }, []);

  const hasBurned = bars.some(b => b.burned > 0);
  const hasSteps = bars.some(b => b.steps > 0);

  const values = bars.map(b => b.burned);
  const maxVal = Math.max(...values, 1);
  const yMax = maxVal <= 500 ? Math.ceil(maxVal / 100) * 100 + 50
    : maxVal <= 2000 ? Math.ceil(maxVal / 200) * 200 + 100
    : Math.ceil(maxVal / 1000) * 1000 + 500;

  const isWeeklyBars = period > 7;
  const barGap = bars.length <= 7 ? 4 : 2;
  const maxBarW = isWeeklyBars ? 44 : 28;
  const totalGaps = Math.max(0, (bars.length - 1)) * barGap;
  const barW = bars.length > 0 ? Math.min(maxBarW, (PLOT_W - totalGaps) / bars.length) : 0;
  const totalBarsW = bars.length * barW + totalGaps;
  const offsetX = PAD_L + (PLOT_W - totalBarsW) / 2;

  const bx = (i: number) => offsetX + i * (barW + barGap);
  const toH = (val: number) => (val / yMax) * PLOT_H;
  const toY = (val: number) => PAD_Y + PLOT_H - toH(val);

  // Y-axis ticks
  const tickStep = yMax <= 300 ? 100 : yMax <= 1000 ? 200 : yMax <= 5000 ? 1000 : yMax <= 15000 ? 2000 : 5000;
  const ticks: number[] = [];
  for (let v = 0; v <= yMax; v += tickStep) ticks.push(v);

  // X-axis label frequency
  const labelEvery = bars.length <= 7 ? 1 : bars.length <= 14 ? 2 : 3;

  // useCallback must be called unconditionally (before any early return)
  const findNearest = useCallback((clientX: number) => {
    if (!containerRef.current || bars.length === 0) return null;
    const rect = containerRef.current.getBoundingClientRect();
    const pxX = ((clientX - rect.left) / rect.width) * CHART_W;
    let closest = 0, closestDist = Infinity;
    for (let i = 0; i < bars.length; i++) {
      const cx = bx(i) + barW / 2;
      const dist = Math.abs(cx - pxX);
      if (dist < closestDist) { closestDist = dist; closest = i; }
    }
    return closest;
  }, [bars.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const startScrub = (clientX: number) => { const i = findNearest(clientX); if (i !== null) { setScrubbing(true); setSelectedIdx(i); } };
  const moveScrub = (clientX: number) => { if (!scrubbing) return; const i = findNearest(clientX); if (i !== null) setSelectedIdx(i); };
  const endScrub = () => { setScrubbing(false); clearTimeout(fadeTimerRef.current); fadeTimerRef.current = setTimeout(() => setSelectedIdx(null), 2000); };

  // Empty state - all hooks are called above, safe to return early here
  if (!hasBurned && !hasSteps) {
    return (
      <div className="flex items-center justify-center" style={{ minHeight: '160px' }}>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No activity data for this period</p>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-1.5 mb-0.5 ml-1">
        <span className="w-2 h-2 rounded-full" style={{ background: '#f97316' }} />
        <span className="text-[11px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
          Active cal {isWeeklyBars && <span className="font-normal" style={{ color: 'var(--text-muted)' }}>(weekly avg)</span>}
        </span>
      </div>

      {/* Tooltip - shows both calories and steps */}
      {selectedIdx !== null && selectedIdx < bars.length && (() => {
        const b = bars[selectedIdx];
        return (
          <div className="mb-1 px-2.5 py-1.5 rounded-lg flex items-center justify-between text-[11px] animate-fade-in"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
            <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{b.label}</span>
            <div className="flex gap-2.5 tabular-nums font-semibold">
              {b.burned > 0 && <span style={{ color: '#f97316' }}>{b.burned.toLocaleString()} cal</span>}
              {b.steps > 0 && <span style={{ color: '#00B0B9' }}>{b.steps.toLocaleString()} steps</span>}
              {b.activeMin > 0 && <span style={{ color: 'var(--text-muted)' }}>{b.activeMin} min</span>}
            </div>
          </div>
        );
      })()}

      <div
        ref={containerRef}
        onTouchStart={e => { startScrub(e.touches[0].clientX); e.stopPropagation(); }}
        onTouchMove={e => moveScrub(e.touches[0].clientX)}
        onTouchEnd={endScrub}
        onMouseDown={e => startScrub(e.clientX)}
        onMouseMove={e => moveScrub(e.clientX)}
        onMouseUp={endScrub}
        onMouseLeave={() => { if (scrubbing) { setScrubbing(false); setSelectedIdx(null); } }}
        style={{ touchAction: 'pan-y', cursor: 'crosshair' }}
      >
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
          <defs>
            <linearGradient id="activity-bar-grad" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0%" stopColor="#f97316" stopOpacity="0.35" />
              <stop offset="100%" stopColor="#f97316" stopOpacity="1" />
            </linearGradient>
          </defs>

          {/* Y-axis grid + labels */}
          {ticks.map(val => (
            <g key={val}>
              <line x1={PAD_L} y1={toY(val)} x2={CHART_W - PAD_R} y2={toY(val)} stroke="var(--track-bg)" strokeWidth="0.5" />
              <text x={PAD_L - 5} y={toY(val) + 3} textAnchor="end" fill="var(--text-muted)" fontSize="7" fontFamily="system-ui" opacity="0.6">
                {val >= 1000 ? `${(val / 1000).toFixed(val >= 10000 ? 0 : 1)}k` : val}
              </text>
            </g>
          ))}

          {/* Bars - active calories */}
          {bars.map((b, i) => {
            if (b.burned === 0) return null;
            const isSelected = selectedIdx === i;
            const h = toH(b.burned);
            const rx = Math.min(3, barW / 2);
            return (
              <g key={i}>
                <rect
                  x={bx(i)} y={PAD_Y + PLOT_H - h}
                  width={barW} height={h}
                  rx={rx}
                  fill="url(#activity-bar-grad)"
                  opacity={selectedIdx === null || isSelected ? 1 : 0.35}
                />
                {isSelected && (
                  <rect
                    x={bx(i) - 1} y={PAD_Y + PLOT_H - h - 1}
                    width={barW + 2} height={h + 2}
                    rx={rx + 1}
                    fill="none" stroke="#f97316" strokeWidth="1" opacity="0.5"
                  />
                )}
              </g>
            );
          })}

          {/* X-axis labels */}
          {bars.map((b, i) => {
            if (i % labelEvery !== 0 && i !== bars.length - 1) return null;
            return (
              <text
                key={i}
                x={bx(i) + barW / 2}
                y={LABEL_Y}
                textAnchor="middle"
                fontSize="7"
                fill="var(--text-muted)"
                fontFamily="system-ui"
              >
                {b.label}
              </text>
            );
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 mt-1.5 text-[11px]" style={{ color: 'var(--text-muted)' }}>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#f97316', opacity: 0.85 }} /> Active cal
        </span>
        {hasSteps && (
          <span className="flex items-center gap-1" style={{ opacity: 0.7 }}>
            tap bar for steps
          </span>
        )}
      </div>
    </div>
  );
}
