import { useToast } from './Toast';
import { subscriptionApi } from '../api/subscription';
import { useSubscription } from '../context/SubscriptionContext';
import Button from './Button';
import { track } from '../api/analytics';

/**
 * Features that UpgradeCard can explain when the daily cap is hit.
 * Matches the backend's `limit_message` keys so a single `feature` prop
 * drives both backend error copy and this card's presentation.
 */
export type UpgradeCardFeature =
  | 'image_analysis'
  | 'text_meal'
  | 'meal_edit'
  | 'ai_chat'
  | string; // fallback for unknown/legacy callers

interface Props {
  feature?: UpgradeCardFeature;
  message?: string;
}

interface BetaCopy {
  label: string;
  body: (limit: number) => string;
}

const BETA_COPY: Record<string, BetaCopy> = {
  image_analysis: {
    label: 'You\u2019re on the free beta',
    body: (limit) =>
      `Photo scans are capped at ${limit}/day while we're in limited beta. Resets at midnight - or log with text anytime.`,
  },
  text_meal: {
    label: 'You\u2019re on the free beta',
    body: (limit) =>
      `Text meal logs are capped at ${limit}/day while we're in limited beta. Resets at midnight - come back tomorrow.`,
  },
  ai_chat: {
    label: 'You\u2019re on the free beta',
    body: (limit) =>
      `AI coach chats are capped at ${limit}/day while we're in limited beta. Resets at midnight.`,
  },
  meal_edit: {
    label: 'You\u2019re on the free beta',
    body: (limit) =>
      `AI meal edits are capped at ${limit} per meal while we're in limited beta. Start a fresh meal to get a new budget.`,
  },
};

export default function UpgradeCard({ feature, message }: Props) {
  const { toast } = useToast();
  const {
    betaMode,
    imageUsage,
    textMealUsage,
    chatUsage,
    mealEditLimit,
  } = useSubscription();

  const displayMessage =
    message ??
    (feature
      ? `${feature} is a Pro feature. Upgrade to unlock it.`
      : 'Upgrade to Pro to unlock all features.');

  async function handleGoPro() {
    track('ui_upgrade_cta_clicked', { feature: feature || 'unknown' });
    try {
      const data = await subscriptionApi.checkout('pro_monthly');
      if (data.url) {
        window.location.href = data.url;
      } else {
        toast(data.error || 'Billing not available', 'error');
      }
    } catch {
      toast('Could not start checkout', 'error');
    }
  }

  // Beta mode branch - no Stripe, no price disclosure. Each feature has
  // its own copy so a chat-cap 429 doesn't render "Photo scans are
  // capped at N/day". Post-beta regressions (betaMode flipping back on)
  // still can't accidentally show a Stripe button - the branch has no
  // button at all.
  if (betaMode) {
    // Feature-specific copy + cap reads the right SubscriptionContext
    // field. Default to image_analysis when feature is unspecified.
    const key = feature && feature in BETA_COPY ? feature : 'image_analysis';
    const copy = BETA_COPY[key];
    const capLookup: Record<string, number> = {
      image_analysis: imageUsage.limit,
      text_meal: textMealUsage.limit,
      ai_chat: chatUsage.limit,
      meal_edit: mealEditLimit,
    };
    const limit = capLookup[key] ?? imageUsage.limit;
    return (
      <div
        className="rounded-2xl p-4"
        role="status"
        aria-live="polite"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-glass)',
          backdropFilter: 'blur(16px)',
        }}
      >
        {/* Header with sparkle icon */}
        <div className="flex items-center gap-2 mb-2">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ color: '#f59e0b' }}
            aria-hidden="true"
          >
            <path d="M12 2l2.09 6.26L20.18 9l-5.09 3.74L16.18 19 12 15.27 7.82 19l1.09-6.26L3.82 9l6.09-.74z" />
          </svg>
          <span
            className="text-sm font-bold"
            style={{ color: 'var(--text-primary)' }}
          >
            {copy.label}
          </span>
        </div>
        <p
          className="text-sm leading-snug"
          style={{ color: 'var(--text-secondary)' }}
        >
          {copy.body(limit > 0 ? limit : 0)}
        </p>
        <p
          className="text-xs leading-snug mt-2"
          style={{ color: 'var(--text-muted)' }}
        >
          These caps are only for the soft launch. After beta, monthly plans with no limits will be available.
        </p>
      </div>
    );
  }

  return (
    <div
      className="rounded-2xl p-4"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-glass)',
        backdropFilter: 'blur(16px)',
      }}
    >
      {/* Header with sparkle icon */}
      <div className="flex items-center gap-2 mb-2">
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ color: '#f59e0b' }}
        >
          <path d="M12 2l2.09 6.26L20.18 9l-5.09 3.74L16.18 19 12 15.27 7.82 19l1.09-6.26L3.82 9l6.09-.74z" />
        </svg>
        <span
          className="text-sm font-bold"
          style={{ color: 'var(--text-primary)' }}
        >
          Pro Feature
        </span>
      </div>

      {/* Message */}
      <p
        className="text-sm mb-4"
        style={{ color: 'var(--text-secondary)' }}
      >
        {displayMessage}
      </p>

      {/* Actions */}
      <div className="flex gap-3">
        <Button variant="primary" size="md" onClick={handleGoPro} className="flex-1">
          Go Pro
        </Button>
      </div>

      {/* Inline auto-renewal disclosure required by California Auto-Renewal Law
          (ARL) and the FTC's Click-to-Cancel rule.  Must be visible directly
          adjacent to the consent button - Stripe's checkout page does not
          substitute under CA law. */}
      <p
        className="text-[10px] mt-2 leading-snug text-center"
        style={{ color: 'var(--text-muted)' }}
      >
        $4.99 billed monthly. Auto-renews until cancelled. Cancel any time
        in Settings → Subscription.
      </p>
    </div>
  );
}
