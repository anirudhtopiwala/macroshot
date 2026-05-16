import { memo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, MealTypeIcon } from './icons';
import MacroDisplay from './MacroDisplay';
import ProgressiveImage from './ProgressiveImage';
import MealPreview from './MealPreview';
import { hapticLight } from '../utils/haptics';
import type { Meal } from '../types';

interface Props {
  meal: Meal;
}

export default memo(function MealCard({ meal }: Props) {
  const navigate = useNavigate();
  const time = meal.logged_at.split(' ')[1] || '';
  const hasImage = !!meal.image_path;
  const [showPreview, setShowPreview] = useState(false);

  // Long-press state stored in dataset to avoid re-renders and useCallback deps
  // This matches the exact pattern that worked for saved meals long-press in LogMeal.tsx
  return (
    <>
      <div
        role="link"
        tabIndex={0}
        className="glass-card-hover flex items-center gap-3 p-3 !rounded-2xl cursor-pointer select-none"
        style={{ WebkitTouchCallout: 'none' } as React.CSSProperties}
        onTouchStart={(e) => {
          const el = e.currentTarget;
          const sx = e.touches[0].clientX;
          const sy = e.touches[0].clientY;
          el.dataset.lpFired = '';
          const timer = setTimeout(() => {
            el.dataset.lpFired = '1';
            hapticLight();
            setShowPreview(true);
          }, 400);
          el.dataset.lpTimer = String(timer);
          // Cancel on move
          const onMove = (ev: TouchEvent) => {
            if (Math.abs(ev.touches[0].clientX - sx) > 10 || Math.abs(ev.touches[0].clientY - sy) > 10) {
              clearTimeout(timer);
              delete el.dataset.lpTimer;
            }
          };
          const onEnd = () => {
            clearTimeout(timer);
            delete el.dataset.lpTimer;
            document.removeEventListener('touchmove', onMove);
          };
          document.addEventListener('touchmove', onMove, { passive: true });
          document.addEventListener('touchend', onEnd, { once: true });
        }}
        onClick={(e) => {
          const el = e.currentTarget as HTMLElement;
          if (el.dataset.lpFired === '1') {
            e.preventDefault();
            e.stopPropagation();
            el.dataset.lpFired = '';
            return;
          }
          navigate(`/meals/${meal.id}`);
        }}
      >
        {hasImage ? (
          <ProgressiveImage
            imagePath={meal.image_path}
            alt={meal.item_name}
            width={80}
            height={80}
            className="rounded-2xl shrink-0"
            style={{ boxShadow: '0 0 0 1px var(--border-glass)' }}
            thumbOnly
          />
        ) : (
          <div className="w-20 h-20 rounded-2xl flex items-center justify-center shrink-0" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)', color: 'var(--text-muted)' }}>
            <MealTypeIcon type={meal.meal_type} className="w-7 h-7" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm truncate flex items-center gap-1.5">
            <span className="truncate">{meal.item_name}</span>
            {meal.score != null && (
              <span
                className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 tabular-nums"
                style={{ background: 'rgba(16,185,129,0.12)', color: '#10b981' }}
                title="Semantic match score (cosine similarity)"
              >
                {meal.score.toFixed(2)}
              </span>
            )}
          </div>
          <div className="text-[11px] mt-0.5 capitalize" style={{ color: 'var(--text-muted)' }}>{time} · {meal.meal_type}</div>
          <MacroDisplay protein={meal.protein} carbs={meal.carbs} fat={meal.fat} compact className="mt-1" />
        </div>
        <div className="text-right shrink-0 flex items-center gap-2">
          <div>
            <div className="text-sm font-bold tabular-nums" style={{ color: 'var(--color-calories)' }}>{Math.round(meal.calories)}</div>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>kcal</div>
          </div>
          <ChevronRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
        </div>
      </div>

      {showPreview && (
        <MealPreview meal={meal} onClose={() => setShowPreview(false)} />
      )}
    </>
  );
});
