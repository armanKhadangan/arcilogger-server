import type { TFunction } from 'i18next';
import { Tags } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { DonutChart, type DonutSlice } from '@/components/charts/donut-chart';
import { CHART_OTHER, seriesColor } from '@/components/charts/palette';
import { useUsageCategories, useUsageCategory } from '@/lib/api-hooks';
import type { CategoryDetail, CategoryRow, UsageParams } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatBytes, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ExportMenu } from './usage-export';
import { cappedHint, shareText } from './usage-format';
import type { Tile } from './usage-helpers';
import { categoryLabel, type DrillTarget } from './usage-model';
import {
  BackBar,
  DataNote,
  EstimateHint,
  MetricTiles,
  PrivacyWait,
  QueryState,
  Section,
} from './usage-parts';
import { CategoryTable, SiteTable, SubjectTable } from './usage-tables';

const SLICES = 6;

/** Rows sorted by time with their chart colour (the six largest, then "Diğer"). */
function colouring(rows: readonly CategoryRow[], t: TFunction) {
  const sorted = [...rows].sort((a, b) => b.seconds - a.seconds);
  const slices: DonutSlice[] = sorted
    .slice(0, SLICES)
    .map((row) => ({ label: categoryLabel(row.category, t), value: row.seconds }));
  const rest = sorted.slice(SLICES).reduce((sum, row) => sum + row.seconds, 0);
  if (rest > 0) slices.push({ label: t('common.other'), value: rest, other: true });
  const order = sorted.map((row) => row.category);
  const colourOf = (category: string) => {
    const index = order.indexOf(category);
    return index < 0 ? null : index < SLICES ? seriesColor(index) : CHART_OTHER;
  };
  return { slices, colourOf };
}

function CategoryContent({
  data,
  params,
  onDrill,
}: {
  data: CategoryDetail;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const totals = data.totals;
  // The category as the page shows it; `data.category` (the API's name) goes to the export.
  const name = visibleText(categoryLabel(data.category, t));
  const tiles: Tile[] = [
    {
      key: 'time',
      label: t('usage.metrics.time'),
      value: formatDuration(totals.seconds, lang),
      hint: t('usage.sites.shareOfActive', { share: shareText(totals.share, lang) }),
      estimate: true,
    },
    {
      key: 'users',
      label: t('usage.metrics.users'),
      value: formatNumber(totals.users, 0, lang),
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
  // The CSV of a category is its sites; its users are a sheet of the Excel file.
  const exportMenu = (label: string, csv = true) => (
    <ExportMenu
      view="category"
      params={params}
      extra={{ category: data.category }}
      label={label}
      csv={csv}
    />
  );
  return (
    <div className="flex flex-col gap-4">
      <MetricTiles
        tiles={tiles}
        noticeKey={data.notice_key}
        label={t('usage.categories.summary', { name })}
      />
      <Section
        title={t('usage.categories.sites')}
        description={cappedHint(
          t('usage.categories.sitesHint'),
          data.sites.length,
          data.sites_total,
          t,
          lang,
        )}
      >
        <SiteTable
          rows={data.sites}
          caption={t('usage.categories.sitesOf', { name })}
          onOpen={(domain) => {
            onDrill({ site: domain });
          }}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={exportMenu(t('usage.categories.sites'))}
          emptyMessage={t('usage.empty.title')}
          maxHeight={420}
        />
      </Section>
      <Section
        title={t('usage.categories.users')}
        description={cappedHint(
          t('usage.categories.usersHint'),
          data.users.length,
          data.users_total,
          t,
          lang,
        )}
      >
        <SubjectTable
          rows={data.users}
          variant="site"
          caption={t('usage.categories.usersOf', { name })}
          subjectLabel={t('usage.columns.subject')}
          onOpen={(subject) => {
            onDrill({ subject: subject.key });
          }}
          toolbar={<EstimateHint noticeKey={data.notice_key} />}
          actions={exportMenu(t('usage.categories.users'), false)}
          emptyMessage={t('usage.empty.title')}
          maxHeight={480}
        />
      </Section>
    </div>
  );
}

/** One category: totals, its sites (→ site) and its users (→ person / device). */
function CategoryView({
  category,
  params,
  onDrill,
  onBack,
}: {
  category: string;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const detail = useUsageCategory(category, params);
  const data = detail.data;
  const name = visibleText(categoryLabel(category, t));
  return (
    <div className="flex flex-col gap-4">
      <BackBar label={t('usage.back.categories')} onBack={onBack} />
      <div className="flex items-center gap-3">
        <Tags aria-hidden="true" className="size-5 text-muted" />
        <h2 className="text-xl font-semibold text-foreground">{name}</h2>
      </div>
      <DataNote computedUntil={data?.computed_until} noticeKey={data?.notice_key} />
      <QueryState
        query={detail}
        empty={data ? data.totals.seconds === 0 && data.sites.length === 0 : false}
        emptyTitle={t('usage.categories.notFound', { name })}
      >
        {() => (data ? <CategoryContent data={data} params={params} onDrill={onDrill} /> : null)}
      </QueryState>
    </div>
  );
}

/** Kategori: the categories of the period (donut + table) and the view of one category. */
export function CategoriesTab({
  category,
  params,
  privacyPending,
  onDrill,
  onBack,
}: {
  category: string | null;
  params: UsageParams;
  privacyPending: boolean;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const list = useUsageCategories(params, !privacyPending && category === null);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  const { slices, colourOf } = useMemo(() => colouring(rows, t), [rows, t]);

  if (privacyPending) return <PrivacyWait />;
  if (category !== null)
    return <CategoryView category={category} params={params} onDrill={onDrill} onBack={onBack} />;

  return (
    <div className="flex flex-col gap-4">
      <DataNote computedUntil={list.data?.computed_until} noticeKey={list.data?.notice_key} />
      <QueryState query={list} skeleton="table" empty={list.isSuccess && rows.length === 0}>
        {() => (
          <div className="grid items-start gap-4 lg:grid-cols-[16rem_1fr]">
            <DonutChart
              data={slices}
              formatValue={(value) => formatDuration(value, lang)}
              centerLabel={formatDuration(
                slices.reduce((sum, slice) => sum + slice.value, 0),
                lang,
              )}
              centerHint={t('usage.estimated')}
              height={240}
            />
            <CategoryTable
              rows={rows}
              caption={t('usage.categories.caption')}
              onOpen={(name) => {
                onDrill({ category: name });
              }}
              colourOf={colourOf}
              toolbar={<EstimateHint noticeKey={list.data?.notice_key ?? null} />}
              actions={
                <ExportMenu
                  view="categories"
                  params={params}
                  label={t('usage.categories.caption')}
                />
              }
              emptyMessage={t('usage.empty.title')}
              maxHeight={640}
            />
          </div>
        )}
      </QueryState>
    </div>
  );
}
