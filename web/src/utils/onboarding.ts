/**
 * Tracks meal log count to gate onboarding tooltips.
 * Tooltips only show during the user's first few meals.
 */

const KEY = 'meal_log_count';

export function incrementMealCount(): void {
  const count = parseInt(localStorage.getItem(KEY) || '0', 10);
  localStorage.setItem(KEY, String(count + 1));
}

/** True if the user has logged fewer than 5 meals (still in onboarding period). */
export function isEarlyUser(): boolean {
  const count = parseInt(localStorage.getItem(KEY) || '0', 10);
  // Also check has_logged_meal - if they've logged meals but have no count,
  // they're an existing user who predates the counter.
  if (count === 0 && localStorage.getItem('has_logged_meal')) return false;
  return count < 5;
}

/** True if the user has logged at least one meal (current or pre-counter). */
export function hasLoggedAnyMeal(): boolean {
  const count = parseInt(localStorage.getItem(KEY) || '0', 10);
  if (count > 0) return true;
  return !!localStorage.getItem('has_logged_meal');
}

const FAB_CLICKED_KEY = 'fab_clicked';

/** True if the user has ever opened the + FAB menu. */
export function hasClickedFab(): boolean {
  return !!localStorage.getItem(FAB_CLICKED_KEY);
}

export function markFabClicked(): void {
  try { localStorage.setItem(FAB_CLICKED_KEY, '1'); } catch { /* ignore */ }
}
