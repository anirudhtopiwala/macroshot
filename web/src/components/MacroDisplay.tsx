import { memo } from 'react';

/**
 * CSS variable names for macro colors.
 * These resolve to theme-aware values defined in index.css:
 *   Dark:  protein=#2dd4bf, carbs=#f97316, fat=#22c55e
 *   Light: protein=#0d9488, carbs=#ea580c, fat=#16a34a
 */
export const MACRO_COLORS = {
  protein: 'var(--color-protein)',
  carbs: 'var(--color-carbs)',
  fat: 'var(--color-fat)',
} as const;

interface MacroDisplayProps {
  protein: number;
  carbs: number;
  fat: number;
  /** Compact: "12p 30c 8f". Full: "12g P  30g C  8g F" */
  compact?: boolean;
  className?: string;
}

/**
 * Consistent macro value display with theme-aware colors.
 * Use `compact` for inline/tight layouts (MealCard, day summaries).
 * Use full (default) for card-level displays (SavedMeals, detail views).
 */
export default memo(function MacroDisplay({
  protein,
  carbs,
  fat,
  compact = false,
  className = '',
}: MacroDisplayProps) {
  const p = Math.round(protein);
  const c = Math.round(carbs);
  const f = Math.round(fat);

  if (compact) {
    return (
      <span className={`inline-flex gap-1.5 tabular-nums font-medium text-[11px] ${className}`}>
        <span style={{ color: MACRO_COLORS.protein }}>{p}p</span>
        <span style={{ color: MACRO_COLORS.carbs }}>{c}c</span>
        <span style={{ color: MACRO_COLORS.fat }}>{f}f</span>
      </span>
    );
  }

  return (
    <span className={`inline-flex gap-3 tabular-nums font-medium text-xs ${className}`}>
      <span style={{ color: MACRO_COLORS.protein }}>{p}g P</span>
      <span style={{ color: MACRO_COLORS.carbs }}>{c}g C</span>
      <span style={{ color: MACRO_COLORS.fat }}>{f}g F</span>
    </span>
  );
});
