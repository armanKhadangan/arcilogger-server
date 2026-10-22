import { FileCheck } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { Label } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import { useLookups } from '@/lib/api-hooks';
import type { LookupSummary } from '@/lib/api-types';
import { formatDay, shortHash } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ModeBadge } from './lookup-results';

/**
 * Earlier lookups (`GET /lookups`): administrators see everyone's, the auditor only their own.
 * The list has no results (person-level data); a row opens the lookup, which the server audits.
 */
export function LookupHistory({ onOpen }: { onOpen: (id: number) => void }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const [mine, setMine] = useState(false);
  const lookups = useLookups(mine ? { mine: true } : {});
  const rows = useMemo(
    () => lookups.data?.pages.flatMap((page) => page.items) ?? [],
    [lookups.data],
  );
  const allUsers = lookups.data?.pages[0]?.all_users ?? false;

  const columns = useMemo<AnyColumnDef<LookupSummary>[]>(() => {
    const list: AnyColumnDef<LookupSummary>[] = [
      {
        id: 'id',
        accessorKey: 'id',
        header: t('lookup.history.columns.id'),
        size: 70,
        meta: { label: t('lookup.history.columns.id'), numeric: true },
        cell: (info) => `#${info.getValue<number>()}`,
      },
      {
        id: 'created_at',
        accessorKey: 'created_at',
        header: t('lookup.history.columns.time'),
        size: 160,
        meta: { label: t('lookup.history.columns.time') },
        cell: (info) => <span className="tabular">{formatDateTime(info.getValue<string>())}</span>,
      },
      {
        id: 'username',
        accessorKey: 'username',
        header: t('lookup.history.columns.user'),
        size: 120,
        meta: { label: t('lookup.history.columns.user') },
      },
      {
        id: 'authority',
        accessorKey: 'authority',
        header: t('lookup.form.authority'),
        size: 240,
        meta: { label: t('lookup.form.authority') },
      },
      {
        id: 'file_no',
        accessorKey: 'file_no',
        header: t('lookup.form.fileNo'),
        size: 170,
        meta: { label: t('lookup.form.fileNo') },
      },
      {
        id: 'request_date',
        accessorKey: 'request_date',
        header: t('lookup.form.requestDate'),
        size: 110,
        meta: { label: t('lookup.form.requestDate') },
        cell: (info) => <span className="tabular">{formatDay(info.getValue<string>())}</span>,
      },
      {
        id: 'mode',
        accessorFn: (row) => row.mode ?? '',
        header: t('lookup.history.columns.result'),
        size: 190,
        meta: { label: t('lookup.history.columns.result') },
        cell: (info) => (
          <span className="flex items-center gap-2">
            <ModeBadge mode={info.row.original.mode} />
            <span className="text-xs text-muted-strong tabular">
              {formatNumber(info.row.original.result_count, 0, lang)}
            </span>
          </span>
        ),
      },
      {
        id: 'report',
        accessorFn: (row) => row.report_sha256 ?? '',
        header: t('lookup.history.columns.report'),
        size: 150,
        meta: { label: t('lookup.history.columns.report'), mono: true },
        cell: (info) =>
          info.row.original.report_sha256 ? (
            <span className="flex items-center gap-1" title={info.row.original.report_sha256}>
              <FileCheck aria-hidden="true" className="size-3.5 text-success" />
              {shortHash(info.row.original.report_sha256)}
            </span>
          ) : (
            '—'
          ),
      },
    ];
    return allUsers ? list : list.filter((column) => column.id !== 'username');
  }, [t, lang, allUsers]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-13 text-muted">
          {allUsers ? t('lookup.history.allUsers') : t('lookup.history.ownOnly')}
        </p>
        {allUsers || mine ? (
          <div className="flex items-center gap-2">
            <Checkbox
              id={`${id}-mine`}
              checked={mine}
              onCheckedChange={(value) => {
                setMine(value === true);
              }}
            />
            <Label htmlFor={`${id}-mine`} className="font-normal">
              {t('lookup.history.mine')}
            </Label>
          </div>
        ) : null}
      </div>
      {lookups.isError ? <Alert tone="danger">{errorMessage(lookups.error)}</Alert> : null}
      <DataTable
        data={rows}
        columns={columns}
        caption={t('lookup.history.caption')}
        getRowId={(row) => String(row.id)}
        onRowClick={(row) => {
          onOpen(row.id);
        }}
        height={420}
        emptyMessage={lookups.isPending ? t('common.loading') : t('lookup.history.empty')}
      />
      {lookups.hasNextPage ? (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            busy={lookups.isFetchingNextPage}
            onClick={() => void lookups.fetchNextPage()}
          >
            {lookups.isFetchingNextPage ? t('common.loading') : t('common.loadMore')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
