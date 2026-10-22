import { FileDown, MoreHorizontal, RefreshCw, Search, UserPlus, Users } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { DEVICE_PAGE_SIZE, useDepartments, usePersons, useUpdatePerson } from '@/lib/api-hooks';
import type { PersonOut } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { useCsvExport } from './csv-export';
import { Pager, PrivacyWait } from './device-parts';
import { rangeText, useDebounced } from './device-text';
import { personQuery } from './devices-model';
import { PersonDialog } from './person-dialog';
import { PersonSheet, PersonStatus } from './person-sheet';

const ALL = 'all';

/** The row's menu: "Düzenle", "Pasifleştir" / "Etkinleştir" (`devices.manage`). */
function PersonActions({
  person,
  triggerId,
  onAction,
}: {
  person: PersonOut;
  triggerId: string;
  onAction: (action: 'edit' | 'toggle', person: PersonOut, trigger: HTMLElement | null) => void;
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
          aria-label={t('devices.persons.actionsFor', { name: person.name })}
        >
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem
          onSelect={() => {
            onAction('edit', person, trigger.current);
          }}
        >
          {t('devices.persons.edit')}
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => {
            onAction('toggle', person, trigger.current);
          }}
        >
          {person.active ? t('devices.persons.deactivate') : t('devices.persons.activate')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * "Kişiler": persons with search, state and department filters (paged by the server), the
 * person drawer (devices, assignment history) and — with `devices.manage` — adding, editing and
 * deactivating. There is no delete: a deactivated person keeps the assignment history that past
 * 5651 lookups rely on.
 */
export function PersonsTab({
  manage,
  privacyPending,
}: {
  manage: boolean;
  privacyPending: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const tableId = useId();
  const { withReauth } = useReauth();
  const [search, setSearch] = useState('');
  const q = useDebounced(search.trim());
  const [active, setActive] = useState<string>('true');
  const [department, setDepartment] = useState<string>(ALL);
  const filters = {
    search: q,
    active: active === ALL ? null : active === 'true',
    departmentId: department === ALL ? null : Number(department),
  };
  const filterKey = JSON.stringify(filters);
  const [paging, setPaging] = useState({ key: filterKey, page: 0 });
  const page = paging.key === filterKey ? paging.page : 0;
  const query = personQuery(filters, page, DEVICE_PAGE_SIZE);
  const persons = usePersons(query, !privacyPending);
  const departments = useDepartments(!privacyPending);
  const update = useUpdatePerson();
  const exporting = useCsvExport();
  const rows = useMemo(() => persons.data?.items ?? [], [persons.data]);
  const total = persons.data?.total ?? 0;

  const [openId, setOpenId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<{ person: PersonOut | null } | null>(null);
  const [confirm, setConfirm] = useState<PersonOut | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  const act = useCallback(
    (action: 'edit' | 'toggle', person: PersonOut, trigger: HTMLElement | null) => {
      returnTo.current = trigger;
      if (action === 'edit') setDialog({ person });
      else {
        setConfirmError(null);
        setConfirm(person);
      }
    },
    [],
  );

  async function toggleActive() {
    if (!confirm) return;
    const person = confirm;
    setConfirmError(null);
    try {
      await withReauth(() => update.mutateAsync({ id: person.id, active: !person.active }));
      toast({
        title: person.active
          ? t('devices.persons.deactivated', { name: person.name })
          : t('devices.persons.activated', { name: person.name }),
        variant: 'success',
      });
      setConfirm(null);
    } catch (error) {
      if (!isReauthCancelled(error)) setConfirmError(errorMessage(error));
    }
  }

  const columns = useMemo<AnyColumnDef<PersonOut>[]>(() => {
    const list: AnyColumnDef<PersonOut>[] = [
      {
        id: 'name',
        accessorKey: 'name',
        header: t('devices.persons.name'),
        size: 220,
        enableSorting: false,
        meta: { label: t('devices.persons.name') },
        cell: (info) => {
          const row = info.row.original;
          return (
            <button
              type="button"
              id={`${tableId}-open-${row.id}`}
              className="max-w-full truncate rounded-sm text-left font-medium text-accent-ink hover:underline"
              onClick={() => {
                setOpenId(row.id);
              }}
            >
              {visibleText(row.name)}
            </button>
          );
        },
      },
      {
        id: 'department',
        accessorFn: (row) => row.department?.name ?? '',
        header: t('devices.persons.department'),
        size: 160,
        enableSorting: false,
        meta: { label: t('devices.persons.department') },
        cell: (info) =>
          visibleText(info.getValue<string>()) || <span className="text-muted">—</span>,
      },
      {
        id: 'title',
        accessorFn: (row) => row.title ?? '',
        header: t('devices.persons.title'),
        size: 160,
        minSize: 120,
        enableSorting: false,
        meta: { label: t('devices.persons.title'), grow: true },
        cell: (info) =>
          visibleText(info.getValue<string>()) || <span className="text-muted">—</span>,
      },
      {
        id: 'email',
        accessorFn: (row) => row.email ?? '',
        header: t('devices.persons.email'),
        size: 200,
        enableSorting: false,
        meta: { label: t('devices.persons.email') },
        cell: (info) =>
          visibleText(info.getValue<string>()) || <span className="text-muted">—</span>,
      },
      {
        id: 'employee_no',
        accessorFn: (row) => row.employee_no ?? '',
        header: t('devices.persons.employeeNo'),
        size: 110,
        enableSorting: false,
        meta: { label: t('devices.persons.employeeNo'), mono: true },
        cell: (info) =>
          visibleText(info.getValue<string>()) || <span className="text-muted">—</span>,
      },
      {
        id: 'device_count',
        accessorKey: 'device_count',
        header: t('devices.persons.deviceCount'),
        size: 90,
        enableSorting: false,
        meta: { label: t('devices.persons.deviceCount'), numeric: true },
        cell: (info) => formatNumber(info.getValue<number>(), 0, lang),
      },
      {
        id: 'active',
        accessorKey: 'active',
        header: t('devices.persons.status'),
        size: 110,
        enableSorting: false,
        meta: { label: t('devices.persons.status') },
        cell: (info) => <PersonStatus active={info.getValue<boolean>()} />,
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
          <PersonActions
            person={info.row.original}
            triggerId={`${tableId}-actions-${info.row.original.id}`}
            onAction={act}
          />
        ),
      },
    ];
  }, [t, lang, tableId, manage, act]);

  // A change emptied this page (e.g. assigned devices left the filtered list): show the last
  // page that still has rows.
  const lastPage = Math.max(0, Math.ceil(total / DEVICE_PAGE_SIZE) - 1);
  const pastTheEnd =
    persons.isSuccess && !persons.isPlaceholderData && rows.length === 0 && page > lastPage;
  useEffect(() => {
    if (pastTheEnd) setPaging({ key: filterKey, page: lastPage });
  }, [pastTheEnd, filterKey, lastPage]);

  if (privacyPending) return <PrivacyWait />;

  const filtered = q !== '' || active !== 'true' || department !== ALL;
  const toolbar = (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex w-64 max-w-full flex-col gap-1">
        <Label htmlFor={`${tableId}-search`} className="text-xs text-muted-strong">
          {t('devices.persons.search')}
        </Label>
        <Input
          id={`${tableId}-search`}
          type="search"
          autoComplete="off"
          leadingIcon={<Search />}
          placeholder={t('devices.persons.searchPlaceholder')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-active`} className="text-xs text-muted-strong">
          {t('devices.persons.status')}
        </Label>
        <Select value={active} onValueChange={setActive}>
          <SelectTrigger id={`${tableId}-active`} className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="true">{t('devices.persons.activeOnly')}</SelectItem>
            <SelectItem value="false">{t('devices.persons.inactiveOnly')}</SelectItem>
            <SelectItem value={ALL}>{t('devices.persons.allStates')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-department`} className="text-xs text-muted-strong">
          {t('devices.persons.department')}
        </Label>
        <Select value={department} onValueChange={setDepartment}>
          <SelectTrigger id={`${tableId}-department`} className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('devices.persons.allDepartments')}</SelectItem>
            {(departments.data ?? []).map((item) => (
              <SelectItem key={item.id} value={String(item.id)}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button
        variant="secondary"
        className="ms-auto"
        busy={exporting.busy === 'persons'}
        disabled={total === 0}
        title={t(filtered ? 'devices.export.filteredHint' : 'devices.export.allHint')}
        onClick={() => void exporting.download('persons', query)}
      >
        <FileDown aria-hidden="true" />
        {t('devices.export.list')}
      </Button>
    </div>
  );

  const offset = persons.data?.offset ?? page * DEVICE_PAGE_SIZE;
  const nobodyYet = persons.isSuccess && total === 0 && !filtered;
  const addButton = manage ? (
    <Button
      onClick={(event) => {
        returnTo.current = event.currentTarget;
        setDialog({ person: null });
      }}
    >
      <UserPlus aria-hidden="true" />
      {t('devices.persons.add')}
    </Button>
  ) : null;

  return (
    <div className="flex flex-col gap-3">
      {persons.isError ? (
        <Alert tone="danger">
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(persons.error)}
            <Button size="sm" variant="secondary" onClick={() => void persons.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-3xl text-13 text-muted">{t('devices.persons.intro')}</p>
        {addButton}
      </div>
      {nobodyYet ? (
        <EmptyState
          icon={Users}
          title={t('devices.persons.empty')}
          description={manage ? t('devices.persons.emptyHint') : undefined}
        />
      ) : (
        <DataTable
          data={rows}
          columns={columns}
          caption={t('devices.persons.caption')}
          getRowId={(row) => String(row.id)}
          toolbar={toolbar}
          height={Math.min(560, 40 + Math.max(rows.length, 4) * 44)}
          initialDensity="comfortable"
          countLabel={
            persons.data ? rangeText(offset, rows.length, total, t, 'persons') : undefined
          }
          emptyMessage={
            persons.isPending ? t('common.loading') : t('devices.persons.emptyFiltered')
          }
        />
      )}
      <Pager
        offset={offset}
        total={total}
        pageSize={DEVICE_PAGE_SIZE}
        label={t('devices.pager.persons')}
        onPage={(next) => {
          setPaging({ key: filterKey, page: next });
        }}
      />

      <PersonSheet
        personId={openId}
        manage={manage}
        onClose={() => {
          setOpenId(null);
        }}
        onEdit={(person) => {
          // Opened from the drawer: the focus goes back to its "Düzenle" button.
          returnTo.current = null;
          setDialog({ person });
        }}
      />
      <PersonDialog
        open={dialog !== null}
        person={dialog?.person ?? null}
        returnFocus={returnTo}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
      />
      <ConfirmDialog
        returnFocus={returnTo}
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={
          confirm?.active
            ? t('devices.persons.confirmDeactivate.title')
            : t('devices.persons.confirmActivate.title')
        }
        description={
          confirm?.active
            ? t('devices.persons.confirmDeactivate.body', { name: confirm.name })
            : t('devices.persons.confirmActivate.body', { name: confirm?.name ?? '' })
        }
        confirmLabel={
          confirm?.active ? t('devices.persons.deactivate') : t('devices.persons.activate')
        }
        danger={confirm?.active ?? false}
        busy={update.isPending}
        error={confirmError}
        onConfirm={() => void toggleActive()}
      />
    </div>
  );
}
