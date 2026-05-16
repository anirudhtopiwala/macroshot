import type { ReactNode } from 'react';

interface Props {
  variant: 'error' | 'warning' | 'info';
  children: ReactNode;
  className?: string;
}

const VARIANT_CLASSES: Record<Props['variant'], string> = {
  error: 'alert-error',
  warning: 'alert-warning',
  info: 'alert-info',
};

export default function Alert({ variant, children, className = '' }: Props) {
  return (
    <div className={`${VARIANT_CLASSES[variant]} rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${className}`}>
      {children}
    </div>
  );
}
