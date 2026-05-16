import { useState, useCallback, useEffect, useRef } from 'react';
import { dashboardApi } from '../api/dashboard';
import { formatLocalDate } from '../utils/date';
import { hapticLight } from '../utils/haptics';
import { ChevronRight } from './icons';
import { getTargetColor } from './ProgressRings';
import type { TrendDay, MacroTotals } from '../types';

const DAY_LABELS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

interface Props {
  selectedDate: string;
  onSelectDate: (date: string) => void;
}

/** Get the Monday-start grid for a month (may include prev/next month padding). */
function getMonthGrid(year: number, month: number): Date[][] {
  const first = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0).getDate();

  // Monday=0 offset
  let startDow = first.getDay() - 1;
  if (startDow < 0) startDow = 6;

  const cells: Date[] = [];

  // Padding from previous month
  for (let i = startDow - 1; i >= 0; i--) {
    const d = new Date(year, month, -i);
    cells.push(d);
  }

  // Days of this month
  for (let i = 1; i <= lastDay; i++) {
    cells.push(new Date(year, month, i));
  }

  // Padding to fill last week
  while (cells.length % 7 !== 0) {
    cells.push(new Date(year, month + 1, cells.length - startDow - lastDay + 1));
  }

  // Split into weeks
  const weeks: Date[][] = [];
  for (let i = 0; i < cells.length; i += 7) {
    weeks.push(cells.slice(i, i + 7));
  }
  return weeks;
}

function fmtMonth(year: number, month: number): string {
  return new Date(year, month).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}

export default function MonthCalendar({ selectedDate, onSelectDate }: Props) {
  const today = formatLocalDate();
  const initDate = selectedDate || today;
  const [viewYear, setViewYear] = useState(() => parseInt(initDate.slice(0, 4)));
  const [viewMonth, setViewMonth] = useState(() => parseInt(initDate.slice(5, 7)) - 1);
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  // Sync view to selected date when it changes to a different month
  useEffect(() => {
    if (!selectedDate) return;
    const y = parseInt(selectedDate.slice(0, 4));
    const m = parseInt(selectedDate.slice(5, 7)) - 1;
    if (y !== viewYear || m !== viewMonth) {
      setViewYear(y);
      setViewMonth(m);
    }
  }, [selectedDate]); // eslint-disable-line react-hooks/exhaustive-deps
  const [dayData, setDayData] = useState<Map<string, TrendDay>>(new Map());
  const [target, setTarget] = useState<MacroTotals | null>(null);

  // Swipe tracking
  const startXRef = useRef(0);
  const swipedRef = useRef(false);

  const weeks = getMonthGrid(viewYear, viewMonth);

  // Load data for the visible month
  const loadMonthData = useCallback(async () => {
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    // Last day of the month as "today" param, request that many days
    const lastDay = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(daysInMonth).padStart(2, '0')}`;
    try {
      const data = await dashboardApi.trend(daysInMonth, lastDay, { calendar: true });
      const map = new Map<string, TrendDay>();
      for (const d of data.days) {
        map.set(d.date, d);
      }
      setDayData(map);
      if (data.target) setTarget(data.target);
    } catch {
      // Silently fail
    }
  }, [viewYear, viewMonth]);

  useEffect(() => {
    loadMonthData();
  }, [loadMonthData]);

  const goMonth = (delta: number) => {
    hapticLight();
    let m = viewMonth + delta;
    let y = viewYear;
    if (m > 11) { m = 0; y++; }
    if (m < 0) { m = 11; y--; }
    setViewMonth(m);
    setViewYear(y);
  };

  const onTouchStart = (e: React.TouchEvent) => {
    startXRef.current = e.touches[0].clientX;
    swipedRef.current = false;
  };

  const onTouchEnd = (e: React.TouchEvent) => {
    const dx = e.changedTouches[0].clientX - startXRef.current;
    if (Math.abs(dx) > 50 && !swipedRef.current) {
      swipedRef.current = true;
      goMonth(dx > 0 ? -1 : 1);
    }
  };

  // Need to update dashboard trend API to accept a specific "today" override
  // The trend API already supports ?today=&days= params

  // Update the dashboard API to accept custom today for month view
  const targetCals = target?.calories || 0;

  return (
    <div className="glass-card overflow-hidden">
      {/* Header */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => { hapticLight(); setCollapsed((c) => !c); }}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); hapticLight(); setCollapsed((c) => !c); } }}
        className="w-full flex items-center justify-between px-4 py-3 cursor-pointer"
      >
        <div className="flex items-center gap-3">
          <button
            onClick={(e) => { e.stopPropagation(); goMonth(-1); }}
            className="p-1 rounded-lg active:bg-black/5 dark:active:bg-white/5" style={{ color: 'var(--text-muted)' }}
          >
            <ChevronRight className="w-4 h-4 rotate-180" />
          </button>
          <span className="text-sm font-bold min-w-[140px] text-center" style={{ color: 'var(--text-primary)' }}>
            {fmtMonth(viewYear, viewMonth)}
          </span>
          <button
            onClick={(e) => { e.stopPropagation(); goMonth(1); }}
            className="p-1 rounded-lg active:bg-black/5 dark:active:bg-white/5" style={{ color: 'var(--text-muted)' }}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
        <ChevronRight className={`w-4 h-4 transition-transform duration-200 ${collapsed ? '' : 'rotate-90'}`} style={{ color: 'var(--text-muted)' }} />
      </div>

      {/* Calendar grid */}
      {!collapsed && (
        <div
          className="px-3 pb-3 animate-fade-in"
          data-swipe-handler
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
        >
          {/* Day labels */}
          <div className="grid grid-cols-7 mb-1">
            {DAY_LABELS.map((d) => (
              <div key={d} className="text-center text-[10px] font-bold uppercase py-1" style={{ color: 'var(--text-muted)' }}>
                {d}
              </div>
            ))}
          </div>

          {/* Weeks - show only current + previous week unless expanded */}
          {(() => {
            let visibleWeeks = weeks;
            if (!expanded) {
              // Find the week containing today (or selected date)
              const refDate = selectedDate || today;
              const refWeekIdx = weeks.findIndex((week) =>
                week.some((d) => formatLocalDate(d) === refDate)
              );
              if (refWeekIdx >= 0) {
                const startIdx = Math.max(0, refWeekIdx - 1);
                visibleWeeks = weeks.slice(startIdx, startIdx + 2);
              } else {
                // Fallback: show last 2 weeks of the month
                visibleWeeks = weeks.slice(-2);
              }
            }
            return visibleWeeks;
          })().map((week, wi) => (
            <div key={wi} className="grid grid-cols-7">
              {week.map((date) => {
                const dateStr = formatLocalDate(date);
                const isCurrentMonth = date.getMonth() === viewMonth;
                const isToday = dateStr === today;
                const isSelected = dateStr === selectedDate;
                const isFuture = dateStr > today;
                const dd = dayData.get(dateStr);
                const hasMeals = dd && dd.meal_count > 0;

                // Ring color based on calorie target
                const ringColor = hasMeals && targetCals > 0
                  ? getTargetColor(dd.calories, targetCals, '#3b82f6')
                  : 'transparent';

                // Ring progress
                const pct = hasMeals && targetCals > 0
                  ? Math.min(dd.calories / targetCals, 1)
                  : 0;
                const r = 13;
                const circ = 2 * Math.PI * r;
                const offset = circ * (1 - pct);

                return (
                  <button
                    key={dateStr}
                    onClick={() => {
                      if (isFuture) return;
                      hapticLight();
                      onSelectDate(isSelected ? '' : dateStr);
                    }}
                    disabled={isFuture}
                    className={`flex items-center justify-center py-1 ${
                      !isCurrentMonth ? 'opacity-20' : isFuture ? 'opacity-30' : ''
                    }`}
                  >
                    <div className="relative w-8 h-8 flex items-center justify-center">
                      {/* Ring */}
                      {hasMeals && (
                        <svg width={30} height={30} className="absolute inset-0 m-auto -rotate-90">
                          <circle cx={15} cy={15} r={r} fill="none" stroke="var(--track-bg)" strokeWidth={2.5} />
                          <circle
                            cx={15} cy={15} r={r} fill="none"
                            stroke={ringColor}
                            strokeWidth={2.5}
                            strokeLinecap="round"
                            strokeDasharray={circ}
                            strokeDashoffset={offset}
                            style={{ filter: `drop-shadow(0 0 3px ${ringColor}40)` }}
                          />
                        </svg>
                      )}

                      {/* Selected highlight */}
                      {isSelected && (
                        <div className="absolute inset-0 rounded-full bg-emerald-500/20 border border-emerald-500/40" />
                      )}

                      {/* Today dot */}
                      {isToday && !isSelected && (
                        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-emerald-400" />
                      )}

                      {/* Day number */}
                      <span
                        className={`relative text-[11px] font-semibold ${
                          isSelected || isToday ? 'text-emerald-400' : ''
                        }`}
                        style={!(isSelected || isToday) ? { color: hasMeals ? 'var(--text-primary)' : 'var(--text-muted)' } : undefined}
                      >
                        {date.getDate()}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          ))}

          {/* Expand / Collapse toggle */}
          <button
            onClick={(e) => { e.stopPropagation(); hapticLight(); setExpanded((v) => !v); }}
            className="w-full flex items-center justify-center gap-1 py-1.5 mt-1 rounded-lg text-[10px] font-semibold active:bg-black/5 dark:active:bg-white/5 transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            {expanded ? 'Show less' : 'Show full month'}
            <ChevronRight className={`w-3 h-3 transition-transform duration-200 ${expanded ? '-rotate-90' : 'rotate-90'}`} />
          </button>

          {/* Legend */}
          <div className="flex items-center justify-center gap-4 mt-2 text-[10px] font-medium" style={{ color: 'var(--text-muted)' }}>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500" style={{ boxShadow: '0 0 4px rgba(16,185,129,0.4)' }} />
              On target
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              Under
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              Over
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
