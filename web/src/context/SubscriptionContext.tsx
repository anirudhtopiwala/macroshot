import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { subscriptionApi } from '../api/subscription';
import type { SubscriptionStatus } from '../types';
import { useAuth } from './AuthContext';
import { waitForCritical } from '../utils/requestScheduler';

/**
 * Usage snapshot for a gated feature. `limit === -1` means unlimited -
 * consumers must handle that case explicitly (don't divide or cap to it).
 */
interface UsageInfo {
  used: number;
  limit: number;
}

interface SubscriptionContextValue {
  plan: string;
  status: string;
  isPremium: boolean;
  /** Founding / OG member - keeps Pro forever without billing. */
  isOG: boolean;
  /** Legacy alias of isOG for old call sites. */
  foundingMember: boolean;
  /** 'self' (self-hosted, everything free) or 'hosted' (our SaaS instance). */
  appMode: string;
  /** True while the app is in limited-beta mode: everyone Pro, Stripe disabled. */
  betaMode: boolean;
  imageUsage: UsageInfo;
  /** Text-only meal analyze daily cap. In non-beta this may be -1 (unlimited). */
  textMealUsage: UsageInfo;
  chatUsage: UsageInfo;
  /** Per-meal correction cap. NOT daily - counts retries on a single meal session. */
  mealEditLimit: number;
  savedMealsUsage: UsageInfo;
  trialAvailable: boolean;
  trialEndsAt: string | null;
  currentPeriodEnd: string | null;
  cancelledAt: string | null;
  loading: boolean;
  refetch: () => Promise<void>;
}

const SubscriptionContext = createContext<SubscriptionContextValue | null>(null);

const SUB_CACHE_KEY = 'macro_cached_subscription';

function getCachedStatus(): SubscriptionStatus | null {
  try {
    const raw = localStorage.getItem(SUB_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function SubscriptionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const cached = getCachedStatus();

  const [status, setStatus] = useState<SubscriptionStatus | null>(cached);
  const [loading, setLoading] = useState(() => !cached);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await subscriptionApi.status();
      setStatus(s);
      try { localStorage.setItem(SUB_CACHE_KEY, JSON.stringify(s)); } catch { /* quota */ }
    } catch {
      // Network/server errors - keep cached status if available
      if (!getCachedStatus()) {
        setStatus(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch once user is confirmed (avoids premature 401 before auth resolves)
  // Cached status from localStorage provides instant display while we wait
  useEffect(() => {
    if (user) {
      // Defer network fetch until Dashboard's critical requests finish
      // (cached status from localStorage provides instant display)
      waitForCritical().then(() => fetchStatus());
    } else if (user === null) {
      // Logged out - clear subscription state
      setStatus(null);
      localStorage.removeItem(SUB_CACHE_KEY);
      setLoading(false);
    }
  }, [user, fetchStatus]);

  // Refetch when any quota-consuming action fires the 'quota-used' event
  useEffect(() => {
    const handler = () => fetchStatus();
    window.addEventListener('quota-used', handler);
    return () => window.removeEventListener('quota-used', handler);
  }, [fetchStatus]);

  // `is_og` is the new field; `founding_member` is kept as a legacy alias so
  // that a stale bundle decoding a fresh response still works. Use `||`
  // instead of `??` so either truthy value wins (a record with
  // `is_og: false` but `founding_member: true` still resolves to OG).
  const isOG = Boolean(status?.is_og || status?.founding_member);

  const value: SubscriptionContextValue = {
    plan: status?.plan ?? 'free',
    status: status?.status ?? 'active',
    isPremium: status?.is_premium ?? false,
    isOG,
    foundingMember: isOG,
    appMode: status?.app_mode ?? 'hosted',
    betaMode: status?.beta_mode ?? false,
    imageUsage: {
      used: status?.usage_image_used ?? 0,
      // -1 = unlimited. Default to -1 when status is missing so a cache
      // miss never renders a "0/5" counter for a user who may actually
      // be uncapped (e.g., self-hosted).
      limit: status?.usage_image_limit ?? -1,
    },
    textMealUsage: {
      used: status?.usage_text_meal_used ?? 0,
      // -1 = unlimited. Keep the raw value - callers branch on `limit === -1`.
      limit: status?.usage_text_meal_limit ?? -1,
    },
    chatUsage: {
      used: status?.usage_chat_used ?? 0,
      // -1 = unlimited default. Previously defaulted to 1 (the legacy
      // monthly free cap), which briefly rendered "0/1" on fresh cache
      // misses for beta users who actually have a 10/day cap.
      limit: status?.usage_chat_limit ?? -1,
    },
    mealEditLimit: status?.usage_meal_edit_limit ?? -1,
    savedMealsUsage: {
      used: status?.usage_saved_meals ?? 0,
      // -1 = unlimited default (same reasoning as image/chat).
      limit: status?.usage_saved_meals_limit ?? -1,
    },
    trialAvailable: status?.trial_available ?? false,
    trialEndsAt: status?.trial_ends_at ?? null,
    currentPeriodEnd: status?.current_period_end ?? null,
    cancelledAt: status?.cancelled_at ?? null,
    loading,
    refetch: fetchStatus,
  };

  return (
    <SubscriptionContext.Provider value={value}>
      {children}
    </SubscriptionContext.Provider>
  );
}

export function useSubscription() {
  const ctx = useContext(SubscriptionContext);
  if (!ctx) throw new Error('useSubscription must be used within SubscriptionProvider');
  return ctx;
}
