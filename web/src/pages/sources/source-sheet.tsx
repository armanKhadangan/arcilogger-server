import { FilePlus2, FileText, RefreshCw, Search, Settings2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';

import { ChartFrame } from '@/components/charts/chart-frame';
import { LineChart } from '@/components/charts/line-chart';
import type { RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatDay, visibleText } from '@/lib/display';
import { formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { handoff, type HandoffPayloads } from '@/lib/handoff';
import { currentLang } from '@/lib/i18n';
import { useSourceDetail, useSourceTail } from '@/lib/sources-hooks';
import type { SourceDetail, SourceHealth } from '@/lib/sources-types';
import { cn } from '@/lib/utils';

import {
  dayRange,
  failureSearch,
  promotion,
  RAW_PERMISSIONS,
  MANAGE_PERMISSION,
  shownState,
  sourceSearch,
  STATE_STYLE,
} from './sources-model';

type Tab = 'volume' | 'tail' | 'gaps' | 'failures';
const TABS: readonly Tab[] = ['volume', 'tail', 'gaps', 'failures'];

const HEAD =
  'sticky top-0 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong';
const CELL = 'border-b border-border px-3 py-1.5 align-top';

/** Arama with `payload`: handed over in memory (never in the address). */
function useOpenSearch() {
  const navigate = useNavigate();
  return (payload: HandoffPayloads['/search']) => {
    handoff.put('/search', payload);
    void navigate('/search');
  };
}

function SourceStateBadge({ item }: { item: SourceHealth }) {
  const { t } = useTranslation();
  const state = shownState(item);
  const style = STATE_STYLE[state];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(`sources.state.${state}`)}
    </Badge>
  );
}

function VolumeTab({ detail }: { detail: SourceDetail }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const hourly = detail.hourly.map((point) => ({ hour: point.hour, count: point.count }));
  const count = (value: number) => formatNumber(value, 0, lang);
  return (
    <div className="flex flex-col gap-4">
      <ChartFrame
        title={t('sources.sheet.volume.hourly')}
        table={{
          columns: [t('sources.sheet.volume.hour'), t('sources.sheet.volume.count')],
          rows: detail.hourly.map((point) => [
            `${formatDay(point.hour.slice(0, 10))} ${formatTime(point.hour)}`,
            count(point.count),
          ]),
        }}
      >
        <LineChart
          data={hourly}
          xKey="hour"
          series={[{ key: 'count', label: t('sources.sheet.volume.count') }]}
          formatValue={count}
          formatX={(value) => formatTime(String(value))}
          height={180}
          integer
        />
      </ChartFrame>
      <section className="flex flex-col gap-2" aria-labelledby="source-daily">
        <h3 id="source-daily" className="text-sm font-semibold text-foreground">
          {t('sources.sheet.volume.daily')}
        </h3>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-separate border-spacing-0 text-13">
            <caption className="sr-only">{t('sources.sheet.volume.dailyCaption')}</caption>
            <thead>
              <tr>
                <th scope="col" className={HEAD}>
                  {t('sources.sheet.volume.day')}
                </th>
                <th scope="col" className={cn(HEAD, 'text-right')}>
                  {t('sources.sheet.volume.lines')}
                </th>
                <th scope="col" className={cn(HEAD, 'text-right')}>
                  {t('sources.sheet.volume.failures')}
                </th>
                <th scope="col" className={HEAD}>
                  {t('sources.sheet.volume.sealed')}
                </th>
              </tr>
            </thead>
            <tbody>
              {detail.daily.map((day) => (
                <tr key={day.day}>
                  <th scope="row" className={cn(CELL, 'text-left font-normal tabular')}>
                    {formatDay(day.day)}
                  </th>
                  <td className={cn(CELL, 'text-right tabular')}>{count(day.lines)}</td>
                  <td className={cn(CELL, 'text-right tabular')}>{count(day.failures)}</td>
                  <td className={CELL}>
                    {day.sealed ? t('sources.sheet.volume.yes') : t('sources.sheet.volume.no')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function TailTab({
  name,
  active,
  onRaw,
}: {
  name: string;
  active: boolean;
  onRaw: (target: RawLineTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { canAny, can, me } = useAuth();
  const openSearch = useOpenSearch();
  const allowed = canAny(RAW_PERMISSIONS);
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;
  const tail = useSourceTail(name, active && allowed && !privacyPending);
  if (!allowed)
    return (
      <Alert tone="info" assertive={false} data-testid="tail-hidden">
        {t('sources.sheet.tail.noPermission')}
      </Alert>
    );
  if (privacyPending)
    return (
      <Alert tone="info" assertive={false}>
        {t('sources.sheet.tail.privacy')}
      </Alert>
    );
  if (tail.isPending)
    return (
      <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
        <Skeleton className="h-8" />
        <Skeleton className="h-40" />
      </div>
    );
  if (tail.isError) return <Alert tone="danger">{errorMessage(tail.error)}</Alert>;
  const data = tail.data;
  const refresh = (
    <Button size="sm" variant="secondary" onClick={() => void tail.refetch()}>
      <RefreshCw aria-hidden="true" />
      {t('sources.sheet.tail.refresh')}
    </Button>
  );
  if (data.day === null)
    return (
      <div className="flex flex-col items-start gap-2" data-testid="tail-no-file">
        <p className="text-13 text-muted">{t('sources.sheet.tail.noFile')}</p>
        {can('search.read') ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              openSearch(sourceSearch(name, { preset: '7d' }));
            }}
          >
            <Search aria-hidden="true" />
            {t('sources.sheet.tail.searchSource')}
          </Button>
        ) : null}
      </div>
    );
  const day = data.day;
  return (
    <div className="flex flex-col gap-3" data-testid="tail">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-13 text-muted tabular">
          {data.from_line !== null && data.to_line !== null
            ? t('sources.sheet.tail.range', {
                day: formatDay(day),
                from: formatNumber(data.from_line, 0, lang),
                to: formatNumber(data.to_line, 0, lang),
              })
            : formatDay(day)}
        </p>
        <span className="ml-auto">{refresh}</span>
      </div>
      {data.lines.length === 0 ? (
        <p className="text-13 text-muted">{t('sources.sheet.tail.empty')}</p>
      ) : (
        <div className="max-h-[28rem] overflow-auto rounded-lg border border-border">
          <table className="w-full border-separate border-spacing-0 text-13">
            <caption className="sr-only">{t('sources.sheet.tail.caption')}</caption>
            <thead>
              <tr>
                <th scope="col" className={cn(HEAD, 'text-right')}>
                  {t('sources.sheet.tail.line')}
                </th>
                <th scope="col" className={HEAD}>
                  {t('sources.sheet.tail.received')}
                </th>
                <th scope="col" className={HEAD}>
                  {t('sources.sheet.tail.text')}
                </th>
                <th scope="col" className={HEAD}>
                  <span className="sr-only">{t('sources.sheet.tail.raw')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.line}>
                  <th scope="row" className={cn(CELL, 'text-right font-normal tabular')}>
                    {formatNumber(line.line, 0, lang)}
                  </th>
                  <td className={cn(CELL, 'whitespace-nowrap tabular')}>
                    {line.received ? formatTime(line.received, true) : '—'}
                    {line.sender ? (
                      <span className="block font-mono text-xs text-muted">{line.sender}</span>
                    ) : null}
                  </td>
                  <td className={cn(CELL, 'font-mono text-xs break-all')}>
                    {visibleText(line.text)}
                  </td>
                  <td className={CELL}>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={t('sources.sheet.tail.openRaw', { line: line.line })}
                      onClick={() => {
                        onRaw({ ref: { source: name, day, line: line.line, offset: line.offset } });
                      }}
                    >
                      <FileText aria-hidden="true" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.range_sha256 ? (
        <CodeBlock label={t('sources.sheet.tail.rangeSha')} code={data.range_sha256} wrap />
      ) : null}
      <p className="text-xs text-muted">{t('sources.sheet.tail.audited')}</p>
    </div>
  );
}

function GapsTab({ detail }: { detail: SourceDetail }) {
  const { t } = useTranslation();
  if (detail.gaps.length === 0)
    return <p className="text-13 text-muted">{t('sources.sheet.gaps.empty')}</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-separate border-spacing-0 text-13">
        <caption className="sr-only">{t('sources.sheet.gaps.caption')}</caption>
        <thead>
          <tr>
            <th scope="col" className={HEAD}>
              {t('sources.sheet.gaps.from')}
            </th>
            <th scope="col" className={HEAD}>
              {t('sources.sheet.gaps.to')}
            </th>
            <th scope="col" className={cn(HEAD, 'text-right')}>
              {t('sources.sheet.gaps.minutes')}
            </th>
            <th scope="col" className={HEAD}>
              {t('sources.sheet.gaps.reason')}
            </th>
          </tr>
        </thead>
        <tbody>
          {detail.gaps.map((gap) => (
            <tr key={`${gap.from}-${gap.to}`}>
              <th
                scope="row"
                className={cn(CELL, 'text-left font-normal whitespace-nowrap tabular')}
              >
                {formatDateTime(gap.from)}
              </th>
              <td className={cn(CELL, 'whitespace-nowrap tabular')}>{formatDateTime(gap.to)}</td>
              <td className={cn(CELL, 'text-right whitespace-nowrap tabular')}>
                {t('settings.sources.minutes', { count: gap.minutes })}
              </td>
              <td className={CELL}>
                <span className="block">{gap.reason}</span>
                <span className="block text-xs text-muted">
                  {t(`sources.sheet.gaps.origins.${gap.origin}`)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FailuresTab({
  detail,
  onRaw,
}: {
  detail: SourceDetail;
  onRaw: (target: RawLineTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { canAny, can } = useAuth();
  const openSearch = useOpenSearch();
  const raw = canAny(RAW_PERMISSIONS);
  if (detail.parse_failures.length === 0)
    return <p className="text-13 text-muted">{t('sources.sheet.failures.empty')}</p>;
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-separate border-spacing-0 text-13">
          <caption className="sr-only">{t('sources.sheet.failures.caption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={HEAD}>
                {t('sources.sheet.failures.pattern')}
              </th>
              <th scope="col" className={cn(HEAD, 'text-right')}>
                {t('sources.sheet.failures.count')}
              </th>
              <th scope="col" className={HEAD}>
                {t('sources.sheet.failures.lastSeen')}
              </th>
              <th scope="col" className={HEAD}>
                <span className="sr-only">{t('sources.sheet.failures.actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {detail.parse_failures.map((failure) => {
              const search = failureSearch(detail.name, failure.pattern, dayRange(failure.day));
              return (
                <tr key={`${failure.day}-${failure.pattern}`} data-pattern={failure.pattern}>
                  <th scope="row" className={cn(CELL, 'text-left font-normal')}>
                    <span className="block font-mono text-xs break-all">{failure.pattern}</span>
                    <span className="block text-xs text-muted">
                      {failure.error
                        ? visibleText(failure.error)
                        : raw
                          ? null
                          : t('sources.sheet.failures.errorHidden')}
                    </span>
                  </th>
                  <td className={cn(CELL, 'text-right tabular')}>
                    {formatNumber(failure.count, 0, lang)}
                  </td>
                  <td className={cn(CELL, 'whitespace-nowrap tabular')}>
                    {formatDateTime(failure.last_seen)}
                  </td>
                  <td className={CELL}>
                    <span className="flex flex-wrap justify-end gap-1">
                      {raw ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            onRaw({ ref: failure.example });
                          }}
                        >
                          <FileText aria-hidden="true" />
                          {t('sources.sheet.failures.example')}
                        </Button>
                      ) : null}
                      {search && can('search.read') ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            openSearch(search);
                          }}
                        >
                          <Search aria-hidden="true" />
                          {t('sources.sheet.failures.search')}
                        </Button>
                      ) : null}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {detail.parse_failures.some(
        (failure) => failureSearch(detail.name, failure.pattern) === null,
      ) ? (
        <p className="text-xs text-muted">{t('sources.sheet.failures.invalidNote')}</p>
      ) : null}
    </div>
  );
}

/**
 * One source (`GET /sources/{name}`): its last 48 hours and 14 days ("Hacim"), its last raw lines
 * ("Son satırlar": read only when the tab opens, audited `raw.tail`; hidden with an explanation
 * without a raw-line permission), the gaps of the last 30 days and the parse failures of the last
 * 7. A raw line opens over the drawer (`RawLineSheet`, audited `raw.view`).
 */
export function SourceSheet({
  item,
  onClose,
  onRaw,
  onPromote,
}: {
  /** The overview's row of the source; `null`: closed. */
  item: SourceHealth | null;
  onClose: () => void;
  onRaw: (target: RawLineTarget) => void;
  onPromote: (item: SourceHealth) => void;
}) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>('volume');
  const detail = useSourceDetail(item?.name ?? null);
  const promote = item && can(MANAGE_PERMISSION) ? promotion(item) : null;
  const editable = item !== null && item.configured && can(MANAGE_PERMISSION);

  return (
    <Sheet
      open={item !== null}
      onOpenChange={(open) => {
        if (!open) {
          setTab('volume');
          onClose();
        }
      }}
    >
      <SheetContent className="max-w-3xl" data-testid="source-sheet">
        <SheetHeader>
          <SheetTitle className="font-mono text-base font-semibold text-foreground">
            {item?.name ?? ''}
          </SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-2 text-13 text-muted">
            {item ? (
              <>
                <SourceStateBadge item={item} />
                <span>
                  {t(`settings.sources.types.${item.type}`, { defaultValue: item.type })}
                  {item.address ? ` · ${item.address}` : ''}
                </span>
                {item.promoted_to ? (
                  <span>{t('sources.badges.promotedTo', { name: item.promoted_to })}</span>
                ) : null}
              </>
            ) : null}
          </SheetDescription>
        </SheetHeader>
        {item ? (
          <SheetBody>
            {detail.isPending ? (
              <div className="flex flex-col gap-2" role="status" aria-label={t('common.loading')}>
                <Skeleton className="h-48" />
                <Skeleton className="h-32" />
              </div>
            ) : detail.isError ? (
              <Alert tone="danger">{errorMessage(detail.error)}</Alert>
            ) : (
              <Tabs
                value={tab}
                onValueChange={(value) => {
                  setTab(value as Tab);
                }}
              >
                <TabsList aria-label={t('sources.sheet.tabs.label')}>
                  {TABS.map((key) => (
                    <TabsTrigger key={key} value={key}>
                      {t(`sources.sheet.tabs.${key}`)}
                    </TabsTrigger>
                  ))}
                </TabsList>
                <TabsContent value="volume">
                  <VolumeTab detail={detail.data} />
                </TabsContent>
                <TabsContent value="tail">
                  <TailTab name={item.name} active={tab === 'tail'} onRaw={onRaw} />
                </TabsContent>
                <TabsContent value="gaps">
                  <GapsTab detail={detail.data} />
                </TabsContent>
                <TabsContent value="failures">
                  <FailuresTab detail={detail.data} onRaw={onRaw} />
                </TabsContent>
              </Tabs>
            )}
          </SheetBody>
        ) : null}
        {item && (promote || editable) ? (
          <SheetFooter>
            {promote ? (
              <Button
                onClick={() => {
                  onPromote(item);
                }}
              >
                <FilePlus2 aria-hidden="true" />
                {t('sources.promote')}
              </Button>
            ) : null}
            {editable ? (
              <Button variant="secondary" asChild>
                <Link to="/settings/sources">
                  <Settings2 aria-hidden="true" />
                  {t('sources.editInSettings')}
                </Link>
              </Button>
            ) : null}
          </SheetFooter>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
