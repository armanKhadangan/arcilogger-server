import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Me } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { DEVICES } from '@/test/samples';

// jsdom has no layout; give the tables' scroll containers a size so the virtualiser renders rows.
const sizes = { offsetHeight: 480, offsetWidth: 1024 };
const originals = Object.fromEntries(
  Object.keys(sizes).map((key) => [
    key,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, key),
  ]),
);
beforeAll(() => {
  for (const [key, value] of Object.entries(sizes))
    Object.defineProperty(HTMLElement.prototype, key, { configurable: true, get: () => value });
});
afterAll(() => {
  for (const [key, descriptor] of Object.entries(originals))
    if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

/** The devices API as the samples describe it (no filtering: the requests are checked). */
function serve(me: Me, vlans = DEVICES.vlans): RecordedRequest[] {
  setCsrfCookie('csrf-1');
  return installFetch((request) => {
    const mac = decodeURIComponent(/^\/api\/v1\/devices\/([^/]+)$/.exec(request.path)?.[1] ?? '');
    const detail = [DEVICES.detail, DEVICES.detail_phone].find((item) => item.mac === mac);
    if (detail) return jsonResponse(200, detail);
    const device = DEVICES.list.items.find((item) => item.mac === mac);
    if (device) return jsonResponse(200, { ...device, assignments: [], recent_leases: [] });
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, me);
      case '/api/v1/devices':
        return jsonResponse(200, DEVICES.list);
      case '/api/v1/persons':
        return jsonResponse(200, DEVICES.persons);
      case '/api/v1/departments':
        return jsonResponse(200, DEVICES.departments);
      case '/api/v1/vlans':
        return jsonResponse(200, vlans);
      case '/api/v1/devices/export.csv':
        return new Response(DEVICES.devices_csv.text, { headers: DEVICES.devices_csv.headers });
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

/** An administrator without `devices.manage` (a read-only role the server may introduce). */
function readOnly(): Me {
  const admin = makeMe('admin');
  return { ...admin, permissions: admin.permissions.filter((item) => item !== 'devices.manage') };
}

describe('Cihazlar ve Kişiler — permission gating', () => {
  it('devices.manage: row selection with the bulk actions, editing on every tab', async () => {
    const calls = serve(makeMe('admin'));
    renderApp('/devices');
    const user = userEvent.setup();
    const table = await screen.findByRole('table', { name: 'Ağda görülen cihazlar' });
    expect(await within(table).findByRole('button', { name: '00:00:5e:00:53:57' })).toBeVisible();
    expect(screen.queryByTestId('read-only')).not.toBeInTheDocument();
    await user.click(within(table).getByRole('checkbox', { name: 'Seç: 3c:52:82:00:00:05' }));
    const bar = screen.getByTestId('bulk-bar');
    expect(bar).toHaveTextContent('1 cihaz seçildi');
    expect(within(bar).getByRole('button', { name: 'Kişiye ata' })).toBeEnabled();
    // The selected device has no owner: nothing to remove.
    expect(within(bar).getByRole('button', { name: 'Atamayı kaldır' })).toBeDisabled();

    await user.click(screen.getByRole('tab', { name: /Kişiler/ }));
    expect(await screen.findByRole('button', { name: 'Kişi ekle' })).toBeVisible();
    await user.click(screen.getByRole('tab', { name: /Departmanlar ve VLAN'lar/ }));
    expect(await screen.findByRole('button', { name: 'Departman ekle' })).toBeVisible();
    expect(await screen.findByRole('combobox', { name: 'Departman: VLAN 43' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'VLAN ekle' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'İşlemler: VLAN 43' })).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /İçe\/Dışa aktar/ }));
    expect(await screen.findByText('İçe aktar (CSV)')).toBeVisible();
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });

  it('devices.read only: everything is shown, nothing can be changed', async () => {
    serve(readOnly());
    renderApp('/devices');
    const user = userEvent.setup();
    const table = await screen.findByRole('table', { name: 'Ağda görülen cihazlar' });
    await within(table).findByRole('button', { name: '00:00:5e:00:53:57' });
    expect(screen.getByTestId('read-only')).toHaveTextContent('yalnızca görüntüleyebilirsiniz');
    expect(within(table).queryAllByRole('checkbox')).toEqual([]);

    // The drawer has no edit form and no assignment buttons (the captured phone has no name:
    // the drawer is titled with its MAC address).
    await user.click(within(table).getByRole('button', { name: '02:00:5e:00:53:64' }));
    const drawer = await screen.findByRole('dialog', { name: '02:00:5e:00:53:64' });
    expect(await within(drawer).findByTestId('randomized-notice')).toBeVisible();
    // Its history: Selin Kaya now, Can Öztürk before; the history is append-only.
    expect(within(drawer).getByText('Can Öztürk')).toBeVisible();
    expect(within(drawer).getByText(/yalnızca eklenerek tutulur/)).toBeVisible();
    expect(within(drawer).queryByRole('button', { name: 'Kaydet' })).not.toBeInTheDocument();
    expect(within(drawer).queryByRole('button', { name: /ata$/ })).not.toBeInTheDocument();
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('tab', { name: /Kişiler/ }));
    await screen.findByRole('table', { name: 'Kişiler' });
    expect(screen.queryByRole('button', { name: 'Kişi ekle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /İşlemler:/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /Departmanlar ve VLAN'lar/ }));
    await screen.findByRole('table', { name: "VLAN'lar" });
    expect(screen.queryByRole('button', { name: 'Departman ekle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /Departman: VLAN/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'VLAN ekle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /İşlemler: VLAN/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /İçe\/Dışa aktar/ }));
    expect(await screen.findByRole('button', { name: 'Cihaz listesini indir' })).toBeVisible();
    expect(screen.queryByText('İçe aktar (CSV)')).not.toBeInTheDocument();
  });

  it('a new installation has no VLANs: the page asks for the network', async () => {
    serve(makeMe('admin'), []);
    const view = renderApp('/devices?tab=departments');
    expect(await screen.findByText('Henüz VLAN yok: ağınızı ekleyin')).toBeVisible();
    expect(screen.getByText(/ağ adresi ve önek uzunluğu/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'VLAN ekle' })).toBeVisible();
    expect(screen.queryByRole('table', { name: "VLAN'lar" })).not.toBeInTheDocument();
    view.unmount();

    serve(readOnly(), []);
    renderApp('/devices?tab=departments');
    expect(await screen.findByText('Henüz VLAN yok: ağınızı ekleyin')).toBeVisible();
    expect(screen.getByText(/VLAN'ları yönetici ekler/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'VLAN ekle' })).not.toBeInTheDocument();
  });

  it('asks the API for person data only after the KVKK notice was accepted', async () => {
    const calls = serve(
      makeMe('admin', { privacy_notice: { version: '1', accepted: false, required: true } }),
    );
    renderApp('/devices?tab=persons');
    expect(await screen.findByRole('dialog', { name: /KVKK/ })).toBeVisible();
    expect(screen.getByText('KVKK bilgilendirmesinin onayı bekleniyor')).toBeInTheDocument();
    await waitFor(() => {
      expect(calls.some((call) => call.path === '/api/v1/auth/me')).toBe(true);
    });
    expect(
      calls.filter((call) => ['/api/v1/devices', '/api/v1/persons'].includes(call.path)),
    ).toEqual([]);
  });

  it('the tab is in the address', async () => {
    serve(makeMe('admin'));
    const { router } = renderApp('/devices?tab=departments');
    const user = userEvent.setup();
    expect(await screen.findByRole('tab', { name: /Departmanlar ve VLAN'lar/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await user.click(screen.getByRole('tab', { name: /İçe\/Dışa aktar/ }));
    expect(router.state.location.search).toBe('?tab=transfer');
    await user.click(screen.getByRole('tab', { name: /Cihazlar/ }));
    expect(router.state.location.search).toBe('');
  });

  it('the quick filter "Bilinmeyen cihazlar" goes to the API and into the address', async () => {
    const calls = serve(makeMe('admin'));
    const { router } = renderApp('/devices');
    const user = userEvent.setup();
    const chip = await screen.findByRole('button', { name: 'Bilinmeyen cihazlar' });
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    await user.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(router.state.location.search).toBe('?unknown=true');
    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.path === '/api/v1/devices' && call.params.get('unknown') === 'true',
        ),
      ).toBe(true);
    });
  });

  it('"CSV indir" exports the filtered list, every page of it', async () => {
    const calls = serve(makeMe('admin'));
    const saved: string[] = [];
    // jsdom has no object URLs: the saved file is the link's `download` name.
    Object.assign(URL, { createObjectURL: () => 'blob:csv', revokeObjectURL: () => undefined });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
    renderApp('/devices?unknown=true&type=printer');
    const user = userEvent.setup();
    const table = await screen.findByRole('table', { name: 'Ağda görülen cihazlar' });
    await within(table).findByRole('button', { name: '00:00:5e:00:53:57' });
    await user.click(screen.getByRole('button', { name: 'CSV indir' }));
    // The API's name (`Content-Disposition`); its time is the capture's clock, not written here.
    const name = /filename="([^"]+)"/.exec(
      DEVICES.devices_csv.headers['content-disposition'] ?? '',
    )?.[1];
    expect(name).toMatch(/^arcilogger-cihazlar-\d{8}-\d{6}\.csv$/);
    await waitFor(() => {
      expect(saved).toEqual([name]);
    });
    const exported = calls.filter((call) => call.path === '/api/v1/devices/export.csv');
    expect(exported.map((call) => Object.fromEntries(call.params))).toEqual([
      { unknown: 'true', type: 'printer', sort: 'last_seen', order: 'desc' },
    ]);
    click.mockRestore();
  });
});
