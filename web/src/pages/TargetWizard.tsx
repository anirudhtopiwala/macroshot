import { useState, useEffect, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronRight, Target, User, Sparkles, TrendingDown, Minus, TrendingUp } from '../components/icons';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { hapticLight } from '../utils/haptics';
import { api } from '../api/client';
import { subscriptionApi } from '../api/subscription';
import { subscribeToPush } from '../api/push';
import { useTargetSession } from '../hooks/useTargetSession';
import BodySliders from '../components/BodySliders';
import CorrectionChat from '../components/CorrectionChat';
import { useToast } from '../components/Toast';
import { useAuth } from '../context/AuthContext';
import { useSubscription } from '../context/SubscriptionContext';
import {
  markTargetsSetForInstallPrompt,
  markInstallPromptSeen,
  triggerNativeInstall,
  isStandalone,
  isIOS,
  isIpad,
  isInAppBrowser,
  canNativeInstall,
} from '../components/InstallPrompt';
import IOSInstallAnimation from '../components/IOSInstallAnimation';
import InAppBrowserAnimation from '../components/InAppBrowserAnimation';
import BadgeCelebration from '../components/BadgeCelebration';
import type { NewBadge, Targets, Profile } from '../types';

const DEFAULT_TARGETS: Targets = { calories: 2000, protein: 150, carbs: 200, fat: 70 };

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

// Centered rate slider window: we pick a symmetric halfWidth around the
// recommended value so the marker sits at 50% of the track. Bounded by the
// hard safety range for each goal (lose: 0.25–1.0, gain: 0.1–0.5 kg/wk)
// and a minimum halfWidth so the slider remains usable even when
// recommended is right up against a hard edge.
function getRateWindow(goal: string, weightKg: number | null) {
  const hardMin = goal === 'lose_weight' ? 0.25 : 0.1;
  const hardMax = goal === 'lose_weight' ? 1.0 : 0.5;
  const recommended = getRecommendedRate(goal, weightKg);
  const naturalHW = Math.min(recommended - hardMin, hardMax - recommended);
  const halfWidth = Math.max(0.1, naturalHW);
  const rateMin = Math.max(hardMin, recommended - halfWidth);
  const rateMax = Math.min(hardMax, recommended + halfWidth);
  return { recommended, rateMin, rateMax };
}

const SEX_OPTIONS = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'not_specified', label: 'Prefer not to say' },
];

function PillButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="py-2.5 px-3 rounded-xl text-xs font-semibold transition-all duration-200"
      style={active ? {
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
      {children}
    </button>
  );
}

export default function TargetWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rawMode = searchParams.get('mode');
  const mode = rawMode === 'refine' ? 'refine' : rawMode === 'settings' ? 'settings' : 'onboarding';
  const { toast } = useToast();
  const { user } = useAuth();
  const { betaMode } = useSubscription();

  // Wizard state
  const startStep = mode === 'refine' ? 3 : mode === 'settings' ? 1 : 0;
  const [step, setStep] = useState(startStep);

  // Profile fields
  const [name, setName] = useState('');
  const [sex, setSex] = useState<string | null>(null);
  const [age, setAge] = useState<number | null>(null);
  const [profile, setProfile] = useState<{ weight_kg: number | null; height_cm: number | null }>({ weight_kg: null, height_cm: null });

  // Goal fields
  const [goal, setGoal] = useState('maintain');
  const [activityLevel, setActivityLevel] = useState('lightly_active');
  const [workoutsPerWeek, setWorkoutsPerWeek] = useState<number>(3);
  const [rateKgPerWeek, setRateKgPerWeek] = useState<number>(0.5);
  const [units, setUnits] = useState('metric');

  // AI targets
  const [editTargets, setEditTargets] = useState<Targets>({ calories: 2000, protein: 150, carbs: 200, fat: 70 });
  const [saving, setSaving] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  // New badges from /accept (or PUT /settings/targets) — celebrated then
  // navigated. Pre-fix this response was discarded, so users hit Goal Setter
  // in the DB without ever seeing the popup.
  const [newBadges, setNewBadges] = useState<NewBadge[]>([]);
  const pendingNavRef = useRef<(() => void) | null>(null);
  const macrosCardRef = useRef<HTMLDivElement>(null);

  const session = useTargetSession();

  const scrollToMacros = () => {
    macrosCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Load the user's saved unit preference in every mode - onboarding users
  // may have visited before and set imperial, and we want BodySliders to
  // open in their preferred unit. Cheap GET, no harm if it fails.
  useEffect(() => {
    api.get<{ units_system: string }>('/settings/prefs')
      .then((prefs) => { if (prefs.units_system) setUnits(prefs.units_system); })
      .catch(() => {});
  }, []);

  // Persist unit changes to prefs so Settings inherits them. Fire-and-forget
  // - a failed PATCH here isn't worth blocking the wizard. Skip the initial
  // 'metric' default until the user actually changes it (tracked via ref).
  const unitsInitialized = useRef(false);
  useEffect(() => {
    if (!unitsInitialized.current) {
      unitsInitialized.current = true;
      return;
    }
    api.put('/settings/prefs', { units_system: units }).catch(() => {});
  }, [units]);

  // Pre-fill from existing data in settings/refine mode
  const autoTriggered = useRef(false);
  useEffect(() => {
    if (mode === 'settings' || mode === 'refine') {
      Promise.all([
        api.get<Profile & { workouts_per_week?: number | null }>('/settings/profile'),
        api.get<Targets>('/settings/targets'),
      ]).then(([p, t]) => {
        if (p.age) setAge(p.age);
        if (p.sex) setSex(p.sex);
        if (p.weight_kg) setProfile((prev) => ({ ...prev, weight_kg: p.weight_kg }));
        if (p.height_cm) setProfile((prev) => ({ ...prev, height_cm: p.height_cm }));
        if (p.activity_level) setActivityLevel(p.activity_level);
        if (p.workouts_per_week != null) setWorkoutsPerWeek(p.workouts_per_week);
        if (p.goal) setGoal(p.goal);
        if (p.weight_change_rate_kg != null) setRateKgPerWeek(p.weight_change_rate_kg);
        setEditTargets({ calories: t.calories, protein: t.protein, carbs: t.carbs, fat: t.fat });

        // In refine mode, auto-trigger AI suggestion with saved profile data
        if (mode === 'refine' && !autoTriggered.current) {
          autoTriggered.current = true;
          setAiLoading(true);
          const savedGoal = p.goal || 'maintain';
          session.suggest({
            age: p.age,
            sex: p.sex,
            weight_kg: p.weight_kg,
            height_cm: p.height_cm,
            goal: savedGoal,
            activity_level: p.activity_level || 'lightly_active',
            workouts_per_week: p.workouts_per_week ?? 3,
            weight_change_rate_kg: savedGoal !== 'maintain' ? (p.weight_change_rate_kg ?? 0.5) : undefined,
          }).then((res) => {
            if (res?.error) toast(res.error, 'error');
          }).finally(() => setAiLoading(false));
        }
      }).catch(() => {});
    }
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // When the user arrives at step 2 (or the goal/weight changes), make sure
  // the stored rate is inside the slider window - otherwise a refine-mode
  // prefill at 0.8 kg/wk could sit outside a recommend-centered window and
  // submit a value the user never saw.
  useEffect(() => {
    if (step !== 2 || goal === 'maintain') return;
    const { rateMin, rateMax } = getRateWindow(goal, profile.weight_kg);
    if (rateKgPerWeek < rateMin) setRateKgPerWeek(rateMin);
    else if (rateKgPerWeek > rateMax) setRateKgPerWeek(rateMax);
  }, [step, goal, profile.weight_kg]);  // eslint-disable-line react-hooks/exhaustive-deps

  // When AI returns targets, update the editable inputs
  useEffect(() => {
    if (session.targets) {
      setEditTargets({
        calories: Math.round(session.targets.calories),
        protein: Math.round(session.targets.protein),
        carbs: Math.round(session.targets.carbs),
        fat: Math.round(session.targets.fat),
      });
    }
  }, [session.targets]);

  const goToAIStep = async () => {
    setAiLoading(true);
    setStep(3);
    try {
      const res = await session.suggest({
        age,
        sex,
        weight_kg: profile.weight_kg,
        height_cm: profile.height_cm,
        goal,
        activity_level: activityLevel,
        workouts_per_week: workoutsPerWeek,
        weight_change_rate_kg: goal !== 'maintain' ? rateKgPerWeek : undefined,
      });
      if (res?.error) {
        toast(res.error, 'error');
      }
    } finally {
      setAiLoading(false);
    }
  };

  const handleAccept = async () => {
    setSaving(true);
    try {
      // Save profile data (including activity level + workouts)
      const profileData: Record<string, unknown> = {};
      if (name.trim()) profileData.first_name = name.trim();
      if (sex) profileData.sex = sex;
      if (age) profileData.age = age;
      if (profile.weight_kg) profileData.weight_kg = profile.weight_kg;
      if (profile.height_cm) profileData.height_cm = profile.height_cm;
      if (activityLevel) profileData.activity_level = activityLevel;
      profileData.workouts_per_week = workoutsPerWeek;
      if (goal) profileData.goal = goal;
      if (goal !== 'maintain') profileData.weight_change_rate_kg = rateKgPerWeek;
      if (Object.keys(profileData).length > 0) {
        await api.put('/settings/profile', profileData);
      }

      // Accept via session or save directly. Both endpoints return
      // `new_badges` (Goal Setter fires here for first-time target setters)
      // — capture and celebrate before navigating away.
      let earned: NewBadge[] = [];
      if (session.sessionId) {
        const res = await session.accept(editTargets);
        if (!res) {
          toast('Error saving target macros', 'error');
          return;
        }
        earned = res.new_badges ?? [];
      } else {
        const res = await api.put<{ new_badges?: NewBadge[] }>('/settings/targets', editTargets);
        earned = res.new_badges ?? [];
      }

      // Re-trigger the install prompt now that the user has real targets -
      // this catches step 1 of the install schedule (post-targets reminder).
      markTargetsSetForInstallPrompt();

      const finishNav = () => {
        if (mode === 'settings' || mode === 'refine') {
          toast('Targets updated!');
          localStorage.removeItem(`targets_skipped_${user?.user_id || ''}`);
          navigate('/settings', { replace: true });
        } else {
          setStep(4);
        }
      };

      if (earned.length > 0) {
        // Hold the navigation until the celebration is dismissed so the user
        // actually sees the popup; without this, the popup unmounts the
        // moment we route away.
        pendingNavRef.current = finishNav;
        setNewBadges(earned);
      } else {
        finishNav();
      }
    } catch {
      toast('Error saving target macros', 'error');
      // In onboarding, don't block
      if (mode === 'onboarding') setStep(4);
    } finally {
      setSaving(false);
    }
  };

  const onboardKey = user?.user_id ? `onboarded_${user.user_id}` : 'onboarded';

  const finishOnboarding = async (skipped = false) => {
    localStorage.setItem(onboardKey, 'true');
    if (skipped) {
      localStorage.setItem(`targets_skipped_${user?.user_id || ''}`, 'true');
    } else {
      localStorage.removeItem(`targets_skipped_${user?.user_id || ''}`);
    }
    // Request push notification permission during onboarding
    // so new users are registered from day one
    try {
      const ok = await subscribeToPush();
      if (ok) {
        // Auto-enable reminders with user's detected timezone
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        await api.put('/settings/prefs', { reminders_on: 1, timezone: tz }).catch(() => {});
      }
    } catch { /* user denied or not supported - that's fine */ }
    navigate('/', { replace: true });
  };

  const skipOnboarding = async () => {
    hapticLight();
    try {
      // Save default targets so has_targets becomes true on the backend
      // skip=true prevents the "Goal Setter" badge from triggering
      await api.put('/settings/targets', { ...DEFAULT_TARGETS, skip: true });
    } catch {
      // Even if save fails, let them through - dashboard has its own defaults
    }
    toast('You can set personalized targets anytime in Settings → Goals');
    finishOnboarding(true);
  };

  const handleRefine = async (text: string) => {
    await session.refine(text);
  };

  const stepIndices = mode === 'refine' ? [3] : mode === 'settings' ? [1, 2, 3] : [0, 1, 2, 3, 4];

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      {newBadges.length > 0 && (
        <BadgeCelebration
          badges={newBadges}
          onView={mode === 'onboarding' ? () => {
            // The user has saved real targets, so they're effectively
            // onboarded. Marking the flag before the route changes prevents
            // App.tsx's !onboarded guard from bouncing the navigate to
            // /settings/achievements back to /onboarding (which would
            // remount this wizard at step 0).
            localStorage.setItem(onboardKey, 'true');
          } : undefined}
          onDone={() => {
            setNewBadges([]);
            const nav = pendingNavRef.current;
            pendingNavRef.current = null;
            if (nav) nav();
          }}
        />
      )}
      <div className="w-full max-w-md">
        {/* Step indicators */}
        <div className="flex justify-center gap-2 mb-8">
          {stepIndices.map((i) => (
            <div
              key={i}
              className={`w-2 h-2 rounded-full transition-all duration-300 ${
                i === step ? 'bg-emerald-400 w-6' : i < step ? 'bg-emerald-600' : ''
              }`}
              style={i > step ? { background: 'var(--track-bg)' } : undefined}
            />
          ))}
        </div>

        {/* Step 0: Welcome (onboarding only) */}
        {step === 0 && (
          <div className="glass-card py-6 px-4 sm:px-6 text-center">
            <div className="w-16 h-16 rounded-2xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center mx-auto mb-6">
              <Target className="w-8 h-8 text-emerald-400" />
            </div>
            <h1 className="text-3xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>Welcome to <span className="text-emerald-400">MacroShot</span></h1>
            <p className="mb-8" style={{ color: 'var(--text-secondary)' }}>Track your nutrition effortlessly with AI-powered meal analysis</p>
            <Button variant="primary" size="lg" className="w-full mb-3" onClick={() => setStep(1)}>
              Get Started <ChevronRight className="w-4 h-4" />
            </Button>
            <button
              onClick={skipOnboarding}
              className="text-xs font-medium w-full py-2"
              style={{ color: 'var(--text-muted)' }}
            >
              Skip for now - use general defaults
            </button>
          </div>
        )}

        {/* Step 1: About You */}
        {step === 1 && (
          <div className="glass-card py-6 px-4 sm:px-6">
            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-4" style={{ background: 'var(--bg-elevated)' }}>
              <User className="w-6 h-6" style={{ color: 'var(--text-secondary)' }} />
            </div>
            <h2 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>About you</h2>
            <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>Optional - helps AI personalize your targets</p>
            <div className="space-y-5">
              <input
                type="text"
                placeholder="Name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="glass-input w-full"
              />

              {/* Sex pills */}
              <div>
                <label className="text-xs mb-2 block" style={{ color: 'var(--text-secondary)' }}>Sex</label>
                <div className="flex gap-2">
                  {SEX_OPTIONS.map((s) => (
                    <PillButton key={s.value} active={sex === s.value} onClick={() => setSex(s.value)}>
                      {s.label}
                    </PillButton>
                  ))}
                </div>
              </div>

              {/* Age */}
              <div>
                <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Age</label>
                <input
                  type="number"
                  placeholder="Age"
                  value={age ?? ''}
                  onChange={(e) => setAge(e.target.value ? Number(e.target.value) : null)}
                  className="glass-input w-full"
                  min={16}
                  max={150}
                />
              </div>

              <BodySliders
                weightKg={profile.weight_kg}
                heightCm={profile.height_cm}
                onChange={(updates) => setProfile({ ...profile, ...updates })}
                units={units === 'imperial' ? 'imperial' : 'metric'}
                onUnitsChange={(u) => setUnits(u)}
              />
            </div>
            <div className="flex gap-3 mt-6">
              {mode === 'onboarding' && (
                <Button variant="secondary" className="flex-1" onClick={() => setStep(0)}>Back</Button>
              )}
              <Button variant="primary" className="flex-1" onClick={() => {
                if (age != null) {
                  if (age < 16) { toast('Age must be above 16', 'error'); return; }
                  if (age > 150) { toast('Age must be less than 150', 'error'); return; }
                }
                setStep(2);
              }}>
                Next <ChevronRight className="w-4 h-4" />
              </Button>
            </div>
            {mode === 'onboarding' && (
              <button onClick={() => setStep(2)} className="w-full text-center text-xs mt-3" style={{ color: 'var(--text-muted)' }}>
                Skip
              </button>
            )}
          </div>
        )}

        {/* Step 2: Your Goal */}
        {step === 2 && (() => {
          const goalColor = GOALS.find(g => g.value === goal)?.color || '#10b981';
          const rateStep = 0.05;
          const { recommended, rateMin, rateMax } = getRateWindow(goal, profile.weight_kg);
          // Clamp the current value into the window so the thumb doesn't
          // sit outside the track after a goal switch or a refine-mode
          // prefill landed outside the centered window.
          const rateClamped = Math.min(rateMax, Math.max(rateMin, rateKgPerWeek));
          const ratePct = ((rateClamped - rateMin) / (rateMax - rateMin)) * 100;
          const recPct = ((recommended - rateMin) / (rateMax - rateMin)) * 100;

          return (
          <div className="glass-card py-6 px-4 sm:px-6">
            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-4" style={{ background: 'var(--bg-elevated)' }}>
              <Target className="w-6 h-6 text-emerald-400" />
            </div>
            <h2 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>Your goal</h2>
            <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>Tell us about your goals and activity</p>

            <div className="space-y-5">
              {/* Goal direction - icon pills with per-goal colors */}
              <div>
                <label className="text-xs mb-2 block" style={{ color: 'var(--text-secondary)' }}>Goal</label>
                <div className="flex gap-2">
                  {GOALS.map((g) => {
                    const active = goal === g.value;
                    return (
                      <button
                        key={g.value}
                        onClick={() => {
                          hapticLight();
                          setGoal(g.value);
                          // Snap rate to the recommended value for the new
                          // goal so the slider opens centered. Only relevant
                          // when the goal actually changes directions; no-op
                          // for Maintain (rate hidden) but harmless.
                          if (g.value !== 'maintain') {
                            setRateKgPerWeek(getRecommendedRate(g.value, profile.weight_kg));
                          }
                        }}
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
              </div>

              {/* Weight change rate - only when not maintain */}
              {goal !== 'maintain' && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                      {getRateLabel(goal, rateClamped)}
                    </span>
                    <span className="text-sm font-bold tabular-nums" style={{ color: goalColor }}>
                      {units === 'imperial' ? (rateClamped * 2.205).toFixed(1) : rateClamped.toFixed(2)} {units === 'imperial' ? 'lbs' : 'kg'}/wk
                    </span>
                  </div>
                  <input
                    type="range"
                    min={rateMin}
                    max={rateMax}
                    step={rateStep}
                    value={rateClamped}
                    onChange={(e) => setRateKgPerWeek(Number(e.target.value))}
                    className="w-full h-2 rounded-full appearance-none cursor-pointer"
                    style={{
                      background: `linear-gradient(to right, ${goalColor} 0%, ${goalColor} ${ratePct}%, var(--bg-elevated) ${ratePct}%, var(--bg-elevated) 100%)`,
                    }}
                  />
                  {/* Recommended marker */}
                  <div className="relative mt-1" style={{ height: '18px' }}>
                    <div
                      className="absolute flex flex-col items-center"
                      style={{ left: `${recPct}%`, transform: 'translateX(-50%)' }}
                    >
                      <div style={{
                        width: 0, height: 0,
                        borderLeft: '4px solid transparent',
                        borderRight: '4px solid transparent',
                        borderBottom: '5px solid #10b981',
                      }} />
                      <span className="text-[9px] font-semibold text-emerald-400 whitespace-nowrap">
                        Recommended
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {/* Activity level - lifestyle-based descriptions */}
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

              {/* Workout frequency */}
              <div>
                <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Workouts per week</label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={7}
                    value={workoutsPerWeek}
                    onChange={(e) => setWorkoutsPerWeek(Number(e.target.value))}
                    className="flex-1 h-2 rounded-full appearance-none cursor-pointer"
                    style={{
                      background: `linear-gradient(to right, #10b981 0%, #10b981 ${(workoutsPerWeek / 7) * 100}%, var(--bg-elevated) ${(workoutsPerWeek / 7) * 100}%, var(--bg-elevated) 100%)`,
                    }}
                  />
                  <span className="text-sm font-bold text-emerald-400 w-6 text-center tabular-nums">{workoutsPerWeek}</span>
                </div>
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <Button variant="secondary" className="flex-1" onClick={() => setStep(1)}>Back</Button>
              <Button variant="primary" className="flex-1" onClick={goToAIStep}>
                <Sparkles className="w-4 h-4" /> Generate Targets
              </Button>
            </div>
            {mode === 'onboarding' && (
              <button
                onClick={skipOnboarding}
                className="text-xs font-medium w-full py-2 mt-2"
                style={{ color: 'var(--text-muted)' }}
              >
                Skip for now - use general defaults
              </button>
            )}
          </div>
          );
        })()}

        {/* Step 3: AI Recommendation + Edit */}
        {step === 3 && (
          <div ref={macrosCardRef} className="glass-card py-6 px-4 sm:px-6" style={{ scrollMarginTop: 16 }}>
            <div className="w-12 h-12 rounded-xl bg-emerald-500/10 flex items-center justify-center mb-4">
              <Sparkles className="w-6 h-6 text-emerald-400" />
            </div>

            {aiLoading || session.suggesting ? (
              <>
                <h2 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>Your macro targets</h2>
                <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>Calculating your personalized targets...</p>
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <LoadingSpinner />
                  <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>Analyzing your profile...</p>
                </div>
              </>
            ) : (
              <>
                <h2 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>Your macro targets</h2>
                <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>Edit below or chat to refine</p>

                {/* Editable macro inputs */}
                <div className="space-y-3 mb-4">
                  <div>
                    <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Calories (kcal)</label>
                    <input
                      type="number"
                      min={1}
                      value={editTargets.calories ?? ''}
                      onChange={(e) => setEditTargets({ ...editTargets, calories: Number(e.target.value) })}
                      className="glass-input w-full"
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs text-macro-protein mb-1 block">Protein (g)</label>
                      <input
                        type="number"
                        min={0}
                        value={editTargets.protein ?? ''}
                        onChange={(e) => setEditTargets({ ...editTargets, protein: Number(e.target.value) })}
                        className="glass-input w-full"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-macro-carbs mb-1 block">Carbs (g)</label>
                      <input
                        type="number"
                        min={0}
                        value={editTargets.carbs ?? ''}
                        onChange={(e) => setEditTargets({ ...editTargets, carbs: Number(e.target.value) })}
                        className="glass-input w-full"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-macro-fat mb-1 block">Fat (g)</label>
                      <input
                        type="number"
                        min={0}
                        value={editTargets.fat ?? ''}
                        onChange={(e) => setEditTargets({ ...editTargets, fat: Number(e.target.value) })}
                        className="glass-input w-full"
                      />
                    </div>
                  </div>
                </div>

                {/* AI explanation */}
                {session.explanation && (
                  <div className="rounded-xl p-3 mb-4" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)' }}>
                    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>{session.explanation}</p>
                  </div>
                )}

                {/* Chat always visible for refinement */}
                <div className="mb-4">
                  <CorrectionChat
                    messages={session.messages}
                    onSend={handleRefine}
                    disabled={session.refining}
                    placeholder="Refine your targets..."
                    expanded
                    onScrollToMacros={scrollToMacros}
                  />
                </div>

                {session.error && (
                  <p className="text-xs text-red-400 mb-4">{session.error}</p>
                )}

                <div className="flex gap-3">
                  <Button
                    variant="secondary"
                    className="flex-1"
                    onClick={() => mode === 'refine' ? navigate('/settings', { replace: true }) : setStep(2)}
                  >
                    {mode === 'refine' ? 'Cancel' : 'Back'}
                  </Button>
                  <Button
                    variant="primary"
                    className="flex-1"
                    onClick={handleAccept}
                    disabled={saving || session.refining}
                  >
                    {saving ? 'Saving...' : 'Accept'}
                  </Button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Step 4: Trial welcome (onboarding only) */}
        {step === 4 && (
          <div className="glass-card py-6 px-4 sm:px-6 text-center">
            <div className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-6" style={{ background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.3)' }}>
              <Sparkles className="w-8 h-8" style={{ color: '#f59e0b' }} />
            </div>
            <h2 className="text-2xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>You're all set!</h2>
            <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
              {betaMode
                ? 'MacroShot is in free beta - all Pro features unlocked, no billing'
                : '7 days of Pro - free, no credit card'}
            </p>

            {/* Feature highlights */}
            <div className="space-y-3 mb-8 text-left">
              {[
                { icon: '📸', label: 'AI photo, text & barcode logging' },
                { icon: '✨', label: 'AI nutrition coach' },
                { icon: '📈', label: '7, 30 & 90-day trends' },
                { icon: '⚖️', label: 'Weight tracking & charts' },
              ].map(({ icon, label }) => (
                <div key={label} className="flex items-center gap-3 px-4 py-2.5 rounded-xl" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
                  <span className="text-lg">{icon}</span>
                  <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{label}</span>
                </div>
              ))}
            </div>

            <div className="space-y-3">
              <Button
                variant="primary"
                size="lg"
                className="w-full"
                onClick={() => {
                  // Already installed as a PWA - skip the install step.
                  if (isStandalone()) { finishOnboarding(); return; }
                  setStep(5);
                }}
              >
                Start Exploring
              </Button>
              {!betaMode && (
                <>
                  <Button
                    variant="secondary"
                    size="lg"
                    className="w-full"
                    onClick={async () => {
                      try {
                        const data = await subscriptionApi.checkout('pro_monthly');
                        if (data.url) window.location.href = data.url;
                      } catch { /* ignore */ }
                    }}
                  >
                    Go Pro Now - $4.99/mo
                  </Button>
                  {/* CA ARL / FTC Click-to-Cancel - required inline auto-renewal disclosure. */}
                  <p className="text-[10px] text-center leading-snug px-2" style={{ color: 'var(--text-muted)' }}>
                    $4.99 billed monthly. Auto-renews until cancelled. Cancel any time in Settings → Subscription.
                  </p>
                </>
              )}
            </div>
          </div>
        )}

        {/* Step 5: Install to home screen (onboarding only, shown after
            "Start Exploring"). Skipped entirely for users already in
            standalone/PWA mode - handled upstream. */}
        {step === 5 && (() => {
          const inInApp = isInAppBrowser();
          const iosDevice = isIOS();
          const ipadDevice = isIpad();
          const nativeAvailable = canNativeInstall();
          return (
            <div className="glass-card py-6 px-4 sm:px-6">
              <div
                className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-6"
                style={{ background: 'rgba(16,185,129,0.15)', border: '1px solid rgba(16,185,129,0.3)' }}
              >
                <span className="text-3xl" role="img" aria-label="Mobile app">📲</span>
              </div>
              <h2 className="text-center text-2xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
                One last thing
              </h2>
              <p className="text-center text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
                MacroShot is much easier to use when it's on your home screen - full-screen, offline-ready, and push reminders actually work. Install it now for the best experience.
              </p>

              {inInApp ? (
                <InAppBrowserAnimation />
              ) : iosDevice ? (
                <IOSInstallAnimation variant={ipadDevice ? 'ipad' : 'ios'} />
              ) : null}

              <div className="flex flex-col gap-3 mt-6">
                {!iosDevice && !inInApp && (
                  <Button
                    variant="primary"
                    size="lg"
                    className="w-full"
                    onClick={async () => {
                      hapticLight();
                      if (nativeAvailable) {
                        await triggerNativeInstall();
                        markInstallPromptSeen();
                        finishOnboarding();
                      } else {
                        toast('Open your browser menu and choose "Install app" or "Add to Home Screen"');
                      }
                    }}
                  >
                    Install to Home Screen
                  </Button>
                )}

                {inInApp && (
                  <Button
                    variant="primary"
                    size="lg"
                    className="w-full"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(window.location.origin + '/macro_app/');
                        toast('Link copied - paste it into Safari or Chrome');
                      } catch { toast('Copy failed', 'error'); }
                    }}
                  >
                    Copy link
                  </Button>
                )}

                <Button
                  variant={iosDevice || inInApp ? 'primary' : 'secondary'}
                  size="lg"
                  className="w-full"
                  onClick={() => {
                    markInstallPromptSeen();
                    finishOnboarding();
                  }}
                >
                  {iosDevice || inInApp ? 'Done - Continue' : 'Maybe Later'}
                </Button>
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
