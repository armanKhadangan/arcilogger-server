/**
 * What Wave 6 adds to the security alerts API (`design/wave-6-contract.md` §2.6.2, §2.6.3, W6-A):
 * the assignee of an alert, its evidence rows and the summary's 30 days and top lists are in
 * `api-types.ts` (checked against the OpenAPI document); the person of a person-level rule's alert
 * (D3) joins `AlertDetail` once its API lands (#279).
 */

import type { AlertDetail } from './api-types';

export type {
  AlertAssignRequest,
  AlertEvidenceOut,
  AlertEvidencePage,
  AlertEvidenceRaw,
  AssigneeOut,
  DayCounts,
  KeyCount,
  RuleCount,
} from './api-types';

/** The person of a person-level rule's alert (R09, R13, R14), only for `usage.person` (D3). */
export interface AlertPerson {
  id: number;
  name: string;
}

export type AlertDetailWithRules = AlertDetail & { person?: AlertPerson | null };
