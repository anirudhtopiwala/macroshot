import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shield } from './icons';

interface Props {
  /** Shield count before the award - popup animates upward from this. */
  shieldsBefore: number;
  /** Shield count after the award. Should be shieldsBefore + 1 (or more on burst). */
  shieldsAfter: number;
  onDone: () => void;
}

const SHIELD_COLOR = '#3b82f6';

function CountUpNumber({ from, to, durationMs = 900 }: { from: number; to: number; durationMs?: number }) {
  const [n, setN] = useState(from);
  useEffect(() => {
    if (from === to) return;
    const startedAt = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - startedAt) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setN(Math.round(from + (to - from) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [from, to, durationMs]);
  return <span className="tabular-nums">{n}</span>;
}

export default function ShieldEarnedCelebration({ shieldsBefore, shieldsAfter, onDone }: Props) {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<'show' | 'fading'>('show');
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const handleDismiss = useCallback(() => {
    clearTimeout(dismissTimerRef.current);
    clearTimeout(fadeTimerRef.current);
    setPhase('fading');
    fadeTimerRef.current = setTimeout(onDone, 400);
  }, [onDone]);

  const handleView = useCallback(() => {
    clearTimeout(dismissTimerRef.current);
    clearTimeout(fadeTimerRef.current);
    navigate('/settings/achievements?section=shields');
    onDone();
  }, [navigate, onDone]);

  useEffect(() => {
    dismissTimerRef.current = setTimeout(() => {
      setPhase('fading');
      fadeTimerRef.current = setTimeout(onDone, 400);
    }, 4500);
    return () => {
      clearTimeout(dismissTimerRef.current);
      clearTimeout(fadeTimerRef.current);
    };
  }, [onDone]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  const awarded = Math.max(1, shieldsAfter - shieldsBefore);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Streak shield earned"
      className={`celebration-overlay fixed inset-0 z-[80] flex items-center justify-center bg-black/60 backdrop-blur-sm transition-opacity duration-[400ms] ${
        phase === 'fading' ? 'opacity-0' : 'opacity-100'
      }`}
      onClick={handleDismiss}
    >
      <div
        className={`flex flex-col items-center gap-3 max-w-xs w-full mx-4 transition-all duration-[400ms] ${
          phase === 'fading' ? 'scale-110 opacity-0' : 'scale-100 opacity-100'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="absolute top-1/4 left-0 right-0 flex justify-center gap-6 text-2xl pointer-events-none" aria-hidden>
          <span className="animate-pulse">&#10024;</span>
          <span className="animate-pulse" style={{ animationDelay: '0.2s' }}>&#10024;</span>
          <span className="animate-pulse" style={{ animationDelay: '0.4s' }}>&#10024;</span>
        </div>

        <div
          className="w-24 h-24 rounded-full flex items-center justify-center animate-shield-bash"
          style={{
            background: `radial-gradient(circle, ${SHIELD_COLOR}33 0%, transparent 70%)`,
            boxShadow: `0 0 60px ${SHIELD_COLOR}80, 0 0 120px ${SHIELD_COLOR}30`,
          }}
        >
          <Shield
            className="w-14 h-14 animate-shield-pulse"
            style={{ color: SHIELD_COLOR }}
            aria-hidden
          />
        </div>

        <p className="text-xl font-bold text-white">Shield Earned!</p>
        <p className="text-sm text-center" style={{ color: 'rgba(255,255,255,0.75)' }}>
          {awarded > 1
            ? `${awarded} shields banked from your on-target streak.`
            : 'Three consecutive on-target days. Banked for the next slip.'}
        </p>

        <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: 'rgba(255,255,255,0.85)' }}>
          <Shield className="w-4 h-4" style={{ color: SHIELD_COLOR }} aria-hidden />
          <span>Shields:</span>
          <span className="px-2 py-0.5 rounded-md text-base font-bold tabular-nums"
            style={{ background: 'rgba(59,130,246,0.18)', border: '1px solid rgba(59,130,246,0.35)', color: SHIELD_COLOR, minWidth: '2rem', textAlign: 'center' }}
          >
            <CountUpNumber from={shieldsBefore} to={shieldsAfter} />
          </span>
        </div>

        <div className="flex items-center gap-3 mt-2">
          <button
            onClick={handleView}
            className="px-5 py-2 rounded-xl text-sm font-bold transition-all active:scale-95"
            style={{
              background: `${SHIELD_COLOR}15`,
              border: `1px solid ${SHIELD_COLOR}40`,
              color: SHIELD_COLOR,
            }}
          >
            View
          </button>
          <button
            onClick={handleDismiss}
            className="px-6 py-2 rounded-xl text-sm font-bold transition-all active:scale-95"
            style={{
              background: 'rgba(255,255,255,0.12)',
              border: '1px solid rgba(255,255,255,0.25)',
              color: '#fff',
              backdropFilter: 'blur(12px)',
            }}
          >
            Nice!
          </button>
        </div>
      </div>
    </div>
  );
}
