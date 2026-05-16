import { useSubscription } from '../context/SubscriptionContext';

export type CapFeature =
  | 'image_analysis'
  | 'text_meal'
  | 'ai_chat'
  | 'saved_meals';

export interface CapState {
  used: number;
  limit: number;
  remaining: number;
  atLimit: boolean;
  nearLimit: boolean;
  lastOne: boolean;
  unlimited: boolean;
  hasCap: boolean;
}

/** Shared cap state for a gated feature. Returns computed UI flags so
 *  every surface (buttons, meters, banners) derives the same
 *  remaining/near/at decisions from the single SubscriptionContext.
 *  Treats limit === -1 as unlimited (no cap), limit === 0 as no cap
 *  (legacy self-host), and limit > 0 as the enforceable daily cap.
 */
export function useCapState(feature: CapFeature): CapState {
  const { imageUsage, textMealUsage, chatUsage, savedMealsUsage } = useSubscription();

  const { used, limit } = (() => {
    switch (feature) {
      case 'image_analysis': return imageUsage;
      case 'text_meal': return textMealUsage;
      case 'ai_chat': return chatUsage;
      case 'saved_meals': return savedMealsUsage;
    }
  })();

  const unlimited = limit === -1;
  const hasCap = limit > 0;
  const remaining = hasCap ? Math.max(0, limit - used) : Infinity;
  const atLimit = hasCap && used >= limit;
  const lastOne = hasCap && used === limit - 1;
  const nearLimit = hasCap && used / limit >= 0.7 && !atLimit;

  return {
    used,
    limit,
    remaining,
    atLimit,
    nearLimit,
    lastOne,
    unlimited,
    hasCap,
  };
}
