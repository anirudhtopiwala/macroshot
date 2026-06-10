import { Link } from 'react-router-dom';
import Button from '../../components/Button';
import { useSiteConfig } from '../../api/siteConfig';
import { GitHubIcon } from './icons';

export default function Hero() {
  const { github_url } = useSiteConfig();
  const GITHUB_URL = github_url || '#';

  return (
    <section className="pt-10 md:pt-16 pb-12 md:pb-24">
      <div className="flex items-center justify-center gap-4 mb-6 md:mb-8">
        <img
          src="/macro_app/logo-login.png"
          alt=""
          className="w-14 h-14 md:w-16 md:h-16 shrink-0"
          style={{ filter: 'drop-shadow(0 10px 30px rgba(16,185,129,0.25))' }}
        />
        <span
          className="text-4xl md:text-6xl font-black italic tracking-wider leading-none"
          style={{ color: 'var(--text-primary)' }}
        >
          MacroShot
        </span>
      </div>

      <div className="relative mb-10 md:mb-14">
        <div
          aria-hidden
          className="absolute inset-0 -z-10 pointer-events-none"
          style={{
            background:
              'radial-gradient(circle at 50% 50%, rgba(16,185,129,0.25) 0%, rgba(168,85,247,0.14) 45%, transparent 70%)',
            filter: 'blur(60px)',
            transform: 'scale(1.05)',
          }}
        />
        <img
          src="/macro_app/marketing/hero-log.png"
          alt="MacroShot analyzing a Buddha bowl with AI-identified ingredients"
          className="w-full h-auto block"
          style={{
            borderRadius: '1.25rem',
            border: '1px solid rgba(16, 185, 129, 0.25)',
            boxShadow: '0 30px 80px -20px rgba(0, 0, 0, 0.6)',
          }}
        />
      </div>

      <div className="text-center max-w-3xl mx-auto">
        <h1
          className="text-3xl md:text-5xl font-black tracking-tight leading-[1.1] mb-5"
          style={{ color: 'var(--text-primary)' }}
        >
          Snap a photo.
          <br />
          Know your macros.
          <br />
          <span
            style={{
              background: 'linear-gradient(135deg, #10b981 0%, #2dd4bf 40%, #a855f7 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >
            Own your data.
          </span>
        </h1>

        <p
          className="text-base md:text-lg leading-relaxed mb-8 max-w-xl mx-auto"
          style={{ color: 'var(--text-secondary)' }}
        >
          The open-source AI macro tracker. Log meals by photo, text, or barcode. Chat with a
          nutrition coach that actually reads your data. Runs on every device you own - phone,
          tablet, laptop.
        </p>

        <div className="flex flex-wrap gap-3 mb-5 justify-center">
          <Link to="/login">
            <Button variant="primary" size="lg">
              Try MacroShot now →
            </Button>
          </Link>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <Button variant="secondary" size="lg">
              <GitHubIcon />
              Star on GitHub
            </Button>
          </a>
        </div>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Open beta · Limited free spots · No credit card
        </p>
      </div>
    </section>
  );
}
