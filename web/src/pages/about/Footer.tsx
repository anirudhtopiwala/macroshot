import { Link } from 'react-router-dom';
import { useSiteConfig } from '../../api/siteConfig';

export default function Footer() {
  const { github_url } = useSiteConfig();
  const GITHUB_URL = github_url || '#';

  return (
    <footer
      className="border-t py-8 mt-8"
      style={{
        borderColor: 'var(--border-glass)',
        paddingBottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))',
      }}
    >
      <div
        className="max-w-5xl mx-auto px-4 md:px-6 flex flex-col md:flex-row items-center justify-between gap-4 text-xs"
        style={{ color: 'var(--text-muted)' }}
      >
        <div className="flex items-center gap-2.5">
          <img src="/macro_app/logo-login.png" alt="" className="w-5 h-5" />
          <span className="font-bold italic tracking-wider" style={{ color: 'var(--text-secondary)' }}>
            MacroShot
          </span>
          <span>·</span>
          <span>Open source · FSL-1.1-Apache-2.0</span>
        </div>
        <div className="flex items-center gap-5">
          <Link to="/terms" className="hover:underline">Terms</Link>
          <Link to="/privacy" className="hover:underline">Privacy</Link>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className="hover:underline">
            GitHub
          </a>
        </div>
      </div>
    </footer>
  );
}
