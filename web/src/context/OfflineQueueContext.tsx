import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import * as Sentry from '@sentry/react';
import { getPendingMeals, removeMeal, queueMeal as dbQueue, type QueuedMeal } from '../utils/offlineQueue';
import { mealsApi } from '../api/meals';
import { clearCache } from '../utils/apiCache';

interface OfflineQueueContextValue {
  queued: QueuedMeal[];
  queueCount: number;
  addToQueue: (text: string, mealType: string) => Promise<void>;
  processQueue: () => Promise<number | undefined>;
  processing: boolean;
}

const OfflineQueueContext = createContext<OfflineQueueContextValue | null>(null);

export function OfflineQueueProvider({ children }: { children: ReactNode }) {
  const [queued, setQueued] = useState<QueuedMeal[]>([]);
  const [processing, setProcessing] = useState(false);
  const processingRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const items = await getPendingMeals();
      setQueued(items);
    } catch (err) {
      // IndexedDB not available
      if (Sentry.isInitialized()) Sentry.captureException(err, { tags: { context: 'offline-queue-refresh' } });
    }
  }, []);

  const addToQueue = useCallback(async (text: string, mealType: string) => {
    await dbQueue(text, mealType);
    await refresh();
  }, [refresh]);

  const processQueue = useCallback(async () => {
    // Use ref to avoid stale closure race - prevent concurrent runs
    if (processingRef.current) return;
    const items = await getPendingMeals();
    if (items.length === 0) return;

    processingRef.current = true;
    setProcessing(true);
    let loggedCount = 0;
    for (const item of items) {
      try {
        const formData = new FormData();
        formData.append('text', item.text);
        formData.append('meal_type', item.mealType);
        const result = await mealsApi.analyze(formData);
        if (result.session_id && result.nutrition && !result.error) {
          try {
            await mealsApi.accept(result.session_id, undefined, item.loggedAt);
            loggedCount++;
            await removeMeal(item.id!);
          } catch {
            // Accept failed - cancel the orphaned session so it doesn't linger
            // Keep meal in queue for retry on next sync
            try { await mealsApi.cancel(result.session_id); } catch { /* best effort */ }
          }
        } else if (result.session_id) {
          await mealsApi.cancel(result.session_id);
          // Analysis returned no nutrition - remove from queue (won't improve on retry)
          await removeMeal(item.id!);
        } else {
          // No session created (analysis failure) - remove from queue
          await removeMeal(item.id!);
        }
      } catch (err) {
        // Network error - keep in queue for retry on next online event.
        // Only log to Sentry for visibility; do NOT remove from IndexedDB.
        // PII safety: never include the meal text/photo in the Sentry event.
        // Send only metadata so we can debug failure rates without leaking
        // user health data.
        if (Sentry.isInitialized()) Sentry.captureException(err, {
          tags: { context: 'offline-queue-sync' },
          extra: { hasText: !!item.text, textLen: item.text?.length ?? 0 },
        });
        continue;
      }
    }
    processingRef.current = false;
    setProcessing(false);
    if (loggedCount > 0) {
      clearCache('dash_');
      clearCache('journal_');
    }
    await refresh();
    return loggedCount;
  }, [refresh]);

  // Load queue count on mount (cheap IndexedDB read, no API calls)
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Only process queue on explicit online event - NOT on mount
  useEffect(() => {
    const handleOnline = () => { processQueue(); };
    window.addEventListener('online', handleOnline);
    return () => window.removeEventListener('online', handleOnline);
  }, [processQueue]);

  return (
    <OfflineQueueContext.Provider value={{ queued, queueCount: queued.length, addToQueue, processQueue, processing }}>
      {children}
    </OfflineQueueContext.Provider>
  );
}

export function useOfflineQueue(): OfflineQueueContextValue {
  const ctx = useContext(OfflineQueueContext);
  if (!ctx) {
    throw new Error('useOfflineQueue must be used within an OfflineQueueProvider');
  }
  return ctx;
}
