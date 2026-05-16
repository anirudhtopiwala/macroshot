import { useId } from 'react';

interface Props {
  eaten: number;
  target: number;
  label: string;
  unit: string;
  color: string;
  size?: number;
  gradient?: boolean;
  glow?: boolean;
  strokeWidth?: number;
  /** When true, overrides color based on target proximity: green (within 10%), yellow (within 20%), red (over), blue (under) */
  autoColor?: boolean;
}

/** Compute ring color based on how close eaten is to target.
 *  3-state: in progress (fallback), on target (green), over (red).
 *  Green: within 200 of target, Red: over target by 200+ */
export function getTargetColor(eaten: number, target: number, fallback: string): string {
  if (target <= 0 || eaten === 0) return fallback;
  const diff = eaten - target;           // positive = over, negative = under
  if (diff > 200) return '#f87171';      // soft red - over target by >200
  if (Math.abs(diff) <= 200) return '#34d399';  // green - within 200
  return fallback;                        // in progress - caller's default
}

export default function ProgressRing({ eaten, target, label, unit, color, size = 120, gradient, glow, strokeWidth: strokeWProp, autoColor }: Props) {
  const pct = target > 0 ? Math.min(eaten / target, 1.5) : 0;
  const strokeW = strokeWProp ?? (size < 80 ? 5 : 10);
  const r = (size - strokeW - 4) / 2;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - Math.min(pct, 1));

  const ringColor = autoColor ? getTargetColor(eaten, target, color) : color;
  const over = pct > 1;
  // When autoColor is on, getTargetColor already handles the over state (red when >200 over).
  // Only apply the hard red override for non-autoColor rings.
  const displayColor = (!autoColor && over) ? '#f87171' : ringColor;

  const reactId = useId();
  const id = `ring-grad-${reactId}`;
  const filterId = `ring-glow-${reactId}`;

  return (
    <div className="flex flex-col items-center relative">
      <svg width={size} height={size} className="-rotate-90" style={{ overflow: 'visible' }}>
        <defs>
          {gradient && (
            <linearGradient id={id} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor={displayColor} />
              <stop offset="100%" stopColor={displayColor} stopOpacity="0.5" />
            </linearGradient>
          )}
          {glow && (
            <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="6" />
            </filter>
          )}
        </defs>
        {/* Track */}
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke="var(--track-bg)"
          strokeWidth={strokeW}
        />
        {/* Glow layer */}
        {glow && (
          <circle
            cx={size / 2} cy={size / 2} r={r}
            fill="none" stroke={displayColor}
            strokeWidth={strokeW + 6} strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            filter={`url(#${filterId})`}
            opacity="0.5"
            className="transition-all duration-700"
          />
        )}
        {/* Progress arc */}
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke={gradient ? `url(#${id})` : displayColor}
          strokeWidth={strokeW} strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="transition-all duration-700"
        />
      </svg>
      {label && (
        <>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-lg font-bold">{Math.round(eaten)}</span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>/ {Math.round(target)} {unit}</span>
          </div>
          <span className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>{label}</span>
        </>
      )}
    </div>
  );
}

interface BarProps {
  eaten: number;
  target: number;
  label: string;
  unit: string;
  color: string;
}

export function MacroBar({ eaten, target, label, unit, color }: BarProps) {
  const pct = target > 0 ? Math.min((eaten / target) * 100, 100) : 0;
  const over = eaten > target && target > 0;

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs w-16 text-right" style={{ color: 'var(--text-muted)' }}>{label}</span>
      <div className="flex-1 h-3 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: over ? '#f87171' : color }}
        />
      </div>
      <span className="text-xs font-medium w-20">
        {Math.round(eaten)} / {Math.round(target)}{unit}
      </span>
    </div>
  );
}
