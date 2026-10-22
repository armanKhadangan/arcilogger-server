import {
  Building2,
  Globe,
  Info,
  LayoutDashboard,
  MonitorSmartphone,
  Tags,
  Users,
} from 'lucide-react';
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';

import { CategoriesTab } from './categories-tab';
import { CompanyTab } from './company-tab';
import { DepartmentsTab } from './departments-tab';
import { RebuildMenu } from './rebuild-menu';
import { SitesTab } from './sites-tab';
import { SubjectsTab } from './subjects-tab';
import { useMethodNote } from './usage-helpers';
import {
  drillTo,
  today,
  usageParams,
  USAGE_TABS,
  viewFromSearch,
  viewToSearch,
  withTab,
  type DayRange,
  type DrillTarget,
  type UsageTab,
  type UsageView,
} from './usage-model';
import { HoursFilter, PeriodPicker } from './usage-toolbar';

const TAB_ICONS: Record<UsageTab, typeof Users> = {
  company: LayoutDashboard,
  departments: Building2,
  persons: Users,
  devices: MonitorSmartphone,
  sites: Globe,
  categories: Tags,
};

/**
 * Kullanım (spec 10, 12.3 item 7; `usage.person` or `usage.anonymous`): internet usage of the
 * company, its departments, persons, devices, sites and categories, with drill-down everywhere
 * (company → department → person → site → host names / URLs; site → users; category → sites →
 * users). Tab, period, hours and the selected department / subject / site / category are in the
 * address; a drill-down is a new history entry (Back returns to the list).
 *
 * Owner decision 2026-10-20: names are shown by default; a pseudonym (only when the optional
 * setting gives `usage.anonymous` viewers pseudonyms) carries an "anonim" badge. Person-level
 * views wait for the accepted KVKK notice. Every duration is an estimate ("tahmini").
 */
export function UsagePage() {
  const { t } = useTranslation();
  const { me, can } = useAuth();
  const [search, setSearch] = useSearchParams();
  const todayDay = today();
  const view = useMemo(() => viewFromSearch(search, todayDay), [search, todayDay]);
  const params = useMemo(() => usageParams(view), [view]);
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;
  const note = useMethodNote();

  const go = useCallback(
    (next: UsageView, push: boolean) => {
      setSearch(viewToSearch(next, todayDay), { replace: !push });
    },
    [setSearch, todayDay],
  );
  const drill = useCallback(
    (target: DrillTarget) => {
      go(drillTo(view, target), true);
      window.scrollTo({ top: 0 });
    },
    [go, view],
  );
  const back = useCallback(() => {
    go(withTab(view, view.tab), true);
  }, [go, view]);
  const list = useCallback(
    (tab: UsageTab) => {
      go(withTab(view, tab), true);
    },
    [go, view],
  );

  return (
    <div className="flex flex-col">
      <PageHeader
        title={t('nav.usage')}
        description={t('usage.description')}
        actions={
          can('settings.manage') ? <RebuildMenu range={view} todayDay={todayDay} /> : undefined
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3" data-testid="usage-filters">
        <PeriodPicker
          range={view}
          todayDay={todayDay}
          onChange={(range: DayRange) => {
            go({ ...view, ...range }, false);
          }}
        />
        <HoursFilter
          value={view.hours}
          onChange={(hours) => {
            go({ ...view, hours }, false);
          }}
        />
      </div>
      <Tabs
        value={view.tab}
        onValueChange={(value) => {
          go(withTab(view, value as UsageTab), false);
        }}
      >
        <TabsList aria-label={t('usage.tabs.label')}>
          {USAGE_TABS.map((key) => {
            const Icon = TAB_ICONS[key];
            return (
              <TabsTrigger key={key} value={key}>
                <Icon aria-hidden="true" />
                {t(`usage.tabs.${key}`)}
              </TabsTrigger>
            );
          })}
        </TabsList>
        <TabsContent value="company">
          <CompanyTab view={view} params={params} onDrill={drill} onList={list} />
        </TabsContent>
        <TabsContent value="departments">
          <DepartmentsTab
            department={view.department}
            params={params}
            privacyPending={privacyPending}
            onDrill={drill}
            onBack={back}
          />
        </TabsContent>
        <TabsContent value="persons">
          <SubjectsTab
            kind="person"
            subject={view.subject}
            params={params}
            privacyPending={privacyPending}
            onDrill={drill}
            onBack={back}
          />
        </TabsContent>
        <TabsContent value="devices">
          <SubjectsTab
            kind="device"
            subject={view.subject}
            params={params}
            privacyPending={privacyPending}
            onDrill={drill}
            onBack={back}
          />
        </TabsContent>
        <TabsContent value="sites">
          <SitesTab
            site={view.site}
            params={params}
            privacyPending={privacyPending}
            onDrill={drill}
            onBack={back}
          />
        </TabsContent>
        <TabsContent value="categories">
          <CategoriesTab
            category={view.category}
            params={params}
            privacyPending={privacyPending}
            onDrill={drill}
            onBack={back}
          />
        </TabsContent>
      </Tabs>
      <p className="mt-6 flex items-start gap-2 text-13 text-muted" data-testid="method-note">
        <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>
          {note} {t('usage.methodFooter')}
        </span>
      </p>
    </div>
  );
}
