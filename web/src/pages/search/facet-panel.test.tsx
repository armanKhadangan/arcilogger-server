import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { FacetPanel } from './facet-panel';
import { stats } from './test-data';

/** Statistics whose /24 of VLANs 70 and 71 is the value `multiple` (contract 2.7.3). */
const MIXED = stats({
  facets: {
    ...stats().facets,
    vlan: {
      status: 'ok',
      values: [
        { value: '42', label_tr: 'VLAN 42 (Planlama)', label_en: 'VLAN 42 (Planlama)', count: 4 },
        { value: 'multiple', label_tr: 'Birden çok VLAN', label_en: 'Several VLANs', count: 2 },
      ],
      other: 0,
    },
    department: {
      status: 'ok',
      values: [
        {
          value: 'multiple',
          label_tr: 'Birden çok departman',
          label_en: 'Several departments',
          count: 2,
        },
      ],
      other: 0,
    },
  },
});

describe('FacetPanel', () => {
  it('adds a value or its exclusion to the query', async () => {
    const onAdd = vi.fn();
    render(<FacetPanel stats={MIXED} loading={false} onAdd={onAdd} />);
    const vlans = screen.getByRole('region', { name: 'VLAN' });
    await userEvent.click(within(vlans).getByRole('button', { name: /^VLAN 42 \(Planlama\)/ }));
    await userEvent.click(
      within(vlans).getByRole('button', { name: 'Hariç tut: VLAN 42 (Planlama)' }),
    );
    expect(onAdd.mock.calls).toEqual([
      ['vlan', '42', false],
      ['vlan', '42', true],
    ]);
  });

  it('shows the value "multiple" with its count, never as a filter', () => {
    render(<FacetPanel stats={MIXED} loading={false} onAdd={vi.fn()} />);
    const vlans = screen.getByRole('region', { name: 'VLAN' });
    expect(within(vlans).getByText('Birden çok VLAN').closest('li')).toHaveTextContent(
      /^Birden çok VLAN.*2$/,
    );
    expect(within(vlans).queryByRole('button', { name: /Birden çok VLAN/ })).toBeNull();
    expect(within(vlans).getAllByRole('button')).toHaveLength(2);
    const departments = screen.getByRole('region', { name: "Departman (VLAN'a göre)" });
    expect(within(departments).getByText('Birden çok departman')).toBeInTheDocument();
    expect(within(departments).queryAllByRole('button')).toHaveLength(0);
    expect(
      within(departments).getByText('Bu değerle süzülemez: bu ağda birden çok VLAN var.'),
    ).toBeInTheDocument();
  });
});
