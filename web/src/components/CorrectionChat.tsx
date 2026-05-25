import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Sparkles } from './icons';
import UpgradeCard from './UpgradeCard';
import { type QuickAction } from '../utils/quickActions';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  /** When true on an assistant message, render a "Macros updated" pill
   *  below the bubble so the user knows to scroll up to the totals. */
  macrosUpdated?: boolean;
  /** When true on an assistant message, the model replied but did not
   *  propose new numbers - show a small inline hint so the user knows
   *  why the totals above didn't move. */
  noUpdateHint?: boolean;
}

interface CorrectionLimitInfo {
  used: number;
  /** Per-meal correction cap (-1 means unlimited; 0 or missing means "skip"). */
  limit: number;
  /** Kept for backward-compat with pre-beta call sites. Ignored by the
   *  gate logic - we always show the counter when `limit > 0`. */
  isPremium: boolean;
}

interface Props {
  messages: Message[];
  /** Gemini follow-up questions to display near the input */
  questions?: string[];
  /** Quick-action chips shown before first correction */
  quickActions?: QuickAction[];
  onSend: (text: string) => void;
  onQuickAction?: (action: QuickAction) => void;
  /** Reprocess callback - distinct from quick actions */
  onReprocess?: () => void;
  disabled?: boolean;
  placeholder?: string;
  /** Use a taller message area for full-chat UX */
  expanded?: boolean;
  /** Purple AI-themed styling for the input */
  aiTheme?: boolean;
  /** Correction limit info for free users */
  correctionLimit?: CorrectionLimitInfo;
  /** Tap handler for the "Macros updated" pill - scrolls the nutrition
   *  card into view. Provided by MealEditor so the scroll target stays
   *  owned by the parent that renders NutritionTable. */
  onScrollToMacros?: () => void;
  /** Called when the user taps an `item_fix` chip. Parent opens the
   *  per-item +/- bottom sheet for that index. */
  onItemFixRequest?: (itemIndex: number) => void;
}

function MacrosUpdatedPill({ onTap, isLatest }: { onTap?: () => void; isLatest: boolean }) {
  // Pulse only on the latest assistant reply that actually changed the
  // macros. Older pills stay clickable but drop the animation so the
  // chat doesn't look like a carnival.
  return (
    <button
      type="button"
      onClick={onTap}
      className={`mt-1.5 inline-flex items-center gap-1 text-[11px] font-semibold py-1 px-2.5 rounded-full transition-all active:scale-95 ${
        isLatest ? 'macros-updated-pulse' : ''
      }`}
      style={{
        background: 'rgba(16,185,129,0.15)',
        border: '1px solid rgba(16,185,129,0.4)',
        color: '#10b981',
        cursor: onTap ? 'pointer' : 'default',
      }}
      aria-label="Macros updated - tap to view"
    >
      <span aria-hidden="true">↑</span>
      <span>Macros updated - tap to view</span>
    </button>
  );
}

function ThinkingBubble() {
  // Three dots that fade in/out staggered. Pure CSS (no animation library).
  // Only rendered while a correction is in flight so the chat doesn't look
  // frozen during the ~4s Gemini call.
  return (
    <div className="flex flex-col items-start" aria-live="polite" aria-label="AI is typing">
      <div
        className="text-sm rounded-xl px-3 py-2 glass-card inline-flex items-center gap-1"
        style={{ color: 'var(--text-secondary)' }}
      >
        <span className="thinking-dot" style={{ animationDelay: '0ms' }}>•</span>
        <span className="thinking-dot" style={{ animationDelay: '160ms' }}>•</span>
        <span className="thinking-dot" style={{ animationDelay: '320ms' }}>•</span>
      </div>
    </div>
  );
}

function ChatBubble({
  message,
  onScrollToMacros,
  isLatestAssistant,
}: {
  message: Message;
  onScrollToMacros?: () => void;
  isLatestAssistant: boolean;
}) {
  const [showFull, setShowFull] = useState(false);
  const isLong = message.text.length > 300;
  const displayText = isLong && !showFull ? message.text.slice(0, 300) + '...' : message.text;

  return (
    <div className={message.role === 'user' ? 'flex flex-col items-end' : 'flex flex-col items-start'}>
      <div
        className={`text-sm rounded-xl px-3 py-2 max-w-[85%] break-words whitespace-pre-wrap ${
          message.role === 'user'
            ? 'bg-emerald-600/90 text-white'
            : 'glass-card'
        }`}
        style={message.role === 'assistant' ? { color: 'var(--text-primary)' } : undefined}
      >
        {displayText}
        {isLong && (
          <button
            onClick={() => setShowFull(!showFull)}
            className="block text-[11px] font-semibold mt-1 opacity-70 hover:opacity-100"
          >
            {showFull ? 'Show less' : 'Show more'}
          </button>
        )}
      </div>
      {message.role === 'assistant' && message.macrosUpdated && (
        <MacrosUpdatedPill onTap={onScrollToMacros} isLatest={isLatestAssistant} />
      )}
      {message.role === 'assistant' && message.noUpdateHint && (
        <p
          className="mt-1.5 text-[11px] italic max-w-[85%]"
          style={{ color: 'var(--text-secondary)' }}
        >
          The numbers above didn't change. Try a more specific change, e.g. "set protein to 200g" or "I'm training for a marathon".
        </p>
      )}
    </div>
  );
}

export default function CorrectionChat({ messages, questions, quickActions, onSend, onQuickAction, onReprocess, disabled, placeholder = 'Correct the estimate...', expanded, aiTheme, correctionLimit, onScrollToMacros, onItemFixRequest }: Props) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  // Auto-scroll to bottom when new messages arrive OR the typing indicator
  // turns on so the dots are visible without a user scroll.
  useEffect(() => {
    if (messages.length === 0 && !disabled) return;
    const timer = setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'nearest' });
    }, 50);
    return () => clearTimeout(timer);
  }, [messages.length, disabled]);

  // Auto-resize textarea without causing page scroll on iOS.
  //
  // Problem: setting height='auto' collapses the textarea → layout reflow → iOS
  // Safari adjusts the visual viewport scroll position asynchronously. The
  // window.scrollY save/restore trick doesn't work because iOS applies the scroll
  // change in a separate compositor pass AFTER JS finishes.
  //
  // Fix: measure scrollHeight without a visible collapse. We clone the textarea's
  // critical styles into an off-screen sizer div, set its content, and read
  // scrollHeight from that. The real textarea's height is then set in one step
  // with no intermediate collapse.
  const sizerRef = useRef<HTMLDivElement | null>(null);
  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;

    // Create or reuse an off-screen sizer div
    if (!sizerRef.current) {
      const sizer = document.createElement('div');
      sizer.setAttribute('aria-hidden', 'true');
      Object.assign(sizer.style, {
        position: 'fixed',
        top: '-9999px',
        left: '-9999px',
        visibility: 'hidden',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflow: 'hidden',
      });
      document.body.appendChild(sizer);
      sizerRef.current = sizer;
    }

    const sizer = sizerRef.current;
    const cs = getComputedStyle(el);
    // Mirror the textarea's sizing properties
    sizer.style.width = cs.width;
    sizer.style.fontSize = cs.fontSize;
    sizer.style.fontFamily = cs.fontFamily;
    sizer.style.lineHeight = cs.lineHeight;
    sizer.style.letterSpacing = cs.letterSpacing;
    sizer.style.paddingTop = cs.paddingTop;
    sizer.style.paddingBottom = cs.paddingBottom;
    sizer.style.paddingLeft = cs.paddingLeft;
    sizer.style.paddingRight = cs.paddingRight;
    sizer.style.borderStyle = cs.borderStyle;
    sizer.style.borderWidth = cs.borderWidth;
    sizer.style.boxSizing = cs.boxSizing;

    // Set content - add a trailing newline so a line ending with \n measures correctly
    sizer.textContent = el.value + '\n';

    const newHeight = Math.max(44, Math.min(sizer.scrollHeight, 120));
    el.style.height = newHeight + 'px';
  }, []);

  // Clean up the sizer div on unmount
  useEffect(() => {
    return () => {
      if (sizerRef.current) {
        document.body.removeChild(sizerRef.current);
        sizerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [input, resizeTextarea]);

  // iOS keyboard handling: scroll the input into view only when the keyboard
  // opens (large viewport height change), not on minor resize events (predictive
  // text bar, autocomplete suggestions). Firing scrollIntoView on every resize
  // event causes scroll jitter during typing.
  const lastVVHeight = useRef<number>(0);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    lastVVHeight.current = vv.height;

    const handleResize = () => {
      const delta = Math.abs(vv.height - lastVVHeight.current);
      lastVVHeight.current = vv.height;
      // Only react to significant viewport changes (>100px = keyboard open/close),
      // not minor ones (predictive text bar, autocomplete popups).
      if (delta > 100 && document.activeElement === textareaRef.current) {
        formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      }
    };

    vv.addEventListener('resize', handleResize);
    return () => vv.removeEventListener('resize', handleResize);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || disabled) return;
    onSend(input.trim());
    setInput('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  // 240px (max-h-60) is too small for 3+ messages - users can't tell it's scrollable
  // and accidentally scroll the page instead. Use 40dvh for a more usable chat area.
  const maxH = expanded ? 'max-h-[50dvh]' : 'max-h-[40dvh]';

  // Only pulse the pill on the most recent assistant reply - older pills
  // stay tappable but shouldn't compete for attention.
  let lastAssistantIdx = -1;
  for (let k = messages.length - 1; k >= 0; k--) {
    if (messages[k].role === 'assistant') { lastAssistantIdx = k; break; }
  }

  const hasQuestions = questions && questions.length > 0;
  // Show the cap counter / limit-reached UI whenever the backend has
  // communicated a finite per-meal cap (> 0). This covers beta Pros,
  // post-beta free, and post-beta Pros - isPremium is no longer
  // relevant because the server enforces the same per-meal cap on
  // everyone in hosted mode.
  const hasLimit = !!correctionLimit && correctionLimit.limit > 0;
  const limitReached =
    hasLimit && correctionLimit!.used >= correctionLimit!.limit;

  return (
    <div className="flex flex-col">
      {(messages.length > 0 || (disabled && messages.length === 0)) && (
        <div
          className={`space-y-2 ${maxH} overflow-y-auto mb-3 scroll-smooth rounded-xl p-2`}
          style={{
            overscrollBehavior: 'contain',
            border: '1px solid var(--border-glass)',
            background: 'var(--bg-elevated)',
          }}
        >
          {messages.map((m, i) => (
            <ChatBubble
              key={i}
              message={m}
              onScrollToMacros={onScrollToMacros}
              isLatestAssistant={i === lastAssistantIdx}
            />
          ))}
          {disabled && (messages.length === 0 || messages[messages.length - 1].role === 'user') && (
            <ThinkingBubble />
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      {/* Questions - shown just above the input so user can see them while typing */}
      {hasQuestions && (
        <div className="mb-2 p-2.5 rounded-xl" style={{ background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)' }}>
          <p className="text-blue-400 text-xs font-medium mb-1">Quick questions:</p>
          {questions.map((q, i) => (
            <p key={i} className="text-xs" style={{ color: 'var(--text-secondary)' }}>• {q}</p>
          ))}
        </div>
      )}

      {/* Quick action chips + Reprocess - persist across the whole correction
          session so the user can keep using shortcut nudges after the AI
          replies, not just before the first turn. */}
      {(quickActions?.length || onReprocess) && !disabled && !limitReached && (
        <div className="flex gap-1.5 flex-wrap mb-2">
          {quickActions?.map((action, i) => {
            const isItemFix = action.kind === 'item_fix';
            return (
              <button
                key={i}
                onClick={() => {
                  if (action.kind === 'item_fix') {
                    onItemFixRequest?.(action.itemIndex);
                  } else {
                    onQuickAction?.(action);
                  }
                }}
                className="text-xs font-medium py-1.5 px-3 rounded-full transition-all active:scale-95"
                style={{
                  background: isItemFix ? 'rgba(168,85,247,0.08)' : 'var(--bg-elevated)',
                  border: isItemFix ? '1px solid rgba(168,85,247,0.3)' : '1px solid var(--border-glass)',
                  color: isItemFix ? '#a855f7' : 'var(--text-secondary)',
                }}
              >
                {isItemFix ? '✏️ ' : ''}{action.label}
              </button>
            );
          })}
          {onReprocess && (
            <button
              onClick={onReprocess}
              className="text-xs font-medium py-1.5 px-3 rounded-full transition-all active:scale-95"
              style={{ background: 'transparent', border: '1px dashed var(--border-glass)', color: 'var(--text-muted)' }}
            >
              Reprocess
            </button>
          )}
        </div>
      )}

      {aiTheme && messages.length === 0 && !hasQuestions && !(quickActions?.length) && !limitReached && (
        <div className="flex items-center gap-2 mb-2 px-1">
          <Sparkles className="w-3.5 h-3.5 shrink-0" style={{ color: '#a855f7' }} />
          <span className="text-xs font-medium" style={{ color: '#a855f7' }}>Edit with AI - describe changes in plain text</span>
        </div>
      )}

      {/* Per-meal correction counter. Hidden until the user is halfway
          through their per-meal cap (>=50%), then surfaced so they can
          pace the remaining edits. At-limit state is handled by the
          UpgradeCard branch below. */}
      {hasLimit && !limitReached && correctionLimit!.used >= Math.ceil(correctionLimit!.limit / 2) && (
        <div className="flex items-center gap-1.5 mb-2 px-1">
          <span
            className="text-[11px] font-medium"
            style={{
              color:
                correctionLimit!.used >= correctionLimit!.limit - 1
                  ? '#f59e0b'
                  : 'var(--text-muted)',
            }}
          >
            {correctionLimit!.used}/{correctionLimit!.limit} corrections used
          </span>
        </div>
      )}

      {/* Upgrade card when the per-meal correction cap is hit. Pass
          feature="meal_edit" so UpgradeCard picks the right beta copy
          instead of the legacy photo-scan wording. */}
      {limitReached && (
        <UpgradeCard feature="meal_edit" />
      )}

      {!limitReached && <form
        ref={formRef}
        onSubmit={handleSubmit}
        className="flex gap-2 items-end"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            // Ensure input stays visible when iOS keyboard opens - only scroll
            // if the input is actually below the visible viewport
            setTimeout(() => {
              const el = formRef.current;
              if (!el) return;
              const rect = el.getBoundingClientRect();
              const vh = window.visualViewport?.height ?? window.innerHeight;
              if (rect.bottom > vh) {
                el.scrollIntoView({ behavior: 'smooth', block: 'end' });
              }
            }, 300);
          }}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="flex-1 text-sm py-2.5 resize-none overflow-hidden rounded-xl px-3"
          style={{
            minHeight: '44px',
            maxHeight: '120px',
            background: aiTheme ? 'rgba(168,85,247,0.08)' : 'var(--input-bg, var(--bg-elevated))',
            border: aiTheme ? '1.5px solid rgba(168,85,247,0.35)' : '1px solid var(--border-glass)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="submit"
          disabled={disabled || !input.trim()}
          className="disabled:opacity-50 px-4 py-3 rounded-xl text-sm font-medium transition-all shrink-0"
          style={{
            background: aiTheme ? 'rgba(168,85,247,0.15)' : 'rgba(16,185,129,0.15)',
            border: aiTheme ? '1px solid rgba(168,85,247,0.3)' : '1px solid rgba(16,185,129,0.3)',
            color: aiTheme ? '#a855f7' : '#10b981',
            boxShadow: aiTheme ? '0 0 12px rgba(168,85,247,0.15)' : '0 0 10px rgba(16,185,129,0.1)',
            minHeight: '44px',
            minWidth: '44px',
          }}
        >
          <Send className="w-5 h-5" />
        </button>
      </form>}
    </div>
  );
}
