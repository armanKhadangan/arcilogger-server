import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { errorMessage, isApiError, passwordProblemText, type ApiError } from '@/lib/api';
import { useChangePassword } from '@/lib/api-hooks';
import {
  focusFirstError,
  hasErrors,
  PASSWORD_MIN_LENGTH,
  validateNewPassword,
  type FieldErrors,
} from '@/lib/validation';

interface PasswordChangeFormProps {
  /** The session ended (401): the sign-in page starts again. */
  onSessionLost: (error: ApiError) => void;
}

/** "Parolanızı değiştirin": current password, new password twice (`POST /auth/password`). */
export function PasswordChangeForm({ onSessionLost }: PasswordChangeFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const change = useChangePassword();
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'current' | 'password' | 'confirm'>>({});
  const [serverError, setServerError] = useState<unknown>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateNewPassword({ current, password, confirm });
    setErrors(found);
    setServerError(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field === 'password' ? 'new' : field}`);
      return;
    }
    change.mutate(
      { current_password: current, new_password: password },
      {
        onError: (error) => {
          if (isApiError(error) && error.status === 401) {
            onSessionLost(error);
            return;
          }
          if (isApiError(error, 'credentials_invalid')) setCurrent('');
          setServerError(error);
        },
      },
    );
  }

  const policyText = passwordProblemText(serverError);
  const currentError = isApiError(serverError, 'credentials_invalid')
    ? serverError.localized
    : errors.current && t(errors.current);
  const passwordError =
    policyText ?? (errors.password && t(errors.password, { min: PASSWORD_MIN_LENGTH }));
  const general =
    serverError && policyText === null && !isApiError(serverError, 'credentials_invalid')
      ? errorMessage(serverError)
      : null;

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      {general ? <Alert tone="danger">{general}</Alert> : null}
      <Field id={`${id}-current`} label={t('password.current')} error={currentError} required>
        <Input
          id={`${id}-current`}
          type="password"
          autoComplete="current-password"
          value={current}
          aria-invalid={currentError ? true : undefined}
          aria-describedby={currentError ? `${id}-current-error` : undefined}
          onChange={(event) => {
            setCurrent(event.target.value);
          }}
          autoFocus
        />
      </Field>
      <Field
        id={`${id}-new`}
        label={t('password.new')}
        hint={t('password.policy', { min: PASSWORD_MIN_LENGTH })}
        error={passwordError}
        required
      >
        <Input
          id={`${id}-new`}
          type="password"
          autoComplete="new-password"
          value={password}
          aria-invalid={passwordError ? true : undefined}
          aria-describedby={passwordError ? `${id}-new-error` : `${id}-new-hint`}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />
      </Field>
      <Field
        id={`${id}-confirm`}
        label={t('password.confirm')}
        error={errors.confirm && t(errors.confirm)}
        required
      >
        <Input
          id={`${id}-confirm`}
          type="password"
          autoComplete="new-password"
          value={confirm}
          aria-invalid={errors.confirm ? true : undefined}
          aria-describedby={errors.confirm ? `${id}-confirm-error` : undefined}
          onChange={(event) => {
            setConfirm(event.target.value);
          }}
        />
      </Field>
      <Button type="submit" busy={change.isPending}>
        {change.isPending ? t('common.working') : t('password.submit')}
      </Button>
    </form>
  );
}
