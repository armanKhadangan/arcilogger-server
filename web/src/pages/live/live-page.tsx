import {
  CircleDashed,
  Eraser,
  Highlighter,
  Lock,
  Pause,
  Play,
  RefreshCw,
  Search,
  ShieldUser,
  Signal,
  Square,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { RawLineSheet, type RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import type { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatNumber } from '@/lib/format';
import { handoff, type HandoffPayloads } from '@/lib/handoff';
import { currentLang } from '@/lib/i18n';
import { LiveConnection, RECONNECTING_END_CODE, type LiveConnectionState } from '@/lib/live-stream';
import type { LiveDropped, LiveEnd, LiveLine } from '@/lib/live-types';
import { DROP_REASONS } from '@/lib/live-types';
import { useSearchFields } from '@/lib/search-hooks';

import { HighlightDialog } from './highlight-dialog';
import { LineSheet } from './line-sheet';
import { LiveFilters } from './live-filters';
import { LiveList, type LiveView } from './live-list';
import {
  addDropped,
  ANNOUNCE_MS,
  appendLines,
  droppedTotal,
  EMPTY_FILTER,
  FLUSH_MS,
  liveQuery,
  loadRules,
  NO_DROPS,
  RATE_SECONDS,
  saveRules,
  searchGap,
  type DropTotals,
  type HighlightRule,
  type LiveFilter,
} from './live-model';

type PageState = LiveConnectionState | 'idle';

const STATE_STYLE: Record<
  LiveConnectionState,
  { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }
> = {
  connecting: { tone: 'info', icon: CircleDashed },
  connected: { tone: 'success', icon: Signal },
  reconnecting: { tone: 'warning', icon: RefreshCw },
  paused: { tone: 'neutral', icon: Pause },
  stopped: { tone: 'neutral', icon: Square },
};

/** End codes after which the page offers to start again ("Sürdür"). */
const RESTARTABLE_ENDS: readonly string[] = ['time_limit', 'slow_client'];

const EMPTY_VIEW: LiveView = { lines: [], removed: 0, added: 0 };

/**
 * Canlı Akış (spec 12.3 page 4; `search.read`, KVKK notice): the lines of the sources as they
 * arrive, read by the API from the raw spool (`lib/live-stream.ts`), so it works while ingest lags
 * or is stopped. Nothing streams until the user presses Başlat (the server audits each stream,
 * `live.open`, with its filter); the filter is a query of the search language (`live-filters`).
 *
 * The page holds the newest 2,000 lines; Duraklat stops reading (the server keeps the stream for
 * 120 s), Sürdür reads on from the last line, Temizle empties the list. After a gap (the API
 * restarted, a long pause) a banner offers the interval in Arama (handed over in memory). The
 * stream and its reconnects are background requests: an unattended screen is signed out at the
 * idle time like any other page. Highlight rules stay in this tab only (`sessionStorage`).
 */
export function LivePage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const navigate = useNavigate();
  const { me } = useAuth();
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;
  const catalogue = useSearchFields();
  const [filter, setFilter] = useState<LiveFilter>(EMPTY_FILTER);
  const [running, setRunning] = useState<string | null>(null);
  const [state, setState] = useState<PageState>('idle');
  const [view, setView] = useState<LiveView>(EMPTY_VIEW);
  const [rate, setRate] = useState(0);
  const [drops, setDrops] = useState<DropTotals>(NO_DROPS);
  const [lastDropped, setLastDropped] = useState<LiveDropped | null>(null);
  const [end, setEnd] = useState<LiveEnd | null>(null);
  const [gap, setGap] = useState<{ message: string; search: HandoffPayloads['/search'] } | null>(
    null,
  );
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<LiveLine | null>(null);
  const [raw, setRaw] = useState<RawLineTarget | null>(null);
  const [rules, setRules] = useState<HighlightRule[]>(() => loadRules());
  const [rulesOpen, setRulesOpen] = useState(false);
  const [rulesUnsaved, setRulesUnsaved] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const connection = useRef<LiveConnection | null>(null);
  const pending = useRef<LiveLine[]>([]);
  const counted = useRef(0);
  const rateWindow = useRef<number[]>([]);
  const lastReceived = useRef<string | null>(null);
  const statusText = useRef('');

  const start = useCallback(() => {
    const q = liveQuery(filter);
    const previous = connection.current;
    pending.current = [];
    setDrops(NO_DROPS);
    setLastDropped(null);
    setEnd(null);
    setGap(null);
    setFailure(null);
    const english = currentLang() === 'en';
    const stream = new LiveConnection({
      q,
      on: {
        state: setState,
        hello: () => {
          // Back after a restart of the API: its "reconnecting" notice is done.
          setEnd((current) => (current?.code === RECONNECTING_END_CODE ? null : current));
        },
        line: (line) => {
          pending.current.push(line);
          counted.current += 1;
          if (line.received) lastReceived.current = line.received;
        },
        dropped: (event) => {
          setDrops((totals) => addDropped(totals, event));
          setLastDropped(event);
        },
        gap: (event) => {
          setGap({
            message: english ? event.message_en : event.message_tr,
            search: searchGap(q, lastReceived.current, new Date()),
          });
        },
        reopened: () => {
          setGap({
            message: t('live.banner.reopened'),
            search: searchGap(q, lastReceived.current, new Date()),
          });
        },
        end: setEnd,
        failed: setFailure,
      },
    });
    connection.current = stream;
    setRunning(q);
    // The old stream gives its slot back first (its DELETE is answered before the new POST).
    void stream.open(previous);
  }, [filter, t]);

  // Lines are added to the list a few times a second (one render for many lines).
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (pending.current.length === 0) return;
      const incoming = pending.current;
      pending.current = [];
      setView((current) => {
        const next = appendLines(current.lines, incoming);
        return {
          lines: next.lines,
          removed: next.removed,
          added: next.lines.length - (current.lines.length - next.removed),
        };
      });
    }, FLUSH_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  // Lines per second, averaged over the last few seconds.
  useEffect(() => {
    const timer = window.setInterval(() => {
      rateWindow.current = [...rateWindow.current, counted.current].slice(-RATE_SECONDS);
      counted.current = 0;
      const next =
        rateWindow.current.reduce((sum, value) => sum + value, 0) / rateWindow.current.length;
      setRate((current) => (current === next ? current : next));
    }, 1_000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  // The polite status region speaks at most every 10 s (the list itself is silent).
  useEffect(() => {
    const timer = window.setInterval(() => {
      setAnnouncement(statusText.current);
    }, ANNOUNCE_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  // Leaving the page closes the stream (not the user's doing: no activity).
  useEffect(
    () => () => {
      void connection.current?.close({ background: true });
    },
    [],
  );

  const openSearch = useCallback(
    (payload: HandoffPayloads['/search']) => {
      handoff.put('/search', payload);
      void navigate('/search');
    },
    [navigate],
  );

  const kindLabel = useCallback(
    (line: LiveLine) => {
      const key = line.kind ?? line.type;
      const kind = catalogue.data?.kinds.find((item) => item.key === key);
      if (kind) return lang === 'en' ? kind.label_en : kind.label_tr;
      return key ?? '—';
    },
    [catalogue.data, lang],
  );

  const active = state !== 'idle' && state !== 'stopped';
  const query = liveQuery(filter);
  const changed = active && running !== null && query !== running;
  const dropped = droppedTotal(drops);
  const status =
    state === 'idle'
      ? t('live.state.idle')
      : [
          t(`live.state.${state}`),
          t('live.status.rate', { rate: formatNumber(rate, 1, lang) }),
          t('live.status.lines', {
            count: view.lines.length,
            formatted: formatNumber(view.lines.length, 0, lang),
          }),
          ...(dropped > 0
            ? [
                t('live.status.dropped', {
                  count: dropped,
                  formatted: formatNumber(dropped, 0, lang),
                }),
              ]
            : []),
        ].join(' · ');
  statusText.current = status;
  const endText = end ? (lang === 'en' ? end.message_en : end.message_tr) : null;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title={t('nav.live')} description={t('live.description')} />
      <p className="-mt-4 flex items-center gap-2 text-13 text-muted">
        <Lock aria-hidden="true" className="size-4 shrink-0" />
        {t('live.privacy')}
      </p>

      <Card>
        <CardContent className="flex flex-col gap-4">
          <LiveFilters
            filter={filter}
            onChange={setFilter}
            onSubmit={() => {
              if (!privacyPending) start();
            }}
            catalogue={catalogue.data}
          />
          <div className="flex flex-wrap items-center gap-2">
            {!active ? (
              <Button onClick={start} disabled={privacyPending}>
                <Play aria-hidden="true" />
                {t('live.actions.start')}
              </Button>
            ) : (
              <>
                {changed ? (
                  <Button onClick={start}>
                    <Play aria-hidden="true" />
                    {t('live.actions.apply')}
                  </Button>
                ) : null}
                {state === 'paused' ? (
                  <Button
                    variant={changed ? 'secondary' : 'primary'}
                    onClick={() => connection.current?.resume()}
                  >
                    <Play aria-hidden="true" />
                    {t('live.actions.resume')}
                  </Button>
                ) : (
                  <Button variant="secondary" onClick={() => connection.current?.pause()}>
                    <Pause aria-hidden="true" />
                    {t('live.actions.pause')}
                  </Button>
                )}
                <Button variant="secondary" onClick={() => void connection.current?.close()}>
                  <Square aria-hidden="true" />
                  {t('live.actions.stop')}
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              disabled={view.lines.length === 0}
              onClick={() => {
                pending.current = [];
                setView(EMPTY_VIEW);
              }}
            >
              <Eraser aria-hidden="true" />
              {t('live.actions.clear')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setRulesOpen(true);
              }}
            >
              <Highlighter aria-hidden="true" />
              {rules.length > 0
                ? t('live.actions.highlightCount', { count: rules.length })
                : t('live.actions.highlight')}
            </Button>
          </div>
        </CardContent>
      </Card>

      {privacyPending ? (
        <EmptyState
          icon={ShieldUser}
          title={t('live.privacyWait.title')}
          description={t('live.privacyWait.body')}
        />
      ) : null}

      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-1 text-13"
        data-testid="live-status"
      >
        {state !== 'idle' ? (
          <Badge tone={STATE_STYLE[state].tone} icon={STATE_STYLE[state].icon} data-state={state}>
            {t(`live.state.${state}`)}
          </Badge>
        ) : null}
        <span className="text-muted tabular" aria-hidden="true">
          {status}
        </span>
        <span className="sr-only" role="status" aria-live="polite">
          {announcement}
        </span>
      </div>

      {failure ? (
        <Alert tone="danger" title={t('live.banner.failed')} data-testid="live-failure">
          <p>{failure.localized}</p>
          {failure.retryAfter !== null ? (
            <p>{t('live.banner.retryAfter', { seconds: failure.retryAfter })}</p>
          ) : null}
        </Alert>
      ) : null}
      {end && endText ? (
        <Alert
          tone={
            end.code === RECONNECTING_END_CODE
              ? 'info'
              : end.code === 'permission_changed'
                ? 'danger'
                : 'warning'
          }
          assertive={false}
          data-testid="live-end"
          data-code={end.code}
        >
          <span className="flex flex-wrap items-center gap-2">
            {endText}
            {RESTARTABLE_ENDS.includes(end.code) && !active ? (
              <Button size="sm" variant="secondary" onClick={start}>
                <Play aria-hidden="true" />
                {t('live.actions.resume')}
              </Button>
            ) : null}
          </span>
        </Alert>
      ) : null}
      {gap ? (
        <Alert tone="warning" assertive={false} data-testid="live-gap">
          <span className="flex flex-wrap items-center gap-2">
            {gap.message}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                openSearch(gap.search);
              }}
            >
              <Search aria-hidden="true" />
              {t('live.banner.openInSearch')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {lastDropped && dropped > 0 ? (
        <Alert tone="info" assertive={false} data-testid="live-dropped">
          <p>{lang === 'en' ? lastDropped.message_en : lastDropped.message_tr}</p>
          <p className="text-xs">
            {DROP_REASONS.filter((reason) => drops[reason] > 0)
              .map((reason) =>
                t(`live.dropped.${reason}`, {
                  count: drops[reason],
                  formatted: formatNumber(drops[reason], 0, lang),
                }),
              )
              .join(' · ')}
          </p>
        </Alert>
      ) : null}
      {rulesUnsaved ? (
        <Alert tone="warning" assertive={false}>
          {t('live.highlight.unsaved')}
        </Alert>
      ) : null}

      <LiveList
        view={view}
        rules={rules}
        kindLabel={kindLabel}
        onOpen={setSelected}
        emptyText={active ? t('live.list.waiting') : t('live.list.empty')}
      />

      <LineSheet
        line={selected}
        kindLabel={kindLabel}
        onClose={() => {
          setSelected(null);
        }}
        onRaw={setRaw}
        onSearch={openSearch}
      />
      <RawLineSheet
        target={raw}
        onClose={() => {
          setRaw(null);
        }}
      />
      <HighlightDialog
        open={rulesOpen}
        rules={rules}
        onOpenChange={setRulesOpen}
        onSave={(next) => {
          setRules(next);
          setRulesUnsaved(!saveRules(next));
          setRulesOpen(false);
        }}
      />
    </div>
  );
}
