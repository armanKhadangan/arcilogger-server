import type { TFunction } from 'i18next';
import { CircleCheck, Lock } from 'lucide-react';
import { useId } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { formatDateTime } from '@/lib/format';
import type { SecretMeta } from '@/lib/settings-types';

import { SECRET_KEPT, type SecretEdit } from './secret-edit';

/** Who changed a secret last (`web:<user>`, `cli`, `installer`) in words. */
function changedBy(by: string | null | undefined, t: TFunction) {
  if (!by) return null;
  if (by.startsWith('web:')) return t('admin.secret.byWeb', { user: by.slice(4) });
  if (by === 'cli') return t('admin.secret.byCli');
  if (by === 'installer') return t('admin.secret.byInstaller');
  return by;
}

/**
 * A write-only field for a stored credential (contract 2.17 "Field states"): the browser never
 * receives its value, so it is never pre-filled. Shown as "Kayıtlı ✓ · son değişiklik …" with
 * "Değiştir" (a new value, sent once with the change) and, where allowed, "Sil".
 */
export function SecretField({
  id,
  label,
  meta,
  edit,
  onChange,
  disabled = false,
  allowClear = false,
  error,
  hint,
}: {
  id: string;
  label: string;
  meta: SecretMeta | undefined;
  edit: SecretEdit;
  onChange: (edit: SecretEdit) => void;
  disabled?: boolean;
  allowClear?: boolean;
  error?: ReactNode;
  hint?: ReactNode;
}) {
  const { t } = useTranslation();
  const statusId = useId();
  const set = meta?.set ?? false;
  const by = changedBy(meta?.changed_by, t);
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className="flex flex-col gap-1.5" data-testid={`secret-${id}`}>
      {edit.editing ? (
        <Label htmlFor={id}>{label}</Label>
      ) : (
        <span id={`${id}-label`} className="text-13 font-medium text-foreground">
          {label}
        </span>
      )}
      {edit.editing ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={edit.value}
            disabled={disabled}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            className="max-w-sm"
            onChange={(event) => {
              onChange({ ...edit, value: event.target.value });
            }}
            autoFocus
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => {
              onChange(SECRET_KEPT);
            }}
          >
            {t('admin.secret.keep')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span id={statusId} className="flex items-center gap-1.5 text-13 text-foreground">
            {edit.clear ? (
              <span className="text-danger-ink">{t('admin.secret.willDelete')}</span>
            ) : set ? (
              <>
                <CircleCheck aria-hidden="true" className="size-4 text-success" />
                {t('admin.secret.stored')}
                {meta?.changed_at ? (
                  <span className="text-xs text-muted-strong">
                    {' · '}
                    {t('admin.secret.changedAt', { time: formatDateTime(meta.changed_at) })}
                    {by ? ` (${by})` : ''}
                  </span>
                ) : null}
              </>
            ) : (
              <>
                <Lock aria-hidden="true" className="size-4 text-muted" />
                {t('admin.secret.notStored')}
              </>
            )}
          </span>
          {edit.clear ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => {
                onChange(SECRET_KEPT);
              }}
            >
              {t('admin.secret.keep')}
            </Button>
          ) : (
            <>
              <Button
                variant="secondary"
                size="sm"
                disabled={disabled}
                aria-describedby={`${id}-label ${statusId}`}
                onClick={() => {
                  onChange({ value: '', editing: true, clear: false });
                }}
              >
                {set ? t('admin.secret.change') : t('admin.secret.enter')}
              </Button>
              {allowClear && set ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={disabled}
                  aria-describedby={`${id}-label`}
                  onClick={() => {
                    onChange({ value: '', editing: false, clear: true });
                  }}
                >
                  {t('admin.secret.delete')}
                </Button>
              ) : null}
            </>
          )}
        </div>
      )}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger-ink">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
