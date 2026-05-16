import type { ReactNode } from 'react';
import { Lock } from './icons';
import { useSubscription } from '../context/SubscriptionContext';
import { subscriptionApi } from '../api/subscription';

interface Props {
  children: ReactNode;
  message?: string;
}

/**
 * Wraps content in a blur overlay for free users.
 * Premium users see children normally.
 */
export default function ProBlur({ children, message = 'Upgrade to Pro' }: Props) {
  const { isPremium } = useSubscription();

  if (isPremium) return <>{children}</>;

  return (
    <div className="relative rounded-2xl overflow-hidden">
      <div style={{ filter: 'blur(6px)', pointerEvents: 'none', userSelect: 'none', maxHeight: 400, overflow: 'hidden' }} aria-hidden>
        {children}
      </div>
      {/* Fade-out gradient - uses CSS variable for theme-aware background */}
      <div className="absolute bottom-0 left-0 right-0 h-24 pointer-events-none" style={{ background: 'linear-gradient(transparent, var(--bg-base))' }} />
      {/* Lock + Go Pro overlay */}
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2.5">
        <div
          className="flex items-center gap-2 px-4 py-2 rounded-full"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)', backdropFilter: 'blur(16px)' }}
        >
          <Lock className="w-3.5 h-3.5" style={{ color: '#f59e0b' }} />
          <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{message}</span>
        </div>
        <button
          className="text-xs font-bold px-5 py-2 rounded-full transition-all active:scale-95"
          style={{ background: 'rgba(16,185,129,0.2)', color: '#10b981', border: '1px solid rgba(16,185,129,0.4)' }}
          onClick={async () => {
            try {
              const data = await subscriptionApi.checkout('pro_monthly');
              if (data.url) window.location.href = data.url;
            } catch { /* ignore */ }
          }}
        >
          Go Pro - $4.99/mo
        </button>
        {/* CA ARL / FTC Click-to-Cancel - required inline auto-renewal disclosure. */}
        <p className="text-[10px] text-center max-w-[240px] leading-snug px-2" style={{ color: 'var(--text-muted)' }}>
          $4.99/mo. Auto-renews until cancelled. Cancel any time in Settings.
        </p>
      </div>
    </div>
  );
}
