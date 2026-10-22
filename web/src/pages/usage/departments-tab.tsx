import { Building2 } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import {
  useUsageCategories,
  useUsageDepartments,
  useUsageSites,
  useUsageSubjects,
} from '@/lib/api-hooks';
import type { UsageParams } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { ExportMenu } from './usage-export';
import { sumMetrics } from './usage-format';
import { metricTiles, noTraffic } from './usage-helpers';
import type { DrillTarget } from './usage-model';
import {
  BackBar,
  DataNote,
  EstimateHint,
  MetricTiles,
  NoTraffic,
  PrivacyWait,
  QueryState,
  Section,
} from './usage-parts';
import { CategoryTable, DepartmentTable, SiteTable, SubjectTable } from './usage-tables';

/**
 * One department: its totals, its persons and devices (unassigned devices of its VLANs
 * included) with every metric, its sites and categories — all asked of the API with
 * `department=<name>`, which scopes them. The subject rows are not filtered again by their own
 * `department`: it is `null` for the subjects of "Bilinmiyor" (the API's name for no department).
 */
function DepartmentDetail({
  name,
  params,
  onDrill,
  onBack,
}: {
  name: string;
  params: UsageParams;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const scoped: UsageParams = { ...params, department: name };
  const departments = useUsageDepartments(params);
  const persons = useUsageSubjects('person', scoped);
  const devices = useUsageSubjects('device', scoped);
  const sites = useUsageSites(scoped);
  const categories = useUsageCategories(scoped);

  const rows = (departments.data?.items ?? []).filter((row) => row.department === name);
  const personRows = persons.data?.items ?? [];
  const deviceRows = devices.data?.items ?? [];
  const metrics = sumMetrics(rows.map((row) => row.metrics));
  const activeUsers = rows.reduce((sum, row) => sum + row.active_users, 0);
  const noticeKey = departments.data?.notice_key ?? null;
  const openSubject = (subject: { key: string }) => {
    onDrill({ subject: subject.key });
  };

  return (
    <div className="flex flex-col gap-4">
      <BackBar label={t('usage.back.departments')} onBack={onBack} />
      <div className="flex flex-wrap items-center gap-2">
        <Building2 aria-hidden="true" className="size-5 text-muted" />
        <h2 className="text-xl font-semibold text-foreground">{visibleText(name)}</h2>
        {rows.map((row) => (
          <Badge key={row.source} tone={row.source === 'person' ? 'neutral' : 'outline'}>
            {t(`usage.sources.${row.source}`)}
          </Badge>
        ))}
      </div>
      <DataNote computedUntil={departments.data?.computed_until} noticeKey={noticeKey} />
      <QueryState
        query={departments}
        empty={rows.length === 0 || noTraffic(metrics)}
        emptyTitle={t('usage.departments.notFound', { name: visibleText(name) })}
      >
        {() => (
          <div className="flex flex-col gap-4">
            <MetricTiles
              label={t('usage.departments.summary', { name: visibleText(name) })}
              noticeKey={noticeKey}
              tiles={[
                {
                  key: 'users',
                  label: t('usage.metrics.activeUsers'),
                  value: formatNumber(activeUsers, 0, lang),
                },
                ...metricTiles(metrics, t),
              ]}
            />
            <Section
              title={t('usage.departments.persons')}
              description={t('usage.departments.personsHint')}
            >
              <QueryState query={persons} skeleton="table">
                {() => (
                  <SubjectTable
                    rows={personRows}
                    caption={t('usage.departments.personsOf', { name: visibleText(name) })}
                    subjectLabel={t('usage.columns.person')}
                    hideDepartment
                    onOpen={openSubject}
                    toolbar={<EstimateHint noticeKey={noticeKey} />}
                    actions={
                      <ExportMenu
                        view="subjects"
                        params={scoped}
                        extra={{ kind: 'person' }}
                        label={t('usage.departments.persons')}
                      />
                    }
                    emptyMessage={t('usage.departments.noPersons')}
                    maxHeight={420}
                  />
                )}
              </QueryState>
            </Section>
            <Section
              title={t('usage.departments.devices')}
              description={t('usage.departments.devicesHint')}
            >
              <QueryState query={devices} skeleton="table">
                {() => (
                  <SubjectTable
                    rows={deviceRows}
                    caption={t('usage.departments.devicesOf', { name: visibleText(name) })}
                    subjectLabel={t('usage.columns.device')}
                    hideDepartment
                    onOpen={openSubject}
                    toolbar={<EstimateHint noticeKey={noticeKey} />}
                    actions={
                      <ExportMenu
                        view="subjects"
                        params={scoped}
                        extra={{ kind: 'device' }}
                        label={t('usage.departments.devices')}
                      />
                    }
                    emptyMessage={t('usage.departments.noDevices')}
                    maxHeight={420}
                  />
                )}
              </QueryState>
            </Section>
            <div className="grid gap-4 xl:grid-cols-2">
              <Section title={t('usage.departments.sites')}>
                <QueryState query={sites} skeleton="table">
                  {() => (
                    <SiteTable
                      rows={sites.data?.items ?? []}
                      caption={t('usage.departments.sitesOf', { name: visibleText(name) })}
                      onOpen={(domain) => {
                        onDrill({ site: domain });
                      }}
                      onCategory={(category) => {
                        onDrill({ category });
                      }}
                      toolbar={<EstimateHint noticeKey={noticeKey} />}
                      actions={
                        <ExportMenu
                          view="sites"
                          params={scoped}
                          label={t('usage.departments.sites')}
                        />
                      }
                      emptyMessage={t('usage.empty.title')}
                      maxHeight={420}
                    />
                  )}
                </QueryState>
              </Section>
              <Section title={t('usage.departments.categories')}>
                <QueryState query={categories} skeleton="table">
                  {() => (
                    <CategoryTable
                      rows={categories.data?.items ?? []}
                      caption={t('usage.departments.categoriesOf', { name: visibleText(name) })}
                      onOpen={(category) => {
                        onDrill({ category });
                      }}
                      toolbar={<EstimateHint noticeKey={noticeKey} />}
                      actions={
                        <ExportMenu
                          view="categories"
                          params={scoped}
                          label={t('usage.departments.categories')}
                        />
                      }
                      emptyMessage={t('usage.empty.title')}
                      maxHeight={420}
                    />
                  )}
                </QueryState>
              </Section>
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}

/** Departman: every department of the period (→ its persons, devices, sites and categories). */
export function DepartmentsTab({
  department,
  params,
  privacyPending,
  onDrill,
  onBack,
}: {
  department: string | null;
  params: UsageParams;
  privacyPending: boolean;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const list = useUsageDepartments(params, !privacyPending && department === null);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);

  if (privacyPending) return <PrivacyWait />;
  if (department !== null)
    return <DepartmentDetail name={department} params={params} onDrill={onDrill} onBack={onBack} />;

  return (
    <div className="flex flex-col gap-4">
      <DataNote computedUntil={list.data?.computed_until} noticeKey={list.data?.notice_key} />
      <QueryState query={list} skeleton="table" empty={list.isSuccess && rows.length === 0}>
        {() => (
          <DepartmentTable
            rows={rows}
            caption={t('usage.departments.caption')}
            onOpen={(name) => {
              onDrill({ department: name });
            }}
            toolbar={<EstimateHint noticeKey={list.data?.notice_key ?? null} />}
            actions={
              <ExportMenu
                view="subjects"
                params={params}
                extra={{ kind: 'department' }}
                label={t('usage.departments.caption')}
              />
            }
            emptyMessage={<NoTraffic />}
            maxHeight={640}
          />
        )}
      </QueryState>
    </div>
  );
}
