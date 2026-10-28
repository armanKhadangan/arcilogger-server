import { useId, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { useCreateVlan, useDepartments, useUpdateVlan } from '@/lib/api-hooks';
import type { VlanOut } from '@/lib/api-types';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { NO_DEPARTMENT } from './persons-model';
import {
  checkSubnet,
  validateVlan,
  vlanChanges,
  vlanCreateBody,
  vlanFormValues,
  vlanLabel,
  type VlanField,
  type VlanFormValues,
} from './vlans-model';

/**
 * "VLAN ekle" (`vlan` null) and "VLAN'ı düzenle": the network (with its prefix length), the VLAN
 * id, the name and the optional fields. An edit sends only what changed; a changed network
 * keeps the VLAN's name, department and flags.
 */
export function VlanDialog({
  open,
  vlan,
  onOpenChange,
  onSaved,
  returnFocus,
  onCloseAutoFocus,
}: {
  open: boolean;
  /** `null`: a new VLAN. */
  vlan: VlanOut | null;
  onOpenChange: (open: boolean) => void;
  /** The VLAN as the server saved it, before the dialog closes. */
  onSaved?: (saved: VlanOut) => void;
  returnFocus?: RefObject<HTMLElement | null>;
  /** Where the focus goes on close, before `returnFocus` (`event.preventDefault()` when done). */
  onCloseAutoFocus?: (event: Event) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const departments = useDepartments(open);
  const create = useCreateVlan();
  const update = useUpdateVlan();
  const [values, setValues] = useState<VlanFormValues>(() => vlanFormValues(vlan));
  const [errors, setErrors] = useState<FieldErrors<VlanField>>({});
  const [network, setNetwork] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;

  // The form begins while rendering when the dialog opens (or for another VLAN), so its first
  // frame already shows that VLAN's values: an effect would run only after that frame.
  const openFor = open ? vlan : undefined;
  const [begunFor, setBegunFor] = useState<VlanOut | null | undefined>(undefined);
  if (openFor !== begunFor) {
    setBegunFor(openFor);
    if (open) {
      setValues(vlanFormValues(vlan));
      setErrors({});
      setNetwork(null);
      setFailure(null);
    }
  }

  function set<K extends keyof VlanFormValues>(field: K, value: VlanFormValues[K]) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateVlan(values);
    const subnet = checkSubnet(values.subnet);
    // Host bits set: the error names the IPv4 network meant.
    setNetwork(!subnet.ok && 'network' in subnet ? subnet.network : null);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    try {
      if (vlan) {
        const changes = vlanChanges(vlan, values);
        const saved = changes ? await withReauth(() => update.mutateAsync(changes)) : vlan;
        onSaved?.(saved);
        toast({
          title: t('devices.vlans.savedVlan', { vlan: vlanLabel(saved) }),
          variant: 'success',
        });
      } else {
        const created = await withReauth(() => create.mutateAsync(vlanCreateBody(values)));
        onSaved?.(created);
        toast({
          title: t('devices.vlans.created', { vlan: vlanLabel(created) }),
          variant: 'success',
        });
      }
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const message = (field: VlanField) =>
    errors[field] &&
    (errors[field] === 'devices.vlans.errors.hostBits' && network === null
      ? t('devices.vlans.errors.hostBitsNoNetwork')
      : t(errors[field], { network }));
  const describedBy = (field: VlanField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;
  const text = (field: Exclude<VlanField, 'notes'>) => ({
    id: `${id}-${field}`,
    autoComplete: 'off',
    value: values[field],
    'aria-invalid': message(field) ? true : undefined,
    onChange: (event: { target: { value: string } }) => {
      set(field, event.target.value);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        returnFocus={returnFocus}
        onCloseAutoFocus={onCloseAutoFocus}
        className="max-w-xl"
      >
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>
              {vlan ? t('devices.vlans.editTitle') : t('devices.vlans.createTitle')}
            </DialogTitle>
            <DialogDescription>{t('devices.vlans.formDescription')}</DialogDescription>
          </DialogHeader>
          {failure ? <Alert tone="danger">{failure}</Alert> : null}
          <div className="grid gap-4 sm:grid-cols-[1fr_9rem]">
            <Field
              id={`${id}-subnet`}
              label={t('devices.vlans.subnet')}
              hint={vlan ? t('devices.vlans.subnetEditHint') : t('devices.vlans.subnetHint')}
              error={message('subnet')}
              required
            >
              <Input
                {...text('subnet')}
                className="font-mono"
                spellCheck={false}
                placeholder="10.0.20.0/24"
                aria-describedby={describedBy('subnet', true)}
                autoFocus
              />
            </Field>
            <Field
              id={`${id}-vlanId`}
              label={t('devices.vlans.vlanId')}
              hint={t('devices.vlans.vlanIdHint')}
              error={message('vlanId')}
            >
              <Input
                {...text('vlanId')}
                inputMode="numeric"
                aria-describedby={describedBy('vlanId', true)}
              />
            </Field>
          </div>
          <Field id={`${id}-name`} label={t('devices.vlans.name')} error={message('name')} required>
            <Input {...text('name')} aria-describedby={describedBy('name')} />
          </Field>
          <Field id={`${id}-purpose`} label={t('devices.vlans.purpose')} error={message('purpose')}>
            <Input {...text('purpose')} aria-describedby={describedBy('purpose')} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              id={`${id}-dhcpServer`}
              label={t('devices.vlans.dhcpServer')}
              hint={t('devices.vlans.dhcpServerHint')}
              error={message('dhcpServer')}
            >
              <Input
                {...text('dhcpServer')}
                className="font-mono"
                spellCheck={false}
                aria-describedby={describedBy('dhcpServer', true)}
              />
            </Field>
            <Field
              id={`${id}-department`}
              label={t('devices.vlans.department')}
              hint={t('devices.vlans.departmentHint')}
            >
              <Select
                value={values.department}
                onValueChange={(value) => {
                  set('department', value);
                }}
              >
                <SelectTrigger id={`${id}-department`} aria-describedby={`${id}-department-hint`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_DEPARTMENT}>{t('devices.persons.noDepartment')}</SelectItem>
                  {(departments.data ?? []).map((department) => (
                    <SelectItem key={department.id} value={String(department.id)}>
                      {department.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="flex items-start gap-2">
            <Checkbox
              id={`${id}-guest`}
              checked={values.guest}
              aria-describedby={`${id}-guest-hint`}
              onCheckedChange={(checked) => {
                set('guest', checked === true);
              }}
              className="mt-0.5"
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-guest`} className="font-normal">
                {t('devices.vlans.guestLabel')}
              </Label>
              <p id={`${id}-guest-hint`} className="text-xs text-muted">
                {t('devices.vlans.guestHint')}
              </p>
            </div>
          </div>
          <Field id={`${id}-notes`} label={t('devices.vlans.notes')} error={message('notes')}>
            <Textarea
              id={`${id}-notes`}
              rows={2}
              value={values.notes}
              aria-invalid={message('notes') ? true : undefined}
              aria-describedby={describedBy('notes')}
              onChange={(event) => {
                set('notes', event.target.value);
              }}
            />
          </Field>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={busy}>
              {busy ? t('common.working') : vlan ? t('common.save') : t('devices.vlans.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
