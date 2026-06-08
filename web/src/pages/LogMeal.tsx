import { useState, useEffect, useCallback, useRef, Suspense } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import ImageCapture from '../components/ImageCapture';
import MealEditor from '../components/MealEditor';
import ServingsSelector from '../components/ServingsSelector';
import lazyWithRetry from '../utils/lazyWithRetry';

const BarcodeScanner = lazyWithRetry(() => import('../components/BarcodeScanner'), 'BarcodeScanner');
import { useMealSession } from '../hooks/useMealSession';
import { mealsApi, type RecentMeal } from '../api/meals';
import { aliasesApi } from '../api/aliases';
import { Check, X, MealTypeIcon, Bookmark, Pencil, Clock } from '../components/icons';
import MacroDisplay from '../components/MacroDisplay';
import LoadingSpinner from '../components/LoadingSpinner';
import { hapticLight, hapticSuccess, hapticWarning } from '../utils/haptics';
import { incrementMealCount } from '../utils/onboarding';
import Celebration from '../components/Celebration';
import BadgeCelebration from '../components/BadgeCelebration';
import type { NewBadge } from '../types';
import { useToast } from '../components/Toast';
import { useRegisterRefresh } from '../context/PullToRefreshContext';
import { useOfflineQueue } from '../context/OfflineQueueContext';
import { getCapturedFiles, clearCapturedFiles } from '../utils/capturedImage';
import { applyItemTune, buildItemTunePrompt, findRemovedItemNames, type ItemTuneField, type ItemTuneDirection } from '../utils/itemTune';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import { trackEvent } from '../utils/analytics';
import { track } from '../api/analytics';
import { useSubscription } from '../context/SubscriptionContext';
import { useCapState } from '../hooks/useCapState';
import { useAuth } from '../context/AuthContext';
import UpgradeCard from '../components/UpgradeCard';
import UsageMeter from '../components/UsageMeter';
import Button from '../components/Button';
import Alert from '../components/Alert';
import Modal from '../components/Modal';
import MealPreview from '../components/MealPreview';
import { clearCachedBarcode, getCachedBarcode, setCachedBarcode } from '../utils/barcodeCache';
import type { Nutrition, Alias } from '../types';

const MEAL_TYPES = ['breakfast', 'lunch', 'snack', 'dinner'] as const;

function hourToMealType(h: number): typeof MEAL_TYPES[number] {
  if (h < 11) return 'breakfast';
  if (h < 16) return 'lunch';
  if (h < 18) return 'snack';
  return 'dinner';
}

/** Scale a per-serving Nutrition by an integer/fractional multiplier.
 *  Shared by handleServingsChange (user picker) and applyBarcodeResult
 *  (Reset preserveServings flow) so both paths round identically. */
function scaleNutrition(base: Nutrition, multiplier: number): Nutrition {
  return {
    ...base,
    calories: Math.round(base.calories * multiplier),
    protein: Math.round(base.protein * multiplier * 10) / 10,
    carbs: Math.round(base.carbs * multiplier * 10) / 10,
    fat: Math.round(base.fat * multiplier * 10) / 10,
    items: base.items.map((item) => ({
      ...item,
      calories: Math.round(item.calories * multiplier),
      protein: Math.round(item.protein * multiplier * 10) / 10,
      carbs: Math.round(item.carbs * multiplier * 10) / 10,
      fat: Math.round(item.fat * multiplier * 10) / 10,
      weight_g: item.weight_g ? Math.round(item.weight_g * multiplier) : null,
    })),
  };
}

const ANALYZING_PHASES = [
  'Reading your meal...',
  'Estimating portions...',
  'Crunching the macros...',
  'Cross-checking data...',
  'Almost done...',
];

function AnalyzingText() {
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setPhase((p) => Math.min(p + 1, ANALYZING_PHASES.length - 1));
    }, 2500);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="flex items-center justify-center gap-2">
      <LoadingSpinner size="sm" />
      {ANALYZING_PHASES[phase]}
    </span>
  );
}

const MEAL_COLORS: Record<string, { active: string; activeBg: string; activeBorder: string }> = {
  breakfast: { active: '#f59e0b', activeBg: 'rgba(245,158,11,0.12)', activeBorder: 'rgba(245,158,11,0.3)' },
  lunch:     { active: '#3b82f6', activeBg: 'rgba(59,130,246,0.12)',  activeBorder: 'rgba(59,130,246,0.3)' },
  snack:     { active: '#a855f7', activeBg: 'rgba(168,85,247,0.12)',  activeBorder: 'rgba(168,85,247,0.3)' },
  dinner:    { active: '#f43f5e', activeBg: 'rgba(244,63,94,0.12)',   activeBorder: 'rgba(244,63,94,0.3)' },
};

export default function LogMeal() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { toast } = useToast();
  const [images, setImages] = useState<File[]>(() => {
    // Pick up files captured from FAB Camera button
    const captured = getCapturedFiles();
    if (captured.length > 0) {
      clearCapturedFiles();
      return captured;
    }
    return [];
  });
  const [text, setText] = useState('');
  const backdateStr = searchParams.get('date') || '';
  const [backdateTime, setBackdateTime] = useState(() => {
    const now = new Date();
    const h = now.getHours();
    const m = now.getMinutes() < 30 ? '00' : '30';
    return `${String(h).padStart(2, '0')}:${m}`;
  });
  const [mealType, setMealType] = useState(() => hourToMealType(new Date().getHours()));

  const { isPremium, mealEditLimit, betaMode } = useSubscription();
  // Daily photo-scan cap applies to BOTH free and pro (pro gets a bigger cap).
  // Daily text-meal cap applies separately when the user logs with no image.
  const imageCap = useCapState('image_analysis');
  const textMealCap = useCapState('text_meal');
  const savedMealsCap = useCapState('saved_meals');
  const atImageLimit = imageCap.atLimit;

  const { isGuest } = useAuth();
  const session = useMealSession();
  const [editedNutrition, setEditedNutrition] = useState<Nutrition | null>(null);
  const [modified, setModified] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [acceptedMealId, setAcceptedMealId] = useState<number | null>(null);
  const [newBadges, setNewBadges] = useState<NewBadge[]>([]);
  const [badgeCelebrating, setBadgeCelebrating] = useState(false);

  const { addToQueue } = useOfflineQueue();

  // Warn before leaving with an active session (browser back/refresh)
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (session.sessionId && !celebrating) {
        e.preventDefault();
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [session.sessionId, celebrating]);

  // Cancel orphaned session on unmount (in-app navigation away)
  const celebratingRef = useRef(celebrating);
  celebratingRef.current = celebrating;
  useEffect(() => {
    const sid = session.sessionId;
    const cancelFn = session.cancel;
    return () => {
      if (sid && !celebratingRef.current) {
        cancelFn();
      }
    };
  }, [session.sessionId, session.cancel]);

  // Quick log aliases - load from cache first, then refresh from API
  const [aliases, setAliases] = useState<Alias[]>(() => getCached<Alias[]>('saved_meals') || []);
  const [loggingAlias, setLoggingAlias] = useState<string | null>(null);
  const [editingAliases, setEditingAliases] = useState(false);
  const [editAlias, setEditAlias] = useState<{ originalName: string; nutrition: Nutrition } | null>(null);
  const [savingAlias, setSavingAlias] = useState(false);
  // Recent unique meals for quick re-logging
  const [recentMeals, setRecentMeals] = useState<RecentMeal[]>(() => getCached<RecentMeal[]>('recent_meals') || []);
  // Long-press preview - accepts real RecentMeal rows plus lightweight
  // alias-derived entries that carry items_json for the preview card.
  const [previewMeal, setPreviewMeal] = useState<(RecentMeal & { items_json?: string; image_path?: string }) | null>(null);
  const [reloggingId, setReloggingId] = useState<number | null>(null);
  const [textGlow, setTextGlow] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const quickLogRef = useRef<HTMLDivElement>(null);
  const textMode = searchParams.get('mode') === 'text';
  const barcodeMode = searchParams.get('mode') === 'barcode';
  const scrollTarget = searchParams.get('scroll');

  // Barcode scanning state
  const [showBarcodeScanner, setShowBarcodeScanner] = useState(barcodeMode);
  const [barcodeLoading, setBarcodeLoading] = useState(false);
  const [barcodeError, setBarcodeError] = useState<string | null>(null);
  const [barcodeNotFound, setBarcodeNotFound] = useState(false);
  const [barcodeServings, setBarcodeServings] = useState(1);
  const [barcodeServingLabel, setBarcodeServingLabel] = useState<string | undefined>(undefined);
  const [baseNutrition, setBaseNutrition] = useState<Nutrition | null>(null);
  const [barcodeImageUrl, setBarcodeImageUrl] = useState<string | null>(null);
  const scannedBarcodeRef = useRef<string | null>(null);
  // QR URL lookups show a distinct loading/error UI - barcode copy would be
  // misleading ("Scanned barcode: https://…"). null means the current scan is
  // a regular barcode.
  const [qrScanInfo, setQrScanInfo] = useState<{ url: string; hostname: string } | null>(null);
  const [manualBarcodeInput, setManualBarcodeInput] = useState('');
  const [showManualBarcodeEntry, setShowManualBarcodeEntry] = useState(false);
  const [barcodeOffServingLabel, setBarcodeOffServingLabel] = useState<string | null>(null);
  const [barcodeServingSizeG, setBarcodeServingSizeG] = useState<number | null>(null);
  const [barcodeServingUnit, setBarcodeServingUnit] = useState<'g' | 'ml'>('g');
  const [barcodeCached, setBarcodeCached] = useState(false);
  const [barcodeCachedDaysAgo, setBarcodeCachedDaysAgo] = useState<number | null>(null);
  // Correction metadata: populated when the backend used the user's saved
  // correction for this barcode instead of OFF/FatSecret. Shows a pill.
  const [barcodeCorrectionApplied, setBarcodeCorrectionApplied] = useState(false);
  const [barcodeCorrectedAt, setBarcodeCorrectedAt] = useState<string | null>(null);
  const [resettingCorrection, setResettingCorrection] = useState(false);

  // Sync showBarcodeScanner when barcodeMode changes (e.g. navigating to ?mode=barcode).
  // IMPORTANT: must also check !showManualBarcodeEntry - when the user taps "Type
  // barcode" from the scanner, the scanner closes and the manual entry view opens.
  // Without this guard, the effect would immediately re-open the scanner and
  // clobber the manual entry view.
  useEffect(() => {
    if (
      barcodeMode
      && !showBarcodeScanner
      && !showManualBarcodeEntry
      && !barcodeLoading
      && !barcodeError
      && !barcodeNotFound
      && !session.sessionId
      && !celebrating
      // After accept, `celebrating` transitions to false in the same batch
      // that sets `badgeCelebrating` to true. Without this guard the scanner
      // re-opens behind the badge overlay and pops into view once the badge
      // dismisses, instead of landing the user back on the dashboard.
      && !badgeCelebrating
    ) {
      setShowBarcodeScanner(true);
    }
  }, [barcodeMode, showBarcodeScanner, showManualBarcodeEntry, barcodeLoading, barcodeError, barcodeNotFound, session.sessionId, celebrating, badgeCelebrating]);

  // Touch-based drag reorder for quick log cards
  const dragIdxRef = useRef<number | null>(null);
  const dragElRef = useRef<HTMLElement | null>(null);

  const handleDragTouchStart = (e: React.TouchEvent, idx: number) => {
    if (!editingAliases) return;
    dragIdxRef.current = idx;
    const el = (e.target as HTMLElement).closest('[data-alias]') as HTMLElement;
    dragElRef.current = el;
    if (el) {
      hapticLight();
      el.style.zIndex = '50';
      el.style.transform = 'scale(1.05)';
      el.style.opacity = '0.8';
    }
  };

  const aliasesRef = useRef(aliases);
  aliasesRef.current = aliases;

  const handleDragTouchMove = (e: React.TouchEvent) => {
    if (dragIdxRef.current === null || !editingAliases) return;
    const touch = e.touches[0];
    const target = document.elementFromPoint(touch.clientX, touch.clientY);
    const card = target?.closest('[data-alias]') as HTMLElement | null;
    if (card && card !== dragElRef.current) {
      const currentAliases = aliasesRef.current;
      const toIdx = currentAliases.findIndex(a => a.name === card.dataset.alias);
      const fromIdx = dragIdxRef.current;
      if (toIdx >= 0 && toIdx !== fromIdx) {
        setAliases((prev) => {
          const updated = [...prev];
          const [moved] = updated.splice(fromIdx, 1);
          updated.splice(toIdx, 0, moved);
          return updated;
        });
        hapticLight();
        dragIdxRef.current = toIdx;
      }
    }
  };

  const handleDragTouchEnd = () => {
    if (dragElRef.current) {
      dragElRef.current.style.zIndex = '';
      dragElRef.current.style.transform = '';
      dragElRef.current.style.opacity = '';
    }
    dragIdxRef.current = null;
    dragElRef.current = null;
  };

  const refreshAliases = useCallback(async () => {
    // Aliases require auth — guests have none and the request would 401.
    if (isGuest) return;
    const data = await aliasesApi.list();
    setAliases(data);
    setCache('saved_meals', data);
  }, [isGuest]);

  useRegisterRefresh(refreshAliases);

  useEffect(() => {
    refreshAliases().catch(() => {});
  }, [refreshAliases]);

  // Fetch recent unique meals on mount (skip for guests — endpoint 401s).
  useEffect(() => {
    if (isGuest) return;
    mealsApi.recentUnique().then((data) => {
      if (data.length > 0) {
        setRecentMeals(data);
        setCache('recent_meals', data);
      }
    }).catch(() => {});
  }, [isGuest]);

  const openEditAlias = (alias: Alias) => {
    const items = alias.items.length > 0
      ? alias.items
      : [{ name: alias.item_name, description: '', calories: alias.calories, protein: alias.protein, carbs: alias.carbs, fat: alias.fat, weight_g: null, source: null }];
    const nutrition: Nutrition = {
      item_name: alias.name,
      meal_description: '',
      items,
      calories: alias.calories,
      protein: alias.protein,
      carbs: alias.carbs,
      fat: alias.fat,
      source: 'alias',
    };
    setEditAlias({ originalName: alias.name, nutrition });
  };

  const handleSaveAlias = async () => {
    if (!editAlias) return;
    const n = editAlias.nutrition;
    const trimmedName = n.item_name.trim();
    if (!trimmedName) { toast('Name is required', 'error'); return; }

    setSavingAlias(true);
    try {
      const itemsJson = JSON.stringify(n.items.map((it) => ({
        name: it.name, description: it.description || '', calories: it.calories, protein: it.protein, carbs: it.carbs, fat: it.fat, weight_g: it.weight_g, source: it.source,
      })));
      if (trimmedName.toLowerCase() !== editAlias.originalName.toLowerCase()) {
        await aliasesApi.delete(editAlias.originalName);
      }
      await aliasesApi.create({
        name: trimmedName,
        item_name: n.items.map((it) => it.name).join(', '),
        calories: n.calories, protein: n.protein, carbs: n.carbs, fat: n.fat,
        items_json: itemsJson,
      });
      await refreshAliases();
      setEditAlias(null);
      toast('Saved!');
    } catch { toast('Failed to save', 'error'); }
    finally { setSavingAlias(false); }
  };

  // Auto-scroll and focus based on FAB entry point
  useEffect(() => {
    if (scrollTarget === 'quicklog') {
      setTimeout(() => quickLogRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    } else if (scrollTarget === 'text' || textMode) {
      setTimeout(() => {
        textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        textareaRef.current?.focus();
        setTextGlow(true);
        setTimeout(() => setTextGlow(false), 3000);
      }, 300);
    }
  }, [scrollTarget, textMode]);

  // Clear every piece of barcode-related transient state. Used from
  // scan-start, accept, cancel, and reset paths - keeping one helper
  // guarantees all four paths reset the same fields.
  const resetBarcodeUiState = useCallback(() => {
    setBaseNutrition(null);
    setBarcodeServings(1);
    setBarcodeServingLabel(undefined);
    setBarcodeImageUrl(null);
    setBarcodeOffServingLabel(null);
    setBarcodeServingSizeG(null);
    setBarcodeServingUnit('g');
    setBarcodeCached(false);
    setBarcodeCachedDaysAgo(null);
    setQrScanInfo(null);
    setBarcodeCorrectionApplied(false);
    setBarcodeCorrectedAt(null);
  }, []);

  // Apply a successful barcode result to the session state
  // NOTE: Do NOT call session.reset() before setSessionId - that creates a
  // window where sessionId is null, causing accept() to silently return null.
  // Instead, set the new session ID directly (overwriting the old one).
  //
  // preserveServings: when set to a value != 1, the caller wants the new
  // per-serving nutrition scaled to that multiplier (e.g. Reset on a meal
  // the user had configured to 2 servings - the "2 servings" selection
  // should survive even though the absolute macros may change because the
  // underlying OFF per-serving values changed).
  const applyBarcodeResult = useCallback((
    nutrition: Nutrition,
    newSessionId: string,
    imageUrl: string | null,
    questions?: string[],
    servingLabel?: string | null,
    servingSizeG?: number | null,
    servingUnit?: 'g' | 'ml' | null,
    cached?: boolean,
    cachedDaysAgo?: number | null,
    correctionApplied?: boolean,
    correctedAt?: string | null,
    preserveServings?: number,
  ) => {
    if (imageUrl) setBarcodeImageUrl(imageUrl);
    setBaseNutrition(nutrition);
    // Set new session state atomically - no reset() gap
    session.setSessionId(newSessionId);
    session.setError(null);
    if (questions?.length) session.setQuestions(questions);

    // Scale the new per-serving to preserveServings (or 1 if unset).
    const targetServings = preserveServings && preserveServings > 0 ? preserveServings : 1;
    const displayed = targetServings === 1 ? nutrition : scaleNutrition(nutrition, targetServings);
    session.setNutrition(displayed);
    setEditedNutrition(displayed);
    // Mark modified iff we scaled - the accept path relies on `modified`
    // being true to send the scaled values through instead of the stale
    // per-serving stored on the session row.
    setModified(targetServings !== 1);
    setBarcodeServings(targetServings);

    const unit: 'g' | 'ml' = servingUnit === 'ml' ? 'ml' : 'g';
    const firstItem = nutrition.items[0];
    if (servingLabel) {
      setBarcodeServingLabel(servingLabel);
    } else if (firstItem?.weight_g) {
      setBarcodeServingLabel(`1 serving (${firstItem.weight_g}${unit})`);
    }
    setBarcodeOffServingLabel(servingLabel ?? null);
    setBarcodeServingSizeG(servingSizeG ?? null);
    setBarcodeServingUnit(unit);
    setBarcodeCached(cached ?? false);
    setBarcodeCachedDaysAgo(cachedDaysAgo ?? null);
    setBarcodeCorrectionApplied(correctionApplied ?? false);
    setBarcodeCorrectedAt(correctedAt ?? null);
  }, [session]);

  // Barcode scan handler
  //
  // preserveServings: optional multiplier to re-apply after the lookup
  // (used by Reset so the user doesn't lose their "2 servings" selection).
  const barcodeLoadingRef = useRef(false);
  const handleBarcodeScan = useCallback(async (barcode: string, preserveServings?: number) => {
    if (barcodeLoadingRef.current) return; // prevent concurrent API calls from rapid scans
    barcodeLoadingRef.current = true;
    setShowBarcodeScanner(false);
    setShowManualBarcodeEntry(false);
    setBarcodeLoading(true);
    setBarcodeError(null);
    setBarcodeNotFound(false);
    scannedBarcodeRef.current = barcode;
    resetBarcodeUiState();

    // Check frontend cache - show cached nutrition instantly for perceived speed,
    // but always call the backend to get a fresh session (cached sessions expire).
    const cached = getCachedBarcode(barcode);
    if (cached) {
      hapticSuccess();
      trackEvent('barcode_cache_hit');
      // Show cached nutrition immediately while we fetch a fresh session
      setBaseNutrition(cached.nutrition);
      if (cached.imageUrl) setBarcodeImageUrl(cached.imageUrl);
    }

    try {
      const res = await mealsApi.barcodeLookup(barcode, 1, mealType);
      if (res.error || !res.nutrition) {
        setBarcodeNotFound(true);
        if (cached) setBaseNutrition(null); // clear stale preview
        hapticWarning(); // buzz for not found
        return;
      }
      if (!cached) hapticSuccess(); // success haptic when product found (skip if already fired for cache hit)

      // Cache successful lookup (nutrition + product info only, not the session)
      setCachedBarcode(
        barcode,
        res.nutrition,
        res.nutrition.item_name,
        res.image_url ?? null,
      );

      applyBarcodeResult(
        res.nutrition, res.session_id, res.image_url ?? null, res.questions,
        res.serving_label, res.serving_size_g, res.serving_size_unit ?? null,
        res.cached, res.cached_days_ago,
        res.correction_applied, res.corrected_at, preserveServings,
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Barcode lookup failed';
      hapticWarning(); // buzz for failure
      if (msg.includes('not found') || msg.includes('404') || msg.includes('No product')) {
        setBarcodeNotFound(true);
      } else {
        setBarcodeError(msg);
      }
    } finally {
      setBarcodeLoading(false);
      barcodeLoadingRef.current = false;
    }
  }, [mealType, applyBarcodeResult, resetBarcodeUiState]);

  // QR-code scan handler - server classifies the payload.
  //   digits 8–14  → server delegates to barcode flow; response shape matches.
  //   URL          → server fetches + Gemini-analyzes as text; nutrition may be
  //                  null and questions populated (e.g. restaurant menu with
  //                  multiple items). MealEditor handles questions via its
  //                  CorrectionChat so the barcode-mode review UI still works.
  //   other        → response.error populated; show like a barcode error.
  const handleQrScan = useCallback(async (payload: string) => {
    if (barcodeLoadingRef.current) return;
    barcodeLoadingRef.current = true;
    setShowBarcodeScanner(false);
    setShowManualBarcodeEntry(false);
    setBarcodeLoading(true);
    setBarcodeError(null);
    setBarcodeNotFound(false);
    // Display payload in the loading card; trim long URLs so they fit.
    scannedBarcodeRef.current = payload.length > 40 ? payload.slice(0, 40) + '…' : payload;
    resetBarcodeUiState();
    // If the payload is a URL, surface it in the loading/error UI so the user
    // knows we're fetching a page (not looking up a barcode). Must run AFTER
    // resetBarcodeUiState so the new value isn't immediately cleared.
    let hostname: string | null = null;
    try {
      const withScheme = /^https?:\/\//i.test(payload) ? payload : 'https://' + payload;
      hostname = new URL(withScheme).hostname;
    } catch { /* not a parseable URL */ }
    setQrScanInfo(hostname ? { url: payload, hostname } : null);
    trackEvent('qr_lookup_start');
    try {
      const res = await mealsApi.qrScan(payload, mealType);
      if (res.error || !res.session_id) {
        setBarcodeError(res.error || 'Could not read that QR code.');
        hapticWarning();
        return;
      }
      if (res.nutrition) {
        hapticSuccess();
        applyBarcodeResult(
          res.nutrition, res.session_id, res.image_url ?? null, res.questions,
          res.serving_label, res.serving_size_g, res.serving_size_unit ?? null,
          res.cached, res.cached_days_ago,
          res.correction_applied, res.corrected_at,
        );
      } else {
        // Menu-style response: no nutrition yet, but Gemini populated
        // questions. Seed the session so MealEditor's CorrectionChat picks up.
        session.setSessionId(res.session_id);
        session.setQuestions(res.questions || []);
        if (res.questions?.length) hapticSuccess(); else hapticWarning();
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'QR lookup failed';
      hapticWarning();
      setBarcodeError(msg);
    } finally {
      setBarcodeLoading(false);
      barcodeLoadingRef.current = false;
    }
  }, [mealType, applyBarcodeResult, resetBarcodeUiState, session]);

  // Submit manual barcode entry
  const handleManualBarcodeSubmit = useCallback(() => {
    const code = manualBarcodeInput.trim();
    if (!code || code.length < 8 || code.length > 14) return;
    setManualBarcodeInput('');
    handleBarcodeScan(code);
  }, [manualBarcodeInput, handleBarcodeScan]);

  // Scale nutrition when servings change.
  // The accept flow depends on `modified=true` being set alongside the
  // scaled nutrition - otherwise accept() would send the stale per-serving
  // values from session.nutrition and the backend would divide them by
  // `servings`, logging half-size meals. Keep these two setters together.
  const handleServingsChange = useCallback((newServings: number) => {
    setBarcodeServings(newServings);
    if (!baseNutrition) return;

    const scaled = scaleNutrition(baseNutrition, newServings);
    setEditedNutrition(scaled);
    session.setNutrition(scaled);
    setModified(true);
  }, [baseNutrition, session]);

  const handleNutritionChange = (n: Nutrition) => {
    const removed = findRemovedItemNames(displayNutrition, n);
    setEditedNutrition(n);
    setModified(true);
    if (removed.length > 0 && session.sessionId) {
      const sid = session.sessionId;
      removed.forEach((name) => {
        mealsApi.itemRemoved(sid, name).catch(() => {});
      });
    }
  };

  // When session nutrition changes (from correction), reset local edits
  const displayNutrition = modified ? editedNutrition : (barcodeMode && baseNutrition ? (editedNutrition || baseNutrition) : session.nutrition);

  const handleItemTune = async (index: number, field: ItemTuneField, direction: ItemTuneDirection) => {
    const prev = displayNutrition;
    if (!prev) return;
    const item = prev.items[index];
    if (!item) return;
    const { detailed, display } = buildItemTunePrompt(item, field, direction);
    const res = await session.correct(detailed, display);
    if (res?.nutrition) {
      setEditedNutrition(applyItemTune(prev, index, res.nutrition));
      setModified(true);
    }
  };

  const handleAnalyze = async () => {
    if (session.analyzing) return;
    if (!images.length && !text.trim()) return;

    // Offline: queue text-only meals
    if (!navigator.onLine && text.trim()) {
      await addToQueue(text.trim(), mealType);
      toast('Meal queued - will submit when back online', 'info');
      navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true });
      return;
    }

    setModified(false);
    setEditedNutrition(null);
    // Telemetry: meal input mode tells us which input path users prefer
    const mode = images.length && text.trim() ? 'combined' : images.length ? 'image' : 'text';
    track('ui_meal_input_mode', {
      mode,
      image_count: images.length,
      has_text: Boolean(text.trim()),
      backdated: Boolean(backdateStr),
    });
    await session.analyze(images, text, mealType);
  };

  // Clear every piece of barcode-related transient state. Used from
  // scan-start, accept, cancel, and reset paths - keeping one helper
  // guarantees all four paths reset the same fields.
  // Reset a barcode correction and refetch from the database. The user
  // taps this when the saved correction is stale or wrong.
  //
  // Two subtleties the obvious "just re-scan" doesn't handle:
  //  1. The frontend barcode cache (24h TTL) still holds the CORRECTED
  //     nutrition from the previous lookup. Without purging it, the next
  //     scan flashes the corrected values for ~300ms before the backend
  //     round-trip returns fresh OFF data. clearCachedBarcode kills it.
  //  2. If the user had 2 servings selected before tapping Reset, that
  //     selection should survive. handleBarcodeScan normally resets
  //     servings to 1; pass preserveServings to override.
  const handleResetCorrection = useCallback(async () => {
    const barcode = scannedBarcodeRef.current;
    if (!barcode || resettingCorrection) return;
    const preserved = barcodeServings;
    setResettingCorrection(true);
    try {
      await mealsApi.resetBarcodeCorrection(barcode);
      clearCachedBarcode(barcode);
      toast('Reset to database values', 'info');
      // Re-run the lookup so the review screen updates with fresh OFF/FS data.
      // Preserve the servings count the user had picked so they don't have to
      // re-select it after the Reset.
      await handleBarcodeScan(barcode, preserved);
    } catch {
      toast('Failed to reset', 'error');
    } finally {
      setResettingCorrection(false);
    }
  }, [resettingCorrection, handleBarcodeScan, barcodeServings]);

  const handleAccept = async () => {
    if (!session.sessionId) {
      toast('Session not ready - please try again', 'error');
      return;
    }
    const nutritionToSend = modified && editedNutrition ? editedNutrition : undefined;
    const loggedAt = backdateStr ? `${backdateStr} ${backdateTime}` : undefined;
    // For barcode sessions, include servings so the backend can back out the
    // per-serving nutrition when saving a barcode correction.
    const servingsToSend = barcodeMode ? barcodeServings : undefined;
    const t0 = performance.now();
    const result = await session.accept(nutritionToSend, loggedAt, servingsToSend);
    const elapsed = Math.round(performance.now() - t0);
    if (elapsed > 3000 && import.meta.env.DEV) console.warn(`[perf] accept took ${elapsed}ms`);
    if (result && !result.error) {
      clearCache('dash_');
      clearCache('journal_');
      clearCache('achievements');
      clearCache('achievement_summary');
      clearCache('challenges');
      // Snapshot nutrition for the celebration screen's "Save as Quick Log" button
      // BEFORE reset clears session.nutrition. Must also set modified=true so
      // displayNutrition reads from editedNutrition (not the soon-to-be-null session.nutrition)
      if (!modified && session.nutrition) {
        setEditedNutrition(session.nutrition);
        setModified(true);
      }
      // Reset barcode state so next scan starts fresh
      resetBarcodeUiState();
      // Clear dangling session
      session.reset();
      if (result.new_badges?.length) setNewBadges(result.new_badges);
      setAcceptedMealId(result.meal_id ?? null);
      hapticSuccess();
      localStorage.setItem('has_logged_meal', '1');
      incrementMealCount();
      trackEvent('meal_logged', { mode: barcodeMode ? 'barcode' : textMode ? 'text' : 'photo' });

      // First meal of day celebration (only for today, not backdated meals)
      if (!backdateStr) {
        const firstMealKey = `first_meal_toast_${new Date().toISOString().slice(0, 10)}`;
        if (!localStorage.getItem(firstMealKey)) {
          localStorage.setItem(firstMealKey, '1');
          toast('Day started! \uD83D\uDD25');
        }
      }

      setCelebrating(true);
    }
  };

  const pendingAliasDeleteRef = useRef<Map<string, { alias: Alias; timer: ReturnType<typeof setTimeout> }>>(new Map());

  useEffect(() => {
    return () => {
      pendingAliasDeleteRef.current.forEach(({ timer }) => clearTimeout(timer));
    };
  }, []);

  const deleteAlias = (name: string) => {
    const alias = aliases.find((a) => a.name === name);
    if (!alias) return;

    setAliases((prev) => prev.filter((a) => a.name !== name));

    const timer = setTimeout(async () => {
      pendingAliasDeleteRef.current.delete(name);
      try {
        await aliasesApi.delete(name);
      } catch {
        setAliases((prev) => [...prev, alias]);
        toast('Failed to delete', 'error');
      }
    }, 5000);

    pendingAliasDeleteRef.current.set(name, { alias, timer });

    toast(`Deleted "${name}"`, 'success', {
      label: 'Undo',
      onClick: () => {
        const pending = pendingAliasDeleteRef.current.get(name);
        if (pending) {
          clearTimeout(pending.timer);
          pendingAliasDeleteRef.current.delete(name);
          setAliases((prev) => [...prev, alias]);
        }
      },
    });
  };

  const handleQuickLog = async (alias: Alias) => {
    setLoggingAlias(alias.name);
    try {
      const loggedAt = backdateStr ? `${backdateStr} ${backdateTime}` : undefined;
      await aliasesApi.log(alias.name, loggedAt);
      clearCache('dash_');
      clearCache('journal_');
      clearCache('achievements');
      clearCache('achievement_summary');
      clearCache('challenges');
      hapticSuccess();
      localStorage.setItem('has_logged_meal', '1');
      incrementMealCount();
      trackEvent('quick_log');
      toast(`Logged "${alias.name}"`);
      navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true });
    } catch {
      toast('Failed to log meal', 'error');
    } finally {
      setLoggingAlias(null);
    }
  };

  const handleRecentRelog = async (meal: RecentMeal) => {
    setReloggingId(meal.id);
    try {
      const loggedAt = backdateStr ? `${backdateStr} ${backdateTime}` : undefined;
      await mealsApi.relog(meal.id, loggedAt);
      clearCache('dash_');
      clearCache('journal_');
      clearCache('recent_meals');
      clearCache('achievements');
      clearCache('achievement_summary');
      clearCache('challenges');
      hapticSuccess();
      localStorage.setItem('has_logged_meal', '1');
      incrementMealCount();
      trackEvent('recent_relog');
      toast(`Logged "${meal.item_name || meal.meal_description}"`);
      navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true });
    } catch {
      toast('Failed to log meal', 'error');
    } finally {
      setReloggingId(null);
    }
  };

  return (
    <div className="space-y-4">
      {badgeCelebrating && newBadges.length > 0 && (
        <BadgeCelebration
          badges={newBadges}
          onDone={() => { setBadgeCelebrating(false); navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true }); }}
        />
      )}
      {celebrating && (
        <Celebration
          mealId={acceptedMealId}
          onDone={() => {
            if (newBadges.length > 0) {
              setCelebrating(false);
              setBadgeCelebrating(true);
            } else {
              navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true });
            }
          }}
          onSaveAlias={displayNutrition ? async () => {
            try {
              await aliasesApi.create({
                name: displayNutrition.item_name,
                item_name: displayNutrition.item_name,
                meal_description: displayNutrition.meal_description || '',
                calories: displayNutrition.calories,
                protein: displayNutrition.protein,
                carbs: displayNutrition.carbs,
                fat: displayNutrition.fat,
                items_json: JSON.stringify(displayNutrition.items || []),
              });
              clearCache('saved_meals');
              toast('Saved as Quick Log!');
            } catch {
              toast('Failed to save', 'error');
            }
          } : undefined}
          saveAliasDisabledReason={
            savedMealsCap.atLimit
              ? `Saved meals full (${savedMealsCap.used}/${savedMealsCap.limit}) - upgrade for unlimited`
              : null
          }
        />
      )}

      {/* Backdate chip + time picker */}
      {backdateStr && (
        <div
          className="px-3.5 py-2.5 rounded-xl text-sm space-y-2"
          style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)', color: '#3b82f6' }}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold">
              Logging for: {new Date(backdateStr + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </span>
            <button
              onClick={() => { const sp = new URLSearchParams(searchParams); sp.delete('date'); setSearchParams(sp); }}
              className="text-blue-400 font-bold text-xs ml-2"
            >
              Clear
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-blue-500">Time:</span>
            <select
              value={backdateTime}
              onChange={(e) => {
                const val = e.target.value;
                setBackdateTime(val);
                const h = parseInt(val.split(':')[0], 10);
                if (!Number.isNaN(h)) setMealType(hourToMealType(h));
              }}
              className="glass-input py-1 px-2 text-xs flex-1"
            >
              {Array.from({ length: 48 }, (_, i) => {
                const h = Math.floor(i / 2);
                const m = i % 2 === 0 ? '00' : '30';
                const val = `${String(h).padStart(2, '0')}:${m}`;
                const label = `${h === 0 ? 12 : h > 12 ? h - 12 : h}:${m} ${h < 12 ? 'AM' : 'PM'}`;
                return <option key={val} value={val}>{label}</option>;
              })}
            </select>
          </div>
        </div>
      )}

      {/* Meal Type Selector */}
      <div className="flex gap-2">
        {MEAL_TYPES.map((t) => {
          const c = MEAL_COLORS[t];
          const isActive = mealType === t;
          return (
            <button
              key={t}
              onClick={() => { hapticLight(); setMealType(t); }}
              className="flex-1 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center justify-center gap-1.5"
              style={isActive ? {
                background: c.activeBg,
                border: `1px solid ${c.activeBorder}`,
                color: c.active,
                boxShadow: `0 0 12px ${c.activeBg}`,
              } : {
                background: 'var(--bg-card)',
                border: '1px solid var(--border-glass)',
                color: 'var(--text-muted)',
              }}
            >
              <MealTypeIcon type={t} className="w-4 h-4" />
              <span className="capitalize">{t}</span>
            </button>
          );
        })}
      </div>

      {/* Barcode Scanner Overlay */}
      {showBarcodeScanner && (
        <Suspense fallback={
          <div className="fixed inset-0 z-[100] flex items-center justify-center" style={{ background: '#000' }}>
            <div className="flex flex-col items-center gap-3">
              <LoadingSpinner size="lg" />
              <p className="text-white text-sm">Loading scanner...</p>
            </div>
          </div>
        }>
          <BarcodeScanner
            onScan={handleBarcodeScan}
            onClose={() => {
              setShowBarcodeScanner(false);
              setBarcodeLoading(false);
              setBarcodeError(null);
              setBarcodeNotFound(false);
              if (!session.sessionId) {
                navigate(backdateStr ? `/?date=${backdateStr}` : '/', { replace: true });
              }
            }}
            onSwitchPhoto={() => {
              setShowBarcodeScanner(false);
              const sp = new URLSearchParams(searchParams);
              sp.set('mode', 'camera');
              setSearchParams(sp);
            }}
            onSwitchText={() => {
              setShowBarcodeScanner(false);
              const sp = new URLSearchParams(searchParams);
              sp.set('mode', 'text');
              setSearchParams(sp);
            }}
            onManualEntry={() => {
              setShowBarcodeScanner(false);
              setShowManualBarcodeEntry(true);
            }}
            onQrScan={handleQrScan}
          />
        </Suspense>
      )}

      {/* Barcode / QR Loading */}
      {barcodeMode && barcodeLoading && (
        <div className="flex flex-col items-center justify-center py-16 gap-4">
          {qrScanInfo ? (
            <div
              className="rounded-2xl px-5 py-3 text-center max-w-sm"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
            >
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--text-muted)' }}>
                🌐 QR code links to a website
              </p>
              <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                {qrScanInfo.hostname}
              </p>
              <p className="text-[11px] mt-1 break-all" style={{ color: 'var(--text-muted)' }}>
                {qrScanInfo.url.length > 80 ? qrScanInfo.url.slice(0, 80) + '…' : qrScanInfo.url}
              </p>
            </div>
          ) : (
            <div
              className="rounded-2xl px-5 py-3 text-center"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
            >
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--text-muted)' }}>Scanned barcode</p>
              <p className="text-lg font-mono font-semibold tracking-wider" style={{ color: 'var(--text-primary)' }}>
                {scannedBarcodeRef.current}
              </p>
            </div>
          )}
          <LoadingSpinner size="lg" />
          <p className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
            {qrScanInfo ? 'Reading nutrition from page…' : 'Looking up product...'}
          </p>
          <Button variant="secondary" size="sm" onClick={() => {
            setBarcodeLoading(false);
            setBarcodeError(null);
            setBarcodeNotFound(false);
            setBaseNutrition(null);
            setBarcodeImageUrl(null);
            scannedBarcodeRef.current = null;
            setShowBarcodeScanner(true);
          }}>
            Cancel
          </Button>
        </div>
      )}

      {/* Barcode / QR Error - show same fallback options as "not found" */}
      {barcodeMode && barcodeError && (
        <div className="space-y-4">
          <div
            className="rounded-2xl p-5 text-center space-y-2"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
          >
            <p className="text-3xl">{qrScanInfo ? '🌐' : '😕'}</p>
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {qrScanInfo ? "Couldn't read that page" : "Couldn't look up barcode"}
            </p>
            {qrScanInfo ? (
              <p className="text-xs break-all" style={{ color: 'var(--text-muted)' }}>
                {qrScanInfo.hostname}
              </p>
            ) : scannedBarcodeRef.current && (
              <p className="text-xs font-mono tracking-wider" style={{ color: 'var(--text-muted)' }}>
                {scannedBarcodeRef.current}
              </p>
            )}
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {barcodeError || 'Try logging it another way:'}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            {([
              { emoji: '📸', label: 'Photo', sub: 'Snap the label', color: '#10b981', action: () => {
                setBarcodeError(null);
                const sp = new URLSearchParams(searchParams);
                sp.set('mode', 'camera');
                setSearchParams(sp);
              }},
              { emoji: '✏️', label: 'Text', sub: 'Type what it is', color: '#3b82f6', action: () => {
                setBarcodeError(null);
                const sp = new URLSearchParams(searchParams);
                sp.set('mode', 'text');
                setSearchParams(sp);
              }},
              { emoji: '🏷️', label: 'Rescan', sub: 'Try another code', color: '#06b6d4', action: () => {
                setBarcodeError(null);
                setShowBarcodeScanner(true);
              }},
              { emoji: '🔢', label: 'Type barcode', sub: 'Enter code manually', color: '#a855f7', action: () => {
                setBarcodeError(null);
                setShowManualBarcodeEntry(true);
              }},
            ] as const).map((opt, i) => (
              <button
                key={opt.label}
                onClick={() => { hapticLight(); opt.action(); }}
                className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                style={{
                  background: `linear-gradient(145deg, ${opt.color}0C 0%, var(--bg-elevated) 50%, ${opt.color}06 100%)`,
                  border: `1px solid ${opt.color}20`,
                  boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 var(--border-glass), 0 0 40px ${opt.color}10`,
                  backdropFilter: 'blur(32px)',
                  WebkitBackdropFilter: 'blur(32px)',
                  animation: `fabIn 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1) both`,
                }}
              >
                <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                <div
                  className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0"
                  style={{
                    background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`,
                    border: `1px solid ${opt.color}35`,
                    boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20`,
                  }}
                >
                  {opt.emoji}
                </div>
                <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{opt.label}</span>
                <span className="text-[11px] leading-tight mt-0.5" style={{ color: 'var(--text-secondary)' }}>{opt.sub}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Barcode Not Found */}
      {barcodeMode && barcodeNotFound && (
        <div className="space-y-4">
          {/* Scanned barcode + not found message */}
          <div
            className="rounded-2xl p-5 text-center space-y-2"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
          >
            <p className="text-3xl">🤷</p>
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              Product not found
            </p>
            {scannedBarcodeRef.current && (
              <p className="text-xs font-mono tracking-wider" style={{ color: 'var(--text-muted)' }}>
                {scannedBarcodeRef.current}
              </p>
            )}
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Not in our database yet. Log it another way:
            </p>
          </div>
          {/* FAB-style action cards */}
          <div className="grid grid-cols-2 gap-2.5">
            {([
              { emoji: '📸', label: 'Photo', sub: 'Snap the label', color: '#10b981', action: () => {
                setBarcodeNotFound(false);
                const sp = new URLSearchParams(searchParams);
                sp.set('mode', 'camera');
                setSearchParams(sp);
              }},
              { emoji: '✏️', label: 'Text', sub: 'Type what it is', color: '#3b82f6', action: () => {
                setBarcodeNotFound(false);
                const sp = new URLSearchParams(searchParams);
                sp.set('mode', 'text');
                setSearchParams(sp);
              }},
              { emoji: '🏷️', label: 'Rescan', sub: 'Try another code', color: '#06b6d4', action: () => {
                setBarcodeNotFound(false);
                setShowBarcodeScanner(true);
              }},
              { emoji: '🔢', label: 'Type barcode', sub: 'Enter code manually', color: '#a855f7', action: () => {
                setBarcodeNotFound(false);
                setShowManualBarcodeEntry(true);
              }},
            ] as const).map((opt, i) => (
              <button
                key={opt.label}
                onClick={() => { hapticLight(); opt.action(); }}
                className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                style={{
                  background: `linear-gradient(145deg, ${opt.color}0C 0%, var(--bg-elevated) 50%, ${opt.color}06 100%)`,
                  border: `1px solid ${opt.color}20`,
                  boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 var(--border-glass), 0 0 40px ${opt.color}10`,
                  backdropFilter: 'blur(32px)',
                  WebkitBackdropFilter: 'blur(32px)',
                  animation: `fabIn 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1) both`,
                }}
              >
                <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                <div
                  className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0"
                  style={{
                    background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`,
                    border: `1px solid ${opt.color}35`,
                    boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20`,
                  }}
                >
                  {opt.emoji}
                </div>
                <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{opt.label}</span>
                <span className="text-[11px] leading-tight mt-0.5" style={{ color: 'var(--text-secondary)' }}>{opt.sub}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Manual Barcode Entry */}
      {barcodeMode && showManualBarcodeEntry && !barcodeLoading && !session.sessionId && (
        <div className="space-y-4">
          <div
            className="rounded-2xl p-5 text-center space-y-3"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
          >
            <p className="text-3xl">🔢</p>
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              Enter barcode number
            </p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Type the numbers below the barcode on the package
            </p>
            <form
              onSubmit={(e) => { e.preventDefault(); handleManualBarcodeSubmit(); }}
              className="flex gap-2 mt-2"
            >
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                autoFocus
                value={manualBarcodeInput}
                onChange={(e) => setManualBarcodeInput(e.target.value.replace(/[^0-9]/g, ''))}
                placeholder="e.g. 5901234123457"
                className="flex-1 glass-input text-center font-mono tracking-wider"
              />
              <Button
                variant="primary"
                size="md"
                onClick={handleManualBarcodeSubmit}
                disabled={!manualBarcodeInput.trim() || manualBarcodeInput.length < 8 || manualBarcodeInput.length > 14}
              >
                Look up
              </Button>
            </form>
            {manualBarcodeInput && (manualBarcodeInput.length < 8 || manualBarcodeInput.length > 14) && (
              <p className="text-[11px] mt-1" style={{ color: '#ef4444' }}>
                Barcode must be 8–14 digits
              </p>
            )}
          </div>
          <button
            onClick={() => { setShowManualBarcodeEntry(false); setShowBarcodeScanner(true); }}
            className="w-full text-center text-sm font-medium py-2"
            style={{ color: 'var(--text-muted)' }}
          >
            Back to scanner
          </button>
        </div>
      )}

      {/* Image Capture + Text Input */}
      {!session.sessionId && !barcodeMode && (
        <>
          <ImageCapture
            images={images}
            onChange={setImages}
            disabled={atImageLimit}
            addLabel={atImageLimit ? (betaMode ? 'Beta limit reached - log with text instead' : 'Photo limit reached - log with text instead') : 'Add photo'}
          />

          {/* First-time hint for new users */}
          {!localStorage.getItem('has_logged_meal') && !images.length && !text.trim() && !atImageLimit && (
            <p className="text-xs text-center" style={{ color: 'var(--text-secondary)' }}>
              Photo, text, or both - you eat, we count
            </p>
          )}

          {/* Photo-scan usage meter - hidden until the user is at ≥70% of
              the cap (or hit it). Keeps the UI clean for users who never
              come close to the limit; appears just in time to warn those
              about to hit it. Segmented bar turns amber near the limit
              and red at it. */}
          {(imageCap.nearLimit || imageCap.atLimit) && (
            <UsageMeter cap={imageCap} label="Photo scans today" />
          )}

          {atImageLimit && (
            <div className="space-y-2">
              {isPremium ? (
                <div
                  className="rounded-xl p-3 text-xs text-center"
                  style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                >
                  You've used all {imageCap.limit} photo scans today - resets at midnight.
                </div>
              ) : (
                <UpgradeCard feature="image_analysis" />
              )}
              {/* Keep the user moving: text + barcode are both uncapped (or
                  capped separately), so surface them as one-tap alternatives
                  instead of leaving them to hunt for the FAB. */}
              <div
                className="rounded-xl p-3 space-y-2"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
              >
                <p className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
                  Still log this meal -
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => {
                      hapticLight();
                      textareaRef.current?.focus();
                      textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                      setTextGlow(true);
                      setTimeout(() => setTextGlow(false), 2000);
                    }}
                    disabled={textMealCap.atLimit}
                    className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6', border: '1px solid rgba(59,130,246,0.25)' }}
                  >
                    <span>💬</span>
                    <span>{textMealCap.atLimit ? 'Text limit hit' : 'Type what you ate'}</span>
                  </button>
                  <button
                    onClick={() => {
                      hapticLight();
                      const sp = new URLSearchParams(searchParams);
                      sp.set('mode', 'barcode');
                      setSearchParams(sp);
                    }}
                    className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition-all active:scale-95"
                    style={{ background: 'rgba(6,182,212,0.12)', color: '#06b6d4', border: '1px solid rgba(6,182,212,0.25)' }}
                  >
                    <span>🏷️</span>
                    <span>Scan barcode</span>
                  </button>
                </div>
                <p className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>
                  Barcode scans don't count toward your photo cap
                </p>
              </div>
            </div>
          )}

          {/* Text-meal usage meter - hidden until ≥70% of the cap (or at
              the cap). Appears just in time to warn users approaching the
              daily text-entry limit. */}
          {!atImageLimit && images.length === 0 && (textMealCap.nearLimit || textMealCap.atLimit) && (
            <UsageMeter cap={textMealCap} label="Text meals today" />
          )}

          {textMealCap.atLimit && images.length === 0 && (
            <div className="space-y-2">
              {isPremium ? (
                <div
                  className="rounded-xl p-3 text-xs text-center"
                  style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                >
                  You've used all {textMealCap.limit} text meal logs today - resets at midnight.
                </div>
              ) : (
                <UpgradeCard feature="text_meal" />
              )}
              {/* Suggest a barcode scan - doesn't share a cap with text. */}
              {!atImageLimit && (
                <p className="text-[11px] text-center" style={{ color: 'var(--text-muted)' }}>
                  Try a <button
                    onClick={() => {
                      hapticLight();
                      const sp = new URLSearchParams(searchParams);
                      sp.set('mode', 'barcode');
                      setSearchParams(sp);
                    }}
                    className="font-semibold underline"
                    style={{ color: '#06b6d4' }}
                  >barcode scan</button> instead - it doesn't count toward this cap.
                </p>
              )}
            </div>
          )}

          <textarea
            ref={textareaRef}
            autoFocus={textMode || atImageLimit}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleAnalyze();
              }
            }}
            placeholder={atImageLimit ? 'Describe your meal - e.g. "2 eggs and toast with butter"' : 'What\u2019d you eat? e.g. "2 eggs and toast with butter"'}
            rows={3}
            disabled={textMealCap.atLimit && images.length === 0}
            className={`w-full glass-input resize-none disabled:opacity-60 disabled:cursor-not-allowed${textGlow ? ' animate-text-glow' : ''}`}
          />
          <p className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>
            Tap 🎙️ on your keyboard to dictate
          </p>

          {(() => {
            const textOnly = !images.length && text.trim().length > 0;
            const textBlocked = textOnly && textMealCap.atLimit;
            const imageBlocked = images.length > 0 && atImageLimit && !text.trim();
            const disabled = session.analyzing
              || (!images.length && !text.trim())
              || imageBlocked
              || textBlocked;
            return (
              <>
                <Button
                  variant="primary"
                  size="lg"
                  onClick={handleAnalyze}
                  disabled={disabled}
                  className="w-full"
                >
                  {session.analyzing ? <AnalyzingText /> : 'Analyze Meal'}
                </Button>
                {imageBlocked && (
                  <p className="text-xs text-center" style={{ color: '#ef4444' }}>
                    Photo limit reached - add a text description to analyze
                  </p>
                )}
                {textBlocked && (
                  <p className="text-xs text-center" style={{ color: '#ef4444' }}>
                    Text meal limit reached today - try again after midnight
                  </p>
                )}
              </>
            );
          })()}

          {/* Recent Meals - last 10 unique meals for quick re-logging */}
          {recentMeals.length > 0 && (
            <div>
              <div className="flex items-center gap-1.5 mb-2.5">
                <Clock className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                <h2 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Recent</h2>
              </div>
              <div className="flex gap-2.5 overflow-x-auto pb-1 scrollbar-hide" style={{ WebkitOverflowScrolling: 'touch' }}>
                {recentMeals.map((meal) => {
                  const name = meal.item_name || meal.meal_description || 'Meal';
                  const mealLabel = (meal.meal_type || '').charAt(0).toUpperCase() + (meal.meal_type || '').slice(1);
                  let lpTimer: ReturnType<typeof setTimeout> | null = null;
                  let lpFired = false;
                  return (
                    <button
                      key={meal.id}
                      onClick={() => { if (!lpFired) handleRecentRelog(meal); }}
                      disabled={reloggingId === meal.id}
                      onTouchStart={(e) => {
                        lpFired = false;
                        const sx = e.touches[0].clientX, sy = e.touches[0].clientY;
                        lpTimer = setTimeout(() => { lpFired = true; hapticLight(); setPreviewMeal(meal); }, 400);
                        const onMove = (ev: TouchEvent) => {
                          if (Math.abs(ev.touches[0].clientX - sx) > 10 || Math.abs(ev.touches[0].clientY - sy) > 10) { if (lpTimer) clearTimeout(lpTimer); }
                        };
                        const onEnd = () => { if (lpTimer) clearTimeout(lpTimer); document.removeEventListener('touchmove', onMove); document.removeEventListener('touchend', onEnd); };
                        document.addEventListener('touchmove', onMove, { passive: true });
                        document.addEventListener('touchend', onEnd, { once: true });
                      }}
                      className="flex-none rounded-2xl p-3 text-left transition-all duration-200 active:scale-[0.96] disabled:opacity-50 select-none"
                      style={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-glass)',
                        boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
                        backdropFilter: 'blur(12px)',
                        minWidth: '150px',
                        maxWidth: '180px',
                        WebkitTouchCallout: 'none',
                      }}
                    >
                      <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                        {mealLabel}
                      </span>
                      <span className="font-semibold text-sm block truncate mt-0.5" style={{ color: 'var(--text-primary)' }}>
                        {name.length > 22 ? name.slice(0, 22) + '…' : name}
                      </span>
                      <span className="text-xs font-bold tabular-nums block mt-1.5" style={{ color: 'var(--color-calories)' }}>
                        {reloggingId === meal.id ? 'Logging...' : `${Math.round(meal.calories)} cal`}
                      </span>
                      <MacroDisplay protein={meal.protein} carbs={meal.carbs} fat={meal.fat} compact className="mt-1" />
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Saved Meals - tap-to-log cards */}
          <div ref={quickLogRef} />
          {aliases.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2.5">
                <div className="flex items-center gap-1.5">
                  <Bookmark className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                  <h2 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Saved Meals</h2>
                </div>
                <button
                  onClick={() => {
                    hapticLight();
                    if (editingAliases) {
                      // Save order when exiting edit mode
                      aliasesApi.reorder(aliases.map(a => a.name)).catch(() => {});
                    }
                    setEditingAliases((v) => !v);
                  }}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all"
                  style={editingAliases ? {
                    background: 'rgba(16,185,129,0.12)',
                    color: '#10b981',
                  } : {
                    color: 'var(--text-muted)',
                  }}
                >
                  {editingAliases ? (
                    <>
                      <Check className="w-3 h-3" />
                      Done
                    </>
                  ) : (
                    <Pencil className="w-3 h-3" />
                  )}
                </button>
              </div>
              <div
                className="grid grid-cols-2 gap-2.5 select-none"
                style={{ WebkitTouchCallout: 'none' } as React.CSSProperties}
                onTouchStart={(e) => {
                  const btn = (e.target as HTMLElement).closest('[data-alias]');
                  if (!btn) return;
                  if (!editingAliases) {
                    // Long-press to show preview
                    const sx = e.touches[0].clientX, sy = e.touches[0].clientY;
                    const timer = setTimeout(() => {
                      hapticLight();
                      const aliasName = (btn as HTMLElement).dataset.alias;
                      const alias = aliases.find(a => a.name === aliasName);
                      if (alias) {
                        setPreviewMeal({
                          id: 0, logged_at: '',
                          item_name: alias.name,
                          meal_description: alias.meal_description || '',
                          calories: alias.calories, protein: alias.protein,
                          carbs: alias.carbs, fat: alias.fat,
                          meal_type: '', items_json: JSON.stringify(alias.items || []),
                        });
                      }
                    }, 400);
                    (btn as HTMLElement).dataset.lp = String(timer);
                    const onMove = (ev: TouchEvent) => {
                      if (Math.abs(ev.touches[0].clientX - sx) > 10 || Math.abs(ev.touches[0].clientY - sy) > 10) {
                        clearTimeout(timer);
                      }
                    };
                    document.addEventListener('touchmove', onMove, { passive: true });
                    document.addEventListener('touchend', () => document.removeEventListener('touchmove', onMove), { once: true });
                  } else {
                    // Drag reorder in edit mode
                    const idx = aliases.findIndex(a => a.name === (btn as HTMLElement).dataset.alias);
                    if (idx >= 0) handleDragTouchStart(e, idx);
                  }
                }}
                onTouchEnd={(e) => {
                  const btn = (e.target as HTMLElement).closest('[data-alias]');
                  if (btn) {
                    const t = (btn as HTMLElement).dataset.lp;
                    if (t) { clearTimeout(Number(t)); delete (btn as HTMLElement).dataset.lp; }
                  }
                  handleDragTouchEnd();
                }}
                onTouchMove={(e) => {
                  const btn = (e.target as HTMLElement).closest('[data-alias]');
                  if (btn) {
                    const t = (btn as HTMLElement).dataset.lp;
                    if (t) { clearTimeout(Number(t)); delete (btn as HTMLElement).dataset.lp; }
                  }
                  handleDragTouchMove(e);
                }}
              >
                {aliases.map((alias, idx) => (
                  <div
                    key={alias.name}
                    data-alias={alias.name}
                    className={`relative ${editingAliases ? 'animate-wiggle' : ''}`}
                    draggable={editingAliases}
                    onDragStart={(e) => {
                      e.dataTransfer.setData('text/plain', String(idx));
                      (e.target as HTMLElement).style.opacity = '0.5';
                    }}
                    onDragEnd={(e) => { (e.target as HTMLElement).style.opacity = '1'; }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      const fromIdx = Number(e.dataTransfer.getData('text/plain'));
                      const toIdx = idx;
                      if (fromIdx !== toIdx) {
                        setAliases((prev) => {
                          const updated = [...prev];
                          const [moved] = updated.splice(fromIdx, 1);
                          updated.splice(toIdx, 0, moved);
                          return updated;
                        });
                        hapticLight();
                      }
                    }}
                  >
                    {editingAliases && (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); hapticLight(); deleteAlias(alias.name); }}
                          className="absolute -top-1.5 -right-1.5 w-6 h-6 rounded-full flex items-center justify-center z-10"
                          style={{ background: 'rgba(239,68,68,0.9)', boxShadow: '0 2px 8px rgba(0,0,0,0.4)' }}
                        >
                          <X className="w-3 h-3 text-white" />
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); hapticLight(); openEditAlias(alias); }}
                          className="absolute -top-1.5 -left-1.5 w-6 h-6 rounded-full flex items-center justify-center z-10"
                          style={{ background: 'rgba(59,130,246,0.9)', boxShadow: '0 2px 8px rgba(0,0,0,0.4)' }}
                        >
                          <Pencil className="w-3 h-3 text-white" />
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => { if (!editingAliases) handleQuickLog(alias); }}
                      disabled={!editingAliases && loggingAlias === alias.name}
                      className={`relative w-full rounded-2xl p-3.5 text-left transition-all duration-200 ${editingAliases ? 'cursor-grab' : 'active:scale-[0.96]'} disabled:opacity-50`}
                      style={{
                        background: 'var(--bg-card)',
                        border: editingAliases ? '1px solid rgba(239,68,68,0.3)' : '1px solid var(--border-glass)',
                        boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
                        backdropFilter: 'blur(12px)',
                      }}
                    >
                      <span className="font-semibold text-sm block truncate" style={{ color: 'var(--text-primary)' }}>
                        {alias.name}
                      </span>
                      <span className="text-xs font-bold tabular-nums block mt-1.5" style={{ color: 'var(--color-calories)' }}>
                        {loggingAlias === alias.name ? 'Logging...' : `${Math.round(alias.calories)} cal`}
                      </span>
                      <MacroDisplay protein={alias.protein} carbs={alias.carbs} fat={alias.fat} compact className="mt-1" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

        </>
      )}

      {/* Error */}
      {session.error && (
        <Alert variant="error">{session.error}</Alert>
      )}

      {/* Analysis Result */}
      {displayNutrition && (
        <>
          {/* Product image + servings for barcode scans */}
          {barcodeMode && baseNutrition && (
            <div
              className="rounded-2xl p-4 space-y-3"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-glass)' }}
            >
              {barcodeImageUrl && (
                <div className="flex justify-center">
                  <img
                    src={barcodeImageUrl}
                    alt={displayNutrition?.item_name || 'Product'}
                    className="rounded-xl object-contain"
                    style={{ maxHeight: '160px', maxWidth: '100%' }}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                  />
                </div>
              )}
              {/* Correction applied pill - appears when the backend used the
                  user's saved correction instead of OFF/FatSecret. Tapping
                  "Reset" removes the correction and re-fetches fresh data.
                  Colors flow from the --color-calories semantic token
                  (emerald in both themes) via color-mix for tinted
                  backgrounds and borders - matches the existing calorie
                  accent elsewhere in the app and stays theme-aware. */}
              {barcodeCorrectionApplied && (
                <div
                  className="flex items-center justify-between gap-2 rounded-xl px-3 py-2"
                  style={{
                    background: 'color-mix(in srgb, var(--color-calories) 12%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-calories) 30%, transparent)',
                  }}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-[11px] font-semibold" style={{ color: 'var(--color-calories)' }}>
                      ✓ Using your saved correction
                    </p>
                    {barcodeCorrectedAt && (
                      <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                        {(() => {
                          const d = new Date(barcodeCorrectedAt.replace(' ', 'T') + 'Z');
                          if (isNaN(d.getTime())) return 'Previously corrected';
                          const days = Math.round((Date.now() - d.getTime()) / 86400000);
                          if (days <= 0) return 'Corrected today';
                          if (days === 1) return 'Corrected yesterday';
                          if (days < 30) return `Corrected ${days} days ago`;
                          const months = Math.round(days / 30);
                          return `Corrected ${months} month${months === 1 ? '' : 's'} ago`;
                        })()}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={handleResetCorrection}
                    disabled={resettingCorrection}
                    className="text-[11px] font-semibold px-2.5 py-1 rounded-lg active:scale-[0.97] disabled:opacity-50"
                    style={{
                      background: 'var(--bg-elevated)',
                      border: '1px solid color-mix(in srgb, var(--color-calories) 35%, transparent)',
                      color: 'var(--color-calories)',
                    }}
                  >
                    {resettingCorrection ? 'Resetting…' : 'Reset'}
                  </button>
                </div>
              )}
              <ServingsSelector
                value={barcodeServings}
                onChange={handleServingsChange}
                label={barcodeServingLabel}
              />
              {barcodeOffServingLabel && (
                <p className="text-[11px] text-center" style={{ color: 'var(--text-muted)' }}>
                  Serving: {barcodeOffServingLabel}
                </p>
              )}
              {barcodeServingSizeG && (
                barcodeServingUnit === 'ml'
                  ? barcodeServingSizeG > 500 && (
                      <p className="text-[11px] mt-1 text-center" style={{ color: '#f59e0b' }}>
                        Large serving size ({barcodeServingSizeG} ml) - double-check your portion
                      </p>
                    )
                  : barcodeServingSizeG > 200 && (
                      <p className="text-[11px] mt-1 text-center" style={{ color: '#f59e0b' }}>
                        Large serving size ({barcodeServingSizeG}g) - double-check your portion
                      </p>
                    )
              )}
              {barcodeCached && barcodeCachedDaysAgo !== null && barcodeCachedDaysAgo > 30 && (
                <button
                  className="text-[11px] mt-1 w-full text-center"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => {
                    const code = scannedBarcodeRef.current;
                    if (code) {
                      // Clear the frontend cache for this barcode and re-scan
                      try { localStorage.removeItem('bc_cache_' + code); } catch {}
                      handleBarcodeScan(code);
                    }
                  }}
                >
                  Data from cache ({barcodeCachedDaysAgo}d ago) · Tap to refresh
                </button>
              )}
            </div>
          )}

          <MealEditor
            nutrition={displayNutrition}
            onNutritionChange={handleNutritionChange}
            aiMessages={session.messages}
            aiQuestions={session.questions}
            onAiSend={async (t, displayText) => {
              setModified(false);
              setEditedNutrition(null);
              const res = await session.correct(t, displayText);
              if (res && !res.error) {
                setModified(false);
                setEditedNutrition(null);
              }
            }}
            onItemTune={handleItemTune}
            itemTuneDisabled={session.correcting || session.correctionLimitReached}
            aiDisabled={session.correcting}
            onReprocess={images.length > 0 || text.trim() ? async () => {
              setModified(false);
              setEditedNutrition(null);
              await session.analyze(images, text, mealType);
            } : undefined}
            // Feed the per-meal AI-edit cap to CorrectionChat for BOTH
            // free and premium users - server-side the cap applies
            // uniformly in hosted mode. Reading `mealEditLimit` from
            // context means the number reflects the backend's current
            // PRO_AI_EDITS_PER_MEAL (10 in beta) without any hard-coding.
            // `limit <= 0` disables the counter (self-host / unlimited).
            correctionLimit={mealEditLimit > 0 ? { used: session.correctionLimitReached ? mealEditLimit : session.correctionCount, limit: mealEditLimit, isPremium } : undefined}
          />

          {/* Accept / Cancel */}
          <div className="flex gap-3">
            <Button
              variant="primary"
              size="lg"
              onClick={handleAccept}
              disabled={session.accepting || celebrating}
              className="flex-1"
            >
              <Check className="w-4 h-4" />
              {session.accepting ? 'Logging...' : 'Log It'}
            </Button>
            <Button
              variant="secondary"
              size="lg"
              onClick={() => {
                session.cancel();
                if (barcodeMode) {
                  resetBarcodeUiState();
                  setEditedNutrition(null);
                  setModified(false);
                  setBarcodeError(null);
                  setBarcodeNotFound(false);
                  setShowBarcodeScanner(true);
                }
              }}
              className="flex-1"
            >
              <X className="w-4 h-4" />
              Cancel
            </Button>
          </div>
        </>
      )}

      {/* Edit Alias Modal - uses the same NutritionTable as meal editing */}
      <Modal open={!!editAlias} onClose={() => setEditAlias(null)} position="bottom">
        {editAlias && (
          <div
            className="p-5 space-y-3 max-h-[90vh] overflow-y-auto"
            style={{ paddingBottom: 'calc(1.25rem + env(safe-area-inset-bottom, 0px))' }}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Edit Saved Meal</span>
              <button onClick={() => setEditAlias(null)} className="p-2 -mr-2 rounded-xl" style={{ color: 'var(--text-muted)' }}>
                <X className="w-5 h-5" />
              </button>
            </div>
            <MealEditor
              nutrition={editAlias.nutrition}
              onNutritionChange={(n) => setEditAlias({ ...editAlias, nutrition: n })}
            />
            <Button
              variant="primary"
              size="lg"
              onClick={handleSaveAlias}
              disabled={savingAlias}
              className="w-full"
            >
              <Check className="w-4 h-4" /> {savingAlias ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        )}
      </Modal>

      {previewMeal && (
        <MealPreview meal={previewMeal} onClose={() => setPreviewMeal(null)} />
      )}
    </div>
  );
}
