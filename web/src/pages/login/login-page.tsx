import { useQueryClient } from '@tanstack/react-query';
import { Clock } from 'lucide-react';
import { useCallback, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { AuthLayout } from '@/components/auth/auth-layout';
import { PasswordChangeForm } from '@/components/auth/password-change-form';
import { RecoveryCodesPanel } from '@/components/auth/recovery-codes-panel';
import { Alert, type AlertTone } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { isApiError, type ApiError } from '@/lib/api';
import { queryKeys, useMe, useSetupStatus } from '@/lib/api-hooks';
import { IDLE_MINUTES_KEY } from '@/lib/auth';
import { REASON_CODES, reasonFor, safeNext } from '@/lib/redirect';
import { readPreference } from '@/lib/storage';

import { CredentialsStep } from './credentials-step';
import { TotpEnrolStep } from './totp-enrol-step';
import { TotpStep } from './totp-step';

/** A message above the form: an i18n key, or a server error (texts follow the UI language). */
interface Notice {
  tone: AlertTone;
  key?: string;
  error?: ApiError;
}

function IdleNote({ minutes }: { minutes: number | null }) {
  const { t } = useTranslation();
  return (
    <p className="flex items-center justify-center gap-1.5 text-xs text-muted">
      <Clock aria-hidden="true" className="size-3.5" />
      {minutes ? t('login.idleNote', { minutes }) : t('login.idleNoteGeneric')}
    </p>
  );
}

function cachedIdleMinutes(): number | null {
  const value = Number(readPreference(IDLE_MINUTES_KEY));
  return Number.isInteger(value) && value > 0 ? value : null;
}

/**
 * Giriş (spec 12.3 page 1): user name + password, then whatever the server says comes next
 * (`/auth/me` → `next`): the TOTP code (or a recovery code), the first TOTP enrolment with the
 * recovery codes shown once, and the forced password change. A reload resumes at the same step;
 * the KVKK notice follows inside the application. `?next=` is the page to return to.
 */
export function LoginPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const target = safeNext(params.get('next'));
  const me = useMe();
  const setup = useSetupStatus();
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [leavingCodes, setLeavingCodes] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(() => {
    const reason = params.get('reason');
    if (reason && REASON_CODES.includes(reason))
      return { tone: 'warning', key: `login.reasons.${reason}` };
    if (params.get('setup') === 'done') return { tone: 'success', key: 'setup.doneNotice' };
    return null;
  });

  /** A step's request found the session gone (locked, expired): start again with a message. */
  const sessionLost = useCallback(
    (error: ApiError) => {
      const reason = reasonFor(error.code);
      const tone = error.code === 'account_locked' ? 'danger' : 'warning';
      setNotice(reason ? { tone, key: `login.reasons.${reason}` } : { tone, error });
      void client.invalidateQueries({ queryKey: queryKeys.me });
    },
    [client],
  );

  if (setup.data?.needs_setup) return <Navigate to="/setup" replace />;

  // Before sign-in the public status says it (a new browser has nothing cached).
  const idleMinutes =
    me.data?.idle_timeout_minutes ?? setup.data?.idle_timeout_minutes ?? cachedIdleMinutes();
  const noticeBox = notice ? (
    <Alert tone={notice.tone}>{notice.key ? t(notice.key) : notice.error?.localized}</Alert>
  ) : null;

  if (recoveryCodes && me.data) {
    return (
      <AuthLayout
        title={t('recovery.title')}
        description={t('recovery.description')}
        wide
        step="recovery"
      >
        <RecoveryCodesPanel
          codes={recoveryCodes}
          account={me.data.user.username}
          busy={leavingCodes}
          onContinue={() => {
            setLeavingCodes(true);
            // Read the next step first: clearing the codes earlier would briefly show the
            // enrolment again from the old answer.
            void client.refetchQueries({ queryKey: queryKeys.me }).finally(() => {
              setRecoveryCodes(null);
              setLeavingCodes(false);
            });
          }}
        />
      </AuthLayout>
    );
  }

  if (me.isPending || setup.isPending) {
    return (
      <AuthLayout title={t('login.title')}>
        <div className="flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
      </AuthLayout>
    );
  }

  if (me.isError) {
    const unexpected = isApiError(me.error) && me.error.status !== 401 ? me.error : null;
    return (
      <AuthLayout title={t('login.title')} description={t('login.description')} step="password">
        {noticeBox}
        {unexpected ? <Alert tone="danger">{unexpected.localized}</Alert> : null}
        <CredentialsStep
          onSubmitted={() => {
            setNotice(null);
          }}
        />
        <IdleNote minutes={idleMinutes} />
      </AuthLayout>
    );
  }

  const step = me.data.next;
  if (step === 'done' || step === 'privacy_notice') return <Navigate to={target} replace />;

  if (step === 'totp') {
    return (
      <AuthLayout title={t('login.totpTitle')} description={t('login.totpDescription')} step="totp">
        {noticeBox}
        <TotpStep
          username={me.data.user.username}
          onSessionLost={sessionLost}
          onSubmitted={() => {
            setNotice(null);
          }}
        />
        <IdleNote minutes={idleMinutes} />
      </AuthLayout>
    );
  }

  if (step === 'totp_setup') {
    return (
      <AuthLayout title={t('enrol.title')} description={t('enrol.description')} wide step="enrol">
        {noticeBox}
        <TotpEnrolStep onSessionLost={sessionLost} onEnrolled={setRecoveryCodes} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={t('password.title')}
      description={t('password.forcedDescription')}
      step="password_change"
    >
      {noticeBox}
      <PasswordChangeForm forced onSessionLost={sessionLost} />
      <IdleNote minutes={idleMinutes} />
    </AuthLayout>
  );
}
