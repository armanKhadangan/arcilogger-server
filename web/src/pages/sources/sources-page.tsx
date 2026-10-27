import { CornerDownRight, FilePlus2, RadioTower, RefreshCw, Settings2 } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { Sparkline } from '@/components/charts/sparkline';
import { RawLineSheet, type RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatBytes, formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { handoff } from '@/lib/handoff';
import { currentLang } from '@/lib/i18n';
import { useSourcesOverview } from '@/lib/sources-hooks';
import type { SourceHealth } from '@/lib/sources-types';
import { cn } from '@/lib/utils';

import { LicenceStrip } from './licence-strip';
import { SourceSheet } from './source-sheet';
import {
  failurePercent,
  filterCounts,
  MANAGE_PERMISSION,
  promotion,
  shownState,
  SOURCE_FILTERS,
  sourceRows,
  sourcesSummary,
  STATE_STYLE,
  type SourceFilter,
  type SourceRow,
} from './sources-model';

function filterOf(params: URLSearchParams): SourceFilter {
  const value = params.get('filter');
  return SOURCE_FILTERS.find((filter) => filter === value) ?? 'all';
}

/**
 * Kaynaklar in the main menu (spec 12.3 page 3; `sources.read`): the state of every device that
 * sends logs — configured sources, the members of a configured network (listed under it) and
 * unknown senders (`unknown-<ip>`) — with the last message, the messages of the last hour, today's
 * lines and parse failures. It only reads (counts, not audited; refreshed every 30 s in the
 * background); the sources are edited in Ayarlar → Kaynaklar (`pages/settings/sources-page.tsx`,
 * `SourcesPage`), where "Kaynak olarak tanımla" opens the add dialog filled in. A row opens the
 * source's drawer (`source-sheet.tsx`). The filter (`?filter=`) and the open source
 * (`?source=`: a device's name, not personal data) are in the address.
 */
export function SourcesHealthPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const overview = useSourcesOverview();
  const [raw, setRaw] = useState<RawLineTarget | null>(null);
  const filter = filterOf(params);
  const openName = params.get('source');
  const items = useMemo(() => overview.data?.items ?? [], [overview.data]);
  const rows = useMemo(() => sourceRows(items, filter), [items, filter]);
  const counts = filterCounts(items);
  const summary = sourcesSummary(items);
  const selected = items.find((item) => item.name === openName) ?? null;
  const manage = can(MANAGE_PERMISSION);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value === null) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const promote = useCallback(
    (item: SourceHealth) => {
      const payload = promotion(item);
      if (!payload) return;
      handoff.put('/settings/sources', payload);
      void navigate('/settings/sources');
    },
    [navigate],
  );

  const columns = useMemo<AnyColumnDef<SourceRow>[]>(
    () => [
      {
        id: 'name',
        accessorFn: (row) => row.item.name,
        header: t('sources.columns.name'),
        size: 240,
        minSize: 200,
        meta: { label: t('sources.columns.name'), grow: true },
        cell: (info) => {
          const { item, member } = info.row.original;
          return (
            <span className={cn('flex min-w-0 items-center gap-1.5', member && 'pl-4')}>
              {member ? (
                <CornerDownRight
                  aria-label={t('sources.badges.member', { network: item.network_of ?? '' })}
                  className="size-3.5 shrink-0 text-muted"
                />
              ) : null}
              <span className="truncate font-mono text-xs" title={item.name}>
                {item.name}
              </span>
              {item.state === 'unknown' ? (
                <Badge tone="info" className="h-5">
                  {t('sources.badges.unknown')}
                </Badge>
              ) : null}
              {item.promoted_to ? (
                <Badge tone="neutral" className="h-5 font-mono">
                  {t('sources.badges.promotedTo', { name: item.promoted_to })}
                </Badge>
              ) : null}
            </span>
          );
        },
      },
      {
        id: 'type',
        accessorFn: (row) => row.item.type,
        header: t('sources.columns.type'),
        size: 120,
        meta: { label: t('sources.columns.type') },
        cell: (info) =>
          t(`settings.sources.types.${info.row.original.item.type}`, {
            defaultValue: info.row.original.item.type,
          }),
      },
      {
        id: 'address',
        accessorFn: (row) => row.item.address ?? '',
        header: t('sources.columns.address'),
        size: 140,
        meta: { label: t('sources.columns.address'), mono: true },
      },
      {
        id: 'lastMessage',
        accessorFn: (row) => row.item.last_message ?? '',
        header: t('sources.columns.lastMessage'),
        size: 170,
        meta: {
          label: t('sources.columns.lastMessage'),
          exportValue: (row) => row.item.last_message ?? '',
        },
        cell: (info) => {
          const item = info.row.original.item;
          if (!item.last_message) return <span className="text-muted">{t('sources.never')}</span>;
          return (
            <span
              className="tabular"
              title={
                item.last_message_origin
                  ? t(`sources.origin.${item.last_message_origin}`)
                  : undefined
              }
            >
              {formatDateTime(item.last_message)}
            </span>
          );
        },
      },
      {
        id: 'rate',
        accessorFn: (row) => row.item.messages_per_minute,
        header: t('sources.columns.rate'),
        size: 160,
        meta: { label: t('sources.columns.rate') },
        cell: (info) => {
          const item = info.row.original.item;
          return (
            <span className="flex items-center gap-2">
              {item.sparkline.length > 1 ? (
                <Sparkline
                  values={item.sparkline}
                  label={t('sources.trend', { name: item.name })}
                  formatValue={(value) => formatNumber(value, 0, lang)}
                  className="h-6 w-20 shrink-0"
                />
              ) : null}
              <span className="tabular">{formatNumber(item.messages_per_minute, 1, lang)}</span>
            </span>
          );
        },
      },
      {
        id: 'linesToday',
        accessorFn: (row) => row.item.lines_today,
        header: t('sources.columns.linesToday'),
        size: 120,
        meta: { label: t('sources.columns.linesToday'), numeric: true },
        cell: (info) => {
          const item = info.row.original.item;
          return (
            <span
              title={
                item.pending_bytes > 0
                  ? t('sources.pending', { size: formatBytes(item.pending_bytes, lang) })
                  : undefined
              }
            >
              {formatNumber(item.lines_today, 0, lang)}
            </span>
          );
        },
      },
      {
        id: 'failures',
        accessorFn: (row) => row.item.parse_failure_permille ?? -1,
        header: t('sources.columns.failures'),
        size: 130,
        meta: {
          label: t('sources.columns.failures'),
          numeric: true,
          exportValue: (row) => failurePercent(row.item.parse_failure_permille) ?? '',
        },
        cell: (info) => {
          const percent = failurePercent(info.row.original.item.parse_failure_permille);
          return percent === null ? (
            <span className="text-muted">—</span>
          ) : (
            t('sources.percent', { value: formatNumber(percent, 1, lang) })
          );
        },
      },
      {
        id: 'heartbeat',
        accessorFn: (row) => row.item.heartbeat_minutes,
        header: t('sources.columns.heartbeat'),
        size: 120,
        meta: { label: t('sources.columns.heartbeat'), numeric: true },
        cell: (info) =>
          t('settings.sources.minutes', { count: info.row.original.item.heartbeat_minutes }),
      },
      {
        id: 'state',
        accessorFn: (row) => shownState(row.item),
        header: t('sources.columns.state'),
        size: 190,
        meta: {
          label: t('sources.columns.state'),
          exportValue: (row) => t(`sources.state.${shownState(row.item)}`),
        },
        cell: (info) => {
          const item = info.row.original.item;
          const state = shownState(item);
          const style = STATE_STYLE[state];
          return (
            <span className="flex items-center gap-1.5">
              <Badge tone={style.tone} icon={style.icon}>
                {t(`sources.state.${state}`)}
              </Badge>
              {item.silent_minutes !== null && item.state === 'silent' ? (
                <span className="text-xs text-muted tabular">
                  {t('settings.sources.minutes', { count: item.silent_minutes })}
                </span>
              ) : null}
            </span>
          );
        },
      },
      ...(manage
        ? [
            {
              id: 'actions',
              header: () => <span className="sr-only">{t('sources.columns.actions')}</span>,
              size: 200,
              enableSorting: false,
              enableHiding: false,
              meta: { label: t('sources.columns.actions') },
              cell: (info) => {
                const item = info.row.original.item;
                return promotion(item) ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7"
                    aria-label={t('sources.promoteFor', { name: item.name })}
                    onClick={(event) => {
                      event.stopPropagation();
                      promote(item);
                    }}
                    onKeyDown={(event) => {
                      // Enter and Space press the button, not the row.
                      event.stopPropagation();
                    }}
                  >
                    <FilePlus2 aria-hidden="true" />
                    {t('sources.promote')}
                  </Button>
                ) : null;
              },
            } satisfies AnyColumnDef<SourceRow>,
          ]
        : []),
    ],
    [t, lang, manage, promote],
  );

  const updatedAt = overview.dataUpdatedAt;
  const data = overview.data;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.sources')}
        description={t('sources.description')}
        actions={
          <>
            {updatedAt > 0 ? (
              <span className="text-13 text-muted tabular" aria-live="polite">
                {t('sources.updated', { time: formatTime(updatedAt, true) })}
              </span>
            ) : null}
            <Button variant="secondary" size="sm" onClick={() => void overview.refresh()}>
              <RefreshCw aria-hidden="true" />
              {t('sources.refresh')}
            </Button>
            {manage ? (
              <Button variant="secondary" size="sm" asChild>
                <Link to="/settings/sources">
                  <Settings2 aria-hidden="true" />
                  {t('sources.editInSettings')}
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

      {overview.isError ? (
        <Alert tone="danger" title={t('sources.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(overview.error)}
            <Button size="sm" variant="secondary" onClick={() => void overview.refresh()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}

      {overview.isPending ? (
        <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-96" />
        </div>
      ) : data ? (
        <>
          <div className="-mt-2 flex flex-col gap-1.5">
            <p
              className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
              data-testid="sources-summary"
            >
              <span className="font-medium text-foreground tabular">
                {t('sources.summary', {
                  active: formatNumber(summary.active, 0, lang),
                  total: formatNumber(summary.total, 0, lang),
                })}
              </span>
              {summary.unknown > 0 ? (
                <span className="text-muted">
                  {t('sources.unknownSenders', { count: summary.unknown })}
                </span>
              ) : null}
            </p>
            <LicenceStrip />
          </div>
          {!data.collector.fresh ? (
            <Alert tone="warning" assertive={false} data-testid="collector-stale">
              {t('sources.collectorStale')}
            </Alert>
          ) : null}
          {items.length === 0 ? (
            <EmptyState
              icon={RadioTower}
              title={t('sources.emptyTitle')}
              description={t('sources.emptyBody')}
            />
          ) : (
            <DataTable
              data={rows}
              columns={columns}
              caption={t('sources.caption')}
              getRowId={(row) => row.item.name}
              onRowClick={(row) => {
                setParam('source', row.item.name);
              }}
              height={Math.min(600, Math.max(200, 38 + rows.length * 32))}
              exportFileName="kaynaklar"
              emptyMessage={t('sources.noMatch')}
              toolbar={
                <div
                  role="group"
                  aria-label={t('sources.filters.label')}
                  className="flex flex-wrap items-center gap-1"
                >
                  {SOURCE_FILTERS.map((key) => (
                    <Button
                      key={key}
                      size="sm"
                      variant={filter === key ? 'primary' : 'secondary'}
                      aria-pressed={filter === key}
                      onClick={() => {
                        setParam('filter', key === 'all' ? null : key);
                      }}
                    >
                      {t(`sources.filters.${key}`)}
                      <span className="tabular">{formatNumber(counts[key], 0, lang)}</span>
                    </Button>
                  ))}
                </div>
              }
            />
          )}
        </>
      ) : null}

      <SourceSheet
        item={selected}
        onClose={() => {
          setParam('source', null);
        }}
        onRaw={setRaw}
        onPromote={promote}
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
