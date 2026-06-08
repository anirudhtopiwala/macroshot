import { useState, useEffect, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { authApi } from '../api/auth';
import { useAuth, getBetaFullDetail, getStagingRedirectDetail } from '../context/AuthContext';
import Button from '../components/Button';

/** Best-effort email extraction from a Google ID token (JWT). Used when the
 *  beta cap is hit during OAuth - we need the email for the waitlist card,
 *  but the only thing Google's callback gives us is the credential string.
 *  Returns null on any parsing error; caller falls back to generic copy. */
function emailFromGoogleCredential(credential: string): string | null {
  try {
    const payload = credential.split('.')[1];
    if (!payload) return null;
    // base64url → base64
    const b64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = JSON.parse(atob(b64)) as { email?: string };
    return typeof json.email === 'string' ? json.email : null;
  } catch {
    return null;
  }
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
            auto_select?: boolean;
          }) => void;
          prompt: () => void;
          renderButton: (
            parent: HTMLElement,
            options: {
              type?: 'standard' | 'icon';
              theme?: 'outline' | 'filled_blue' | 'filled_black';
              size?: 'large' | 'medium' | 'small';
              text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin';
              shape?: 'rectangular' | 'pill' | 'circle' | 'square';
              logo_alignment?: 'left' | 'center';
              width?: number | string;
            },
          ) => void;
        };
      };
    };
  }
}


/**
 * Confirmation card shown after a 503 `beta_full` signup. The backend
 * auto-adds the email to the waitlist and sends a confirmation email, so
 * all we do here is tell the user they've been added. Accessible by
 * default - role=status + aria-live so screen readers announce the state
 * change, and focus is moved to the "Back to sign in" button on mount so
 * keyboard users don't have to Tab from the top of the page.
 */
function WaitlistConfirmation({
  email,
  message,
  onBack,
}: {
  email: string;
  message: string | null;
  onBack: () => void;
}) {
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    backRef.current?.focus();
  }, []);
  const backendMessage = message && message.trim() ? message : null;
  return (
    <div className="space-y-6">
      <div
        role="status"
        aria-live="polite"
        className="rounded-2xl p-5 space-y-3"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-glass)',
          backdropFilter: 'blur(16px)',
        }}
      >
        <h2
          className="text-base font-semibold text-center"
          tabIndex={-1}
          style={{ color: 'var(--text-primary)' }}
        >
          You're on the waitlist
        </h2>
        <p
          className="text-sm leading-relaxed text-center"
          style={{ color: 'var(--text-secondary)' }}
        >
          {backendMessage ?? (
            <>
              MacroShot is in beta. You've been added to the waitlist - we'll
              email{' '}
              <span style={{ color: 'var(--text-primary)' }}>{email}</span>{' '}
              once we're ready to have you join.
            </>
          )}
        </p>
      </div>
      <Button
        ref={backRef}
        type="button"
        variant="secondary"
        size="md"
        onClick={onBack}
        className="w-full"
      >
        ← Back to sign in
      </Button>
    </div>
  );
}

export default function Login() {
  const navigate = useNavigate();
  const {
    refetch,
    enterGuestMode,
    waitlistedEmail,
    waitlistedMessage,
    markWaitlisted,
    clearWaitlisted,
  } = useAuth();
  const [email, setEmail] = useState('');
  const [pin, setPin] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [step, setStep] = useState<'email' | 'pin'>('email');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [googleReady, setGoogleReady] = useState(false);
  const [tosAgreed, setTosAgreed] = useState(false);
  const googleButtonRef = useRef<HTMLDivElement>(null);
  const googleWrapperRef = useRef<HTMLDivElement>(null);
  const [googleWidth, setGoogleWidth] = useState(0);
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;

  // If the error is a 503 `beta_full`, surface the waitlist confirmation state
  // via AuthContext. `attemptedEmail` is a best-effort fallback for when the
  // backend doesn't populate `detail.email` (older bundles, or if the email
  // wasn't known to the 503 handler). Newer backends echo the email verbatim
  // in detail.email, and AuthContext.markWaitlisted prefers that.
  const handleBetaFullError = (err: unknown, attemptedEmail: string): boolean => {
    const detail = getBetaFullDetail(err);
    if (!detail) return false;
    markWaitlisted(detail.email ?? attemptedEmail, detail);
    setError('');
    return true;
  };

  // If the backend rejects the login with a 403 staging_not_allowed, navigate
  // the user to production instead of showing a raw error. Returns true when
  // the redirect was fired so callers can short-circuit their catch block.
  const handleStagingRedirect = (err: unknown): boolean => {
    const detail = getStagingRedirectDetail(err);
    if (!detail) return false;
    setError('');
    setLoading(true);
    window.location.href = detail.redirect;
    return true;
  };

  useEffect(() => {
    let cancelled = false;

    fetch('/macro_app/api/config')
      .then(res => res.json())
      .then(config => {
        if (cancelled || !config.google_client_id) return;
        if (document.querySelector('script[src*="accounts.google.com/gsi/client"]')) {
          window.google?.accounts.id.initialize({
            client_id: config.google_client_id,
            callback: async (response: { credential: string }) => {
              try {
                await authApi.googleAuth(response.credential);
                await authApi.acceptTos();
                await refetchRef.current();
                navigate('/', { replace: true });
              } catch (err: unknown) {
                const googleEmail = emailFromGoogleCredential(response.credential);
                if (handleStagingRedirect(err)) return;
                if (handleBetaFullError(err, googleEmail ?? 'your email')) return;
                setError(err instanceof Error ? err.message : 'Google sign-in failed');
              }
            },
          });
          setGoogleReady(true);
          return;
        }

        const script = document.createElement('script');
        script.src = 'https://accounts.google.com/gsi/client';
        script.async = true;
        script.onload = () => {
          if (cancelled) return;
          window.google?.accounts.id.initialize({
            client_id: config.google_client_id,
            callback: async (response: { credential: string }) => {
              try {
                await authApi.googleAuth(response.credential);
                await authApi.acceptTos();
                await refetchRef.current();
                navigate('/', { replace: true });
              } catch (err: unknown) {
                const googleEmail = emailFromGoogleCredential(response.credential);
                if (handleStagingRedirect(err)) return;
                if (handleBetaFullError(err, googleEmail ?? 'your email')) return;
                setError(err instanceof Error ? err.message : 'Google sign-in failed');
              }
            },
          });
          setGoogleReady(true);
        };
        document.head.appendChild(script);
      })
      .catch(() => { /* Google auth not available */ });

    return () => { cancelled = true; };
  }, [navigate]);

  // Track the wrapper width so we can re-render Google's button whenever
  // the viewport resizes (orientation change, tablet tab resize, desktop
  // window drag). Google's renderButton takes a fixed pixel width - if
  // we don't keep it in sync, the invisible click target drifts out of
  // alignment with our custom visual layer. Clamped to Google's allowed
  // 200–400px range; the wrapper itself is capped at 400px so clicks
  // always land on the iframe.
  useEffect(() => {
    const wrapper = googleWrapperRef.current;
    if (!wrapper) return;
    const measure = () => {
      const w = wrapper.getBoundingClientRect().width;
      if (w <= 0) return;
      const clamped = Math.min(400, Math.max(200, Math.round(w)));
      setGoogleWidth(clamped);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(wrapper);
    window.addEventListener('orientationchange', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('orientationchange', measure);
    };
  }, [googleReady]);

  // Render Google's official button invisibly on top of our custom visual.
  // We use renderButton instead of prompt() because One Tap has a cooldown
  // after a few dismissals / FedCM suppression, making the prompt silently
  // do nothing - users saw an apparently disabled button. The iframe
  // captures clicks; our styled overlay (pointer-events: none) shows the
  // branded UI. Re-rendered whenever width changes so the hit target
  // stays aligned.
  useEffect(() => {
    if (!googleReady || !googleWidth) return;
    const container = googleButtonRef.current;
    if (!container) return;
    container.innerHTML = '';
    window.google?.accounts.id.renderButton(container, {
      type: 'standard',
      theme: 'filled_blue',
      size: 'large',
      text: 'signin_with',
      shape: 'pill',
      logo_alignment: 'left',
      width: googleWidth,
    });
  }, [googleReady, googleWidth]);

  const sendPin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setLoading(true);
    setError('');
    try {
      await authApi.sendPin(email);
      setStep('pin');
    } catch (err: unknown) {
      if (handleStagingRedirect(err)) return;
      if (handleBetaFullError(err, email)) return;
      setError(err instanceof Error ? err.message : 'Failed to send PIN');
    } finally {
      setLoading(false);
    }
  };

  const verifyPin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pin) return;
    setLoading(true);
    setError('');
    try {
      await authApi.verifyPin(email, pin, firstName.trim() || undefined, lastName.trim() || undefined);
      await authApi.acceptTos();
      await refetch();
      navigate('/', { replace: true });
    } catch (err: unknown) {
      if (handleStagingRedirect(err)) return;
      if (handleBetaFullError(err, email)) return;
      setError(err instanceof Error ? err.message : 'Invalid PIN');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative">
      {/* Radial gradient background */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(16,185,129,0.08)_0%,transparent_70%)]" />

      <div className="w-full max-w-sm space-y-8 relative z-10">
        <div className="text-center">
          <img src="/macro_app/logo-login.png" alt="MacroShot" className="w-48 h-48 mx-auto mb-4" />
          <h1 className="text-4xl font-black italic tracking-wide">
            <span style={{ color: 'var(--text-primary)' }}>MacroShot</span>
          </h1>
          <p className="mt-2 text-sm font-medium" style={{ color: 'var(--text-muted)' }}>AI-powered meal tracking</p>
        </div>

        {/* Waitlist confirmation - shown after a failed signup that hit the
            beta_full cap, OR after a proactive "Join waitlist" submission.
            Replaces the entire signup UI so the user can't accidentally
            re-trigger the failure. Driven by AuthContext.waitlistedEmail so
            the state persists across the auth flow and is a single source of
            truth. */}
        {waitlistedEmail ? (
          <WaitlistConfirmation
            email={waitlistedEmail}
            message={waitlistedMessage}
            onBack={() => {
              clearWaitlisted();
              // Back-to-sign-in also resets the local form state so the
              // user can try a different email without seeing stale input
              // from the attempt that just hit the cap.
              setEmail('');
              setPin('');
              setFirstName('');
              setLastName('');
              setStep('email');
              setError('');
            }}
          />
        ) : (
        /* Regular sign-in flow (Google + email PIN) */
        <>
        {/* TOS Agreement - age gate + binding link to Terms/Privacy.
            The full AI / health / eating-disorder carve-outs live in
            Terms §7 and §8 - by agreeing here the user is bound to them
            the same as if they were inlined.  Not repeated in the
            checkbox because wall-of-text disclaimers get skipped. */}
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={tosAgreed}
            onChange={(e) => setTosAgreed(e.target.checked)}
            className="mt-0.5 w-4 h-4 rounded accent-emerald-500 shrink-0"
          />
          <span className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            I'm 16+ and agree to the{' '}
            <Link to="/terms" className="underline" style={{ color: '#10b981' }}>
              Terms
            </Link>
            {' '}and{' '}
            <Link to="/privacy" className="underline" style={{ color: '#10b981' }}>
              Privacy Policy
            </Link>
            .
          </span>
        </label>

        {/* Google Sign-In - custom styled visual over an invisible Google
            rendered button. The iframe captures the click (no One Tap
            cooldown), our overlay shows the branded look. Wrapper height
            matches Google's large button (40px) so clicks never miss. */}
        {googleReady && (
          <>
            <div
              ref={googleWrapperRef}
              className="relative w-full mx-auto"
              style={{ height: 40, maxWidth: 400 }}
            >
              {/* Real Google button - captures clicks, near-invisible */}
              <div
                ref={googleButtonRef}
                className="absolute inset-0 z-10"
                style={{
                  opacity: 0.001,
                  pointerEvents: tosAgreed && !loading ? 'auto' : 'none',
                }}
              />
              {/* Branded visual overlay - pointer-events:none so clicks
                  fall through to the Google iframe below */}
              <div
                aria-hidden="true"
                className="absolute inset-0 flex items-center justify-center gap-2 bg-white text-gray-800 rounded-xl font-medium shadow-glass-sm pointer-events-none transition-opacity"
                style={{ opacity: tosAgreed ? 1 : 0.5 }}
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Sign in with Google
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>or</span>
              <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
            </div>
          </>
        )}

        {step === 'email' ? (
          <form onSubmit={sendPin} className="space-y-4">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Enter your email"
              required
              className="w-full glass-input"
            />
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={loading || !tosAgreed}
              className="w-full"
            >
              {loading ? 'Sending...' : 'Send Code'}
            </Button>
          </form>
        ) : (
          <form onSubmit={verifyPin} className="space-y-4">
            <p className="text-sm text-center" style={{ color: 'var(--text-secondary)' }}>
              We sent a code to <span style={{ color: 'var(--text-primary)' }}>{email}</span>
            </p>
            <input
              type="text"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="6-digit code"
              maxLength={6}
              required
              className="w-full glass-input text-center text-2xl tracking-widest"
            />
            <div className="flex gap-2">
              <input
                type="text"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                placeholder="First name (optional)"
                className="flex-1 glass-input text-sm"
              />
              <input
                type="text"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                placeholder="Last name (optional)"
                className="flex-1 glass-input text-sm"
              />
            </div>
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={loading || pin.length < 6 || !tosAgreed}
              className="w-full"
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => { setStep('email'); setPin(''); setError(''); }}
              className="w-full"
            >
              Use a different email
            </Button>
          </form>
        )}

        {error && (
          <p className="text-red-400 text-sm text-center">{error}</p>
        )}

        <div className="flex items-center gap-4 pt-2">
          <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>or</span>
          <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
        </div>
        <Button
          type="button"
          variant="secondary"
          size="lg"
          className="w-full"
          onClick={() => {
            enterGuestMode();
            navigate('/');
          }}
        >
          Continue as guest
        </Button>
        <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
          Try a meal analysis without signing up. Meals you log stay on
          this device until you create an account.
        </p>
        </>
        )}
      </div>
    </div>
  );
}
