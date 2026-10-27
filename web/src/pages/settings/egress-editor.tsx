import { Plus, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { EgressMode, PortPreservation } from '@/lib/lookup-types';

import {
  EGRESS_MODES,
  egressWarnings,
  emptyRow,
  PORT_PRESERVATION,
  refusalText,
  showsField,
  valueFromRows,
  type EgressField,
  type EgressRefusal,
  type EgressRow,
  type RowWarning,
} from './egress-model';

/** A part of an id made of the characters an id may hold. */
function idPart(text: string): string {
  return text.replace(/[^A-Za-z0-9-]/g, '-');
}

function FieldMessages({ id, hint, error }: { id: string; hint?: string; error?: string }) {
  return (
    <>
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger-ink">
          {error}
        </p>
      ) : null}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </>
  );
}

function describedBy(id: string, hint: boolean, error: boolean): string | undefined {
  const ids = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean);
  return ids.length > 0 ? ids.join(' ') : undefined;
}

/** A warning tone per editor warning: the catch-all is only information. */
const WARNING_TONE: Record<RowWarning, 'info' | 'warning'> = {
  noPublic: 'warning',
  dynamic: 'warning',
  catchAll: 'info',
  publicNat: 'warning',
  converted: 'warning',
};

function ExitRow({
  row,
  baseId,
  number,
  warnings,
  refusal,
  disabled,
  onChange,
  onRemove,
}: {
  row: EgressRow;
  baseId: string;
  number: number;
  warnings: readonly RowWarning[];
  refusal: EgressRefusal | undefined;
  disabled: boolean;
  onChange: (row: EgressRow) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const id = `${baseId}-${idPart(row.key)}`;
  const set = (fields: Partial<EgressRow>) => {
    onChange({ ...row, ...fields });
  };
  const errorOf = (field: EgressField | 'name') =>
    refusal && refusal.field === field ? refusalText(refusal, t) : undefined;
  const rowError = refusal && refusal.field === undefined ? refusalText(refusal, t) : undefined;
  const shown = (field: EgressField) => showsField(row.mode, field);
  const title = row.name.trim() || t('network.egress.unnamed');

  function list(field: 'public' | 'transips' | 'interfaces', hint: string): ReactNode {
    const fieldId = `${id}-${field}`;
    const error = errorOf(field);
    return (
      <div className="flex flex-col gap-1">
        <Label htmlFor={fieldId}>{t(`network.egress.${field}`)}</Label>
        <Textarea
          id={fieldId}
          rows={2}
          className="font-mono text-13"
          spellCheck={false}
          value={row[field]}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(fieldId, true, Boolean(error))}
          onChange={(event) => {
            set({ [field]: event.target.value });
          }}
        />
        <FieldMessages id={fieldId} hint={hint} error={error} />
      </div>
    );
  }

  return (
    <fieldset
      className="flex flex-col gap-3 rounded-lg border border-border p-3"
      data-testid={`egress-${row.key}`}
    >
      <legend className="px-1 text-13 font-medium text-foreground">
        {t('network.egress.exit', { name: title, number })}
      </legend>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-name`}>{t('network.egress.name')}</Label>
          <Input
            id={`${id}-name`}
            mono
            spellCheck={false}
            autoComplete="off"
            value={row.name}
            disabled={disabled}
            aria-invalid={errorOf('name') ? true : undefined}
            aria-describedby={describedBy(`${id}-name`, true, Boolean(errorOf('name')))}
            onChange={(event) => {
              set({ name: event.target.value });
            }}
          />
          <FieldMessages
            id={`${id}-name`}
            hint={t('network.egress.nameHint')}
            error={errorOf('name')}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-label`}>{t('network.egress.label')}</Label>
          <Input
            id={`${id}-label`}
            value={row.label}
            disabled={disabled}
            aria-invalid={errorOf('label') ? true : undefined}
            aria-describedby={describedBy(`${id}-label`, false, Boolean(errorOf('label')))}
            onChange={(event) => {
              set({ label: event.target.value });
            }}
          />
          <FieldMessages id={`${id}-label`} error={errorOf('label')} />
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <Label id={`${id}-mode-label`} htmlFor={`${id}-mode`}>
          {t('network.egress.mode')}
        </Label>
        <Select
          value={row.mode}
          disabled={disabled}
          onValueChange={(mode) => {
            set({ mode: mode as EgressMode });
          }}
        >
          <SelectTrigger
            id={`${id}-mode`}
            className="w-full max-w-xl"
            aria-labelledby={`${id}-mode-label`}
            aria-invalid={errorOf('mode') ? true : undefined}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EGRESS_MODES.map((mode) => (
              <SelectItem key={mode} value={mode}>
                {t(`network.egress.modes.${mode}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <FieldMessages id={`${id}-mode`} error={errorOf('mode')} />
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {shown('public') ? list('public', t('network.egress.publicHint')) : null}
        {shown('transips') ? list('transips', t('network.egress.transipsHint')) : null}
        {shown('interfaces') ? list('interfaces', t('network.egress.interfacesHint')) : null}
      </div>
      {shown('port_preservation') || shown('public_dynamic') ? (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          {shown('port_preservation') ? (
            <div className="flex flex-col gap-1">
              <Label id={`${id}-preservation-label`} htmlFor={`${id}-preservation`}>
                {t('network.egress.portPreservation')}
              </Label>
              <Select
                value={row.port_preservation}
                disabled={disabled}
                onValueChange={(value) => {
                  set({ port_preservation: value as PortPreservation });
                }}
              >
                <SelectTrigger
                  id={`${id}-preservation`}
                  className="w-64"
                  aria-labelledby={`${id}-preservation-label`}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PORT_PRESERVATION.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`network.egress.preservation.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldMessages id={`${id}-preservation`} error={errorOf('port_preservation')} />
            </div>
          ) : null}
          {shown('public_dynamic') ? (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 py-2">
                <Checkbox
                  id={`${id}-dynamic`}
                  checked={row.public_dynamic}
                  disabled={disabled}
                  onCheckedChange={(checked) => {
                    set({ public_dynamic: checked === true });
                  }}
                />
                <Label htmlFor={`${id}-dynamic`} className="font-normal">
                  {t('network.egress.dynamic')}
                </Label>
              </div>
              <FieldMessages id={`${id}-dynamic`} error={errorOf('public_dynamic')} />
            </div>
          ) : null}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {warnings.map((warning) => (
            <li key={warning} data-warning={warning}>
              <Alert tone={WARNING_TONE[warning]} assertive={false}>
                {t(`network.egress.warnings.${warning}`)}
              </Alert>
            </li>
          ))}
        </ul>
      ) : null}
      {rowError ? <p className="text-xs text-danger-ink">{rowError}</p> : null}
      <div>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          aria-label={t('network.egress.remove', { name: title })}
          onClick={onRemove}
        >
          <X aria-hidden="true" />
          {t('network.egress.removeShort')}
        </Button>
      </div>
    </fieldset>
  );
}

/**
 * The editor of `nat.egresses` (contract 2.13): one row per internet exit with the fields its
 * mode takes, the server's rules checked on the field (`egress-model.ts`) and the warnings of
 * set-ups that work but answer worse. A refusal of the server (no field path) is shown by the
 * setting's field, at the editor level.
 */
export function EgressEditor({
  id,
  draft,
  onChange,
  disabled,
  invalid,
  describedBy: described,
  labelId,
}: {
  id: string;
  draft: EgressRow[];
  onChange: (draft: EgressRow[]) => void;
  disabled: boolean;
  invalid: boolean;
  describedBy: string;
  labelId: string;
}) {
  const { t } = useTranslation();
  const warnings = egressWarnings(draft);
  const refusal = valueFromRows(draft).refusal;
  const outer = refusal && refusal.row === undefined ? refusalText(refusal, t) : null;
  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-labelledby={labelId}
      aria-describedby={described}
      aria-invalid={invalid ? true : undefined}
      id={id}
      tabIndex={-1}
      data-testid="egress-editor"
    >
      {warnings.editor.map((warning) => (
        <Alert key={warning} tone="warning" assertive={false} data-warning={warning}>
          {t(`network.egress.warnings.${warning}`)}
        </Alert>
      ))}
      {draft.length === 0 ? (
        <p className="text-13 text-muted">{t('network.egress.empty')}</p>
      ) : null}
      {draft.map((row, index) => (
        <ExitRow
          key={row.key}
          row={row}
          baseId={id}
          number={index + 1}
          warnings={warnings.rows[row.key] ?? []}
          refusal={refusal?.row === row.key ? refusal : undefined}
          disabled={disabled}
          onChange={(next) => {
            onChange(draft.map((item) => (item.key === row.key ? next : item)));
          }}
          onRemove={() => {
            onChange(draft.filter((item) => item.key !== row.key));
          }}
        />
      ))}
      {outer ? <p className="text-xs text-danger-ink">{outer}</p> : null}
      <div>
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => {
            onChange([...draft, emptyRow(draft)]);
          }}
        >
          <Plus aria-hidden="true" />
          {t('network.egress.add')}
        </Button>
      </div>
    </div>
  );
}
