import { useRef, useMemo, useCallback, useState } from 'react';
import NutritionTable from './NutritionTable';
import CorrectionChat from './CorrectionChat';
import ItemTuneSheet from './ItemTuneSheet';
import Tooltip from './Tooltip';
import type { Nutrition, FoodItem } from '../types';
import type { ItemTuneField, ItemTuneDirection } from '../utils/itemTune';
import { generateQuickActions, type QuickAction } from '../utils/quickActions';
import { hapticLight } from '../utils/haptics';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  macrosUpdated?: boolean;
}

interface CorrectionLimitInfo {
  used: number;
  limit: number;
  isPremium: boolean;
}

interface Props {
  /** Current nutrition data to display/edit */
  nutrition: Nutrition;
  /** Called when user manually edits macros via dial pickers */
  onNutritionChange?: (nutrition: Nutrition) => void;

  // AI correction props (all optional - omit to disable AI)
  /** Chat message history */
  aiMessages?: ChatMessage[];
  /** Gemini follow-up questions to show near the chat input */
  aiQuestions?: string[];
  /** Called when user sends an AI correction message. displayText is the short label shown in the chat bubble. */
  onAiSend?: (text: string, displayText?: string) => void;
  /** Whether an AI correction is in-flight */
  aiDisabled?: boolean;
  /** Custom placeholder text for the AI input */
  aiPlaceholder?: string;
  /** Purple AI-themed styling for the chat input */
  aiTheme?: boolean;
  /** Reprocess callback - re-runs analysis with original image/text */
  onReprocess?: () => void;
  /** Correction limit info for free users */
  correctionLimit?: CorrectionLimitInfo;
  /** Per-item AI tune handler. When provided, item rows + chat-side
   *  per-item chips both open the +/- bottom sheet. */
  onItemTune?: (index: number, field: ItemTuneField, direction: ItemTuneDirection) => void | Promise<void>;
  /** Disables the per-item tune chips while a tune call is in flight. */
  itemTuneDisabled?: boolean;
}

function sumTotals(items: FoodItem[]) {
  return items.reduce(
    (acc, it) => ({
      calories: acc.calories + (it.calories || 0),
      protein: acc.protein + (it.protein || 0),
      carbs: acc.carbs + (it.carbs || 0),
      fat: acc.fat + (it.fat || 0),
    }),
    { calories: 0, protein: 0, carbs: 0, fat: 0 },
  );
}

/**
 * Unified meal editing component: manual dial pickers + optional AI corrections.
 * Used by LogMeal (pre-accept), MealDetail (post-accept), and SavedMeals (aliases).
 */
export default function MealEditor({
  nutrition,
  onNutritionChange,
  aiMessages,
  aiQuestions,
  onAiSend,
  aiDisabled,
  aiPlaceholder,
  aiTheme,
  onReprocess,
  correctionLimit,
  onItemTune,
  itemTuneDisabled,
}: Props) {
  const correctionRef = useRef<HTMLDivElement>(null);
  const nutritionRef = useRef<HTMLDivElement>(null);
  const quickActions = useMemo(() => generateQuickActions(nutrition), [nutrition]);
  // The +/- popup is owned here so both the item bubble (top of card) and
  // the per-item chips inside CorrectionChat (just above the input) drive
  // the same sheet without re-implementing state in two places.
  const [tuneIndex, setTuneIndex] = useState<number | null>(null);
  const tuneEnabled = !!onItemTune;

  const handleQuickAction = (action: QuickAction) => {
    if (action.kind === 'send') {
      onAiSend?.(action.detailedText, action.displayText);
    }
    // item_fix actions are handled via onItemFixRequest below.
  };

  const handleDeleteItem = useCallback((index: number) => {
    if (!onNutritionChange) return;
    const newItems = nutrition.items.filter((_, i) => i !== index);
    onNutritionChange({ ...nutrition, items: newItems, ...sumTotals(newItems) });
  }, [nutrition, onNutritionChange]);

  // Invoked from the "Macros updated" pill inside CorrectionChat - scrolls
  // the NutritionTable back into view and fires a light haptic so the
  // tap feels responsive on mobile.
  const handleScrollToMacros = useCallback(() => {
    hapticLight();
    nutritionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  return (
    <>
      <div ref={nutritionRef}>
        <NutritionTable
          nutrition={nutrition}
          onNutritionChange={onNutritionChange}
        />
      </div>
      {onAiSend && (
        <div ref={correctionRef}>
          <CorrectionChat
            messages={aiMessages || []}
            questions={aiQuestions}
            quickActions={quickActions}
            onSend={(text) => onAiSend(text)}
            onQuickAction={handleQuickAction}
            onItemFixRequest={tuneEnabled ? setTuneIndex : undefined}
            onReprocess={onReprocess}
            disabled={aiDisabled}
            placeholder={aiPlaceholder}
            aiTheme={aiTheme}
            correctionLimit={correctionLimit}
            onScrollToMacros={handleScrollToMacros}
          />
          <Tooltip id="correction_chat" show={true} targetRef={correctionRef} position="top">
            Not accurate? Type a correction or tap a suggestion above.
          </Tooltip>
        </div>
      )}

      {tuneEnabled && (
        <ItemTuneSheet
          item={tuneIndex !== null ? nutrition.items[tuneIndex] ?? null : null}
          disabled={itemTuneDisabled}
          onTune={(field, dir) => onItemTune!(tuneIndex!, field, dir)}
          onDelete={onNutritionChange ? () => handleDeleteItem(tuneIndex!) : undefined}
          onClose={() => setTuneIndex(null)}
        />
      )}
    </>
  );
}
