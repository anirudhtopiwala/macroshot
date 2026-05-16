import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  action?: ReactNode;
  /** Use a glass-card wrapper with p-12 (default). Set false for inline usage. */
  card?: boolean;
}

export default function EmptyState({ icon, title, subtitle, action, card = true }: EmptyStateProps) {
  const content = (
    <>
      {icon && (
        <div className="flex justify-center gap-3 mb-3" style={{ color: 'var(--text-muted)' }}>
          {icon}
        </div>
      )}
      <p className="font-medium" style={{ color: 'var(--text-secondary)' }}>{title}</p>
      {subtitle && (
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>{subtitle}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </>
  );

  if (!card) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-center">
        {content}
      </div>
    );
  }

  return (
    <div className="glass-card p-12 text-center">
      {content}
    </div>
  );
}
