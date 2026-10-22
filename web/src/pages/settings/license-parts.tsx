import type { TFunction } from 'i18next';
import { CircleCheck, CircleMinus, Copy, Download, Lock, Trash2, Upload } from 'lucide-react';
import type { ReactNode } from 'react';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LicenseStateBadge } from '@/components/license/license-state-badge';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useRemoveLicense, useUploadLicense } from '@/lib/api-hooks';
import type { LicenseLimitOut, LicenseModuleOut, LicenseOut } from '@/lib/api-types';
import { saveBlob } from '@/lib/download';
import { formatBytes, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import {
  formatRequestCode,
  historySummary,
  licenseDayText,
  licenseFileProblem,
  moduleShownState,
  REQUEST_CODE_FILE,
  requestCodeFileText,
  timeSourceText,
} from '@/lib/license';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { cn } from '@/lib/utils';

/** One section of the licence page: a card with a level-2 heading. */
export function LicenseSection({
  title,
  description,
  children,
  testId,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  const id = useId();
  return (
    <Card className="flex flex-col gap-4 p-5" data-testid={testId}>
      <section aria-labelledby={id} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id={id} className="text-base font-semibold text-foreground">
            {title}
          </h2>
          {description ? <p className="text-13 text-muted">{description}</p> : null}
        </div>
        {children}
      </section>
    </Card>
  );
}

function Item({
  label,
  children,
  mono = false,
  wide = false,
  testId,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
  wide?: boolean;
  testId?: string;
}) {
  return (
    <div
      className={cn('flex min-w-0 flex-col gap-0.5', wide && 'sm:col-span-2')}
      data-testid={testId}
    >
      <dt className="text-xs text-muted-strong">{label}</dt>
      <dd className={cn('text-sm break-words text-foreground', mono && 'font-mono text-13')}>
        {children}
      </dd>
    </div>
  );
}

/** Durum: the state and the fields of the verified licence. */
export function StatusFields({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const details = status.details;
  const verified = status.maintenance !== null;
  const maintenance = status.maintenance;
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2" data-testid="license-status">
      <Item label={t('license.page.status.state')}>
        <LicenseStateBadge state={status.status} />
      </Item>
      <Item label={t('license.page.status.licensee')} testId="license-licensee">
        {status.licensee ??
          // `invalid`: a licence is installed, it only did not verify (no licensee to show).
          t(
            status.status === 'invalid'
              ? 'license.page.status.unverified'
              : 'license.page.status.noLicense',
          )}
      </Item>
      {status.problem ? (
        <Item label={t('license.page.status.problem')} wide testId="license-problem">
          {t(`license.page.problems.${status.problem}`, { defaultValue: status.problem })}
        </Item>
      ) : null}
      {details?.license_id ? (
        <Item label={t('license.page.status.licenseId')} mono testId="license-id">
          {details.license_id}
        </Item>
      ) : null}
      {details?.key_id ? (
        <Item label={t('license.page.status.keyId')} mono>
          {details.key_id}
        </Item>
      ) : null}
      {details?.issued_at ? (
        <Item label={t('license.page.status.issuedAt')}>
          <span className="tabular">{formatDateTime(details.issued_at)}</span>
        </Item>
      ) : null}
      {details?.installed_at ? (
        <Item label={t('license.page.status.installed')}>
          <span className="tabular">
            {t('license.page.status.installedValue', {
              at: formatDateTime(details.installed_at),
              by: details.installed_by ?? '—',
            })}
          </span>
        </Item>
      ) : null}
      {verified || status.valid_until ? (
        <Item label={t('license.page.status.validUntil')} testId="license-valid-until">
          {status.valid_until
            ? licenseDayText(status.valid_until, lang)
            : t('license.page.status.noEnd')}
        </Item>
      ) : null}
      {maintenance ? (
        <Item label={t('license.page.status.maintenance')} testId="license-maintenance">
          <span className="tabular">
            {maintenance.expired
              ? t('license.page.status.maintenanceOver', {
                  until: licenseDayText(maintenance.until, lang),
                })
              : t('license.page.status.maintenanceLeft', {
                  until: licenseDayText(maintenance.until, lang),
                  count: maintenance.days_left,
                })}
          </span>
          <span className="block text-xs text-muted">
            {t('license.page.status.maintenanceHint')}
          </span>
        </Item>
      ) : null}
      {status.trial ? (
        <Item label={t('license.page.status.trialEnds')} testId="license-trial">
          <span className="tabular">
            {status.trial.days_left > 0
              ? t('license.page.status.trialLeft', {
                  at: formatDateTime(status.trial.ends_at),
                  count: status.trial.days_left,
                })
              : t('license.page.status.trialOver', { at: formatDateTime(status.trial.ends_at) })}
          </span>
        </Item>
      ) : null}
      <Item label={t('license.page.status.checkedAt')}>
        <span className="tabular">{formatDateTime(status.checked_at)}</span>
      </Item>
    </dl>
  );
}

function ModuleStateBadge({ module }: { module: LicenseModuleOut }) {
  const { t } = useTranslation();
  const shown = moduleShownState(module);
  if (shown === 'on')
    return (
      <Badge tone="success" icon={CircleCheck}>
        {t('license.module.on')}
      </Badge>
    );
  if (shown === 'off')
    return (
      <Badge tone="neutral" icon={CircleMinus}>
        {t('license.module.off')}
      </Badge>
    );
  return (
    <Badge tone="outline" icon={Lock}>
      {t('license.module.notLicensed')}
    </Badge>
  );
}

const CELL = 'border-b border-border px-3 py-2 align-top';
const HEAD =
  'border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong';

/** The module table: name, description, On / Off / Not licensed (texts from the API). */
export function ModuleTable({ modules }: { modules: readonly LicenseModuleOut[] }) {
  const { t } = useTranslation();
  const english = currentLang() === 'en';
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table
        className="w-full border-separate border-spacing-0 text-13"
        data-testid="license-modules"
      >
        <caption className="sr-only">{t('license.module.caption')}</caption>
        <thead>
          <tr>
            <th scope="col" className={HEAD}>
              {t('license.module.name')}
            </th>
            <th scope="col" className={HEAD}>
              {t('license.module.description')}
            </th>
            <th scope="col" className={HEAD}>
              {t('license.module.state')}
            </th>
          </tr>
        </thead>
        <tbody>
          {modules.map((module) => (
            <tr key={module.key} data-module={module.key}>
              <th scope="row" className={cn(CELL, 'text-left font-medium text-foreground')}>
                <span className="flex flex-col gap-1">
                  {english ? module.name_en : module.name_tr}
                  {module.always ? (
                    <span className="text-xs font-normal text-muted">
                      {t('license.module.always')}
                    </span>
                  ) : null}
                </span>
              </th>
              <td className={cn(CELL, 'text-muted-strong')}>
                {english ? module.description_en : module.description_tr}
              </td>
              <td className={cn(CELL, 'whitespace-nowrap')}>
                <ModuleStateBadge module={module} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function limitText(limit: LicenseLimitOut, t: TFunction): string {
  const lang = currentLang();
  return t('license.page.limits.usedOf', {
    used: formatNumber(limit.used, 0, lang),
    max: limit.max === null ? t('license.page.limits.unlimited') : formatNumber(limit.max, 0, lang),
  });
}

/** Log sources and users: `used / max` or "Sınırsız". */
export function LimitsTable({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const rows = [
    ['log_sources', status.limits.log_sources],
    ['users', status.limits.users],
  ] as const;
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto rounded-lg border border-border">
        <table
          className="w-full border-separate border-spacing-0 text-13"
          data-testid="license-limits"
        >
          <caption className="sr-only">{t('license.page.limits.caption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={HEAD}>
                {t('license.page.limits.name')}
              </th>
              <th scope="col" className={HEAD}>
                {t('license.page.limits.value')}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, limit]) => (
              <tr key={key} data-limit={key}>
                <th scope="row" className={cn(CELL, 'text-left font-medium text-foreground')}>
                  {t(`license.page.limits.${key}`)}
                </th>
                <td className={CELL}>
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="tabular">{limitText(limit, t)}</span>
                    {limit.exceeded ? (
                      <Badge tone="warning">{t('license.page.limits.exceeded')}</Badge>
                    ) : limit.full ? (
                      <Badge tone="neutral">{t('license.page.limits.full')}</Badge>
                    ) : null}
                    {key === 'log_sources' && limit.unconfigured > 0 ? (
                      <span className="text-xs text-muted">
                        {t('license.page.limits.unconfigured', { count: limit.unconfigured })}
                      </span>
                    ) : null}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{t('license.page.limits.sourcesHint')}</p>
    </div>
  );
}

/** Etkinleştirme: the steps, the request code (copy, download) and this server's ids. */
export function ActivationFields({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const codeBox = useRef<HTMLParagraphElement>(null);
  const details = status.details;
  if (!details) return null;
  const code = details.request_code;
  const email = status.licensor.email;

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast({ title: t('common.copied'), variant: 'success' });
    } catch {
      // No clipboard (plain HTTP): select the code so Ctrl+C copies it.
      if (codeBox.current) window.getSelection()?.selectAllChildren(codeBox.current);
      toast({ title: t('common.copyFailed'), variant: 'danger' });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-13 font-semibold text-foreground">
          {t('license.page.activation.steps')}
        </h3>
        <ol className="list-decimal pl-5 text-sm text-foreground" data-testid="license-steps">
          <li>{t('license.page.activation.step1')}</li>
          <li>
            <StepWithEmail email={email} />
          </li>
          <li>{t('license.page.activation.step3')}</li>
        </ol>
      </div>
      {code ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-13 font-medium text-foreground">
              {t('license.page.activation.requestCode')}
            </span>
            <span className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copy(formatRequestCode(code))}
              >
                <Copy aria-hidden="true" />
                {t('license.page.activation.copy')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  saveBlob(
                    new Blob([requestCodeFileText(code)], { type: 'text/plain;charset=utf-8' }),
                    REQUEST_CODE_FILE,
                  );
                }}
              >
                <Download aria-hidden="true" />
                {t('license.page.activation.download')}
              </Button>
            </span>
          </div>
          <p
            ref={codeBox}
            data-testid="request-code"
            className="rounded-lg border border-border bg-subtle p-3 font-mono text-13 leading-6 wrap-anywhere text-foreground select-all"
          >
            {formatRequestCode(code)}
          </p>
        </div>
      ) : (
        <Alert tone="warning" data-testid="request-code-unavailable">
          {t('license.page.activation.serverIdUnavailable')}
          {details.server_id_error ? (
            <span className="mt-1 block font-mono text-xs">{details.server_id_error}</span>
          ) : null}
        </Alert>
      )}
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-3">
        <Item label={t('license.page.activation.serverId')} mono testId="license-server-id">
          {details.server_id ?? '—'}
        </Item>
        <Item label={t('license.page.activation.installationId')} mono>
          {details.installation_id}
        </Item>
        <Item label={t('license.page.activation.organization')} testId="license-organization">
          {details.organization.trim() ? (
            details.organization
          ) : (
            <span className="text-warning-ink">
              {t('license.page.activation.organizationMissing')}
            </span>
          )}
        </Item>
      </dl>
    </div>
  );
}

/** Step 2 with the licensor's address as a mail link (no network: the mail program opens). */
function StepWithEmail({ email }: { email: string }) {
  const { t } = useTranslation();
  // The sentence around the address, whatever the word order of the language.
  const marker = '\u0001';
  const [before = '', after = ''] = t('license.page.activation.step2', { email: marker }).split(
    marker,
  );
  return (
    <>
      {before}
      <a
        href={`mailto:${email}`}
        className="font-medium text-primary-ink underline underline-offset-2"
      >
        {email}
      </a>
      {after}
    </>
  );
}

/** Lisans dosyası: upload a `.lic` (re-authentication first), remove the active licence. */
export function LicenseFileForm({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const { withReauth } = useReauth();
  const upload = useUploadLicense();
  const remove = useRemoveLicense();
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const problem = file ? licenseFileProblem(file) : null;
  // In the trial states no licence is active; every other state comes from an installed file.
  const installed = status.status !== 'trial' && status.status !== 'trial_expired';

  async function send() {
    if (!file || problem) return;
    setUploadError(null);
    try {
      await withReauth(() => upload.mutateAsync(file));
      toast({ title: t('license.page.file.uploaded'), variant: 'success' });
      setFile(null);
      setInputKey((value) => value + 1);
    } catch (error) {
      if (!isReauthCancelled(error)) setUploadError(errorMessage(error));
    }
  }

  async function removeLicense() {
    setRemoveError(null);
    try {
      await withReauth(() => remove.mutateAsync());
      setConfirmOpen(false);
      toast({ title: t('license.page.file.removed'), variant: 'success' });
    } catch (error) {
      if (!isReauthCancelled(error)) setRemoveError(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${id}-file`} className="text-13 font-medium text-foreground">
          {t('license.page.file.choose')}
        </label>
        <input
          key={inputKey}
          id={`${id}-file`}
          type="file"
          accept=".lic,application/json"
          disabled={upload.isPending}
          aria-describedby={`${id}-hint`}
          aria-invalid={problem ? true : undefined}
          onChange={(event) => {
            setUploadError(null);
            setFile(event.target.files?.[0] ?? null);
          }}
          className="block w-full max-w-xl text-13 text-foreground file:mr-3 file:h-8 file:rounded-lg file:border file:border-border file:bg-surface file:px-3 file:text-13 file:font-medium file:text-foreground hover:file:bg-subtle"
        />
        <p
          id={`${id}-hint`}
          className={cn('text-xs', problem ? 'text-danger-ink' : 'text-muted')}
          data-testid="license-file-hint"
        >
          {problem
            ? t(problem)
            : file
              ? t('license.page.file.chosen', {
                  name: file.name,
                  size: formatBytes(file.size, lang),
                })
              : t('license.page.file.hint')}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!file || problem !== null}
          busy={upload.isPending}
          onClick={() => void send()}
        >
          <Upload aria-hidden="true" />
          {upload.isPending ? t('common.working') : t('license.page.file.upload')}
        </Button>
        {installed ? (
          <Button
            variant="secondary"
            onClick={() => {
              setRemoveError(null);
              setConfirmOpen(true);
            }}
          >
            <Trash2 aria-hidden="true" />
            {t('license.page.file.remove')}
          </Button>
        ) : null}
      </div>
      {uploadError ? (
        <Alert tone="danger" title={t('license.page.file.refused')} data-testid="license-refusal">
          {uploadError}
        </Alert>
      ) : null}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t('license.page.file.removeTitle')}
        description={t('license.page.file.removeBody')}
        confirmLabel={t('license.page.file.remove')}
        danger
        busy={remove.isPending}
        error={removeError}
        onConfirm={() => void removeLicense()}
      />
    </div>
  );
}

/** Geçmiş: the newest licence events (`details.history`). */
export function HistoryTable({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const history = status.details?.history ?? [];
  if (history.length === 0)
    return <p className="text-13 text-muted">{t('license.page.history.empty')}</p>;
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-border">
      <table
        className="w-full border-separate border-spacing-0 text-13"
        data-testid="license-history"
      >
        <caption className="sr-only">{t('license.page.history.caption')}</caption>
        <thead>
          <tr>
            {(['at', 'kind', 'source', 'actor', 'licenseId', 'details'] as const).map((column) => (
              <th key={column} scope="col" className={cn(HEAD, 'sticky top-0 whitespace-nowrap')}>
                {t(`license.page.history.${column}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {history.map((entry, index) => (
            <tr key={`${entry.at}-${index}`}>
              <td className={cn(CELL, 'whitespace-nowrap tabular')}>{formatDateTime(entry.at)}</td>
              <td className={cn(CELL, 'whitespace-nowrap')}>
                {t(`license.page.history.kinds.${entry.kind}`, { defaultValue: entry.kind })}
              </td>
              <td className={cn(CELL, 'whitespace-nowrap')}>
                {t(`license.page.history.sources.${entry.source}`, { defaultValue: entry.source })}
              </td>
              <td className={cn(CELL, 'font-mono text-xs')}>{entry.actor}</td>
              <td className={cn(CELL, 'font-mono text-xs whitespace-nowrap')}>
                {entry.license_id ?? '—'}
              </td>
              <td className={CELL}>{historySummary(entry, t)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Hakkında: version, release date, rights holder, the time the licence checks use. */
export function AboutFields({ status }: { status: LicenseOut }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const time = status.details?.time ?? null;
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2" data-testid="license-about">
        <Item label={t('license.page.about.version')}>
          {status.product.name} {status.product.version}
        </Item>
        <Item label={t('license.page.about.releaseDate')}>
          {licenseDayText(status.product.release_date, lang)}
        </Item>
        <Item label={t('license.page.about.rightsHolder')} testId="license-rights-holder">
          {status.licensor.name},{' '}
          <a
            href={`mailto:${status.licensor.email}`}
            className="text-primary-ink underline underline-offset-2"
          >
            {status.licensor.email}
          </a>
        </Item>
        {time ? (
          <>
            <Item label={t('license.page.about.timeSource')} testId="license-time-source">
              {timeSourceText(time, t)}
            </Item>
            <Item label={t('license.page.about.trustedTime')}>
              <span className="tabular">{formatDateTime(time.now)}</span>
            </Item>
          </>
        ) : null}
      </dl>
      <p className="text-xs text-muted">{t('license.page.about.offline')}</p>
    </div>
  );
}
