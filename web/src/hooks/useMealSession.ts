import { useState, useCallback } from 'react';
import { mealsApi } from '../api/meals';
import type { Nutrition, AnalyzeResponse, AcceptResponse } from '../types';

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
      fd.append('meal_type', mealType);

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
  }, []);

  const correct = useCallback(async (text: string, displayText?: string) => {
    if (!sessionId) return null;
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
  }, [sessionId]);

  const reset = useCallback(() => {
    setSessionId(null);
    setNutrition(null);
    setQuestions([]);
    setMessages([]);
    setError(null);
    setCorrectionCount(0);
    setCorrectionLimitReached(false);
  }, []);

  const cancel = useCallback(async () => {
    if (!sessionId) return;
    try {
      await mealsApi.cancel(sessionId);
    } catch {
      // ignore
    }
    reset();
  }, [sessionId, reset]);

  return {
    sessionId, setSessionId, nutrition, setNutrition, questions, setQuestions, messages, error, setError,
    analyzing, correcting, accepting,
    correctionCount, correctionLimitReached,
    analyze, correct, accept, cancel, reset,
  };
}
