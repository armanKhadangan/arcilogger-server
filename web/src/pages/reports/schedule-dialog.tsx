import { CalendarClock } from 'lucide-react';
import { useEffect, useId, useMemo, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
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
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useCreateSchedule, useUpdateSchedule } from '@/lib/api-hooks';
import type { ReportFormats, ScheduleOut } from '@/lib/api-types';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { RecipientsInput } from './recipients-input';
import {
  formTiming,
  isReportKind,
  MONTHDAY_MAX,
  NAME_MAX,
  REPORT_KINDS,
  scheduleChanges,
  scheduleCreateBody,
  scheduleFormValues,
  validateSchedule,
  withKind,
  type ScheduleField,
  type ScheduleFormValues,
} from './schedule-model';
import { kindLabel, nextRunText } from './schedule-text';

const FORMATS: (keyof ReportFormats)[] = ['email_html', 'pdf', 'xlsx'];

/**
 * "Yeni zamanlama" (`schedule` null) and "Zamanlamayı düzenle": name, kind with its day and time,
 * recipients, formats and detail (person-level on, pseudonyms off by default — owner decision of
 * 2026-10-20). The next run and its period are previewed as the form changes.
 */
export function ScheduleDialog({
  open,
  schedule,
  onOpenChange,
  returnFocus,
}: {
  open: boolean;
  schedule: ScheduleOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const create = useCreateSchedule();
  const update = useUpdateSchedule();
  const [values, setValues] = useState<ScheduleFormValues>(() => scheduleFormValues(schedule));
  const [errors, setErrors] = useState<FieldErrors<ScheduleField>>({});
  const [chipProblem, setChipProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;

  useEffect(() => {
    if (open) {
      setValues(scheduleFormValues(schedule));
      setErrors({});
      setChipProblem(null);
      setFailure(null);
    }
  }, [open, schedule]);

  const preview = useMemo(() => nextRunText(formTiming(values), new Date(), t), [values, t]);

  function set<K extends keyof ScheduleFormValues>(field: K, value: ScheduleFormValues[K]) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateSchedule(values);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) =>
        field === 'formats' ? `${id}-format-email_html` : `${id}-${field}`,
      );
      return;
    }
    try {
      if (schedule) {
        const changes = scheduleChanges(schedule, values);
        if (Object.keys(changes).length > 0)
          await withReauth(() => update.mutateAsync({ id: schedule.id, ...changes }));
        toast({ title: t('reports.form.saved', { name: values.name.trim() }), variant: 'success' });
      } else {
        const created = await withReauth(() => create.mutateAsync(scheduleCreateBody(values)));
        toast({ title: t('reports.form.created', { name: created.name }), variant: 'success' });
      }
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const message = (field: ScheduleField) =>
    field === 'recipients' && chipProblem
      ? t(chipProblem)
      : errors[field] && t(errors[field], { max: field === 'name' ? NAME_MAX : MONTHDAY_MAX });
  const describedBy = (field: ScheduleField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus} className="max-w-2xl">
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>
              {schedule ? t('reports.form.editTitle') : t('reports.form.createTitle')}
            </DialogTitle>
            <DialogDescription>{t('reports.form.description')}</DialogDescription>
          </DialogHeader>
          {failure ? <Alert tone="danger">{failure}</Alert> : null}
          <Field id={`${id}-name`} label={t('reports.form.name')} error={message('name')} required>
            <Input
              id={`${id}-name`}
              autoComplete="off"
              value={values.name}
              aria-invalid={message('name') ? true : undefined}
              aria-describedby={describedBy('name')}
              onChange={(event) => {
                set('name', event.target.value);
              }}
              autoFocus
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field id={`${id}-kind`} label={t('reports.form.kind')}>
              <Select
                value={values.kind}
                onValueChange={(value) => {
                  if (isReportKind(value)) setValues((current) => withKind(current, value));
                }}
              >
                <SelectTrigger id={`${id}-kind`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {REPORT_KINDS.map((kind) => (
                    <SelectItem key={kind} value={kind}>
                      {kindLabel(kind, t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {values.kind === 'weekly' ? (
              <Field
                id={`${id}-weekday`}
                label={t('reports.form.weekday')}
                error={message('weekday')}
                required
              >
                <Select
                  value={values.weekday}
                  onValueChange={(value) => {
                    set('weekday', value);
                  }}
                >
                  <SelectTrigger id={`${id}-weekday`} aria-required>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[1, 2, 3, 4, 5, 6, 7].map((day) => (
                      <SelectItem key={day} value={String(day)}>
                        {t(`reports.weekdays.${day}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            ) : null}
            {values.kind === 'monthly' ? (
              <Field
                id={`${id}-monthday`}
                label={t('reports.form.monthday')}
                hint={t('reports.form.monthdayHint')}
                error={message('monthday')}
                required
              >
                <Input
                  id={`${id}-monthday`}
                  inputMode="numeric"
                  autoComplete="off"
                  value={values.monthday}
                  aria-invalid={message('monthday') ? true : undefined}
                  aria-describedby={describedBy('monthday', true)}
                  onChange={(event) => {
                    set('monthday', event.target.value);
                  }}
                />
              </Field>
            ) : null}
            <Field
              id={`${id}-time`}
              label={t('reports.form.time')}
              hint={t('reports.form.timeHint')}
              error={message('time')}
              required
            >
              <Input
                id={`${id}-time`}
                inputMode="numeric"
                autoComplete="off"
                placeholder="SS:dd"
                value={values.time}
                aria-invalid={message('time') ? true : undefined}
                aria-describedby={describedBy('time', true)}
                onChange={(event) => {
                  set('time', event.target.value);
                }}
              />
            </Field>
          </div>
          <p
            className="flex items-start gap-2 rounded-lg bg-subtle px-3 py-2 text-13 text-foreground"
            data-testid="next-run-preview"
            aria-live="polite"
          >
            <CalendarClock
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-muted-strong"
            />
            {preview}
          </p>
          <Field
            id={`${id}-recipients`}
            label={t('reports.form.recipients')}
            hint={t('reports.form.recipientsHint')}
            error={message('recipients')}
          >
            <RecipientsInput
              id={`${id}-recipients`}
              recipients={values.recipients}
              pending={values.pending}
              invalid={Boolean(message('recipients'))}
              describedBy={describedBy('recipients', true)}
              onChange={(next) => {
                setValues((current) => ({ ...current, ...next }));
                setErrors((current) => ({ ...current, recipients: undefined }));
              }}
              onProblem={setChipProblem}
            />
          </Field>
          <fieldset className="flex flex-col gap-2" aria-describedby={describedBy('formats')}>
            <legend className="mb-1 text-13 font-medium text-foreground">
              {t('reports.form.formats')}
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              {FORMATS.map((format) => (
                <div key={format} className="flex items-center gap-2">
                  <Checkbox
                    id={`${id}-format-${format}`}
                    checked={values.formats[format]}
                    onCheckedChange={(checked) => {
                      set('formats', { ...values.formats, [format]: checked === true });
                    }}
                  />
                  <Label htmlFor={`${id}-format-${format}`} className="font-normal">
                    {t(`reports.formats.${format}`)}
                  </Label>
                </div>
              ))}
            </div>
            {message('formats') ? (
              <p id={`${id}-formats-error`} className="text-xs text-danger-ink">
                {message('formats')}
              </p>
            ) : null}
          </fieldset>
          <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
            {(
              [
                ['personLevel', 'reports.form.personLevel', 'reports.form.personLevelHint'],
                ['pseudonymize', 'reports.form.pseudonymize', 'reports.form.pseudonymizeHint'],
                ['enabled', 'reports.form.enabled', 'reports.form.enabledHint'],
              ] as const
            ).map(([field, label, hint]) => (
              <div key={field} className="flex items-start gap-2">
                <Checkbox
                  id={`${id}-${field}`}
                  checked={values[field]}
                  aria-describedby={`${id}-${field}-hint`}
                  onCheckedChange={(checked) => {
                    set(field, checked === true);
                  }}
                  className="mt-0.5"
                />
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor={`${id}-${field}`} className="font-normal">
                    {t(label)}
                  </Label>
                  <p id={`${id}-${field}-hint`} className="text-xs text-muted">
                    {t(hint)}
                  </p>
                </div>
              </div>
            ))}
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
            <Button type="submit" busy={busy}>
              {busy ? t('common.working') : schedule ? t('common.save') : t('reports.form.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
