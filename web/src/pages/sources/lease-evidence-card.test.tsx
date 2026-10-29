import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { LeaseEvidence, SourcesOverview } from '@/lib/sources-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { SOURCES } from '@/test/samples';
import {
  leaseEvidenceAbsent,
  leaseEvidenceNone,
  leaseEvidenceOk,
  leaseEvidenceRefused,
} from '@/test/wave3-mocks';

/*
 * "Kira kanıtı" on Kaynaklar (contract 2.13): the three states with `refused` winning over
 * `none`, one line per table of `none_tables`, the hedged MikroTik DHCP text, the `absent` text,
 * the unverified format, refused rows and the excluded-interfaces info line. `GET /sources` is the
 * captured overview with a hand-mocked `lease_evidence` until the server side captures it.
 */

beforeAll(async () => {
  await import('@/pages/sources/sources-page');
}, 120_000);

afterEach(() => {
  vi.unstubAllGlobals();
});

function serve(evidence: LeaseEvidence | undefined) {
  const overview: SourcesOverview & { lease_evidence?: LeaseEvidence } = {
    ...structuredClone(SOURCES.overview),
    ...(evidence ? { lease_evidence: evidence } : {}),
  };
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/sources') return jsonResponse(200, overview);
    return errorResponse(404, 'not_found');
  });
}

async function card(): Promise<HTMLElement> {
  renderApp('/sources');
  return screen.findByTestId('lease-evidence');
}

describe('the lease-evidence card', () => {
  it('ok: usable rows and current leases per table, srcmac interfaces', async () => {
    serve(leaseEvidenceOk());
    const box = await card();
    expect(within(box).getByRole('heading', { name: 'Kira kanıtı' })).toBeVisible();
    expect(box).toHaveTextContent('Kayıtlar geliyor');
    const fortigate = box.querySelector('[data-table="fg_dhcp"]') as HTMLElement;
    expect(fortigate).toHaveTextContent('FortiGate DHCP');
    expect(fortigate).toHaveTextContent('Son 24 saatte 412 kullanılabilir kayıt');
    expect(fortigate).toHaveTextContent('96 güncel kira');
    expect(box.querySelector('[data-table="mt_dhcp"]')).toHaveTextContent(
      '1.234 kullanılabilir kayıt',
    );
    expect(box).toHaveTextContent(
      'srcmac kısmi kanıt arayüzleri: vlan42 · son bir saatte 4.012 kayıt',
    );
    expect(within(box).queryByRole('status', { name: /doğrulanmadı/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('lease-evidence-unverified')).not.toBeInTheDocument();
  });

  it('none: one line per table, the hedged MikroTik DHCP text', async () => {
    serve(leaseEvidenceNone());
    const box = await card();
    expect(box).toHaveTextContent('Kayıt gelmiyor');
    const lines = [...box.querySelectorAll('[data-none]')].map((line) =>
      line.getAttribute('data-none'),
    );
    expect(lines).toEqual(['mt_dhcp', 'fg_dhcp']);
    expect(box.querySelector('[data-none="fg_dhcp"]')).toHaveTextContent(
      'Kullanılabilir FortiGate DHCP kaydı gelmiyor (daha önce geliyordu): bu kayıtlara dayanan 5651 sorguları iç IP adresinde durabilir. Kaynağın kayıt göndermesini sağlayın (DEVICE-SETUP.md bölüm 2.4 ve 3).',
    );
    expect(box.querySelector('[data-none="mt_dhcp"]')).toHaveTextContent(
      'Son 24 saatte yeni MikroTik DHCP kaydı gelmedi; mevcut MikroTik kiraları trafikle sürer, yeni istemciler zincirsiz kalabilir; kayıtların geldiğini denetleyin (DEVICE-SETUP.md bölüm 3).',
    );
    expect(screen.queryByTestId('lease-evidence-refused')).not.toBeInTheDocument();
  });

  it('none without tables: the absent text', async () => {
    serve(leaseEvidenceAbsent());
    const box = await card();
    expect(box.querySelector('[data-none="absent"]')).toHaveTextContent(
      'Kullanılabilir DHCP ya da ARP-LOG kaydı gelmiyor: 5651 sorguları iç IP adresinde durur (Kurulum kılavuzu bölüm 0.1).',
    );
    expect(box).not.toHaveTextContent('sahada doğrulanmadı');
  });

  it('refused wins over none, with the counts, the unverified format and the excluded rows', async () => {
    serve(leaseEvidenceRefused());
    const box = await card();
    expect(box).toHaveTextContent('Kayıtlar kullanılmıyor');
    expect(within(box).getByTestId('lease-evidence-refused')).toHaveTextContent(
      'FortiGate DHCP kayıtları geliyor ama son 24 saatte 214 tanesi kullanılmadı; nedenler uyarıda.',
    );
    // The records arrive: the "make them arrive" line of `none` would be the wrong fix.
    expect(box.querySelector('[data-none]')).toBeNull();
    expect(within(box).getByRole('link', { name: 'Uyarıyı aç (Güvenlik)' })).toHaveAttribute(
      'href',
      '/security?rule=R20-lease',
    );
    expect(within(box).getByRole('link', { name: 'FortiGate DHCP ayarları' })).toHaveAttribute(
      'href',
      '/settings/application?group=lookup',
    );
    expect(box.querySelector('[data-table="fg_dhcp"]')).toHaveTextContent('7 güncel kira');
    expect(within(box).getByTestId('lease-evidence-refused-rows')).toHaveTextContent(
      'Son 24 saatte 214 FortiGate DHCP kaydı kira kanıtı olarak kullanılmadı.',
    );
    expect(within(box).getByTestId('lease-evidence-excluded')).toHaveTextContent(
      'Hariç tutulan arayüzler: 12 kayıt (kira kanıtı sayılmaz)',
    );
    expect(within(box).getByTestId('lease-evidence-unverified')).toHaveTextContent(
      'FortiGate DHCP kayıt biçimi sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4).',
    );
    expect(box).toHaveTextContent('son bir saatin kayıtları sayılamadı');
  });

  it('an overview without lease_evidence shows no card; the supported set-ups open', async () => {
    serve(undefined);
    renderApp('/sources');
    const note = await screen.findByTestId('supported-topologies');
    expect(screen.queryByTestId('lease-evidence')).not.toBeInTheDocument();
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
