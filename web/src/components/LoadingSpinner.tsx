interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  fullPage?: boolean;
  className?: string;
}

const sizeClasses = {
  sm: 'w-4 h-4',
  md: 'w-8 h-8',
  lg: 'w-10 h-10',
};

const SHIMMER = 'rounded-xl bg-[length:200%_100%] animate-shimmer dark:bg-gradient-to-r dark:from-white/[0.04] dark:via-white/[0.08] dark:to-white/[0.04] bg-gradient-to-r from-black/[0.03] via-black/[0.06] to-black/[0.03]';

export default function LoadingSpinner({ size = 'md', fullPage = false, className = '' }: LoadingSpinnerProps) {
  const spinner = (
    <div className={`animate-spin ${sizeClasses[size]} border-2 border-emerald-500 border-t-transparent rounded-full ${className}`} />
  );

  if (fullPage) {
    return (
      <div className="space-y-4">
        <div className={`${SHIMMER} h-8 w-40`} />
        <div className={`${SHIMMER} h-20 !rounded-2xl`} />
        <div className={`${SHIMMER} h-20 !rounded-2xl`} />
        <div className={`${SHIMMER} h-20 !rounded-2xl`} />
        <div className={`${SHIMMER} h-20 !rounded-2xl`} />
      </div>
    );
  }

  return spinner;
}
