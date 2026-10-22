import { useQueryClient } from '@tanstack/react-query';
import { FileDown, Package, Stamp } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { HashCheckBadge } from '@/components/evidence/hash-check-badge';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { CodeBlock } from '@/components/ui/code-block';
import { apiDownload, errorMessage } from '@/lib/api';
import { queryKeys } from '@/lib/api-hooks';
import type { LookupOut, ReportOut } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { checkDownload, saveBlob, type HashCheck } from '@/lib/download';
import { formatBytes, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { timestampOf } from '@/lib/lookup-types';

/** Time-stamp status of the stored report (spec 9.5: optionally stamped, 1 kontör). */
function ReportTimestampInfo({ report }: { report: ReportOut }) {
  const { t } = useTranslation();
  const stamp = timestampOf(report) ?? {};
  if (report.timestamp_status === 'not_requested')
    return (
      <Alert tone="info" assertive={false}>
        {t('lookup.report.timestamp.notRequested')}
      </Alert>
    );
  if (report.timestamp_status === 'failed')
    return (
      <Alert tone="warning" assertive={false} title={t('lookup.report.timestamp.failedTitle')}>
        {stamp.error ?? stamp.errors?.join('; ') ?? '—'}
      </Alert>
    );
  return (
    <div className="flex flex-col gap-2">
      {stamp.legal_grade ? null : (
        <Alert tone="danger" title={t('legal.notLegalTitle')} data-testid="not-legal-grade">
          {t('lookup.report.timestamp.notLegal', { provider: stamp.provider ?? '—' })}
        </Alert>
      )}
      <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-13">
        <dt className="text-muted">{t('lookup.report.timestamp.status')}</dt>
        <dd>
          <Badge tone={stamp.verified ? 'success' : 'danger'} icon={Stamp}>
            {stamp.verified
              ? t('lookup.report.timestamp.stampedVerified')
              : t('lookup.report.timestamp.stampedUnverified')}
          </Badge>
        </dd>
        <dt className="text-muted">{t('lookup.report.timestamp.provider')}</dt>
        <dd className="font-mono text-xs">
          {stamp.provider ?? '—'}
          {stamp.server ? ` · ${stamp.server}` : ''}
        </dd>
        <dt className="text-muted">{t('lookup.report.timestamp.genTime')}</dt>
        <dd className="tabular">{stamp.gen_time ? formatDateTime(stamp.gen_time) : '—'}</dd>
        <dt className="text-muted">{t('lookup.report.timestamp.serial')}</dt>
        <dd className="font-mono text-xs break-all">{stamp.serial ?? '—'}</dd>
        <dt className="text-muted">{t('lookup.report.timestamp.tsa')}</dt>
        <dd className="font-mono text-xs break-all">{stamp.tsa_name ?? '—'}</dd>
      </dl>
    </div>
  );
}

/**
 * Bottom actions of the lookup page: "Resmi Rapor (PDF)" — generated on the first download, the
 * same bytes afterwards; its SHA-256 ("imza özeti") and time stamp are shown after the download —
 * and "Kanıt Paketi" (only with `evidence.create`), which opens the package wizard for this
 * lookup.
 */
export function ReportPanel({ lookup }: { lookup: LookupOut }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [busy, setBusy] = useState<'pdf' | 'zd' | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [check, setCheck] = useState<{ sha256: string | null; result: HashCheck } | null>(null);
  const report = lookup.report;

  async function download(kind: 'pdf' | 'zd') {
    setBusy(kind);
    setProblem(null);
    try {
      const file =
        kind === 'pdf'
          ? await apiDownload(`/lookup/${lookup.id}/report.pdf`, {
              fallbackName: `ARCILOGGER-5651-rapor-${lookup.id}.pdf`,
            })
          : await apiDownload(`/lookup/${lookup.id}/report.pdf.zd`, {
              fallbackName: `ARCILOGGER-5651-rapor-${lookup.id}.pdf.zd`,
            });
      saveBlob(file.blob, file.filename);
      if (kind === 'pdf') {
        const announced = file.headers.get('X-Arci-Sha256');
        setCheck({ sha256: announced, result: await checkDownload(file.blob, announced) });
        if (report === null) {
          // The first download stored the report (hash, pages, time stamp): read the lookup
          // once more to show it. Later downloads change only the counter — no new audited read
          // (`GET /lookup/{id}` is recorded as `lookup.view` every time).
          await client.invalidateQueries({ queryKey: queryKeys.lookup(lookup.id) });
          void client.invalidateQueries({ queryKey: queryKeys.lookupLists });
        } else {
          client.setQueryData<LookupOut>(queryKeys.lookup(lookup.id), (current) =>
            current?.report
              ? {
                  ...current,
                  report: { ...current.report, download_count: current.report.download_count + 1 },
                }
              : current,
          );
        }
      }
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  const sha256 = report?.sha256 ?? check?.sha256 ?? null;
  return (
    <Card className="flex flex-col gap-4 p-4" aria-labelledby={`report-${lookup.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={`report-${lookup.id}`} className="text-sm font-semibold text-foreground">
          {t('lookup.report.title')}
        </h2>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => void download('pdf')}
            disabled={lookup.outcome === null}
            busy={busy !== null}
          >
            <FileDown aria-hidden="true" />
            {busy === 'pdf' ? t('lookup.report.preparing') : t('lookup.report.download')}
          </Button>
          {report?.timestamp_status === 'stamped' ? (
            <Button variant="secondary" onClick={() => void download('zd')} busy={busy !== null}>
              <Stamp aria-hidden="true" />
              {t('lookup.report.downloadToken')}
            </Button>
          ) : null}
          {can('evidence.create') ? (
            <Button
              variant="secondary"
              onClick={() => void navigate(`/evidence?lookup=${lookup.id}`)}
            >
              <Package aria-hidden="true" />
              {t('lookup.report.evidence')}
            </Button>
          ) : null}
        </div>
      </div>
      <p className="text-13 text-muted">{t('lookup.report.note')}</p>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {sha256 ? (
        <section className="flex flex-col gap-2" aria-label={t('lookup.report.hashTitle')}>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-13 font-semibold text-foreground">
              {t('lookup.report.hashTitle')}
            </h3>
            {check ? <HashCheckBadge check={check.result} /> : null}
          </div>
          <CodeBlock label={t('lookup.report.hashLabel')} code={sha256} wrap />
          <p className="text-xs text-muted">{t('lookup.report.hashHint')}</p>
        </section>
      ) : null}
      {report ? (
        <>
          <p className="text-xs text-muted-strong tabular">
            {t('lookup.report.facts', {
              time: formatDateTime(report.created_at),
              user: report.username,
              pages: formatNumber(report.pages, 0, lang),
              size: formatBytes(report.size, lang),
              downloads: formatNumber(report.download_count, 0, lang),
              version: report.version,
            })}
          </p>
          <ReportTimestampInfo report={report} />
        </>
      ) : null}
    </Card>
  );
}
