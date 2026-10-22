import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpFromLine,
  MonitorPlay,
  Moon,
  Network,
  ShieldBan,
  Users,
  Wifi,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { ChartFrame } from '@/components/charts/chart-frame';
import { DonutChart } from '@/components/charts/donut-chart';
import { LineChart } from '@/components/charts/line-chart';
import { CHART_OTHER, seriesColor } from '@/components/charts/palette';
import { Button } from '@/components/ui/button';
import { KpiCard } from '@/components/ui/card';
import { useUsageCompany } from '@/lib/api-hooks';
import type { CompanyUsage, SiteRow, UsageParams } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatBytes, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ExportMenu } from './usage-export';
import { DONUT_SLICES, donutSlices, hourLabel, shareText } from './usage-format';
import { noTraffic, useMethodNote } from './usage-helpers';
import {
  categoryLabel,
  isSingleDay,
  type DrillTarget,
  type UsageTab,
  type UsageView,
} from './usage-model';
import { DataNote, DrillButton, EstimateHint, QueryState, Section } from './usage-parts';
import { AppTable, DepartmentTable } from './usage-tables';

/** Ranked sites with a share bar: domain (→ site), category, time, share. */
function TopSites({
  rows,
  onDrill,
}: {
  rows: readonly SiteRow[];
  onDrill: (target: DrillTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const top = Math.max(1, ...rows.map((row) => row.seconds));
  return (
    <ol className="flex flex-col gap-2" aria-label={t('usage.company.topSites')}>
      {rows.map((row, index) => (
        <li key={row.domain} className="grid grid-cols-[1.5rem_1fr_auto] items-center gap-x-3">
          <span className="text-right text-xs text-muted tabular">{index + 1}</span>
          <span className="flex min-w-0 flex-col gap-1">
            <span className="flex min-w-0 items-baseline gap-2">
              <DrillButton
                onClick={() => {
                  onDrill({ site: row.domain });
                }}
                title={t('usage.openDetail', { name: visibleText(row.domain) })}
                className="text-13 font-medium"
              >
                {visibleText(row.domain)}
              </DrillButton>
              <span className="truncate text-xs text-muted-strong">
                {visibleText(categoryLabel(row.category, t))}
              </span>
            </span>
            <span aria-hidden="true" className="h-1.5 w-full rounded-full bg-subtle">
              <span
                className="block h-full rounded-full"
                style={{ width: `${(row.seconds / top) * 100}%`, background: seriesColor(0) }}
              />
            </span>
          </span>
          <span className="flex flex-col items-end text-13 tabular">
            <span>{formatDuration(row.seconds, lang)}</span>
            <span className="text-xs text-muted-strong">{shareText(row.share, lang)}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function CompanyContent({
  data,
  view,
  params,
  onDrill,
  onList,
}: {
  data: CompanyUsage;
  view: UsageView;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
  onList: (tab: UsageTab) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const note = useMethodNote(data.notice_key);
  const metrics = data.metrics;
  const singleDay = isSingleDay(view);
  const perUser = data.active_users > 0 ? metrics.active_seconds / data.active_users : 0;
  const offShare =
    metrics.active_seconds > 0 ? metrics.off_hours_active_seconds / metrics.active_seconds : 0;
  const hourly = data.hourly.map((point) => ({
    hour: point.hour,
    download: point.download_bytes,
    upload: point.upload_bytes,
    users: point.active_users,
  }));
  const slices = donutSlices(data.top_categories, t('common.other'), metrics.active_seconds);
  const formatHour = (value: string | number) => hourLabel(String(value), singleDay);

  return (
    <div className="flex flex-col gap-4">
      <div
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
        role="group"
        aria-label={t('usage.company.kpis')}
      >
        <KpiCard
          label={t('usage.metrics.activeUsers')}
          icon={Users}
          value={formatNumber(data.active_users, 0, lang)}
          hint={t('usage.hints.activeUsers')}
        />
        <KpiCard
          label={t('usage.metrics.online')}
          icon={Wifi}
          value={formatDuration(metrics.online_seconds, lang)}
          hint={t('usage.hints.online')}
          estimate={note}
        />
        <KpiCard
          label={t('usage.metrics.active')}
          icon={MonitorPlay}
          value={formatDuration(metrics.active_seconds, lang)}
          hint={t('usage.hints.perUser', { value: formatDuration(perUser, lang) })}
          estimate={note}
        />
        <KpiCard
          label={t('usage.metrics.offHours')}
          icon={Moon}
          value={formatDuration(metrics.off_hours_active_seconds, lang)}
          hint={t('usage.hints.offHoursShare', { share: shareText(offShare, lang) })}
          estimate={note}
        />
        <KpiCard
          label={t('usage.metrics.download')}
          icon={ArrowDownToLine}
          value={formatBytes(metrics.download_bytes, lang)}
        />
        <KpiCard
          label={t('usage.metrics.upload')}
          icon={ArrowUpFromLine}
          value={formatBytes(metrics.upload_bytes, lang)}
        />
        <KpiCard
          label={t('usage.metrics.sessions')}
          icon={Network}
          value={formatNumber(metrics.sessions, 0, lang)}
          hint={t('usage.hints.sessions')}
        />
        <KpiCard
          label={t('usage.metrics.blocked')}
          icon={ShieldBan}
          value={formatNumber(metrics.blocked, 0, lang)}
          hint={t('usage.hints.blocked')}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ChartFrame
          className="lg:col-span-2"
          title={t('usage.company.hourlyTraffic')}
          description={t('usage.company.hourlyHint')}
          legend={[
            { label: t('usage.metrics.download'), color: seriesColor(0) },
            { label: t('usage.metrics.upload'), color: seriesColor(1) },
          ]}
          table={{
            columns: [
              t('usage.columns.hour'),
              t('usage.metrics.download'),
              t('usage.metrics.upload'),
            ],
            rows: data.hourly.map((point) => [
              formatHour(point.hour),
              formatBytes(point.download_bytes, lang),
              formatBytes(point.upload_bytes, lang),
            ]),
          }}
        >
          <StackedAreaChart
            data={hourly}
            xKey="hour"
            series={[
              { key: 'download', label: t('usage.metrics.download') },
              { key: 'upload', label: t('usage.metrics.upload') },
            ]}
            formatValue={(value) => formatBytes(value, lang)}
            formatX={formatHour}
          />
        </ChartFrame>
        <ChartFrame
          title={t('usage.company.hourlyUsers')}
          description={t('usage.company.hourlyUsersHint')}
          table={{
            columns: [t('usage.columns.hour'), t('usage.metrics.activeUsers')],
            rows: data.hourly.map((point) => [
              formatHour(point.hour),
              formatNumber(point.active_users, 0, lang),
            ]),
          }}
        >
          <LineChart
            data={hourly}
            xKey="hour"
            series={[{ key: 'users', label: t('usage.metrics.activeUsers') }]}
            formatValue={(value) => formatNumber(value, 0, lang)}
            formatX={formatHour}
            height={220}
            integer
          />
        </ChartFrame>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title={t('usage.company.topSites')}
          description={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <>
              <ExportMenu
                view="company"
                params={params}
                label={t('usage.company.topSites')}
                csv={false}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  onList('sites');
                }}
              >
                {t('usage.company.allSites')}
                <ArrowRight aria-hidden="true" />
              </Button>
            </>
          }
        >
          {data.top_sites.length > 0 ? (
            <TopSites rows={data.top_sites} onDrill={onDrill} />
          ) : (
            <p className="text-13 text-muted">{t('usage.empty.title')}</p>
          )}
        </Section>
        <Section
          title={t('usage.company.topCategories')}
          description={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <>
              <ExportMenu
                view="company"
                params={params}
                label={t('usage.company.topCategories')}
                csv={false}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  onList('categories');
                }}
              >
                {t('usage.company.allCategories')}
                <ArrowRight aria-hidden="true" />
              </Button>
            </>
          }
        >
          <div className="grid items-center gap-4 sm:grid-cols-[minmax(0,14rem)_1fr]">
            <DonutChart
              data={slices.map((slice) => ({ ...slice, label: categoryLabel(slice.label, t) }))}
              formatValue={(value) => formatDuration(value, lang)}
              centerLabel={formatDuration(metrics.active_seconds, lang)}
              centerHint={t('usage.estimated')}
              height={220}
            />
            <ul className="flex flex-col gap-1.5" aria-label={t('usage.company.topCategories')}>
              {data.top_categories.map((row, index) => {
                const colour = index < DONUT_SLICES ? seriesColor(index) : CHART_OTHER;
                const name = visibleText(categoryLabel(row.category, t));
                return (
                  <li key={row.category} className="flex items-center gap-2 text-13">
                    <span
                      aria-hidden="true"
                      className="size-2.5 shrink-0 rounded-sm"
                      style={{ background: colour }}
                    />
                    <DrillButton
                      onClick={() => {
                        onDrill({ category: row.category });
                      }}
                      title={t('usage.openDetail', { name })}
                    >
                      {name}
                    </DrillButton>
                    <span className="ml-auto pl-2 whitespace-nowrap tabular">
                      {formatDuration(row.seconds, lang)}
                    </span>
                    <span className="w-14 text-right text-xs text-muted-strong tabular">
                      {shareText(row.share, lang)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </Section>
      </div>

      <Section
        title={t('usage.company.departments')}
        description={t('usage.company.departmentsHint')}
        headingLevel={3}
      >
        <DepartmentTable
          rows={data.departments}
          caption={t('usage.company.departments')}
          onOpen={(department) => {
            onDrill({ department });
          }}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <ExportMenu view="company" params={params} label={t('usage.company.departments')} />
          }
          emptyMessage={t('usage.empty.title')}
          maxHeight={400}
        />
      </Section>

      <Section title={t('usage.company.topApps')} description={t('usage.company.topAppsHint')}>
        <AppTable
          rows={data.top_apps}
          caption={t('usage.company.topApps')}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <ExportMenu
              view="company"
              params={params}
              label={t('usage.company.topApps')}
              csv={false}
            />
          }
          emptyMessage={t('usage.empty.title')}
          maxHeight={400}
        />
      </Section>
    </div>
  );
}

/**
 * Şirket: totals of the period (KPIs), the hourly volume and active users, top sites, categories
 * and applications, and the departments (→ Departman). No names: also for `dashboard.usage`.
 */
export function CompanyTab({
  view,
  params,
  onDrill,
  onList,
}: {
  view: UsageView;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
  onList: (tab: UsageTab) => void;
}) {
  const company = useUsageCompany(params);
  const data = company.data;
  return (
    <div className="flex flex-col gap-4">
      <DataNote computedUntil={data?.computed_until} noticeKey={data?.notice_key} />
      <QueryState query={company} empty={data ? noTraffic(data.metrics) : false}>
        {() =>
          data ? (
            <CompanyContent
              data={data}
              view={view}
              params={params}
              onDrill={onDrill}
              onList={onList}
            />
          ) : null
        }
      </QueryState>
    </div>
  );
}
