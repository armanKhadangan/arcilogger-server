import { FileDown, Lock, Search, ShieldUser } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Histogram } from '@/components/charts/histogram';
import { clipSpan } from '@/components/charts/histogram-data';
import { RawLineSheet, type RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage, isApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { parseId } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { useHandoff } from '@/lib/handoff';
import { currentLang } from '@/lib/i18n';
import {
  useParseQuery,
  useSearchFields,
  useSearchSession,
  useSearchStats,
} from '@/lib/search-hooks';
import type { Notice, SavedSearch, SearchFacets, SearchRow } from '@/lib/search-types';
import {
  DEFAULT_MAX_DAYS,
  fitRange,
  rangeKey,
  rangeToParams,
  resolveRange,
  useTimeRange,
  type TimeRange,
} from '@/lib/time-range';

import { EvidenceAction } from './evidence-action';
import { ExportDialog } from './export-dialog';
import { FacetPanel } from './facet-panel';
import { PartialBanner } from './partial-banner';
import { QueryBar } from './query-bar';
import { ResultsTable } from './results-table';
import { SavedMenu } from './saved-menu';
import {
  addClause,
  FACET_FIELDS,
  fieldNames,
  problemOf,
  scanState,
  type QueryProblem,
} from './search-model';
import { EventSheet } from './event-sheet';
import { EXAMPLES, SyntaxHelp } from './syntax-help';

/** Time after the last key before the query is read by the server (contract 2.22.2). */
export const PARSE_DELAY_MS = 250;

/** A search to run once the KVKK notice is accepted. */
type Waiting = { q: string; range: TimeRange } | { saved: number };

/** The notices of the page and of its statistics, each code once. */
function noticesOf(...lists: (Notice[] | undefined)[]): Notice[] {
  const seen = new Set<string>();
  return lists
    .flatMap((list) => list ?? [])
    .filter((notice) => !seen.has(notice.code) && seen.add(notice.code));
}

/**
 * Arama (spec 12.3 page 5; `search.read`): field and text search over the loaded log lines, each
 * row leading to its raw line. The route carries `handle.timeRange`, so the top bar shows the
 * global time range here and on no other page.
 *
 * Nothing is searched when the page opens: a search runs when the user asks (the query bar, a
 * saved search, `?saved=<id>`), when a term is handed over (the top bar, Canlı Akış, the Panel;
 * `lib/handoff.ts`), and again with the same query when the time range changes (the picker, a
 * histogram zoom) or a facet edits the query. Every run is a new audited search (`search.run`);
 * the query text is never in the address, the tab's history or the browser's storage — the
 * address holds only the range and a saved search's id.
 */
export function SearchPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can, me } = useAuth();
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;
  const [params, setParams] = useSearchParams();
  const fields = useSearchFields();
  const maxDays = fields.data?.limits.max_days ?? DEFAULT_MAX_DAYS;
  const { range, setRange } = useTimeRange(maxDays);
  const session = useSearchSession();
  const { state, run, next, renew, withSearch } = session;
  const stats = useSearchStats(privacyPending ? null : (state.first?.search_id ?? null));
  const [text, setText] = useState('');
  const [debounced, setDebounced] = useState('');
  const parse = useParseQuery(debounced);
  const [helpOpen, setHelpOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [selected, setSelected] = useState<SearchRow | null>(null);
  const [raw, setRaw] = useState<RawLineTarget | null>(null);
  /** The query the shown results belong to (facets edit it). */
  const committed = useRef('');
  /** The range key of the last run: a different range in the address runs the query again. */
  const ranWith = useRef<string | null>(null);
  const waiting = useRef<Waiting | null>(null);
  /** The range the last search ran on (a refused one runs again on it). */
  const rangeAt = useRef<TimeRange>(range);
  const names = useMemo(() => fieldNames(fields.data?.fields ?? []), [fields.data]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(text);
    }, PARSE_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [text]);

  const dropSaved = useCallback(() => {
    setParams(
      (current) => {
        if (!current.has('saved')) return current;
        const nextParams = new URLSearchParams(current);
        nextParams.delete('saved');
        return nextParams;
      },
      { replace: true },
    );
  }, [setParams]);

  /** Run `q` on `on` (resolved against the clock now). */
  const search = useCallback(
    (q: string, on: TimeRange) => {
      committed.current = q;
      ranWith.current = rangeKey(on);
      rangeAt.current = on;
      setSelected(null);
      if (privacyPending) {
        waiting.current = { q, range: on };
        return;
      }
      const { from, to } = resolveRange(on);
      void run({ q, from, to });
    },
    [privacyPending, run],
  );

  const searchSaved = useCallback(
    (id: number) => {
      setSelected(null);
      if (privacyPending) {
        waiting.current = { saved: id };
        return;
      }
      void run({ saved_id: id }).then((page) => {
        if (!page) return;
        committed.current = page.query.normalized;
        setText(page.query.normalized);
      });
    },
    [privacyPending, run],
  );

  // The picker or a zoom changed the range: the same query, a new search. Declared before the
  // hand-over: a hand-over taken when the page opens runs in the same commit, and this effect must
  // not see its range as already run while the address still holds the old one (it would search
  // again on that range and write it back).
  const key = rangeKey(range);
  useEffect(() => {
    if (ranWith.current === null || ranWith.current === key) return;
    dropSaved();
    search(committed.current, range);
  }, [key, range, search, dropSaved]);

  // A term handed over by the top bar (or Canlı Akış, the Panel): run it, with its range if any.
  useHandoff('/search', (payload) => {
    setText(payload.q);
    dropSaved();
    const on = payload.range ? fitRange(payload.range, maxDays) : range;
    if (payload.range) {
      ranWith.current = rangeKey(on);
      setRange(on);
    }
    search(payload.q, on);
  });

  // `?saved=<id>` when the page opens (a reload after choosing a saved search), once the field
  // catalogue says how long a range may be (the address's preset may not fit it).
  const openedWith = useRef(parseId(params.get('saved')));
  useEffect(() => {
    const id = openedWith.current;
    if (id === null || fields.isPending) return;
    openedWith.current = null;
    ranWith.current = rangeKey(range);
    searchSaved(id);
    // Only once, when the page opens and the catalogue has answered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields.isPending]);

  // The KVKK notice was accepted: the search that waited for it runs now.
  useEffect(() => {
    const wanted = waiting.current;
    if (privacyPending || !wanted) return;
    waiting.current = null;
    if ('saved' in wanted) searchSaved(wanted.saved);
    else search(wanted.q, wanted.range);
  }, [privacyPending, search, searchSaved]);

  // The server asked for the KVKK notice first: the refused search runs once it is accepted.
  useEffect(() => {
    const input = state.input;
    if (!isApiError(state.error, 'privacy_notice_required') || input === null) return;
    waiting.current =
      'saved_id' in input ? { saved: input.saved_id } : { q: input.q, range: rangeAt.current };
  }, [state.error, state.input]);

  // Statistics of an expired search: the search runs again (once, `useSearchSession`).
  useEffect(() => {
    if (isApiError(stats.error, 'search_expired')) void renew();
  }, [stats.error, renew]);

  function submit(q: string) {
    dropSaved();
    search(q, range);
  }

  function runSaved(item: SavedSearch) {
    // The address reads a preset too long for the site as the default; the server refuses the
    // saved search itself with `search_range_too_long`.
    ranWith.current = rangeKey(fitRange(item.range, maxDays));
    setParams(
      (current) => {
        const nextParams = rangeToParams(item.range, current);
        nextParams.set('saved', String(item.id));
        return nextParams;
      },
      { replace: true },
    );
    searchSaved(item.id);
  }

  function addFacet(facet: keyof SearchFacets, value: string, negated: boolean) {
    const q = addClause(committed.current, FACET_FIELDS[facet], value, negated, names);
    setText(q);
    dropSaved();
    search(q, range);
  }

  const problems: QueryProblem[] = useMemo(() => {
    const refused = problemOf(state.error);
    const input = state.input;
    if (refused && input && 'q' in input && input.q === text) return [refused];
    if (parse.data && debounced === text) return parse.data.errors;
    return [];
  }, [state.error, state.input, text, parse.data, debounced]);

  const first = state.first;
  const last = state.last;
  // A refusal of the run itself (rows cleared), unless the query bar or the KVKK notice shows it.
  const runError =
    state.error !== null &&
    first === null &&
    problemOf(state.error) === null &&
    !isApiError(state.error, 'privacy_notice_required');
  // A failed page: the rows stay, and an incomplete scan waits for "Devam et".
  const pageError = state.error !== null && first !== null;
  const scan = last ? scanState(last, state.continues, state.rows.length, pageError) : null;
  const notices = noticesOf(first?.notices, stats.data?.notices);
  const kinds = fields.data?.kinds ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.search')}
        description={t('search.description')}
        actions={
          <>
            <SavedMenu
              query={first ? committed.current : null}
              range={range}
              onRun={runSaved}
              enabled={!privacyPending}
            />
            {can('search.export') && first ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setExportOpen(true);
                }}
              >
                <FileDown aria-hidden="true" />
                {t('search.export.open')}
              </Button>
            ) : null}
            {can('evidence.create') && first ? (
              <EvidenceAction page={first} withSearch={withSearch} />
            ) : null}
          </>
        }
      />
      <p className="-mt-4 flex items-center gap-2 text-13 text-muted">
        <Lock aria-hidden="true" className="size-4 shrink-0" />
        {t('search.privacy')}
      </p>

      <QueryBar
        value={text}
        onChange={setText}
        onSubmit={submit}
        onHelp={() => {
          setHelpOpen(true);
        }}
        catalogue={fields.data}
        facets={stats.data?.facets}
        problems={problems}
        busy={state.running}
      />
      {first ? (
        <p className="text-xs text-muted tabular">
          {t('search.range', {
            from: formatDateTime(first.range.from),
            to: formatDateTime(first.range.to),
          })}
        </p>
      ) : null}

      {runError ? (
        <Alert tone="danger">
          <p>{errorMessage(state.error)}</p>
          {isApiError(state.error) && state.error.retryAfter !== null ? (
            <p>{t('search.retryAfter', { seconds: state.error.retryAfter })}</p>
          ) : null}
        </Alert>
      ) : null}
      {notices.map((notice) => (
        <Alert key={notice.code} tone="info" assertive={false}>
          {lang === 'en' ? notice.message_en : notice.message_tr}
        </Alert>
      ))}

      {privacyPending ? (
        <EmptyState
          icon={ShieldUser}
          title={t('search.privacyWait.title')}
          description={t('search.privacyWait.body')}
        />
      ) : first === null ? (
        state.running ? (
          <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
            <Skeleton className="h-40" />
            <Skeleton className="h-96" />
          </div>
        ) : runError ? null : (
          <EmptyState
            icon={Search}
            title={t('search.empty.title')}
            description={t('search.empty.body')}
            action={
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.slice(0, 3).map((example) => (
                  <Button
                    key={example}
                    variant="secondary"
                    size="sm"
                    className="font-mono text-xs"
                    aria-label={t('search.help.useExample', { query: example })}
                    onClick={() => {
                      // Like the syntax help's "Kullan": only "Ara" runs (and audits) a search,
                      // so an example's address is never searched by a click alone.
                      setText(example);
                    }}
                  >
                    {example}
                  </Button>
                ))}
              </div>
            }
          />
        )
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
          <FacetPanel stats={stats.data} loading={stats.isPending} onAdd={addFacet} />
          <div className="flex min-w-0 flex-col gap-4">
            {stats.data ? (
              <Histogram
                buckets={stats.data.histogram.buckets}
                bucketSeconds={stats.data.histogram.bucket_seconds}
                covered={
                  stats.data.complete
                    ? null
                    : { from: stats.data.covered_from, to: stats.data.covered_to }
                }
                total={stats.data.total}
                onZoom={(span) => {
                  // Whole buckets may start before the search's range and end after it.
                  const zoom = clipSpan(span, first.range);
                  if (zoom) setRange(zoom);
                }}
              />
            ) : stats.isError && !isApiError(stats.error, 'search_expired') ? (
              <Alert tone="warning">{errorMessage(stats.error)}</Alert>
            ) : (
              <Skeleton className="h-52" />
            )}
            <ResultsTable
              rows={state.rows}
              kinds={kinds}
              state={scan}
              loadingMore={state.paging === 'more'}
              onOpen={setSelected}
              onRaw={(row) => {
                setRaw({ ref: row.raw });
              }}
              onMore={() => void next('more')}
              emptyMessage={
                state.running
                  ? t('common.loading')
                  : scan === 'continuing' || scan === 'paused' || scan === 'stopped'
                    ? t('search.results.noneYet')
                    : t('common.noResults')
              }
            />
            {last && scan ? (
              <PartialBanner
                first={first}
                last={last}
                state={scan}
                busy={state.paging === 'continue'}
                onContinue={() => void next('continue')}
              />
            ) : null}
            {pageError ? <Alert tone="danger">{errorMessage(state.error)}</Alert> : null}
          </div>
        </div>
      )}

      <SyntaxHelp
        open={helpOpen}
        onOpenChange={setHelpOpen}
        catalogue={fields.data}
        onUse={(example) => {
          setHelpOpen(false);
          setText(example);
        }}
      />
      {can('search.export') ? (
        <ExportDialog open={exportOpen} onOpenChange={setExportOpen} withSearch={withSearch} />
      ) : null}
      <EventSheet
        row={selected}
        catalogue={fields.data}
        onClose={() => {
          setSelected(null);
        }}
        onRaw={(row) => {
          setRaw({ ref: row.raw });
        }}
      />
      <RawLineSheet
        target={raw}
        onClose={() => {
          setRaw(null);
        }}
      />
    </div>
  );
}
