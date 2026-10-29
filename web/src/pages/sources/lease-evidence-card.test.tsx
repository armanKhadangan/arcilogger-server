import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setLanguage } from '@/lib/i18n';
import type { SourcesOverview } from '@/lib/sources-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { SOURCES } from '@/test/samples';

/*
 * "Kira kanıtı" on Kaynaklar (contract 2.13): the three states with `refused` winning over
 * `none`, one line per table of `none_tables`, the hedged MikroTik DHCP text, the `absent` text,
 * the unverified format, refused rows and the excluded-interfaces info line. `GET /sources` is a
 * captured overview: `overview` (nothing arrives yet) and the three `overview_lease_*` states
 * (d-25). Not captured, so derived from a captured state here: `absent` (2.12 names ok, none and
 * refused only) and a srcmac count that did not finish.
 */

/** The captured lease-evidence states; `src/test/samples.ts` types the Wave 2 keys only. */
const CAPTURED = SOURCES as typeof SOURCES &
  Record<'overview_lease_ok' | 'overview_lease_none' | 'overview_lease_refused', SourcesOverview>;

beforeAll(async () => {
  await import('@/pages/sources/sources-page');
}, 120_000);

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

function serve(overview: SourcesOverview) {
  const answer = structuredClone(overview);
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/sources') return jsonResponse(200, answer);
    return errorResponse(404, 'not_found');
  });
}

async function card(): Promise<HTMLElement> {
  renderApp('/sources');
  return screen.findByTestId('lease-evidence');
}

describe('the lease-evidence card', () => {
  it('ok: usable rows and current leases per table, srcmac interfaces', async () => {
    serve(CAPTURED.overview_lease_ok);
    const box = await card();
    expect(within(box).getByRole('heading', { name: 'Kira kanıtı' })).toBeVisible();
    expect(box).toHaveTextContent('Kayıtlar geliyor');
    const fortigate = box.querySelector('[data-table="fg_dhcp"]') as HTMLElement;
    expect(fortigate).toHaveTextContent('FortiGate DHCP');
    expect(fortigate).toHaveTextContent('Son 24 saatte 2 kullanılabilir kayıt');
    expect(fortigate).toHaveTextContent('2 güncel kira');
    expect(fortigate).toHaveTextContent('Son kayıt 13.10.2026 06:35:59');
    const mikrotik = box.querySelector('[data-table="mt_dhcp"]') as HTMLElement;
    expect(mikrotik).toHaveTextContent('Son 24 saatte 0 kullanılabilir kayıt');
    expect(mikrotik).toHaveTextContent('Son bir saatte kayıt yok');
    expect(box).toHaveTextContent('srcmac kısmi kanıt arayüzleri: vlan42 · son bir saatte 0 kayıt');
    // FortiGate DHCP rows arrive while the format is not verified on site.
    expect(within(box).getByTestId('lease-evidence-unverified')).toHaveTextContent(
      'FortiGate DHCP kayıt biçimi sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4).',
    );
    expect(box.querySelector('[data-none]')).toBeNull();
    expect(within(box).queryByTestId('lease-evidence-refused')).not.toBeInTheDocument();
  });

  it('ok without FortiGate DHCP rows: no unverified line, no srcmac line', async () => {
    serve(SOURCES.overview);
    const box = await card();
    expect(box).toHaveTextContent('Kayıtlar geliyor');
    expect(box.querySelector('[data-table="fg_dhcp"]')).toHaveTextContent(
      'Son 24 saatte 0 kullanılabilir kayıt',
    );
    expect(within(box).queryByTestId('lease-evidence-unverified')).not.toBeInTheDocument();
    expect(box).not.toHaveTextContent('srcmac');
  });

  it('in English: the title and the state', async () => {
    serve(CAPTURED.overview_lease_ok);
    act(() => {
      setLanguage('en');
    });
    const box = await card();
    expect(within(box).getByRole('heading', { name: 'Lease evidence' })).toBeVisible();
    expect(box).toHaveTextContent('Records arrive');
    expect(box.querySelector('[data-table="fg_dhcp"]')).toHaveTextContent(
      '2 usable records in the last 24 hours',
    );
    expect(box).not.toHaveTextContent('Kira kanıtı');
  });

  it('none: one line per table, the hedged MikroTik DHCP text', async () => {
    serve(CAPTURED.overview_lease_none);
    const box = await card();
    expect(box).toHaveTextContent('Kayıt gelmiyor');
    const lines = [...box.querySelectorAll('[data-none]')].map((line) =>
      line.getAttribute('data-none'),
    );
    expect(lines).toEqual(['mt_dhcp', 'mt_arp']);
    expect(box.querySelector('[data-none="mt_arp"]')).toHaveTextContent(
      'Kullanılabilir MikroTik ARP-LOG kaydı gelmiyor (daha önce geliyordu): bu kayıtlara dayanan 5651 sorguları iç IP adresinde durabilir. Kaynağın kayıt göndermesini sağlayın (DEVICE-SETUP.md bölüm 2.4 ve 3).',
    );
    expect(box.querySelector('[data-none="mt_dhcp"]')).toHaveTextContent(
      'Son 24 saatte yeni MikroTik DHCP kaydı gelmedi; mevcut MikroTik kiraları trafikle sürer, yeni istemciler zincirsiz kalabilir; kayıtların geldiğini denetleyin (DEVICE-SETUP.md bölüm 3).',
    );
    expect(box).toHaveTextContent('srcmac kısmi kanıt arayüzleri: vlan42 · son bir saatte 6 kayıt');
    expect(screen.queryByTestId('lease-evidence-refused')).not.toBeInTheDocument();
  });

  it('none without tables: the absent text', async () => {
    // Not captured (d-25): the captured `none` state with an empty `none_tables`.
    const overview = structuredClone(CAPTURED.overview_lease_none);
    overview.lease_evidence.none_tables = [];
    serve(overview);
    const box = await card();
    const lines = [...box.querySelectorAll('[data-none]')].map((line) =>
      line.getAttribute('data-none'),
    );
    expect(lines).toEqual(['absent']);
    expect(box.querySelector('[data-none="absent"]')).toHaveTextContent(
      'Kullanılabilir DHCP ya da ARP-LOG kaydı gelmiyor: 5651 sorguları iç IP adresinde durur (Kurulum kılavuzu bölüm 0.1).',
    );
  });

  it('refused wins over none, with the counts, the unverified format and the excluded rows', async () => {
    serve(CAPTURED.overview_lease_refused);
    const box = await card();
    expect(box).toHaveTextContent('Kayıtlar kullanılmıyor');
    expect(within(box).getByTestId('lease-evidence-refused')).toHaveTextContent(
      'FortiGate DHCP kayıtları geliyor ama son 24 saatte 1 tanesi kullanılmadı; nedenler uyarıda.',
    );
    // The records arrive: the "make them arrive" lines of `none` (its tables are still listed in
    // `none_tables`) would be the wrong fix.
    expect(CAPTURED.overview_lease_refused.lease_evidence.none_tables).not.toEqual([]);
    expect(box.querySelector('[data-none]')).toBeNull();
    expect(within(box).getByRole('link', { name: 'Uyarıyı aç (Güvenlik)' })).toHaveAttribute(
      'href',
      '/security?rule=R20-lease',
    );
    expect(within(box).getByRole('link', { name: 'FortiGate DHCP ayarları' })).toHaveAttribute(
      'href',
      '/settings/application?group=lookup',
    );
    expect(box.querySelector('[data-table="fg_dhcp"]')).toHaveTextContent('2 güncel kira');
    expect(within(box).getByTestId('lease-evidence-refused-rows')).toHaveTextContent(
      'Son 24 saatte 1 FortiGate DHCP kaydı kira kanıtı olarak kullanılmadı.',
    );
    expect(within(box).getByTestId('lease-evidence-excluded')).toHaveTextContent(
      'Hariç tutulan arayüzler: 1 kayıt (kira kanıtı sayılmaz)',
    );
    expect(within(box).getByTestId('lease-evidence-unverified')).toHaveTextContent(
      'FortiGate DHCP kayıt biçimi sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4).',
    );
  });

  it('a srcmac count that did not finish', async () => {
    // Not captured: the captured `ok` state with the count missing (`rows_last_hour: null`).
    const overview = structuredClone(CAPTURED.overview_lease_ok);
    overview.lease_evidence.srcmac.rows_last_hour = null;
    serve(overview);
    const box = await card();
    expect(box).toHaveTextContent(
      'srcmac kısmi kanıt arayüzleri: vlan42 · son bir saatin kayıtları sayılamadı',
    );
  });

  it('the supported set-ups open', async () => {
    serve(SOURCES.overview);
    renderApp('/sources');
    const note = await screen.findByTestId('supported-topologies');
    const toggle = within(note).getByRole('button', { name: 'Desteklenen ağ yapıları' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(note).queryByRole('listitem')).not.toBeInTheDocument();
    await userEvent.setup().click(toggle);
    const items = within(note)
      .getAllByRole('listitem')
      .map((item) => item.textContent);
    expect(items).toHaveLength(7);
    expect(items[0]).toBe('Tek yerleşke ve internete çıkışta bir FortiGate.');
    expect(items[6]).toMatch(/^Desteklenmeyenler: IPv6 sorguları; /);
    expect(note).toHaveTextContent('Ayrıntılar: Kurulum kılavuzu bölüm 0.1');
  });
});
