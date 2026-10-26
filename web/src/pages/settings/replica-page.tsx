import { PlugZap } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SwitchField, TextField } from '@/components/admin/form-fields';
import { HeldChanges } from '@/components/admin/held-change';
import { RequestProgress } from '@/components/admin/request-progress';
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
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { editable, localized } from '@/lib/admin-requests';
import { errorMessage, isApiError } from '@/lib/api';
import { useReplicaTest } from '@/lib/api-hooks';
import type {
  AdminRequestOut,
  ReplicaBody,
  ReplicaSection,
  ReplicaTestResult,
} from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { validateReplicaUser, validateUnc } from './settings-validation';

interface ReplicaForm {
  enabled: boolean;
  target: string;
  user: string;
}

type ReplicaField = 'target' | 'user' | 'password';

const EMPTY: ReplicaForm = { enabled: false, target: '', user: '' };
const PATHS: Record<ReplicaField, string[]> = {
  target: ['target', 'replica.target'],
  user: ['user', 'replica.user'],
  password: ['password', 'replica.password'],
};

function toForm(replica: ReplicaSection | undefined): ReplicaForm | undefined {
  if (!replica) return undefined;
  return { enabled: replica.enabled, target: replica.target ?? '', user: replica.user ?? '' };
}

/** The host of a `\\host\share` target (lower case), `''` when there is none. */
function hostOf(target: string): string {
  return target.trim().slice(2).split('\\')[0]?.toLowerCase() ?? '';
}

function validate(
  form: ReplicaForm,
  server: ReplicaForm,
  stored: boolean,
  password: SecretEdit,
): FieldErrors<ReplicaField> {
  const errors: FieldErrors<ReplicaField> = {};
  if (form.enabled || form.target.trim()) {
    const target = validateUnc(form.target);
    if (target) errors.target = target;
  }
  const typed = password.editing && password.value !== '';
  if (form.user.trim()) {
    const user = validateReplicaUser(form.user);
    if (user) errors.user = user;
    else if (password.clear || (!stored && !typed))
      errors.password = 'settings.replica.errors.passwordRequired';
    else if (
      stored &&
      !typed &&
      (hostOf(form.target) !== hostOf(server.target) || form.user.trim() !== server.user)
    )
      errors.password = 'settings.replica.errors.rebind';
  }
  if (password.editing && password.value === '') errors.password = 'validation.required';
  return errors;
}

function TestOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = request.result as
    (ReplicaTestResult & Partial<Record<'message_tr' | 'message_en', string>>) | null;
  if (!result) return null;
  // Contract 2.9 names `{ok, message_tr, message_en}`, 2.4.9 `{ok, message}`: either is shown.
  const text =
    result.message_tr !== undefined && result.message_en !== undefined
      ? localized({ message_tr: result.message_tr, message_en: result.message_en })
      : result.message;
  return (
    <Alert
      tone={result.ok ? 'success' : 'danger'}
      assertive={!result.ok}
      title={result.ok ? t('settings.replica.test.ok') : t('settings.replica.test.failed')}
      data-testid="replica-test-result"
    >
      {text}
    </Alert>
  );
}

/** "Bağlantıyı test et": the saved target and user, through the Requests task (1 a minute). */
function ReplicaTest({ writable }: { writable: boolean }) {
  const { t } = useTranslation();
  const test = useReplicaTest();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function run() {
    setProblem(null);
    try {
      const accepted = await test.mutateAsync();
      setRequestId(accepted.request_id);
    } catch (error) {
      const seconds = isApiError(error) ? error.retryAfter : null;
      setProblem(
        seconds
          ? `${errorMessage(error)} ${t('sealing.credits.retryAfter', { seconds })}`
          : errorMessage(error),
      );
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button
          variant="secondary"
          disabled={!writable}
          busy={test.isPending}
          onClick={() => void run()}
        >
          <PlugZap aria-hidden="true" />
          {t('settings.replica.test.button')}
        </Button>
      </div>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          renderResult={(request) => <TestOutcome request={request} />}
        />
      ) : null}
    </div>
  );
}

/**
 * Ayarlar → Replika (contract 2.17, `settings.manage`): the SMB share the sealed days are copied
 * to, its user and write-only password. Saving tries the new values first (unless the box is
 * cleared); switching the replica off or moving it while it is on is held for
 * `policy.hold_hours` and can be cancelled until then. Every change needs a password
 * confirmation; a changed server or user needs the share's password again.
 */
export function ReplicaPage() {
  const { t } = useTranslation();
  const id = useId();
  const section = useMachineSection('replica');
  const { config, flow } = section;
  const replica = config.data?.sections.replica;
  const server = useMemo(() => toForm(replica), [replica]);
  const form = useServerValues(server, EMPTY);
  const [password, setPassword] = useState<SecretEdit>(SECRET_KEPT);
  const [probe, setProbe] = useState(true);
  const [errors, setErrors] = useState<FieldErrors<ReplicaField>>({});
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
  const stored = replica?.secrets['replica.password']?.set ?? false;
  const holdHours = config.data?.policy.hold_hours ?? 0;
  const held =
    holdHours > 0 && base.enabled && (!values.enabled || values.target.trim() !== base.target);

  function change(next: Partial<ReplicaForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const found = validate(values, base, stored, password);
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    const body: ReplicaBody = {
      etag: section.etag,
      enabled: values.enabled,
      target: values.target.trim() || null,
      user: values.user.trim() || null,
      ...(password.editing && password.value !== '' ? { password: password.value } : {}),
      ...(password.clear ? { clear_password: true } : {}),
      probe,
    };
    void flow.start(body);
  }

  const message = (field: ReplicaField) =>
    fieldMessage(t, flow.problems, errors[field], PATHS[field]);

  return (
    <SettingsFrame
      title={t('settings.replica.title')}
      description={t('settings.replica.description')}
    >
      <UnsavedChangesGuard when={dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : replica ? (
        <>
          <HeldChanges config={config.data} section="replica" except={flow.requestId} />
          <SettingsCard
            title={t('settings.replica.cardTitle')}
            description={t('settings.replica.cardDescription')}
            testId="settings-replica"
          >
            <SwitchField
              id={`${id}-enabled`}
              label={t('settings.replica.enabled')}
              hint={t('settings.replica.enabledHint')}
              checked={values.enabled}
              disabled={locked}
              onChange={(enabled) => {
                change({ enabled });
              }}
            />
            {base.enabled && !values.enabled ? (
              <Alert tone="warning" assertive={false}>
                {t('settings.replica.disabling')}
              </Alert>
            ) : null}
            <TextField
              id={`${id}-target`}
              label={t('settings.replica.target')}
              hint={t('settings.replica.targetHint')}
              value={values.target}
              error={message('target')}
              disabled={locked}
              mono
              spellCheck={false}
              autoComplete="off"
              required={values.enabled}
              onChange={(target) => {
                change({ target });
              }}
            />
            <TextField
              id={`${id}-user`}
              label={t('settings.replica.user')}
              hint={t('settings.replica.userHint')}
              value={values.user}
              error={message('user')}
              disabled={locked}
              mono
              spellCheck={false}
              autoComplete="off"
              onChange={(user) => {
                change({ user });
              }}
            />
            <SecretField
              id={`${id}-password`}
              label={t('settings.replica.password')}
              meta={replica.secrets['replica.password']}
              edit={password}
              allowClear
              disabled={locked}
              error={message('password')}
              onChange={(next) => {
                setPassword(next);
                setErrors({});
                flow.edit();
              }}
            />
            {held ? (
              <Alert tone="warning" assertive={false} data-testid="replica-held">
                {t('settings.replica.held', { count: holdHours })}
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
                {t('settings.replica.probe')}
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
                const fresh = toForm(result.data?.sections.replica);
                if (fresh) form.setValues(fresh);
                setPassword(SECRET_KEPT);
                setErrors({});
              });
            }}
            onTerminal={(request) => {
              if (request.state === 'active' || request.state === 'partially_active')
                setPassword(SECRET_KEPT);
            }}
            probingText={t('settings.replica.probing')}
          />
          <SettingsCard
            title={t('settings.replica.test.title')}
            description={t('settings.replica.test.description')}
            testId="settings-replica-test"
          >
            <ReplicaTest writable={section.writable && replica.enabled} />
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
