import type { TFunction } from 'i18next';

/** Tables an evidence reference can come from, with a readable label (`evidenceTables.*`). */
export const EVIDENCE_TABLES = [
  'fg_traffic',
  'fg_web',
  'fg_dns',
  'fg_app',
  'fg_dhcp',
  'fg_event',
  'mt_dhcp',
  'mt_arp',
];

/** "FortiGate oturum kaydı" for `fg_traffic`; unknown tables show their name. */
export function evidenceTableLabel(table: string, t: TFunction): string {
  return EVIDENCE_TABLES.includes(table) ? t(`evidenceTables.${table}`) : table;
}
