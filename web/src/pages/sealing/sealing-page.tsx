import { CalendarDays } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge, STATUS_STYLE } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { SealCalendar, SEAL_STATUSES } from '@/components/ui/seal-calendar';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useCredits, useSealCalendar } from '@/lib/api-hooks';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { CreditsCard } from './credits-card';
import {
  calendarDays,
  calendarStatus,
  dayDate,
  defaultDay,
  monthDate,
  monthKey,
} from './seal-states';
import { SealDayPanel } from './seal-day-panel';

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Mühürleme (spec 12.3 page 9; `seal.read`): the month calendar of seal states (from the vault
 * and the scheduled tasks' state files, refreshed every minute in the background), the selected
 * day's details and actions, and the Kamu SM credit. `?month=2026-10&day=2026-10-09` keeps the
 * view across reloads.
 */
export function SealingPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const [params, setParams] = useSearchParams();
  const monthParam = params.get('month');
  const month = monthParam && MONTH.test(monthParam) ? monthParam : null;
  const dayParam = params.get('day');
  const calendar = useSealCalendar(month);
  const credits = useCredits();
  const data = calendar.data;
  const selected = dayParam && DAY.test(dayParam) ? dayParam : data ? defaultDay(data) : null;

  // Keep the default day in the address once the calendar knows it (back button, reload).
  useEffect(() => {
    if (!dayParam && selected && data) {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set('day', selected);
          return next;
        },
        { replace: true },
      );
    }
  }, [dayParam, selected, data, setParams]);

  const cells = useMemo(() => (data ? calendarDays(data, lang) : null), [data, lang]);
  const legend = useMemo(() => {
    const present = new Set(data?.days.map((cell) => calendarStatus(cell.state)) ?? []);
    return SEAL_STATUSES.filter((status) => present.has(status) || status === 'sealed');
  }, [data]);

  function choose(next: { month?: string; day?: string | null }) {
    setParams((current) => {
      const updated = new URLSearchParams(current);
      if (next.month !== undefined) updated.set('month', next.month);
      if (next.day === null) updated.delete('day');
      else if (next.day !== undefined) updated.set('day', next.day);
      return updated;
    });
  }

  const configured = credits.data;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader title={t('nav.sealing')} description={t('sealing.description')} />
      {configured && !configured.legal_grade ? (
        <Alert tone="danger" title={t('legal.notLegalTitle')} data-testid="provider-not-legal">
          {t('sealing.providerNotLegal', { provider: configured.provider })}
        </Alert>
      ) : null}
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(20rem,24rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CalendarDays aria-hidden="true" className="size-4 text-muted-strong" />
                {t('sealing.calendarTitle')}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {calendar.isPending ? (
                <Skeleton className="h-80" />
              ) : calendar.isError ? (
                <Alert tone="danger">{errorMessage(calendar.error)}</Alert>
              ) : data && cells ? (
                <>
                  <SealCalendar
                    month={monthDate(data.month)}
                    days={cells.days}
                    notes={cells.notes}
                    today={dayDate(data.today)}
                    selected={selected}
                    legend={legend}
                    onSelect={(day) => {
                      choose({ day });
                    }}
                    onMonthChange={(date) => {
                      choose({ month: monthKey(date), day: null });
                    }}
                  />
                  <ul className="flex flex-col gap-1 text-13" aria-label={t('sealing.counts')}>
                    {data.legend
                      .filter((item) => (data.counts[item.state] ?? 0) > 0)
                      .map((item) => {
                        const Icon = STATUS_STYLE[calendarStatus(item.state)].icon;
                        return (
                          <li key={item.state} className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-1.5">
                              <Icon aria-hidden="true" className="size-3.5 text-muted-strong" />
                              {lang === 'en' ? item.label_en : item.label_tr}
                            </span>
                            <span className="tabular">
                              {formatNumber(data.counts[item.state] ?? 0, 0, lang)}
                            </span>
                          </li>
                        );
                      })}
                  </ul>
                  {data.replica_enabled ? null : (
                    <Badge tone="neutral" className="self-start">
                      {t('sealing.replicaDisabled')}
                    </Badge>
                  )}
                  {data.problems.length > 0 ? (
                    <Alert tone="warning" assertive={false} title={t('sealing.problems')}>
                      {data.problems.join('; ')}
                    </Alert>
                  ) : null}
                  <p className="text-xs text-muted">
                    {t('sealing.verifiedWindow', { days: data.recent_verify_days })}
                  </p>
                </>
              ) : null}
            </CardContent>
          </Card>
          <CreditsCard
            credits={credits.data}
            error={credits.isError ? credits.error : null}
            pending={credits.isPending}
          />
        </div>
        <div className="min-w-0">
          {selected ? (
            <SealDayPanel key={selected} day={selected} />
          ) : (
            <EmptyState
              icon={CalendarDays}
              title={t('sealing.noDayTitle')}
              description={t('sealing.noDayBody')}
            />
          )}
        </div>
      </div>
    </div>
  );
}
