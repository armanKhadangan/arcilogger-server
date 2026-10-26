import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SwitchField, TextField } from '@/components/admin/form-fields';
import { HeldChanges } from '@/components/admin/held-change';
import { SectionActions } from '@/components/admin/section-actions';
import {
  SettingsCard,
  SettingsFrame,
  UnsavedChangesGuard,
} from '@/components/admin/settings-frame';
import { useMachineSection } from '@/components/admin/use-machine-section';
import { flowState, useServerValues } from '@/components/admin/use-section-flow';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { dayText } from '@/lib/dates';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { RetentionPreview, RetentionSection } from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

const MAX_DAYS = 36_500;

interface RetentionForm {
  enabled: boolean;
  days: string;
}

const EMPTY: RetentionForm = { enabled: false, days: '730' };

function toForm(retention: RetentionSection | undefined): RetentionForm | undefined {
  if (!retention) return undefined;
  return { enabled: retention.enabled, days: String(retention.days) };
}

/** "X gün silinecek, en eski …, ilk silme … 02:00" (contract 2.17). */
export function PreviewText({ preview }: { preview: RetentionPreview }) {
  const { t } = useTranslation();
  const lang = currentLang();
  if (preview.days_to_delete === 0)
    return (
      <p className="text-13 text-foreground">
        {t('settings.retention.previewNone', { cutoff: dayText(preview.cutoff) })}
      </p>
    );
  return (
    <p className="text-13 text-foreground">
      {t('settings.retention.preview', {
        count: preview.days_to_delete,
        days: formatNumber(preview.days_to_delete, 0, lang),
        oldest: preview.oldest ? dayText(preview.oldest) : '—',
        newest: preview.newest ? dayText(preview.newest) : '—',
        cutoff: dayText(preview.cutoff),
      })}
      {preview.first_run_at
        ? ` ${t('settings.retention.firstRun', { time: formatDateTime(preview.first_run_at) })}`
        : ''}
    </p>
  );
}

/**
 * Ayarlar → Saklama (contract 2.17, `settings.manage`): whether sealed days older than the
 * retention period are deleted, and that period (at least 730 days; the server refuses less).
 * Switching it on or shortening it shows how many days the Retention task would delete and needs
 * that number typed; the change is held for `policy.hold_hours` and can be cancelled until then.
 * Raw logs inside the period are never touched.
 */
export function RetentionPage() {
  const { t } = useTranslation();
  const id = useId();
  const section = useMachineSection('retention');
  const { config, flow } = section;
  const retention = config.data?.sections.retention;
  const server = useMemo(() => toForm(retention), [retention]);
  const form = useServerValues(server, EMPTY);
  const [errors, setErrors] = useState<FieldErrors<'days'>>({});
  const [typed, setTyped] = useState('');
  const values = form.values;
  const state = flowState(flow, {
    loading: config.isPending,
    canEdit: section.canEdit,
    agent: section.agent,
    dirty: form.dirty,
    invalid: hasErrors(errors),
    restartRequired: section.restartRequired,
  });
  const locked = !editable(state) || !section.writable;
  const minimum = retention?.minimum_days ?? 730;
  const base = server ?? EMPTY;
  const days = parseInteger(values.days);
  // Enabling or shortening deletes days: the number to delete is typed (contract 2.6).
  const deletes =
    values.enabled && (!base.enabled || (days !== null && days < (parseInteger(base.days) ?? 0)));
  const preview = flow.check?.retention_preview ?? null;

  function change(next: Partial<RetentionForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const error = integerError(values.days, minimum, MAX_DAYS);
    const found: FieldErrors<'days'> = error ? { days: error } : {};
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    setTyped('');
    void flow.start({ etag: section.etag, enabled: values.enabled, days: days ?? minimum });
  }

  return (
    <SettingsFrame
      title={t('settings.retention.title')}
      description={t('settings.retention.description')}
    >
      <UnsavedChangesGuard when={form.dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : retention ? (
        <>
          <HeldChanges config={config.data} section="retention" except={flow.requestId} />
          <SettingsCard
            title={t('settings.retention.cardTitle')}
            description={t('settings.retention.cardDescription')}
            testId="settings-retention"
          >
            <div className="flex flex-col gap-1" data-testid="retention-current">
              <p className="text-13 font-medium text-foreground">
                {retention.enabled
                  ? t('settings.retention.currentOn', { count: retention.days })
                  : t('settings.retention.currentOff')}
              </p>
              {retention.enabled ? <PreviewText preview={retention.preview} /> : null}
            </div>
            <SwitchField
              id={`${id}-enabled`}
              label={t('settings.retention.enabled')}
              hint={t('settings.retention.enabledHint')}
              checked={values.enabled}
              disabled={locked}
              onChange={(enabled) => {
                change({ enabled });
              }}
            />
            <TextField
              id={`${id}-days`}
              label={t('settings.retention.days')}
              hint={t('settings.retention.daysHint', { min: minimum })}
              value={values.days}
              error={fieldMessage(t, flow.problems, errors.days, ['days', 'retention.days'], {
                min: minimum,
                max: MAX_DAYS,
              })}
              disabled={locked}
              inputMode="numeric"
              inputClassName="w-32"
              onChange={(text) => {
                change({ days: text });
              }}
            />
            {deletes ? (
              <Alert tone="warning" assertive={false} data-testid="retention-deletes">
                {t('settings.retention.deletes')}
              </Alert>
            ) : null}
          </SettingsCard>
          <SectionActions
            state={state}
            flow={flow}
            dirty={form.dirty}
            onSave={save}
            onRestore={() => {
              form.restore();
              setErrors({});
            }}
            onReload={() => {
              void config.refetch().then((result) => {
                const fresh = toForm(result.data?.sections.retention);
                if (fresh) form.setValues(fresh);
                setErrors({});
              });
            }}
          />
        </>
      ) : null}
      <ChangeReviewDialog
        open={flow.phase === 'confirm' || flow.phase === 'submitting'}
        onOpenChange={(open) => {
          if (!open) flow.dismiss();
        }}
        check={flow.check}
        busy={flow.phase === 'submitting'}
        danger={deletes}
        confirmDisabled={
          deletes && (preview === null || typed.trim() !== String(preview.days_to_delete))
        }
        onConfirm={() =>
          void flow.confirm(deletes ? { confirm_delete_days: Number(typed.trim()) } : undefined)
        }
      >
        {preview ? (
          <div className="flex flex-col gap-3" data-testid="retention-preview">
            <PreviewText preview={preview} />
            {deletes ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${id}-confirm`}>
                  {t('settings.retention.confirmType', { count: preview.days_to_delete })}
                </Label>
                <Input
                  id={`${id}-confirm`}
                  value={typed}
                  inputMode="numeric"
                  autoComplete="off"
                  className="w-32"
                  onChange={(event) => {
                    setTyped(event.target.value);
                  }}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </ChangeReviewDialog>
    </SettingsFrame>
  );
}
