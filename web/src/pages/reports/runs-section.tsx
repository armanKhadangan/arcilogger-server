import type { TFunction } from 'i18next';
import { History, RefreshCw } from 'lucide-react';
import { useCallback, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { RUN_PAGE_SIZE, useReportRuns } from '@/lib/api-hooks';
import type { RunFilters, RunOut, ReportRunStatus, ScheduleOut } from '@/lib/api-types';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { Pager } from '@/pages/devices/device-parts';

import { shownStatus } from './report-errors';
import { RunStatusBadge } from './report-parts';
import { RunSheet } from './run-sheet';
import { kindLabel, periodText } from './schedule-text';

const ALL = 'all';
const STATUSES: readonly ReportRunStatus[] = ['queued', 'running', 'sent', 'partial', 'failed'];
const TH =
  'sticky top-0 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong';
const TD = 'border-b border-border px-3 py-2 align-top';

/**
 * "3/4 gönderildi" over the recipients tried so far, or "E-posta yok" for a run that e-mails
 * nobody (`send: false`: its deliveries are `skipped`).
 */
function deliveryText(run: RunOut, t: TFunction): string {
  const tried = run.deliveries.filter((item) => item.status !== 'skipped');
  if (tried.length === 0) return t('reports.runs.notSent');
  const sent = tried.filter((item) => item.status === 'sent').length;
  return t('reports.runs.delivered', { sent, total: tried.length });
}

/**
 * "Çalışma geçmişi": every run, newest first (filtered by schedule and state, paged by the
 * server, refreshed every few seconds while one is queued or running); a row opens the run with
 * each recipient's delivery and the files (also from the address, `?run=<id>`: the e-mail links
 * there when its attachments were too big).
 */
export function RunsSection({
  schedules,
  scheduleFilter,
  onScheduleFilter,
  highlight,
  openRun,
  onOpenRun,
}: {
  schedules: readonly ScheduleOut[];
  scheduleFilter: number | null;
  onScheduleFilter: (id: number | null) => void;
  /** The run just started from this page (highlighted). */
  highlight: number | null;
  /** The run shown in the drawer (`null`: none). */
  openRun: number | null;
  onOpenRun: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const [status, setStatus] = useState<ReportRunStatus | null>(null);
  const filterKey = JSON.stringify([scheduleFilter, status]);
  const [paging, setPaging] = useState({ key: filterKey, page: 0 });
  const page = paging.key === filterKey ? paging.page : 0;
  const filters: RunFilters = { limit: RUN_PAGE_SIZE, offset: page * RUN_PAGE_SIZE };
  if (scheduleFilter !== null) filters.schedule_id = scheduleFilter;
  if (status) filters.status = status;
  const runs = useReportRuns(filters);
  const nameOf = useCallback(
    (run: Pick<RunOut, 'schedule_id'>) =>
      run.schedule_id === null
        ? null
        : (schedules.find((schedule) => schedule.id === run.schedule_id)?.name ??
          t('reports.runs.deletedSchedule')),
    [schedules, t],
  );
  const items = runs.data?.items ?? [];
  const total = runs.data?.total ?? 0;

  return (
    <Card>
      <CardHeader className="flex-wrap items-center">
        <CardTitle className="flex items-center gap-2">
          <History aria-hidden="true" className="size-4 text-muted-strong" />
          {t('reports.runs.title')}
        </CardTitle>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-schedule`} className="text-xs text-muted-strong">
              {t('reports.runs.filterSchedule')}
            </Label>
            <Select
              value={scheduleFilter === null ? ALL : String(scheduleFilter)}
              onValueChange={(value) => {
                onScheduleFilter(value === ALL ? null : Number(value));
              }}
            >
              <SelectTrigger id={`${id}-schedule`} className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('reports.runs.allSchedules')}</SelectItem>
                {schedules.map((schedule) => (
                  <SelectItem key={schedule.id} value={String(schedule.id)}>
                    {schedule.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-status`} className="text-xs text-muted-strong">
              {t('reports.runs.columns.status')}
            </Label>
            <Select
              value={status ?? ALL}
              onValueChange={(value) => {
                setStatus(value === ALL ? null : (value as ReportRunStatus));
              }}
            >
              <SelectTrigger id={`${id}-status`} className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('reports.runs.allStatuses')}</SelectItem>
                {STATUSES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {t(`reports.status.${item}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {runs.isPending ? (
          <Skeleton className="h-40" />
        ) : runs.isError ? (
          <Alert tone="danger">
            <span className="flex flex-wrap items-center gap-2">
              {errorMessage(runs.error)}
              <Button size="sm" variant="secondary" onClick={() => void runs.refresh()}>
                <RefreshCw aria-hidden="true" />
                {t('common.retry')}
              </Button>
            </span>
          </Alert>
        ) : items.length === 0 ? (
          <p className="text-13 text-muted">{t('reports.runs.empty')}</p>
        ) : (
          <div className="max-h-[32rem] overflow-auto rounded-lg border border-border">
            <table className="w-full border-separate border-spacing-0 text-13">
              <caption className="sr-only">{t('reports.runs.caption')}</caption>
              <thead>
                <tr>
                  {(
                    [
                      'started',
                      'report',
                      'period',
                      'status',
                      'delivery',
                      'files',
                      'trigger',
                    ] as const
                  ).map((column) => (
                    <th key={column} scope="col" className={TH}>
                      {t(`reports.runs.columns.${column}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((run) => (
                  <tr
                    key={run.id}
                    className={run.id === highlight ? 'bg-primary-soft/40' : undefined}
                  >
                    <td className={`${TD} tabular`}>
                      <button
                        type="button"
                        className="rounded-sm text-left text-accent-ink hover:underline"
                        aria-label={t('reports.runs.open', { id: run.id })}
                        onClick={() => {
                          onOpenRun(run.id);
                        }}
                      >
                        {formatDateTime(run.started_at)}
                      </button>
                    </td>
                    <td className={TD}>
                      <span className="flex flex-col">
                        <span>{nameOf(run) ?? kindLabel(run.kind, t)}</span>
                        <span className="text-xs text-muted-strong">{kindLabel(run.kind, t)}</span>
                      </span>
                    </td>
                    <td className={`${TD} tabular`}>
                      {periodText(run.period.from, run.period.to)}
                    </td>
                    <td className={TD}>
                      <RunStatusBadge status={shownStatus(run)} />
                    </td>
                    <td className={TD}>{deliveryText(run, t)}</td>
                    <td className={`${TD} tabular`}>{formatNumber(run.files.length, 0, lang)}</td>
                    <td className={TD}>
                      {run.triggered_by === 'manual'
                        ? t('reports.runs.manualBy', { user: run.requested_by ?? '—' })
                        : t('reports.runs.scheduled')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager
          offset={runs.data?.offset ?? page * RUN_PAGE_SIZE}
          total={total}
          pageSize={RUN_PAGE_SIZE}
          label={t('reports.runs.pager')}
          onPage={(next) => {
            setPaging({ key: filterKey, page: next });
          }}
        />
      </CardContent>
      <RunSheet
        runId={openRun}
        nameOf={nameOf}
        onClose={() => {
          onOpenRun(null);
        }}
      />
    </Card>
  );
}
