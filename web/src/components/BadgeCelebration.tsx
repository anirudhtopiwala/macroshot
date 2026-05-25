import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { achievementsApi } from '../api/achievements';
import type { NewBadge } from '../types';

const TIER_COLORS: Record<string, string> = {
  bronze: '#CD7F32',
  silver: '#9CA3AF',
  gold: '#D4A017',
  diamond: '#A78BFA',
  locked: '#6b7280',
};

// Streak badge IDs that get extra celebration
const STREAK_BADGE_IDS = ['streak_on_a_roll'];

// Milestone badges - single-threshold, no tier progression
const MILESTONE_BADGE_IDS = new Set([
  'milestone_first_meal',
  'milestone_first_day',
  'milestone_first_week',
  'explorer_goal_setter',
]);

interface Props {
  badges: NewBadge[];
  onDone: () => void;
  // Fires synchronously just before navigating to the achievements page when
  // the user taps "View". Lets the parent settle any state (e.g. mark the
  // user onboarded) so the navigate isn't bounced by a route guard.
  onView?: () => void;
}

export default function BadgeCelebration({ badges, onDone, onView }: Props) {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<'show' | 'fading'>('show');
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const isMulti = badges.length > 1;
  const hasStreakBadge = badges.some(
    (b) => STREAK_BADGE_IDS.includes(b.badge_id) && b.tier >= 1
  );

  // Mark badges as seen immediately on mount (prevents re-showing after force-close)
  useEffect(() => {
    const badgeIds = badges.map((b) => b.badge_id);
    if (badgeIds.length > 0) {
      achievementsApi.markSeen(badgeIds).catch(() => {});
    }
  }, [badges]);

  const handleDismiss = useCallback(() => {
    clearTimeout(timerRef.current);
    clearTimeout(fadeTimerRef.current);
    setPhase('fading');
    fadeTimerRef.current = setTimeout(onDone, 400);
  }, [onDone]);

  const handleViewAchievements = useCallback((badgeId?: string) => {
    clearTimeout(timerRef.current);
    clearTimeout(fadeTimerRef.current);
    // Let the parent settle any pre-navigation state (e.g. onboarding flag)
    // before the route changes — otherwise a route guard may bounce us back.
    onView?.();
    // Run onDone next so any parent-side navigation in onDone doesn't
    // clobber our navigate to the achievements page.
    onDone();
    // Deep-link to a specific badge when we know which one was tapped so the
    // achievements page can scroll to it and open the detail popup. Falls back
    // to the badges section when no id is given (e.g. multi-badge "View All").
    const target = badgeId
      ? `/settings/achievements?badge=${encodeURIComponent(badgeId)}`
      : '/settings/achievements?section=badges';
    navigate(target);
  }, [navigate, onDone, onView]);

  useEffect(() => {
    // Auto-dismiss after 4s for multi-badge, 3s for single
    const delay = isMulti ? 4000 : 3000;
    timerRef.current = setTimeout(() => {
      setPhase('fading');
      fadeTimerRef.current = setTimeout(onDone, 400);
    }, delay);
    return () => {
      clearTimeout(timerRef.current);
      clearTimeout(fadeTimerRef.current);
    };
  }, [onDone, isMulti]);

  // Block body scroll while modal is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  // Escape dismisses the modal so keyboard-only users aren't trapped. Gated
  // on a visible modal so we don't add a stray window listener on empty
  // renders or during the fade-out (where it would re-fire onDone).
  useEffect(() => {
    if (badges.length === 0 || phase === 'fading') return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') handleDismiss(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handleDismiss, badges.length, phase]);

  if (badges.length === 0) return null;

  // Single badge view
  if (!isMulti) {
    const badge = badges[0];
    const isMilestone = MILESTONE_BADGE_IDS.has(badge.badge_id);
    const color = isMilestone ? '#10b981' : (TIER_COLORS[badge.tier_name] || TIER_COLORS.bronze);
    const isStreakHighTier = STREAK_BADGE_IDS.includes(badge.badge_id) && badge.tier >= 1;

    return (
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isMilestone ? 'Milestone unlocked' : (badge.is_new ? 'Badge unlocked' : 'Badge upgraded')}
        className={`celebration-overlay fixed inset-0 z-[210] flex items-center justify-center bg-black/60 backdrop-blur-sm transition-opacity duration-[400ms] ${
          phase === 'fading' ? 'opacity-0' : 'opacity-100'
        }`}
        onClick={handleDismiss}
      >
        <div
          className={`flex flex-col items-center gap-3 transition-all duration-[400ms] ${
            phase === 'fading' ? 'scale-110 opacity-0' : 'scale-100 opacity-100'
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Confetti text for streak badges */}
          {isStreakHighTier && (
            <div className="absolute top-1/4 left-0 right-0 flex justify-center gap-6 text-2xl animate-bounce pointer-events-none">
              <span className="animate-pulse">&#127881;</span>
              <span className="animate-pulse" style={{ animationDelay: '0.2s' }}>&#127881;</span>
              <span className="animate-pulse" style={{ animationDelay: '0.4s' }}>&#127881;</span>
            </div>
          )}

          {/* Badge icon with glow - tap to view achievements */}
          <div
            className="w-24 h-24 rounded-full flex items-center justify-center animate-[bounce_0.6s_ease-out] cursor-pointer"
            style={{
              background: `radial-gradient(circle, ${color}30 0%, transparent 70%)`,
              boxShadow: isStreakHighTier
                ? `0 0 80px ${color}80, 0 0 140px ${color}40, 0 0 200px ${color}15`
                : `0 0 50px ${color}60, 0 0 100px ${color}20`,
            }}
            onClick={() => handleViewAchievements(badge.badge_id)}
          >
            <span
              className={`text-5xl ${isStreakHighTier ? 'animate-pulse' : ''}`}
              role="img"
              aria-label={`${badge.name} badge icon`}
            >
              {badge.icon}
            </span>
          </div>

          {/* Pulsing flame for streak badges */}
          {isStreakHighTier && (
            <div className="text-3xl animate-bounce" style={{ animationDelay: '0.3s' }}>
              &#128293;
            </div>
          )}

          {/* Tier badge (hidden for milestones) */}
          {!isMilestone && (
            <div
              className="px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider"
              style={{
                background: `${color}20`,
                border: `1px solid ${color}50`,
                color,
              }}
            >
              {badge.tier_name}
            </div>
          )}

          {/* Badge name - tap to view achievements */}
          <p className="text-lg font-bold text-white">
            {isMilestone ? 'Milestone Unlocked!' : (badge.is_new ? 'Badge Unlocked!' : 'Badge Upgraded!')}
          </p>
          <p className="text-base font-semibold cursor-pointer" style={{ color }} onClick={() => handleViewAchievements(badge.badge_id)}>{badge.name}</p>

          {/* Action buttons */}
          <div className="flex items-center gap-3 mt-2">
            <button
              onClick={() => handleViewAchievements(badge.badge_id)}
              className="px-5 py-2 rounded-xl text-sm font-bold transition-all active:scale-95"
              style={{
                background: `${color}15`,
                border: `1px solid ${color}30`,
                color,
              }}
            >
              View
            </button>
            <button
              onClick={handleDismiss}
              className="px-5 py-2 rounded-xl text-sm font-bold transition-all active:scale-95"
              style={{
                background: `${color}20`,
                border: `1px solid ${color}40`,
                color: '#fff',
              }}
            >
              Nice!
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Multi-badge grid view
  return (
    <div
      className={`celebration-overlay fixed inset-0 z-[210] flex items-center justify-center bg-black/60 backdrop-blur-sm transition-opacity duration-[400ms] ${
        phase === 'fading' ? 'opacity-0' : 'opacity-100'
      }`}
      onClick={handleDismiss}
    >
      <div
        className={`flex flex-col items-center gap-4 max-w-xs w-full mx-4 transition-all duration-[400ms] ${
          phase === 'fading' ? 'scale-110 opacity-0' : 'scale-100 opacity-100'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Confetti for streak badges in multi */}
        {hasStreakBadge && (
          <div className="absolute top-1/4 left-0 right-0 flex justify-center gap-6 text-2xl animate-bounce pointer-events-none">
            <span className="animate-pulse">&#127881;</span>
            <span className="animate-pulse" style={{ animationDelay: '0.2s' }}>&#127881;</span>
            <span className="animate-pulse" style={{ animationDelay: '0.4s' }}>&#127881;</span>
          </div>
        )}

        {/* Title */}
        <p className="text-lg font-bold text-white animate-[bounce_0.6s_ease-out]">
          {badges.length} Badges Unlocked!
        </p>

        {/* Badge grid - 2 or 3 columns */}
        <div className={`grid gap-3 w-full ${badges.length <= 4 ? 'grid-cols-2' : 'grid-cols-3'}`}>
          {badges.map((badge) => {
            const badgeIsMilestone = MILESTONE_BADGE_IDS.has(badge.badge_id);
            const color = badgeIsMilestone ? '#10b981' : (TIER_COLORS[badge.tier_name] || TIER_COLORS.bronze);
            const isStreak = STREAK_BADGE_IDS.includes(badge.badge_id) && badge.tier >= 1;
            return (
              <div
                key={badge.badge_id}
                className="flex flex-col items-center gap-1.5 py-3 px-2 rounded-xl cursor-pointer active:scale-95 transition-transform"
                onClick={() => handleViewAchievements(badge.badge_id)}
                style={{
                  background: `${color}10`,
                  border: `1px solid ${color}30`,
                  boxShadow: isStreak
                    ? `0 0 20px ${color}40`
                    : `0 0 12px ${color}15`,
                }}
              >
                <span className={`text-3xl ${isStreak ? 'animate-pulse' : ''}`}>
                  {badge.icon}
                </span>
                {!badgeIsMilestone && (
                  <div
                    className="px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider"
                    style={{
                      background: `${color}20`,
                      border: `1px solid ${color}40`,
                      color,
                    }}
                  >
                    {badge.tier_name}
                  </div>
                )}
                <p className="text-xs font-semibold text-white/90 text-center leading-tight">
                  {badge.name}
                </p>
              </div>
            );
          })}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-3 mt-1">
          <button
            onClick={() => handleViewAchievements()}
            className="px-5 py-2.5 rounded-xl text-sm font-bold transition-all active:scale-95"
            style={{
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.2)',
              color: 'rgba(255,255,255,0.7)',
              backdropFilter: 'blur(12px)',
            }}
          >
            View All
          </button>
          <button
            onClick={handleDismiss}
            className="px-8 py-2.5 rounded-xl text-sm font-bold transition-all active:scale-95"
            style={{
              background: 'rgba(255,255,255,0.12)',
              border: '1px solid rgba(255,255,255,0.25)',
              color: '#fff',
              backdropFilter: 'blur(12px)',
            }}
          >
            Nice!
          </button>
        </div>
      </div>
    </div>
  );
}
