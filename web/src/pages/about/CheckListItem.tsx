import type { ReactNode } from 'react';
import { CheckIcon } from './icons';

export type Accent = 'emerald' | 'purple' | 'orange' | 'blue';

const ACCENTS: Record<Accent, { bg: string; fg: string }> = {
  emerald: { bg: 'rgba(16, 185, 129, 0.15)', fg: '#10b981' },
  purple: { bg: 'rgba(168, 85, 247, 0.15)', fg: '#a855f7' },
  orange: { bg: 'rgba(249, 115, 22, 0.15)', fg: '#f97316' },
  blue: { bg: 'rgba(59, 130, 246, 0.15)', fg: '#3b82f6' },
};

interface CheckListItemProps {
  accent: Accent;
  children: ReactNode;
}

export default function CheckListItem({ accent, children }: CheckListItemProps) {
  const { bg, fg } = ACCENTS[accent];
  return (
    <li className="flex items-start gap-3 text-sm" style={{ color: 'var(--text-secondary)' }}>
      <span
        className="shrink-0 w-5 h-5 rounded-full flex items-center justify-center mt-0.5"
        style={{ background: bg, color: fg }}
      >
        <CheckIcon className="w-3 h-3" />
      </span>
      <span>{children}</span>
    </li>
  );
}
