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
