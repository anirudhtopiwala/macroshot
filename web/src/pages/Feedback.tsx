import { useState } from 'react';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import { Lightbulb } from '../components/icons';
import { feedbackApi } from '../api/feedback';
import { hapticLight } from '../utils/haptics';
import { trackEvent } from '../utils/analytics';
import { useToast } from '../components/Toast';

type FeedbackType = 'bug' | 'feature' | 'other';

const TYPES: { value: FeedbackType; label: string }[] = [
  { value: 'bug', label: 'Bug Report' },
  { value: 'feature', label: 'Feature Idea' },
  { value: 'other', label: 'Other' },
];

const PLACEHOLDERS: Record<FeedbackType, string> = {
  bug: 'What went wrong?',
  feature: 'What would you love to see?',
  other: 'Tell us anything...',
};

export default function Feedback() {
  const { toast } = useToast();
  const [type, setType] = useState<FeedbackType>('feature');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async () => {
    if (!message.trim()) return;
    hapticLight();
    setSubmitting(true);
    try {
      await feedbackApi.submit({
        type,
        message: message.trim(),
        page: window.location.pathname,
      });
      trackEvent('feedback_sent', { type });
      setSubmitted(true);
    } catch {
      toast('Could not send feedback. Please try again.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleAnother = () => {
    hapticLight();
    setSubmitted(false);
    setMessage('');
    setType('feature');
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">Food for Thought</h1>
      </div>

      {submitted ? (
        <div className="glass-card p-6 text-center space-y-4">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center mx-auto"
            style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.2)' }}
          >
            <Lightbulb className="w-7 h-7" style={{ color: '#f59e0b' }} />
          </div>
          <div>
            <p className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
              Thanks for the food for thought!
            </p>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              We'll digest this shortly.
            </p>
          </div>
          <Button variant="secondary" className="w-full" onClick={handleAnother}>
            Send More Feedback
          </Button>
        </div>
      ) : (
        <div className="glass-card p-5 space-y-4">
          {/* Type selector */}
          <div>
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              What kind of feedback?
            </label>
            <div className="flex gap-1 mt-2 p-0.5 rounded-xl" style={{ background: 'var(--bg-elevated)' }}>
              {TYPES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => { hapticLight(); setType(t.value); }}
                  className={`flex-1 px-3 py-2 rounded-lg text-xs font-bold transition-all ${
                    type === t.value ? 'bg-amber-500/20 text-amber-400' : ''
                  }`}
                  style={type !== t.value ? { color: 'var(--text-muted)' } : undefined}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Message textarea */}
          <div>
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Your message
            </label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={PLACEHOLDERS[type]}
              maxLength={2000}
              rows={5}
              className="w-full glass-input mt-2 resize-none"
              style={{ minHeight: '120px' }}
            />
            <p className="text-[10px] mt-1 text-right" style={{ color: 'var(--text-muted)' }}>
              {message.length}/2000
            </p>
          </div>

          {/* Submit */}
          <Button
            variant="primary"
            className="w-full"
            onClick={handleSubmit}
            disabled={!message.trim() || submitting}
          >
            {submitting ? 'Sending...' : 'Send Feedback'}
          </Button>
        </div>
      )}
    </div>
  );
}
