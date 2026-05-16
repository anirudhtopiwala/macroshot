import { useState, useEffect } from 'react';
import IOSInstallAnimation from './IOSInstallAnimation';

type Platform = 'ios' | 'android';

interface Step {
  title: string;
  body: string;
}

const STEPS: Record<Platform, Step[]> = {
  ios: [
    {
      title: 'Open in Safari or Chrome',
      body: 'Visit MacroShot in Safari or Chrome on your iPhone or iPad. Both browsers work. The only difference is where the Share button lives.',
    },
    {
      title: 'Tap Share',
      body: 'In Safari, tap the ··· button on the floating URL bar at the bottom, then tap Share. (If the toolbar is hidden, tap the bottom URL pill once to bring it back. On iPad, tap the Share icon next to the address bar directly.) In Chrome, the Share button sits on the address bar itself. Tap it directly, no menu needed.',
    },
    {
      title: 'Scroll down and tap "Add to Home Screen"',
      body: 'In both browsers the share sheet opens the same way. Scroll down past the row of apps until you see "Add to Home Screen" and tap it.',
    },
    {
      title: 'Tap "Add"',
      body: 'MacroShot lands on your home screen. Tap to launch - full-screen, offline-ready, native feel.',
    },
  ],
  android: [
    {
      title: 'Open in Chrome',
      body: 'Visit MacroShot in Chrome (or any Chromium-based browser like Edge or Brave).',
    },
    {
      title: 'Tap "Install app"',
      body: 'Watch for the install banner at the bottom, or open the menu (⋮) and tap "Install app".',
    },
    {
      title: 'Tap "Install"',
      body: 'MacroShot lands on your home screen with its own icon - exactly like a native app.',
    },
  ],
};

function detectPlatform(): Platform {
  if (typeof navigator === 'undefined') return 'ios';
  const ua = navigator.userAgent.toLowerCase();
  if (/android/.test(ua)) return 'android';
  return 'ios';
}

function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-6">
      {steps.map((step, i) => (
        <li key={i} className="flex items-start gap-4">
          <span
            className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center font-black text-base"
            style={{
              background: 'rgba(16, 185, 129, 0.18)',
              border: '2px solid rgba(16, 185, 129, 0.55)',
              color: '#10b981',
              boxShadow: '0 6px 20px rgba(16,185,129,0.3)',
              backdropFilter: 'blur(12px)',
            }}
          >
            {i + 1}
          </span>
          <div className="pt-1">
            <h3
              className="text-base md:text-lg font-bold mb-1"
              style={{ color: 'var(--text-primary)' }}
            >
              {step.title}
            </h3>
            <p
              className="text-sm leading-relaxed"
              style={{ color: 'var(--text-muted)' }}
            >
              {step.body}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function InstallPwaSection() {
  const [platform, setPlatform] = useState<Platform>('ios');

  useEffect(() => {
    setPlatform(detectPlatform());
  }, []);

  const steps = STEPS[platform];

  return (
    <section className="py-12 md:py-20">
      <div className="text-center mb-8 md:mb-10">
        <p className="section-heading mb-3">Install it</p>
        <h2
          className="text-3xl md:text-4xl font-black tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          Add MacroShot to your home screen.
        </h2>
        <p className="mt-4 text-base max-w-2xl mx-auto" style={{ color: 'var(--text-muted)' }}>
          One link, one tap. MacroShot installs like a native app - full-screen, offline-ready, with
          push reminders. No app store. No review cycle. No 50 MB download.
        </p>
      </div>

      {/* Platform tabs */}
      <div className="flex justify-center gap-2 mb-10 md:mb-12">
        {(['ios', 'android'] as Platform[]).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPlatform(p)}
            className={platform === p ? 'pill-active' : 'pill-inactive'}
          >
            {p === 'ios' ? 'iPhone / iPad' : 'Android'}
          </button>
        ))}
      </div>

      {/* Steps + animation */}
      {platform === 'ios' ? (
        <div className="grid md:grid-cols-2 gap-10 md:gap-16 items-center max-w-5xl mx-auto">
          <div className="order-2 md:order-1">
            <StepList steps={steps} />
          </div>
          <div className="order-1 md:order-2 flex justify-center">
            <div className="w-full max-w-[260px]">
              <IOSInstallAnimation />
            </div>
          </div>
        </div>
      ) : (
        <div className="max-w-xl mx-auto">
          <StepList steps={steps} />
        </div>
      )}

      {/* Desktop footnote */}
      <div className="mt-10 md:mt-14 max-w-2xl mx-auto">
        <div className="glass-card px-5 py-4 flex items-start gap-3">
          <span className="text-xl shrink-0" aria-hidden>🖥️</span>
          <p className="text-xs md:text-sm leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            <strong style={{ color: 'var(--text-secondary)' }}>On desktop?</strong> Open MacroShot in
            Chrome or Edge, click the install icon (a small monitor) in the address bar, then "Install
            MacroShot". It opens in its own window - accessible from your dock or taskbar.
          </p>
        </div>
      </div>
    </section>
  );
}
