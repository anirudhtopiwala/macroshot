export interface QueuedMeal {
  id?: number;
  text: string;
  mealType: string;
  loggedAt: string;  // intended meal time (captured at queue time)
  queuedAt: string;
}

const DB_NAME = 'macro_offline';
const STORE = 'pending_meals';

let _dbPromise: Promise<IDBDatabase> | null = null;

function openDB(): Promise<IDBDatabase> {
  if (!_dbPromise) {
    const rawOpen = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        // Reset cache if browser closes the connection (mobile backgrounding)
        db.onclose = () => { _dbPromise = null; };
        db.onversionchange = () => { db.close(); _dbPromise = null; };
        resolve(db);
      };
      req.onerror = () => { _dbPromise = null; reject(req.error); };
      req.onblocked = () => { _dbPromise = null; reject(new Error('IndexedDB open blocked')); };
    });
    // If another tab holds the DB or the store is corrupted, indexedDB.open
    // can pend indefinitely. Cap it so callers (offline queue sync on
    // reconnect, queue count on mount) never hang.
    _dbPromise = Promise.race([
      rawOpen,
      new Promise<IDBDatabase>((_, reject) =>
        setTimeout(() => {
          _dbPromise = null;
          reject(new Error('IndexedDB open timeout'));
        }, 5000),
      ),
    ]);
  }
  return _dbPromise;
}

export async function queueMeal(text: string, mealType: string): Promise<void> {
  const db = await openDB();
  const d = new Date();
  const now = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add({ text, mealType, loggedAt: now, queuedAt: now });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function getPendingMeals(): Promise<QueuedMeal[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function removeMeal(id: number): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export function clearOfflineQueue(): void {
  // Close any open connection first so deleteDatabase can succeed
  if (_dbPromise) {
    _dbPromise.then((db) => { try { db.close(); } catch { /* ignore */ } }).catch(() => {});
    _dbPromise = null;
  }
  try {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onerror = () => {}; // Suppress errors (e.g., blocked by open connection)
    req.onblocked = () => {}; // Another tab may have the DB open
  } catch { /* indexedDB not available */ }
}

export async function getQueueCount(): Promise<number> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).count();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
