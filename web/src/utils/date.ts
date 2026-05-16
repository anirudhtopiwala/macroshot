/** Format a Date as YYYY-MM-DD in the local timezone (avoids UTC drift from toISOString). */
export function formatLocalDate(d: Date = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Format a Date as YYYY-MM-DD HH:MM in the local timezone. */
export function formatLocalDateTime(d: Date = new Date()): string {
  return `${formatLocalDate(d)} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Relative "time since" label for an ISO timestamp (e.g. "Just now", "5m ago", "3h ago", "2d ago"). Returns "" for null/undefined. */
export function formatTimeAgo(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts.includes('T') ? ts : ts + 'Z');
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
