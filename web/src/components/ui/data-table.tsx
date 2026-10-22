import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type RowData,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Download, Rows3 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { downloadText, toCsv, type CsvValue } from '@/lib/csv';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { Button } from './button';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from './dropdown-menu';

declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- signature fixed by the library
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Header text for the column chooser and CSV export. */
    label?: string;
    /** Right-aligned, tabular figures. */
    numeric?: boolean;
    /** Monospace (IP, MAC, hashes, raw lines). */
    mono?: boolean;
    /** Value written to CSV; defaults to the cell's accessor value. */
    exportValue?: (row: TData) => CsvValue;
    /**
     * Takes the width the other (fixed) columns leave, at least its `minSize`: a long text
     * (truncated, full text in its `title`) that must not push other columns out of view.
     */
    grow?: boolean;
  }
}

/**
 * Column definitions of mixed value types. TanStack Table needs `any` here (its documented
 * pattern); cell renderers still get typed values through `info.getValue<T>()`.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyColumnDef<T> = ColumnDef<T, any>;

export type Density = 'compact' | 'comfortable';

const ROW_HEIGHT: Record<Density, number> = { compact: 32, comfortable: 40 };

interface DataTableProps<T> {
  data: T[];
  columns: AnyColumnDef<T>[];
  /** Accessible name of the table. */
  caption: string;
  getRowId?: (row: T, index: number) => string;
  /** Opens the detail drawer for a row. */
  onRowClick?: (row: T) => void;
  /** Enables the CSV export button; the date is appended to the name. */
  exportFileName?: string;
  /** Height of the scroll area in px (rows are virtualised inside it). */
  height?: number;
  initialDensity?: Density;
  /** Extra controls on the left of the toolbar (filters). */
  toolbar?: ReactNode;
  emptyMessage?: ReactNode;
  className?: string;
  /**
   * Sorting done by the server (`data` is one page of a sorted list): the headers show
   * `sorting` and report clicks through `onSortingChange`; the rows keep the order given.
   */
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;
  /** Replaces the "N satır" counter, e.g. "1–50 / 312" of a paged list. */
  countLabel?: ReactNode;
  /** Controls on the right of the toolbar, before the view menus (e.g. a server export). */
  actions?: ReactNode;
}

/**
 * Virtualised table for large result sets (100k rows stay smooth): sticky header, sorting,
 * column chooser, density toggle and CSV export of the visible columns in the current order.
 */
export function DataTable<T>({
  data,
  columns,
  caption,
  getRowId,
  onRowClick,
  exportFileName,
  height = 480,
  initialDensity = 'compact',
  toolbar,
  emptyMessage,
  className,
  sorting: serverSorting,
  onSortingChange,
  countLabel,
  actions,
}: DataTableProps<T>) {
  const { t } = useTranslation();
  const [localSorting, setLocalSorting] = useState<SortingState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});
  const [density, setDensity] = useState<Density>(initialDensity);
  const scrollRef = useRef<HTMLDivElement>(null);
  const manualSorting = serverSorting !== undefined;
  const sorting = serverSorting ?? localSorting;

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility },
    onSortingChange: (updater) => {
      const next = typeof updater === 'function' ? updater(sorting) : updater;
      if (manualSorting) onSortingChange?.(next);
      else setLocalSorting(next);
    },
    manualSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    ...(getRowId ? { getRowId } : {}),
  });

  const rows = table.getRowModel().rows;
  const rowHeight = ROW_HEIGHT[density];
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
    initialRect: { width: 1024, height },
  });

  useEffect(() => {
    virtualizer.measure();
  }, [density, virtualizer]);

  const virtualRows = virtualizer.getVirtualItems();
  const paddingTop = virtualRows[0]?.start ?? 0;
  const paddingBottom = virtualizer.getTotalSize() - (virtualRows.at(-1)?.end ?? 0);
  const visibleColumns = table.getVisibleLeafColumns();
  // With a growing column the table is as wide as its container but never narrower than the
  // fixed columns plus the growing one's minimum (then it scrolls sideways).
  const growing = visibleColumns.some((column) => column.columnDef.meta?.grow);
  const minWidth = visibleColumns.reduce(
    (sum, column) =>
      sum +
      (column.columnDef.meta?.grow
        ? (column.columnDef.minSize ?? column.getSize())
        : column.getSize()),
    0,
  );

  function exportCsv() {
    if (!exportFileName) return;
    const csv = toCsv(
      rows,
      visibleColumns.map((column) => ({
        header: column.columnDef.meta?.label ?? column.id,
        value: (row: (typeof rows)[number]) => {
          const exporter = column.columnDef.meta?.exportValue;
          return exporter ? exporter(row.original) : row.getValue<CsvValue>(column.id);
        },
      })),
    );
    const stamp = new Date().toISOString().slice(0, 10);
    downloadText(`${exportFileName}-${stamp}.csv`, csv);
  }

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="flex flex-wrap items-center gap-2">
        {toolbar}
        <span className="text-13 text-muted tabular" aria-live="polite">
          {countLabel ??
            t('common.rows', {
              count: rows.length,
              formattedCount: formatNumber(rows.length, 0, currentLang()),
            })}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {actions}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label={t('common.density')}>
                <Rows3 aria-hidden="true" />
                <span className="hidden sm:inline">{t('common.density')}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuRadioGroup
                value={density}
                onValueChange={(next) => {
                  setDensity(next as Density);
                }}
              >
                <DropdownMenuRadioItem value="compact">
                  {t('common.densityCompact')}
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="comfortable">
                  {t('common.densityComfortable')}
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label={t('common.columns')}>
                <Columns3 aria-hidden="true" />
                <span className="hidden sm:inline">{t('common.columns')}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>{t('common.columns')}</DropdownMenuLabel>
              {table
                .getAllLeafColumns()
                .filter((column) => column.getCanHide())
                .map((column) => (
                  <DropdownMenuCheckboxItem
                    key={column.id}
                    checked={column.getIsVisible()}
                    onCheckedChange={(checked) => {
                      column.toggleVisibility(checked);
                    }}
                    onSelect={(event) => {
                      event.preventDefault();
                    }}
                  >
                    {column.columnDef.meta?.label ?? column.id}
                  </DropdownMenuCheckboxItem>
                ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {exportFileName ? (
            <Button variant="secondary" size="sm" onClick={exportCsv} disabled={rows.length === 0}>
              <Download aria-hidden="true" />
              {t('common.exportCsv')}
            </Button>
          ) : null}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="relative overflow-auto rounded-lg border border-border bg-surface"
        style={{ height }}
        tabIndex={-1}
      >
        <table
          className="w-full table-fixed border-separate border-spacing-0 text-13"
          style={growing ? { width: `max(100%, ${minWidth}px)` } : undefined}
        >
          <caption className="sr-only">{caption}</caption>
          <colgroup>
            {visibleColumns.map((column) => (
              <col
                key={column.id}
                style={column.columnDef.meta?.grow ? undefined : { width: column.getSize() }}
              />
            ))}
          </colgroup>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  const meta = header.column.columnDef.meta;
                  const SortIcon =
                    sorted === 'asc' ? ArrowUp : sorted === 'desc' ? ArrowDown : ArrowUpDown;
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={
                        sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'
                      }
                      className={cn(
                        'sticky top-0 z-10 h-9 border-b border-border bg-subtle px-3 text-left text-xs font-medium text-muted-strong',
                        meta?.numeric && 'text-right',
                      )}
                    >
                      {header.isPlaceholder ? null : header.column.getCanSort() ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className={cn(
                            'inline-flex max-w-full items-center gap-1 rounded-sm hover:text-foreground',
                            meta?.numeric && 'flex-row-reverse',
                          )}
                        >
                          <span className="truncate">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                          </span>
                          <SortIcon
                            aria-hidden="true"
                            className={cn('size-3.5 shrink-0', !sorted && 'opacity-40')}
                          />
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={visibleColumns.length} className="px-3 py-10 text-center text-muted">
                  {emptyMessage ?? t('common.noResults')}
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 ? (
                  <tr aria-hidden="true">
                    <td
                      colSpan={visibleColumns.length}
                      style={{ height: paddingTop, padding: 0 }}
                    />
                  </tr>
                ) : null}
                {virtualRows.map((virtualRow) => {
                  const row = rows[virtualRow.index];
                  if (!row) return null;
                  return (
                    <tr
                      key={row.id}
                      style={{ height: rowHeight }}
                      tabIndex={onRowClick ? 0 : undefined}
                      onClick={
                        onRowClick
                          ? () => {
                              onRowClick(row.original);
                            }
                          : undefined
                      }
                      onKeyDown={
                        onRowClick
                          ? (event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                onRowClick(row.original);
                              }
                            }
                          : undefined
                      }
                      className={cn(
                        'group',
                        onRowClick && 'cursor-pointer hover:bg-subtle focus-visible:bg-subtle',
                      )}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const meta = cell.column.columnDef.meta;
                        return (
                          <td
                            key={cell.id}
                            className={cn(
                              'truncate border-b border-border px-3 text-foreground',
                              meta?.numeric && 'text-right tabular',
                              meta?.mono && 'font-mono text-xs',
                            )}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
                {paddingBottom > 0 ? (
                  <tr aria-hidden="true">
                    <td
                      colSpan={visibleColumns.length}
                      style={{ height: paddingBottom, padding: 0 }}
                    />
                  </tr>
                ) : null}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
