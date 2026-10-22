import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { AuthLayout } from '@/components/auth/auth-layout';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Stepper } from '@/components/ui/stepper';
import { errorMessage, isApiError, passwordProblemText } from '@/lib/api';
import { useSetupAdmin, useSetupStatus } from '@/lib/api-hooks';
import {
  focusFirstError,
  hasErrors,
  normalizeUsername,
  PASSWORD_MIN_LENGTH,
  validateSetup,
  type FieldErrors,
} from '@/lib/validation';

type SetupField = 'token' | 'username' | 'displayName' | 'password' | 'confirm';

/**
 * The console command, as the operator types it in an elevated Windows PowerShell on the
 * server: the programs are not on PATH, so the full path of the default install folder
 * (docs/KURULUM.md section 9; the installer prints the same command with the real path).
 */
const SETUP_TOKEN_COMMAND =
  '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\arcilogger-api.exe" setup-token --config "C:\\ProgramData\\ARCILOGGER\\arcilogger.toml"';

/** Which form field a server refusal belongs to (`null`: shown above the form). */
function fieldOf(code: string): SetupField | null {
  switch (code) {
    case 'setup_token_invalid':
      return 'token';
    case 'username_taken':
    case 'username_invalid':
      return 'username';
    case 'password_rejected':
      return 'password';
    default:
      return null;
  }
}

/**
 * İlk kurulum (spec 12.3 page 17 — the administrator step; the other wizard steps follow in M6):
 * the one-time token printed by `arcilogger-api setup-token` on the server console, the first
 * administrator's account; TOTP enrolment follows at the first sign-in. Only while
 * `/auth/setup/status` says no administrator exists.
 */
export function SetupPage() {
  const { t } = useTranslation();
  const id = useId();
  const status = useSetupStatus();
  const create = useSetupAdmin();
  const [values, setValues] = useState<Record<SetupField, string>>({
    token: '',
    username: '',
    displayName: '',
    password: '',
    confirm: '',
  });
  const [errors, setErrors] = useState<FieldErrors<SetupField>>({});
  const [serverError, setServerError] = useState<unknown>(null);

  if (status.isPending) {
    return (
      <AuthLayout title={t('setup.title')}>
        <div className="flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
      </AuthLayout>
    );
  }
  // The account was just created: go to the sign-in page with the "created" notice. This must be
  // decided here, before the status check below — `markSetupDone()` makes the cached status say
  // "no setup needed" at once, and an imperative `navigate()` (a React Router transition) could
  // lose to that check's redirect to a bare /login on a slow PC (the notice was then missing).
  if (create.isSuccess) return <Navigate to="/login?setup=done" replace />;
  if (status.data && !status.data.needs_setup) return <Navigate to="/login" replace />;

  function set(field: SetupField, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateSetup(values);
    setErrors(found);
    setServerError(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    create.mutate(
      {
        token: values.token.trim(),
        username: normalizeUsername(values.username) ?? values.username,
        display_name: values.displayName.trim(),
        password: values.password,
      },
      {
        // The redirects are rendered (above), not navigated to from here: see `create.isSuccess`.
        onSuccess: () => {
          create.markSetupDone();
        },
        onError: (error) => {
          // Someone else finished the setup meanwhile: the status check sends us to /login.
          if (isApiError(error, 'setup_done')) {
            create.markSetupDone();
            return;
          }
          setServerError(error);
        },
      },
    );
  }

  const serverField = isApiError(serverError) ? fieldOf(serverError.code) : null;
  const serverText =
    serverError === null ? null : (passwordProblemText(serverError) ?? errorMessage(serverError));
  const message = (field: SetupField): string | undefined => {
    if (serverField === field && serverText) return serverText;
    const key = errors[field];
    return key ? t(key, { min: PASSWORD_MIN_LENGTH }) : undefined;
  };
  const describedBy = (field: SetupField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;

  const steps = [
    { id: 'admin', title: t('setup.stepAdmin') },
    { id: 'totp', title: t('setup.stepTotp'), description: t('setup.stepTotpHint') },
  ];

  return (
    <AuthLayout title={t('setup.title')} description={t('setup.description')} wide>
      <Stepper steps={steps} current={0} label={t('setup.progress')} />
      {status.isError ? <Alert tone="danger">{errorMessage(status.error)}</Alert> : null}
      {status.data && !status.data.token_active ? (
        <Alert tone="info" title={t('setup.noTokenTitle')} assertive={false}>
          {t('setup.noTokenBody')}
        </Alert>
      ) : null}
      <CodeBlock label={t('setup.commandLabel')} code={SETUP_TOKEN_COMMAND} wrap />
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {serverError !== null && serverField === null ? (
          <Alert tone="danger">{serverText}</Alert>
        ) : null}
        <Field
          id={`${id}-token`}
          label={t('setup.token')}
          hint={t('setup.tokenHint')}
          error={message('token')}
          required
        >
          <Input
            id={`${id}-token`}
            mono
            autoComplete="off"
            spellCheck={false}
            value={values.token}
            aria-invalid={message('token') ? true : undefined}
            aria-describedby={describedBy('token', true)}
            onChange={(event) => {
              set('token', event.target.value);
            }}
            autoFocus
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id={`${id}-username`}
            label={t('users.username')}
            hint={t('users.usernameHint')}
            error={message('username')}
            required
          >
            <Input
              id={`${id}-username`}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={values.username}
              aria-invalid={message('username') ? true : undefined}
              aria-describedby={describedBy('username', true)}
              onChange={(event) => {
                set('username', event.target.value);
              }}
            />
          </Field>
          <Field
            id={`${id}-displayName`}
            label={t('users.displayName')}
            error={message('displayName')}
            required
          >
            <Input
              id={`${id}-displayName`}
              autoComplete="name"
              value={values.displayName}
              aria-invalid={message('displayName') ? true : undefined}
              aria-describedby={describedBy('displayName')}
              onChange={(event) => {
                set('displayName', event.target.value);
              }}
            />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id={`${id}-password`}
            label={t('password.new')}
            hint={t('password.policy', { min: PASSWORD_MIN_LENGTH })}
            error={message('password')}
            required
          >
            <Input
              id={`${id}-password`}
              type="password"
              autoComplete="new-password"
              value={values.password}
              aria-invalid={message('password') ? true : undefined}
              aria-describedby={describedBy('password', true)}
              onChange={(event) => {
                set('password', event.target.value);
              }}
            />
          </Field>
          <Field
            id={`${id}-confirm`}
            label={t('password.confirm')}
            error={message('confirm')}
            required
          >
            <Input
              id={`${id}-confirm`}
              type="password"
              autoComplete="new-password"
              value={values.confirm}
              aria-invalid={message('confirm') ? true : undefined}
              aria-describedby={describedBy('confirm')}
              onChange={(event) => {
                set('confirm', event.target.value);
              }}
            />
          </Field>
        </div>
        <Button type="submit" size="lg" busy={create.isPending}>
          {create.isPending ? t('common.working') : t('setup.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
