import { useTranslation } from 'react-i18next';

import { SwitchField, TextField } from '@/components/admin/form-fields';
import { Field } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { formatBytes, formatDuration } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { RuleParam, RuleParamValue } from '@/lib/rules-types';

import {
  isKnownKind,
  isListKind,
  MIB,
  paramField,
  paramLabel,
  ruleFieldId,
  shownValue,
} from './rules-model';

/** A typed number as a byte count (MiB), `null` while it is not one. */
function typedBytes(text: string): number | null {
  const value = text.trim().replace(',', '.');
  if (!/^\d+(\.\d{1,3})?$/.test(value)) return null;
  return Math.round(Number(value) * MIB);
}

function typedSeconds(text: string): number | null {
  const value = text.trim();
  return /^\d+$/.test(value) ? Number(value) : null;
}

/**
 * One parameter of a rule, typed by its kind (§2.3): a number (bytes in MiB, seconds, percent),
 * a list one entry per line (strings, CIDR networks, numbers) or a switch. Its default is shown
 * below; a kind this version has no input for is shown as it is and kept.
 */
export function RuleParamInput({
  param,
  value,
  stored,
  fallback,
  error,
  disabled,
  onChange,
}: {
  param: RuleParam;
  /** The text (or flag) in the form. */
  value: string | boolean | undefined;
  /** The stored value (shown for a kind without an input). */
  stored: RuleParamValue | undefined;
  /** The registry's default. */
  fallback: RuleParamValue | undefined;
  error?: string;
  disabled: boolean;
  onChange: (value: string | boolean) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = ruleFieldId(paramField(param.key));
  const label = paramLabel(param, lang);
  const empty = Array.isArray(fallback) && fallback.length === 0;
  const byDefault = empty
    ? t('rules.sheet.defaultEmpty')
    : t('rules.sheet.default', { value: shownValue(param, fallback, lang, t) });

  if (!isKnownKind(param.kind))
    return (
      <div className="flex flex-col gap-1" data-testid="rule-param" data-param={param.key}>
        <span className="text-13 font-medium text-foreground">{label}</span>
        <p className="text-xs text-muted">
          {t('rules.sheet.unknownParam', { value: JSON.stringify(stored ?? null) })}
        </p>
      </div>
    );

  if (param.kind === 'bool')
    return (
      <div data-testid="rule-param" data-param={param.key}>
        <SwitchField
          id={id}
          label={label}
          hint={byDefault}
          checked={value === true}
          disabled={disabled}
          onChange={onChange}
        />
      </div>
    );

  const text = typeof value === 'string' ? value : '';
  if (isListKind(param.kind)) {
    const kindHint =
      param.kind === 'networks'
        ? t('rules.sheet.networksHint')
        : param.kind === 'ints'
          ? t('rules.sheet.intsHint')
          : t('rules.sheet.listHint');
    const lines = Math.min(8, Math.max(3, text.split('\n').length + 1));
    return (
      <div data-testid="rule-param" data-param={param.key}>
        <Field id={id} label={label} hint={`${kindHint} ${byDefault}`} error={error}>
          <Textarea
            id={id}
            value={text}
            rows={lines}
            disabled={disabled}
            spellCheck={false}
            autoComplete="off"
            className="font-mono text-13"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : `${id}-hint`}
            onChange={(event) => {
              onChange(event.target.value);
            }}
          />
        </Field>
      </div>
    );
  }

  let kindHint = '';
  if (param.kind === 'bytes') {
    const bytes = typedBytes(text);
    kindHint = t('rules.sheet.bytesHint', {
      readable: bytes === null ? '—' : formatBytes(bytes, lang),
    });
  } else if (param.kind === 'seconds') {
    const seconds = typedSeconds(text);
    kindHint = t('rules.sheet.secondsHint', {
      duration: seconds === null ? '—' : formatDuration(seconds, lang),
    });
  } else if (param.kind === 'percent') {
    kindHint = t('rules.sheet.percentHint');
  }
  return (
    <div data-testid="rule-param" data-param={param.key}>
      <TextField
        id={id}
        label={param.kind === 'bytes' ? `${label} (MiB)` : label}
        value={text}
        hint={kindHint ? `${kindHint} ${byDefault}` : byDefault}
        error={error}
        disabled={disabled}
        inputMode={param.kind === 'bytes' ? 'decimal' : 'numeric'}
        inputClassName="w-40"
        autoComplete="off"
        onChange={onChange}
      />
    </div>
  );
}
