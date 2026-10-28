/**
 * HAND MOCKS of the Wave 3 answers (contract 2.10–2.13, ADR-0016) for the unit and E2E tests of
 * the web, written from the contract before the server side captures them: the observed NAT
 * addresses (`GET /settings/fortigate/observed`), the lease-evidence card of `GET /sources` and
 * the new parts of a stored lookup outcome. WP-E's second step replaces them with the samples
 * `capture_api_samples.py` writes (`web/e2e/samples/*.json`) and deletes this file.
 *
 * Only type imports, relative ones: the E2E tests import this module too. The example world only
 * (172.20.x, 100.64.12.9 / 100.64.13.9 behind the ISP routers, RFC 5737 public addresses).
 */

import type { LookupOut } from '../lib/api-types';
import type {
  AttributionChain,
  EgressEntry,
  EvidenceRef,
  Explanation,
  LookupEgress,
  LookupOutcome,
  NatMatch,
} from '../lib/lookup-types';
import type { Egresses, FortigateObserved } from '../pages/settings/egress-model';
import type { LeaseEvidence } from '../pages/sources/lease-evidence-model';

// -- GET /settings/fortigate/observed (2.12) -------------------------------------------------------

/**
 * A dual-WAN day: hat-1 direct on wan1 (`203.0.113.200`), hat-2 behind an ISP router on wan2
 * (`100.64.13.9`, not yet an exit), the DMZ NAT on `dmz` (`172.20.99.1`, unclaimed) and a NAT type
 * nobody listed; srcmac on two VLAN interfaces, one behind a router.
 */
export function observedSample(): FortigateObserved {
  return {
    generated_at: '2026-10-12T12:00:00+00:00',
    nat: {
      hours: 24,
      truncated: false,
      rows: [
        {
          transip: '203.0.113.200',
          dstintf: 'wan1',
          trandisp: 'snat',
          sessions: 41_877,
          first_seen: '2026-10-11T12:00:04+00:00',
          last_seen: '2026-10-12T11:59:58+00:00',
          egress: 'hat-1',
          trandisp_known: true,
        },
        {
          transip: '100.64.13.9',
          dstintf: 'wan2',
          trandisp: 'snat',
          sessions: 18_234,
          first_seen: '2026-10-11T12:00:31+00:00',
          last_seen: '2026-10-12T11:59:41+00:00',
          egress: null,
          trandisp_known: true,
        },
        {
          transip: '172.20.99.1',
          dstintf: 'dmz',
          trandisp: 'snat',
          sessions: 912,
          first_seen: '2026-10-11T12:14:09+00:00',
          last_seen: '2026-10-12T11:40:12+00:00',
          egress: null,
          trandisp_known: true,
        },
        {
          transip: '198.51.100.20',
          dstintf: 'wan1',
          trandisp: 'snat-pool',
          sessions: 37,
          first_seen: '2026-10-12T08:02:11+00:00',
          last_seen: '2026-10-12T09:15:40+00:00',
          egress: null,
          trandisp_known: false,
        },
      ],
      unmapped_transips: ['100.64.13.9', '172.20.99.1', '198.51.100.20'],
      unknown_trandisp: ['snat-pool'],
    },
    srcmac: {
      hours: 1,
      truncated: false,
      interfaces: [
        { srcintf: 'vlan42', rows: 4_012, macs: 37, max_ips_per_mac: 1, configured: false },
        { srcintf: 'vlan60', rows: 1_830, macs: 1, max_ips_per_mac: 23, configured: false },
      ],
    },
  };
}

/** The same with the stored `nat.egresses` unreadable (every exit `null`, `exits_invalid`). */
export function observedExitsInvalid(): FortigateObserved {
  const sample = observedSample();
  return {
    ...sample,
    nat: {
      ...sample.nat,
      exits_invalid: true,
      rows: sample.nat.rows.map((row) => ({ ...row, egress: null })),
    },
  };
}

/** The exits of the dual-WAN example as `nat.egresses` stores them (hat-1 only). */
export const HAT_1: Egresses = {
  'hat-1': {
    label: 'Ana hat',
    mode: 'direct',
    public: ['203.0.113.200'],
    public_dynamic: false,
    transips: [],
    interfaces: ['wan1'],
    port_preservation: 'unknown',
  },
};

// -- GET /sources lease_evidence (2.12) -------------------------------------------------------------

/** A healthy mixed site: MikroTik DHCP and ARP-LOG for some networks, FortiGate DHCP for others. */
export function leaseEvidenceOk(): LeaseEvidence {
  return {
    hours: 24,
    mt_dhcp: { rows: 1_234, last_hour: '2026-10-12T11:52:10+00:00', leases_current: 310 },
    fg_dhcp: {
      rows: 412,
      last_hour: '2026-10-12T11:58:41+00:00',
      leases_current: 96,
      refused_rows: 0,
      excluded_rows: 0,
      format_verified: true,
    },
    mt_arp: { rows: 560, last_hour: '2026-10-12T11:00:02+00:00', leases_current: 42 },
    srcmac: { interfaces: ['vlan42'], rows_last_hour: 4_012 },
    state: 'ok',
    none_tables: [],
  };
}

/** FortiGate DHCP and MikroTik DHCP stopped (both `none` causes active), ARP-LOG still arrives. */
export function leaseEvidenceNone(): LeaseEvidence {
  const sample = leaseEvidenceOk();
  return {
    ...sample,
    mt_dhcp: { rows: 0, last_hour: null, leases_current: 288 },
    fg_dhcp: { ...sample.fg_dhcp, rows: 0, last_hour: null, leases_current: 0 },
    srcmac: { interfaces: [], rows_last_hour: null },
    state: 'none',
    none_tables: ['mt_dhcp', 'fg_dhcp'],
  };
}

/** No usable record of any table in 30 days (`R20-lease:absent`). */
export function leaseEvidenceAbsent(): LeaseEvidence {
  return {
    hours: 24,
    mt_dhcp: { rows: 0, last_hour: null, leases_current: 0 },
    fg_dhcp: {
      rows: 0,
      last_hour: null,
      leases_current: 0,
      refused_rows: 0,
      excluded_rows: 0,
      format_verified: false,
    },
    mt_arp: { rows: 0, last_hour: null, leases_current: 0 },
    srcmac: { interfaces: [], rows_last_hour: null },
    state: 'none',
    none_tables: [],
  };
}

/**
 * A FortiGate-only site whose rows are nearly all refused (a `dhcp_msg` nobody listed): `refused`
 * wins over the `none` cause the same feed opens; rows of an excluded IPsec interface are only
 * information; the format is not verified yet.
 */
export function leaseEvidenceRefused(): LeaseEvidence {
  return {
    hours: 24,
    mt_dhcp: { rows: 0, last_hour: null, leases_current: 0 },
    fg_dhcp: {
      rows: 3,
      last_hour: '2026-10-12T11:58:41+00:00',
      leases_current: 7,
      refused_rows: 214,
      excluded_rows: 12,
      format_verified: false,
    },
    mt_arp: { rows: 0, last_hour: null, leases_current: 0 },
    srcmac: { interfaces: ['vlan42', 'vlan60'], rows_last_hour: null },
    state: 'refused',
    none_tables: ['fg_dhcp'],
  };
}

// -- stored lookup outcomes (2.10, 2.11) ------------------------------------------------------------

/** An explanation as `texts.explain` gives it (both texts filled with one params dict). */
function explanation(
  key: string,
  textTr: string,
  textEn: string,
  params: Explanation['params'] = {},
): Explanation {
  return { key, text_tr: textTr, text_en: textEn, params };
}

const EXITS_PATH_TR =
  'Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)';
const EXITS_PATH_EN =
  'Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT)';

/** The exits of the dual-WAN example as a lookup records them (2.8.1's example). */
export const EXIT_HAT_1: EgressEntry = {
  name: 'hat-1',
  label: 'Ana hat',
  mode: 'direct',
  public: ['203.0.113.200'],
  public_dynamic: false,
  transips: [],
  interfaces: ['wan1'],
  port_preservation: 'unknown',
};
export const EXIT_HAT_2: EgressEntry = {
  name: 'hat-2',
  label: 'Yedek hat',
  mode: 'isp_nat',
  public: ['198.51.100.77'],
  public_dynamic: true,
  transips: ['100.64.13.9'],
  interfaces: ['wan2'],
  port_preservation: 'unknown',
};
export const EXIT_DMZ: EgressEntry = {
  name: 'dmz',
  label: "DMZ'ye NAT",
  mode: 'internal',
  public: [],
  public_dynamic: false,
  transips: ['172.20.99.1'],
  interfaces: ['dmz'],
  port_preservation: 'unknown',
};
/** The implicit catch-all of the shipped `nat.egresses = {}` (name `""`, round-5 note). */
export const EXIT_IMPLICIT: EgressEntry = {
  name: '',
  label: '',
  mode: 'isp_nat',
  public: [],
  public_dynamic: false,
  transips: [],
  interfaces: [],
  port_preservation: 'unknown',
};

function egressOf(
  resolution: LookupEgress['resolution'],
  egresses: EgressEntry[],
  searched: Partial<LookupEgress['searched']> = {},
): LookupEgress {
  return {
    resolution,
    egresses,
    searched: {
      transips: [],
      unclaimed: false,
      unclaimed_seen: [],
      outside_seen: [],
      interfaces: [],
      interface_filter: false,
      ...searched,
    },
    changes: [],
    changed_after_event: false,
  };
}

function outcomeOf(lookup: LookupOut): LookupOutcome {
  const outcome = lookup.outcome as LookupOutcome | null;
  if (!outcome) throw new Error(`lookup ${lookup.id} has no outcome`);
  return outcome;
}

function withId(lookup: LookupOut, id: number): LookupOut {
  return { ...lookup, id };
}

/** The FortiGate DHCP Ack of the golden chain (`PLN-PC14`, lease 86400, `vlan42`). */
const FG_ACK: EvidenceRef = {
  table: 'fg_dhcp',
  row_id: 7,
  time: '2026-10-09T06:00:12+00:00',
  raw: { source: 'fortigate', day: '2026-10-09', line: 3, offset: 610 },
  summary: 'vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14',
};

function fortigateLease(chain: AttributionChain): void {
  if (!chain.lease) throw new Error('the chain has no lease');
  chain.lease = {
    ...chain.lease,
    source_table: 'fg_dhcp',
    lease_seconds: 86_400,
    last_dhcp_evidence: FG_ACK.time,
    evidence: [FG_ACK, ...chain.lease.evidence.filter((ref) => ref.table !== 'mt_dhcp')],
  };
  chain.explanations = [
    ...chain.explanations,
    explanation(
      'fortigate_format_unverified',
      'FortiGate DHCP kayıt biçimi bu kurulumda henüz sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4); kira kaydı FortiOS belgelerindeki biçime göre okundu.',
      'The FortiGate DHCP record format has not yet been verified on site at this installation (DEVICE-SETUP.md section 2.4); the lease was read by the format of the FortiOS documentation.',
    ),
  ];
}

function lowered(chain: AttributionChain, item: Explanation): void {
  chain.confidence = 'low';
  if (chain.lease) chain.lease.confidence = 'low';
  chain.explanations = [...chain.explanations, item];
}

/** `srcmac_only` for the session records of `name` (2.11). */
function srcmacOnlyNote(name: string): Explanation {
  return explanation(
    'srcmac_only',
    `Bu zincirde kira kaydı yok. MAC adresi FortiGate oturum kaydının srcmac alanından alındı (${name} arayüzü): FortiGate'in aygıt tanıma bilgisine göre kaynak IP adresi o anda bu MAC adresiyle ilişkilendirilmişti. Bu kısmi bir kanıttır; MAC adresinin bu oturumu gönderdiğini ya da adresin bir DHCP kirasıyla verildiğini kanıtlamaz. Cihaz ve kişi bu MAC adresine göre düşük güvenle gösterilir.`,
    `This chain has no lease. The MAC address comes from the srcmac field of the FortiGate session record (interface ${name}): the FortiGate's device identification associated the source IP with this MAC address at that moment. This is partial evidence; it does not prove that this MAC sent the session or that a DHCP lease gave it the address. The device and the person are shown for this MAC address with low confidence.`,
    { interface: name },
  );
}

/**
 * A srcmac that differs from the lease's MAC (D10): the lease chain lowered with
 * `srcmac_mismatch` and, as its last alternative, the chain by the srcmac (no lease, no person).
 */
function srcmacMismatch(chain: AttributionChain, srcmac: string): void {
  const mac = chain.lease?.mac ?? '';
  const mismatch = explanation(
    'srcmac_mismatch',
    `FortiGate oturum kaydındaki srcmac (${srcmac}) kira kaydındaki MAC adresinden (${mac}) farklı: o anda bu IP adresini iki cihaz kullanmış olabilir. Zincir kira kaydına göre kuruldu, srcmac'e göre olası zincir de gösterildi; ikisi de düşük güvenlidir.`,
    `The srcmac of the FortiGate session record (${srcmac}) differs from the lease's MAC address (${mac}): two devices may have used this IP address at that moment. The chain follows the lease and the possible chain by srcmac is shown too; both have low confidence.`,
    { srcmac, mac },
  );
  lowered(chain, mismatch);
  const other = structuredClone(chain);
  other.lease = null;
  other.mac_source = 'srcmac';
  other.person = null;
  other.department = null;
  other.department_source = 'none';
  other.explanations = [mismatch];
  other.alternatives = [];
  if (other.device)
    other.device = { ...other.device, mac: srcmac, name: null, hostnames: [], confidence: 'low' };
  chain.alternatives = [...chain.alternatives, other];
}

export interface CapturedLookups {
  exact: LookupOut;
  candidates: LookupOut;
  conflict: LookupOut;
  internal: LookupOut;
  none: LookupOut;
}

/**
 * The Wave 3 lookups built from the captured ones (ids from 101 on, free in the mocks): a
 * FortiGate-only exact match and its chain at `low` for the three lease reasons, a srcmac-only
 * chain, a srcmac conflict, the `hat-2` candidates with an unconfigured sender, a quiet line's
 * fallback, the NAT address of the DMZ, an unclaimed address, a match without a chain with the
 * unconverted FortiGate DHCP record, candidates under the implicit catch-all of the shipped
 * `nat.egresses = {}`, an internal-mode srcmac chain, an IP conflict beside a srcmac conflict,
 * and the exact lookup as stored before ADR-0016.
 */
export function wave3Lookups(base: CapturedLookups) {
  const copy = (lookup: LookupOut, id: number) => withId(structuredClone(lookup), id);

  const fortigate = copy(base.exact, 101);
  {
    const outcome = outcomeOf(fortigate);
    const match = outcome.matches[0] as NatMatch;
    match.traffic = {
      ...match.traffic,
      srcintf: 'vlan42',
      dstintf: 'wan1',
      transip: '203.0.113.200',
    };
    match.egress = 'hat-1';
    outcome.egress = egressOf('public', [EXIT_HAT_1], { transips: [], interfaces: ['wan1'] });
    fortigateLease(match.chain as AttributionChain);
  }

  const renewal = copy(fortigate, 102);
  lowered(
    outcomeOf(renewal).matches[0]?.chain as AttributionChain,
    explanation(
      'lease_renewal_not_seen',
      'Bu kiranın istenen andan önceki son DHCP kaydı 09.10.2026 09:00:12 anında (kira süresi 86400 sn). İstenen an bundan kira süresinin yarısından fazla sonra ve arada bir yenileme kaydı yok. Adresi hâlâ tutan bir istemci kira süresinin yarısında yeniler; bu yenileme kayıtlarda yoksa ya yenileme kaydedilmedi ya da adresi o anda başka bir cihaz (ör. havuzda statik adresli bir cihaz) kullanıyordu. Kayıtlar hangisi olduğunu gösteremez; zincir bu nedenle düşük güvenle gösterildi.',
      'The last DHCP record of this lease before the requested moment is at 09.10.2026 09:00:12 (lease time 86400 s). The requested moment is more than half the lease time later, with no renewal record in between. A client that still holds the address renews at half the lease; if that renewal is not in the logs, either it was not logged or another device (e.g. a static device in the pool) used the address at that moment. The records cannot show which, so the chain is shown with low confidence.',
      { last: '09.10.2026 09:00:12', lease: 86_400 },
    ),
  );

  const noNetwork = copy(fortigate, 103);
  lowered(
    outcomeOf(noNetwork).matches[0]?.chain as AttributionChain,
    explanation(
      'fortigate_dhcp_no_network',
      "172.20.42.57 adresi tanımlı hiçbir ağda değil (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar). Bir istemcinin FortiGate'ten aldığı adresi reddedip (DHCPDECLINE, ör. adresi havuzda statik adresli bir cihaz kullanıyorsa) başka bir adres aldığı yalnızca tanımlı ağlarda anlaşılır; bu kira o anda adresi kullanan cihazı göstermiyor olabilir, zincir bu nedenle düşük güvenle gösterildi.",
      '172.20.42.57 lies in no configured network (Devices and people → Departments and VLANs). A client that declines an address the FortiGate gave it (DHCPDECLINE, e.g. because a static device in the pool uses it) and takes another is only detected in configured networks; this lease may not show the device that used the address at that moment, so the chain is shown with low confidence.',
      { ip: '172.20.42.57' },
    ),
  );

  const sinceLease = copy(fortigate, 104);
  lowered(
    outcomeOf(sinceLease).matches[0]?.chain as AttributionChain,
    explanation(
      'fortigate_dhcp_not_converted_since_lease',
      'Bu kiranın 09.10.2026 09:00:12 anındaki son DHCP kaydından sonra, istenen andan önce, 172.20.42.57 adresi ya da bu kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı FortiGate DHCP kayıtları var. Zincir bu nedenle düşük güvenle gösterildi.',
      "After this lease's last DHCP record at 09.10.2026 09:00:12 and before the requested moment there are FortiGate DHCP records for 172.20.42.57 or for this lease's MAC address that the lease timeline did not apply to this lease. The chain is shown with low confidence.",
      { last: '09.10.2026 09:00:12', ip: '172.20.42.57' },
    ),
  );

  const srcmacOnly = copy(base.exact, 105);
  {
    const outcome = outcomeOf(srcmacOnly);
    const match = outcome.matches[0] as NatMatch;
    match.traffic = {
      ...match.traffic,
      srcintf: 'vlan42',
      dstintf: 'wan1',
      srcmac: '00:00:5e:00:53:57',
    };
    const chain = match.chain as AttributionChain;
    chain.lease = null;
    chain.mac_source = 'srcmac';
    chain.confidence = 'low';
    if (chain.device) chain.device.confidence = 'low';
    if (chain.person) chain.person.confidence = 'low';
    chain.explanations = [srcmacOnlyNote('vlan42')];
  }

  const srcmacConflict = copy(base.exact, 106);
  {
    const outcome = outcomeOf(srcmacConflict);
    const match = outcome.matches[0] as NatMatch;
    match.traffic = {
      ...match.traffic,
      srcintf: 'vlan42',
      dstintf: 'wan1',
      srcmac: '00:00:5e:00:53:31',
    };
    srcmacMismatch(match.chain as AttributionChain, '00:00:5e:00:53:31');
  }

  const hat2 = copy(base.candidates, 107);
  {
    const outcome = outcomeOf(hat2);
    outcome.request = { ...outcome.request, public_ip: '198.51.100.77' };
    outcome.egress = egressOf('public', [EXIT_HAT_2], {
      transips: ['100.64.13.9'],
      interfaces: ['wan2'],
      interface_filter: true,
    });
    outcome.matches.forEach((match, index) => {
      match.egress = 'hat-2';
      match.traffic = {
        ...match.traffic,
        transip: '100.64.13.9',
        dstintf: 'wan2',
        srcintf: 'vlan42',
      };
      match.untrusted_sources = index === 2 ? ['unknown-198.51.100.7'] : [];
    });
    outcome.explanations = [
      ...outcome.explanations,
      explanation(
        'egress_public_dynamic',
        'hat-2 hattının genel adresi değişebilir olarak işaretli; 198.51.100.77 adresinin 09.10.2026 11:23:41 anında bu hatta ait olduğu yalnızca servis sağlayıcıdan doğrulanabilir.',
        'The public address of hat-2 is marked as changing; that 198.51.100.77 belonged to this line at 09.10.2026 11:23:41 can only be confirmed by the ISP.',
        { egress: 'hat-2', address: '198.51.100.77', at: '09.10.2026 11:23:41' },
      ),
      explanation(
        'session_from_unknown_source',
        '1 eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi (unknown-198.51.100.7); bu eşleşmeler ayrıca kendi satırlarında işaretlidir. Bu kayıtlar kurumun FortiGate\'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır (uyarı "yeni log kaynağı").',
        'The records of 1 match(es) came from a sender that is not configured (unknown-198.51.100.7); those matches are also marked on their own rows. Do not use them as evidence until it is confirmed that they came from the organisation\'s FortiGate (alert "new log source").',
        { count: 1, sources: 'unknown-198.51.100.7' },
      ),
    ];
  }
  queryFields(hat2, { public_ip: '198.51.100.77' });

  const fallbackDirect = copy(base.candidates, 108);
  outcomeOf(fallbackDirect).egress = egressOf('fallback', [EXIT_HAT_1], {
    unclaimed: true,
    outside_seen: ['100.64.12.9'],
    interfaces: ['wan1'],
    interface_filter: true,
  });
  queryFields(fallbackDirect, { public_ip: '203.0.113.200' });

  const fallbackTransip = copy(base.candidates, 109);
  outcomeOf(fallbackTransip).egress = egressOf('fallback', [{ ...EXIT_HAT_2, public: [] }], {
    transips: ['100.64.13.9'],
    unclaimed: true,
  });
  queryFields(fallbackTransip, { public_ip: '100.64.13.9' });

  const internalNat = copy(base.none, 110);
  {
    const outcome = outcomeOf(internalNat);
    outcome.egress = egressOf('transip', [EXIT_DMZ]);
    outcome.explanations = [
      explanation(
        'egress_internal',
        `172.20.99.1, dmz tanımına göre FortiGate'in iç ağlara (DMZ, başka bir VLAN, VPN ya da şube) yaptığı kaynak NAT'ın adresidir, bir internet çıkışı değildir; oturum aranmadı ve kimse listelenmedi. Adres bir internet hattına aitse tanımı ${EXITS_PATH_TR} bölümünde düzeltin.`,
        `172.20.99.1 is, by the definition dmz, the address of the FortiGate's source NAT towards internal networks (a DMZ, another VLAN, a VPN or a branch), not an internet exit; no session was searched and nobody was listed. If the address belongs to an internet line, correct the definition in ${EXITS_PATH_EN}.`,
        { address: '172.20.99.1', egress: 'dmz' },
      ),
    ];
  }
  queryFields(internalNat, { public_ip: '172.20.99.1' });

  const noEgress = copy(base.none, 111);
  {
    const outcome = outcomeOf(noEgress);
    outcome.egress = egressOf('no_egress', [EXIT_HAT_1, EXIT_HAT_2]);
    outcome.explanations = [
      explanation(
        'public_ip_no_egress',
        `192.0.2.44 hiçbir internet çıkışına tanımlı değil; kimse listelenmedi. Adres kurumun bir hattına aitse ${EXITS_PATH_TR} bölümünde tanımlayıp sorguyu yeniden çalıştırın.`,
        `192.0.2.44 belongs to no internet exit; nobody was listed. If it is one of the organisation's lines, define it in ${EXITS_PATH_EN} and run the lookup again.`,
        { address: '192.0.2.44' },
      ),
    ];
  }
  queryFields(noEgress, { public_ip: '192.0.2.44' });

  const chainMissing = copy(base.exact, 112);
  {
    const outcome = outcomeOf(chainMissing);
    const match = outcome.matches[0] as NatMatch;
    match.chain = null;
    match.egress = 'hat-1';
    match.traffic = { ...match.traffic, dstintf: 'wan1', srcintf: 'vlan42' };
    outcome.egress = egressOf('public', [EXIT_HAT_1]);
    outcome.unconverted_dhcp = [
      {
        table: 'fg_event',
        row_id: 3,
        time: '2026-10-09T06:00:12+00:00',
        raw: { source: 'fortigate', day: '2026-10-09', line: 3, offset: 610 },
        summary: 'DHCP Ack log 172.20.42.57',
      },
    ];
    outcome.explanations = [
      explanation(
        'chain_missing',
        '1 eşleşmede iç IP adresinden cihaza zincir kurulamadı: o anda bu adresler için kira kaydı yok. Bu eşleşmeler FortiGate oturumunda ve iç IP adresinde biter.',
        'For 1 match(es) no chain from the internal IP to a device could be built: no lease covered those addresses at that moment. These matches end at the FortiGate session and the internal IP address.',
        { count: 1 },
      ),
    ];
  }

  const unconfigured = copy(base.candidates, 114);
  {
    // The shipped `nat.egresses = {}`: every match under the implicit catch-all (name `""`).
    const outcome = outcomeOf(unconfigured);
    outcome.egress = egressOf('unconfigured', [EXIT_IMPLICIT], {
      unclaimed: true,
      unclaimed_seen: ['100.64.12.9'],
    });
    for (const match of outcome.matches) {
      match.egress = '';
      match.traffic = { ...match.traffic, dstintf: 'wan1', srcintf: 'vlan42' };
    }
  }

  const srcmacInternal = copy(base.internal, 115);
  {
    // Internal mode, the IP given, no lease, one usable srcmac (2.9): the chain without a session.
    const outcome = outcomeOf(srcmacInternal);
    const chain = outcome.attribution as AttributionChain;
    const sighting = chain.lease?.evidence.find((ref) => ref.table === 'fg_traffic');
    if (!sighting) throw new Error('the internal lookup cites no session record');
    chain.lease = null;
    chain.mac_source = 'srcmac';
    chain.confidence = 'low';
    if (chain.device) chain.device = { ...chain.device, confidence: 'low', evidence: [sighting] };
    chain.explanations = [...chain.explanations, srcmacOnlyNote('vlan60')];
    outcome.explanations = [
      ...outcome.explanations,
      explanation(
        'no_lease',
        `${chain.internal_ip} adresi için 09.10.2026 11:23:41 anında geçerli bir DHCP/ARP kaydı (kira) yok. En yakın kayıtlar: önce -, sonra -. DHCP ve saatlik ARP-LOG kayıtlarının ana yönlendiriciden (MikroTik, DHCP sunucusu) geldiğini kontrol edin; statik IP'li cihazlar yalnızca ARP-LOG ile görünür.`,
        `No DHCP/ARP lease covers ${chain.internal_ip} at 09.10.2026 11:23:41. Nearest leases: before -, after -. Check that the main router (MikroTik, the DHCP server) sends DHCP and hourly ARP-LOG messages; static-IP devices only appear through ARP-LOG.`,
        { ip: chain.internal_ip, at: '09.10.2026 11:23:41', before: '-', after: '-' },
      ),
    ];
  }

  const conflictSrcmac = copy(base.conflict, 116);
  {
    // Two leases on the IP (`ip_conflict`) and a srcmac that differs from the first one's MAC.
    const match = outcomeOf(conflictSrcmac).matches[0] as NatMatch;
    match.traffic = { ...match.traffic, srcintf: 'vlan42', srcmac: '00:00:5e:00:53:31' };
    srcmacMismatch(match.chain as AttributionChain, '00:00:5e:00:53:31');
  }

  const stored = copy(base.exact, 113);
  {
    // As stored before ADR-0016: none of its members (read with their 2.10 defaults).
    const outcome = outcomeOf(stored);
    delete outcome.egress;
    delete outcome.unconverted_dhcp;
    for (const match of outcome.matches) {
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
  }

  return {
    fortigate,
    renewal,
    noNetwork,
    sinceLease,
    srcmacOnly,
    srcmacConflict,
    hat2,
    fallbackDirect,
    fallbackTransip,
    internalNat,
    noEgress,
    chainMissing,
    unconfigured,
    srcmacInternal,
    conflictSrcmac,
    stored,
  };
}

/** Set fields of the stored query and of the outcome's request (they say the same). */
function queryFields(lookup: LookupOut, fields: { public_ip: string }): LookupOut {
  lookup.query.public_ip = fields.public_ip;
  const outcome = outcomeOf(lookup);
  outcome.request = { ...outcome.request, public_ip: fields.public_ip };
  return lookup;
}
