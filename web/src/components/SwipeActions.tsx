import { useRef, useState, useCallback, useEffect, type ReactNode } from 'react';
import { Trash2 } from './icons';
import { hapticLight, hapticWarning, hapticSuccess } from '../utils/haptics';

interface Props {
  onDelete: () => void | Promise<void>;
  onRelog?: () => void | Promise<void>;
  children: ReactNode;
}

export default function SwipeActions({ onDelete, onRelog, children }: Props) {
  const startXRef = useRef(0);
  const startYRef = useRef(0);
  const swipingRef = useRef(false);
  const lockedRef = useRef(false);
  const verticalRef = useRef(false);
  const crossedRef = useRef(false);
  const translateXRef = useRef(0);
  const [translateX, setTranslateX] = useState(0);
  const [transitioning, setTransitioning] = useState(false);
  const [revealedSide, setRevealedSide] = useState<'left' | 'right' | null>(null);

  const REVEAL = 72;
  const AUTO_TRIGGER = 160;
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    return () => { timersRef.current.forEach(clearTimeout); };
  }, []);

  const track = (timer: ReturnType<typeof setTimeout>) => {
    timersRef.current.add(timer);
    return timer;
  };

  const reset = useCallback(() => {
    setTransitioning(true);
    setTranslateX(0);
    setRevealedSide(null);
    track(setTimeout(() => setTransitioning(false), 300));
  }, []);

  const doDelete = useCallback(async () => {
    hapticWarning();
    setTransitioning(true);
    setTranslateX(-400);
    track(setTimeout(async () => {
      try {
        await onDelete();
      } finally {
        setTranslateX(0);
        setRevealedSide(null);
        setTransitioning(false);
      }
    }, 250));
  }, [onDelete]);

  const doRelog = useCallback(async () => {
    if (!onRelog) return;
    hapticSuccess();
    setTransitioning(true);
    setTranslateX(400);
    track(setTimeout(async () => {
      try {
        await onRelog();
      } finally {
        setTranslateX(0);
        setRevealedSide(null);
        setTransitioning(false);
      }
    }, 250));
  }, [onRelog]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    startXRef.current = e.touches[0].clientX;
    startYRef.current = e.touches[0].clientY;
    swipingRef.current = false;
    lockedRef.current = false;
    verticalRef.current = false;
    crossedRef.current = false;
    setTransitioning(false);
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (verticalRef.current) return;

    const dx = e.touches[0].clientX - startXRef.current;
    const dy = e.touches[0].clientY - startYRef.current;

    if (!lockedRef.current) {
      if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) {
        verticalRef.current = true;
        return;
      }
      if (Math.abs(dx) > 8) {
        lockedRef.current = true;
        swipingRef.current = true;
      } else {
        return;
      }
    }

    const base = revealedSide === 'right' ? -REVEAL : revealedSide === 'left' ? REVEAL : 0;
    let raw = base + dx;

    // If no relog handler, don't allow right swipe
    if (!onRelog && raw > 0) raw = 0;

    translateXRef.current = raw;
    setTranslateX(raw);

    // Haptic at threshold
    if (Math.abs(raw) > REVEAL / 2 && !crossedRef.current) {
      crossedRef.current = true;
      hapticLight();
    }
  }, [revealedSide, onRelog]);

  const onTouchEnd = useCallback(() => {
    if (!swipingRef.current) return;
    swipingRef.current = false;
    lockedRef.current = false;
    const tx = translateXRef.current;

    if (tx < -AUTO_TRIGGER) {
      doDelete();
    } else if (tx > AUTO_TRIGGER && onRelog) {
      doRelog();
    } else if (tx < -(REVEAL / 2)) {
      setTransitioning(true);
      setTranslateX(-REVEAL);
      setRevealedSide('right');
      track(setTimeout(() => setTransitioning(false), 300));
    } else if (tx > REVEAL / 2 && onRelog) {
      setTransitioning(true);
      setTranslateX(REVEAL);
      setRevealedSide('left');
      track(setTimeout(() => setTransitioning(false), 300));
    } else {
      reset();
    }
  }, [doDelete, doRelog, onRelog, reset]);

  const onClickCapture = useCallback((e: React.MouseEvent) => {
    if (revealedSide) {
      e.preventDefault();
      e.stopPropagation();
      reset();
    }
  }, [revealedSide, reset]);

  return (
    <div className="relative overflow-hidden rounded-2xl">
      {/* Re-log zone (left side, revealed on swipe right) */}
      {onRelog && translateX > 0 && (
        <div className="absolute inset-y-0 left-0 w-[72px] flex items-center justify-center bg-emerald-600 rounded-l-2xl">
          <button
            onClick={doRelog}
            className="flex flex-col items-center justify-center w-full h-full text-white text-[11px] font-bold gap-0.5"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Relog
          </button>
        </div>
      )}

      {/* Delete zone (right side, revealed on swipe left) */}
      {translateX < 0 && (
        <div className="absolute inset-y-0 right-0 w-[72px] flex items-center justify-center bg-red-600 rounded-r-2xl">
          <button
            onClick={doDelete}
            className="flex items-center justify-center w-full h-full text-white"
          >
            <Trash2 className="w-5 h-5" />
          </button>
        </div>
      )}

      <div
        data-swipe-handler
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onClickCapture={onClickCapture}
        className={transitioning ? 'transition-transform duration-300 ease-out' : ''}
        style={{ transform: `translateX(${translateX}px)`, position: 'relative', zIndex: 1 }}
      >
        {children}
      </div>
    </div>
  );
}
