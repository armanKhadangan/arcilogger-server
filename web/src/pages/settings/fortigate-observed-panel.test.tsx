import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { FortigateObserved } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingOut, SettingsOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { HAT_1, observedExitsInvalid, observedSample } from '@/test/wave3-mocks';

/*
 * "Kayıtlarda görülen NAT adresleri" under the exit editor (contract 2.13): the observed
 * addresses with their exits, "Çıkış olarak ekle" by the fixed prefill rule, the NAT types
 * outside `nat.snat_trandisp` and "srcmac için ekle". Hand mocks of `GET /settings/fortigate/
 * observed` (`src/test/wave3-mocks.ts`) until the endpoint's sample is captured.
 */

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

function setting(key: string, type: SettingOut['type'], value: SettingOut['value']): SettingOut {
  return {
    key,
    value,
    default: value,
    is_default: true,
    type,
    min: type === 'integer' ? 1 : null,
    max: type === 'integer' ? 16 : null,
    reauth: false,
    locked: false,
    label_tr: `Etiket ${key}`,
    label_en: `Label ${key}`,
    help_tr: `Yardım ${key}`,
    help_en: `Help ${key}`,
    updated_by: ':seed',
    updated_at: '2026-09-30T09:12:00+03:00',
  };
}

const GROUPS: SettingGroup[] = [
  {
    id: 'lookup',
    label_tr: '5651 sorgu ve kira zaman çizelgesi',
    label_en: '5651 lookup and lease timeline',
    settings: [
      setting('nat.egresses', 'object', HAT_1),
      setting('lookup.srcmac_interfaces', 'string_list', []),
      setting('lookup.srcmac_max_ips', 'integer', 1),
    ],
  },
];

function serve(observed: () => Response) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings')
      return jsonResponse(200, { groups: GROUPS } satisfies SettingsOut);
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

async function panel(answer: FortigateObserved = observedSample()) {
  const calls = serve(() => jsonResponse(200, answer));
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
    const claimed = observedRow(found, '203.0.113.200');
    expect(claimed).toHaveTextContent('hat-1');
    expect(within(claimed).queryByRole('button')).not.toBeInTheDocument();
    expect(observedRow(found, '100.64.13.9')).toHaveTextContent('Tanımsız');
  });

  it('a private or shared address prefills ISP NAT, or offers "not an internet exit"', async () => {
    const { found } = await panel();
    const row = observedRow(found, '100.64.13.9');
    expect(
      within(row).getByRole('button', { name: 'İnternet çıkışı değil olarak ekle: 100.64.13.9' }),
    ).toBeVisible();
    expect(row).toHaveTextContent(
      "FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da şubeye yaptığı NAT'ın adresiyse bunu seçin; bu adreslerde oturum aranmaz.",
    );
    const user = userEvent.setup();
    await user.click(within(row).getByRole('button', { name: 'Çıkış olarak ekle: 100.64.13.9' }));
    const added = screen.getByRole('group', { name: 'Çıkış hat-2' });
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
    expect(observedRow(found, '100.64.13.9')).toHaveTextContent('Taslakta: hat-2 (kaydedilmedi)');
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

  it('a public address on a line of the draft is offered to that line first; an unknown NAT type is flagged', async () => {
    const { found } = await panel();
    const row = observedRow(found, '198.51.100.20');
    const buttons = within(row)
      .getAllByRole('button')
      .map((button) => button.textContent);
    expect(buttons).toEqual(['hat-1 çıkışının genel adreslerine ekle', 'Çıkış olarak ekle']);
    expect(within(row).getByText('Ayarlarda yok')).toBeVisible();
    expect(within(found).getByTestId('observed-unknown-trandisp')).toHaveTextContent('snat-pool');
    const user = userEvent.setup();
    await user.click(
      within(row).getByRole('button', { name: /^hat-1 çıkışının genel adreslerine ekle/ }),
    );
    expect(
      within(screen.getByRole('group', { name: 'Çıkış hat-1' })).getByLabelText('Genel adresler'),
    ).toHaveValue('203.0.113.200\n198.51.100.20');
  });

  it('"srcmac için ekle" adds the interface to the draft; not for an interface behind a router', async () => {
    const { found } = await panel();
    const table = within(found).getByRole('table', {
      name: 'Oturum kayıtlarında srcmac taşıyan FortiGate arayüzleri',
    });
    const shared = within(table).getByRole('button', { name: 'srcmac için ekle: vlan60' });
    expect(shared).toBeDisabled();
    expect(shared).toHaveAccessibleDescription(
      'Bir MAC adresi 23 farklı IP adresiyle görüldü (sınır 1): arayüzün arkasında bir yönlendirici ya da anahtar olabilir.',
    );
    const user = userEvent.setup();
    await user.click(within(table).getByRole('button', { name: 'srcmac için ekle: vlan42' }));
    expect(
      within(screen.getByTestId('setting-lookup.srcmac_interfaces')).getByRole('textbox'),
    ).toHaveValue('vlan42');
    expect(within(table).getByText('Taslakta (kaydedilmedi)')).toBeVisible();
  });

  it('says so when the stored exits cannot be read', async () => {
    const { found } = await panel(observedExitsInvalid());
    expect(within(found).getByTestId('observed-exits-invalid')).toHaveTextContent(
      'Kayıtlı çıkış tanımı okunamıyor; çıkış sütunu boş.',
    );
  });

  it('shows the time-out of the summary with the API text', async () => {
    serve(() =>
      errorResponse(504, 'fortigate_observed_timeout', {
        tr: 'FortiGate kayıtları 5 saniyede özetlenemedi; biraz sonra yeniden deneyin.',
      }),
    );
    renderApp('/settings/application?group=lookup');
    const found = await screen.findByTestId('fortigate-observed');
    expect(
      await within(found).findByText(
        'FortiGate kayıtları 5 saniyede özetlenemedi; biraz sonra yeniden deneyin.',
      ),
    ).toBeVisible();
  });
});
