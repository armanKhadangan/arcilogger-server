import { ShieldBan } from 'lucide-react';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import { Badge } from '@/components/ui/badge';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import type {
  AppRow,
  BlockedRow,
  CategoryRow,
  DepartmentRow,
  DeviceUsage,
  HostnameRow,
  Metrics,
  SiteHost,
  SiteRow,
  SubjectRef,
  SubjectRow,
  TopSite,
  UrlRow,
} from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatBytes, formatDateTime, formatDuration, formatNumber, type Lang } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { activitySpan, tableHeight } from './usage-helpers';
import { addressOf, shareText, type DayTotals } from './usage-format';
import { categoryLabel, siteCategory } from './usage-model';
import { DrillButton, SubjectName } from './usage-parts';

/*
 * The tables of the usage views. Every table shows all rows (virtualised by DataTable), sorts by
 * every column in the browser and has its export (`actions`) and filters (`toolbar`) passed in.
 * Durations are estimates: the toolbars carry the "tahmini" hint (`EstimateHint`).
 */

interface TableFrame {
  caption: string;
  toolbar?: ReactNode;
  actions?: ReactNode;
  emptyMessage?: ReactNode;
  /** Maximum height of the scroll area. */
  maxHeight?: number;
}

function durationColumn<T>(
  id: string,
  label: string,
  get: (row: T) => number,
  lang: Lang,
  size = 100,
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: get,
    header: label,
    size,
    sortDescFirst: true,
    meta: { label, numeric: true, exportValue: (row: T) => get(row) },
    cell: (info) => formatDuration(info.getValue<number>(), lang),
  };
}

function bytesColumn<T>(
  id: string,
  label: string,
  get: (row: T) => number,
  lang: Lang,
  size = 95,
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: get,
    header: label,
    size,
    sortDescFirst: true,
    meta: { label, numeric: true },
    cell: (info) => formatBytes(info.getValue<number>(), lang),
  };
}

function countColumn<T>(
  id: string,
  label: string,
  get: (row: T) => number,
  lang: Lang,
  size = 85,
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: get,
    header: label,
    size,
    sortDescFirst: true,
    meta: { label, numeric: true },
    cell: (info) => formatNumber(info.getValue<number>(), 0, lang),
  };
}

function shareColumn<T>(
  id: string,
  label: string,
  get: (row: T) => number,
  lang: Lang,
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: get,
    header: label,
    size: 80,
    sortDescFirst: true,
    meta: { label, numeric: true },
    cell: (info) => shareText(info.getValue<number>(), lang),
  };
}

function timeColumn<T>(
  id: string,
  label: string,
  get: (row: T) => string | null,
  size = 160,
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: (row: T) => get(row) ?? '',
    header: label,
    size,
    sortDescFirst: true,
    meta: { label },
    cell: (info) => {
      const value = info.getValue<string>();
      return <span className="tabular">{value ? formatDateTime(value) : '—'}</span>;
    },
  };
}

function textColumn<T>(
  id: string,
  label: string,
  get: (row: T) => string | null,
  size = 140,
  options: { grow?: boolean; mono?: boolean; minSize?: number } = {},
): AnyColumnDef<T> {
  return {
    id,
    accessorFn: (row: T) => get(row) ?? '',
    header: label,
    size,
    ...(options.minSize ? { minSize: options.minSize } : {}),
    meta: {
      label,
      ...(options.grow ? { grow: true } : {}),
      ...(options.mono ? { mono: true } : {}),
    },
    cell: (info) => {
      const value = info.getValue<string>();
      return value ? (
        <span className="block truncate" title={visibleText(value)}>
          {visibleText(value)}
        </span>
      ) : (
        '—'
      );
    },
  };
}

/**
 * The metric columns of the subject and department tables. `site`: the figures of one site or
 * category (its time, off-hours part, volumes, sessions; no online time, blocked attempts or
 * first/last activity: the rollups keep none of them per site).
 */
function metricColumns<T>(
  get: (row: T) => Metrics,
  t: TFunction,
  lang: Lang,
  variant: 'full' | 'site' = 'full',
): AnyColumnDef<T>[] {
  const span: AnyColumnDef<T> = {
    id: 'span',
    accessorFn: (row) => get(row).first_activity ?? '',
    header: t('usage.metrics.firstLastShort'),
    size: 108,
    meta: { label: t('usage.metrics.firstLast') },
    cell: (info) => {
      const metrics = get(info.row.original);
      const text = activitySpan(metrics.first_activity, metrics.last_activity);
      return <span className="tabular">{text ?? '—'}</span>;
    },
  };
  const active = durationColumn(
    'active',
    variant === 'site' ? t('usage.metrics.time') : t('usage.metrics.activeShort'),
    (row: T) => get(row).active_seconds,
    lang,
    92,
  );
  const offHours = durationColumn(
    'off_hours',
    t('usage.metrics.offHours'),
    (row: T) => get(row).off_hours_active_seconds,
    lang,
    92,
  );
  const volumes = [
    bytesColumn(
      'download',
      t('usage.metrics.download'),
      (row: T) => get(row).download_bytes,
      lang,
      88,
    ),
    bytesColumn('upload', t('usage.metrics.upload'), (row: T) => get(row).upload_bytes, lang, 88),
    countColumn('sessions', t('usage.metrics.sessions'), (row: T) => get(row).sessions, lang, 74),
  ];
  if (variant === 'site') return [active, offHours, ...volumes];
  return [
    active,
    durationColumn(
      'online',
      t('usage.metrics.onlineShort'),
      (row: T) => get(row).online_seconds,
      lang,
      92,
    ),
    ...volumes,
    countColumn('blocked', t('usage.metrics.blocked'), (row: T) => get(row).blocked, lang, 84),
    offHours,
    span,
  ];
}

function topSitesText(sites: readonly TopSite[], lang: Lang, count = 3): string {
  return sites
    .slice(0, count)
    .map((site) => `${visibleText(site.domain)} (${formatDuration(site.seconds, lang)})`)
    .join(', ');
}

/** `www.example.com/path`: the web filter logs the path and the host separately. */
function fullUrl(hostname: string | null, url: string): string {
  return hostname && url.startsWith('/') ? `${hostname}${url}` : url;
}

/** What a blocked attempt was after: host and path (web, DNS) or nothing (an application). */
function blockedAddress(row: BlockedRow): string | null {
  const host = row.hostname ?? null;
  if (row.url) return fullUrl(host, row.url);
  return host;
}

function frameHeight(rows: number, maxHeight = 520): number {
  return tableHeight(rows, maxHeight);
}

/** Persons or devices with every metric and their top sites. */
export function SubjectTable({
  rows,
  onOpen,
  subjectLabel,
  variant = 'list',
  hideDepartment = false,
  ...frame
}: TableFrame & {
  rows: SubjectRow[];
  onOpen: (subject: SubjectRef) => void;
  /** Header of the first column ("Kişi", "Cihaz", "Kullanıcı"). */
  subjectLabel: string;
  /** `site`: the figures are one site's or category's (no top sites column). */
  variant?: 'list' | 'site';
  /** Leave the department column out (a department's own table). */
  hideDepartment?: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<SubjectRow>[]>(() => {
    const list: AnyColumnDef<SubjectRow>[] = [
      {
        id: 'subject',
        accessorFn: (row) => row.subject.label,
        header: subjectLabel,
        size: 176,
        meta: { label: subjectLabel, exportValue: (row: SubjectRow) => row.subject.label },
        cell: (info) => {
          const subject = info.row.original.subject;
          const address = addressOf(subject);
          return (
            <span className="flex min-w-0 flex-col leading-tight">
              <DrillButton
                onClick={() => {
                  onOpen(subject);
                }}
                title={t('usage.openDetail', { name: visibleText(subject.label) })}
              >
                <SubjectName subject={subject} />
              </DrillButton>
              {address && address !== subject.label ? (
                <span className="truncate font-mono text-xs text-muted-strong">{address}</span>
              ) : null}
            </span>
          );
        },
      },
      ...(hideDepartment
        ? []
        : [
            textColumn<SubjectRow>(
              'department',
              t('usage.columns.department'),
              (row) => row.department,
              120,
            ),
          ]),
      ...metricColumns<SubjectRow>(
        (row) => row.metrics,
        t,
        lang,
        variant === 'site' ? 'site' : 'full',
      ),
    ];
    if (variant === 'list')
      list.push({
        id: 'top_sites',
        accessorFn: (row) => topSitesText(row.top_sites, lang),
        header: t('usage.columns.topSites'),
        size: 220,
        minSize: 150,
        enableSorting: false,
        meta: { label: t('usage.columns.topSites'), grow: true },
        cell: (info) => {
          const text = info.getValue<string>();
          return text ? (
            <span
              className="block truncate"
              title={topSitesText(info.row.original.top_sites, lang, 10)}
            >
              {text}
            </span>
          ) : (
            '—'
          );
        },
      });
    return list;
  }, [t, lang, onOpen, subjectLabel, variant, hideDepartment]);
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.subject.key}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
      initialDensity="comfortable"
    />
  );
}

/** Departments with every metric. */
export function DepartmentTable({
  rows,
  onOpen,
  ...frame
}: TableFrame & { rows: DepartmentRow[]; onOpen: (department: string) => void }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<DepartmentRow>[]>(
    () => [
      {
        id: 'department',
        accessorKey: 'department',
        header: t('usage.columns.department'),
        size: 190,
        meta: { label: t('usage.columns.department') },
        cell: (info) => {
          const name = info.row.original.department;
          return (
            <DrillButton
              onClick={() => {
                onOpen(name);
              }}
              title={t('usage.openDetail', { name: visibleText(name) })}
            >
              {visibleText(name)}
            </DrillButton>
          );
        },
      },
      {
        id: 'source',
        accessorKey: 'source',
        header: t('usage.columns.source'),
        size: 110,
        meta: { label: t('usage.columns.source') },
        cell: (info) => (
          <Badge tone={info.row.original.source === 'person' ? 'neutral' : 'outline'}>
            {t(`usage.sources.${info.row.original.source}`)}
          </Badge>
        ),
      },
      countColumn(
        'active_users',
        t('usage.metrics.activeUsers'),
        (row) => row.active_users,
        lang,
        100,
      ),
      ...metricColumns<DepartmentRow>((row) => row.metrics, t, lang),
    ],
    [t, lang, onOpen],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => `${row.department}#${row.source}`}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
      initialDensity="comfortable"
    />
  );
}

/** Sites: time, share, raw time, volumes, sessions (and users). */
export function SiteTable({
  rows,
  onOpen,
  onCategory,
  showUsers = true,
  ...frame
}: TableFrame & {
  rows: SiteRow[];
  onOpen: (domain: string) => void;
  onCategory?: (category: string) => void;
  showUsers?: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<SiteRow>[]>(() => {
    const list: AnyColumnDef<SiteRow>[] = [
      {
        id: 'domain',
        accessorKey: 'domain',
        header: t('usage.columns.domain'),
        size: 200,
        minSize: 160,
        meta: { label: t('usage.columns.domain'), grow: true },
        cell: (info) => {
          const row = info.row.original;
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <DrillButton
                onClick={() => {
                  onOpen(row.domain);
                }}
                title={t('usage.openDetail', { name: visibleText(row.domain) })}
                className={row.kind === 'ip' ? 'font-mono text-xs' : undefined}
              >
                {visibleText(row.domain)}
              </DrillButton>
              {row.kind !== 'domain' ? (
                <Badge tone="outline" title={t(`usage.siteKind.${row.kind}Hint`)}>
                  {t(`usage.siteKind.${row.kind}`)}
                </Badge>
              ) : null}
              {row.background ? (
                <Badge tone="neutral" title={t('usage.siteKind.backgroundHint')}>
                  {t('usage.siteKind.background')}
                </Badge>
              ) : null}
            </span>
          );
        },
      },
      {
        // The API's name ("Bilinmiyor" in every language) goes to the drill-down; the cell
        // shows (and sorts by) the label, translated for the unknown category.
        id: 'category',
        accessorFn: (row) => categoryLabel(row.category, t),
        header: t('usage.columns.category'),
        size: 170,
        meta: { label: t('usage.columns.category') },
        cell: (info) => {
          const category = siteCategory(info.row.original.category);
          const text = visibleText(info.getValue<string>());
          return onCategory ? (
            <DrillButton
              onClick={() => {
                onCategory(category);
              }}
              title={t('usage.openDetail', { name: text })}
            >
              {text}
            </DrillButton>
          ) : (
            <span className="block truncate">{text}</span>
          );
        },
      },
      durationColumn('seconds', t('usage.metrics.time'), (row) => row.seconds, lang),
      shareColumn('share', t('usage.metrics.share'), (row) => row.share, lang),
      durationColumn('raw_seconds', t('usage.metrics.rawTime'), (row) => row.raw_seconds, lang),
      bytesColumn('download', t('usage.metrics.download'), (row) => row.download_bytes, lang),
      bytesColumn('upload', t('usage.metrics.upload'), (row) => row.upload_bytes, lang),
      countColumn('sessions', t('usage.metrics.sessions'), (row) => row.sessions, lang, 80),
    ];
    if (showUsers)
      list.push(countColumn('users', t('usage.metrics.users'), (row) => row.users, lang, 85));
    return list;
  }, [t, lang, onOpen, onCategory, showUsers]);
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.domain}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}

/** Categories: time, share, volumes, users. */
export function CategoryTable({
  rows,
  onOpen,
  colourOf,
  ...frame
}: TableFrame & {
  rows: CategoryRow[];
  onOpen: (category: string) => void;
  /** The chart colour of a category (the donut's legend), if shown next to a chart. */
  colourOf?: (category: string) => string | null;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<CategoryRow>[]>(
    () => [
      {
        // Like the site table's: the API's name opens the category, the label is shown.
        id: 'category',
        accessorFn: (row) => categoryLabel(row.category, t),
        header: t('usage.columns.category'),
        size: 220,
        minSize: 160,
        meta: { label: t('usage.columns.category'), grow: true },
        cell: (info) => {
          const category = info.row.original.category;
          const text = visibleText(info.getValue<string>());
          const colour = colourOf?.(category) ?? null;
          return (
            <span className="flex min-w-0 items-center gap-2">
              {colour ? (
                <span
                  aria-hidden="true"
                  className="size-2.5 shrink-0 rounded-sm"
                  style={{ background: colour }}
                />
              ) : null}
              <DrillButton
                onClick={() => {
                  onOpen(category);
                }}
                title={t('usage.openDetail', { name: text })}
              >
                {text}
              </DrillButton>
            </span>
          );
        },
      },
      durationColumn('seconds', t('usage.metrics.time'), (row) => row.seconds, lang),
      shareColumn('share', t('usage.metrics.share'), (row) => row.share, lang),
      bytesColumn('download', t('usage.metrics.download'), (row) => row.download_bytes, lang),
      bytesColumn('upload', t('usage.metrics.upload'), (row) => row.upload_bytes, lang),
      countColumn('users', t('usage.metrics.users'), (row) => row.users, lang),
    ],
    [t, lang, onOpen, colourOf],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.category}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}

/** Applications (FortiGate application control): time and volume. */
export function AppTable({ rows, ...frame }: TableFrame & { rows: AppRow[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<AppRow>[]>(
    () => [
      textColumn('app', t('usage.columns.app'), (row) => row.app, 200, {
        grow: true,
        minSize: 140,
      }),
      textColumn('appcat', t('usage.columns.appcat'), (row) => row.appcat, 170),
      durationColumn('seconds', t('usage.metrics.time'), (row) => row.seconds, lang),
      bytesColumn('bytes', t('usage.metrics.bytes'), (row) => row.bytes, lang),
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.app}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}

/** Every blocked attempt, newest first. */
export function BlockedTable({ rows, ...frame }: TableFrame & { rows: BlockedRow[] }) {
  const { t } = useTranslation();
  const columns = useMemo<AnyColumnDef<BlockedRow>[]>(
    () => [
      timeColumn('time', t('usage.columns.time'), (row) => row.time, 165),
      textColumn('domain', t('usage.columns.domain'), (row) => row.domain, 170),
      textColumn('address', t('usage.columns.address'), (row) => blockedAddress(row), 220, {
        grow: true,
        minSize: 150,
        mono: true,
      }),
      textColumn('app', t('usage.columns.app'), (row) => row.app ?? null, 130),
      textColumn('category', t('usage.columns.category'), (row) => row.category, 170),
      textColumn('action', t('usage.columns.action'), (row) => row.action, 110),
      {
        id: 'source',
        accessorFn: (row) => t(`usage.blockSource.${row.source}`),
        header: t('usage.columns.blockSource'),
        size: 150,
        meta: { label: t('usage.columns.blockSource') },
      },
    ],
    [t],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row, index) => `${row.time}#${index}`}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}

/** URLs of the web filter log, newest first. */
export function UrlTable({ rows, ...frame }: TableFrame & { rows: UrlRow[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<UrlRow>[]>(
    () => [
      textColumn('url', t('usage.columns.url'), (row) => fullUrl(row.hostname, row.url), 320, {
        grow: true,
        minSize: 220,
        mono: true,
      }),
      textColumn('hostname', t('usage.columns.hostname'), (row) => row.hostname, 190),
      {
        id: 'blocked',
        accessorFn: (row) => (row.blocked ? 1 : 0),
        header: t('usage.columns.blocked'),
        size: 105,
        sortDescFirst: true,
        meta: {
          label: t('usage.columns.blocked'),
          exportValue: (row: UrlRow) => (row.blocked ? t('usage.urls.blocked') : ''),
        },
        cell: (info) =>
          info.row.original.blocked ? (
            <Badge tone="danger" icon={ShieldBan}>
              {t('usage.urls.blocked')}
            </Badge>
          ) : null,
      },
      textColumn('category', t('usage.columns.category'), (row) => row.category, 170),
      countColumn('hits', t('usage.metrics.hits'), (row) => row.hits, lang, 80),
      timeColumn('first_seen', t('usage.columns.firstSeen'), (row) => row.first_seen),
      timeColumn('last_seen', t('usage.columns.lastSeen'), (row) => row.last_seen),
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row, index) => `${row.url}#${index}`}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}

/** Host names of a subject's site or of a site: time, volume, sessions. */
export function HostnameTable({
  rows,
  ...frame
}: TableFrame & { rows: (HostnameRow | SiteHost)[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<HostnameRow | SiteHost>[]>(
    () => [
      textColumn('hostname', t('usage.columns.hostname'), (row) => row.hostname, 260, {
        grow: true,
        minSize: 180,
      }),
      durationColumn('seconds', t('usage.metrics.time'), (row) => row.seconds, lang),
      bytesColumn('bytes', t('usage.metrics.bytes'), (row) => row.bytes, lang),
      countColumn('sessions', t('usage.metrics.sessions'), (row) => row.sessions, lang, 80),
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.hostname}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight ?? 360)}
    />
  );
}

/**
 * A person's devices with their figures. Not links: the traffic of an assigned device belongs to
 * its person, so the device has no usage view of its own (the API answers 404 for its key).
 */
export function DeviceUsageTable({ rows, ...frame }: TableFrame & { rows: DeviceUsage[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<DeviceUsage>[]>(
    () => [
      {
        id: 'device',
        accessorFn: (row) => row.device.label,
        header: t('usage.columns.device'),
        size: 200,
        meta: { label: t('usage.columns.device') },
        cell: (info) => {
          const device = info.row.original.device;
          const address = addressOf(device);
          return (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-medium text-foreground">
                <SubjectName subject={device} />
              </span>
              {address && address !== device.label ? (
                <span className="truncate font-mono text-xs text-muted-strong">{address}</span>
              ) : null}
            </span>
          );
        },
      },
      ...metricColumns<DeviceUsage>((row) => row.metrics, t, lang),
      {
        id: 'top_sites',
        accessorFn: (row) => topSitesText(row.top_sites, lang),
        header: t('usage.columns.topSites'),
        size: 220,
        minSize: 160,
        enableSorting: false,
        meta: { label: t('usage.columns.topSites'), grow: true },
        cell: (info) => (
          <span
            className="block truncate"
            title={topSitesText(info.row.original.top_sites, lang, 10)}
          >
            {info.getValue<string>() || '—'}
          </span>
        ),
      },
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.device.key}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
      initialDensity="comfortable"
    />
  );
}

/** A period day by day (with the "Toplam" line above the table). */
export function DayTable({ rows, ...frame }: TableFrame & { rows: DayTotals[] }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const columns = useMemo<AnyColumnDef<DayTotals>[]>(
    () => [
      {
        id: 'day',
        accessorKey: 'day',
        header: t('usage.columns.day'),
        size: 130,
        meta: { label: t('usage.columns.day') },
        cell: (info) => {
          const day = info.getValue<string>();
          return (
            <span className="tabular">{`${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}`}</span>
          );
        },
      },
      durationColumn(
        'active',
        t('usage.metrics.activeShort'),
        (row) => row.active_seconds,
        lang,
        110,
      ),
      durationColumn(
        'online',
        t('usage.metrics.onlineShort'),
        (row) => row.online_seconds,
        lang,
        110,
      ),
      bytesColumn('download', t('usage.metrics.download'), (row) => row.download_bytes, lang, 110),
      bytesColumn('upload', t('usage.metrics.upload'), (row) => row.upload_bytes, lang, 110),
      textColumn('top_domain', t('usage.columns.topDomain'), (row) => row.top_domain, 200, {
        grow: true,
        minSize: 140,
      }),
    ],
    [t, lang],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      caption={frame.caption}
      getRowId={(row) => row.day}
      toolbar={frame.toolbar}
      actions={frame.actions}
      emptyMessage={frame.emptyMessage}
      height={frameHeight(rows.length + 1, frame.maxHeight)}
    />
  );
}
