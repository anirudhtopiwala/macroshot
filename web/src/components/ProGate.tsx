import type { ReactNode } from 'react';
import { useSubscription } from '../context/SubscriptionContext';
import UpgradeCard from './UpgradeCard';

interface Props {
  feature: string;
  children: ReactNode;
  fallback?: ReactNode;
}

export default function ProGate({ feature, children, fallback }: Props) {
  const { isPremium } = useSubscription();

  if (isPremium) {
    return <>{children}</>;
  }

  return <>{fallback ?? <UpgradeCard feature={feature} />}</>;
}
