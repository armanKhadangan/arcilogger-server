import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { evidenceTableLabel } from '@/components/evidence/evidence-labels';
import i18n, { setLanguage } from '@/lib/i18n';
import type { LookupOut } from '@/lib/api-types';
import { outcomeOf, type AttributionChain, type Explanation } from '@/lib/lookup-types';
import { LOOKUPS, need } from '@/test/samples';

import { egressLine, explanationTone } from './chain-text';
import { LookupResults } from './lookup-results';

/*
 * The 5651 Sorgu result view of ADR-0016 (contract 2.13): the exit line with the eight labels of
 * the seven resolutions, every match's exit and dstintf, the unconfigured-sender badge, the
 * srcmac step and conflict, the FortiGate lease, the `low` lease reasons beside the lease step,
 * the no-chain text, the unconverted record, the implicit catch-all's candidates and a lookup
 * stored before ADR-0016. The captured Wave 3 lookups (`lookups.json`); derived from them only
 * what no capture has: the two `low` reasons other than the renewal (the captured renewal chain
 * with its explanation swapped for the opening of the engine's text), an IP conflict beside a
 * srcmac conflict, a srcmac chain that carries a lease, a `superseded` lease end, a match of a
 * search without a public IP and the stored form before ADR-0016.
 */

type Wave3Lookup =
  | 'fortigate_internal'
  | 'fortigate_candidates'
  | 'hat2_candidates'
  | 'fortigate_renewal_low'
  | 'srcmac_only'
  | 'srcmac_conflict'
  | 'fallback_direct'
  | 'fallback_transip'
  | 'observed'
  | 'not_logged'
  | 'not_logged_seen'
  | 'public_unknown'
  | 'no_egress'
  | 'internal_exit'
  | 'unconverted_dhcp'
  | 'untrusted_match';

/** The Wave 3 captures; `src/test/samples.ts` types the Wave 2 keys only. */
const CAPTURED = LOOKUPS as typeof LOOKUPS & Record<Wave3Lookup, LookupOut>;

const t = i18n.getFixedT('tr');
const en = i18n.getFixedT('en');

function sample(key: Wave3Lookup | 'exact' | 'conflict'): LookupOut {
  return structuredClone(CAPTURED[key]);
}

function outcome(lookup: LookupOut) {
  return need(outcomeOf(lookup), `outcome of lookup ${lookup.id}`);
}

/** The chain of an internal-mode lookup, or of its first match. */
function chainOf(lookup: LookupOut): AttributionChain {
  const found = outcome(lookup);
  return need(found.attribution ?? found.matches[0]?.chain, `chain of lookup ${lookup.id}`);
}

function show(lookup: LookupOut, onOpen = vi.fn()) {
  render(<LookupResults lookup={lookup} onOpen={onOpen} />);
  return onOpen;
}

/** The lease step (or the srcmac step in its place) of the first chain on the page. */
function leaseStep(within_: HTMLElement = document.body): HTMLElement {
  const label = within(within_).getAllByText(/^MAC \((DHCP\/ARP|srcmac)\)$/)[0] as HTMLElement;
  return label.closest('li') as HTMLElement;
}

afterEach(() => {
  act(() => {
    setLanguage('tr');
  });
});

describe('the exit line (one label table with the PDF)', () => {
  it.each([
    ['hat2_candidates', "Genel IP — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı"],
    ['not_logged', "Genel IP — misafir, FortiGate'ten geçmiyor"],
    [
      'exact',
      "NAT adresi — wan (FortiGate WAN adresi (yükseltmede aktarıldı)), Servis sağlayıcı NAT'ı",
    ],
    ['internal_exit', 'NAT adresi — dmz, İnternet çıkışı değil'],
    ['fallback_direct', 'Doğrudan hat doğrulanamadı — hat-1, Doğrudan'],
    ['fallback_transip', "NAT adresi doğrulanamadı — hat-2, Servis sağlayıcı NAT'ı"],
    ['observed', 'Kayıtlarda görülen adres'],
    ['not_logged_seen', "Kayıtlarda görülen adres — misafir, FortiGate'ten geçmiyor"],
    ['public_unknown', "Genel adresi bilinmeyen hatlar — hat-2, Servis sağlayıcı NAT'ı"],
    ['fortigate_candidates', 'Çıkış tanımlı değil'],
    ['no_egress', 'Çıkış yok'],
  ] as const)('%s', (key, line) => {
    expect(egressLine(need(outcome(sample(key)).egress, key), t)).toBe(line);
  });

  it('in English too', () => {
    expect(egressLine(need(outcome(sample('fallback_direct')).egress, 'egress'), en)).toBe(
      'Direct line not confirmed — hat-1, Direct',
    );
    expect(egressLine(need(outcome(sample('no_egress')).egress, 'egress'), en)).toBe('No exit');
  });
});

describe('the result view', () => {
  it('hat-2: the exit line, the match with its exit and dstintf', () => {
    show(sample('hat2_candidates'));
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent(
      "Genel IP — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    );
    const [match, ...others] = screen.getAllByTestId('match');
    expect(others).toHaveLength(0);
    expect(within(match as HTMLElement).getByTestId('match-exit')).toHaveTextContent(
      'Çıkış: hat-2 (Yedek hat) · dstintf wan2',
    );
    expect(within(match as HTMLElement).queryByTestId('untrusted-sender')).toBeNull();
  });

  it('a match from an unconfigured sender is marked; matches without a chain say why', () => {
    show(sample('untrusted_match'));
    const matches = screen.getAllByTestId('match');
    expect(matches).toHaveLength(4);
    const marked = matches.map((match) => within(match).queryByTestId('untrusted-sender'));
    expect(marked.map((badge) => badge !== null)).toEqual([false, true, false, false]);
    expect(marked[1]).toHaveTextContent('Tanımsız gönderici: unknown-192.0.2.7');
    for (const index of [0, 2, 3])
      expect(
        within(matches[index] as HTMLElement).getByText(
          'Zincir kurulamadı: o anda bu iç IP için kira kaydı yok',
        ),
      ).toBeVisible();
  });

  it('a FortiGate lease shows its lease time, its last DHCP evidence and its source', () => {
    show(sample('fortigate_internal'));
    const step = leaseStep();
    expect(within(step).getByTestId('lease-fortigate')).toHaveTextContent(
      'kira süresi (FortiGate kaydı): 86400 sn · son DHCP kanıtı: 01.09.2026 10:42:18',
    );
    expect(within(step).getByTestId('lease-source')).toHaveTextContent(
      'Kaynak: DHCP kaydı (FortiGate)',
    );
    expect(
      within(step).getAllByRole('button', { name: /^DHCP kaydı \(FortiGate\) · / }),
    ).toHaveLength(2);
    expect(
      screen.getByText(/^FortiGate DHCP kayıt biçimi bu kurulumda henüz sahada/),
    ).toBeVisible();
  });

  it('a MikroTik DHCP lease shows no FortiGate lease line, though it carries its last evidence', () => {
    const lookup = sample('exact');
    const lease = need(chainOf(lookup).lease, 'lease of exact');
    expect(lease.source_table).toBe('mt_dhcp');
    expect(lease.last_dhcp_evidence).not.toBeNull();
    show(lookup);
    const step = leaseStep();
    expect(within(step).getByTestId('lease-source')).toHaveTextContent(
      'Kaynak: DHCP kaydı (MikroTik)',
    );
    expect(within(step).queryByTestId('lease-fortigate')).not.toBeInTheDocument();
    expect(step).not.toHaveTextContent('son DHCP kanıtı');
  });

  it('a lease closed as superseded has the neutral end label, in both languages', () => {
    // No captured lease ended so: the captured FortiGate lease with that end reason.
    const lookup = sample('fortigate_internal');
    need(chainOf(lookup).lease, 'lease').end_reason = 'superseded';
    show(lookup);
    expect(leaseStep()).toHaveTextContent('sonraki bir DHCP atamasıyla kapandı');
    expect(leaseStep()).not.toHaveTextContent('başka cihaza verildi');
    act(() => {
      setLanguage('en');
    });
    expect(leaseStep()).toHaveTextContent('closed by a later DHCP assignment');
    expect(leaseStep()).not.toHaveTextContent('given to another device');
  });

  it.each([
    [
      'lease_renewal_not_seen',
      /^Bu kiranın istenen andan önceki son DHCP kaydı 03\.09\.2026 08:00:00/,
    ],
    ['fortigate_dhcp_no_network', /^172\.20\.42\.61 adresi tanımlı hiçbir ağda değil/],
    [
      'fortigate_dhcp_not_converted_since_lease',
      /^Bu kiranın 03\.09\.2026 08:00:00 anındaki son DHCP kaydından sonra/,
    ],
  ] as const)('a low chain through %s shows its text beside the lease step', (key, text) => {
    const lookup = sample('fortigate_renewal_low');
    const chain = chainOf(lookup);
    expect(chain.lease?.confidence).toBe('low');
    chain.explanations = chain.explanations.map((item) =>
      item.key === 'lease_renewal_not_seen' ? (LOW_REASONS[key] ?? item) : item,
    );
    show(lookup);
    const step = leaseStep();
    expect(within(step).getByText('Düşük güven')).toBeVisible();
    expect(within(step).getByText(text)).toBeVisible();
    expect(step.querySelector(`[data-explanation="${key}"]`)).not.toBeNull();
    // Once, beside its step, not again above the chain.
    expect(screen.getAllByText(text)).toHaveLength(1);
  });

  it('a srcmac chain shows "Kısmi kanıt: srcmac" in place of the lease step', () => {
    show(sample('srcmac_only'));
    const card = screen.getByTestId('attribution');
    const step = leaseStep();
    expect(card).toContainElement(step);
    expect(within(step).getByText('MAC (srcmac)')).toBeVisible();
    expect(within(step).getByText('Kısmi kanıt: srcmac')).toBeVisible();
    expect(step).toHaveTextContent('MAC 00:00:5e:00:53:57');
    expect(step).toHaveTextContent(
      'FortiGate oturum kaydının srcmac alanı (vlan42 arayüzü); kira kaydı yok',
    );
    expect(screen.queryByText('MAC (DHCP/ARP)')).not.toBeInTheDocument();
    expect(screen.getByTestId('chain-summary')).toHaveTextContent(
      'MAC 00:00:5e:00:53:57 (Kısmi kanıt: srcmac)',
    );
  });

  it('candidates under the implicit catch-all of nat.egresses = {}: "-" as their exit', () => {
    show(sample('fortigate_candidates'));
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent('Çıkış tanımlı değil');
    const exits = screen.getAllByTestId('match-exit').map((item) => item.textContent);
    expect(exits).toEqual(['Çıkış: - · dstintf -', 'Çıkış: - · dstintf wan2']);
  });

  it('without a public IP in the request no exit was determined, never "-" (d-27)', () => {
    // The captured hat-2 match as a destination search returns it: no exit resolved.
    const lookup = sample('hat2_candidates');
    const found = outcome(lookup);
    found.egress = null;
    for (const match of found.matches) match.egress = null;
    show(lookup);
    expect(screen.queryByTestId('lookup-exit')).not.toBeInTheDocument();
    const exit = screen.getByTestId('match-exit');
    expect(exit).toHaveTextContent(
      'Çıkış: çıkış belirlenmedi (talepte genel IP yok) · dstintf wan2',
    );
    expect(exit).not.toHaveTextContent('Çıkış: -');
    act(() => {
      setLanguage('en');
    });
    expect(screen.getByTestId('match-exit')).toHaveTextContent(
      'Exit: exit not determined (no public IP in the request) · dstintf wan2',
    );
    expect(screen.getByTestId('match-exit')).not.toHaveTextContent('Exit: -');
  });

  it('a srcmac conflict shows both chains as alternatives, the srcmac one never as a lease', () => {
    show(sample('srcmac_conflict'));
    const conflict = screen.getByTestId('ip-conflict');
    expect(within(conflict).getByText('srcmac farklı: 2 olası cihaz')).toBeVisible();
    expect(within(conflict).queryByText(/^IP çakışması/)).not.toBeInTheDocument();
    const lease = within(conflict).getByRole('region', { name: 'Olasılık 1' });
    const srcmac = within(conflict).getByRole('region', {
      name: 'Olasılık 2 (srcmac, kısmi kanıt)',
    });
    expect(within(lease).getByText('MAC (DHCP/ARP)')).toBeVisible();
    expect(lease).toHaveTextContent('MAC 00:00:5e:00:53:77');
    expect(within(srcmac).getByText('MAC (srcmac)')).toBeVisible();
    expect(within(srcmac).queryByText('MAC (DHCP/ARP)')).not.toBeInTheDocument();
    expect(srcmac).toHaveTextContent('MAC 00:00:5e:00:53:57');
    expect(within(srcmac).getByText('Kısmi kanıt: srcmac')).toBeVisible();
    expect(srcmac).toHaveTextContent(
      'FortiGate oturum kaydının srcmac alanı (vlan42 arayüzü); kira kaydı yok',
    );
  });

  it('a srcmac chain is never drawn as a lease, even when it carries one', () => {
    // Not captured: the srcmac alternative of srcmac_conflict given the main chain's lease.
    const lookup = sample('srcmac_conflict');
    const chain = chainOf(lookup);
    const other = need(chain.alternatives[0], 'srcmac alternative');
    expect(other.mac_source).toBe('srcmac');
    other.lease = structuredClone(chain.lease);
    show(lookup);
    const srcmac = screen.getByRole('region', { name: 'Olasılık 2 (srcmac, kısmi kanıt)' });
    expect(within(srcmac).getByText('Kısmi kanıt: srcmac')).toBeVisible();
    expect(within(srcmac).queryByText('MAC (DHCP/ARP)')).not.toBeInTheDocument();
    const step = leaseStep(srcmac);
    expect(step).toHaveTextContent('MAC 00:00:5e:00:53:57');
    expect(step).not.toHaveTextContent('00:00:5e:00:53:77');
    expect(srcmac).toHaveTextContent('MAC 00:00:5e:00:53:57 (Kısmi kanıt: srcmac)');
  });

  it('an IP conflict beside a srcmac conflict keeps both statements', () => {
    // Not captured: the captured IP conflict with the captured srcmac alternative added.
    const lookup = sample('conflict');
    const chain = chainOf(lookup);
    const other = need(chainOf(sample('srcmac_conflict')).alternatives[0], 'srcmac alternative');
    chain.alternatives = [...chain.alternatives, other];
    show(lookup);
    const conflict = screen.getByTestId('ip-conflict');
    expect(within(conflict).getByText('IP çakışması: 2 olası cihaz')).toBeVisible();
    expect(
      within(conflict).getByText(/^Bu IP adresi bu anda birden fazla MAC adresinde görünüyor/),
    ).toBeVisible();
    expect(within(conflict).getByText('srcmac farklı: 3 olası cihaz')).toBeVisible();
    expect(
      within(conflict).getByRole('region', { name: 'Olasılık 3 (srcmac, kısmi kanıt)' }),
    ).toHaveTextContent('00:00:5e:00:53:57');
    // The engine's ip_conflict text is the header's, not repeated in each chain.
    expect(conflict.querySelector('[data-explanation="ip_conflict"]')).toBeNull();
  });

  it('the unconverted FortiGate DHCP record: the fixed label, in the list and the raw-line sheet', async () => {
    const lookup = sample('unconverted_dhcp');
    const stored = need(outcome(lookup).unconverted_dhcp?.[0], 'unconverted record');
    const onOpen = show(lookup);
    const card = screen.getByTestId('unconverted-dhcp');
    expect(card).toHaveTextContent('Kira zaman çizelgesine girmemiş FortiGate DHCP kaydı');
    const button = within(card).getByRole('button', { name: /^FortiGate olay kaydı · / });
    expect(button).toHaveTextContent(
      'FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)',
    );
    await userEvent.setup().click(button);
    expect(onOpen).toHaveBeenCalledWith({
      ref: stored.raw,
      evidence: {
        ...stored,
        summary:
          'FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)',
      },
    });
  });

  it('the unconverted record in English: the English label, not the stored Turkish summary', async () => {
    const lookup = sample('unconverted_dhcp');
    const stored = need(outcome(lookup).unconverted_dhcp?.[0], 'unconverted record');
    act(() => {
      setLanguage('en');
    });
    const onOpen = show(lookup);
    const card = screen.getByTestId('unconverted-dhcp');
    const label =
      'FortiGate DHCP record (not in the lease timeline; no chain built; no confidence given)';
    expect(card).toHaveTextContent(label);
    expect(card).not.toHaveTextContent(stored.summary);
    expect(card).not.toHaveTextContent('kira zaman çizelgesine girmedi');
    await userEvent.setup().click(within(card).getByRole('button'));
    expect(onOpen).toHaveBeenCalledWith({
      ref: stored.raw,
      evidence: { ...stored, summary: label },
    });
  });

  it('the NAT address of the DMZ: the exit line, nobody listed', () => {
    show(sample('internal_exit'));
    expect(screen.getByTestId('lookup-exit')).toHaveTextContent(
      'NAT adresi — dmz, İnternet çıkışı değil',
    );
    expect(screen.getByText(/^172\.20\.99\.1, dmz tanımına göre/)).toBeVisible();
    expect(screen.queryAllByTestId('match')).toHaveLength(0);
  });

  it('a lookup stored before ADR-0016 shows no exit line and no exit on its matches', () => {
    // As stored before ADR-0016: the captured exact lookup without the members it added.
    const lookup = sample('exact');
    const found = outcome(lookup);
    delete found.egress;
    delete found.unconverted_dhcp;
    for (const match of found.matches) {
      delete match.egress;
      delete match.untrusted_sources;
      delete match.traffic.srcintf;
      delete match.traffic.dstintf;
      delete match.traffic.srcmac;
      if (match.chain) {
        delete match.chain.mac_source;
        if (match.chain.lease) {
          delete match.chain.lease.source_table;
          delete match.chain.lease.lease_seconds;
          delete match.chain.lease.last_dhcp_evidence;
        }
      }
    }
    show(lookup);
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

/**
 * The `low` reasons no capture has, as the engine gives them for the captured renewal chain
 * (172.20.42.61, last DHCP record 03.09.2026 08:00:00): the opening of the engine's text.
 */
const LOW_REASONS: Partial<Record<string, Explanation>> = {
  fortigate_dhcp_no_network: {
    key: 'fortigate_dhcp_no_network',
    text_tr:
      "172.20.42.61 adresi tanımlı hiçbir ağda değil (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar). Bir istemcinin FortiGate'ten aldığı adresi reddedip (DHCPDECLINE, ör. adresi havuzda statik adresli bir cihaz kullanıyorsa) başka bir adres aldığı yalnızca tanımlı ağlarda anlaşılır; bu kira o anda adresi kullanan cihazı göstermiyor olabilir, zincir bu nedenle düşük güvenle gösterildi.",
    text_en:
      '172.20.42.61 lies in no configured network (Devices and people → Departments and VLANs). A client that declines an address the FortiGate gave it (DHCPDECLINE, e.g. because a static device in the pool uses it) and takes another is only detected in configured networks; this lease may not show the device that used the address at that moment, so the chain is shown with low confidence.',
    params: { ip: '172.20.42.61' },
  },
  fortigate_dhcp_not_converted_since_lease: {
    key: 'fortigate_dhcp_not_converted_since_lease',
    text_tr:
      'Bu kiranın 03.09.2026 08:00:00 anındaki son DHCP kaydından sonra, istenen andan önce, 172.20.42.61 adresi ya da bu kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı FortiGate DHCP kayıtları var. Zincir bu nedenle düşük güvenle gösterildi.',
    text_en:
      "After this lease's last DHCP record at 03.09.2026 08:00:00 and before the requested moment there are FortiGate DHCP records for 172.20.42.61 or for this lease's MAC address that the lease timeline did not apply to this lease. The chain is shown with low confidence.",
    params: { last: '2026-09-03T05:00:00+00:00', ip: '172.20.42.61' },
  },
};
