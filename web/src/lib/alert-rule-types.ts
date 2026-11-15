/**
 * What Wave 6 adds to the security alerts API (`design/wave-6-contract.md` §2.6.2, §2.6.3, W6-A):
 * the assignee of an alert, its evidence rows, the person of a person-level rule's alert (D3) and
 * the summary's 30 days and top lists.
 *
 * Names and fields as W6-A's API has them (its branch `w6-a-api-243`); every addition to an existing
 * answer is optional here: the page works with the answers of today's API, and the fields join
 * `AlertOut`, `AlertDetail` and `AlertSummary` in `api-types.ts` (checked against the OpenAPI
 * document by `api-types.test.ts`) once the API has them.
 */

import type { AlertDetail, AlertOut, AlertSummary, DateString, DateTimeString } from './api-types';

/** The user an alert is assigned to (only users with `alerts.read`). */
export interface AssigneeOut {
  id: number;
  username: string;
}

/** The fields each alert of `GET /alerts` and `GET /alerts/{id}` gains. */
export interface AlertAdditions {
  assignee?: AssigneeOut | null;
  /**
   * A rule's alert: the most rows any one window of it held (C1: the largest window count, not
   * a sum); at most `rules.evidence_max` rows are kept as evidence. `null` for a check's alert.
   */
  evidence_total?: number | null;
}

/** The person of a person-level rule's alert (R09, R13, R14), only for `usage.person` (D3). */
export interface AlertPerson {
  id: number;
  name: string;
}

export type AlertWithAssignment = AlertOut & AlertAdditions;
export type AlertDetailWithRules = AlertDetail & AlertAdditions & { person?: AlertPerson | null };

/** `POST /alerts/{id}/assign` (`alerts.manage`): a user id, or `null` for nobody. */
export interface AlertAssignRequest {
  user_id: number | null;
}

/** The line of a spool file a row came from (`GET /raw`). */
export interface AlertEvidenceRaw {
  source: string;
  day: DateString;
  line: number;
  offset: number;
}

/** One row that caused the alert (`alert_evidence`), oldest first; never the line's content. */
export interface AlertEvidenceOut {
  n: number;
  /** The parsed table (`fg_traffic`, …) or `devices`. */
  table: string;
  row_id: number | null;
  /** Device time of the row. */
  time: DateTimeString | null;
  time_received: DateTimeString | null;
  /** `null` for a row without a raw line (a `devices` row). */
  raw: AlertEvidenceRaw | null;
}

/** `GET /alerts/{id}/evidence` (`alerts.read`). */
export interface AlertEvidencePage {
  alert_id: number;
  /** As `AlertOut.evidence_total`; `items` holds at most `rules.evidence_max`. */
  evidence_total: number | null;
  items: AlertEvidenceOut[];
}

/** One site day of the last 30: alerts opened that day, per severity. */
export interface DayCounts {
  day: DateString;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface RuleCount {
  rule: string;
  count: number;
}

export interface KeyCount {
  rule: string;
  /** The rule's group value: an address, a MAC, `user:<name>`; never a person's name. */
  key: string;
  count: number;
}

/** `GET /alerts/summary` gains the last 30 site days and the 10 largest of the last 7 days. */
export interface AlertSummaryAdditions {
  by_day?: DayCounts[];
  top_rules?: RuleCount[];
  top_keys?: KeyCount[];
}

export type AlertSummaryWithRules = AlertSummary & AlertSummaryAdditions;
