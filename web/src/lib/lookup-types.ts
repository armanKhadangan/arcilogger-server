/**
 * The JSON inside a stored 5651 lookup (`LookupOut.outcome`, `.query`, `ReportOut.timestamp`).
 *
 * The API returns these as plain objects (`dict[str, Any]` in OpenAPI): they are the
 * `to_dict()` forms of the worker's frozen dataclasses (`arcilogger_worker.lookup.types`,
 * docs/MVP-CONTRACTS.md section 5): UTC ISO times, integers only (scores 0-100, distances in
 * milliseconds). `api-samples.test.ts` checks every interface here against answers captured from
 * the real API (`web/scripts/capture_api_samples.py`), so a renamed or new field fails a test.
 */

import type { DateString, DateTimeString, LookupOut, ReportOut } from './api-types';

export type Confidence = 'high' | 'medium' | 'low';
export type LookupMode = 'exact' | 'candidates' | 'internal' | 'none';
export type MatchKind = 'exact' | 'candidate';
/**
 * How a match met the requested destination (`port`: only a port was requested). The weaker
 * kinds have their own labels: `ip_other_port` (requested IP, another port), `domain_other_port`,
 * `domain_address` (only the address is known for the name, from another client's logs) and
 * `domain_unverified` (exact NAT match of a domain-only request; no log ties it to the name).
 */
export type DestinationMatch =
  | 'ip_port'
  | 'ip'
  | 'ip_other_port'
  | 'domain'
  | 'domain_other_port'
  | 'domain_address'
  | 'domain_unverified'
  | 'port'
  | 'none'
  | 'not_requested';
export type DepartmentSource = 'person' | 'vlan' | 'none';
/**
 * Where a chain's MAC comes from (ADR-0016): `lease` — the lease timeline; `srcmac` — the srcmac
 * field of the FortiGate session record on an interface listed in `lookup.srcmac_interfaces`
 * (low confidence, partial evidence, never a lease).
 */
export type MacSource = 'lease' | 'srcmac';
/** The kind of an internet exit of `nat.egresses` (`internal`: a NAT address, no internet exit). */
export type EgressMode = 'direct' | 'isp_nat' | 'not_logged' | 'internal';
export type PortPreservation = 'unknown' | 'preserved' | 'not_preserved';
/** How a public-IP lookup resolved its address against the internet exits. */
export type EgressResolution =
  'public' | 'transip' | 'fallback' | 'observed' | 'public_unknown' | 'unconfigured' | 'no_egress';

/** Line `line` (1-based) of `spool/<source>/<day>.log` = the same line of the sealed file. */
export interface RawRef {
  source: string;
  day: DateString;
  line: number;
  offset: number | null;
}

/** A database row that supports a step, with the raw line it came from. */
export interface EvidenceRef {
  table: string;
  row_id: number | null;
  time: DateTimeString;
  raw: RawRef | null;
  summary: string;
}

/** A sentence for the result page and the PDF (placeholders already filled in both texts). */
export interface Explanation {
  key: string;
  text_tr: string;
  text_en: string;
  params: Record<string, string | number | null>;
}

export interface LeaseInfo {
  lease_id: number | null;
  ip: string;
  mac: string;
  vlan: number | null;
  hostname: string | null;
  start: DateTimeString;
  end: DateTimeString | null;
  /** `dhcp` | `timeout` | `superseded` | `arp` | `open`. */
  end_reason: string;
  confidence: Confidence;
  evidence: EvidenceRef[];
  /**
   * `mt_dhcp` | `fg_dhcp` | `mt_arp`. Optional, as the next two: `GET /lookup/{id}` returns the
   * outcome as stored, and lookups stored before ADR-0016 have none of the three (read `null`).
   */
  source_table?: string | null;
  /** The lease time the FortiGate logged (seconds; `fg_dhcp` leases only). */
  lease_seconds?: number | null;
  /** The lease's own newest DHCP record at or before the chain's moment. */
  last_dhcp_evidence?: DateTimeString | null;
}

export interface DeviceInfo {
  mac: string;
  name: string | null;
  hostnames: string[];
  vendor: string | null;
  randomized: boolean;
  type: string | null;
  infrastructure: boolean;
  confidence: Confidence;
  evidence: EvidenceRef[];
}

/**
 * Where the person step came from: `history` — the device's assignment valid at the requested
 * moment (`valid_from <= T < valid_to`); `current` — the device has no assignment history at all
 * (data written before the history existed): its owner at lookup time is named, with the
 * explanation `person_link_at_lookup_time`. A device with history but no assignment at T names
 * no person (`person_unassigned_at_time`), never the owner of another time (ADR-0008).
 */
export type PersonLink = 'history' | 'current';

export interface PersonInfo {
  person_id: number;
  name: string;
  department: string | null;
  title: string | null;
  confidence: Confidence;
  evidence: EvidenceRef[];
  /**
   * The assignment behind the person (devices and persons API, ADR-0008). Optional: lookups
   * stored before the assignment history have none of the three fields.
   */
  assigned_from?: DateTimeString | null;
  assigned_to?: DateTimeString | null;
  link?: PersonLink;
}

/**
 * `internal IP + T → lease (MAC) → device → person / department` (spec 9.4). With an IP conflict
 * the other chains are in `alternatives` (confidence `low`, explanation `ip_conflict`): no
 * single device or person may be named.
 */
export interface AttributionChain {
  internal_ip: string;
  at: DateTimeString;
  vlan: number | null;
  vlan_name: string | null;
  lease: LeaseInfo | null;
  device: DeviceInfo | null;
  person: PersonInfo | null;
  department: string | null;
  department_source: DepartmentSource;
  confidence: Confidence;
  explanations: Explanation[];
  alternatives: AttributionChain[];
  /** Optional: absent in lookups stored before ADR-0016 (read `lease`). */
  mac_source?: MacSource;
}

/** The FortiGate session behind a match (from `fg_traffic`). */
export interface TrafficInfo {
  sessionid: number | null;
  proto: number | null;
  srcip: string;
  srcport: number | null;
  dstip: string | null;
  dstport: number | null;
  transip: string | null;
  transport: number | null;
  start: DateTimeString;
  end: DateTimeString;
  evidence: EvidenceRef[];
  /** Optional, as the next two: absent in lookups stored before ADR-0016 (read `null`). */
  srcintf?: string | null;
  dstintf?: string | null;
  /** Only for interfaces listed in `lookup.srcmac_interfaces`. */
  srcmac?: string | null;
}

export interface NatMatch {
  kind: MatchKind;
  /** 0-100, higher is better (exact matches 100). */
  score: number;
  /** Distance between the requested time and the session's [start, end] (0 = inside). */
  time_distance_ms: number;
  destination_match: DestinationMatch;
  traffic: TrafficInfo;
  chain: AttributionChain | null;
  /**
   * The internet exit of the match (from its session row nearest the requested moment).
   * Optional, as `untrusted_sources`: absent in lookups stored before ADR-0016 (`null`, `[]`).
   */
  egress?: string | null;
  /** Sources of its rows that are not configured FortiGate senders (shown as a badge). */
  untrusted_sources?: string[];
}

/** One internet exit as the lookup used it (the normalised fields of `nat.egresses`). */
export interface EgressEntry {
  /** `""` for the implicit exit of a site without exits (`unconfigured`). */
  name: string;
  label: string;
  mode: EgressMode;
  public: string[];
  public_dynamic: boolean;
  transips: string[];
  interfaces: string[];
  port_preservation: PortPreservation;
}

/** What a public-IP lookup searched. */
export interface EgressSearched {
  transips: string[];
  /** The private or shared NAT addresses no exit claims were searched. */
  unclaimed: boolean;
  /** Unclaimed NAT addresses the logs showed near the moment (at most 5). */
  unclaimed_seen: string[];
  /** `fallback` only: the private or shared addresses that made the log check fail. */
  outside_seen: string[];
  interfaces: string[];
  interface_filter: boolean;
}

/** The last content change of an exit (`changed_by` as stored: `:migration`, `:seed`, …). */
export interface EgressChange {
  name: string;
  changed_at: DateTimeString;
  changed_by: string;
  deleted: boolean;
  after_event: boolean;
}

/** `LookupOutcome.egress`: how the requested public IP was resolved (ADR-0016). */
export interface LookupEgress {
  resolution: EgressResolution;
  egresses: EgressEntry[];
  searched: EgressSearched;
  changes: EgressChange[];
  changed_after_event: boolean;
}

/** Where the data of the requested day lives (the database keeps less than the vault). */
export interface DataWindow {
  requested_day: DateString;
  db_first_day: DateString | null;
  db_last_day: DateString | null;
  in_database: boolean;
  in_vault: boolean;
  only_in_vault: boolean;
}

/** `LookupRequest.to_dict()`: the normalised request (IPs canonical, protocol as a number). */
export interface LookupRequestData {
  at: DateTimeString;
  public_ip: string | null;
  public_port: number | null;
  protocol: number | null;
  dest_ip: string | null;
  dest_port: number | null;
  dest_domain: string | null;
  tolerance_seconds: number | null;
  internal_ip: string | null;
  mac: string | null;
}

/** How `at` was entered (the report prints it). */
export interface TimeInput {
  text: string;
  offset_given: boolean;
  time_zone: string | null;
}

/** `lookups.query`: the request plus `time_input`. */
export interface StoredQuery extends LookupRequestData {
  time_input?: TimeInput;
}

export interface LookupOutcome {
  request: LookupRequestData;
  mode: LookupMode;
  /** Best first (exact before candidates, then score, then time distance). */
  matches: NatMatch[];
  /**
   * Double NAT: the ISP router translates once more on the exit the public IP resolved to (an
   * `isp_nat` exit, a fallback or no exits configured; `egress`).
   */
  double_nat: boolean;
  /** For `internal` lookups: the chain of the requested internal IP / MAC. */
  attribution: AttributionChain | null;
  explanations: Explanation[];
  data_window: DataWindow | null;
  tolerance_seconds: number;
  generated_at: DateTimeString;
  /** More candidates existed than `lookup.max_candidates`. */
  truncated: boolean;
  /**
   * `null` without a public IP in the request. Optional, as `unconverted_dhcp`: absent in
   * lookups stored before ADR-0016 (`null`, `[]`).
   */
  egress?: LookupEgress | null;
  /** FortiGate DHCP records of the moment that never entered the lease timeline (cited). */
  unconverted_dhcp?: EvidenceRef[];
  result_count: number;
}

/** `ReportOut.timestamp`: the time stamp of the report's hash (keys depend on the outcome). */
export interface ReportTimestamp {
  provider?: string;
  legal_grade?: boolean;
  server?: string;
  serial?: string;
  gen_time?: string;
  policy?: string;
  root?: string;
  tsa_name?: string;
  token_file?: string;
  token_sha256?: string;
  verified?: boolean;
  errors?: string[];
  error?: string;
}

export function outcomeOf(lookup: LookupOut): LookupOutcome | null {
  return lookup.outcome as LookupOutcome | null;
}

export function queryOf(lookup: LookupOut): StoredQuery {
  return lookup.query as unknown as StoredQuery;
}

export function timestampOf(report: ReportOut): ReportTimestamp | null {
  return report.timestamp;
}

/** Every chain of an attribution: the most likely one first, then the conflict alternatives. */
export function chainsOf(chain: AttributionChain | null): AttributionChain[] {
  return chain ? [chain, ...chain.alternatives] : [];
}
