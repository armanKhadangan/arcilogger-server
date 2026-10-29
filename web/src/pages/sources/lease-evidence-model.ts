/**
 * The "Kira kanıtı" card of Kaynaklar (contract 2.12 `GET /sources` `lease_evidence`, 2.13, D30):
 * whether the records the 5651 lookup's lease timeline rests on arrive and are used.
 */

// Relative imports: the E2E mocks import this module, outside the `@/` alias.
import type { LeaseEvidence, LeaseTable } from '../../lib/sources-types';

/** The tables in the order the card lists them. */
export const LEASE_TABLES: readonly LeaseTable[] = ['mt_dhcp', 'fg_dhcp', 'mt_arp'];

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
