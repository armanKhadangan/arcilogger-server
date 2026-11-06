import { ArrowDownToLine, ArrowUpFromLine, Lock, Network, Users, Waypoints } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router-dom';

import { ChartFrame } from '@/components/charts/chart-frame';
import { LineChart } from '@/components/charts/line-chart';
import { seriesColor } from '@/components/charts/palette';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { KpiCard } from '@/components/ui/card';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import type { ActivityRow, ActivityWindow, WanLine } from '@/lib/activity-types';
import { useActivityDevices, useActivityWan, useLicense } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { visibleText } from '@/lib/display';
import { formatBytes, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { moduleEnabled } from '@/lib/license';
import { cn } from '@/lib/utils';
import { PrivacyWait, QueryState, Section, SubjectName } from '@/pages/usage/usage-parts';

import {
  ACTIVITY_WINDOWS,
  capacityShare,
  formatBps,
  lineSeries,
  minuteSeries,
  shareText,
  windowFromSearch,
} from './activity-model';

/**
 * Canlı Durum (Wave 5 contract §6, M1–M3, N6, P3): the internet lines' speed over SNMP on top
 * (`dashboard.view` or `system.read`; no person data, not licence-gated), and below "Şu an": who
 * sends and receives data in the last 1, 5 or 15 minutes, from the FortiGate traffic logs
 * (`usage.person` or `usage.anonymous`, module `usage_analytics`, after the KVKK notice). Each
 * part is shown only to those who may see it; both poll every 10 s while the page is visible.
 */
export function ActivityPage() {
  const { t } = useTranslation();
  const { me, can } = useAuth();
  const [search, setSearch] = useSearchParams();
  const window = windowFromSearch(search.get('window'));
  const linesAllowed = can('dashboard.view') || can('system.read');
  const nowAllowed = can('usage.person') || can('usage.anonymous');
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('nav.activity')} description={t('activity.description')} />
      {linesAllowed ? <LinesSection /> : null}
      {nowAllowed ? (
        <NowSection
          window={window}
          privacyPending={privacyPending}
          onWindow={(next) => {
            setSearch(next === 5 ? {} : { window: String(next) }, { replace: true });
          }}
        />
      ) : null}
    </div>
  );
}

// -- the internet lines (N6) ------------------------------------------------------------------------

function LinesSection() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const wan = useActivityWan('60m');
  return (
    <section aria-labelledby="activity-lines" className="flex flex-col gap-3">
      <h2 id="activity-lines" className="text-base font-semibold text-foreground">
        {t('activity.lines.title')}
      </h2>
      <QueryState query={wan} skeleton="table">
        {() =>
          (wan.data?.lines.length ?? 0) === 0 ? (
            <EmptyState
              icon={Network}
              title={t('activity.lines.none')}
              description={t('activity.lines.noneHint')}
              action={
                can('settings.manage') ? (
                  <Button asChild variant="secondary" size="sm">
                    <Link to="/settings/sources">{t('activity.lines.openSources')}</Link>
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {wan.data?.lines.map((line) => (
                <LineCard key={`${line.source}/${line.name}`} line={line} />
              ))}
            </div>
          )
        }
      </QueryState>
    </section>
  );
}

function LineCard({ line }: { line: WanLine }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const rows = useMemo(() => lineSeries(line), [line]);
  const down = capacityShare(line.download_bps, line.capacity_mbps?.down);
  const up = capacityShare(line.upload_bps, line.capacity_mbps?.up);
  // The axis keeps to a number; the unit is in the description and the table's headers.
  const mbps = (value: number) => formatNumber(value, value < 10 ? 1 : 0, lang);
  const labels = { download: t('activity.download'), upload: t('activity.upload') };
  return (
    <ChartFrame
      title={line.name}
      description={t('activity.lines.source', { source: line.source })}
      legend={[
        {
          label: labels.download,
          color: seriesColor(0),
          value:
            formatBps(line.download_bps, lang) +
            (down === null
              ? ''
              : ` · ${t('activity.lines.share', { share: shareText(down, lang) })}`),
        },
        {
          label: labels.upload,
          color: seriesColor(1),
          value:
            formatBps(line.upload_bps, lang) +
            (up === null ? '' : ` · ${t('activity.lines.share', { share: shareText(up, lang) })}`),
        },
      ]}
      table={{
        columns: [
          t('activity.columns.time'),
          t('activity.columns.mbps', { what: labels.download }),
          t('activity.columns.mbps', { what: labels.upload }),
        ],
        rows: rows.map((row) => [row.time, mbps(row.download), mbps(row.upload)]),
      }}
    >
      <div className="flex flex-col gap-2">
        {line.status === 'ok' ? null : (
          <Badge tone={line.status === 'disabled' ? 'neutral' : 'warning'}>
            {t(`activity.status.${line.status}`)}
            {line.last_error_at
              ? ` · ${t('activity.lines.since', { time: formatTime(line.last_error_at) })}`
              : ''}
          </Badge>
        )}
        {rows.length === 0 ? (
          <p className="text-13 text-muted">{t('activity.lines.noSamples')}</p>
        ) : (
          <LineChart
            data={rows}
            xKey="time"
            series={[
              { key: 'download', label: labels.download },
              { key: 'upload', label: labels.upload },
            ]}
            formatValue={mbps}
            height={160}
          />
        )}
      </div>
    </ChartFrame>
  );
}

// -- "Şu an" (M2, M3) -------------------------------------------------------------------------------

function WindowFilter({
  value,
  onChange,
}: {
  value: ActivityWindow;
  onChange: (window: ActivityWindow) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t('activity.window.label')}
      className="inline-flex items-center rounded-lg border border-border bg-surface p-0.5"
    >
      {ACTIVITY_WINDOWS.map((window) => (
        <button
          key={window}
          type="button"
          aria-pressed={value === window}
          onClick={() => {
            onChange(window);
          }}
          className={cn(
            'h-8 rounded-md px-3 text-13 font-medium whitespace-nowrap text-muted-strong hover:text-foreground',
            value === window && 'bg-primary-soft text-primary-ink',
          )}
        >
          {t('activity.window.minutes', { minutes: window })}
        </button>
      ))}
    </div>
  );
}

function NowSection({
  window,
  privacyPending,
  onWindow,
}: {
  window: ActivityWindow;
  privacyPending: boolean;
  onWindow: (window: ActivityWindow) => void;
}) {
  const { t } = useTranslation();
  const license = useLicense();
  // As `ModuleGate`: wait for the licence; a licence answer that failed opens the part (the API
  // refuses a locked module anyway).
  const known = license.data !== undefined || license.isError;
  const locked = license.data ? !moduleEnabled(license.data, 'usage_analytics') : false;
  return (
    <section aria-labelledby="activity-now" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="activity-now" className="text-base font-semibold text-foreground">
          {t('activity.now.title')}
        </h2>
        {!known || locked || privacyPending ? null : (
          <WindowFilter value={window} onChange={onWindow} />
        )}
      </div>
      {!known ? (
        <Skeleton className="h-64" />
      ) : locked ? (
        <EmptyState
          icon={Lock}
          title={t('activity.now.locked')}
          description={t('activity.now.lockedHint')}
        />
      ) : privacyPending ? (
        <PrivacyWait />
      ) : (
        <NowContent window={window} />
      )}
    </section>
  );
}

function NowContent({ window }: { window: ActivityWindow }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const devices = useActivityDevices(window);
  const data = devices.data;
  return (
    <QueryState query={devices}>
      {() =>
        data ? (
          <div className="flex flex-col gap-4">
            {data.warming_up ? (
              <Alert
                tone="warning"
                title={t('activity.now.warmingUp')}
                data-testid="activity-warming"
              >
                {t('activity.now.warmingUpHint')}
              </Alert>
            ) : null}
            <div
              className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
              role="group"
              aria-label={t('activity.now.totals', { minutes: window })}
            >
              <KpiCard
                label={t('activity.download')}
                icon={ArrowDownToLine}
                value={formatBps(data.totals.download_bps, lang)}
                hint={formatBytes(data.totals.download_bytes, lang)}
              />
              <KpiCard
                label={t('activity.upload')}
                icon={ArrowUpFromLine}
                value={formatBps(data.totals.upload_bps, lang)}
                hint={formatBytes(data.totals.upload_bytes, lang)}
              />
              <KpiCard
                label={t('activity.now.subjects')}
                icon={Users}
                value={formatNumber(data.totals.subjects, 0, lang)}
              />
              <KpiCard
                label={t('activity.now.sessions')}
                icon={Waypoints}
                value={formatNumber(data.totals.sessions, 0, lang)}
              />
            </div>
            <MinuteChart data={data.per_minute} />
            <Section
              title={t('activity.now.table')}
              description={
                data.more ? t('activity.now.more', { shown: data.rows.length }) : undefined
              }
            >
              <NowTable rows={data.rows} />
            </Section>
            <p className="text-13 text-muted-strong" data-testid="activity-note">
              {data.data_until
                ? t('activity.now.dataUntil', { time: formatTime(data.data_until, true) })
                : t('activity.now.noData')}{' '}
              {t('activity.now.interim')}
            </p>
          </div>
        ) : null
      }
    </QueryState>
  );
}

function MinuteChart({ data }: { data: Parameters<typeof minuteSeries>[0] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const rows = useMemo(() => minuteSeries(data), [data]);
  const mbps = (value: number) => formatNumber(value, value < 10 ? 1 : 0, lang);
  const labels = { download: t('activity.download'), upload: t('activity.upload') };
  return (
    <ChartFrame
      title={t('activity.now.perMinute')}
      description={t('activity.now.perMinuteHint')}
      legend={[
        { label: labels.download, color: seriesColor(0) },
        { label: labels.upload, color: seriesColor(1) },
      ]}
      table={{
        columns: [
          t('activity.columns.time'),
          t('activity.columns.mbps', { what: labels.download }),
          t('activity.columns.mbps', { what: labels.upload }),
        ],
        rows: rows.map((row) => [row.time, mbps(row.download), mbps(row.upload)]),
      }}
    >
      <LineChart
        data={rows}
        xKey="time"
        series={[
          { key: 'download', label: labels.download },
          { key: 'upload', label: labels.upload },
        ]}
        formatValue={mbps}
        height={200}
      />
    </ChartFrame>
  );
}

function NowTable({ rows }: { rows: ActivityRow[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<ActivityRow>[]>(
    () => [
      {
        id: 'subject',
        accessorFn: (row) => row.subject.label,
        header: t('activity.columns.subject'),
        size: 200,
        meta: { label: t('activity.columns.subject') },
        cell: (info) => <SubjectName subject={info.row.original.subject} />,
      },
      {
        id: 'download',
        accessorKey: 'download_bps',
        header: t('activity.download'),
        size: 120,
        meta: { label: t('activity.download'), numeric: true },
        cell: (info) => (
          <span className="tabular">{formatBps(info.row.original.download_bps, lang)}</span>
        ),
      },
      {
        id: 'upload',
        accessorKey: 'upload_bps',
        header: t('activity.upload'),
        size: 120,
        meta: { label: t('activity.upload'), numeric: true },
        cell: (info) => (
          <span className="tabular">{formatBps(info.row.original.upload_bps, lang)}</span>
        ),
      },
      {
        id: 'sites',
        accessorFn: (row) => row.top_domains.map((item) => item.domain).join(', '),
        header: t('activity.columns.sites'),
        size: 220,
        minSize: 140,
        enableSorting: false,
        meta: { label: t('activity.columns.sites'), grow: true },
        cell: (info) => {
          const text = visibleText(info.getValue<string>());
          return text ? (
            <span className="block truncate" title={text}>
              {text}
            </span>
          ) : (
            '—'
          );
        },
      },
      {
        id: 'apps',
        accessorFn: (row) => row.top_apps.map((item) => item.app).join(', '),
        header: t('activity.columns.apps'),
        size: 160,
        enableSorting: false,
        meta: { label: t('activity.columns.apps') },
        cell: (info) => {
          const text = visibleText(info.getValue<string>());
          return text ? (
            <span className="block truncate" title={text}>
              {text}
            </span>
          ) : (
            '—'
          );
        },
      },
      {
        id: 'last_seen',
        accessorKey: 'last_seen',
        header: t('activity.columns.lastSeen'),
        size: 100,
        meta: { label: t('activity.columns.lastSeen') },
        cell: (info) => (
          <span className="tabular">{formatTime(info.row.original.last_seen, true)}</span>
        ),
      },
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={t('activity.now.table')}
      getRowId={(row) => row.subject.key}
      height={Math.min(480, 48 + rows.length * 40)}
      emptyMessage={t('activity.now.empty')}
    />
  );
}
