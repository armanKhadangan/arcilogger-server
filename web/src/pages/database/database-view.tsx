import { CircleCheck, CircleX, Database, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { ChartFrame } from '@/components/charts/chart-frame';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage, type ApiError } from '@/lib/api';
import { formatBytes, formatDate, formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { DatabaseDetails } from '@/lib/system-types';

import { DriveMeter } from '../system/drive-meter';
import { forecastText } from '../system/system-model';

import { engineText, growthText } from './database-model';

const NONE = '—';

/** A card with a level-2 heading (`database-<id>`), like the Sistem page's blocks. */
function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const headingId = `database-${id}`;
  return (
    <section aria-labelledby={headingId} data-testid={headingId} className="min-w-0">
      <Card className="flex h-full flex-col">
        <CardHeader>
          <h2 id={headingId} className="text-sm font-semibold text-foreground">
            {title}
          </h2>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">{children}</CardContent>
      </Card>
    </section>
  );
}

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

function EngineSection({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const connections =
    database.connections === null
      ? NONE
      : database.max_connections === null
        ? formatNumber(database.connections, 0, lang)
        : t('database.engine.connectionsValue', {
            count: formatNumber(database.connections, 0, lang),
            limit: formatNumber(database.max_connections, 0, lang),
          });
  return (
    <Section id="engine" title={t('database.engine.title')}>
      <Facts
        items={[
          [t('database.engine.product'), engineText(database, t)],
          [t('database.engine.name'), database.name ?? NONE],
          [t('database.engine.address'), `${database.host}:${database.port}`],
          [t('database.engine.connections'), connections],
        ]}
      />
    </Section>
  );
}

function SizeSection({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Section id="size" title={t('database.size.title')}>
      <Facts
        items={[
          [
            t('database.size.total'),
            database.total_bytes === null ? (
              <span className="text-muted">{t('database.size.unmeasured')}</span>
            ) : (
              <span className="text-lg font-semibold">
                {formatBytes(database.total_bytes, lang)}
              </span>
            ),
          ],
          [t('database.size.growth'), growthText(database.growth, t, lang)],
          [
            t('database.size.window'),
            database.window_days === null
              ? NONE
              : t('database.size.windowValue', {
                  days: formatNumber(database.window_days, 0, lang),
                }),
          ],
          [
            t('database.size.measuredAt'),
            database.measured_at ? formatDateTime(database.measured_at) : NONE,
          ],
        ]}
      />
      <p className="text-13 text-muted">{t('database.size.windowNote')}</p>
    </Section>
  );
}

function HistoryChart({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  if (database.history.length === 0) {
    return (
      <Section id="history" title={t('database.chart.title')}>
        <p className="text-13 text-muted">{t('database.chart.empty')}</p>
      </Section>
    );
  }
  const rows = database.history.map((sample) => ({ day: sample.day, bytes: sample.bytes }));
  return (
    <ChartFrame
      title={t('database.chart.title')}
      description={t('database.chart.caption')}
      table={{
        columns: [t('database.chart.day'), t('database.chart.size')],
        rows: rows.map((row) => [formatDate(row.day), formatBytes(row.bytes, lang)]),
      }}
    >
      <StackedAreaChart
        data={rows}
        xKey="day"
        series={[{ key: 'bytes', label: t('database.chart.size') }]}
        formatValue={(value) => formatBytes(value, lang)}
        formatX={(value) => formatDate(String(value)).slice(0, 5)}
        fitValues
      />
    </ChartFrame>
  );
}

function DriveSection({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const drive = database.data_drive;
  return (
    <Section id="drive" title={t('database.drive.title')}>
      {drive ? (
        <>
          <DriveMeter drive={drive} />
          <p className="text-13 text-foreground">
            {forecastText(drive, database.checked_at, t, lang)}
          </p>
          <p className="text-13 text-muted">{t('database.drive.note')}</p>
        </>
      ) : (
        <p className="text-13 text-muted">{t('database.drive.none')}</p>
      )}
    </Section>
  );
}

function BackupSection({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const backup = database.last_backup;
  return (
    <Section id="backup" title={t('database.backup.title')}>
      {backup ? (
        <>
          <div>
            {backup.ok ? (
              <Badge tone="success" icon={CircleCheck}>
                {t('database.backup.ok')}
              </Badge>
            ) : (
              <Badge tone="danger" icon={CircleX}>
                {t('database.backup.failed')}
              </Badge>
            )}
          </div>
          <Facts
            items={[
              [
                t('database.backup.finished'),
                backup.finished ? formatDateTime(backup.finished) : NONE,
              ],
              [
                t('database.backup.size'),
                backup.bytes === null ? NONE : formatBytes(backup.bytes, lang),
              ],
              [t('database.backup.file'), backup.file ?? NONE],
            ]}
          />
        </>
      ) : (
        <p className="text-13 text-muted">{t('database.backup.none')}</p>
      )}
      <Link
        to="/settings/backup"
        className="w-fit text-13 font-medium text-primary underline-offset-4 hover:underline"
      >
        {t('database.backup.settings')}
      </Link>
    </Section>
  );
}

function TablesSection({ database }: { database: DatabaseDetails }) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Section id="tables" title={t('database.tables.title')}>
      {database.tables.length === 0 ? (
        <p className="text-13 text-muted">{t('database.tables.none')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-13" data-testid="database-table-sizes">
            <caption className="pb-1.5 text-left text-xs font-medium text-muted-strong">
              {t('database.tables.caption')}
            </caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-strong">
                <th scope="col" className="py-1.5 pr-2 font-medium">
                  {t('database.tables.table')}
                </th>
                <th scope="col" className="py-1.5 pl-2 text-right font-medium">
                  {t('database.tables.size')}
                </th>
              </tr>
            </thead>
            <tbody>
              {database.tables.map((table) => (
                <tr key={table.name} className="border-b border-border last:border-b-0">
                  <th scope="row" className="py-1.5 pr-2 text-left font-normal">
                    <code className="font-mono text-xs">{table.name}</code>
                  </th>
                  <td className="py-1.5 pl-2 text-right tabular">
                    {formatBytes(table.bytes, lang)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

export interface DatabaseViewProps {
  database: DatabaseDetails | undefined;
  error: ApiError | Error | null | undefined;
  loading: boolean;
  updatedAt: number;
  onRefresh: () => void;
}

/**
 * Veritabanı (`/database`, `system.read`; Wave 5 addendum C, C7): the database in use, its size,
 * daily growth and the days it keeps, the data drive with its forecast, the last backup and the
 * largest tables. Names of tables, sizes and counts only: nothing about log subjects.
 */
export function DatabaseView({
  database,
  error,
  loading,
  updatedAt,
  onRefresh,
}: DatabaseViewProps) {
  const { t } = useTranslation();
  const failed = error !== null && error !== undefined;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.database')}
        description={t('database.description')}
        actions={
          <>
            {updatedAt > 0 ? (
              <span className="text-13 text-muted tabular" aria-live="polite">
                {t('database.updated', { time: formatTime(updatedAt, true) })}
              </span>
            ) : null}
            <Button variant="secondary" size="sm" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('database.refresh')}
            </Button>
          </>
        }
      />

      {failed ? (
        <Alert tone="danger" title={t('database.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(error)}
            <Button size="sm" variant="secondary" onClick={onRefresh}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}

      {database ? (
        <div className="@container flex flex-col gap-4" data-testid="database-sections">
          <div className="grid grid-cols-1 gap-4 @4xl:grid-cols-2">
            <EngineSection database={database} />
            <SizeSection database={database} />
          </div>
          <HistoryChart database={database} />
          <div className="grid grid-cols-1 gap-4 @4xl:grid-cols-2">
            <DriveSection database={database} />
            <BackupSection database={database} />
          </div>
          <TablesSection database={database} />
        </div>
      ) : loading ? (
        <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-96" />
        </div>
      ) : failed ? null : (
        <p className="flex items-center gap-2 text-13 text-muted">
          <Database aria-hidden="true" className="size-4" />
          {t('database.unknown')}
        </p>
      )}
    </div>
  );
}
