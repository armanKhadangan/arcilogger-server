import { Info, RefreshCw, TrendingUp } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import type { LicenseOut } from '@/lib/api-types';
import { formatDay, formatDistance } from '@/lib/display';
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatTime,
} from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { licenseDayText } from '@/lib/license';
import type { SystemHealth } from '@/lib/system-health-types';
import type {
  CollectorInfo,
  DatabaseInfo,
  DriveInfo,
  HostInfo,
  IngestInfo,
  StatusFileState,
  SystemSummary,
  VersionInfo,
} from '@/lib/system-types';
import { cn } from '@/lib/utils';

import { DriveMeter } from './drive-meter';
import {
  AGENT_STYLE,
  APPLY_SYSTEM_STYLE,
  cpuPending,
  DATABASE_STYLE,
  databaseState,
  forecastText,
  formatPercent,
  JOB_STYLE,
  MAINTENANCE_STYLE,
  maintenanceState,
  memoryText,
  SAMPLE_STYLE,
  sampleState,
  scheduleText,
  STATUS_FILE_STYLE,
  TASK_STYLE,
  SYSTEM_BLOCKS,
  type StateStyle,
  type SystemBlock,
} from './system-model';

/** The blocks `GET /system/health` feeds; the others come from `GET /system` (and `/license`). */
const HEALTH_BLOCKS: ReadonlySet<SystemBlock> = new Set(['services', 'tasks', 'jobs', 'clock']);
/** Blocks with a table: the whole width on a wide screen. */
const WIDE_BLOCKS: ReadonlySet<SystemBlock> = new Set([
  'disks',
  'collector',
  'ingest',
  'services',
  'tasks',
  'jobs',
  'clock',
]);

const NONE = '—';

function dateTime(value: string | null): string {
  return value ? formatDateTime(value) : NONE;
}

function StateBadge({ style }: { style: StateStyle }) {
  const { t } = useTranslation();
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(style.label)}
    </Badge>
  );
}

/** A block of the page: a card with its heading (level 2) and, beside it, a state badge. */
function Block({
  id,
  status,
  children,
}: {
  id: SystemBlock;
  status?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const headingId = `system-block-${id}`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid={headingId}
      className={cn('min-w-0', WIDE_BLOCKS.has(id) && 'lg:col-span-2')}
    >
      <Card className="flex h-full flex-col">
        <CardHeader className="flex-wrap items-center">
          <h2 id={headingId} className="text-sm font-semibold text-foreground">
            {t(`system.blocks.${id}`)}
          </h2>
          {status}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">{children}</CardContent>
      </Card>
    </section>
  );
}

/** Label and value pairs as a description list. */
function Facts({ items }: { items: readonly (readonly [string, ReactNode])[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-13 sm:grid-cols-2">
      {items.map(([label, value]) => (
        <div key={label} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-xs text-muted">{label}</dt>
          <dd className="min-w-0 break-words text-foreground tabular">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A small table with a visible caption; `numeric` columns align right. */
function Table({
  caption,
  columns,
  rows,
  testId,
}: {
  caption: string;
  columns: readonly { label: string; numeric?: boolean }[];
  rows: readonly { key: string; cells: readonly ReactNode[] }[];
  testId?: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-13" data-testid={testId}>
        <caption className="pb-1.5 text-left text-xs font-medium text-muted-strong">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-strong">
            {columns.map((column) => (
              <th
                key={column.label}
                scope="col"
                className={cn('px-2 py-1.5 font-medium first:pl-0', column.numeric && 'text-right')}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-border last:border-b-0">
              {row.cells.map((cell, index) => (
                <td
                  key={columns[index]?.label ?? index}
                  className={cn(
                    'px-2 py-1.5 align-top first:pl-0',
                    columns[index]?.numeric && 'text-right tabular',
                  )}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A remark under a block's facts (a status file that is old or absent). */
function Note({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <Alert tone="warning" assertive={false} data-testid={testId}>
      {children}
    </Alert>
  );
}

function ResourcesBlock({ host }: { host: HostInfo }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const unreadable = <span className="text-muted">{t('system.host.unreadable')}</span>;
  const memory = memoryText(host, lang);
  return (
    <Block id="resources">
      <Facts
        items={[
          [
            t('system.host.cpu'),
            host.cpu_percent === null ? (
              <span className="text-muted" data-testid="system-cpu-pending">
                {t(`system.host.${cpuPending(host)}`)}
              </span>
            ) : (
              formatPercent(host.cpu_percent, lang)
            ),
          ],
          [t('system.host.memory'), memory ?? unreadable],
          [
            t('system.host.uptime'),
            host.uptime_seconds === null ? unreadable : formatDuration(host.uptime_seconds, lang),
          ],
        ]}
      />
    </Block>
  );
}

function DisksBlock({ drives, checkedAt }: { drives: DriveInfo[]; checkedAt: string }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Block id="disks">
      {drives.length === 0 ? (
        <p className="text-13 text-muted">{t('system.drive.noDrives')}</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {drives.map((drive) => (
            <li key={drive.path} className="flex flex-col gap-2">
              <DriveMeter drive={drive} />
              <p
                className="flex items-start gap-2 text-13 text-foreground"
                data-testid="drive-forecast"
              >
                <TrendingUp aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                {forecastText(drive, checkedAt, t, lang)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Block>
  );
}

function DatabaseBlock({ database }: { database: DatabaseInfo }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Block id="database">
      <Facts
        items={[
          [t('system.database.storage'), t(`system.database.storageName.${database.storage}`)],
          [
            t('system.database.total'),
            database.total_bytes === null ? (
              <span className="text-muted">{t('system.database.unmeasured')}</span>
            ) : (
              formatBytes(database.total_bytes, lang)
            ),
          ],
          [t('system.database.measuredAt'), dateTime(database.measured_at)],
        ]}
      />
      {database.tables.length > 0 ? (
        <Table
          caption={t('system.database.tablesCaption')}
          columns={[
            { label: t('system.database.table') },
            { label: t('system.database.size'), numeric: true },
          ]}
          rows={database.tables.map((table) => ({
            key: table.name,
            cells: [
              <span key="name" className="font-mono text-xs">
                {table.name}
              </span>,
              formatBytes(table.bytes, lang),
            ],
          }))}
          testId="system-tables"
        />
      ) : null}
    </Block>
  );
}

/** What an old or absent status file means for the values shown under it. */
function StatusFileNote({ state, area }: { state: StatusFileState; area: 'collector' | 'ingest' }) {
  const { t } = useTranslation();
  if (state === 'running') return null;
  return (
    <Note testId={`system-${area}-${state}`}>
      {t(state === 'stale' ? `system.${area}.staleNote` : `system.${area}.missingNote`)}
    </Note>
  );
}

/**
 * Toplayıcı: configured sources only, under their configured names; every other sender is only
 * counted (contract 2.5.2: the server never returns a sender's address, and neither does the page).
 */
function CollectorBlock({ collector }: { collector: CollectorInfo }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const number = (value: number) => formatNumber(value, 0, lang);
  const unknown = collector.unknown_senders;
  return (
    <Block id="collector" status={<StateBadge style={STATUS_FILE_STYLE[collector.state]} />}>
      <StatusFileNote state={collector.state} area="collector" />
      <Facts
        items={[
          [t('system.collector.updatedAt'), dateTime(collector.updated_at)],
          [t('system.collector.startedAt'), dateTime(collector.started_at)],
          [
            t('system.collector.unknownSenders'),
            <span
              key="unknown"
              className="flex flex-col gap-0.5"
              data-testid="system-unknown-senders"
            >
              <span>
                {t('system.collector.unknownSendersValue', {
                  count: unknown.count,
                  formatted: number(unknown.count),
                  messages: number(unknown.messages),
                  size: formatBytes(unknown.bytes, lang),
                })}
              </span>
              {unknown.count > 0 ? (
                <span className="text-xs text-muted">
                  {t('system.collector.unknownSendersHint')}
                </span>
              ) : null}
            </span>,
          ],
          [t('system.collector.droppedUnknown'), number(collector.dropped_unknown)],
          [t('system.collector.droppedTransport'), number(collector.dropped_transport)],
          [t('system.collector.writeFailures'), number(collector.write_failures)],
          [t('system.collector.writeErrorSince'), dateTime(collector.write_error_since)],
          [t('system.collector.tornLines'), number(collector.torn_lines)],
          [t('system.collector.tcpConnections'), number(collector.tcp_connections)],
          [t('system.collector.tcpFramingErrors'), number(collector.tcp_framing_errors)],
        ]}
      />
      {collector.sources.length === 0 ? (
        <p className="text-13 text-muted">{t('system.collector.noSources')}</p>
      ) : (
        <Table
          caption={t('system.collector.sourcesCaption')}
          columns={[
            { label: t('system.collector.source') },
            { label: t('system.collector.messages'), numeric: true },
            { label: t('system.collector.bytes'), numeric: true },
            { label: t('system.collector.lastReceived') },
          ]}
          rows={collector.sources.map((source) => ({
            key: source.name,
            cells: [
              <span key="name" className="font-mono text-xs">
                {source.name}
              </span>,
              number(source.messages),
              formatBytes(source.bytes, lang),
              source.last_received ? (
                formatDateTime(source.last_received)
              ) : (
                <span key="never" className="text-muted">
                  {t('system.collector.never')}
                </span>
              ),
            ],
          }))}
          testId="system-collector-sources"
        />
      )}
    </Block>
  );
}

function IngestBlock({ ingest }: { ingest: IngestInfo }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const rate = (value: number | null) => (value === null ? NONE : formatNumber(value, 1, lang));
  return (
    <Block id="ingest" status={<StateBadge style={STATUS_FILE_STYLE[ingest.state]} />}>
      <StatusFileNote state={ingest.state} area="ingest" />
      <Facts
        items={[
          [t('system.ingest.updatedAt'), dateTime(ingest.updated_at)],
          [
            t('system.ingest.serviceState'),
            ingest.service_state ? (
              <span className="font-mono text-xs">{ingest.service_state}</span>
            ) : (
              NONE
            ),
          ],
          [
            t('system.ingest.database'),
            <StateBadge key="database" style={DATABASE_STYLE[databaseState(ingest.database_ok)]} />,
          ],
          [
            t('system.ingest.workers'),
            ingest.workers_active === null || ingest.workers_configured === null
              ? NONE
              : t('system.ingest.workersValue', {
                  active: formatNumber(ingest.workers_active, 0, lang),
                  configured: formatNumber(ingest.workers_configured, 0, lang),
                }),
          ],
          [t('system.ingest.rate'), rate(ingest.lines_per_second)],
          [t('system.ingest.unknownSources'), formatNumber(ingest.unknown_sources, 0, lang)],
        ]}
      />
      {ingest.sources.length === 0 ? (
        <p className="text-13 text-muted">{t('system.ingest.noSources')}</p>
      ) : (
        <Table
          caption={t('system.ingest.sourcesCaption')}
          columns={[
            { label: t('system.ingest.source') },
            { label: t('system.ingest.lagBytes'), numeric: true },
            { label: t('system.ingest.lagSeconds'), numeric: true },
            { label: t('system.ingest.rate'), numeric: true },
            { label: t('system.ingest.unfinishedDays'), numeric: true },
            { label: t('system.ingest.error') },
          ]}
          rows={ingest.sources.map((source) => ({
            key: source.name,
            cells: [
              <span key="name" className="font-mono text-xs">
                {source.name}
              </span>,
              formatBytes(source.lag_bytes, lang),
              source.lag_seconds === null ? NONE : formatDuration(source.lag_seconds, lang),
              rate(source.lines_per_second),
              formatNumber(source.unfinished_days, 0, lang),
              source.has_error ? (
                <Badge key="error" tone="danger" icon={STATUS_FILE_STYLE.missing.icon}>
                  {t('system.ingest.hasError')}
                </Badge>
              ) : (
                <span key="error" className="text-muted">
                  {t('system.ingest.noError')}
                </span>
              ),
            ],
          }))}
          testId="system-ingest-sources"
        />
      )}
    </Block>
  );
}

/** Hizmetler: the three services by their status files, ArciApi, the agent and ApplySystem. */
function ServicesBlock({ health }: { health: SystemHealth }) {
  const { t } = useTranslation();
  const apply = health.apply_system;
  return (
    <Block id="services">
      <Table
        caption={t('system.services.caption')}
        columns={[
          { label: t('system.services.name') },
          { label: t('system.services.state') },
          { label: t('system.services.updatedAt') },
        ]}
        rows={[
          ...health.services.map((service) => ({
            key: service.name,
            cells: [
              <span key="name" className="font-mono text-xs">
                {service.name}
              </span>,
              <StateBadge key="state" style={STATUS_FILE_STYLE[service.state]} />,
              dateTime(service.updated_at),
            ],
          })),
          {
            key: 'agent',
            cells: [
              t('system.services.agent'),
              <StateBadge key="state" style={AGENT_STYLE[health.agent.state]} />,
              dateTime(health.agent.heartbeat_at),
            ],
          },
          {
            key: 'apply-system',
            cells: [
              t('system.services.applySystem'),
              <span key="state" className="flex flex-col items-start gap-0.5">
                <StateBadge style={APPLY_SYSTEM_STYLE[apply.state]} />
                {apply.code ? (
                  <span className="text-xs text-muted">
                    {t('system.services.code')}: <span className="font-mono">{apply.code}</span>
                  </span>
                ) : null}
              </span>,
              dateTime(apply.at),
            ],
          },
        ]}
        testId="system-services"
      />
    </Block>
  );
}

function TasksBlock({ health }: { health: SystemHealth }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Block id="tasks">
      <Table
        caption={t('system.tasks.caption')}
        columns={[
          { label: t('system.tasks.task') },
          { label: t('system.tasks.schedule') },
          { label: t('system.tasks.state') },
          { label: t('system.tasks.lastEvidence') },
          { label: t('system.tasks.evidence') },
        ]}
        rows={health.tasks.map((task) => ({
          key: task.task,
          cells: [
            <span key="task" className="font-mono text-xs">
              {task.task}
            </span>,
            scheduleText(task, lang),
            <StateBadge key="state" style={TASK_STYLE[task.state]} />,
            dateTime(task.last_evidence_at),
            task.evidence ? (
              <span key="evidence" className="font-mono text-xs break-all">
                {task.evidence}
              </span>
            ) : (
              NONE
            ),
          ],
        }))}
        testId="system-tasks"
      />
    </Block>
  );
}

function JobsBlock({ health }: { health: SystemHealth }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Block id="jobs">
      <Table
        caption={t('system.jobs.caption')}
        columns={[
          { label: t('system.jobs.job') },
          { label: t('system.jobs.schedule') },
          { label: t('system.jobs.module') },
          { label: t('system.jobs.state') },
          { label: t('system.jobs.lastStarted') },
          { label: t('system.jobs.duration'), numeric: true },
          { label: t('system.jobs.failures'), numeric: true },
          { label: t('system.jobs.nextDue') },
        ]}
        rows={health.jobs.map((job) => ({
          key: job.job,
          cells: [
            <span key="job" className="font-mono text-xs">
              {job.job}
            </span>,
            scheduleText(job, lang),
            job.module ? (
              <span key="module" className="font-mono text-xs">
                {job.module}
              </span>
            ) : (
              NONE
            ),
            <StateBadge key="state" style={JOB_STYLE[job.state]} />,
            dateTime(job.last_started),
            job.duration_ms === null ? NONE : formatDistance(job.duration_ms, lang),
            formatNumber(job.failures, 0, lang),
            dateTime(job.next_due),
          ],
        }))}
        testId="system-jobs"
      />
    </Block>
  );
}

/** Saat: the configured time servers, what ApplySystem gave Windows, the samples of 24 hours. */
function ClockBlock({ health }: { health: SystemHealth }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const time = health.time;
  const ms = (value: number | null, decimals = 1) =>
    value === null ? NONE : t('system.clock.ms', { value: formatNumber(value, decimals, lang) });
  const notSet = <span className="text-muted">{t('system.clock.notSet')}</span>;
  return (
    <Block id="clock">
      <Facts
        items={[
          [
            t('system.clock.ntpServer'),
            time.ntp_server ? <span className="font-mono text-xs">{time.ntp_server}</span> : notSet,
          ],
          [
            t('system.clock.fallbackServer'),
            time.fallback_server ? (
              <span className="font-mono text-xs">{time.fallback_server}</span>
            ) : (
              notSet
            ),
          ],
          [t('system.clock.alertOffset'), ms(time.alert_offset_ms, 0)],
          [
            t('system.clock.appliedPeers'),
            time.applied_peers ? (
              <span className="font-mono text-xs break-all">{time.applied_peers}</span>
            ) : (
              <span className="text-muted">{t('system.clock.notApplied')}</span>
            ),
          ],
          [t('system.clock.appliedAt'), dateTime(time.applied_at)],
        ]}
      />
      {time.samples.length === 0 ? (
        <p className="text-13 text-muted">{t('system.clock.noSamples')}</p>
      ) : (
        <Table
          caption={t('system.clock.samplesCaption')}
          columns={[
            { label: t('system.clock.time') },
            { label: t('system.clock.server') },
            { label: t('system.clock.state') },
            { label: t('system.clock.offset'), numeric: true },
            { label: t('system.clock.delay'), numeric: true },
            { label: t('system.clock.stratum'), numeric: true },
          ]}
          rows={time.samples.map((sample) => ({
            key: sample.time,
            cells: [
              formatDateTime(sample.time),
              sample.server ? (
                <span key="server" className="font-mono text-xs">
                  {sample.server}
                </span>
              ) : (
                NONE
              ),
              <StateBadge
                key="state"
                style={SAMPLE_STYLE[sampleState(sample, time.alert_offset_ms)]}
              />,
              ms(sample.offset_ms),
              ms(sample.delay_ms),
              sample.stratum === null ? NONE : formatNumber(sample.stratum, 0, lang),
            ],
          }))}
          testId="system-clock-samples"
        />
      )}
    </Block>
  );
}

/**
 * Sürüm ve güncelleme: the installed versions, the licence maintenance of `GET /license`, and how
 * to upgrade — by hand, with the offline bundle; the page downloads and links nothing.
 */
function VersionBlock({
  version,
  license,
}: {
  version: VersionInfo;
  license: LicenseOut | undefined;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const maintenance = license?.maintenance ?? null;
  return (
    <Block id="version">
      <Facts
        items={[
          [t('system.version.product'), version.product],
          [t('system.version.version'), version.version],
          [t('system.version.core'), version.core_version],
          [t('system.version.releaseDate'), formatDay(version.release_date)],
          ...(license
            ? [
                [
                  t('system.version.maintenance'),
                  <span
                    key="maintenance"
                    className="flex flex-col items-start gap-1"
                    data-testid="system-maintenance"
                  >
                    <StateBadge style={MAINTENANCE_STYLE[maintenanceState(maintenance)]} />
                    {maintenance ? (
                      <span>
                        {maintenance.expired
                          ? t('license.page.status.maintenanceOver', {
                              until: licenseDayText(maintenance.until, lang),
                            })
                          : t('license.page.status.maintenanceLeft', {
                              until: licenseDayText(maintenance.until, lang),
                              count: maintenance.days_left,
                            })}
                      </span>
                    ) : null}
                    {maintenance ? (
                      <span className="text-xs text-muted">
                        {t(
                          maintenance.covers_version
                            ? 'system.version.coversVersion'
                            : 'system.version.notCoversVersion',
                        )}
                      </span>
                    ) : null}
                  </span>,
                ] as const,
              ]
            : []),
        ]}
      />
      <p className="flex items-start gap-2 text-13 text-foreground" data-testid="system-upgrade">
        <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info-ink" />
        {t('system.version.upgrade')}
      </p>
    </Block>
  );
}

export interface SystemViewProps {
  /** `GET /system`; `undefined` until it is answered. */
  summary: SystemSummary | undefined;
  /** `GET /system/health`; `undefined` until it is answered. */
  health: SystemHealth | undefined;
  /** `GET /license` for the maintenance line; without it the line is left out. */
  license: LicenseOut | undefined;
  /** The last request's failure (`null` when none); the data already shown stays. */
  error: unknown;
  /** A first answer is still on its way. */
  loading: boolean;
  /** When the shown data arrived (ms since the epoch; 0: not yet). */
  updatedAt: number;
  onRefresh: () => void;
}

/**
 * Sistem (`/system`, `system.read`; Wave 4 contract 2.8.2): the server's resources, drives with
 * the disk forecast, the database, the collector and the ingest, the services, scheduled tasks,
 * worker jobs and clock, and the versions. It only shows what it is given (the page of W4-F
 * fetches it, every 30 s in the background); loading, a failure and old data behave as on
 * Kaynaklar: a failure keeps the data shown and offers "Tekrar dene". No block names a person, a
 * client address, a device host name or a sender address (contract 0.1 item 4).
 */
export function SystemView({
  summary,
  health,
  license,
  error,
  loading,
  updatedAt,
  onRefresh,
}: SystemViewProps) {
  const { t } = useTranslation();

  const block = (id: SystemBlock): ReactNode => {
    if (HEALTH_BLOCKS.has(id) ? !health : !summary) {
      return loading ? (
        <Skeleton key={id} className={cn('h-40', WIDE_BLOCKS.has(id) && 'lg:col-span-2')} />
      ) : null;
    }
    switch (id) {
      case 'resources':
        return summary ? <ResourcesBlock key={id} host={summary.host} /> : null;
      case 'disks':
        return summary ? (
          <DisksBlock key={id} drives={summary.drives} checkedAt={summary.checked_at} />
        ) : null;
      case 'database':
        return summary ? <DatabaseBlock key={id} database={summary.database} /> : null;
      case 'collector':
        return summary ? <CollectorBlock key={id} collector={summary.collector} /> : null;
      case 'ingest':
        return summary ? <IngestBlock key={id} ingest={summary.ingest} /> : null;
      case 'services':
        return health ? <ServicesBlock key={id} health={health} /> : null;
      case 'tasks':
        return health ? <TasksBlock key={id} health={health} /> : null;
      case 'jobs':
        return health ? <JobsBlock key={id} health={health} /> : null;
      case 'clock':
        return health ? <ClockBlock key={id} health={health} /> : null;
      case 'version':
        return summary ? (
          <VersionBlock key={id} version={summary.version} license={license} />
        ) : null;
    }
  };

  const failed = error !== null && error !== undefined;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.system')}
        description={t('system.description')}
        actions={
          <>
            {updatedAt > 0 ? (
              <span className="text-13 text-muted tabular" aria-live="polite">
                {t('system.updated', { time: formatTime(updatedAt, true) })}
              </span>
            ) : null}
            <Button variant="secondary" size="sm" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('system.refresh')}
            </Button>
          </>
        }
      />

      {failed ? (
        <Alert tone="danger" title={t('system.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(error)}
            <Button size="sm" variant="secondary" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}

      {loading && !summary && !health ? (
        <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-96" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="system-blocks">
          {SYSTEM_BLOCKS.map(block)}
        </div>
      )}
    </div>
  );
}
