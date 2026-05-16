import { useEffect, useState, useRef } from 'react';
import { hapticLight } from '../utils/haptics';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { setCapturedFiles } from '../utils/capturedImage';
import { track } from '../api/analytics';

interface Props {
  onClose: () => void;
  onCamera: (files: File[]) => void;
  onBarcode: () => void;
  onText: () => void;
  onWeight: () => void;
  onChat: () => void;
  onCopyDay: () => void;
}

const OPTIONS = [
  { key: 'camera', emoji: '📸', label: 'Camera', sub: 'Snap a meal photo', color: '#10b981' },
  { key: 'text', emoji: '💬', label: 'Text', sub: 'Just say what you ate', color: '#3b82f6' },
  { key: 'barcode', emoji: '🏷️', label: 'Barcode', sub: 'Scan a product barcode', color: '#06b6d4' },
  { key: 'chat', emoji: '✨', label: 'Ask AI', sub: 'Chat with your coach', color: '#8b5cf6' },
  { key: 'copyday', emoji: '⚡', label: 'Quick Add', sub: 'Recent & Saved', color: '#ec4899' },
  { key: 'weight', emoji: '⚖️', label: 'Weight', sub: 'Log your weight', color: '#f59e0b' },
] as const;

export default function FABMenu({ onClose, onCamera, onBarcode, onText, onWeight, onChat, onCopyDay }: Props) {
  useOverlayHistory(onClose);
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const handleClose = () => {
    setClosing(true);
    setOpen(false);
    setTimeout(onClose, 220);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const fileArr = Array.from(files);
      setCapturedFiles(fileArr);
      onCamera(fileArr);
    }
  };

  const handlers: Record<string, () => void> = {
    camera: () => fileRef.current?.click(),
    barcode: onBarcode,
    text: onText,
    weight: onWeight,
    chat: onChat,
    copyday: onCopyDay,
  };

  const show = open && !closing;

  return (
    <div className="fixed inset-0 z-[60]">
      {/* Backdrop */}
      <div
        className="absolute inset-0"
        style={{
          background: 'rgba(0,0,0,0.55)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          opacity: show ? 1 : 0,
          transition: 'opacity 200ms cubic-bezier(0.4, 0, 0.2, 1)',
        }}
        onClick={handleClose}
      />

      {/* Hidden file input for camera */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={handleFileChange}
        className="hidden"
      />

      {/* Full-width 2x2 grid */}
      <div
        className="absolute bottom-[88px] left-3 right-3"
        style={{
          transform: show ? 'translateY(0) scale(1)' : 'translateY(40px) scale(0.92)',
          opacity: show ? 1 : 0,
          transformOrigin: 'bottom center',
          transition: 'transform 420ms cubic-bezier(0.32, 0.72, 0, 1), opacity 300ms ease',
        }}
      >
        <div className="max-w-lg mx-auto">
          {/* Top row: 3 items */}
          <div className="grid grid-cols-3 gap-2.5 mb-2.5">
            {OPTIONS.slice(0, 3).map((opt, i) => (
              <button
                key={opt.key}
                onClick={() => {
                  hapticLight();
                  track('ui_fab_action', { action: opt.key });
                  handlers[opt.key]();
                }}
                className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                style={{
                  background: `linear-gradient(145deg, ${opt.color}0C 0%, var(--bg-elevated) 50%, ${opt.color}06 100%)`,
                  border: `1px solid ${opt.color}20`,
                  boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 var(--border-glass), 0 0 40px ${opt.color}10`,
                  backdropFilter: 'blur(32px)',
                  WebkitBackdropFilter: 'blur(32px)',
                  opacity: show ? 1 : 0,
                  transform: show ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.9)',
                  transition: `opacity 200ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1), transform 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1)`,
                }}
              >
                <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0" style={{ background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`, border: `1px solid ${opt.color}35`, boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20` }}>
                  {opt.emoji}
                </div>
                <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{opt.label}</span>
                <span className="text-[10px] leading-tight mt-0.5" style={{ color: 'var(--text-secondary)' }}>{opt.sub}</span>
              </button>
            ))}
          </div>
          {/* Bottom row: 3 items */}
          <div className="grid grid-cols-3 gap-2.5">
            {OPTIONS.slice(3).map((opt, i) => (
              <button
                key={opt.key}
                onClick={() => {
                  hapticLight();
                  track('ui_fab_action', { action: opt.key });
                  handlers[opt.key]();
                }}
                className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                style={{
                  background: `linear-gradient(145deg, ${opt.color}0C 0%, var(--bg-elevated) 50%, ${opt.color}06 100%)`,
                  border: `1px solid ${opt.color}20`,
                  boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 var(--border-glass), 0 0 40px ${opt.color}10`,
                  backdropFilter: 'blur(32px)',
                  WebkitBackdropFilter: 'blur(32px)',
                  opacity: show ? 1 : 0,
                  transform: show ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.9)',
                  transition: `opacity 200ms ${130 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1), transform 280ms ${130 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1)`,
                }}
              >
                <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0" style={{ background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`, border: `1px solid ${opt.color}35`, boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20` }}>
                  {opt.emoji}
                </div>
                <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{opt.label}</span>
                <span className="text-[10px] leading-tight mt-0.5" style={{ color: 'var(--text-secondary)' }}>{opt.sub}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
