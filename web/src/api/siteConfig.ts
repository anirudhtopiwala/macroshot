import { useEffect, useState } from 'react';

export interface SiteConfig {
  google_client_id: string | null;
  vapid_public_key: string | null;
  operator_name: string | null;
  operator_first_name: string | null;
  contact_email: string | null;
  github_url: string | null;
  sponsor_url: string | null;
  donate_url: string | null;
  app_url: string | null;
  domain: string | null;
}

// Build-time fallbacks injected via VITE_* env vars. These let Privacy,
// Terms, and About render the right operator identity on first paint -
// before /api/config resolves - eliminating the "the operator at this
// instance" flash on the hosted instance. Self-hosters that skip these
// still get correct values a moment later from /api/config.
const BUILD_TIME_CONFIG: SiteConfig = {
  google_client_id: null,
  vapid_public_key: null,
  operator_name: (import.meta.env.VITE_OPERATOR_NAME as string) || null,
  operator_first_name: null,
  contact_email: (import.meta.env.VITE_CONTACT_EMAIL as string) || null,
  github_url: (import.meta.env.VITE_GITHUB_URL as string) || null,
  sponsor_url: (import.meta.env.VITE_SPONSOR_URL as string) || null,
  donate_url: (import.meta.env.VITE_DONATE_URL as string) || null,
  app_url: (import.meta.env.VITE_APP_URL as string) || null,
  domain: null,
};

let cachedPromise: Promise<SiteConfig> | null = null;

export function getSiteConfig(): Promise<SiteConfig> {
  if (!cachedPromise) {
    cachedPromise = fetch('/macro_app/api/config')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return BUILD_TIME_CONFIG;
        // Prefer server values; fall back to build-time for any field
        // the server left null (e.g. operator_name on a bare install).
        return {
          google_client_id: data.google_client_id ?? null,
          vapid_public_key: data.vapid_public_key ?? null,
          operator_name: data.operator_name ?? BUILD_TIME_CONFIG.operator_name,
          operator_first_name: data.operator_first_name ?? null,
          contact_email: data.contact_email ?? BUILD_TIME_CONFIG.contact_email,
          github_url: data.github_url ?? BUILD_TIME_CONFIG.github_url,
          sponsor_url: data.sponsor_url ?? BUILD_TIME_CONFIG.sponsor_url,
          donate_url: data.donate_url ?? BUILD_TIME_CONFIG.donate_url,
          app_url: data.app_url ?? BUILD_TIME_CONFIG.app_url,
          domain: data.domain ?? null,
        };
      })
      .catch(() => {
        // Reset the cache so the next caller retries instead of being
        // stuck on BUILD_TIME_CONFIG forever after a transient failure.
        cachedPromise = null;
        return BUILD_TIME_CONFIG;
      });
  }
  return cachedPromise;
}

export function useSiteConfig(): SiteConfig {
  // Start with build-time values so first paint already has the right
  // operator identity, then upgrade with /api/config values when they
  // arrive (picks up domain, vapid key, google client id, etc.).
  const [config, setConfig] = useState<SiteConfig>(BUILD_TIME_CONFIG);
  useEffect(() => {
    let cancelled = false;
    getSiteConfig().then((c) => {
      if (!cancelled) setConfig(c);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return config;
}

// Extract the hostname from a URL so pages can show just "macro.example.com"
// (not the full scheme + path). Falls back to the input when parsing fails.
export function hostFromUrl(url: string | null | undefined): string {
  if (!url) return '';
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
