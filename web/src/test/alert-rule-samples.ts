/**
 * What Wave 6 adds to the alerts API (contract §2.6.2, §2.6.3), written from the contract until
 * W6-A's API is captured: two rule alerts beside the captured ones (R01 with its evidence, R13 of
 * a person), the evidence of R01 on raw lines the captured `/raw` answers hold
 * (`e2e/samples/raw.json`, `fortigate` 01.09.2026), and the summary's 30 days and top lists.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-alerts.ts`), so it imports nothing with
 * the `@/` alias and reads no file.
 */

import type {
  AlertDetailWithRules,
  AlertEvidence,
  AlertsOfDay,
  AlertSummaryAdditions,
} from '../lib/alert-rule-types';

const TIME = '2026-10-13T08:41:07Z';

/** R01, open, nobody assigned; 47 rows behind it, the first three kept here. */
export const RULE_ALERT: AlertDetailWithRules = {
  id: 9001,
  time: TIME,
  created_at: '2026-10-13T08:43:02Z',
  updated_at: '2026-10-13T08:43:02Z',
  resolved_at: null,
  rule: 'R01',
  rule_name_tr: 'İçeriden port taraması',
  rule_name_en: 'Internal port scan',
  severity: 'high',
  state: 'open',
  title: 'İç adres 172.20.41.17 47 farklı porta / 3 adrese engellendi',
  summary_tr: 'İç adres 172.20.41.17 47 farklı porta / 3 adrese engellendi',
  summary_en: 'Internal address 172.20.41.17 was blocked on 47 ports / 3 addresses',
  auto_resolved: false,
  notes_count: 0,
  details: {
    title_en: 'Internal address 172.20.41.17 was blocked on 47 ports / 3 addresses',
    rule_kind: 'event',
    key: '172.20.41.17',
    count: 47,
    values: { ports: 47, hosts: 3, window_seconds: 60 },
    first_time: '2026-10-13T08:40:11Z',
    last_time: TIME,
    actions: { email: true, webhook: true },
    person_id: null,
    format_verified: true,
    evidence_total: 47,
  },
  events: [
    {
      id: 90001,
      kind: 'state',
      from_state: null,
      to_state: 'open',
      text: null,
      by: 'system',
      at: '2026-10-13T08:43:02Z',
    },
  ],
  runbook_tr:
    'Adresin hangi cihaza ait olduğunu Cihazlar sayfasında bulun. Cihaz tanınmıyorsa ağdan ayırıp inceleyin.',
  runbook_en:
    'Find the device of the address on the Devices page. If the device is unknown, take it off the network and examine it.',
  deliveries: [],
  assignee: null,
  evidence_total: 47,
};

/** R13 (person level): the person of the address, sent only to `usage.person` (D3). */
export const PERSON_ALERT: AlertDetailWithRules = {
  ...RULE_ALERT,
  id: 9002,
  time: '2026-10-13T07:12:40Z',
  created_at: '2026-10-13T07:14:02Z',
  updated_at: '2026-10-13T07:14:02Z',
  rule: 'R13',
  rule_name_tr: 'Büyük veri gönderimi',
  rule_name_en: 'Large upload',
  severity: 'medium',
  title: '172.20.30.41 bir saatte 1,4 GB gönderdi',
  summary_tr: '172.20.30.41 bir saatte 1,4 GB gönderdi',
  summary_en: '172.20.30.41 sent 1.4 GB in one hour',
  details: {
    title_en: '172.20.30.41 sent 1.4 GB in one hour',
    rule_kind: 'event',
    key: 'person:12',
    count: 1,
    values: { hour_bytes: 1_503_238_554 },
    person_id: 12,
    format_verified: true,
    evidence_total: 0,
  },
  events: [
    {
      id: 90002,
      kind: 'state',
      from_state: null,
      to_state: 'open',
      text: null,
      by: 'system',
      at: '2026-10-13T07:14:02Z',
    },
  ],
  evidence_total: 0,
  person: { id: 12, name: 'Deniz Örnek' },
};

export const RULE_EVIDENCE: AlertEvidence = {
  alert_id: 9001,
  total: 47,
  items: [
    {
      n: 1,
      tbl: 'fg_traffic',
      row_id: 880412,
      time: '2026-09-01T07:42:18Z',
      time_received: '2026-09-01T07:42:18Z',
      source: 'fortigate',
      raw: { source: 'fortigate', day: '2026-09-01', line: 1, offset: 0 },
    },
    {
      n: 2,
      tbl: 'fg_traffic',
      row_id: 880413,
      time: '2026-09-01T19:42:18Z',
      time_received: '2026-09-01T19:42:18Z',
      source: 'fortigate',
      raw: { source: 'fortigate', day: '2026-09-01', line: 2, offset: 411 },
    },
    {
      n: 3,
      tbl: 'fg_traffic',
      row_id: 880409,
      time: '2026-09-01T06:37:52Z',
      time_received: '2026-09-01T06:37:52Z',
      source: 'fortigate',
      raw: { source: 'fortigate', day: '2026-09-01', line: 3, offset: 822 },
    },
  ],
};

function day(offset: number): AlertsOfDay {
  const date = new Date(Date.UTC(2026, 9, 13 - offset));
  const iso = date.toISOString().slice(0, 10);
  // A fixed, varied pattern: a few medium and low alerts, a high one every fifth day.
  return {
    day: iso,
    critical: offset === 3 ? 1 : 0,
    high: offset % 5 === 0 ? 2 : 0,
    medium: (offset * 7) % 4,
    low: (offset * 3) % 3,
    info: offset % 2,
  };
}

export const SUMMARY_ADDITIONS: Required<AlertSummaryAdditions> = {
  by_day: Array.from({ length: 30 }, (_, index) => day(29 - index)),
  top_rules: [
    { rule: 'R01', count: 6 },
    { rule: 'R13', count: 3 },
    { rule: 'R15', count: 2 },
  ],
  top_keys: [
    { rule: 'R01', key: '172.20.41.17', count: 4 },
    { rule: 'R13', key: 'ip:172.20.30.41', count: 2 },
  ],
};
