import { useState, useCallback } from 'react';
import { mealsApi } from '../api/meals';
import { guestApi } from '../api/guest';
import { useAuth } from '../context/AuthContext';
import { saveGuestMeal } from '../utils/guestStorage';
import { formatLocalDateTime } from '../utils/date';
import type { Nutrition, AnalyzeResponse, AcceptResponse } from '../types';

// Synthetic session id used when running an analyze in guest mode. No
// server-side meal_sessions row exists for this id; only the local
// nutrition/messages state is hydrated. Lets the existing UI re-use
// every existing sessionId-gated render branch (review card, accept
// button) without a parallel "guest result" pathway.
const GUEST_SESSION_PREFIX = 'guest:';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  /** True when this assistant reply actually changed the macro totals -
   *  used by CorrectionChat to render a "Macros updated" pill so the
   *  user knows to scroll up to the nutrition card. */
  macrosUpdated?: boolean;
}

function macrosChanged(a: Nutrition | null, b: Nutrition | null): boolean {
  if (!a || !b) return false;
  // Compare totals at 1-decimal precision - ignores float jitter.
  const round1 = (n: number) => Math.round(n * 10) / 10;
  return (
    round1(a.calories ?? 0) !== round1(b.calories ?? 0) ||
    round1(a.protein ?? 0) !== round1(b.protein ?? 0) ||
    round1(a.carbs ?? 0) !== round1(b.carbs ?? 0) ||
    round1(a.fat ?? 0) !== round1(b.fat ?? 0)
  );
}

export function useMealSession() {
  const { isGuest } = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [nutrition, setNutrition] = useState<Nutrition | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [correctionCount, setCorrectionCount] = useState(0);
  const [correctionLimitReached, setCorrectionLimitReached] = useState(false);
  const [pendingMealType, setPendingMealType] = useState<string>('');
  const [pendingUserInput, setPendingUserInput] = useState<string>('');

  const analyze = useCallback(async (images: File[], text: string, mealType: string) => {
    setAnalyzing(true);
    setError(null);
    setMessages([]);
    setCorrectionCount(0);
    setCorrectionLimitReached(false);
    try {
      const fd = new FormData();
      images.forEach((img) => fd.append('images', img));
      fd.append('text', text);
      // Guest analyze ignores meal_type server-side - but we hold onto
      // it locally so accept can attach it to the IndexedDB row.
      if (!isGuest) fd.append('meal_type', mealType);

      if (isGuest) {
        const guestRes = await guestApi.analyze(fd);
        const fakeId = `${GUEST_SESSION_PREFIX}${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
        setSessionId(fakeId);
        setNutrition(guestRes.nutrition);
        setQuestions([]);
        setPendingMealType(mealType);
        setPendingUserInput(text.trim());
        if (text.trim()) {
          setMessages([{ role: 'user', text: text.trim() }]);
        }
        if (guestRes.error) setError(guestRes.error);
        // Match the AnalyzeResponse shape so existing callers can read
        // session_id/nutrition/questions/raw_text/error.
        const res: AnalyzeResponse = {
          session_id: fakeId,
          nutrition: guestRes.nutrition,
          questions: [],
          raw_text: guestRes.raw_text,
          error: guestRes.error,
        };
        return res;
      }

      const res: AnalyzeResponse = await mealsApi.analyze(fd);
      setSessionId(res.session_id);
      setNutrition(res.nutrition);
      setQuestions(res.questions);
      // Show the initial text prompt in the correction chat so user has context
      if (text.trim()) {
        setMessages([{ role: 'user', text: text.trim() }]);
      }
      if (res.error) setError(res.error);
      // Refresh subscription quota counters after EVERY analyze (image
      // OR text-only) so SubscriptionContext picks up the new usage.
      // Previously this only fired for image analyses, leaving the text
      // meal counter stale in Settings.
      window.dispatchEvent(new Event('quota-used'));
      return res;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Analysis failed';
      setError(msg);
      return null;
    } finally {
      setAnalyzing(false);
    }
  }, [isGuest]);

  const correct = useCallback(async (text: string, displayText?: string) => {
    if (!sessionId) return null;
    // Guests don't have a server-side correction loop. Surface a soft
    // prompt instead of letting the call 404.
    if (sessionId.startsWith(GUEST_SESSION_PREFIX)) {
      setMessages((prev) => [
        ...prev,
        { role: 'user', text: displayText ?? text },
        {
          role: 'assistant',
          text: 'Sign up to refine this meal with AI corrections.',
        },
      ]);
      return null;
    }
    setCorrecting(true);
    setQuestions([]); // Clear questions while correction is in-flight
    try {
      setMessages((prev) => [...prev, { role: 'user', text: displayText ?? text }]);
      const prevNutrition = nutrition;
      const res = await mealsApi.correct(sessionId, text);
      if (res.nutrition) setNutrition(res.nutrition);
      // Always render an assistant bubble after a successful correction. If
      // Gemini returned only JSON (no conversational text), the chat would
      // otherwise look frozen even though macros updated.
      const changed = macrosChanged(prevNutrition, res.nutrition ?? null);
      const replyText = (res.reply_text ?? '').trim()
        || (changed ? 'Updated the macros for you.' : 'No changes needed.');
      setMessages((prev) => [...prev, { role: 'assistant', text: replyText, macrosUpdated: changed }]);
      // Re-populate questions if Gemini sent follow-ups
      if (res.questions?.length) setQuestions(res.questions);
      if (res.error) setError(res.error);
      setCorrectionCount((c) => c + 1);
      return res;
    } catch (e: unknown) {
      // Detect the per-meal AI-edit cap via the structured error shape:
      // 429 + detail.feature === 'meal_edit'. Fall back to the legacy
      // substring match on "corrections per meal" so older cached
      // bundles / post-beta free tier messages still trip the flag.
      const isLimitError = (e && typeof e === 'object' && 'status' in e && (e as { status: number }).status === 429);
      const errDetail = (e && typeof e === 'object' && 'detail' in e
        ? (e as { detail?: { feature?: string } }).detail
        : undefined);
      const isMealEditCap =
        isLimitError && (errDetail?.feature === 'meal_edit');
      const msg = e instanceof Error ? e.message : 'Correction failed';
      if (isMealEditCap || msg.includes('corrections per meal') || msg.includes('AI meal edits')) {
        setCorrectionLimitReached(true);
        // Remove the optimistic user message that was just added
        setMessages((prev) => prev.slice(0, -1));
      }
      setError(msg);
      return null;
    } finally {
      setCorrecting(false);
    }
  }, [sessionId, nutrition]);

  const accept = useCallback(async (nutritionOverride?: Nutrition, loggedAt?: string, servings?: number): Promise<AcceptResponse | null> => {
    if (!sessionId) return null;
    setAccepting(true);
    try {
      if (sessionId.startsWith(GUEST_SESSION_PREFIX)) {
        const n = nutritionOverride ?? nutrition;
        if (!n) {
          setError('Nothing to log');
          return null;
        }
        // Servings multiplier is applied server-side for real users on
        // /meals/{id}/accept (barcode path); for guests we always log
        // the displayed values verbatim because no upstream
        // per-serving math has happened.
        const _servings = servings ?? 1;
        const scaled: Nutrition = _servings === 1 ? n : {
          ...n,
          calories: n.calories * _servings,
          protein: n.protein * _servings,
          carbs: n.carbs * _servings,
          fat: n.fat * _servings,
          items: n.items.map((it) => ({
            ...it,
            calories: it.calories * _servings,
            protein: it.protein * _servings,
            carbs: it.carbs * _servings,
            fat: it.fat * _servings,
          })),
        };
        await saveGuestMeal({
          loggedAt: loggedAt ?? formatLocalDateTime(),
          mealType: pendingMealType,
          userInput: pendingUserInput,
          nutrition: scaled,
        });
        window.dispatchEvent(new Event('guest-meal-added'));
        const emptyTotals = { calories: 0, protein: 0, carbs: 0, fat: 0 };
        return {
          meal_id: null,
          nutrition: scaled,
          progress: { totals: emptyTotals, target: null, remaining: null },
          new_badges: [],
          error: null,
        };
      }
      const res = await mealsApi.accept(sessionId, nutritionOverride, loggedAt, servings);
      if (res.error) setError(res.error);
      return res;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Accept failed';
      setError(msg);
      return null;
    } finally {
      setAccepting(false);
    }
  }, [sessionId, nutrition, pendingMealType, pendingUserInput]);

  const reset = useCallback(() => {
    setSessionId(null);
    setNutrition(null);
    setQuestions([]);
    setMessages([]);
    setError(null);
    setCorrectionCount(0);
    setCorrectionLimitReached(false);
    setPendingMealType('');
    setPendingUserInput('');
  }, []);

  const cancel = useCallback(async () => {
    if (!sessionId) return;
    // Guest sessions have no server row to cancel.
    if (!sessionId.startsWith(GUEST_SESSION_PREFIX)) {
      try {
        await mealsApi.cancel(sessionId);
      } catch {
        // ignore
      }
    }
    reset();
  }, [sessionId, reset]);

  return {
    sessionId, setSessionId, nutrition, setNutrition, questions, setQuestions, messages, error, setError,
    analyzing, correcting, accepting,
    correctionCount, correctionLimitReached,
    analyze, correct, accept, cancel, reset,
    // Exposed for the LogMeal barcode flow: when a guest accepts a
    // barcode session there's no analyze() call to seed mealType, so
    // the page sets it on the session directly before accept fires.
    setPendingMealType,
  };
}
