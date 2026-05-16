import Button from './Button';
import { Minus, Plus } from './icons';

interface Props {
  value: number;
  onChange: (value: number) => void;
  label?: string;
}

const MAX_SERVINGS = 20;

export default function ServingsSelector({ value, onChange, label }: Props) {
  const decrement = () => {
    const next = Math.max(0.5, Math.round((value - 0.5) * 10) / 10);
    onChange(next);
  };

  const increment = () => {
    const next = Math.min(MAX_SERVINGS, Math.round((value + 0.5) * 10) / 10);
    onChange(next);
  };

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={decrement} disabled={value <= 0.5}>
          <Minus className="w-4 h-4" />
        </Button>
        <span
          className="text-lg font-bold tabular-nums min-w-[100px] text-center"
          style={{ color: 'var(--text-primary)' }}
        >
          {value === 1 ? '1 serving' : `${value} servings`}
        </span>
        <Button variant="secondary" size="sm" onClick={increment} disabled={value >= MAX_SERVINGS}>
          <Plus className="w-4 h-4" />
        </Button>
      </div>
      {label && (
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {label}
        </span>
      )}
    </div>
  );
}
