import { useQueryClient } from '@tanstack/react-query';
import { Copy, ShieldCheck } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError, type ApiError } from '@/lib/api';
import { queryKeys, useTotpConfirm, useTotpSetup } from '@/lib/api-hooks';
import { groupSecret, svgDataUri } from '@/lib/totp-display';
import {
  focusFirstError,
  hasErrors,
  normalizeTotp,
  validateSecondFactor,
  type FieldErrors,
} from '@/lib/validation';

interface TotpEnrolStepProps {
  onSessionLost: (error: ApiError) => void;
  /** Called with the recovery codes (shown once) after the code was confirmed. */
  onEnrolled: (codes: string[]) => void;
}

function StepNumber({ value }: { value: number }) {
  return (
    <span
      aria-hidden="true"
      className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary-ink tabular"
    >
      {value}
    </span>
  );
}

/**
 * First TOTP enrolment (spec 12.3 page 1, 13): QR code from the API (`GET /auth/totp/setup`,
 * rendered as an image so the SVG can never run anything), the key for manual entry, and the
 * confirmation code (`POST /auth/totp/confirm`), which returns the recovery codes.
 */
export function TotpEnrolStep({ onSessionLost, onEnrolled }: TotpEnrolStepProps) {
  const { t } = useTranslation();
  const id = useId();
  const client = useQueryClient();
  const setup = useTotpSetup(true);
  const confirm = useTotpConfirm();
  const [code, setCode] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'code'>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const setupError = setup.error;

  useEffect(() => {
    if (!isApiError(setupError)) return;
    if (setupError.status === 401) onSessionLost(setupError);
    // Already enrolled (another tab finished it): /auth/me knows the next step.
    if (setupError.code === 'totp_already_enabled')
      void client.invalidateQueries({ queryKey: queryKeys.me });
  }, [setupError, onSessionLost, client]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateSecondFactor(code, 'totp');
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    confirm.mutate(
      { code: normalizeTotp(code) ?? code },
      {
        onSuccess: (result) => {
          onEnrolled(result.recovery_codes);
        },
        onError: (error) => {
          setCode('');
          if (isApiError(error) && error.status === 401) {
            onSessionLost(error);
            return;
          }
          setFailure(errorMessage(error));
        },
      },
    );
  }

  async function copyKey(secret: string) {
    try {
      await navigator.clipboard.writeText(secret);
      toast({ title: t('common.copied'), variant: 'success' });
    } catch {
      toast({ title: t('common.copyFailed'), variant: 'danger' });
    }
  }

  if (setup.isPending) {
    return (
      <div className="flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
        <Skeleton className="mx-auto size-48" />
        <Skeleton className="h-9" />
      </div>
    );
  }
  if (setup.isError) {
    return (
      <div className="flex flex-col gap-3">
        <Alert tone="danger">{errorMessage(setup.error)}</Alert>
        <Button variant="secondary" className="self-start" onClick={() => void setup.refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  const data = setup.data;
  const fieldError = errors.code && t(errors.code);
  return (
    <div className="flex flex-col gap-5">
      <ol className="flex flex-col gap-5">
        <li className="flex gap-3">
          <StepNumber value={1} />
          <p className="text-13 text-foreground">{t('enrol.install')}</p>
        </li>
        <li className="flex gap-3">
          <StepNumber value={2} />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <p className="text-13 text-foreground">{t('enrol.scan')}</p>
            <div className="flex flex-col items-start gap-4 sm:flex-row">
              <img
                src={svgDataUri(data.qr_svg)}
                alt={t('enrol.qrAlt', { account: data.account })}
                className="size-48 shrink-0 rounded-lg border border-border bg-white p-1"
                width={192}
                height={192}
              />
              <div className="flex min-w-0 flex-col gap-2">
                <p className="text-13 text-muted">{t('enrol.manual')}</p>
                <div className="flex items-center gap-1">
                  <code
                    className="rounded-md bg-subtle px-2 py-1 font-mono text-13 break-all text-foreground"
                    aria-label={t('enrol.secretLabel')}
                  >
                    {groupSecret(data.secret)}
                  </code>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('enrol.copySecret')}
                    title={t('enrol.copySecret')}
                    onClick={() => void copyKey(data.secret)}
                  >
                    <Copy aria-hidden="true" />
                  </Button>
                </div>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-muted">
                  <dt>{t('enrol.account')}</dt>
                  <dd className="font-mono text-foreground">
                    {data.issuer}:{data.account}
                  </dd>
                  <dt>{t('enrol.settings')}</dt>
                  <dd className="text-foreground">
                    {t('enrol.settingsValue', {
                      digits: data.digits,
                      period: data.period,
                      algorithm: data.algorithm,
                    })}
                  </dd>
                </dl>
              </div>
            </div>
          </div>
        </li>
        <li className="flex gap-3">
          <StepNumber value={3} />
          <form onSubmit={submit} className="flex min-w-0 flex-1 flex-col gap-3" noValidate>
            {failure ? <Alert tone="danger">{failure}</Alert> : null}
            <Field
              id={`${id}-code`}
              label={t('enrol.codeLabel')}
              hint={t('login.totpHint')}
              error={fieldError}
              required
            >
              <Input
                id={`${id}-code`}
                mono
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={9}
                value={code}
                aria-invalid={fieldError ? true : undefined}
                aria-describedby={fieldError ? `${id}-code-error` : `${id}-code-hint`}
                onChange={(event) => {
                  setCode(event.target.value);
                }}
                className="max-w-48"
              />
            </Field>
            <Button type="submit" className="self-start" busy={confirm.isPending}>
              <ShieldCheck aria-hidden="true" />
              {confirm.isPending ? t('common.working') : t('enrol.confirm')}
            </Button>
          </form>
        </li>
      </ol>
    </div>
  );
}
