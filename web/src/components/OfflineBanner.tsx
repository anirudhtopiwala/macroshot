import { useState, useEffect } from 'react';
import { useOfflineQueue } from '../context/OfflineQueueContext';

export default function OfflineBanner() {
  const [offline, setOffline] = useState(!navigator.onLine);
  const { queueCount, processing } = useOfflineQueue();

  useEffect(() => {
    const goOffline = () => setOffline(true);
    const goOnline = () => setOffline(false);
    window.addEventListener('offline', goOffline);
    window.addEventListener('online', goOnline);
    return () => {
      window.removeEventListener('offline', goOffline);
      window.removeEventListener('online', goOnline);
    };
  }, []);

  if (!offline && !processing) return null;

  return (
    <div className="fixed left-0 right-0 z-[55] flex justify-center pointer-events-none" style={{ top: 'calc(3.5rem + env(safe-area-inset-top, 0px) + 0.25rem)' }}>
      <div
        className="max-w-lg w-full mx-4 px-4 py-2 rounded-xl text-center text-xs font-semibold backdrop-blur-xl border pointer-events-auto"
        style={{
          background: 'rgba(245, 158, 11, 0.15)',
          borderColor: 'rgba(245, 158, 11, 0.3)',
          color: '#f59e0b',
        }}
      >
        {offline
          ? `You're offline - viewing cached data${queueCount > 0 ? ` · ${queueCount} meal${queueCount > 1 ? 's' : ''} queued` : ''}`
          : `Syncing ${queueCount} queued meal${queueCount > 1 ? 's' : ''}...`
        }
      </div>
    </div>
  );
}
