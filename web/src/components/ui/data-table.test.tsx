import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import * as csv from '@/lib/csv';

import { DataTable, type AnyColumnDef } from './data-table';

interface Row {
  id: string;
  ip: string;
  bytes: number;
}

const DATA: Row[] = [
  { id: 'a', ip: '172.20.10.37', bytes: 300 },
  { id: 'b', ip: '172.20.60.142', bytes: 100 },
  { id: 'c', ip: '172.20.42.165', bytes: 200 },
];

const COLUMNS: AnyColumnDef<Row>[] = [
  { id: 'ip', accessorFn: (row) => row.ip, header: 'IP', meta: { label: 'IP', mono: true } },
  {
    id: 'bytes',
    accessorFn: (row) => row.bytes,
    header: 'Bayt',
    meta: { label: 'Bayt', numeric: true },
  },
];

function bodyIps(): string[] {
  const table = screen.getByRole('table', { name: 'Test tablosu' });
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('td')?.textContent ?? '');
}

describe('DataTable', () => {
  // jsdom has no layout; give the scroll container a size so the virtualiser renders rows.
  const sizes = { offsetHeight: 480, offsetWidth: 1024 };
  const originals = Object.fromEntries(
    Object.keys(sizes).map((key) => [
      key,
      Object.getOwnPropertyDescriptor(HTMLElement.prototype, key),
    ]),
  );
  beforeAll(() => {
    for (const [key, value] of Object.entries(sizes)) {
      Object.defineProperty(HTMLElement.prototype, key, { configurable: true, get: () => value });
    }
  });
  afterAll(() => {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor);
    }
  });

  it('a growing column takes the rest of the width, the others keep theirs', () => {
    const columns: AnyColumnDef<Row>[] = [
      { ...COLUMNS[0], size: 300, minSize: 120, meta: { label: 'IP', grow: true } },
      { ...COLUMNS[1], size: 90 },
    ] as AnyColumnDef<Row>[];
    render(<DataTable data={DATA} columns={columns} caption="Test tablosu" />);
    const table = screen.getByRole('table', { name: 'Test tablosu' });
    const cols = table.querySelectorAll('col');
    expect((cols[0] as HTMLElement).style.width).toBe('');
    expect((cols[1] as HTMLElement).style.width).toBe('90px');
    expect(table.style.width).toBe('max(100%, 210px)');
  });

  it("without a growing column the widths are the columns' sizes", () => {
    render(<DataTable data={DATA} columns={COLUMNS} caption="Test tablosu" />);
    const table = screen.getByRole('table', { name: 'Test tablosu' });
    expect(table.style.width).toBe('');
    expect((table.querySelector('col') as HTMLElement).style.width).not.toBe('');
  });

  it('renders rows and the row count', () => {
    render(<DataTable data={DATA} columns={COLUMNS} caption="Test tablosu" />);
    expect(bodyIps()).toEqual(['172.20.10.37', '172.20.60.142', '172.20.42.165']);
    expect(screen.getByText('3 satır')).toBeInTheDocument();
  });

  it('sorts when a header is clicked and exposes aria-sort', async () => {
    render(<DataTable data={DATA} columns={COLUMNS} caption="Test tablosu" />);
    const header = screen.getByRole('columnheader', { name: /Bayt/ });
    expect(header).toHaveAttribute('aria-sort', 'none');
    // Numeric columns sort largest-first on the first click (TanStack default).
    await userEvent.click(within(header).getByRole('button'));
    expect(header).toHaveAttribute('aria-sort', 'descending');
    expect(bodyIps()).toEqual(['172.20.10.37', '172.20.42.165', '172.20.60.142']);
    await userEvent.click(within(header).getByRole('button'));
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    expect(bodyIps()).toEqual(['172.20.60.142', '172.20.42.165', '172.20.10.37']);
  });

  it('opens a row with click and with the keyboard', () => {
    const onRowClick = vi.fn();
    render(
      <DataTable data={DATA} columns={COLUMNS} caption="Test tablosu" onRowClick={onRowClick} />,
    );
    const firstRow = screen.getByText('172.20.10.37').closest('tr');
    if (!firstRow) throw new Error('row missing');
    fireEvent.click(firstRow);
    fireEvent.keyDown(firstRow, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);
  });

  it('exports the visible columns as Excel-friendly CSV', async () => {
    const download = vi.spyOn(csv, 'downloadText').mockImplementation(() => undefined);
    render(
      <DataTable data={DATA} columns={COLUMNS} caption="Test tablosu" exportFileName="trafik" />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'CSV dışa aktar' }));
    expect(download).toHaveBeenCalledOnce();
    const [fileName, text] = download.mock.calls[0] ?? [];
    expect(fileName).toMatch(/^trafik-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(text).toBe('﻿IP;Bayt\r\n172.20.10.37;300\r\n172.20.60.142;100\r\n172.20.42.165;200\r\n');
  });

  it('shows the empty message when there is no data', () => {
    render(<DataTable data={[]} columns={COLUMNS} caption="Test tablosu" />);
    expect(screen.getByText(/Bu aralıkta kayıt bulunamadı/)).toBeInTheDocument();
  });
});
