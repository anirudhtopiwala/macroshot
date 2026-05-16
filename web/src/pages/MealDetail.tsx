import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { mealsApi } from '../api/meals';
import { aliasesApi } from '../api/aliases';
import MealEditor from '../components/MealEditor';
import ConfirmDialog from '../components/ConfirmDialog';
import PromptDialog from '../components/PromptDialog';
import { Trash2, Pencil, Check, X, MealTypeIcon, Bookmark } from '../components/icons';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { useToast } from '../components/Toast';
import { useSubscription } from '../context/SubscriptionContext';
import { useCapState } from '../hooks/useCapState';
import { clearCache } from '../utils/apiCache';
import { hapticLight } from '../utils/haptics';
import { applyItemTune, buildItemTunePrompt, findRemovedItemNames, type ItemTuneField, type ItemTuneDirection } from '../utils/itemTune';
import type { Meal, FoodItem, Nutrition } from '../types';

import ProgressiveImage from '../components/ProgressiveImage';

const MEAL_TYPES = ['breakfast', 'lunch', 'snack', 'dinner'] as const;

const MEAL_TYPE_COLORS: Record<string, string> = {
  breakfast: '#f59e0b',
  lunch: '#3b82f6',
  snack: '#a855f7',
  dinner: '#f43f5e',
};

const MEAL_TYPE_STYLES: Record<string, { activeBg: string; activeBorder: string }> = {
  breakfast: { activeBg: 'rgba(245,158,11,0.12)', activeBorder: 'rgba(245,158,11,0.3)' },
  lunch:     { activeBg: 'rgba(59,130,246,0.12)',  activeBorder: 'rgba(59,130,246,0.3)' },
  snack:     { activeBg: 'rgba(168,85,247,0.12)',  activeBorder: 'rgba(168,85,247,0.3)' },
  dinner:    { activeBg: 'rgba(244,63,94,0.12)',   activeBorder: 'rgba(244,63,94,0.3)' },
};

export default function MealDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [meal, setMeal] = useState<Meal | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [savingAlias, setSavingAlias] = useState(false);

  // Unified edit state
  const [editing, setEditing] = useState(false);
  const [editedNutrition, setEditedNutrition] = useState<Nutrition | null>(null);
  const [editMealType, setEditMealType] = useState<string>('');
  const [saving, setSaving] = useState(false);

  // AI correction (lazy - session created on first send)
  const [aiSessionId, setAiSessionId] = useState<string | null>(null);
  const [aiMessages, setAiMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([]);
  const [aiCorrecting, setAiCorrecting] = useState(false);
  const [aiCorrectionCount, setAiCorrectionCount] = useState(0);
  const [aiCorrectionLimitReached, setAiCorrectionLimitReached] = useState(false);
  const { isPremium, mealEditLimit } = useSubscription();
  const savedMealsCap = useCapState('saved_meals');

  // Dialog state
  const [aliasPromptOpen, setAliasPromptOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    mealsApi.get(Number(id)).then(setMeal).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  // Warn before leaving with unsaved edits
  useEffect(() => {
    if (!editing) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [editing]);

  const items: FoodItem[] = useMemo(() => {
    if (!meal?.items_json) return [];
    try { return JSON.parse(meal.items_json); } catch { return []; }
  }, [meal?.items_json]);

  const toNutrition = useCallback((): Nutrition | null => {
    if (!meal) return null;
    return {
      item_name: meal.item_name,
      meal_description: meal.meal_description,
      items,
      calories: meal.calories,
      protein: meal.protein,
      carbs: meal.carbs,
      fat: meal.fat,
      source: 'edited',
    };
  }, [meal, items]);

  // Buffer for item names the user deleted before the AI session existed.
  // Flushed to the server once the session is created so Gemini's
  // conversation history knows the items are gone and stops re-adding
  // them on subsequent corrections.
  const pendingDeletionsRef = useRef<string[]>([]);

  const flushPendingDeletions = useCallback(async (sid: string) => {
    if (pendingDeletionsRef.current.length === 0) return;
    const names = pendingDeletionsRef.current.splice(0);
    await Promise.all(
      names.map((name) => mealsApi.itemRemoved(sid, name).catch(() => {})),
    );
  }, []);

  const handleNutritionChange = useCallback((next: Nutrition) => {
    const removed = findRemovedItemNames(editedNutrition, next);
    setEditedNutrition(next);
    if (removed.length === 0) return;
    if (aiSessionId) {
      removed.forEach((name) => {
        mealsApi.itemRemoved(aiSessionId, name).catch(() => {});
      });
    } else {
      pendingDeletionsRef.current.push(...removed);
    }
  }, [editedNutrition, aiSessionId]);

  const handleEdit = () => {
    const n = toNutrition();
    if (!n) return;
    setEditedNutrition(n);
    setEditMealType(meal?.meal_type || 'lunch');
    setEditing(true);
    // Reset AI state for a fresh edit session
    setAiSessionId(null);
    setAiMessages([]);
    pendingDeletionsRef.current = [];
  };

  // Cancel orphaned AI edit session on unmount (navigating away)
  const aiSessionIdRef = useRef(aiSessionId);
  aiSessionIdRef.current = aiSessionId;
  const cancelledSessionRef = useRef<string | null>(null);
  useEffect(() => {
    return () => {
      if (aiSessionIdRef.current && cancelledSessionRef.current !== aiSessionIdRef.current) {
        cancelledSessionRef.current = aiSessionIdRef.current;
        mealsApi.cancel(aiSessionIdRef.current).catch(() => {});
      }
    };
  }, []);

  const handleCancel = () => {
    // Clean up AI session if one was started (guard against double-cancel)
    if (aiSessionId && cancelledSessionRef.current !== aiSessionId) {
      cancelledSessionRef.current = aiSessionId;
      mealsApi.cancel(aiSessionId).catch(() => {});
    }
    setEditing(false);
    setEditedNutrition(null);
    setAiSessionId(null);
    setAiMessages([]);
  };

  const handleSave = async () => {
    if (!meal || !editedNutrition) return;
    setSaving(true);
    try {
      const itemsJson = JSON.stringify(editedNutrition.items);
      await mealsApi.update(meal.id, {
        item_name: editedNutrition.item_name,
        meal_description: editedNutrition.meal_description,
        calories: editedNutrition.calories,
        protein: editedNutrition.protein,
        carbs: editedNutrition.carbs,
        fat: editedNutrition.fat,
        items_json: itemsJson,
        meal_type: editMealType !== meal.meal_type ? editMealType : undefined,
      });
      // Clean up AI session if one was started
      if (aiSessionId) {
        mealsApi.cancel(aiSessionId).catch(() => {});
      }
      const updated = await mealsApi.get(meal.id);
      setMeal(updated);
      setEditing(false);
      setEditedNutrition(null);
      setAiSessionId(null);
      setAiMessages([]);
      clearCache('dash_day_');
      clearCache('dash_trend');
      clearCache('dash_challenges');
      clearCache('journal_');
      toast('Meal updated!');
    } catch {
      toast('Failed to save changes', 'error');
    } finally {
      setSaving(false);
    }
  };

  // AI correction - lazily creates session on first send
  const creatingSessionRef = useRef(false);
  const [aiQuestions, setAiQuestions] = useState<string[]>([]);
  const handleAiCorrect = async (text: string, displayText?: string) => {
    if (!meal) return;
    setAiCorrecting(true);
    setAiMessages((prev) => [...prev, { role: 'user', text: displayText ?? text }]);
    setAiQuestions([]); // Clear questions while correction is in-flight

    try {
      let sessionId = aiSessionId;

      // Create session on first AI correction (guarded against double-creation)
      if (!sessionId) {
        if (creatingSessionRef.current) return;
        creatingSessionRef.current = true;
        try {
          const res = await mealsApi.createEditSession(meal.id);
          sessionId = res.session_id;
          setAiSessionId(sessionId);
          await flushPendingDeletions(sessionId);
        } finally {
          creatingSessionRef.current = false;
        }
      }

      const res = await mealsApi.correct(sessionId, text);
      if (res.nutrition) {
        setEditedNutrition(res.nutrition);
      }
      if (res.reply_text) {
        setAiMessages((prev) => [...prev, { role: 'assistant', text: res.reply_text }]);
      }
      if (res.questions?.length) setAiQuestions(res.questions);
      setAiCorrectionCount((c) => c + 1);
    } catch (e: unknown) {
      const isLimitError = (e && typeof e === 'object' && 'status' in e && (e as { status: number }).status === 429);
      const msg = e instanceof Error ? e.message : '';
      if (isLimitError || msg.includes('corrections per meal')) {
        setAiCorrectionLimitReached(true);
        setAiMessages((prev) => prev.slice(0, -1));
      } else {
        toast('AI correction failed', 'error');
      }
    } finally {
      setAiCorrecting(false);
    }
  };

  const handleItemTune = async (index: number, field: ItemTuneField, direction: ItemTuneDirection) => {
    if (!meal || !editedNutrition) return;
    const item = editedNutrition.items[index];
    if (!item) return;
    const { detailed, display } = buildItemTunePrompt(item, field, direction);
    const prevNutrition = editedNutrition;

    setAiCorrecting(true);
    setAiMessages((prev) => [...prev, { role: 'user', text: display }]);
    setAiQuestions([]);

    try {
      let sessionId = aiSessionId;
      if (!sessionId) {
        if (creatingSessionRef.current) return;
        creatingSessionRef.current = true;
        try {
          const res = await mealsApi.createEditSession(meal.id);
          sessionId = res.session_id;
          setAiSessionId(sessionId);
          await flushPendingDeletions(sessionId);
        } finally {
          creatingSessionRef.current = false;
        }
      }

      const res = await mealsApi.correct(sessionId, detailed);
      if (res.nutrition) {
        setEditedNutrition(applyItemTune(prevNutrition, index, res.nutrition));
      }
      if (res.reply_text) {
        setAiMessages((prev) => [...prev, { role: 'assistant', text: res.reply_text }]);
      }
      if (res.questions?.length) setAiQuestions(res.questions);
      setAiCorrectionCount((c) => c + 1);
    } catch (e: unknown) {
      const isLimitError = (e && typeof e === 'object' && 'status' in e && (e as { status: number }).status === 429);
      const msg = e instanceof Error ? e.message : '';
      if (isLimitError || msg.includes('corrections per meal')) {
        setAiCorrectionLimitReached(true);
        setAiMessages((prev) => prev.slice(0, -1));
      } else {
        toast('AI tune failed', 'error');
      }
    } finally {
      setAiCorrecting(false);
    }
  };

  const handleSaveAsAlias = async (name: string) => {
    if (!meal) return;
    setSavingAlias(true);
    try {
      await aliasesApi.create({
        name,
        item_name: meal.item_name,
        meal_description: meal.meal_description,
        calories: meal.calories,
        protein: meal.protein,
        carbs: meal.carbs,
        fat: meal.fat,
        items_json: meal.items_json || '[]',
      });
      toast(`Saved as "${name}"!`);
    } catch {
      toast('Failed to save alias', 'error');
    } finally {
      setSavingAlias(false);
    }
  };

  const handleDelete = async () => {
    if (!meal) return;
    setDeleting(true);
    try {
      await mealsApi.delete(meal.id);
      clearCache('dash_day_');
      clearCache('dash_trend');
      clearCache('dash_challenges');
      clearCache('journal_');
      // Achievements + summary depend on meal counts (Meal Machine, Paparazzi,
      // bullseye/protein/carbs/macro_master days). Without these, the
      // achievements page shows stale progress for minutes after a delete.
      clearCache('achievements');
      clearCache('achievement_summary');
      clearCache('challenges');
      navigate('/journal', { replace: true });
    } catch {
      toast('Failed to delete meal', 'error');
      setDeleting(false);
    }
  };

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  if (!meal) {
    return <div className="text-center py-12" style={{ color: 'var(--text-secondary)' }}>Meal not found</div>;
  }

  const typeColor = MEAL_TYPE_COLORS[meal.meal_type] || '#10b981';

  return (
    <div className="space-y-4">
      <BackButton label fallbackPath="/journal" />

      {/* Meal image - progressive: blurred thumbnail → full-res crossfade */}
      {meal.image_path && (
        <div className="glass-card overflow-hidden">
          <ProgressiveImage
            imagePath={meal.image_path}
            alt={meal.item_name}
            className="max-h-64"
          />
        </div>
      )}

      {editing && editedNutrition ? (
        /* ── Unified Edit Mode: manual dials + AI chat ── */
        <>
          {/* Meal type selector */}
          <div className="flex gap-2">
            {MEAL_TYPES.map((t) => {
              const c = MEAL_TYPE_STYLES[t];
              const color = MEAL_TYPE_COLORS[t];
              const isActive = editMealType === t;
              return (
                <button
                  key={t}
                  onClick={() => { hapticLight(); setEditMealType(t); }}
                  className="flex-1 py-2 rounded-xl text-xs font-medium transition-all duration-200 flex items-center justify-center gap-1.5"
                  style={isActive ? {
                    background: c.activeBg,
                    border: `1px solid ${c.activeBorder}`,
                    color: color,
                    boxShadow: `0 0 12px ${c.activeBg}`,
                  } : {
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-glass)',
                    color: 'var(--text-muted)',
                  }}
                >
                  <MealTypeIcon type={t} className="w-3.5 h-3.5" />
                  <span className="capitalize">{t}</span>
                </button>
              );
            })}
          </div>

          <MealEditor
            nutrition={editedNutrition}
            onNutritionChange={handleNutritionChange}
            aiMessages={aiMessages}
            aiQuestions={aiQuestions}
            onAiSend={handleAiCorrect}
            onItemTune={handleItemTune}
            itemTuneDisabled={aiCorrecting || aiCorrectionLimitReached}
            aiDisabled={aiCorrecting}
            aiPlaceholder="Describe changes with AI..."
            aiTheme
            correctionLimit={mealEditLimit > 0 ? { used: aiCorrectionLimitReached ? mealEditLimit : aiCorrectionCount, limit: mealEditLimit, isPremium } : undefined}
          />
          <div className="flex gap-3 pb-4">
            <Button variant="primary" onClick={handleSave} disabled={saving} className="flex-1">
              <Check className="w-4 h-4" />
              {saving ? 'Saving...' : 'Save Changes'}
            </Button>
            <Button variant="secondary" onClick={handleCancel} className="flex-1">
              <X className="w-4 h-4" /> Cancel
            </Button>
          </div>
        </>
      ) : (
        /* ── View Mode ── */
        <>
          <div className="glass-card p-5">
            <div className="flex items-center gap-3 mb-3">
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center"
                style={{ background: `${typeColor}12`, border: `1px solid ${typeColor}25`, color: typeColor }}
              >
                <MealTypeIcon type={meal.meal_type} className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-xl font-bold">{meal.item_name}</h1>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  {meal.logged_at} · <span className="capitalize" style={{ color: typeColor }}>{meal.meal_type}</span>
                </p>
              </div>
            </div>

            {meal.meal_description && (
              <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>{meal.meal_description}</p>
            )}

            {items.length > 0 && (
              <div className="space-y-1.5 mb-4">
                {items.map((item: FoodItem, i: number) => (
                  <div
                    key={i}
                    className="flex justify-between items-start gap-2 text-sm rounded-xl px-3 py-2.5"
                    style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}
                  >
                    <div className="min-w-0">
                      <span className="font-medium break-words">{item.name}</span>
                      {item.weight_g && <span className="ml-1 text-xs" style={{ color: 'var(--text-secondary)' }}>({item.weight_g}g)</span>}
                    </div>
                    <div className="text-sm space-x-2 tabular-nums shrink-0">
                      <span className="font-semibold" style={{ color: 'var(--color-calories)' }}>{Math.round(item.calories)} cal</span>
                      <span style={{ color: 'var(--color-protein)' }}>{(item.protein ?? 0).toFixed(1)}p</span>
                      <span style={{ color: 'var(--color-carbs)' }}>{(item.carbs ?? 0).toFixed(1)}c</span>
                      <span style={{ color: 'var(--color-fat)' }}>{(item.fat ?? 0).toFixed(1)}f</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="flex justify-between items-center pt-3" style={{ borderTop: '1px solid var(--border-glass)' }}>
              <span className="font-semibold">Total</span>
              <div className="flex gap-4 text-sm tabular-nums">
                <span className="font-bold" style={{ color: 'var(--color-calories)' }}>{Math.round(meal.calories)} cal</span>
                <span style={{ color: 'var(--color-protein)' }}>{meal.protein.toFixed(1)}g P</span>
                <span style={{ color: 'var(--color-carbs)' }}>{meal.carbs.toFixed(1)}g C</span>
                <span style={{ color: 'var(--color-fat)' }}>{meal.fat.toFixed(1)}g F</span>
              </div>
            </div>
          </div>

          {/* Action buttons */}
          <Button variant="accent" onClick={handleEdit} className="w-full">
            <Pencil className="w-3.5 h-3.5" /> Edit
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              if (savedMealsCap.atLimit) {
                toast(`Saved meals full (${savedMealsCap.used}/${savedMealsCap.limit}). Delete one or upgrade.`, 'error');
                return;
              }
              setAliasPromptOpen(true);
            }}
            disabled={savingAlias || savedMealsCap.atLimit}
            className="w-full"
          >
            <Bookmark className="w-3.5 h-3.5" />
            {savingAlias
              ? 'Saving...'
              : savedMealsCap.atLimit
                ? `Saved meals full (${savedMealsCap.used}/${savedMealsCap.limit})`
                : 'Save as Quick Meal'}
          </Button>
        </>
      )}

      {!editing && (
        <Button variant="destructive" onClick={() => setDeleteConfirmOpen(true)} disabled={deleting} className="w-full">
          <Trash2 className="w-4 h-4" />
          {deleting ? 'Deleting...' : 'Delete Meal'}
        </Button>
      )}

      <PromptDialog
        open={aliasPromptOpen}
        title="Save as Quick Meal"
        message="Enter a name for this quick meal."
        placeholder="e.g. Morning Oatmeal"
        onConfirm={(name) => { setAliasPromptOpen(false); handleSaveAsAlias(name); }}
        onCancel={() => setAliasPromptOpen(false)}
      />

      <ConfirmDialog
        open={deleteConfirmOpen}
        title="Delete Meal"
        message="Are you sure you want to delete this meal? This cannot be undone."
        confirmLabel="Delete"
        destructive
        onConfirm={() => { setDeleteConfirmOpen(false); handleDelete(); }}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </div>
  );
}
