import { useEffect, useId, useState } from 'react';
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
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useCreatePerson, useDepartments, useUpdatePerson } from '@/lib/api-hooks';
import type { PersonOut } from '@/lib/api-types';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import {
  NO_DEPARTMENT,
  personChanges,
  personCreateBody,
  personFormValues,
  validatePerson,
  type PersonField,
  type PersonFormValues,
} from './persons-model';

/**
 * "Kişi ekle" (`person` null) and "Kişiyi düzenle". Persons are never deleted: the edit form's
 * "Etkin" box deactivates one, and the history stays.
 */
export function PersonDialog({
  open,
  person,
  onOpenChange,
  returnFocus,
}: {
  open: boolean;
  /** `null`: a new person. */
  person: PersonOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const departments = useDepartments(open);
  const create = useCreatePerson();
  const update = useUpdatePerson();
  const [values, setValues] = useState<PersonFormValues>(() => personFormValues(person));
  const [errors, setErrors] = useState<FieldErrors<PersonField>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;

  useEffect(() => {
    if (open) {
      setValues(personFormValues(person));
      setErrors({});
      setFailure(null);
    }
  }, [open, person]);

  function set<K extends keyof PersonFormValues>(field: K, value: PersonFormValues[K]) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validatePerson(values);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    try {
      if (person) {
        const changes = personChanges(person, values);
        if (Object.keys(changes).length > 0)
          await withReauth(() => update.mutateAsync({ id: person.id, ...changes }));
        toast({
          title: t('devices.persons.saved', { name: values.name.trim() }),
          variant: 'success',
        });
      } else {
        const created = await withReauth(() => create.mutateAsync(personCreateBody(values)));
        toast({ title: t('devices.persons.created', { name: created.name }), variant: 'success' });
      }
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const message = (field: PersonField) => errors[field] && t(errors[field]);
  const describedBy = (field: PersonField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;
  const text = (field: Exclude<PersonField, 'department'>, key: keyof PersonFormValues) => ({
    id: `${id}-${field}`,
    autoComplete: 'off',
    value: values[key] as string,
    'aria-invalid': message(field) ? true : undefined,
    onChange: (event: { target: { value: string } }) => {
      set(key, event.target.value);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus} className="max-w-xl">
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>
              {person ? t('devices.persons.editTitle') : t('devices.persons.createTitle')}
            </DialogTitle>
            <DialogDescription>{t('devices.persons.formDescription')}</DialogDescription>
          </DialogHeader>
          {failure ? <Alert tone="danger">{failure}</Alert> : null}
          <Field
            id={`${id}-name`}
            label={t('devices.persons.name')}
            error={message('name')}
            required
          >
            <Input {...text('name', 'name')} aria-describedby={describedBy('name')} autoFocus />
          </Field>
          <Field id={`${id}-department`} label={t('devices.persons.department')}>
            <Select
              value={values.department}
              onValueChange={(value) => {
                set('department', value);
              }}
            >
              <SelectTrigger id={`${id}-department`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_DEPARTMENT}>{t('devices.persons.noDepartment')}</SelectItem>
                {(departments.data ?? []).map((department) => (
                  <SelectItem key={department.id} value={String(department.id)}>
                    {department.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id={`${id}-title`} label={t('devices.persons.title')} error={message('title')}>
              <Input {...text('title', 'title')} aria-describedby={describedBy('title')} />
            </Field>
            <Field
              id={`${id}-employeeNo`}
              label={t('devices.persons.employeeNo')}
              hint={t('devices.persons.employeeNoHint')}
              error={message('employeeNo')}
            >
              <Input
                {...text('employeeNo', 'employeeNo')}
                aria-describedby={describedBy('employeeNo', true)}
              />
            </Field>
          </div>
          <Field id={`${id}-email`} label={t('devices.persons.email')} error={message('email')}>
            <Input
              {...text('email', 'email')}
              type="email"
              aria-describedby={describedBy('email')}
            />
          </Field>
          <Field id={`${id}-notes`} label={t('devices.persons.notes')} error={message('notes')}>
            <Textarea
              id={`${id}-notes`}
              rows={2}
              value={values.notes}
              aria-invalid={message('notes') ? true : undefined}
              aria-describedby={describedBy('notes')}
              onChange={(event) => {
                set('notes', event.target.value);
              }}
            />
          </Field>
          {person ? (
            <div className="flex items-start gap-2">
              <Checkbox
                id={`${id}-active`}
                checked={values.active}
                aria-describedby={`${id}-active-hint`}
                onCheckedChange={(checked) => {
                  set('active', checked === true);
                }}
                className="mt-0.5"
              />
              <div className="flex flex-col gap-0.5">
                <Label htmlFor={`${id}-active`} className="font-normal">
                  {t('devices.persons.activeLabel')}
                </Label>
                <p id={`${id}-active-hint`} className="text-xs text-muted">
                  {t('devices.persons.keepsHistory')}
                </p>
              </div>
            </div>
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
            <Button type="submit" busy={busy}>
              {busy ? t('common.working') : person ? t('common.save') : t('devices.persons.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
