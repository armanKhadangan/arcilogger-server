import { Activity, Clock3, Search, ShieldAlert } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { SEVERITY_STYLE } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import type { AlertSummary } from '@/lib/api-types';
import { formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { ALERT_SEVERITIES } from './alerts-model';

const SEVERITY_TILE: Record<string, string> = {
  critical: 'bg-danger-soft text-danger-ink ring-1 ring-danger',
  high: 'bg-danger-soft text-danger-ink',
  medium: 'bg-warning-soft text-warning-ink',
  low: 'bg-subtle text-foreground',
  info: 'bg-info-soft text-info-ink',
};

function Figure({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="flex items-center gap-1.5 text-13 text-muted">
        <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} />
        {label}
      </dt>
      <dd className="text-xl font-semibold text-foreground tabular">{value}</dd>
    </div>
  );
}

/**
 * "Güvenlik Özeti" (spec 11): open alerts by severity, open and being investigated, and the mean
 * time to close over the last 30 days.
 */
export function SecuritySummary({
  summary,
  pending,
  error,
}: {
  summary: AlertSummary | undefined;
  pending: boolean;
  error: unknown;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const open = summary
    ? ALERT_SEVERITIES.reduce((sum, severity) => sum + summary.open_by_severity[severity], 0)
    : 0;
  return (
    <Card className="flex flex-col gap-4 p-5" data-testid="security-summary">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <ShieldAlert aria-hidden="true" className="size-4 text-muted-strong" />
        {t('security.summary.title')}
      </h2>
      {pending ? (
        <Skeleton className="h-20" />
      ) : error ? (
        <Alert tone="danger">{errorMessage(error)}</Alert>
      ) : summary ? (
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <ul className="flex flex-wrap gap-2" aria-label={t('security.summary.bySeverity')}>
            {ALERT_SEVERITIES.map((severity) => {
              const Icon = SEVERITY_STYLE[severity].icon;
              const count = summary.open_by_severity[severity];
              return (
                <li
                  key={severity}
                  className={cn(
                    'flex min-w-24 flex-col gap-0.5 rounded-lg px-3 py-2',
                    count > 0 ? SEVERITY_TILE[severity] : 'bg-subtle text-muted-strong',
                  )}
                >
                  <span className="flex items-center gap-1.5 text-xs font-medium">
                    <Icon aria-hidden="true" className="size-3.5" />
                    {t(`severity.${severity}`)}
                  </span>
                  <span className="text-lg font-semibold tabular">
                    {formatNumber(count, 0, lang)}
                  </span>
                </li>
              );
            })}
          </ul>
          <dl className="grid grid-cols-3 gap-6">
            <Figure
              icon={Activity}
              label={t('security.summary.open')}
              value={formatNumber(open, 0, lang)}
            />
            <Figure
              icon={Search}
              label={t('security.summary.acknowledged')}
              value={formatNumber(summary.acknowledged, 0, lang)}
            />
            <Figure
              icon={Clock3}
              label={t('security.summary.meanTimeToClose')}
              value={
                summary.mean_time_to_close_seconds_30d === null
                  ? '—'
                  : formatDuration(summary.mean_time_to_close_seconds_30d, lang)
              }
            />
          </dl>
        </div>
      ) : null}
    </Card>
  );
}
