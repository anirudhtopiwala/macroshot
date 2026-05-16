import { useState, useEffect } from 'react';

const base = 'rounded-xl bg-[length:200%_100%] animate-shimmer dark:bg-gradient-to-r dark:from-white/[0.04] dark:via-white/[0.08] dark:to-white/[0.04] bg-gradient-to-r from-black/[0.03] via-black/[0.06] to-black/[0.03]';

/** Shows skeleton briefly, then an offline message if still loading after delay */
export function OfflineAwareSkeleton({ skeleton, delay = 3000 }: { skeleton: React.ReactNode; delay?: number }) {
  const [showOffline, setShowOffline] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (!navigator.onLine) setShowOffline(true);
    }, delay);
    const handleOnline = () => setShowOffline(false);
    window.addEventListener('online', handleOnline);
    return () => { clearTimeout(timer); window.removeEventListener('online', handleOnline); };
  }, [delay]);

  if (showOffline) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center px-6">
        <div className="text-4xl mb-3">📡</div>
        <p className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>No cached data available</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Connect to the internet to load your data. It'll be cached for offline use after that.</p>
      </div>
    );
  }

  return <>{skeleton}</>;
}

export function SkeletonBox({ className = '' }: { className?: string }) {
  return <div className={`${base} ${className}`} />;
}

export function DashboardSkeleton() {
  return (
    <div className="space-y-4">
      {/* Week strip */}
      <div className="glass-card p-2.5">
        <div className="flex justify-between px-0.5">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex flex-col items-center gap-1 py-2 px-1.5">
              <SkeletonBox className="w-6 h-3 !rounded-md" />
              <SkeletonBox className="w-10 h-10 !rounded-full" />
            </div>
          ))}
        </div>
      </div>

      {/* Hero card */}
      <SkeletonBox className="h-52 !rounded-2xl" />

      {/* Macro cards */}
      <div className="grid grid-cols-3 gap-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <SkeletonBox key={i} className="h-28 !rounded-2xl" />
        ))}
      </div>

      {/* Meal type slots */}
      <div className="grid grid-cols-4 gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonBox key={i} className="h-20 !rounded-2xl" />
        ))}
      </div>

      {/* Meal cards */}
      <div>
        <SkeletonBox className="w-24 h-3 mb-3 !rounded-md" />
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonBox key={i} className="h-16 !rounded-2xl" />
          ))}
        </div>
      </div>
    </div>
  );
}

export function JournalSkeleton() {
  return (
    <div className="space-y-4">
      <SkeletonBox className="w-20 h-3 !rounded-md" />
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonBox key={i} className="h-16 !rounded-2xl" />
        ))}
      </div>
    </div>
  );
}
