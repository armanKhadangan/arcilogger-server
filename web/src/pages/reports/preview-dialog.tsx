import { Code2, ExternalLink } from 'lucide-react';
import { useId, useState } from 'react';
import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { api, apiDownload, type QueryValue } from '@/lib/api';
import type { ScheduleOut } from '@/lib/api-types';
import { saveBlob } from '@/lib/download';
import { focusFirstError, type FieldErrors } from '@/lib/validation';

import { PeriodChoice } from './period-fields';
import { ReportProblemAlert } from './report-parts';
import { reportProblem, type ReportProblem } from './report-errors';
import { checkPeriod, EMPTY_PERIOD, type PeriodField, type PeriodValues } from './schedule-model';
import { ownPeriodText } from './schedule-text';

/** How long the PDF's object URL stays valid in the new tab (then it is released). */
const PDF_URL_MS = 10 * 60_000;

/**
 * "Önizle": `GET /reports/preview` renders the schedule's report for its own period (the API's,
 * as "run now") or a chosen one without storing or sending it — the PDF in a new browser tab, the
 * e-mail (HTML) here in a sandboxed frame (no scripts, no access to this page). Until the usage
 * engine is installed the API answers 503 `usage_engine_unavailable`: said so in words.
 */
export function PreviewDialog({
  schedule,
  onOpenChange,
  returnFocus,
}: {
  schedule: ScheduleOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [custom, setCustom] = useState(false);
  const [period, setPeriod] = useState<PeriodValues>(EMPTY_PERIOD);
  const [errors, setErrors] = useState<FieldErrors<PeriodField>>({});
  const [busy, setBusy] = useState<'pdf' | 'html' | null>(null);
  const [html, setHtml] = useState<string | null>(null);
  const [failure, setFailure] = useState<ReportProblem | null>(null);
  const open = schedule !== null;

  // The form begins anew while rendering when the dialog opens, so its first frame never shows the
  // previous period or preview: an effect would run only after that frame.
  const [wasOpen, setWasOpen] = useState(false);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setCustom(false);
      setPeriod(EMPTY_PERIOD);
      setErrors({});
      setHtml(null);
      setFailure(null);
    }
  }

  /** The query of the preview, `null` (with the errors shown) when the custom period is wrong. */
  function params(format: 'pdf' | 'html'): Record<string, QueryValue> | null {
    if (!schedule) return null;
    const query: Record<string, QueryValue> = {
      kind: schedule.kind,
      format,
      person_level: schedule.person_level,
      pseudonymize: schedule.pseudonymize,
    };
    if (custom) {
      const checked = checkPeriod(period, new Date());
      setErrors(checked.errors);
      if (!checked.from || !checked.to) {
        focusFirstError(checked.errors, (field) => `${id}-${field}`);
        return null;
      }
      query.from = checked.from;
      query.to = checked.to;
    }
    return query;
  }

  async function openPdf() {
    const query = params('pdf');
    if (!query) return;
    setFailure(null);
    // Opened at once (still inside the click): a tab opened after the wait would be blocked.
    const tab = window.open('', '_blank');
    setBusy('pdf');
    try {
      const file = await apiDownload('/reports/preview', {
        params: query,
        fallbackName: 'rapor-onizleme.pdf',
      });
      if (tab && !tab.closed) {
        const url = URL.createObjectURL(file.blob);
        tab.location.href = url;
        window.setTimeout(() => {
          URL.revokeObjectURL(url);
        }, PDF_URL_MS);
      } else {
        // The browser blocked the new tab: the file is offered as a download instead.
        saveBlob(file.blob, file.filename);
      }
    } catch (error) {
      tab?.close();
      setFailure(reportProblem(error, t));
    } finally {
      setBusy(null);
    }
  }

  async function showHtml() {
    const query = params('html');
    if (!query) return;
    setFailure(null);
    setBusy('html');
    try {
      setHtml(await api.get<string>('/reports/preview', { params: query }));
    } catch (error) {
      setHtml(null);
      setFailure(reportProblem(error, t));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus} className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t('reports.preview.title', { name: schedule?.name ?? '' })}</DialogTitle>
          <DialogDescription>{t('reports.preview.description')}</DialogDescription>
        </DialogHeader>
        {failure ? <ReportProblemAlert problem={failure} /> : null}
        {schedule ? (
          <PeriodChoice
            id={id}
            custom={custom}
            onCustomChange={setCustom}
            defaultText={ownPeriodText(schedule.kind, new Date(), t)}
            values={period}
            onChange={setPeriod}
            errors={errors}
          />
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button busy={busy === 'pdf'} onClick={() => void openPdf()}>
            <ExternalLink aria-hidden="true" />
            {busy === 'pdf' ? t('common.working') : t('reports.preview.pdf')}
          </Button>
          <Button variant="secondary" busy={busy === 'html'} onClick={() => void showHtml()}>
            <Code2 aria-hidden="true" />
            {busy === 'html' ? t('common.working') : t('reports.preview.html')}
          </Button>
        </div>
        {html !== null ? (
          <iframe
            title={t('reports.preview.frame')}
            sandbox=""
            srcDoc={html}
            className="h-[60vh] w-full shrink-0 rounded-lg border border-border bg-white"
            data-testid="preview-frame"
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
