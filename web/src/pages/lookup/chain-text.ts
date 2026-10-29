/** Text helpers of the attribution chain view (no components: `chain-view.tsx` renders). */

import type { TFunction } from 'i18next';

import type { AlertTone } from '@/components/ui/alert';
import { endpoint, visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import type {
  AttributionChain,
  EgressEntry,
  LookupEgress,
  PersonInfo,
  TrafficInfo,
} from '@/lib/lookup-types';

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
  // ADR-0016 (contract 2.11): FortiGate DHCP, srcmac and the internet exits. `no_lease`,
  // `no_lease_for_mac`, `srcmac_agrees`, `srcmac_infrastructure`, `egress_search`,
  // `egress_unconfigured`, `fortigate_format_unverified`, `fortigate_format_verified` and
  // `source_configured_later` are information by design.
  chain_missing: 'warning',
  fortigate_dhcp_not_converted: 'warning',
  srcmac_only: 'warning',
  srcmac_mismatch: 'warning',
  srcmac_ambiguous: 'warning',
  srcmac_shared: 'warning',
  srcmac_other_lease: 'warning',
  egress_public_unknown: 'warning',
  public_ip_no_egress: 'warning',
  egress_not_logged: 'warning',
  egress_transip_not_seen: 'warning',
  egress_interface_not_seen: 'warning',
  egress_interface_not_seen_kept: 'warning',
  egress_unmapped_seen: 'warning',
  nat_unknown_trandisp: 'warning',
  lease_renewal_not_seen: 'warning',
  fortigate_dhcp_not_converted_since_lease: 'warning',
  fortigate_dhcp_no_network: 'warning',
  egress_observed_not_configured: 'warning',
  egress_changed_after_event: 'warning',
  session_changed_exit: 'warning',
  egress_not_logged_but_seen: 'warning',
  dhcp_sources_overlap: 'warning',
  session_from_unknown_source: 'warning',
  egress_public_dynamic: 'warning',
  egress_internal: 'warning',
  source_clock_offset: 'warning',
};

/**
 * Why a chain is `low` that the page shows beside its lease step (contract 2.13), not above the
 * chain: the lease's own record is the reason.
 */
export const LEASE_STEP_EXPLANATIONS: readonly string[] = [
  'lease_renewal_not_seen',
  'fortigate_dhcp_not_converted_since_lease',
  'fortigate_dhcp_no_network',
];

/**
 * The label of an exit resolution (contract 2.12, one table with the PDF's section-4 row and the
 * reading table): eight labels for the seven resolutions, `fallback` split by the exit whose
 * check failed (the first of `egresses`).
 */
export function resolutionLabelKey(egress: Pick<LookupEgress, 'resolution' | 'egresses'>): string {
  if (egress.resolution !== 'fallback') return `network.lookup.resolution.${egress.resolution}`;
  return egress.egresses[0]?.mode === 'direct'
    ? 'network.lookup.resolution.fallback_direct'
    : 'network.lookup.resolution.fallback_transip';
}

/** "hat-2 (Yedek hat), Servis sağlayıcı NAT'ı"; the implicit exit of no exits has no name. */
export function exitText(exit: EgressEntry, t: TFunction): string {
  const name = exit.label ? `${exit.name} (${visibleText(exit.label)})` : exit.name;
  return `${name}, ${t(`network.lookup.mode.${exit.mode}`)}`;
}

/**
 * The "Çıkış" line of a public-IP lookup: "Genel IP — hat-1 (Ana hat), Doğrudan". No exit names
 * for `unconfigured` (the implicit exit) and `no_egress` (the configured exits P is in none of).
 */
export function egressLine(egress: LookupEgress, t: TFunction): string {
  const label = t(resolutionLabelKey(egress));
  if (egress.resolution === 'unconfigured' || egress.resolution === 'no_egress') return label;
  const exits = egress.egresses.filter((exit) => exit.name !== '');
  return exits.length === 0
    ? label
    : `${label} — ${exits.map((exit) => exitText(exit, t)).join('; ')}`;
}

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
  // A srcmac chain is partial evidence, never a lease (as the chain view draws it).
  if (chain.mac_source === 'srcmac') {
    const mac = chain.device?.mac ?? traffic?.srcmac;
    if (mac) parts.push(`MAC ${mac} (${t('network.lookup.srcmacBadge')})`);
  } else if (chain.lease) {
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
