import { Download, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { apiDownload, errorMessage } from '@/lib/api';
import { useReportRun } from '@/lib/api-hooks';
import type { ReportFile, RunOut } from '@/lib/api-types';
import { shortHash } from '@/lib/display';
import { checkDownload, saveBlob } from '@/lib/download';
import { formatBytes, formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { Facts } from '@/pages/devices/device-parts';

import { engineMissing, reportProblem, retryPending, shownStatus } from './report-errors';
import { DeliveryBadge, RunStatusBadge } from './report-parts';
import { kindLabel, periodText } from './schedule-text';

const TH =
  'border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong';
const TD = 'border-b border-border px-3 py-1.5';

/**
 * Download one file of a run (audited `report.download`) and check it against its SHA-256. The
 * API refuses a file deleted after `reports.keep_days` (410 `report_expired`), missing or changed
 * on the disk (409 `report_missing` / `report_damaged`): its words are shown.
 */
function FileRow({ run, file }: { run: RunOut; file: ReportFile }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true);
    try {
      const received = await apiDownload(
        `/reports/runs/${run.id}/files/${encodeURIComponent(file.name)}`,
        { fallbackName: file.name },
      );
      const check = await checkDownload(received.blob, file.sha256);
      saveBlob(received.blob, received.filename);
      if (check.state === 'mismatch') toast({ title: t('download.mismatch'), variant: 'danger' });
    } catch (error) {
      toast({ title: reportProblem(error, t).text, variant: 'danger' });
    } finally {
      setBusy(false);
    }
  }
  return (
    <tr>
      <td className={`${TD} break-all`}>{file.name}</td>
      <td className={`${TD} uppercase`}>{file.format}</td>
      <td className={`${TD} tabular`}>{formatBytes(file.size, lang)}</td>
      <td className={`${TD} font-mono text-xs`} title={file.sha256}>
        {shortHash(file.sha256)}
      </td>
      <td className={TD}>
        <Button
          size="sm"
          variant="secondary"
          busy={busy}
          aria-label={t('reports.runs.downloadFile', { name: file.name })}
          onClick={() => void download()}
        >
          <Download aria-hidden="true" />
          {t('reports.runs.download')}
        </Button>
      </td>
    </tr>
  );
}

/**
 * One run: period, state, who started it, every recipient's delivery (the ones tried so far; a
 * failed one is retried while the run is running) and the files.
 */
export function RunSheet({
  runId,
  nameOf,
  onClose,
}: {
  runId: number | null;
  /** Name of the run's schedule (`null`: a run without one). */
  nameOf: (run: Pick<RunOut, 'schedule_id'>) => string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const detail = useReportRun(runId);
  const run = detail.data;
  const scheduleName = run ? nameOf(run) : null;
  return (
    <Sheet
      open={runId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {t('reports.runs.detailTitle', { id: runId ?? '' })}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {scheduleName ?? (run ? kindLabel(run.kind, t) : '')}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-5">
          {detail.isPending ? (
            <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
              <Skeleton className="h-24" />
              <Skeleton className="h-32" />
            </div>
          ) : detail.isError ? (
            <Alert tone="danger">
              <span className="flex flex-wrap items-center gap-2">
                {errorMessage(detail.error)}
                <Button size="sm" variant="secondary" onClick={() => void detail.refresh()}>
                  <RefreshCw aria-hidden="true" />
                  {t('common.retry')}
                </Button>
              </span>
            </Alert>
          ) : run ? (
            <>
              <Facts
                items={[
                  [t('reports.runs.columns.status'), <RunStatusBadge status={shownStatus(run)} />],
                  [t('reports.runs.columns.kind'), kindLabel(run.kind, t)],
                  [t('reports.runs.columns.period'), periodText(run.period.from, run.period.to)],
                  [t('reports.runs.started'), formatDateTime(run.started_at)],
                  [
                    t('reports.runs.finished'),
                    run.finished_at ? formatDateTime(run.finished_at) : '—',
                  ],
                  [
                    t('reports.runs.columns.trigger'),
                    run.triggered_by === 'manual'
                      ? t('reports.runs.manualBy', { user: run.requested_by ?? '—' })
                      : t('reports.runs.scheduled'),
                  ],
                ]}
              />
              {engineMissing(run) ? (
                <Alert
                  tone="warning"
                  assertive={false}
                  title={t('reports.errors.engineTitle')}
                  data-testid="run-engine-missing"
                >
                  {t('reports.errors.engineRun')}
                </Alert>
              ) : run.error ? (
                <Alert tone="danger" assertive={false} title={t('reports.runs.error')}>
                  {run.error}
                </Alert>
              ) : null}
              <section className="flex flex-col gap-2" aria-labelledby="run-deliveries">
                <h3 id="run-deliveries" className="text-sm font-semibold text-foreground">
                  {t('reports.runs.deliveries')}
                </h3>
                {run.deliveries.length === 0 ? (
                  <p className="text-13 text-muted">{t('reports.runs.noDeliveries')}</p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full border-separate border-spacing-0 text-13">
                      <caption className="sr-only">{t('reports.runs.deliveries')}</caption>
                      <thead>
                        <tr>
                          <th scope="col" className={TH}>
                            {t('reports.runs.recipient')}
                          </th>
                          <th scope="col" className={TH}>
                            {t('reports.runs.columns.status')}
                          </th>
                          <th scope="col" className={TH}>
                            {t('reports.runs.sentAt')}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {run.deliveries.map((delivery) => (
                          <tr key={delivery.recipient}>
                            <td className={`${TD} break-all`}>{delivery.recipient}</td>
                            <td className={TD}>
                              <span className="flex flex-col items-start gap-1">
                                <DeliveryBadge status={delivery.status} />
                                {delivery.error ? (
                                  <span className="text-xs text-danger-ink">{delivery.error}</span>
                                ) : null}
                                {retryPending(run, delivery) ? (
                                  <span className="text-xs text-muted-strong">
                                    {t('reports.runs.retryPending')}
                                  </span>
                                ) : null}
                              </span>
                            </td>
                            <td className={`${TD} tabular`}>
                              {delivery.sent_at ? formatDateTime(delivery.sent_at) : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
              <section className="flex flex-col gap-2" aria-labelledby="run-files">
                <h3 id="run-files" className="text-sm font-semibold text-foreground">
                  {t('reports.runs.files')}
                </h3>
                {run.files.length === 0 ? (
                  <p className="text-13 text-muted">{t('reports.runs.noFiles')}</p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full border-separate border-spacing-0 text-13">
                      <caption className="sr-only">{t('reports.runs.files')}</caption>
                      <thead>
                        <tr>
                          {(['name', 'format', 'size', 'sha256', 'action'] as const).map(
                            (column) => (
                              <th key={column} scope="col" className={TH}>
                                {t(`reports.runs.fileColumns.${column}`)}
                              </th>
                            ),
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {run.files.map((file) => (
                          <FileRow key={file.name} run={run} file={file} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="text-xs text-muted">{t('reports.runs.filesNote')}</p>
              </section>
            </>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
