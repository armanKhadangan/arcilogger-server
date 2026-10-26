import { useCallback, useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SwitchField, TextField } from '@/components/admin/form-fields';
import { SectionActions } from '@/components/admin/section-actions';
import { SECRET_KEPT, secretChanged, type SecretEdit } from '@/components/admin/secret-edit';
import { SecretField } from '@/components/admin/secret-field';
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
import { Field, Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useSettings } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import type { SmtpBody, SmtpSection, SmtpSecurity } from '@/lib/settings-types';
import { codePointLength, focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { RecipientSelect, SmtpCaCard, TestEmailCard } from './email-parts';
import { SettingsGroupForm } from './settings-group-form';
import {
  LOCAL_HOSTS,
  integerError,
  parseInteger,
  validateEmail,
  validateHost,
} from './settings-validation';

const SECURITIES: readonly SmtpSecurity[] = ['starttls', 'ssl', 'none'];

interface SmtpForm {
  enabled: boolean;
  host: string;
  port: string;
  security: SmtpSecurity;
  username: string;
  from_address: string;
  timeout: string;
}

type SmtpField =
  'host' | 'port' | 'security' | 'username' | 'password' | 'from_address' | 'timeout' | 'test_to';

const EMPTY: SmtpForm = {
  enabled: false,
  host: '',
  port: '587',
  security: 'starttls',
  username: '',
  from_address: '',
  timeout: '30',
};

const PATHS: Record<SmtpField, string[]> = {
  host: ['host', 'smtp.host'],
  port: ['port', 'smtp.port'],
  security: ['security', 'smtp.security'],
  username: ['username', 'smtp.username'],
  password: ['password', 'smtp.password'],
  from_address: ['from_address', 'smtp.from_address'],
  timeout: ['timeout_seconds', 'smtp.timeout_seconds'],
  test_to: ['test_to'],
};
const BOUNDS: Partial<Record<SmtpField, Record<string, number>>> = {
  port: { min: 1, max: 65_535 },
  timeout: { min: 1, max: 300 },
};

function toForm(smtp: SmtpSection | undefined): SmtpForm | undefined {
  if (!smtp) return undefined;
  return {
    enabled: smtp.enabled,
    host: smtp.host ?? '',
    port: String(smtp.port),
    security: smtp.security,
    username: smtp.username ?? '',
    from_address: smtp.from_address ?? '',
    timeout: String(smtp.timeout_seconds),
  };
}

/** The server, port, security or user name differ (a stored password is not sent to a new one). */
function endpointChanged(form: SmtpForm, server: SmtpForm): boolean {
  return (
    form.host.trim() !== server.host ||
    form.port.trim() !== server.port ||
    form.security !== server.security ||
    form.username.trim() !== server.username
  );
}

/** The browser's checks of the SMTP section (contract 2.6 "smtp"); the server runs them again. */
function validate(
  form: SmtpForm,
  server: SmtpForm,
  stored: boolean,
  password: SecretEdit,
  testTo: string,
  needsTest: boolean,
): FieldErrors<SmtpField> {
  const errors: FieldErrors<SmtpField> = {};
  const host = validateHost(form.host, !form.enabled);
  if (host) errors.host = host;
  const port = integerError(form.port, 1, 65_535);
  if (port) errors.port = port;
  const timeout = integerError(form.timeout, 1, 300);
  if (timeout) errors.timeout = timeout;
  const from = validateEmail(form.from_address, !form.enabled);
  if (from) errors.from_address = from;
  const username = form.username.trim();
  if (codePointLength(username) > 256) errors.username = 'settings.email.errors.username';
  const typed = password.editing && password.value !== '';
  if (username) {
    if (password.clear || (!stored && !typed))
      errors.password = 'settings.email.errors.passwordRequired';
    else if (stored && !typed && endpointChanged(form, server))
      errors.password = 'settings.email.errors.rebind';
    if (form.security === 'none' && !LOCAL_HOSTS.includes(form.host.trim().toLowerCase()))
      errors.security = 'settings.email.errors.plaintextAuth';
  }
  if (password.editing && password.value === '') errors.password = 'validation.required';
  if (needsTest && !testTo) errors.test_to = 'settings.email.errors.testTo';
  return errors;
}

/**
 * Ayarlar → E-posta (contract 2.17, `settings.manage`): the SMTP server of the alert e-mails
 * (machine configuration, applied by the administration service; enabling it or changing the
 * server sends a trial message first), its CA, "Test e-postası gönder", and the `alerts.email_*`
 * database settings (recipients, minimum severity, hourly limit) on one page. The password is
 * write-only and never sent to another server than the one it was entered for.
 */
export function EmailPage() {
  const { t } = useTranslation();
  const id = useId();
  const { can, me } = useAuth();
  const section = useMachineSection('smtp');
  const { config, flow } = section;
  const smtp = config.data?.sections.smtp;
  const server = useMemo(() => toForm(smtp), [smtp]);
  const form = useServerValues(server, EMPTY);
  const [password, setPassword] = useState<SecretEdit>(SECRET_KEPT);
  const [testTo, setTestTo] = useState('');
  const [probe, setProbe] = useState(true);
  const [errors, setErrors] = useState<FieldErrors<SmtpField>>({});
  const [settingsDirty, setSettingsDirty] = useState(false);
  const settings = useSettings();
  const alertSettings = useMemo(
    () =>
      settings.data?.groups
        .find((group) => group.id === 'alerts')
        ?.settings.filter((setting) => setting.key.startsWith('alerts.email_')) ?? [],
    [settings.data],
  );
  const recipients = useMemo(() => {
    const listed = alertSettings.find((setting) => setting.key === 'alerts.email_recipients');
    const addresses = Array.isArray(listed?.value)
      ? listed.value.filter((item): item is string => typeof item === 'string')
      : [];
    return [...new Set([...addresses, ...(me.email ? [me.email] : [])])];
  }, [alertSettings, me.email]);
  const values = form.values;
  const dirty = form.dirty || secretChanged(password) || password.editing;
  const state = flowState(flow, {
    loading: config.isPending,
    canEdit: section.canEdit,
    agent: section.agent,
    dirty,
    invalid: hasErrors(errors),
    restartRequired: section.restartRequired,
  });
  const locked = !editable(state) || !section.writable;
  const base = server ?? EMPTY;
  const needsTest = values.enabled && (!base.enabled || endpointChanged(values, base));
  const stored = smtp?.secrets['smtp.password']?.set ?? false;

  function change(next: Partial<SmtpForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const found = validate(values, base, stored, password, testTo, needsTest);
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    const body: SmtpBody = {
      etag: section.etag,
      enabled: values.enabled,
      host: values.host.trim() || null,
      port: parseInteger(values.port) ?? 587,
      security: values.security,
      username: values.username.trim() || null,
      from_address: values.from_address.trim() || null,
      timeout_seconds: parseInteger(values.timeout) ?? 30,
      ...(password.editing && password.value !== '' ? { password: password.value } : {}),
      ...(password.clear ? { clear_password: true } : {}),
      ...(needsTest ? { test_to: testTo } : {}),
      probe,
    };
    void flow.start(body);
  }

  const message = (field: SmtpField) =>
    fieldMessage(t, flow.problems, errors[field], PATHS[field], BOUNDS[field]);
  const field = (name: Exclude<SmtpField, 'security' | 'password' | 'test_to'>) => ({
    id: `${id}-${name}`,
    value: values[name],
    error: message(name),
    disabled: locked,
    onChange: (value: string) => {
      change({ [name]: value });
    },
  });
  const onSettingsDirty = useCallback((value: boolean) => {
    setSettingsDirty(value);
  }, []);

  return (
    <SettingsFrame title={t('settings.email.title')} description={t('settings.email.description')}>
      <UnsavedChangesGuard when={(dirty && editable(state)) || settingsDirty} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : smtp ? (
        <>
          <SettingsCard
            title={t('settings.email.smtpTitle')}
            description={t('settings.email.smtpDescription')}
            testId="settings-smtp"
          >
            <SwitchField
              id={`${id}-enabled`}
              label={t('settings.email.enabled')}
              hint={t('settings.email.enabledHint')}
              checked={values.enabled}
              disabled={locked}
              onChange={(enabled) => {
                change({ enabled });
              }}
            />
            {base.enabled && !values.enabled ? (
              <Alert tone="warning" assertive={false}>
                {t('settings.email.disabling')}
              </Alert>
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              <TextField
                {...field('host')}
                label={t('settings.email.host')}
                hint={t('settings.email.hostHint')}
                mono
                spellCheck={false}
                required={values.enabled}
              />
              <TextField
                {...field('port')}
                label={t('settings.email.port')}
                inputMode="numeric"
                inputClassName="w-32"
              />
              <Field
                id={`${id}-security`}
                label={t('settings.email.security')}
                error={message('security')}
                hint={values.security === 'none' ? t('settings.email.plaintext') : undefined}
              >
                <Select
                  value={values.security}
                  disabled={locked}
                  onValueChange={(security) => {
                    change({ security: security as SmtpSecurity });
                  }}
                >
                  <SelectTrigger
                    id={`${id}-security`}
                    className="w-56"
                    aria-invalid={message('security') ? true : undefined}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SECURITIES.map((security) => (
                      <SelectItem key={security} value={security}>
                        {t(`settings.email.securities.${security}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <TextField
                {...field('timeout')}
                label={t('settings.email.timeout')}
                hint={t('settings.email.timeoutHint')}
                inputMode="numeric"
                inputClassName="w-32"
              />
              <TextField
                {...field('from_address')}
                label={t('settings.email.from')}
                hint={t('settings.email.fromHint')}
                autoComplete="off"
                spellCheck={false}
                required={values.enabled}
              />
              <TextField
                {...field('username')}
                label={t('settings.email.username')}
                hint={t('settings.email.usernameHint')}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <SecretField
              id={`${id}-password`}
              label={t('settings.email.password')}
              meta={smtp.secrets['smtp.password']}
              edit={password}
              allowClear
              disabled={locked}
              error={message('password')}
              hint={t('settings.email.passwordHint')}
              onChange={(next) => {
                setPassword(next);
                setErrors({});
                flow.edit();
              }}
            />
            {needsTest ? (
              <RecipientSelect
                id={`${id}-test_to`}
                label={t('settings.email.testTo')}
                hint={t('settings.email.testToHint')}
                value={testTo}
                options={recipients}
                disabled={locked}
                error={message('test_to')}
                onChange={(value) => {
                  setTestTo(value);
                  setErrors({});
                }}
              />
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
                {t('settings.email.probe')}
              </Label>
            </div>
          </SettingsCard>
          <SectionActions
            state={state}
            flow={flow}
            dirty={dirty}
            onSave={save}
            onRestore={() => {
              form.restore();
              setPassword(SECRET_KEPT);
              setErrors({});
            }}
            onReload={() => {
              void config.refetch().then((result) => {
                const fresh = toForm(result.data?.sections.smtp);
                if (fresh) form.setValues(fresh);
                setPassword(SECRET_KEPT);
                setErrors({});
              });
            }}
            onTerminal={(request) => {
              if (request.state === 'active' || request.state === 'partially_active')
                setPassword(SECRET_KEPT);
            }}
            probingText={t('settings.email.probing')}
          />
          <SmtpCaCard smtp={smtp} etag={section.etag} writable={section.writable} />
          {/* The worker sends it (contract 2.9), so it works while ArciAdmin is not running. */}
          <TestEmailCard
            recipients={recipients}
            writable={can('settings.manage') && smtp.enabled}
          />
        </>
      ) : null}
      {settings.isError ? (
        <Alert tone="danger">{errorMessage(settings.error)}</Alert>
      ) : alertSettings.length > 0 ? (
        <SettingsGroupForm
          title={t('settings.email.alertsTitle')}
          description={t('settings.email.alertsDescription')}
          settings={alertSettings}
          canEdit={can('settings.manage')}
          onDirty={onSettingsDirty}
          testId="settings-group-email"
        />
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
