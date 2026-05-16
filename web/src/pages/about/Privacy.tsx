import { Link } from 'react-router-dom';
import CheckListItem from './CheckListItem';

const BULLETS: Array<{ title: string; body: string }> = [
  {
    title: 'No ads, no trackers, no data sales.',
    body: 'We don’t have a business model that depends on watching what you eat.',
  },
  {
    title: 'Delete anything, anytime.',
    body: 'Remove a meal, wipe your account. The images go with it.',
  },
  {
    title: 'Want zero third parties? Self-host.',
    body: 'Runs on your own machine with your own API key. No calls home.',
  },
];

export default function Privacy() {
  return (
    <section className="py-12 md:py-20">
      <div className="max-w-3xl mx-auto">
        <div className="text-center mb-8 md:mb-10">
          <p className="section-heading mb-3">Privacy</p>
          <h2
            className="text-3xl md:text-4xl font-black tracking-tight mb-4"
            style={{ color: 'var(--text-primary)' }}
          >
            Your meals are yours.
          </h2>
          <p
            className="text-base leading-relaxed max-w-2xl mx-auto"
            style={{ color: 'var(--text-secondary)' }}
          >
            Here’s what happens when you log a meal. The fine print lives in our{' '}
            <Link to="/terms" className="underline underline-offset-4" style={{ color: '#3b82f6' }}>
              Terms
            </Link>{' '}
            and{' '}
            <Link to="/privacy" className="underline underline-offset-4" style={{ color: '#3b82f6' }}>
              Privacy Policy
            </Link>
            .
          </p>
        </div>
        <ul className="space-y-3">
          {BULLETS.map((item) => (
            <CheckListItem key={item.title} accent="blue">
              <strong style={{ color: 'var(--text-primary)' }}>{item.title}</strong> {item.body}
            </CheckListItem>
          ))}
        </ul>
      </div>
    </section>
  );
}
