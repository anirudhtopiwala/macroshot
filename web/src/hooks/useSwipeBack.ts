import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

const EDGE_THRESHOLD = 30;   // px from left edge to start tracking
const SWIPE_MIN = 80;        // minimum horizontal distance to trigger back
const MAX_VERTICAL = 60;     // abort if vertical movement exceeds this
const SWIPE_VELOCITY = 0.3;  // px/ms - fast swipes need less distance

/**
 * Edge swipe-to-go-back gesture (left edge → right).
 *
 * Only triggers when the touch starts within EDGE_THRESHOLD px of the left
 * screen edge.  Avoids conflicts with in-content swipe actions by checking
 * for `data-swipe-handler` on ancestor elements.
 *
 * Disabled on iOS Safari (non-standalone) where the native edge-back gesture
 * already exists - enabling both would double-navigate.
 */
export default function useSwipeBack() {
  const navigate = useNavigate();
  const touchRef = useRef<{ startX: number; startY: number; startTime: number; id: number } | null>(null);

  useEffect(() => {
    // iOS has native edge-swipe-back in both Safari and standalone PWA (iOS 17+).
    // Enabling our hook would double-navigate. Skip entirely on iOS.
    if (/iPhone|iPad|iPod/.test(navigator.userAgent)) return;

    function onTouchStart(e: TouchEvent) {
      // Reject multi-touch (pinch/zoom)
      if (e.touches.length > 1) { touchRef.current = null; return; }

      const touch = e.touches[0];
      if (touch.clientX <= EDGE_THRESHOLD) {
        // Skip if the target is inside a component with its own horizontal swipe
        const target = e.target as HTMLElement;
        if (target.closest?.('[data-swipe-handler]')) {
          touchRef.current = null;
          return;
        }
        touchRef.current = { startX: touch.clientX, startY: touch.clientY, startTime: Date.now(), id: touch.identifier };
      } else {
        touchRef.current = null;
      }
    }

    function onTouchMove(e: TouchEvent) {
      if (!touchRef.current) return;
      // Find the tracked finger
      const touch = Array.from(e.touches).find(t => t.identifier === touchRef.current!.id);
      if (!touch) { touchRef.current = null; return; }
      // Abort early if gesture becomes too vertical at any point
      const dy = Math.abs(touch.clientY - touchRef.current.startY);
      if (dy > MAX_VERTICAL) touchRef.current = null;
    }

    function onTouchEnd(e: TouchEvent) {
      if (!touchRef.current) return;
      // Find the correct finger that ended
      const touch = Array.from(e.changedTouches).find(t => t.identifier === touchRef.current!.id);
      if (!touch) return;

      const dx = touch.clientX - touchRef.current.startX;
      const dy = Math.abs(touch.clientY - touchRef.current.startY);
      const dt = Date.now() - touchRef.current.startTime;
      const velocity = dx / Math.max(dt, 1);

      touchRef.current = null;

      // Must be a rightward swipe, not too vertical
      if (dy > MAX_VERTICAL || dx < 0) return;

      // Trigger on either sufficient distance or sufficient velocity
      if (dx >= SWIPE_MIN || (dx >= 40 && velocity >= SWIPE_VELOCITY)) {
        if (window.history.length > 1) {
          navigate(-1);
        }
      }
    }

    function onTouchCancel() {
      touchRef.current = null;
    }

    document.addEventListener('touchstart', onTouchStart, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: true });
    document.addEventListener('touchend', onTouchEnd, { passive: true });
    document.addEventListener('touchcancel', onTouchCancel, { passive: true });
    return () => {
      document.removeEventListener('touchstart', onTouchStart);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', onTouchEnd);
      document.removeEventListener('touchcancel', onTouchCancel);
    };
  }, [navigate]);
}
