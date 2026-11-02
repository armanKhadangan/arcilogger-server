/**
 * The API answers captured from the real ARCILOGGER API (`web/e2e/samples/*.json`, written by
 * `web/scripts/capture_api_samples.py`) for unit tests. The E2E tests load the same files through
 * `e2e/samples.ts`; `src/lib/api-samples.test.ts` checks them against the TypeScript types.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type {
  AlertDetail,
  AlertPage,
  AlertSummary,
  ApiErrorBody,
  Calendar,
  CompanyUsage,
  DashboardSummary,
  DepartmentOut,
  DeviceDetail,
  DevicePage,
  EvidenceCreated,
  EvidenceDetail,
  ImportResult,
  LicenseOut,
  LookupOut,
  PersonDetail,
  PersonPage,
  RawLineOut,
  RunOut,
  RunPage,
  ScheduleOut,
  VlanOut,
} from '@/lib/api-types';
import type { LiveStatus, LiveStreamOut } from '@/lib/live-types';
import type {
  FieldCatalogue,
  ParseResult,
  SearchEvent,
  SearchPage,
  SearchRefs,
  SearchStats,
} from '@/lib/search-types';
import type { SealDayDetail } from '@/lib/seal-types';
import type {
  AdminAccepted,
  AdminCheck,
  AdminConfig,
  AdminRequestOut,
  AdminStatus,
} from '@/lib/settings-types';
import type { SourceDetail, SourcesOverview, SourceTail } from '@/lib/sources-types';

import {
  viewerForm,
  type LicenseDocumentName,
  type LicenseErrorName,
  type LicenseErrorSample,
  type LicenseSamples,
} from './license-samples';

function load(name: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), 'e2e', 'samples', `${name}.json`), 'utf8'));
}

export interface ErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

export const LOOKUPS = load('lookups') as Record<
  | 'exact'
  | 'candidates'
  | 'conflict'
  | 'internal'
  | 'none'
  | 'vault_only'
  | 'truncated'
  | 'auditor_run'
  | 'exact_with_report'
  | 'candidates_with_report',
  LookupOut
> & { errors: Record<string, ErrorSample> };

export const RAW = load('raw') as {
  lines: Record<string, RawLineOut>;
  odd_line: string;
  errors: Record<string, ErrorSample>;
};

export const EVIDENCE = load('evidence') as {
  created: EvidenceCreated;
  created_password: EvidenceCreated;
  details: Record<string, EvidenceDetail>;
  ready_expired: EvidenceDetail;
  errors: Record<string, ErrorSample>;
};

export const SEALS = load('seals') as {
  meta: { site_today: string };
  calendars: Record<string, Calendar>;
  days: Record<string, SealDayDetail>;
  /** "Şimdi mühürle": the 202 answer and the request queued and done. */
  seal_now: AdminAccepted;
  seal_now_queued: AdminRequestOut;
  seal_now_done: AdminRequestOut;
  errors: Record<string, ErrorSample>;
};

/** Browser administration (contract 2.11): the configuration, the status, checks and requests. */
export const ADMIN = load('admin') as {
  config: AdminConfig;
  status: AdminStatus;
  check_general: AdminCheck;
  request_active: AdminRequestOut;
  sources: { check_add: AdminCheck; check_update: AdminCheck; remove_accepted: AdminAccepted };
  sections: { check_smtp: AdminCheck; smtp_accepted: AdminAccepted; smtp_done: AdminRequestOut };
  errors: Record<string, ErrorSample>;
};

/** Devices, persons, departments, VLANs and CSV imports and exports. */
export const DEVICES = load('devices') as {
  list: DevicePage;
  list_unknown: DevicePage;
  detail: DeviceDetail;
  detail_phone: DeviceDetail;
  persons: PersonPage;
  person_detail: PersonDetail;
  departments: DepartmentOut[];
  vlans: VlanOut[];
  vlan_created: VlanOut;
  vlan_edited: VlanOut;
  import_persons_preview: ImportResult;
  import_persons_errors: ImportResult;
  import_assignments_preview: ImportResult;
  import_assignments_applied: ImportResult;
  /** A CSV export as the API sent it (headers and text). */
  devices_csv: { headers: Record<string, string>; text: string };
  errors: Record<string, ErrorSample>;
};

/** Security alerts: lists, details with history, workflow changes and refusals. */
export const ALERTS = load('alerts') as {
  list: AlertPage;
  detail: AlertDetail;
  detail_auto_resolved: AlertDetail;
  detail_dismissed: AlertDetail;
  detail_seal: AlertDetail;
  state_acknowledged: AlertDetail;
  state_reopened: AlertDetail;
  state_resolved: AlertDetail;
  summary: AlertSummary;
  errors: Record<string, ErrorSample>;
};

/** `GET /dashboard/summary` per role (blocks the role may not see are `null`). */
export const DASHBOARD = load('dashboard') as {
  admin: DashboardSummary;
  auditor: DashboardSummary;
  viewer: DashboardSummary;
};

/** Company usage (`GET /usage/company`): the sample day 12.10.2026. */
export const USAGE = load('usage') as { company: CompanyUsage };

/** Scheduled reports and their runs (captured). */
export const REPORTS = load('reports') as {
  schedules: ScheduleOut[];
  runs: RunPage;
  run_partial: RunOut;
  errors: Record<string, ErrorSample>;
};

/** Arama (`POST /search` and its pages, statistics, parse, catalogue, records, references). */
export const SEARCH = load('search') as {
  search: Record<
    'typed' | 'text' | 'first_page' | 'next_page' | 'empty' | 'partial' | 'auditor',
    SearchPage
  >;
  stats: Record<'rollup' | 'hybrid' | 'direct' | 'partial' | 'auditor', SearchStats>;
  parse: Record<'typed' | 'text' | 'refused' | 'unknown_field', ParseResult>;
  fields: FieldCatalogue;
  event: Record<'traffic' | 'dhcp' | 'message', SearchEvent>;
  refs: SearchRefs;
  errors: Record<string, ErrorSample>;
};

/** One server-sent event of a captured live stream (`{event, id, data}`). */
export interface LiveEventSample {
  event: string;
  id: string | null;
  data: unknown;
}

/** Canlı Akış: opened streams, the hub's status and four whole captured streams. */
export const LIVE = load('live') as {
  open: { admin: LiveStreamOut; auditor: LiveStreamOut };
  status: LiveStatus;
  events: Record<'stream' | 'dropped' | 'gap' | 'end', LiveEventSample[]>;
  errors: Record<string, ErrorSample>;
};

/** Kaynaklar: the overview, details, last lines and the Panel with today's parse failures. */
export const SOURCES = load('sources') as {
  overview: SourcesOverview;
  detail: Record<string, SourceDetail>;
  tail: { 'core-r1': SourceTail; no_file_today: SourceTail };
  dashboard: DashboardSummary;
  errors: Record<string, ErrorSample>;
};

/** The licence (`GET/POST/DELETE /license`, the locked modules, the users limit); see
 * `license-samples.ts` for the captured sites' stories. */
export const LICENSE = load('license') as LicenseSamples;

/**
 * A copy of a captured licence document; `viewer`: as a user without `settings.manage` gets it
 * (`details: null`).
 */
export function licenseSample(
  name: LicenseDocumentName,
  { viewer = false }: { viewer?: boolean } = {},
): LicenseOut {
  return viewer ? viewerForm(LICENSE[name]) : structuredClone(LICENSE[name]);
}

/** A copy of a captured licence error answer (`{status, headers, body}`). */
export function licenseError(name: LicenseErrorName): LicenseErrorSample {
  return structuredClone(LICENSE[name]);
}

/** This server (`fixed_identity()`), its installation and request code, the main licence. */
export const LICENSE_SERVER_ID = need(LICENSE.trial.details?.server_id, 'license server id');
export const LICENSE_INSTALLATION_ID = need(
  LICENSE.trial.details?.installation_id,
  'license installation id',
);
export const LICENSE_REQUEST_CODE = need(LICENSE.trial.details?.request_code, 'request code');
export const LICENSE_ID = need(LICENSE.licensed.details?.license_id, 'licence id');

/** The sample, or a test failure naming the missing key. */
export function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`sample missing: ${what}`);
  return value;
}
