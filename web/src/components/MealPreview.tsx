import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { MealTypeIcon } from './icons';
import MacroDisplay, { MACRO_COLORS } from './MacroDisplay';
import ProgressiveImage from './ProgressiveImage';
import type { Meal } from '../types';
import type { RecentMeal } from '../api/meals';

interface FoodItem {
  name: string;
  calories?: number;
  protein?: number;
  carbs?: number;
  fat?: number;
  quantity?: string;
}

type PreviewMeal = Meal | (RecentMeal & { items_json?: string; image_path?: string });

interface Props {
  meal: PreviewMeal;
  onClose: () => void;
}

export default function MealPreview({ meal, onClose }: Props) {
  useOverlayHistory(onClose);
  const [visible, setVisible] = useState(false);
  const imagePath = meal.image_path;

  // Parse items
  let items: FoodItem[] = [];
  try {
    const parsed = JSON.parse(meal.items_json || '[]');
    if (Array.isArray(parsed)) items = parsed;
  } catch { /* */ }

  const time = meal.logged_at.split(' ')[1]?.slice(0, 5) || '';
  const mealType = (meal.meal_type || '').charAt(0).toUpperCase() + (meal.meal_type || '').slice(1);
  const subtitle = [mealType, time].filter(Boolean).join(' · ');

  useEffect(() => {
    // Prevent text selection while preview is open
    document.body.style.userSelect = 'none';
    document.body.style.webkitUserSelect = 'none';
    requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true)));
    return () => {
      document.body.style.userSelect = '';
      document.body.style.webkitUserSelect = '';
    };
  }, []);

  const handleClose = () => {
    setVisible(false);
    setTimeout(onClose, 200);
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center px-4"
      onClick={handleClose}
      style={{
        background: 'rgba(0,0,0,0.65)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        opacity: visible ? 1 : 0,
        transition: 'opacity 200ms ease',
        userSelect: 'none',
        WebkitUserSelect: 'none',
      }}
    >
      <div
        className="w-full max-w-sm rounded-3xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-glass)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05) inset',
          transform: visible ? 'scale(1) translateY(0)' : 'scale(0.92) translateY(40px)',
          opacity: visible ? 1 : 0,
          transition: 'transform 300ms cubic-bezier(0.32, 0.72, 0, 1), opacity 200ms ease',
        }}
      >
        {/* Image */}
        {imagePath ? (
          <div className="relative" style={{ height: 220 }}>
            <ProgressiveImage
              imagePath={imagePath}
              alt={meal.item_name}
              width={400}
              height={220}
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0" style={{ background: 'linear-gradient(transparent 40%, rgba(0,0,0,0.7))' }} />
            {/* Title overlay on image */}
            <div className="absolute bottom-0 left-0 right-0 p-4">
              <h3 className="text-lg font-bold text-white" style={{ textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>{meal.item_name}</h3>
              {subtitle && <p className="text-xs text-white/70 mt-0.5">{subtitle}</p>}
            </div>
          </div>
        ) : (
          <div className="p-4 pb-2">
            <div className="flex items-center gap-3 mb-1">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
                <MealTypeIcon type={meal.meal_type} className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>{meal.item_name}</h3>
                {subtitle && <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{subtitle}</p>}
              </div>
            </div>
          </div>
        )}

        {/* Content */}
        <div className="p-4 pt-3 space-y-3">
          {/* Description (only if no image, since image has title overlay) */}
          {imagePath && meal.meal_description && meal.meal_description !== meal.item_name && (
            <p className="text-xs line-clamp-2" style={{ color: 'var(--text-secondary)' }}>{meal.meal_description}</p>
          )}
          {!imagePath && meal.meal_description && meal.meal_description !== meal.item_name && (
            <p className="text-xs line-clamp-2" style={{ color: 'var(--text-secondary)' }}>{meal.meal_description}</p>
          )}

          {/* Macro bar */}
          <div className="rounded-2xl p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className="text-2xl font-black tabular-nums" style={{ color: 'var(--color-calories)' }}>{Math.round(meal.calories)}</span>
                <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>kcal</span>
              </div>
              <MacroDisplay protein={meal.protein} carbs={meal.carbs} fat={meal.fat} />
            </div>
            {/* Visual macro bar */}
            {meal.calories > 0 && (() => {
              const total = (meal.protein * 4) + (meal.carbs * 4) + (meal.fat * 9);
              if (total <= 0) return null;
              const pPct = (meal.protein * 4 / total) * 100;
              const cPct = (meal.carbs * 4 / total) * 100;
              const fPct = (meal.fat * 9 / total) * 100;
              return (
                <div className="flex h-2 rounded-full overflow-hidden gap-0.5">
                  <div style={{ width: `${pPct}%`, background: MACRO_COLORS.protein, borderRadius: '9999px' }} />
                  <div style={{ width: `${cPct}%`, background: MACRO_COLORS.carbs, borderRadius: '9999px' }} />
                  <div style={{ width: `${fPct}%`, background: MACRO_COLORS.fat, borderRadius: '9999px' }} />
                </div>
              );
            })()}
          </div>

          {/* Items breakdown */}
          {items.length > 1 && (
            <div className="space-y-1.5">
              <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Items</p>
              {items.slice(0, 6).map((item, i) => (
                <div key={i} className="flex items-center justify-between text-xs py-0.5">
                  <span className="truncate flex-1 mr-2" style={{ color: 'var(--text-secondary)' }}>
                    {item.quantity ? `${item.quantity} ` : ''}{item.name}
                  </span>
                  <span className="tabular-nums font-semibold shrink-0" style={{ color: 'var(--text-muted)' }}>
                    {item.calories ? `${Math.round(item.calories)} cal` : ''}
                  </span>
                </div>
              ))}
              {items.length > 6 && (
                <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>+{items.length - 6} more</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
