import type { Nutrition } from '../types';

/** Tagged union for chat-side correction shortcuts.
 *
 *  - `send` actions are pre-canned messages that go straight to Gemini
 *    when tapped (handled by MealEditor.handleQuickAction).
 *  - `item_fix` actions are per-item edit chips. Tapping one bubbles up
 *    via CorrectionChat's onItemFixRequest prop and the parent opens the
 *    NutritionTable's +/- bottom sheet for that item.
 */
export type QuickAction =
  | { kind: 'send'; label: string; displayText: string; detailedText: string }
  | { kind: 'item_fix'; label: string; itemIndex: number; itemName: string };

function itemSummary(nutrition: Nutrition): string {
  const items = nutrition.items.slice(0, 5);
  const parts = items.map((it) => {
    const w = it.weight_g ? `, ${Math.round(it.weight_g)}g` : '';
    return `${it.name} (${Math.round(it.calories)} kcal${w})`;
  });
  if (nutrition.items.length > 5) parts.push(`... and ${nutrition.items.length - 5} more`);
  return parts.join(', ');
}

/** Truncate item names so chips don't overflow on small screens. */
function chipLabel(name: string, max = 16): string {
  if (name.length <= max) return name;
  return name.slice(0, max - 1).trimEnd() + '…';
}

/** Cap on per-item Fix chips - long meals (>6 items) would otherwise overflow
 *  the chip row on phones. Users can still tap the item bubble at the top. */
const MAX_ITEM_FIX_CHIPS = 6;

export function generateQuickActions(nutrition: Nutrition): QuickAction[] {
  const cal = Math.round(nutrition.calories);
  const name = nutrition.item_name || 'this meal';
  const summary = itemSummary(nutrition);
  const hasItems = nutrition.items.length > 0;

  const actions: QuickAction[] = [
    {
      kind: 'send',
      label: 'Macros too high',
      displayText: 'Macros seem too high',
      detailedText: hasItems
        ? `The macros (${cal} kcal total) look too high for ${name}. Items: ${summary}. Per-100g values are from reference databases and should stay, so please reduce the portion weights to match what's actually on the plate and recalculate all macros proportionally.`
        : `The macros (${cal} kcal total) look too high for ${name}. Please reduce the portion sizes and recalculate.`,
    },
    {
      kind: 'send',
      label: 'Macros too low',
      displayText: 'Macros seem too low',
      detailedText: hasItems
        ? `The macros (${cal} kcal total) look too low for ${name}. Items: ${summary}. Per-100g values are from reference databases and should stay, so please increase the portion weights to match what's actually on the plate and recalculate all macros proportionally.`
        : `The macros (${cal} kcal total) look too low for ${name}. Please increase the portion sizes and recalculate.`,
    },
    {
      kind: 'send',
      label: 'Missing item',
      displayText: "There's a missing item",
      detailedText: hasItems
        ? `I think you missed something in this meal. Currently identified: ${summary}. There appears to be at least one more item. Please re-examine and add any missing foods.`
        : `I think you missed some items in this meal. Please re-examine and identify all foods present.`,
    },
  ];

  // Single-item misidentification
  if (nutrition.items.length === 1) {
    const item = nutrition.items[0];
    actions.push({
      kind: 'send',
      label: `Wrong item`,
      displayText: `That's not ${item.name}`,
      detailedText: `The identified food "${item.name}" is incorrect. It currently shows ${Math.round(item.calories)} kcal, ${Math.round(item.protein)}g protein, ${Math.round(item.carbs)}g carbs, ${Math.round(item.fat)}g fat${item.weight_g ? ` at ${Math.round(item.weight_g)}g` : ''}. Please re-identify this food and recalculate.`,
    });
  }

  // Per-item Fix chips - shown whenever there are items, so the per-item
  // edit affordance sits right above the chat input (no scroll-up needed).
  if (nutrition.items.length >= 1) {
    nutrition.items.slice(0, MAX_ITEM_FIX_CHIPS).forEach((item, index) => {
      actions.push({
        kind: 'item_fix',
        label: `Edit ${chipLabel(item.name)}`,
        itemIndex: index,
        itemName: item.name,
      });
    });
  }

  return actions;
}
