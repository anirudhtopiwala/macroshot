import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft } from './icons';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { weightApi, type WeightEntry } from '../api/weight';
import WeightChart from './WeightChart';
import SwipeActions from './SwipeActions';
import BadgeCelebration from './BadgeCelebration';
import { useToast } from './Toast';
import { hapticLight, hapticSuccess } from '../utils/haptics';
import { clearCache } from '../utils/apiCache';
import { trackEvent } from '../utils/analytics';
import LoadingSpinner from './LoadingSpinner';
import Button from './Button';
import type { NewBadge } from '../types';

const KG_TO_LBS = 2.20462;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso: string): string {
  const [datePart, timePart = ''] = iso.split(/[T ]/);
  const [, mo, d] = datePart.split('-');
  const day = parseInt(d, 10);
  const month = MONTHS[parseInt(mo, 10) - 1] || mo;
  if (!timePart) return `${day} ${month}`;
  const [hStr, minStr] = timePart.split(':');
  let h = parseInt(hStr, 10);
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${day} ${month} · ${h}:${minStr} ${ampm}`;
}

interface Props {
  onClose: () => void;
}

export default function WeightTracker({ onClose }: Props) {
  const { toast } = useToast();
  const [entries, setEntries] = useState<WeightEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [value, setValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [unit, setUnit] = useState<'kg' | 'lbs'>(() =>
    (localStorage.getItem('weight_unit') as 'kg' | 'lbs') || 'kg'
  );
  const [newBadges, setNewBadges] = useState<NewBadge[]>([]);

  useOverlayHistory(onClose);

  const fetchHistory = useCallback(async () => {
    try {
      const data = await weightApi.history();
      setEntries(data.entries);
      // Pre-fill input with last weight for quick editing
      if (data.entries.length > 0) {
        const lastKg = data.entries[0].weight_kg;
        const curUnit = (localStorage.getItem('weight_unit') || 'kg') as 'kg' | 'lbs';
        const display = curUnit === 'lbs'
          ? (lastKg * KG_TO_LBS).toFixed(1) : lastKg.toFixed(1);
        setValue(display);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Persist unit + broadcast change + convert input value
  const switchUnit = (newUnit: 'kg' | 'lbs') => {
    if (newUnit === unit) return;
    hapticLight();
    // Convert current input value to new unit
    const num = parseFloat(value);
    if (num && num > 0) {
      if (newUnit === 'lbs') setValue((num * KG_TO_LBS).toFixed(1));
      else setValue((num / KG_TO_LBS).toFixed(1));
    }
    setUnit(newUnit);
    localStorage.setItem('weight_unit', newUnit);
    window.dispatchEvent(new CustomEvent('weight-unit-changed', { detail: newUnit }));
  };

  // Block body scroll while tracker is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Broadcast weight change so other components can refresh
  const broadcastWeightChange = () => {
    window.dispatchEvent(new CustomEvent('weight-changed'));
  };

  const toDisplay = (kg: number) => unit === 'lbs' ? (kg * KG_TO_LBS).toFixed(1) : kg.toFixed(1);
  const toKg = (val: number) => unit === 'lbs' ? val / KG_TO_LBS : val;

  // Compute delta from previous entry
  const getDelta = (idx: number): string | null => {
    if (idx >= entries.length - 1) return null;
    const diff = entries[idx].weight_kg - entries[idx + 1].weight_kg;
    const displayDiff = unit === 'lbs' ? diff * KG_TO_LBS : diff;
    if (Math.abs(displayDiff) < 0.05) return null;
    return `${displayDiff > 0 ? '+' : ''}${displayDiff.toFixed(1)}`;
  };

  const handleLog = async () => {
    const num = parseFloat(value);
    if (!num || num <= 0) return;
    setSubmitting(true);
    try {
      const result = await weightApi.log(toKg(num));
      hapticSuccess();
      trackEvent('weight_logged', { unit });
      setValue('');
      setEntries((prev) => [{ id: result.id, logged_at: result.logged_at, weight_kg: result.weight_kg }, ...prev]);
      if (result.new_badges?.length) {
        setNewBadges(result.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      }
      broadcastWeightChange();
    } catch {
      toast('Failed to log weight', 'error');
    }
    setSubmitting(false);
  };

  const handleDelete = async (id: number) => {
    try {
      await weightApi.delete(id);
      setEntries((prev) => prev.filter((e) => e.id !== id));
      broadcastWeightChange();
    } catch {
      toast('Failed to delete', 'error');
    }
  };

  // Weekly rate of change
  const weeklyRate = (() => {
    if (entries.length < 2) return null;
    const newest = entries[0];
    const oldest = entries[entries.length - 1];
    const d1 = new Date(oldest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
    const d2 = new Date(newest.logged_at.split(/[T ]/)[0] + 'T00:00:00');
    const days = (d2.getTime() - d1.getTime()) / 86400000;
    if (days < 7) return null;
    const deltaKg = newest.weight_kg - oldest.weight_kg;
    const rateKg = (deltaKg / days) * 7;
    const rate = unit === 'lbs' ? rateKg * KG_TO_LBS : rateKg;
    return rate;
  })();

  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col" style={{ background: 'var(--bg-base)' }}>
      {/* Header */}
      <div className="relative flex items-center justify-center px-4 pb-2" style={{ paddingTop: 'calc(16px + env(safe-area-inset-top, 0px))' }}>
        <button onClick={onClose} className="absolute left-4 flex items-center gap-1.5 min-h-[44px] px-2 rounded-xl active:scale-95 transition-transform" style={{ color: 'var(--text-secondary)' }}>
          <ArrowLeft className="w-5 h-5" />
          <span className="text-sm font-medium">Back</span>
        </button>
        <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>Weight</h2>
      </div>

      {/* Unit toggle */}
      <div className="flex items-center gap-2 mx-4">
        <div className="flex items-center gap-1 p-1 rounded-xl w-fit" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
          {(['kg', 'lbs'] as const).map((u) => (
            <button
              key={u}
              onClick={() => switchUnit(u)}
              className="px-4 min-h-[38px] rounded-lg text-sm font-semibold transition-all"
              style={unit === u ? { background: 'rgba(245,158,11,0.15)', color: '#f59e0b' } : { color: 'var(--text-muted)' }}
            >
              {u}
            </button>
          ))}
        </div>
        {weeklyRate !== null && (
          <span className={`text-xs font-semibold tabular-nums ${weeklyRate < -0.05 ? 'text-emerald-400' : weeklyRate > 0.05 ? 'text-red-400' : ''}`} style={Math.abs(weeklyRate) <= 0.05 ? { color: 'var(--text-muted)' } : undefined}>
            {weeklyRate > 0 ? '+' : ''}{weeklyRate.toFixed(1)} {unit}/wk
          </span>
        )}
      </div>

      {/* Chart */}
      {entries.length > 1 && (
        <div className="mx-4 mt-4 p-3 rounded-2xl" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
          <WeightChart entries={entries} unit={unit} gradientId="wg-tracker" />
        </div>
      )}

      {/* Input */}
      <div className="mx-4 mt-4 flex gap-2">
        <div className="relative flex-1">
          <input
            type="number"
            inputMode="decimal"
            step="0.1"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleLog(); }}
            placeholder={`Enter weight (${unit})`}
            className="w-full glass-input pr-12"
            onFocus={(e) => e.target.select()}
          />
          <button
            type="button"
            onClick={() => switchUnit(unit === 'kg' ? 'lbs' : 'kg')}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-sm font-bold min-w-[40px] min-h-[36px] rounded-md active:scale-95 transition-all flex items-center justify-center"
            style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.1)' }}
          >{unit}</button>
        </div>
        <Button variant="primary" size="md" onClick={handleLog} disabled={submitting || !value} className="px-5">
          {submitting ? '...' : 'Log'}
        </Button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto mt-4 px-4 space-y-1.5" style={{ overscrollBehavior: 'contain', paddingBottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}>
        {loading ? (
          <div className="flex justify-center py-8">
            <LoadingSpinner size="sm" className="w-6 h-6 border-amber-500" />
          </div>
        ) : entries.length === 0 ? (
          <p className="text-center py-8 text-sm" style={{ color: 'var(--text-muted)' }}>No weight entries yet</p>
        ) : entries.map((e, idx) => {
          const delta = getDelta(idx);
          return (
            <SwipeActions key={e.id} onDelete={() => handleDelete(e.id)}>
              <div
                className="flex items-center justify-between px-3.5 py-3 rounded-xl"
                style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-bold tabular-nums" style={{ color: '#f59e0b' }}>{toDisplay(e.weight_kg)}</span>
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{unit}</span>
                  {delta && (
                    <span className={`text-xs font-semibold tabular-nums ${parseFloat(delta) < 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {delta}
                    </span>
                  )}
                </div>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{formatDate(e.logged_at)}</span>
              </div>
            </SwipeActions>
          );
        })}
      </div>

      {/* Badge celebration */}
      {newBadges.length > 0 && (
        <BadgeCelebration badges={newBadges} onDone={() => setNewBadges([])} />
      )}
    </div>,
    document.body
  );
}
