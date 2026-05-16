import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { Check } from '../components/icons';
import { useToast } from '../components/Toast';

interface StravaStatus {
  connected: boolean;
  available: boolean;
  athlete_id: number | null;
  last_synced: string | null;
  health: string | null;
}

interface FitbitStatus {
  connected: boolean;
  available: boolean;
  fitbit_user_id: string | null;
  last_synced: string | null;
  health: string | null;
}

interface OuraStatus {
  connected: boolean;
  available: boolean;
  oura_user_id: string | null;
  last_synced: string | null;
  health: string | null;
}

function formatLastSynced(ts: string | null | undefined): string {
  if (!ts) return '';
  let iso = ts.replace(' ', 'T');
  if (!/\d{2}:\d{2}/.test(iso)) {
    iso = `${iso}T00:00:00Z`;
  } else if (!iso.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(iso)) {
    iso = `${iso}Z`;
  }
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

interface Feature { label: string; detail: string; }

const STRAVA_FEATURES: Feature[] = [
  { label: 'Runs, rides & workouts', detail: 'Auto-synced to your dashboard' },
  { label: 'Calories burned', detail: 'Shown alongside your meal data' },
  { label: 'Distance & duration', detail: 'Per-activity breakdown' },
  { label: 'Heart rate', detail: 'Average HR per workout' },
];

const FITBIT_FEATURES: Feature[] = [
  { label: 'Daily steps', detail: 'Step count on your dashboard' },
  { label: 'Active calories', detail: 'Activity-based burn estimate' },
  { label: 'Active minutes', detail: 'Time spent moving' },
  { label: 'Resting heart rate', detail: 'Daily resting HR tracking' },
];

const OURA_FEATURES: Feature[] = [
  { label: 'Daily steps', detail: 'Step count on your dashboard' },
  { label: 'Active calories', detail: 'Activity-based burn estimate' },
  { label: 'Active minutes', detail: 'Medium + high intensity time' },
  { label: 'Workouts & heart rate', detail: 'Ring-detected sessions + resting HR' },
];

const StravaIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: '#fc4c02' }}>
    <polyline points="22 12 18 20 15 14" />
    <polyline points="15 14 12 20 2 4 9 4 12 10 15 4 18 20" />
  </svg>
);

const FitbitIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" style={{ color: '#00B0B9' }}>
    <circle cx="12" cy="4" r="2.2" fill="currentColor" />
    <circle cx="12" cy="9.5" r="2.2" fill="currentColor" />
    <circle cx="12" cy="15" r="2.2" fill="currentColor" />
    <circle cx="12" cy="20.5" r="1.5" fill="currentColor" />
    <circle cx="6.5" cy="9.5" r="1.5" fill="currentColor" />
    <circle cx="17.5" cy="9.5" r="1.5" fill="currentColor" />
    <circle cx="6.5" cy="15" r="1.5" fill="currentColor" />
    <circle cx="17.5" cy="15" r="1.5" fill="currentColor" />
  </svg>
);

const OuraIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ color: 'var(--text-primary)' }}>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="4" fill="currentColor" opacity="0.25" />
  </svg>
);

interface IntegrationRowProps {
  name: string;
  icon: ReactNode;
  iconBg: string;
  iconBorder: string;
  connected: boolean;
  health: string | null;
  lastSynced: string | null;
  features: Feature[];
  connectLoading: boolean;
  syncing: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  disconnectLoading: boolean;
}

function IntegrationRow({
  name, icon, iconBg, iconBorder, connected, health, lastSynced, features,
  connectLoading, syncing, onConnect, onDisconnect, onSync, disconnectLoading,
}: IntegrationRowProps) {
  const [expanded, setExpanded] = useState(false);
  const tokenExpired = health === 'token_expired';

  const statusText = connected
    ? (tokenExpired ? 'Reconnect needed' : (lastSynced ? `Synced ${formatLastSynced(lastSynced)}` : 'Syncing…'))
    : 'Not connected';

  return (
    <div className="glass-card overflow-hidden">
      {/* Collapsed row - always visible */}
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-3 p-4 text-left"
        style={{ background: 'transparent' }}
      >
        <div
          className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
          style={{ background: iconBg, border: `1px solid ${iconBorder}` }}
        >
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">{name}</p>
          <p className="text-xs truncate" style={{ color: connected && !tokenExpired ? '#10b981' : 'var(--text-muted)' }}>
            {connected && !tokenExpired && (
              <span
                className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle"
                style={{ background: '#10b981' }}
              />
            )}
            {statusText}
          </p>
        </div>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            color: 'var(--text-muted)',
            transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
            transition: 'transform 0.2s',
          }}
          aria-hidden="true"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Expanded body - features + actions */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3" style={{ borderTop: '1px solid var(--border-glass)' }}>
          <div className="space-y-2 pt-3">
            {features.map((f) => (
              <div key={f.label} className="flex items-start gap-2">
                <Check className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: connected ? '#10b981' : 'var(--text-muted)' }} />
                <div>
                  <p className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{f.label}</p>
                  <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{f.detail}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="space-y-2">
            {connected ? (
              <>
                <Button size="sm" variant="secondary" className="w-full" onClick={onSync} disabled={syncing}>
                  {syncing ? 'Syncing…' : 'Sync Now'}
                </Button>
                <Button size="sm" variant="destructive" className="w-full" onClick={onDisconnect} disabled={disconnectLoading}>
                  {disconnectLoading ? 'Disconnecting…' : `Disconnect ${name}`}
                </Button>
              </>
            ) : (
              <Button size="sm" variant="accent" className="w-full" onClick={onConnect} disabled={connectLoading}>
                {connectLoading ? 'Connecting…' : `Connect ${name}`}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ConnectedApps() {
  const { toast } = useToast();
  const [stravaStatus, setStravaStatus] = useState<StravaStatus | null>(null);
  const [fitbitStatus, setFitbitStatus] = useState<FitbitStatus | null>(null);
  const [ouraStatus, setOuraStatus] = useState<OuraStatus | null>(null);
  const [stravaLoading, setStravaLoading] = useState(false);
  const [fitbitLoading, setFitbitLoading] = useState(false);
  const [ouraLoading, setOuraLoading] = useState(false);
  const [stravaSyncing, setStravaSyncing] = useState(false);
  const [fitbitSyncing, setFitbitSyncing] = useState(false);
  const [ouraSyncing, setOuraSyncing] = useState(false);
  const [stravaDisconnecting, setStravaDisconnecting] = useState(false);
  const [fitbitDisconnecting, setFitbitDisconnecting] = useState(false);
  const [ouraDisconnecting, setOuraDisconnecting] = useState(false);
  const [loading, setLoading] = useState(true);

  const connectStrava = async () => {
    setStravaLoading(true);
    try {
      const data = await api.get<{ auth_url: string }>('/strava/connect');
      if (data.auth_url) window.location.href = data.auth_url;
    } catch { toast('Could not connect to Strava', 'error'); }
    finally { setStravaLoading(false); }
  };

  const connectFitbit = async () => {
    setFitbitLoading(true);
    try {
      const data = await api.get<{ auth_url: string }>('/fitbit/connect');
      if (data.auth_url) window.location.href = data.auth_url;
    } catch { toast('Could not connect to Fitbit', 'error'); }
    finally { setFitbitLoading(false); }
  };

  const connectOura = async () => {
    setOuraLoading(true);
    try {
      const data = await api.get<{ auth_url: string }>('/oura/connect');
      if (data.auth_url) window.location.href = data.auth_url;
    } catch { toast('Could not connect to Oura', 'error'); }
    finally { setOuraLoading(false); }
  };

  const disconnectStrava = async () => {
    setStravaDisconnecting(true);
    try {
      await api.delete('/strava/disconnect');
      setStravaStatus(prev => prev ? { ...prev, connected: false, athlete_id: null } : prev);
      toast('Strava disconnected');
    } catch { toast('Failed to disconnect', 'error'); }
    finally { setStravaDisconnecting(false); }
  };

  const disconnectFitbit = async () => {
    setFitbitDisconnecting(true);
    try {
      await api.delete('/fitbit/disconnect');
      setFitbitStatus(prev => prev ? { ...prev, connected: false, fitbit_user_id: null } : prev);
      toast('Fitbit disconnected');
    } catch { toast('Failed to disconnect', 'error'); }
    finally { setFitbitDisconnecting(false); }
  };

  const disconnectOura = async () => {
    setOuraDisconnecting(true);
    try {
      await api.delete('/oura/disconnect');
      setOuraStatus(prev => prev ? { ...prev, connected: false, oura_user_id: null } : prev);
      toast('Oura disconnected');
    } catch { toast('Failed to disconnect', 'error'); }
    finally { setOuraDisconnecting(false); }
  };

  const syncStrava = async () => {
    setStravaSyncing(true);
    try {
      const data = await api.post<{ synced: number }>('/strava/sync');
      toast(data.synced > 0 ? `Synced ${data.synced} activities from Strava` : 'Strava is up to date');
      setStravaStatus(prev => prev ? { ...prev, last_synced: new Date().toISOString() } : prev);
      window.dispatchEvent(new CustomEvent('workouts-synced'));
    } catch {
      toast('Failed to sync Strava activities', 'error');
    } finally {
      setStravaSyncing(false);
    }
  };

  const syncFitbit = async () => {
    setFitbitSyncing(true);
    try {
      const data = await api.post<{ synced: number }>('/fitbit/sync');
      toast(data.synced > 0 ? `Synced ${data.synced} days of Fitbit data` : 'Fitbit is up to date');
      setFitbitStatus(prev => prev ? { ...prev, last_synced: new Date().toISOString() } : prev);
      window.dispatchEvent(new CustomEvent('workouts-synced'));
    } catch {
      toast('Failed to sync Fitbit data', 'error');
    } finally {
      setFitbitSyncing(false);
    }
  };

  const syncOura = async () => {
    setOuraSyncing(true);
    try {
      const data = await api.post<{ synced: number; workouts: number }>('/oura/sync');
      const parts: string[] = [];
      if (data.synced > 0) parts.push(`${data.synced} days`);
      if (data.workouts > 0) parts.push(`${data.workouts} workouts`);
      toast(parts.length ? `Synced ${parts.join(' + ')} from Oura` : 'Oura is up to date');
      setOuraStatus(prev => prev ? { ...prev, last_synced: new Date().toISOString() } : prev);
      window.dispatchEvent(new CustomEvent('workouts-synced'));
    } catch {
      toast('Failed to sync Oura data', 'error');
    } finally {
      setOuraSyncing(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const justConnectedStrava = params.get('strava') === 'connected';
    const justConnectedFitbit = params.get('fitbit') === 'connected';
    const justConnectedOura = params.get('oura') === 'connected';

    if (justConnectedStrava) {
      setStravaStatus({ connected: true, available: true, athlete_id: null, last_synced: null, health: 'ok' });
      setLoading(false);
      toast('Strava connected! Syncing your recent activities…');
      window.history.replaceState({}, '', window.location.pathname);
    }
    if (justConnectedFitbit) {
      setFitbitStatus({ connected: true, available: true, fitbit_user_id: null, last_synced: null, health: 'ok' });
      setLoading(false);
      toast('Fitbit connected! Syncing your recent activity…');
      window.history.replaceState({}, '', window.location.pathname);
    }
    if (justConnectedOura) {
      setOuraStatus({ connected: true, available: true, oura_user_id: null, last_synced: null, health: 'ok' });
      setLoading(false);
      toast('Oura connected! Syncing your recent activity…');
      window.history.replaceState({}, '', window.location.pathname);
    }

    Promise.all([
      api.get<StravaStatus>('/strava/status').catch(() => ({ connected: false, available: false, athlete_id: null, last_synced: null, health: null })),
      api.get<FitbitStatus>('/fitbit/status').catch(() => ({ connected: false, available: false, fitbit_user_id: null, last_synced: null, health: null })),
      api.get<OuraStatus>('/oura/status').catch(() => ({ connected: false, available: false, oura_user_id: null, last_synced: null, health: null })),
    ]).then(([s, f, o]) => {
      if (justConnectedStrava && !s.connected) setStravaStatus(prev => prev ?? s);
      else setStravaStatus(s);
      if (justConnectedFitbit && !f.connected) setFitbitStatus(prev => prev ?? f);
      else setFitbitStatus(f);
      if (justConnectedOura && !o.connected) setOuraStatus(prev => prev ?? o);
      else setOuraStatus(o);
    }).finally(() => setLoading(false));

    if (justConnectedStrava) {
      setStravaSyncing(true);
      api.post<{ synced: number }>('/strava/sync')
        .then((data) => toast(`Synced ${data.synced} activities from Strava`))
        .catch(() => toast('Failed to sync activities', 'error'))
        .finally(() => setStravaSyncing(false));
    }
    if (justConnectedFitbit) {
      setFitbitSyncing(true);
      api.post<{ synced: number }>('/fitbit/sync')
        .then((data) => toast(`Synced ${data.synced} days of Fitbit data`))
        .catch(() => toast('Failed to sync Fitbit data', 'error'))
        .finally(() => setFitbitSyncing(false));
    }
    if (justConnectedOura) {
      setOuraSyncing(true);
      api.post<{ synced: number; workouts: number }>('/oura/sync')
        .then((data) => {
          const parts: string[] = [];
          if (data.synced > 0) parts.push(`${data.synced} days`);
          if (data.workouts > 0) parts.push(`${data.workouts} workouts`);
          toast(parts.length ? `Synced ${parts.join(' + ')} from Oura` : 'Oura is up to date');
        })
        .catch(() => toast('Failed to sync Oura data', 'error'))
        .finally(() => setOuraSyncing(false));
    }
  }, []);

  if (loading) return <LoadingSpinner fullPage />;

  const hasAny = stravaStatus?.available || fitbitStatus?.available || ouraStatus?.available;

  return (
    <div className="space-y-3">
      <BackButton fallbackPath="/settings" />
      <h1 className="text-xl font-bold">Connected Apps</h1>
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
        Link your fitness apps to sync workouts, steps, and activity data. Tap any row to see what gets synced.
      </p>

      {!hasAny && (
        <div className="glass-card p-6 text-center space-y-2">
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            No integrations configured on this server.
          </p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Ask the admin to set up Strava, Fitbit, or Oura credentials.
          </p>
        </div>
      )}

      {(() => {
        const rows: { key: string; connected: boolean; node: ReactNode }[] = [];
        if (stravaStatus?.available) {
          rows.push({
            key: 'strava',
            connected: stravaStatus.connected,
            node: (
              <IntegrationRow
                name="Strava"
                icon={StravaIcon}
                iconBg="rgba(252,76,2,0.1)"
                iconBorder="rgba(252,76,2,0.2)"
                connected={stravaStatus.connected}
                health={stravaStatus.health}
                lastSynced={stravaStatus.last_synced}
                features={STRAVA_FEATURES}
                connectLoading={stravaLoading}
                syncing={stravaSyncing}
                disconnectLoading={stravaDisconnecting}
                onConnect={connectStrava}
                onDisconnect={disconnectStrava}
                onSync={syncStrava}
              />
            ),
          });
        }
        if (fitbitStatus?.available) {
          rows.push({
            key: 'fitbit',
            connected: fitbitStatus.connected,
            node: (
              <IntegrationRow
                name="Fitbit"
                icon={FitbitIcon}
                iconBg="rgba(0,176,185,0.1)"
                iconBorder="rgba(0,176,185,0.2)"
                connected={fitbitStatus.connected}
                health={fitbitStatus.health}
                lastSynced={fitbitStatus.last_synced}
                features={FITBIT_FEATURES}
                connectLoading={fitbitLoading}
                syncing={fitbitSyncing}
                disconnectLoading={fitbitDisconnecting}
                onConnect={connectFitbit}
                onDisconnect={disconnectFitbit}
                onSync={syncFitbit}
              />
            ),
          });
        }
        if (ouraStatus?.available) {
          rows.push({
            key: 'oura',
            connected: ouraStatus.connected,
            node: (
              <IntegrationRow
                name="Oura Ring"
                icon={OuraIcon}
                iconBg="rgba(126,132,143,0.15)"
                iconBorder="rgba(126,132,143,0.3)"
                connected={ouraStatus.connected}
                health={ouraStatus.health}
                lastSynced={ouraStatus.last_synced}
                features={OURA_FEATURES}
                connectLoading={ouraLoading}
                syncing={ouraSyncing}
                disconnectLoading={ouraDisconnecting}
                onConnect={connectOura}
                onDisconnect={disconnectOura}
                onSync={syncOura}
              />
            ),
          });
        }
        // Connected integrations on top; preserve registration order within each group.
        rows.sort((a, b) => Number(b.connected) - Number(a.connected));
        return rows.map(r => <div key={r.key}>{r.node}</div>);
      })()}
    </div>
  );
}
