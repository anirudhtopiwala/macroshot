import { Suspense, useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { track } from './api/analytics';
import { AuthProvider, useAuth } from './context/AuthContext';
import { SubscriptionProvider } from './context/SubscriptionContext';
import { ThemeProvider } from './context/ThemeContext';
import { PullToRefreshProvider } from './context/PullToRefreshContext';
import { OfflineQueueProvider } from './context/OfflineQueueContext';
import { ToastProvider } from './components/Toast';
import Header from './components/Header';
import BottomNav from './components/BottomNav';
import OfflineBanner from './components/OfflineBanner';
import ErrorBoundary from './components/ErrorBoundary';
import AnimatedPage from './components/AnimatedPage';
import useSwipeBack from './hooks/useSwipeBack';
import InstallPrompt from './components/InstallPrompt';
import UpdateToast from './components/UpdateToast';
import Button from './components/Button';

import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import GuestDashboard from './pages/GuestDashboard';
import GuestBanner from './components/GuestBanner';
import SignupPrompt from './pages/SignupPrompt';
import lazyWithRetry from './utils/lazyWithRetry';

// Lazy-load secondary pages with retry - handles stale chunk hashes after deploys.
// Each component gets a name so the per-component reload guard is independent.
const LogMeal = lazyWithRetry(() => import('./pages/LogMeal'), 'LogMeal');
const Journal = lazyWithRetry(() => import('./pages/Journal'), 'Journal');
const MealDetail = lazyWithRetry(() => import('./pages/MealDetail'), 'MealDetail');
const SavedMeals = lazyWithRetry(() => import('./pages/SavedMeals'), 'SavedMeals');
const Trends = lazyWithRetry(() => import('./pages/Trends'), 'Trends');
const Settings = lazyWithRetry(() => import('./pages/Settings'), 'Settings');
const SettingsPersonal = lazyWithRetry(() => import('./pages/SettingsPersonal'), 'SettingsPersonal');
const SettingsGoals = lazyWithRetry(() => import('./pages/SettingsGoals'), 'SettingsGoals');
const SettingsReminders = lazyWithRetry(() => import('./pages/SettingsReminders'), 'SettingsReminders');
const MemorySettings = lazyWithRetry(() => import('./pages/MemorySettings'), 'MemorySettings');
const ConnectedApps = lazyWithRetry(() => import('./pages/ConnectedApps'), 'ConnectedApps');
const Onboarding = lazyWithRetry(() => import('./pages/Onboarding'), 'Onboarding');
const TargetWizard = lazyWithRetry(() => import('./pages/TargetWizard'), 'TargetWizard');
const Chat = lazyWithRetry(() => import('./pages/Chat'), 'Chat');
const Terms = lazyWithRetry(() => import('./pages/Terms'), 'Terms');
const Privacy = lazyWithRetry(() => import('./pages/Privacy'), 'Privacy');
const Achievements = lazyWithRetry(() => import('./pages/Achievements'), 'Achievements');
const Feedback = lazyWithRetry(() => import('./pages/Feedback'), 'Feedback');
const About = lazyWithRetry(() => import('./pages/About'), 'About');
const Tips = lazyWithRetry(() => import('./pages/Tips'), 'Tips');
const WorkoutHistory = lazyWithRetry(() => import('./pages/WorkoutHistory'), 'WorkoutHistory');
const AdminMetrics = lazyWithRetry(() => import('./pages/AdminMetrics'), 'AdminMetrics');
const NotFound = lazyWithRetry(() => import('./pages/NotFound'), 'NotFound');

// Prefetch most-likely navigation targets after initial load
// Wrapped in catch to prevent reload loops if chunks fail to load
if (typeof window !== 'undefined') {
  window.addEventListener('load', () => {
    setTimeout(() => {
      import('./pages/LogMeal').catch(() => {});
      import('./pages/Journal').catch(() => {});
    }, 3000);
  }, { once: true });
}

function PageFallback() {
  const shimmer = 'rounded-xl bg-[length:200%_100%] animate-shimmer dark:bg-gradient-to-r dark:from-white/[0.04] dark:via-white/[0.08] dark:to-white/[0.04] bg-gradient-to-r from-black/[0.03] via-black/[0.06] to-black/[0.03]';
  return (
    <div className="space-y-4" style={{ minHeight: 'calc(100vh - 12rem)' }}>
      <div className={`${shimmer} h-8 w-32`} />
      <div className={`${shimmer} h-48 !rounded-2xl`} />
      <div className="grid grid-cols-3 gap-3">
        <div className={`${shimmer} h-24 !rounded-2xl`} />
        <div className={`${shimmer} h-24 !rounded-2xl`} />
        <div className={`${shimmer} h-24 !rounded-2xl`} />
      </div>
      <div className="space-y-2">
        <div className={`${shimmer} h-20 !rounded-2xl`} />
        <div className={`${shimmer} h-20 !rounded-2xl`} />
      </div>
    </div>
  );
}

function TosGate() {
  const { user, refetch } = useAuth();
  const [accepting, setAccepting] = useState(false);

  if (!user || user.tos_accepted) return null;

  const handleAccept = async () => {
    setAccepting(true);
    try {
      const { authApi } = await import('./api/auth');
      await authApi.acceptTos();
      await refetch();
    } catch {
      // retry on next render
    } finally {
      setAccepting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}>
      <div className="w-full max-w-sm rounded-2xl p-6 space-y-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}>
        <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>Terms & Privacy</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          We've added Terms of Service and a Privacy Policy for MacroShot. Please review and accept to continue using the app.
        </p>
        <div className="flex gap-4">
          <a
            href="/macro_app/terms"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm underline"
            style={{ color: '#10b981' }}
          >
            Terms of Service
          </a>
          <a
            href="/macro_app/privacy"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm underline"
            style={{ color: '#10b981' }}
          >
            Privacy Policy
          </a>
        </div>
        <Button
          onClick={handleAccept}
          disabled={accepting}
          variant="primary"
          size="lg"
          className="w-full"
        >
          {accepting ? 'Accepting...' : 'I Agree'}
        </Button>
      </div>
    </div>
  );
}

function AuthenticatedLayout() {
  useSwipeBack();
  const { user, isGuest } = useAuth();
  const { pathname } = useLocation();

  // Fire ui_page_view once per unique pathname. Centralised here (in a
  // NON-lazy layout component) so we don't add hooks to lazy pages -
  // per CLAUDE.md, adding hooks to lazy pages crashes users with stale
  // cached chunks. See "Code-Split Pages / Lazy Loading" note.
  useEffect(() => {
    // Normalise dynamic IDs out of the path so /meals/123 collapses
    // to /meals/:id - prevents telemetry cardinality explosion.
    const normalized = pathname
      .replace(/\/meals\/\d+/, '/meals/:id')
      .replace(/\/chat\/[a-f0-9-]{8,}/, '/chat/:id');
    track('ui_page_view', { path: normalized });
  }, [pathname]);

  const uid = user?.user_id || '';
  const onboardKey = uid ? `onboarded_${uid}` : 'onboarded';
  const onboarded = localStorage.getItem(onboardKey) || user?.has_targets;
  // Sync localStorage from backend so future checks are instant
  if (!localStorage.getItem(onboardKey) && user?.has_targets) {
    localStorage.setItem(onboardKey, 'true');
  }
  // Redirect logic - computed as elements to avoid early returns that break
  // child hook counts (caused "Rendered more hooks" on Mobile Safari).
  // Guests skip the onboarding gate: they have no profile and the wizard
  // would just push them to /login. They get the Dashboard immediately
  // and can sign up from the banner / signup prompt routes.
  const redirect = isGuest
    ? null
    : (!onboarded && pathname !== '/onboarding')
      ? <Navigate to="/onboarding" replace />
      : (onboarded && pathname === '/onboarding')
        ? <Navigate to="/" replace />
        : null;

  return (
    <PullToRefreshProvider>
      <OfflineQueueProvider>
        {redirect || (
          <>
            {!isGuest && <TosGate />}
            <OfflineBanner />
            <Header />
            <div className="max-w-lg mx-auto px-4 pb-32" style={{ paddingTop: 'calc(4rem + env(safe-area-inset-top, 0px))', minHeight: '100vh' }}>
              {isGuest && <GuestBanner />}
              <ErrorBoundary>
                <AnimatedPage>
                  <Suspense fallback={<PageFallback />}>
                    {/* Guest mode: only Dashboard + LogMeal + Terms/Privacy
                        are reachable. Everything else routes to the signup
                        prompt — no API calls land that would 401. */}
                    {isGuest ? (
                      <Routes>
                        <Route path="/" element={<GuestDashboard />} />
                        <Route path="/log" element={<LogMeal />} />
                        <Route path="/terms" element={<Terms />} />
                        <Route path="/privacy" element={<Privacy />} />
                        <Route path="*" element={<SignupPrompt />} />
                      </Routes>
                    ) : (
                      <Routes>
                        <Route path="/" element={<Dashboard />} />
                        <Route path="/log" element={<LogMeal />} />
                        <Route path="/journal" element={<Journal />} />
                        <Route path="/meals/:id" element={<MealDetail />} />
                        <Route path="/saved" element={<SavedMeals />} />
                        <Route path="/trends" element={<Trends />} />
                        <Route path="/settings" element={<Settings />} />
                        <Route path="/settings/personal" element={<SettingsPersonal />} />
                        <Route path="/settings/goals" element={<SettingsGoals />} />
                        <Route path="/settings/reminders" element={<SettingsReminders />} />
                        <Route path="/settings/connected-apps" element={<ConnectedApps />} />
                        <Route path="/workouts" element={<WorkoutHistory />} />
                        <Route path="/settings/achievements" element={<Achievements />} />
                        <Route path="/settings/feedback" element={<Feedback />} />
                        <Route path="/settings/tips" element={<Tips />} />
                        <Route path="/settings/targets" element={<TargetWizard />} />
                        <Route path="/settings/memory" element={<MemorySettings />} />
                        <Route path="/chat" element={<Chat />} />
                        <Route path="/chat/:sessionId" element={<Chat />} />
                        <Route path="/onboarding" element={<Onboarding />} />
                        <Route path="/admin/metrics" element={<AdminMetrics />} />
                        <Route path="/terms" element={<Terms />} />
                        <Route path="/privacy" element={<Privacy />} />
                        <Route path="*" element={<NotFound />} />
                      </Routes>
                    )}
                  </Suspense>
                </AnimatedPage>
              </ErrorBoundary>
            </div>
            {(isGuest || onboarded) && <BottomNav />}
            <UpdateToast />
          </>
        )}
      </OfflineQueueProvider>
    </PullToRefreshProvider>
  );
}

function AppRoutes() {
  const { user, isGuest, loading } = useAuth();

  // Signal splash screen that app is ready once auth check completes
  useEffect(() => {
    if (!loading) {
      window.dispatchEvent(new Event('app-ready'));
    }
  }, [loading]);

  if (loading) {
    return null; // Splash screen is still visible, no need for a loading spinner
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      {/* /signup is a convenience alias guest pages link to. Same target as
          /login — Login.tsx hosts both sign-in and sign-up. */}
      <Route path="/signup" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/terms" element={
        <Suspense fallback={<PageFallback />}>
          <div className="max-w-lg mx-auto px-4 py-8">
            <Terms />
          </div>
        </Suspense>
      } />
      <Route path="/privacy" element={
        <Suspense fallback={<PageFallback />}>
          <div className="max-w-lg mx-auto px-4 py-8">
            <Privacy />
          </div>
        </Suspense>
      } />
      <Route path="/about" element={
        <Suspense fallback={<PageFallback />}>
          <About />
        </Suspense>
      } />
      <Route path="/*" element={(user || isGuest) ? <AuthenticatedLayout /> : <Navigate to="/login" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/macro_app">
      <ThemeProvider>
        <AuthProvider>
          <SubscriptionProvider>
            <ToastProvider>
              <AppRoutes />
              <InstallPrompt />
            </ToastProvider>
          </SubscriptionProvider>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
