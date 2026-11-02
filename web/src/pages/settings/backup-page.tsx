import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SwitchField, TextField } from '@/components/admin/form-fields';
import { LockedList } from '@/components/admin/locked-field';
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
import { SeverityBadge } from '@/components/ui/badge';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useAlerts } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { BackupSection } from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

type BackupField = 'keep_daily' | 'keep_weekly' | 'keep_monthly' | 'min_free_gb';

interface BackupForm extends Record<BackupField, string> {
  enabled: boolean;
}

/** `BackupConfig` ranges (contract 2.6 "backup"). */
const RANGES: Record<BackupField, [number, number]> = {
  keep_daily: [1, 3660],
  keep_weekly: [0, 520],
  keep_monthly: [0, 1200],
  min_free_gb: [0, 100_000],
};
const FIELDS: readonly BackupField[] = ['keep_daily', 'keep_weekly', 'keep_monthly', 'min_free_gb'];
/** The paths the API may name for a field (422 `details`). */
const PATHS = (name: BackupField) => [name, `backup.${name}`];
const SHOWN = FIELDS.flatMap(PATHS);

const EMPTY: BackupForm = {
  enabled: true,
  keep_daily: '14',
  keep_weekly: '8',
  keep_monthly: '12',
  min_free_gb: '10',
};

function toForm(backup: BackupSection | undefined): BackupForm | undefined {
  if (!backup) return undefined;
  return {
    enabled: backup.enabled,
    keep_daily: String(backup.keep_daily),
    keep_weekly: String(backup.keep_weekly),
    keep_monthly: String(backup.keep_monthly),
    min_free_gb: String(backup.min_free_gb),
  };
}

/**
 * The last backup as the system reports it: the open `R20-backup` alerts (a failed or refused
 * run). The file `state\backup\last.json` is not served by the API yet.
 */
function BackupAlerts() {
  const { t } = useTranslation();
  const lang = currentLang();
  const alerts = useAlerts({ rule: 'R20-backup', state: 'open', limit: 5 });
  const items = alerts.data?.items ?? [];
  return (
    <div className="flex flex-col gap-2" data-testid="backup-state">
      <p className="text-13 text-muted-strong">{t('settings.backup.stateNote')}</p>
      {alerts.isPending ? null : alerts.isError ? (
        <Alert tone="danger">{errorMessage(alerts.error)}</Alert>
      ) : items.length === 0 ? (
        <Alert tone="success" assertive={false}>
          {t('settings.backup.noAlerts')}
        </Alert>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((alert) => (
            <li key={alert.id} className="flex flex-wrap items-center gap-2 text-13">
              <SeverityBadge severity={alert.severity} />
              <span className="text-muted-strong tabular">{formatDateTime(alert.time)}</span>
              <span>{lang === 'en' ? alert.summary_en : alert.summary_tr}</span>
            </li>
          ))}
        </ul>
      )}
      <Link to="/security" className="text-13 text-primary-ink underline">
        {t('settings.backup.alertsLink')}
      </Link>
    </div>
  );
}

function BackupState() {
  const { t } = useTranslation();
  const { can } = useAuth();
  if (!can('alerts.read'))
    return <p className="text-13 text-muted">{t('settings.backup.stateHidden')}</p>;
  return <BackupAlerts />;
}

/**
 * Ayarlar → Yedekleme (contract 2.17, `settings.manage`): the database backup task's retention
 * (daily, weekly, monthly copies) and the free space it needs on the backup disk. The folder and
 * the tools stay on the server console. Switching the backup off warns and needs a password
 * confirmation.
 */
export function BackupPage() {
  const { t } = useTranslation();
  const id = useId();
  const section = useMachineSection('backup', { fields: SHOWN });
  const { config, flow } = section;
  const backup = config.data?.sections.backup;
  const server = useMemo(() => toForm(backup), [backup]);
  const form = useServerValues(server, EMPTY);
  const [errors, setErrors] = useState<FieldErrors<BackupField>>({});
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

  function change(next: Partial<BackupForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const found: FieldErrors<BackupField> = {};
    for (const field of FIELDS) {
      const error = integerError(values[field], ...RANGES[field]);
      if (error) found[field] = error;
    }
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    void flow.start({
      etag: section.etag,
      enabled: values.enabled,
      keep_daily: parseInteger(values.keep_daily) ?? 14,
      keep_weekly: parseInteger(values.keep_weekly) ?? 8,
      keep_monthly: parseInteger(values.keep_monthly) ?? 12,
      min_free_gb: parseInteger(values.min_free_gb) ?? 10,
    });
  }

  const field = (name: BackupField) => ({
    id: `${id}-${name}`,
    value: values[name],
    error: fieldMessage(t, flow.problems, errors[name], PATHS(name), {
      min: RANGES[name][0],
      max: RANGES[name][1],
    }),
    disabled: locked,
    inputMode: 'numeric' as const,
    inputClassName: 'w-32',
    label: t(`settings.backup.fields.${name}`),
    hint: t(`settings.backup.hints.${name}`, { min: RANGES[name][0], max: RANGES[name][1] }),
    onChange: (value: string) => {
      change({ [name]: value });
    },
  });
  const lockedPaths = config.data?.locked.filter((item) => item.path.startsWith('backup.')) ?? [];

  return (
    <SettingsFrame
      title={t('settings.backup.title')}
      description={t('settings.backup.description')}
    >
      <UnsavedChangesGuard when={form.dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : backup ? (
        <>
          <SettingsCard
            title={t('settings.backup.cardTitle')}
            description={t('settings.backup.cardDescription')}
            testId="settings-backup"
          >
            <SwitchField
              id={`${id}-enabled`}
              label={t('settings.backup.enabled')}
              hint={t('settings.backup.enabledHint')}
              checked={values.enabled}
              disabled={locked}
              onChange={(enabled) => {
                change({ enabled });
              }}
            />
            {backup.enabled && !values.enabled ? (
              <Alert tone="warning" assertive={false}>
                {t('settings.backup.disabling')}
              </Alert>
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              {FIELDS.map((name) => (
                <TextField key={name} {...field(name)} />
              ))}
            </div>
            <LockedList paths={lockedPaths} />
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
                const fresh = toForm(result.data?.sections.backup);
                if (fresh) form.setValues(fresh);
                setErrors({});
              });
            }}
          />
          <SettingsCard title={t('settings.backup.stateTitle')} testId="settings-backup-state">
            <BackupState />
          </SettingsCard>
        </>
      ) : null}
      <ChangeReviewDialog
        open={flow.phase === 'confirm' || flow.phase === 'submitting'}
        onOpenChange={(open) => {
          if (!open) flow.dismiss();
        }}
        check={flow.check}
        busy={flow.phase === 'submitting'}
        onConfirm={() => void flow.confirm()}
      />
    </SettingsFrame>
  );
}
