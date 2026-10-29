import { FileDown, ListChecks, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { ComplianceStatus } from '@/lib/compliance-types';
import { formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ComplianceCardView } from './compliance-card';
import {
  canConfirmBackup,
  cardLinks,
  showWizardLink,
  summaryFigures,
  toneCounts,
  TONE_STYLE,
  WIZARD_PATH,
} from './compliance-model';

export interface ComplianceViewProps {
  /** `GET /compliance`; `undefined` until the first answer. */
  status: ComplianceStatus | undefined;
  /** The last request's error; the answer shown before it stays (stale data). */
  error: unknown;
  /** No answer yet and the first request is under way. */
  loading: boolean;
  /** When the shown answer arrived (ms since the epoch; 0: never). */
  updatedAt: number;
  onRefresh: () => void;
  /** "Uyum Durumu Raporu (PDF)" (`GET /compliance/report.pdf`). */
  onExport: () => void;
  exporting: boolean;
  /** `POST /compliance/config-backup`; resolves once it is recorded, rejects with its error. */
  onConfirmBackup: () => Promise<unknown>;
  confirming: boolean;
}

/**
 * Uyum Durumu (`/compliance`, `compliance.read`; Wave 4 contract 2.8.1): the ten technical checks
 * of log collection, sealing and backups as the server words them, the notice that they are not a
 * legal assessment, the summary and the Uyum Durumu Raporu. The view fetches nothing: the page of
 * W4-F gives it the answer and the actions. Loading, error ("Tekrar dene") and stale data follow
 * Kaynaklar: a failed refresh keeps the cards shown under the error.
 */
export function ComplianceView({
  status,
  error,
  loading,
  updatedAt,
  onRefresh,
  onExport,
  exporting,
  onConfirmBackup,
  confirming,
}: ComplianceViewProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { permissions } = useAuth();
  const figures = status ? summaryFigures(status.counts) : null;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.compliance')}
        description={t('compliance.description')}
        actions={
          <>
            {updatedAt > 0 ? (
              <span className="text-13 text-muted tabular" aria-live="polite">
                {t('compliance.updated', { time: formatTime(updatedAt, true) })}
              </span>
            ) : null}
            <Button variant="secondary" size="sm" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('compliance.refresh')}
            </Button>
            <Button variant="secondary" size="sm" busy={exporting} onClick={onExport}>
              <FileDown aria-hidden="true" />
              {t('compliance.export')}
            </Button>
          </>
        }
      />

      {error ? (
        <Alert tone="danger" title={t('compliance.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(error)}
            <Button size="sm" variant="secondary" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}

      {status && figures ? (
        <>
          <Alert tone="info" assertive={false} data-testid="compliance-notice">
            {lang === 'en' ? status.notice_en : status.notice_tr}
          </Alert>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p
              className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
              data-testid="compliance-summary"
            >
              <span className="font-medium text-foreground tabular">
                {t('compliance.summary', {
                  ok: formatNumber(figures.ok, 0, lang),
                  total: formatNumber(figures.total, 0, lang),
                })}
              </span>
              {toneCounts(status.counts)
                .filter((item) => item.tone !== 'ok')
                .map(({ tone, count }) => (
                  <Badge key={tone} tone={TONE_STYLE[tone].tone} icon={TONE_STYLE[tone].icon}>
                    {t('compliance.toneCount', {
                      tone: t(TONE_STYLE[tone].label),
                      count: formatNumber(count, 0, lang),
                    })}
                  </Badge>
                ))}
            </p>
            {showWizardLink(status, permissions) ? (
              <Button variant="secondary" size="sm" asChild>
                <Link to={WIZARD_PATH}>
                  <ListChecks aria-hidden="true" />
                  {t('compliance.openWizard')}
                </Link>
              </Button>
            ) : null}
          </div>
          <ol className="grid gap-4 lg:grid-cols-2" aria-label={t('compliance.cardsLabel')}>
            {status.cards.map((card) => (
              <li key={card.id} className="min-w-0">
                <ComplianceCardView
                  card={card}
                  links={cardLinks(card, permissions)}
                  {...(canConfirmBackup(card, permissions) ? { onConfirmBackup, confirming } : {})}
                />
              </li>
            ))}
          </ol>
        </>
      ) : loading ? (
        <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
          <Skeleton className="h-14" />
          <Skeleton className="h-6 w-64" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      ) : null}
    </div>
  );
}
