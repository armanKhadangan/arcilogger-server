import { BarChart3, Clock3, Globe, PieChart, Table2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { DonutChart } from '@/components/charts/donut-chart';
import { seriesColor, sliceColor } from '@/components/charts/palette';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import type { CompanyUsage } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatBytes, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { categoryLabel } from '@/pages/usage/usage-model';

import { categorySlices, formatTotalTime, hourlyRows } from './dashboard-model';
import { DashCard, EstimateHint } from './dashboard-parts';

interface UsageProps {
  usage: CompanyUsage | undefined;
  error: unknown;
  pending: boolean;
  /** The usage page, `null` when the user may not open it. */
  to: string | null;
}

/** The method note of the answer (`notice_key`), with the contract's text as the fallback. */
function useMethodNote(usage: CompanyUsage | undefined): string {
  const { t } = useTranslation();
  const fallback = t('dashboard.usage.methodNote');
  return usage ? t(usage.notice_key, { defaultValue: fallback }) : fallback;
}

function Kpi({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="flex items-center gap-1 text-13 text-muted">
        {label}
        {note ? <EstimateHint text={note} /> : null}
      </dt>
      <dd className="text-xl font-semibold text-foreground tabular">{value}</dd>
    </div>
  );
}

/** "Bugünkü kullanım (şirket)": the day's totals and downloaded / uploaded bytes per hour. */
export function UsageCard({
  usage,
  error,
  pending,
  to,
  className,
}: UsageProps & { className?: string }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const note = useMethodNote(usage);
  const [table, setTable] = useState(false);
  const rows = usage ? hourlyRows(usage.hourly) : [];
  return (
    <DashCard
      title={t('dashboard.usage.title')}
      icon={Clock3}
      to={to}
      page={t('nav.usage')}
      estimate={note}
      className={className}
      testId="card-usage"
    >
      {pending ? (
        <Skeleton className="h-64" />
      ) : error ? (
        <Alert tone="danger">{`${t('dashboard.usage.failed')} ${errorMessage(error)}`}</Alert>
      ) : usage ? (
        <>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Kpi
              label={t('dashboard.usage.activeUsers')}
              value={formatNumber(usage.active_users, 0, lang)}
            />
            <Kpi
              label={t('dashboard.usage.activeTime')}
              value={formatTotalTime(usage.metrics.active_seconds, lang)}
              note={note}
            />
            <Kpi
              label={t('dashboard.usage.download')}
              value={formatBytes(usage.metrics.download_bytes, lang)}
            />
            <Kpi
              label={t('dashboard.usage.upload')}
              value={formatBytes(usage.metrics.upload_bytes, lang)}
            />
          </dl>
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-13 font-medium text-foreground">
              {t('dashboard.usage.chartTitle')}
            </h4>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-pressed={table}
              aria-label={table ? t('common.showChart') : t('common.showTable')}
              title={table ? t('common.showChart') : t('common.showTable')}
              onClick={() => {
                setTable((value) => !value);
              }}
            >
              {table ? <BarChart3 aria-hidden="true" /> : <Table2 aria-hidden="true" />}
            </Button>
          </div>
          {rows.length === 0 ? (
            <p className="text-13 text-muted">{t('dashboard.usage.empty')}</p>
          ) : table ? (
            <div
              className="max-h-64 overflow-auto rounded-lg border border-border"
              tabIndex={0}
              role="region"
              aria-label={t('dashboard.usage.chartTitle')}
            >
              <table className="w-full text-13">
                <caption className="sr-only">{t('dashboard.usage.chartTitle')}</caption>
                <thead className="sticky top-0 bg-subtle text-left text-muted-strong">
                  <tr>
                    <th scope="col" className="px-3 py-1.5 font-medium">
                      {t('dashboard.usage.hour')}
                    </th>
                    <th scope="col" className="px-3 py-1.5 text-right font-medium">
                      {t('dashboard.usage.download')}
                    </th>
                    <th scope="col" className="px-3 py-1.5 text-right font-medium">
                      {t('dashboard.usage.upload')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.hour} className="border-t border-border">
                      <td className="px-3 py-1.5 tabular">{row.hour}</td>
                      <td className="px-3 py-1.5 text-right tabular">
                        {formatBytes(row.download, lang)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular">
                        {formatBytes(row.upload, lang)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <>
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-13 text-muted" aria-hidden="true">
                {[t('dashboard.usage.download'), t('dashboard.usage.upload')].map(
                  (label, index) => (
                    <li key={label} className="flex items-center gap-1.5">
                      <span
                        className="size-2.5 rounded-sm"
                        style={{ background: seriesColor(index) }}
                      />
                      <span className="text-foreground">{label}</span>
                    </li>
                  ),
                )}
              </ul>
              <StackedAreaChart
                data={rows}
                xKey="hour"
                series={[
                  { key: 'download', label: t('dashboard.usage.download') },
                  { key: 'upload', label: t('dashboard.usage.upload') },
                ]}
                formatValue={(value) => formatBytes(value, lang)}
                height={200}
              />
            </>
          )}
          <p className="text-xs text-muted">
            {usage.computed_until
              ? t('dashboard.usage.computedUntil', { time: formatTime(usage.computed_until) })
              : t('dashboard.usage.notComputed')}
          </p>
        </>
      ) : null}
    </DashCard>
  );
}

function percent(share: number, lang: 'tr' | 'en'): string {
  return lang === 'tr'
    ? `%${formatNumber(share * 100, 0, lang)}`
    : `${formatNumber(share * 100, 0, lang)}%`;
}

/** "En çok vakit geçirilen siteler": today's top 10 by estimated active time. */
export function SitesCard({ usage, error, pending, to }: UsageProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const note = useMethodNote(usage);
  const sites = usage?.top_sites ?? [];
  return (
    <DashCard
      title={t('dashboard.sites.title')}
      icon={Globe}
      to={to}
      page={t('nav.usage')}
      estimate={note}
      testId="card-sites"
    >
      {pending ? (
        <Skeleton className="h-48" />
      ) : error ? (
        <Alert tone="danger">{errorMessage(error)}</Alert>
      ) : sites.length === 0 ? (
        <p className="text-13 text-muted">{t('dashboard.sites.empty')}</p>
      ) : (
        <table className="w-full table-fixed text-13">
          <caption className="sr-only">{t('dashboard.sites.caption')}</caption>
          <thead className="text-left text-xs text-muted-strong">
            <tr>
              <th scope="col" className="pb-1 font-medium">
                {t('dashboard.sites.domain')}
              </th>
              <th scope="col" className="w-20 pb-1 text-right font-medium">
                {t('dashboard.sites.time')}
              </th>
              <th scope="col" className="w-14 pb-1 text-right font-medium">
                {t('dashboard.sites.share')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sites.map((site) => (
              <tr key={site.domain} className="border-t border-border">
                <td className="py-1.5 pr-2">
                  <span className="block truncate" title={visibleText(site.domain)}>
                    {visibleText(site.domain)}
                  </span>
                  <span className="block truncate text-xs text-muted-strong">
                    {site.category ? visibleText(site.category) : t('dashboard.sites.noCategory')}
                  </span>
                </td>
                <td className="py-1.5 text-right tabular">{formatTotalTime(site.seconds, lang)}</td>
                <td className="py-1.5 text-right tabular">{percent(site.share, lang)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </DashCard>
  );
}

/**
 * "Kategoriler": share of today's estimated active time per category (donut + legend); the
 * middle is the day's active time, the same as the usage card's.
 */
export function CategoriesCard({ usage, error, pending, to }: UsageProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const note = useMethodNote(usage);
  const total = usage?.metrics.active_seconds ?? 0;
  // The unknown category is "Bilinmiyor" in the API, shown in the UI language.
  const rows = (usage?.top_categories ?? []).map((row) => ({
    ...row,
    category: categoryLabel(row.category, t),
  }));
  const slices = categorySlices(rows, t('common.other'), total);
  return (
    <DashCard
      title={t('dashboard.categories.title')}
      icon={PieChart}
      to={to}
      page={t('nav.usage')}
      estimate={note}
      testId="card-categories"
    >
      {pending ? (
        <Skeleton className="h-48" />
      ) : error ? (
        <Alert tone="danger">{errorMessage(error)}</Alert>
      ) : slices.length === 0 ? (
        <p className="text-13 text-muted">{t('dashboard.categories.empty')}</p>
      ) : (
        <>
          <DonutChart
            data={slices}
            formatValue={(value) => formatTotalTime(value, lang)}
            centerLabel={formatTotalTime(total, lang)}
            centerHint={t('common.estimateShort')}
            height={180}
          />
          <ul className="flex flex-col gap-1 text-13" aria-label={t('dashboard.categories.legend')}>
            {slices.map((slice, index) => (
              <li key={slice.label} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-2.5 shrink-0 rounded-sm"
                  style={{ background: sliceColor(slice, index) }}
                />
                <span className="min-w-0 flex-1 truncate">{visibleText(slice.label)}</span>
                <span className="tabular">{formatTotalTime(slice.value, lang)}</span>
                <span className="w-10 text-right text-muted tabular">
                  {percent(slice.share, lang)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </DashCard>
  );
}
