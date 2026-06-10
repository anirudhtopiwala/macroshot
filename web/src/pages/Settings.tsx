import { useEffect, useState, useRef } from 'react';
import { api, ApiError } from '../api/client';
import { adminApi } from '../api/admin';

import { Link } from 'react-router-dom';
import { LogOut, Sun, Moon, Scale, Trash2, Target, ChevronRight, ChevronDown, Bell, Trophy, Lightbulb, Zap, Download, RefreshCw, Lock, Sparkles, Heart } from '../components/icons';
import { useSiteConfig } from '../api/siteConfig';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { hapticLight } from '../utils/haptics';
import { useToast } from '../components/Toast';
import { useTheme } from '../context/ThemeContext';
import { useSubscription } from '../context/SubscriptionContext';
import { useCapState } from '../hooks/useCapState';
import UsageMeter from '../components/UsageMeter';
import { subscriptionApi } from '../api/subscription';
import ConfirmDialog from '../components/ConfirmDialog';
import { useAuth } from '../context/AuthContext';
import { useInstallPrompt } from '../components/InstallPrompt';
import IOSInstallAnimation from '../components/IOSInstallAnimation';
import InAppBrowserAnimation from '../components/InAppBrowserAnimation';
import Modal from '../components/Modal';
import UpdateProgressCard, { type UpdateProgressPhase } from '../components/UpdateProgressCard';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import { clearOfflineQueue } from '../utils/offlineQueue';
import type { Targets, Prefs, Profile, UserMe } from '../types';

const GOAL_LABELS: Record<string, string> = {
  lose_weight: 'Losing weight',
  maintain: 'Maintaining',
  gain_weight: 'Gaining weight',
};

export default function Settings() {
  const { toast } = useToast();
  const { theme, toggleTheme } = useTheme();
  const { user: authUser, logout: authLogout, isGuest } = useAuth();
  // `foundingMember` is a legacy alias of `isOG` (same value - the context
  // exposes both for backward compat). Prefer `isOG` in new code.
  const { isPremium, foundingMember, isOG, betaMode, plan, status: subStatus, imageUsage, textMealUsage, chatUsage, savedMealsUsage, trialEndsAt, currentPeriodEnd } = useSubscription();
  const imageCap = useCapState('image_analysis');
  const textMealCap = useCapState('text_meal');
  const chatCap = useCapState('ai_chat');
  const savedMealsCap = useCapState('saved_meals');
  const { canInstall, isIOS, isIpad, isInApp, install } = useInstallPrompt();
  const siteConfig = useSiteConfig();
  const cached = getCached<{ user: UserMe; targets: Targets; prefs: Prefs; profile: Profile }>('settings_main');
  const [loading, setLoading] = useState(!cached);
  const [targets, setTargets] = useState<Targets>(cached?.targets ?? { calories: 2000, protein: 150, carbs: 200, fat: 70 });
  const [prefs, setPrefs] = useState<Prefs | null>(cached?.prefs ?? null);
  const [profile, setProfile] = useState<Profile>(cached?.profile ?? { age: null, height_cm: null, weight_kg: null, sex: null, weight_goal_kg: null, activity_level: null, workouts_per_week: null, weight_change_rate_kg: null, goal: null });
  const [deleteStep, setDeleteStep] = useState<0 | 1 | 2>(0);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [isAdmin, setIsAdmin] = useState<boolean>(() => localStorage.getItem('macro_is_admin') === '1');
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [subExpanded, setSubExpanded] = useState(false);
  const [updatePhase, setUpdatePhase] = useState<UpdateProgressPhase | null>(null);
  const [updateProgress, setUpdateProgress] = useState({ done: 0, total: 0 });
  // Aborts an in-flight checkForUpdate on unmount so a route change away from
  // Settings doesn't leave the SW lifecycle subscription dangling - and, more
  // importantly, doesn't fire applyUpdate() (which triggers a page reload)
  // under a user who is now mid-task on another page.
  const updateAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => updateAbortRef.current?.abort(), []);

  // Surface a warning dot on the compact Subscription row when any quota
  // is ≥70% of its limit - signals the user to tap for details. Free-tier
  // limits and paid photo-scan caps are both considered.
  const subHasWarning = (() => {
    const rows = [imageUsage, textMealUsage, chatUsage, savedMealsUsage];
    return rows.some(r => r.limit > 0 && (r.used / r.limit) >= 0.70);
  })();

  // Admin probe - the server gate already enforces access; this is just a UX
  // hint so admins see the metrics link. 404 means "not admin".
  useEffect(() => {
    let cancelled = false;
    adminApi.whoami()
      .then(() => {
        if (cancelled) return;
        setIsAdmin(true);
        localStorage.setItem('macro_is_admin', '1');
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setIsAdmin(false);
          localStorage.removeItem('macro_is_admin');
        }
      });
    return () => { cancelled = true; };
  }, []);

  // Prefs dirty tracking - prevents GET response from overwriting user's toggle
  const savedPrefsRef = useRef<string>('');
  const prefsDirtyRef = useRef(false);


  useEffect(() => {
    // Skip API calls for guests - they're not authenticated for these endpoints
    if (isGuest) {
      setLoading(false);
      return;
    }
    Promise.allSettled([
      api.get<Targets>('/settings/targets'),
      api.get<Prefs>('/settings/prefs'),
      api.get<Profile>('/settings/profile'),
    ]).then(([tRes, pRes, prRes]) => {
      const t = tRes.status === 'fulfilled' ? tRes.value : targets;
      const p = pRes.status === 'fulfilled' ? pRes.value : prefs;
      const pr = prRes.status === 'fulfilled' ? pRes.value : profile;
      if (tRes.status === 'fulfilled') setTargets(t);
      // Only overwrite prefs from API if user hasn't modified them yet
      if (pRes.status === 'fulfilled' && !prefsDirtyRef.current) {
        setPrefs(p);
        savedPrefsRef.current = JSON.stringify(p);
      }
      if (prRes.status === 'fulfilled') setProfile(pr);
      if (authUser) setCache('settings_main', { user: authUser, targets: t, prefs: prefsDirtyRef.current ? prefs : p, profile: pr });
    }).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authUser, isGuest]);

  const logout = async () => {
    await authLogout();
    window.location.href = '/macro_app/login';
  };

  const handleDeleteAccount = async () => {
    try {
      const resp = await api.delete<{
        message: string;
        summary?: {
          meals?: number; weights?: number; chats?: number;
          saved_meals?: number; workouts?: number; photos?: number;
        };
      }>('/settings/account');
      // Clear all client-side data before redirect (preserve theme preference)
      const savedTheme = localStorage.getItem('theme');
      localStorage.clear();
      if (savedTheme) localStorage.setItem('theme', savedTheme);
      clearCache();
      clearOfflineQueue();
      if (navigator.serviceWorker?.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
      }
      // Show the user exactly what was erased so the action feels concrete.
      const s = resp?.summary;
      const parts = s
        ? [
            s.meals ? `${s.meals} meals` : '',
            s.photos ? `${s.photos} photos` : '',
            s.weights ? `${s.weights} weight entries` : '',
            s.chats ? `${s.chats} chats` : '',
          ].filter(Boolean)
        : [];
      toast(parts.length ? `Deleted ${parts.join(', ')}` : 'Account deleted');
      window.location.href = '/macro_app/login';
    } catch {
      toast('Failed to delete account', 'error');
    }
  };

  if (loading) {
    return (
      <LoadingSpinner fullPage />
    );
  }

  const firstName = authUser?.first_name || authUser?.email?.split('@')[0] || '';

  const guestOverlay = isGuest ? 'opacity-60 pointer-events-none' : '';

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Settings</h1>

      {isGuest && (
        <div className="p-4 rounded-2xl" style={{ background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
          <p className="text-sm font-semibold text-emerald-400">Sign up to save your settings</p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>Create an account to unlock personalized goals, reminders, and sync across devices</p>
        </div>
      )}

      <div className={guestOverlay}>
        <h2 className="section-heading" style={{ marginTop: '0.25rem' }}>You</h2>

      {/* Personal Details - nav row */}
      <Link to="/settings/personal" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        {authUser?.avatar_url ? (
          <img src={authUser.avatar_url} alt="" referrerPolicy="no-referrer" className="w-10 h-10 rounded-full object-cover ring-1 ring-emerald-500/40 shrink-0" />
        ) : (
          <div className="w-10 h-10 rounded-full bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center text-sm font-bold text-emerald-400 uppercase shrink-0">
            {isGuest ? 'G' : (firstName?.[0] || '?')}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold truncate">{firstName}</p>
          <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
            {[profile.height_cm ? `${Math.round(profile.height_cm)} cm` : null, profile.weight_kg ? `${Math.round(profile.weight_kg * 10) / 10} kg` : null, profile.age ? `${profile.age} yr` : null].filter(Boolean).join(' · ') || 'Complete your profile'}
          </p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Subscription */}
      {plan === 'self_hosted' ? (
        <div className="glass-card p-5 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-lg">🚀</span>
            <h2 className="section-heading">Self-Hosted</h2>
          </div>
          <p className="text-sm font-semibold" style={{ color: '#10b981' }}>All features unlocked</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>You're running your own instance - unlimited chat and everything else. Photo scans are still capped daily to protect your AI budget.</p>
        </div>
      ) : (
        <div
          className="glass-card p-5 space-y-3 cursor-pointer select-none"
          onClick={() => { hapticLight(); setSubExpanded(v => !v); }}
          role="button"
          aria-expanded={subExpanded}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: isPremium ? '#f59e0b' : 'var(--text-secondary)' }}>
                <path d="M12 2l2.09 6.26L20.18 9l-5.09 3.74L16.18 19 12 15.27 7.82 19l1.09-6.26L3.82 9l6.09-.74z" />
              </svg>
              <h2 className="section-heading">Subscription</h2>
              {!subExpanded && subHasWarning && (
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ background: '#f59e0b' }}
                  aria-label="Usage approaching limit"
                />
              )}
            </div>
            <ChevronDown
              className={`w-4 h-4 transition-transform ${subExpanded ? 'rotate-180' : ''}`}
              style={{ color: 'var(--text-muted)' }}
            />
          </div>

          {/* Limited-beta mode - reuse the paid-Pro layout but swap the
              portal button for a read-only "Free beta" (or "Founding member"
              for OGs) pill. In beta everyone is Pro with no billing, so the
              checkout + portal buttons must NOT render. Usage rows are
              filtered to only show counters that are ≥70% of their limit -
              the panel stays clean when usage is light. */}
          {betaMode ? (() => {
            const pillLabel = isOG ? '\uD83D\uDC51 OG Member' : 'Free beta';
            const tagline = isOG
              ? 'Permanent Pro access'
              : 'All features unlocked - no billing';
            const pillBg = isOG ? 'rgba(245,158,11,0.15)' : 'rgba(16,185,129,0.15)';
            const pillColor = isOG ? '#f59e0b' : '#10b981';

            // Show every capped row when expanded (no 70% filter). The
            // per-meal AI-edit cap is omitted - it's per-session, not
            // daily, so it has no stable "used" value here; the inline
            // counter in CorrectionChat surfaces that contextually.
            const capRows: Array<{ label: string; cap: ReturnType<typeof useCapState> }> = [
              { label: 'Photo scans today', cap: imageCap },
              { label: 'Text meals today', cap: textMealCap },
              { label: 'AI chats today', cap: chatCap },
              { label: 'Saved meals', cap: savedMealsCap },
            ];
            const visibleRows = capRows.filter(r => r.cap.hasCap);

            return (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Pro Monthly</p>
                    <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>{tagline}</p>
                  </div>
                  <span
                    className="text-xs font-semibold px-2.5 py-1 rounded-full whitespace-nowrap shrink-0"
                    style={{ background: pillBg, color: pillColor }}
                  >
                    {pillLabel}
                  </span>
                </div>
                {subExpanded && visibleRows.length > 0 && (
                  <div className="space-y-3 pt-1">
                    {visibleRows.map(({ label, cap }) => (
                      <UsageMeter key={label} cap={cap} label={label} compact />
                    ))}
                  </div>
                )}
              </div>
            );
          })()

          /* Founding member */
          : foundingMember ? (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Founding Member</p>
                <p className="text-xs mt-0.5" style={{ color: '#f59e0b' }}>Pro features unlocked - no billing needed</p>
              </div>
              <span className="text-xs font-semibold px-2.5 py-1 rounded-full" style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>{'\uD83D\uDC51'} OG</span>
            </div>

          /* Trial active - countdown + progress bar */
          ) : isPremium && plan === 'trial' ? (() => {
            const daysLeft = trialEndsAt ? Math.max(0, Math.ceil((new Date(trialEndsAt).getTime() - Date.now()) / 86400000)) : 7;
            const elapsed = ((7 - daysLeft) / 7) * 100;
            const barColor = daysLeft <= 2 ? '#f59e0b' : '#10b981';
            return (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Pro Trial</p>
                    <p className="text-xs mt-0.5" style={{ color: barColor }}>{daysLeft} day{daysLeft !== 1 ? 's' : ''} left</p>
                  </div>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        const data = await subscriptionApi.checkout('pro_monthly');
                        if (data.url) window.location.href = data.url;
                        else toast(data.error || 'Billing not available', 'error');
                      } catch { toast('Could not start checkout', 'error'); }
                    }}
                  >Go Pro - $4.99/mo</Button>
                </div>
                {subExpanded && (
                  <>
                    <div className="w-full h-1.5 rounded-full" style={{ background: 'var(--bg-elevated)' }}>
                      <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(100, elapsed)}%`, background: barColor }} />
                    </div>
                    {/* CA ARL / FTC Click-to-Cancel - required inline auto-renewal disclosure. */}
                    <p className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                      $4.99 billed monthly. Auto-renews until cancelled. Cancel any time in Settings → Subscription.
                    </p>
                  </>
                )}
              </div>
            );
          })()

          /* Paid Pro (active or cancelled-but-still-in-period) */
          : isPremium ? (() => {
            const isCancelled = subStatus === 'cancelled' && currentPeriodEnd;
            const endsDate = isCancelled ? new Date(currentPeriodEnd).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : null;
            const daysLeft = isCancelled ? Math.max(0, Math.ceil((new Date(currentPeriodEnd).getTime() - Date.now()) / 86400000)) : 0;
            return (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Pro</p>
                  {isCancelled ? (
                    <p className="text-xs mt-0.5" style={{ color: '#f59e0b' }}>
                      Cancels {endsDate} ({daysLeft} day{daysLeft !== 1 ? 's' : ''} left)
                    </p>
                  ) : (
                    <p className="text-xs mt-0.5" style={{ color: '#10b981' }}>All features unlocked</p>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={async (e) => {
                    e.stopPropagation();
                    try {
                      const data = await subscriptionApi.portal();
                      if (data.url) window.location.href = data.url;
                      else toast(data.error || 'Billing not available', 'error');
                    } catch { toast('Could not open billing portal', 'error'); }
                  }}
                >Manage</Button>
              </div>
              {subExpanded && isCancelled && (
                <div className="w-full h-1.5 rounded-full" style={{ background: 'var(--bg-elevated)' }}>
                  <div className="h-full rounded-full transition-all" style={{
                    width: `${Math.min(100, ((30 - daysLeft) / 30) * 100)}%`,
                    background: daysLeft <= 3 ? '#ef4444' : '#f59e0b',
                  }} />
                </div>
              )}
              {/* Daily photo-scan cap (applies to pro too - protects AI cost).
                  Each retry on an image meal counts toward the same cap.
                  Also show text-meal + chat caps here so a paid Pro can
                  see every cap in one place when expanded. */}
              {subExpanded && (
                <div className="space-y-3">
                  {[
                    { label: 'Photo scans today', cap: imageCap },
                    { label: 'Text meals today', cap: textMealCap },
                    { label: 'AI chats today', cap: chatCap },
                  ].filter(r => r.cap.hasCap).map(({ label, cap }) => (
                    <UsageMeter key={label} cap={cap} label={label} compact />
                  ))}
                </div>
              )}
            </div>
            );
          })()

          /* Free tier - usage dashboard */
          : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Free</p>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={async (e) => {
                    e.stopPropagation();
                    try {
                      const data = await subscriptionApi.checkout('pro_monthly');
                      if (data.url) window.location.href = data.url;
                      else toast(data.error || 'Billing not available', 'error');
                    } catch { toast('Could not start checkout', 'error'); }
                  }}
                >Go Pro</Button>
              </div>
              {subExpanded && (
                <>
                  {/* CA ARL / FTC Click-to-Cancel - required inline auto-renewal disclosure. */}
                  <p className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                    $4.99 billed monthly. Auto-renews until cancelled. Cancel any time in Settings → Subscription.
                  </p>
                  {/* Free limits dashboard - always shown when expanded so
                      the user knows where they stand before hitting a cap. */}
                  <div className="space-y-3">
                    {[
                      { label: 'Photo scans today', cap: imageCap },
                      { label: 'Text meals today', cap: textMealCap },
                      { label: 'AI chats', cap: chatCap },
                      { label: 'Saved meals', cap: savedMealsCap },
                    ].filter(r => r.cap.hasCap).map(({ label, cap }) => (
                      <UsageMeter key={label} cap={cap} label={label} compact />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Goals & Targets - nav row */}
      <Link to="/settings/goals" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}>
          <Target className="w-5 h-5 text-emerald-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Goals & Targets</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {GOAL_LABELS[profile.goal || ''] || 'Not set'} · {Math.round(targets.calories)} kcal
          </p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      <h2 className="section-heading" style={{ marginTop: '0.75rem' }}>Habits</h2>

      {/* Reminders - nav row */}
      <Link to="/settings/reminders" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.2)' }}>
          <Bell className="w-5 h-5" style={{ color: '#f97316' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Reminders</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {prefs?.reminders_on ? 'Enabled' : 'Disabled'} · {prefs?.timezone?.replace(/_/g, ' ').split('/').pop() || ''}
          </p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Achievements - nav row */}
      <Link to="/settings/achievements" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.2)' }}>
          <Trophy className="w-5 h-5" style={{ color: '#fbbf24' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Achievements</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>View your badges</p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Tips & Shortcuts - nav row */}
      <Link to="/settings/tips" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}>
          <Lightbulb className="w-5 h-5" style={{ color: '#3b82f6' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Tips & Shortcuts</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Get the most out of MacroShot</p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Coach Memory - nav row */}
      <Link to="/settings/memory" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}>
          <Sparkles className="w-5 h-5" style={{ color: '#10b981' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Coach Memory</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>What the AI coach remembers about you</p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      <h2 className="section-heading" style={{ marginTop: '0.75rem' }}>Fitness Apps</h2>

      {/* Connected Apps - nav row */}
      <Link to="/settings/connected-apps" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}>
          <Zap className="w-5 h-5" style={{ color: '#3b82f6' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Connected Apps</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Strava, Fitbit, and more</p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Workout Calorie Adjustment - controls how logged workouts affect
          the daily calorie target. Lives here so it sits next to the
          fitness-app integrations that feed it. */}
      {prefs && (
        <div className="glass-card p-5 space-y-3">
          <h2 className="section-heading">Workout Calories</h2>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4" style={{ color: '#3b82f6' }} />
              <span className="text-sm">Exercise calorie adjustment</span>
            </div>
            <button
              onClick={async () => {
                hapticLight();
                const newVal = prefs.exercise_adjustment_on ? 0 : 1;
                const updated = { ...prefs, exercise_adjustment_on: newVal };
                prefsDirtyRef.current = true; setPrefs(updated);
                try {
                  await api.put('/settings/prefs', updated);
                  navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
                } catch {
                  toast('Failed to save setting', 'error');
                }
              }}
              className="w-11 h-6 rounded-full transition-colors relative"
              style={{ background: prefs.exercise_adjustment_on ? '#059669' : 'var(--track-bg)' }}
            >
              <div className={`w-4 h-4 rounded-full absolute top-1 transition-transform shadow-sm bg-white ${prefs.exercise_adjustment_on ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          {!!prefs.exercise_adjustment_on && (
            <div className="flex items-center justify-between">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Eat-back percentage</span>
              <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
                {([50, 75, 100] as const).map((pct) => (
                  <button
                    key={pct}
                    onClick={async () => {
                      hapticLight();
                      const updated = { ...prefs, exercise_eat_back_pct: pct / 100 };
                      prefsDirtyRef.current = true; setPrefs(updated);
                      try {
                        await api.put('/settings/prefs', updated);
                        navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
                      } catch {
                        toast('Failed to save setting', 'error');
                      }
                    }}
                    className={`px-2.5 py-1 rounded-md text-xs font-bold transition-all ${Math.round((prefs.exercise_eat_back_pct ?? 0.75) * 100) === pct ? 'text-blue-400' : ''}`}
                    style={{
                      background: Math.round((prefs.exercise_eat_back_pct ?? 0.75) * 100) === pct ? 'rgba(59,130,246,0.2)' : undefined,
                      color: Math.round((prefs.exercise_eat_back_pct ?? 0.75) * 100) !== pct ? 'var(--text-muted)' : undefined,
                    }}
                  >
                    {pct}%
                  </button>
                ))}
              </div>
            </div>
          )}
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {prefs.exercise_adjustment_on
              ? `Adds ${Math.round((prefs.exercise_eat_back_pct ?? 0.75) * 100)}% of workout calories to your daily target.`
              : 'Workout calories won\'t adjust your daily target.'}
          </p>
        </div>
      )}

      {/* Install App - only when running in browser, not as installed PWA */}
      {canInstall && (
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">📲</span>
            <h2 className="section-heading">Install App</h2>
          </div>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            {isInApp
              ? "You're in an in-app browser. Install MacroShot on your home screen for full-screen access."
              : 'Install MacroShot on your home screen for full-screen, offline-ready access with push reminders.'}
          </p>
          <Button
            variant="primary"
            className="w-full"
            onClick={() => {
              hapticLight();
              // Android can install directly without a modal
              if (!isIOS && !isInApp) {
                install();
              } else {
                setShowInstallModal(true);
              }
            }}
          >
            Install App
          </Button>
        </div>
      )}

      <h2 className="section-heading" style={{ marginTop: '0.75rem' }}>Preferences</h2>

      {/* Appearance + Units */}
      <div className="glass-card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {theme === 'dark' ? <Moon className="w-4 h-4" style={{ color: 'var(--text-secondary)' }} /> : <Sun className="w-4 h-4 text-amber-400" />}
            <span className="text-sm">Dark mode</span>
          </div>
          <button
            onClick={() => { hapticLight(); toggleTheme(); }}
            className="w-11 h-6 rounded-full transition-colors relative"
            style={{ background: theme === 'dark' ? '#059669' : 'var(--track-bg)' }}
          >
            <div className={`w-4 h-4 rounded-full absolute top-1 transition-transform shadow-sm bg-white ${theme === 'dark' ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>
        {prefs && (
          <div className="flex items-center justify-between pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <div className="flex items-center gap-2">
              <Scale className="w-4 h-4 text-amber-400" />
              <span className="text-sm">Units</span>
            </div>
            <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
              {(['metric', 'imperial'] as const).map((u) => (
                <button
                  key={u}
                  onClick={async () => {
                    hapticLight();
                    const updated = { ...prefs, units_system: u };
                    prefsDirtyRef.current = true; setPrefs(updated);
                    localStorage.setItem('weight_unit', u === 'imperial' ? 'lbs' : 'kg');
                    const cm = getCached<{ user: any; targets: any; prefs: any; profile: any }>('settings_main');
                    if (cm) setCache('settings_main', { ...cm, prefs: updated });
                    try {
                      await api.put('/settings/prefs', updated);
                      savedPrefsRef.current = JSON.stringify(updated);
                      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
                    } catch {
                      toast('Failed to save setting', 'error');
                    }
                  }}
                  className={`px-3 py-1 rounded-md text-xs font-bold transition-all capitalize ${prefs.units_system === u ? 'bg-amber-500/20 text-amber-400' : ''}`}
                  style={prefs.units_system !== u ? { color: 'var(--text-muted)' } : undefined}
                >
                  {u === 'metric' ? 'kg / cm' : 'lbs / ft'}
                </button>
              ))}
            </div>
          </div>
        )}
        {prefs && (
          <div className="pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Trophy className="w-4 h-4" style={{ color: '#fbbf24' }} />
                <span className="text-sm">Gamification</span>
              </div>
              <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
                {(['full', 'off'] as const).map((g) => (
                  <button
                    key={g}
                    onClick={async () => {
                      hapticLight();
                      const updated = { ...prefs, gamification: g };
                      prefsDirtyRef.current = true; setPrefs(updated);
                      // Update cache so it persists across navigations
                      const cachedMain = getCached<{ user: any; targets: any; prefs: any; profile: any }>('settings_main');
                      if (cachedMain) setCache('settings_main', { ...cachedMain, prefs: updated });
                      // Notify Header immediately (no navigation needed)
                      window.dispatchEvent(new CustomEvent('gamification-changed', { detail: g }));
                      try {
                        await api.put('/settings/prefs', updated);
                        savedPrefsRef.current = JSON.stringify(updated);
                        navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
                      } catch {
                        toast('Failed to save setting', 'error');
                      }
                    }}
                    className={`px-3 py-1 rounded-md text-xs font-bold transition-all capitalize ${(prefs.gamification ?? 'full') === g ? 'bg-amber-500/20 text-amber-400' : ''}`}
                    style={(prefs.gamification ?? 'full') !== g ? { color: 'var(--text-muted)' } : undefined}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
              {(prefs.gamification ?? 'full') === 'full' && 'Badges, celebrations, shields, and achievements page.'}
              {(prefs.gamification ?? 'full') === 'off' && 'Badges, shields, and celebrations are hidden.'}
            </p>
          </div>
        )}
      </div>

      {/* Install modal - opened from the button above */}
      <Modal open={showInstallModal} onClose={() => setShowInstallModal(false)} position="bottom">
        <div className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-lg">📲</span>
              <h2 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>
                {isInApp ? 'Open in your browser' : 'Install MacroShot'}
              </h2>
            </div>
            <button
              onClick={() => setShowInstallModal(false)}
              className="p-1 rounded-full active:scale-90 transition-transform"
              aria-label="Close"
            >
              <span className="text-xl" style={{ color: 'var(--text-muted)' }}>×</span>
            </button>
          </div>

          {isInApp ? (
            <>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                You're in an in-app browser. Tap the ⋯ menu and choose <strong>Open in Safari</strong> (or your default browser), then install from there.
              </p>
              <InAppBrowserAnimation />
              <Button
                variant="secondary"
                className="w-full"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(window.location.origin + '/macro_app/');
                    toast('Link copied - paste it into Safari');
                  } catch { toast('Copy failed', 'error'); }
                }}
              >
                Copy link
              </Button>
            </>
          ) : (
            <>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                Watch the steps below, then follow along in your browser.
              </p>
              <IOSInstallAnimation variant={isIpad ? 'ipad' : 'ios'} />
              <Button variant="secondary" className="w-full" onClick={() => setShowInstallModal(false)}>
                Got it
              </Button>
            </>
          )}
        </div>
      </Modal>

      {/* Data & Account */}
      <div className="glass-card p-5 space-y-3">
        <h2 className="section-heading">Data & Account</h2>
        {authUser && <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Signed in as {authUser.email}</p>}

        {/* Export buttons - side by side */}
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--text-muted)' }}>Export Your Data</p>
          <div className="grid grid-cols-2 gap-2.5">
            <button
              className="rounded-xl p-3.5 text-center transition-all duration-200 active:scale-[0.97]"
              style={{ background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)' }}
              onClick={async () => {
                hapticLight(); toast('Preparing export...');
                try {
                  const res = await fetch('/macro_app/api/v1/settings/export', { credentials: 'include' });
                  if (!res.ok) throw new Error();
                  const blob = await res.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `macroshot_export_${new Date().toISOString().split('T')[0]}.csv`;
                  a.click();
                  URL.revokeObjectURL(url);
                  toast('Data exported!');
                } catch { toast('Export failed', 'error'); }
              }}
            >
              <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-2" style={{ background: 'rgba(59,130,246,0.12)', border: '1px solid rgba(59,130,246,0.2)' }}>
                <Download className="w-5 h-5" style={{ color: '#3b82f6' }} />
              </div>
              <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>CSV</p>
              <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>Raw meal data</p>
            </button>

            <button
              className={`relative rounded-xl p-3.5 text-center transition-all duration-200 active:scale-[0.97] ${exporting ? 'opacity-60 pointer-events-none' : ''}`}
              style={{ background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.15)' }}
              disabled={exporting}
              onClick={async () => {
                if (!isPremium) {
                  try {
                    await subscriptionApi.checkout('pro_monthly');
                  } catch {
                    toast('Upgrade to Pro to export PDF reports', 'info');
                  }
                  return;
                }
                hapticLight(); setExporting(true);
                toast('Generating PDF report...');
                try {
                  const res = await fetch('/macro_app/api/v1/settings/export?format=pdf', { credentials: 'include' });
                  if (!res.ok) throw new Error();
                  const blob = await res.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `macroshot_report_${new Date().toISOString().split('T')[0]}.pdf`;
                  a.click();
                  URL.revokeObjectURL(url);
                  toast('PDF exported!');
                } catch { toast('PDF export failed', 'error'); }
                finally { setExporting(false); }
              }}
            >
              {!isPremium && (
                <div className="absolute top-2 right-2">
                  <Lock className="w-3 h-3" style={{ color: '#a855f7' }} />
                </div>
              )}
              <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-2" style={{ background: 'rgba(168,85,247,0.12)', border: '1px solid rgba(168,85,247,0.2)' }}>
                {exporting ? <LoadingSpinner size="sm" /> : <Sparkles className="w-5 h-5" style={{ color: '#a855f7' }} />}
              </div>
              <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{exporting ? 'Generating...' : 'PDF Report'}</p>
              <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{isPremium ? 'Nutrition report' : 'Pro feature'}</p>
            </button>
          </div>
        </div>

        <div style={{ borderTop: '1px solid var(--border-glass)' }} />

        {/* Check for Updates */}
        <button
          className="flex items-center gap-3 w-full py-2.5 text-left"
          onClick={async () => {
            hapticLight();
            const {
              checkForUpdate,
              isUpdateAvailable,
              applyUpdate,
              onPrecacheProgress,
              getPrecacheProgress,
            } = await import('../utils/swManager');

            // Subscribe to precache progress for the duration of this flow.
            setUpdateProgress(getPrecacheProgress());
            const unsubProgress = onPrecacheProgress((p) => setUpdateProgress(p));

            // Already-precached update from a previous tab - skip to apply.
            if (isUpdateAvailable()) {
              setUpdatePhase('applying');
              applyUpdate();
              // Reload will dismiss the modal; no cleanup needed for unsub
              // because the page goes away. Still detach to be safe if reload
              // is delayed by the watchdog.
              setTimeout(unsubProgress, 12_000);
              return;
            }

            setUpdatePhase('checking');
            // Abort any previous in-flight check (e.g. user double-tapped)
            // before starting a new one.
            updateAbortRef.current?.abort();
            updateAbortRef.current = new AbortController();
            const found = await checkForUpdate(
              () => setUpdatePhase('downloading'),
              updateAbortRef.current.signal,
            );
            if (found) {
              setUpdatePhase('applying');
              applyUpdate();
              setTimeout(unsubProgress, 12_000);
            } else {
              // No new SW found - don't reset caches or reload. The dedicated
              // "Refresh app data" button below handles the stale-data case
              // when the user actually wants it. Auto-reloading here surprises
              // the user and used to trigger an iOS spurious-controllerchange
              // re-reload that left the route content blank.
              unsubProgress();
              setUpdatePhase('uptodate');
            }
          }}
        >
          <div className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}>
            <RefreshCw className="w-4 h-4" style={{ color: '#10b981' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Check for Updates</p>
          </div>
          <ChevronRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
        </button>

        <div style={{ borderTop: '1px solid var(--border-glass)' }} />

        {/* Refresh app data - narrow clear (API caches only). Preserves auth,
            tooltips, onboarded state, offline queue, and meal images. Users
            rarely need this after the auto-clear-on-update flow; kept as a
            troubleshooting nicety. */}
        <button
          className="flex items-center gap-3 w-full py-2.5 text-left"
          onClick={() => { hapticLight(); setShowResetConfirm(true); }}
        >
          <div className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.2)' }}>
            <RefreshCw className="w-4 h-4" style={{ color: '#f59e0b' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Refresh app data</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Having trouble? Reload with fresh data</p>
          </div>
          <ChevronRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
        </button>

        <div style={{ borderTop: '1px solid var(--border-glass)' }} />

        {/* Log Out */}
        <button
          className="flex items-center gap-3 w-full py-2.5 text-left"
          onClick={() => { hapticLight(); setShowLogoutConfirm(true); }}
        >
          <div className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: 'rgba(148,163,184,0.1)', border: '1px solid rgba(148,163,184,0.2)' }}>
            <LogOut className="w-4 h-4" style={{ color: 'var(--text-secondary)' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Log Out</p>
          </div>
          <ChevronRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
        </button>
      </div>

      <h2 className="section-heading" style={{ marginTop: '0.75rem' }}>Help</h2>

      {/* Support development - shown only when operator has configured a
          donation/sponsor URL via env (DONATE_URL or SPONSOR_URL). Self-hosters
          and bare instances see nothing. Donate URL takes priority since it
          accepts payment from anyone (sponsor URL is GitHub-account-gated). */}
      {(siteConfig.donate_url || siteConfig.sponsor_url) && (
        <a
          href={(siteConfig.donate_url || siteConfig.sponsor_url) as string}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => hapticLight()}
          className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl"
        >
          <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(244,63,94,0.1)', border: '1px solid rgba(244,63,94,0.2)' }}>
            <Heart className="w-5 h-5" style={{ color: '#f43f5e' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">Sponsor the project</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Open source and built in the open. Help keep it actively developed.
            </p>
          </div>
          <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
        </a>
      )}

      {/* Food for Thought - nav row */}
      <Link to="/settings/feedback" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.2)' }}>
          <Lightbulb className="w-5 h-5" style={{ color: '#f59e0b' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">Food for Thought</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Bugs, ideas, or anything else</p>
        </div>
        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
      </Link>

      {/* Admin metrics - visible only to admin users (server still enforces access) */}
      {isAdmin && (
        <Link to="/admin/metrics" className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
          <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}>
            <Lock className="w-5 h-5" style={{ color: '#3b82f6' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">Admin metrics</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Per-user activity insights · private</p>
          </div>
          <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
        </Link>
      )}

      {/* Legal + Branding */}
      <div className="text-center pt-4 pb-8 space-y-3">
        <div className="flex items-center justify-center gap-2">
          <img src="/macro_app/logo-header.png" alt="" className="w-5 h-5" />
          <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>MacroShot</span>
        </div>
        <p className="text-[10px] leading-snug px-6 max-w-xs mx-auto" style={{ color: 'var(--text-muted)' }}>
          AI estimates may be inaccurate.
        </p>
        <div className="flex justify-center gap-4">
          <Link to="/terms" className="text-xs py-2 px-3" style={{ color: 'var(--text-muted)' }}>Terms of Service</Link>
          <span className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>·</span>
          <Link to="/privacy" className="text-xs py-2 px-3" style={{ color: 'var(--text-muted)' }}>Privacy Policy</Link>
        </div>
        <p className="text-[10px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'}
        </p>
      </div>
      </div>

      {/* Delete Account - isolated at bottom */}
      <div className="pb-12 px-4">
        <Button variant="destructive" className="w-full" size="sm" onClick={() => setDeleteStep(1)}>
          <Trash2 className="w-3.5 h-3.5" /> Delete Account
        </Button>
      </div>

      {/* Confirmation dialogs */}
      <UpdateProgressCard
        open={updatePhase !== null}
        phase={updatePhase ?? 'checking'}
        done={updateProgress.done}
        total={updateProgress.total}
        onClose={() => setUpdatePhase(null)}
      />
      <ConfirmDialog open={showLogoutConfirm} title="Log Out" message="Are you sure you want to log out?" confirmLabel="Log Out" destructive onConfirm={() => { setShowLogoutConfirm(false); logout(); }} onCancel={() => setShowLogoutConfirm(false)} />
      <ConfirmDialog open={showResetConfirm} title="Refresh app data" message="This will clear cached data and reload the app with fresh data from the server. You'll stay signed in." confirmLabel="Refresh" onConfirm={async () => {
        setShowResetConfirm(false);
        toast('Refreshing...');
        try {
          const { narrowClearApiCaches } = await import('../utils/swManager');
          await narrowClearApiCaches();
        } catch { /* best effort */ }
        window.location.reload();
      }} onCancel={() => setShowResetConfirm(false)} />
      <ConfirmDialog open={deleteStep === 1} title="Delete Account" message="Are you sure? This will permanently delete ALL your data. This cannot be undone." confirmLabel="Delete Everything" destructive onConfirm={() => setDeleteStep(2)} onCancel={() => setDeleteStep(0)} />
      <ConfirmDialog open={deleteStep === 2} title="Last Chance" message="This is your final confirmation. Delete everything?" confirmLabel="Yes, Delete" destructive onConfirm={() => { setDeleteStep(0); handleDeleteAccount(); }} onCancel={() => setDeleteStep(0)} />
    </div>
  );
}
