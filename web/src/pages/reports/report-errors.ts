/**
 * The refusals of the report API in words (no components here; unit-tested in
 * `report-errors.test.ts`). The server's own texts name its parameters (`send=false`); the page
 * says what to do on the page instead and where e-mail is set up.
 */

import type { TFunction } from 'i18next';

import { errorMessage, isApiError } from '@/lib/api';
import type { ReportRunStatus, RunOut } from '@/lib/api-types';

export type ReportProblemKind = 'engine' | 'smtp' | 'recipients' | 'other';

export interface ReportProblem {
  kind: ReportProblemKind;
  title?: string;
  text: string;
}

/**
 * 503 `usage_engine_unavailable` (preview, until the usage engine is installed), 409
 * `report_smtp_disabled` (e-mail switched off on the server), 422 `report_recipients_required`
 * (switching on or sending without recipients); anything else as the API words it.
 */
export function reportProblem(error: unknown, t: TFunction): ReportProblem {
  if (isApiError(error, 'usage_engine_unavailable'))
    return {
      kind: 'engine',
      title: t('reports.errors.engineTitle'),
      text: t('reports.errors.engine'),
    };
  if (isApiError(error, 'report_smtp_disabled'))
    return { kind: 'smtp', title: t('reports.errors.smtpTitle'), text: t('reports.errors.smtp') };
  if (isApiError(error, 'report_recipients_required'))
    return { kind: 'recipients', text: t('reports.errors.recipients') };
  return { kind: 'other', text: errorMessage(error) };
}

/**
 * A run the worker could not make because the usage engine is missing (its error starts with the
 * runner's `kullanım motoru yok / usage engine not installed`).
 */
export function engineMissing(run: Pick<RunOut, 'status' | 'error'>): boolean {
  return run.status === 'failed' && (run.error ?? '').startsWith('kullanım motoru yok');
}

/** The state shown for a run: a finished run that e-mailed nobody is "Tamamlandı", not "Gönderildi". */
export type RunShownStatus = ReportRunStatus | 'done';

export function shownStatus(run: Pick<RunOut, 'status' | 'deliveries'>): RunShownStatus {
  if (run.status === 'sent' && !run.deliveries.some((item) => item.status === 'sent'))
    return 'done';
  return run.status;
}

/** A delivery waiting for its retry: the API shows it `failed` while the run is still `running`. */
export function retryPending(
  run: Pick<RunOut, 'status'>,
  delivery: RunOut['deliveries'][number],
): boolean {
  return run.status === 'running' && delivery.status === 'failed';
}
