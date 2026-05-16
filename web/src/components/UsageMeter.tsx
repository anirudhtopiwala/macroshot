import type { CapState } from '../hooks/useCapState';

interface Props {
  /** Cap state from useCapState - the source of truth. */
  cap: CapState;
  /** Row label (e.g. "Photo scans today", "AI chats today"). */
  label: string;
  /** Unit shown after the count when `used` label needs clarifying. */
  unit?: string;
  /** Compact variant: thinner bar, smaller text, for Settings/inline use. */
  compact?: boolean;
  /** Hide the numeric "X / Y" suffix. */
  hideNumbers?: boolean;
}

/** Visual meter for a capped feature - segmented bar (≤10 segments) or
 *  a continuous gradient bar when the limit is higher. Colors shift from
 *  emerald → amber → red as usage approaches/exceeds the cap. Returns null
 *  if the feature is uncapped/unlimited so callers can drop this into any
 *  layout without a beta-mode conditional. */
export default function UsageMeter({ cap, label, unit, compact = false, hideNumbers = false }: Props) {
  if (!cap.hasCap) return null;

  const { used, limit, atLimit, lastOne, nearLimit } = cap;
  const color = atLimit ? '#ef4444' : lastOne || nearLimit ? '#f59e0b' : '#10b981';
  const pct = Math.min(100, (used / limit) * 100);
  const useSegments = limit <= 10;

  const textSize = compact ? 'text-[11px]' : 'text-xs';
  const barHeight = compact ? 'h-1' : 'h-1.5';

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span
          className={`${textSize} font-medium`}
          style={{ color: atLimit ? color : 'var(--text-secondary)' }}
        >
          {atLimit ? `${label} - limit reached` : lastOne ? `${label} - last one today` : label}
        </span>
        {!hideNumbers && (
          <span
            className={`${textSize} font-semibold tabular-nums`}
            style={{ color }}
          >
            {used}/{limit}{unit ? ` ${unit}` : ''}
          </span>
        )}
      </div>
      {useSegments ? (
        <div className="flex gap-1">
          {Array.from({ length: limit }, (_, i) => (
            <div
              key={i}
              className={`${barHeight} flex-1 rounded-full transition-all duration-300`}
              style={{ background: i < used ? color : 'var(--track-bg)' }}
            />
          ))}
        </div>
      ) : (
        <div className={`w-full ${barHeight} rounded-full`} style={{ background: 'var(--track-bg)' }}>
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{ width: `${pct}%`, background: color }}
          />
        </div>
      )}
    </div>
  );
}
