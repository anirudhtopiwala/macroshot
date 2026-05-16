import { Link, useLocation } from 'react-router-dom';

export default function NotFound() {
  const { pathname } = useLocation();

  return (
    <div className="space-y-6 text-center py-12">
      <div>
        <p className="text-5xl font-bold" style={{ color: 'var(--text-primary)' }}>404</p>
        <p className="mt-2 text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
          Page not found
        </p>
        <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
          We couldn't find <span className="font-mono">{pathname}</span>.
        </p>
      </div>

      <div className="flex justify-center gap-3">
        <Link
          to="/"
          className="px-4 py-2 rounded-xl text-sm font-semibold"
          style={{ background: 'var(--accent)', color: 'var(--accent-contrast, #fff)' }}
        >
          Go to Dashboard
        </Link>
      </div>
    </div>
  );
}
