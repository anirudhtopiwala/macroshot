import { useEffect, useState, useRef } from 'react';
import { aliasesApi } from '../api/aliases';
import { mealsApi } from '../api/meals';
import { Search, Trash2, Pencil, X, Check } from '../components/icons';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import MealEditor from '../components/MealEditor';
import MacroDisplay from '../components/MacroDisplay';
import BadgeCelebration from '../components/BadgeCelebration';
import { useToast } from '../components/Toast';
import ConfirmDialog from '../components/ConfirmDialog';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import { incrementMealCount } from '../utils/onboarding';
import { applyItemTune, buildItemTunePrompt, findRemovedItemNames, type ItemTuneField, type ItemTuneDirection } from '../utils/itemTune';
import { useCapState } from '../hooks/useCapState';
import { useSubscription } from '../context/SubscriptionContext';
import UsageMeter from '../components/UsageMeter';
import UpgradeCard from '../components/UpgradeCard';
import type { Alias, Nutrition, NewBadge } from '../types';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

function aliasToNutrition(alias: Alias): Nutrition {
  const items = alias.items.length > 0
    ? alias.items
    : [{ name: alias.item_name, description: '', calories: alias.calories, protein: alias.protein, carbs: alias.carbs, fat: alias.fat, weight_g: null, source: null }];
  return {
    item_name: alias.name,
    meal_description: '',
    items,
    calories: alias.calories,
    protein: alias.protein,
    carbs: alias.carbs,
    fat: alias.fat,
    source: 'alias',
  };
}

export default function SavedMeals() {
  const { toast } = useToast();
  const savedMealsCap = useCapState('saved_meals');
  const { isPremium } = useSubscription();
  const cachedAliases = getCached<Alias[]>('saved_meals');
  const [aliases, setAliases] = useState<Alias[]>(cachedAliases || []);
  const [loading, setLoading] = useState(!cachedAliases);
  const [fetchError, setFetchError] = useState('');
  const [search, setSearch] = useState('');
  const [logging, setLogging] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ originalName: string; nutrition: Nutrition } | null>(null);
  const [saving, setSaving] = useState(false);
  const editRef = useRef<HTMLDivElement>(null);

  const [newBadges, setNewBadges] = useState<NewBadge[]>([]);

  // AI editing state
  const [aiSessionId, setAiSessionId] = useState<string | null>(null);
  const [aiMessages, setAiMessages] = useState<ChatMessage[]>([]);
  const [aiCorrecting, setAiCorrecting] = useState(false);
  const creatingSessionRef = useRef(false);

  useEffect(() => {
    aliasesApi.list().then((data) => {
      setAliases(data);
      setCache('saved_meals', data);
    }).catch((err) => {
      console.error(err);
      if (!cachedAliases) setFetchError('Could not load saved meals.');
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (editing && editRef.current) {
      editRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [editing]);

  const handleLog = async (name: string) => {
    setLogging(name);
    try {
      const res = await aliasesApi.log(name);
      localStorage.setItem('has_logged_meal', '1');
      incrementMealCount();
      if (res.new_badges?.length) {
        setNewBadges(res.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      } else {
        toast(`Logged "${name}"`);
      }
    } catch {
      toast('Failed to log meal', 'error');
    } finally {
      setLogging(null);
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await aliasesApi.delete(name);
      setAliases((prev) => {
        const updated = prev.filter((a) => a.name !== name);
        setCache('saved_meals', updated);
        return updated;
      });
      toast(`Deleted "${name}"`);
    } catch {
      toast('Failed to delete', 'error');
    }
  };

  // Buffer for item names removed before the AI session was created.
  // Flushed once the lazy session creation completes so Gemini's
  // conversation reflects the deletion.
  const pendingDeletionsRef = useRef<string[]>([]);

  const flushPendingDeletions = async (sid: string) => {
    if (pendingDeletionsRef.current.length === 0) return;
    const names = pendingDeletionsRef.current.splice(0);
    await Promise.all(
      names.map((name) => mealsApi.itemRemoved(sid, name).catch(() => {})),
    );
  };

  const handleEditNutritionChange = (n: Nutrition) => {
    if (!editing) return;
    const removed = findRemovedItemNames(editing.nutrition, n);
    setEditing({ ...editing, nutrition: n });
    if (removed.length === 0) return;
    if (aiSessionId) {
      const sid = aiSessionId;
      removed.forEach((name) => {
        mealsApi.itemRemoved(sid, name).catch(() => {});
      });
    } else {
      pendingDeletionsRef.current.push(...removed);
    }
  };

  const handleEdit = (alias: Alias) => {
    // Clean up any previous AI session
    if (aiSessionId) mealsApi.cancel(aiSessionId).catch(() => {});
    setAiSessionId(null);
    setAiMessages([]);
    pendingDeletionsRef.current = [];
    setEditing({ originalName: alias.name, nutrition: aliasToNutrition(alias) });
  };

  const [aiQuestions, setAiQuestions] = useState<string[]>([]);
  const handleAiCorrect = async (text: string, displayText?: string) => {
    if (!editing) return;
    setAiCorrecting(true);
    setAiMessages((prev) => [...prev, { role: 'user', text: displayText ?? text }]);
    setAiQuestions([]);
    try {
      let sessionId = aiSessionId;
      if (!sessionId) {
        if (creatingSessionRef.current) return;
        creatingSessionRef.current = true;
        try {
          const res = await aliasesApi.createEditSession(editing.originalName);
          sessionId = res.session_id;
          setAiSessionId(sessionId);
          await flushPendingDeletions(sessionId);
        } finally {
          creatingSessionRef.current = false;
        }
      }
      const res = await mealsApi.correct(sessionId, text);
      if (res.nutrition) {
        setEditing({ ...editing, nutrition: res.nutrition });
      }
      if (res.reply_text) {
        setAiMessages((prev) => [...prev, { role: 'assistant', text: res.reply_text }]);
      }
      if (res.questions?.length) setAiQuestions(res.questions);
    } catch {
      toast('AI correction failed', 'error');
    } finally {
      setAiCorrecting(false);
    }
  };

  const handleItemTune = async (index: number, field: ItemTuneField, direction: ItemTuneDirection) => {
    if (!editing) return;
    const item = editing.nutrition.items[index];
    if (!item) return;
    const { detailed, display } = buildItemTunePrompt(item, field, direction);
    const prevNutrition = editing.nutrition;

    setAiCorrecting(true);
    setAiMessages((prev) => [...prev, { role: 'user', text: display }]);
    setAiQuestions([]);

    try {
      let sessionId = aiSessionId;
      if (!sessionId) {
        if (creatingSessionRef.current) return;
        creatingSessionRef.current = true;
        try {
          const res = await aliasesApi.createEditSession(editing.originalName);
          sessionId = res.session_id;
          setAiSessionId(sessionId);
          await flushPendingDeletions(sessionId);
        } finally {
          creatingSessionRef.current = false;
        }
      }
      const res = await mealsApi.correct(sessionId, detailed);
      if (res.nutrition) {
        setEditing({ ...editing, nutrition: applyItemTune(prevNutrition, index, res.nutrition) });
      }
      if (res.reply_text) {
        setAiMessages((prev) => [...prev, { role: 'assistant', text: res.reply_text }]);
      }
      if (res.questions?.length) setAiQuestions(res.questions);
    } catch {
      toast('AI tune failed', 'error');
    } finally {
      setAiCorrecting(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!editing) return;
    const n = editing.nutrition;
    const trimmedName = n.item_name.trim();
    if (!trimmedName) { toast('Name is required', 'error'); return; }

    setSaving(true);
    try {
      const itemsJson = JSON.stringify(n.items.map((it) => ({
        name: it.name, description: it.description || '', calories: it.calories, protein: it.protein, carbs: it.carbs, fat: it.fat, weight_g: it.weight_g, source: it.source,
      })));

      if (trimmedName.toLowerCase() !== editing.originalName.toLowerCase()) {
        await aliasesApi.delete(editing.originalName);
      }

      const updated = await aliasesApi.create({
        name: trimmedName,
        item_name: n.items.map((it) => it.name).join(', '),
        calories: n.calories, protein: n.protein, carbs: n.carbs, fat: n.fat,
        items_json: itemsJson,
      });

      setAliases((prev) => {
        const without = prev.filter((a) => a.name !== editing.originalName && a.name !== trimmedName.toLowerCase());
        const next = [...without, updated];
        setCache('saved_meals', next);
        return next;
      });
      if (aiSessionId) mealsApi.cancel(aiSessionId).catch(() => {});
      setAiSessionId(null);
      setAiMessages([]);
      setEditing(null);
      if (updated.new_badges?.length) {
        setNewBadges(updated.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      } else {
        toast('Saved!');
      }
    } catch {
      toast('Failed to save', 'error');
    } finally {
      setSaving(false);
    }
  };

  const filtered = aliases.filter((a) =>
    a.name.toLowerCase().includes(search.toLowerCase()) ||
    a.item_name.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  const isEditing = (name: string) => editing?.originalName === name;

  return (
    <div className="space-y-4">
      {newBadges.length > 0 && (
        <BadgeCelebration badges={newBadges} onDone={() => { setNewBadges([]); toast('Saved!'); }} />
      )}
      <h1 className="text-xl font-bold">Saved Meals</h1>

      {(savedMealsCap.nearLimit || savedMealsCap.atLimit) && (
        <UsageMeter cap={savedMealsCap} label="Saved meals" />
      )}

      {savedMealsCap.atLimit && !isPremium && (
        <UpgradeCard
          feature="saved_meals"
          message={`You've saved the max ${savedMealsCap.limit} meals on the free plan. Delete one to free a slot, or upgrade for unlimited.`}
        />
      )}

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search saved meals..."
        className="w-full glass-input text-sm"
      />

      {fetchError && (
        <div className="text-center py-4">
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>{fetchError}</p>
          <button
            onClick={() => { setFetchError(''); setLoading(true); aliasesApi.list().then(setAliases).catch((err) => { console.error(err); setFetchError('Could not load saved meals.'); }).finally(() => setLoading(false)); }}
            className="mt-2 text-xs font-semibold text-emerald-400"
          >
            Retry
          </button>
        </div>
      )}

      {filtered.length === 0 && !fetchError ? (
        <div className="glass-card p-12 text-center" style={{ color: 'var(--text-secondary)' }}>
          <Search className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--text-muted)' }} />
          <p className="font-medium">No saved meals yet</p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>After logging a meal, tap 'Save' to create a shortcut for quick one-tap re-logging</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2">
          {filtered.map((alias) => (
            <div key={alias.name} ref={isEditing(alias.name) ? editRef : undefined}>
              {isEditing(alias.name) && editing ? (
                /* ── Edit Mode - unified MealEditor with AI ── */
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>Editing</span>
                    <button onClick={() => {
                      if (aiSessionId) mealsApi.cancel(aiSessionId).catch(() => {});
                      setAiSessionId(null);
                      setAiMessages([]);
                      setEditing(null);
                    }} className="p-1 rounded-lg" style={{ color: 'var(--text-muted)' }}>
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <MealEditor
                    nutrition={editing.nutrition}
                    onNutritionChange={handleEditNutritionChange}
                    aiMessages={aiMessages}
                    aiQuestions={aiQuestions}
                    onAiSend={handleAiCorrect}
                    onItemTune={handleItemTune}
                    itemTuneDisabled={aiCorrecting}
                    aiDisabled={aiCorrecting}
                    aiPlaceholder="Describe changes with AI..."
                    aiTheme
                  />
                  <Button variant="primary" size="sm" onClick={handleSaveEdit} disabled={saving} className="w-full">
                    <Check className="w-3.5 h-3.5" /> {saving ? 'Saving...' : 'Save Changes'}
                  </Button>
                </div>
              ) : (
                /* ── View Mode ── */
                <div className="glass-card p-4">
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <h3 className="font-semibold">{alias.name}</h3>
                      <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>{alias.item_name}</p>
                    </div>
                    <span className="font-bold text-sm tabular-nums" style={{ color: 'var(--color-calories)' }}>
                      {Math.round(alias.calories)} cal
                    </span>
                  </div>
                  <MacroDisplay protein={alias.protein} carbs={alias.carbs} fat={alias.fat} className="mb-3" />
                  <div className="flex gap-2">
                    <Button variant="primary" size="sm" onClick={() => handleLog(alias.name)} disabled={logging === alias.name} className="flex-1">
                      {logging === alias.name ? 'Logging...' : 'Log Now'}
                    </Button>
                    <Button variant="accent" size="sm" onClick={() => handleEdit(alias)}>
                      <Pencil className="w-3 h-3" /> Edit
                    </Button>
                    <Button variant="destructive" size="sm" onClick={() => setDeleteTarget(alias.name)}>
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Saved Meal"
        message={`Delete saved meal "${deleteTarget}"? This cannot be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={() => { if (deleteTarget) handleDelete(deleteTarget); setDeleteTarget(null); }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
