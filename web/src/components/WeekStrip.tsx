import { useRef, useState, useEffect } from 'react';
import { getTargetColor } from './ProgressRings';
import { hapticLight } from '../utils/haptics';
import type { TrendDay } from '../types';

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MAX_WEEKS_BACK = 5;
const SWIPE_THRESHOLD = 50;

interface Props {
  days: TrendDay[];
  selectedDate: string;
  onSelect: (date: string) => void;
  target?: number;
}

function getWeekDates(date: Date): Date[] {
  const d = new Date(date);
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  const monday = new Date(d);
  monday.setDate(d.getDate() + diff);
  const week: Date[] = [];
  for (let i = 0; i < 7; i++) {
    const wd = new Date(monday);
    wd.setDate(monday.getDate() + i);
    week.push(wd);
  }
  return week;
}

import { formatLocalDate } from '../utils/date';

const fmt = formatLocalDate;

function getRingColor(calories: number, target: number): string {
  return getTargetColor(calories, target, '');
}

function getRingPct(calories: number, target: number): number {
  if (target <= 0 || calories === 0) return 0;
  return Math.min(calories / target, 1);
}

function weeksBetween(a: string, b: string): number {
  const da = new Date(a + 'T00:00:00');
  const db = new Date(b + 'T00:00:00');
  return Math.round((db.getTime() - da.getTime()) / (7 * 86400000));
}

function addDays(dateStr: string, n: number): string {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return fmt(d);
}

export default function WeekStrip({ days, selectedDate, onSelect, target = 0 }: Props) {
  const today = fmt(new Date());
  const week = getWeekDates(new Date(selectedDate + 'T00:00:00'));
  const dayMap = new Map(days.map((d) => [d.date, d]));

  const containerRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef(0);
  const startYRef = useRef(0);
  const draggingRef = useRef(false);
  // 'none' = haven't decided yet, 'horizontal' = we own the gesture, 'vertical' = browser owns it
  const gestureRef = useRef<'none' | 'horizontal' | 'vertical'>('none');
  const dxRef = useRef(0);
  const [shake, setShake] = useState(false);

  // Store latest props in refs so native listeners always see current values
  const selectedDateRef = useRef(selectedDate);
  selectedDateRef.current = selectedDate;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const canGoBack = weeksBetween(addDays(selectedDate, -7), today) <= MAX_WEEKS_BACK;
  const canGoForward = addDays(selectedDate, 7) <= today;
  const canGoBackRef = useRef(canGoBack);
  canGoBackRef.current = canGoBack;
  const canGoForwardRef = useRef(canGoForward);
  canGoForwardRef.current = canGoForward;

  const setShakeRef = useRef(setShake);
  setShakeRef.current = setShake;

  // Attach non-passive touch listeners so we can preventDefault on horizontal drags
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const handleStart = (e: TouchEvent) => {
      startXRef.current = e.touches[0].clientX;
      startYRef.current = e.touches[0].clientY;
      draggingRef.current = false;
      gestureRef.current = 'none';
      dxRef.current = 0;
      el.style.transition = 'none';
      el.style.transform = 'translateX(0)';
    };

    const handleMove = (e: TouchEvent) => {
      const dx = e.touches[0].clientX - startXRef.current;
      const dy = e.touches[0].clientY - startYRef.current;

      // Decide gesture direction on first significant movement
      if (gestureRef.current === 'none') {
        if (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy) * 1.2) {
          gestureRef.current = 'horizontal';
          draggingRef.current = true;
          hapticLight(); // Feel the strip "grab"
        } else if (Math.abs(dy) > 8) {
          gestureRef.current = 'vertical';
          return;
        } else {
          return;
        }
      }

      if (gestureRef.current === 'vertical') return;

      // We own this gesture - prevent page scroll
      e.preventDefault();

      dxRef.current = dx;

      let visual = dx;
      const goingBack = dx > 0;
      if ((goingBack && !canGoBackRef.current) || (!goingBack && !canGoForwardRef.current)) {
        visual = Math.sign(dx) * Math.sqrt(Math.abs(dx)) * 4;
      }

      el.style.transform = `translateX(${visual}px)`;
    };

    const handleEnd = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      gestureRef.current = 'none';
      const dx = dxRef.current;
      const goBack = dx > 0;

      const shouldCommit = Math.abs(dx) > SWIPE_THRESHOLD;
      const blocked = (goBack && !canGoBackRef.current) || (!goBack && !canGoForwardRef.current);

      if (shouldCommit && !blocked) {
        const width = el.offsetWidth;
        el.style.transition = 'transform 0.25s ease-out, opacity 0.25s ease-out';
        el.style.transform = `translateX(${goBack ? width : -width}px)`;
        el.style.opacity = '0';
        hapticLight();

        setTimeout(() => {
          const targetDate = addDays(selectedDateRef.current, goBack ? -7 : 7);
          onSelectRef.current(targetDate);
          el.style.transition = 'none';
          el.style.transform = `translateX(${goBack ? -width : width}px)`;
          el.style.opacity = '0';
          el.offsetHeight; // force reflow
          el.style.transition = 'transform 0.3s ease-out, opacity 0.2s ease-out';
          el.style.transform = 'translateX(0)';
          el.style.opacity = '1';
        }, 250);
      } else if (shouldCommit && blocked) {
        el.style.transition = 'transform 0.3s cubic-bezier(0.36, 0.07, 0.19, 0.97)';
        el.style.transform = 'translateX(0)';
        setShakeRef.current(true);
        hapticLight();
        setTimeout(() => setShakeRef.current(false), 400);
      } else {
        el.style.transition = 'transform 0.3s ease-out';
        el.style.transform = 'translateX(0)';
      }
    };

    el.addEventListener('touchstart', handleStart, { passive: true });
    el.addEventListener('touchmove', handleMove, { passive: false });
    el.addEventListener('touchend', handleEnd, { passive: true });

    return () => {
      el.removeEventListener('touchstart', handleStart);
      el.removeEventListener('touchmove', handleMove);
      el.removeEventListener('touchend', handleEnd);
    };
  }, []); // Empty deps - refs handle current values

  return (
    <div
      ref={containerRef}
      className={`flex justify-between ${shake ? 'animate-shake-x' : ''}`}
      data-swipe-handler
    >
      {week.map((date, i) => {
        const dateStr = fmt(date);
        const dayNum = date.getDate();
        const dayData = dayMap.get(dateStr);
        const meals = dayData?.meal_count ?? 0;
        const cals = dayData?.calories ?? 0;
        const isToday = dateStr === today;
        const isSelected = dateStr === selectedDate;
        const isFuture = dateStr > today;
        const hasLogged = meals > 0;
        const dayTarget = dayData?.effective_target_calories ?? target;
        const ringColor = hasLogged ? getRingColor(cals, dayTarget) || '#3b82f6' : '';
        const pct = getRingPct(cals, dayTarget);
        const r = 17;
        const circumference = 2 * Math.PI * r;
        const arcOffset = circumference * (1 - pct);

        return (
          <button
            key={dateStr}
            onClick={() => !isFuture && onSelect(dateStr)}
            disabled={isFuture}
            className={`flex flex-col items-center gap-0.5 py-2 px-1 rounded-2xl transition-all duration-200 ${
              isFuture ? 'opacity-30' : 'cursor-pointer'}`}
            style={isSelected ? { background: 'var(--track-bg)', boxShadow: 'inset 0 0 0 1px var(--border-glass)' } : undefined}
          >
            <span className={`text-[11px] font-semibold ${
              isToday ? 'text-emerald-400' : ''
            }`} style={!isToday ? { color: 'var(--text-muted)' } : undefined}>
              {DAY_LABELS[i]}
            </span>
            <div className="relative w-10 h-10 flex items-center justify-center">
              <svg width={40} height={40} className="absolute inset-0 -rotate-90">
                <circle
                  cx={20} cy={20} r={r}
                  fill="none"
                  stroke="var(--track-bg)"
                  strokeWidth={hasLogged ? 3 : 1.5}
                  strokeDasharray={hasLogged ? 'none' : '4 3'}
                />
                {hasLogged && (
                  <circle
                    cx={20} cy={20} r={r}
                    fill="none"
                    stroke={ringColor}
                    strokeWidth={3}
                    strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={arcOffset}
                    style={{
                      filter: `drop-shadow(0 0 4px ${ringColor})`,
                      transition: 'stroke-dashoffset 0.5s ease',
                    }}
                  />
                )}
              </svg>
              <span
                className={`text-sm font-bold relative ${
                  isToday && !isSelected ? 'text-emerald-400' : ''
                }`}
                style={!(isToday && !isSelected) ? { color: hasLogged ? 'var(--text-primary)' : 'var(--text-muted)' } : undefined}
              >
                {dayNum}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
