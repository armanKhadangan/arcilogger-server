import { Copy } from 'lucide-react';
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
import { Button } from '@/components/ui/button';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useAlerts } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatBytes, formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { BackupSection } from '@/lib/settings-types';
import { useSystemBackups } from '@/lib/system-hooks';
import type { BackupMirrorState, BackupsInfo } from '@/lib/system-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

type BackupField = 'keep_daily' | 'keep_weekly' | 'keep_monthly' | 'min_free_gb';
type FormField = BackupField | 'mirror_target';

interface BackupForm extends Record<BackupField, string> {
  enabled: boolean;
  /** `\\\\host\\share[\\path]` on the replica's NAS; empty: no copy. */
  mirror_target: string;
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
const PATHS = (name: FormField) => [name, `backup.${name}`];
const SHOWN = [...FIELDS, 'mirror_target' as const].flatMap(PATHS);

const EMPTY: BackupForm = {
  enabled: true,
  keep_daily: '14',
  keep_weekly: '8',
  keep_monthly: '12',
  min_free_gb: '10',
  mirror_target: '',
};

/** A share as the API takes it (`\\\\host\\share`, also `//host/share`); the server checks more. */
function isShare(value: string): boolean {
  return /^(\\\\|\/\/)[^\\/]+[\\/][^\\/]+/.test(value);
}

function toForm(backup: BackupSection | undefined): BackupForm | undefined {
  if (!backup) return undefined;
  return {
    enabled: backup.enabled,
    keep_daily: String(backup.keep_daily),
    keep_weekly: String(backup.keep_weekly),
    keep_monthly: String(backup.keep_monthly),
    min_free_gb: String(backup.min_free_gb),
    mirror_target: backup.mirror_target ?? '',
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

/** The last copy to the NAS, in words (`state/backup/mirror.json`). */
function MirrorState({ info }: { info: BackupsInfo }) {
  const { t } = useTranslation();
  const mirror: BackupMirrorState | null = info.mirror;
  if (!info.mirror_target)
    return <p className="text-13 text-muted">{t('settings.backup.mirrorState.none')}</p>;
  if (!mirror)
    return <p className="text-13 text-muted">{t('settings.backup.mirrorState.never')}</p>;
  const time = mirror.finished ? formatDateTime(mirror.finished) : '—';
  const failed = Object.entries(mirror.failed);
  return (
    <div className="flex flex-col gap-1.5 text-13" data-testid="backup-mirror-state">
      {mirror.ok ? (
        <Alert tone="success" assertive={false}>
          {t('settings.backup.mirrorState.ok', {
            time,
            copied: mirror.copied,
            present: mirror.present,
          })}
        </Alert>
      ) : (
        <Alert
          tone="danger"
          assertive={false}
          title={t('settings.backup.mirrorState.failed', { time })}
        >
          {mirror.error ? (
            <p>{t('settings.backup.mirrorState.error', { error: mirror.error })}</p>
          ) : null}
          {failed.length > 0 ? (
            <ul className="list-disc pl-5">
              {failed.map(([name, why]) => (
                <li key={name}>{t('settings.backup.mirrorState.failedItem', { name, why })}</li>
              ))}
            </ul>
          ) : null}
        </Alert>
      )}
      {mirror.pruned > 0 ? (
        <p className="text-muted">
          {t('settings.backup.mirrorState.pruned', { count: mirror.pruned })}
        </p>
      ) : null}
      {mirror.prune_refused > 0 ? (
        <p className="text-warning-ink">
          {t('settings.backup.mirrorState.pruneRefused', { count: mirror.prune_refused })}
        </p>
      ) : null}
    </div>
  );
}

/** The dumps the backup folder keeps (`GET /system/backups`), with the folder's path. */
function BackupList() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const allowed = can('system.read');
  const backups = useSystemBackups(allowed);
  const [copied, setCopied] = useState(false);
  if (!allowed) return <p className="text-13 text-muted">{t('settings.backup.listHidden')}</p>;
  if (backups.isPending) return null;
  if (backups.isError) return <Alert tone="danger">{errorMessage(backups.error)}</Alert>;
  const info = backups.data;
  return (
    <div className="flex flex-col gap-3" data-testid="backup-list">
      <div className="flex flex-wrap items-center gap-2 text-13">
        <span className="text-muted">{t('settings.backup.folder')}</span>
        <code className="bg-surface-sunken min-w-0 rounded px-1.5 py-0.5 font-mono text-xs break-all">
          {info.folder}
        </code>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard.writeText(info.folder).then(() => {
              setCopied(true);
            });
          }}
        >
          <Copy aria-hidden="true" />
          {copied ? t('settings.backup.copied') : t('settings.backup.copyPath')}
        </Button>
      </div>
      {info.dumps.length === 0 ? (
        <p className="text-13 text-muted">{t('settings.backup.noDumps')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-13" data-testid="backup-dumps">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-strong">
                <th scope="col" className="py-1.5 pr-2 font-medium">
                  {t('settings.backup.name')}
                </th>
                <th scope="col" className="px-2 py-1.5 font-medium">
                  {t('settings.backup.taken')}
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  {t('settings.backup.size')}
                </th>
                <th scope="col" className="py-1.5 pl-2 font-medium">
                  {t('settings.backup.sha256')}
                </th>
              </tr>
            </thead>
            <tbody>
              {info.dumps.map((dump) => (
                <tr key={dump.name} className="border-b border-border last:border-b-0">
                  <th scope="row" className="py-1.5 pr-2 text-left font-normal">
                    <code className="font-mono text-xs">{dump.name}</code>
                  </th>
                  <td className="px-2 py-1.5 tabular">
                    {dump.taken ? formatDateTime(dump.taken) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular">
                    {formatBytes(dump.bytes, lang)}
                  </td>
                  <td className="py-1.5 pl-2">
                    {dump.sha256 ? (
                      <code className="font-mono text-xs" title={dump.sha256}>
                        {dump.sha256.slice(0, 16)}…
                      </code>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {info.list_updated ? (
        <p className="text-xs text-muted">
          {t('settings.backup.listUpdated', { time: formatDateTime(info.list_updated) })}
        </p>
      ) : null}
      <MirrorState info={info} />
      <p className="text-xs text-muted">{t('settings.backup.openNote')}</p>
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
 * (daily, weekly, monthly copies), the free space it needs on the backup disk and the second copy
 * on the replica's NAS (`mirror_target`, Wave 5 addendum C, C6), with the list of the dumps the
 * folder keeps and the copy's last result (`GET /system/backups`, `system.read`). The folder and
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
  const [errors, setErrors] = useState<FieldErrors<FormField>>({});
  const replica = config.data?.sections.replica;
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
    const found: FieldErrors<FormField> = {};
    for (const field of FIELDS) {
      const error = integerError(values[field], ...RANGES[field]);
      if (error) found[field] = error;
    }
    const mirror = values.mirror_target.trim();
    if (mirror && !isShare(mirror)) found.mirror_target = t('settings.backup.mirrorInvalid');
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
      mirror_target: mirror,
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
          <SettingsCard
            title={t('settings.backup.mirrorTitle')}
            description={t('settings.backup.mirrorDescription')}
            testId="settings-backup-mirror"
          >
            {replica?.enabled && replica.target ? (
              <p className="text-13 text-muted-strong">
                {t('settings.backup.mirrorReplica', { target: replica.target })}
              </p>
            ) : (
              <Alert tone="info" assertive={false}>
                {t('settings.backup.mirrorNoReplica')}{' '}
                <Link to="/settings/replica" className="text-primary-ink underline">
                  {t('settings.backup.mirrorReplicaLink')}
                </Link>
              </Alert>
            )}
            <TextField
              id={`${id}-mirror_target`}
              label={t('settings.backup.mirrorField')}
              hint={t('settings.backup.mirrorHint')}
              value={values.mirror_target}
              error={fieldMessage(t, flow.problems, errors.mirror_target, PATHS('mirror_target'))}
              disabled={locked}
              mono
              spellCheck={false}
              placeholder="\\\\NAS\\arcilogger-yedek"
              onChange={(value) => {
                change({ mirror_target: value });
              }}
            />
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
          <SettingsCard
            title={t('settings.backup.listTitle')}
            description={t('settings.backup.listDescription')}
            testId="settings-backup-list"
          >
            <BackupList />
          </SettingsCard>
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
