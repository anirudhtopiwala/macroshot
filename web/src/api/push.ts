import { api } from './client';

// Prefer getRegistration() (returns the registration immediately if one exists)
// over `ready` (which can hang on hard-reloaded / uncontrolled pages even when
// an active SW is registered for the scope). Fall back to `ready` only when no
// registration is found yet — covers the just-registered race window.
async function getReadyRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null;
  const existing = await navigator.serviceWorker.getRegistration();
  if (existing) return existing;
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000)),
  ]);
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

export async function subscribeToPush(): Promise<boolean> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    console.warn('[push] subscribe: browser lacks serviceWorker or PushManager');
    return false;
  }

  // iOS Safari PWA: Notification.requestPermission() must run inside the
  // user-gesture frame. Any `await` before it consumes the gesture and the
  // prompt silently never appears. Call it first, before VAPID/SW awaits.
  if (Notification.permission === 'default') {
    const result = await Notification.requestPermission();
    if (result !== 'granted') {
      console.warn('[push] subscribe: permission not granted:', result);
      return false;
    }
  } else if (Notification.permission !== 'granted') {
    console.warn('[push] subscribe: permission already denied');
    return false;
  }

  const vapidKey = await getVapidKey();
  if (!vapidKey) {
    console.warn('[push] subscribe: VAPID key fetch failed');
    return false;
  }

  const registration = await getReadyRegistration();
  if (!registration) {
    console.warn('[push] subscribe: no service worker registration ready');
    return false;
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
      return false;
    }
  }

  const subJson = subscription.toJSON();
  await api.post('/settings/push/subscribe', {
    endpoint: subJson.endpoint,
    keys: subJson.keys,
  });

  return true;
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
