import { ArrowLeft, Clock3, Info, RefreshCw, SearchX, ShieldUser } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { SimpleTooltip } from '@/components/ui/tooltip';
import { errorMessage, isApiError } from '@/lib/api';
import type { SubjectRef } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { cn } from '@/lib/utils';

import { computedUntilText } from './usage-format';
import { useMethodNote, type Tile } from './usage-helpers';
import { today } from './usage-model';

/**
 * The "tahmini" info icon (with the word, unless `iconOnly`): hover or focus shows how the
 * figures are estimated. Usage numbers are never presented as exact.
 */
export function EstimateHint({
  noticeKey,
  iconOnly = false,
  className,
}: {
  noticeKey?: string | null;
  iconOnly?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const note = useMethodNote(noticeKey);
  return (
    <SimpleTooltip content={note}>
      <button
        type="button"
        aria-label={`${t('usage.estimated')}: ${note}`}
        className={cn(
          'inline-flex items-center gap-1 rounded-sm text-xs font-normal text-muted-strong hover:text-foreground',
          className,
        )}
      >
        <Info aria-hidden="true" className="size-3.5" />
        {iconOnly ? null : <span aria-hidden="true">{t('usage.estimated')}</span>}
      </button>
    </SimpleTooltip>
  );
}

/** "Veriler 14:35'e kadar işlendi · ⓘ tahmini" above a usage view. */
export function DataNote({
  computedUntil,
  noticeKey,
}: {
  computedUntil: string | null | undefined;
  noticeKey?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <p
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-13 text-muted-strong"
      data-testid="data-note"
    >
      <span className="inline-flex items-center gap-1.5">
        <Clock3 aria-hidden="true" className="size-3.5" />
        {computedUntilText(computedUntil ?? null, today(), t)}
      </span>
      <EstimateHint noticeKey={noticeKey ?? null} />
    </p>
  );
}

/** A subject's name (device-provided text made visible) with the "anonim" badge of a pseudonym. */
export function SubjectName({ subject, className }: { subject: SubjectRef; className?: string }) {
  const { t } = useTranslation();
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)}>
      <span className="truncate">{visibleText(subject.label)}</span>
      {subject.pseudonymous ? (
        <Badge tone="neutral" icon={ShieldUser} title={t('usage.anonymousHint')}>
          {t('usage.anonymous')}
        </Badge>
      ) : null}
    </span>
  );
}

/** A link-styled button that opens a detail (keyboard: Tab + Enter). */
export function DrillButton({
  children,
  onClick,
  title,
  className,
}: {
  children: ReactNode;
  onClick: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn(
        'max-w-full min-w-0 truncate rounded-sm text-left text-accent-ink hover:underline',
        className,
      )}
    >
      {children}
    </button>
  );
}

/** "← Kişiler": back from a detail to its list. */
export function BackBar({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <div>
      <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2">
        <ArrowLeft aria-hidden="true" />
        {label}
      </Button>
    </div>
  );
}

/** A titled card around a table or a list, with actions (export) on the right. */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  headingLevel = 3,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <Card className={cn('flex min-w-0 flex-col', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div className="flex min-w-0 flex-col gap-0.5">
          <Heading className="text-sm font-semibold text-foreground">{title}</Heading>
          {description ? <p className="text-13 text-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="px-5 pt-3 pb-4">{children}</div>
    </Card>
  );
}

/** Loading, error (with retry) and "Bu aralıkta trafik yok" around a usage answer. */
export function QueryState({
  query,
  empty,
  emptyTitle,
  children,
  skeleton = 'page',
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    refresh: () => unknown;
    isFetching?: boolean;
  };
  /** True when the answer has no traffic at all. */
  empty?: boolean;
  /** Replaces "Bu aralıkta trafik yok" (e.g. "Bu dönemde youtube.com için trafik yok"). */
  emptyTitle?: string;
  children: () => ReactNode;
  skeleton?: 'page' | 'table';
}) {
  const { t } = useTranslation();
  if (query.isPending)
    return (
      <div className="flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
        {skeleton === 'page' ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-24" />
            ))}
          </div>
        ) : null}
        <Skeleton className="h-64" />
      </div>
    );
  // "No usage data for this person / site / category in this period": an empty state.
  if (query.isError && isApiError(query.error) && query.error.status === 404)
    return <NoTraffic title={emptyTitle ?? query.error.localized} />;
  if (query.isError)
    return (
      <Alert tone="danger" title={t('usage.loadFailed')}>
        <span className="flex flex-wrap items-center gap-2">
          {errorMessage(query.error)}
          <Button size="sm" variant="secondary" onClick={() => void query.refresh()}>
            <RefreshCw aria-hidden="true" />
            {t('common.retry')}
          </Button>
        </span>
      </Alert>
    );
  if (empty) return <NoTraffic {...(emptyTitle ? { title: emptyTitle } : {})} />;
  return <>{children()}</>;
}

/** "Bu aralıkta trafik yok". */
export function NoTraffic({ title }: { title?: string }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={SearchX}
      title={title ?? t('usage.empty.title')}
      description={t('usage.empty.body')}
    />
  );
}

/** Shown instead of person-level views while the KVKK notice (dialog) is not accepted. */
export function PrivacyWait() {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={ShieldUser}
      title={t('usage.privacy.title')}
      description={t('usage.privacy.body')}
    />
  );
}

/** A compact grid of metric tiles (the KPI cards of a detail view). */
export function MetricTiles({
  tiles,
  noticeKey,
  label,
}: {
  tiles: Tile[];
  noticeKey?: string | null;
  /** Accessible name of the list. */
  label: string;
}) {
  const note = useMethodNote(noticeKey);
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={label}>
      {tiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <li key={tile.key} className="flex flex-col gap-1 rounded-lg border border-border p-3">
            <span className="flex items-center gap-1.5 text-13 font-medium text-muted">
              {Icon ? <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} /> : null}
              {tile.label}
              {tile.estimate ? (
                <SimpleTooltip content={note}>
                  <button type="button" className="rounded-sm text-muted" aria-label={note}>
                    <Info aria-hidden="true" className="size-3.5" />
                  </button>
                </SimpleTooltip>
              ) : null}
            </span>
            <span className="text-xl font-semibold text-foreground tabular">{tile.value}</span>
            {tile.hint ? <span className="text-xs text-muted-strong">{tile.hint}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}
