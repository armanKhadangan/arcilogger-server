/**
 * What Wave 6 adds to the security alerts API (`design/wave-6-contract.md` §2.6.2, §2.6.3, W6-A):
 * the assignee of an alert, its evidence rows, the person of a person-level rule's alert (D3) and
 * the summary's 30 days and top lists, as `api-types.ts` has them (checked against the OpenAPI
 * document).
 */

export type {
  AlertAssignRequest,
  AlertEvidenceOut,
  AlertEvidencePage,
  AlertEvidenceRaw,
  AlertPersonOut,
  AssigneeOut,
  DayCounts,
  KeyCount,
  RuleCount,
} from './api-types';
