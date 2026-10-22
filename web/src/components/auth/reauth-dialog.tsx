import { ShieldCheck } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import { useReauthMutation } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { RECENT_AUTH_MINUTES } from '@/lib/permissions';
import { normalizeTotp } from '@/lib/validation';

interface ReauthDialogProps {
  open: boolean;
  /** `true` after a successful `/auth/reauth`, `false` when cancelled. */
  onDone: (ok: boolean) => void;
}

/**
 * "Kimliğinizi doğrulayın": password (+ TOTP code when enrolled) before a sensitive action.
 * Used through `useReauth()` (`lib/reauth.tsx`), which retries the refused action afterwards.
 */
export function ReauthDialog({ open, onDone }: ReauthDialogProps) {
  const { t } = useTranslation();
  const { me } = useAuth();
  const id = useId();
  const reauth = useReauthMutation();
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const needsCode = me.mfa.enabled;
  const { reset } = reauth;

  useEffect(() => {
    if (!open) {
      setPassword('');
      setCode('');
      setProblem(null);
      reset();
    }
  }, [open, reset]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!password) {
      setProblem(t('reauth.passwordRequired'));
      return;
    }
    const totp = normalizeTotp(code);
    if (needsCode && totp === null) {
      setProblem(t('validation.totpFormat'));
      return;
    }
    setProblem(null);
    reauth.mutate(
      { password, code: needsCode ? totp : null },
      {
        onSuccess: () => {
          onDone(true);
        },
        onError: (error) => {
          setPassword('');
          setCode('');
          setProblem(errorMessage(error));
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onDone(false);
      }}
    >
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="size-5 text-primary" />
              {t('reauth.title')}
            </DialogTitle>
            <DialogDescription>
              {t('reauth.description', { minutes: RECENT_AUTH_MINUTES })}
            </DialogDescription>
          </DialogHeader>
          {problem ? <Alert tone="danger">{problem}</Alert> : null}
          <Field id={`${id}-password`} label={t('login.password')} required>
            <Input
              id={`${id}-password`}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              autoFocus
            />
          </Field>
          {needsCode ? (
            <Field
              id={`${id}-code`}
              label={t('login.totpCode')}
              hint={t('reauth.codeHint')}
              required
            >
              <Input
                id={`${id}-code`}
                inputMode="numeric"
                autoComplete="one-time-code"
                mono
                maxLength={9}
                value={code}
                aria-describedby={`${id}-code-hint`}
                onChange={(event) => {
                  setCode(event.target.value);
                }}
              />
            </Field>
          ) : null}
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onDone(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={reauth.isPending}>
              {reauth.isPending ? t('common.working') : t('reauth.confirm')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
