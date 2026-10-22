import { useEffect, useId, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
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
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError } from '@/lib/api';
import { useAssignDevices } from '@/lib/api-hooks';
import type { DeviceOut, PersonRef } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { cn } from '@/lib/utils';
import { focusFirstError, type FieldErrors } from '@/lib/validation';

import {
  checkAssign,
  initialDraft,
  latestAssignedSince,
  type AssignDraft,
  type AssignField,
  type AssignMode,
} from './assign-model';
import { RandomizedNotice } from './device-parts';
import { PersonPicker } from './person-picker';

/** MAC addresses listed by name in the dialog; the rest are counted. */
const LISTED = 6;

/**
 * "Kişiye ata" / "Atamayı kaldır" for the selected devices (one request, atomic on the server).
 * The start of validity is "now" unless the user enters an earlier moment; the server refuses a
 * start in the future or before a device's current assignment (409), and the dialog shows its
 * answer as it comes, with what to change.
 */
export function AssignDialog({
  mode,
  devices,
  open,
  onOpenChange,
  onDone,
  returnFocus,
}: {
  mode: AssignMode;
  devices: readonly DeviceOut[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone?: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const assign = useAssignDevices();
  const { reset } = assign;
  const [draft, setDraft] = useState<AssignDraft>(() => initialDraft(new Date()));
  const [person, setPerson] = useState<PersonRef | null>(null);
  const [errors, setErrors] = useState<FieldErrors<AssignField>>({});
  const [failure, setFailure] = useState<{ text: string; conflict: boolean } | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(initialDraft(new Date()));
      setPerson(null);
      setErrors({});
      setFailure(null);
      reset();
    }
  }, [open, reset]);

  const latest = latestAssignedSince(devices);
  const randomized = devices.filter((device) => device.randomized).length;

  function update(patch: Partial<AssignDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const check = checkAssign(
      mode,
      { ...draft, personId: person?.id ?? null },
      devices,
      new Date(),
    );
    setErrors(check.errors);
    setFailure(null);
    if (!check.body) {
      focusFirstError(check.errors, (field) => `${id}-${field}`);
      return;
    }
    const body = check.body;
    try {
      const result = await withReauth(() => assign.mutateAsync(body));
      // A device the person already holds (or an unassigned one) is left as it is.
      const unchanged = body.macs.length - result.updated;
      toast({
        title:
          result.updated === 0
            ? t('devices.assign.nothingChanged')
            : mode === 'assign'
              ? t('devices.assign.doneAssign', { count: result.updated, name: person?.name ?? '' })
              : t('devices.assign.doneUnassign', { count: result.updated }),
        description:
          result.updated > 0 && unchanged > 0
            ? t('devices.assign.unchanged', { count: unchanged })
            : undefined,
        variant: result.updated === 0 ? 'info' : 'success',
      });
      onOpenChange(false);
      onDone?.();
    } catch (error) {
      if (isReauthCancelled(error)) return;
      setFailure({
        text: errorMessage(error),
        // The history is appended to, never rewritten: the start is what to change.
        conflict: isApiError(error, 'assignment_overlap', 'assignment_in_future'),
      });
    }
  }

  const message = (field: AssignField) => errors[field] && t(errors[field]);
  const describedBy = (field: AssignField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;
  const listed = devices.slice(0, LISTED);
  const title =
    mode === 'assign' ? t('devices.assign.titleAssign') : t('devices.assign.titleUnassign');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus} className="max-w-xl">
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>
              {mode === 'assign'
                ? t('devices.assign.descriptionAssign', { count: devices.length })
                : t('devices.assign.descriptionUnassign', { count: devices.length })}
            </DialogDescription>
          </DialogHeader>
          <ul
            className="flex flex-wrap gap-1.5"
            aria-label={t('devices.assign.selected')}
            id={`${id}-devices`}
            tabIndex={-1}
          >
            {listed.map((device) => (
              <li
                key={device.mac}
                className="rounded-md bg-subtle px-2 py-0.5 font-mono text-xs text-foreground"
              >
                {device.mac}
                {device.person ? (
                  <span className="ml-1 font-sans text-muted-strong">
                    ({visibleText(device.person.name)})
                  </span>
                ) : null}
              </li>
            ))}
            {devices.length > LISTED ? (
              <li className="px-1 py-0.5 text-xs text-muted-strong">
                {t('devices.assign.andMore', { count: devices.length - LISTED })}
              </li>
            ) : null}
          </ul>
          {message('devices') ? <Alert tone="danger">{message('devices')}</Alert> : null}
          {mode === 'assign' && randomized > 0 ? <RandomizedNotice count={randomized} /> : null}
          {failure ? (
            <Alert
              tone="danger"
              title={failure.conflict ? t('devices.assign.conflictTitle') : undefined}
            >
              <p>{failure.text}</p>
              {failure.conflict ? <p className="mt-1">{t('devices.assign.conflictHint')}</p> : null}
            </Alert>
          ) : null}

          {mode === 'assign' ? (
            <Field
              id={`${id}-person`}
              label={t('devices.assign.person')}
              error={message('person')}
              required
            >
              <PersonPicker
                id={`${id}-person`}
                value={person}
                onChange={setPerson}
                invalid={Boolean(message('person'))}
                describedBy={describedBy('person')}
              />
            </Field>
          ) : null}

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-13 font-medium text-foreground">
              {t('devices.assign.validFrom')}
            </legend>
            {(['now', 'at'] as const).map((when) => (
              <label
                key={when}
                className={cn(
                  'flex cursor-pointer items-start gap-2 rounded-lg border border-border px-3 py-2 text-13',
                  draft.when === when && 'border-primary ring-1 ring-primary',
                )}
              >
                <input
                  type="radio"
                  name={`${id}-when`}
                  value={when}
                  checked={draft.when === when}
                  onChange={() => {
                    update({ when });
                  }}
                  className="mt-0.5 size-4 accent-[var(--primary)]"
                />
                <span className="flex flex-col gap-0.5">
                  <span className="font-medium text-foreground">
                    {t(`devices.assign.when.${when}`)}
                  </span>
                  <span className="text-muted">{t(`devices.assign.when.${when}Hint`)}</span>
                </span>
              </label>
            ))}
            {draft.when === 'at' ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  id={`${id}-day`}
                  label={t('devices.assign.day')}
                  hint={t('devices.assign.dayHint')}
                  error={message('day')}
                  required
                >
                  <Input
                    id={`${id}-day`}
                    inputMode="numeric"
                    autoComplete="off"
                    value={draft.day}
                    aria-invalid={message('day') ? true : undefined}
                    aria-describedby={describedBy('day', true)}
                    onChange={(event) => {
                      update({ day: event.target.value });
                    }}
                  />
                </Field>
                <Field
                  id={`${id}-time`}
                  label={t('devices.assign.time')}
                  hint={t('devices.assign.timeHint')}
                  error={message('time')}
                  required
                >
                  <Input
                    id={`${id}-time`}
                    inputMode="numeric"
                    autoComplete="off"
                    value={draft.time}
                    aria-invalid={message('time') ? true : undefined}
                    aria-describedby={describedBy('time', true)}
                    onChange={(event) => {
                      update({ time: event.target.value });
                    }}
                  />
                </Field>
                <p className="text-xs text-muted sm:col-span-2">
                  {latest
                    ? t('devices.assign.rulesWithCurrent', { time: formatDateTime(latest) })
                    : t('devices.assign.rules')}
                </p>
              </div>
            ) : null}
          </fieldset>

          {/* The note is kept on the new assignment: removing one opens none, so it has none. */}
          {mode === 'assign' ? (
            <Field
              id={`${id}-note`}
              label={t('devices.assign.note')}
              hint={t('devices.assign.noteHint')}
              error={message('note')}
            >
              <Textarea
                id={`${id}-note`}
                rows={2}
                value={draft.note}
                aria-invalid={message('note') ? true : undefined}
                aria-describedby={describedBy('note', true)}
                onChange={(event) => {
                  update({ note: event.target.value });
                }}
              />
            </Field>
          ) : null}

          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              variant={mode === 'unassign' ? 'danger' : 'primary'}
              busy={assign.isPending}
            >
              {assign.isPending
                ? t('common.working')
                : mode === 'assign'
                  ? t('devices.assign.submitAssign')
                  : t('devices.assign.submitUnassign')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
