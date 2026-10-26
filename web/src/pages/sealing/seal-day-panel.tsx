import {
  CircleCheck,
  CircleX,
  FileDown,
  Link2,
  ShieldCheck,
  Stamp,
  Unlink,
  type LucideIcon,
} from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { RequestProgress } from '@/components/admin/request-progress';
import { Alert, type AlertTone } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { CodeBlock } from '@/components/ui/code-block';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { apiDownload, errorMessage, isApiError } from '@/lib/api';
import { queryKeys, useSealDay, useSealNow, useVerifySeal } from '@/lib/api-hooks';
import type { Calendar, VerifyResult } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { formatDay, shortHash } from '@/lib/display';
import { saveBlob } from '@/lib/download';
import { formatBytes, formatDateTime, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { SealDayDetail, SealVerification } from '@/lib/seal-types';
import type { AdminRequestOut, SealActionResult } from '@/lib/settings-types';

import { AGENT_CODES, gapReason, legalGrade, toneOf } from './seal-states';

const LEGAL_TONE = { legal: 'success', claimed: 'info', test: 'danger' } as const;
const LEGAL_LABEL = {
  legal: 'sealing.token.legal',
  claimed: 'sealing.token.claimed',
  test: 'sealing.token.notLegal',
} as const;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
      {items.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted">{name}</dt>
          <dd className="min-w-0 break-words text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function time(value: string | null | undefined): string {
  return value ? formatDateTime(value) : '—';
}

function mono(value: ReactNode) {
  return <span className="font-mono text-xs break-all">{value}</span>;
}

const OUTCOME_TONE: Record<string, AlertTone> = {
  ok: 'success',
  failed: 'danger',
  timeout: 'warning',
  error: 'warning',
};

function VerificationLine({ verification }: { verification: SealVerification }) {
  const { t } = useTranslation();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Badge
        tone={verification.ok ? 'success' : 'danger'}
        icon={verification.ok ? CircleCheck : CircleX}
      >
        {verification.ok ? t('sealing.verification.ok') : t('sealing.verification.failed')}
      </Badge>
      <span className="text-13 tabular">{formatDateTime(verification.at)}</span>
      <span className="text-xs text-muted-strong">
        {t(`sealing.verification.source.${verification.source}`, {
          defaultValue: verification.source,
        })}
        {' · '}
        {verification.by}
        {' · '}
        {verification.deep ? t('sealing.verification.deep') : t('sealing.verification.quick')}
      </span>
    </span>
  );
}

function VerifyOutcome({ result }: { result: VerifyResult }) {
  const { t } = useTranslation();
  const lang = currentLang();
  // The core's messages are English; the API sends them translated as well (spec 12.4).
  const errors = lang === 'en' ? result.errors : result.errors_tr;
  const warnings = lang === 'en' ? result.warnings : result.warnings_tr;
  return (
    <Alert
      tone={OUTCOME_TONE[result.outcome] ?? 'info'}
      title={lang === 'en' ? result.message_en : result.message_tr}
      data-testid="verify-result"
    >
      <p className="text-xs">
        {t('sealing.verify.took', { duration: formatDuration(result.duration_ms / 1000, lang) })}
        {' · '}
        {result.deep ? t('sealing.verification.deep') : t('sealing.verification.quick')}
      </p>
      {errors.length > 0 ? (
        <ul className="mt-1 list-disc pl-4 text-xs">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      ) : null}
      {warnings.length > 0 ? (
        <p className="mt-1 text-xs">
          {t('sealing.verify.warnings', { warnings: warnings.join('; ') })}
        </p>
      ) : null}
    </Alert>
  );
}

function ManifestTokenFacts({ detail }: { detail: SealDayDetail }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const manifest = detail.manifest;
  const token = detail.token;
  return (
    <>
      {manifest ? (
        <Section title={t('sealing.manifest.title')}>
          <Facts
            items={[
              [t('sealing.manifest.path'), mono(manifest.path)],
              [t('sealing.manifest.size'), formatBytes(manifest.bytes, lang)],
              [t('sealing.manifest.created'), time(manifest.created_at)],
              [t('sealing.manifest.schema'), mono(manifest.schema ?? '—')],
              [
                t('sealing.manifest.system'),
                manifest.system
                  ? `${manifest.system.name} ${manifest.system.version} · ${manifest.system.host}`
                  : '—',
              ],
              [
                t('sealing.manifest.window'),
                manifest.window
                  ? `${time(manifest.window.from)} – ${time(manifest.window.to)}`
                  : '—',
              ],
              [
                t('sealing.manifest.late'),
                manifest.stamped_late
                  ? `${t('sealing.yes')}${manifest.late_reason ? ` (${manifest.late_reason})` : ''}`
                  : t('sealing.no'),
              ],
            ]}
          />
          <CodeBlock label={t('sealing.manifest.sha256')} code={manifest.sha256} wrap />
        </Section>
      ) : null}
      {token ? (
        <Section title={t('sealing.token.title')}>
          <div className="flex flex-wrap items-center gap-2">
            {token.check.ok === true ? (
              <Badge tone="success" icon={ShieldCheck}>
                {t('sealing.token.checkOk')}
              </Badge>
            ) : token.check.ok === false ? (
              <Badge tone="danger" icon={CircleX}>
                {t('sealing.token.checkFailed')}
              </Badge>
            ) : (
              <Badge tone="neutral">{t('sealing.token.checkUnknown')}</Badge>
            )}
            <Badge tone={LEGAL_TONE[legalGrade(token)]} icon={Stamp}>
              {t(LEGAL_LABEL[legalGrade(token)])}
            </Badge>
          </div>
          <Facts
            items={[
              [t('sealing.token.genTime'), time(token.gen_time)],
              [t('sealing.token.serial'), mono(token.serial ?? '—')],
              [t('sealing.token.tsa'), mono(token.tsa_name ?? '—')],
              [t('sealing.token.provider'), mono(token.provider ?? '—')],
              [t('sealing.token.policy'), mono(token.policy ?? '—')],
              [t('sealing.token.digest'), mono(token.digest_alg ?? '—')],
              [
                t('sealing.token.certificate'),
                `${time(token.tsa_cert_not_before)} – ${time(token.tsa_cert_not_after)}`,
              ],
              [t('sealing.token.root'), mono(token.check.root ?? '—')],
              [t('sealing.token.size'), formatBytes(token.bytes, lang)],
            ]}
          />
          {token.check.errors.length > 0 ? (
            <Alert tone={token.check.ok === false ? 'danger' : 'warning'} assertive={false}>
              {(lang === 'en' ? token.check.errors : token.check.errors_tr).join('; ')}
            </Alert>
          ) : null}
          <CodeBlock label={t('sealing.token.sha256')} code={token.sha256} wrap />
        </Section>
      ) : null}
    </>
  );
}

function FilesTable({ detail }: { detail: SealDayDetail }) {
  const { t } = useTranslation();
  const lang = currentLang();
  if (detail.files.length === 0) return null;
  return (
    <Section title={t('sealing.files.title')}>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-13">
          <caption className="sr-only">
            {t('sealing.files.caption', { day: formatDay(detail.day) })}
          </caption>
          <thead className="bg-subtle text-left text-xs text-muted-strong">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                {t('sealing.files.source')}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                {t('sealing.files.lines')}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                {t('sealing.files.size')}
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                {t('sealing.files.received')}
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                SHA-256
              </th>
            </tr>
          </thead>
          <tbody>
            {detail.files.map((file) => (
              <tr key={file.path} className="border-t border-border align-top">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  {file.source}
                  <span className="block font-mono text-xs font-normal text-muted-strong">
                    {file.sender_ips.join(', ')}
                  </span>
                </th>
                <td className="px-3 py-2 text-right tabular">
                  {formatNumber(file.lines, 0, lang)}
                </td>
                <td className="px-3 py-2 text-right tabular">
                  {formatBytes(file.bytes, lang)}
                  <span className="block text-xs text-muted-strong">
                    {t('sealing.files.raw', { size: formatBytes(file.bytes_uncompressed, lang) })}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs tabular">
                  {time(file.first_received)}
                  <br />
                  {time(file.last_received)}
                </td>
                <td className="px-3 py-2 font-mono text-xs" title={file.sha256}>
                  {shortHash(file.sha256, 16)}
                  <span className="block text-muted-strong" title={file.sha256_uncompressed}>
                    {t('sealing.files.rawHash', { hash: shortHash(file.sha256_uncompressed, 10) })}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
          {detail.totals ? (
            <tfoot>
              <tr className="border-t border-border font-medium">
                <th scope="row" className="px-3 py-2 text-left">
                  {t('sealing.files.total', { count: detail.totals.files })}
                </th>
                <td className="px-3 py-2 text-right tabular">
                  {formatNumber(detail.totals.lines, 0, lang)}
                </td>
                <td className="px-3 py-2 text-right tabular">
                  {formatBytes(detail.totals.bytes, lang)}
                </td>
                <td colSpan={2} />
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
    </Section>
  );
}

/** Day states "Şimdi mühürle" may ask for (the API checks again: 409 `seal_not_due`, …). */
const SEALABLE = new Set(['failed', 'pending']);
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/**
 * Why "Şimdi mühürle" cannot be asked for this day, or `null` when it can: only the oldest
 * closed unsealed day in state `failed` or `pending` (contract 2.9). The calendar on screen
 * (the page's query, read from the cache: the page re-renders this panel whenever it changes)
 * tells whether an earlier day still waits; days before its month are the API's to check.
 */
function sealNowBlock(detail: SealDayDetail, calendar: Calendar | undefined): string | null {
  if (detail.sealed) return 'sealing.sealNow.blocked.sealed';
  if (detail.state === 'open' || detail.state === 'future') return 'sealing.sealNow.blocked.open';
  if (!SEALABLE.has(detail.state)) return 'sealing.sealNow.blocked.notApplicable';
  const earlier = calendar?.days.find((cell) => cell.day < detail.day && SEALABLE.has(cell.state));
  return earlier ? 'sealing.sealNow.blocked.notOldest' : null;
}

function useShownCalendar(): Calendar | undefined {
  const client = useQueryClient();
  const [params] = useSearchParams();
  const month = params.get('month');
  return client.getQueryData<Calendar>(
    queryKeys.sealCalendar(month && MONTH.test(month) ? month : null),
  );
}

/** The Seal task's outcome of a "Şimdi mühürle" request. */
function SealOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = request.result as SealActionResult | null;
  if (!result) return null;
  return (
    <p className="text-13 text-foreground" data-testid="seal-now-result">
      {t(`sealing.sealNow.outcome.${result.status}`, {
        day: formatDay(result.day),
        defaultValue: result.message,
      })}
      {result.serial ? (
        <span className="block font-mono text-xs text-muted-strong">
          {t('sealing.sealNow.serial', { serial: result.serial })}
        </span>
      ) : null}
    </p>
  );
}

/**
 * "Şimdi mühürle" (`seal.manage`; contract 2.9): asks the Seal task, through the administration
 * service, to seal the oldest closed unsealed day once without waiting for its retry time. The
 * quiet window after midnight, the empty-day rule and the order of days are never bypassed.
 * The request is followed until the task reports; the calendar and the day are read again.
 */
function SealNow({ detail, calendar }: { detail: SealDayDetail; calendar: Calendar | undefined }) {
  const { t } = useTranslation();
  const id = useId();
  const client = useQueryClient();
  const sealNow = useSealNow();
  const [confirming, setConfirming] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const blocked = sealNowBlock(detail, calendar);
  const earliest = calendar?.days.find((cell) => cell.day < detail.day && SEALABLE.has(cell.state));

  async function start() {
    setProblem(null);
    try {
      const accepted = await sealNow.mutateAsync(detail.day);
      setRequestId(accepted.request_id);
      setConfirming(false);
    } catch (error) {
      setConfirming(false);
      setProblem(
        isApiError(error, ...AGENT_CODES)
          ? `${errorMessage(error)} ${t('sealing.sealNow.console')}`
          : errorMessage(error),
      );
    }
  }

  const settled = useCallback(() => {
    void client.invalidateQueries({ queryKey: queryKeys.sealsAll });
    void client.invalidateQueries({ queryKey: queryKeys.credits });
  }, [client]);

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button
          variant="secondary"
          disabled={blocked !== null}
          busy={sealNow.isPending}
          aria-describedby={`${id}-seal-now`}
          onClick={() => {
            setConfirming(true);
          }}
        >
          <Stamp aria-hidden="true" />
          {t('sealing.sealNow.button')}
        </Button>
      </div>
      <p id={`${id}-seal-now`} className="text-xs text-muted">
        {blocked
          ? t(blocked, { day: earliest ? formatDay(earliest.day) : '' })
          : t('sealing.sealNow.hint')}
      </p>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          onTerminal={settled}
          renderResult={(request) => <SealOutcome request={request} />}
        />
      ) : null}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('sealing.sealNow.confirmTitle', { day: formatDay(detail.day) })}
        description={t('sealing.sealNow.confirmBody')}
        confirmLabel={t('sealing.sealNow.button')}
        busy={sealNow.isPending}
        onConfirm={() => void start()}
      />
    </div>
  );
}

/**
 * One day of the sealing page (spec 12.3 page 9): its state, the manifest, the time-stamp token
 * (genTime, serial, TSA, certificate validity, legal grade — a red banner when the stamp is not
 * legally valid, spec 21), the files exactly as sealed, the chain link, verifications, replica;
 * "Doğrula", "Manifesti indir", "Belirteci indir", and "Şimdi mühürle" for the day that is due.
 */
export function SealDayPanel({ day }: { day: string }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const { can } = useAuth();
  const detail = useSealDay(day);
  const calendar = useShownCalendar();
  const verify = useVerifySeal();
  const [deep, setDeep] = useState(true);
  const [download, setDownload] = useState<{ busy: string | null; problem: string | null }>({
    busy: null,
    problem: null,
  });
  const data = detail.data;
  const result = verify.data ?? null;

  async function save(kind: 'manifest' | 'token') {
    setDownload({ busy: kind, problem: null });
    try {
      const file =
        kind === 'manifest'
          ? await apiDownload(`/seals/${day}/manifest`, { fallbackName: `manifest-${day}.json` })
          : await apiDownload(`/seals/${day}/token`, { fallbackName: `manifest-${day}.json.zd` });
      saveBlob(file.blob, file.filename);
      setDownload({ busy: null, problem: null });
    } catch (error) {
      setDownload({ busy: null, problem: errorMessage(error) });
    }
  }

  if (detail.isPending)
    return (
      <Card className="flex flex-col gap-3 p-4" role="status" aria-label={t('common.loading')}>
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40" />
      </Card>
    );
  if (detail.isError)
    return (
      <Card className="p-4">
        <Alert tone="danger">{errorMessage(detail.error)}</Alert>
      </Card>
    );
  if (!data) return null;

  const message = lang === 'en' ? data.message_en : data.message_tr;
  const label = lang === 'en' ? data.label_en : data.label_tr;
  const previous = data.previous;
  const PreviousIcon: LucideIcon = previous?.matches === false ? Unlink : Link2;
  return (
    <Card className="flex flex-col gap-5 p-4" aria-labelledby={`${id}-title`}>
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={`${id}-title`} className="text-base font-semibold text-foreground">
            {t('sealing.day.title', { day: formatDay(day) })}
          </h2>
          <Badge tone={toneOf(data.tone)}>{label}</Badge>
          {data.overdue ? <Badge tone="danger">{t('sealing.day.overdue')}</Badge> : null}
        </div>
        {data.state === 'verified' ? (
          <Alert tone="success" assertive={false}>
            {t('sealing.day.sealedOk', { day: formatDay(day) })}
          </Alert>
        ) : message ? (
          <Alert
            tone={data.tone === 'error' ? 'danger' : data.tone === 'warning' ? 'warning' : 'info'}
            assertive={false}
          >
            {message}
          </Alert>
        ) : null}
        {data.token && legalGrade(data.token) === 'test' ? (
          <Alert tone="danger" title={t('legal.notLegalTitle')} data-testid="not-legal-grade">
            {t('sealing.day.notLegal', { provider: data.token.provider ?? '—' })}
          </Alert>
        ) : null}
        {data.token && legalGrade(data.token) === 'claimed' ? (
          <Alert tone="info" assertive={false} data-testid="legal-root-unconfirmed">
            {t('sealing.day.rootUnconfirmed', { provider: data.token.provider ?? '—' })}
          </Alert>
        ) : null}
        {data.problems.length > 0 ? (
          <Alert tone="warning" assertive={false} title={t('sealing.problems')}>
            {data.problems.join('; ')}
          </Alert>
        ) : null}
      </header>

      <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center gap-2">
          {can('seal.verify') ? (
            <>
              <Button
                onClick={() => {
                  verify.mutate({ day, deep });
                }}
                disabled={!data.sealed}
                busy={verify.isPending}
              >
                <ShieldCheck aria-hidden="true" />
                {verify.isPending ? t('sealing.verify.running') : t('sealing.verify.button')}
              </Button>
              <div className="flex items-center gap-2">
                <Checkbox
                  id={`${id}-deep`}
                  checked={deep}
                  onCheckedChange={(value) => {
                    setDeep(value === true);
                  }}
                />
                <Label htmlFor={`${id}-deep`} className="font-normal">
                  {t('sealing.verify.deep')}
                </Label>
              </div>
            </>
          ) : null}
          <Button
            variant="secondary"
            disabled={!data.sealed}
            busy={download.busy !== null}
            onClick={() => void save('manifest')}
          >
            <FileDown aria-hidden="true" />
            {t('sealing.downloadManifest')}
          </Button>
          <Button
            variant="secondary"
            disabled={!data.sealed}
            busy={download.busy !== null}
            onClick={() => void save('token')}
          >
            <Stamp aria-hidden="true" />
            {t('sealing.downloadToken')}
          </Button>
        </div>
        {verify.isPending ? (
          <p className="text-xs text-muted" role="status">
            {t('sealing.verify.wait')}
          </p>
        ) : null}
        {result ? <VerifyOutcome result={result} /> : null}
        {verify.isError ? <Alert tone="danger">{errorMessage(verify.error)}</Alert> : null}
        {download.problem ? <Alert tone="danger">{download.problem}</Alert> : null}
        {can('seal.manage') ? <SealNow detail={data} calendar={calendar} /> : null}
      </div>

      {data.seal_state ? (
        <Section title={t('sealing.state.title')}>
          <Facts
            items={[
              [
                t('sealing.state.status'),
                data.seal_state.status
                  ? t(`sealing.state.states.${data.seal_state.status}`, {
                      defaultValue: data.seal_state.status,
                    })
                  : '—',
              ],
              [t('sealing.state.attempts'), String(data.seal_state.attempts ?? 0)],
              [t('sealing.state.lastAttempt'), time(data.seal_state.last_attempt_at)],
              [t('sealing.state.sealedAt'), time(data.seal_state.sealed_at)],
              [t('sealing.state.nextAttempt'), time(data.seal_state.next_attempt_at)],
              [
                t('sealing.state.lastError'),
                (lang === 'en' ? null : data.seal_state.last_error_tr) ??
                  data.seal_state.last_error ??
                  '—',
              ],
              ...(data.seal_state.last_error && lang !== 'en'
                ? [[t('sealing.state.technical'), data.seal_state.last_error] as [string, string]]
                : []),
            ]}
          />
        </Section>
      ) : null}

      {data.deleted ? (
        <Section title={t('sealing.deleted.title')}>
          <Facts
            items={[
              [t('sealing.deleted.record'), mono(data.deleted.record)],
              [t('sealing.deleted.created'), time(data.deleted.created_at)],
              [t('sealing.token.serial'), mono(data.deleted.serial ?? '—')],
            ]}
          />
        </Section>
      ) : null}

      <ManifestTokenFacts detail={data} />
      <FilesTable detail={data} />

      {previous ? (
        <Section title={t('sealing.chain.title')}>
          <p className="flex items-center gap-2 text-13">
            <PreviousIcon aria-hidden="true" className="size-4 text-muted-strong" />
            {previous.matches === true
              ? t('sealing.chain.matches', { day: formatDay(previous.day ?? '') })
              : previous.matches === false
                ? t('sealing.chain.broken', { day: formatDay(previous.day ?? '') })
                : t('sealing.chain.unknown', { day: formatDay(previous.day ?? '') })}
          </p>
          <Facts
            items={[
              [t('sealing.chain.manifest'), mono(previous.manifest_sha256 ?? '—')],
              [t('sealing.chain.token'), mono(previous.token_sha256 ?? '—')],
              [
                t('sealing.chain.target'),
                previous.target ? t(`sealing.chain.targets.${previous.target}`) : '—',
              ],
            ]}
          />
        </Section>
      ) : data.manifest ? (
        <Section title={t('sealing.chain.title')}>
          <p className="text-13 text-muted-strong">{t('sealing.chain.start')}</p>
        </Section>
      ) : null}

      <Section title={t('sealing.verification.title')}>
        {data.verification ? (
          <VerificationLine verification={data.verification} />
        ) : (
          <p className="text-13 text-muted-strong">{t('sealing.verification.none')}</p>
        )}
        {data.verifications.on_demand &&
        data.verifications.on_demand.outcome !== 'ok' &&
        data.verifications.on_demand.outcome !== 'failed' ? (
          <p className="text-xs text-muted-strong">
            {t('sealing.verification.inconclusive', {
              time: formatDateTime(data.verifications.on_demand.at),
              outcome: t(`sealing.verification.outcomes.${data.verifications.on_demand.outcome}`, {
                defaultValue: data.verifications.on_demand.outcome,
              }),
            })}
          </p>
        ) : null}
        {data.verification && data.verification.errors.length > 0 ? (
          <Alert tone="danger" assertive={false}>
            {(lang === 'en' ? data.verification.errors : data.verification.errors_tr).join('; ')}
          </Alert>
        ) : null}
      </Section>

      {data.replica ? (
        <Section title={t('sealing.replica.title')}>
          <Facts
            items={[
              [
                t('sealing.replica.status'),
                t(`sealing.replica.states.${data.replica.status}`, {
                  defaultValue: data.replica.status,
                }),
              ],
              [t('sealing.replica.verifiedAt'), time(data.replica.verified_at)],
              [t('sealing.replica.target'), mono(data.replica.target ?? '—')],
              [
                t('sealing.replica.files'),
                data.replica.files === null || data.replica.files === undefined
                  ? '—'
                  : String(data.replica.files),
              ],
              ...(data.replica.message
                ? ([[t('sealing.replica.message'), mono(data.replica.message)]] as [
                    string,
                    ReactNode,
                  ][])
                : []),
            ]}
          />
        </Section>
      ) : null}

      {data.gaps.length > 0 ? (
        <Section title={t('sealing.gaps.title')}>
          <ul className="flex flex-col gap-1 text-13">
            {data.gaps.map((gap) => {
              const reason = gapReason(gap.reason);
              return (
                <li key={`${gap.source}-${gap.from}`}>
                  <span className="font-medium">{gap.source}</span>: {time(gap.from)} –{' '}
                  {time(gap.to)}{' '}
                  <span className="text-muted-strong" title={gap.reason}>
                    ({reason ? t(reason.key, { minutes: reason.minutes }) : gap.reason})
                  </span>
                </li>
              );
            })}
          </ul>
        </Section>
      ) : null}

      {data.clock ? (
        <Section title={t('sealing.clock.title')}>
          <Facts
            items={[
              [t('sealing.clock.server'), mono(data.clock.ntp_server ?? '—')],
              [t('sealing.clock.samples'), String(data.clock.samples)],
              [
                t('sealing.clock.maxOffset'),
                data.clock.max_offset_us === null
                  ? '—'
                  : t('sealing.clock.ms', {
                      value: formatNumber(data.clock.max_offset_us / 1000, 1, lang),
                    }),
              ],
            ]}
          />
        </Section>
      ) : null}
    </Card>
  );
}
