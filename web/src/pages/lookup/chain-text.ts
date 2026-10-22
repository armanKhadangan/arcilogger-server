/** Text helpers of the attribution chain view (no components: `chain-view.tsx` renders). */

import type { TFunction } from 'i18next';

import type { AlertTone } from '@/components/ui/alert';
import { endpoint, visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import type { AttributionChain, PersonInfo, TrafficInfo } from '@/lib/lookup-types';

/** Explanation keys shown as warnings or errors; everything else is information. */
const EXPLANATION_TONE: Record<string, AlertTone> = {
  double_nat: 'warning',
  double_nat_no_candidates: 'warning',
  ip_conflict: 'danger',
  truncated: 'warning',
  only_in_vault: 'warning',
  not_in_database: 'warning',
  older_than_db_retention: 'warning',
  no_data_for_day: 'warning',
  lease_timeline_behind: 'warning',
  future_time: 'warning',
  port_hint: 'warning',
  public_ip_not_transip: 'warning',
  wan_transip_not_seen: 'warning',
  exact_domain_unverified: 'warning',
  exact_nat: 'success',
};

export function explanationTone(key: string): AlertTone {
  return EXPLANATION_TONE[key] ?? 'info';
}

/** "Selin Kaya / Planlama"; without a person "bağlı kişi yok · Departman Planlama". */
export function personText(chain: AttributionChain, t: TFunction): string {
  if (chain.person) {
    const department = chain.person.department ?? chain.department;
    return department ? `${chain.person.name} / ${department}` : chain.person.name;
  }
  return chain.department
    ? `${t('lookup.chain.noPerson')} · ${t('lookup.chain.department')} ${chain.department}`
    : t('lookup.chain.noPerson');
}

/**
 * The assignment behind the person step (devices and persons API): "Atama: 12.03.2026 09:00:00
 * itibarıyla" or "Atama: 12.03.2026 09:00:00 – 15.04.2026 17:30:00"; `null` when the lookup was
 * stored without it. A link to the current owner (`link: "current"`) keeps the engine's own
 * explanation (`person_link_at_lookup_time`), shown with the other explanations.
 */
export function assignmentText(person: PersonInfo, t: TFunction): string | null {
  const from = person.assigned_from ? formatDateTime(person.assigned_from) : null;
  const to = person.assigned_to ? formatDateTime(person.assigned_to) : null;
  if (from && to) return t('lookup.chain.assignedBetween', { from, to });
  if (from) return t('lookup.chain.assignedSince', { from });
  if (to) return t('lookup.chain.assignedUntil', { to });
  return null;
}

/**
 * The chain in one line, as in spec 12.3 page 6:
 * `WAN 100.64.12.9:51001 → 172.20.42.57:51001 → MAC 00:00:5e:… (PLN-PC14) → Cihaz "…" → Kişi … / …`.
 * Device-provided names are shown with invisible characters escaped.
 */
export function chainSummary(
  chain: AttributionChain,
  traffic: TrafficInfo | null,
  t: TFunction,
): string {
  const parts: string[] = [];
  if (traffic?.transip) parts.push(`WAN ${endpoint(traffic.transip, traffic.transport)}`);
  parts.push(endpoint(chain.internal_ip, traffic?.srcport ?? null));
  if (chain.lease) {
    const host = chain.lease.hostname ? ` (${visibleText(chain.lease.hostname)})` : '';
    parts.push(`MAC ${chain.lease.mac}${host}`);
  }
  if (chain.device?.name)
    parts.push(`${t('lookup.chain.device')} "${visibleText(chain.device.name)}"`);
  parts.push(
    chain.person
      ? `${t('lookup.chain.person')} ${visibleText(personText(chain, t))}`
      : visibleText(personText(chain, t)),
  );
  return parts.join(' → ');
}
