import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import type { WeightEntry } from '../api/weight';

const KG_TO_LBS = 2.20462;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso: string): string {
  const parts = iso.split(/[T ]/)[0].split('-');
  const day = parseInt(parts[2], 10);
  const month = MONTHS[parseInt(parts[1], 10) - 1] || parts[1];
  return `${day} ${month}`;
}

interface Props {
  entries: WeightEntry[];
  unit: 'kg' | 'lbs';
  /** Unique SVG gradient ID to avoid collisions when multiple charts exist */
  gradientId?: string;
  /** Enable scrub interaction (default true) */
  interactive?: boolean;
}

export default function WeightChart({ entries, unit, gradientId = 'wg', interactive = true }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [pathLength, setPathLength] = useState(0);
  const [animated, setAnimated] = useState(false);
  const pathRef = useRef<SVGPathElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevLenRef = useRef(entries.length);
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useLayoutEffect(() => {
    if (pathRef.current) {
      setPathLength(pathRef.current.getTotalLength());
    }
  }, [entries, unit]);

  useEffect(() => {
    const lenDiff = Math.abs(entries.length - prevLenRef.current);
    if (prevLenRef.current > 0 && lenDiff === 1) {
      setAnimated(true);
      prevLenRef.current = entries.length;
      return;
    }
    setAnimated(false);
    const raf = requestAnimationFrame(() => setAnimated(true));
    prevLenRef.current = entries.length;
    return () => cancelAnimationFrame(raf);
  }, [entries.length]);

  useEffect(() => { setSelectedIdx(null); }, [entries.length]);

  // Clean up fade timer on unmount
  useEffect(() => {
    return () => { clearTimeout(fadeTimerRef.current); };
  }, []);

  // Convert a touch/mouse clientX to the nearest data point index
  const findNearestPoint = useCallback((clientX: number) => {
    if (!containerRef.current || points.length === 0) return null;
    const rect = containerRef.current.getBoundingClientRect();
    // Convert client position to SVG coordinate space
    const svgX = ((clientX - rect.left) / rect.width) * chartW;

    let closest = 0;
    let closestDist = Infinity;
    for (let i = 0; i < points.length; i++) {
      const dist = Math.abs(points[i].x - svgX);
      if (dist < closestDist) {
        closestDist = dist;
        closest = i;
      }
    }
    return closest;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, unit]);

  if (entries.length === 0) return null;

  const chartEntries = [...entries].reverse(); // oldest first
  const weights = chartEntries.map((e) => unit === 'lbs' ? e.weight_kg * KG_TO_LBS : e.weight_kg);
  const minW = Math.floor(Math.min(...weights) - 1);
  const maxW = Math.ceil(Math.max(...weights) + 1);
  const range = maxW - minW || 1;

  const yTickCount = range <= 3 ? range + 1 : Math.min(5, range + 1);
  const yStep = yTickCount > 1 ? range / (yTickCount - 1) : range;
  const yTicks = Array.from({ length: yTickCount }, (_, i) => minW + i * yStep);

  const chartW = 320;
  const chartH = 140;
  const padLeft = 40;
  const padRight = 8;
  const padY = 10;
  const plotW = chartW - padLeft - padRight;

  const points = weights.map((w, i) => ({
    x: padLeft + (weights.length > 1 ? (i / (weights.length - 1)) * plotW : plotW / 2),
    y: padY + (1 - (w - minW) / range) * (chartH - 2 * padY),
  }));

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ');
  const areaD = pathD
    ? `${pathD} L${points[points.length - 1].x},${chartH} L${points[0].x},${chartH} Z`
    : '';

  // Scrub touch handlers
  const handleTouchStart = (e: React.TouchEvent) => {
    if (!interactive || points.length < 2) return;
    const idx = findNearestPoint(e.touches[0].clientX);
    if (idx !== null) {
      setScrubbing(true);
      setSelectedIdx(idx);
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!scrubbing) return;
    e.preventDefault(); // Prevent scroll while scrubbing
    const idx = findNearestPoint(e.touches[0].clientX);
    if (idx !== null) setSelectedIdx(idx);
  };

  const handleTouchEnd = () => {
    setScrubbing(false);
    clearTimeout(fadeTimerRef.current);
    fadeTimerRef.current = setTimeout(() => setSelectedIdx(null), 1500);
  };

  // Mouse scrub for desktop
  const handleMouseDown = (e: React.MouseEvent) => {
    if (!interactive || points.length < 2) return;
    const idx = findNearestPoint(e.clientX);
    if (idx !== null) {
      setScrubbing(true);
      setSelectedIdx(idx);
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!scrubbing) return;
    const idx = findNearestPoint(e.clientX);
    if (idx !== null) setSelectedIdx(idx);
  };

  const handleMouseUp = () => {
    if (scrubbing) {
      setScrubbing(false);
      clearTimeout(fadeTimerRef.current);
      fadeTimerRef.current = setTimeout(() => setSelectedIdx(null), 1500);
    }
  };

  const handleMouseLeave = () => {
    if (scrubbing) {
      setScrubbing(false);
      setSelectedIdx(null);
    }
  };

  // Crosshair + tooltip
  const renderCrosshair = () => {
    if (selectedIdx === null) return null;
    const p = points[selectedIdx];
    const w = weights[selectedIdx];
    const entry = chartEntries[selectedIdx];
    const label = `${w.toFixed(1)} ${unit}`;
    const date = formatDate(entry.logged_at);

    const tooltipW = 78;
    const tooltipH = 30;
    let tx = p.x - tooltipW / 2;
    if (tx < padLeft) tx = padLeft;
    if (tx + tooltipW > chartW - padRight) tx = chartW - padRight - tooltipW;
    const ty = Math.max(0, p.y - tooltipH - 12);

    return (
      <g>
        {/* Vertical crosshair line */}
        <line x1={p.x} y1={padY} x2={p.x} y2={chartH} stroke="#f59e0b" strokeOpacity="0.3" strokeWidth="1" strokeDasharray="3,3" />
        {/* Highlight dot */}
        <circle cx={p.x} cy={p.y} r="5" fill="#f59e0b" />
        <circle cx={p.x} cy={p.y} r="8" fill="none" stroke="#f59e0b" strokeOpacity="0.3" strokeWidth="1.5" />
        {/* Tooltip */}
        <rect x={tx} y={ty} width={tooltipW} height={tooltipH} rx="6" fill="var(--bg-elevated)" stroke="var(--border-glass)" strokeWidth="0.5" />
        <text x={tx + tooltipW / 2} y={ty + 12} textAnchor="middle" fill="#f59e0b" fontSize="10" fontWeight="bold" fontFamily="system-ui">
          {label}
        </text>
        <text x={tx + tooltipW / 2} y={ty + 24} textAnchor="middle" fill="var(--text-muted)" fontSize="8" fontFamily="system-ui">
          {date}
        </text>
      </g>
    );
  };

  const maxStagger = Math.min(50, 2000 / Math.max(points.length, 1));

  return (
    <div
      ref={containerRef}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
      style={{ touchAction: interactive && points.length >= 2 ? 'pan-y' : undefined, cursor: interactive ? 'crosshair' : undefined }}
    >
      <svg
        viewBox={`0 0 ${chartW} ${chartH}`}
        className="w-full"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Weight chart showing ${entries.length} entries`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Y-axis labels + grid lines */}
        {yTicks.map((val) => {
          const y = padY + (1 - (val - minW) / range) * (chartH - 2 * padY);
          return (
            <g key={val}>
              <line x1={padLeft} y1={y} x2={chartW - padRight} y2={y} stroke="var(--track-bg)" strokeWidth="0.5" />
              <text x={padLeft - 6} y={y + 3.5} textAnchor="end" fill="var(--text-muted)" fontSize="9" fontFamily="system-ui">
                {Math.round(val)}
              </text>
            </g>
          );
        })}
        {/* Area fill */}
        {areaD && (
          <path
            d={areaD}
            fill={`url(#${gradientId})`}
            style={{ opacity: animated ? 1 : 0, transition: 'opacity 600ms ease-out 300ms' }}
          />
        )}
        {/* Line */}
        <path
          ref={pathRef}
          d={pathD}
          fill="none"
          stroke="#f59e0b"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={pathLength > 0 ? {
            strokeDasharray: pathLength,
            strokeDashoffset: animated ? 0 : pathLength,
            transition: 'stroke-dashoffset 800ms ease-out',
          } : undefined}
        />
        {/* Data points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r={selectedIdx === i ? 0 : 3}
            fill="#f59e0b"
            style={{
              transform: animated ? 'scale(1)' : 'scale(0)',
              transformOrigin: `${p.x}px ${p.y}px`,
              transition: `transform 300ms ease-out ${300 + i * maxStagger}ms`,
            }}
          />
        ))}
        {/* Crosshair + tooltip */}
        {renderCrosshair()}
      </svg>
      <div className="flex justify-between text-[10px] mt-1" style={{ paddingLeft: `${padLeft * 100 / chartW}%`, color: 'var(--text-muted)' }}>
        {chartEntries.length > 0 && <span>{formatDate(chartEntries[0].logged_at)}</span>}
        {chartEntries.length > 1 && <span>{formatDate(chartEntries[chartEntries.length - 1].logged_at)}</span>}
      </div>
    </div>
  );
}
