import { Link } from 'react-router-dom';
import Button from '../../components/Button';
import { useSiteConfig } from '../../api/siteConfig';
import { GitHubIcon, HeartIcon } from './icons';

export default function Header() {
  const { github_url, sponsor_url, donate_url } = useSiteConfig();
  const GITHUB_URL = github_url || '#';
  // Header pill points at whichever donation route is configured;
  // donate_url (Stripe) wins because it accepts payment from anyone
  // without a GitHub account. Pill hides entirely when both are unset.
  const SUPPORT_URL = donate_url || sponsor_url || null;

  return (
    <header
      className="sticky top-0 z-50 backdrop-blur-[24px] border-b"
      style={{
        background: 'var(--header-bg)',
        borderColor: 'var(--border-glass)',
        paddingTop: 'env(safe-area-inset-top, 0px)',
      }}
    >
      <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
        <Link
          to="/about"
          aria-current="page"
          className="flex items-center gap-2.5 active:scale-[0.98] transition-transform"
        >
          <img src="/macro_app/logo-login.png" alt="" className="w-7 h-7" />
          <span
            className="text-base font-bold italic tracking-wide"
            style={{ color: 'var(--text-primary)' }}
          >
            MacroShot
          </span>
        </Link>
        <div className="flex items-center gap-2">
          {SUPPORT_URL && (
            <a
              href={SUPPORT_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Sponsor MacroShot"
              className="inline-flex items-center gap-1.5 px-2.5 sm:px-3 py-2 rounded-xl text-xs font-semibold transition-all active:scale-95"
              style={{
                background: 'rgba(244,63,94,0.1)',
                border: '1px solid rgba(244,63,94,0.25)',
                color: '#f43f5e',
              }}
            >
              <HeartIcon className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Sponsor</span>
            </a>
          )}
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-block"
          >
            <Button variant="secondary" size="sm">
              <GitHubIcon />
              GitHub
            </Button>
          </a>
          <Link to="/">
            <Button variant="primary" size="sm">
              Open the app →
            </Button>
          </Link>
        </div>
      </div>
    </header>
  );
}
