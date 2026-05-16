import { useState, useRef, useEffect, useCallback } from 'react';
import type { TrendDay } from '../types';
import type { ActivityTrendDay } from '../api/dashboard';

type MacroKey = 'calories' | 'protein' | 'carbs' | 'fat';

interface Props {
  days: TrendDay[];
  activityDays?: ActivityTrendDay[];
  target: number;
  period: number;
  /** Which field to plot - defaults to 'calories' */
  valueKey?: MacroKey;
  /** Base bar color when no target adherence coloring applies */
  baseColor?: string;
  /** Unit label for legend - defaults to 'kcal' for calories, 'g' otherwise */
  unit?: string;
}

const MACRO_COLORS: Record<MacroKey, string> = {
  calories: '#10b981',
  protein: '#2dd4bf',
  carbs: '#f97316',
  fat: '#22c55e',
};

const CHART_W = 320;
const CHART_H = 184;
const PAD_L = 36;
const PAD_R = 8;
const PAD_Y = 10;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - 2 * PAD_Y - 14;  // reserve 14px for labels
const LABEL_Y = CHART_H - 2;

interface Bar {
  date: string;         // ISO start date of the week (or the day for daily bars)
  label: string;        // axis label (e.g. "Mon", "Mar 17")
  tooltipDate: string;  // tooltip header (e.g. "Mon, Mar 17" or "Week of Mar 17")
  value: number;        // primary value plotted (average-per-day for weekly, or raw for daily)
  burned: number;       // optional burned-calories overlay value
  calories: number;     // for tooltip when plotting calories
  dayCount: number;     // number of days aggregated (1 for daily)
}

function buildBars(days: TrendDay[], activityDays: ActivityTrendDay[] | undefined, valueKey: MacroKey, period: number): Bar[] {
  const burnedMap = activityDays ? new Map(activityDays.map(d => [d.date, d.burned_calories])) : null;

  if (period <= 7) {
    return days.map((d) => {
      const dt = new Date(d.date + 'T00:00:00');
      return {
        date: d.date,
        label: dt.toLocaleDateString('en-US', { weekday: 'short' }),
        tooltipDate: dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }),
        value: d[valueKey],
        burned: burnedMap?.get(d.date) || 0,
        calories: d.calories,
        dayCount: 1,
      };
    });
  }

  // Weekly aggregation for 30D/90D - Monday-based weeks, average per day within the week
  const weeks: Map<string, TrendDay[]> = new Map();
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
    .map(([weekKey, weekDays]) => {
      const n = weekDays.length;
      const first = new Date(weekKey + 'T00:00:00');
      const avg = weekDays.reduce((s, d) => s + d[valueKey], 0) / n;
      const avgCal = weekDays.reduce((s, d) => s + d.calories, 0) / n;
      const avgBurned = burnedMap
        ? weekDays.reduce((s, d) => s + (burnedMap.get(d.date) || 0), 0) / n
        : 0;
      const fmtShort = (dt: Date) => dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      return {
        date: weekKey,
        label: fmtShort(first),
        tooltipDate: `Week of ${fmtShort(first)}`,
        value: avg,
        burned: avgBurned,
        calories: avgCal,
        dayCount: n,
      };
    });
}

export default function CaloriesChart({ days, activityDays, target, period, valueKey = 'calories', baseColor, unit }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const isCal = valueKey === 'calories';
  const color = baseColor || MACRO_COLORS[valueKey];
  const displayUnit = unit || (isCal ? 'kcal' : 'g');

  useEffect(() => { setSelectedIdx(null); }, [days.length, period, valueKey]);
  useEffect(() => () => { clearTimeout(fadeTimerRef.current); }, []);

  const isWeekly = period > 7;
  const bars = buildBars(days, activityDays, valueKey, period);
  const values = bars.map(b => b.value);

  // Y-axis scaling
  const allVals = [...values];
  if (target > 0) allVals.push(target);
  const maxVal = Math.max(...allVals, isCal ? 100 : 10);
  const yMax = isCal
    ? Math.ceil((maxVal + 150) / 200) * 200
    : maxVal <= 50 ? Math.ceil((maxVal + 10) / 10) * 10
    : maxVal <= 200 ? Math.ceil((maxVal + 20) / 20) * 20
    : Math.ceil((maxVal + 50) / 50) * 50;

  // Bar layout - weekly bars get a wider cap and bigger gap so they fill the chart
  const barGap = bars.length <= 7 ? 4 : isWeekly ? 3 : bars.length <= 14 ? 2 : 1;
  const maxBarW = isWeekly ? 44 : 28;
  const totalGaps = Math.max(0, bars.length - 1) * barGap;
  const barW = bars.length > 0 ? Math.min(maxBarW, (PLOT_W - totalGaps) / bars.length) : 0;
  const totalBarsW = bars.length * barW + totalGaps;
  const offsetX = PAD_L + (PLOT_W - totalBarsW) / 2;

  const bx = (i: number) => offsetX + i * (barW + barGap);
  const toH = (val: number) => Math.max((val / yMax) * PLOT_H, 0);
  const toY = (val: number) => PAD_Y + PLOT_H - toH(val);

  // Y-axis ticks
  const tickStep = isCal
    ? (yMax <= 1000 ? 200 : yMax <= 2000 ? 500 : 1000)
    : (yMax <= 50 ? 10 : yMax <= 100 ? 20 : yMax <= 200 ? 50 : 100);
  const ticks: number[] = [];
  for (let v = 0; v <= yMax; v += tickStep) ticks.push(v);

  // X-axis label frequency
  const labelEvery = bars.length <= 7 ? 1 : bars.length <= 14 ? 2 : 3;

  // Color based on target adherence
  const getBarColor = (val: number) => {
    if (target <= 0 || val === 0) return color;
    if (isCal) {
      const ratio = val / target;
      if (ratio <= 1.05) return '#10b981';
      if (ratio <= 1.15) return '#f59e0b';
      return '#ef4444';
    }
    // For macros: green if within 10% of target, amber if over by 10-20%, red if way over
    const ratio = val / target;
    if (ratio >= 0.9 && ratio <= 1.1) return '#10b981';
    if (ratio > 1.2) return '#f59e0b';
    return color;
  };

  // Gradient IDs (unique per valueKey to avoid SVG conflicts)
  const gradPrefix = `chart-grad-${valueKey}`;

  // Scrub interaction
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

  // Target line Y position
  const targetY = target > 0 ? toY(target) : -1;

  // Collect unique bar colors for gradient defs
  const barColors = new Set<string>();
  values.forEach(v => barColors.add(getBarColor(v)));

  return (
    <div>
      {/* Tooltip */}
      {selectedIdx !== null && selectedIdx < bars.length && (() => {
        const b = bars[selectedIdx];
        return (
          <div className="mb-2 p-2.5 rounded-xl flex items-center justify-between text-[11px] animate-fade-in"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
            <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{b.tooltipDate}</span>
            {isCal ? (
              <div className="flex gap-2.5 tabular-nums font-semibold">
                <span style={{ color: '#60a5fa' }}>{Math.round(b.calories)} in</span>
                {b.burned > 0 && <span style={{ color: '#f97316' }}>-{Math.round(b.burned)}</span>}
                {b.burned > 0 && <span style={{ color: '#10b981' }}>= {Math.round(b.calories - b.burned)}</span>}
              </div>
            ) : (
              <div className="flex gap-2.5 tabular-nums font-semibold">
                <span style={{ color }}>{Math.round(b.value)} {displayUnit}</span>
                {target > 0 && <span style={{ color: 'var(--text-muted)' }}>/ {Math.round(target)}</span>}
              </div>
            )}
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
            {Array.from(barColors).map(c => (
              <linearGradient key={c} id={`${gradPrefix}-${c.slice(1)}`} x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor={c} stopOpacity="0.56" />
                <stop offset="100%" stopColor={c} stopOpacity="1" />
              </linearGradient>
            ))}
          </defs>

          {/* Y-axis grid + labels */}
          {ticks.map(val => (
            <g key={val}>
              <line x1={PAD_L} y1={toY(val)} x2={CHART_W - PAD_R} y2={toY(val)} stroke="var(--track-bg)" strokeWidth="0.5" />
              <text x={PAD_L - 5} y={toY(val) + 3} textAnchor="end" fill="var(--text-muted)" fontSize="7.5" fontFamily="system-ui" opacity="0.6">
                {val >= 1000 ? `${(val / 1000).toFixed(1)}k` : val}
              </text>
            </g>
          ))}

          {/* Target dashed line */}
          {target > 0 && targetY >= PAD_Y && targetY <= PAD_Y + PLOT_H && (
            <line x1={PAD_L} y1={targetY} x2={CHART_W - PAD_R} y2={targetY}
              stroke="#f59e0b" strokeWidth="1" strokeDasharray="4,3" strokeOpacity="0.5" />
          )}

          {/* Bars */}
          {bars.map((b, i) => {
            if (b.value === 0) return null;
            const isSelected = selectedIdx === i;
            const dimmed = selectedIdx !== null && !isSelected;
            const barColor = getBarColor(b.value);
            const gradId = `${gradPrefix}-${barColor.slice(1)}`;
            const h = toH(b.value);
            const rx = Math.min(3, barW / 2);

            return (
              <g key={b.date} opacity={dimmed ? 0.3 : 1}>
                <rect
                  x={bx(i)} y={PAD_Y + PLOT_H - h}
                  width={barW} height={h}
                  rx={rx}
                  fill={`url(#${gradId})`}
                />
                {isSelected && (
                  <rect
                    x={bx(i) - 1} y={PAD_Y + PLOT_H - h - 1}
                    width={barW + 2} height={h + 2}
                    rx={rx + 1}
                    fill="none" stroke={barColor} strokeWidth="1" opacity="0.5"
                  />
                )}
              </g>
            );
          })}

          {/* X-axis date labels */}
          {bars.map((b, i) => {
            if (i % labelEvery !== 0 && i !== bars.length - 1) return null;
            return (
              <text key={b.date} x={bx(i) + barW / 2} y={LABEL_Y} textAnchor="middle"
                fill="var(--text-muted)" fontSize="7.5" fontFamily="system-ui">
                {b.label}
              </text>
            );
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 mt-2 text-[11px]" style={{ color: 'var(--text-muted)' }}>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color, opacity: 0.85 }} />
          {isCal ? 'Intake' : valueKey.charAt(0).toUpperCase() + valueKey.slice(1)}
          {isWeekly && <span style={{ opacity: 0.7 }}>(weekly avg)</span>}
        </span>
        {target > 0 && (
          <>
            {isCal && (
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#f59e0b', opacity: 0.85 }} /> Over
              </span>
            )}
            <span className="flex items-center gap-1">
              <span className="w-4 border-t border-dashed" style={{ borderColor: '#f59e0b' }} /> Target {Math.round(target)} {displayUnit}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
