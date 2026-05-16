import { useEffect, useRef, useState } from 'react';
import { Download, Check } from 'lucide-react';
import Modal from './Modal';
import LoadingSpinner from './LoadingSpinner';

export type UpdateProgressPhase = 'checking' | 'downloading' | 'applying' | 'uptodate' | 'error';

interface Props {
  open: boolean;
  phase: UpdateProgressPhase;
  /** Optional: real precache progress hint. Used to *bump* the bar forward
   * faster than the time-based pacing when many fresh files are arriving;
   * never used to cap or floor it. The user only sees the bar. */
  done?: number;
  total?: number;
  errorMessage?: string;
  onClose: () => void;
}

/**
 * Modal walking the user through the explicit "Check for Updates" flow.
 *
 * Progress strategy: we don't tie the bar to a literal file count. The
 * underlying signal (Workbox cacheDidUpdate) only fires for *fresh* fetches,
 * so a small incremental update would otherwise leave the bar stalled at
 * a fraction of the total. Instead, the bar paces forward over ~30s to
 * reach 92%, with real-progress events bumping it faster when available.
 * It snaps to 100% the moment the SW reaches `installed` (phase=applying).
 */
const PACING_MS = 30_000;
const PACING_MAX = 0.92;

export default function UpdateProgressCard({
  open,
  phase,
  done = 0,
  total = 0,
  errorMessage,
  onClose,
}: Props) {
  const [displayProgress, setDisplayProgress] = useState(0);
  const downloadStartRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const phaseRef = useRef(phase);
  const doneRef = useRef(done);
  const totalRef = useRef(total);

  // Keep refs in sync so the rAF loop reads the latest values without
  // tearing down/restarting on every prop change.
  useEffect(() => { phaseRef.current = phase; }, [phase]);
  useEffect(() => { doneRef.current = done; }, [done]);
  useEffect(() => { totalRef.current = total; }, [total]);

  // Reset paced timer at the start of a fresh download phase.
  useEffect(() => {
    if (phase === 'downloading' && downloadStartRef.current === null) {
      downloadStartRef.current = performance.now();
    }
    if (phase === 'checking' || phase === 'uptodate' || phase === 'error') {
      // Allow the next downloading phase to start its timer cleanly.
      if (phase !== 'checking') downloadStartRef.current = null;
    }
  }, [phase]);

  // Reset bar position when the modal opens fresh.
  useEffect(() => {
    if (open && phase === 'checking') {
      setDisplayProgress(0);
      downloadStartRef.current = null;
    }
  }, [open, phase]);

  useEffect(() => {
    if (!open) return;

    const tick = () => {
      const p = phaseRef.current;
      let target: number;
      if (p === 'checking') {
        target = 0.08;
      } else if (p === 'downloading') {
        if (downloadStartRef.current === null) {
          downloadStartRef.current = performance.now();
        }
        const elapsed = performance.now() - downloadStartRef.current;
        const timed = Math.min(PACING_MAX, elapsed / PACING_MS);
        const real =
          totalRef.current > 0
            ? Math.min(PACING_MAX, doneRef.current / totalRef.current)
            : 0;
        target = Math.max(timed, real);
      } else if (p === 'applying' || p === 'uptodate') {
        target = 1;
      } else {
        target = 0;
      }

      setDisplayProgress((prev) => {
        const next = prev + (target - prev) * 0.12; // gentle ease
        // Snap when very close so we don't asymptote forever.
        return Math.abs(target - next) < 0.001 ? target : next;
      });

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [open]);

  const dismissable = phase === 'uptodate' || phase === 'error';

  const title =
    phase === 'checking'
      ? 'Checking for updates'
      : phase === 'downloading'
        ? 'Updating app'
        : phase === 'applying'
          ? 'Finishing up'
          : phase === 'uptodate'
            ? "You're on the latest version"
            : 'Update failed';

  const subtitle =
    phase === 'checking'
      ? 'Looking for a new version'
      : phase === 'downloading'
        ? 'Adding new features and improvements'
        : phase === 'applying'
          ? 'Reloading shortly'
          : phase === 'uptodate'
            ? 'You have the newest version.'
            : (errorMessage ?? 'Something went wrong. Try again in a moment.');

  return (
    <Modal open={open} onClose={dismissable ? onClose : () => {}}>
      <div className="px-6 py-7 flex flex-col items-center text-center">
        <div
          className="w-14 h-14 rounded-full flex items-center justify-center mb-4"
          style={{ background: 'rgba(16, 185, 129, 0.15)', border: '1px solid rgba(16, 185, 129, 0.3)' }}
        >
          {phase === 'uptodate' ? (
            <Check className="w-6 h-6" style={{ color: '#10b981' }} />
          ) : phase === 'error' ? (
            <Download className="w-6 h-6" style={{ color: '#ef4444' }} />
          ) : phase === 'applying' ? (
            <LoadingSpinner size="md" />
          ) : phase === 'downloading' ? (
            <Download className="w-6 h-6" style={{ color: '#10b981' }} />
          ) : (
            <LoadingSpinner size="md" />
          )}
        </div>

        <p className="text-base font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
          {title}
        </p>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {subtitle}
        </p>

        {(phase === 'checking' || phase === 'downloading' || phase === 'applying') && (
          <div
            className="mt-5 w-full h-1.5 rounded-full overflow-hidden"
            style={{ background: 'rgba(148, 163, 184, 0.18)' }}
            role="progressbar"
            aria-valuenow={Math.round(displayProgress * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${displayProgress * 100}%`,
                background: 'linear-gradient(90deg, #10b981, #34d399)',
              }}
            />
          </div>
        )}

        {dismissable && (
          <button
            onClick={onClose}
            className="mt-5 w-full py-2.5 rounded-xl text-sm font-semibold"
            style={{
              background: 'rgba(148, 163, 184, 0.12)',
              border: '1px solid var(--border-glass)',
              color: 'var(--text-primary)',
            }}
          >
            OK
          </button>
        )}
      </div>
    </Modal>
  );
}
