import { useQueryClient } from '@tanstack/react-query';
import { KeyRound, ShieldCheck } from 'lucide-react';
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { toast } from '@/components/ui/toast';
import { api, errorMessage, isApiError, type ApiError } from '@/lib/api';
import { queryKeys, useTotpStep } from '@/lib/api-hooks';
import {
  focusFirstError,
  hasErrors,
  normalizeRecoveryCode,
  normalizeTotp,
  validateSecondFactor,
  type FieldErrors,
} from '@/lib/validation';

interface TotpStepProps {
  username: string;
  onSessionLost: (error: ApiError) => void;
  onSubmitted: () => void;
}

/** Step 2: the 6-digit code from the authenticator app, or one recovery code (`POST /auth/totp`). */
export function TotpStep({ username, onSessionLost, onSubmitted }: TotpStepProps) {
  const { t } = useTranslation();
  const id = useId();
  const client = useQueryClient();
  const totp = useTotpStep();
  const [mode, setMode] = useState<'totp' | 'recovery'>('totp');
  const [code, setCode] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'code'>>({});
  const [failure, setFailure] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateSecondFactor(code, mode);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    onSubmitted();
    const value = mode === 'totp' ? normalizeTotp(code) : normalizeRecoveryCode(code);
    totp.mutate(
      { code: value ?? code },
      {
        onSuccess: () => {
          if (mode === 'recovery') {
            toast({
              title: t('login.recoveryUsedTitle'),
              description: t('login.recoveryUsedBody'),
              variant: 'warning',
            });
          }
        },
        onError: (error) => {
          setCode('');
          // `totp_invalid` keeps the session (try again); any other 401 closed it.
          if (isApiError(error) && error.status === 401 && error.code !== 'totp_invalid') {
            onSessionLost(error);
            return;
          }
          setFailure(errorMessage(error));
        },
      },
    );
  }

  async function otherAccount() {
    try {
      await api.post('/auth/logout', undefined, { silent: true });
    } finally {
      await client.invalidateQueries({ queryKey: queryKeys.me });
    }
  }

  const label = mode === 'totp' ? t('login.totpCode') : t('login.recoveryCode');
  const hint = mode === 'totp' ? t('login.totpHint') : t('login.recoveryHint');
  const fieldError = errors.code && t(errors.code);

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      <p className="text-13 text-muted">{t('login.signedInAs', { username })}</p>
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <Field id={`${id}-code`} label={label} hint={hint} error={fieldError} required>
        <Input
          key={mode}
          id={`${id}-code`}
          name="code"
          mono
          autoComplete="one-time-code"
          inputMode={mode === 'totp' ? 'numeric' : 'text'}
          autoCapitalize="none"
          spellCheck={false}
          maxLength={mode === 'totp' ? 9 : 24}
          value={code}
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={fieldError ? `${id}-code-error` : `${id}-code-hint`}
          onChange={(event) => {
            setCode(event.target.value);
          }}
          autoFocus
        />
      </Field>
      <Button type="submit" size="lg" busy={totp.isPending}>
        <ShieldCheck aria-hidden="true" />
        {totp.isPending ? t('common.working') : t('login.verify')}
      </Button>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="link"
          size="sm"
          onClick={() => {
            setMode(mode === 'totp' ? 'recovery' : 'totp');
            setCode('');
            setErrors({});
            setFailure(null);
          }}
        >
          <KeyRound aria-hidden="true" />
          {mode === 'totp' ? t('login.useRecovery') : t('login.useTotp')}
        </Button>
        <Button variant="link" size="sm" onClick={() => void otherAccount()}>
          {t('login.otherAccount')}
        </Button>
      </div>
    </form>
  );
}
