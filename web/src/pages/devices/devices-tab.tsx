import {
  FileDown,
  FilterX,
  Link2,
  MonitorSmartphone,
  RefreshCw,
  Search,
  Unlink,
  UserX,
} from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
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
import { errorMessage } from '@/lib/api';
import { DEVICE_PAGE_SIZE, useDevices, useVlans } from '@/lib/api-hooks';
import type { DeviceOut } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDate, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { AssignDialog } from './assign-dialog';
import type { AssignMode } from './assign-model';
import { useCsvExport } from './csv-export';
import { OwnerText, Pager, PrivacyWait, RandomizedBadge } from './device-parts';
import { DeviceSheet } from './device-sheet';
import { deviceTypeLabel, rangeText, useDebounced } from './device-text';
import {
  DEFAULT_VIEW,
  DEVICE_TYPES,
  deviceQuery,
  isDeviceType,
  isFiltered,
  sortingOf,
  viewFromSearch,
  viewToSearch,
  viewWithSorting,
  type DeviceView,
} from './devices-model';

const ALL = 'all';

function tristate(value: boolean | null): string {
  return value === null ? ALL : String(value);
}

function fromTristate(value: string): boolean | null {
  return value === ALL ? null : value === 'true';
}

/**
 * "Cihazlar": every device the network has shown (server-side search, filters, sort and paging),
 * the "Bilinmeyen cihazlar" quick filter, the detail drawer and — with `devices.manage` — row
 * selection with "Kişiye ata" / "Atamayı kaldır" for the selected devices of the page.
 */
export function DevicesTab({
  manage,
  privacyPending,
}: {
  manage: boolean;
  privacyPending: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const tableId = useId();
  const [params, setParams] = useSearchParams();
  const view = useMemo(() => viewFromSearch(params), [params]);
  const [search, setSearch] = useState('');
  const q = useDebounced(search.trim());
  /** The page belongs to one filter combination: another filter starts at page 1 again. */
  const filterKey = JSON.stringify([view, q]);
  const [paging, setPaging] = useState({ key: filterKey, page: 0 });
  const page = paging.key === filterKey ? paging.page : 0;
  const query = deviceQuery(view, q, page, DEVICE_PAGE_SIZE);
  const devices = useDevices(query, !privacyPending);
  const vlans = useVlans(!privacyPending);
  const rows = useMemo(() => devices.data?.items ?? [], [devices.data]);
  const total = devices.data?.total ?? 0;
  const exporting = useCsvExport();

  /** Selection of the page on screen (another page or filter starts empty). */
  const pageKey = `${filterKey}#${page}`;
  const [selection, setSelection] = useState<{ key: string; macs: ReadonlySet<string> }>({
    key: pageKey,
    macs: new Set(),
  });
  const selectedMacs = useMemo(
    () => (selection.key === pageKey ? selection.macs : new Set<string>()),
    [selection, pageKey],
  );
  const selected = useMemo(
    () => rows.filter((row) => selectedMacs.has(row.mac)),
    [rows, selectedMacs],
  );

  const [openMac, setOpenMac] = useState<string | null>(null);
  const [assign, setAssign] = useState<{
    mode: AssignMode;
    devices: DeviceOut[];
    fromBulk: boolean;
  } | null>(null);
  const bulkButton = useRef<HTMLElement | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  /**
   * Focus after the dialog of the selection bar: back to its button, or — once the assignment
   * cleared the selection and the bar is gone — to the search box.
   */
  const bulkReturn = useMemo<RefObject<HTMLElement | null>>(
    () => ({
      get current() {
        const button = bulkButton.current;
        return button?.isConnected ? button : searchInput.current;
      },
    }),
    [],
  );

  const setView = useCallback(
    (next: DeviceView) => {
      setParams((current) => viewToSearch(next, current), { replace: true });
    },
    [setParams],
  );

  const toggle = useCallback(
    (mac: string, on: boolean) => {
      setSelection((current) => {
        const macs = new Set(current.key === pageKey ? current.macs : []);
        if (on) macs.add(mac);
        else macs.delete(mac);
        return { key: pageKey, macs };
      });
    },
    [pageKey],
  );

  const allOnPage = rows.length > 0 && selected.length === rows.length;
  const someOnPage = selected.length > 0 && !allOnPage;

  const columns = useMemo<AnyColumnDef<DeviceOut>[]>(() => {
    const list: AnyColumnDef<DeviceOut>[] = [
      {
        id: 'mac',
        accessorKey: 'mac',
        header: t('devices.columns.mac'),
        size: 235,
        meta: { label: t('devices.columns.mac') },
        cell: (info) => {
          const row = info.row.original;
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <button
                type="button"
                id={`${tableId}-open-${row.mac}`}
                className="shrink-0 rounded-sm font-mono text-xs text-accent-ink hover:underline"
                title={t('devices.openDetail')}
                onClick={() => {
                  setOpenMac(row.mac);
                }}
              >
                {row.mac}
              </button>
              {row.randomized ? <RandomizedBadge short /> : null}
            </span>
          );
        },
      },
      {
        // Name, host names and vendor in one column: every column of spec 12.3 item 11 fits the
        // page at 1440 px with the menu open (the drawer shows each in full).
        id: 'name',
        accessorFn: (row) => row.name ?? '',
        header: t('devices.columns.device'),
        size: 180,
        minSize: 160,
        meta: { label: t('devices.columns.deviceLabel'), grow: true },
        cell: (info) => {
          const row = info.row.original;
          const name = row.name ? visibleText(row.name) : null;
          const hostnames = row.hostnames.map(visibleText).join(', ');
          const vendor = row.vendor ? visibleText(row.vendor) : '';
          const second = [hostnames, vendor].filter(Boolean).join(' · ');
          return (
            <span
              className="flex min-w-0 flex-col leading-tight"
              title={[name, hostnames, vendor].filter(Boolean).join(' · ')}
            >
              <span className={name ? 'truncate' : 'truncate text-muted'}>
                {name ?? t('devices.unnamed')}
              </span>
              {second ? <span className="truncate text-xs text-muted-strong">{second}</span> : null}
            </span>
          );
        },
      },
      {
        id: 'last_ip',
        accessorFn: (row) => row.last_ip ?? '',
        header: t('devices.columns.lastIp'),
        size: 165,
        meta: { label: t('devices.columns.lastIp') },
        cell: (info) => {
          const row = info.row.original;
          return (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-mono text-xs">{row.last_ip ?? '—'}</span>
              {row.last_vlan !== null ? (
                <span className="truncate text-xs text-muted-strong">
                  {t('devices.vlanLabel', { vlan: row.last_vlan, name: row.vlan_name ?? '—' })}
                </span>
              ) : null}
            </span>
          );
        },
      },
      {
        id: 'type',
        accessorFn: (row) => deviceTypeLabel(row.type, t),
        header: t('devices.columns.type'),
        size: 95,
        enableSorting: false,
        meta: { label: t('devices.columns.type') },
      },
      {
        id: 'owner',
        accessorFn: (row) => row.person?.name ?? '',
        header: t('devices.columns.owner'),
        size: 160,
        enableSorting: false,
        meta: { label: t('devices.columns.owner') },
        cell: (info) => <OwnerText device={info.row.original} />,
      },
      {
        id: 'first_seen',
        accessorFn: (row) => row.first_seen ?? '',
        header: t('devices.columns.firstSeen'),
        size: 110,
        sortDescFirst: true,
        meta: { label: t('devices.columns.firstSeen') },
        cell: (info) => {
          const value = info.row.original.first_seen;
          // The day only (the drawer has the time): the column stays narrow.
          return value ? (
            <span className="tabular" title={formatDateTime(value)}>
              {formatDate(value)}
            </span>
          ) : (
            '—'
          );
        },
      },
      {
        id: 'last_seen',
        accessorFn: (row) => row.last_seen ?? '',
        header: t('devices.columns.lastSeen'),
        size: 165,
        sortDescFirst: true,
        meta: { label: t('devices.columns.lastSeen') },
        cell: (info) => {
          const value = info.row.original.last_seen;
          return <span className="tabular">{value ? formatDateTime(value) : '—'}</span>;
        },
      },
    ];
    if (!manage) return list;
    return [
      {
        id: 'select',
        header: () => (
          <Checkbox
            aria-label={t('devices.selectPage')}
            checked={allOnPage ? true : someOnPage ? 'indeterminate' : false}
            disabled={rows.length === 0}
            onCheckedChange={(checked) => {
              setSelection({
                key: pageKey,
                macs: new Set(checked === true ? rows.map((row) => row.mac) : []),
              });
            }}
          />
        ),
        size: 44,
        enableSorting: false,
        enableHiding: false,
        meta: { label: t('devices.columns.select') },
        cell: (info) => {
          const mac = info.row.original.mac;
          return (
            <Checkbox
              aria-label={t('devices.selectOne', { mac })}
              checked={selectedMacs.has(mac)}
              onCheckedChange={(checked) => {
                toggle(mac, checked === true);
              }}
            />
          );
        },
      },
      ...list,
    ];
  }, [t, tableId, manage, allOnPage, someOnPage, rows, pageKey, selectedMacs, toggle]);

  // A change emptied this page (e.g. assigned devices left the filtered list): show the last
  // page that still has rows.
  const lastPage = Math.max(0, Math.ceil(total / DEVICE_PAGE_SIZE) - 1);
  const pastTheEnd =
    devices.isSuccess && !devices.isPlaceholderData && rows.length === 0 && page > lastPage;
  useEffect(() => {
    if (pastTheEnd) setPaging({ key: filterKey, page: lastPage });
  }, [pastTheEnd, filterKey, lastPage]);

  if (privacyPending) return <PrivacyWait />;

  const filtered = isFiltered(view, search);
  const vlanOptions = (vlans.data ?? [])
    .filter((vlan): vlan is typeof vlan & { vlan_id: number } => vlan.vlan_id !== null)
    .sort((a, b) => a.vlan_id - b.vlan_id);

  const toolbar = (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex w-60 max-w-full flex-col gap-1">
        <Label htmlFor={`${tableId}-search`} className="text-xs text-muted-strong">
          {t('devices.search')}
        </Label>
        <Input
          ref={searchInput}
          id={`${tableId}-search`}
          type="search"
          autoComplete="off"
          spellCheck={false}
          leadingIcon={<Search />}
          placeholder={t('devices.searchPlaceholder')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
      </div>
      <Button
        variant={view.unknown ? 'primary' : 'secondary'}
        aria-pressed={view.unknown}
        onClick={() => {
          setView({ ...view, unknown: !view.unknown });
        }}
      >
        <UserX aria-hidden="true" />
        {t('devices.unknownFilter')}
      </Button>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-type`} className="text-xs text-muted-strong">
          {t('devices.columns.type')}
        </Label>
        <Select
          value={view.type ?? ALL}
          onValueChange={(value) => {
            setView({ ...view, type: isDeviceType(value) ? value : null });
          }}
        >
          <SelectTrigger id={`${tableId}-type`} className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('devices.filters.allTypes')}</SelectItem>
            {DEVICE_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {deviceTypeLabel(type, t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-vlan`} className="text-xs text-muted-strong">
          {t('devices.filters.vlan')}
        </Label>
        <Select
          value={view.vlan === null ? ALL : String(view.vlan)}
          onValueChange={(value) => {
            setView({ ...view, vlan: value === ALL ? null : Number(value) });
          }}
        >
          <SelectTrigger id={`${tableId}-vlan`} className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('devices.filters.allVlans')}</SelectItem>
            {view.vlan !== null && !vlanOptions.some((vlan) => vlan.vlan_id === view.vlan) ? (
              <SelectItem value={String(view.vlan)}>
                {t('devices.vlanLabel', { vlan: view.vlan, name: '—' })}
              </SelectItem>
            ) : null}
            {vlanOptions.map((vlan) => (
              <SelectItem key={vlan.subnet} value={String(vlan.vlan_id)}>
                {t('devices.vlanLabel', { vlan: vlan.vlan_id, name: vlan.name_tr })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-randomized`} className="text-xs text-muted-strong">
          {t('devices.filters.mac')}
        </Label>
        <Select
          value={tristate(view.randomized)}
          onValueChange={(value) => {
            setView({ ...view, randomized: fromTristate(value) });
          }}
        >
          <SelectTrigger id={`${tableId}-randomized`} className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('devices.filters.allMacs')}</SelectItem>
            <SelectItem value="true">{t('devices.filters.randomizedOnly')}</SelectItem>
            <SelectItem value="false">{t('devices.filters.fixedOnly')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${tableId}-infrastructure`} className="text-xs text-muted-strong">
          {t('devices.filters.infrastructure')}
        </Label>
        <Select
          value={tristate(view.infrastructure)}
          onValueChange={(value) => {
            setView({ ...view, infrastructure: fromTristate(value) });
          }}
        >
          <SelectTrigger id={`${tableId}-infrastructure`} className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('devices.filters.allDevices')}</SelectItem>
            <SelectItem value="true">{t('devices.filters.infrastructureOnly')}</SelectItem>
            <SelectItem value="false">{t('devices.filters.noInfrastructure')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {filtered ? (
        <Button
          variant="ghost"
          onClick={() => {
            setSearch('');
            setView({ ...DEFAULT_VIEW, sort: view.sort, order: view.order });
          }}
        >
          <FilterX aria-hidden="true" />
          {t('devices.filters.clear')}
        </Button>
      ) : null}
      <Button
        variant="secondary"
        className="ms-auto"
        busy={exporting.busy === 'devices'}
        disabled={total === 0}
        title={t(filtered ? 'devices.export.filteredHint' : 'devices.export.allHint')}
        onClick={() => void exporting.download('devices', query)}
      >
        <FileDown aria-hidden="true" />
        {t('devices.export.list')}
      </Button>
    </div>
  );

  const offset = devices.data?.offset ?? page * DEVICE_PAGE_SIZE;
  const noDevicesAtAll = devices.isSuccess && total === 0 && !filtered;

  return (
    <div className="flex flex-col gap-3">
      {devices.isError ? (
        <Alert tone="danger">
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(devices.error)}
            <Button size="sm" variant="secondary" onClick={() => void devices.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {manage && selected.length > 0 ? (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-primary-soft px-3 py-2"
          data-testid="bulk-bar"
        >
          <span className="text-13 font-medium text-primary-ink" aria-live="polite">
            {t('devices.bulk.selected', {
              count: selected.length,
              formattedCount: formatNumber(selected.length, 0, lang),
            })}
          </span>
          <Button
            size="sm"
            onClick={(event) => {
              bulkButton.current = event.currentTarget;
              setAssign({ mode: 'assign', devices: selected, fromBulk: true });
            }}
          >
            <Link2 aria-hidden="true" />
            {t('devices.bulk.assign')}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={selected.every((device) => device.person === null)}
            onClick={(event) => {
              bulkButton.current = event.currentTarget;
              setAssign({ mode: 'unassign', devices: selected, fromBulk: true });
            }}
          >
            <Unlink aria-hidden="true" />
            {t('devices.bulk.unassign')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSelection({ key: pageKey, macs: new Set() });
            }}
          >
            {t('devices.bulk.clear')}
          </Button>
        </div>
      ) : null}
      {noDevicesAtAll ? (
        <>
          {toolbar}
          <EmptyState
            icon={MonitorSmartphone}
            title={t('devices.empty')}
            description={t('devices.emptyHint')}
          />
        </>
      ) : (
        <DataTable
          data={rows}
          columns={columns}
          caption={t('devices.caption')}
          getRowId={(row) => row.mac}
          toolbar={toolbar}
          height={Math.min(560, 40 + Math.max(rows.length, 4) * 44)}
          initialDensity="comfortable"
          sorting={sortingOf(view)}
          onSortingChange={(sorting) => {
            setView(viewWithSorting(view, sorting));
          }}
          countLabel={
            devices.data ? rangeText(offset, rows.length, total, t, 'devices') : undefined
          }
          emptyMessage={
            devices.isPending ? (
              t('common.loading')
            ) : (
              <span className="flex flex-col items-center gap-2">
                {t('devices.emptyFiltered')}
                {filtered ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setSearch('');
                      setView({ ...DEFAULT_VIEW, sort: view.sort, order: view.order });
                    }}
                  >
                    <FilterX aria-hidden="true" />
                    {t('devices.filters.clear')}
                  </Button>
                ) : null}
              </span>
            )
          }
        />
      )}
      <Pager
        offset={offset}
        total={total}
        pageSize={DEVICE_PAGE_SIZE}
        label={t('devices.pager.devices')}
        onPage={(next) => {
          setPaging({ key: filterKey, page: next });
        }}
      />

      <DeviceSheet
        mac={openMac}
        manage={manage}
        onClose={() => {
          setOpenMac(null);
        }}
        onAssign={(mode, device) => {
          setAssign({ mode, devices: [device], fromBulk: false });
        }}
      />
      <AssignDialog
        mode={assign?.mode ?? 'assign'}
        devices={assign?.devices ?? []}
        open={assign !== null}
        returnFocus={assign?.fromBulk ? bulkReturn : undefined}
        onOpenChange={(open) => {
          if (!open) setAssign(null);
        }}
        onDone={() => {
          setSelection({ key: pageKey, macs: new Set() });
        }}
      />
    </div>
  );
}
