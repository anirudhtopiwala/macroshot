import { useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Check, Sparkles, TrendingDown, Minus, TrendingUp } from '../components/icons';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import BadgeCelebration from '../components/BadgeCelebration';
import { api } from '../api/client';
import { hapticLight } from '../utils/haptics';
import { useToast } from '../components/Toast';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import type { Targets, Profile, Prefs } from '../types';

const GOALS = [
  { value: 'lose_weight', label: 'Lose', Icon: TrendingDown, color: '#f97316' },
  { value: 'maintain', label: 'Maintain', Icon: Minus, color: '#10b981' },
  { value: 'gain_weight', label: 'Gain', Icon: TrendingUp, color: '#3b82f6' },
];

const ACTIVITY_LEVELS = [
  { value: 'sedentary', label: 'Sedentary', desc: 'Desk job, minimal walking' },
  { value: 'lightly_active', label: 'Lightly Active', desc: 'On your feet some of the day' },
  { value: 'active', label: 'Active', desc: 'Active job - nurse, retail, waiter' },
  { value: 'very_active', label: 'Very Active', desc: 'Physical job - construction, farming' },
];

function getRateLabel(goal: string, rate: number): string {
  if (goal === 'lose_weight') {
    if (rate <= 0.35) return 'Conservative';
    if (rate <= 0.65) return 'Moderate';
    return 'Aggressive';
  }
  if (rate <= 0.2) return 'Lean gain';
  if (rate <= 0.35) return 'Moderate';
  return 'Aggressive';
}

function getRecommendedRate(goal: string, weightKg: number | null): number {
  if (!weightKg) return goal === 'lose_weight' ? 0.5 : 0.25;
  if (goal === 'lose_weight') {
    return Math.round(Math.min(1.0, Math.max(0.25, weightKg * 0.0075)) * 20) / 20;
  }
  return Math.round(Math.min(0.5, Math.max(0.1, weightKg * 0.003)) * 20) / 20;
}

type CachedGoals = { profile: Profile; targets: Targets; units: string };

export default function SettingsGoals() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { user: authUser } = useAuth();
  const cached = getCached<CachedGoals>('settings_goals');
  const [loading, setLoading] = useState(!cached);
  const [saving, setSaving] = useState('');

  // Profile fields (goal, activity, rate, workouts)
  const [goal, setGoal] = useState(cached?.profile?.goal || 'maintain');
  const [activityLevel, setActivityLevel] = useState(cached?.profile?.activity_level || 'lightly_active');
  const [workoutsPerWeek, setWorkoutsPerWeek] = useState(cached?.profile?.workouts_per_week ?? 3);
  const [rateKgPerWeek, setRateKgPerWeek] = useState(cached?.profile?.weight_change_rate_kg ?? 0.5);
  const [weightKg, setWeightKg] = useState<number | null>(cached?.profile?.weight_kg ?? null);
  const [weightGoalKg, setWeightGoalKg] = useState<number | null>(cached?.profile?.weight_goal_kg ?? null);
  const [units, setUnits] = useState(cached?.units || 'metric');

  // Targets
  const [targets, setTargets] = useState<Targets>(cached?.targets || { calories: 2000, protein: 150, carbs: 200, fat: 70 });
  const [newBadges, setNewBadges] = useState<any[]>([]);

  const savedProfileRef = useRef(cached ? JSON.stringify({
    goal: cached.profile?.goal || 'maintain',
    activityLevel: cached.profile?.activity_level || 'lightly_active',
    workoutsPerWeek: cached.profile?.workouts_per_week ?? 3,
    rateKgPerWeek: cached.profile?.weight_change_rate_kg ?? 0.5,
    weightGoalKg: cached.profile?.weight_goal_kg,
  }) : '');
  const savedTargetsRef = useRef(cached ? JSON.stringify(cached.targets) : '');

  const profileSnapshot = JSON.stringify({ goal, activityLevel, workoutsPerWeek, rateKgPerWeek, weightGoalKg });
  const profileDirty = profileSnapshot !== savedProfileRef.current;
  const targetsDirty = JSON.stringify(targets) !== savedTargetsRef.current;

  useEffect(() => {
    Promise.all([
      api.get<Profile>('/settings/profile'),
      api.get<Targets>('/settings/targets'),
      api.get<Prefs>('/settings/prefs'),
    ]).then(([p, t, prefsRes]) => {
      if (p.goal) setGoal(p.goal);
      if (p.activity_level) setActivityLevel(p.activity_level);
      if (p.workouts_per_week != null) setWorkoutsPerWeek(p.workouts_per_week);
      if (p.weight_change_rate_kg != null) setRateKgPerWeek(p.weight_change_rate_kg);
      if (p.weight_kg) setWeightKg(p.weight_kg);
      if (p.weight_goal_kg) setWeightGoalKg(p.weight_goal_kg);
      setUnits(prefsRes.units_system || 'metric');
      setTargets(t);
      setCache('settings_goals', { profile: p, targets: t, units: prefsRes.units_system || 'metric' });
      // Set saved snapshots synchronously to avoid one-frame dirty flash
      savedProfileRef.current = JSON.stringify({
        goal: p.goal || 'maintain',
        activityLevel: p.activity_level || 'lightly_active',
        workoutsPerWeek: p.workouts_per_week ?? 3,
        rateKgPerWeek: p.weight_change_rate_kg ?? 0.5,
        weightGoalKg: p.weight_goal_kg,
      });
      savedTargetsRef.current = JSON.stringify(t);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const saveProfile = useCallback(async () => {
    setSaving('profile');
    try {
      await api.put('/settings/profile', {
        goal,
        activity_level: activityLevel,
        workouts_per_week: workoutsPerWeek,
        weight_change_rate_kg: goal !== 'maintain' ? rateKgPerWeek : null,
        weight_goal_kg: weightGoalKg,
      });
      savedProfileRef.current = profileSnapshot;
      setCache('settings_goals', { profile: { goal, activity_level: activityLevel, workouts_per_week: workoutsPerWeek, weight_change_rate_kg: rateKgPerWeek, weight_kg: weightKg, weight_goal_kg: weightGoalKg } as Profile, targets, units });
      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
      hapticLight();
      toast('Goals saved!');
    } catch {
      toast('Error saving goals', 'error');
    } finally {
      setSaving('');
    }
  }, [goal, activityLevel, workoutsPerWeek, rateKgPerWeek, weightGoalKg, profileSnapshot, toast]);

  const saveTargets = useCallback(async () => {
    // Client-side eating-disorder safeguard: refuse calories < 1200 before
    // hitting the API.  Backend Pydantic validator enforces the same floor
    // as defense-in-depth.
    if (targets.calories > 0 && targets.calories < 1200) {
      toast('Calorie target must be at least 1200 kcal. Lower targets can be unsafe - please consult a registered dietitian.', 'error');
      return;
    }
    setSaving('targets');
    try {
      const res = await api.put<{ new_badges?: any[] }>('/settings/targets', targets);
      savedTargetsRef.current = JSON.stringify(targets);
      setCache('settings_goals', { profile: { goal, activity_level: activityLevel, workouts_per_week: workoutsPerWeek, weight_change_rate_kg: rateKgPerWeek, weight_kg: weightKg, weight_goal_kg: weightGoalKg } as Profile, targets, units });
      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
      clearCache('achievements');
      clearCache('achievement_summary');
      // Clear "skipped targets" banner if user is now setting real targets
      localStorage.removeItem(`targets_skipped_${authUser?.user_id || ''}`);
      hapticLight();
      if (res.new_badges && res.new_badges.length > 0) {
        setNewBadges(res.new_badges);
      } else {
        toast('Targets saved!');
      }
    } catch (err: unknown) {
      // Surface backend validation errors (e.g. calorie floor) to the user.
      let msg = 'Error saving targets';
      if (err && typeof err === 'object' && 'message' in err && typeof (err as { message: unknown }).message === 'string') {
        const m = (err as { message: string }).message;
        if (m.toLowerCase().includes('calorie') || m.toLowerCase().includes('1200')) {
          msg = 'Calorie target must be at least 1200 kcal.';
        }
      }
      toast(msg, 'error');
    } finally {
      setSaving('');
    }
  }, [targets, toast]);

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  const goalColor = GOALS.find(g => g.value === goal)?.color || '#10b981';
  const rateMin = goal === 'lose_weight' ? 0.25 : 0.1;
  const rateMax = goal === 'lose_weight' ? 1.0 : 0.5;
  const ratePct = ((rateKgPerWeek - rateMin) / (rateMax - rateMin)) * 100;
  const recommended = getRecommendedRate(goal, weightKg);
  const recPct = ((recommended - rateMin) / (rateMax - rateMin)) * 100;
  const isImperial = units === 'imperial';

  return (
    <div className="space-y-4">
      {newBadges.length > 0 && (
        <BadgeCelebration badges={newBadges} onDone={() => { setNewBadges([]); toast('Targets saved!'); }} />
      )}
      {/* Header */}
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">Goals & Targets</h1>
      </div>

      {/* Goal + Activity Card */}
      <div className="glass-card p-5 space-y-5">
        <h2 className="section-heading">Goal</h2>

        {/* Goal direction */}
        <div className="flex gap-2">
          {GOALS.map((g) => {
            const active = goal === g.value;
            return (
              <button
                key={g.value}
                onClick={() => { hapticLight(); setGoal(g.value); }}
                className="flex-1 flex flex-col items-center gap-1.5 py-3 px-3 rounded-xl text-xs font-semibold transition-all duration-200"
                style={active ? {
                  background: `${g.color}18`,
                  border: `1px solid ${g.color}4D`,
                  color: g.color,
                  boxShadow: `0 0 12px ${g.color}26`,
                } : {
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-glass)',
                  color: 'var(--text-muted)',
                }}
              >
                <g.Icon className="w-5 h-5" />
                {g.label}
              </button>
            );
          })}
        </div>

        {/* Rate slider */}
        {goal !== 'maintain' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                {getRateLabel(goal, rateKgPerWeek)}
              </span>
              <span className="text-sm font-bold tabular-nums" style={{ color: goalColor }}>
                {isImperial ? (rateKgPerWeek * 2.205).toFixed(1) : rateKgPerWeek.toFixed(2)} {isImperial ? 'lbs' : 'kg'}/wk
              </span>
            </div>
            <input
              type="range"
              min={rateMin}
              max={rateMax}
              step={0.05}
              value={rateKgPerWeek}
              onChange={(e) => setRateKgPerWeek(Number(e.target.value))}
              className="w-full h-2 rounded-full appearance-none cursor-pointer"
              style={{
                background: `linear-gradient(to right, ${goalColor} 0%, ${goalColor} ${ratePct}%, var(--bg-elevated) ${ratePct}%, var(--bg-elevated) 100%)`,
              }}
            />
            <div className="relative mt-1" style={{ height: '18px' }}>
              <div className="absolute flex flex-col items-center" style={{ left: `${recPct}%`, transform: 'translateX(-50%)' }}>
                <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderBottom: '5px solid #10b981' }} />
                <span className="text-[9px] font-semibold text-emerald-400 whitespace-nowrap">Recommended</span>
              </div>
            </div>
          </div>
        )}

        {/* Goal weight */}
        <div>
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            Goal weight {isImperial ? '(lbs)' : '(kg)'}
          </label>
          <input
            type="number"
            min={0}
            value={(() => {
              if (!weightGoalKg) return '';
              if (isImperial) return Math.round(weightGoalKg * 2.205);
              return Math.round(weightGoalKg * 10) / 10;
            })()}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (v >= 0) {
                const kg = isImperial ? Math.round(v / 2.205 * 10) / 10 : v;
                setWeightGoalKg(kg || null);
              }
            }}
            placeholder="Optional"
            className="w-full glass-input mt-1"
          />
        </div>

        {/* Activity level */}
        <div>
          <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Daily activity level</label>
          <p className="text-[10px] mb-2" style={{ color: 'var(--text-muted)' }}>Your lifestyle outside of workouts</p>
          <div className="grid grid-cols-2 gap-2">
            {ACTIVITY_LEVELS.map((a) => (
              <button
                key={a.value}
                onClick={() => { hapticLight(); setActivityLevel(a.value); }}
                className="py-2.5 px-3 rounded-xl text-left transition-all duration-200"
                style={activityLevel === a.value ? {
                  background: 'rgba(16,185,129,0.12)',
                  border: '1px solid rgba(16,185,129,0.3)',
                  color: '#10b981',
                  boxShadow: '0 0 12px rgba(16,185,129,0.15)',
                } : {
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-glass)',
                  color: 'var(--text-muted)',
                }}
              >
                <div className="text-xs font-semibold">{a.label}</div>
                <div className="text-[10px] opacity-70 mt-0.5">{a.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Workouts per week */}
        <div>
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Workouts per week</label>
          <div className="flex items-center gap-3 mt-1.5">
            <button
              onClick={() => { hapticLight(); setWorkoutsPerWeek(Math.max(0, workoutsPerWeek - 1)); }}
              className="w-9 h-9 rounded-xl flex items-center justify-center text-lg font-bold transition-all active:scale-90"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}
            >−</button>
            <span className="text-2xl font-black text-emerald-400 tabular-nums w-8 text-center">{workoutsPerWeek}</span>
            <button
              onClick={() => { hapticLight(); setWorkoutsPerWeek(Math.min(7, workoutsPerWeek + 1)); }}
              className="w-9 h-9 rounded-xl flex items-center justify-center text-lg font-bold transition-all active:scale-90"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}
            >+</button>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>days</span>
          </div>
        </div>

        {/* Save goals */}
        <Button
          variant={profileDirty ? 'primary' : 'secondary'}
          className="w-full"
          onClick={saveProfile}
          disabled={saving === 'profile' || !profileDirty}
        >
          {saving === 'profile' ? (
            <><LoadingSpinner size="sm" className="border-emerald-400" /> Saving...</>
          ) : profileDirty ? 'Save Goals' : (
            <><Check className="w-3.5 h-3.5" /> Saved</>
          )}
        </Button>
      </div>

      {/* Daily Targets Card */}
      <div className="glass-card p-5 space-y-3">
        <h2 className="section-heading">Daily Targets</h2>
        <div className="grid grid-cols-2 gap-3">
          {(['calories', 'protein', 'carbs', 'fat'] as const).map((k) => (
            <div key={k}>
              <label className="text-xs capitalize" style={{ color: 'var(--text-secondary)' }}>{k} {k === 'calories' ? '(kcal)' : '(g)'}</label>
              <input
                type="number"
                min={0}
                max={k === 'calories' ? 10000 : 1000}
                value={targets[k] ?? ''}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  const max = k === 'calories' ? 10000 : 1000;
                  if (v >= 0 && v <= max) setTargets({ ...targets, [k]: v });
                }}
                className="w-full glass-input mt-1"
              />
            </div>
          ))}
        </div>
        <Button
          variant={targetsDirty ? 'primary' : 'secondary'}
          className="w-full"
          onClick={saveTargets}
          disabled={saving === 'targets' || !targetsDirty}
        >
          {saving === 'targets' ? (
            <><LoadingSpinner size="sm" className="border-emerald-400" /> Saving...</>
          ) : targetsDirty ? 'Save Targets' : (
            <><Check className="w-3.5 h-3.5" /> Saved</>
          )}
        </Button>
        <Button
          variant="accent"
          className="w-full"
          onClick={() => navigate('/settings/targets?mode=refine')}
        >
          <Sparkles className="w-4 h-4" /> Refine with AI
        </Button>
      </div>

    </div>
  );
}
