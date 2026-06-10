import { useEffect, useState, useCallback, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { mealsApi } from '../api/meals';
import { formatLocalDate } from '../utils/date';
import MealCard from '../components/MealCard';
import Button from '../components/Button';
import MacroDisplay from '../components/MacroDisplay';
import SwipeActions from '../components/SwipeActions';
import { JournalSkeleton, OfflineAwareSkeleton } from '../components/Skeleton';
import MonthCalendar from '../components/MonthCalendar';
import WeightSummaryCard from '../components/WeightSummaryCard';
import { ClipboardList, LayoutGrid, List, Image, Search } from '../components/icons';
import { useToast } from '../components/Toast';
import { useRegisterRefresh } from '../context/PullToRefreshContext';
import LoadingSpinner from '../components/LoadingSpinner';
import BadgeCelebration from '../components/BadgeCelebration';
import type { NewBadge } from '../types';
import { getCached, setCache, clearCache } from '../utils/apiCache';
import { schedulePendingDelete, cancelPendingDelete, isDeletePending } from '../utils/pendingMealDelete';
import { incrementMealCount } from '../utils/onboarding';
import { track } from '../api/analytics';
import type { Meal } from '../types';

import { IMG_BASE, thumbUrl } from '../components/ProgressiveImage';
const FILTERS = ['all', 'breakfast', 'lunch', 'snack', 'dinner'] as const;

export default function Journal() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initFilter = searchParams.get('type') || 'all';
  const initDate = searchParams.get('date') || '';
  const cachedMeals = getCached<Meal[]>(`journal_${initFilter}_${initDate}`);
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;

  const [meals, setMeals] = useState<Meal[]>(cachedMeals || []);
  const [filter, setFilter] = useState<string>(initFilter);
  const [search, setSearch] = useState('');
  const [dateFilter, setDateFilter] = useState(initDate);
  const [loading, setLoading] = useState(!cachedMeals);
  const [relogBadges, setRelogBadges] = useState<NewBadge[]>([]);
  const [fetchError, setFetchError] = useState('');
  const [hasMore, setHasMore] = useState(true);
  const [view, setView] = useState<'list' | 'gallery'>('list');
  const offsetRef = useRef(0);
  const { toast } = useToast();
  const mealsRef = useRef<Meal[]>(meals);
  mealsRef.current = meals;
  // Semantic search results live in their own state below; the ref lets
  // deleteMeal look up + mutate them without depending on the state being
  // declared further down. Without this, swipe-deleting a meal that only
  // appears in search results was a silent no-op (lookup miss).
  const [searchResults, setSearchResults] = useState<Meal[] | null>(null);
  const searchResultsRef = useRef<Meal[] | null>(searchResults);
  searchResultsRef.current = searchResults;

  // Strip meals that the user just deleted-with-undo from any server-derived
  // list. Without this, a refresh that fires during the 5-second undo window
  // (pull-to-refresh, focus refresh, copy-yesterday reload, etc.) repopulates
  // state with the server's pre-delete view and the just-deleted card flashes
  // back into the UI until the deferred DELETE finally lands.
  const dropPending = useCallback((list: Meal[]): Meal[] =>
    list.filter((m) => !isDeletePending(m.id)), []);

  // Sync filter + date to URL so navigating back restores state
  const syncUrlParams = useCallback((f: string, d: string) => {
    const params: Record<string, string> = {};
    if (f && f !== 'all') params.type = f;
    if (d) params.date = d;
    setSearchParamsRef.current(params, { replace: true });
  }, []);

  const deleteMeal = useCallback((id: number) => {
    // Search-mode swipe deletes go through here too. The meal may live only
    // in searchResults (if it's older than the paginated journal slice) or
    // only in meals (no active search) - look in both.
    const meal =
      mealsRef.current.find((m) => m.id === id)
      ?? searchResultsRef.current?.find((m) => m.id === id);
    if (!meal) return;

    setMeals((prev) => prev.filter((m) => m.id !== id));
    setSearchResults((prev) => (prev ? prev.filter((m) => m.id !== id) : prev));
    schedulePendingDelete(id, {
      cachesToClear: ['dash_day_', 'dash_trend', 'achievements', 'achievement_summary', 'challenges'],
    });

    toast('Meal deleted', 'success', {
      label: 'Undo',
      onClick: () => {
        if (cancelPendingDelete(id)) {
          setMeals((prev) =>
            prev.some((m) => m.id === meal.id) ? prev : [...prev, meal].sort((a, b) => b.id - a.id),
          );
          // Restore to the search list too so the card reappears in place
          // when the user is mid-search. Prepend rather than re-rank - we
          // didn't capture the original score index, and prepending matches
          // the user's mental model ("the thing I just undeleted is on top").
          setSearchResults((prev) =>
            prev && !prev.some((m) => m.id === meal.id) ? [meal, ...prev] : prev,
          );
        }
      },
    });
  }, [toast]);

  const relogMeal = useCallback(async (id: number) => {
    try {
      const res = await mealsApi.relog(id);
      clearCache('dash_');
      localStorage.setItem('has_logged_meal', '1');
      incrementMealCount();
      toast('Meal re-logged!');
      if (res.new_badges?.length) {
        setRelogBadges(res.new_badges);
        clearCache('achievements');
        clearCache('achievement_summary');
        clearCache('challenges');
      }
    } catch {
      toast('Failed to re-log', 'error');
    }
  }, [toast]);

  const refreshJournal = useCallback(async () => {
    setFetchError('');
    offsetRef.current = 0;
    setHasMore(true);
    const data = await mealsApi.list({
      type: filter === 'all' ? undefined : filter,
      date: dateFilter || undefined,
      limit: 20,
      offset: 0,
      refresh: true,
    });
    setMeals(dropPending(data));
    offsetRef.current = data.length;
    if (data.length < 20) setHasMore(false);
    setCache(`journal_${filter}_${dateFilter}`, data);
  }, [filter, dateFilter, dropPending]);

  useRegisterRefresh(refreshJournal);

  const isFirstLoad = useRef(true);

  const loadMeals = useCallback(async (reset = false) => {
    if (reset) {
      offsetRef.current = 0;
      setHasMore(true);
    }
    // Skip loading spinner only on very first load when we have cached data
    const firstLoad = isFirstLoad.current;
    const skipSpinner = firstLoad && reset && !!getCached<Meal[]>(`journal_${filter}_${dateFilter}`);
    isFirstLoad.current = false;
    if (!skipSpinner) setLoading(true);
    setFetchError('');
    try {
      const data = await mealsApi.list({
        type: filter === 'all' ? undefined : filter,
        date: dateFilter || undefined,
        limit: 20,
        offset: offsetRef.current,
        // Use refresh on first mount so newly logged meals appear
        refresh: firstLoad && reset,
      });
      if (reset) {
        setMeals(dropPending(data));
        setCache(`journal_${filter}_${dateFilter}`, data);
      } else {
        setMeals((prev) => [...prev, ...dropPending(data)]);
      }
      offsetRef.current += data.length;
      if (data.length < 20) setHasMore(false);
    } catch (err) {
      console.error(err);
      setFetchError('Could not load meals. Pull down to refresh.');
    } finally {
      setLoading(false);
    }
  }, [filter, dateFilter, dropPending]);

  useEffect(() => {
    loadMeals(true);
  }, [loadMeals]);

  // Semantic search backed by /meals/search. Debounced 300ms.
  //
  // Stale-while-revalidate: we keep the previous result set visible while the
  // next call is in flight so typing doesn't oscillate "many → 0 → many" as
  // each keystroke briefly drops searchResults and the substring fallback
  // returns nothing for early prefixes ("M", "Mo", …). Only true network
  // failures bump back to the substring filter.
  // (searchResults state is declared above so deleteMeal can mutate it.)
  const [searchInFlight, setSearchInFlight] = useState(false);
  useEffect(() => {
    const q = search.trim();
    if (!q) {
      setSearchResults(null);
      setSearchInFlight(false);
      return;
    }
    let cancelled = false;
    setSearchInFlight(true);
    const timer = setTimeout(async () => {
      try {
        const res = await mealsApi.search(q, 50);
        if (!cancelled) setSearchResults(res);
      } catch {
        // Network/rate-limit: keep whatever we last had (don't blank out).
      } finally {
        if (!cancelled) setSearchInFlight(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [search]);

  // While searching: prefer the most recent semantic result set. Only when we
  // have NO result at all (very first keystroke before any backend response)
  // do we fall back to the local substring filter for instant feedback.
  const searchLower = search.toLowerCase();
  const filtered = !search
    ? meals
    : searchResults
      ? searchResults
      : meals.filter((m) => m.item_name.toLowerCase().includes(searchLower));

  // Group meals by date
  const groups: Record<string, Meal[]> = {};
  for (const m of filtered) {
    const dateStr = m.logged_at.split(' ')[0];
    if (!groups[dateStr]) groups[dateStr] = [];
    groups[dateStr].push(m);
  }

  const now = new Date();
  const today = formatLocalDate(now);
  const yd = new Date(now);
  yd.setDate(yd.getDate() - 1);
  const yesterday = formatLocalDate(yd);
  const formatDate = (d: string) => {
    if (d === today) return 'Today';
    if (d === yesterday) return 'Yesterday';
    return new Date(d + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  };

  const photosOnly = filtered.filter((m) => !!m.image_path);

  return (
    <div className="space-y-4">
      {relogBadges.length > 0 && (
        <BadgeCelebration badges={relogBadges} onDone={() => setRelogBadges([])} />
      )}
      <h1 className="text-xl font-bold">Journal</h1>

      {/* Month Calendar */}
      <MonthCalendar selectedDate={dateFilter} onSelectDate={(d) => {
        setDateFilter(d);
        syncUrlParams(filter, d);
        window.dispatchEvent(new CustomEvent('viewingDate', { detail: d }));
      }} />

      {/* Weight Summary */}
      <WeightSummaryCard />

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search meals..."
          className="w-full glass-input pl-9 py-2.5 text-sm"
          style={search ? { paddingRight: '5rem' } : undefined}
        />
        {search && (
          <span
            className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] font-semibold px-2 py-0.5 rounded-full"
            style={{ background: 'rgba(16,185,129,0.12)', color: '#10b981', maxWidth: '5rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {filtered.length} result{filtered.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Add meal to selected past day */}
      {dateFilter && dateFilter <= today && (
        <Link
          to={`/log?date=${dateFilter}`}
          className="w-full glass-card-hover py-2.5 text-sm text-center font-semibold flex items-center justify-center gap-2"
          style={{ color: '#3b82f6' }}
        >
          + Add meal to {dateFilter === today ? 'today' : formatDate(dateFilter)}
        </Link>
      )}

      {/* Filter Tabs + View Toggle */}
      <div className="flex items-center gap-2">
        <div className="flex gap-1.5 overflow-x-auto flex-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => {
                setFilter(f);
                syncUrlParams(f, dateFilter);
                track('ui_journal_filter_applied', { meal_type: f });
              }}
              className={filter === f ? 'pill-active' : 'pill-inactive'}
            >
              {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <div className="flex gap-1 p-0.5 rounded-lg shrink-0" style={{ background: 'var(--bg-elevated)' }}>
          <button
            onClick={() => setView('list')}
            className={`p-1.5 rounded-md transition-colors ${view === 'list' ? 'bg-emerald-500/20 text-emerald-400' : ''}`}
            style={view !== 'list' ? { color: 'var(--text-muted)' } : undefined}
            title="List view"
          >
            <List className="w-4 h-4" />
          </button>
          <button
            onClick={() => setView('gallery')}
            className={`p-1.5 rounded-md transition-colors ${view === 'gallery' ? 'bg-emerald-500/20 text-emerald-400' : ''}`}
            style={view !== 'gallery' ? { color: 'var(--text-muted)' } : undefined}
            title="Gallery view"
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
        </div>
      </div>

      {fetchError && !loading && (
        <p className="text-xs text-center py-4" style={{ color: 'var(--text-secondary)' }}>{fetchError}</p>
      )}

      {view === 'list' ? (
        // Reserve a stable min-height for the results region while a search
        // is active. Without this, pages with no/few hits would visibly
        // collapse vs pages with many hits, and the page itself would
        // shrink - causing the visible chrome (input, calendar above, page
        // bottom) to lurch up/down between keystrokes. With the min-height,
        // empty space below the results absorbs the variance silently.
        <div style={search ? { minHeight: '60vh' } : undefined}>
          {/* Semantic-ranked search results: render flat in score order so the
              best match is first. Skip date grouping (which would hide ranking). */}
          {searchResults && searchResults.length > 0 ? (
            <div className="space-y-2">
              {filtered.map((m) => (
                <SwipeActions key={m.id} onDelete={() => deleteMeal(m.id)} onRelog={() => relogMeal(m.id)}>
                  <MealCard meal={m} />
                </SwipeActions>
              ))}
            </div>
          ) : (
            <>
              {/* Meal Groups - List View */}
              {Object.entries(groups).map(([dateStr, dateMeals]) => {
                const dayCals = dateMeals.reduce((s, m) => s + m.calories, 0);
                const dayP = dateMeals.reduce((s, m) => s + m.protein, 0);
                const dayC = dateMeals.reduce((s, m) => s + m.carbs, 0);
                const dayF = dateMeals.reduce((s, m) => s + m.fat, 0);

                return (
                  <div key={dateStr}>
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="section-heading">{formatDate(dateStr)}</h3>
                      <div className="flex items-center gap-2 text-xs font-semibold">
                        <span className="tabular-nums" style={{ color: 'var(--color-calories)' }}>{Math.round(dayCals)} kcal</span>
                        <MacroDisplay protein={dayP} carbs={dayC} fat={dayF} compact />
                      </div>
                    </div>
                    <div className="space-y-2">
                      {dateMeals.map((m) => (
                        <SwipeActions key={m.id} onDelete={() => deleteMeal(m.id)} onRelog={() => relogMeal(m.id)}>
                          <MealCard meal={m} />
                        </SwipeActions>
                      ))}
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      ) : (
        <>
          {/* Photo Gallery View */}
          {photosOnly.length > 0 ? (
            <div className="grid grid-cols-3 gap-1.5">
              {photosOnly.map((m) => (
                <Link
                  key={m.id}
                  to={`/meals/${m.id}`}
                  className="relative aspect-square rounded-xl overflow-hidden group"
                >
                  <img
                    src={thumbUrl(m.image_path)}
                    alt={m.item_name}
                    className="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
                    loading="lazy"
                    onError={(e) => { (e.target as HTMLImageElement).src = IMG_BASE + m.image_path; }}
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                  <div className="absolute bottom-0 left-0 right-0 p-1.5">
                    <p className="text-[11px] text-white font-medium truncate">{m.item_name}</p>
                    <p className="text-[11px] text-white/70">{Math.round(m.calories)} kcal</p>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="glass-card p-12 text-center" style={{ color: 'var(--text-secondary)' }}>
              <Image className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--text-muted)' }} />
              <p>{search || filter !== 'all' ? 'No photos match your filters' : 'No photos yet'}</p>
              <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                {search || filter !== 'all'
                  ? 'Try clearing your search or filter'
                  : 'Photos from your meals will show up here'}
              </p>
            </div>
          )}
        </>
      )}

      {filtered.length === 0 && !loading && view === 'list' && (
        search ? (
          // Compact inline message during search so the page height doesn't
          // bounce between "tall empty card" and "flat list" as the result
          // count flips. The big empty-state card is reserved for the
          // genuinely-empty journal (no meals at all).
          <p className="text-sm text-center py-4" style={{ color: 'var(--text-muted)' }}>
            {searchInFlight ? 'Searching…' : 'No matching meals'}
          </p>
        ) : (
          <div className="glass-card p-12 text-center" style={{ color: 'var(--text-secondary)' }}>
            <ClipboardList className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--text-muted)' }} />
            <p>Your meal history will appear here</p>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              Log your first meal to start building your food journal
            </p>
          </div>
        )
      )}

      {/* Load More */}
      {hasMore && !loading && meals.length > 0 && !search && (
        <Button variant="secondary" onClick={() => loadMeals(false)} className="w-full">
          Load more
        </Button>
      )}

      {loading && meals.length === 0 ? (
        <OfflineAwareSkeleton skeleton={<JournalSkeleton />} />
      ) : loading ? (
        <div className="flex justify-center py-4">
          <LoadingSpinner />
        </div>
      ) : null}
    </div>
  );
}
