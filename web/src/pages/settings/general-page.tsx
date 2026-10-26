import { Clock } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { TextField } from '@/components/admin/form-fields';
import { LockedValue } from '@/components/admin/locked-field';
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
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useDashboardSummary } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { GeneralSection } from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { parseInteger, validateClock, validateSiteName } from './settings-validation';

/** The form of the section: numbers as typed. */
interface GeneralForm {
  site_name: string;
  ntp_server: string;
  fallback_server: string;
  alert_offset_ms: string;
}

type GeneralField = keyof GeneralForm;

const EMPTY: GeneralForm = {
  site_name: '',
  ntp_server: '',
  fallback_server: '',
  alert_offset_ms: '1000',
};
const PATHS: Record<GeneralField, string[]> = {
  site_name: ['site_name'],
  ntp_server: ['clock.ntp_server'],
  fallback_server: ['clock.fallback_server'],
  alert_offset_ms: ['clock.alert_offset_ms'],
};

function toForm(general: GeneralSection | undefined): GeneralForm | undefined {
  if (!general) return undefined;
  return {
    site_name: general.site_name,
    ntp_server: general.clock.ntp_server,
    fallback_server: general.clock.fallback_server,
    alert_offset_ms: String(general.clock.alert_offset_ms),
  };
}

/** "Son ölçülen sapma": the clock check of the Panel (`GET /dashboard/summary`). */
function MeasuredOffset() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const summary = useDashboardSummary(can('dashboard.view'));
  const clock = summary.data?.clock;
  if (!clock || clock.offset_ms === null)
    return <p className="text-13 text-muted">{t('settings.general.offsetUnknown')}</p>;
  return (
    <p className="flex items-center gap-2 text-13 text-foreground" data-testid="measured-offset">
      <Clock aria-hidden="true" className="size-4 text-muted-strong" />
      {t('settings.general.offset', {
        offset: formatNumber(clock.offset_ms, 1, lang),
        server: clock.ntp_server ?? '—',
        time: clock.checked_at ? formatDateTime(clock.checked_at) : '—',
      })}
    </p>
  );
}

/**
 * Ayarlar → Genel (contract 2.17, `settings.manage`): the organisation's name (reports, e-mails,
 * evidence packages) and the time sources with the clock alert threshold. The administration
 * service writes `arcilogger.toml`; the API and the worker take the name without a restart,
 * the system task updates the Windows Time service. A changed time server is tried first (unless
 * the box is cleared) and needs a password confirmation.
 */
export function GeneralPage() {
  const { t } = useTranslation();
  const id = useId();
  const section = useMachineSection('general');
  const { config, flow, status } = section;
  const server = useMemo(() => toForm(config.data?.sections.general), [config.data]);
  const form = useServerValues(server, EMPTY);
  const [errors, setErrors] = useState<FieldErrors<GeneralField>>({});
  const [probe, setProbe] = useState(true);
  const state = flowState(flow, {
    loading: config.isPending,
    canEdit: section.canEdit,
    agent: section.agent,
    dirty: form.dirty,
    invalid: hasErrors(errors),
    restartRequired: section.restartRequired,
  });
  const locked = !editable(state) || !section.writable;
  const values = form.values;
  const clockChanged =
    server !== undefined &&
    (values.ntp_server !== server.ntp_server ||
      values.fallback_server !== server.fallback_server ||
      values.alert_offset_ms !== server.alert_offset_ms);
  const systemTask = status.data?.agent.system_task;

  function change(next: Partial<GeneralForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const found: FieldErrors<GeneralField> = { ...validateClock(values) };
    const name = validateSiteName(values.site_name);
    if (name) found.site_name = name;
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    void flow.start({
      etag: section.etag,
      site_name: values.site_name.trim(),
      clock: {
        ntp_server: values.ntp_server.trim(),
        fallback_server: values.fallback_server.trim(),
        alert_offset_ms: parseInteger(values.alert_offset_ms) ?? 0,
      },
      probe,
    });
  }

  const message = (field: GeneralField) =>
    fieldMessage(t, flow.problems, errors[field], PATHS[field], { min: 1, max: 60_000 });
  const field = (name: GeneralField) => ({
    id: `${id}-${name}`,
    value: values[name],
    error: message(name),
    disabled: locked,
    onChange: (value: string) => {
      change({ [name]: value });
    },
  });
  const ntpPort = config.data?.locked.find((item) => item.path === 'clock.ntp_port');

  return (
    <SettingsFrame
      title={t('settings.general.title')}
      description={t('settings.general.description')}
    >
      <UnsavedChangesGuard when={form.dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : (
        <>
          <SettingsCard
            title={t('settings.general.siteTitle')}
            description={t('settings.general.siteDescription')}
            testId="settings-site-name"
          >
            <TextField
              {...field('site_name')}
              label={t('settings.general.siteName')}
              hint={t('settings.general.siteNameHint')}
              maxLength={400}
              autoComplete="organization"
              required
            />
          </SettingsCard>
          <SettingsCard
            title={t('settings.general.clockTitle')}
            description={t('settings.general.clockDescription')}
            testId="settings-clock"
          >
            <MeasuredOffset />
            <div className="grid gap-4 md:grid-cols-2">
              <TextField
                {...field('ntp_server')}
                label={t('settings.general.ntpServer')}
                hint={t('settings.general.ntpServerHint')}
                mono
                spellCheck={false}
              />
              <TextField
                {...field('fallback_server')}
                label={t('settings.general.fallbackServer')}
                hint={t('settings.general.fallbackServerHint')}
                mono
                spellCheck={false}
              />
              <TextField
                {...field('alert_offset_ms')}
                label={t('settings.general.alertOffset')}
                hint={t('settings.general.alertOffsetHint')}
                inputMode="numeric"
                inputClassName="w-40"
              />
              {ntpPort ? (
                // The API names the locked key, not its value (it may differ from 123).
                <LockedValue
                  label={t('settings.general.ntpPort')}
                  value={`${ntpPort.path} (${t(`admin.locked.reasons.${ntpPort.reason}`)})`}
                />
              ) : null}
            </div>
            {!values.ntp_server.trim() && values.fallback_server.trim() ? (
              <Alert tone="warning" assertive={false}>
                {t('settings.general.ntpEmpty')}
              </Alert>
            ) : null}
            {clockChanged && systemTask && !systemTask.ok ? (
              <Alert tone="warning" assertive={false} data-testid="system-task-warning">
                {t('settings.general.systemTask')}
              </Alert>
            ) : null}
            <div className="flex items-center gap-2">
              <Checkbox
                id={`${id}-probe`}
                checked={probe}
                disabled={locked}
                onCheckedChange={(checked) => {
                  setProbe(checked === true);
                }}
              />
              <Label htmlFor={`${id}-probe`} className="font-normal">
                {t('settings.general.probe')}
              </Label>
            </div>
            {clockChanged ? (
              <p className="text-xs text-muted-strong">{t('settings.general.clockReauth')}</p>
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
              // "Sayfayı yenile": the values saved meanwhile replace what was typed.
              void config.refetch().then((result) => {
                const fresh = toForm(result.data?.sections.general);
                if (fresh) form.setValues(fresh);
                setErrors({});
              });
            }}
            probingText={t('settings.general.probing')}
          />
        </>
      )}
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
