import { LayoutDashboard, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { RawLineSheet, type RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { PageHeader } from '@/components/layout/page-header';
import { ModuleLockedCard } from '@/components/license/module-locked';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useDashboardSummary, useLicense, useTodayCompanyUsage } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatTime } from '@/lib/format';
import { moduleEnabled, type ModuleKey } from '@/lib/license';
import { hasAny } from '@/lib/permissions';
import { cn } from '@/lib/utils';

import { AlertsCard, SealStripCard } from './dashboard-alerts';
import { ClockCard, CreditCard, IngestCard, ParseFailuresCard, SealCard } from './dashboard-cards';
import {
  dashboardRequests,
  isRefusal,
  linkTarget,
  RAW_LINE_PERMISSIONS,
  sealDayHref,
  visibleBlocks,
} from './dashboard-model';
import { CategoriesCard, SitesCard, UsageCard } from './dashboard-usage';

/** The usage widgets belong to "Kullanım analizi"; the health boxes never depend on a licence. */
const USAGE_MODULE: ModuleKey = 'usage_analytics';

/**
 * Panel (spec 12.3 item 2; `dashboard.view` or `dashboard.usage`): log intake and today's parse
 * failures, yesterday's seal, Kamu SM credit and clock offset, today's company usage, open security
 * alerts, the most visited sites, categories and the last 30 days of sealing. Each block appears only for those who may
 * see it (the API leaves the others out); each links to its page when the user may open that.
 *
 * The live blocks refresh every 30 s, the usage figures every 5 minutes, both in the background
 * (`X-Arci-Background`: an unattended Panel does not keep the session alive) and only while the
 * tab is visible. The usage figures need the "Kullanım analizi" licence module: while it is off a
 * small locked card takes their place and `/usage/company` is never asked; the health boxes are
 * the same in every licence state.
 */
export function DashboardPage() {
  const { t } = useTranslation();
  const { permissions } = useAuth();
  const asked = dashboardRequests(permissions);
  const summary = useDashboardSummary(asked.summary);
  // Today's usage only once the licence is known and enables "Kullanım analizi" (contract 8.4):
  // no `/usage/company` request while the module is off or the licence is not read yet.
  const license = useLicense();
  const usageLicensed = license.data !== undefined && moduleEnabled(license.data, USAGE_MODULE);
  const usageLocked = asked.usage && license.data !== undefined && !usageLicensed;
  const licenseFailed = asked.usage && license.data === undefined && license.isError;
  const usage = useTodayCompanyUsage(asked.usage && usageLicensed);
  const blocks = visibleBlocks(permissions, summary.data, {
    data: usageLicensed ? usage.data : undefined,
    error: usageLicensed ? usage.error : null,
  });
  const data = summary.data;
  const usageProps = {
    usage: usage.data,
    error: usage.isError && !isRefusal(usage.error) ? usage.error : null,
    pending: usage.isPending,
    to: linkTarget('usage', permissions),
  };
  const sealing = linkTarget('sealing', permissions);
  const security = linkTarget('security', permissions);
  const summaryLoading = asked.summary && summary.isPending;
  const usageLoading =
    asked.usage &&
    ((license.data === undefined && license.isPending) || (usageLicensed && usage.isPending));
  const updatedAt = Math.max(summary.dataUpdatedAt, usageLicensed ? usage.dataUpdatedAt : 0);
  const summaryFailed = asked.summary && summary.isError && !isRefusal(summary.error);
  const [raw, setRaw] = useState<RawLineTarget | null>(null);
  const sources = linkTarget('sources', permissions);

  const top = [
    blocks.has('ingest') && data?.ingest ? (
      <IngestCard key="ingest" ingest={data.ingest} to={sources} />
    ) : null,
    blocks.has('parseFailures') && data?.parse_failures ? (
      <ParseFailuresCard
        key="parse-failures"
        box={data.parse_failures}
        to={sources}
        canRaw={hasAny(permissions, RAW_LINE_PERMISSIONS)}
        canSearch={permissions.has('search.read')}
        onRaw={setRaw}
      />
    ) : null,
    blocks.has('seal') && data?.seal ? (
      <SealCard
        key="seal"
        yesterday={data.seal.yesterday}
        to={sealing ? sealDayHref(sealing, data.seal.yesterday.day) : null}
      />
    ) : null,
    blocks.has('credit') && data?.credit ? (
      <CreditCard key="credit" credit={data.credit} to={sealing} />
    ) : null,
    blocks.has('clock') && data?.clock ? (
      <ClockCard key="clock" clock={data.clock} to={linkTarget('system', permissions)} />
    ) : null,
  ].filter(Boolean);

  const showUsage = blocks.has('usage') || usageLoading;
  /** In place of the usage widgets: the module is off, or the licence could not be read. */
  const usageCard = usageLocked ? (
    <ModuleLockedCard
      moduleKey={USAGE_MODULE}
      status={license.data}
      data-testid="card-usage-locked"
    />
  ) : licenseFailed ? (
    <Alert tone="warning" title={t('license.page.loadFailed')} data-testid="card-usage-license">
      {errorMessage(license.error)}
    </Alert>
  ) : null;
  const showAlerts = blocks.has('alerts') && data?.alerts;
  const nothing =
    !summaryLoading && !usageLoading && !summaryFailed && blocks.size === 0 && !usageCard;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.dashboard')}
        description={t('dashboard.description')}
        actions={
          <>
            {updatedAt > 0 ? (
              <span className="text-13 text-muted tabular" aria-live="polite">
                {t('dashboard.updated', { time: formatTime(updatedAt, true) })}
              </span>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (asked.summary) void summary.refresh();
                // `refresh()` would fetch even a disabled query: never while the module is off.
                if (asked.usage && usageLicensed) void usage.refresh();
              }}
            >
              <RefreshCw aria-hidden="true" />
              {t('dashboard.refresh')}
            </Button>
          </>
        }
      />

      {summaryFailed ? (
        <Alert tone="danger" title={t('dashboard.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(summary.error)}
            <Button size="sm" variant="secondary" onClick={() => void summary.refresh()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}

      {summaryLoading ? (
        <div
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          role="status"
          aria-label={t('common.loading')}
        >
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-36" />
          ))}
        </div>
      ) : top.length > 0 ? (
        // Five health cards: three a row up to 1535 px, then all five (each title wraps, never cut).
        <div
          className={cn(
            'grid gap-4 sm:grid-cols-2',
            top.length > 4 ? 'lg:grid-cols-3 2xl:grid-cols-5' : 'xl:grid-cols-4',
          )}
        >
          {top}
        </div>
      ) : null}

      {showUsage || showAlerts || usageCard ? (
        <div className="grid gap-4 lg:grid-cols-3">
          {usageCard ? (
            <div className={cn('self-start', showAlerts ? 'lg:col-span-2' : 'lg:col-span-3')}>
              {usageCard}
            </div>
          ) : showUsage ? (
            <UsageCard
              {...usageProps}
              className={cn(showAlerts ? 'lg:col-span-2' : 'lg:col-span-3')}
            />
          ) : null}
          {showAlerts ? (
            <AlertsCard
              alerts={showAlerts}
              to={security}
              className={showUsage || usageCard ? undefined : 'lg:col-span-3'}
            />
          ) : null}
        </div>
      ) : null}

      {showUsage || blocks.has('calendar') ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {showUsage ? (
            <>
              <SitesCard {...usageProps} />
              <CategoriesCard {...usageProps} />
            </>
          ) : null}
          {blocks.has('calendar') && data?.seal ? (
            <SealStripCard
              calendar={data.seal.calendar}
              lastDay={data.seal.yesterday.day}
              to={sealing}
            />
          ) : null}
        </div>
      ) : null}

      {nothing ? <EmptyState icon={LayoutDashboard} title={t('dashboard.nothing')} /> : null}
      <RawLineSheet
        target={raw}
        onClose={() => {
          setRaw(null);
        }}
      />
    </div>
  );
}
