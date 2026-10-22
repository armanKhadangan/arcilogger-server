import { FileText, Plus, RefreshCw } from 'lucide-react';
import { useCallback, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useDeleteSchedule, useReportSchedules } from '@/lib/api-hooks';
import type { ScheduleOut } from '@/lib/api-types';
import { parseId } from '@/lib/display';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { PreviewDialog } from './preview-dialog';
import { RunDialog } from './run-dialog';
import { RunsSection } from './runs-section';
import { ScheduleDialog } from './schedule-dialog';
import { SchedulesTable, type ScheduleAction } from './schedules-section';

/**
 * Raporlar (spec 10.5, 12.3 item 12; `reports.manage`): the scheduled reports (end of day, full
 * day, weekly, monthly) with their recipients, formats and detail, "Şimdi çalıştır", "Önizle" and
 * the run history with each recipient's delivery and the files. Person-level detail is on by
 * default (owner decision 2026-10-20); every change is audited by the server. `?run=<id>` opens
 * that run (the report e-mail links to it when its attachments were too big).
 */
export function ReportsPage() {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const schedules = useReportSchedules();
  const remove = useDeleteSchedule();
  const list = useMemo(() => schedules.data ?? [], [schedules.data]);
  const [editing, setEditing] = useState<{ schedule: ScheduleOut | null } | null>(null);
  const [running, setRunning] = useState<ScheduleOut | null>(null);
  const [previewing, setPreviewing] = useState<ScheduleOut | null>(null);
  const [deleting, setDeleting] = useState<ScheduleOut | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState<number | null>(null);
  const [queued, setQueued] = useState<number | null>(null);
  const [params, setParams] = useSearchParams();
  const openRun = parseId(params.get('run'));
  const setOpenRun = useCallback(
    (id: number | null) => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        if (id === null) next.delete('run');
        else next.set('run', String(id));
        return next;
      });
    },
    [setParams],
  );
  const trigger = useRef<HTMLElement | null>(null);
  const history = useRef<HTMLDivElement>(null);
  const returnFocus: RefObject<HTMLElement | null> = trigger;

  function act(action: ScheduleAction, schedule: ScheduleOut, from: HTMLElement | null) {
    trigger.current = from;
    if (action === 'edit') setEditing({ schedule });
    else if (action === 'run') setRunning(schedule);
    else if (action === 'preview') setPreviewing(schedule);
    else if (action === 'history') {
      setHistoryFilter(schedule.id);
      history.current?.scrollIntoView({ block: 'start' });
    } else {
      setDeleteError(null);
      setDeleting(schedule);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteError(null);
    try {
      await withReauth(() => remove.mutateAsync(deleting.id));
      toast({ title: t('reports.delete.done', { name: deleting.name }), variant: 'success' });
      if (historyFilter === deleting.id) setHistoryFilter(null);
      setDeleting(null);
    } catch (error) {
      if (!isReauthCancelled(error)) setDeleteError(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.reports')}
        description={t('reports.description')}
        actions={
          <Button
            onClick={(event) => {
              trigger.current = event.currentTarget;
              setEditing({ schedule: null });
            }}
          >
            <Plus aria-hidden="true" />
            {t('reports.schedules.add')}
          </Button>
        }
      />
      <Alert tone="info" assertive={false} data-testid="person-level-note">
        {t('reports.personLevelNote')}
      </Alert>

      <section className="flex flex-col gap-3" aria-labelledby="schedules-title">
        <h2 id="schedules-title" className="text-base font-semibold text-foreground">
          {t('reports.schedules.title')}
        </h2>
        {schedules.isPending ? (
          <Skeleton className="h-40" />
        ) : schedules.isError ? (
          <Alert tone="danger">
            <span className="flex flex-wrap items-center gap-2">
              {errorMessage(schedules.error)}
              <Button size="sm" variant="secondary" onClick={() => void schedules.refresh()}>
                <RefreshCw aria-hidden="true" />
                {t('common.retry')}
              </Button>
            </span>
          </Alert>
        ) : list.length === 0 ? (
          <EmptyState
            icon={FileText}
            title={t('reports.schedules.empty')}
            description={t('reports.schedules.emptyHint')}
          />
        ) : (
          <SchedulesTable schedules={list} onAction={act} />
        )}
      </section>

      <div ref={history} className="scroll-mt-20">
        <RunsSection
          schedules={list}
          scheduleFilter={historyFilter}
          onScheduleFilter={setHistoryFilter}
          highlight={queued}
          openRun={openRun}
          onOpenRun={setOpenRun}
        />
      </div>

      <ScheduleDialog
        open={editing !== null}
        schedule={editing?.schedule ?? null}
        returnFocus={returnFocus}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      />
      <RunDialog
        schedule={running}
        returnFocus={returnFocus}
        onOpenChange={(open) => {
          if (!open) setRunning(null);
        }}
        onQueued={(run) => {
          setQueued(run.id);
          setHistoryFilter(null);
        }}
      />
      <PreviewDialog
        schedule={previewing}
        returnFocus={returnFocus}
        onOpenChange={(open) => {
          if (!open) setPreviewing(null);
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={t('reports.delete.title')}
        description={t('reports.delete.body', { name: deleting?.name ?? '' })}
        confirmLabel={t('reports.delete.confirm')}
        danger
        busy={remove.isPending}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
        returnFocus={returnFocus}
      />
    </div>
  );
}
