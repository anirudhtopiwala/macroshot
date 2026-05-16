import { useEffect, useState, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { hapticLight } from '../utils/haptics';

// Singleton: only one tooltip visible at a time
let activeTooltipId: string | null = null;

interface TooltipProps {
  id: string;
  show: boolean;
  targetRef: React.RefObject<HTMLElement | null>;
  position?: 'top' | 'bottom';
  children: React.ReactNode;
}

function calcCoords(el: HTMLElement, position: 'top' | 'bottom') {
  const rect = el.getBoundingClientRect();
  const tooltipWidth = Math.min(280, window.innerWidth - 32);
  let left = rect.left + rect.width / 2 - tooltipWidth / 2;
  left = Math.max(16, Math.min(left, window.innerWidth - tooltipWidth - 16));
  const arrowLeft = rect.left + rect.width / 2 - left;
  const top = position === 'top' ? rect.top - 12 : rect.bottom + 12;
  return { top, left, arrowLeft };
}

/**
 * Contextual tooltip - shows once per feature, dismissed by tapping "Got it" or overlay.
 * Uses localStorage guard (`tooltip_seen_<id>`) to never reappear.
 * Recalculates position on scroll so it stays anchored to its target.
 * Renders via portal to escape overflow:hidden containers.
 */
export default function Tooltip({ id, show, targetRef, position = 'bottom', children }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number; arrowLeft: number } | null>(null);
  const rafRef = useRef(0);

  const guardKey = `tooltip_seen_${id}`;

  const dismiss = useCallback(() => {
    setVisible(false);
    if (activeTooltipId === id) activeTooltipId = null;
    try { localStorage.setItem(guardKey, '1'); } catch { /* */ }
  }, [guardKey, id]);

  // Clean up singleton on unmount (e.g., page navigation while tooltip is visible)
  useEffect(() => {
    return () => {
      if (activeTooltipId === id) activeTooltipId = null;
    };
  }, [id]);

  // Recalculate position on scroll/resize so tooltip stays anchored
  useEffect(() => {
    if (!visible || !targetRef.current) return;

    const update = () => {
      if (!targetRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        if (!targetRef.current) return;
        setCoords(calcCoords(targetRef.current, position));
      });
    };

    window.addEventListener('scroll', update, true); // capture phase for nested scrollers
    window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
      cancelAnimationFrame(rafRef.current);
    };
  }, [visible, targetRef, position]);

  useEffect(() => {
    if (!show) return;
    // Already seen or another tooltip is active
    if (localStorage.getItem(guardKey)) return;
    if (activeTooltipId && activeTooltipId !== id) return;

    const timer = setTimeout(() => {
      if (!targetRef.current) return;
      if (localStorage.getItem(guardKey)) return;
      if (activeTooltipId && activeTooltipId !== id) return;

      activeTooltipId = id;
      setCoords(calcCoords(targetRef.current, position));
      setVisible(true);
      hapticLight();
    }, 1500);

    return () => clearTimeout(timer);
  }, [show, id, guardKey, targetRef, position]);

  if (!visible || !coords) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[75]"
      onClick={dismiss}
    >
      <div
        className="absolute rounded-2xl p-3 px-4 border animate-fade-in"
        style={{
          top: coords.top,
          left: coords.left,
          maxWidth: 280,
          ...(position === 'top' ? { transform: 'translateY(-100%)' } : {}),
          background: 'var(--bg-elevated)',
          borderColor: 'var(--border-glass)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
          backdropFilter: 'blur(24px)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Arrow */}
        <div
          className="absolute w-3 h-3 rotate-45 border"
          style={{
            [position === 'top' ? 'bottom' : 'top']: -7,
            left: Math.max(12, Math.min(coords.arrowLeft - 6, 268)),
            background: 'var(--bg-elevated)',
            borderColor: 'var(--border-glass)',
            [position === 'top' ? 'borderTop' : 'borderBottom']: 'none',
            [position === 'top' ? 'borderLeft' : 'borderRight']: 'none',
          }}
        />
        <p className="text-xs font-medium leading-relaxed" style={{ color: 'var(--text-primary)' }}>
          {children}
        </p>
        <button
          className="text-[11px] mt-1.5 ml-auto block font-medium"
          style={{ color: 'var(--text-muted)' }}
          onClick={(e) => { e.stopPropagation(); dismiss(); }}
        >
          Got it
        </button>
      </div>
    </div>,
    document.body,
  );
}
