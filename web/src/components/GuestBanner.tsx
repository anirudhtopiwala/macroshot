import { Link } from 'react-router-dom';
import { listGuestMeals } from '../utils/guestStorage';
import { useEffect, useState } from 'react';

/**
 * Persistent sign-up nudge shown above the page content while the
 * visitor is browsing in guest mode. Surfaces the number of locally-
 * stored meals so the user knows what will be migrated on signup.
 */
export default function GuestBanner() {
  const [count, setCount] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    listGuestMeals().then((meals) => {
      if (!cancelled) setCount(meals.length);
    });
    // Refresh on the same custom event LogMeal fires after accept so the
    // counter ticks up without a navigation. Cheap — listGuestMeals is
    // one IndexedDB getAll.
    const onAdded = () => listGuestMeals().then((m) => { if (!cancelled) setCount(m.length); });
    window.addEventListener('guest-meal-added', onAdded);
    return () => { cancelled = true; window.removeEventListener('guest-meal-added', onAdded); };
  }, []);

  return (
    <div
      className="mb-4 rounded-xl p-3 flex items-center justify-between gap-3"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-glass)',
      }}
    >
      <div className="text-sm flex-1 min-w-0" style={{ color: 'var(--text-secondary)' }}>
        <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>You're browsing as a guest.</span>{' '}
        {count > 0
          ? `Sign up to save ${count} ${count === 1 ? 'meal' : 'meals'} and unlock streaks, chat, and trends.`
          : 'Sign up to save meals across devices and unlock streaks, chat, and trends.'}
      </div>
      <Link
        to="/signup"
        className="glass-btn glass-btn-primary text-xs py-2 px-3 font-semibold rounded-xl whitespace-nowrap"
      >
        Sign up
      </Link>
    </div>
  );
}
