import { Link } from 'react-router-dom';
import Button from '../../components/Button';

export default function FinalCTA() {
  return (
    <section className="py-12 md:py-20">
      <div
        className="relative overflow-hidden rounded-3xl p-10 md:p-16 text-center"
        style={{
          background:
            'linear-gradient(135deg, rgba(16,185,129,0.14) 0%, rgba(45,212,191,0.08) 50%, rgba(168,85,247,0.12) 100%)',
          border: '1px solid rgba(16, 185, 129, 0.25)',
          boxShadow: '0 0 60px -20px rgba(16, 185, 129, 0.3)',
        }}
      >
        <div
          aria-hidden
          className="absolute inset-0 -z-10"
          style={{
            background:
              'radial-gradient(circle at 20% 30%, rgba(16,185,129,0.22) 0%, transparent 50%), radial-gradient(circle at 80% 70%, rgba(168,85,247,0.18) 0%, transparent 50%)',
          }}
        />
        <h2
          className="text-3xl md:text-5xl font-black tracking-tight mb-4"
          style={{ color: 'var(--text-primary)' }}
        >
          Ready to try it?
        </h2>
        <p
          className="text-base md:text-lg mb-8 max-w-xl mx-auto"
          style={{ color: 'var(--text-secondary)' }}
        >
          Open beta · Limited free spots · No credit card.
        </p>
        <Link to="/" className="inline-block">
          <Button variant="primary" size="lg">
            Try MacroShot now →
          </Button>
        </Link>
      </div>
    </section>
  );
}
