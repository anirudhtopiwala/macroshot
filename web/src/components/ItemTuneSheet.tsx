import Modal from './Modal';
import { Plus, Minus, X, Sparkles, Trash2 } from './icons';
import { MACRO_COLORS } from './MacroDisplay';
import { hapticLight } from '../utils/haptics';
import type { FoodItem } from '../types';
import type { ItemTuneField, ItemTuneDirection } from '../utils/itemTune';

interface Props {
  item: FoodItem | null;
  disabled?: boolean;
  onTune: (field: ItemTuneField, direction: ItemTuneDirection) => void | Promise<void>;
  onDelete?: () => void;
  onClose: () => void;
}

interface Row {
  field: ItemTuneField;
  label: string;
  unit: string;
  color: string;
}

const ROWS: Row[] = [
  { field: 'calories', label: 'Calories', unit: 'kcal', color: 'var(--color-calories)' },
  { field: 'protein', label: 'Protein', unit: 'g', color: MACRO_COLORS.protein },
  { field: 'carbs', label: 'Carbs', unit: 'g', color: MACRO_COLORS.carbs },
  { field: 'fat', label: 'Fat', unit: 'g', color: MACRO_COLORS.fat },
];

export default function ItemTuneSheet({ item, disabled, onTune, onDelete, onClose }: Props) {
  if (!item) return null;

  // Tap: fire the AI tune call (no await — fire-and-forget) and close the
  // popup immediately so the user lands back in the chat where the
  // assistant typing indicator + reply will appear. The chip in chat is
  // already hidden while disabled, so spam-tap protection comes from there.
  const handleTap = (field: ItemTuneField, dir: ItemTuneDirection) => {
    if (disabled) return;
    hapticLight();
    void onTune(field, dir);
    onClose();
  };

  return (
    <Modal open={!!item} onClose={onClose} position="center">
      <div
        className="p-5 space-y-4"
        style={{ paddingBottom: 'calc(1.25rem + env(safe-area-inset-bottom, 0px))' }}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              <Sparkles className="w-3.5 h-3.5" style={{ color: '#a855f7' }} />
              <span className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: '#a855f7' }}>
                Tune with AI
              </span>
            </div>
            <h3 className="font-semibold text-base truncate" style={{ wordBreak: 'break-word' }}>
              {item.name}
            </h3>
            {item.weight_g ? (
              <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                {Math.round(item.weight_g)}g
              </span>
            ) : null}
          </div>
          <button
            onClick={onClose}
            className="p-2 -mr-2 -mt-1 rounded-xl shrink-0"
            style={{ color: 'var(--text-muted)' }}
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Rows */}
        <div className="space-y-2">
          {ROWS.map((row) => {
            const value = item[row.field];
            return (
              <div
                key={row.field}
                className="flex items-center gap-3 rounded-xl px-3 py-2.5"
                style={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-glass)',
                  backdropFilter: 'blur(12px)',
                  WebkitBackdropFilter: 'blur(12px)',
                }}
              >
                <div className="w-16 shrink-0">
                  <div className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
                    {row.label}
                  </div>
                  <div className="text-base font-bold tabular-nums" style={{ color: row.color }}>
                    {Math.round(value)}
                    <span className="text-[10px] ml-0.5" style={{ color: 'var(--text-muted)' }}>
                      {row.unit}
                    </span>
                  </div>
                </div>
                <div className="flex-1" />
                <button
                  onClick={() => handleTap(row.field, 'down')}
                  disabled={disabled}
                  className="w-12 h-12 rounded-full flex items-center justify-center transition-all active:scale-90 disabled:opacity-40"
                  style={{
                    background: `color-mix(in srgb, ${row.color} 12%, transparent)`,
                    border: `1px solid color-mix(in srgb, ${row.color} 30%, transparent)`,
                    color: row.color,
                    boxShadow: `0 4px 12px color-mix(in srgb, ${row.color} 18%, transparent)`,
                  }}
                  aria-label={`${row.label} too high`}
                >
                  <Minus className="w-5 h-5" strokeWidth={3} />
                </button>
                <button
                  onClick={() => handleTap(row.field, 'up')}
                  disabled={disabled}
                  className="w-12 h-12 rounded-full flex items-center justify-center transition-all active:scale-90 disabled:opacity-40"
                  style={{
                    background: `color-mix(in srgb, ${row.color} 12%, transparent)`,
                    border: `1px solid color-mix(in srgb, ${row.color} 30%, transparent)`,
                    color: row.color,
                    boxShadow: `0 4px 12px color-mix(in srgb, ${row.color} 18%, transparent)`,
                  }}
                  aria-label={`${row.label} too low`}
                >
                  <Plus className="w-5 h-5" strokeWidth={3} />
                </button>
              </div>
            );
          })}
        </div>

        <p className="text-[11px] text-center" style={{ color: 'var(--text-muted)' }}>
          Tap − if too high, + if too low. AI re-estimates this item.
        </p>

        {onDelete && (
          <button
            onClick={() => {
              if (disabled) return;
              onDelete();
              onClose();
            }}
            disabled={disabled}
            className="w-full py-2.5 rounded-xl text-sm font-medium flex items-center justify-center gap-1.5 transition-all active:scale-[0.98] disabled:opacity-40"
            style={{
              background: 'rgba(239,68,68,0.08)',
              border: '1px solid rgba(239,68,68,0.18)',
              color: '#ef4444',
            }}
          >
            <Trash2 className="w-4 h-4" />
            Remove item
          </button>
        )}
      </div>
    </Modal>
  );
}
