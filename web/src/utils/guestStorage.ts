/**
 * IndexedDB store for guest-mode meals.
 *
 * The guest path lets a visitor analyze meals without signing up. The
 * server returns the nutrition result but does NOT persist anything;
 * we keep the accepted meals here so the dashboard can show today's
 * totals and so we can replay them into the user's real account on
 * signup via POST /meals/import-guest.
 *
 * Mirrors the offlineQueue.ts shape on purpose - same open/race
 * semantics, same 5s timeout cap. Different DB name so the two stores
 * are independent and the guest store survives a signed-in user's
 * logout (which deletes the offline queue DB).
 */

import type { Nutrition } from '../types';

export interface GuestMeal {
  id?: number;
  /** Pre-formatted "YYYY-MM-DD HH:MM" - matches the server-side AcceptRequest pattern. */
  loggedAt: string;
  mealType: string;
  userInput: string;
  /** The full NutritionOut returned by /guest/analyze. Replayed verbatim on import. */
  nutrition: Nutrition;
}

const DB_NAME = 'macro_guest';
const STORE = 'guest_meals';

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
        db.onclose = () => { _dbPromise = null; };
        db.onversionchange = () => { db.close(); _dbPromise = null; };
        resolve(db);
      };
      req.onerror = () => { _dbPromise = null; reject(req.error); };
      req.onblocked = () => { _dbPromise = null; reject(new Error('IndexedDB open blocked')); };
    });
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

export async function saveGuestMeal(meal: Omit<GuestMeal, 'id'>): Promise<void> {
  try {
    const db = await openDB();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).add(meal);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // Private-mode / quota - silently drop. Dashboard will just be empty.
  }
}

export async function listGuestMeals(): Promise<GuestMeal[]> {
  try {
    const db = await openDB();
    return await new Promise<GuestMeal[]>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => resolve(req.result as GuestMeal[]);
      req.onerror = () => reject(req.error);
    });
  } catch {
    return [];
  }
}

export async function deleteGuestMeal(id: number): Promise<void> {
  try {
    const db = await openDB();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

export function clearGuestMeals(): void {
  if (_dbPromise) {
    _dbPromise.then((db) => { try { db.close(); } catch { /* ignore */ } }).catch(() => {});
    _dbPromise = null;
  }
  try {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onerror = () => {};
    req.onblocked = () => {};
  } catch {
    /* indexedDB unavailable */
  }
}
