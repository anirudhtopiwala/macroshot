import { api } from './client';

// Get an active SW registration for /macro_app/. swManager normally registers
// on `window.load`, but on a fresh PWA install (or any code path that reaches
// here before that listener fires) there's nothing to find. Register on demand
// so a user-gesture-initiated subscribe never silently fails for missing SW.
async function getReadyRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null;
  let reg = await navigator.serviceWorker.getRegistration('/macro_app/');
  if (!reg) {
    try {
      reg = await navigator.serviceWorker.register('/macro_app/sw.js', { updateViaCache: 'none' });
    } catch (err) {
      const e = err as { name?: string; message?: string };
      console.warn('[push] register sw.js failed:', e?.name, e?.message);
      return null;
    }
  }
  if (reg.active) return reg;
  // Wait up to 10s for install→activate. Fresh PWA installs can be slow.
  const ready = await Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 10000)),
  ]);
  return ready ?? null;
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

  const registration = await getReadyRegistration();
  if (!registration) {
    return { ok: false, reason: 'sw-not-ready' };
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
  const registration = await getReadyRegistration();
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
    const registration = await getReadyRegistration();
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
