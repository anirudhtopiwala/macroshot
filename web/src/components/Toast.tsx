import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { Check, X } from './icons';

interface ToastAction {
  label: string;
  onClick: () => void;
}

interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info';
  action?: ToastAction;
  persistent?: boolean;
}

interface ToastOptions {
  action?: ToastAction;
  persistent?: boolean;
}

interface ToastContextValue {
  toast: (message: string, type?: Toast['type'], actionOrOptions?: ToastAction | ToastOptions) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue>({ toast: () => 0, dismiss: () => {} });

export const useToast = () => useContext(ToastContext);

let nextId = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((message: string, type: Toast['type'] = 'success', actionOrOptions?: ToastAction | ToastOptions) => {
    const id = ++nextId;
    const action = actionOrOptions && 'label' in actionOrOptions ? actionOrOptions : actionOrOptions?.action;
    const persistent = actionOrOptions && 'label' in actionOrOptions ? false : actionOrOptions?.persistent;
    setToasts((prev) => [...prev, { id, message, type, action, persistent }]);
    return id;
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Bridge for module-scoped code that needs to surface a toast without
  // holding a useToast() reference (e.g., pendingMealDelete.ts firing
  // after the originating page has unmounted).
  useEffect(() => {
    const onAppToast = (e: Event) => {
      const detail = (e as CustomEvent<{ message: string; type?: Toast['type'] }>).detail;
      if (!detail?.message) return;
      toast(detail.message, detail.type ?? 'info');
    };
    window.addEventListener('app-toast', onAppToast);
    return () => window.removeEventListener('app-toast', onAppToast);
  }, [toast]);

  return (
    <ToastContext.Provider value={{ toast, dismiss }}>
      {children}
      <div className="fixed left-0 right-0 z-[70] flex flex-col items-center gap-2 pointer-events-none px-4" style={{ top: 'calc(3.75rem + env(safe-area-inset-top, 0px))' }}>
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast: t, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const touchStartRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const elRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (t.persistent) return;
    const ms = t.action ? 5000 : 3000;
    const timer = setTimeout(() => onDismiss(t.id), ms);
    return () => clearTimeout(timer);
  }, [t.id, t.action, t.persistent, onDismiss]);

  const handleTouchStart = (e: React.TouchEvent) => {
    touchStartRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, time: Date.now() };
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (!touchStartRef.current) return;
    const dx = e.changedTouches[0].clientX - touchStartRef.current.x;
    const dy = e.changedTouches[0].clientY - touchStartRef.current.y;
    // Swipe horizontally > 60px to dismiss
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 2) {
      if (elRef.current) {
        elRef.current.style.transition = 'transform 200ms ease-out, opacity 200ms ease-out';
        elRef.current.style.transform = `translateX(${dx > 0 ? '100%' : '-100%'})`;
        elRef.current.style.opacity = '0';
        setTimeout(() => onDismiss(t.id), 200);
      } else {
        onDismiss(t.id);
      }
    }
    touchStartRef.current = null;
  };

  const colors = {
    success: 'border-emerald-500/30 text-emerald-400',
    error: 'border-red-500/30 text-red-400',
    info: 'border-blue-500/30 text-blue-400',
  };

  const icons = {
    success: <Check className="w-4 h-4 text-emerald-400 shrink-0" />,
    error: <X className="w-4 h-4 text-red-400 shrink-0" />,
    info: <Check className="w-4 h-4 text-blue-400 shrink-0" />,
  };

  return (
    <div
      ref={elRef}
      className={`pointer-events-auto glass-card px-4 py-2.5 flex items-center gap-2 text-sm max-w-sm animate-slide-down ${colors[t.type]}`}
      onClick={() => onDismiss(t.id)}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {icons[t.type]}
      <span className="flex-1">{t.message}</span>
      {t.action && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            t.action!.onClick();
            onDismiss(t.id);
          }}
          className="font-bold text-emerald-400 ml-2 shrink-0"
        >
          {t.action.label}
        </button>
      )}
    </div>
  );
}
