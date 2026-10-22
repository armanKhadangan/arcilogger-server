import { useTranslation } from 'react-i18next';

import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import type { FieldErrors } from '@/lib/validation';
import { cn } from '@/lib/utils';

import type { PeriodField, PeriodValues } from './schedule-model';

function Choice({
  name,
  checked,
  onSelect,
  title,
  description,
}: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  description: string;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer gap-3 rounded-lg border border-border p-3 text-13',
        checked && 'border-primary ring-1 ring-primary',
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onSelect}
        className="mt-0.5 size-4 accent-[var(--primary)]"
      />
      <span className="flex flex-col gap-0.5">
        <span className="font-medium text-foreground">{title}</span>
        <span className="text-muted">{description}</span>
      </span>
    </label>
  );
}

/**
 * The period of a manual run or a preview: the schedule's own (the last complete period, shown)
 * or a chosen one, typed as Turkish day and time (`12.10.2026`, `08:00`; site time, end exclusive).
 */
export function PeriodChoice({
  id,
  custom,
  onCustomChange,
  defaultText,
  values,
  onChange,
  errors,
}: {
  id: string;
  custom: boolean;
  onCustomChange: (custom: boolean) => void;
  /** The schedule's period, e.g. "12.10.2026 – 18.10.2026". */
  defaultText: string;
  values: PeriodValues;
  onChange: (values: PeriodValues) => void;
  errors: FieldErrors<PeriodField>;
}) {
  const { t } = useTranslation();
  const field = (name: PeriodField, label: string, placeholder: string, hint?: string) => {
    const error = errors[name] ? t(errors[name]) : undefined;
    return (
      <Field id={`${id}-${name}`} label={label} hint={hint} error={error}>
        <Input
          id={`${id}-${name}`}
          autoComplete="off"
          inputMode="numeric"
          placeholder={placeholder}
          value={values[name]}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-${name}-error` : hint ? `${id}-${name}-hint` : undefined}
          onChange={(event) => {
            onChange({ ...values, [name]: event.target.value });
          }}
        />
      </Field>
    );
  };
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-13 font-medium text-foreground">
        {t('reports.period.title')}
      </legend>
      <Choice
        name={`${id}-period`}
        checked={!custom}
        onSelect={() => {
          onCustomChange(false);
        }}
        title={t('reports.period.own')}
        description={defaultText}
      />
      <Choice
        name={`${id}-period`}
        checked={custom}
        onSelect={() => {
          onCustomChange(true);
        }}
        title={t('reports.period.custom')}
        description={t('reports.period.customHint')}
      />
      {custom ? (
        <div className="grid gap-3 sm:grid-cols-2" data-testid="custom-period">
          {field(
            'fromDay',
            t('reports.period.fromDay'),
            t('dates.dayPlaceholder'),
            t('dates.dayHint'),
          )}
          {field('fromTime', t('reports.period.fromTime'), 'SS:dd', t('reports.period.timeHint'))}
          {field('toDay', t('reports.period.toDay'), t('dates.dayPlaceholder'))}
          {field('toTime', t('reports.period.toTime'), 'SS:dd', t('reports.period.toTimeHint'))}
        </div>
      ) : null}
    </fieldset>
  );
}
