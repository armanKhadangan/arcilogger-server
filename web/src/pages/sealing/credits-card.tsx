import { useQueryClient } from '@tanstack/react-query';
import { Coins, RefreshCw, TriangleAlert } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { RequestProgress } from '@/components/admin/request-progress';
import { Sparkline } from '@/components/charts/sparkline';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage, isApiError } from '@/lib/api';
import { queryKeys, useCreditQuery } from '@/lib/api-hooks';
import type { Credits } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { formatDate, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { AdminRequestOut, CreditActionResult } from '@/lib/settings-types';

import { AGENT_CODES } from './seal-states';

function CreditOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const result = request.result as CreditActionResult | null;
  if (!result) return null;
  return (
    <p className="text-13 text-foreground" data-testid="credit-query-result">
      {t(result.below_threshold ? 'sealing.credits.queryLow' : 'sealing.credits.queryResult', {
        credits: formatNumber(result.credits, 0, lang),
        threshold: formatNumber(result.threshold, 0, lang),
      })}
    </p>
  );
}

/**
 * "Kontör sorgula" (`seal.manage`; contract 2.9): the Requests task on the server asks Kamu SM
 * once, as the daily Credit task does (and records the answer in the same history). At most one
 * query a minute; the request is followed until the task reports.
 */
export function CreditQuery() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useCreditQuery();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function start() {
    setProblem(null);
    try {
      const accepted = await query.mutateAsync();
      setRequestId(accepted.request_id);
    } catch (error) {
      const seconds = isApiError(error) ? error.retryAfter : null;
      setProblem(
        seconds
          ? `${errorMessage(error)} ${t('sealing.credits.retryAfter', { seconds })}`
          : isApiError(error, ...AGENT_CODES)
            ? `${errorMessage(error)} ${t('sealing.credits.console')}`
            : errorMessage(error),
      );
    }
  }

  const settled = useCallback(() => {
    void client.invalidateQueries({ queryKey: queryKeys.credits });
  }, [client]);

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="secondary"
        size="sm"
        className="self-start"
        busy={query.isPending}
        onClick={() => void start()}
      >
        <RefreshCw aria-hidden="true" />
        {t('sealing.credits.query')}
      </Button>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          onTerminal={settled}
          renderResult={(request) => <CreditOutcome request={request} />}
        />
      ) : null}
    </div>
  );
}

/**
 * "Kontör" (spec 6.1, 12.3 page 9): the Kamu SM credit history the scheduled Credit task records
 * once a day, the last known number, the alert threshold and a trend line, and "Kontör sorgula".
 * The web service never asks Kamu SM itself (it has no access to the credentials, ADR-0007): a
 * query runs as a task on the server.
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
  const { can } = useAuth();
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
        {can('seal.manage') && !pending ? <CreditQuery /> : null}
      </CardContent>
    </Card>
  );
}
