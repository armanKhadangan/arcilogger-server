import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setLanguage } from '@/lib/i18n';
import type { SettingChange, SettingGroup, SettingsOut, SettingValue } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import type { ErrorSample } from '@/test/samples';

import type { Egresses } from './egress-model';

/*
 * Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi: the editor of the internet
 * exits (`nat.egresses`, contract 2.13). The group is the captured `GET /settings` group `lookup`
 * (`settings.json`) with the exits of each test in place of the stored converted `wan`; the
 * server's refusal is the captured 422 `setting_invalid_egress`.
 */

/** `samples/settings.json`, read as `src/test/samples.ts` reads the samples (it does not load it). */
const SETTINGS = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'settings.json'), 'utf8'),
) as { list: SettingsOut; errors: Record<string, ErrorSample> };

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

/** The captured group `lookup`, with `egresses` and `values` in place of stored values. */
function groups(egresses: Egresses, values: Record<string, SettingValue> = {}): SettingGroup[] {
  const group = structuredClone(SETTINGS.list.groups.find((item) => item.id === 'lookup'));
  if (!group) throw new Error('sample missing: settings group lookup');
  const stored: Record<string, SettingValue> = { 'nat.egresses': egresses, ...values };
  for (const setting of group.settings)
    if (setting.key in stored) setting.value = stored[setting.key] as SettingValue;
  return [group];
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

function serve(
  egresses: Egresses,
  patch?: (body: unknown) => Response,
  values: Record<string, SettingValue> = {},
) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings' && request.method === 'GET')
      return jsonResponse(200, { groups: groups(egresses, values) } satisfies SettingsOut);
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

  it('takes the server answer again after saving a new exit', async () => {
    // A server that keeps what it is sent: PATCH stores the values, a reset stores the default.
    const stored: Record<string, SettingValue> = {
      'nat.egresses': {},
      'lookup.max_candidates': 150,
    };
    const sent: unknown[] = [];
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      if (request.path === '/api/v1/settings' && request.method === 'GET') {
        const answer = groups(stored['nat.egresses'] as Egresses, stored);
        for (const setting of answer[0]?.settings ?? [])
          setting.is_default = JSON.stringify(setting.value) === JSON.stringify(setting.default);
        return jsonResponse(200, { groups: answer } satisfies SettingsOut);
      }
      if (request.path === '/api/v1/settings' && request.method === 'PATCH') {
        sent.push(request.body);
        const { changes } = request.body as { changes: SettingChange[] };
        for (const change of changes) stored[change.key] = change.value;
        return jsonResponse(200, {
          changed: changes.map(({ key, old, value }) => ({ key, old, new: value })),
          unchanged: [],
        });
      }
      if (request.path === '/api/v1/settings/lookup.max_candidates/reset') {
        stored['lookup.max_candidates'] = 100;
        return jsonResponse(200, {
          changed: [{ key: 'lookup.max_candidates', old: 150, new: 100 }],
          unchanged: [],
        });
      }
      return errorResponse(404, 'not_found');
    });
    const box = await editor();
    const user = userEvent.setup();
    await user.click(within(box).getByRole('button', { name: 'Çıkış ekle' }));
    const row = within(box).getByRole('group', { name: 'Çıkış adsız çıkış' });
    await user.type(within(row).getByLabelText('Ad'), 'hat-1');
    await user.type(within(row).getByLabelText('Genel adresler'), '203.0.113.200');
    const save = screen.getByRole('button', { name: 'Kaydet' });
    await user.click(save);
    await waitFor(() => {
      expect(save).toBeDisabled();
    });
    expect(sent).toHaveLength(1);
    const candidates = screen.getByTestId('setting-lookup.max_candidates');
    await user.click(within(candidates).getByRole('button', { name: 'Varsayılana dön' }));
    await waitFor(() => {
      expect(within(candidates).getByRole('textbox')).toHaveValue('100');
    });
    expect(within(candidates).queryByRole('button', { name: 'Varsayılana dön' })).toBeNull();
    expect(screen.queryByText(/^\d+ değişiklik$/)).not.toBeInTheDocument();
    expect(save).toBeDisabled();
    // The next save sends only its own change, never the value from before the reset.
    await user.click(screen.getByRole('switch', { name: 'Trafik FortiGate kirasını uzatır' }));
    await user.click(save);
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(2);
    });
    expect(sent[1]).toEqual({
      changes: [{ key: 'dhcp.fortigate_traffic_extends', old: false, value: true }],
    });
  });

  it('shows a refusal of the server at the editor level with its code and message', async () => {
    const refusal = SETTINGS.errors.setting_invalid_egress;
    if (!refusal) throw new Error('sample missing: settings error setting_invalid_egress');
    serve(TWO_LINES, () => jsonResponse(refusal.status, refusal.body));
    await editor();
    const user = userEvent.setup();
    await user.type(within(exit('hat-2')).getByLabelText('Arayüzler (dstintf)'), 'wan2');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    const field = screen.getByTestId('setting-nat.egresses');
    expect(
      await within(field).findByText(
        /^Genel adres özel \(RFC 1918\), paylaşılan \(RFC 6598\) ya da ayrılmış bir adres olamaz;/,
      ),
    ).toHaveTextContent('(setting_egress_public_private)');
    expect(
      screen.getByText('Bazı ayar değerleri geçersiz; işaretli ayarları düzeltin.'),
    ).toBeVisible();
    await waitFor(() => {
      expect(screen.getByTestId('egress-editor')).toHaveFocus();
    });
  });
});

describe('a stored value the rows cannot give back as stored', () => {
  /** An unknown mode (written around the rules, or by a later version before a rollback). */
  const UNREADABLE = { misafir: { mode: 'notlogged', public: ['198.51.100.90'] } };
  /** Valid on the server: a label with spaces around it, an interface name with a comma. */
  const AS_STORED: Egresses = {
    'hat-1': {
      ...(TWO_LINES['hat-1'] as Egresses[string]),
      label: ' Ana hat',
      interfaces: ['a,b'],
    },
  };

  it.each([
    ['an exit it cannot read', UNREADABLE],
    ['a label and an interface name as stored', AS_STORED],
  ])('is no change: saving another key of the group sends only that key (%s)', async (_, value) => {
    let sent: unknown = null;
    const calls = serve(value as Egresses, (body) => {
      sent = body;
      return jsonResponse(200, { changed: [], unchanged: [] });
    });
    await editor();
    const save = screen.getByRole('button', { name: 'Kaydet' });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole('switch', { name: 'Trafik FortiGate kirasını uzatır' }));
    await user.click(save);
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(1);
    });
    expect(sent).toEqual({
      changes: [{ key: 'dhcp.fortigate_traffic_extends', old: false, value: true }],
    });
  });

  it('a stored value another editor reads back differently is no change of the group', async () => {
    // A FortiGate interface name may hold a comma; the list editor splits at commas.
    let sent: unknown = null;
    const calls = serve(
      TWO_LINES,
      (body) => {
        sent = body;
        return jsonResponse(200, { changed: [], unchanged: [] });
      },
      { 'lookup.srcmac_interfaces': ['a,b'] },
    );
    await editor();
    const save = screen.getByRole('button', { name: 'Kaydet' });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole('switch', { name: 'Trafik FortiGate kirasını uzatır' }));
    await user.click(save);
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(1);
    });
    expect(sent).toEqual({
      changes: [{ key: 'dhcp.fortigate_traffic_extends', old: false, value: true }],
    });
  });

  it('shows an exit it cannot read as stored, with the rule it breaks, never as a direct line', async () => {
    serve(UNREADABLE as unknown as Egresses);
    await editor();
    const kept = exit('misafir');
    expect(
      within(kept).getByText(/^Bu çıkış kaydedildiği biçimiyle düzenleyicide gösterilemiyor/),
    ).toBeVisible();
    expect(
      within(kept).getByText('NAT türü direct, isp_nat, not_logged ya da internal olmalı.'),
    ).toBeVisible();
    expect(within(kept).getByRole('figure', { name: 'Kayıtlı değer' })).toHaveTextContent(
      '"mode": "notlogged"',
    );
    expect(within(kept).queryByRole('combobox')).not.toBeInTheDocument();
    expect(within(kept).queryByText("Doğrudan (genel IP FortiGate'te)")).not.toBeInTheDocument();
  });

  it('keeps it in every save of the exits until it is removed', async () => {
    let sent: unknown = null;
    const calls = serve(UNREADABLE as unknown as Egresses, (body) => {
      sent = body;
      return jsonResponse(200, { changed: [], unchanged: [] });
    });
    const box = await editor();
    const user = userEvent.setup();
    await user.click(within(box).getByRole('button', { name: 'Çıkış ekle' }));
    const row = within(box).getByRole('group', { name: 'Çıkış adsız çıkış' });
    await user.type(within(row).getByLabelText('Ad'), 'hat-1');
    await user.type(within(row).getByLabelText('Genel adresler'), '203.0.113.200');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(
      within(screen.getByTestId('setting-nat.egresses')).getAllByText(
        'NAT türü direct, isp_nat, not_logged ya da internal olmalı.',
      ).length,
    ).toBeGreaterThan(0);
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
    await user.click(within(box).getByRole('button', { name: 'Kaldır: misafir' }));
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(1);
    });
    expect(sent).toMatchObject({
      changes: [
        {
          key: 'nat.egresses',
          old: UNREADABLE,
          value: { 'hat-1': { mode: 'direct', public: ['203.0.113.200'] } },
        },
      ],
    });
  });

  it('names a stored value that is no object above the rows', async () => {
    serve('hat-1' as unknown as Egresses);
    const box = await editor();
    expect(within(box).getByTestId('egress-stored-unreadable')).toHaveTextContent(
      'Kayıtlı çıkış tanımı okunamıyor: Değer türü yanlış: nesne bekleniyor.',
    );
    expect(within(box).queryByText(/^Tanımlı çıkış yok/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Kaydet' })).toBeDisabled();
  });
});

describe('the other FortiGate DHCP settings', () => {
  it('the traffic switch shows its help as a warning while it is on', async () => {
    serve(TWO_LINES);
    await editor();
    expect(screen.queryByTestId('traffic-extends-warning')).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole('switch', { name: 'Trafik FortiGate kirasını uzatır' }));
    // The server's help of the switch (captured).
    expect(screen.getByTestId('traffic-extends-warning')).toHaveTextContent(
      'Kapalı (önerilen): FortiGate kirası yalnızca DHCP kayıtlarıyla (Ack, yenileme) açık kalır;',
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
