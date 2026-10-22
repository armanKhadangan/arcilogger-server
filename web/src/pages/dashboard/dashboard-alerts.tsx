import { addDays, format, startOfWeek } from 'date-fns';
import { enUS, tr } from 'date-fns/locale';
import { CalendarDays, CircleCheck, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { SeverityBadge, SEVERITY_STYLE, STATUS_STYLE } from '@/components/ui/badge';
import { DAY_CLASS } from '@/components/ui/seal-calendar';
import type { DashboardAlerts, SealCalendarDay } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { alertHref, alertSummaryText } from '@/pages/security/alerts-model';

import { lastDays, sealDayHref, sealStatus, SEVERITY_ORDER } from './dashboard-model';
import { DashCard } from './dashboard-parts';

/** "Açık güvenlik uyarıları": open alerts per severity and the newest ones, each a link. */
export function AlertsCard({
  alerts,
  to,
  className,
}: {
  alerts: DashboardAlerts;
  /** The security page, `null` when the user may not open it. */
  to: string | null;
  className?: string;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const counts = SEVERITY_ORDER.map((severity) => ({
    severity,
    count: alerts.open_by_severity[severity],
  })).filter((item) => item.count > 0);
  return (
    <DashCard
      title={t('dashboard.alerts.title')}
      icon={ShieldAlert}
      to={to}
      page={t('nav.security')}
      className={className}
      testId="card-alerts"
    >
      {counts.length === 0 && alerts.latest.length === 0 ? (
        <p className="flex items-center gap-2 text-13 text-success-ink">
          <CircleCheck aria-hidden="true" className="size-4" />
          {t('dashboard.alerts.none')}
        </p>
      ) : null}
      {counts.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label={t('dashboard.alerts.bySeverity')}>
          {counts.map(({ severity, count }) => {
            const Icon = SEVERITY_STYLE[severity].icon;
            const text = t('dashboard.alerts.count', {
              severity: t(`severity.${severity}`),
              value: formatNumber(count, 0, lang),
            });
            const chip = cn(
              'inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-13 font-medium tabular',
              {
                info: 'bg-info-soft text-info-ink',
                low: 'bg-subtle text-foreground',
                medium: 'bg-warning-soft text-warning-ink',
                high: 'bg-danger-soft text-danger-ink',
                critical: 'bg-danger-soft font-semibold text-danger-ink ring-1 ring-danger',
              }[severity],
            );
            return (
              <li key={severity}>
                {to ? (
                  <Link
                    to={`${to}?state=open&severity=${severity}`}
                    className={cn(chip, 'hover:underline')}
                  >
                    <Icon aria-hidden="true" className="size-3.5" />
                    {text}
                  </Link>
                ) : (
                  <span className={chip}>
                    <Icon aria-hidden="true" className="size-3.5" />
                    {text}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
      {alerts.latest.length > 0 ? (
        <ul
          className="flex flex-col divide-y divide-border"
          aria-label={t('dashboard.alerts.latest')}
        >
          {alerts.latest.map((alert) => {
            const body = (
              <>
                <span className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={alert.severity} />
                  <span className="font-mono text-xs text-muted-strong">{alert.rule}</span>
                  <time className="ml-auto text-xs text-muted tabular">
                    {formatDateTime(alert.time)}
                  </time>
                </span>
                <span className="line-clamp-2 text-13 text-foreground">
                  {visibleText(alertSummaryText(alert, lang))}
                </span>
              </>
            );
            return (
              <li key={alert.id} className="py-2 first:pt-0 last:pb-0">
                {to ? (
                  <Link
                    to={alertHref(alert.id)}
                    className="flex flex-col gap-1 rounded-md hover:bg-subtle"
                  >
                    {body}
                  </Link>
                ) : (
                  <div className="flex flex-col gap-1">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </DashCard>
  );
}

/** "Mühür takvimi (son 30 gün)": one cell per day, colour + icon + label, a link to the day. */
export function SealStripCard({
  calendar,
  lastDay,
  to,
}: {
  calendar: readonly SealCalendarDay[];
  /** The newest day of the strip (yesterday: today is sealed tomorrow). */
  lastDay: string;
  to: string | null;
}) {
  const { t } = useTranslation();
  const locale = currentLang() === 'en' ? enUS : tr;
  const cells = lastDays(calendar, lastDay);
  const weekStart = startOfWeek(new Date(2026, 0, 5), { weekStartsOn: 1 });
  const present = new Set(cells.map((cell) => (cell ? sealStatus(cell.status) : null)));
  const legend = (['sealed', 'pending', 'failed', 'noData'] as const).filter(
    (status) => present.has(status) || status === 'sealed',
  );
  return (
    <DashCard
      title={t('dashboard.calendar.title')}
      icon={CalendarDays}
      to={to}
      page={t('nav.sealing')}
      testId="card-calendar"
    >
      <table className="w-full table-fixed border-separate border-spacing-1">
        <caption className="sr-only">{t('dashboard.calendar.caption')}</caption>
        <thead>
          <tr>
            {Array.from({ length: 7 }, (_, index) => (
              <th key={index} scope="col" className="py-0.5 text-xs font-medium text-muted">
                {format(addDays(weekStart, index), 'EEEEEE', { locale })}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: Math.ceil(cells.length / 7) }, (_, week) => (
            <tr key={week}>
              {cells.slice(week * 7, week * 7 + 7).map((cell, index) => {
                if (!cell) return <td key={`blank-${index}`} />;
                const status = sealStatus(cell.status);
                const Icon = STATUS_STYLE[status].icon;
                const date = new Date(`${cell.day}T12:00:00`);
                const label = t('sealCalendar.dayLabel', {
                  date: format(date, 'd MMMM yyyy', { locale }),
                  status: t(`status.${status}`),
                });
                const content = (
                  <>
                    {format(date, 'd')}
                    <Icon aria-hidden="true" className="size-3" strokeWidth={2} />
                  </>
                );
                const style = cn(
                  'flex h-10 w-full flex-col items-center justify-center gap-0.5 rounded-md text-xs font-medium tabular',
                  DAY_CLASS[status],
                );
                return (
                  <td key={cell.day} className="p-0">
                    {to ? (
                      <Link
                        to={sealDayHref(to, cell.day)}
                        aria-label={label}
                        title={label}
                        className={cn(style, 'hover:ring-2 hover:ring-accent')}
                      >
                        {content}
                      </Link>
                    ) : (
                      <span aria-label={label} title={label} role="img" className={style}>
                        {content}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <ul
        className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted"
        aria-label={t('sealCalendar.legend')}
      >
        {legend.map((status) => {
          const Icon = STATUS_STYLE[status].icon;
          return (
            <li key={status} className="flex items-center gap-1">
              <span
                className={cn(
                  'flex size-4 items-center justify-center rounded-sm',
                  DAY_CLASS[status],
                )}
              >
                <Icon aria-hidden="true" className="size-3" />
              </span>
              {t(`status.${status}`)}
            </li>
          );
        })}
      </ul>
    </DashCard>
  );
}
