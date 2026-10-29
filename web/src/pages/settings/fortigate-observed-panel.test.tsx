import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { FortigateObserved } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingsOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import type { ErrorSample } from '@/test/samples';

/*
 * "Kayıtlarda görülen NAT adresleri" under the exit editor (contract 2.13): the observed
 * addresses with their exits, "Çıkış olarak ekle" by the fixed prefill rule, the NAT types
 * outside `nat.snat_trandisp` and "srcmac için ekle". `GET /settings/fortigate/observed` and
 * `GET /settings` are captured (`settings-observed.json`, `settings.json`): the stored exits are
 * the converted `wan` (100.64.12.9, no interfaces), which claims the first observed row.
 */

/** Captured answers `src/test/samples.ts` does not load, read the same way. */
function load(name: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), 'e2e', 'samples', `${name}.json`), 'utf8'));
}

const OBSERVED = load('settings-observed') as {
  observed: FortigateObserved;
  observed_exits_invalid: FortigateObserved;
  errors: Record<string, ErrorSample>;
};
const SETTINGS = load('settings') as { list: SettingsOut };

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

/** The captured group `lookup`, with `values` in place of stored values. */
function lookupGroup(values: Record<string, unknown> = {}): SettingGroup[] {
  const group = structuredClone(SETTINGS.list.groups.find((item) => item.id === 'lookup'));
  if (!group) throw new Error('sample missing: settings group lookup');
  for (const setting of group.settings)
    if (setting.key in values) setting.value = values[setting.key] as typeof setting.value;
  return [group];
}

function serve(observed: () => Response, groups: SettingGroup[] = lookupGroup()) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings')
      return jsonResponse(200, { groups } satisfies SettingsOut);
    if (request.path === '/api/v1/settings/fortigate/observed') return observed();
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

async function panel(answer: FortigateObserved = OBSERVED.observed, groups?: SettingGroup[]) {
  const calls = serve(() => jsonResponse(200, structuredClone(answer)), groups);
  renderApp('/settings/application?group=lookup');
  const found = await screen.findByTestId('fortigate-observed');
  await within(found).findByRole('table', {
    name: 'FortiGate oturum kayıtlarında görülen kaynak NAT adresleri',
  });
  return { found, calls };
}

function observedRow(panelBox: HTMLElement, transip: string): HTMLElement {
  const table = within(panelBox).getByRole('table', {
    name: 'FortiGate oturum kayıtlarında görülen kaynak NAT adresleri',
  });
  const header = within(table).getByRole('rowheader', { name: transip });
  return header.closest('tr') as HTMLElement;
}

describe('the observed NAT addresses', () => {
  it('lists every address with its exit; reads the last 24 hours', async () => {
    const { found, calls } = await panel();
    expect(
      within(found).getByRole('heading', {
        name: 'Kayıtlarda görülen NAT adresleri (son 24 saat)',
      }),
    ).toBeVisible();
    const request = calls.find((call) => call.path === '/api/v1/settings/fortigate/observed');
    expect(request?.params.get('hours')).toBe('24');
    const claimed = observedRow(found, '100.64.12.9');
    expect(claimed).toHaveTextContent('wan');
    expect(within(claimed).queryByRole('button')).not.toBeInTheDocument();
    for (const address of OBSERVED.observed.nat.unmapped_transips)
      expect(observedRow(found, address)).toHaveTextContent('Tanımsız');
  });

  it('a private or shared address prefills ISP NAT, or offers "not an internet exit"', async () => {
    const { found } = await panel();
    const row = observedRow(found, '100.64.13.9');
    // No exit of the draft lists wan2: the two new-exit offers only.
    expect(
      within(row)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Çıkış olarak ekle', 'İnternet çıkışı değil olarak ekle']);
    expect(
      within(row).getByRole('button', { name: 'İnternet çıkışı değil olarak ekle: 100.64.13.9' }),
    ).toBeVisible();
    expect(row).toHaveTextContent(
      "FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da şubeye yaptığı NAT'ın adresiyse bunu seçin; bu adreslerde oturum aranmaz.",
    );
    const user = userEvent.setup();
    await user.click(within(row).getByRole('button', { name: 'Çıkış olarak ekle: 100.64.13.9' }));
    const added = screen.getByRole('group', { name: 'Çıkış hat-1' });
    expect(within(added).getByRole('combobox', { name: 'NAT türü' })).toHaveTextContent(
      "Servis sağlayıcı NAT'ı (çift NAT)",
    );
    expect(within(added).getByLabelText('NAT adresleri (transip)')).toHaveValue('100.64.13.9');
    expect(within(added).getByLabelText('Arayüzler (dstintf)')).toHaveValue('wan2');
    expect(within(added).getByLabelText('Genel adresler')).toHaveValue('');
    expect(within(added).getByText(/^Genel adres girilmedi: /)).toBeVisible();
    await waitFor(() => {
      expect(within(added).getByLabelText('Ad')).toHaveFocus();
    });
    // The address is in the draft now: no second offer, until it is saved.
    expect(observedRow(found, '100.64.13.9')).toHaveTextContent('Taslakta: hat-1 (kaydedilmedi)');
    expect(within(observedRow(found, '100.64.13.9')).queryByRole('button')).not.toBeInTheDocument();
  });

  it('the DMZ address taken as not an internet exit', async () => {
    const { found } = await panel();
    const user = userEvent.setup();
    await user.click(
      within(observedRow(found, '172.20.99.1')).getByRole('button', {
        name: 'İnternet çıkışı değil olarak ekle: 172.20.99.1',
      }),
    );
    const added = screen.getByRole('group', { name: 'Çıkış nat-1' });
    expect(within(added).getByRole('combobox', { name: 'NAT türü' })).toHaveTextContent(
      'İnternet çıkışı değil (DMZ, VLAN, VPN ya da şubeye NAT)',
    );
    expect(within(added).queryByLabelText('Genel adresler')).not.toBeInTheDocument();
    expect(within(added).getByLabelText('NAT adresleri (transip)')).toHaveValue('172.20.99.1');
    expect(within(added).getByLabelText('Arayüzler (dstintf)')).toHaveValue('dmz');
  });

  it('a public address prefills Direct; an unknown NAT type is flagged', async () => {
    const { found } = await panel();
    const row = observedRow(found, '198.51.100.20');
    expect(
      within(row)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Çıkış olarak ekle']);
    expect(within(row).getByText('Ayarlarda yok')).toBeVisible();
    expect(within(found).getByTestId('observed-unknown-trandisp')).toHaveTextContent('snat-pool');
    const user = userEvent.setup();
    await user.click(within(row).getByRole('button', { name: 'Çıkış olarak ekle: 198.51.100.20' }));
    const added = screen.getByRole('group', { name: 'Çıkış hat-1' });
    expect(within(added).getByRole('combobox', { name: 'NAT türü' })).toHaveTextContent(
      "Doğrudan (genel IP FortiGate'te)",
    );
    expect(within(added).getByLabelText('Genel adresler')).toHaveValue('198.51.100.20');
    expect(within(added).getByLabelText('Arayüzler (dstintf)')).toHaveValue('wan1');
  });

  it('an address on a line of the draft is offered to that line first', async () => {
    const { found } = await panel();
    const user = userEvent.setup();
    // The converted wan exit given the interface the second line's NAT address is seen on.
    await user.type(
      within(screen.getByRole('group', { name: 'Çıkış wan' })).getByLabelText(
        'Arayüzler (dstintf)',
      ),
      'wan2',
    );
    const row = observedRow(found, '100.64.13.9');
    expect(
      within(row)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual([
      'wan çıkışının NAT adreslerine ekle',
      'Çıkış olarak ekle',
      'İnternet çıkışı değil olarak ekle',
    ]);
    await user.click(
      within(row).getByRole('button', { name: /^wan çıkışının NAT adreslerine ekle/ }),
    );
    expect(
      within(screen.getByRole('group', { name: 'Çıkış wan' })).getByLabelText(
        'NAT adresleri (transip)',
      ),
    ).toHaveValue('100.64.12.9\n100.64.13.9');
    expect(observedRow(found, '100.64.13.9')).toHaveTextContent('Taslakta: wan (kaydedilmedi)');
  });

  it('srcmac: a used interface, and none behind a router (limit 1)', async () => {
    const { found } = await panel();
    const table = within(found).getByRole('table', {
      name: 'Oturum kayıtlarında srcmac taşıyan FortiGate arayüzleri',
    });
    const used = within(table).getByRole('rowheader', { name: 'vlan42' }).closest('tr');
    expect(used).toHaveTextContent('srcmac kullanılıyor');
    expect(within(used as HTMLElement).queryByRole('button')).not.toBeInTheDocument();
    const shared = within(table).getByRole('button', { name: 'srcmac için ekle: vlan60' });
    expect(shared).toBeDisabled();
    expect(shared).toHaveAccessibleDescription(
      'Bir MAC adresi 3 farklı IP adresiyle görüldü (sınır 1): arayüzün arkasında bir yönlendirici ya da anahtar olabilir.',
    );
  });

  it('"srcmac için ekle" adds the interface to the draft within the limit', async () => {
    // The captured observed answer under a limit of 3 IPv4 addresses per MAC.
    const { found } = await panel(OBSERVED.observed, lookupGroup({ 'lookup.srcmac_max_ips': 3 }));
    const table = within(found).getByRole('table', {
      name: 'Oturum kayıtlarında srcmac taşıyan FortiGate arayüzleri',
    });
    const user = userEvent.setup();
    await user.click(within(table).getByRole('button', { name: 'srcmac için ekle: vlan60' }));
    expect(
      within(screen.getByTestId('setting-lookup.srcmac_interfaces')).getByRole('textbox'),
    ).toHaveValue('vlan60');
    expect(within(table).getByText('Taslakta (kaydedilmedi)')).toBeVisible();
  });

  it('says so when the stored exits cannot be read', async () => {
    // The server answers so for a stored value its rules refuse (here an unknown mode).
    const { found } = await panel(
      OBSERVED.observed_exits_invalid,
      lookupGroup({ 'nat.egresses': { wan: { mode: 'nat', transips: ['100.64.12.9'] } } }),
    );
    expect(within(found).getByTestId('observed-exits-invalid')).toHaveTextContent(
      'Kayıtlı çıkış tanımı okunamıyor; çıkış sütunu boş.',
    );
    // A row kept as stored claims nothing: every address is unclaimed.
    for (const row of OBSERVED.observed_exits_invalid.nat.rows)
      expect(observedRow(found, row.transip)).toHaveTextContent('Tanımsız');
  });

  it('shows the time-out of the summary with the API text', async () => {
    const timeout = OBSERVED.errors.fortigate_observed_timeout;
    if (!timeout) throw new Error('sample missing: fortigate_observed_timeout');
    serve(() => jsonResponse(timeout.status, timeout.body));
    renderApp('/settings/application?group=lookup');
    const found = await screen.findByTestId('fortigate-observed');
    expect(
      await within(found).findByText(
        'FortiGate kayıtları 5 saniyede özetlenemedi; biraz sonra yeniden deneyin.',
      ),
    ).toBeVisible();
  });
});
