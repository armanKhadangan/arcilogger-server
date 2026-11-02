import { Play } from 'lucide-react';
import { useId, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toast';
import { useRunSchedule } from '@/lib/api-hooks';
import type { RunOut, ScheduleOut } from '@/lib/api-types';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { focusFirstError, type FieldErrors } from '@/lib/validation';

import { PeriodChoice } from './period-fields';
import { ReportProblemAlert } from './report-parts';
import { reportProblem, type ReportProblem } from './report-errors';
import {
  checkPeriod,
  EMPTY_PERIOD,
  runBody,
  type PeriodField,
  type PeriodValues,
} from './schedule-model';
import { ownPeriodText } from './schedule-text';

/**
 * "Şimdi çalıştır": the schedule's report now, over its own period or a chosen one (then a
 * `custom` run), sent to its recipients or only kept in the run history (its files can be
 * downloaded there). E-mail switched off on the server (409 `report_smtp_disabled`) is explained
 * with where to set it up.
 */
export function RunDialog({
  schedule,
  onOpenChange,
  onQueued,
  returnFocus,
}: {
  /** `null`: closed. */
  schedule: ScheduleOut | null;
  onOpenChange: (open: boolean) => void;
  onQueued: (run: RunOut) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const run = useRunSchedule();
  const [custom, setCustom] = useState(false);
  const [period, setPeriod] = useState<PeriodValues>(EMPTY_PERIOD);
  const [errors, setErrors] = useState<FieldErrors<PeriodField>>({});
  const [send, setSend] = useState(true);
  const [failure, setFailure] = useState<ReportProblem | null>(null);
  const open = schedule !== null;
  const noRecipients = (schedule?.recipients.length ?? 0) === 0;

  // The form begins anew while rendering when the dialog opens (and again when the schedule's
  // recipients come or go), so its first frame never shows the previous period or choice to send:
  // an effect would run only after that frame.
  const openFor = open ? noRecipients : undefined;
  const [begunFor, setBegunFor] = useState<boolean | undefined>(undefined);
  if (openFor !== begunFor) {
    setBegunFor(openFor);
    if (open) {
      setCustom(false);
      setPeriod(EMPTY_PERIOD);
      setErrors({});
      setSend(!noRecipients);
      setFailure(null);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!schedule) return;
    let bounds: { from: string; to: string } | undefined;
    if (custom) {
      const checked = checkPeriod(period, new Date());
      setErrors(checked.errors);
      if (!checked.from || !checked.to) {
        focusFirstError(checked.errors, (field) => `${id}-${field}`);
        return;
      }
      bounds = { from: checked.from, to: checked.to };
    }
    setFailure(null);
    try {
      const queued = await withReauth(() =>
        run.mutateAsync({ id: schedule.id, ...runBody(send && !noRecipients, bounds) }),
      );
      toast({ title: t('reports.run.queued', { id: queued.id }), variant: 'success' });
      onQueued(queued);
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(reportProblem(error, t));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus} className="max-w-xl">
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{t('reports.run.title')}</DialogTitle>
            <DialogDescription>{schedule?.name ?? ''}</DialogDescription>
          </DialogHeader>
          {failure ? <ReportProblemAlert problem={failure} /> : null}
          {schedule ? (
            <PeriodChoice
              id={id}
              custom={custom}
              onCustomChange={setCustom}
              defaultText={ownPeriodText(schedule.kind, new Date(), t)}
              values={period}
              onChange={setPeriod}
              errors={errors}
            />
          ) : null}
          <div className="flex items-start gap-2">
            <Checkbox
              id={`${id}-send`}
              checked={send && !noRecipients}
              disabled={noRecipients}
              aria-describedby={`${id}-send-hint`}
              onCheckedChange={(checked) => {
                setSend(checked === true);
              }}
              className="mt-0.5"
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-send`} className="font-normal">
                {t('reports.run.send', { count: schedule?.recipients.length ?? 0 })}
              </Label>
              <p id={`${id}-send-hint`} className="text-xs text-muted">
                {noRecipients ? t('reports.run.noRecipients') : t('reports.run.sendHint')}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={run.isPending}>
              <Play aria-hidden="true" />
              {run.isPending ? t('common.working') : t('reports.run.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
