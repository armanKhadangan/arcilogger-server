/**
 * What Wave 6 adds to the security alerts API (`design/wave-6-contract.md` §2.6.2, §2.6.3, W6-A):
 * the assignee of an alert, its evidence rows, the person of a person-level rule's alert (D3) and
 * the summary's 30 days and top lists.
 *
 * Written from the contract while W6-A's API is on its way, so every addition to an existing
 * answer is optional here: the page works with the answers of today's API, and the fields join
 * `AlertOut`, `AlertDetail` and `AlertSummary` in `api-types.ts` (checked against the OpenAPI
 * document by `api-types.test.ts`) once the API has them.
 */

import type {
  AlertDetail,
  AlertOut,
  AlertSeverity,
  AlertSummary,
  DateString,
  DateTimeString,
} from './api-types';

/** The user an alert is assigned to (only users with `alerts.read`). */
export interface AlertAssignee {
  id: number;
  username: string;
}

/** The fields each alert of `GET /alerts` and `GET /alerts/{id}` gains. */
export interface AlertAdditions {
  assignee?: AlertAssignee | null;
  /** Rows behind the alert; at most `rules.evidence_max` of them are kept as evidence. */
  evidence_total?: number;
}

/** The person of a person-level rule's alert (R09, R13, R14), only for `usage.person` (D3). */
export interface AlertPerson {
  id: number;
  name: string;
}

export type AlertWithAssignment = AlertOut & AlertAdditions;
export type AlertDetailWithRules = AlertDetail & AlertAdditions & { person?: AlertPerson | null };

/** `POST /alerts/{id}/assign` (`alerts.manage`): a user id, or `null` for nobody. */
export interface AssignAlertBody {
  user_id: number | null;
}

/** The line of a spool file a row came from (`GET /raw`). */
export interface EvidenceRawRef {
  source: string;
  day: DateString;
  line: number;
  offset: number | null;
}

/** One row that caused the alert (`alert_evidence`), oldest first; never the line's content. */
export interface AlertEvidenceRow {
  n: number;
  /** The parsed table (`fg_traffic`, …) or `devices`. */
  tbl: string;
  row_id: number;
  /** Device time of the row. */
  time: DateTimeString;
  time_received: DateTimeString;
  source: string;
  /** `null` for a row without a raw line (a `devices` row). */
  raw: EvidenceRawRef | null;
}

/** `GET /alerts/{id}/evidence` (`alerts.read`). */
export interface AlertEvidence {
  alert_id: number;
  /** Rows behind the alert, also those beyond the kept evidence. */
  total: number;
  items: AlertEvidenceRow[];
}

/** One site day of the last 30: alerts opened that day, per severity. */
export type AlertsOfDay = { day: DateString } & Record<AlertSeverity, number>;

export interface TopRule {
  rule: string;
  count: number;
}

export interface TopKey {
  rule: string;
  /** The rule's group value: an address, a MAC, `user:<name>`; never a person's name. */
  key: string;
  count: number;
}

/** `GET /alerts/summary` gains the last 30 site days and the 10 largest of the last 7 days. */
export interface AlertSummaryAdditions {
  by_day?: AlertsOfDay[];
  top_rules?: TopRule[];
  top_keys?: TopKey[];
}

export type AlertSummaryWithRules = AlertSummary & AlertSummaryAdditions;
