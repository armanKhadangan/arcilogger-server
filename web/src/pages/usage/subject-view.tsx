import { ExternalLink, Globe, MonitorSmartphone, Search, UserRound } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { ChartFrame } from '@/components/charts/chart-frame';
import { DonutChart, type DonutSlice } from '@/components/charts/donut-chart';
import { LineChart } from '@/components/charts/line-chart';
import { CHART_OTHER, seriesColor } from '@/components/charts/palette';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Heatmap } from '@/components/ui/heatmap';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DayActivityBar } from '@/components/ui/timeline';
import { useUsageSubject } from '@/lib/api-hooks';
import type { SubjectDetail, UsageParams } from '@/lib/api-types';
import { formatDay, visibleText } from '@/lib/display';
import { formatBytes, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { heatmapRows, HEAT_MAX_MINUTES, HEATMAP_DAYS } from './heatmap-model';
import { clockOfMinute, layoutTimeline } from './timeline-model';
import { ExportMenu } from './usage-export';
import { addressOf, dayTotals, hourLabel, matchesSearch, perDay, shareText } from './usage-format';
import { metricTiles, noTraffic } from './usage-helpers';
import { addDays, categoryLabel, isSingleDay, weekday, type DrillTarget } from './usage-model';
import {
  BackBar,
  DataNote,
  DrillButton,
  EstimateHint,
  MetricTiles,
  QueryState,
  Section,
  SubjectName,
} from './usage-parts';
import {
  AppTable,
  BlockedTable,
  CategoryTable,
  DayTable,
  DeviceUsageTable,
  HostnameTable,
  SiteTable,
  UrlTable,
} from './usage-tables';

const KIND_ICON = { person: UserRound, device: MonitorSmartphone, ip: Globe } as const;

type DetailSection = 'time' | 'sites' | 'urls' | 'categories' | 'apps' | 'blocked' | 'devices';

/** True when `hostname` is `domain` or one of its subdomains. */
function inDomain(hostname: string, domain: string): boolean {
  const host = hostname.toLowerCase();
  const base = domain.toLowerCase();
  return host === base || host.endsWith(`.${base}`);
}

/** Header card: who, department, devices (a person's) or address (a device's), the figures. */
function SubjectHeader({
  detail,
  onDrill,
  onDevices,
}: {
  detail: SubjectDetail;
  onDrill: (target: DrillTarget) => void;
  /** Show the per-device breakdown (a person's device has no usage view of its own). */
  onDevices: () => void;
}) {
  const { t } = useTranslation();
  const subject = detail.subject;
  const Icon = KIND_ICON[subject.kind];
  const address = addressOf(subject);
  return (
    <Card className="flex flex-col gap-4 p-5" data-testid="subject-header">
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary-ink">
            <Icon aria-hidden="true" className="size-5" strokeWidth={1.75} />
          </span>
          <div className="flex min-w-0 flex-col">
            <h2 className="text-xl font-semibold text-foreground">
              <SubjectName subject={subject} />
            </h2>
            <span className="text-13 text-muted-strong">
              {t(`usage.subject.kind.${subject.kind}`)}
              {address && address !== subject.label ? (
                <>
                  {' · '}
                  <span className="font-mono text-xs">{address}</span>
                </>
              ) : null}
            </span>
          </div>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-13">
          {subject.kind !== 'person' && !subject.pseudonymous
            ? (
                [
                  ['mac', t('usage.columns.mac'), subject.mac],
                  ['ip', t('usage.columns.ip'), subject.ip],
                  ['hostname', t('usage.columns.hostname'), subject.hostname],
                ] as const
              )
                .filter(([, , value]) => value)
                .map(([key, label, value]) => (
                  <div key={key} className="contents">
                    <dt className="text-muted">{label}</dt>
                    <dd className="font-mono text-xs leading-5">{visibleText(value ?? '')}</dd>
                  </div>
                ))
            : null}
          <dt className="text-muted">{t('usage.columns.department')}</dt>
          <dd>
            {detail.department ? (
              <DrillButton
                onClick={() => {
                  if (detail.department) onDrill({ department: detail.department });
                }}
                title={t('usage.openDetail', { name: visibleText(detail.department) })}
              >
                {visibleText(detail.department)}
              </DrillButton>
            ) : (
              <span className="text-muted-strong">{t('usage.subject.noDepartment')}</span>
            )}
          </dd>
          {subject.kind === 'person' ? (
            <>
              <dt className="text-muted">{t('usage.subject.devices')}</dt>
              <dd className="flex flex-wrap gap-x-3 gap-y-1">
                {detail.devices.length === 0 ? (
                  <span className="text-muted-strong">—</span>
                ) : (
                  detail.devices.map((device) => (
                    <DrillButton
                      key={device.key}
                      onClick={onDevices}
                      title={t('usage.subject.showDevices')}
                    >
                      <SubjectName subject={device} />
                    </DrillButton>
                  ))
                )}
              </dd>
            </>
          ) : null}
        </dl>
      </div>
      <MetricTiles
        label={t('usage.subject.summary', { name: visibleText(subject.label) })}
        noticeKey={detail.notice_key}
        tiles={metricTiles(detail.metrics, t)}
      />
    </Card>
  );
}

/** The day's active minutes coloured by category; hover shows time and top domain. */
function DayTimeline({ detail, day }: { detail: SubjectDetail; day: string }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const layout = useMemo(
    () =>
      layoutTimeline(detail.timeline, day, detail.hourly, {
        unknown: t('usage.unknownCategory'),
        other: t('common.other'),
      }),
    [detail.timeline, detail.hourly, day, t],
  );
  const segments = layout.segments.map((segment) => ({
    start: segment.start,
    end: segment.end,
    category: segment.category,
    ...(segment.topDomains.length > 0
      ? { detail: t('usage.subject.topDomain', { domains: segment.topDomains.join(', ') }) }
      : {}),
  }));
  return (
    <ChartFrame
      title={t('usage.subject.timeline')}
      description={t('usage.subject.timelineHint', {
        day: formatDay(day),
        duration: formatDuration(layout.activeMinutes * 60, lang),
      })}
      table={{
        columns: [
          t('usage.columns.span'),
          t('usage.columns.category'),
          t('usage.metrics.time'),
          t('usage.columns.topDomain'),
        ],
        rows: layout.segments.map((segment) => [
          `${clockOfMinute(segment.start)}–${clockOfMinute(segment.end)}`,
          segment.category,
          formatDuration((segment.end - segment.start) * 60, lang),
          segment.topDomains.join(', ') || '—',
        ]),
      }}
    >
      {segments.length === 0 ? (
        <p className="text-13 text-muted">{t('usage.subject.timelineEmpty')}</p>
      ) : (
        <DayActivityBar
          segments={segments}
          categories={layout.categories}
          lang={lang}
          label={t('usage.subject.timelineLabel', { day: formatDay(day) })}
        />
      )}
    </ChartFrame>
  );
}

/** Per day (a period), hourly charts and the 30-day heat map. */
function TimeSection({
  detail,
  params,
  exportMenu,
}: {
  detail: SubjectDetail;
  params: UsageParams;
  exportMenu: (label: string) => ReactNode;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const single = isSingleDay(params);
  const days = useMemo(() => perDay(detail.hourly), [detail.hourly]);
  const totals = dayTotals(days);
  const hours = detail.hourly.map((hour) => ({
    hour: hour.hour,
    active: hour.active_seconds,
    online: hour.online_seconds,
    download: hour.download_bytes,
    upload: hour.upload_bytes,
  }));
  const formatHour = (value: string | number) => hourLabel(String(value), single);
  const weekdays = t('usage.subject.weekdays').split(',');
  const heatRows = heatmapRows(
    detail.heatmap,
    params.to,
    (day) => `${weekdays[weekday(day) - 1] ?? ''} ${day.slice(8, 10)}.${day.slice(5, 7)}`,
  );
  const hourTable = {
    columns: [
      t('usage.columns.hour'),
      t('usage.metrics.activeShort'),
      t('usage.metrics.onlineShort'),
      t('usage.metrics.download'),
      t('usage.metrics.upload'),
      t('usage.columns.topDomain'),
    ],
    rows: detail.hourly.map((hour) => [
      formatHour(hour.hour),
      formatDuration(hour.active_seconds, lang),
      formatDuration(hour.online_seconds, lang),
      formatBytes(hour.download_bytes, lang),
      formatBytes(hour.upload_bytes, lang),
      hour.top_domain ? visibleText(hour.top_domain) : '—',
    ]),
  };

  return (
    <div className="flex flex-col gap-4">
      {single ? null : (
        <Section
          title={t('usage.subject.perDay')}
          description={t('usage.subject.perDayTotal', {
            active: formatDuration(totals.active_seconds, lang),
            online: formatDuration(totals.online_seconds, lang),
            download: formatBytes(totals.download_bytes, lang),
            upload: formatBytes(totals.upload_bytes, lang),
          })}
        >
          <DayTable
            rows={days}
            caption={t('usage.subject.perDay')}
            toolbar={<EstimateHint noticeKey={detail.notice_key} />}
            actions={exportMenu(t('usage.subject.perDay'))}
            maxHeight={420}
          />
        </Section>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame
          title={t('usage.subject.hourlyActivity')}
          description={t('usage.subject.hourlyActivityHint')}
          legend={[
            { label: t('usage.metrics.activeShort'), color: seriesColor(0) },
            { label: t('usage.metrics.onlineShort'), color: seriesColor(1) },
          ]}
          table={hourTable}
        >
          <LineChart
            data={hours}
            xKey="hour"
            series={[
              { key: 'active', label: t('usage.metrics.activeShort') },
              { key: 'online', label: t('usage.metrics.onlineShort') },
            ]}
            formatValue={(value) => formatDuration(value, lang)}
            formatX={formatHour}
            height={220}
          />
        </ChartFrame>
        <ChartFrame
          title={t('usage.subject.hourlyTraffic')}
          legend={[
            { label: t('usage.metrics.download'), color: seriesColor(0) },
            { label: t('usage.metrics.upload'), color: seriesColor(1) },
          ]}
          table={hourTable}
        >
          <StackedAreaChart
            data={hours}
            xKey="hour"
            series={[
              { key: 'download', label: t('usage.metrics.download') },
              { key: 'upload', label: t('usage.metrics.upload') },
            ]}
            formatValue={(value) => formatBytes(value, lang)}
            formatX={formatHour}
          />
        </ChartFrame>
      </div>
      <Section
        title={t('usage.subject.heatmap', { days: HEATMAP_DAYS })}
        description={t('usage.subject.heatmapHint', {
          from: formatDay(addDays(params.to, 1 - HEATMAP_DAYS)),
          to: formatDay(params.to),
          max: HEAT_MAX_MINUTES,
        })}
      >
        <Heatmap
          rows={heatRows}
          max={HEAT_MAX_MINUTES}
          caption={t('usage.subject.heatmap', { days: HEATMAP_DAYS })}
          formatValue={(minutes) =>
            t('usage.subject.minutes', { minutes: formatNumber(minutes, 0, lang) })
          }
        />
      </Section>
    </div>
  );
}

/** A site of the subject: its host names and URLs ("expand a site"), and the way to the site. */
function SiteSheet({
  domain,
  detail,
  onClose,
  onDrill,
}: {
  domain: string | null;
  detail: SubjectDetail;
  onClose: () => void;
  onDrill: (target: DrillTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const site = detail.sites.find((row) => row.domain === domain) ?? null;
  const hostnames = domain ? detail.hostnames.filter((row) => row.domain === domain) : [];
  const urls = domain
    ? detail.urls.filter((row) => row.hostname !== null && inDomain(row.hostname, domain))
    : [];
  return (
    <Sheet
      open={domain !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-3xl">
        {domain ? (
          <>
            <SheetHeader>
              <SheetTitle className="text-base font-semibold">{visibleText(domain)}</SheetTitle>
              <SheetDescription className="text-13 text-muted">
                {t('usage.siteSheet.description', { name: visibleText(detail.subject.label) })}
              </SheetDescription>
            </SheetHeader>
            <SheetBody className="flex flex-col gap-5">
              {site ? (
                <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
                  <dt className="text-muted">{t('usage.columns.category')}</dt>
                  <dd>{visibleText(categoryLabel(site.category, t))}</dd>
                  <dt className="text-muted">{t('usage.metrics.time')}</dt>
                  <dd className="tabular">
                    {formatDuration(site.seconds, lang)} ({shareText(site.share, lang)}) ·{' '}
                    {t('usage.metrics.rawTime')} {formatDuration(site.raw_seconds, lang)}{' '}
                    <EstimateHint noticeKey={detail.notice_key} iconOnly />
                  </dd>
                  <dt className="text-muted">{t('usage.metrics.bytes')}</dt>
                  <dd className="tabular">
                    ↓ {formatBytes(site.download_bytes, lang)} · ↑{' '}
                    {formatBytes(site.upload_bytes, lang)}
                  </dd>
                  <dt className="text-muted">{t('usage.metrics.sessions')}</dt>
                  <dd className="tabular">{formatNumber(site.sessions, 0, lang)}</dd>
                </dl>
              ) : null}
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">
                  {t('usage.siteSheet.hostnames', { count: hostnames.length })}
                </h3>
                <HostnameTable
                  rows={hostnames}
                  caption={t('usage.siteSheet.hostnamesOf', { domain: visibleText(domain) })}
                  emptyMessage={t('usage.siteSheet.noHostnames')}
                  maxHeight={300}
                />
              </section>
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">
                  {t('usage.siteSheet.urls', { count: urls.length })}
                </h3>
                <UrlTable
                  rows={urls}
                  caption={t('usage.siteSheet.urlsOf', { domain: visibleText(domain) })}
                  emptyMessage={t('usage.siteSheet.noUrls')}
                  maxHeight={300}
                />
              </section>
            </SheetBody>
            <SheetFooter>
              <Button
                onClick={() => {
                  onDrill({ site: domain });
                }}
              >
                <ExternalLink aria-hidden="true" />
                {t('usage.siteSheet.openSite')}
              </Button>
            </SheetFooter>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function SubjectContent({
  detail,
  params,
  onDrill,
}: {
  detail: SubjectDetail;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const [section, setSection] = useState<DetailSection>('time');
  const sectionsRef = useRef<HTMLDivElement>(null);
  const [openSite, setOpenSite] = useState<string | null>(null);
  const [urlSearch, setUrlSearch] = useState('');
  const person = detail.subject.kind === 'person';
  const urls = useMemo(
    () =>
      detail.urls.filter((row) => matchesSearch(urlSearch, [row.url, row.hostname, row.category])),
    [detail.urls, urlSearch],
  );
  const slices: DonutSlice[] = useMemo(() => {
    const sorted = [...detail.categories].sort((a, b) => b.seconds - a.seconds);
    const list: DonutSlice[] = sorted
      .slice(0, 6)
      .map((row) => ({ label: categoryLabel(row.category, t), value: row.seconds }));
    const rest = sorted.slice(6).reduce((sum, row) => sum + row.seconds, 0);
    if (rest > 0) list.push({ label: t('common.other'), value: rest, other: true });
    return list;
  }, [detail.categories, t]);
  const colourOf = useMemo(() => {
    const order = [...detail.categories]
      .sort((a, b) => b.seconds - a.seconds)
      .map((row) => row.category);
    return (category: string) => {
      const index = order.indexOf(category);
      return index < 0 ? null : index < 6 ? seriesColor(index) : CHART_OTHER;
    };
  }, [detail.categories]);

  // The CSV of a subject is its sites; every other section is a sheet of the Excel file.
  const exportMenu = (label: string, csv = false) => (
    <ExportMenu
      view="subject"
      params={params}
      extra={{ key: detail.subject.key }}
      label={label}
      csv={csv}
    />
  );
  const hint = <EstimateHint noticeKey={detail.notice_key} />;
  const sections: [DetailSection, string, number | null][] = [
    ['time', t('usage.subject.sections.time'), null],
    ['sites', t('usage.subject.sections.sites'), detail.sites.length],
    ['urls', t('usage.subject.sections.urls'), detail.urls.length],
    ['categories', t('usage.subject.sections.categories'), detail.categories.length],
    ['apps', t('usage.subject.sections.apps'), detail.apps.length],
    ['blocked', t('usage.subject.sections.blocked'), detail.blocked.length],
    ...(person
      ? ([['devices', t('usage.subject.sections.devices'), detail.per_device.length]] as [
          DetailSection,
          string,
          number,
        ][])
      : []),
  ];

  return (
    <div className="flex flex-col gap-4">
      <SubjectHeader
        detail={detail}
        onDrill={onDrill}
        onDevices={() => {
          setSection('devices');
          requestAnimationFrame(() => {
            sectionsRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
          });
        }}
      />
      {isSingleDay(params) ? <DayTimeline detail={detail} day={params.from} /> : null}
      <Tabs
        ref={sectionsRef}
        value={section}
        onValueChange={(value) => {
          setSection(value as DetailSection);
        }}
      >
        <TabsList aria-label={t('usage.subject.sectionsLabel')}>
          {sections.map(([key, label, count]) => (
            <TabsTrigger key={key} value={key}>
              {label}
              {count !== null ? (
                <span className="text-xs text-muted tabular">{formatNumber(count, 0, lang)}</span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="time">
          <TimeSection detail={detail} params={params} exportMenu={exportMenu} />
        </TabsContent>
        <TabsContent value="sites">
          <SiteTable
            rows={detail.sites}
            caption={t('usage.subject.sitesOf', { name: visibleText(detail.subject.label) })}
            onOpen={setOpenSite}
            onCategory={(category) => {
              onDrill({ category });
            }}
            showUsers={false}
            toolbar={
              <span className="flex items-center gap-3 text-13 text-muted">
                {t('usage.subject.sitesHint')}
                {hint}
              </span>
            }
            actions={exportMenu(t('usage.subject.sections.sites'), true)}
            emptyMessage={t('usage.empty.title')}
            maxHeight={560}
          />
        </TabsContent>
        <TabsContent value="urls">
          <UrlTable
            rows={urls}
            caption={t('usage.subject.urlsOf', { name: visibleText(detail.subject.label) })}
            toolbar={
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex w-72 max-w-full flex-col gap-1">
                  <Label htmlFor="usage-url-search" className="text-xs text-muted-strong">
                    {t('usage.subject.urlSearch')}
                  </Label>
                  <Input
                    id="usage-url-search"
                    type="search"
                    autoComplete="off"
                    spellCheck={false}
                    leadingIcon={<Search />}
                    value={urlSearch}
                    onChange={(event) => {
                      setUrlSearch(event.target.value);
                    }}
                  />
                </div>
                <span className="pb-2 text-13 text-muted">{t('usage.subject.urlsHint')}</span>
              </div>
            }
            actions={exportMenu(t('usage.subject.sections.urls'))}
            emptyMessage={
              detail.urls.length === 0 ? t('usage.subject.noUrls') : t('usage.empty.filtered')
            }
            maxHeight={560}
          />
        </TabsContent>
        <TabsContent value="categories">
          <div className="grid items-start gap-4 lg:grid-cols-[16rem_1fr]">
            <DonutChart
              data={slices}
              formatValue={(value) => formatDuration(value, lang)}
              centerLabel={formatDuration(
                slices.reduce((sum, slice) => sum + slice.value, 0),
                lang,
              )}
              centerHint={t('usage.estimated')}
              height={220}
            />
            <CategoryTable
              rows={detail.categories}
              caption={t('usage.subject.categoriesOf', { name: visibleText(detail.subject.label) })}
              onOpen={(category) => {
                onDrill({ category });
              }}
              colourOf={colourOf}
              toolbar={hint}
              actions={exportMenu(t('usage.subject.sections.categories'))}
              emptyMessage={t('usage.empty.title')}
              maxHeight={420}
            />
          </div>
        </TabsContent>
        <TabsContent value="apps">
          <AppTable
            rows={detail.apps}
            caption={t('usage.subject.appsOf', { name: visibleText(detail.subject.label) })}
            toolbar={hint}
            actions={exportMenu(t('usage.subject.sections.apps'))}
            emptyMessage={t('usage.subject.noApps')}
            maxHeight={560}
          />
        </TabsContent>
        <TabsContent value="blocked">
          <BlockedTable
            rows={detail.blocked}
            caption={t('usage.subject.blockedOf', { name: visibleText(detail.subject.label) })}
            toolbar={<span className="text-13 text-muted">{t('usage.subject.blockedHint')}</span>}
            actions={exportMenu(t('usage.subject.sections.blocked'))}
            emptyMessage={t('usage.subject.noBlocked')}
            maxHeight={560}
          />
        </TabsContent>
        {person ? (
          <TabsContent value="devices">
            <DeviceUsageTable
              rows={detail.per_device}
              caption={t('usage.subject.devicesOf', { name: visibleText(detail.subject.label) })}
              toolbar={hint}
              actions={exportMenu(t('usage.subject.sections.devices'))}
              emptyMessage={t('usage.subject.noDevices')}
              maxHeight={420}
            />
          </TabsContent>
        ) : null}
      </Tabs>
      <SiteSheet
        domain={openSite}
        detail={detail}
        onClose={() => {
          setOpenSite(null);
        }}
        onDrill={(target) => {
          setOpenSite(null);
          onDrill(target);
        }}
      />
    </div>
  );
}

/** A person's (or a device's / IP address's) view of the period: every detail the API has. */
export function SubjectView({
  subjectKey,
  params,
  backLabel,
  onDrill,
  onBack,
}: {
  subjectKey: string;
  params: UsageParams;
  backLabel: string;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const detail = useUsageSubject(subjectKey, params);
  const data = detail.data;
  return (
    <div className="flex flex-col gap-4">
      <BackBar label={backLabel} onBack={onBack} />
      <DataNote computedUntil={data?.computed_until} noticeKey={data?.notice_key} />
      <QueryState
        query={detail}
        empty={data ? noTraffic(data.metrics) && data.blocked.length === 0 : false}
        emptyTitle={
          data ? t('usage.subject.notFound', { name: visibleText(data.subject.label) }) : undefined
        }
      >
        {() =>
          data ? (
            <SubjectContent
              key={data.subject.key}
              detail={data}
              params={params}
              onDrill={onDrill}
            />
          ) : null
        }
      </QueryState>
    </div>
  );
}
