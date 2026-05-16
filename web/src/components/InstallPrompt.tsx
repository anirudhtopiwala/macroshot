import { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { X } from './icons';
import Button from './Button';
import IOSInstallAnimation from './IOSInstallAnimation';
import InAppBrowserAnimation from './InAppBrowserAnimation';
import { useAuth } from '../context/AuthContext';

/**
 * Step-based schedule for the install banner. Each dismissal advances
 * the step; the banner re-appears only when the step's condition is met.
 *
 *   0  after login (first time the app is opened while not standalone)
 *   1  after macro targets are saved (fired via `pwa-install-trigger` event)
 *   2  at least 2 days after last dismissal - shown as a soft hint
 *   3  at least 7 days after last dismissal - soft hint
 *   4  stop asking
 */
const STEP_KEY = 'pwa_install_step';
const DISMISSED_AT_KEY = 'pwa_install_dismissed_at';
const TARGETS_TRIGGER_KEY = 'pwa_install_targets_trigger';
const DAY_MS = 24 * 60 * 60 * 1000;
const STEP_2_DELAY_MS = 2 * DAY_MS;
const STEP_3_DELAY_MS = 7 * DAY_MS;

function readStep(): number {
  try { return Math.max(0, Math.min(4, Number(localStorage.getItem(STEP_KEY) || 0))); } catch { return 0; }
}
function readDismissedAt(): number {
  try { return Number(localStorage.getItem(DISMISSED_AT_KEY) || 0); } catch { return 0; }
}
function readTargetsTrigger(): boolean {
  try { return localStorage.getItem(TARGETS_TRIGGER_KEY) === '1'; } catch { return false; }
}

function hasLoggedMeal(): boolean {
  try { return localStorage.getItem('has_logged_meal') === '1'; } catch { return false; }
}

/**
 * Decide whether the banner should be visible right now given the step and
 * last-dismissal time. Returns the copy variant to render.
 *   'full' - full card with animation (steps 0 and 1)
 *   'hint' - soft tip (steps 2 and 3)
 *   null  - don't show
 */
function computeShowMode(step: number, dismissedAt: number, targetsTrigger: boolean): 'full' | 'hint' | null {
  const elapsed = Date.now() - (dismissedAt || 0);
  if (step === 0) return 'full';
  if (step === 1) return (targetsTrigger || hasLoggedMeal()) ? 'full' : null;
  if (step === 2) return elapsed >= STEP_2_DELAY_MS ? 'hint' : null;
  if (step === 3) return elapsed >= STEP_3_DELAY_MS ? 'hint' : null;
  return null;
}

/** Public: call after macro targets are saved to re-trigger the banner. */
export function markTargetsSetForInstallPrompt() {
  try { localStorage.setItem(TARGETS_TRIGGER_KEY, '1'); } catch { /* */ }
  window.dispatchEvent(new CustomEvent('pwa-install-recheck'));
}

/**
 * Public: call when the user has seen the dedicated install screen during
 * onboarding. Advances the schedule past steps 0 and 1 straight to the
 * +2 day reminder window so we don't re-nag immediately after onboarding.
 */
export function markInstallPromptSeen() {
  try {
    localStorage.setItem(STEP_KEY, '2');
    localStorage.setItem(DISMISSED_AT_KEY, String(Date.now()));
    localStorage.removeItem(TARGETS_TRIGGER_KEY);
  } catch { /* */ }
  window.dispatchEvent(new CustomEvent('pwa-install-recheck'));
}

/**
 * Public: fire the stored Android/Chrome install prompt without needing
 * the React hook. Returns 'accepted' | 'dismissed' | 'unavailable'.
 */
export async function triggerNativeInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const prompt = (window as any).__pwaInstallPrompt;
  if (!prompt) return 'unavailable';
  try {
    prompt.prompt();
    const result = await prompt.userChoice;
    (window as any).__pwaInstallPrompt = null;
    return result?.outcome === 'accepted' ? 'accepted' : 'dismissed';
  } catch {
    return 'unavailable';
  }
}

/** True when the app is running as an installed PWA (standalone). */
export function isStandalone(): boolean {
  if ((navigator as any).standalone) return true; // iOS
  if (window.matchMedia('(display-mode: standalone)').matches) return true;
  if (window.matchMedia('(display-mode: fullscreen)').matches) return true;
  return false;
}

export function isIOS(): boolean {
  if (/iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as any).MSStream) return true;
  // iPadOS 13+ reports a Mac UA but has touch support
  if (navigator.platform === 'MacIntel' && (navigator.maxTouchPoints || 0) > 1) return true;
  return false;
}

export function isIpad(): boolean {
  if (/iPad/.test(navigator.userAgent)) return true;
  if (navigator.platform === 'MacIntel' && (navigator.maxTouchPoints || 0) > 1) return true;
  return false;
}

/**
 * True when the page is loaded inside an in-app webview (Instagram, FB,
 * TikTok, Gmail, LinkedIn, etc). These can't install PWAs - user has to
 * re-open in Safari/Chrome first.
 */
export function isInAppBrowser(): boolean {
  const ua = navigator.userAgent;
  return /FBAN|FBAV|FB_IAB|Instagram|Twitter|Line\/|KAKAOTALK|LinkedInApp|MicroMessenger|TikTok|musical_ly|Pinterest|Snapchat|GSA\/|GMAIL|Outlook-iOS/i.test(ua);
}

/** True when the browser has a deferred `beforeinstallprompt` we can fire. */
export function canNativeInstall(): boolean {
  return !!(window as any).__pwaInstallPrompt;
}

/**
 * Bottom card prompting users to install the PWA.
 * - Android/Chrome: triggers native install prompt
 * - iOS Safari: shows animated 3-step guide
 * - In-app browsers (IG/FB/TikTok/Gmail): shows "Open in Safari" animation
 * - Step-based schedule (see STEP_KEY): login → targets-set → +2d → +7d
 * - Hidden if already installed (standalone)
 */
export default function InstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<any>(null);
  const [mode, setMode] = useState<'full' | 'hint' | null>(null);
  const [showInApp, setShowInApp] = useState(false);
  const [iosReady, setIosReady] = useState(false);
  const location = useLocation();
  const { user } = useAuth();

  const recompute = () => {
    if (isStandalone()) { setMode(null); return; }
    const step = readStep();
    const show = computeShowMode(step, readDismissedAt(), readTargetsTrigger());
    setMode(show);
  };

  // Re-check on every route change - catches the "user just logged their
  // first meal" case without having to wire an explicit event from every
  // meal-logging call site.
  useEffect(() => {
    if (isStandalone() || isInAppBrowser()) return;
    recompute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  useEffect(() => {
    if (isStandalone()) return;

    // In-app browsers always see the "Open in Safari" guidance (they can't install here).
    if (isInAppBrowser()) {
      setShowInApp(true);
      setMode('full');
      return;
    }

    if (isIOS()) setIosReady(true);

    // Android/Chrome: listen for the native install prompt
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };
    window.addEventListener('beforeinstallprompt', handler);
    if ((window as any).__pwaInstallPrompt) {
      setDeferredPrompt((window as any).__pwaInstallPrompt);
    }

    recompute();

    // Re-check when targets get saved (same tab) or when another tab changes state.
    const onRecheck = () => recompute();
    const onStorage = (e: StorageEvent) => {
      if (e.key === STEP_KEY || e.key === DISMISSED_AT_KEY || e.key === TARGETS_TRIGGER_KEY) recompute();
    };
    window.addEventListener('pwa-install-recheck', onRecheck);
    window.addEventListener('storage', onStorage);

    return () => {
      window.removeEventListener('beforeinstallprompt', handler);
      window.removeEventListener('pwa-install-recheck', onRecheck);
      window.removeEventListener('storage', onStorage);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dismiss = () => {
    try {
      const currentStep = readStep();
      const nextStep = Math.min(4, currentStep + 1);
      localStorage.setItem(STEP_KEY, String(nextStep));
      localStorage.setItem(DISMISSED_AT_KEY, String(Date.now()));
      // Clear the one-shot targets trigger so step 1 doesn't re-show until next save.
      if (currentStep === 1) localStorage.removeItem(TARGETS_TRIGGER_KEY);
    } catch { /* */ }
    setMode(null);
    setDeferredPrompt(null);
    setShowInApp(false);
  };

  const handleInstall = async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const result = await deferredPrompt.userChoice;
    if (result.outcome === 'accepted') {
      setDeferredPrompt(null);
    }
    dismiss();
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + '/macro_app/');
    } catch { /* noop */ }
  };

  // Nothing to show
  if (isStandalone()) return null;
  if (!user) return null; // only prompt after the user has signed in
  if (mode === null) return null;
  const canShowSomething = deferredPrompt || iosReady || showInApp;
  if (!canShowSomething) return null;

  const isHint = mode === 'hint';
  const step = readStep();

  return (
    <div className="fixed bottom-20 left-0 right-0 z-[70] flex justify-center pointer-events-none">
      <div
        className="pointer-events-auto mx-4 p-4 rounded-2xl backdrop-blur-xl border max-w-md w-full"
        style={{
          background: 'var(--bg-elevated)',
          borderColor: 'var(--border-glass)',
          boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
        }}
      >
        {/* Header: icon + title + dismiss */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">{isHint ? '💡' : '📲'}</span>
            <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
              {showInApp ? 'Open in your browser' : isHint ? 'Quick tip' : 'Install MacroShot'}
            </p>
          </div>
          <button onClick={dismiss} className="p-1 rounded-full active:scale-90 transition-transform" aria-label="Dismiss">
            <X className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
          </button>
        </div>

        {showInApp ? (
          /* In-app webview - installation impossible here */
          <div className="space-y-3">
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              You're in an in-app browser. Open this page in Safari or Chrome to install MacroShot on your home screen.
            </p>
            <InAppBrowserAnimation />
            <Button variant="secondary" size="sm" className="w-full" onClick={copyLink}>
              Copy link
            </Button>
          </div>
        ) : isHint ? (
          /* Step 2/3 - soft hint, no animation (less intrusive) */
          <div className="space-y-3">
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              MacroShot is much easier to use when installed to your home screen - full-screen, offline-ready, and push reminders work.
            </p>
            <div className="flex gap-2">
              {deferredPrompt ? (
                <Button variant="primary" size="sm" className="flex-1" onClick={handleInstall}>
                  Install now
                </Button>
              ) : (
                <Button variant="primary" size="sm" className="flex-1" onClick={() => setMode('full')}>
                  Show me how
                </Button>
              )}
              <Button variant="secondary" size="sm" className="flex-1" onClick={dismiss}>
                Maybe later
              </Button>
            </div>
            <p className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>
              {step === 2 ? 'Reminder 1 of 2' : 'Last reminder'}
            </p>
          </div>
        ) : deferredPrompt ? (
          /* Chrome/Android - native install */
          <>
            <p className="text-xs mb-3" style={{ color: 'var(--text-secondary)' }}>
              Add to your home screen for quick access to meal tracking - offline-ready with push reminders.
            </p>
            <Button variant="primary" size="sm" className="w-full" onClick={handleInstall}>
              Add to Home Screen
            </Button>
          </>
        ) : (
          /* iOS Safari - animated step-by-step guide */
          <div className="space-y-3">
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              Watch the steps below, then follow along in your browser.
            </p>
            <IOSInstallAnimation variant={isIpad() ? 'ipad' : 'ios'} />
          </div>
        )}
      </div>
    </div>
  );
}

/** Hook for Settings page: returns install state without showing banner */
export function useInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<any>(null);
  const [canInstall, setCanInstall] = useState(false);
  const standalone = isStandalone();
  const inApp = isInAppBrowser();
  const ios = isIOS();
  const ipad = isIpad();

  useEffect(() => {
    if (standalone) return;

    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setCanInstall(true);
    };
    window.addEventListener('beforeinstallprompt', handler);

    if ((window as any).__pwaInstallPrompt) {
      setDeferredPrompt((window as any).__pwaInstallPrompt);
      setCanInstall(true);
    }

    // iOS / in-app: can't trigger install, but show the row
    if (ios || inApp) setCanInstall(true);

    return () => window.removeEventListener('beforeinstallprompt', handler);
  }, [standalone, ios, inApp]);

  const install = async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    setDeferredPrompt(null);
    setCanInstall(false);
  };

  return {
    canInstall: canInstall && !standalone,
    isIOS: ios,
    isIpad: ipad,
    isInApp: inApp,
    standalone,
    install,
  };
}
