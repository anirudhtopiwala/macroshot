import { useEffect, useState } from 'react';
import { Plus, Trash2, Pencil, Heart, Shield, Sparkles, FileText } from '../components/icons';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ConfirmDialog from '../components/ConfirmDialog';
import PromptDialog from '../components/PromptDialog';
import Modal from '../components/Modal';
import { useToast } from '../components/Toast';
import { memoryApi } from '../api/memory';
import type { MemoryEntry, MemoryKind } from '../api/memory';
import { ApiError } from '../api/client';

const KIND_ORDER: MemoryKind[] = ['allergy', 'restriction', 'preference', 'note'];

const KIND_META: Record<MemoryKind, {
  label: string;
  description: string;
  icon: React.ReactNode;
  bg: string;
  border: string;
  fg: string;
}> = {
  allergy: {
    label: 'Allergies',
    description: 'Hard constraint — coach will never recommend foods that violate this.',
    icon: <Shield className="w-4 h-4" />,
    bg: 'rgba(239,68,68,0.1)',
    border: 'rgba(239,68,68,0.25)',
    fg: '#ef4444',
  },
  restriction: {
    label: 'Dietary restrictions',
    description: 'Hard constraint — vegetarian, vegan, halal, lactose-free, etc.',
    icon: <Heart className="w-4 h-4" />,
    bg: 'rgba(168,85,247,0.1)',
    border: 'rgba(168,85,247,0.25)',
    fg: '#a855f7',
  },
  preference: {
    label: 'Preferences',
    description: 'Soft signals — likes and dislikes the coach uses to personalize.',
    icon: <Sparkles className="w-4 h-4" />,
    bg: 'rgba(16,185,129,0.1)',
    border: 'rgba(16,185,129,0.25)',
    fg: '#10b981',
  },
  note: {
    label: 'Notes',
    description: 'Anything else worth remembering — goals, schedule, household.',
    icon: <FileText className="w-4 h-4" />,
    bg: 'rgba(59,130,246,0.1)',
    border: 'rgba(59,130,246,0.25)',
    fg: '#3b82f6',
  },
};

export default function MemorySettings() {
  const { toast } = useToast();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [adding, setAdding] = useState(false);
  const [addKind, setAddKind] = useState<MemoryKind>('allergy');
  const [addText, setAddText] = useState('');
  const [saving, setSaving] = useState(false);

  const [editing, setEditing] = useState<MemoryEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MemoryEntry | null>(null);

  useEffect(() => {
    memoryApi.list()
      .then((res) => setEntries(res.entries))
      .catch((err) => {
        console.error('memory.list failed', err);
        setError('Could not load coach memory.');
      })
      .finally(() => setLoading(false));
  }, []);

  const handleAdd = async () => {
    const text = addText.trim();
    if (!text) return;
    setSaving(true);
    try {
      const res = await memoryApi.create(addKind, text);
      const now = new Date().toISOString();
      setEntries((prev) => [...prev, {
        id: res.id, kind: addKind, text,
        source: 'user', created_at: now, updated_at: now,
      }]);
      toast('Saved');
      setAdding(false);
      setAddText('');
      setAddKind('allergy');
    } catch (err) {
      console.error('memory.create failed', err);
      // 409 = MAX_USER_MEMORIES cap. Surface the server's actionable
      // detail ("...Delete an older memory before saving a new one.")
      // instead of a generic toast.
      if (err instanceof ApiError && err.status === 409) {
        toast(err.message, 'error');
      } else {
        toast('Failed to save', 'error');
      }
    } finally {
      setSaving(false);
    }
  };

  // Best-effort, dependency-free relative time. Falls back to the raw
  // ISO string if Date parsing returns NaN (defensive against legacy
  // rows with a different timestamp shape).
  const formatRelative = (iso: string): string => {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return iso;
    const diff = Date.now() - t;
    const min = 60_000;
    if (diff < min) return 'just now';
    if (diff < 60 * min) return `${Math.floor(diff / min)}m ago`;
    if (diff < 24 * 60 * min) return `${Math.floor(diff / (60 * min))}h ago`;
    const days = Math.floor(diff / (24 * 60 * min));
    if (days < 30) return `${days}d ago`;
    if (days < 365) return `${Math.floor(days / 30)}mo ago`;
    return `${Math.floor(days / 365)}y ago`;
  };

  const handleEdit = async (entry: MemoryEntry, newText: string) => {
    const text = newText.trim();
    if (!text || text === entry.text) {
      setEditing(null);
      return;
    }
    try {
      await memoryApi.update(entry.id, { text });
      setEntries((prev) => prev.map((e) =>
        e.id === entry.id ? { ...e, text, updated_at: new Date().toISOString() } : e,
      ));
      toast('Updated');
    } catch (err) {
      console.error('memory.update failed', err);
      toast('Failed to update', 'error');
    } finally {
      setEditing(null);
    }
  };

  const handleDelete = async (entry: MemoryEntry) => {
    try {
      await memoryApi.delete(entry.id);
      setEntries((prev) => prev.filter((e) => e.id !== entry.id));
      // Undo: re-creates the memory at the API; the new id will differ
      // from the original, but the user-visible content (kind+text) is
      // identical so this matches the expected mental model.
      toast('Memory deleted', 'info', {
        label: 'Undo',
        onClick: async () => {
          try {
            const res = await memoryApi.create(entry.kind, entry.text);
            const now = new Date().toISOString();
            setEntries((prev) => [...prev, {
              id: res.id, kind: entry.kind, text: entry.text,
              source: entry.source, created_at: now, updated_at: now,
            }]);
            toast('Restored');
          } catch (e) {
            console.error('memory.create (undo) failed', e);
            if (e instanceof ApiError && e.status === 409) {
              toast(e.message, 'error');
            } else {
              toast("Couldn't undo — please re-add manually", 'error');
            }
          }
        },
      });
    } catch (err) {
      console.error('memory.delete failed', err);
      toast('Failed to delete', 'error');
    } finally {
      setDeleteTarget(null);
    }
  };

  if (loading) return <LoadingSpinner fullPage />;

  const grouped: Record<MemoryKind, MemoryEntry[]> = {
    allergy: [], restriction: [], preference: [], note: [],
  };
  for (const e of entries) {
    if (e.kind in grouped) grouped[e.kind].push(e);
  }
  // Newest-first within each kind. Backend returns oldest-first (good for
  // a stable seed-context byte prefix → prompt-cache friendly), but the
  // list page ranks recently-added or recently-edited highest because
  // that's what users want to verify or edit.
  for (const k of KIND_ORDER) {
    grouped[k].sort((a, b) =>
      (b.updated_at || b.created_at || '').localeCompare(
        a.updated_at || a.created_at || '',
      ),
    );
  }
  const isEmpty = entries.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-xl font-bold">Coach Memory</h1>
      </div>

      <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
        Things the AI coach remembers about you across chats. The coach can also propose new
        memories during a conversation — you'll always be asked to confirm before anything is saved.
      </p>

      {error && (
        <div className="glass-card p-4 text-sm text-red-400">{error}</div>
      )}

      <div className="flex justify-end">
        <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
          <Plus className="w-3.5 h-3.5" /> Add memory
        </Button>
      </div>

      {isEmpty ? (
        <EmptyState
          icon={<Sparkles className="w-10 h-10" />}
          title="No memories yet"
          subtitle="Add an allergy, dietary restriction, food preference, or any other context worth remembering."
        />
      ) : (
        KIND_ORDER.map((kind) => {
          const items = grouped[kind];
          if (items.length === 0) return null;
          const meta = KIND_META[kind];
          return (
            <div key={kind} className="space-y-2">
              <h2 className="section-heading flex items-center gap-2" style={{ marginTop: '0.5rem' }}>
                <span
                  className="w-7 h-7 rounded-full flex items-center justify-center"
                  style={{ background: meta.bg, border: `1px solid ${meta.border}`, color: meta.fg }}
                >
                  {meta.icon}
                </span>
                {meta.label}
              </h2>
              <p className="text-xs px-1" style={{ color: 'var(--text-muted)' }}>
                {meta.description}
              </p>
              {items.map((entry) => (
                <div key={entry.id} className="glass-card-hover flex items-center gap-3 p-4 !rounded-2xl">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{entry.text}</p>
                    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      {entry.source === 'coach_suggested' ? 'Suggested by coach' : 'Added by you'}
                      {' · '}
                      {entry.updated_at && entry.updated_at !== entry.created_at
                        ? `Updated ${formatRelative(entry.updated_at)}`
                        : `Added ${formatRelative(entry.created_at)}`}
                    </p>
                  </div>
                  <button
                    onClick={() => setEditing(entry)}
                    className="p-2 rounded-lg"
                    style={{ color: 'var(--text-muted)' }}
                    aria-label="Edit"
                  >
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(entry)}
                    className="p-2 rounded-lg"
                    style={{ color: 'var(--text-muted)' }}
                    aria-label="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          );
        })
      )}

      {/* Add modal */}
      <Modal open={adding} onClose={() => setAdding(false)} position="center">
        <div className="p-5" role="dialog" aria-label="Add memory">
          <h3 className="text-base font-bold mb-3" style={{ color: 'var(--text-primary)' }}>
            Add a memory
          </h3>
          <label className="block text-xs font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
            Type
          </label>
          <div className="grid grid-cols-2 gap-2 mb-4">
            {KIND_ORDER.map((k) => {
              const meta = KIND_META[k];
              const selected = addKind === k;
              return (
                <button
                  key={k}
                  onClick={() => setAddKind(k)}
                  className="flex items-center gap-2 p-2.5 rounded-lg text-xs font-medium border"
                  style={{
                    background: selected ? meta.bg : 'transparent',
                    borderColor: selected ? meta.border : 'var(--border-glass)',
                    color: selected ? meta.fg : 'var(--text-secondary)',
                  }}
                >
                  {meta.icon}
                  {meta.label.replace('Dietary ', '')}
                </button>
              );
            })}
          </div>
          <label className="block text-xs font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
            Memory
          </label>
          <input
            value={addText}
            onChange={(e) => setAddText(e.target.value)}
            placeholder={
              addKind === 'allergy' ? 'allergic to peanuts' :
              addKind === 'restriction' ? 'vegetarian' :
              addKind === 'preference' ? 'loves Thai food' :
              'training for a half marathon in October'
            }
            maxLength={200}
            className="glass-input w-full text-sm mb-4"
            autoFocus
          />
          <div className="flex gap-3">
            <Button
              type="button" variant="secondary" size="md" className="flex-1"
              onClick={() => { setAdding(false); setAddText(''); }}
            >
              Cancel
            </Button>
            <Button
              type="button" variant="primary" size="md" className="flex-1"
              onClick={handleAdd}
              disabled={!addText.trim() || saving}
            >
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      </Modal>

      <PromptDialog
        open={editing !== null}
        title="Edit memory"
        message={editing ? KIND_META[editing.kind].label : undefined}
        defaultValue={editing?.text || ''}
        placeholder="Memory text"
        confirmLabel="Save"
        onConfirm={(v) => editing && handleEdit(editing, v)}
        onCancel={() => setEditing(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete memory"
        message={`Forget "${deleteTarget?.text}"? The coach won't know this about you anymore.`}
        confirmLabel="Delete"
        destructive
        onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
