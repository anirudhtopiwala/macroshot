import Button from '../../components/Button';
import { useSiteConfig } from '../../api/siteConfig';
import { GitHubIcon } from './icons';

export default function SelfHost() {
  const { github_url } = useSiteConfig();
  const GITHUB_URL = github_url || '#';
  const hasGithub = Boolean(github_url);
  const cloneTarget = hasGithub
    ? GITHUB_URL.replace('https://', '')
    : 'github.com/your-org/macro_app';

  return (
    <section className="py-12 md:py-20">
      <div className="grid md:grid-cols-2 gap-10 md:gap-12 items-start">
        <div>
          <p className="section-heading mb-3">Open source</p>
          <h2
            className="text-3xl md:text-4xl font-black tracking-tight mb-5"
            style={{ color: 'var(--text-primary)' }}
          >
            Built in the open.
          </h2>
          <p className="text-base leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
            MacroShot’s entire codebase is public. Every pixel, every prompt, every query.
            Auditable and forkable. The app is{' '}
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-4"
              style={{ color: '#10b981' }}
            >
              open on GitHub
            </a>
            , and you can run it end-to-end on your own machine with an AI API key.
          </p>
          <p className="text-base leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
            I’m building it this way because nutrition tracking should be transparent and
            community-driven. If you’re a developer, a designer, or just someone who wants to own
            their data, clone it, run it, and make it better.
          </p>
          <p className="text-base leading-relaxed mb-6" style={{ color: 'var(--text-secondary)' }}>
            A new “best model on the planet” drops every Tuesday. Self-host and you get to run
            whichever one’s winning this week. We don’t pick which AI thinks about your lunch. You do.
          </p>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <Button variant="primary" size="lg">
              <GitHubIcon />
              View on GitHub
            </Button>
          </a>
        </div>

        <div
          className="glass-card p-5 md:p-6 font-mono text-xs md:text-sm leading-relaxed overflow-x-auto"
          style={{ color: 'var(--text-secondary)' }}
        >
          <div
            className="flex items-center gap-1.5 mb-4 pb-3 border-b"
            style={{ borderColor: 'var(--border-glass)' }}
          >
            <span className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
            <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
            <span className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
            <span
              className="ml-3 text-[10px] uppercase tracking-wider"
              style={{ color: 'var(--text-muted)' }}
            >
              self-host
            </span>
          </div>
          <div className="space-y-1.5">
            <div><span style={{ color: '#10b981' }}>$</span> git clone {cloneTarget}</div>
            <div><span style={{ color: '#10b981' }}>$</span> cd macro_app</div>
            <div><span style={{ color: '#10b981' }}>$</span> python3 -m venv .venv</div>
            <div><span style={{ color: '#10b981' }}>$</span> .venv/bin/pip install -r requirements.txt</div>
            <div><span style={{ color: '#10b981' }}>$</span> cd web && npm install && npm run build</div>
            <div><span style={{ color: '#10b981' }}>$</span> export GEMINI_API_KEY=...</div>
            <div><span style={{ color: '#10b981' }}>$</span> .venv/bin/uvicorn src.web.app:app --port 8000</div>
            <div className="pt-2" style={{ color: 'var(--text-muted)' }}># → http://localhost:8000/macro_app/</div>
          </div>
          <p
            className="mt-4 pt-3 border-t text-[11px] italic"
            style={{ borderColor: 'var(--border-glass)', color: 'var(--text-muted)' }}
          >
            Runs entirely on your own machine. Every feature unlocked.
          </p>
        </div>
      </div>
    </section>
  );
}
