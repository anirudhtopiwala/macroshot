/**
 * Frontend cache for barcode lookups.
 * Stores successful results in localStorage with a 24-hour TTL
 * and a max of 100 entries (oldest evicted on overflow).
 */

import type { Nutrition } from '../types';

const PREFIX = 'bc_cache_';
const INDEX_KEY = 'bc_cache__index';
const TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const MAX_ENTRIES = 100;

export interface BarcodeCacheEntry {
  nutrition: Nutrition;
  productName: string;
  imageUrl: string | null;
  timestamp: number;
}

/** Read the ordered list of cached barcode keys (oldest first). */
function getIndex(): string[] {
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function setIndex(keys: string[]): void {
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(keys));
  } catch { /* storage full - handled by caller */ }
}

/**
 * Look up a barcode in the local cache.
 * Returns the entry if found and fresh, otherwise null.
 */
export function getCachedBarcode(barcode: string): BarcodeCacheEntry | null {
  try {
    const raw = localStorage.getItem(PREFIX + barcode);
    if (!raw) return null;
    const entry: BarcodeCacheEntry = JSON.parse(raw);
    if (Date.now() - entry.timestamp > TTL_MS) {
      // Expired - remove it
      localStorage.removeItem(PREFIX + barcode);
      const idx = getIndex().filter((k) => k !== barcode);
      setIndex(idx);
      return null;
    }
    return entry;
  } catch {
    return null;
  }
}

/**
 * Remove a single barcode entry from the cache. Used when the user resets
 * a saved correction - the cached (corrected) nutrition must be dropped so
 * the next scan doesn't flash the stale corrected values before the backend
 * round-trip returns fresh OFF data.
 */
export function clearCachedBarcode(barcode: string): void {
  try {
    localStorage.removeItem(PREFIX + barcode);
    const idx = getIndex().filter((k) => k !== barcode);
    setIndex(idx);
  } catch { /* storage unavailable - ignore */ }
}

/**
 * Store a successful barcode lookup in the cache.
 * Evicts the oldest entry if the cache exceeds MAX_ENTRIES.
 */
export function setCachedBarcode(
  barcode: string,
  nutrition: Nutrition,
  productName: string,
  imageUrl: string | null,
): void {
  const entry: BarcodeCacheEntry = {
    nutrition,
    productName,
    imageUrl,
    timestamp: Date.now(),
  };

  try {
    localStorage.setItem(PREFIX + barcode, JSON.stringify(entry));
  } catch {
    // localStorage full - try evicting oldest and retry once
    const idx = getIndex();
    if (idx.length > 0) {
      const oldest = idx.shift()!;
      localStorage.removeItem(PREFIX + oldest);
      setIndex(idx);
      try {
        localStorage.setItem(PREFIX + barcode, JSON.stringify(entry));
      } catch {
        return; // give up
      }
    } else {
      return;
    }
  }

  // Update index: remove if already present, append to end (newest)
  const idx = getIndex().filter((k) => k !== barcode);
  idx.push(barcode);

  // Evict oldest entries beyond MAX_ENTRIES
  while (idx.length > MAX_ENTRIES) {
    const oldest = idx.shift()!;
    localStorage.removeItem(PREFIX + oldest);
  }

  setIndex(idx);
}
