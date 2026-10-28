import { render, screen, within } from '@testing-library/react';

import { evidenceTableLabel } from '@/components/evidence/evidence-labels';
import i18n from '@/lib/i18n';
import type { LookupOut } from '@/lib/api-types';
import type { EgressEntry, LookupEgress } from '@/lib/lookup-types';
import { LOOKUPS } from '@/test/samples';
import { EXIT_DMZ, EXIT_HAT_1, EXIT_HAT_2, EXIT_IMPLICIT, wave3Lookups } from '@/test/wave3-mocks';

import { egressLine, explanationTone } from './chain-text';
import { LookupResults } from './lookup-results';

/*
 * The 5651 Sorgu result view of ADR-0016 (contract 2.13): the exit line with the eight labels of
 * the seven resolutions, every match's exit and dstintf, the unconfigured-sender badge, the
 * srcmac step and conflict, the FortiGate lease, the `low` lease reasons beside the lease step,
 * the no-chain text with the unconverted record, the implicit catch-all's candidates, an
 * internal-mode srcmac chain, an IP conflict beside a srcmac one, and a lookup stored before
 * ADR-0016. Hand mocks built from the captured lookups (`src/test/wave3-mocks.ts`).
 */

const MOCKS = wave3Lookups(LOOKUPS);
const t = i18n.getFixedT('tr');

function show(lookup: LookupOut) {
  render(<LookupResults lookup={lookup} onOpen={vi.fn()} />);
}

/** The lease step (or the srcmac step in its place) of the first chain on the page. */
function leaseStep(): HTMLElement {
  const label = screen.getAllByText(/^MAC \((DHCP\/ARP|srcmac)\)$/)[0] as HTMLElement;
  return label.closest('li') as HTMLElement;
}

function egress(resolution: LookupEgress['resolution'], exits: EgressEntry[]): LookupEgress {
  return {
    resolution,
    egresses: exits,
    searched: {
      transips: [],
      unclaimed: false,
      unclaimed_seen: [],
      outside_seen: [],
      interfaces: [],
      interface_filter: false,
    },
    changes: [],
    changed_after_event: false,
  };
}

describe('the exit line (one label table with the PDF)', () => {
  it.each([
    ['public', [EXIT_HAT_1], 'Genel IP — hat-1 (Ana hat), Doğrudan'],
    ['transip', [EXIT_HAT_2], "NAT adresi — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı"],
    ['transip', [EXIT_DMZ], "NAT adresi — dmz (DMZ'ye NAT), İnternet çıkışı değil"],
    [
      'fallback',
      [EXIT_HAT_1, EXIT_HAT_2],
      "Doğrudan hat doğrulanamadı — hat-1 (Ana hat), Doğrudan; hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    ],
    [
      'fallback',
      [EXIT_HAT_2],
      "NAT adresi doğrulanamadı — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    ],
    ['observed', [], 'Kayıtlarda görülen adres'],
    [
      'observed',
      [{ ...EXIT_HAT_1, mode: 'not_logged', name: 'misafir', label: '' }],
      "Kayıtlarda görülen adres — misafir, FortiGate'ten geçmiyor",
    ],
    [
      'public_unknown',
      [EXIT_HAT_2],
      "Genel adresi bilinmeyen hatlar — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    ],
    ['unconfigured', [EXIT_IMPLICIT], 'Çıkış tanımlı değil'],
    ['no_egress', [EXIT_HAT_1, EXIT_HAT_2], 'Çıkış yok'],
  ] as const)('%s %#', (resolution, exits, line) => {
    expect(egressLine(egress(resolution, [...exits]), t)).toBe(line);
  });

  it('in English too', () => {
    const en = i18n.getFixedT('en');
    expect(egressLine(egress('fallback', [EXIT_HAT_1]), en)).toBe(
      'Direct line not confirmed — hat-1 (Ana hat), Direct',
    );
    expect(egressLine(egress('public_unknown', []), en)).toBe('Lines with unknown public address');
  });
});

describe('the result view', () => {
  it('hat-2: the exit line, each match with its exit and dstintf, the unconfigured sender', () => {
    show(MOCKS.hat2);
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent(
      "Genel IP — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    );
    const matches = screen.getAllByTestId('match');
    expect(matches).toHaveLength(3);
    for (const match of matches)
      expect(within(match).getByTestId('match-exit')).toHaveTextContent(
        'Çıkış: hat-2 (Yedek hat) · dstintf wan2',
      );
    expect(within(matches[0] as HTMLElement).queryByTestId('untrusted-sender')).toBeNull();
    expect(within(matches[2] as HTMLElement).getByTestId('untrusted-sender')).toHaveTextContent(
      'Tanımsız gönderici: unknown-198.51.100.7',
    );
  });

  it('a FortiGate lease shows its lease time, its last DHCP evidence and its source', () => {
    show(MOCKS.fortigate);
    const step = leaseStep();
    expect(within(step).getByTestId('lease-fortigate')).toHaveTextContent(
      'kira süresi (FortiGate kaydı): 86400 sn · son DHCP kanıtı: 09.10.2026 09:00:12',
    );
    expect(within(step).getByTestId('lease-source')).toHaveTextContent(
      'Kaynak: DHCP kaydı (FortiGate)',
    );
    expect(
      within(step).getByRole('button', { name: /^DHCP kaydı \(FortiGate\) · / }),
    ).toBeVisible();
    expect(
      screen.getByText(/^FortiGate DHCP kayıt biçimi bu kurulumda henüz sahada/),
    ).toBeVisible();
  });

  it.each([
    ['renewal', 'lease_renewal_not_seen', /^Bu kiranın istenen andan önceki son DHCP kaydı/],
    ['noNetwork', 'fortigate_dhcp_no_network', /^172\.20\.42\.57 adresi tanımlı hiçbir ağda değil/],
    [
      'sinceLease',
      'fortigate_dhcp_not_converted_since_lease',
      /^Bu kiranın 09\.10\.2026 09:00:12 anındaki son DHCP kaydından sonra/,
    ],
  ] as const)('a low chain through %s shows its text beside the lease step', (name, key, text) => {
    show(MOCKS[name]);
    const step = leaseStep();
    expect(within(step).getByText('Düşük güven')).toBeVisible();
    expect(within(step).getByText(text)).toBeVisible();
    expect(step.querySelector(`[data-explanation="${key}"]`)).not.toBeNull();
    // Once, beside its step, not again above the chain.
    expect(screen.getAllByText(text)).toHaveLength(1);
  });

  it('a srcmac chain shows "Kısmi kanıt: srcmac" in place of the lease step', () => {
    show(MOCKS.srcmacOnly);
    const step = leaseStep();
    expect(within(step).getByText('Kısmi kanıt: srcmac')).toBeVisible();
    expect(step).toHaveTextContent('MAC 00:00:5e:00:53:57');
    expect(step).toHaveTextContent(
      'FortiGate oturum kaydının srcmac alanı (vlan42 arayüzü); kira kaydı yok',
    );
    expect(screen.queryByText('Bu anda geçerli kira kaydı yok')).not.toBeInTheDocument();
    expect(screen.getByTestId('chain-summary')).toHaveTextContent(
      'MAC 00:00:5e:00:53:57 (Kısmi kanıt: srcmac)',
    );
  });

  it('candidates under the implicit catch-all of nat.egresses = {}: "-" as their exit', () => {
    show(MOCKS.unconfigured);
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent('Çıkış tanımlı değil');
    const matches = screen.getAllByTestId('match');
    expect(matches.length).toBeGreaterThan(0);
    for (const match of matches)
      expect(within(match).getByTestId('match-exit')).toHaveTextContent(
        /^Çıkış: - · dstintf wan1$/,
      );
  });

  it('an internal-mode srcmac chain names the interface of its session record', () => {
    show(MOCKS.srcmacInternal);
    const card = screen.getByTestId('attribution');
    const step = leaseStep();
    expect(card).toContainElement(step);
    expect(within(step).getByText('Kısmi kanıt: srcmac')).toBeVisible();
    expect(step).toHaveTextContent('MAC 02:00:5e:00:53:64');
    expect(step).toHaveTextContent(
      'FortiGate oturum kaydının srcmac alanı (vlan60 arayüzü); kira kaydı yok',
    );
  });

  it('an IP conflict beside a srcmac conflict keeps both statements', () => {
    show(MOCKS.conflictSrcmac);
    const conflict = screen.getByTestId('ip-conflict');
    expect(within(conflict).getByText('IP çakışması: 2 olası cihaz')).toBeVisible();
    expect(
      within(conflict).getByText(/^Bu IP adresi bu anda birden fazla MAC adresinde görünüyor/),
    ).toBeVisible();
    expect(within(conflict).getByText('srcmac farklı: 3 olası cihaz')).toBeVisible();
    expect(within(conflict).getByRole('region', { name: 'Olasılık 2' })).toHaveTextContent(
      '00:0c:29:00:00:99',
    );
    expect(
      within(conflict).getByRole('region', { name: 'Olasılık 3 (srcmac, kısmi kanıt)' }),
    ).toHaveTextContent('00:00:5e:00:53:31');
    // The engine's ip_conflict text is the header's, not repeated in each chain.
    expect(conflict.querySelector('[data-explanation="ip_conflict"]')).toBeNull();
  });

  it('a srcmac conflict shows both chains as alternatives', () => {
    show(MOCKS.srcmacConflict);
    const conflict = screen.getByTestId('ip-conflict');
    expect(within(conflict).getByText('srcmac farklı: 2 olası cihaz')).toBeVisible();
    expect(within(conflict).queryByText(/^IP çakışması/)).not.toBeInTheDocument();
    const lease = within(conflict).getByRole('region', { name: 'Olasılık 1' });
    const srcmac = within(conflict).getByRole('region', {
      name: 'Olasılık 2 (srcmac, kısmi kanıt)',
    });
    expect(lease).toHaveTextContent('00:00:5e:00:53:57');
    expect(srcmac).toHaveTextContent('00:00:5e:00:53:31');
    expect(within(srcmac).getByText('Kısmi kanıt: srcmac')).toBeVisible();
  });

  it('a match without a chain: the no-chain text and the unconverted FortiGate DHCP record', () => {
    show(MOCKS.chainMissing);
    expect(
      screen.getByText('Zincir kurulamadı: o anda bu iç IP için kira kaydı yok'),
    ).toBeVisible();
    const card = screen.getByTestId('unconverted-dhcp');
    expect(card).toHaveTextContent(
      'kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez',
    );
    expect(within(card).getByRole('button', { name: /^FortiGate olay kaydı · / })).toBeVisible();
  });

  it('the NAT address of the DMZ and an address of no exit: the exit line, nobody listed', () => {
    show(MOCKS.internalNat);
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent(
      "NAT adresi — dmz (DMZ'ye NAT), İnternet çıkışı değil",
    );
    expect(screen.getByText(/^172\.20\.99\.1, dmz tanımına göre/)).toBeVisible();
    expect(screen.queryAllByTestId('match')).toHaveLength(0);
  });

  it('a lookup stored before ADR-0016 shows no exit line and no exit on its matches', () => {
    show(MOCKS.stored);
    expect(screen.queryByTestId('lookup-exit')).not.toBeInTheDocument();
    expect(screen.queryByTestId('match-exit')).not.toBeInTheDocument();
    expect(screen.queryByTestId('lease-source')).not.toBeInTheDocument();
    expect(screen.getByTestId('chain-summary')).toHaveTextContent('00:00:5e:00:53:57');
  });
});

describe('tones and labels', () => {
  it('every key the contract marks as a warning has the warning tone', () => {
    const warnings = [
      'chain_missing',
      'fortigate_dhcp_not_converted',
      'srcmac_only',
      'srcmac_mismatch',
      'srcmac_ambiguous',
      'srcmac_shared',
      'srcmac_other_lease',
      'egress_public_unknown',
      'public_ip_no_egress',
      'egress_not_logged',
      'egress_transip_not_seen',
      'egress_interface_not_seen',
      'egress_interface_not_seen_kept',
      'egress_unmapped_seen',
      'port_hint',
      'nat_unknown_trandisp',
      'public_ip_not_transip',
      'lease_renewal_not_seen',
      'fortigate_dhcp_not_converted_since_lease',
      'fortigate_dhcp_no_network',
      'egress_observed_not_configured',
      'egress_changed_after_event',
      'session_changed_exit',
      'egress_not_logged_but_seen',
      'dhcp_sources_overlap',
      'session_from_unknown_source',
      'egress_public_dynamic',
      'egress_internal',
      'source_clock_offset',
      // retired keys keep their tones (stored lookups still render)
      'wan_transip_not_seen',
    ];
    for (const key of warnings) expect(explanationTone(key), key).toBe('warning');
    for (const key of [
      'no_lease',
      'no_lease_for_mac',
      'srcmac_agrees',
      'srcmac_infrastructure',
      'egress_search',
      'egress_unconfigured',
      'fortigate_format_unverified',
      'fortigate_format_verified',
      'source_configured_later',
    ])
      expect(explanationTone(key), key).toBe('info');
  });

  it('names the FortiGate DHCP table', () => {
    expect(evidenceTableLabel('fg_dhcp', t)).toBe('DHCP kaydı (FortiGate)');
    expect(evidenceTableLabel('fg_dhcp', i18n.getFixedT('en'))).toBe('DHCP record (FortiGate)');
  });
});
