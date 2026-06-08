import { useRef } from 'react';
import { useLocation } from 'react-router-dom';

const TAB_ORDER: Record<string, number> = {
  '/': 0,
  '/journal': 1,
  '/log': 2,
  '/trends': 3,
  '/settings': 4,
};

export default function AnimatedPage({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  const prevPathRef = useRef(pathname);

  const prevIdx = TAB_ORDER[prevPathRef.current] ?? -1;
  const currIdx = TAB_ORDER[pathname] ?? -1;

  // Default is no animation on same-path mounts (e.g. reload, initial paint).
  // Previously this was `animate-fade-in` (opacity 0 → 1 over 200ms) but on
  // iOS PWA reloads the animation could stall mid-frame while the JS thread
  // was busy resolving the lazy chunk - leaving the page visually blank
  // until the user scrolled and forced a repaint. The fade is cosmetic only
  // on first paint; keep slide animations for real route transitions.
  let animation = '';
  if (prevPathRef.current !== pathname) {
    if (currIdx >= 0 && prevIdx >= 0) {
      animation = currIdx > prevIdx ? 'animate-slide-in-right' : 'animate-slide-in-left';
    } else if (currIdx < 0) {
      // Entering a detail page (e.g. /meals/:id)
      animation = 'animate-slide-up';
    }
    prevPathRef.current = pathname;
  }

  // Defensive `opacity: 1` so that even if a slide animation stalls
  // (the same iOS paint-stall failure mode), the element is at least
  // visible - animations only OVERRIDE opacity while they're playing,
  // they don't enforce the final value without `animation-fill-mode`.
  return (
    <div key={pathname} className={animation} style={{ opacity: 1 }}>
      {children}
    </div>
  );
}
