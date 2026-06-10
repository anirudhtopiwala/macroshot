import { Link } from 'react-router-dom';
import Button from '../../components/Button';
import { useSiteConfig } from '../../api/siteConfig';
import { CheckIcon, HeartIcon } from './icons';

export default function BetaCTA() {
  const { donate_url, sponsor_url } = useSiteConfig();
  const SUPPORT_URL = donate_url || sponsor_url || null;

  return (
    <section className="py-12 md:py-20">
      <div className="glass-card p-8 md:p-12 text-center max-w-2xl mx-auto relative overflow-hidden">
        <div
          aria-hidden
          className="absolute inset-0 -z-10 opacity-60"
          style={{
            background:
              'radial-gradient(circle at 50% 0%, rgba(16,185,129,0.12) 0%, transparent 60%)',
          }}
        />
        <div
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-5"
          style={{
            background: 'rgba(16, 185, 129, 0.1)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
          }}
        >
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
            Open beta · Limited spots
          </span>
        </div>
        <h2
          className="text-3xl md:text-4xl font-black tracking-tight mb-4"
          style={{ color: 'var(--text-primary)' }}
        >
          Get in while it’s free.
        </h2>
        <p
          className="text-base leading-relaxed mb-6 max-w-lg mx-auto"
          style={{ color: 'var(--text-secondary)' }}
        >
          MacroShot is in open beta and a limited number of users get full access at no cost.
          Every feature unlocked, no credit card. Or grab the code and self-host on your own
          machine. Your choice, your data.
        </p>
        <div className="flex flex-wrap gap-2 justify-center mb-6">
          {['Every feature unlocked', 'No credit card', 'Self-host anytime'].map((chip) => (
            <span
              key={chip}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
              style={{
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.25)',
                color: '#10b981',
              }}
            >
              <CheckIcon className="w-3 h-3" />
              {chip}
            </span>
          ))}
        </div>
        <Link to="/login" className="inline-block">
          <Button variant="primary" size="lg">
            Try MacroShot now →
          </Button>
        </Link>
        {SUPPORT_URL && (
          <p
            className="mt-6 text-sm"
            style={{ color: 'var(--text-muted)' }}
          >
            The beta stays free. If you’d like to help keep the open-source project going,{' '}
            <a
              href={SUPPORT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 underline underline-offset-4 font-semibold"
              style={{ color: '#f43f5e' }}
            >
              <HeartIcon className="w-3.5 h-3.5" />
              donate or sponsor
            </a>
            .
          </p>
        )}
      </div>
    </section>
  );
}
