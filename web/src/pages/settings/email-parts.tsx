import { Mail, ShieldCheck } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { TextField } from '@/components/admin/form-fields';
import { RequestProgress } from '@/components/admin/request-progress';
import { SettingsCard } from '@/components/admin/settings-frame';
import { UploadField } from '@/components/admin/upload-field';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { localized } from '@/lib/admin-requests';
import { errorMessage, isApiError } from '@/lib/api';
import { useClearSmtpCa, useSetSmtpCa, useTestEmail } from '@/lib/api-hooks';
import { shortHash } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import type {
  AdminRequestOut,
  SmtpSection,
  TestEmailResult,
  UploadPreview,
} from '@/lib/settings-types';

/** A list of the addresses a test e-mail may go to (alert recipients, one's own address). */
export function RecipientSelect({
  id,
  label,
  hint,
  value,
  options,
  onChange,
  disabled = false,
  error,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string | undefined;
}) {
  const { t } = useTranslation();
  if (options.length === 0)
    return (
      <Alert tone="warning" assertive={false}>
        {t('settings.email.noRecipients')}
      </Alert>
    );
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          id={id}
          className="w-80 max-w-full"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        >
          <SelectValue placeholder={t('settings.email.chooseRecipient')} />
        </SelectTrigger>
        <SelectContent>
          {options.map((address) => (
            <SelectItem key={address} value={address}>
              {address}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

function TestEmailOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = request.result as TestEmailResult | null;
  if (!result) return null;
  return result.ok ? (
    <Alert tone="success" assertive={false} data-testid="test-email-result">
      {t('settings.email.test.sent')}
    </Alert>
  ) : (
    <Alert tone="danger" data-testid="test-email-result">
      {t('settings.email.test.failed', { error: result.error_text ?? '—' })}
    </Alert>
  );
}

/**
 * "Test e-postası gönder" (contract 2.9): the saved SMTP configuration sends one message to an
 * alert recipient or to the signed-in user (at most 5 an hour); the worker reports the answer.
 */
export function TestEmailCard({
  recipients,
  writable,
}: {
  recipients: readonly string[];
  writable: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const test = useTestEmail();
  const [to, setTo] = useState('');
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function send() {
    setProblem(null);
    try {
      const accepted = await test.mutateAsync({ to });
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
    <SettingsCard
      title={t('settings.email.test.title')}
      description={t('settings.email.test.description')}
      testId="settings-test-email"
    >
      <RecipientSelect
        id={`${id}-to`}
        label={t('settings.email.test.to')}
        value={to}
        options={recipients}
        onChange={setTo}
        disabled={!writable}
      />
      <div>
        <Button
          variant="secondary"
          disabled={!writable || !to}
          busy={test.isPending}
          onClick={() => void send()}
        >
          <Mail aria-hidden="true" />
          {t('settings.email.test.send')}
        </Button>
      </div>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          renderResult={(request) => <TestEmailOutcome request={request} />}
        />
      ) : null}
    </SettingsCard>
  );
}

/**
 * The CA of the SMTP server (contract 2.6 "smtp"): the system's trust store, or an uploaded PEM
 * file of 1–10 certificates (`smtp-ca\<sha256>.pem`). Both changes need a password confirmation.
 * The CA decides which server is trusted, so an enabled server is tried with it first (a test
 * e-mail to `recipients`), and with a user name the SMTP password is typed again: the stored one
 * is never sent to a server trusted differently (WP1-10, WP4-4).
 */
export function SmtpCaCard({
  smtp,
  etag,
  writable,
  recipients,
}: {
  smtp: SmtpSection;
  etag: string;
  writable: boolean;
  recipients: readonly string[];
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const setCa = useSetSmtpCa();
  const clearCa = useClearSmtpCa();
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [testTo, setTestTo] = useState('');
  const [password, setPassword] = useState('');
  // Going back to the system's store asks for the same as a new file first.
  const [clearing, setClearing] = useState(false);
  const certificate = preview?.certificate ?? null;
  const needsTest = smtp.enabled;
  const needsPassword = smtp.username !== null;
  const ready = (!needsTest || testTo !== '') && (!needsPassword || password !== '');
  // What the server is tried with (the same for the upload and for going back).
  const probe = {
    ...(needsTest ? { test_to: testTo } : {}),
    ...(needsPassword ? { password } : {}),
  };

  async function run(action: () => Promise<{ request_id: string }>) {
    setFailure(null);
    try {
      const accepted = await withReauth(action);
      setRequestId(accepted.request_id);
      setPreview(null);
      setClearing(false);
      setPassword('');
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const probeFields =
    needsTest || needsPassword ? (
      <div className="flex flex-col gap-3">
        {needsTest ? (
          <RecipientSelect
            id={`${id}-test_to`}
            label={t('settings.email.testTo')}
            hint={t('settings.email.ca.testToHint')}
            value={testTo}
            options={recipients}
            disabled={!writable}
            onChange={setTestTo}
          />
        ) : null}
        {needsPassword ? (
          <TextField
            id={`${id}-password`}
            label={t('settings.email.password')}
            hint={t('settings.email.ca.passwordHint')}
            type="password"
            autoComplete="new-password"
            value={password}
            disabled={!writable}
            inputClassName="w-80 max-w-full"
            onChange={setPassword}
          />
        ) : null}
      </div>
    ) : null;

  return (
    <SettingsCard
      title={t('settings.email.ca.title')}
      description={t('settings.email.ca.description')}
      testId="settings-smtp-ca"
    >
      <p
        className="flex flex-wrap items-center gap-2 text-13 text-foreground"
        data-testid="smtp-ca"
      >
        <ShieldCheck aria-hidden="true" className="size-4 text-muted-strong" />
        {smtp.ca.sha256 ? (
          <>
            {t('settings.email.ca.custom')}
            <span className="font-mono text-xs" title={smtp.ca.sha256}>
              {shortHash(smtp.ca.sha256, 16)}
            </span>
          </>
        ) : (
          t('settings.email.ca.system')
        )}
      </p>
      {smtp.ca.sha256 && clearing ? (
        <div
          className="flex flex-col gap-3 rounded-lg border border-border p-4"
          data-testid="smtp-ca-clear"
        >
          {probeFields}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={!writable || !ready}
              busy={clearCa.isPending}
              onClick={() => void run(() => clearCa.mutateAsync({ etag, ...probe }))}
            >
              {t('settings.email.ca.clear')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setClearing(false);
                setPassword('');
              }}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      ) : smtp.ca.sha256 ? (
        <div>
          <Button
            variant="secondary"
            size="sm"
            disabled={!writable}
            busy={clearCa.isPending}
            onClick={() => {
              if (probeFields) setClearing(true);
              else void run(() => clearCa.mutateAsync({ etag }));
            }}
          >
            {t('settings.email.ca.clear')}
          </Button>
        </div>
      ) : null}
      <UploadField
        kind="smtp_ca"
        label={t('settings.email.ca.upload')}
        hint={t('settings.email.ca.uploadHint')}
        accept=".pem,.crt,.cer"
        disabled={!writable}
        onUploaded={(next) => {
          setPreview(next);
          setFailure(null);
        }}
      />
      {preview && certificate ? (
        <div
          className="flex flex-col gap-3 rounded-lg border border-border p-4"
          data-testid="smtp-ca-preview"
        >
          <dl className="grid gap-x-6 gap-y-1.5 text-13 sm:grid-cols-[minmax(10rem,auto)_1fr]">
            <dt className="text-muted-strong">{t('settings.kamusm.roots.subject')}</dt>
            <dd>{certificate.subject}</dd>
            <dt className="text-muted-strong">{t('settings.kamusm.roots.validity')}</dt>
            <dd className="tabular">
              {formatDateTime(certificate.not_before)} – {formatDateTime(certificate.not_after)}
            </dd>
          </dl>
          <CodeBlock label={t('settings.kamusm.roots.fingerprint')} code={preview.sha256} wrap />
          {certificate.warnings.map((warning) => (
            <Alert key={warning.code} tone="warning" assertive={false}>
              {localized(warning)}
            </Alert>
          ))}
          {probeFields}
          <div>
            <Button
              disabled={!writable || !ready}
              busy={setCa.isPending}
              onClick={() =>
                void run(() => setCa.mutateAsync({ etag, upload_id: preview.upload_id, ...probe }))
              }
            >
              {t('settings.email.ca.use')}
            </Button>
          </div>
        </div>
      ) : null}
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      {requestId ? <RequestProgress requestId={requestId} /> : null}
    </SettingsCard>
  );
}
