import { LogIn } from 'lucide-react';
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { errorMessage, isApiError } from '@/lib/api';
import { useLogin } from '@/lib/api-hooks';
import { focusFirstError, hasErrors, validateLogin, type FieldErrors } from '@/lib/validation';

/** Step 1: user name + password (`POST /auth/login`). */
export function CredentialsStep({ onSubmitted }: { onSubmitted: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const login = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'username' | 'password'>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateLogin({ username, password });
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    onSubmitted();
    login.mutate(
      { username: username.trim(), password },
      {
        onError: () => {
          setPassword('');
        },
      },
    );
  }

  const error = login.error;
  const retry =
    isApiError(error, 'rate_limited') && error.retryAfter
      ? t('login.retryAfter', { seconds: error.retryAfter })
      : null;

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      {error ? (
        <Alert tone="danger">
          {errorMessage(error)}
          {retry ? ` ${retry}` : null}
        </Alert>
      ) : null}
      <Field
        id={`${id}-username`}
        label={t('login.username')}
        error={errors.username && t(errors.username)}
        required
      >
        <Input
          id={`${id}-username`}
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          aria-invalid={errors.username ? true : undefined}
          aria-describedby={errors.username ? `${id}-username-error` : undefined}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
          autoFocus
        />
      </Field>
      <Field
        id={`${id}-password`}
        label={t('login.password')}
        error={errors.password && t(errors.password)}
        required
      >
        <Input
          id={`${id}-password`}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          aria-invalid={errors.password ? true : undefined}
          aria-describedby={errors.password ? `${id}-password-error` : undefined}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />
      </Field>
      <Button type="submit" size="lg" busy={login.isPending}>
        <LogIn aria-hidden="true" />
        {login.isPending ? t('login.signingIn') : t('login.submit')}
      </Button>
    </form>
  );
}
