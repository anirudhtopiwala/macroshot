import { useEffect, useState, useRef } from 'react';
import { Check, Bookmark, ThumbsUp, ThumbsDown, Send } from './icons';
import { feedbackApi } from '../api/feedback';

interface Props {
  onDone: () => void;
  onSaveAlias?: () => void;
  /** Disable the Save-as-Quick-Log button and show a hint in its place.
   *  Used when the user has hit the saved-meals cap. */
  saveAliasDisabledReason?: string | null;
  mealId?: number | null;
}

export default function Celebration({ onDone, onSaveAlias, saveAliasDisabledReason, mealId }: Props) {
  const [phase, setPhase] = useState<'show' | 'fading'>('show');
  const interactedRef = useRef(false);
  const innerTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const autoDismissRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Feedback state
  const [feedbackState, setFeedbackState] = useState<'idle' | 'thumbsDown' | 'sent'>('idle');
  const [comment, setComment] = useState('');
  const feedbackSentRef = useRef(false);

  // Auto-dismiss: 5s if feedback available, 2s otherwise
  const autoDismissMs = mealId ? 5000 : 2000;

  useEffect(() => {
    autoDismissRef.current = setTimeout(() => {
      if (!interactedRef.current) {
        setPhase('fading');
        innerTimerRef.current = setTimeout(onDone, 400);
      }
    }, autoDismissMs);
    return () => {
      clearTimeout(autoDismissRef.current);
      clearTimeout(innerTimerRef.current);
    };
  }, [onDone, autoDismissMs]);

  const fadeOut = () => {
    setPhase('fading');
    clearTimeout(autoDismissRef.current);
    clearTimeout(innerTimerRef.current);
    innerTimerRef.current = setTimeout(onDone, 400);
  };

  const handleSave = () => {
    interactedRef.current = true;
    onSaveAlias?.();
    fadeOut();
  };

  const handleDismiss = () => {
    interactedRef.current = true;
    fadeOut();
  };

  const sendFeedback = (rating: 0 | 1, feedbackComment?: string) => {
    if (!mealId || feedbackSentRef.current) return;
    feedbackSentRef.current = true;
    // Fire and forget - don't block the celebration flow
    feedbackApi.mealFeedback(mealId, rating, feedbackComment).catch(() => {});
  };

  const handleThumbsUp = () => {
    interactedRef.current = true;
    sendFeedback(1);
    setFeedbackState('sent');
    // Pause briefly to show "Thanks!" then fade out
    clearTimeout(autoDismissRef.current);
    clearTimeout(innerTimerRef.current);
    innerTimerRef.current = setTimeout(fadeOut, 800);
  };

  const handleThumbsDown = () => {
    interactedRef.current = true;
    // Extend auto-dismiss to give time to type comment
    clearTimeout(autoDismissRef.current);
    setFeedbackState('thumbsDown');
  };

  const handleSendComment = () => {
    sendFeedback(0, comment.trim() || undefined);
    setFeedbackState('sent');
    clearTimeout(innerTimerRef.current);
    innerTimerRef.current = setTimeout(fadeOut, 800);
  };

  const handleSkipComment = () => {
    sendFeedback(0);
    setFeedbackState('sent');
    clearTimeout(innerTimerRef.current);
    innerTimerRef.current = setTimeout(fadeOut, 800);
  };

  return (
    <div
      className={`celebration-overlay fixed inset-0 z-[80] flex items-center justify-center bg-black/50 backdrop-blur-sm animate-fade-in transition-opacity duration-400 ${
        phase === 'fading' ? 'opacity-0' : ''
      }`}
      onClick={handleDismiss}
    >
      <div
        className={`flex flex-col items-center gap-4 transition-all duration-400 ${
          phase === 'fading' ? 'scale-110 opacity-0' : 'scale-100 opacity-100'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="w-20 h-20 rounded-full flex items-center justify-center animate-[bounce_0.5s_ease-out]"
          style={{
            background: 'linear-gradient(135deg, #10b981, #059669)',
            boxShadow: '0 0 40px rgba(16,185,129,0.5), 0 0 80px rgba(16,185,129,0.2)',
          }}
        >
          <Check className="w-10 h-10 text-white" strokeWidth={3} />
        </div>
        <p className="text-lg font-bold text-white">Meal Logged! 🎉</p>

        {/* Accuracy feedback - just the two icons, no leading label */}
        {mealId && feedbackState === 'idle' && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-xl animate-fade-in"
            style={{
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.15)',
              backdropFilter: 'blur(12px)',
            }}
          >
            <button
              onClick={handleThumbsUp}
              className="p-1.5 rounded-lg transition-all active:scale-90 hover:bg-emerald-500/20"
              aria-label="Meal nutrition is accurate"
            >
              <ThumbsUp className="w-5 h-5 text-emerald-400" />
            </button>
            <button
              onClick={handleThumbsDown}
              className="p-1.5 rounded-lg transition-all active:scale-90 hover:bg-red-500/20"
              aria-label="Meal nutrition looks off"
            >
              <ThumbsDown className="w-5 h-5 text-red-400" />
            </button>
          </div>
        )}

        {/* Expanded comment input for thumbs down */}
        {mealId && feedbackState === 'thumbsDown' && (
          <div
            className="flex flex-col gap-2 px-4 py-3 rounded-xl animate-fade-in w-72"
            style={{
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.15)',
              backdropFilter: 'blur(12px)',
            }}
          >
            <span className="text-sm text-white/70">What was off?</span>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSendComment(); }}
                placeholder="e.g. calories too high"
                maxLength={500}
                autoFocus
                className="flex-1 px-3 py-2 rounded-lg text-sm outline-none placeholder:text-white/30"
                style={{
                  background: 'rgba(255,255,255,0.1)',
                  border: '1px solid rgba(255,255,255,0.15)',
                  color: '#fff',
                }}
              />
              <button
                onClick={handleSendComment}
                className="p-2 rounded-lg transition-all active:scale-90"
                style={{
                  background: 'rgba(239,68,68,0.2)',
                  border: '1px solid rgba(239,68,68,0.3)',
                }}
                aria-label="Send feedback"
              >
                <Send className="w-4 h-4 text-red-400" />
              </button>
            </div>
            <button
              onClick={handleSkipComment}
              className="text-xs text-white/40 hover:text-white/60 transition-colors self-end"
            >
              Skip
            </button>
          </div>
        )}

        {/* Sent confirmation */}
        {feedbackState === 'sent' && (
          <p className="text-sm text-emerald-300 animate-fade-in">Thanks for the feedback!</p>
        )}

        {onSaveAlias && feedbackState !== 'thumbsDown' && !saveAliasDisabledReason && (
          <button
            onClick={handleSave}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all active:scale-95"
            style={{
              background: 'rgba(255,255,255,0.1)',
              border: '1px solid rgba(255,255,255,0.2)',
              color: '#fff',
              backdropFilter: 'blur(12px)',
            }}
          >
            <Bookmark className="w-4 h-4" />
            Save as Quick Log
          </button>
        )}
        {onSaveAlias && feedbackState !== 'thumbsDown' && saveAliasDisabledReason && (
          <div
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs text-center"
            style={{
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.15)',
              color: 'rgba(255,255,255,0.8)',
              backdropFilter: 'blur(12px)',
            }}
          >
            <Bookmark className="w-3.5 h-3.5 opacity-70" />
            <span>{saveAliasDisabledReason}</span>
          </div>
        )}
      </div>
    </div>
  );
}
