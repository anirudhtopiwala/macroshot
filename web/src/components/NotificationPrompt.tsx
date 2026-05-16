import { useState, useEffect } from 'react';
import { Bell, X } from './icons';
import Button from './Button';
import { subscribeToPush, getPushStatus } from '../api/push';
import { api } from '../api/client';
import { hapticLight } from '../utils/haptics';

/**
 * Notification permission prompt card - shown on Dashboard after first meal accept.
 *
 * Only shows when:
 * - User has logged at least 1 meal
 * - Push notifications are supported
 * - Permission hasn't been granted yet
 * - User hasn't dismissed the prompt twice
 */
export default function NotificationPrompt() {
  const [visible, setVisible] = useState(false);
  const [enabling, setEnabling] = useState(false);

  useEffect(() => {
    (async () => {
      // Don't show if user has dismissed twice
      const dismissCount = parseInt(localStorage.getItem('notif_prompt_dismiss_count') || '0', 10);
      if (dismissCount >= 2) return;

      // Don't show if no meals logged yet
      const hasLoggedMeal = localStorage.getItem('has_logged_meal');
      if (!hasLoggedMeal) return;

      // Check push status
      const status = await getPushStatus();
      if (!status.supported) return;
      if (status.permission === 'granted' && status.subscribed) return;
      if (status.permission === 'denied') return;

      setVisible(true);
    })();
  }, []);

  if (!visible) return null;

  const handleEnable = async () => {
    hapticLight();
    setEnabling(true);
    try {
      const success = await subscribeToPush();
      if (success) {
        // Auto-detect timezone and set reminders on with defaults
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        await api.put('/settings/prefs', {
          reminders_on: 1,
          timezone: tz,
        });
        setVisible(false);
        localStorage.setItem('notif_prompt_dismiss_count', '99'); // Never show again
      }
    } catch (err) {
      console.warn('[push] enable failed:', err);
    }
    setEnabling(false);
  };

  const handleDismiss = () => {
    hapticLight();
    const count = parseInt(localStorage.getItem('notif_prompt_dismiss_count') || '0', 10);
    localStorage.setItem('notif_prompt_dismiss_count', String(count + 1));
    setVisible(false);
  };

  return (
    <div className="glass-card p-4 relative">
      <button
        onClick={handleDismiss}
        className="absolute top-3 right-3 p-1 rounded-lg active:scale-90 transition-transform"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
      </button>
      <div className="flex items-start gap-3">
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
          style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}
        >
          <Bell className="w-5 h-5" style={{ color: '#10b981' }} />
        </div>
        <div className="flex-1 min-w-0 pr-4">
          <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
            Get meal reminders
          </p>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            A gentle nudge at meal times to keep your streak alive. You can customize times in Settings.
          </p>
          <div className="flex gap-2 mt-3">
            <Button
              variant="primary"
              size="sm"
              onClick={handleEnable}
              disabled={enabling}
            >
              {enabling ? 'Enabling...' : 'Enable'}
            </Button>
            <button
              onClick={handleDismiss}
              className="text-xs font-medium px-3 py-1.5 rounded-lg active:scale-95 transition-transform"
              style={{ color: 'var(--text-muted)' }}
            >
              Not now
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
