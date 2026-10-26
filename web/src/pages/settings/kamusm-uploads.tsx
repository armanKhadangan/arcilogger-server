import { ShieldCheck } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { RequestProgress } from '@/components/admin/request-progress';
import { UploadField } from '@/components/admin/upload-field';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { CodeBlock } from '@/components/ui/code-block';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { localized } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useAddLegalRoot, useAddRoot, useStageZamane } from '@/lib/api-hooks';
import { shortHash } from '@/lib/display';
import { formatBytes, formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import type { AdminPolicyOut, TsaSection, UploadPreview } from '@/lib/settings-types';

import { normalizeSha256 } from './kamusm-model';

/** The console command that approves a staged Zamane client (D0-16). */
function approveCommand(sha256: string): string {
  return `python -I -m arcilogger_core.admin approve-jar ${sha256}`;
}

/** A request one of these steps started, followed until it ends. */
function Started({ requestId }: { requestId: string | null }) {
  if (!requestId) return null;
  return <RequestProgress requestId={requestId} />;
}

/**
 * Step 2 "Zamane istemcisi": the client in use and why it may run, clients waiting for their
 * approval, and the upload of a new one. A known Kamu SM release is used once the system task
 * has copied it; any other release waits for an administrator's `approve-jar` on the server
 * console, at the earliest after `jar_hold_hours`, against the SHA-256 typed here from the Kamu SM
 * download page.
 */
export function ZamaneStep({
  tsa,
  etag,
  writable,
  policy,
}: {
  tsa: TsaSection;
  etag: string;
  writable: boolean;
  policy: AdminPolicyOut;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const { withReauth } = useReauth();
  const stage = useStageZamane();
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [expected, setExpected] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const client = tsa.zamane;
  const zamane = preview?.zamane ?? null;

  async function use() {
    if (!preview || !zamane) return;
    setFailure(null);
    let sha: string | undefined;
    if (!zamane.known) {
      const typed = normalizeSha256(expected);
      if (!typed) {
        setProblem('settings.kamusm.errors.sha');
        return;
      }
      if (typed !== preview.sha256) {
        setProblem('settings.kamusm.errors.shaMismatch');
        return;
      }
      sha = typed;
    }
    setProblem(null);
    try {
      const accepted = await withReauth(() =>
        stage.mutateAsync({
          etag,
          upload_id: preview.upload_id,
          ...(sha ? { expected_sha256: sha } : {}),
        }),
      );
      setRequestId(accepted.request_id);
      setPreview(null);
      setExpected('');
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid gap-x-6 gap-y-2 text-13 sm:grid-cols-[minmax(10rem,auto)_1fr]">
        <dt className="text-muted-strong">{t('settings.kamusm.zamane.current')}</dt>
        <dd data-testid="zamane-current">
          {client.present && client.sha256 ? (
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs" title={client.sha256}>
                {shortHash(client.sha256, 16)}
              </span>
              <span>{client.release ?? t('settings.kamusm.zamane.unknownRelease')}</span>
              <Badge tone={client.trusted === false ? 'danger' : 'success'}>
                {t(`settings.kamusm.zamane.trusted.${String(client.trusted)}`)}
              </Badge>
            </span>
          ) : (
            t('settings.kamusm.zamane.none')
          )}
        </dd>
      </dl>
      {client.staged.map((item) => (
        <Alert
          key={item.sha256}
          tone="warning"
          assertive={false}
          title={t('settings.kamusm.zamane.staged', { sha: shortHash(item.sha256, 16) })}
          data-testid="zamane-staged"
        >
          <p>
            {item.effective_at
              ? t('settings.kamusm.zamane.approveAfter', {
                  time: formatDateTime(item.effective_at),
                })
              : t('settings.kamusm.zamane.approve')}
          </p>
          <CodeBlock
            label={t('settings.kamusm.zamane.command')}
            code={approveCommand(item.sha256)}
            wrap
          />
        </Alert>
      ))}
      <UploadField
        kind="zamane_client"
        label={t('settings.kamusm.zamane.upload')}
        hint={t('settings.kamusm.zamane.uploadHint')}
        accept=".jar,.zip"
        disabled={!writable}
        onUploaded={(next) => {
          setPreview(next);
          setProblem(null);
          setFailure(null);
          setExpected('');
        }}
      />
      {preview && zamane ? (
        <div
          className="flex flex-col gap-3 rounded-lg border border-border p-4"
          data-testid="zamane-preview"
        >
          <p className="text-13">
            {t('settings.kamusm.zamane.file', {
              kind: zamane.kind === 'zip' ? 'ZIP' : 'JAR',
              name: zamane.jar_name,
              size: formatBytes(preview.size, lang),
            })}
          </p>
          <CodeBlock label={t('settings.kamusm.zamane.sha')} code={preview.sha256} wrap />
          {!zamane.accepted ? (
            <Alert tone="danger">{zamane.reason ?? t('settings.kamusm.zamane.refused')}</Alert>
          ) : zamane.known ? (
            <Alert tone="success" assertive={false}>
              {t('settings.kamusm.zamane.known', { release: zamane.release ?? '—' })}
            </Alert>
          ) : (
            <>
              <Alert
                tone="warning"
                assertive={false}
                title={t('settings.kamusm.zamane.unknownTitle')}
              >
                <p>{t('settings.kamusm.zamane.unknownBody', { hours: policy.jar_hold_hours })}</p>
                <CodeBlock
                  label={t('settings.kamusm.zamane.command')}
                  code={approveCommand(preview.sha256)}
                  wrap
                />
              </Alert>
              <Field
                id={`${id}-expected`}
                label={t('settings.kamusm.zamane.expected')}
                hint={t('settings.kamusm.zamane.expectedHint')}
                error={problem ? t(problem) : undefined}
                required
              >
                <Input
                  id={`${id}-expected`}
                  mono
                  spellCheck={false}
                  value={expected}
                  aria-invalid={problem ? true : undefined}
                  aria-describedby={problem ? `${id}-expected-error` : `${id}-expected-hint`}
                  onChange={(event) => {
                    setExpected(event.target.value);
                    setProblem(null);
                  }}
                />
              </Field>
            </>
          )}
          {zamane.accepted ? (
            <div>
              <Button busy={stage.isPending} disabled={!writable} onClick={() => void use()}>
                {t('settings.kamusm.zamane.use')}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <Started requestId={requestId} />
    </div>
  );
}

/**
 * Step 3 "Kök sertifikalar": the installed roots (subject, validity, SHA-256, legal root) and
 * the upload of another. A root becomes a legal root only after the administrator confirms that
 * its fingerprint was compared with the value Kamu SM publishes; removing a root or a legal
 * fingerprint is done on the server console.
 */
export function RootsStep({
  tsa,
  etag,
  writable,
}: {
  tsa: TsaSection;
  etag: string;
  writable: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const addRoot = useAddRoot();
  const addLegal = useAddLegalRoot();
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [legal, setLegal] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [marking, setMarking] = useState<string | null>(null);
  const [markConfirmed, setMarkConfirmed] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const certificate = preview?.certificate ?? null;
  const legalCount = tsa.roots.filter((root) => root.legal).length;

  async function install() {
    if (!preview) return;
    setFailure(null);
    try {
      const accepted = await withReauth(() =>
        addRoot.mutateAsync({
          etag,
          upload_id: preview.upload_id,
          legal,
          confirmed: legal && confirmed,
        }),
      );
      setRequestId(accepted.request_id);
      setPreview(null);
      setLegal(false);
      setConfirmed(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  async function markLegal() {
    if (!marking) return;
    setFailure(null);
    try {
      const accepted = await withReauth(() =>
        addLegal.mutateAsync({ etag, sha256: marking, confirmed: true }),
      );
      setRequestId(accepted.request_id);
      setMarking(null);
    } catch (error) {
      if (!isReauthCancelled(error)) {
        setFailure(errorMessage(error));
        setMarking(null);
      }
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {tsa.roots.length === 0 ? (
        <p className="text-13 text-muted">{t('settings.kamusm.roots.none')}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-13">
            <caption className="sr-only">{t('settings.kamusm.roots.caption')}</caption>
            <thead className="bg-subtle text-left text-xs text-muted-strong">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t('settings.kamusm.roots.subject')}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t('settings.kamusm.roots.notAfter')}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  SHA-256
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t('settings.kamusm.roots.legal')}
                </th>
              </tr>
            </thead>
            <tbody>
              {tsa.roots.map((root) => (
                <tr key={root.sha256} className="border-t border-border align-top">
                  <th scope="row" className="px-3 py-2 text-left font-medium">
                    {root.subject}
                    <span className="block text-xs font-normal text-muted-strong">
                      {t(`settings.kamusm.roots.source.${root.source}`)}
                    </span>
                  </th>
                  <td className="px-3 py-2 tabular">{formatDateTime(root.not_after)}</td>
                  <td className="px-3 py-2 font-mono text-xs" title={root.sha256}>
                    {shortHash(root.sha256, 16)}
                  </td>
                  <td className="px-3 py-2">
                    {root.legal ? (
                      <Badge tone="success" icon={ShieldCheck}>
                        {t('settings.kamusm.roots.isLegal')}
                      </Badge>
                    ) : (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={!writable}
                        aria-label={t('settings.kamusm.roots.markLabel', { subject: root.subject })}
                        onClick={() => {
                          setMarkConfirmed(false);
                          setMarking(root.sha256);
                        }}
                      >
                        {t('settings.kamusm.roots.mark')}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {legalCount === 0 && tsa.roots.length > 0 ? (
        <Alert tone="warning" assertive={false}>
          {t('settings.kamusm.roots.noLegal')}
        </Alert>
      ) : null}
      <UploadField
        kind="kamusm_root"
        label={t('settings.kamusm.roots.upload')}
        hint={t('settings.kamusm.roots.uploadHint')}
        accept=".pem,.crt,.cer,.der"
        disabled={!writable}
        onUploaded={(next) => {
          setPreview(next);
          setLegal(false);
          setConfirmed(false);
          setFailure(null);
        }}
      />
      {preview && certificate ? (
        <div
          className="flex flex-col gap-3 rounded-lg border border-border p-4"
          data-testid="root-preview"
        >
          <dl className="grid gap-x-6 gap-y-1.5 text-13 sm:grid-cols-[minmax(10rem,auto)_1fr]">
            <dt className="text-muted-strong">{t('settings.kamusm.roots.subject')}</dt>
            <dd>{certificate.subject}</dd>
            <dt className="text-muted-strong">{t('settings.kamusm.roots.issuer')}</dt>
            <dd>{certificate.issuer}</dd>
            <dt className="text-muted-strong">{t('settings.kamusm.roots.validity')}</dt>
            <dd className="tabular">
              {formatDateTime(certificate.not_before)} – {formatDateTime(certificate.not_after)}
            </dd>
            <dt className="text-muted-strong">{t('settings.kamusm.roots.key')}</dt>
            <dd>{certificate.key}</dd>
          </dl>
          <CodeBlock label={t('settings.kamusm.roots.fingerprint')} code={preview.sha256} wrap />
          {certificate.warnings.map((warning) => (
            <Alert key={warning.code} tone="warning" assertive={false}>
              {localized(warning)}
            </Alert>
          ))}
          {certificate.installed ? (
            <Alert tone="info" assertive={false}>
              {t('settings.kamusm.roots.installed')}
            </Alert>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <Switch
                  id={`${id}-legal`}
                  checked={legal}
                  onCheckedChange={setLegal}
                  disabled={!writable}
                />
                <Label htmlFor={`${id}-legal`}>{t('settings.kamusm.roots.legalSwitch')}</Label>
              </div>
              {legal ? (
                <div className="flex items-start gap-2">
                  <Checkbox
                    id={`${id}-confirmed`}
                    checked={confirmed}
                    onCheckedChange={(checked) => {
                      setConfirmed(checked === true);
                    }}
                  />
                  <Label htmlFor={`${id}-confirmed`} className="font-normal">
                    {t('settings.kamusm.roots.compared')}
                  </Label>
                </div>
              ) : null}
              <div>
                <Button
                  busy={addRoot.isPending}
                  disabled={!writable || (legal && !confirmed)}
                  onClick={() => void install()}
                >
                  {t('settings.kamusm.roots.install')}
                </Button>
              </div>
            </>
          )}
        </div>
      ) : null}
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <Started requestId={requestId} />
      <ChangeReviewDialog
        open={marking !== null}
        onOpenChange={(open) => {
          if (!open) setMarking(null);
        }}
        check={null}
        title={t('settings.kamusm.roots.markTitle')}
        description={t('settings.kamusm.roots.markBody')}
        confirmLabel={t('settings.kamusm.roots.mark')}
        busy={addLegal.isPending}
        confirmDisabled={!markConfirmed}
        onConfirm={() => void markLegal()}
      >
        {marking ? (
          <CodeBlock label={t('settings.kamusm.roots.fingerprint')} code={marking} wrap />
        ) : null}
        <div className="flex items-start gap-2">
          <Checkbox
            id={`${id}-mark-confirmed`}
            checked={markConfirmed}
            onCheckedChange={(checked) => {
              setMarkConfirmed(checked === true);
            }}
          />
          <Label htmlFor={`${id}-mark-confirmed`} className="font-normal">
            {t('settings.kamusm.roots.compared')}
          </Label>
        </div>
      </ChangeReviewDialog>
    </div>
  );
}
