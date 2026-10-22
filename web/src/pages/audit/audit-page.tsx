import { Filter, X } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import { useAuditEntries } from '@/lib/api-hooks';
import type { AuditEntry, AuditFilters } from '@/lib/api-types';
import { formatDateTime } from '@/lib/format';

import { AuditDetailSheet } from './audit-detail-sheet';
import { EMPTY_DRAFT, toFilters, type AuditDraft } from './audit-filters';
import { ACTION_PREFIXES, useActionLabel } from './audit-labels';
import { ChainStatus } from './chain-status';

/**
 * Denetim Kaydı (spec 8.5, 12.3 page 14; `audit.read`): the hash-chained audit log, newest
 * first, read-only. Filters go to the server (time range, user, action prefix, target); pages of
 * 100 are loaded on demand ("Daha fazla yükle") into a virtualised table; a row opens the detail
 * drawer with the canonical JSON and the hashes. The chain badge verifies the whole log.
 */
export function AuditPage() {
  const { t } = useTranslation();
  const id = useId();
  const label = useActionLabel();
  const [draft, setDraft] = useState<AuditDraft>(EMPTY_DRAFT);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const entries = useAuditEntries(filters);
  const rows = useMemo(
    () => entries.data?.pages.flatMap((page) => page.items) ?? [],
    [entries.data],
  );

  function apply(event: FormEvent) {
    event.preventDefault();
    setFilters(toFilters(draft));
  }

  function clear() {
    setDraft(EMPTY_DRAFT);
    setFilters({});
  }

  const columns = useMemo<AnyColumnDef<AuditEntry>[]>(
    () => [
      {
        id: 'time',
        accessorKey: 'time',
        header: t('audit.columns.time'),
        size: 170,
        enableSorting: false,
        meta: { label: t('audit.columns.time') },
        cell: (info) => <span className="tabular">{formatDateTime(info.getValue<string>())}</span>,
      },
      {
        id: 'actor',
        accessorFn: (row) => row.actor ?? '',
        header: t('audit.columns.actor'),
        size: 150,
        enableSorting: false,
        meta: { label: t('audit.columns.actor') },
        cell: (info) => info.getValue<string>() || '—',
      },
      {
        id: 'action',
        accessorKey: 'action',
        header: t('audit.columns.action'),
        size: 300,
        enableSorting: false,
        meta: { label: t('audit.columns.action') },
        cell: (info) => {
          const action = info.getValue<string>();
          const text = label(action);
          return (
            <span className="flex min-w-0 items-baseline gap-2">
              {text ? <span className="truncate">{text}</span> : null}
              {/* muted-strong: the row turns bg-subtle on hover/focus, where text-muted is
                  below 4.5:1 (WCAG 1.4.3) */}
              <code className="truncate font-mono text-xs text-muted-strong">{action}</code>
            </span>
          );
        },
      },
      {
        id: 'target',
        accessorFn: (row) => row.target ?? '',
        header: t('audit.columns.target'),
        size: 160,
        enableSorting: false,
        meta: { label: t('audit.columns.target'), mono: true },
      },
      {
        id: 'ip',
        accessorFn: (row) => row.ip ?? '',
        header: t('audit.columns.ip'),
        size: 150,
        enableSorting: false,
        meta: { label: t('audit.columns.ip'), mono: true },
      },
      {
        id: 'hash',
        // Not sent to a reader who sees usage pseudonyms (`AuditEntry.hash`).
        accessorFn: (row) => row.hash ?? '',
        header: t('audit.columns.hash'),
        size: 130,
        enableSorting: false,
        meta: { label: t('audit.columns.hash'), mono: true },
        cell: (info) => {
          const hash = info.getValue<string>();
          return hash ? <span title={hash}>{hash.slice(0, 12)}…</span> : '—';
        },
      },
    ],
    [t, label],
  );

  const active = Object.keys(filters).length > 0;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title={t('nav.audit')} description={t('audit.description')} />
      <ChainStatus />
      <Card className="p-4">
        <form
          onSubmit={apply}
          className="flex flex-wrap items-end gap-3"
          aria-label={t('audit.filters')}
        >
          <Field id={`${id}-range`} label={t('dateRange.label')}>
            <DateRangePicker
              id={`${id}-range`}
              value={draft.range}
              onChange={(range) => {
                setDraft((current) => ({ ...current, range }));
              }}
            />
          </Field>
          <Field id={`${id}-actor`} label={t('audit.columns.actor')} className="w-44">
            <Input
              id={`${id}-actor`}
              value={draft.actor}
              placeholder={t('audit.actorPlaceholder')}
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => {
                setDraft((current) => ({ ...current, actor: event.target.value }));
              }}
            />
          </Field>
          <Field id={`${id}-action`} label={t('audit.actionFilter')} className="w-48">
            <Input
              id={`${id}-action`}
              mono
              list={`${id}-actions`}
              value={draft.action}
              placeholder="auth."
              spellCheck={false}
              onChange={(event) => {
                setDraft((current) => ({ ...current, action: event.target.value }));
              }}
            />
            <datalist id={`${id}-actions`}>
              {ACTION_PREFIXES.map((prefix) => (
                <option key={prefix} value={prefix} />
              ))}
            </datalist>
          </Field>
          <Field id={`${id}-target`} label={t('audit.columns.target')} className="w-44">
            <Input
              id={`${id}-target`}
              mono
              value={draft.target}
              placeholder="user:2"
              spellCheck={false}
              onChange={(event) => {
                setDraft((current) => ({ ...current, target: event.target.value }));
              }}
            />
          </Field>
          <div className="flex gap-2">
            <Button type="submit">
              <Filter aria-hidden="true" />
              {t('audit.apply')}
            </Button>
            {active || draft !== EMPTY_DRAFT ? (
              <Button variant="ghost" onClick={clear}>
                <X aria-hidden="true" />
                {t('audit.clear')}
              </Button>
            ) : null}
          </div>
        </form>
      </Card>
      {entries.isError ? <Alert tone="danger">{errorMessage(entries.error)}</Alert> : null}
      <DataTable
        data={rows}
        columns={columns}
        caption={t('audit.caption')}
        getRowId={(row) => String(row.id)}
        onRowClick={setSelected}
        height={560}
        emptyMessage={entries.isPending ? t('common.loading') : t('audit.empty')}
      />
      <div className="flex flex-col items-center gap-1">
        {entries.hasNextPage ? (
          <Button
            variant="secondary"
            busy={entries.isFetchingNextPage}
            onClick={() => void entries.fetchNextPage()}
          >
            {entries.isFetchingNextPage ? t('common.loading') : t('common.loadMore')}
          </Button>
        ) : rows.length > 0 ? (
          <p className="text-xs text-muted">{t('audit.end')}</p>
        ) : null}
      </div>
      <AuditDetailSheet
        entry={selected}
        onClose={() => {
          setSelected(null);
        }}
      />
    </div>
  );
}
