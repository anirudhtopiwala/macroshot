import { useState, useCallback, useRef } from 'react';
import { ChevronRight, ChevronDown } from './icons';
import DialPicker from './DialPicker';
import SwipeActions from './SwipeActions';
import { MACRO_COLORS } from './MacroDisplay';
import type { Nutrition, FoodItem } from '../types';

interface Props {
  nutrition: Nutrition;
  onNutritionChange?: (nutrition: Nutrition) => void;
}

/** Hardcoded rgba tints for macro card backgrounds/borders (theme-stable). */
const MACRO_TINTS: Record<string, { bg: string; border: string; pillBg: string; editBg: string; editBorder: string }> = {
  protein: { bg: 'rgba(45,212,191,0.04)', border: 'rgba(45,212,191,0.08)', pillBg: 'rgba(45,212,191,0.13)', editBg: 'rgba(45,212,191,0.03)', editBorder: 'rgba(45,212,191,0.08)' },
  carbs:   { bg: 'rgba(249,115,22,0.04)', border: 'rgba(249,115,22,0.08)', pillBg: 'rgba(249,115,22,0.13)', editBg: 'rgba(249,115,22,0.03)', editBorder: 'rgba(249,115,22,0.08)' },
  fat:     { bg: 'rgba(34,197,94,0.04)',   border: 'rgba(34,197,94,0.08)',  pillBg: 'rgba(34,197,94,0.13)',  editBg: 'rgba(34,197,94,0.03)',  editBorder: 'rgba(34,197,94,0.08)' },
};

function MacroPill({ label, value, color, tintKey }: { label: string; value: number; color: string; tintKey: string }) {
  const tint = MACRO_TINTS[tintKey];
  return (
    <span className={`px-2 py-0.5 rounded-md text-[11px] font-semibold`} style={{ backgroundColor: tint?.pillBg, color }}>
      {label} {Math.round(value)}g
    </span>
  );
}

function EditableNumber({ value, onChange, color, tintKey, unit, label, className = '', max }: {
  value: number;
  onChange: (v: number) => void;
  color?: string;
  tintKey?: string;
  unit?: string;
  label?: string;
  className?: string;
  max?: number;
}) {
  const [showDial, setShowDial] = useState(false);
  const isCalories = unit === 'kcal';
  const step = isCalories ? 5 : 1;
  const dialMax = max || (isCalories ? 3000 : 500);
  const tint = tintKey ? MACRO_TINTS[tintKey] : null;

  return (
    <>
      <button
        onClick={() => setShowDial(true)}
        className={`w-full text-center py-2 rounded-xl transition-all active:scale-95 ${className}`}
        style={color ? {
          color,
          background: tint?.editBg || 'var(--input-bg)',
          border: `1px solid ${tint?.editBorder || 'var(--border-glass)'}`,
        } : {
          background: 'var(--input-bg)',
          border: '1px solid var(--border-glass)',
        }}
      >
        <span className="tabular-nums font-bold">{Math.round(value)}</span>
        {unit && <span className="text-[11px] ml-1" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </button>
      {showDial && (
        <DialPicker
          value={value}
          onChange={onChange}
          onClose={() => setShowDial(false)}
          min={0}
          max={dialMax}
          step={step}
          label={label || unit || ''}
          unit={unit || ''}
          color={color || '#10b981'}
        />
      )}
    </>
  );
}

export default function NutritionTable({ nutrition, onNutritionChange }: Props) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  const editable = !!onNutritionChange;
  const nutritionRef = useRef(nutrition);
  nutritionRef.current = nutrition;

  const toggleExpand = (i: number) => {
    setExpandedIndex(expandedIndex === i ? null : i);
  };

  const updateItem = useCallback((index: number, updates: Partial<FoodItem>) => {
    if (!onNutritionChange) return;
    const current = nutritionRef.current;
    const newItems = [...current.items];
    const item = { ...newItems[index], ...updates };
    newItems[index] = item;

    const totals = newItems.reduce(
      (acc, it) => ({
        calories: acc.calories + it.calories,
        protein: acc.protein + it.protein,
        carbs: acc.carbs + it.carbs,
        fat: acc.fat + it.fat,
      }),
      { calories: 0, protein: 0, carbs: 0, fat: 0 }
    );

    onNutritionChange({ ...current, items: newItems, ...totals });
  }, [onNutritionChange]);

  const updateItemWeight = useCallback((index: number, newWeight: number) => {
    if (!onNutritionChange) return;
    const item = nutritionRef.current.items[index];
    if (item.weight_g && item.weight_g > 0) {
      const ratio = newWeight / item.weight_g;
      updateItem(index, {
        weight_g: newWeight,
        calories: item.calories * ratio,
        protein: item.protein * ratio,
        carbs: item.carbs * ratio,
        fat: item.fat * ratio,
      });
    } else {
      updateItem(index, { weight_g: newWeight });
    }
  }, [onNutritionChange, updateItem]);

  const updateTotal = useCallback((field: 'calories' | 'protein' | 'carbs' | 'fat', value: number) => {
    if (!onNutritionChange) return;
    onNutritionChange({ ...nutritionRef.current, [field]: value });
  }, [onNutritionChange]);

  const deleteItem = useCallback((index: number) => {
    if (!onNutritionChange) return;
    const current = nutritionRef.current;
    const newItems = current.items.filter((_, i) => i !== index);
    const totals = newItems.reduce(
      (acc, it) => ({
        calories: acc.calories + it.calories,
        protein: acc.protein + it.protein,
        carbs: acc.carbs + it.carbs,
        fat: acc.fat + it.fat,
      }),
      { calories: 0, protein: 0, carbs: 0, fat: 0 }
    );
    setExpandedIndex((prev) => {
      if (prev === null) return null;
      if (prev === index) return null;
      return prev > index ? prev - 1 : prev;
    });
    onNutritionChange({ ...current, items: newItems, ...totals });
  }, [onNutritionChange]);

  return (
    <div className="glass-card p-4">
      {/* Title & Description */}
      {editable ? (
        <div className="space-y-2 mb-4">
          <input
            type="text"
            value={nutrition.item_name}
            onChange={(e) => onNutritionChange!({ ...nutritionRef.current, item_name: e.target.value })}
            placeholder="Meal name"
            className="glass-input w-full text-lg font-semibold"
          />
          <textarea
            value={nutrition.meal_description}
            onChange={(e) => onNutritionChange!({ ...nutritionRef.current, meal_description: e.target.value })}
            placeholder="Description (optional)"
            rows={3}
            className="glass-input w-full text-sm resize-y"
            style={{ color: 'var(--text-secondary)' }}
          />
        </div>
      ) : (
        <>
          <h3 className="font-semibold text-lg mb-1" style={{ wordBreak: 'break-word' }}>{nutrition.item_name}</h3>
          {nutrition.meal_description && (
            <p className="text-sm mb-3" style={{ color: 'var(--text-muted)', wordBreak: 'break-word' }}>{nutrition.meal_description}</p>
          )}
        </>
      )}

      {/* Item rows */}
      {nutrition.items.length > 0 && (
        <div className="space-y-1.5 mb-4">
          {nutrition.items.map((item, i) => {
            const isExpanded = expandedIndex === i;

            const collapsedRow = (
              <button
                onClick={() => toggleExpand(i)}
                className="w-full flex items-center gap-2 text-sm rounded-xl px-3 py-2.5 transition-colors text-left"
                style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium truncate">{item.name}</span>
                    {item.weight_g && (
                      <span className="text-[11px] px-1.5 py-0.5 rounded-md" style={{ background: 'var(--track-bg)', color: 'var(--text-muted)' }}>
                        {Math.round(item.weight_g)}g
                      </span>
                    )}
                  </div>
                  <div className="flex gap-1.5 mt-1">
                    <MacroPill label="P" value={item.protein} color={MACRO_COLORS.protein} tintKey="protein" />
                    <MacroPill label="C" value={item.carbs} color={MACRO_COLORS.carbs} tintKey="carbs" />
                    <MacroPill label="F" value={item.fat} color={MACRO_COLORS.fat} tintKey="fat" />
                  </div>
                </div>
                <span className="text-sm font-semibold shrink-0" style={{ color: 'var(--color-calories)' }}>{Math.round(item.calories)}</span>
                {isExpanded ? (
                  <ChevronDown className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
                ) : (
                  <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
                )}
              </button>
            );

            return (
              <div key={i}>
                {editable ? (
                  <SwipeActions onDelete={() => deleteItem(i)}>{collapsedRow}</SwipeActions>
                ) : (
                  collapsedRow
                )}

                {/* Expanded edit view */}
                {isExpanded && (
                  <div className="mt-1.5 p-3 rounded-xl space-y-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
                    {/* Calories */}
                    <div className="rounded-xl p-3 text-center" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.12)' }}>
                      {editable ? (
                        <EditableNumber
                          value={item.calories}
                          onChange={(v) => updateItem(i, { calories: v })}
                          color="#10b981"
                          unit="kcal"
                          label="Calories"
                          className="text-2xl font-bold"
                        />
                      ) : (
                        <span className="text-2xl font-bold" style={{ color: 'var(--color-calories)' }}>{Math.round(item.calories)}</span>
                      )}
                      <p className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>Calories</p>
                    </div>

                    {/* Macro cards */}
                    <div className="grid grid-cols-3 gap-2">
                      {([
                        { key: 'protein' as const, label: 'Protein', color: MACRO_COLORS.protein },
                        { key: 'carbs' as const, label: 'Carbs', color: MACRO_COLORS.carbs },
                        { key: 'fat' as const, label: 'Fat', color: MACRO_COLORS.fat },
                      ]).map((m) => (
                        <div key={m.key} className="rounded-xl p-3 flex flex-col items-center" style={{ background: MACRO_TINTS[m.key].bg, border: `1px solid ${MACRO_TINTS[m.key].border}` }}>
                          {editable ? (
                            <EditableNumber
                              value={item[m.key]}
                              onChange={(v) => updateItem(i, { [m.key]: v })}
                              color={m.color}
                              tintKey={m.key}
                              label={m.label}
                              unit="g"
                              className="text-xl font-bold"
                            />
                          ) : (
                            <span className="text-xl font-bold" style={{ color: m.color }}>
                              {Math.round(item[m.key])}
                            </span>
                          )}
                          <span className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>{m.label}</span>
                        </div>
                      ))}
                    </div>

                    {/* Weight input (editable mode) */}
                    {editable && (
                      <div className="rounded-xl p-3" style={{ background: 'rgba(148,163,184,0.06)', border: '1px solid rgba(148,163,184,0.1)' }}>
                        <label className="text-[11px] block mb-1.5 font-medium" style={{ color: 'var(--text-muted)' }}>Weight</label>
                        <EditableNumber
                          value={item.weight_g || 0}
                          onChange={(v) => updateItemWeight(i, v)}
                          unit="g"
                          label="Weight"
                          max={2000}
                          className="text-sm"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Overall totals */}
      <div className="pt-3 border-t" style={{ borderColor: 'var(--border-glass)' }}>
        <div className="text-center mb-3">
          {editable ? (
            <div className="max-w-[140px] mx-auto">
              <EditableNumber
                value={nutrition.calories}
                onChange={(v) => updateTotal('calories', v)}
                color="#10b981"
                unit="kcal"
                label="Total Calories"
                className="text-3xl font-bold"
              />
            </div>
          ) : (
            <>
              <span className="text-3xl font-bold" style={{ color: 'var(--color-calories)' }}>{Math.round(nutrition.calories)}</span>
              <span className="text-sm ml-1" style={{ color: 'var(--text-muted)' }}>cal</span>
            </>
          )}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {([
            { key: 'protein' as const, label: 'Protein', color: MACRO_COLORS.protein },
            { key: 'carbs' as const, label: 'Carbs', color: MACRO_COLORS.carbs },
            { key: 'fat' as const, label: 'Fat', color: MACRO_COLORS.fat },
          ]).map((m) => (
            <div key={m.label} className="rounded-xl py-3 flex flex-col items-center" style={{ background: MACRO_TINTS[m.key].bg, border: `1px solid ${MACRO_TINTS[m.key].border}` }}>
              {editable ? (
                <EditableNumber
                  value={nutrition[m.key]}
                  onChange={(v) => updateTotal(m.key, v)}
                  color={m.color}
                  tintKey={m.key}
                  unit="g"
                  label={`Total ${m.label}`}
                  className="text-2xl font-bold"
                />
              ) : (
                <span className="text-2xl font-bold" style={{ color: m.color }}>
                  {Math.round(nutrition[m.key])}
                </span>
              )}
              <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{m.label}</span>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}
