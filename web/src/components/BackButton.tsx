import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from './icons';

interface BackButtonProps {
  /** If set, navigate here instead of going back when there's no history */
  fallbackPath?: string;
  /** Show "Back" label next to the icon (default: false - icon only) */
  label?: boolean;
  /** aria-label override (default: "Back") */
  ariaLabel?: string;
}

export default function BackButton({ fallbackPath, label, ariaLabel }: BackButtonProps) {
  const navigate = useNavigate();

  const handleClick = () => {
    // history.length > 1 means there's a real page to go back to
    // (initial page load in a new tab still has length === 1)
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate(fallbackPath || '/');
    }
  };

  if (label) {
    return (
      <button
        onClick={handleClick}
        className="flex items-center gap-1.5 py-3 px-4 -mx-4 text-sm font-medium rounded-xl active:scale-95 transition-all focus-visible:ring-2 focus-visible:ring-emerald-500/50 focus-visible:outline-none"
        style={{ color: 'var(--text-secondary)' }}
        aria-label={ariaLabel || 'Back'}
      >
        <ArrowLeft className="w-4 h-4" />
        Back
      </button>
    );
  }

  return (
    <button
      onClick={handleClick}
      className="p-2 -ml-2 rounded-xl active:scale-95 focus-visible:ring-2 focus-visible:ring-emerald-500/50 focus-visible:outline-none"
      aria-label={ariaLabel || 'Back'}
    >
      <ArrowLeft className="w-5 h-5" style={{ color: 'var(--text-secondary)' }} />
    </button>
  );
}
