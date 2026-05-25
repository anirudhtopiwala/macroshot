import { useEffect, useState, useRef, useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Shield, Check, Lock } from '../components/icons';
import BackButton from '../components/BackButton';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { useSubscription } from '../context/SubscriptionContext';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import Button from '../components/Button';
import { achievementsApi, type ShieldHistoryEntry } from '../api/achievements';
import { getCached, setCache } from '../utils/apiCache';
import type { Badge, AchievementsResponse, Challenge, ChallengesResponse, AchievementSummaryResponse } from '../types';
import { hapticLight } from '../utils/haptics';

const TIER_COLORS: Record<string, string> = {
  bronze: '#CD7F32',
  silver: '#9CA3AF',  // darker silver for light mode readability
  gold: '#D4A017',    // darker gold for light mode readability
  diamond: '#A78BFA',  // violet-400 - premium/exclusive
  locked: 'var(--text-muted)',
};

const TIER_DOTS = ['bronze', 'silver', 'gold', 'diamond'];

// Frontend-only display name overrides for badge categories
const CATEGORY_DISPLAY_NAMES: Record<string, string> = {
  'Explorer': 'AI Features',
  'Meta': 'Collection',
};

// Frontend icon overrides when backend icons need correction
const ICON_OVERRIDES: Record<string, string> = {
  'variety_saved_chef': '\u{1F468}\u200D\u{1F373}', // chef emoji
};

// Fixed display order for milestone checklist
const MILESTONE_BADGE_ORDER = [
  'milestone_first_meal',
  'milestone_first_day',
  'explorer_goal_setter',
  'milestone_first_week',
];

function isMilestoneBadge(badge: Badge): boolean {
  return badge.thresholds.length === 1;
}

// Map challenge IDs to the most related badge ID for detail popup
const CHALLENGE_TO_BADGE: Record<string, string> = {
  'protein_30': 'target_protein',
  'three_meals': 'meals_meal_machine',
  'four_meals': 'streak_full_day',
  'photo_log': 'meals_snap_happy',
  'balanced_meal': 'target_macro_master',
  'early_log': 'streak_early_bird',
  'under_target': 'target_bullseye',
  'week_target_5': 'target_perfect_week',
  'week_log_20': 'meals_meal_machine',
  'week_every_day': 'streak_on_a_roll',
  'week_photos_5': 'meals_snap_happy',
  'week_variety_7': 'variety_world_plate',
};

/** Infer a short unit label from badge_id for next-milestone hints. */
function getBadgeUnit(badgeId: string): string {
  if (badgeId.startsWith('milestone_')) return 'more';
  if (badgeId === 'streak_comeback') return 'comebacks';
  if (badgeId.startsWith('streak_') || badgeId === 'target_perfect_week') return 'days';
  if (badgeId.startsWith('meals_') || badgeId === 'variety_world_plate') return 'meals';
  if (badgeId === 'variety_quick_draw') return 'quick logs';
  if (badgeId === 'variety_saved_chef') return 'saved meals';
  if (badgeId === 'weight_trend_setter') return 'weeks';
  if (badgeId === 'weight_scale_warrior') return 'weigh-ins';
  if (badgeId === 'target_bullseye' || badgeId === 'target_protein' || badgeId === 'target_macro_master' || badgeId === 'target_carbs_master') return 'days';
  if (badgeId === 'explorer_coach_fav') return 'sessions';
  if (badgeId === 'explorer_perfectionist') return 'corrections';
  if (badgeId === 'explorer_goal_setter') return 'more';
  if (badgeId === 'meta_completionist') return 'badges';
  return 'more';
}

// ── Milestone Card (square grid card, no tier dots) ──

function MilestoneCard({ badge, onTap }: { badge: Badge; onTap: (b: Badge) => void }) {
  const earned = badge.tier >= 0;
  const displayIcon = ICON_OVERRIDES[badge.badge_id] || badge.icon;
  const inProgress = !earned && badge.current_value > 0;
  const progress = badge.next_threshold
    ? Math.min(badge.current_value / badge.next_threshold, 1)
    : earned ? 1 : 0;

  return (
    <button
      onClick={() => { hapticLight(); onTap(badge); }}
      className="glass-card p-3.5 flex flex-col items-center text-center w-full relative"
      style={{ opacity: earned ? 1 : inProgress ? 0.7 : 0.35 }}
    >
      <div
        className="w-14 h-14 rounded-full flex items-center justify-center mb-2 relative"
        style={{
          background: earned ? 'rgba(16,185,129,0.12)' : 'var(--bg-elevated)',
          border: earned ? '2px solid #10b981' : '2px solid transparent',
          boxShadow: earned ? '0 0 20px rgba(16,185,129,0.2)' : undefined,
        }}
      >
        <span className="text-2xl">{displayIcon}</span>
        {earned && (
          <div className="absolute -bottom-0.5 -right-0.5 w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center">
            <Check className="w-3 h-3 text-white" />
          </div>
        )}
      </div>
      <p className="text-xs font-bold w-full line-clamp-2 leading-tight" style={{ color: earned ? 'var(--text-primary)' : 'var(--text-muted)' }}>
        {badge.name}
      </p>
      <div className="w-full h-1 rounded-full mt-2 overflow-hidden" style={{ background: 'var(--track-bg)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${progress * 100}%`,
            background: earned ? '#10b981' : inProgress ? '#10b981' : 'var(--text-muted)',
          }}
        />
      </div>
      {badge.next_threshold && !earned ? (
        <p className="text-[11px] mt-1 tabular-nums font-medium" style={{ color: 'var(--text-muted)' }}>
          {badge.current_value} / {badge.next_threshold}
        </p>
      ) : earned ? (
        <p className="text-[11px] mt-1 font-medium" style={{ color: '#10b981' }}>Earned</p>
      ) : null}
    </button>
  );
}

// ── Badge Card (progressive badges in category grid) ──

function BadgeCard({ badge, onTap }: { badge: Badge; onTap: (b: Badge) => void }) {
  const earned = badge.tier >= 0;
  const inProgress = !earned && badge.current_value > 0;
  const color = TIER_COLORS[badge.tier_name] || TIER_COLORS.locked;
  const progress = badge.next_threshold
    ? Math.min(badge.current_value / badge.next_threshold, 1)
    : earned ? 1 : 0;
  const displayIcon = ICON_OVERRIDES[badge.badge_id] || badge.icon;

  const remaining = badge.next_threshold ? badge.next_threshold - badge.current_value : 0;
  const nextTierIdx = badge.tier + 1;
  const nextTierName = nextTierIdx >= 0 && nextTierIdx < TIER_DOTS.length ? TIER_DOTS[nextTierIdx] : null;
  const showHint = badge.current_value > 0 && remaining > 0 && nextTierName;

  return (
    <div className="relative">
      <button
        onClick={() => { hapticLight(); onTap(badge); }}
        className="glass-card p-3.5 flex flex-col items-center text-center transition-opacity w-full"
        style={{
          opacity: earned ? 1 : inProgress ? 0.7 : 0.35,
          border: inProgress ? '1px solid rgba(16,185,129,0.3)' : undefined,
          boxShadow: inProgress
            ? '0 0 12px rgba(16,185,129,0.1)'
            : earned
              ? `0 0 12px ${color}15`
              : undefined,
        }}
      >
        {/* Animation only for gold (tier 2) and diamond (tier 3) - bronze/silver are intentionally static */}
        <div
          className={`w-14 h-14 rounded-full flex items-center justify-center mb-2${earned && badge.tier >= 2 ? ' animate-tier-glow' : ''}`}
          style={{
            background: earned ? `${color}15` : 'var(--bg-elevated)',
            border: earned ? `2px solid ${color}` : '2px solid transparent',
            ...(!earned ? {} :
              badge.tier_name === 'diamond' ? {
                boxShadow: `0 0 24px ${color}35, 0 0 48px ${color}15`,
                '--glow-shadow-min': `0 0 24px ${color}35, 0 0 48px ${color}15`,
                '--glow-shadow-max': `0 0 32px ${color}55, 0 0 64px ${color}30`,
              } as React.CSSProperties :
              badge.tier_name === 'gold' ? {
                boxShadow: `0 0 20px ${color}30`,
                '--glow-shadow-min': `0 0 20px ${color}30`,
                '--glow-shadow-max': `0 0 28px ${color}50, 0 0 48px ${color}20`,
              } as React.CSSProperties :
              badge.tier_name === 'silver' ? {
                boxShadow: `0 0 16px ${color}20`,
              } : {
                boxShadow: `0 0 12px ${color}15`,
              }),
          }}
        >
          <span className="text-2xl">{displayIcon}</span>
        </div>
        <p className="text-xs font-bold w-full line-clamp-2 leading-tight" style={{ color: earned ? 'var(--text-primary)' : 'var(--text-muted)' }}>
          {badge.name}
        </p>
        {earned ? (
          <span
            className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full mt-1.5"
            style={{ background: `${color}20`, color, border: `1px solid ${color}30` }}
          >
            {badge.tier_name}
          </span>
        ) : (
          <div className="flex gap-1 mt-1.5">
            {TIER_DOTS.map((t, i) => (
              <div
                key={t}
                className="w-2 h-2 rounded-full"
                style={{
                  background: i <= badge.tier ? TIER_COLORS[t] : 'var(--track-bg)',
                  boxShadow: i <= badge.tier ? `0 0 4px ${TIER_COLORS[t]}40` : undefined,
                }}
              />
            ))}
          </div>
        )}
        <div className="w-full h-1 rounded-full mt-2 overflow-hidden" style={{ background: 'var(--track-bg)' }}>
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${progress * 100}%`,
              background: earned ? color : inProgress ? '#10b981' : 'var(--text-muted)',
            }}
          />
        </div>
        <p className="text-[11px] mt-1 tabular-nums font-medium" style={{ color: 'var(--text-muted)' }}>
          {badge.current_value}{badge.next_threshold ? ` / ${badge.next_threshold}` : ''}
        </p>
        {showHint && (
          <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {remaining} more {getBadgeUnit(badge.badge_id)} to {nextTierName}
          </p>
        )}
      </button>
    </div>
  );
}

// ── Challenge Card (square grid card, clickable) ──

function ChallengeCard({ challenge, onTap }: { challenge: Challenge; onTap: () => void }) {
  const pct = challenge.target > 0 ? Math.min(challenge.progress / challenge.target, 1) : 0;
  const isDaily = challenge.type === 'daily';
  const color = challenge.completed ? '#10b981' : (isDaily ? '#3b82f6' : '#a855f7');

  return (
    <button
      onClick={() => { hapticLight(); onTap(); }}
      className="glass-card p-3.5 flex flex-col items-center text-center w-full"
      style={{ opacity: challenge.completed ? 0.7 : 1 }}
    >
      <div
        className="w-14 h-14 rounded-full flex items-center justify-center mb-2 relative"
        style={{
          background: challenge.completed ? 'rgba(16,185,129,0.12)' : challenge.progress > 0 ? `${color}12` : 'var(--bg-elevated)',
          border: challenge.completed || challenge.progress > 0 ? `2px solid ${color}` : '2px solid transparent',
          boxShadow: challenge.completed
            ? '0 0 20px rgba(16,185,129,0.2)'
            : challenge.progress > 0 ? `0 0 16px ${color}20` : undefined,
        }}
      >
        {challenge.completed
          ? <Check className="w-6 h-6 text-emerald-400" />
          : <span className="text-2xl">{challenge.icon}</span>
        }
      </div>
      <p className="text-xs font-bold w-full line-clamp-2 leading-tight" style={{ color: challenge.completed ? 'var(--text-muted)' : 'var(--text-primary)' }}>
        {challenge.name}
      </p>
      <span
        className="text-[11px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mt-1"
        style={{ background: `${color}15`, color }}
      >
        {isDaily ? 'Daily' : 'Weekly'}
      </span>
      <div className="w-full h-1 rounded-full mt-2 overflow-hidden" style={{ background: 'var(--track-bg)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct * 100}%`, background: color }}
        />
      </div>
      <p className="text-[11px] mt-1 tabular-nums font-medium" style={{ color: 'var(--text-muted)' }}>
        {challenge.progress} / {challenge.target}
      </p>
    </button>
  );
}

// ── Main Page ──

export default function Achievements() {
  const { isPremium } = useSubscription();
  const [searchParams, setSearchParams] = useSearchParams();
  const cached = getCached<AchievementsResponse>('achievements');
  const cachedChallenges = getCached<ChallengesResponse>('challenges');
  const [data, setData] = useState<AchievementsResponse | null>(cached);
  const [challenges, setChallenges] = useState<ChallengesResponse | null>(cachedChallenges);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState(false);
  const [selectedBadge, setSelectedBadge] = useState<Badge | null>(null);
  const closeBadge = useCallback(() => setSelectedBadge(null), []);
  useOverlayHistory(closeBadge, !!selectedBadge);
  const [shieldHistory, setShieldHistory] = useState<ShieldHistoryEntry[]>([]);
  const [summary, setSummary] = useState<AchievementSummaryResponse | null>(null);
  const shieldSectionRef = useRef<HTMLDivElement>(null);
  const badgeSectionRef = useRef<HTMLDivElement>(null);
  const milestoneSectionRef = useRef<HTMLDivElement>(null);

  // Block scroll when badge popup is open
  useEffect(() => {
    if (selectedBadge) {
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = ''; };
    }
  }, [selectedBadge]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [allRes, challengeRes, shieldRes, summaryRes] = await Promise.all([
          achievementsApi.getAll(),
          achievementsApi.getChallenges().catch(() => null),
          achievementsApi.getShieldHistory().catch(() => null),
          achievementsApi.getSummary().catch(() => null),
        ]);
        if (cancelled) return;
        setData(allRes);
        setCache('achievements', allRes);
        if (challengeRes) { setChallenges(challengeRes); setCache('challenges', challengeRes); }
        if (shieldRes) setShieldHistory(shieldRes.history);
        if (summaryRes) setSummary(summaryRes);
      } catch { if (!cancelled) setError(true); }
      finally { if (!cancelled) setLoading(false); }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // Auto-scroll to section, OR deep-link to a specific badge: ?badge=<id>
  // scrolls to the badges section and opens that badge's detail popup.
  // Used by BadgeCelebration's "View" so users land on the badge they just
  // earned, not just somewhere on the page.
  const deepLinkedRef = useRef<string | null>(null);
  useEffect(() => {
    if (loading) return;
    const badgeId = searchParams.get('badge');
    if (badgeId && deepLinkedRef.current !== badgeId) {
      const all = data?.badges || [];
      const target = all.find((b) => b.badge_id === badgeId);
      if (target) {
        deepLinkedRef.current = badgeId;
        setTimeout(() => badgeSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
        // Open the detail popup just after the scroll kicks off so the user
        // doesn't have to hunt for the card themselves.
        setTimeout(() => setSelectedBadge(target), 450);
        // Strip the deep-link param so a later refresh / back-nav doesn't
        // re-pop the modal after the user closes it. The ref guards against
        // re-firing the open in the same lifetime; this guards across mounts.
        setSearchParams({}, { replace: true });
        return;
      }
    }
    const section = searchParams.get('section');
    if (section === 'shields' && shieldSectionRef.current) {
      setTimeout(() => shieldSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300);
    } else if (section === 'badges' && badgeSectionRef.current) {
      setTimeout(() => badgeSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    } else if (section === 'milestones' && milestoneSectionRef.current) {
      setTimeout(() => milestoneSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    }
  }, [searchParams, loading, data, setSearchParams]);

  const badges = data?.badges || [];
  const earned = badges.filter((b) => b.tier >= 0).length;
  const total = badges.length;
  const shieldsAvailable = data?.shields_available || 0;

  // Split badges into milestones and progressive
  const milestoneBadges = useMemo(() => {
    const badgeMap = new Map(badges.map(b => [b.badge_id, b]));
    return MILESTONE_BADGE_ORDER
      .map(id => badgeMap.get(id))
      .filter((b): b is Badge => !!b);
  }, [badges]);

  const allMilestonesEarned = milestoneBadges.length > 0 && milestoneBadges.every(b => b.tier >= 0);

  const progressiveBadges = useMemo(() =>
    badges.filter(b => !isMilestoneBadge(b)),
    [badges]
  );

  // "Almost There" - top 3 progressive badges closest to next tier
  const almostThereBadges = useMemo(() => {
    return progressiveBadges
      .filter(b => b.next_threshold !== null && b.current_value > 0)
      .map(b => ({ ...b, _pct: b.current_value / b.next_threshold! }))
      .filter(b => b._pct < 1)
      .sort((a, b) => b._pct - a._pct)
      .slice(0, 3);
  }, [progressiveBadges]);

  // Progressive badges grouped by category with smart sort
  const progressiveCategories = useMemo(() => {
    const cats = [...new Set(progressiveBadges.map(b => b.category))];
    return cats.map(cat => {
      const catBadges = progressiveBadges.filter(b => b.category === cat);
      const sorted = [...catBadges].sort((a, b) => {
        const aState = a.tier >= 0 ? 0 : (a.current_value > 0 ? 1 : 2);
        const bState = b.tier >= 0 ? 0 : (b.current_value > 0 ? 1 : 2);
        if (aState !== bState) return aState - bState;
        if (aState === 0) return b.tier - a.tier; // earned: higher tier first
        if (aState === 1) {
          const aPct = a.next_threshold ? a.current_value / a.next_threshold : 0;
          const bPct = b.next_threshold ? b.current_value / b.next_threshold : 0;
          return bPct - aPct;
        }
        return 0;
      });
      const earnedCount = catBadges.filter(b => b.tier >= 0).length;
      return { cat, sorted, earnedCount, total: catBadges.length };
    }).filter(c => c.sorted.length > 0);
  }, [progressiveBadges]);

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  if (error && !data) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <BackButton fallbackPath="/settings" />
          <h1 className="text-lg font-bold">Achievements</h1>
        </div>
        <EmptyState
          icon={<span className="text-3xl">🏆</span>}
          title="Couldn't load achievements"
          subtitle="Check your connection and try again."
          action={<Button variant="primary" size="sm" onClick={() => window.location.reload()}>Retry</Button>}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">Achievements</h1>
      </div>

      {/* Stats hero */}
      <div className="grid grid-cols-3 gap-3">
        <div className="glass-card p-3 text-center">
          <p className="text-2xl font-black text-emerald-400 tabular-nums">{earned}</p>
          <p className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: 'var(--text-secondary)' }}>
            of {total} earned
          </p>
        </div>
        <div className="glass-card p-3 text-center">
          <p className="text-2xl font-black tabular-nums" style={{ color: '#f97316' }}>
            {badges.find((b) => b.badge_id === 'streak_on_a_roll')?.current_value || 0}
          </p>
          <p className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: 'var(--text-secondary)' }}>
            day streak
          </p>
        </div>
        <div className="glass-card p-3 text-center flex flex-col items-center">
          <div className="flex items-center gap-1">
            <Shield className="w-3.5 h-3.5" style={{ color: '#3b82f6' }} />
            <p className="text-2xl font-black tabular-nums" style={{ color: '#3b82f6' }}>{shieldsAvailable}</p>
          </div>
          <p className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: 'var(--text-secondary)' }}>
            shields
          </p>
        </div>
      </div>

      {/* Streak Shields section */}
      <div ref={shieldSectionRef} className="glass-card p-4">
        <div className="flex items-start gap-3">
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}
          >
            <Shield className="w-6 h-6" style={{ color: '#3b82f6' }} />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between">
              <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Streak Shields</p>
              <div className="flex gap-1.5">
                {[0, 1, 2].map((i) => {
                  const active = i < shieldsAvailable;
                  const locked = !isPremium && i >= 1;
                  return (
                    <div
                      key={i}
                      className="w-6 h-6 rounded-lg flex items-center justify-center transition-all duration-300"
                      style={{
                        background: locked ? 'var(--track-bg)' : active ? 'rgba(59,130,246,0.25)' : 'var(--track-bg)',
                        border: `1.5px solid ${locked ? 'rgba(245,158,11,0.3)' : active ? 'rgba(59,130,246,0.5)' : 'var(--border-glass)'}`,
                        boxShadow: active && !locked ? '0 0 8px rgba(59,130,246,0.3), inset 0 1px 0 rgba(59,130,246,0.2)' : 'none',
                      }}
                    >
                      {locked ? (
                        <Lock className="w-3 h-3" style={{ color: '#f59e0b' }} />
                      ) : (
                        <Shield className="w-3.5 h-3.5" style={{
                          color: active ? '#3b82f6' : 'var(--text-muted)',
                          filter: active ? 'drop-shadow(0 0 2px rgba(59,130,246,0.6))' : 'none',
                        }} />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              {shieldsAvailable > 0
                ? `${shieldsAvailable} shield${shieldsAvailable > 1 ? 's' : ''} ready. Miss a day? A shield saves your streak automatically.`
                : 'Shields protect your streak when you miss a day.'}
            </p>
            <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
              Hit your calorie target (within 20%) for 3 days to earn a shield. Max 3 at a time.
            </p>
            {!isPremium && (
              <p className="text-[11px] mt-1" style={{ color: '#f59e0b' }}>
                Free accounts get 1 shield. Go Pro for unlimited shields.
              </p>
            )}
            {/* Shield earning progress */}
            {summary?.shield_progress && !summary.shields_at_max && (
              <div className="mt-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[11px] font-semibold" style={{ color: 'var(--text-muted)' }}>Next shield</span>
                  <span className="text-[11px] font-bold tabular-nums" style={{ color: '#3b82f6' }}>
                    {summary.shield_progress.on_target_days % 3}/3 days on target
                  </span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${((summary.shield_progress.on_target_days % 3) / 3) * 100}%`,
                      background: '#3b82f6',
                      boxShadow: '0 0 6px rgba(59,130,246,0.4)',
                    }}
                  />
                </div>
                {summary.shield_progress.days_until_next > 0 && (
                  <p className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>
                    {summary.shield_progress.days_until_next} more on-target day{summary.shield_progress.days_until_next > 1 ? 's' : ''} to earn a shield
                  </p>
                )}
              </div>
            )}
            {summary?.shields_at_max && (
              <p className="text-[11px] mt-2 font-semibold" style={{ color: '#3b82f6' }}>
                Max shields! Keep your streak going.
              </p>
            )}
            {/* Shield usage summary */}
            {(() => {
              const usedEntries = shieldHistory.filter(e => !!e.used_at);
              if (usedEntries.length === 0) return null;
              const lastUsed = usedEntries[0];
              const lastDateStr = lastUsed.bridged_date || lastUsed.used_at!;
              const lastLabel = new Date(lastDateStr + (lastDateStr.length <= 10 ? 'T00:00:00' : ''))
                .toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
              const streak = badges.find((b) => b.badge_id === 'streak_on_a_roll')?.current_value || 0;
              return (
                <div className="flex items-center gap-2 mt-2 pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
                  <Shield className="w-3.5 h-3.5 shrink-0" style={{ color: '#f97316' }} />
                  <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                    {usedEntries.length === 1
                      ? `Clutch save on ${lastLabel}`
                      : `${usedEntries.length} clutch saves · last on ${lastLabel}`}
                    {streak > 0 ? ` · keeping a ${streak}-day streak alive` : ''}
                  </p>
                </div>
              );
            })()}
          </div>
        </div>
      </div>

      {/* Challenges */}
      {(challenges?.daily || challenges?.weekly) && (
        <div>
          <h2 className="section-heading mb-2">Challenges</h2>
          <div className="grid grid-cols-2 gap-2">
            {challenges.daily && <ChallengeCard challenge={challenges.daily} onTap={() => {
              const badgeId = CHALLENGE_TO_BADGE[challenges.daily!.id];
              const related = badgeId ? badges.find(b => b.badge_id === badgeId) : null;
              if (related) setSelectedBadge(related);
            }} />}
            {challenges.weekly && <ChallengeCard challenge={challenges.weekly} onTap={() => {
              const badgeId = CHALLENGE_TO_BADGE[challenges.weekly!.id];
              const related = badgeId ? badges.find(b => b.badge_id === badgeId) : null;
              if (related) setSelectedBadge(related);
            }} />}
          </div>
        </div>
      )}

      {/* ── Getting Started (Milestones) - show first if incomplete, move below badges when done ── */}
      {milestoneBadges.length > 0 && !allMilestonesEarned && (
        <div ref={milestoneSectionRef}>
          <h2 className="section-heading mb-2">Getting Started</h2>
          <div className="grid grid-cols-3 gap-2">
            {milestoneBadges.map((badge) => (
              <MilestoneCard key={badge.badge_id} badge={badge} onTap={setSelectedBadge} />
            ))}
          </div>
        </div>
      )}

      {/* ── Almost There ── */}
      {almostThereBadges.length > 0 && (
        <div>
          <h2 className="section-heading mb-2">Almost There</h2>
          <div className="grid grid-cols-3 gap-2">
            {almostThereBadges.map((badge) => (
              <BadgeCard key={badge.badge_id} badge={badge} onTap={setSelectedBadge} />
            ))}
          </div>
        </div>
      )}

      {/* ── Badge Collection (progressive, by category) ── */}
      <div ref={badgeSectionRef}>
        <div className="flex items-center justify-between mb-2">
          <h2 className="section-heading">All Badges</h2>
          <span className="text-[11px] font-bold tabular-nums" style={{ color: 'var(--text-muted)' }}>
            {progressiveBadges.filter(b => b.tier >= 0).length}/{progressiveBadges.length}
          </span>
        </div>
      </div>
      {progressiveCategories.map(({ cat, sorted, earnedCount, total: catTotal }) => {
        const displayName = CATEGORY_DISPLAY_NAMES[cat] || cat;
        return (
          <div key={cat}>
            <div className="flex items-center justify-between mb-2">
              <h2 className="section-heading">{displayName}</h2>
              <span className="text-[11px] tabular-nums font-medium" style={{ color: 'var(--text-muted)' }}>
                {earnedCount}/{catTotal}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {sorted.map((b) => (
                <BadgeCard
                  key={b.badge_id}
                  badge={b}
                  onTap={setSelectedBadge}
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* ── Getting Started (completed - show at bottom) ── */}
      {milestoneBadges.length > 0 && allMilestonesEarned && (
        <div ref={milestoneSectionRef}>
          <div className="flex items-center gap-2 mb-2">
            <h2 className="section-heading">Getting Started</h2>
            <span
              className="text-[11px] font-bold px-2 py-0.5 rounded-full"
              style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981' }}
            >
              All done!
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {milestoneBadges.map((badge) => (
              <MilestoneCard key={badge.badge_id} badge={badge} onTap={setSelectedBadge} />
            ))}
          </div>
        </div>
      )}

      {/* Badge detail popup */}
      {selectedBadge && (
        <div
          className="fixed inset-0 z-[70] flex items-end justify-center bg-black/50 backdrop-blur-sm"
          onClick={() => setSelectedBadge(null)}
        >
          <div
            className="w-full max-w-lg rounded-t-3xl p-6 animate-[slideUp_0.3s_ease-out]"
            style={{ background: 'var(--bg-card)', borderTop: '1px solid var(--border-glass)', paddingBottom: 'calc(2.5rem + env(safe-area-inset-bottom, 0px))' }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Drag handle */}
            <div className="w-10 h-1 rounded-full mx-auto mb-4" style={{ background: 'var(--track-bg)' }} />

            {(() => {
              const isMilestone = isMilestoneBadge(selectedBadge);
              const popupIcon = ICON_OVERRIDES[selectedBadge.badge_id] || selectedBadge.icon;
              const popupCategory = CATEGORY_DISPLAY_NAMES[selectedBadge.category] || selectedBadge.category;
              const popupRemaining = selectedBadge.next_threshold ? selectedBadge.next_threshold - selectedBadge.current_value : 0;
              const popupNextIdx = selectedBadge.tier + 1;
              const popupNextTier = popupNextIdx >= 0 && popupNextIdx < TIER_DOTS.length ? TIER_DOTS[popupNextIdx] : null;
              const popupShowHint = !isMilestone && selectedBadge.current_value > 0 && popupRemaining > 0 && popupNextTier;
              return (
                <div className="flex flex-col items-center text-center gap-3">
                  {/* Animation only for gold (tier 2) and diamond (tier 3) - bronze/silver are intentionally static */}
                  <div
                    className={`w-20 h-20 rounded-full flex items-center justify-center${!isMilestone && selectedBadge.tier >= 2 ? ' animate-tier-glow' : ''}`}
                    style={{
                      background: selectedBadge.tier >= 0
                        ? isMilestone
                          ? 'rgba(16,185,129,0.12)'
                          : `${TIER_COLORS[selectedBadge.tier_name]}15`
                        : 'var(--bg-elevated)',
                      border: selectedBadge.tier >= 0
                        ? isMilestone
                          ? '2.5px solid #10b981'
                          : `2.5px solid ${TIER_COLORS[selectedBadge.tier_name]}`
                        : '2.5px solid transparent',
                      ...(!isMilestone && selectedBadge.tier >= 0 ? (() => {
                        const c = TIER_COLORS[selectedBadge.tier_name];
                        if (selectedBadge.tier_name === 'diamond') return {
                          boxShadow: `0 0 24px ${c}35, 0 0 48px ${c}15`,
                          '--glow-shadow-min': `0 0 24px ${c}35, 0 0 48px ${c}15`,
                          '--glow-shadow-max': `0 0 32px ${c}55, 0 0 64px ${c}30`,
                        } as React.CSSProperties;
                        if (selectedBadge.tier_name === 'gold') return {
                          boxShadow: `0 0 20px ${c}30`,
                          '--glow-shadow-min': `0 0 20px ${c}30`,
                          '--glow-shadow-max': `0 0 28px ${c}50, 0 0 48px ${c}20`,
                        } as React.CSSProperties;
                        return { boxShadow: `0 0 16px ${c}20` };
                      })() : isMilestone && selectedBadge.tier >= 0 ? {
                        boxShadow: '0 0 30px rgba(16,185,129,0.2)',
                      } : {}),
                    }}
                  >
                    <span className="text-4xl">{popupIcon}</span>
                  </div>

                  <div>
                    <p className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{selectedBadge.name}</p>
                    <p className="text-sm mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                      {isMilestone ? 'Milestone' : popupCategory}
                    </p>
                  </div>

                  <p className="text-sm" style={{ color: 'var(--text-primary)' }}>{selectedBadge.description}</p>

                  {/* Milestone: earned/locked indicator */}
                  {isMilestone && (
                    <div className="flex items-center gap-2 mt-1">
                      {selectedBadge.tier >= 0 ? (
                        <>
                          <div className="w-6 h-6 rounded-full bg-emerald-500 flex items-center justify-center">
                            <Check className="w-4 h-4 text-white" />
                          </div>
                          <span className="text-sm font-semibold" style={{ color: '#10b981' }}>Earned</span>
                        </>
                      ) : (
                        <span className="text-sm" style={{ color: 'var(--text-muted)' }}>Not yet earned</span>
                      )}
                    </div>
                  )}

                  {/* Progressive: tier dots */}
                  {!isMilestone && (
                    <div className="flex gap-3 mt-1">
                      {TIER_DOTS.map((t, i) => (
                        <div key={t} className="flex flex-col items-center gap-1">
                          <div
                            className="w-3.5 h-3.5 rounded-full"
                            style={{
                              background: i <= selectedBadge.tier ? TIER_COLORS[t] : 'var(--track-bg)',
                              boxShadow: i <= selectedBadge.tier ? `0 0 6px ${TIER_COLORS[t]}50` : undefined,
                            }}
                          />
                          <span className="text-[11px] font-bold capitalize" style={{
                            color: i <= selectedBadge.tier ? TIER_COLORS[t] : 'var(--text-muted)',
                          }}>{t}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Progress bar (progressive only) */}
                  {!isMilestone && (
                    <div className="w-full mt-2">
                      <div className="w-full h-2 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            width: `${selectedBadge.next_threshold ? Math.min(selectedBadge.current_value / selectedBadge.next_threshold, 1) * 100 : selectedBadge.tier >= 0 ? 100 : 0}%`,
                            background: selectedBadge.tier >= 0
                              ? TIER_COLORS[selectedBadge.tier_name]
                              : 'var(--text-muted)',
                          }}
                        />
                      </div>
                      <p className="text-sm mt-1 tabular-nums font-semibold" style={{ color: 'var(--text-primary)' }}>
                        {selectedBadge.current_value}{selectedBadge.next_threshold ? ` / ${selectedBadge.next_threshold}` : ''}
                      </p>
                      {popupShowHint && (
                        <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                          {popupRemaining} more {getBadgeUnit(selectedBadge.badge_id)} to unlock {popupNextTier}
                        </p>
                      )}
                    </div>
                  )}

                  {selectedBadge.earned_at && (
                    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                      Earned {new Date(selectedBadge.earned_at + (selectedBadge.earned_at.includes('Z') ? '' : 'Z')).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                    </p>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
}
