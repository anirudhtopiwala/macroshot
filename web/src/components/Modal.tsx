import { useEffect, type ReactNode } from 'react';
import useOverlayHistory from '../hooks/useOverlayHistory';

interface Props {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  position?: 'center' | 'bottom';
}

export default function Modal({ open, onClose, children, position = 'center' }: Props) {
  useOverlayHistory(onClose, open);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    // Scroll-lock the page behind the modal so taps/scrolls on
    // content-behind don't bleed through on iOS (visible through the
    // blur backdrop). Mirrors the pattern already used by
    // BadgeCelebration and WeightTracker.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', handler);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const isBottom = position === 'bottom';

  return (
    <div
      className="fixed inset-0 z-[90]"
      style={{
        display: 'flex',
        alignItems: isBottom ? 'flex-end' : 'center',
        justifyContent: 'center',
      }}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 transition-opacity duration-200"
        style={{ background: 'var(--overlay-bg)', backdropFilter: 'blur(4px)' }}
        onClick={onClose}
      />
      {/* Content */}
      <div
        className={
          isBottom
            ? 'relative w-full max-w-lg rounded-t-3xl'
            : 'glass-card relative mx-6 max-w-sm w-full rounded-2xl'
        }
        style={
          isBottom
            ? {
                background: 'var(--bg-base)',
                border: '1px solid var(--border-glass)',
                borderBottom: 'none',
                animation: 'slideUp 200ms ease-out',
              }
            : { animation: 'fadeSlideUp 200ms ease-out' }
        }
      >
        {children}
      </div>
    </div>
  );
}
