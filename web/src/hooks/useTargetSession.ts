import { useState, useCallback } from 'react';
import { targetsApi, type SuggestRequest } from '../api/targets';
import type { Targets } from '../types';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  macrosUpdated?: boolean;
  noUpdateHint?: boolean;
}

export function useTargetSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [targets, setTargets] = useState<Targets | null>(null);
  const [explanation, setExplanation] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [refining, setRefining] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suggest = useCallback(async (data: SuggestRequest) => {
    setSuggesting(true);
    setError(null);
    setMessages([]);
    try {
      const res = await targetsApi.suggest(data);
      setSessionId(res.session_id);
      if (res.targets) setTargets(res.targets);
      if (res.explanation) setExplanation(res.explanation);
      // Note: the initial AI reply is shown in the explanation card, not
      // duplicated into the chat messages. Chat starts empty; refinement
      // turns appear there.
      if (res.error) setError(res.error);
      return res;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Suggestion failed';
      setError(msg);
      return null;
    } finally {
      setSuggesting(false);
    }
  }, []);

  const refine = useCallback(async (text: string) => {
    if (!sessionId) return null;
    setRefining(true);
    try {
      setMessages((prev) => [...prev, { role: 'user', text }]);
      const res = await targetsApi.refine(sessionId, text);
      const hadTargets = !!res.targets;
      if (res.targets) setTargets(res.targets);
      if (res.explanation) setExplanation(res.explanation);
      if (res.reply_text) {
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            text: res.reply_text,
            macrosUpdated: hadTargets,
            noUpdateHint: !hadTargets,
          },
        ]);
      }
      if (res.error) setError(res.error);
      return res;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Refinement failed';
      setError(msg);
      return null;
    } finally {
      setRefining(false);
    }
  }, [sessionId]);

  const accept = useCallback(async (t: Targets) => {
    if (!sessionId) return null;
    try {
      // Return the API response so callers can react to `new_badges`
      // (e.g., render BadgeCelebration after a target_set).
      return await targetsApi.accept(sessionId, t);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Accept failed';
      setError(msg);
      return null;
    }
  }, [sessionId]);

  const reset = useCallback(() => {
    setSessionId(null);
    setTargets(null);
    setExplanation('');
    setMessages([]);
    setError(null);
  }, []);

  return {
    sessionId, targets, setTargets, explanation, messages, error,
    suggesting, refining,
    suggest, refine, accept, reset,
  };
}
