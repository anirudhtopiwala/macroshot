import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Button from '../components/Button';
import MacroDisplay from '../components/MacroDisplay';
import EmptyState from '../components/EmptyState';
import { listGuestMeals, deleteGuestMeal, type GuestMeal } from '../utils/guestStorage';
import { formatLocalDate } from '../utils/date';

/**
 * Lightweight dashboard for visitors browsing without an account.
 *
 * Reads everything from IndexedDB — no backend calls, no quota,
 * no 401 surface. Filters to "today" so the experience mirrors the
 * real Dashboard's top card.
 */
export default function GuestDashboard() {
  const [meals, setMeals] = useState<GuestMeal[]>([]);

  const refresh = () =>
    listGuestMeals().then((all) => {
      const today = formatLocalDate();
      setMeals(all.filter((m) => m.loggedAt.startsWith(today)));
    });

  useEffect(() => {
    refresh();
    const onAdded = () => { refresh(); };
    window.addEventListener('guest-meal-added', onAdded);
    return () => window.removeEventListener('guest-meal-added', onAdded);
  }, []);

  const totals = meals.reduce(
    (acc, m) => ({
      calories: acc.calories + m.nutrition.calories,
      protein: acc.protein + m.nutrition.protein,
      carbs: acc.carbs + m.nutrition.carbs,
      fat: acc.fat + m.nutrition.fat,
    }),
    { calories: 0, protein: 0, carbs: 0, fat: 0 },
  );

  return (
    <div className="space-y-6">
      <div
        className="rounded-2xl p-5"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
      >
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>
            Today
          </h2>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {meals.length} {meals.length === 1 ? 'meal' : 'meals'}
          </span>
        </div>
        <div className="text-4xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
          {Math.round(totals.calories)}
          <span className="text-base font-normal ml-2" style={{ color: 'var(--text-muted)' }}>
            kcal
          </span>
        </div>
        <MacroDisplay
          protein={totals.protein}
          carbs={totals.carbs}
          fat={totals.fat}
        />
        <div className="mt-5">
          <Link to="/log">
            <Button variant="primary" size="lg" className="w-full">
              Log a meal with AI
            </Button>
          </Link>
        </div>
      </div>

      {meals.length === 0 ? (
        <EmptyState
          icon="🍳"
          title="Try the AI analyzer"
          subtitle="Snap a photo or describe a meal. We'll estimate the macros. Sign up later to keep them."
          card
        />
      ) : (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold px-1" style={{ color: 'var(--text-secondary)' }}>
            Today's meals
          </h3>
          {meals.map((m) => (
            <div
              key={m.id}
              className="rounded-xl p-4"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {m.nutrition.item_name || 'Meal'}
                  </div>
                  {m.nutrition.meal_description && (
                    <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
                      {m.nutrition.meal_description}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => m.id != null && deleteGuestMeal(m.id).then(refresh)}
                  className="text-xs"
                  style={{ color: 'var(--text-muted)' }}
                  aria-label="Delete"
                >
                  ✕
                </button>
              </div>
              <div className="mt-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
                {Math.round(m.nutrition.calories)} kcal · P {Math.round(m.nutrition.protein)}g · C {Math.round(m.nutrition.carbs)}g · F {Math.round(m.nutrition.fat)}g
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
