import { CircleCheck, CircleSlash, EyeOff, Pencil } from 'lucide-react';
import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { usePerson } from '@/lib/api-hooks';
import type { PersonOut } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';

import { AssignmentHistory, Facts, RandomizedBadge, RandomizedNotice } from './device-parts';
import { deviceTypeLabel } from './device-text';

export function PersonStatus({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return active ? (
    <Badge tone="success" icon={CircleCheck}>
      {t('devices.persons.active')}
    </Badge>
  ) : (
    <Badge tone="neutral" icon={CircleSlash}>
      {t('devices.persons.inactive')}
    </Badge>
  );
}

/** The person is left out of the person-level usage views and reports (`usage_excluded`). */
export function UsageExcludedBadge() {
  const { t } = useTranslation();
  return (
    <Badge tone="neutral" icon={EyeOff} title={t('devices.persons.usageExcludedHint')}>
      {t('devices.persons.usageExcludedBadge')}
    </Badge>
  );
}

/**
 * One person (`GET /persons/{id}`): their data, the devices assigned to them now and the whole
 * assignment history. Several devices per person are normal: phones with randomised MAC
 * addresses appear as a new device per network (spec 9.2).
 */
export function PersonSheet({
  personId,
  onClose,
  manage,
  onEdit,
  returnFocus,
}: {
  personId: number | null;
  onClose: () => void;
  manage: boolean;
  onEdit: (person: PersonOut) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const detail = usePerson(personId);
  const person = detail.data;
  const randomized = person?.devices.filter((device) => device.randomized).length ?? 0;

  return (
    <Sheet
      open={personId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl" returnFocus={returnFocus}>
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {person ? visibleText(person.name) : t('common.loading')}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {person?.department
              ? visibleText(person.department.name)
              : t('devices.persons.noDepartment')}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-5">
          {detail.isPending ? (
            <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
              <Skeleton className="h-24" />
              <Skeleton className="h-48" />
            </div>
          ) : detail.isError ? (
            <Alert tone="danger">{errorMessage(detail.error)}</Alert>
          ) : person ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <PersonStatus active={person.active} />
                {person.usage_excluded ? <UsageExcludedBadge /> : null}
                {manage ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      onEdit(person);
                    }}
                  >
                    <Pencil aria-hidden="true" />
                    {t('devices.persons.edit')}
                  </Button>
                ) : null}
              </div>
              {!person.active ? (
                <Alert tone="info" assertive={false}>
                  {t('devices.persons.inactiveNote')}
                </Alert>
              ) : null}
              {person.usage_excluded ? (
                <Alert tone="info" assertive={false}>
                  {t('devices.persons.usageExcludedNote')}
                </Alert>
              ) : null}
              <Facts
                items={[
                  [t('devices.persons.title'), person.title ? visibleText(person.title) : '—'],
                  [t('devices.persons.email'), person.email ? visibleText(person.email) : '—'],
                  [
                    t('devices.persons.employeeNo'),
                    person.employee_no ? visibleText(person.employee_no) : '—',
                  ],
                  [t('devices.persons.notes'), person.notes ? visibleText(person.notes) : '—'],
                  [t('devices.persons.createdAt'), formatDateTime(person.created_at)],
                  [t('devices.persons.updatedAt'), formatDateTime(person.updated_at)],
                ]}
              />

              <section className="flex flex-col gap-2" aria-labelledby="person-devices">
                <h3 id="person-devices" className="text-sm font-semibold text-foreground">
                  {t('devices.persons.devices', { count: person.devices.length })}
                </h3>
                {randomized > 0 ? <RandomizedNotice count={randomized} /> : null}
                {person.devices.length === 0 ? (
                  <p className="text-13 text-muted">{t('devices.persons.noDevices')}</p>
                ) : (
                  <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
                    {person.devices.map((device) => (
                      <li
                        key={device.mac}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-13"
                      >
                        <span className="font-mono text-xs">{device.mac}</span>
                        {device.randomized ? <RandomizedBadge /> : null}
                        <span>
                          {device.name ? visibleText(device.name) : t('lookup.chain.unnamedDevice')}
                        </span>
                        <span className="text-muted-strong">{deviceTypeLabel(device.type, t)}</span>
                        <span className="ml-auto text-xs text-muted-strong tabular">
                          {device.assigned_since
                            ? t('devices.persons.since', {
                                time: formatDateTime(device.assigned_since),
                              })
                            : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="flex flex-col gap-2" aria-labelledby="person-history">
                <h3 id="person-history" className="text-sm font-semibold text-foreground">
                  {t('devices.history.title')}
                </h3>
                <AssignmentHistory assignments={person.assignments} withMac />
              </section>
            </>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
