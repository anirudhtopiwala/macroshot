import { useState } from 'react';
import { useSiteConfig } from '../../api/siteConfig';

export default function Community() {
  const { github_url, app_url, sponsor_url, donate_url } = useSiteConfig();
  const GITHUB_URL = github_url || '#';
  const SHARE_URL = app_url ? `${app_url}about` : '';
  const hasGithub = Boolean(github_url);

  const [shareCopied, setShareCopied] = useState(false);
  const handleCopyShare = async () => {
    if (!SHARE_URL) return;
    try {
      await navigator.clipboard?.writeText(SHARE_URL);
      setShareCopied(true);
      window.setTimeout(() => setShareCopied(false), 2000);
    } catch {
      /* noop */
    }
  };

  // Compute grid columns based on how many tiles render. Two sponsor
  // tiles (Stripe + GitHub Sponsors) push the count past 5, at which
  // point a 3-col grid (2 rows × 3) reads cleaner than cramming 6 into
  // one row.
  const tileCount =
    (hasGithub ? 3 : 0) + 1 + (donate_url ? 1 : 0) + (sponsor_url ? 1 : 0);
  const gridCols =
    tileCount >= 6
      ? 'md:grid-cols-3'
      : tileCount === 5
        ? 'md:grid-cols-5'
        : 'md:grid-cols-4';

  return (
    <section className="py-12 md:py-20">
      <div className="text-center mb-10 md:mb-12">
        <p className="section-heading mb-3">Contribute</p>
        <h2
          className="text-3xl md:text-4xl font-black tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          Help make it better.
        </h2>
        <p
          className="mt-4 text-base max-w-2xl mx-auto"
          style={{ color: 'var(--text-muted)' }}
        >
          Every star, bug report, and PR helps. Even just sharing with a friend makes a difference.
        </p>
      </div>

      <div className={`grid grid-cols-2 ${gridCols} gap-3 md:gap-4`}>
        {hasGithub && (
          <>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="glass-card p-5 flex flex-col gap-2 active:scale-[0.98] transition-transform"
            >
              <span className="text-2xl" aria-hidden>⭐</span>
              <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Star the repo</h3>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Helps others discover it.</p>
            </a>
            <a
              href={`${GITHUB_URL}/issues`}
              target="_blank"
              rel="noopener noreferrer"
              className="glass-card p-5 flex flex-col gap-2 active:scale-[0.98] transition-transform"
            >
              <span className="text-2xl" aria-hidden>🐛</span>
              <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Report a bug</h3>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Open an issue on GitHub.</p>
            </a>
            <a
              href={`${GITHUB_URL}/pulls`}
              target="_blank"
              rel="noopener noreferrer"
              className="glass-card p-5 flex flex-col gap-2 active:scale-[0.98] transition-transform"
            >
              <span className="text-2xl" aria-hidden>🔧</span>
              <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Submit a PR</h3>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Fix, feature, or refactor.</p>
            </a>
          </>
        )}
        <button
          type="button"
          onClick={handleCopyShare}
          aria-live="polite"
          className="glass-card p-5 flex flex-col gap-2 text-left active:scale-[0.98] transition-transform"
        >
          <span className="text-2xl" aria-hidden>{shareCopied ? '✅' : '📣'}</span>
          <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
            {shareCopied ? 'Link copied!' : 'Share with a friend'}
          </h3>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {shareCopied ? 'Paste it anywhere.' : 'Copy the link to clipboard.'}
          </p>
        </button>
        {donate_url && (
          <a
            href={donate_url}
            target="_blank"
            rel="noopener noreferrer"
            className="glass-card p-5 flex flex-col gap-2 active:scale-[0.98] transition-transform"
          >
            <span className="text-2xl" aria-hidden>☕</span>
            <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Buy me a coffee</h3>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>One-time tip via Stripe.</p>
          </a>
        )}
        {sponsor_url && (
          <a
            href={sponsor_url}
            target="_blank"
            rel="noopener noreferrer"
            className="glass-card p-5 flex flex-col gap-2 active:scale-[0.98] transition-transform"
          >
            <span className="text-2xl" aria-hidden>❤️</span>
            <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>GitHub Sponsors</h3>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Recurring or one-time, no fees.</p>
          </a>
        )}
      </div>
    </section>
  );
}
