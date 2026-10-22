import { useQueryClient } from '@tanstack/react-query';
import { Ellipsis, LoaderCircle, RotateCcw } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { queryKeys, useRebuildJob, useStartRebuild } from '@/lib/api-hooks';
import type { RebuildJob } from '@/lib/api-types';
import { formatDay } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { rebuildRequest, type DayRange, type RebuildForm } from './usage-model';

function percent(job: RebuildJob | undefined): string {
  const value = Math.round(Math.min(1, Math.max(0, job?.progress ?? 0)) * 100);
  return `${formatNumber(value, 0, currentLang())} %`;
}

function Progress({ job }: { job: RebuildJob | undefined }) {
  const { t } = useTranslation();
  const value = Math.round(Math.min(1, Math.max(0, job?.progress ?? 0)) * 100);
  return (
    <div className="flex flex-col gap-2">
      <div
        role="progressbar"
        aria-label={t('usage.rebuild.progress')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        className="h-2 w-full overflow-hidden rounded-full bg-subtle"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${value}%` }} />
      </div>
      <p className="text-13 text-muted-strong" role="status" data-testid="rebuild-status">
        {!job || job.status === 'queued'
          ? t('usage.rebuild.queued')
          : job.status === 'running'
            ? t('usage.rebuild.running', { percent: percent(job) })
            : job.status === 'done'
              ? t('usage.rebuild.done')
              : t('usage.rebuild.failed', { error: job.error ?? '—' })}
      </p>
      {job ? (
        <p className="text-xs text-muted">
          {t('usage.rebuild.requested', {
            days:
              job.from === job.to
                ? formatDay(job.from)
                : `${formatDay(job.from)} – ${formatDay(job.to)}`,
            user: job.requested_by,
            time: formatDateTime(job.created_at),
          })}
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Yeniden hesapla" for administrators (`settings.manage`, spec 10.4): recomputes the usage
 * rollups of a range of site days — e.g. after devices were assigned to persons — and follows the
 * job until it is done; then every usage view is read again. Raw logs never change.
 */
export function RebuildMenu({ range, todayDay }: { range: DayRange; todayDay: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const { withReauth } = useReauth();
  const trigger = useRef<HTMLButtonElement>(null);
  const fromId = useId();
  const toId = useId();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<RebuildForm>({ from: '', to: '' });
  const [errors, setErrors] = useState<{ from?: string; to?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const start = useStartRebuild();
  const job = useRebuildJob(jobId);
  const status = job.data?.status;
  const running = jobId !== null && status !== 'done' && status !== 'failed';
  const announced = useRef<number | null>(null);

  useEffect(() => {
    if (!jobId || announced.current === jobId) return;
    if (status === 'done') {
      announced.current = jobId;
      toast({ title: t('usage.rebuild.done'), variant: 'success' });
      void client.invalidateQueries({ queryKey: queryKeys.usageAll });
    } else if (status === 'failed') {
      announced.current = jobId;
      toast({
        title: t('usage.rebuild.failedTitle'),
        description: job.data?.error ?? undefined,
        variant: 'danger',
      });
    }
  }, [status, jobId, client, t, job.data?.error]);

  function openDialog() {
    if (!running) {
      setForm({ from: formatDay(range.from), to: formatDay(range.to) });
      setErrors({});
      setFailure(null);
      if (status === 'done' || status === 'failed') setJobId(null);
    }
    setOpen(true);
  }

  async function submit() {
    const { body, errors: problems } = rebuildRequest(form, todayDay);
    setErrors(problems);
    setFailure(null);
    if (!body) return;
    try {
      const accepted = await withReauth(() => start.mutateAsync(body));
      announced.current = null;
      setJobId(accepted.job_id);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <div className="flex items-center gap-2">
      {running ? (
        <button
          type="button"
          className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-soft px-2.5 text-13 font-medium text-primary-ink"
          onClick={openDialog}
          data-testid="rebuild-chip"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          {t('usage.rebuild.chip', { percent: percent(job.data) })}
        </button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            ref={trigger}
            variant="secondary"
            size="icon"
            aria-label={t('usage.rebuild.menu')}
            title={t('usage.rebuild.menu')}
          >
            <Ellipsis aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={openDialog}>
            <RotateCcw aria-hidden="true" />
            {t('usage.rebuild.action')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent returnFocus={trigger}>
          <DialogHeader>
            <DialogTitle>{t('usage.rebuild.title')}</DialogTitle>
            <DialogDescription>{t('usage.rebuild.description')}</DialogDescription>
          </DialogHeader>
          {jobId !== null ? (
            <Progress job={job.data} />
          ) : (
            <form
              noValidate
              className="flex flex-col gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  id={fromId}
                  label={t('usage.rebuild.from')}
                  required
                  error={errors.from ? t(errors.from) : undefined}
                  hint={t('usage.rebuild.dayHint')}
                >
                  <Input
                    id={fromId}
                    inputMode="numeric"
                    autoComplete="off"
                    value={form.from}
                    aria-invalid={errors.from ? true : undefined}
                    aria-describedby={errors.from ? `${fromId}-error` : `${fromId}-hint`}
                    onChange={(event) => {
                      setForm({ ...form, from: event.target.value });
                    }}
                  />
                </Field>
                <Field
                  id={toId}
                  label={t('usage.rebuild.to')}
                  required
                  error={errors.to ? t(errors.to) : undefined}
                  hint={t('usage.rebuild.dayHint')}
                >
                  <Input
                    id={toId}
                    inputMode="numeric"
                    autoComplete="off"
                    value={form.to}
                    aria-invalid={errors.to ? true : undefined}
                    aria-describedby={errors.to ? `${toId}-error` : `${toId}-hint`}
                    onChange={(event) => {
                      setForm({ ...form, to: event.target.value });
                    }}
                  />
                </Field>
              </div>
              {failure ? <Alert tone="danger">{failure}</Alert> : null}
              <DialogFooter>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setOpen(false);
                  }}
                >
                  {t('common.cancel')}
                </Button>
                <Button type="submit" busy={start.isPending}>
                  <RotateCcw aria-hidden="true" />
                  {t('usage.rebuild.start')}
                </Button>
              </DialogFooter>
            </form>
          )}
          {jobId !== null ? (
            <DialogFooter>
              <Button
                variant={running ? 'secondary' : 'primary'}
                onClick={() => {
                  setOpen(false);
                }}
              >
                {running ? t('usage.rebuild.background') : t('common.close')}
              </Button>
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
