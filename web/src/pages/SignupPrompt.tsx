import { Link, useLocation } from 'react-router-dom';
import Button from '../components/Button';

/**
 * Default destination for guest-mode visitors who hit a route that
 * requires a real account (chat, journal, trends, settings, etc.).
 * No API calls — we never want a stray request to 401 the guest off
 * the app shell.
 */
export default function SignupPrompt() {
  const { pathname } = useLocation();
  return (
    <div className="py-12 text-center space-y-4">
      <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>
        Sign up to unlock this
      </h1>
      <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
        Guest mode lets you try meal analysis and see today's totals.
        The rest of MacroShot — streaks, AI coaching, history, and
        trends — opens up after a 10-second signup.
      </p>
      <div className="pt-2">
        <Link to="/signup">
          <Button variant="primary" size="lg">Sign up — it's free</Button>
        </Link>
      </div>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Your guest meals migrate into your new account automatically.
      </p>
      {pathname && pathname !== '/' && (
        <Link to="/" className="text-xs underline" style={{ color: 'var(--text-muted)' }}>
          ← Back to dashboard
        </Link>
      )}
    </div>
  );
}
