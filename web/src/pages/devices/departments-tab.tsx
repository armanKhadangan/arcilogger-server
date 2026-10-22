import { Building2, MoreHorizontal, Network, Pencil, Plus, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EmptyState } from '@/components/ui/empty-state';
import { focusBack } from '@/components/ui/focus-return';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
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
import {
  useCreateDepartment,
  useDeleteVlan,
  useDepartments,
  useUpdateDepartment,
  useUpdateVlan,
  useVlans,
} from '@/lib/api-hooks';
import type { DepartmentOut, VlanOut } from '@/lib/api-types';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { validateDisplayName } from '@/lib/validation';

import { NO_DEPARTMENT } from './persons-model';
import { VlanDialog } from './vlan-dialog';
import { vlanLabel } from './vlans-model';

/** "Departman ekle" (`department` null) and "Yeniden adlandır". */
function DepartmentDialog({
  open,
  department,
  onOpenChange,
  returnFocus,
}: {
  open: boolean;
  department: DepartmentOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const create = useCreateDepartment();
  const update = useUpdateDepartment();
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;

  useEffect(() => {
    if (open) {
      setName(department?.name ?? '');
      setNotes(department?.notes ?? '');
      setError(null);
      setFailure(null);
    }
  }, [open, department]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = validateDisplayName(name);
    setError(problem ? t(problem) : null);
    setFailure(null);
    if (problem) {
      document.getElementById(`${id}-name`)?.focus();
      return;
    }
    const trimmed = name.trim();
    const note = notes.trim() || null;
    try {
      if (department) {
        await withReauth(() =>
          update.mutateAsync({
            id: department.id,
            ...(trimmed !== department.name ? { name: trimmed } : {}),
            ...(note !== department.notes ? { notes: note } : {}),
          }),
        );
        toast({ title: t('devices.departments.saved', { name: trimmed }), variant: 'success' });
      } else {
        await withReauth(() =>
          create.mutateAsync({ name: trimmed, ...(note ? { notes: note } : {}) }),
        );
        toast({ title: t('devices.departments.created', { name: trimmed }), variant: 'success' });
      }
      onOpenChange(false);
    } catch (failed) {
      if (!isReauthCancelled(failed)) setFailure(errorMessage(failed));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>
              {department
                ? t('devices.departments.editTitle')
                : t('devices.departments.createTitle')}
            </DialogTitle>
            <DialogDescription>{t('devices.departments.formDescription')}</DialogDescription>
          </DialogHeader>
          {failure ? <Alert tone="danger">{failure}</Alert> : null}
          <Field id={`${id}-name`} label={t('devices.departments.name')} error={error} required>
            <Input
              id={`${id}-name`}
              autoComplete="off"
              value={name}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${id}-name-error` : undefined}
              onChange={(event) => {
                setName(event.target.value);
              }}
              autoFocus
            />
          </Field>
          <Field id={`${id}-notes`} label={t('devices.departments.notes')}>
            <Textarea
              id={`${id}-notes`}
              rows={2}
              value={notes}
              onChange={(event) => {
                setNotes(event.target.value);
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
              {busy
                ? t('common.working')
                : department
                  ? t('common.save')
                  : t('devices.departments.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The department of one VLAN, changed in place (`devices.manage`). */
function VlanDepartment({
  vlan,
  departments,
  manage,
}: {
  vlan: VlanOut;
  departments: readonly DepartmentOut[];
  manage: boolean;
}) {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const update = useUpdateVlan();
  const label = vlanLabel(vlan);
  if (!manage)
    return vlan.department ? vlan.department.name : <span className="text-muted">—</span>;
  return (
    <Select
      value={vlan.department ? String(vlan.department.id) : NO_DEPARTMENT}
      disabled={update.isPending}
      onValueChange={(value) => {
        const departmentId = value === NO_DEPARTMENT ? null : Number(value);
        void withReauth(() =>
          update.mutateAsync({ subnet: vlan.subnet, department_id: departmentId }),
        )
          .then((saved) => {
            toast({
              title: saved.department
                ? t('devices.vlans.saved', { vlan: label, name: saved.department.name })
                : t('devices.vlans.cleared', { vlan: label }),
              variant: 'success',
            });
          })
          .catch((error: unknown) => {
            if (!isReauthCancelled(error)) toast({ title: errorMessage(error), variant: 'danger' });
          });
      }}
    >
      <SelectTrigger
        className="h-8 w-full"
        aria-label={t('devices.vlans.departmentFor', { vlan: label })}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_DEPARTMENT}>{t('devices.persons.noDepartment')}</SelectItem>
        {departments.map((department) => (
          <SelectItem key={department.id} value={String(department.id)}>
            {department.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The VLAN row's menu: "Düzenle", "Sil" (`devices.manage`). The trigger's id lets the focus come
 * back to it when the table re-rendered the row after a change.
 */
function VlanActions({
  vlan,
  triggerId,
  onAction,
}: {
  vlan: VlanOut;
  triggerId: string;
  onAction: (action: 'edit' | 'delete', vlan: VlanOut, trigger: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          ref={trigger}
          id={triggerId}
          variant="ghost"
          size="icon-sm"
          aria-label={t('devices.vlans.actionsFor', { vlan: vlanLabel(vlan) })}
        >
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem
          onSelect={() => {
            onAction('edit', vlan, trigger.current);
          }}
        >
          {t('devices.vlans.edit')}
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => {
            onAction('delete', vlan, trigger.current);
          }}
        >
          {t('devices.vlans.delete')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * "Departmanlar ve VLAN'lar": departments (create, rename) and the site's VLANs/subnets (add,
 * edit, delete — ARCILOGGER ships none) with their departments. A device's department is its
 * person's; a device without a person takes its VLAN's department.
 */
export function DepartmentsTab({ manage }: { manage: boolean }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const tableId = useId();
  const { withReauth } = useReauth();
  const departments = useDepartments();
  const vlans = useVlans();
  const removeVlan = useDeleteVlan();
  const [dialog, setDialog] = useState<{ department: DepartmentOut | null } | null>(null);
  const [vlanDialog, setVlanDialog] = useState<{ vlan: VlanOut | null } | null>(null);
  const [deleting, setDeleting] = useState<VlanOut | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const addVlan = useRef<HTMLButtonElement>(null);
  /** The new network of the VLAN just edited (`null`: the network stayed). */
  const movedTo = useRef<string | null>(null);
  /** The id of a VLAN row's menu button (the table names a row by its subnet). */
  const vlanActionsId = useCallback(
    (subnet: string) => `${tableId}-vlan-actions-${subnet}`,
    [tableId],
  );

  const actOnVlan = useCallback(
    (action: 'edit' | 'delete', vlan: VlanOut, trigger: HTMLElement | null) => {
      returnTo.current = trigger;
      if (action === 'edit') setVlanDialog({ vlan });
      else {
        setDeleteError(null);
        setDeleting(vlan);
      }
    },
    [],
  );

  async function deleteVlan() {
    if (!deleting) return;
    const vlan = deleting;
    setDeleteError(null);
    try {
      await withReauth(() => removeVlan.mutateAsync(vlan.subnet));
      toast({ title: t('devices.vlans.deleted', { vlan: vlanLabel(vlan) }), variant: 'success' });
      // The row and its menu button are gone: the focus goes to "VLAN ekle".
      returnTo.current = addVlan.current;
      setDeleting(null);
    } catch (error) {
      if (!isReauthCancelled(error)) setDeleteError(errorMessage(error));
    }
  }

  const departmentRows = useMemo(() => departments.data ?? [], [departments.data]);
  const vlanRows = useMemo(
    () =>
      [...(vlans.data ?? [])].sort(
        (a, b) => (a.vlan_id ?? Number.MAX_SAFE_INTEGER) - (b.vlan_id ?? Number.MAX_SAFE_INTEGER),
      ),
    [vlans.data],
  );

  const departmentColumns = useMemo<AnyColumnDef<DepartmentOut>[]>(() => {
    const list: AnyColumnDef<DepartmentOut>[] = [
      {
        id: 'name',
        accessorKey: 'name',
        header: t('devices.departments.name'),
        size: 200,
        meta: { label: t('devices.departments.name') },
        cell: (info) => <span className="font-medium">{info.getValue<string>()}</span>,
      },
      {
        id: 'person_count',
        accessorKey: 'person_count',
        header: t('devices.departments.personCount'),
        size: 100,
        meta: { label: t('devices.departments.personCount'), numeric: true },
        cell: (info) => formatNumber(info.getValue<number>(), 0, lang),
      },
      {
        id: 'vlans',
        accessorFn: (row) => row.vlan_ids.join(', '),
        header: t('devices.departments.vlans'),
        size: 140,
        enableSorting: false,
        meta: { label: t('devices.departments.vlans') },
        cell: (info) => info.getValue<string>() || <span className="text-muted">—</span>,
      },
      {
        id: 'notes',
        accessorFn: (row) => row.notes ?? '',
        header: t('devices.departments.notes'),
        size: 160,
        minSize: 120,
        enableSorting: false,
        meta: { label: t('devices.departments.notes'), grow: true },
        cell: (info) => {
          const text = info.getValue<string>();
          return text ? <span title={text}>{text}</span> : <span className="text-muted">—</span>;
        },
      },
    ];
    if (!manage) return list;
    return [
      ...list,
      {
        id: 'actions',
        header: () => <span className="sr-only">{t('devices.persons.actions')}</span>,
        size: 64,
        enableSorting: false,
        enableHiding: false,
        meta: { label: t('devices.persons.actions') },
        cell: (info) => (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('devices.departments.renameFor', { name: info.row.original.name })}
            onClick={(event) => {
              returnTo.current = event.currentTarget;
              setDialog({ department: info.row.original });
            }}
          >
            <Pencil aria-hidden="true" />
          </Button>
        ),
      },
    ];
  }, [t, lang, manage]);

  const vlanColumns = useMemo<AnyColumnDef<VlanOut>[]>(() => {
    const list: AnyColumnDef<VlanOut>[] = [
      {
        id: 'vlan_id',
        accessorFn: (row) => row.vlan_id ?? 0,
        header: t('devices.vlans.vlan'),
        size: 80,
        meta: { label: t('devices.vlans.vlan'), numeric: true },
        cell: (info) => info.row.original.vlan_id ?? '—',
      },
      {
        id: 'subnet',
        accessorKey: 'subnet',
        header: t('devices.vlans.subnet'),
        size: 150,
        meta: { label: t('devices.vlans.subnet'), mono: true },
      },
      {
        id: 'name_tr',
        accessorKey: 'name_tr',
        header: t('devices.vlans.name'),
        size: 170,
        meta: { label: t('devices.vlans.name') },
        cell: (info) => (
          <span className="flex items-center gap-1.5">
            <span className="truncate">{info.getValue<string>()}</span>
            {info.row.original.guest ? <Badge tone="info">{t('devices.vlans.guest')}</Badge> : null}
          </span>
        ),
      },
      {
        id: 'purpose',
        accessorFn: (row) => row.purpose ?? '',
        header: t('devices.vlans.purpose'),
        size: 160,
        minSize: 120,
        enableSorting: false,
        meta: { label: t('devices.vlans.purpose'), grow: true },
        cell: (info) => {
          const text = info.getValue<string>();
          return text ? <span title={text}>{text}</span> : <span className="text-muted">—</span>;
        },
      },
      {
        id: 'dhcp_server',
        accessorFn: (row) => row.dhcp_server ?? '',
        header: t('devices.vlans.dhcpServer'),
        size: 120,
        enableSorting: false,
        meta: { label: t('devices.vlans.dhcpServer'), mono: true },
        cell: (info) => info.getValue<string>() || <span className="text-muted">—</span>,
      },
      {
        id: 'department',
        accessorFn: (row) => row.department?.name ?? '',
        header: t('devices.vlans.department'),
        size: 220,
        enableSorting: false,
        meta: { label: t('devices.vlans.department') },
        cell: (info) => (
          <VlanDepartment vlan={info.row.original} departments={departmentRows} manage={manage} />
        ),
      },
    ];
    if (!manage) return list;
    return [
      ...list,
      {
        id: 'actions',
        header: () => <span className="sr-only">{t('devices.persons.actions')}</span>,
        size: 64,
        enableSorting: false,
        enableHiding: false,
        meta: { label: t('devices.persons.actions') },
        cell: (info) => (
          <VlanActions
            vlan={info.row.original}
            triggerId={vlanActionsId(info.row.original.subnet)}
            onAction={actOnVlan}
          />
        ),
      },
    ];
  }, [t, departmentRows, manage, actOnVlan, vlanActionsId]);

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-1">
            <CardTitle>{t('devices.departments.title')}</CardTitle>
            <CardDescription>{t('devices.departments.description')}</CardDescription>
          </div>
          {manage ? (
            <Button
              size="sm"
              onClick={(event) => {
                returnTo.current = event.currentTarget;
                setDialog({ department: null });
              }}
            >
              <Plus aria-hidden="true" />
              {t('devices.departments.add')}
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {departments.isError ? (
            <Alert tone="danger">
              <span className="flex flex-wrap items-center gap-2">
                {errorMessage(departments.error)}
                <Button size="sm" variant="secondary" onClick={() => void departments.refetch()}>
                  <RefreshCw aria-hidden="true" />
                  {t('common.retry')}
                </Button>
              </span>
            </Alert>
          ) : departments.isSuccess && departmentRows.length === 0 ? (
            <EmptyState
              icon={Building2}
              title={t('devices.departments.empty')}
              description={manage ? t('devices.departments.emptyHint') : undefined}
            />
          ) : (
            <DataTable
              data={departmentRows}
              columns={departmentColumns}
              caption={t('devices.departments.caption')}
              getRowId={(row) => String(row.id)}
              height={Math.min(420, 40 + Math.max(departmentRows.length, 3) * 44)}
              initialDensity="comfortable"
              emptyMessage={t('common.loading')}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-1">
            <CardTitle>{t('devices.vlans.title')}</CardTitle>
            <CardDescription>{t('devices.vlans.description')}</CardDescription>
          </div>
          {manage ? (
            <Button
              ref={addVlan}
              size="sm"
              onClick={(event) => {
                returnTo.current = event.currentTarget;
                setVlanDialog({ vlan: null });
              }}
            >
              <Plus aria-hidden="true" />
              {t('devices.vlans.add')}
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {vlans.isError ? (
            <Alert tone="danger">
              <span className="flex flex-wrap items-center gap-2">
                {errorMessage(vlans.error)}
                <Button size="sm" variant="secondary" onClick={() => void vlans.refetch()}>
                  <RefreshCw aria-hidden="true" />
                  {t('common.retry')}
                </Button>
              </span>
            </Alert>
          ) : vlans.isSuccess && vlanRows.length === 0 ? (
            <EmptyState
              icon={Network}
              title={t('devices.vlans.empty')}
              description={
                manage ? t('devices.vlans.emptyHint') : t('devices.vlans.emptyHintReadOnly')
              }
            />
          ) : (
            <DataTable
              data={vlanRows}
              columns={vlanColumns}
              caption={t('devices.vlans.caption')}
              getRowId={(row) => row.subnet}
              height={Math.min(640, 40 + Math.max(vlanRows.length, 3) * 44)}
              initialDensity="comfortable"
              emptyMessage={t('common.loading')}
            />
          )}
        </CardContent>
      </Card>

      <DepartmentDialog
        open={dialog !== null}
        department={dialog?.department ?? null}
        returnFocus={returnTo}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
      />
      <VlanDialog
        open={vlanDialog !== null}
        vlan={vlanDialog?.vlan ?? null}
        returnFocus={returnTo}
        onOpenChange={(open) => {
          if (!open) setVlanDialog(null);
        }}
        onSaved={(saved) => {
          const before = vlanDialog?.vlan;
          movedTo.current = before && saved.subnet !== before.subnet ? saved.subnet : null;
        }}
        onCloseAutoFocus={(event) => {
          // A moved VLAN is a new row: the old row's menu button is gone. The focus goes to the
          // new row's, which the reloaded list shows by now (else to "VLAN ekle").
          const subnet = movedTo.current;
          movedTo.current = null;
          if (subnet === null) return;
          if (focusBack(document.getElementById(vlanActionsId(subnet)) ?? addVlan.current))
            event.preventDefault();
        }}
      />
      <ConfirmDialog
        returnFocus={returnTo}
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={t('devices.vlans.confirmDelete.title')}
        description={t('devices.vlans.confirmDelete.body', {
          vlan: deleting ? vlanLabel(deleting) : '',
          subnet: deleting?.subnet ?? '',
          name: deleting?.name_tr ?? '',
        })}
        confirmLabel={t('devices.vlans.delete')}
        danger
        busy={removeVlan.isPending}
        error={deleteError}
        onConfirm={() => void deleteVlan()}
      />
    </div>
  );
}
