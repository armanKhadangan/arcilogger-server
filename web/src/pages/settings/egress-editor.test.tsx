import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingOut, SettingsOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

import type { Egresses } from './egress-model';

/*
 * Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi: the editor of the internet
 * exits (`nat.egresses`, contract 2.13). Hand mock of the settings group (the day-0 settings with
 * the server's labels); the captured settings sample replaces it in WP-E's second step.
 */

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

function setting(
  key: string,
  type: SettingOut['type'],
  value: SettingOut['value'],
  extra: Partial<SettingOut> = {},
): SettingOut {
  return {
    key,
    value,
    default: type === 'object' ? {} : value,
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
    ...extra,
  };
}

/** The server's help of a setting as the captured `GET /settings` gives it. */
function capturedHelp(key: string): Pick<SettingOut, 'help_tr' | 'help_en'> {
  const sample = JSON.parse(
    readFileSync(join(process.cwd(), 'e2e', 'samples', 'settings.json'), 'utf8'),
  ) as { list: SettingsOut };
  const found = sample.list.groups
    .flatMap((group) => group.settings)
    .find((item) => item.key === key);
  if (!found) throw new Error(`${key} is not in the captured settings`);
  return { help_tr: found.help_tr, help_en: found.help_en };
}

function groups(egresses: Egresses): SettingGroup[] {
  return [
    {
      id: 'lookup',
      label_tr: '5651 sorgu ve kira zaman çizelgesi',
      label_en: '5651 lookup and lease timeline',
      settings: [
        setting('nat.egresses', 'object', egresses, {
          label_tr: 'İnternet çıkışları (NAT)',
          label_en: 'Internet exits (NAT)',
        }),
        setting(
          'dhcp.fortigate_logids',
          'object',
          { '0100026001': 'assigned', '0100026002': 'deassigned' },
          capturedHelp('dhcp.fortigate_logids'),
        ),
        setting(
          'dhcp.fortigate_dhcp_msgs',
          'object',
          { '0100026001': ['Ack'] },
          capturedHelp('dhcp.fortigate_dhcp_msgs'),
        ),
        setting('dhcp.fortigate_traffic_extends', 'boolean', false, {
          help_tr: 'Kapalı (önerilen): FortiGate kirası yalnızca DHCP kayıtlarıyla açık kalır.',
        }),
        setting('lease.server_seconds', 'object', { dhcp61: 3600 }),
      ],
    },
  ];
}

const TWO_LINES: Egresses = {
  'hat-1': {
    label: 'Ana hat',
    mode: 'direct',
    public: ['203.0.113.200'],
    public_dynamic: false,
    transips: [],
    interfaces: ['wan1'],
    port_preservation: 'unknown',
  },
  'hat-2': {
    label: 'Yedek hat',
    mode: 'isp_nat',
    public: ['198.51.100.77'],
    public_dynamic: false,
    transips: ['100.64.13.9'],
    interfaces: [],
    port_preservation: 'unknown',
  },
};

function serve(egresses: Egresses, patch?: (body: unknown) => Response) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings' && request.method === 'GET')
      return jsonResponse(200, { groups: groups(egresses) } satisfies SettingsOut);
    if (request.path === '/api/v1/settings' && request.method === 'PATCH' && patch)
      return patch(request.body);
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

async function editor() {
  renderApp('/settings/application?group=lookup');
  return screen.findByTestId('egress-editor');
}

function exit(name: string): HTMLElement {
  return screen.getByRole('group', { name: `Çıkış ${name}` });
}

describe('the exit editor', () => {
  it('shows one row per exit with the fields of its mode', async () => {
    serve(TWO_LINES);
    await editor();
    const direct = exit('hat-1');
    expect(within(direct).getByLabelText('Ad')).toHaveValue('hat-1');
    expect(within(direct).getByLabelText('Genel adresler')).toHaveValue('203.0.113.200');
    expect(within(direct).getByLabelText('Arayüzler (dstintf)')).toHaveValue('wan1');
    // Direct: no NAT addresses, no port preservation, no "may change".
    expect(within(direct).queryByLabelText('NAT adresleri (transip)')).not.toBeInTheDocument();
    expect(within(direct).queryByLabelText('Port koruma')).not.toBeInTheDocument();
    expect(
      within(direct).queryByLabelText('Genel IP değişebilir (dinamik)'),
    ).not.toBeInTheDocument();
    const nat = exit('hat-2');
    expect(within(nat).getByLabelText('NAT adresleri (transip)')).toHaveValue('100.64.13.9');
    expect(within(nat).getByRole('combobox', { name: 'Port koruma' })).toHaveTextContent(
      'Bilinmiyor',
    );
    expect(
      within(nat).getByRole('checkbox', { name: 'Genel IP değişebilir (dinamik)' }),
    ).not.toBeChecked();
  });

  it('offers the mode internal and hides the public addresses for it', async () => {
    serve(TWO_LINES);
    await editor();
    const user = userEvent.setup();
    const row = exit('hat-2');
    await user.click(within(row).getByRole('combobox', { name: 'NAT türü' }));
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual([
      "Doğrudan (genel IP FortiGate'te)",
      "Servis sağlayıcı NAT'ı (çift NAT)",
      "FortiGate'ten geçmiyor",
      'İnternet çıkışı değil (DMZ, VLAN, VPN ya da şubeye NAT)',
    ]);
    await user.click(screen.getByRole('option', { name: /İnternet çıkışı değil/ }));
    expect(within(row).queryByLabelText('Genel adresler')).not.toBeInTheDocument();
    expect(within(row).getByLabelText('NAT adresleri (transip)')).toHaveValue('100.64.13.9');
    expect(within(row).getByLabelText('Arayüzler (dstintf)')).toBeInTheDocument();
    expect(within(row).queryByLabelText('Port koruma')).not.toBeInTheDocument();
  });

  it('warns when two lines are entered while one has no interfaces', async () => {
    serve(TWO_LINES);
    const box = await editor();
    expect(within(box).getByText(/^Her hattın arayüzlerini \(dstintf\) girin/)).toBeVisible();
    const user = userEvent.setup();
    await user.type(within(exit('hat-2')).getByLabelText('Arayüzler (dstintf)'), 'wan2');
    expect(within(box).queryByText(/^Her hattın arayüzlerini/)).not.toBeInTheDocument();
  });

  it('the dynamic box only for ISP NAT, with its warning', async () => {
    serve(TWO_LINES);
    await editor();
    const user = userEvent.setup();
    const row = exit('hat-2');
    await user.click(within(row).getByRole('checkbox', { name: 'Genel IP değişebilir (dinamik)' }));
    expect(
      within(row).getByText(
        "Genel IP değişebilir: bu çıkış, tanımlı adreslerinin yanında hiçbir çıkışa tanımlı olmayan genel IP'ler için de aranır.",
      ),
    ).toBeVisible();
  });

  it('warns about a public NAT address of an ISP NAT exit and about no public address', async () => {
    serve(TWO_LINES);
    await editor();
    const user = userEvent.setup();
    const row = exit('hat-2');
    const nat = within(row).getByLabelText('NAT adresleri (transip)');
    await user.clear(nat);
    await user.type(nat, '198.51.100.10');
    expect(within(row).getByText(/^NAT adresi genel bir adres: /)).toBeVisible();
    await user.clear(within(row).getByLabelText('Genel adresler'));
    expect(within(row).getByText(/^Genel adres girilmedi: /)).toBeVisible();
  });

  it('warns about a converted exit while its converted partner is beside it', async () => {
    serve({
      'genel-1': {
        label: 'Genel IP (yükseltmede aktarıldı)',
        mode: 'direct',
        public: ['203.0.113.200'],
        public_dynamic: false,
        transips: [],
        interfaces: [],
        port_preservation: 'unknown',
      },
      wan: {
        label: 'FortiGate WAN adresi (yükseltmede aktarıldı)',
        mode: 'isp_nat',
        public: [],
        public_dynamic: false,
        transips: ['100.64.12.9'],
        interfaces: [],
        port_preservation: 'unknown',
      },
    });
    await editor();
    const converted = /^Yükseltmede aktarıldı: /;
    expect(within(exit('genel-1')).getByText(converted)).toBeVisible();
    expect(within(exit('wan')).getByText(converted)).toBeVisible();
    const user = userEvent.setup();
    await user.type(within(exit('wan')).getByLabelText('Arayüzler (dstintf)'), 'wan1');
    expect(within(exit('genel-1')).queryByText(converted)).not.toBeInTheDocument();
    expect(within(exit('wan')).queryByText(converted)).not.toBeInTheDocument();
  });

  it('names a refusal of the rules on its field and sends nothing', async () => {
    const calls = serve(TWO_LINES);
    await editor();
    const user = userEvent.setup();
    const row = exit('hat-1');
    const public_ = within(row).getByLabelText('Genel adresler');
    await user.clear(public_);
    await user.type(public_, '100.64.12.9');
    const message =
      "Genel adres özel (RFC 1918), paylaşılan (RFC 6598) ya da ayrılmış bir adres olamaz; FortiGate'in servis sağlayıcı yönlendiricisinin arkasındaki adresini servis sağlayıcı NAT'ı çıkışının NAT adresi olarak girin.";
    expect(within(row).getByText(message)).toBeVisible();
    expect(public_).toHaveAccessibleDescription(expect.stringContaining('RFC 1918') as string);
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
  });

  it('sends the normalised value of a new exit', async () => {
    let sent: unknown = null;
    const calls = serve({}, (body) => {
      sent = body;
      return jsonResponse(200, { changed: [], unchanged: [] });
    });
    const box = await editor();
    expect(
      within(box).getByText('Tanımlı çıkış yok: tek bir çift NAT çıkışı varsayılır.'),
    ).toBeVisible();
    const user = userEvent.setup();
    await user.click(within(box).getByRole('button', { name: 'Çıkış ekle' }));
    const row = within(box).getByRole('group', { name: 'Çıkış adsız çıkış' });
    await user.type(within(row).getByLabelText('Ad'), 'hat-1');
    await user.type(
      within(row).getByLabelText('Genel adresler'),
      '203.0.113.200/32{enter}203.0.113.200',
    );
    await user.type(within(row).getByLabelText('Arayüzler (dstintf)'), 'wan1');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(1);
    });
    expect(sent).toEqual({
      changes: [
        {
          key: 'nat.egresses',
          old: {},
          value: {
            'hat-1': {
              label: '',
              mode: 'direct',
              public: ['203.0.113.200'],
              public_dynamic: false,
              transips: [],
              interfaces: ['wan1'],
              port_preservation: 'unknown',
            },
          },
        },
      ],
    });
  });

  it('shows a refusal of the server at the editor level with its code and message', async () => {
    serve(TWO_LINES, () =>
      errorResponse(422, 'setting_invalid', {
        tr: 'Ayarlar geçersiz.',
        details: [
          {
            key: 'nat.egresses',
            code: 'setting_egress_overlap',
            message_tr:
              'Bir genel adres ya da NAT adresi iki çıkışta birden olamaz; aynı transip yalnızca iki çıkışın arayüzleri ayrıysa kullanılabilir.',
            message_en:
              'A public or NAT address cannot be in two exits; the same transip is allowed only when the two exits have separate interfaces.',
          },
        ],
      }),
    );
    await editor();
    const user = userEvent.setup();
    await user.type(within(exit('hat-2')).getByLabelText('Arayüzler (dstintf)'), 'wan2');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    const field = screen.getByTestId('setting-nat.egresses');
    expect(
      await within(field).findByText(/^Bir genel adres ya da NAT adresi iki çıkışta birden olamaz/),
    ).toHaveTextContent('(setting_egress_overlap)');
    await waitFor(() => {
      expect(screen.getByTestId('egress-editor')).toHaveFocus();
    });
  });
});

describe('the other FortiGate DHCP settings', () => {
  it('the traffic switch shows its help as a warning while it is on', async () => {
    serve(TWO_LINES);
    await editor();
    expect(screen.queryByTestId('traffic-extends-warning')).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole('switch', { name: 'Etiket dhcp.fortigate_traffic_extends' }));
    expect(screen.getByTestId('traffic-extends-warning')).toHaveTextContent(
      'Kapalı (önerilen): FortiGate kirası yalnızca DHCP kayıtlarıyla açık kalır.',
    );
  });

  it('both FortiGate DHCP maps say once that a rebuild applies them to past days; the server field names the interface', async () => {
    serve(TWO_LINES);
    await editor();
    // The logid map's own help (the server's) says it; the message map gets the hint.
    const rebuild = /yeniden oluşturulunca uygulanır/g;
    for (const key of ['dhcp.fortigate_logids', 'dhcp.fortigate_dhcp_msgs'])
      expect(screen.getByTestId(`setting-${key}`).textContent.match(rebuild), key).toHaveLength(1);
    expect(screen.getByTestId('setting-dhcp.fortigate_dhcp_msgs')).toHaveTextContent(
      'Değişiklik geçmiş günlere ancak kiralar yeniden oluşturulunca uygulanır',
    );
    expect(
      within(screen.getByTestId('setting-lease.server_seconds')).getByLabelText(
        'DHCP sunucusu ya da FortiGate arayüz adı',
      ),
    ).toHaveValue('dhcp61');
  });
});
