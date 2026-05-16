import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import BackButton from '../components/BackButton';
import LoadingSpinner from '../components/LoadingSpinner';
import { ApiError } from '../api/client';
import {
  adminApi,
  type AdminOverviewResponse,
  type AdminUserDetailResponse,
  type AdminDashboardResponse,
  type AdminFeedbackResponse,
  type FeedbackThumbsDown,
  type TodayActivityResponse,
  type TodayMeal,
} from '../api/admin';

// Admin-only metrics page. The backend returns 404 to non-admin callers, so
// unauthorized visitors see a "Not found" message. There is no BottomNav
// entry or public link - reachable by typing /admin/metrics or from a
// hidden link in Settings (admin-only).

const EVENT_COLUMNS: { key: string; label: string }[] = [
  { key: 'meal_analyze_image', label: 'Analyze (img)' },
  { key: 'meal_analyze_text', label: 'Analyze (txt)' },
  { key: 'meal_analyze_combined', label: 'Analyze (both)' },
  { key: 'meal_correct', label: 'Corrections' },
  { key: 'meal_accept', label: 'Accepted' },
  { key: 'meal_barcode_scan', label: 'Barcode' },
  { key: 'meal_quick_log', label: 'Quick log' },
  { key: 'meal_relog', label: 'Relog' },
  { key: 'meal_copy_day', label: 'Copy day' },
  { key: 'weight_log', label: 'Weight' },
];

function pct(num: number, denom: number): string {
  if (denom <= 0) return '-';
  return `${((num / denom) * 100).toFixed(0)}%`;
}

function formatNumber(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export default function AdminMetrics() {
  const [overview, setOverview] = useState<AdminOverviewResponse | null>(null);
  const [dashboard, setDashboard] = useState<AdminDashboardResponse | null>(null);
  const [feedback, setFeedback] = useState<AdminFeedbackResponse | null>(null);
  const [today, setToday] = useState<TodayActivityResponse | null>(null);
  const [detail, setDetail] = useState<AdminUserDetailResponse | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [expandedFeedbackId, setExpandedFeedbackId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refreshToday = () => {
    setRefreshing(true);
    adminApi.today(24)
      .then((t) => { setToday(t); })
      .catch(() => {})
      .finally(() => { setRefreshing(false); });
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setForbidden(false);
    // Fetch today separately so a failure doesn't blank the whole page.
    adminApi.today(24).then((t) => { if (!cancelled) setToday(t); }).catch(() => {});
    Promise.all([adminApi.overview(days), adminApi.dashboard(days), adminApi.feedback(days)])
      .then(([o, d, f]) => {
        if (cancelled) return;
        setOverview(o);
        setDashboard(d);
        setFeedback(f);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setForbidden(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [days]);

  const openUser = async (userId: number) => {
    setDetail(null);
    try {
      const data = await adminApi.user(userId, days);
      setDetail(data);
    } catch {
      // ignore - admin gate already verified by overview
    }
  };

  if (forbidden) {
    return (
      <div className="space-y-4">
        <BackButton fallbackPath="/settings" />
        <div className="glass-card p-6 text-center">
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>Not found.</p>
        </div>
      </div>
    );
  }

  if (loading && !overview) {
    return (
      <div className="py-16">
        <LoadingSpinner size="md" fullPage />
      </div>
    );
  }

  const funnel = dashboard?.funnel;
  const acq = dashboard?.acquisition;
  const adoption = dashboard?.adoption;
  const limits = dashboard?.limits;
  const cost = dashboard?.cost;
  const budget = dashboard?.budget;
  const retention = dashboard?.retention;

  return (
    <div className="space-y-4">
      <BackButton fallbackPath="/settings" />

      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>Admin metrics</h1>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className="text-xs px-2.5 py-1 rounded-md font-semibold transition-colors"
              style={{
                background: days === d ? 'var(--bg-card)' : 'transparent',
                color: days === d ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* ── Today's Activity ──────────────────────────────────────── */}
      {today && (today.meals.length > 0 || today.weights.length > 0) && (
        <TodayActivity data={today} onRefresh={refreshToday} refreshing={refreshing} />
      )}

      {/* ── Funnel ───────────────────────────────────────────────── */}
      {funnel && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Meal funnel (last {days}d)
          </p>
          <div className="space-y-2">
            <FunnelRow label="Analyzed" value={funnel.analyze_total} total={funnel.analyze_total} />
            <FunnelRow label="Accepted" value={funnel.accept} total={funnel.analyze_total}
              sub={`${pct(funnel.accept, funnel.analyze_total)} of analyses`} />
            <FunnelRow label="Cancelled" value={funnel.cancel} total={funnel.analyze_total}
              sub={`${pct(funnel.cancel, funnel.analyze_total)} drop-off`} />
            <FunnelRow label="Corrected" value={funnel.correct} total={funnel.analyze_total}
              sub={`${pct(funnel.correct, funnel.analyze_total)} needed a fix`} />
            <FunnelRow label="Deleted" value={funnel.delete} total={funnel.accept}
              sub={`${pct(funnel.delete, funnel.accept)} of accepted (regret)`} />
          </div>
          <div className="mt-4 pt-3 grid grid-cols-2 gap-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <Stat label="Image" value={funnel.analyze_image} />
            <Stat label="Text" value={funnel.analyze_text} />
            <Stat label="Combined" value={funnel.analyze_combined} />
            <Stat label="Barcode" value={funnel.barcode_scan} />
            <Stat label="Quick-log" value={funnel.quick_log} />
            <Stat label="Relog" value={funnel.relog} />
          </div>
        </div>
      )}

      {/* ── Accuracy feedback ───────────────────────────────────
          Users rate each logged meal 👍/👎. This card surfaces the
          totals plus a drill-down into every 👎 with the full Gemini
          analysis snapshot stored at accept time (conversation,
          nutrition before + after corrections, image path, barcode)
          so the analysis can be reproduced locally. */}
      {feedback && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Accuracy feedback (last {days}d)
          </p>
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="text-center">
              <div className="text-lg font-bold tabular-nums" style={{ color: '#10b981' }}>
                👍 {feedback.totals.thumbs_up}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Accurate</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold tabular-nums" style={{ color: '#ef4444' }}>
                👎 {feedback.totals.thumbs_down}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Inaccurate</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                {(feedback.totals.submission_rate * 100).toFixed(0)}%
              </div>
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                Rated ({feedback.totals.total}/{feedback.totals.meals_logged})
              </div>
            </div>
          </div>

          {feedback.thumbs_downs.length === 0 ? (
            <p className="text-xs text-center py-3" style={{ color: 'var(--text-muted)' }}>
              No 👎 ratings in this window.
            </p>
          ) : (
            <div className="space-y-1" style={{ borderTop: '1px solid var(--border-glass)', paddingTop: '0.75rem' }}>
              <p className="text-[10px] mb-1" style={{ color: 'var(--text-muted)' }}>
                Recent 👎 ({feedback.thumbs_downs.length}) - tap to expand
              </p>
              {feedback.thumbs_downs.map((fd) => (
                <FeedbackRow
                  key={fd.feedback_id}
                  fd={fd}
                  expanded={expandedFeedbackId === fd.feedback_id}
                  onToggle={() => setExpandedFeedbackId(expandedFeedbackId === fd.feedback_id ? null : fd.feedback_id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Acquisition ────────────────────────────────────────── */}
      {acq && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Acquisition (last {days}d)
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Google signups" value={acq.signup_google} />
            <Stat label="Email PIN signups" value={acq.signup_email} />
            <Stat label="Total signups" value={acq.signup_total} />
            <Stat label="All-time users" value={acq.total_users} />
            <Stat label="Waitlist (pending)" value={acq.waitlist_pending} />
            <Stat label="Waitlist (total)" value={acq.waitlist_total} />
          </div>
        </div>
      )}

      {/* ── Feature adoption ───────────────────────────────────── */}
      {adoption && acq && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Feature adoption
          </p>
          <div className="space-y-1.5 text-xs">
            <AdoptionRow label="Gamification on" num={adoption.gamification_on} denom={acq.total_users} />
            <AdoptionRow label="Gamification off" num={adoption.gamification_off} denom={acq.total_users} />
            <AdoptionRow label="Has macro targets" num={adoption.has_targets} denom={acq.total_users} />
            <AdoptionRow label="Reminders on" num={adoption.reminders_on} denom={acq.total_users} />
            <AdoptionRow label="Push subscribed" num={adoption.push_subscribed} denom={acq.total_users} />
            <AdoptionRow label="Ever used AI Chat" num={adoption.chat_users} denom={acq.total_users} />
            <AdoptionRow label="Strava connected" num={adoption.strava_connected} denom={acq.total_users} />
            <AdoptionRow label="Fitbit connected" num={adoption.fitbit_connected} denom={acq.total_users} />
            <AdoptionRow label={`Logged weight (${days}d)`} num={adoption.logged_weight_recent} denom={acq.total_users} />
            <AdoptionRow label={`Barcode users (${days}d)`} num={adoption.barcode_users_recent} denom={acq.total_users} />
            <AdoptionRow label={`Quick-log users (${days}d)`} num={adoption.quick_log_users_recent} denom={acq.total_users} />
            <AdoptionRow label="Premium / OG" num={adoption.premium_users} denom={acq.total_users} />
            <AdoptionRow label="Active trial" num={adoption.trial_users} denom={acq.total_users} />
          </div>
        </div>
      )}

      {/* ── Limit hits (upgrade pressure) ──────────────────────── */}
      {limits && Object.keys(limits).length > 0 && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Limit hits (last {days}d)
          </p>
          <div className="space-y-1.5">
            {Object.entries(limits).map(([feature, entry]) => (
              <div key={feature} className="flex items-center justify-between text-xs">
                <span style={{ color: 'var(--text-secondary)' }}>{feature}</span>
                <span className="tabular-nums" style={{ color: 'var(--text-primary)' }}>
                  <strong>{entry.count}</strong>
                  <span className="ml-2" style={{ color: 'var(--text-muted)' }}>
                    ({entry.unique_users} user{entry.unique_users === 1 ? '' : 's'})
                  </span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Monthly spend vs budget gate ────────────────────────── */}
      {budget && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Month-to-date Gemini spend
          </p>
          <div className="flex items-baseline justify-between mb-2">
            <span className="text-2xl font-semibold tabular-nums"
              style={{ color: budget.gate_tripped ? '#ef4444' : 'var(--text-primary)' }}>
              ${budget.monthly_cost_usd.toFixed(4)}
            </span>
            <span className="text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
              of ${budget.monthly_budget_usd.toFixed(0)} cap ({budget.pct_used.toFixed(1)}%)
            </span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(100, budget.pct_used)}%`,
                background: budget.gate_tripped
                  ? '#ef4444'
                  : budget.pct_used > 75
                    ? '#f59e0b'
                    : '#10b981',
              }}
            />
          </div>
          {budget.gate_tripped ? (
            <p className="text-[11px] mt-2 font-semibold" style={{ color: '#ef4444' }}>
              ⚠ Gate tripped - Gemini calls are returning 503 until next month.
            </p>
          ) : (
            <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
              Gate trips at 100%. Pub/Sub kill switch is the backstop at a higher threshold.
            </p>
          )}
        </div>
      )}

      {/* ── Gemini cost ─────────────────────────────────────────── */}
      {cost && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Gemini API cost (last {days}d)
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="API calls" value={cost.calls} />
            <Stat label="Unique users" value={cost.unique_users} />
            <Stat label="Image calls" value={cost.image_calls} />
            <Stat label="Web searches" value={cost.web_searches} />
            <Stat label="Input tokens" value={cost.input_tokens} format />
            <Stat label="Output tokens" value={cost.output_tokens} format />
          </div>
          <div className="mt-3 pt-3 flex justify-between text-xs" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <span style={{ color: 'var(--text-muted)' }}>Estimated cost</span>
            <span className="font-semibold tabular-nums" style={{ color: '#10b981' }}>
              ${cost.estimated_cost_usd.toFixed(2)}
            </span>
          </div>
          <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
            Rough estimate at 2.5 Flash Lite pricing ($0.10/$0.40 per M tokens).
          </p>
        </div>
      )}

      {/* ── Retention (DAU) ────────────────────────────────────── */}
      {retention && retention.length > 0 && (
        <div className="glass-card p-4">
          <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
            Daily active users (last {days}d)
          </p>
          <RetentionChart data={retention} />
          <div className="mt-3 flex justify-between text-[10px]" style={{ color: 'var(--text-muted)' }}>
            <span>Avg DAU:&nbsp;
              <span style={{ color: 'var(--text-secondary)' }}>
                {(retention.reduce((s, d) => s + d.active_users, 0) / retention.length).toFixed(1)}
              </span>
            </span>
            <span>Peak DAU:&nbsp;
              <span style={{ color: 'var(--text-secondary)' }}>
                {Math.max(...retention.map(d => d.active_users))}
              </span>
            </span>
          </div>
        </div>
      )}

      {overview && (
        <>
          {/* Global totals */}
          <div className="glass-card p-4">
            <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>Raw event totals (last {days}d)</p>
            <div className="grid grid-cols-2 gap-2">
              {EVENT_COLUMNS.map(({ key, label }) => (
                <div key={key} className="flex justify-between text-xs">
                  <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
                  <span className="font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                    {overview.totals[key] ?? 0}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Per-user table */}
          <div className="glass-card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-glass)' }}>
              <p className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>
                Per user ({overview.users.length} active)
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ background: 'var(--bg-elevated)' }}>
                    <th className="text-left px-3 py-2 font-semibold" style={{ color: 'var(--text-muted)' }}>User</th>
                    {EVENT_COLUMNS.map(({ key, label }) => (
                      <th key={key} className="text-right px-2 py-2 font-semibold whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                        {label}
                      </th>
                    ))}
                    <th className="text-right px-3 py-2 font-semibold" style={{ color: 'var(--text-muted)' }}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.users.map((u) => (
                    <tr
                      key={u.user_id}
                      onClick={() => openUser(u.user_id)}
                      className="cursor-pointer transition-colors"
                      style={{ borderTop: '1px solid var(--border-glass)' }}
                    >
                      <td className="px-3 py-2">
                        <div className="font-semibold" style={{ color: 'var(--text-primary)' }}>
                          {u.first_name || u.email.split('@')[0]}
                        </div>
                        <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                          {u.email} · #{u.user_id}
                        </div>
                      </td>
                      {EVENT_COLUMNS.map(({ key }) => (
                        <td key={key} className="text-right px-2 py-2 tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                          {u.events[key] || ''}
                        </td>
                      ))}
                      <td className="text-right px-3 py-2 font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                        {u.total}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Drilldown */}
          {detail && (
            <div className="glass-card p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {detail.first_name || detail.email}
                  </p>
                  <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>#{detail.user_id}</p>
                </div>
                <button
                  onClick={() => setDetail(null)}
                  className="text-xs"
                  style={{ color: 'var(--text-muted)' }}
                >Close</button>
              </div>
              <div className="grid grid-cols-2 gap-1">
                {EVENT_COLUMNS.map(({ key, label }) => (
                  <div key={key} className="flex justify-between text-[11px]">
                    <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
                    <span className="font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                      {detail.totals[key] ?? 0}
                    </span>
                  </div>
                ))}
              </div>
              {detail.timeseries.length > 0 && (
                <div className="pt-2" style={{ borderTop: '1px solid var(--border-glass)' }}>
                  <p className="text-[10px] mb-1" style={{ color: 'var(--text-muted)' }}>Daily breakdown</p>
                  <div className="space-y-0.5 max-h-60 overflow-y-auto">
                    {detail.timeseries.map((r, i) => (
                      <div key={i} className="flex justify-between text-[10px] tabular-nums">
                        <span style={{ color: 'var(--text-muted)' }}>{r.day}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>{r.event_type}</span>
                        <span style={{ color: 'var(--text-primary)' }}>{r.cnt}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      <p className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>
        Private · never exposed publicly · <Link to="/settings" style={{ textDecoration: 'underline' }}>Settings</Link>
      </p>
    </div>
  );
}

// ── Today's Activity ─────────────────────────────────────────────

function formatTime(logged_at: string): string {
  const parts = logged_at.split(' ');
  return parts[1] || logged_at;
}

function formatDate(logged_at: string): string {
  const parts = logged_at.split(' ');
  return parts[0] || logged_at;
}

const SOURCE_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  image:    { label: 'Photo',   color: '#7c3aed', bg: 'rgba(124,58,237,0.12)' },
  text:     { label: 'Text',    color: '#2563eb', bg: 'rgba(37,99,235,0.12)' },
  combined: { label: 'Photo+',  color: '#7c3aed', bg: 'rgba(124,58,237,0.12)' },
  barcode:  { label: 'Barcode', color: '#d97706', bg: 'rgba(217,119,6,0.12)' },
  alias:    { label: 'Saved',   color: '#059669', bg: 'rgba(5,150,105,0.12)' },
  relog:    { label: 'Relog',   color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
  copy_day: { label: 'Copy',   color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
  gemini:   { label: 'Gemini',  color: '#2563eb', bg: 'rgba(37,99,235,0.12)' },
};

function SourceChip({ source }: { source: string }) {
  const s = SOURCE_LABELS[source] || { label: source || '?', color: '#6b7280', bg: 'rgba(107,114,128,0.12)' };
  return (
    <span
      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 uppercase tracking-wide"
      style={{ color: s.color, background: s.bg }}
    >
      {s.label}
    </span>
  );
}

interface UserMealGroup {
  user_id: number;
  first_name: string;
  email: string;
  meals: TodayMeal[];
  total_cal: number;
  total_protein: number;
}

function UserInitial({ name, email }: { name: string; email: string }) {
  const letter = (name || email || '?')[0].toUpperCase();
  return (
    <div
      className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
      style={{ background: 'rgba(16,185,129,0.15)', color: '#10b981' }}
    >
      {letter}
    </div>
  );
}

function TodayActivity({ data, onRefresh, refreshing }: { data: TodayActivityResponse; onRefresh: () => void; refreshing?: boolean }) {
  const byUser = new Map<number, UserMealGroup>();
  for (const m of data.meals) {
    let grp = byUser.get(m.user_id);
    if (!grp) {
      grp = { user_id: m.user_id, first_name: m.first_name, email: m.email, meals: [], total_cal: 0, total_protein: 0 };
      byUser.set(m.user_id, grp);
    }
    grp.meals.push(m);
    grp.total_cal += m.calories;
    grp.total_protein += m.protein;
  }
  const groups = Array.from(byUser.values()).sort((a, b) => {
    const aTime = a.meals[0]?.logged_at || '';
    const bTime = b.meals[0]?.logged_at || '';
    return bTime.localeCompare(aTime);
  });

  const dates = [...new Set(data.meals.map(m => formatDate(m.logged_at)))].sort().reverse();
  const todayStr = dates[0] || '';

  return (
    <div className="glass-card p-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border-glass)' }}>
        <p className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>
          Today's activity
        </p>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-[11px] tabular-nums">
            <span style={{ color: 'var(--text-muted)' }}>
              <strong style={{ color: 'var(--text-primary)' }}>{data.meals.length}</strong> meals
            </span>
            <span style={{ color: 'var(--border-glass)' }}>|</span>
            <span style={{ color: 'var(--text-muted)' }}>
              <strong style={{ color: 'var(--text-primary)' }}>{groups.length}</strong> users
            </span>
            {data.weights.length > 0 && (
              <>
                <span style={{ color: 'var(--border-glass)' }}>|</span>
                <span style={{ color: 'var(--text-muted)' }}>
                  <strong style={{ color: 'var(--text-primary)' }}>{data.weights.length}</strong> weigh-ins
                </span>
              </>
            )}
          </div>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="text-[10px] px-2.5 py-1 rounded-md font-semibold transition-colors"
            style={{
              background: 'var(--bg-elevated)',
              color: refreshing ? 'var(--text-muted)' : '#10b981',
              opacity: refreshing ? 0.6 : 1,
            }}
          >
            {refreshing ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Per-user sections */}
      <div>
        {groups.map((grp, gi) => (
          <div
            key={grp.user_id}
            style={gi > 0 ? { borderTop: '1px solid var(--border-glass)' } : undefined}
          >
            {/* User header */}
            <div className="flex items-center gap-2.5 px-4 py-3" style={{ background: 'var(--bg-elevated)' }}>
              <UserInitial name={grp.first_name} email={grp.email} />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
                  {grp.first_name || grp.email.split('@')[0]}
                </div>
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {grp.email}
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-bold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                  {grp.total_cal.toFixed(0)}
                  <span className="text-[10px] font-normal ml-0.5" style={{ color: 'var(--text-muted)' }}>cal</span>
                </div>
                <div className="text-[10px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
                  {grp.total_protein.toFixed(0)}g protein · {grp.meals.length} meal{grp.meals.length !== 1 ? 's' : ''}
                </div>
              </div>
            </div>

            {/* Meal rows */}
            <div className="px-4 py-2 space-y-2">
              {grp.meals.map((m) => {
                const showDate = formatDate(m.logged_at) !== todayStr;
                const desc = m.meal_description
                  ? (m.meal_description.length > 60 ? m.meal_description.slice(0, 60) + '...' : m.meal_description)
                  : m.meal_type || '(no description)';
                return (
                  <div
                    key={m.meal_id}
                    className="rounded-lg px-3 py-2"
                    style={{ background: 'var(--bg-elevated)' }}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] tabular-nums font-medium" style={{ color: 'var(--text-muted)' }}>
                          {showDate ? m.logged_at : formatTime(m.logged_at)}
                        </span>
                        <SourceChip source={m.source} />
                      </div>
                      <div className="flex items-center gap-2.5 text-[11px] tabular-nums">
                        <span className="font-bold" style={{ color: 'var(--text-primary)' }}>
                          {m.calories.toFixed(0)} cal
                        </span>
                        <span style={{ color: 'var(--text-muted)' }}>
                          P{m.protein.toFixed(0)} C{m.carbs.toFixed(0)} F{m.fat.toFixed(0)}
                        </span>
                      </div>
                    </div>
                    <div className="text-[11px] leading-snug" style={{ color: 'var(--text-secondary)' }}>
                      {desc}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {/* Weight logs */}
        {data.weights.length > 0 && (
          <div className="px-4 py-3" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <p className="text-[10px] font-semibold mb-2 uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
              Weight logs
            </p>
            <div className="space-y-1.5">
              {data.weights.map((w) => (
                <div key={w.weight_id} className="flex items-center gap-2.5 text-xs">
                  <span className="tabular-nums" style={{ color: 'var(--text-muted)' }}>
                    {formatTime(w.logged_at)}
                  </span>
                  <span className="font-bold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                    {w.weight_kg.toFixed(1)} kg
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {w.first_name || w.email.split('@')[0]}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Small presentational helpers ──────────────────────────────────

function FunnelRow({ label, value, total, sub }: { label: string; value: number; total: number; sub?: string }) {
  const width = total > 0 ? Math.max(2, (value / total) * 100) : 0;
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-0.5">
        <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
        <span className="tabular-nums font-semibold" style={{ color: 'var(--text-primary)' }}>{value}</span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--track-bg)' }}>
        <div className="h-full rounded-full" style={{ width: `${width}%`, background: '#10b981' }} />
      </div>
      {sub && (
        <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{sub}</div>
      )}
    </div>
  );
}

function Stat({ label, value, format }: { label: string; value: number; format?: boolean }) {
  const display = format ? formatNumber(value) : String(value);
  return (
    <div className="flex justify-between text-xs">
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span className="font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>{display}</span>
    </div>
  );
}

function AdoptionRow({ label, num, denom }: { label: string; num: number; denom: number }) {
  const width = denom > 0 ? (num / denom) * 100 : 0;
  return (
    <div>
      <div className="flex items-center justify-between">
        <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
        <span className="tabular-nums" style={{ color: 'var(--text-primary)' }}>
          {num} <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>({pct(num, denom)})</span>
        </span>
      </div>
      <div className="h-1 mt-0.5 rounded-full" style={{ background: 'var(--track-bg)' }}>
        <div className="h-full rounded-full" style={{ width: `${Math.min(100, width)}%`, background: '#3b82f6' }} />
      </div>
    </div>
  );
}

function FeedbackRow({ fd, expanded, onToggle }: { fd: FeedbackThumbsDown; expanded: boolean; onToggle: () => void }) {
  const [copied, setCopied] = useState(false);
  const snapshotStr = fd.snapshot ? JSON.stringify(fd.snapshot, null, 2) : '(no snapshot - pre-rollout meal)';
  const date = fd.created_at ? new Date(fd.created_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';

  const copySnapshot = async () => {
    try {
      await navigator.clipboard.writeText(snapshotStr);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // silently ignore - clipboard may be denied
    }
  };

  return (
    <div className="rounded-md" style={{ background: expanded ? 'var(--bg-elevated)' : 'transparent' }}>
      <button
        onClick={onToggle}
        className="w-full text-left px-2 py-1.5 flex items-center justify-between gap-2"
      >
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {fd.item_name || '(no name)'}
          </div>
          <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>
            #{fd.meal_id} · user {fd.user_id} · {fd.source || 'unknown'} · {date}
          </div>
          {fd.comment && (
            <div className="text-[11px] mt-0.5 italic" style={{ color: 'var(--text-secondary)' }}>
              "{fd.comment}"
            </div>
          )}
        </div>
        <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>
          {expanded ? '−' : '+'}
        </span>
      </button>
      {expanded && (
        <div className="px-2 pb-2 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Analysis snapshot</span>
            <button
              onClick={copySnapshot}
              className="text-[10px] px-2 py-0.5 rounded"
              style={{ background: 'var(--bg-card)', color: 'var(--text-secondary)' }}
            >
              {copied ? '✓ Copied' : 'Copy JSON'}
            </button>
          </div>
          <pre
            className="text-[10px] p-2 rounded overflow-x-auto max-h-72 overflow-y-auto"
            style={{ background: 'var(--bg-card)', color: 'var(--text-secondary)' }}
          >
            {snapshotStr}
          </pre>
        </div>
      )}
    </div>
  );
}

function RetentionChart({ data }: { data: { day: string; active_users: number; events: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.active_users));
  return (
    <div className="flex items-end gap-[2px] h-24">
      {data.map((d) => (
        <div
          key={d.day}
          className="flex-1 rounded-t"
          style={{
            height: `${Math.max(4, (d.active_users / max) * 100)}%`,
            background: 'linear-gradient(180deg, #10b981 0%, #10b98180 100%)',
            minWidth: 0,
          }}
          title={`${d.day}: ${d.active_users} users / ${d.events} events`}
        />
      ))}
    </div>
  );
}
