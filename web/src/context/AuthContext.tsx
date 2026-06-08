import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import * as Sentry from '@sentry/react';
import { authApi } from '../api/auth';
import { guestApi } from '../api/guest';
import { ApiError } from '../api/client';
import { clearCache } from '../utils/apiCache';
import { clearOfflineQueue } from '../utils/offlineQueue';
import { clearGuestMeals, listGuestMeals } from '../utils/guestStorage';
import type { UserMe } from '../types';

/** Shape of the structured 503 body returned when the beta signup cap is hit. */
interface BetaFullDetail {
  error: 'beta_full';
  /** Email the user just tried to sign up with. Added server-side so the
   *  client doesn't need to JWT-decode the Google credential to display it. */
  email?: string;
  /** True when the backend successfully inserted the email into the waitlist. */
  waitlist_added?: boolean;
  message?: string;
  cap?: number;
}

/**
 * If the given error is a 503 `beta_full` response, return the parsed detail.
 * Otherwise return null. Exported so Login.tsx can share this with the
 * AuthContext without duplicating the type guard.
 */
export function getBetaFullDetail(err: unknown): BetaFullDetail | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status !== 503) return null;
  const d = err.detail as BetaFullDetail | null | undefined;
  if (!d || d.error !== 'beta_full') return null;
  return d;
}

/** Shape of the 403 body returned when a non-allowlisted email tries to log into staging. */
interface StagingNotAllowedDetail {
  error: 'staging_not_allowed';
  redirect: string;
  message?: string;
}

/**
 * If the error is a 403 `staging_not_allowed` response, return the parsed detail.
 * Login.tsx uses this to redirect the user to production instead of showing
 * a confusing error.
 */
export function getStagingRedirectDetail(err: unknown): StagingNotAllowedDetail | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status !== 403) return null;
  const d = err.detail as StagingNotAllowedDetail | null | undefined;
  if (!d || d.error !== 'staging_not_allowed' || !d.redirect) return null;
  return d;
}

interface AuthContextValue {
  user: UserMe | null;
  loading: boolean;
  /**
   * True while the visitor is browsing without signing up. Mutually
   * exclusive with `user !== null`: signing in via Google or PIN
   * automatically clears guest mode (and migrates any guest meals).
   */
  isGuest: boolean;
  enterGuestMode: () => void;
  /** Sign out of guest mode and wipe local guest meals. */
  exitGuestMode: () => void;
  logout: () => Promise<void>;
  refetch: () => Promise<void>;
  /**
   * Email the user most recently tried to sign up with when they hit the
   * beta cap. When non-null, Login.tsx should render the waitlist
   * confirmation card instead of the normal signup form.
   */
  waitlistedEmail: string | null;
  /** Optional backend-authored message to prefer over our hardcoded fallback. */
  waitlistedMessage: string | null;
  /** Numeric signup cap (e.g. 200) surfaced from err.detail.cap. */
  waitlistedCap: number | null;
  /**
   * Mark the user as waitlisted. Called by the login flow when it catches a
   * 503 `beta_full`. Clears any stale cached auth/subscription state so the
   * app doesn't show pre-failure plan data.
   */
  markWaitlisted: (email: string, detail: BetaFullDetail | null) => void;
  /** Exit the waitlist confirmation state (e.g., "Back to sign in"). */
  clearWaitlisted: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const USER_CACHE_KEY = 'macro_cached_user';
const SUB_CACHE_KEY = 'macro_cached_subscription';
const GUEST_FLAG_KEY = 'macro_guest_mode';

function getCachedUser(): UserMe | null {
  try {
    const raw = localStorage.getItem(USER_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readGuestFlag(): boolean {
  try {
    return localStorage.getItem(GUEST_FLAG_KEY) === '1';
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Show cached user immediately so the app doesn't block on /auth/me
  const [user, setUser] = useState<UserMe | null>(() => getCachedUser());
  const [isGuest, setIsGuest] = useState<boolean>(() => !getCachedUser() && readGuestFlag());
  // Guest mode short-circuits the /auth/me probe so we don't show the
  // splash spinner forever and never trip the 401 redirect interceptor.
  const [loading, setLoading] = useState(() => !getCachedUser() && !readGuestFlag());
  const [waitlistedEmail, setWaitlistedEmail] = useState<string | null>(null);
  const [waitlistedMessage, setWaitlistedMessage] = useState<string | null>(null);
  const [waitlistedCap, setWaitlistedCap] = useState<number | null>(null);

  // Ref so in-flight checkAuth calls can observe the latest waitlisted
  // state synchronously. React state updates are batched and won't be
  // visible to an already-resolved `await authApi.me()` that landed
  // between `markWaitlisted()` firing and the promise returning. The
  // ref flip is visible the instant we set it.
  const waitlistedRef = useRef(false);

  const wipeLocalSession = useCallback(() => {
    localStorage.removeItem(USER_CACHE_KEY);
    localStorage.removeItem(SUB_CACHE_KEY);
    clearCache();
    clearOfflineQueue();
    if (navigator.serviceWorker?.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
    }
  }, []);

  const markWaitlisted = useCallback(
    (email: string, detail: BetaFullDetail | null) => {
      // Defensive cleanup: the backend should not have set a session cookie
      // since the 503 prevented user creation, but any prior cached state
      // from a previous session would mislead the app into thinking the
      // user is logged in. Wipe everything logout wipes - localStorage,
      // in-memory API cache, IndexedDB offline queue, and SW runtime
      // cache - so a stale /auth/me resolving after markWaitlisted can't
      // repopulate auth state.
      wipeLocalSession();
      waitlistedRef.current = true;
      setUser(null);
      // Prefer the backend-provided email (added in the server's 503 body
      // so we don't have to JWT-decode the Google credential client-side).
      setWaitlistedEmail(detail?.email ?? email);
      setWaitlistedMessage(detail?.message ?? null);
      setWaitlistedCap(detail?.cap ?? null);
      // Best-effort: drop the session cookie too. The 503 from the signup
      // endpoints shouldn't have set one, but if a stale cookie from a
      // prior session survives on this browser, logout nukes it.
      authApi.logout().catch(() => { /* non-critical */ });
    },
    [wipeLocalSession, waitlistedRef],
  );

  const clearWaitlisted = useCallback(() => {
    waitlistedRef.current = false;
    setWaitlistedEmail(null);
    setWaitlistedMessage(null);
    setWaitlistedCap(null);
  }, [waitlistedRef]);

  const enterGuestMode = useCallback(() => {
    try { localStorage.setItem(GUEST_FLAG_KEY, '1'); } catch { /* quota */ }
    setIsGuest(true);
    setUser(null);
    setLoading(false);
  }, []);

  const exitGuestMode = useCallback(() => {
    try { localStorage.removeItem(GUEST_FLAG_KEY); } catch { /* quota */ }
    clearGuestMeals();
    setIsGuest(false);
  }, []);

  /**
   * After a successful real login, migrate any cached guest meals into
   * the new user's history via /meals/import-guest. Idempotent on the
   * server (users.guest_meals_imported_at) — safe if the migration
   * already ran on another device. Best-effort: a network failure here
   * loses the migration but logs to Sentry so we can investigate.
   */
  const migrateGuestMealsIfAny = useCallback(async (): Promise<void> => {
    if (!readGuestFlag()) return;
    let meals;
    try {
      meals = await listGuestMeals();
    } catch {
      meals = [];
    }
    if (meals.length === 0) {
      // Nothing to import, just clear the flag.
      try { localStorage.removeItem(GUEST_FLAG_KEY); } catch { /* quota */ }
      clearGuestMeals();
      setIsGuest(false);
      return;
    }
    try {
      // Server caps the payload at 30 entries via Pydantic max_length;
      // trim client-side so a generous local store can still migrate.
      const payload = meals.slice(0, 30).map((m) => ({
        nutrition: m.nutrition,
        logged_at: m.loggedAt,
        meal_type: m.mealType,
        user_input: m.userInput,
      }));
      await guestApi.importGuestMeals(payload);
    } catch (err) {
      if (Sentry.isInitialized()) {
        Sentry.captureException(err, {
          tags: { context: 'guest-meal-migration' },
          extra: { count: meals.length },
        });
      }
    } finally {
      // Always wipe local state — partial-import-then-retry would
      // duplicate rows in the new account, and the server's
      // already_imported flag protects against a fresh retry anyway.
      try { localStorage.removeItem(GUEST_FLAG_KEY); } catch { /* quota */ }
      clearGuestMeals();
      setIsGuest(false);
    }
  }, []);

  // wipeLocalSession is used inside checkAuth; it's defined above so the ref
  // is stable. Listed as a dep to satisfy the hook-lint invariant.
  const checkAuth = useCallback(async () => {
    // Guest mode: never probe /auth/me. The visitor has no cookie and
    // the 401-interceptor would redirect them off the app shell.
    if (readGuestFlag() && !getCachedUser()) {
      setIsGuest(true);
      setLoading(false);
      return;
    }
    try {
      const me = await authApi.me();
      // If markWaitlisted fired while /auth/me was in flight, ignore the
      // response - we deliberately wiped the session and must not
      // resurrect it by writing `me` back into state.
      if (waitlistedRef.current) {
        setLoading(false);
        return;
      }
      // Shared-device / account-switch safety: if the previously-cached user
      // had a different user_id, wipe all per-user caches before we start
      // serving the new user. Without this, the first paint of Dashboard /
      // Settings / Trends would briefly show the previous user's data from
      // localStorage before a fresh fetch overwrites it.
      try {
        const prev = localStorage.getItem(USER_CACHE_KEY);
        if (prev) {
          const prevUser = JSON.parse(prev) as { user_id?: number };
          if (prevUser?.user_id && prevUser.user_id !== me.user_id) {
            wipeLocalSession();
          }
        }
      } catch { /* malformed cache - treat as empty */ }
      setUser(me);
      try { localStorage.setItem(USER_CACHE_KEY, JSON.stringify(me)); } catch { /* quota */ }
      // Real login won — replay any pre-signup guest meals into the
      // new user's history, then exit guest mode locally.
      migrateGuestMealsIfAny().catch(() => { /* logged in Sentry */ });
      // Auto-detect timezone on login - send to backend if not already set
      try {
        const detectedTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const tzSyncKey = `tz_synced_${me.user_id}`;
        if (detectedTz && !localStorage.getItem(tzSyncKey)) {
          const { api } = await import('../api/client');
          await api.put('/settings/prefs', { timezone: detectedTz });
          localStorage.setItem(tzSyncKey, '1');
        }
      } catch { /* non-critical */ }
      // Transfer any orphaned push subscription to this user. Previous
      // account on this device may have left a PushManager subscription
      // behind - the server now has no row for it (logout wipes the old
      // user's rows), so subscribeToPush() re-registers the same endpoint
      // under the current user. save_push_subscription() deletes any prior
      // row with that endpoint, so this never produces duplicates.
      try {
        const { getPushStatus, subscribeToPush } = await import('../api/push');
        const status = await getPushStatus();
        if (status.supported && status.subscribed && !status.serverSubscribed && status.permission === 'granted') {
          await subscribeToPush();
        }
      } catch { /* non-critical */ }
    } catch {
      // Network errors, 503 (SW offline response), server errors - keep cached user.
      // Genuine 401s are handled by the API client interceptor in client.ts
      // (clears localStorage + redirects to /login) before this catch runs.
      if (!getCachedUser()) {
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, [waitlistedRef, wipeLocalSession, migrateGuestMealsIfAny]);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const logout = useCallback(async () => {
    // Unsubscribe push BEFORE clearing the session - /settings/push/unsubscribe
    // needs the cookie to identify the user. unsubscribeFromPush also detaches
    // the browser-side PushManager sub, so the next logged-in user on this
    // device starts fresh. The server additionally wipes any lingering rows
    // for this user in /auth/logout as a safety net.
    try {
      const { unsubscribeFromPush } = await import('../api/push');
      await unsubscribeFromPush();
    } catch {
      // Non-critical - server-side logout will still wipe rows for this user.
    }
    try {
      await authApi.logout();
    } catch {
      // Server unreachable - still clear local session
    }
    localStorage.removeItem(USER_CACHE_KEY);
    clearCache(); // Wipe all cached API data (dashboard, settings, etc.)
    clearOfflineQueue(); // Wipe IndexedDB offline meal queue
    // Tell the service worker to clear its runtime cache
    if (navigator.serviceWorker?.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
    }
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isGuest,
        enterGuestMode,
        exitGuestMode,
        logout,
        refetch: checkAuth,
        waitlistedEmail,
        waitlistedMessage,
        waitlistedCap,
        markWaitlisted,
        clearWaitlisted,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
