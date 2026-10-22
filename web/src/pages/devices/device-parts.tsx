import { ChevronLeft, ChevronRight, Shuffle, ShieldUser, UserRound, UserX } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Timeline, type TimelineItem } from '@/components/ui/timeline';
import type { AssignmentOut, DeviceOut } from '@/lib/api-types';
import { visibleText } from '@/lib/display';

import { assignmentSpan } from './device-text';

/** Label + value pairs of a detail drawer. */
export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
      {items.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted">{name}</dt>
          <dd className="min-w-0 break-words text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A randomised (private) MAC address: icon and text, never colour alone. */
export function RandomizedBadge({ short = false }: { short?: boolean }) {
  const { t } = useTranslation();
  return (
    <Badge tone="warning" icon={Shuffle} title={t('devices.randomized.hint')}>
      {short ? t('devices.randomized.short') : t('devices.randomized.badge')}
    </Badge>
  );
}

/**
 * Spec 9.2: phones change a randomised MAC address per network and over time, so one phone can
 * appear as several devices. The warning says so, and that several MACs may belong to one person.
 */
export function RandomizedNotice({ count }: { count?: number }) {
  const { t } = useTranslation();
  return (
    <Alert
      tone="warning"
      assertive={false}
      title={
        count === undefined
          ? t('devices.randomized.title')
          : t('devices.randomized.titleSome', { count })
      }
      data-testid="randomized-notice"
    >
      {t('devices.randomized.body')}
    </Alert>
  );
}

/** Owner of a device: the person, or why there is none (infrastructure, unassigned). */
export function OwnerText({ device }: { device: DeviceOut }) {
  const { t } = useTranslation();
  if (device.person) {
    return (
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate">{visibleText(device.person.name)}</span>
        {device.department ? (
          <span className="truncate text-xs text-muted-strong">
            {visibleText(device.department.name)}
          </span>
        ) : null}
      </span>
    );
  }
  if (device.infrastructure) {
    return (
      <Badge tone="neutral" icon={ShieldUser}>
        {t('devices.infrastructureBadge')}
      </Badge>
    );
  }
  return (
    <span className="flex min-w-0 flex-col leading-tight">
      <span className="text-muted-strong">{t('devices.unassigned')}</span>
      {device.department ? (
        <span className="truncate text-xs text-muted-strong">
          {t('devices.departmentFromVlan', { name: visibleText(device.department.name) })}
        </span>
      ) : null}
    </span>
  );
}

/**
 * The assignment history, newest first (as the API sends it). `withMac`: the device of each row
 * is named (a person's history covers several devices).
 */
export function AssignmentHistory({
  assignments,
  withMac = false,
}: {
  assignments: readonly AssignmentOut[];
  withMac?: boolean;
}) {
  const { t } = useTranslation();
  if (assignments.length === 0)
    return <p className="text-13 text-muted">{t('devices.history.empty')}</p>;
  const items: TimelineItem[] = assignments.map((item) => {
    const parts = [
      withMac ? `MAC ${item.mac}` : null,
      t('devices.history.by', { user: item.assigned_by }),
      item.note ? t('devices.history.note', { note: visibleText(item.note) }) : null,
      item.backfilled ? t('devices.history.backfilled') : null,
    ].filter((part): part is string => part !== null);
    return {
      id: String(item.id),
      time: assignmentSpan(item, t),
      title: item.person ? visibleText(item.person.name) : t('devices.history.nobody'),
      description: parts.join(' · '),
      icon: item.person ? UserRound : UserX,
      tone: item.valid_to === null ? 'primary' : 'neutral',
    };
  });
  return <Timeline items={items} />;
}

/** Shown instead of person-level lists while the KVKK notice (dialog) is not accepted. */
export function PrivacyWait() {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={ShieldUser}
      title={t('devices.privacy.title')}
      description={t('devices.privacy.body')}
    />
  );
}

/** Previous / next page of a server-paged table, with "Sayfa 2 / 7". */
export function Pager({
  offset,
  total,
  pageSize,
  onPage,
  label,
}: {
  offset: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  /** What the pages hold, e.g. "Cihaz listesinin sayfaları". */
  label: string;
}) {
  const { t } = useTranslation();
  const page = Math.floor(offset / pageSize);
  const last = Math.max(0, Math.ceil(total / pageSize) - 1);
  if (last === 0 && page === 0) return null;
  return (
    <nav aria-label={label} className="flex items-center justify-end gap-2">
      <Button
        variant="secondary"
        size="sm"
        disabled={page === 0}
        onClick={() => {
          onPage(page - 1);
        }}
      >
        <ChevronLeft aria-hidden="true" />
        {t('devices.pager.previous')}
      </Button>
      <span className="text-13 text-muted tabular">
        {t('devices.pager.page', { page: page + 1, pages: last + 1 })}
      </span>
      <Button
        variant="secondary"
        size="sm"
        disabled={page >= last}
        onClick={() => {
          onPage(page + 1);
        }}
      >
        {t('devices.pager.next')}
        <ChevronRight aria-hidden="true" />
      </Button>
    </nav>
  );
}
