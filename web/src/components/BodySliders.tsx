import { useCallback } from 'react';

type Units = 'metric' | 'imperial';

function kgToLbs(kg: number): number { return Math.round(kg * 2.20462); }
function lbsToKg(lbs: number): number { return Math.round(lbs / 2.20462 * 10) / 10; }
function cmToInches(cm: number): number { return Math.round(cm / 2.54); }
function inchesToCm(inches: number): number { return Math.round(inches * 2.54); }
function inchesToFtIn(inches: number): string {
  const ft = Math.floor(inches / 12);
  const inR = inches % 12;
  return `${ft}'${inR}"`;
}

interface UnitToggleProps {
  value: Units;
  onChange: (u: Units) => void;
}

function UnitToggle({ value, onChange }: UnitToggleProps) {
  return (
    <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
      {(['metric', 'imperial'] as const).map((u) => (
        <button
          key={u}
          onClick={() => onChange(u)}
          className="px-3 py-1 rounded-md text-[11px] font-semibold transition-all capitalize"
          style={value === u ? {
            background: 'rgba(16,185,129,0.15)',
            color: '#10b981',
          } : {
            color: 'var(--text-muted)',
          }}
        >
          {u}
        </button>
      ))}
    </div>
  );
}

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  displayValue: string;
  color: string;
  onChange: (v: number) => void;
}

function Slider({ label, value, min, max, step, displayValue, color, onChange }: SliderProps) {
  const scopeClass = `slider-${label.toLowerCase()}`;
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>{label}</span>
        <span className="text-sm font-bold tabular-nums" style={{ color }}>{displayValue}</span>
      </div>
      <div className="relative">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className={`w-full h-2 rounded-full appearance-none cursor-pointer ${scopeClass}`}
          style={{
            background: `linear-gradient(to right, ${color} 0%, ${color} ${pct}%, var(--bg-elevated) ${pct}%, var(--bg-elevated) 100%)`,
            WebkitAppearance: 'none',
          }}
        />
      </div>
      <style>{`
        .${scopeClass}::-webkit-slider-thumb {
          -webkit-appearance: none;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: white;
          border: 2px solid var(--border-glass);
          box-shadow: 0 0 8px ${color}80, 0 2px 6px rgba(0,0,0,0.3);
          cursor: pointer;
        }
        .${scopeClass}::-moz-range-thumb {
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: white;
          border: 2px solid var(--border-glass);
          box-shadow: 0 0 8px ${color}80, 0 2px 6px rgba(0,0,0,0.3);
          cursor: pointer;
        }
      `}</style>
    </div>
  );
}

interface BodySlidersProps {
  weightKg: number | null;
  heightCm: number | null;
  onChange: (updates: { weight_kg?: number | null; height_cm?: number | null }) => void;
  units: Units;
  onUnitsChange: (u: Units) => void;
}

export default function BodySliders({ weightKg, heightCm, onChange, units, onUnitsChange }: BodySlidersProps) {

  const effectiveWeight = weightKg ?? 70;
  const effectiveHeight = heightCm ?? 170;

  const handleWeightChange = useCallback((v: number) => {
    const kg = units === 'imperial' ? lbsToKg(v) : v;
    onChange({ weight_kg: kg });
  }, [units, onChange]);

  const handleHeightChange = useCallback((v: number) => {
    const cm = units === 'imperial' ? inchesToCm(v) : v;
    onChange({ height_cm: cm });
  }, [units, onChange]);

  const weightDisplay = units === 'imperial'
    ? `${kgToLbs(effectiveWeight)} lbs`
    : `${effectiveWeight} kg`;

  const heightDisplay = units === 'imperial'
    ? inchesToFtIn(cmToInches(effectiveHeight))
    : `${effectiveHeight} cm`;

  const weightSliderValue = units === 'imperial' ? kgToLbs(effectiveWeight) : effectiveWeight;
  const heightSliderValue = units === 'imperial' ? cmToInches(effectiveHeight) : effectiveHeight;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="section-heading">Body</span>
        <UnitToggle value={units} onChange={onUnitsChange} />
      </div>
      <Slider
        label="Weight"
        value={weightSliderValue}
        min={units === 'imperial' ? 66 : 30}
        max={units === 'imperial' ? 400 : 180}
        step={units === 'imperial' ? 1 : 0.5}
        displayValue={weightDisplay}
        color="#3b82f6"
        onChange={handleWeightChange}
      />
      <Slider
        label="Height"
        value={heightSliderValue}
        min={units === 'imperial' ? 48 : 120}
        max={units === 'imperial' ? 84 : 215}
        step={1}
        displayValue={heightDisplay}
        color="#a855f7"
        onChange={handleHeightChange}
      />
    </div>
  );
}
