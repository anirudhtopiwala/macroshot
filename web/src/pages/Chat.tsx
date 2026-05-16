import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Send, Sparkles, Trash2, Plus, Camera, X } from '../components/icons';
import useOverlayHistory from '../hooks/useOverlayHistory';
import Button from '../components/Button';
import BackButton from '../components/BackButton';
import LoadingSpinner from '../components/LoadingSpinner';
import BadgeCelebration from '../components/BadgeCelebration';
import { api } from '../api/client';
import { useToast } from '../components/Toast';
import { _ALLOWED_PENDING_TOOLS, type PendingActionTool } from './chatPendingTools';
import { clearCache } from '../utils/apiCache';
import { hapticLight, hapticSuccess } from '../utils/haptics';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
// B16: only allow http(s): links and force noopener/noreferrer + target=_blank.
// Custom anchor renderer used by ReactMarkdown's `components.a` slot.
type SafeAnchorProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & { children?: React.ReactNode };
function SafeMarkdownAnchor({ href, children, ...rest }: SafeAnchorProps) {
  let safe = '';
  try {
    if (typeof href === 'string') {
      const u = new URL(href, window.location.href);
      if (u.protocol === 'http:' || u.protocol === 'https:') {
        safe = u.toString();
      }
    }
  } catch {
    // ignore - leave safe empty
  }
  if (!safe) {
    return <span {...rest}>{children}</span>;
  }
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
import { useSubscription } from '../context/SubscriptionContext';
import { useCapState } from '../hooks/useCapState';
import UpgradeCard from '../components/UpgradeCard';
import UsageMeter from '../components/UsageMeter';
import type { ChatSession, ChatMessage, ChatCreateResponse, ChatHistoryResponse, NewBadge } from '../types';

const SUGGESTIONS = [
  'Was my breakfast healthy?',
  'Am I on track today?',
  'What should I eat for dinner?',
  'How has my diet been this week?',
];

// Per-session cap on uploaded images, summed across every user turn. Mirrors
// MAX_CHAT_IMAGES_PER_SESSION on the backend; the server is authoritative
// (returns 400 if exceeded), this just hides the camera button proactively.
const MAX_CHAT_IMAGES_PER_SESSION = 3;

const IMG_BASE = '/macro_app/api/v1/images/';

/** Build the URL for a stored chat image path. Returns the path as-is when
 *  it's a local blob: URL (optimistic preview before the next history reload). */
function imageUrlFor(path: string): string {
  return path.startsWith('blob:') ? path : IMG_BASE + path;
}

/** Compress an image File via canvas. Mirrors ImageCapture.tsx's compressor —
 *  duplicated rather than exported because the chat input attaches images
 *  inline (no shared image-grid component to slot in). */
async function compressChatImage(file: File): Promise<File> {
  if (file.size < 500 * 1024) return file;
  const MAX_DIMENSION = 1280;
  const JPEG_QUALITY = 0.8;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (out: File) => { if (!settled) { settled = true; resolve(out); } };
    const watchdog = setTimeout(() => finish(file), 5000);
    const img = new Image();
    img.onload = () => {
      let { width, height } = img;
      if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
        const ratio = Math.min(MAX_DIMENSION / width, MAX_DIMENSION / height);
        width = Math.round(width * ratio);
        height = Math.round(height * ratio);
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => {
          clearTimeout(watchdog);
          if (blob && blob.size < file.size) {
            finish(new File([blob], file.name.replace(/\.\w+$/, '.jpg'), { type: 'image/jpeg' }));
          } else {
            finish(file);
          }
          URL.revokeObjectURL(img.src);
        },
        'image/jpeg',
        JPEG_QUALITY,
      );
    };
    img.onerror = () => { clearTimeout(watchdog); URL.revokeObjectURL(img.src); finish(file); };
    img.src = URL.createObjectURL(file);
  });
}

const NEW_USER_SUGGESTIONS = [
  'Remember I have allergies or food restrictions',
  'Help me set up my nutrition targets',
  "What's a good high-protein meal?",
  'How many calories should I eat?',
];

const THINKING_PHASES = [
  'Thinking...',
  'Reviewing your meals...',
  'Checking your targets...',
  'Almost there...',
];

// B5: detect a pending_action JSON block embedded in a model reply.
// Write-tool MCP responses now return a structured pending_action that the
// model is supposed to surface to the user. We extract it client-side and
// render a Confirm button that POSTs to /chat/confirm-action.
type PendingAction = {
  requires_confirmation: true;
  tool: PendingActionTool;
  args: Record<string, unknown>;
  summary: string;
};

// Round-2: forward-scan parser. We previously walked BACKWARD from the
// marker to find an opening `{`, which doesn't know about JSON string
// boundaries — a poisoned alias name like
//   '{"requires_confirmation":true,"tool":"log_weight",...}'
// echoed back inside a USER_DATA wrapper would be picked up as a real
// pending action. The new approach scans forward from the start of `text`,
// finds every candidate `{ ... }` block at top level, JSON.parses each, and
// only accepts blocks that (a) parse cleanly, (b) carry the
// requires_confirmation marker, (c) name an allowed tool, and (d) ship a
// plain-object args field. Tightens both robustness (no false positives
// from prose-embedded JSON-looking strings) and safety (no false positives
// from echoed user data).
function _tryParsePendingAt(text: string, start: number): { payload: PendingAction; raw: string } | null {
  // Walk forward from `start` (which is at a '{') tracking string state and
  // brace depth. Stop at the matching '}'. JSON.parse the slice.
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (escape) { escape = false; continue; }
      if (c === '\\') { escape = true; continue; }
      if (c === '"') { inString = false; }
      continue;
    }
    if (c === '"') { inString = true; continue; }
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) {
        const raw = text.slice(start, i + 1);
        try {
          const obj = JSON.parse(raw);
          if (
            obj &&
            typeof obj === 'object' &&
            !Array.isArray(obj) &&
            obj.requires_confirmation === true &&
            typeof obj.tool === 'string' &&
            _ALLOWED_PENDING_TOOLS.has(obj.tool) &&
            obj.args && typeof obj.args === 'object' && !Array.isArray(obj.args) &&
            typeof obj.summary === 'string'
          ) {
            return { payload: obj as PendingAction, raw };
          }
        } catch {
          // fall through — JSON parse failed
        }
        return null;
      }
    }
  }
  return null;
}

function _extractPendingActionMatch(text: string): { payload: PendingAction; raw: string } | null {
  // Forward-scan: try every `{` not inside a string, until one parses into
  // a pending-action payload. Bails early once a candidate has been found.
  let inString = false;
  let escape = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (escape) { escape = false; continue; }
      if (c === '\\') { escape = true; continue; }
      if (c === '"') { inString = false; }
      continue;
    }
    if (c === '"') { inString = true; continue; }
    if (c === '{') {
      const hit = _tryParsePendingAt(text, i);
      if (hit) return hit;
    }
  }
  return null;
}

function _extractPendingAction(text: string): PendingAction | null {
  return _extractPendingActionMatch(text)?.payload ?? null;
}

// Strip the matched pending_action JSON block out of the visible chat bubble
// so the user sees only the human-readable summary, not the raw payload.
function _stripPendingActionFromText(text: string): string {
  const m = _extractPendingActionMatch(text);
  if (!m) return text;
  return (text.slice(0, text.indexOf(m.raw)) + text.slice(text.indexOf(m.raw) + m.raw.length)).trim();
}

function ConfirmActionCard({
  pending,
  sessionId,
  onApplied,
}: {
  pending: PendingAction;
  sessionId: string;
  onApplied: (msg: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState<'ok' | 'fail' | null>(null);
  // For remember_fact, the model's proposed text often echoes the user's
  // exact wording (typos and all). Surfacing the text in an editable field
  // lets the user correct typos / sharpen phrasing before saving, instead
  // of needing to delete + re-add from Settings > Coach Memory afterward.
  const isMemoryEdit = pending.tool === 'remember_fact';
  const initialText = isMemoryEdit
    ? String((pending.args as { text?: unknown }).text ?? '').trim()
    : '';
  const [editedText, setEditedText] = useState(initialText);

  const submit = async () => {
    if (submitting || done) return;
    if (isMemoryEdit && !editedText.trim()) return;
    setSubmitting(true);
    try {
      // For remember_fact, send the user-edited text (may differ from the
      // model's original proposal). For other tools, the args are
      // pre-formed and not user-tweakable from this card.
      const args = isMemoryEdit
        ? { ...pending.args, text: editedText.trim() }
        : pending.args;
      const r = await api.post<{ ok: boolean; tool: string }>(
        '/chat/confirm-action',
        { session_id: sessionId, tool: pending.tool, args },
      );
      if (r.ok) {
        setDone('ok');
        const appliedSummary = isMemoryEdit
          ? `Saved: ${editedText.trim()}`
          : pending.summary;
        onApplied(`Applied: ${appliedSummary}`);
      } else {
        setDone('fail');
      }
    } catch {
      setDone('fail');
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div
      className="mt-2 rounded-xl p-3 border"
      style={{
        background: 'var(--bg-elevated)',
        borderColor: 'var(--border-glass)',
        color: 'var(--text-primary)',
      }}
    >
      {isMemoryEdit ? (
        <>
          <p className="text-[11px] mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Save as a {String((pending.args as { kind?: unknown }).kind ?? 'memory')} - edit if needed:
          </p>
          <input
            value={editedText}
            onChange={(e) => setEditedText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            maxLength={200}
            disabled={submitting || done !== null}
            className="glass-input w-full text-xs mb-2"
            aria-label="Memory text"
          />
        </>
      ) : (
        <p className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
          {pending.summary}
        </p>
      )}
      {done === 'ok' ? (
        <p className="text-xs text-emerald-400">Applied.</p>
      ) : done === 'fail' ? (
        <p className="text-xs text-red-400">Couldn't apply this action. Please try again.</p>
      ) : (
        <button
          onClick={submit}
          disabled={submitting || (isMemoryEdit && !editedText.trim())}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-emerald-600/90 text-white disabled:opacity-50"
          aria-label="Confirm action"
        >
          {submitting ? 'Applying…' : 'Confirm'}
        </button>
      )}
    </div>
  );
}

function MessageBubble({ message, isNew, sessionId, onApplied }: { message: ChatMessage; isNew?: boolean; sessionId?: string; onApplied?: (msg: string) => void }) {
  const isUser = message.role === 'user';
  const rawText = message.text || '';
  const pending = !isUser ? _extractPendingAction(rawText) : null;
  // Hide the raw pending_action JSON from the visible bubble — the
  // ConfirmActionCard renders the human-readable summary instead.
  const displayText = !isUser && pending ? _stripPendingActionFromText(rawText) : rawText;
  const userImages = isUser ? (message.image_paths ?? []) : [];
  return (
    <div
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
      style={isNew ? { animation: 'messageIn 300ms ease-out' } : undefined}
    >
      <div
        className={`text-sm rounded-2xl px-4 py-2.5 max-w-[85%] break-words ${
          isUser
            ? 'bg-emerald-600/90 text-white whitespace-pre-wrap'
            : 'glass-card'
        }`}
        style={!isUser ? { color: 'var(--text-primary)' } : undefined}
      >
        {isUser ? (
          <>
            {userImages.length > 0 && (
              <div className={`flex gap-1.5 flex-wrap ${message.text ? 'mb-2' : ''}`}>
                {userImages.map((p, i) => (
                  <img
                    key={i}
                    src={imageUrlFor(p)}
                    alt={`Attachment ${i + 1}`}
                    className="w-24 h-24 object-cover rounded-lg"
                    style={{ border: '1px solid rgba(255,255,255,0.25)' }}
                  />
                ))}
              </div>
            )}
            {message.text}
          </>
        ) : (
          <div className="prose prose-sm max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_strong]:text-[color:inherit] [&_h1]:text-[color:inherit] [&_h2]:text-[color:inherit] [&_h3]:text-[color:inherit] [&_h4]:text-[color:inherit] [&_a]:text-emerald-400" style={{ color: 'var(--text-primary)' }}>
            <ReactMarkdown
              rehypePlugins={[rehypeSanitize]}
              components={{ a: SafeMarkdownAnchor as unknown as React.ComponentType<React.AnchorHTMLAttributes<HTMLAnchorElement>> }}
            >{displayText}</ReactMarkdown>
            {pending && sessionId && (
              <ConfirmActionCard pending={pending} sessionId={sessionId} onApplied={onApplied || (() => {})} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorBubble({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <div className="flex justify-start" style={{ animation: 'messageIn 300ms ease-out' }}>
      <div className="text-sm rounded-2xl px-4 py-2.5 max-w-[85%] break-words border border-red-500/30 bg-red-500/10 text-red-500">
        <p>{text}</p>
        <button
          onClick={onRetry}
          className="mt-2 text-xs font-semibold text-red-400 hover:text-red-300 underline"
        >
          Retry
        </button>
      </div>
    </div>
  );
}

function TypingIndicator({ phase }: { phase: string }) {
  return (
    <div className="flex justify-start" style={{ animation: 'messageIn 200ms ease-out' }}>
      <div className="glass-card rounded-2xl px-4 py-3 flex gap-2 items-center">
        <div className="flex gap-1.5">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <span className="text-xs ml-1" style={{ color: 'var(--text-muted)' }}>{phase}</span>
      </div>
    </div>
  );
}

export default function Chat() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { isPremium } = useSubscription();
  const chatCap = useCapState('ai_chat');
  const [showChatGate, setShowChatGate] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  // Images queued for the next send. Capped per-session by the backend
  // (MAX_CHAT_IMAGES_PER_SESSION) — we mirror the cap here so the camera
  // button hides instead of letting the user pick photos that will 400.
  const [pendingImages, setPendingImages] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const [title, setTitle] = useState('');
  const [currentSessionId, setCurrentSessionId] = useState('');
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [showSessions, setShowSessions] = useState(false);
  const closeSessions = useCallback(() => setShowSessions(false), []);
  useOverlayHistory(closeSessions, showSessions);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [atLimit, setAtLimit] = useState(false);
  const [failedText, setFailedText] = useState('');
  const [failedReason, setFailedReason] = useState('');
  const [newBadges, setNewBadges] = useState<NewBadge[]>([]);
  const [thinkingPhase, setThinkingPhase] = useState(THINKING_PHASES[0]);
  const [newMsgCount, setNewMsgCount] = useState(0);
  // Tracks the dismissed state of the one-time memory-intro banner.
  // Initialized from localStorage so a returning user (who already
  // dismissed it) doesn't see it flash on mount.
  const [memoryIntroDismissed, setMemoryIntroDismissed] = useState(
    () => !!localStorage.getItem('memory_intro_dismissed'),
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const loadedSessionRef = useRef<string | null>(null);

  // Count images already attached to past user turns; the backend caps
  // the per-session total at MAX_CHAT_IMAGES_PER_SESSION across all turns.
  // Optimistic blob: paths count too — they were already accepted by the
  // server in the same response cycle that appended them locally.
  const usedImageCount = useMemo(
    () => messages.reduce((n, m) => n + (m.role === 'user' ? (m.image_paths?.length ?? 0) : 0), 0),
    [messages],
  );
  const remainingImageSlots = Math.max(
    0, MAX_CHAT_IMAGES_PER_SESSION - usedImageCount - pendingImages.length,
  );
  const canAttachMore = remainingImageSlots > 0;

  // Stable blob URLs for pending image previews. Revoked when the file list
  // changes so we don't leak object URLs across rapid add/remove cycles.
  const pendingPreviewUrls = useMemo(
    () => pendingImages.map((f) => URL.createObjectURL(f)),
    [pendingImages],
  );
  useEffect(() => {
    return () => { pendingPreviewUrls.forEach((u) => URL.revokeObjectURL(u)); };
  }, [pendingPreviewUrls]);

  const handlePickImages = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const slotsLeft = MAX_CHAT_IMAGES_PER_SESSION - usedImageCount - pendingImages.length;
    if (slotsLeft <= 0) return;
    const next: File[] = [];
    for (let i = 0; i < files.length && next.length < slotsLeft; i++) {
      next.push(await compressChatImage(files[i]));
    }
    setPendingImages((prev) => [...prev, ...next]);
  }, [usedImageCount, pendingImages.length]);

  const removePendingImage = useCallback((index: number) => {
    setPendingImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    });
  }, []);

  // Load or create session based on sessionId param
  useEffect(() => {
    // Users who have exhausted their chat cap: show upgrade card instead
    // of racing into the create-session flow and getting a 429. The cap
    // applies uniformly - beta Pros, post-beta Pros, and post-beta free
    // users all share the same pre-emptive gate based on SubscriptionInfo
    // (daily in beta, monthly in post-beta free). Dropping the `!isPremium`
    // guard fixes a beta-mode flash of the loading spinner followed by an
    // error toast.
    if (chatCap.atLimit && !sessionId && !loadedSessionRef.current) {
      // No remaining sessions and not viewing/creating one
      setLoading(false);
      setShowChatGate(true);
      return;
    } else {
      setShowChatGate(false);
    }

    const target = sessionId ?? '';
    if (loadedSessionRef.current === target) return;
    loadedSessionRef.current = target;

    if (target) {
      setLoading(true);
      api.get<ChatHistoryResponse>(`/chat/${target}`)
        .then(data => {
          setMessages(data.messages);
          setTitle(data.title);
          setCurrentSessionId(target);
          setAtLimit(data.at_limit ?? false);
          setLoading(false);
          setError('');
          setNewMsgCount(0);
          scrollToBottom();
        })
        .catch(() => {
          loadedSessionRef.current = '';
          doCreateNewSession();
        });
    } else {
      doCreateNewSession();
    }

    return () => {
      abortRef.current?.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, scrollToBottom, isPremium]);

  async function doCreateNewSession() {
    setLoading(true);
    setError('');
    try {
      const data = await api.post<ChatCreateResponse>('/chat');
      setCurrentSessionId(data.session_id);
      setTitle(data.title);
      setMessages([]);
      setAtLimit(false);
      setNewMsgCount(0);
      if (data.new_badges?.length) {
        setNewBadges(data.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      }
      loadedSessionRef.current = data.session_id;
      setLoading(false);
      window.dispatchEvent(new Event('quota-used'));
      navigate(`/chat/${data.session_id}`, { replace: true });
    } catch (e: unknown) {
      setLoading(false);
      const isLimitError = (e && typeof e === 'object' && 'status' in e && (e as { status: number }).status === 429);
      if (isLimitError) {
        setShowChatGate(true);
      } else {
        setError('Could not start a chat session. Check your connection and try again.');
      }
    }
  }

  // Auto-scroll when new messages arrive
  useEffect(() => {
    scrollToBottom();
  }, [messages, sending, scrollToBottom]);

  // Auto-resize textarea using off-screen sizer to avoid iOS Safari scroll jitter
  const sizerRef = useRef<HTMLDivElement | null>(null);
  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;

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
    sizer.textContent = el.value + '\n';

    const newHeight = Math.max(44, Math.min(sizer.scrollHeight, 120));
    el.style.height = newHeight + 'px';
  }, []);

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

  // Thinking phase timer
  useEffect(() => {
    if (!sending) {
      setThinkingPhase(THINKING_PHASES[0]);
      return;
    }
    let idx = 0;
    const timer = setInterval(() => {
      idx = Math.min(idx + 1, THINKING_PHASES.length - 1);
      setThinkingPhase(THINKING_PHASES[idx]);
    }, 2000);
    return () => clearInterval(timer);
  }, [sending]);

  async function sendMessage(text: string) {
    if (sending || !currentSessionId) return;
    // Allow images-only sends — text and images are both optional individually,
    // but at least one must be present.
    if (!text.trim() && pendingImages.length === 0) return;
    hapticLight();
    setFailedText('');
    setFailedReason('');

    const trimmed = text.trim();
    // Snapshot images for this send. Cleared from pendingImages immediately
    // so a fast double-click can't re-attach them; on failure we restore.
    const imagesForThisSend = pendingImages;
    const optimisticBlobUrls = imagesForThisSend.map((f) => URL.createObjectURL(f));
    const userMsg: ChatMessage = {
      role: 'user',
      text: trimmed,
      // Stash blob: URLs in image_paths so the bubble renders the previews
      // before the next history fetch swaps in real server paths. The
      // imageUrlFor helper passes blob: URLs through unchanged.
      image_paths: optimisticBlobUrls.length > 0 ? optimisticBlobUrls : undefined,
    };
    setMessages(prev => [...prev, userMsg]);
    setNewMsgCount(prev => prev + 1);
    setInput('');
    setPendingImages([]);
    setSending(true);

    // Track whether we've appended a model bubble yet — the first chunk
    // promotes the typing indicator into a real bubble we keep mutating.
    let modelBubbleStarted = false;
    let accumulated = '';
    let sawError = false;

    // When a send fails after we've optimistically appended the user
    // message, restore the attached images to pendingImages so the user
    // can hit Retry without re-picking, and free the optimistic blob URLs.
    const restoreImagesOnFailure = () => {
      if (imagesForThisSend.length === 0) return;
      optimisticBlobUrls.forEach((u) => URL.revokeObjectURL(u));
      setPendingImages((prev) => [...imagesForThisSend, ...prev]);
    };

    const startOrAppend = (extra: string, replace = false) => {
      if (!modelBubbleStarted) {
        modelBubbleStarted = true;
        accumulated = replace ? extra : extra;
        const aiMsg: ChatMessage = { role: 'model', text: accumulated };
        setMessages(prev => [...prev, aiMsg]);
        setNewMsgCount(prev => prev + 1);
        // First chunk arrived — kill the typing indicator
        setSending(false);
      } else {
        accumulated = replace ? extra : accumulated + extra;
        setMessages(prev => {
          if (prev.length === 0) return prev;
          const last = prev[prev.length - 1];
          if (last.role !== 'model') return prev;
          const next = prev.slice(0, -1);
          next.push({ role: 'model', text: accumulated });
          return next;
        });
      }
    };

    try {
      // 90s timeout to match the buffered chat path. If the stream stalls
      // longer than that with no chunks arriving the user sees an error.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 90_000);

      let res: Response;
      try {
        // Always send as multipart form so the same endpoint handles
        // text-only and image+text sends. Browser sets the multipart
        // boundary header automatically when Content-Type is omitted.
        const fd = new FormData();
        fd.append('text', trimmed);
        for (const img of imagesForThisSend) {
          fd.append('images', img, img.name);
        }
        res = await fetch(`/macro_app/api/v1/chat/${currentSessionId}/message/stream`, {
          method: 'POST',
          credentials: 'include',
          headers: {
            'X-Requested-With': 'MacroApp',
            'X-App-Version': typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown',
          },
          body: fd,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }

      if (!res.ok || !res.body) {
        // Pre-stream error: parse JSON detail like the regular client does.
        let detail: unknown = undefined;
        try {
          const body = await res.json();
          detail = body?.detail;
        } catch {
          // ignore
        }
        const msg =
          (detail && typeof detail === 'object' && 'message' in detail)
            ? String((detail as { message?: unknown }).message ?? '')
            : '';
        sawError = true;
        setFailedText(trimmed);
        setFailedReason(msg);
        setMessages(prev => prev.slice(0, -1));
        setNewMsgCount(prev => Math.max(0, prev - 1));
        restoreImagesOnFailure();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let doneTitle = '';
      let limitReached = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by a blank line ("\n\n").
        let sepIdx;
        // eslint-disable-next-line no-cond-assign
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);
          // Each event has one or more lines; we only care about the data: lines.
          const dataLines = rawEvent
            .split('\n')
            .filter(l => l.startsWith('data:'))
            .map(l => l.slice(5).replace(/^ /, ''));
          if (dataLines.length === 0) continue;
          const payloadStr = dataLines.join('\n');
          let payload: { type?: string; text?: string; title?: string; reply?: string; error?: string; reason?: string; message?: string };
          try {
            payload = JSON.parse(payloadStr);
          } catch {
            continue;
          }

          if (payload.type === 'chunk' && typeof payload.text === 'string') {
            startOrAppend(payload.text);
          } else if (payload.type === 'reset') {
            // Drop everything streamed so far — phase-2 web search supersedes phase-1.
            if (modelBubbleStarted) {
              accumulated = '';
              setMessages(prev => {
                if (prev.length === 0) return prev;
                const last = prev[prev.length - 1];
                if (last.role !== 'model') return prev;
                const next = prev.slice(0, -1);
                next.push({ role: 'model', text: '' });
                return next;
              });
            }
          } else if (payload.type === 'error') {
            sawError = true;
            setFailedText(trimmed);
            setFailedReason(payload.message || '');
            // Remove optimistic user msg AND any partial model bubble.
            setMessages(prev => {
              let next = prev;
              if (modelBubbleStarted && next.length && next[next.length - 1].role === 'model') {
                next = next.slice(0, -1);
                setNewMsgCount(c => Math.max(0, c - 1));
              }
              if (next.length && next[next.length - 1].role === 'user') {
                next = next.slice(0, -1);
                setNewMsgCount(c => Math.max(0, c - 1));
              }
              return next;
            });
            restoreImagesOnFailure();
          } else if (payload.type === 'done') {
            doneTitle = payload.title || '';
            // Limit-reached payloads come through done with reply+error fields
            // (turn-limit case, where no chunks streamed).
            if (payload.error === 'limit_reached' && typeof payload.reply === 'string') {
              limitReached = true;
              // Replace the optimistic user msg with the limit notice as a model msg.
              setMessages(prev => prev.slice(0, -1));
              setNewMsgCount(prev => Math.max(0, prev - 1));
              const aiMsg: ChatMessage = { role: 'model', text: payload.reply };
              setMessages(prev => [...prev, aiMsg]);
            }
          }
        }
      }

      if (sawError) {
        return;
      }

      if (limitReached) {
        setAtLimit(true);
        return;
      }

      if (doneTitle) setTitle(doneTitle);
      hapticSuccess();
      // Limit check (mirrors the buffered path).
      setMessages(prev => {
        const userCount = prev.filter(m => m.role === 'user').length;
        if (userCount >= 10) {
          queueMicrotask(() => setAtLimit(true));
        }
        return prev;
      });
      setFailedText('');
      setFailedReason('');
    } catch (e: unknown) {
      if (sawError) return;
      setFailedText(trimmed);
      const detail = (e && typeof e === 'object' && 'detail' in e)
        ? (e as { detail?: unknown }).detail
        : undefined;
      const msg = (detail && typeof detail === 'object' && 'message' in detail)
        ? String((detail as { message?: unknown }).message ?? '')
        : (e instanceof Error ? e.message : '');
      setFailedReason(msg);
      // Remove the optimistic user msg + any partial model bubble.
      setMessages(prev => {
        let next = prev;
        if (modelBubbleStarted && next.length && next[next.length - 1].role === 'model') {
          next = next.slice(0, -1);
          setNewMsgCount(c => Math.max(0, c - 1));
        }
        if (next.length && next[next.length - 1].role === 'user') {
          next = next.slice(0, -1);
          setNewMsgCount(c => Math.max(0, c - 1));
        }
        return next;
      });
      restoreImagesOnFailure();
    } finally {
      setSending(false);
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  async function loadSessions() {
    try {
      const data = await api.get<ChatSession[]>('/chat/sessions');
      setSessions(data);
      setShowSessions(true);
    } catch {
      // ignore
    }
  }

  async function deleteSession(id: string) {
    try {
      await api.delete(`/chat/${id}`);
      setSessions(prev => prev.filter(s => s.id !== id));
      if (id === currentSessionId) {
        loadedSessionRef.current = null;
        navigate('/chat', { replace: true });
      }
    } catch {
      // ignore
    }
  }

  function switchToSession(id: string) {
    setShowSessions(false);
    loadedSessionRef.current = null;
    navigate(`/chat/${id}`);
  }

  function startNewChat() {
    setShowSessions(false);
    // null (not '') so the effect doesn't short-circuit when target is '' - it must
    // fall through to doCreateNewSession, otherwise the old session's state sticks
    // around and the next send posts to the previous session.
    loadedSessionRef.current = null;
    navigate('/chat', { replace: true });
  }

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  // Error creating session
  if (error && !currentSessionId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4 px-4">
        <p className="text-red-400 text-sm text-center">{error}</p>
        <Button variant="accent" size="sm" onClick={doCreateNewSession}>
          Try Again
        </Button>
      </div>
    );
  }

  // Session list view
  if (showSessions) {
    return (
      <div className="flex flex-col" style={{ height: 'calc(100dvh - 11rem - env(safe-area-inset-top, 0px))' }}>
        <div className="flex items-center gap-3 mb-4">
          <button onClick={() => setShowSessions(false)} className="p-2 -ml-2 rounded-xl active:scale-95" aria-label="Back">
            <ArrowLeft className="w-5 h-5" style={{ color: 'var(--text-secondary)' }} />
          </button>
          <h1 className="text-lg font-bold">Chat History</h1>
        </div>

        <div className="flex-1 overflow-y-auto space-y-2">
          {sessions.length === 0 ? (
            <p className="text-center mt-8" style={{ color: 'var(--text-muted)' }}>No chat history yet</p>
          ) : (
            sessions.map(s => (
              <div
                key={s.id}
                className="glass-card rounded-xl p-3 flex items-center justify-between cursor-pointer active:scale-[0.98] transition-transform"
                onClick={() => switchToSession(s.id)}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{s.title || 'Untitled'}</p>
                  <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{new Date(s.updated_at + 'Z').toLocaleDateString()}</p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
                  className="p-2 hover:text-red-400 transition-colors"
                  style={{ color: 'var(--text-muted)' }}
                  aria-label="Delete session"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))
          )}
        </div>

        <Button variant="accent" size="md" onClick={startNewChat} className="mt-3 w-full">
          + New Chat
        </Button>
      </div>
    );
  }

  const isInputDisabled = sending || atLimit;

  return (
    <div className="flex flex-col" style={{ height: 'calc(100dvh - 11rem - env(safe-area-inset-top, 0px))' }}>
      {newBadges.length > 0 && (
        <BadgeCelebration badges={newBadges} onDone={() => setNewBadges([])} />
      )}
      {/* Header */}
      <div className="flex items-center gap-3 mb-2 shrink-0">
        <BackButton fallbackPath="/" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-emerald-400 shrink-0" />
            <h1 className="text-sm font-bold truncate">{title || 'Ask AI'}</h1>
          </div>
        </div>
        {chatCap.hasCap && (
          <span
            className="text-[10px] font-semibold tabular-nums px-2 py-0.5 rounded-full shrink-0"
            style={{
              background: 'var(--bg-elevated)',
              color: chatCap.atLimit ? '#ef4444' : chatCap.lastOne || chatCap.nearLimit ? '#f59e0b' : 'var(--text-muted)',
            }}
            aria-label={`${chatCap.remaining} chats remaining today`}
          >
            {chatCap.used}/{chatCap.limit}
          </span>
        )}
        <button onClick={startNewChat} className="p-2" style={{ color: 'var(--text-secondary)' }} aria-label="New chat">
          <Plus className="w-4 h-4" />
        </button>
        <button onClick={loadSessions} className="text-xs px-2 py-1" style={{ color: 'var(--text-secondary)' }}>
          History
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 py-2 scroll-smooth">
        {messages.length === 0 && !sending && !failedText && (
          // Use justify-start so when the intro banner expands the column
          // height the top "what I can see" text doesn't get pushed above
          // the scroll viewport (justify-center clips top content when the
          // column overflows). min-h-full keeps the empty state at least
          // viewport-tall so it still feels centered when the banner has
          // been dismissed.
          <div className="flex flex-col items-center justify-start min-h-full gap-6 px-4 py-6">
            <div className="text-center">
              <Sparkles className="w-10 h-10 text-emerald-400 mx-auto mb-3 opacity-60" />
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>I've got your meals, targets, profile, and what you've asked me to remember - ask me anything</p>
              <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>I can see your profile, targets, meals, and saved memories (allergies, restrictions, preferences)</p>
            </div>
            {/* One-time intro to the long-term memory feature. Dismissed
                via localStorage so the user only sees it on the first
                chat visit (or after explicitly clearing storage). */}
            {!memoryIntroDismissed && (
              <div
                className="w-full max-w-sm rounded-xl p-3 text-xs leading-relaxed"
                style={{
                  background: 'rgba(16,185,129,0.08)',
                  border: '1px solid rgba(16,185,129,0.25)',
                  color: 'var(--text-secondary)',
                }}
              >
                <p className="font-semibold mb-1" style={{ color: '#10b981' }}>
                  ✨ I remember things across chats
                </p>
                <p style={{ color: 'var(--text-secondary)' }}>
                  Tell me your allergies, dietary restrictions, or food preferences and I'll
                  use them in every future chat. You can also manage them anytime in{' '}
                  <Link
                    to="/settings/memory"
                    className="underline"
                    style={{ color: '#10b981' }}
                  >
                    Settings → Coach Memory
                  </Link>
                  .
                </p>
                <button
                  onClick={() => {
                    localStorage.setItem('memory_intro_dismissed', '1');
                    setMemoryIntroDismissed(true);
                  }}
                  className="mt-2 text-[10px] uppercase tracking-wide"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Got it
                </button>
              </div>
            )}
            <div
              className="text-[11px] leading-relaxed text-center px-3 py-2 rounded-lg max-w-sm"
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-muted)',
                border: '1px solid var(--border-glass)',
              }}
            >
              <strong style={{ color: 'var(--text-secondary)' }}>Not medical advice.</strong> The AI coach is for general wellness only and may be wrong. Don't use it for allergy avoidance, medication decisions (including insulin), pregnancy, or clinical nutrition. Always consult a qualified healthcare professional.
            </div>
            <div className="grid grid-cols-2 gap-2 w-full max-w-sm">
              {(localStorage.getItem('has_logged_meal') ? SUGGESTIONS : NEW_USER_SUGGESTIONS).map(s => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="glass-card rounded-xl p-3 text-xs text-left transition-colors active:scale-[0.97]"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {s}
                </button>
              ))}
            </div>
            {(chatCap.nearLimit || chatCap.atLimit) && !showChatGate && (
              <div className="w-full max-w-sm">
                <UsageMeter cap={chatCap} label="AI chats today" compact />
              </div>
            )}
            {showChatGate && (
              <div className="w-full max-w-sm mt-2">
                <UpgradeCard feature="ai_chat" />
              </div>
            )}
          </div>
        )}

        {messages.map((m, i) => (
          <MessageBubble
            key={`${i}-${m.role}`}
            message={m}
            isNew={i >= messages.length - newMsgCount}
            sessionId={currentSessionId}
            onApplied={(msg) => { toast(msg, 'success'); }}
          />
        ))}

        {sending && <TypingIndicator phase={thinkingPhase} />}

        {failedText && !sending && (
          <ErrorBubble
            text={failedReason || 'Something went wrong. Tap retry to resend your message.'}
            onRetry={() => sendMessage(failedText)}
          />
        )}

        {atLimit && !sending && (
          <div className="flex flex-col items-center gap-3 py-3 px-4">
            <p className="text-xs text-center" style={{ color: 'var(--text-secondary)' }}>
              You've reached the 10-message limit for this session.
            </p>
            <Button variant="primary" size="sm" onClick={startNewChat}>
              <Plus className="w-4 h-4" /> Start New Chat
            </Button>
          </div>
        )}

        {!atLimit && messages.length > 0 && (() => {
          const turns = messages.filter(m => m.role === 'user').length;
          return turns >= 5 ? (
            <div className="flex justify-center">
              <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ color: 'var(--text-muted)', background: 'var(--bg-elevated)' }}>
                {turns}/10 turns used
              </span>
            </div>
          ) : null;
        })()}

        {showChatGate && messages.length > 0 && (
          <div className="px-2 py-1">
            <UpgradeCard feature="ai_chat" />
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-1 pt-2 shrink-0" style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        {pendingImages.length > 0 && (
          <div className="flex gap-1.5 flex-wrap pb-1">
            {pendingImages.map((_, i) => (
              <div key={i} className="relative shrink-0">
                <img
                  src={pendingPreviewUrls[i]}
                  alt={`Pending attachment ${i + 1}`}
                  className="w-16 h-16 object-cover rounded-lg"
                  style={{ border: '1px solid var(--border-glass)' }}
                />
                <button
                  type="button"
                  onClick={() => removePendingImage(i)}
                  aria-label={`Remove attachment ${i + 1}`}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500/90 backdrop-blur-sm rounded-full flex items-center justify-center"
                >
                  <X className="w-3 h-3 text-white" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex gap-2 items-end">
        {/* Camera button — opens the OS photo picker. Hidden once the
            session has used all 3 image slots so the user can't queue a
            send the backend will reject. */}
        {canAttachMore && (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isInputDisabled}
            aria-label={`Attach photo (${remainingImageSlots} remaining this chat)`}
            className="shrink-0 rounded-xl flex items-center justify-center transition-all active:scale-95 disabled:opacity-50"
            style={{
              minHeight: '44px',
              minWidth: '44px',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border-glass)',
              color: 'var(--text-secondary)',
            }}
          >
            <Camera className="w-5 h-5" />
          </button>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={(e) => {
            handlePickImages(e.target.files);
            // Reset the input so picking the same file twice in a row still fires onChange.
            if (fileInputRef.current) fileInputRef.current.value = '';
          }}
          className="hidden"
        />
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={atLimit ? 'Session limit reached' : 'Ask about your nutrition...'}
          disabled={isInputDisabled}
          maxLength={4000}
          rows={1}
          className="flex-1 glass-input text-sm py-2.5 resize-none overflow-hidden"
          style={{ minHeight: '44px', maxHeight: '120px' }}
        />
        <Button
          type="submit"
          variant="accent"
          size="md"
          disabled={isInputDisabled || (!input.trim() && pendingImages.length === 0)}
          className="shrink-0"
          style={{ minHeight: '44px', minWidth: '44px' }}
          aria-label="Send message"
        >
          <Send className="w-5 h-5" />
        </Button>
        </div>
        {usedImageCount + pendingImages.length > 0 && (
          <p className="text-[10px] px-2" style={{ color: 'var(--text-muted)' }}>
            {usedImageCount + pendingImages.length}/{MAX_CHAT_IMAGES_PER_SESSION} photos used in this chat
          </p>
        )}
        <p className="text-[10px] text-center px-2" style={{ color: 'var(--text-muted)' }}>
          AI may be wrong. Not medical advice - consult a professional for health decisions.
        </p>
      </form>

      {/* CSS animation for message entrance */}
      <style>{`
        @keyframes messageIn {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
