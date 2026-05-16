import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { hapticLight } from '../utils/haptics';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { useDisablePullToRefresh } from '../context/PullToRefreshContext';

interface Props {
  value: number;
  onChange: (v: number) => void;
  onClose: () => void;
  min?: number;
  max?: number;
  step?: number;
  label: string;
  unit: string;
  color: string;
}

const ITEM_HEIGHT = 44;
const VISIBLE_ITEMS = 5;
const CONTAINER_HEIGHT = ITEM_HEIGHT * VISIBLE_ITEMS;
const PAD = ITEM_HEIGHT * Math.floor(VISIBLE_ITEMS / 2);
const HAPTIC_THROTTLE_MS = 50;

export default function DialPicker({ value, onChange, onClose, min = 0, max = 2000, step = 1, label, unit, color }: Props) {
  useOverlayHistory(onClose);
  useDisablePullToRefresh();

  const options = useMemo(() => {
    const arr: number[] = [];
    for (let v = min; v <= max; v += step) arr.push(v);
    return arr;
  }, [min, max, step]);

  const initial = Math.round(value / step) * step;
  const initialIndex = Math.max(0, options.indexOf(initial));

  const scrollRef = useRef<HTMLDivElement>(null);
  const lastTickIndex = useRef(-1);
  const lastHapticTs = useRef(0);
  const settleTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const didMount = useRef(false);

  const [selectedIndex, setSelectedIndex] = useState(initialIndex);

  // Clean up settleTimer on unmount to prevent stale onChange calls
  useEffect(() => {
    return () => clearTimeout(settleTimer.current);
  }, []);

  // Block background scroll via non-passive touchmove on the overlay
  const overlayRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const block = (e: TouchEvent) => {
      // Only block if touch is NOT inside the scroll container
      if (!scrollRef.current?.contains(e.target as Node)) {
        e.preventDefault();
      }
    };
    el.addEventListener('touchmove', block, { passive: false });
    return () => el.removeEventListener('touchmove', block);
  }, []);

  // Scroll to initial value on mount
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = initialIndex * ITEM_HEIGHT;
    lastTickIndex.current = initialIndex;
    requestAnimationFrame(() => { didMount.current = true; });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !didMount.current) return;

    const index = Math.max(0, Math.min(
      Math.round(el.scrollTop / ITEM_HEIGHT),
      options.length - 1,
    ));

    setSelectedIndex(index);

    // Haptic on each item boundary crossing
    if (index !== lastTickIndex.current) {
      lastTickIndex.current = index;
      const now = Date.now();
      if (now - lastHapticTs.current >= HAPTIC_THROTTLE_MS) {
        lastHapticTs.current = now;
        hapticLight();
      }
    }

    // Debounce value commit until scroll settles
    clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(() => {
      onChange(options[index]);
    }, 100);
  }, [options, onChange]);

  return createPortal(
    <div ref={overlayRef} className="fixed inset-0 z-[80] flex items-center justify-center px-6" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-sm rounded-3xl pb-6 pt-4 px-6 animate-slide-up"
        style={{ background: 'var(--bg-base)', border: '1px solid var(--border-glass)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Handle */}
        <div className="w-10 h-1 rounded-full mx-auto mb-4" style={{ background: 'var(--text-muted)' }} />

        {/* Label */}
        <p className="text-center text-sm font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>{label}</p>

        {/* Current value */}
        <p className="text-center text-3xl font-black tabular-nums mb-2" style={{ color }}>
          {options[selectedIndex]}<span className="text-sm font-semibold ml-1" style={{ color: 'var(--text-muted)' }}>{unit}</span>
        </p>

        {/* Picker wheel */}
        <div className="relative mx-auto" style={{ maxWidth: '180px', height: CONTAINER_HEIGHT }}>
          {/* Selection highlight bar */}
          <div
            className="absolute left-0 right-0 rounded-xl pointer-events-none z-10"
            style={{
              top: PAD,
              height: ITEM_HEIGHT,
              background: `${color}12`,
              border: `1px solid ${color}25`,
            }}
          />

          {/* Top/bottom gradient masks */}
          <div
            className="absolute inset-x-0 top-0 pointer-events-none z-20"
            style={{ height: PAD, background: 'linear-gradient(to bottom, var(--bg-base), transparent)' }}
          />
          <div
            className="absolute inset-x-0 bottom-0 pointer-events-none z-20"
            style={{ height: PAD, background: 'linear-gradient(to top, var(--bg-base), transparent)' }}
          />

          {/* Scrollable column */}
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="absolute inset-0 overflow-y-scroll scrollbar-hide"
            style={{
              scrollSnapType: 'y mandatory',
              WebkitOverflowScrolling: 'touch',
              overscrollBehavior: 'contain',
            }}
          >
            {/* Top spacer */}
            <div style={{ height: PAD }} />

            {options.map((v, i) => (
              <div
                key={v}
                className="flex items-center justify-center"
                style={{ height: ITEM_HEIGHT, scrollSnapAlign: 'center' }}
              >
                <span
                  className="tabular-nums font-bold"
                  style={{
                    fontSize: selectedIndex === i ? '1.5rem' : '1rem',
                    color: selectedIndex === i ? color : 'var(--text-muted)',
                    transition: 'font-size 0.1s, color 0.1s',
                  }}
                >
                  {v}
                </span>
              </div>
            ))}

            {/* Bottom spacer */}
            <div style={{ height: PAD }} />
          </div>
        </div>

        {/* Done button */}
        <button
          onClick={onClose}
          className="w-full mt-4 py-3 rounded-xl text-sm font-bold transition-all active:scale-[0.98]"
          style={{
            background: `${color}18`,
            border: `1px solid ${color}30`,
            color,
          }}
        >
          Done
        </button>
      </div>
    </div>,
    document.body,
  );
}
