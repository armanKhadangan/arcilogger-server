import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { ChartFrame } from '@/components/charts/chart-frame';
import { seriesColor } from '@/components/charts/palette';
import { Card } from '@/components/ui/card';
import type { AlertSummaryWithRules } from '@/lib/alert-rule-types';
import { formatDay } from '@/lib/display';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ALERT_SEVERITIES, byDayRows } from './alerts-model';

/** `13.10` on the chart's axis (the table and the tooltip give the whole day). */
function shortDay(day: string | number): string {
  return formatDay(String(day)).slice(0, 5);
}

function TopList({
  title,
  testId,
  empty,
  items,
}: {
  title: string;
  testId: string;
  empty: string;
  items: { id: string; label: ReactNode; count: number }[];
}) {
  const lang = currentLang();
  return (
    <Card className="flex flex-col gap-3 p-5" data-testid={testId}>
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {items.length === 0 ? (
        <p className="text-13 text-muted">{empty}</p>
      ) : (
        <ol className="flex flex-col divide-y divide-border text-13">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 py-1.5">
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              <span className="font-medium text-foreground tabular">
                {formatNumber(item.count, 0, lang)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/**
 * Below "Güvenlik Özeti" (Wave 6 §2.6.2): the alerts of the last 30 site days by severity, and the
 * ten rules and keys that raised the most alerts in the last 7 days. A rule opens the list
 * filtered by it. Shown once the API sends them (W6-A).
 */
export function SecurityTrends({ summary }: { summary: AlertSummaryWithRules }) {
  const { t } = useTranslation();
  const lang = currentLang();
  if (!summary.by_day && !summary.top_rules && !summary.top_keys) return null;
  const days = byDayRows(summary.by_day);
  const severities = [...ALERT_SEVERITIES];
  return (
    <div className="grid gap-4 xl:grid-cols-3" data-testid="security-trends">
      {summary.by_day ? (
        <ChartFrame
          className="xl:col-span-3"
          title={t('security.trends.byDay')}
          description={t('security.trends.byDayHint')}
          legend={severities.map((severity, index) => ({
            label: t(`severity.${severity}`),
            color: seriesColor(index),
          }))}
          table={{
            columns: [
              t('security.trends.day'),
              ...severities.map((severity) => t(`severity.${severity}`)),
              t('security.trends.total'),
            ],
            rows: days.map((day) => [
              formatDay(day.day),
              ...severities.map((severity) => formatNumber(day[severity], 0, lang)),
              formatNumber(day.total, 0, lang),
            ]),
          }}
        >
          <StackedAreaChart
            data={days}
            xKey="day"
            series={severities.map((severity) => ({
              key: severity,
              label: t(`severity.${severity}`),
            }))}
            formatValue={(value) => formatNumber(value, 0, lang)}
            formatX={shortDay}
            height={200}
          />
        </ChartFrame>
      ) : null}
      {summary.top_rules ? (
        <TopList
          title={t('security.trends.topRules')}
          testId="top-rules"
          empty={t('security.trends.none')}
          items={summary.top_rules.map((item) => ({
            id: item.rule,
            count: item.count,
            label: (
              <Link
                to={`/security?rule=${encodeURIComponent(item.rule)}`}
                className="rounded-sm font-mono text-xs text-accent-ink hover:underline"
              >
                {item.rule}
              </Link>
            ),
          }))}
        />
      ) : null}
      {summary.top_keys ? (
        <TopList
          title={t('security.trends.topKeys')}
          testId="top-keys"
          empty={t('security.trends.none')}
          items={summary.top_keys.map((item) => ({
            id: `${item.rule}:${item.key}`,
            count: item.count,
            label: (
              <span className="flex items-baseline gap-2">
                <span className="truncate font-mono text-xs text-foreground">{item.key}</span>
                <span className="font-mono text-xs text-muted">{item.rule}</span>
              </span>
            ),
          }))}
        />
      ) : null}
    </div>
  );
}
