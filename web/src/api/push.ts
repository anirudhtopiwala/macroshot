import { api } from './client';

// Get an active SW registration for /macro_app/. swManager normally registers
// on `window.load`, but on a fresh PWA install (or any code path that reaches
// here before that listener fires) there's nothing to find. Register on demand
// so a user-gesture-initiated subscribe never silently fails for missing SW.
// Returns { reg, debug } where debug describes which state we ended in - so
// the caller can surface the failure mode in the user-visible toast.
async function getReadyRegistration(): Promise<{ reg: ServiceWorkerRegistration | null; debug: string }> {
  if (!('serviceWorker' in navigator)) return { reg: null, debug: 'no-sw-api' };
  let reg = await navigator.serviceWorker.getRegistration('/macro_app/');
  let source = 'existing';
  if (!reg) {
    try {
      // Intentionally re-registers even after a nuclearReset() set the
      // `sw-nuked` flag. swManager skips its load-time register to avoid a
      // controllerchange→reload loop, but this path runs inside a user-
      // gesture subscribe and does not reload, so it's safe to bring the SW
      // back here.
      reg = await navigator.serviceWorker.register('/macro_app/sw.js', { updateViaCache: 'none' });
      source = 'fresh-register';
    } catch (err) {
      const e = err as { name?: string; message?: string };
      console.warn('[push] register sw.js failed:', e?.name, e?.message);
      return { reg: null, debug: `register-throw:${e?.name || 'Error'}` };
    }
  }
  if (reg.active) return { reg, debug: `active-${source}` };

  // Wait for an installing worker to finish (or 20s, whichever first).
  if (reg.installing) {
    const w = reg.installing;
    const finalState = await new Promise<string>((resolve) => {
      const timer = setTimeout(() => resolve(`timeout-state:${w.state}`), 20000);
      w.addEventListener('statechange', () => {
        if (w.state === 'activated' || w.state === 'redundant') {
          clearTimeout(timer);
          resolve(w.state);
        }
      });
    });
    if (reg.active) return { reg, debug: `installing->${finalState}-${source}` };
    return { reg: null, debug: `installing->${finalState}-${source}` };
  }

  if (reg.waiting) return { reg: null, debug: `stuck-waiting-${source}` };

  // No active, no installing, no waiting - try `ready` once as a last resort.
  const ready = await Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 5000)),
  ]);
  if (ready?.active) return { reg: ready, debug: `late-ready-${source}` };
  return { reg: null, debug: `no-worker-${source}` };
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

async function getVapidKey(): Promise<string | null> {
  try {
    const res = await fetch('/macro_app/api/config');
    if (!res.ok) return null;
    const config = await res.json();
    return config.vapid_public_key || null;
  } catch {
    return null;
  }
}

export type SubscribeResult =
  | { ok: true }
  | { ok: false; reason: string };

// Backwards-compat wrapper for callers that still expect a boolean. New
// callers should use `subscribeToPushDetailed` to surface the failure reason.
export async function subscribeToPush(): Promise<boolean> {
  return (await subscribeToPushDetailed()).ok;
}

export async function subscribeToPushDetailed(): Promise<SubscribeResult> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return { ok: false, reason: 'browser-unsupported' };
  }

  // iOS Safari PWA: Notification.requestPermission() must run inside the
  // user-gesture frame. Any `await` before it consumes the gesture and the
  // prompt silently never appears. Call it first, before VAPID/SW awaits.
  if (Notification.permission === 'default') {
    const result = await Notification.requestPermission();
    if (result !== 'granted') {
      return { ok: false, reason: `permission:${result}` };
    }
  } else if (Notification.permission !== 'granted') {
    return { ok: false, reason: 'permission:denied' };
  }

  const vapidKey = await getVapidKey();
  if (!vapidKey) {
    return { ok: false, reason: 'vapid-fetch-failed' };
  }

  const { reg: registration, debug: regDebug } = await getReadyRegistration();
  if (!registration) {
    return { ok: false, reason: `sw-not-ready:${regDebug}` };
  }
  let subscription = await registration.pushManager.getSubscription();

  if (!subscription) {
    try {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey) as BufferSource,
      });
    } catch (err) {
      const e = err as { name?: string; message?: string };
      console.warn('[push] subscribe: pushManager.subscribe failed:', e?.name, e?.message);
      return { ok: false, reason: `subscribe-throw:${e?.name || 'Error'}:${e?.message?.slice(0, 80) || ''}` };
    }
  }

  const subJson = subscription.toJSON();
  try {
    await api.post('/settings/push/subscribe', {
      endpoint: subJson.endpoint,
      keys: subJson.keys,
    });
  } catch (err) {
    const e = err as { name?: string; message?: string };
    console.warn('[push] subscribe: server POST failed:', e?.name, e?.message);
    return { ok: false, reason: `server-post:${e?.name || 'Error'}` };
  }

  return { ok: true };
}

export async function unsubscribeFromPush(): Promise<boolean> {
  const { reg: registration } = await getReadyRegistration();
  if (!registration) return false;

  const subscription = await registration.pushManager.getSubscription();

  if (subscription) {
    const endpoint = subscription.endpoint;
    await subscription.unsubscribe();
    await api.post('/settings/push/unsubscribe', { endpoint });
  }

  return true;
}

export async function getPushStatus(): Promise<{
  supported: boolean;
  permission: NotificationPermission | 'unsupported';
  subscribed: boolean;           // browser PushManager has a subscription
  serverSubscribed: boolean;     // server has a row for (current user, this endpoint)
}> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return { supported: false, permission: 'unsupported', subscribed: false, serverSubscribed: false };
  }

  const permission = Notification.permission;
  let subscribed = false;
  try {
    const { reg: registration } = await getReadyRegistration();
    if (registration) {
      const sub = await registration.pushManager.getSubscription();
      subscribed = sub !== null;
    }
  } catch {
    // ignore
  }

  let serverSubscribed = false;
  try {
    const res = await api.get<{ subscribed: boolean }>('/settings/push/status');
    serverSubscribed = !!res.subscribed;
  } catch {
    // Not logged in or server unreachable - treat as not subscribed server-side.
  }

  return { supported: true, permission, subscribed, serverSubscribed };
}
