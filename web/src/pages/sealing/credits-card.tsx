import { Coins, TriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Sparkline } from '@/components/charts/sparkline';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import type { Credits } from '@/lib/api-types';
import { formatDate, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

/**
 * "Kontör" (spec 6.1, 12.3 page 9): the Kamu SM credit history the scheduled Credit task records
 * once a day, the last known number, the alert threshold and a trend line. The web service never
 * asks Kamu SM itself (it has no access to the credentials, ADR-0007).
 */
export function CreditsCard({
  credits,
  error,
  pending,
}: {
  credits: Credits | undefined;
  error: unknown;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const values = (credits?.history ?? [])
    .map((check) => check.credits)
    .filter((value): value is number => value !== null);
  const latest = credits?.latest;
  const first = credits?.history[0];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Coins aria-hidden="true" className="size-4 text-muted-strong" />
          {t('sealing.credits.title')}
        </CardTitle>
        {credits?.below_threshold ? (
          <Badge tone="danger" icon={TriangleAlert}>
            {t('sealing.credits.low')}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {pending ? (
          <Skeleton className="h-24" />
        ) : error ? (
          <Alert tone="danger">{errorMessage(error)}</Alert>
        ) : credits ? (
          <>
            <div className="flex items-end justify-between gap-3">
              <div className="flex flex-col">
                <span className="text-2xl leading-8 font-semibold text-foreground tabular">
                  {credits.remaining === null
                    ? t('sealing.credits.unknown')
                    : formatNumber(credits.remaining, 0, lang)}
                </span>
                <span className="text-13 text-muted">
                  {t('sealing.credits.threshold', {
                    threshold: formatNumber(credits.threshold, 0, lang),
                  })}
                </span>
              </div>
              {values.length > 1 ? (
                <Sparkline
                  values={values}
                  label={t('sealing.credits.trend', {
                    from: first ? formatDate(first.time) : '',
                  })}
                  formatValue={(value) => formatNumber(value, 0, lang)}
                  className="h-10 w-32 shrink-0"
                />
              ) : null}
            </div>
            <p className="text-xs text-muted-strong">
              {t('sealing.credits.provider', { provider: credits.provider })}
              {latest
                ? ` · ${t('sealing.credits.latest', { time: formatDateTime(latest.time) })}`
                : ` · ${t('sealing.credits.never')}`}
            </p>
            {latest?.error ? (
              <Alert tone="warning" assertive={false} title={t('sealing.credits.lastError')}>
                {latest.error}
              </Alert>
            ) : null}
            {credits.below_threshold ? (
              <Alert tone="danger" assertive={false}>
                {t('sealing.credits.lowBody', {
                  remaining: formatNumber(credits.remaining ?? 0, 0, lang),
                  threshold: formatNumber(credits.threshold, 0, lang),
                })}
              </Alert>
            ) : null}
            {credits.unreadable_lines > 0 ? (
              <Alert tone="warning" assertive={false}>
                {t('sealing.credits.unreadable', { count: credits.unreadable_lines })}
              </Alert>
            ) : null}
            {credits.history.length > 0 ? (
              <details className="text-13">
                <summary className="cursor-pointer font-medium text-foreground">
                  {t('sealing.credits.history', { count: credits.history.length })}
                </summary>
                <table className="mt-2 w-full text-xs">
                  <caption className="sr-only">{t('sealing.credits.historyCaption')}</caption>
                  <thead>
                    <tr className="text-left text-muted-strong">
                      <th scope="col" className="py-1 font-medium">
                        {t('sealing.credits.columns.time')}
                      </th>
                      <th scope="col" className="py-1 text-right font-medium">
                        {t('sealing.credits.columns.credits')}
                      </th>
                      <th scope="col" className="py-1 pl-3 font-medium">
                        {t('sealing.credits.columns.note')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...credits.history].reverse().map((check) => (
                      <tr key={check.time} className="border-t border-border">
                        <td className="py-1 tabular">{formatDateTime(check.time)}</td>
                        <td className="py-1 text-right tabular">
                          {check.credits === null ? '—' : formatNumber(check.credits, 0, lang)}
                        </td>
                        <td className="py-1 pl-3 text-muted-strong">{check.error ?? ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            ) : null}
            <p className="text-xs text-muted">{t('sealing.credits.note')}</p>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
