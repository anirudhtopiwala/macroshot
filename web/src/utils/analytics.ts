/** Track custom events in Umami with queuing until the script loads */

const eventQueue: Array<[string, Record<string, string | number> | undefined]> = [];
let umamiReady = false;

// Check if Umami is loaded, flush queue when ready
function flushQueue() {
  if (!(window as any).umami?.track) return;
  umamiReady = true;
  while (eventQueue.length > 0) {
    const [name, data] = eventQueue.shift()!;
    try { (window as any).umami.track(name, data); } catch {}
  }
}

const MAX_QUEUE = 50;
let queueingEnabled = true;

// Poll for Umami readiness (script loads async)
if (typeof window !== 'undefined') {
  const check = setInterval(() => {
    if ((window as any).umami?.track) {
      flushQueue();
      clearInterval(check);
    }
  }, 500);
  // Stop polling after 30s - discard queue if Umami never loaded (ad-blocker, etc.)
  setTimeout(() => {
    clearInterval(check);
    if (!umamiReady) {
      eventQueue.length = 0;
      queueingEnabled = false;
    }
  }, 30000);
}

export function trackEvent(name: string, data?: Record<string, string | number>) {
  if (typeof window === 'undefined') return;
  if (umamiReady) {
    try { (window as any).umami.track(name, data); } catch {}
  } else if (queueingEnabled && eventQueue.length < MAX_QUEUE) {
    eventQueue.push([name, data]);
  }
}
