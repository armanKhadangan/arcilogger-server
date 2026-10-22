import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { LookupOut } from '@/lib/api-types';
import { outcomeOf, type PersonInfo } from '@/lib/lookup-types';
import { LOOKUPS } from '@/test/samples';

import { LookupResults } from './lookup-results';

/** Every explanation the API gave (outcome and attribution chain). */
function explanations(lookup: LookupOut): { key: string; text_tr: string }[] {
  const outcome = outcomeOf(lookup);
  return [...(outcome?.explanations ?? []), ...(outcome?.attribution?.explanations ?? [])];
}

function show(lookup: LookupOut) {
  const onOpen = vi.fn();
  render(<LookupResults lookup={lookup} onOpen={onOpen} />);
  return onOpen;
}

describe('LookupResults with answers captured from the API', () => {
  it.each([
    'exact',
    'candidates',
    'conflict',
    'internal',
    'none',
    'vault_only',
    'truncated',
  ] as const)('%s: every explanation of the API is shown as sent', (name) => {
    const lookup = LOOKUPS[name];
    show(lookup);
    for (const item of explanations(lookup)) {
      expect(screen.getAllByText(item.text_tr).length, item.key).toBeGreaterThan(0);
    }
  });

  it('exact: one match with the whole chain; an evidence row opens its raw line', async () => {
    const onOpen = show(LOOKUPS.exact);
    const matches = screen.getAllByTestId('match');
    expect(matches).toHaveLength(1);
    const match = matches[0] as HTMLElement;
    expect(within(match).getByText('Kesin', { exact: true })).toBeInTheDocument();
    expect(screen.getByText('Kesin eşleşme')).toBeInTheDocument();
    const summary = within(match).getByTestId('chain-summary');
    for (const part of [
      '100.64.12.9',
      '172.20.42.57',
      '00:00:5e:00:53:57',
      'PLN-PC14',
      'Selin Kaya',
    ])
      expect(summary).toHaveTextContent(part);
    expect(screen.queryByTestId('ip-conflict')).not.toBeInTheDocument();

    const first = within(match).getAllByRole('button', { name: /satır \d+/ })[0] as HTMLElement;
    await userEvent.click(first);
    expect(onOpen).toHaveBeenCalledTimes(1);
    const target = onOpen.mock.calls[0]?.[0] as { ref: { source: string; day: string } };
    expect(target.ref).toMatchObject({ day: '2026-10-09' });
  });

  it('double NAT: candidates, never presented as exact', () => {
    show(LOOKUPS.candidates);
    const matches = screen.getAllByTestId('match');
    expect(matches).toHaveLength(3);
    for (const match of matches) {
      expect(within(match).getByText('Aday')).toBeInTheDocument();
      expect(within(match).queryByText('Kesin', { exact: true })).not.toBeInTheDocument();
    }
    expect(screen.queryByText('Kesin eşleşme')).not.toBeInTheDocument();
    const doubleNat = explanations(LOOKUPS.candidates).find((item) => item.key === 'double_nat');
    expect(doubleNat?.text_tr).toBe(
      'Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe göre adaylar listelenmiştir.',
    );
  });

  it('a lease that is still running reads "– hâlâ açık", without a doubled reason', () => {
    show(LOOKUPS.candidates);
    const open = screen.getAllByText(/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2} – hâlâ açık$/);
    expect(open.length).toBeGreaterThan(0);
    expect(screen.queryByText(/açık \(hâlâ açık\)/)).not.toBeInTheDocument();
  });

  it('IP conflict: every possible device is listed, none chosen silently', () => {
    show(LOOKUPS.conflict);
    const conflict = screen.getByTestId('ip-conflict');
    expect(within(conflict).getByText('IP çakışması: 2 olası cihaz')).toBeInTheDocument();
    const first = within(conflict).getByRole('region', { name: 'Olasılık 1' });
    const second = within(conflict).getByRole('region', { name: 'Olasılık 2' });
    expect(first).toHaveTextContent('00:00:5e:00:53:57');
    expect(second).toHaveTextContent('00:0c:29:00:00:99');
    expect(second).toHaveTextContent('bağlı kişi yok');
  });

  it('no result: an empty state, no match cards', () => {
    show(LOOKUPS.none);
    expect(screen.getByText('Eşleşme bulunamadı')).toBeInTheDocument();
    expect(screen.queryAllByTestId('match')).toHaveLength(0);
  });

  it('an internal address: the chain of that address without traffic', () => {
    show(LOOKUPS.internal);
    const attribution = screen.getByTestId('attribution');
    expect(attribution).toHaveTextContent('172.20.60.142');
    expect(attribution).toHaveTextContent('02:00:5e:00:53:64');
  });
});

describe('the person step with the assignment history (devices and persons API)', () => {
  /** The exact sample with its person's assignment replaced by `fields`. */
  function withAssignment(fields: Partial<PersonInfo>): LookupOut {
    const lookup = structuredClone(LOOKUPS.exact);
    const person = outcomeOf(lookup)?.matches[0]?.chain?.person;
    if (!person) throw new Error('the exact sample has no person');
    Object.assign(person, fields);
    return lookup;
  }

  it('shows since when the person had the device at the requested moment', () => {
    show(
      withAssignment({ assigned_from: '2026-03-12T06:00:00Z', assigned_to: null, link: 'history' }),
    );
    expect(screen.getByTestId('person-assignment')).toHaveTextContent(
      'Atama: 12.03.2026 09:00:00 itibarıyla',
    );
  });

  it('a closed assignment shows both ends', () => {
    show(
      withAssignment({
        assigned_from: '2026-03-02T05:12:44Z',
        assigned_to: '2026-03-12T06:00:00Z',
        link: 'history',
      }),
    );
    expect(screen.getByTestId('person-assignment')).toHaveTextContent(
      'Atama: 02.03.2026 08:12:44 – 12.03.2026 09:00:00',
    );
  });

  it('the captured lookup: Selin Kaya has had the PC since 01.10.2026 (history link)', () => {
    show(LOOKUPS.exact);
    expect(screen.getByTestId('person-assignment')).toHaveTextContent(
      'Atama: 01.10.2026 09:00:00 itibarıyla',
    );
  });

  it('a lookup stored before the history (no fields) shows no assignment line', () => {
    const lookup = structuredClone(LOOKUPS.exact);
    const person = outcomeOf(lookup)?.matches[0]?.chain?.person;
    if (!person) throw new Error('the exact sample has no person');
    delete person.assigned_from;
    delete person.assigned_to;
    delete person.link;
    show(lookup);
    expect(screen.queryByTestId('person-assignment')).not.toBeInTheDocument();
    // The title stays the detail of the person step.
    expect(screen.getAllByText('Planlama Uzmanı').length).toBeGreaterThan(0);
  });
});
