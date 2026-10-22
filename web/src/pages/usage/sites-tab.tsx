import { FilterX, Globe, Search } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useUsageSite, useUsageSites } from '@/lib/api-hooks';
import type { SiteDetail, UsageParams } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatBytes, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ExportMenu } from './usage-export';
import { cappedHint, matchesApiSearch, shareText } from './usage-format';
import type { Tile } from './usage-helpers';
import { categoryLabel, siteCategory, type DrillTarget } from './usage-model';
import {
  BackBar,
  DataNote,
  DrillButton,
  EstimateHint,
  MetricTiles,
  NoTraffic,
  PrivacyWait,
  QueryState,
  Section,
} from './usage-parts';
import { HostnameTable, SiteTable, SubjectTable } from './usage-tables';

const ALL = '*';

function SiteContent({
  data,
  params,
  onDrill,
}: {
  data: SiteDetail;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const totals = data.totals;
  const tiles: Tile[] = [
    {
      key: 'time',
      label: t('usage.metrics.time'),
      value: formatDuration(totals.seconds, lang),
      hint: t('usage.sites.shareOfActive', { share: shareText(totals.share, lang) }),
      estimate: true,
    },
    {
      key: 'raw',
      label: t('usage.metrics.rawTime'),
      value: formatDuration(totals.raw_seconds, lang),
      hint: t('usage.hints.rawTime'),
      estimate: true,
    },
    {
      key: 'users',
      label: t('usage.metrics.users'),
      value: formatNumber(totals.users, 0, lang),
    },
    {
      key: 'sessions',
      label: t('usage.metrics.sessions'),
      value: formatNumber(totals.sessions, 0, lang),
    },
    {
      key: 'download',
      label: t('usage.metrics.download'),
      value: formatBytes(totals.download_bytes, lang),
    },
    {
      key: 'upload',
      label: t('usage.metrics.upload'),
      value: formatBytes(totals.upload_bytes, lang),
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <MetricTiles
        tiles={tiles}
        noticeKey={data.notice_key}
        label={t('usage.sites.summary', { domain: visibleText(data.domain) })}
      />
      <Section
        title={t('usage.sites.users')}
        description={cappedHint(
          t('usage.sites.usersHint'),
          data.users.length,
          data.users_total,
          t,
          lang,
        )}
      >
        <SubjectTable
          rows={data.users}
          variant="site"
          caption={t('usage.sites.usersOf', { domain: visibleText(data.domain) })}
          subjectLabel={t('usage.columns.subject')}
          onOpen={(subject) => {
            onDrill({ subject: subject.key });
          }}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <ExportMenu
              view="site"
              params={params}
              extra={{ domain: data.domain }}
              label={t('usage.sites.users')}
            />
          }
          emptyMessage={t('usage.empty.title')}
          maxHeight={480}
        />
      </Section>
      <Section title={t('usage.sites.hostnames')} description={t('usage.sites.hostnamesHint')}>
        <HostnameTable
          rows={data.hostnames}
          caption={t('usage.sites.hostnamesOf', { domain: visibleText(data.domain) })}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={
            <ExportMenu
              view="site"
              params={params}
              extra={{ domain: data.domain }}
              label={t('usage.sites.hostnames')}
              csv={false}
            />
          }
          emptyMessage={t('usage.siteSheet.noHostnames')}
        />
      </Section>
    </div>
  );
}

/** One site: totals, who used it for how long (→ person / device) and its host names. */
function SiteView({
  domain,
  params,
  onDrill,
  onBack,
}: {
  domain: string;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const site = useUsageSite(domain, params);
  const data = site.data;
  const category = data?.category ?? null;
  return (
    <div className="flex flex-col gap-4">
      <BackBar label={t('usage.back.sites')} onBack={onBack} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Globe aria-hidden="true" className="size-5 text-muted" />
        <h2 className="text-xl font-semibold text-foreground">{visibleText(domain)}</h2>
        {data ? (
          <span className="text-13 text-muted-strong">
            {t('usage.columns.category')}:{' '}
            {category ? (
              <DrillButton
                onClick={() => {
                  onDrill({ category });
                }}
                title={t('usage.openDetail', { name: visibleText(category) })}
              >
                {visibleText(category)}
              </DrillButton>
            ) : (
              t('usage.unknownCategory')
            )}
          </span>
        ) : null}
      </div>
      <DataNote computedUntil={data?.computed_until} noticeKey={data?.notice_key} />
      <QueryState
        query={site}
        empty={data ? data.totals.seconds === 0 && data.totals.sessions === 0 : false}
        emptyTitle={t('usage.sites.notFound', { domain: visibleText(domain) })}
      >
        {() => (data ? <SiteContent data={data} params={params} onDrill={onDrill} /> : null)}
      </QueryState>
    </div>
  );
}

/** Site: every site of the period (search, category filter) and the view of one site. */
export function SitesTab({
  site,
  params,
  privacyPending,
  onDrill,
  onBack,
}: {
  site: string | null;
  params: UsageParams;
  privacyPending: boolean;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const searchId = useId();
  const categoryId = useId();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState(ALL);
  const list = useUsageSites(params, !privacyPending && site === null);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  // The filter's values are the API's names (also the export's `category`), shown translated.
  const categories = useMemo(
    () =>
      [...new Set(rows.map((row) => siteCategory(row.category)))]
        .map((value) => ({ value, label: categoryLabel(value, t) }))
        .sort((a, b) => a.label.localeCompare(b.label, 'tr')),
    [rows, t],
  );
  const shown = useMemo(
    () =>
      rows.filter(
        (row) =>
          (category === ALL || siteCategory(row.category) === category) &&
          matchesApiSearch(search, [row.domain]),
      ),
    [rows, search, category],
  );

  // The API answers sites (and who used them) only after the KVKK notice, like the persons.
  if (privacyPending) return <PrivacyWait />;
  if (site !== null)
    return <SiteView domain={site} params={params} onDrill={onDrill} onBack={onBack} />;

  const filtered = search.trim() !== '' || category !== ALL;
  const toolbar = (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex w-60 max-w-full flex-col gap-1">
        <Label htmlFor={searchId} className="text-xs text-muted-strong">
          {t('usage.sites.search')}
        </Label>
        <Input
          id={searchId}
          type="search"
          autoComplete="off"
          spellCheck={false}
          leadingIcon={<Search />}
          placeholder={t('usage.sites.searchPlaceholder')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={categoryId} className="text-xs text-muted-strong">
          {t('usage.columns.category')}
        </Label>
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger id={categoryId} className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('usage.sites.allCategories')}</SelectItem>
            {categories.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {visibleText(item.label)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {filtered ? (
        <Button
          variant="ghost"
          onClick={() => {
            setSearch('');
            setCategory(ALL);
          }}
        >
          <FilterX aria-hidden="true" />
          {t('usage.subjects.clear')}
        </Button>
      ) : null}
      <span className="pb-2">
        <EstimateHint noticeKey={list.data?.notice_key ?? null} />
      </span>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <DataNote computedUntil={list.data?.computed_until} noticeKey={list.data?.notice_key} />
      <QueryState query={list} skeleton="table" empty={list.isSuccess && rows.length === 0}>
        {() => (
          <SiteTable
            rows={shown}
            caption={t('usage.sites.caption')}
            onOpen={(domain) => {
              onDrill({ site: domain });
            }}
            onCategory={(name) => {
              onDrill({ category: name });
            }}
            toolbar={toolbar}
            actions={
              <ExportMenu
                view="sites"
                params={params}
                extra={{
                  q: search.trim() || undefined,
                  category: category === ALL ? undefined : category,
                }}
                label={t('usage.sites.caption')}
              />
            }
            emptyMessage={filtered ? t('usage.empty.filtered') : <NoTraffic />}
            maxHeight={640}
          />
        )}
      </QueryState>
    </div>
  );
}
