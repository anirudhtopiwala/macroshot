import type { FoodItem, Nutrition } from '../types';

export type ItemTuneField = 'calories' | 'protein' | 'carbs' | 'fat';
export type ItemTuneDirection = 'up' | 'down';

const FIELD_LABEL: Record<ItemTuneField, string> = {
  calories: 'calorie estimate',
  protein: 'protein',
  carbs: 'carbs',
  fat: 'fat',
};

const FIELD_UNIT: Record<ItemTuneField, string> = {
  calories: 'kcal',
  protein: 'g',
  carbs: 'g',
  fat: 'g',
};

export function buildItemTunePrompt(
  item: FoodItem,
  field: ItemTuneField,
  direction: ItemTuneDirection,
): { detailed: string; display: string } {
  const current = Math.round(item[field]);
  const label = FIELD_LABEL[field];
  const unit = FIELD_UNIT[field];
  const verdict = direction === 'up' ? 'too low' : 'too high';
  const detailed =
    `For the item "${item.name}" the ${label} (currently ${current}${unit}) looks ${verdict}. ` +
    `Re-estimate the macros for ONLY that item, keep every other item exactly as it is, ` +
    `and return the full updated meal JSON.`;
  const display = `${item.name}: ${label} ${verdict}`;
  return { detailed, display };
}

/**
 * Diff old vs new items by name, returning names the user removed.
 * Used by page editors to fire mealsApi.itemRemoved so the session's
 * Gemini conversation learns about the deletion.
 */
export function findRemovedItemNames(
  prev: Nutrition | null | undefined,
  next: Nutrition | null | undefined,
): string[] {
  if (!prev || !next) return [];
  const nextNames = new Set(next.items.map((it) => it.name));
  return prev.items
    .map((it) => it.name)
    .filter((name) => !nextNames.has(name));
}

/**
 * Merge a Gemini correction response back into prev nutrition while honoring
 * the "only re-estimate this item" contract. We ignore changes to siblings
 * even if Gemini drifted on them, and we deliberately do NOT recompute the
 * meal-level totals at the top of the card - those are the user's manual
 * adjustment dial and should not be overwritten by per-item AI tunes.
 *
 * Match strategy: prefer the item at `index` in the response (Gemini almost
 * always preserves order); fall back to a name match for safety.
 */
export function applyItemTune(
  prev: Nutrition,
  index: number,
  response: Nutrition,
): Nutrition {
  const target = prev.items[index];
  if (!target) return prev;

  const candidate =
    response.items[index]?.name === target.name
      ? response.items[index]
      : response.items.find((it) => it.name === target.name) ?? response.items[index];
  if (!candidate) return prev;

  const merged: FoodItem = {
    ...target,
    calories: candidate.calories,
    protein: candidate.protein,
    carbs: candidate.carbs,
    fat: candidate.fat,
    weight_g: candidate.weight_g ?? target.weight_g,
  };

  const newItems = prev.items.map((it, i) => (i === index ? merged : it));
  return { ...prev, items: newItems };
}
