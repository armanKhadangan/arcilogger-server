import { FilterX, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { SeverityBadge, SEVERITY_STYLE } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { errorMessage } from '@/lib/api';
import { ALERT_PAGE_SIZE, useAlerts, useAlertSummary } from '@/lib/api-hooks';
import type { AlertOut } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { visibleText } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { Pager } from '@/pages/devices/device-parts';
import { useDebounced } from '@/pages/devices/device-text';

import { AlertStateBadge } from './alert-parts';
import { AlertSheet } from './alert-sheet';
import {
  ALERT_SEVERITIES,
  ALERT_STATES,
  alertIdFrom,
  alertQuery,
  alertSummaryText,
  alertViewFromSearch,
  alertViewToSearch,
  alertViewWithSorting,
  DEFAULT_ALERT_VIEW,
  isAlertState,
  isAlertViewFiltered,
  normalizeRule,
  ruleName,
  sortingOfAlerts,
  type AlertView,
} from './alerts-model';
import { SecuritySummary } from './security-summary';

const ALL = 'all';

function toDate(day: string): Date {
  const [year = 1970, month = 1, date = 1] = day.split('-').map(Number);
  return new Date(year, month - 1, date, 12);
}

function toDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/**
 * Güvenlik (spec 11, 12.3 item 8; `alerts.read`, changes `alerts.manage`): "Güvenlik Özeti", the
 * alerts (filtered, sorted and paged by the server; refreshed every 30 s in the background) and the
 * detail drawer with the state workflow, notes, runbook and e-mails. The rule editor comes in a
 * later milestone.
 */
export function SecurityPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const tableId = useId();
  const { can } = useAuth();
  const manage = can('alerts.manage');
  const [params, setParams] = useSearchParams();
  const view = useMemo(() => alertViewFromSearch(params), [params]);
  const openId = alertIdFrom(params);
  const [search, setSearch] = useState('');
  const q = useDebounced(search.trim());
  const [ruleText, setRuleText] = useState(view.rule);
  const filterKey = JSON.stringify([view, q]);
  const [paging, setPaging] = useState({ key: filterKey, page: 0 });
  const page = paging.key === filterKey ? paging.page : 0;
  const query = alertQuery(view, q, page, ALERT_PAGE_SIZE);
  const alerts = useAlerts(query);
  const summary = useAlertSummary();
  const rows = useMemo(() => alerts.data?.items ?? [], [alerts.data]);
  const total = alerts.data?.total ?? 0;

  useEffect(() => {
    setRuleText(view.rule);
  }, [view.rule]);

  const setView = useCallback(
    (next: AlertView) => {
      setParams((current) => alertViewToSearch(next, current), { replace: true });
    },
    [setParams],
  );

  const openAlert = useCallback(
    (id: number | null) => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        if (id === null) next.delete('alert');
        else next.set('alert', String(id));
        return next;
      });
    },
    [setParams],
  );

  const applyRule = useCallback(() => {
    const rule = normalizeRule(ruleText);
    setRuleText(rule);
    if (rule !== view.rule) setView({ ...view, rule });
  }, [ruleText, view, setView]);

  const clear = useCallback(() => {
    setSearch('');
    setRuleText('');
    setView({ ...DEFAULT_ALERT_VIEW, sort: view.sort, order: view.order });
  }, [setView, view.sort, view.order]);

  const columns = useMemo<AnyColumnDef<AlertOut>[]>(
    () => [
      {
        id: 'severity',
        accessorKey: 'severity',
        header: t('security.columns.severity'),
        size: 120,
        sortDescFirst: true,
        meta: { label: t('security.columns.severity') },
        cell: (info) => <SeverityBadge severity={info.row.original.severity} />,
      },
      {
        id: 'time',
        accessorKey: 'time',
        header: t('security.columns.time'),
        size: 170,
        sortDescFirst: true,
        meta: { label: t('security.columns.time') },
        cell: (info) => <span className="tabular">{formatDateTime(info.row.original.time)}</span>,
      },
      {
        id: 'rule',
        accessorKey: 'rule',
        header: t('security.columns.rule'),
        size: 200,
        meta: { label: t('security.columns.rule') },
        cell: (info) => {
          const row = info.row.original;
          const name = ruleName(row, lang);
          return (
            <span className="flex min-w-0 flex-col leading-tight" title={`${row.rule} · ${name}`}>
              <span className="font-mono text-xs">{row.rule}</span>
              <span className="truncate text-xs text-muted-strong">{name}</span>
            </span>
          );
        },
      },
      {
        id: 'summary',
        accessorFn: (row) => alertSummaryText(row, lang),
        header: t('security.columns.summary'),
        size: 260,
        minSize: 220,
        enableSorting: false,
        meta: { label: t('security.columns.summary'), grow: true },
        cell: (info) => {
          const row = info.row.original;
          const text = visibleText(alertSummaryText(row, lang));
          return (
            <button
              type="button"
              className="block max-w-full truncate rounded-sm text-left text-accent-ink hover:underline"
              title={text}
              onClick={(event) => {
                event.stopPropagation();
                openAlert(row.id);
              }}
            >
              {text}
            </button>
          );
        },
      },
      {
        id: 'state',
        accessorKey: 'state',
        header: t('security.columns.state'),
        size: 140,
        meta: { label: t('security.columns.state') },
        cell: (info) => <AlertStateBadge state={info.row.original.state} />,
      },
      {
        id: 'notes',
        accessorKey: 'notes_count',
        header: t('security.columns.notes'),
        size: 70,
        enableSorting: false,
        meta: { label: t('security.columns.notes'), numeric: true },
      },
    ],
    [t, lang, openAlert],
  );

  // A change emptied the page (e.g. a closed alert left a filtered list): show the last page.
  const lastPage = Math.max(0, Math.ceil(total / ALERT_PAGE_SIZE) - 1);
  const pastTheEnd =
    alerts.isSuccess && !alerts.isPlaceholderData && rows.length === 0 && page > lastPage;
  useEffect(() => {
    if (pastTheEnd) setPaging({ key: filterKey, page: lastPage });
  }, [pastTheEnd, filterKey, lastPage]);

  const filtered = isAlertViewFiltered(view, search);
  const range =
    view.from || view.to
      ? { from: toDate(view.from ?? view.to ?? ''), to: toDate(view.to ?? view.from ?? '') }
      : null;
  const offset = alerts.data?.offset ?? page * ALERT_PAGE_SIZE;
  const first = total === 0 ? 0 : offset + 1;
  const last = offset + rows.length;

  const toolbar = (
    <div className="flex w-full flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex w-60 max-w-full flex-col gap-1">
          <Label htmlFor={`${tableId}-search`} className="text-xs text-muted-strong">
            {t('security.filters.search')}
          </Label>
          <Input
            id={`${tableId}-search`}
            type="search"
            autoComplete="off"
            spellCheck={false}
            leadingIcon={<Search />}
            placeholder={t('security.filters.searchPlaceholder')}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${tableId}-state`} className="text-xs text-muted-strong">
            {t('security.columns.state')}
          </Label>
          <Select
            value={view.state ?? ALL}
            onValueChange={(value) => {
              setView({ ...view, state: isAlertState(value) ? value : null });
            }}
          >
            <SelectTrigger id={`${tableId}-state`} className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t('security.filters.allStates')}</SelectItem>
              {ALERT_STATES.map((state) => (
                <SelectItem key={state} value={state}>
                  {t(`alertStates.${state}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex w-36 flex-col gap-1">
          <Label htmlFor={`${tableId}-rule`} className="text-xs text-muted-strong">
            {t('security.columns.rule')}
          </Label>
          <Input
            id={`${tableId}-rule`}
            autoComplete="off"
            spellCheck={false}
            mono
            placeholder={t('security.filters.rulePlaceholder')}
            value={ruleText}
            onChange={(event) => {
              setRuleText(event.target.value);
            }}
            onBlur={applyRule}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                applyRule();
              }
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <span id={`${tableId}-range`} className="text-13 text-xs font-medium text-muted-strong">
            {t('security.filters.range')}
          </span>
          <DateRangePicker
            value={range}
            onChange={(next) => {
              setView({ ...view, from: toDay(next.from), to: toDay(next.to) });
            }}
          />
        </div>
        {filtered ? (
          <Button variant="ghost" onClick={clear}>
            <FilterX aria-hidden="true" />
            {t('security.filters.clear')}
          </Button>
        ) : null}
      </div>
      <div
        className="flex flex-wrap items-center gap-2"
        role="group"
        aria-label={t('security.filters.severity')}
      >
        <span className="text-xs font-medium text-muted-strong">
          {t('security.filters.severity')}
        </span>
        {ALERT_SEVERITIES.map((severity) => {
          const Icon = SEVERITY_STYLE[severity].icon;
          const pressed = view.severity === severity;
          return (
            <button
              key={severity}
              type="button"
              aria-pressed={pressed}
              onClick={() => {
                setView({ ...view, severity: pressed ? null : severity });
              }}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-13 font-medium',
                pressed
                  ? 'border-primary bg-primary-soft text-primary-ink'
                  : 'border-border bg-surface text-foreground hover:bg-subtle',
              )}
            >
              <Icon aria-hidden="true" className="size-3.5" />
              {t(`severity.${severity}`)}
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.security')}
        description={t('security.description')}
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void alerts.refresh();
              void summary.refresh();
            }}
          >
            <RefreshCw aria-hidden="true" />
            {t('dashboard.refresh')}
          </Button>
        }
      />
      <SecuritySummary
        summary={summary.data}
        pending={summary.isPending}
        error={summary.isError ? summary.error : null}
      />
      {!manage ? (
        <Alert tone="info" assertive={false} data-testid="read-only">
          {t('security.readOnly')}
        </Alert>
      ) : null}
      {alerts.isError ? (
        <Alert tone="danger">
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(alerts.error)}
            <Button size="sm" variant="secondary" onClick={() => void alerts.refresh()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {alerts.isSuccess && total === 0 && !filtered ? (
        <>
          {toolbar}
          <EmptyState
            icon={ShieldCheck}
            title={t('security.empty')}
            description={t('security.emptyHint')}
          />
        </>
      ) : (
        <DataTable
          data={rows}
          columns={columns}
          caption={t('security.caption')}
          getRowId={(row) => String(row.id)}
          onRowClick={(row) => {
            openAlert(row.id);
          }}
          toolbar={toolbar}
          height={Math.min(560, 40 + Math.max(rows.length, 4) * 44)}
          initialDensity="comfortable"
          sorting={sortingOfAlerts(view)}
          onSortingChange={(sorting) => {
            setView(alertViewWithSorting(view, sorting));
          }}
          countLabel={
            alerts.data
              ? total === 0
                ? t('security.rangeEmpty')
                : t('security.range', {
                    count: total,
                    first: formatNumber(first, 0, lang),
                    last: formatNumber(last, 0, lang),
                    formattedCount: formatNumber(total, 0, lang),
                  })
              : undefined
          }
          emptyMessage={
            alerts.isPending ? (
              t('common.loading')
            ) : (
              <span className="flex flex-col items-center gap-2">
                {t('security.emptyFiltered')}
                {filtered ? (
                  <Button size="sm" variant="secondary" onClick={clear}>
                    <FilterX aria-hidden="true" />
                    {t('security.filters.clear')}
                  </Button>
                ) : null}
              </span>
            )
          }
        />
      )}
      <Pager
        offset={offset}
        total={total}
        pageSize={ALERT_PAGE_SIZE}
        label={t('security.pager')}
        onPage={(next) => {
          setPaging({ key: filterKey, page: next });
        }}
      />
      <p className="text-xs text-muted" data-testid="rule-editor-note">
        {t('security.ruleEditorLater')}
      </p>
      <AlertSheet
        alertId={openId}
        manage={manage}
        onClose={() => {
          openAlert(null);
        }}
        onOpenAlert={openAlert}
      />
    </div>
  );
}
