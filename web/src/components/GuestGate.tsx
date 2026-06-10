import { Link } from 'react-router-dom';
import { Lock } from './icons';
import Button from './Button';

/**
 * Wraps a page that guests may browse to but cannot use. The real page is
 * rendered underneath - blurred and non-interactive - so the visitor gets
 * a teaser of what they unlock, with a glass signup card floating on top.
 *
 * Used for /trends and /journal: unlike chat/settings sub-pages (which use
 * SignupPrompt and render nothing of value), these pages have a visually
 * rich layout worth previewing behind the glass.
 *
 * The child page still mounts and may fire its own API calls; for a guest
 * those 401 and are swallowed by the client interceptor (it never redirects
 * while the guest flag is set). The blur hides whatever empty/error state
 * results, so the preview reads as "locked content" rather than "broken".
 */
export default function GuestGate({
  children,
  title,
  subtitle,
}: {
  children: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="relative">
      {/* Blurred, inert preview of the real page */}
      <div
        className="pointer-events-none select-none blur-[6px] opacity-60"
        aria-hidden
      >
        {children}
      </div>

      {/* Signup glass overlay */}
      <div className="absolute inset-0 flex items-start justify-center pt-20 px-4">
        <div
          className="w-full max-w-xs rounded-2xl p-6 text-center space-y-4 backdrop-blur-[24px]"
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-glass)',
            boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
          }}
        >
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center mx-auto"
            style={{ background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.25)' }}
          >
            <Lock className="w-5 h-5" style={{ color: '#10b981' }} />
          </div>
          <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>
            {title}
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            {subtitle}
          </p>
          <Link to="/signup" className="block">
            <Button variant="primary" size="lg" className="w-full">Sign up - it's free</Button>
          </Link>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Your guest meals migrate into your new account automatically.
          </p>
        </div>
      </div>
    </div>
  );
}
