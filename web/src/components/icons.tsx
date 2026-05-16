import { Utensils as UtensilsIcon } from 'lucide-react';

// Lucide icon re-exports
export {
  Home,
  Camera,
  ClipboardList,
  Settings,
  Plus,
  Send,
  ArrowLeft,
  Trash2,
  Search,
  Flame,
  Check,
  X,
  ChevronRight,
  ChevronDown,
  TrendingUp,
  TrendingDown,
  Minus,
  Target,
  User,
  LogOut,
  Pencil,
  Utensils,
  Sun,
  Moon,
  LayoutGrid,
  List,
  Image,
  Sparkles,
  Bookmark,
  Bell,
  Scale,
  Type,
  Trophy,
  Shield,
  Zap,
  Lightbulb,
  ThumbsUp,
  ThumbsDown,
  CircleCheck,
  Circle,
  Clock,
  Download,
  FileText,
  RefreshCw,
  Lock,
  Heart,
} from 'lucide-react';

// Custom meal-type SVG icons
export function BreakfastIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2v4M4.93 4.93l2.83 2.83M2 12h4M4.93 19.07l2.83-2.83" />
      <path d="M12 10a6 6 0 0 1 6 6H6a6 6 0 0 1 6-6z" />
      <line x1="4" y1="22" x2="20" y2="22" />
    </svg>
  );
}

export function LunchIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

export function SnackIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a5 5 0 0 1 5 5c0 2-1 3-2 4l-1 8H10l-1-8c-1-1-2-2-2-4a5 5 0 0 1 5-5z" />
      <path d="M10 19h4" />
      <path d="M10 22h4" />
    </svg>
  );
}

export function DinnerIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

// Map meal type string to icon component
export function MealTypeIcon({ type, className = 'w-5 h-5' }: { type: string; className?: string }) {
  switch (type) {
    case 'breakfast': return <BreakfastIcon className={className} />;
    case 'lunch': return <LunchIcon className={className} />;
    case 'snack': return <SnackIcon className={className} />;
    case 'dinner': return <DinnerIcon className={className} />;
    default: return <UtensilsIcon className={className} />;
  }
}
