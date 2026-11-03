/**
 * The Panel's parse-failure box for the unit tests (contract 2.17), in the shape of
 * `GET /dashboard/summary` → `parse_failures`. The captured samples of a fresh test site have no
 * parse failures; this box has two patterns, one of them a line not in the collector's format.
 * Its example lines are captured raw lines (`e2e/samples/raw.json`).
 */

import type { DashboardParseFailures, DashboardSummary, SealBlocking } from '@/lib/api-types';

export const PARSE_FAILURES: DashboardParseFailures = {
  day: '2026-10-13',
  lines: 1240,
  failures: 31,
  permille: 25,
  by_source: [
    { source: 'fortigate', lines: 1000, failures: 30, permille: 30 },
    { source: 'core-r1', lines: 240, failures: 1, permille: 4.2 },
  ],
  top_patterns: [
    {
      source: 'fortigate',
      pattern: 'fortigate.kv',
      count: 30,
      example: { source: 'fortigate', day: '2026-10-12', line: 49, offset: 13392 },
    },
    {
      source: 'core-r1',
      pattern: 'rawlog.invalid',
      count: 1,
      example: { source: 'core-r1', day: '2026-10-09', line: 4, offset: 475 },
    },
  ],
  last7: [
    { day: '2026-10-07', lines: 0, failures: 0 },
    { day: '2026-10-08', lines: 0, failures: 0 },
    { day: '2026-10-09', lines: 34, failures: 2 },
    { day: '2026-10-10', lines: 0, failures: 0 },
    { day: '2026-10-11', lines: 0, failures: 0 },
    { day: '2026-10-12', lines: 820, failures: 6 },
    { day: '2026-10-13', lines: 1240, failures: 31 },
  ],
};

/**
 * `summary` with a known parse-failure box: the box where the summary has the ingest box (the
 * API shows both with the same permission), none otherwise.
 */
export function withParseFailures(
  summary: DashboardSummary,
  box: DashboardParseFailures | null = PARSE_FAILURES,
): DashboardSummary {
  return { ...summary, parse_failures: summary.ingest ? structuredClone(box) : null };
}

/**
 * The Panel's blocking day for the unit tests (Wave 5 contract 2.3.1 `DashboardSeal.blocking`):
 * the captured summaries have none (`blocking: null`), so it is hand-built: 10.10.2026, 49 hours
 * late (W5-C's own test point above the 48 h of `seal.late_after_hours`).
 */
export const BLOCKING: SealBlocking = { oldest_unstamped_day: '2026-10-10', hours_late: 49 };

/**
 * `summary` with a blocking day in its seal box (`blocking`, Wave 5 contract 2.3.1): where the
 * summary has the seal box (`seal.read`), none otherwise.
 */
export function withBlocking(
  summary: DashboardSummary,
  blocking: SealBlocking | null = BLOCKING,
): DashboardSummary {
  if (!summary.seal) return summary;
  return {
    ...summary,
    seal: { ...summary.seal, blocking: structuredClone(blocking) },
  };
}
