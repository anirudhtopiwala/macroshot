import { useState } from 'react';
import Button from './Button';
import { workoutsApi } from '../api/workouts';
import { useToast } from './Toast';

const ACTIVITY_TYPES = [
  'Run', 'Walk', 'Ride', 'Swim', 'Hike',
  'Yoga', 'Weights', 'HIIT', 'Other',
];

interface Props {
  date: string;
  onSaved: () => void;
}

function nowHHMM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function AddWorkout({ date, onSaved }: Props) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [type, setType] = useState('');
  const [duration, setDuration] = useState('');
  const [calories, setCalories] = useState('');
  const [time, setTime] = useState(nowHHMM());
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setType(''); setDuration(''); setCalories(''); setTime(nowHHMM()); setOpen(false);
  };

  const save = async () => {
    if (!type || !duration) return;
    setSaving(true);
    try {
      await workoutsApi.addManual({
        activity_type: type,
        duration_min: parseInt(duration, 10),
        calories_burned: calories ? parseFloat(calories) : 0,
        date,
        time: time || undefined,
      });
      toast('Workout logged!');
      reset();
      onSaved();
    } catch {
      toast('Failed to save workout', 'error');
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full py-2 text-xs font-semibold text-center rounded-xl transition-colors"
        style={{ color: '#3b82f6', background: 'rgba(59,130,246,0.06)' }}
      >
        + Add workout
      </button>
    );
  }

  return (
    <div className="space-y-3 pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
      <div className="flex flex-wrap gap-1.5">
        {ACTIVITY_TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setType(t)}
            className="px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all"
            style={{
              background: type === t ? 'rgba(59,130,246,0.2)' : 'var(--bg-elevated)',
              color: type === t ? '#3b82f6' : 'var(--text-secondary)',
            }}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-[10px] font-semibold block mb-1" style={{ color: 'var(--text-muted)' }}>Duration (min)</label>
          <input
            type="number"
            inputMode="numeric"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            placeholder="30"
            className="w-full px-3 py-2 rounded-xl text-sm"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)' }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <label className="text-[10px] font-semibold block mb-1" style={{ color: 'var(--text-muted)' }}>Calories</label>
          <input
            type="number"
            inputMode="numeric"
            value={calories}
            onChange={(e) => setCalories(e.target.value)}
            placeholder="200"
            className="w-full px-3 py-2 rounded-xl text-sm"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)' }}
          />
        </div>
        <div className="w-[92px] shrink-0">
          <label className="text-[10px] font-semibold block mb-1" style={{ color: 'var(--text-muted)' }}>Time</label>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="w-full px-2 py-2 rounded-xl text-sm tabular-nums"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)' }}
          />
        </div>
      </div>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" className="flex-1" onClick={reset}>Cancel</Button>
        <Button variant="primary" size="sm" className="flex-1" onClick={save} disabled={!type || !duration || saving}>
          {saving ? 'Saving...' : 'Save'}
        </Button>
      </div>
    </div>
  );
}
