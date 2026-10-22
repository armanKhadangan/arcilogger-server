import { Search } from 'lucide-react';
import { useId, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { errorMessage } from '@/lib/api';
import { useRunLookup } from '@/lib/api-hooks';
import type { LookupOut } from '@/lib/api-types';
import { siteToday } from '@/lib/display';
import { hasErrors, type FieldErrors } from '@/lib/validation';

import {
  LIMITS,
  MAX_TOLERANCE_SECONDS,
  PROTOCOLS,
  serverFieldErrors,
  TIME_ZONES,
  toLookupBody,
  validateLookup,
  type LookupDraft,
  type LookupField,
} from './lookup-form-model';

/** Radix Select needs a non-empty value for "not given". */
const ANY = 'any';

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <legend className="mb-1 text-sm font-semibold text-foreground">{title}</legend>
      {hint ? <p className="-mt-2 text-xs text-muted">{hint}</p> : null}
      {children}
    </fieldset>
  );
}

/**
 * Left column of "5651 Sorgu": the request details (all required: no results without a reason,
 * spec 9.5) and the query. Checked in the browser first, then by the server (422 details are
 * shown next to their fields).
 */
export function LookupForm({
  initial,
  onDone,
}: {
  initial: LookupDraft;
  onDone: (lookup: LookupOut) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const run = useRunLookup();
  const [draft, setDraft] = useState<LookupDraft>(initial);
  const [errors, setErrors] = useState<FieldErrors<LookupField>>({});
  const [server, setServer] = useState<Partial<Record<LookupField, string>>>({});
  const [failure, setFailure] = useState<string | null>(null);

  function set<K extends keyof LookupDraft>(field: K, value: LookupDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateLookup(draft, siteToday());
    setErrors(found);
    setServer({});
    setFailure(null);
    if (hasErrors(found)) {
      // Focus the first field with a problem (or the summary for "nothing to look for").
      const first = (Object.keys(found) as LookupField[])[0];
      window.requestAnimationFrame(() => {
        document.getElementById(`${id}-${first ?? 'target'}`)?.focus();
      });
      return;
    }
    run.mutate(toLookupBody(draft), {
      onSuccess: onDone,
      onError: (error) => {
        const fields = serverFieldErrors(error);
        setServer(fields);
        if (Object.keys(fields).length === 0) setFailure(errorMessage(error));
      },
    });
  }

  const message = (field: LookupField): string | undefined => {
    const own = errors[field];
    if (own) return t(own, { max: MAX_TOLERANCE_SECONDS });
    const remote = server[field];
    return remote === undefined ? undefined : t('lookup.validation.server', { message: remote });
  };

  /** Props wiring an input to its label, hint and error. */
  const control = (field: keyof LookupDraft, hint = false) => {
    const problem = message(field);
    return {
      id: `${id}-${field}`,
      value: draft[field],
      'aria-invalid': problem ? true : undefined,
      'aria-describedby': problem
        ? `${id}-${field}-error`
        : hint
          ? `${id}-${field}-hint`
          : undefined,
      onChange: (event: { target: { value: string } }) => {
        set(field, event.target.value);
      },
    };
  };

  const targetProblem = message('target');
  return (
    <Card className="p-4">
      <form
        onSubmit={submit}
        noValidate
        className="flex flex-col gap-6"
        aria-label={t('lookup.form.label')}
      >
        <Section title={t('lookup.form.requestTitle')} hint={t('lookup.form.requestHint')}>
          <Field
            id={`${id}-authority`}
            label={t('lookup.form.authority')}
            error={message('authority')}
            hint={t('lookup.form.authorityHint')}
            required
          >
            <Input
              {...control('authority', true)}
              maxLength={LIMITS.authority}
              autoComplete="off"
            />
          </Field>
          <Field
            id={`${id}-fileNo`}
            label={t('lookup.form.fileNo')}
            error={message('fileNo')}
            required
          >
            <Input {...control('fileNo')} maxLength={LIMITS.fileNo} autoComplete="off" />
          </Field>
          <Field
            id={`${id}-requestDate`}
            label={t('lookup.form.requestDate')}
            error={message('requestDate')}
            hint={t('dates.dayHint')}
            required
          >
            <Input
              {...control('requestDate', true)}
              inputMode="numeric"
              autoComplete="off"
              placeholder={t('dates.dayPlaceholder')}
            />
          </Field>
          <Field
            id={`${id}-reason`}
            label={t('lookup.form.reason')}
            error={message('reason')}
            hint={t('lookup.form.reasonHint')}
            required
          >
            <Textarea {...control('reason', true)} rows={3} maxLength={LIMITS.reason} />
          </Field>
        </Section>

        <Section title={t('lookup.form.queryTitle')} hint={t('lookup.form.queryHint')}>
          {targetProblem ? (
            <div id={`${id}-target`} tabIndex={-1} className="outline-none">
              <Alert tone="danger">{targetProblem}</Alert>
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_6.5rem]">
            <Field
              id={`${id}-publicIp`}
              label={t('lookup.form.publicIp')}
              error={message('publicIp')}
            >
              <Input
                {...control('publicIp')}
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="198.51.100.23"
              />
            </Field>
            <Field
              id={`${id}-publicPort`}
              label={t('lookup.form.publicPort')}
              error={message('publicPort')}
            >
              <Input {...control('publicPort')} mono inputMode="numeric" autoComplete="off" />
            </Field>
          </div>
          <Field
            id={`${id}-protocol`}
            label={t('lookup.form.protocol')}
            error={message('protocol')}
          >
            <Select
              value={draft.protocol || ANY}
              onValueChange={(value) => {
                set('protocol', value === ANY ? '' : value);
              }}
            >
              <SelectTrigger id={`${id}-protocol`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>{t('lookup.form.protocolAny')}</SelectItem>
                {PROTOCOLS.map((number) => (
                  <SelectItem key={number} value={number}>
                    {t(`lookup.form.protocols.${number}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              id={`${id}-date`}
              label={t('lookup.form.date')}
              error={message('date')}
              hint={t('dates.dayHint')}
              required
            >
              <Input
                {...control('date', true)}
                inputMode="numeric"
                autoComplete="off"
                placeholder={t('dates.dayPlaceholder')}
              />
            </Field>
            <Field
              id={`${id}-time`}
              label={t('lookup.form.time')}
              error={message('time')}
              hint={t('lookup.form.timeHint')}
              required
            >
              <Input
                {...control('time', true)}
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t('dates.timePlaceholder')}
              />
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_8rem]">
            <Field
              id={`${id}-timeZone`}
              label={t('lookup.form.timeZone')}
              error={message('timeZone')}
              hint={t('lookup.form.timeZoneHint')}
            >
              <Select
                value={draft.timeZone}
                onValueChange={(value) => {
                  set('timeZone', value);
                }}
              >
                <SelectTrigger id={`${id}-timeZone`} aria-describedby={`${id}-timeZone-hint`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIME_ZONES.map((zone) => (
                    <SelectItem key={zone} value={zone}>
                      {t(`lookup.form.zones.${zone === 'UTC' ? 'utc' : 'istanbul'}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field
              id={`${id}-tolerance`}
              label={t('lookup.form.tolerance')}
              error={message('tolerance')}
            >
              <Input {...control('tolerance')} mono inputMode="numeric" autoComplete="off" />
            </Field>
          </div>
        </Section>

        <Section title={t('lookup.form.destinationTitle')} hint={t('lookup.form.destinationHint')}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_6.5rem]">
            <Field id={`${id}-destIp`} label={t('lookup.form.destIp')} error={message('destIp')}>
              <Input {...control('destIp')} mono spellCheck={false} autoComplete="off" />
            </Field>
            <Field
              id={`${id}-destPort`}
              label={t('lookup.form.destPort')}
              error={message('destPort')}
            >
              <Input {...control('destPort')} mono inputMode="numeric" autoComplete="off" />
            </Field>
          </div>
          <Field
            id={`${id}-destDomain`}
            label={t('lookup.form.destDomain')}
            error={message('destDomain')}
          >
            <Input
              {...control('destDomain')}
              mono
              spellCheck={false}
              autoComplete="off"
              placeholder="ornek.com.tr"
            />
          </Field>
        </Section>

        <Section title={t('lookup.form.internalTitle')} hint={t('lookup.form.internalHint')}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              id={`${id}-internalIp`}
              label={t('lookup.form.internalIp')}
              error={message('internalIp')}
            >
              <Input {...control('internalIp')} mono spellCheck={false} autoComplete="off" />
            </Field>
            <Field id={`${id}-mac`} label={t('lookup.form.mac')} error={message('mac')}>
              <Input
                {...control('mac')}
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="aa:bb:cc:dd:ee:ff"
              />
            </Field>
          </div>
        </Section>

        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        <div className="flex flex-col gap-2">
          <Button type="submit" size="lg" busy={run.isPending}>
            <Search aria-hidden="true" />
            {run.isPending ? t('lookup.form.running') : t('lookup.form.submit')}
          </Button>
          <p className="text-xs text-muted">{t('lookup.form.auditNote')}</p>
        </div>
      </form>
    </Card>
  );
}
