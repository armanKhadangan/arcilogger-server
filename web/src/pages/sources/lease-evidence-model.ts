/**
 * The "Kira kanıtı" card of Kaynaklar (contract 2.12 `GET /sources` `lease_evidence`, 2.13, D30):
 * whether the records the 5651 lookup's lease timeline rests on arrive and are used.
 *
 * Interim types: WP-D adds `lease_evidence` to `lib/sources-types.ts` with the endpoint; until
 * then they are declared here, exactly as 2.12 gives the JSON, and the overview is read through
 * `leaseEvidenceOf` (a server without the field shows no card).
 */

// Relative imports: the E2E mocks import this module, outside the `@/` alias.
import type { DateTimeString } from '../../lib/api-types';
import type { SourcesOverview } from '../../lib/sources-types';

/** Usable rows of one evidence table in the last 24 hours. */
export interface LeaseTableCounts {
  rows: number;
  /** The newest usable row within the last hour, `null` when none arrived in it. */
  last_hour: DateTimeString | null;
  /** Leases of this table open or ended in the last 24 hours. */
  leases_current: number;
}

export interface FortigateDhcpCounts extends LeaseTableCounts {
  /** Rows the lease job refuses (untrusted sender, unmapped logid, `dhcp_msg`, time, MAC). */
  refused_rows: number;
  /** Rows of excluded interfaces and IPv6 rows: configuration, never refusals. */
  excluded_rows: number;
  /** `dhcp.fortigate_format_verified` (D29). */
  format_verified: boolean;
}

export interface SrcmacCounts {
  /** `lookup.srcmac_interfaces`. */
  interfaces: string[];
  /** `fg_traffic` rows with srcmac on those interfaces in the last hour (`null`: not counted). */
  rows_last_hour: number | null;
}

export type LeaseTable = 'mt_dhcp' | 'fg_dhcp' | 'mt_arp';
/** `refused` wins over `none` (records arrive, so "make them arrive" would be the wrong fix). */
export type LeaseEvidenceState = 'ok' | 'none' | 'refused';

export interface LeaseEvidence {
  hours: number;
  mt_dhcp: LeaseTableCounts;
  fg_dhcp: FortigateDhcpCounts;
  mt_arp: LeaseTableCounts;
  srcmac: SrcmacCounts;
  state: LeaseEvidenceState;
  /** The tables of the active `R20-lease:none:<table>` causes; empty for `absent`. */
  none_tables: LeaseTable[];
}

/** The tables in the order the card lists them. */
export const LEASE_TABLES: readonly LeaseTable[] = ['mt_dhcp', 'fg_dhcp', 'mt_arp'];

/** `GET /sources` with `lease_evidence` (2.12), as long as the mirror type lacks it. */
export type SourcesOverviewWithLease = SourcesOverview & { lease_evidence?: LeaseEvidence | null };

export function leaseEvidenceOf(overview: SourcesOverview | undefined): LeaseEvidence | null {
  const answer: SourcesOverviewWithLease | undefined = overview;
  return answer?.lease_evidence ?? null;
}

/**
 * The lines of the `none` state: one per table of `none_tables` (`fg_dhcp` and `mt_arp` share a
 * text with the table's name, `mt_dhcp` has the hedged one: a quiet period looks the same), or
 * the `absent` line when the list is empty.
 */
export function noneLines(evidence: LeaseEvidence): { key: string; table: LeaseTable | null }[] {
  if (evidence.state !== 'none') return [];
  if (evidence.none_tables.length === 0) return [{ key: 'absent', table: null }];
  return LEASE_TABLES.filter((table) => evidence.none_tables.includes(table)).map((table) => ({
    key: table === 'mt_dhcp' ? 'noneMikrotikDhcp' : 'noneTable',
    table,
  }));
}

/** FortiGate DHCP rows arrive (used, refused or excluded) while the format is not verified. */
export function formatUnverified(evidence: LeaseEvidence): boolean {
  const fg = evidence.fg_dhcp;
  return !fg.format_verified && fg.rows + fg.refused_rows + fg.excluded_rows > 0;
}
