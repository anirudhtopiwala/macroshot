import { useEffect, useState, useRef, useCallback } from 'react';
import { Check, Bell } from '../components/icons';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { api } from '../api/client';
import { subscribeToPush, subscribeToPushDetailed, unsubscribeFromPush, getPushStatus } from '../api/push';
import { hapticLight } from '../utils/haptics';
import { useToast } from '../components/Toast';
import { getCached, setCache } from '../utils/apiCache';
import type { Prefs } from '../types';

// Group timezones by region for <optgroup> rendering
function getGroupedTimezones(): Record<string, { tz: string; city: string }[]> {
  const zones = Intl.supportedValuesOf('timeZone');
  const groups: Record<string, { tz: string; city: string }[]> = {};
  const regionLabels: Record<string, string> = {
    Africa: 'Africa',
    America: 'Americas',
    Antarctica: 'Antarctica',
    Arctic: 'Arctic',
    Asia: 'Asia',
    Atlantic: 'Atlantic',
    Australia: 'Australia',
    Europe: 'Europe',
    Indian: 'Indian Ocean',
    Pacific: 'Pacific',
  };
  for (const tz of zones) {
    const parts = tz.split('/');
    const regionKey = parts[0];
    const label = regionLabels[regionKey] || regionKey;
    const city = parts[parts.length - 1].replace(/_/g, ' ');
    if (!groups[label]) groups[label] = [];
    groups[label].push({ tz, city });
  }
  // Sort cities within each group
  for (const key of Object.keys(groups)) {
    groups[key].sort((a, b) => a.city.localeCompare(b.city));
  }
  return groups;
}

const GROUPED_TIMEZONES = getGroupedTimezones();

const MEAL_HOURS = [
  { key: 'breakfast_hour' as const, label: 'Breakfast reminder' },
  { key: 'lunch_hour' as const, label: 'Lunch reminder' },
  { key: 'snack_hour' as const, label: 'Snack reminder' },
  { key: 'dinner_hour' as const, label: 'Dinner reminder' },
  { key: 'streak_alert_hour' as const, label: 'Streak alert' },
];

function hourLabel(h: number): string {
  if (h === 0) return '12 AM';
  if (h < 12) return `${h} AM`;
  if (h === 12) return '12 PM';
  return `${h - 12} PM`;
}

export default function SettingsReminders() {
  const { toast } = useToast();
  const cachedPrefs = getCached<Prefs>('settings_reminders');
  const [loading, setLoading] = useState(!cachedPrefs);
  const [prefs, setPrefs] = useState<Prefs | null>(cachedPrefs);
  const [pushSupported, setPushSupported] = useState(false);
  const [pushPermission, setPushPermission] = useState<string>('default');
  const [pushSubscribed, setPushSubscribed] = useState(false);
  const [pushServerSubscribed, setPushServerSubscribed] = useState(false);
  const [pushLoading, setPushLoading] = useState(false);
  const savedRef = useRef<string>(cachedPrefs ? JSON.stringify(cachedPrefs) : '');

  const dirty = prefs ? JSON.stringify(prefs) !== savedRef.current : false;

  useEffect(() => {
    api.get<Prefs>('/settings/prefs').then((p) => {
      setPrefs(p);
      savedRef.current = JSON.stringify(p);
      setCache('settings_reminders', p);
    }).catch(() => {}).finally(() => setLoading(false));

    getPushStatus().then(({ supported, permission, subscribed, serverSubscribed }) => {
      setPushSupported(supported);
      setPushPermission(permission);
      setPushSubscribed(subscribed);
      setPushServerSubscribed(serverSubscribed);
    });
  }, []);

  const toggleReminders = async () => {
    if (!prefs) return;
    const enabling = !prefs.reminders_on;
    const updatedPrefs = { ...prefs, reminders_on: enabling ? 1 : 0 };
    setPrefs(updatedPrefs);

    if (enabling && pushSupported && !(pushSubscribed && pushServerSubscribed)) {
      setPushLoading(true);
      try {
        const ok = await subscribeToPush();
        if (ok) { setPushSubscribed(true); setPushServerSubscribed(true); setPushPermission('granted'); }
        else if (Notification.permission === 'denied') {
          setPushPermission('denied');
        } else {
          // Subscribe failed - user will see the warning banner
        }
      } catch { /* */ } finally { setPushLoading(false); }
    } else if (!enabling && pushSubscribed) {
      try { await unsubscribeFromPush(); setPushSubscribed(false); setPushServerSubscribed(false); } catch { /* */ }
    }

    try {
      await api.put('/settings/prefs', updatedPrefs);
      savedRef.current = JSON.stringify(updatedPrefs);
      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
    } catch {
      setPrefs(prefs);
      toast('Failed to save reminder setting', 'error');
    }
  };

  const savePrefs = useCallback(async () => {
    if (!prefs) return;
    try {
      await api.put('/settings/prefs', prefs);
      savedRef.current = JSON.stringify(prefs);
      setCache('settings_reminders', prefs);
      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
      hapticLight();
      toast('Reminders saved!');
    } catch {
      toast('Error saving reminders', 'error');
    }
  }, [prefs, toast]);

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  if (!prefs) return null;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">Reminders</h1>
      </div>

      <div className="glass-card p-5 space-y-4">
        {/* Timezone */}
        <div>
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Timezone</label>
          <select
            value={prefs.timezone}
            onChange={(e) => setPrefs({ ...prefs, timezone: e.target.value })}
            className="w-full glass-input mt-1"
          >
            {Object.entries(GROUPED_TIMEZONES).map(([region, zones]) => (
              <optgroup key={region} label={region}>
                {zones.map(({ tz, city }) => (
                  <option key={tz} value={tz}>{city}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        {/* Toggle */}
        <div className="flex items-center justify-between py-1">
          <div className="flex items-center gap-2">
            <Bell className="w-4 h-4" style={{ color: prefs.reminders_on ? '#10b981' : 'var(--text-muted)' }} />
            <span className="text-sm font-medium">Push reminders</span>
          </div>
          <button
            onClick={() => { hapticLight(); toggleReminders(); }}
            disabled={pushLoading}
            className="w-11 h-6 rounded-full transition-colors relative"
            style={{ background: prefs.reminders_on ? '#059669' : 'var(--track-bg)' }}
          >
            <div className={`w-4 h-4 rounded-full bg-white shadow-sm absolute top-1 transition-transform ${prefs.reminders_on ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>

        {!!prefs.reminders_on && pushPermission === 'denied' && (
          <p className="text-xs text-red-400">Push notifications blocked - check browser/device settings to allow notifications for MacroShot</p>
        )}

        {!!prefs.reminders_on && pushSupported && !pushLoading && !(pushSubscribed && pushServerSubscribed) && pushPermission !== 'denied' && (
          <div className="rounded-xl p-3" style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
            <p className="text-xs font-medium mb-2" style={{ color: '#f59e0b' }}>
              {pushSubscribed && !pushServerSubscribed
                ? "This device has an old push subscription from a different account. Re-register it to start receiving reminders."
                : "Notifications are enabled but your device isn't registered for push alerts yet."}
            </p>
            <Button
              size="sm"
              variant="primary"
              disabled={pushLoading}
              onClick={async () => {
                setPushLoading(true);
                try {
                  // This is the explicit diagnostic surface — surface the
                  // failure reason in the toast so a user (or we, debugging
                  // their report) can see which step broke without needing
                  // Safari remote-debugger on iOS PWA.
                  const result = await subscribeToPushDetailed();
                  if (result.ok) {
                    setPushSubscribed(true);
                    setPushServerSubscribed(true);
                    setPushPermission('granted');
                    toast('Push notifications activated!');
                  } else if (Notification.permission === 'denied') {
                    setPushPermission('denied');
                  } else {
                    toast(`Could not enable push (${result.reason})`, 'error');
                  }
                } catch { toast('Could not enable push', 'error'); }
                finally { setPushLoading(false); }
              }}
            >
              {pushLoading ? 'Enabling...' : (pushSubscribed && !pushServerSubscribed ? 'Re-register This Device' : 'Enable Push Notifications')}
            </Button>
          </div>
        )}

        {/* Meal hours */}
        {!!prefs.reminders_on && (
          <div className="space-y-3 pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
            {MEAL_HOURS.map((mh) => (
              <div key={mh.key} className="flex items-center justify-between">
                <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>{mh.label}</span>
                <select
                  value={prefs[mh.key]}
                  onChange={(e) => setPrefs({ ...prefs, [mh.key]: Number(e.target.value) })}
                  className="glass-input py-1.5 px-2 text-sm w-28"
                >
                  {Array.from({ length: 24 }, (_, i) => (
                    <option key={i} value={i}>{hourLabel(i)}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}

        {/* Save */}
        <Button
          variant={dirty ? 'primary' : 'secondary'}
          className="w-full"
          onClick={savePrefs}
          disabled={!dirty}
        >
          {dirty ? 'Save Changes' : <><Check className="w-3.5 h-3.5" /> Saved</>}
        </Button>
      </div>
    </div>
  );
}
