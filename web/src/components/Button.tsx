import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'destructive' | 'accent';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

const SIZE_CLASSES: Record<Size, string> = {
  sm: 'text-xs py-2 px-3',
  md: 'text-sm py-3 px-5',
  lg: 'text-base py-3.5 px-6',
};

// Static class names so Tailwind/CSS can detect them
const VARIANT_CLASSES: Record<Variant, string> = {
  primary: 'glass-btn glass-btn-primary',
  secondary: 'glass-btn glass-btn-secondary',
  destructive: 'glass-btn glass-btn-destructive',
  accent: 'glass-btn glass-btn-accent',
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', className = '', children, ...props }, ref) => {
    const base = `${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} font-semibold rounded-xl transition-all duration-200 active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none flex items-center justify-center gap-2 focus-visible:ring-2 focus-visible:ring-emerald-500/50 focus-visible:outline-none`;
    return (
      <button ref={ref} className={`${base} ${className}`} {...props}>
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';
export default Button;
