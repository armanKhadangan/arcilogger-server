import { FileText } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { SEARCH_VIEW_ROWS } from '@/lib/search-hooks';
import type { CatalogueKind, SearchRow } from '@/lib/search-types';

import { rowSummary, type ScanState } from './search-model';

/**
 * The search's records, newest first (virtualised: `DataTable` with `@tanstack/react-virtual`):
 * Zaman, Kaynak, Tür, Özet (by kind, from the row's summary), Ham satır. A row opens the record's
 * drawer; "Ham satır" opens the raw line itself. Up to {@link SEARCH_VIEW_ROWS} rows are kept in
 * view ("Daha fazla" reads the next page); the order is the server's (no sorting here).
 */
export function ResultsTable({
  rows,
  kinds,
  state,
  loadingMore,
  onOpen,
  onRaw,
  onMore,
  emptyMessage,
}: {
  rows: SearchRow[];
  kinds: readonly CatalogueKind[];
  state: ScanState | null;
  loadingMore: boolean;
  onOpen: (row: SearchRow) => void;
  onRaw: (row: SearchRow) => void;
  onMore: () => void;
  emptyMessage: string;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const kindLabels = useMemo(
    () => new Map(kinds.map((kind) => [kind.key, lang === 'en' ? kind.label_en : kind.label_tr])),
    [kinds, lang],
  );

  const columns = useMemo<AnyColumnDef<SearchRow>[]>(
    () => [
      {
        id: 'time',
        accessorKey: 'time',
        header: t('search.results.time'),
        size: 170,
        enableSorting: false,
        meta: { label: t('search.results.time') },
        cell: (info) => (
          <span
            className="tabular"
            title={t('search.results.received', {
              time: formatDateTime(info.row.original.time_received),
            })}
          >
            {formatDateTime(info.getValue<string>())}
          </span>
        ),
      },
      {
        id: 'source',
        accessorKey: 'source',
        header: t('search.results.source'),
        size: 150,
        enableSorting: false,
        meta: { label: t('search.results.source'), mono: true },
      },
      {
        id: 'kind',
        accessorFn: (row) => kindLabels.get(row.kind) ?? row.kind,
        header: t('search.results.kind'),
        size: 150,
        enableSorting: false,
        meta: { label: t('search.results.kind') },
      },
      {
        id: 'summary',
        accessorFn: (row) => rowSummary(row),
        header: t('search.results.summary'),
        size: 200,
        minSize: 200,
        enableSorting: false,
        meta: { label: t('search.results.summary'), grow: true, mono: true },
        cell: (info) => <span title={info.getValue<string>()}>{info.getValue<string>()}</span>,
      },
      {
        id: 'raw',
        header: t('search.results.raw'),
        size: 130,
        enableSorting: false,
        enableHiding: false,
        meta: { label: t('search.results.raw') },
        cell: (info) => (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            aria-label={t('search.results.openRaw', {
              source: info.row.original.raw.source,
              line: info.row.original.raw.line,
            })}
            onClick={(event) => {
              event.stopPropagation();
              onRaw(info.row.original);
            }}
            onKeyDown={(event) => {
              // Enter and Space belong to the button, not to the row around it.
              event.stopPropagation();
            }}
          >
            <FileText aria-hidden="true" />
            {t('search.results.raw')}
          </Button>
        ),
      },
    ],
    [t, kindLabels, onRaw],
  );

  return (
    <div className="flex flex-col gap-2">
      <DataTable
        data={rows}
        columns={columns}
        caption={t('search.results.caption')}
        getRowId={(row) => row.key}
        onRowClick={onOpen}
        height={520}
        emptyMessage={emptyMessage}
        countLabel={t('search.results.count', {
          count: rows.length,
          formatted: formatNumber(rows.length, 0, lang),
        })}
      />
      {state === 'more' ? (
        <Button variant="secondary" className="self-center" busy={loadingMore} onClick={onMore}>
          {loadingMore ? t('common.loading') : t('search.results.more')}
        </Button>
      ) : null}
      {state === 'capped' ? (
        <p className="text-center text-13 text-muted">
          {t('search.results.capped', {
            formatted: formatNumber(SEARCH_VIEW_ROWS, 0, lang),
          })}
        </p>
      ) : null}
    </div>
  );
}
