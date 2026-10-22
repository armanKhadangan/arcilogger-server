import { Link2, Unlink } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useDevice, useUpdateDevice } from '@/lib/api-hooks';
import type { DeviceDetail, DeviceOut, LeaseBrief } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import type { AssignMode } from './assign-model';
import {
  AssignmentHistory,
  Facts,
  OwnerText,
  RandomizedBadge,
  RandomizedNotice,
} from './device-parts';
import { deviceTypeLabel } from './device-text';
import {
  DEVICE_TYPES,
  deviceChanges,
  deviceEditProblems,
  deviceEditValues,
  EDIT_LIMITS,
  UNKNOWN_TYPE,
  type DeviceEditValues,
} from './devices-model';

type EditField = 'name' | 'tags' | 'notes';

/** Name, type, infrastructure flag, notes and tags of a device (`devices.manage`). */
function DeviceEditForm({ device }: { device: DeviceDetail }) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const update = useUpdateDevice();
  const [values, setValues] = useState<DeviceEditValues>(() => deviceEditValues(device));
  const [errors, setErrors] = useState<FieldErrors<EditField>>({});
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    setValues(deviceEditValues(device));
  }, [device]);

  const changes = deviceChanges(device, values);
  const changed = Object.keys(changes).length > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = deviceEditProblems(values);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    if (!changed) return;
    try {
      await withReauth(() => update.mutateAsync({ mac: device.mac, ...changes }));
      toast({ title: t('devices.detail.saved'), variant: 'success' });
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const fieldError = (field: EditField) =>
    errors[field] ? t(errors[field], { max: EDIT_LIMITS[field], tag: EDIT_LIMITS.tag }) : undefined;
  const describedBy = (field: EditField, hint: boolean) =>
    fieldError(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;

  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-3" noValidate>
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <Field
        id={`${id}-name`}
        label={t('devices.detail.name')}
        hint={t('devices.detail.nameHint')}
        error={fieldError('name')}
      >
        <Input
          id={`${id}-name`}
          autoComplete="off"
          value={values.name}
          aria-invalid={fieldError('name') ? true : undefined}
          aria-describedby={describedBy('name', true)}
          onChange={(event) => {
            setValues((current) => ({ ...current, name: event.target.value }));
          }}
        />
      </Field>
      <Field id={`${id}-type`} label={t('devices.columns.type')}>
        <Select
          value={values.type}
          onValueChange={(type) => {
            setValues((current) => ({ ...current, type }));
          }}
        >
          <SelectTrigger id={`${id}-type`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={UNKNOWN_TYPE}>{t('devices.typeUnknown')}</SelectItem>
            {DEVICE_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {deviceTypeLabel(type, t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <div className="flex items-start gap-2">
        <Checkbox
          id={`${id}-infrastructure`}
          checked={values.infrastructure}
          aria-describedby={`${id}-infrastructure-hint`}
          onCheckedChange={(checked) => {
            setValues((current) => ({ ...current, infrastructure: checked === true }));
          }}
          className="mt-0.5"
        />
        <div className="flex flex-col gap-0.5">
          <Label htmlFor={`${id}-infrastructure`} className="font-normal">
            {t('devices.detail.infrastructure')}
          </Label>
          <p id={`${id}-infrastructure-hint`} className="text-xs text-muted">
            {t('devices.detail.infrastructureHint')}
          </p>
        </div>
      </div>
      <Field
        id={`${id}-tags`}
        label={t('devices.detail.tags')}
        hint={t('devices.detail.tagsHint')}
        error={fieldError('tags')}
      >
        <Input
          id={`${id}-tags`}
          autoComplete="off"
          value={values.tags}
          aria-invalid={fieldError('tags') ? true : undefined}
          aria-describedby={describedBy('tags', true)}
          onChange={(event) => {
            setValues((current) => ({ ...current, tags: event.target.value }));
          }}
        />
      </Field>
      <Field id={`${id}-notes`} label={t('devices.detail.notes')} error={fieldError('notes')}>
        <Textarea
          id={`${id}-notes`}
          rows={3}
          value={values.notes}
          aria-invalid={fieldError('notes') ? true : undefined}
          aria-describedby={describedBy('notes', false)}
          onChange={(event) => {
            setValues((current) => ({ ...current, notes: event.target.value }));
          }}
        />
      </Field>
      <div className="flex justify-end gap-2">
        <Button
          variant="secondary"
          disabled={!changed || update.isPending}
          onClick={() => {
            setValues(deviceEditValues(device));
            setErrors({});
            setFailure(null);
          }}
        >
          {t('devices.detail.discard')}
        </Button>
        <Button type="submit" disabled={!changed} busy={update.isPending}>
          {update.isPending ? t('common.working') : t('common.save')}
        </Button>
      </div>
    </form>
  );
}

function LeaseTable({ leases }: { leases: readonly LeaseBrief[] }) {
  const { t } = useTranslation();
  if (leases.length === 0)
    return <p className="text-13 text-muted">{t('devices.detail.noLeases')}</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-separate border-spacing-0 text-13">
        <caption className="sr-only">{t('devices.detail.leases')}</caption>
        <thead>
          <tr>
            {(['ip', 'vlan', 'start', 'end', 'confidence'] as const).map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
              >
                {t(`devices.detail.leaseColumns.${column}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {leases.map((lease, index) => (
            <tr key={`${lease.ip}-${lease.start}-${index}`}>
              <td className="border-b border-border px-3 py-1.5 font-mono text-xs">{lease.ip}</td>
              <td className="border-b border-border px-3 py-1.5 tabular">{lease.vlan ?? '—'}</td>
              <td className="border-b border-border px-3 py-1.5 tabular">
                {formatDateTime(lease.start)}
              </td>
              <td className="border-b border-border px-3 py-1.5 tabular">
                {lease.end ? formatDateTime(lease.end) : t('lookup.endReason.open')}
                {lease.end && lease.end_reason ? (
                  <span className="block text-xs text-muted-strong">
                    {t(`lookup.endReason.${lease.end_reason}`, { defaultValue: lease.end_reason })}
                  </span>
                ) : null}
              </td>
              <td className="border-b border-border px-3 py-1.5">
                {t(`lookup.confidence.${lease.confidence}`, { defaultValue: lease.confidence })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One device (`GET /devices/{mac}`): what the network told about it, its owner with the
 * assignment history (newest first) and its newest leases. With `devices.manage` its name, type,
 * infrastructure flag, notes and tags can be edited and it can be assigned or unassigned.
 */
export function DeviceSheet({
  mac,
  onClose,
  manage,
  onAssign,
  returnFocus,
}: {
  mac: string | null;
  onClose: () => void;
  manage: boolean;
  /** Opens the assignment dialog for this device. */
  onAssign: (mode: AssignMode, device: DeviceOut) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const detail = useDevice(mac);
  const device = detail.data;

  return (
    <Sheet
      open={mac !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl" returnFocus={returnFocus}>
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {device?.name ? visibleText(device.name) : (mac ?? '')}
          </SheetTitle>
          <SheetDescription className="font-mono text-13 text-muted">
            {device?.name ? mac : t('devices.detail.subtitle')}
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
          ) : device ? (
            <>
              {device.randomized ? <RandomizedNotice /> : null}
              <section className="flex flex-col gap-2" aria-labelledby="device-facts">
                <h3 id="device-facts" className="text-sm font-semibold text-foreground">
                  {t('devices.detail.facts')}
                </h3>
                <Facts
                  items={[
                    [
                      t('devices.columns.mac'),
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-xs">{device.mac}</span>
                        {device.randomized ? <RandomizedBadge /> : null}
                      </span>,
                    ],
                    [t('devices.columns.vendor'), device.vendor ? visibleText(device.vendor) : '—'],
                    [
                      t('devices.columns.hostnames'),
                      device.hostnames.length > 0
                        ? device.hostnames.map(visibleText).join(', ')
                        : '—',
                    ],
                    [
                      t('devices.detail.lastIp'),
                      <span className="font-mono text-xs">{device.last_ip ?? '—'}</span>,
                    ],
                    [
                      t('devices.detail.vlan'),
                      device.last_vlan === null
                        ? '—'
                        : t('devices.vlanLabel', {
                            vlan: device.last_vlan,
                            name: device.vlan_name ?? '—',
                          }),
                    ],
                    [t('devices.columns.type'), deviceTypeLabel(device.type, t)],
                    [
                      t('devices.detail.infrastructure'),
                      device.infrastructure ? t('devices.yes') : t('devices.no'),
                    ],
                    [
                      t('devices.columns.firstSeen'),
                      device.first_seen ? formatDateTime(device.first_seen) : '—',
                    ],
                    [
                      t('devices.columns.lastSeen'),
                      device.last_seen ? formatDateTime(device.last_seen) : '—',
                    ],
                    [t('devices.detail.tags'), device.tags.length ? device.tags.join(', ') : '—'],
                    [t('devices.detail.notes'), device.notes ? visibleText(device.notes) : '—'],
                  ]}
                />
              </section>

              <section className="flex flex-col gap-2" aria-labelledby="device-owner">
                <h3 id="device-owner" className="text-sm font-semibold text-foreground">
                  {t('devices.detail.owner')}
                </h3>
                <Facts
                  items={[
                    [t('devices.columns.owner'), <OwnerText device={device} />],
                    [
                      t('devices.detail.assignedSince'),
                      device.assigned_since ? formatDateTime(device.assigned_since) : '—',
                    ],
                    [
                      t('devices.detail.department'),
                      device.department
                        ? t(
                            `devices.detail.departmentSource.${device.department_source ?? 'none'}`,
                            {
                              name: visibleText(device.department.name),
                            },
                          )
                        : device.vlan_name
                          ? // A VLAN without a linked department (e.g. a Wi-Fi network).
                            t('devices.detail.noDepartmentVlan', {
                              vlan: visibleText(device.vlan_name),
                            })
                          : '—',
                    ],
                  ]}
                />
                {manage ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => {
                        onAssign('assign', device);
                      }}
                    >
                      <Link2 aria-hidden="true" />
                      {device.person ? t('devices.detail.reassign') : t('devices.bulk.assign')}
                    </Button>
                    {device.person ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          onAssign('unassign', device);
                        }}
                      >
                        <Unlink aria-hidden="true" />
                        {t('devices.bulk.unassign')}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section className="flex flex-col gap-2" aria-labelledby="device-history">
                <h3 id="device-history" className="text-sm font-semibold text-foreground">
                  {t('devices.history.title')}
                </h3>
                <AssignmentHistory assignments={device.assignments} />
                <p className="text-xs text-muted">{t('devices.history.appendOnly')}</p>
              </section>

              <section className="flex flex-col gap-2" aria-labelledby="device-leases">
                <h3 id="device-leases" className="text-sm font-semibold text-foreground">
                  {t('devices.detail.leases')}
                </h3>
                <LeaseTable leases={device.recent_leases} />
              </section>

              {manage ? (
                <section className="flex flex-col gap-2" aria-labelledby="device-edit">
                  <h3 id="device-edit" className="text-sm font-semibold text-foreground">
                    {t('devices.detail.edit')}
                  </h3>
                  <DeviceEditForm device={device} />
                </section>
              ) : null}
            </>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
