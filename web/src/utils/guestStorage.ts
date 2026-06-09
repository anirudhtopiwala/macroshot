/**
 * IndexedDB store for guest-mode meals + weight history.
 *
 * The guest path lets a visitor analyze meals / track weight without
 * signing up. The server returns the nutrition result (or accepts
 * barcode + photo + text input) but does NOT persist anything; we
 * keep accepted entries here so the dashboard can show today's
 * totals, the weight tracker can show recent log entries, and we can
 * replay both into the user's real account on signup via the
 * `/meals/import-guest` and `/weight/import-guest` endpoints.
 *
 * Mirrors the offlineQueue.ts shape on purpose - same open/race
 * semantics, same 5s timeout cap. Different DB name so the two
 * stores are independent and the guest store survives a signed-in
 * user's logout (which deletes the offline queue DB).
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

export interface GuestWeight {
  id?: number;
  weight_kg: number;
  /** "YYYY-MM-DD HH:MM[:SS]" - matches the server WeightLogRequest pattern. */
  logged_at: string;
}

const DB_NAME = 'macro_guest';
const DB_VERSION = 2;
const STORE_MEALS = 'guest_meals';
const STORE_WEIGHTS = 'guest_weights';

let _dbPromise: Promise<IDBDatabase> | null = null;

function openDB(): Promise<IDBDatabase> {
  if (!_dbPromise) {
    const rawOpen = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_MEALS)) {
          db.createObjectStore(STORE_MEALS, { keyPath: 'id', autoIncrement: true });
        }
        // v2 adds the weights store. Existing browsers with v1 of the
        // DB hit this branch on the next open and pick up the new
        // store without losing their meal history.
        if (!db.objectStoreNames.contains(STORE_WEIGHTS)) {
          db.createObjectStore(STORE_WEIGHTS, { keyPath: 'id', autoIncrement: true });
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

// ── Meals ───────────────────────────────────────────────────────────

export async function saveGuestMeal(meal: Omit<GuestMeal, 'id'>): Promise<void> {
  try {
    const db = await openDB();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MEALS, 'readwrite');
      tx.objectStore(STORE_MEALS).add(meal);
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
      const tx = db.transaction(STORE_MEALS, 'readonly');
      const req = tx.objectStore(STORE_MEALS).getAll();
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
      const tx = db.transaction(STORE_MEALS, 'readwrite');
      tx.objectStore(STORE_MEALS).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

// ── Weights ─────────────────────────────────────────────────────────

export async function saveGuestWeight(entry: Omit<GuestWeight, 'id'>): Promise<number | null> {
  try {
    const db = await openDB();
    return await new Promise<number>((resolve, reject) => {
      const tx = db.transaction(STORE_WEIGHTS, 'readwrite');
      const req = tx.objectStore(STORE_WEIGHTS).add(entry);
      let key: number | undefined;
      req.onsuccess = () => { key = req.result as number; };
      // Resolve on tx.oncomplete so a quota-exceeded / constraint
      // abort doesn't leave the caller thinking the row landed - the
      // optimistic UI would otherwise show a phantom entry until the
      // next refresh. Matches saveGuestMeal's commit semantics.
      tx.oncomplete = () => resolve(key as number);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error ?? new Error('tx aborted'));
    });
  } catch {
    return null;
  }
}

export async function listGuestWeights(): Promise<GuestWeight[]> {
  try {
    const db = await openDB();
    const raw = await new Promise<GuestWeight[]>((resolve, reject) => {
      const tx = db.transaction(STORE_WEIGHTS, 'readonly');
      const req = tx.objectStore(STORE_WEIGHTS).getAll();
      req.onsuccess = () => resolve(req.result as GuestWeight[]);
      req.onerror = () => reject(req.error);
    });
    // Server returns weight history newest-first; mirror that here so
    // WeightTracker doesn't need a guest-specific sort branch.
    return raw.sort((a, b) => (a.logged_at < b.logged_at ? 1 : -1));
  } catch {
    return [];
  }
}

export async function deleteGuestWeight(id: number): Promise<void> {
  try {
    const db = await openDB();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_WEIGHTS, 'readwrite');
      tx.objectStore(STORE_WEIGHTS).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

// ── Whole-DB clear (signup migration / logout) ──────────────────────

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
