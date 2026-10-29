/**
 * Answers captured from the REAL ARCILOGGER API (`web/scripts/capture_api_samples.py`, which
 * runs the production code against a throwaway database and a mock time-stamp authority). The
 * mocked API of the E2E tests serves these instead of hand-written guesses; the capture script's
 * `--check` and `src/lib/api-samples.test.ts` keep them in line with the API and the types.
 */

import { readFileSync } from 'node:fs';

import type {
  AlertDetail,
  AlertEvent,
  AlertPage,
  AlertSummary,
  ApiErrorBody,
  AssignResult,
  Calendar,
  CategoryDetail,
  CategoryList,
  CompanyUsage,
  Credits,
  DashboardSummary,
  DepartmentOut,
  DepartmentPage,
  DeviceDetail,
  DeviceOut,
  DevicePage,
  EvidenceCreated,
  EvidenceDetail,
  EvidencePage,
  ImportResult,
  LookupOut,
  LookupPage,
  PersonDetail,
  PersonOut,
  PersonPage,
  PseudonymRow,
  RawLineOut,
  RebuildJob,
  RunOut,
  RunPage,
  ScheduleOut,
  SiteDetail,
  SitePage,
  SubjectDetail,
  SubjectPage,
  VerifyResult,
  VlanOut,
} from '../src/lib/api-types';
import type { ComplianceStatus, ConfigBackupOut } from '../src/lib/compliance-types';
import type { LiveStatus, LiveStreamOut } from '../src/lib/live-types';
import type {
  FieldCatalogue,
  ParseResult,
  SavedSearch,
  SavedSearchList,
  SearchEvent,
  SearchPage,
  SearchRefs,
  SearchStats,
} from '../src/lib/search-types';
import type { SealDayDetail } from '../src/lib/seal-types';
import type {
  AdminAccepted,
  AdminCheck,
  AdminConfig,
  AdminRequestOut,
  AdminRequestPage,
  AdminStatus,
  ConfigVersion,
  SettingsOut,
  SettingsPatchResult,
  UploadPreview,
} from '../src/lib/settings-types';
import type { SourceDetail, SourcesOverview, SourceTail } from '../src/lib/sources-types';
import type { SystemHealth } from '../src/lib/system-health-types';
import type { SystemDisk, SystemSummary } from '../src/lib/system-types';
import type { LicenseSamples } from '../src/test/license-samples';

function load(name: string): unknown {
  return JSON.parse(readFileSync(new URL(`./samples/${name}.json`, import.meta.url), 'utf8'));
}

export interface ErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

export interface FileSample {
  headers: Record<string, string>;
  size: number;
  sha256: string;
}

export interface SampleMeta {
  captured_at: string;
  /** The capture's clock (`capture_api_samples.CLOCK`), not the machine's date. */
  clock: string;
  site_today: string;
  site_day: string;
}

export const LOOKUPS = load('lookups') as {
  meta: SampleMeta;
  exact: LookupOut;
  candidates: LookupOut;
  conflict: LookupOut;
  internal: LookupOut;
  none: LookupOut;
  vault_only: LookupOut;
  truncated: LookupOut;
  auditor_run: LookupOut;
  exact_with_report: LookupOut;
  candidates_with_report: LookupOut;
  list_admin: LookupPage;
  list_admin_mine: LookupPage;
  list_auditor: LookupPage;
  report_pdf: FileSample;
  report_pdf_stamped: FileSample;
  report_token: FileSample;
  errors: Record<string, ErrorSample>;
};

export const RAW = load('raw') as {
  lines: Record<string, RawLineOut>;
  odd_line: string;
  errors: Record<string, ErrorSample>;
};

export const EVIDENCE = load('evidence') as {
  created: EvidenceCreated;
  created_password: EvidenceCreated;
  created_queued: EvidenceCreated;
  list: EvidencePage;
  list_auditor: EvidencePage;
  details: Record<string, EvidenceDetail>;
  download: FileSample;
  /** A package whose expiry passed before the nightly purge marked it (`ready`, not downloadable). */
  ready_expired: EvidenceDetail;
  verify_tampered: VerifyResult;
  verify_restored: VerifyResult;
  status_labels: Record<string, { tr: string; en: string }>;
  errors: Record<string, ErrorSample>;
};

export const SEALS = load('seals') as {
  meta: SampleMeta;
  calendars: Record<string, Calendar>;
  calendar_current: Calendar;
  days: Record<string, SealDayDetail>;
  verify: VerifyResult;
  verify_quick: VerifyResult;
  manifest_file: FileSample & { text: string };
  token_file: FileSample & { base64: string };
  /** "Şimdi mühürle": the 202 answer and the request queued and done. */
  seal_now: AdminAccepted;
  seal_now_queued: AdminRequestOut;
  seal_now_done: AdminRequestOut;
  errors: Record<string, ErrorSample>;
};

export const CREDITS = load('credits') as {
  mock: Credits;
  kamusm: Credits;
  kamusm_low: Credits;
  /** "Kontör sorgula": the 202 answer, the request done, the second query within a minute. */
  query: AdminAccepted;
  query_done: AdminRequestOut;
  query_limited: ErrorSample;
};

/**
 * Browser administration (contract 2.11, captured with the real administration agent and its
 * tasks on a throwaway installation, `FakeAgent(system=True)`): the configuration as installed,
 * after the Kamu SM steps and after the other sections were changed, the status in each state of
 * the agent, every answer of a change and the history.
 */
export const ADMIN = load('admin') as {
  meta: SampleMeta;
  config: AdminConfig;
  status: AdminStatus;
  status_after: AdminStatus;
  status_agent_missing: AdminStatus;
  status_disabled: AdminStatus;
  check_general: AdminCheck;
  check_clock: AdminCheck;
  accepted: AdminAccepted;
  request_queued: AdminRequestOut;
  request_active: AdminRequestOut;
  requests: AdminRequestPage;
  kamusm: {
    config: AdminConfig;
    check_credentials: AdminCheck;
    credentials_accepted: AdminAccepted;
    credentials_active: AdminRequestOut;
    upload_root: UploadPreview;
    root_accepted: AdminAccepted;
    root_active: AdminRequestOut;
    upload_zamane: UploadPreview;
    zamane_accepted: AdminAccepted;
    zamane_pending: AdminRequestOut;
    tsa_test_accepted: AdminAccepted;
    tsa_test_done: AdminRequestOut;
  };
  sources: {
    check_add: AdminCheck;
    check_update: AdminCheck;
    add_accepted: AdminAccepted;
    add_applied: AdminRequestOut;
    add_active: AdminRequestOut;
    remove_accepted: AdminAccepted;
    remove_active: AdminRequestOut;
  };
  sections: {
    config: AdminConfig;
    check_smtp: AdminCheck;
    smtp_accepted: AdminAccepted;
    smtp_done: AdminRequestOut;
    upload_smtp_ca: UploadPreview;
    smtp_ca_accepted: AdminAccepted;
    smtp_ca_done: AdminRequestOut;
    test_email_accepted: AdminAccepted;
    test_email_done: AdminRequestOut;
    check_replica: AdminCheck;
    replica_accepted: AdminAccepted;
    replica_done: AdminRequestOut;
    replica_test_accepted: AdminAccepted;
    replica_test_done: AdminRequestOut;
    check_retention: AdminCheck;
    retention_accepted: AdminAccepted;
    retention_done: AdminRequestOut;
    check_backup: AdminCheck;
    backup_accepted: AdminAccepted;
    backup_done: AdminRequestOut;
    lease_rebuild_accepted: AdminAccepted;
    lease_rebuild_done: AdminRequestOut;
  };
  history: {
    held_accepted: AdminAccepted;
    held_pending: AdminRequestOut;
    cancel_held_accepted: AdminAccepted;
    cancel_held_done: AdminRequestOut;
    held_cancelled: AdminRequestOut;
    cancel_queued: AdminAccepted;
    cancelled_queued: AdminRequestOut;
    revert_accepted: AdminAccepted;
    revert_done: AdminRequestOut;
    requests: AdminRequestPage;
    versions: ConfigVersion[];
  };
  errors: Record<string, ErrorSample>;
};

/** The database settings (contract 2.13): every group, a change, a reset and the refusals. */
export const SETTINGS = load('settings') as {
  meta: SampleMeta;
  list: SettingsOut;
  list_changed: SettingsOut;
  patch: SettingsPatchResult;
  reset: SettingsPatchResult;
  errors: Record<string, ErrorSample>;
};

/** A CSV file the API sent (export, template): headers and the text as received. */
export interface CsvSample {
  headers: Record<string, string>;
  size: number;
  sha256: string;
  text: string;
}

/** Devices, persons, departments, VLANs, assignments and CSV imports and exports. */
export const DEVICES = load('devices') as {
  meta: SampleMeta;
  list: DevicePage;
  list_unknown: DevicePage;
  list_search: DevicePage;
  list_page: DevicePage;
  detail: DeviceDetail;
  detail_phone: DeviceDetail;
  device_updated: DeviceOut;
  assigned: AssignResult;
  reassigned: AssignResult;
  unassigned: AssignResult;
  persons: PersonPage;
  persons_search: PersonPage;
  person_detail: PersonDetail;
  person_created: PersonOut;
  person_updated: PersonOut;
  departments: DepartmentOut[];
  department_created: DepartmentOut;
  department_updated: DepartmentOut;
  vlans: VlanOut[];
  vlan_updated: VlanOut;
  vlan_created: VlanOut;
  vlan_edited: VlanOut;
  import_persons_preview: ImportResult;
  import_persons_errors: ImportResult;
  import_assignments_preview: ImportResult;
  import_assignments_errors: ImportResult;
  import_assignments_applied: ImportResult;
  devices_csv: CsvSample;
  persons_csv: CsvSample;
  template_persons: CsvSample;
  template_assignments: CsvSample;
  errors: Record<string, ErrorSample>;
};

/**
 * Security alerts: lists, details with their history and e-mails, every workflow change and
 * refusal (captured; contract section 5).
 */
export const ALERTS = load('alerts') as {
  meta: SampleMeta;
  list: AlertPage;
  list_active: AlertPage;
  list_page: AlertPage;
  list_search: AlertPage;
  detail: AlertDetail;
  detail_auto_resolved: AlertDetail;
  detail_dismissed: AlertDetail;
  detail_seal: AlertDetail;
  state_acknowledged: AlertDetail;
  state_dismissed: AlertDetail;
  state_reopened: AlertDetail;
  state_resolved: AlertDetail;
  note_created: AlertEvent;
  summary: AlertSummary;
  errors: Record<string, ErrorSample>;
};

/** `GET /dashboard/summary` per role (captured; the blocks a role may not see are `null`). */
export const DASHBOARD = load('dashboard') as {
  meta: SampleMeta;
  admin: DashboardSummary;
  auditor: DashboardSummary;
  viewer: DashboardSummary;
  errors: Record<string, ErrorSample>;
};

/**
 * Usage analytics (`/usage/*`) of one captured site day (`company.period.from`, 12.10.2026: the
 * day before the capture's "today") — persons Ayşe Yılmaz, Burak Demir and Can Öztürk (a device
 * reassigned from Burak to Can that day), the unassigned PC-02 and a bare IP address —, with the
 * lists filtered, searched and paged, single subjects, sites and categories, the pseudonyms of a
 * `usage.anonymous` viewer, rebuild jobs, exports and refusals.
 */
export const USAGE = load('usage') as {
  meta: SampleMeta;
  company: CompanyUsage;
  company_work_hours: CompanyUsage;
  company_off_hours: CompanyUsage;
  company_today: CompanyUsage;
  company_week: CompanyUsage;
  viewer_company: CompanyUsage;
  subjects_persons: SubjectPage;
  subjects_devices: SubjectPage;
  subjects_departments: DepartmentPage;
  subjects_department_filter: SubjectPage;
  subjects_search: SubjectPage;
  subjects_page: SubjectPage;
  viewer_subjects: SubjectPage;
  viewer_subjects_pseudonymous: SubjectPage;
  hr_subjects_with_pseudonyms_on: SubjectPage;
  subject_person: SubjectDetail;
  subject_reassigned_before: SubjectDetail;
  subject_reassigned_after: SubjectDetail;
  subject_device: SubjectDetail;
  subject_ip: SubjectDetail;
  subject_week: SubjectDetail;
  viewer_subject_pseudonymous: SubjectDetail;
  sites: SitePage;
  sites_department_filter: SitePage;
  sites_category_filter: SitePage;
  sites_search: SitePage;
  site: SiteDetail;
  site_department_filter: SiteDetail;
  viewer_sites_pseudonymous: SiteDetail;
  categories: CategoryList;
  categories_department_filter: CategoryList;
  category: CategoryDetail;
  category_department_filter: CategoryDetail;
  category_unknown: CategoryDetail;
  rebuild_requested: RebuildJob;
  rebuild_queued: RebuildJob;
  rebuild_done: RebuildJob;
  pseudonyms: PseudonymRow[];
  pseudonyms_search: PseudonymRow[];
  export_company_csv: CsvSample;
  export_subjects_csv: CsvSample;
  export_subjects_department_csv: CsvSample;
  export_departments_csv: CsvSample;
  export_subject_csv: CsvSample;
  export_site_csv: CsvSample;
  export_category_csv: CsvSample;
  export_subject_xlsx: FileSample;
  export_sites_xlsx: FileSample;
  export_categories_xlsx: FileSample;
  export_company_xlsx: FileSample;
  errors: Record<string, ErrorSample>;
};

/**
 * Scheduled reports (captured, contract section 4): the shipped and changed schedules, runs made
 * by the worker job (one e-mailed to one recipient and refused by the other) and by "run now",
 * the files and the preview (headers, sizes and hashes only) and the refusals.
 */
export const REPORTS = load('reports') as {
  meta: SampleMeta;
  schedules: ScheduleOut[];
  schedules_shipped: ScheduleOut[];
  schedule_created: ScheduleOut;
  schedule_updated: ScheduleOut;
  schedule_deleted: { status: number };
  runs: RunPage;
  runs_filtered: RunPage;
  run_partial: RunOut;
  run_files_only: RunOut;
  run_queued: RunOut;
  file_html: FileSample;
  file_pdf: FileSample;
  file_xlsx: FileSample;
  preview_html: FileSample;
  preview_pdf: FileSample;
  errors: Record<string, ErrorSample>;
};

/**
 * The licence (captured on sites of their own, licensing contract 8.5; the stories are in
 * `src/test/license-samples.ts`): `GET /license` in every state, `POST /license` answers and
 * refusals, `DELETE`, the locked modules' 403 answers and the users limit's 409.
 */
export const LICENSE = load('license') as LicenseSamples;

/**
 * Arama (captured, Wave 2 contract 2.10, `samples_search.py`, the sealed day 2026-10-09 of the
 * main site): runs (`typed`, `text`, `first_page` and its `next_page`, `empty`, `partial`, the
 * auditor's), statistics in every mode, `POST /search/parse`, the field catalogue, records of
 * three tables, references, the exports' headers, saved searches and the refusals. The search
 * ids are `ornek-arama-NN`.
 */
export const SEARCH = load('search') as {
  meta: SampleMeta;
  search: Record<
    'typed' | 'text' | 'first_page' | 'next_page' | 'empty' | 'partial' | 'auditor',
    SearchPage
  >;
  stats: Record<'rollup' | 'hybrid' | 'direct' | 'partial' | 'auditor', SearchStats>;
  parse: Record<'typed' | 'text' | 'refused' | 'unknown_field', ParseResult>;
  fields: FieldCatalogue;
  event: Record<'traffic' | 'dhcp' | 'message', SearchEvent>;
  refs: SearchRefs;
  export: Record<'csv' | 'xlsx' | 'truncated', FileSample>;
  saved: {
    list: SavedSearchList;
    auditor_list: SavedSearchList;
    created: SavedSearch;
    created_preset: SavedSearch;
    updated: SavedSearch;
    run: SearchPage;
    deleted_status: number;
    list_after_delete: SavedSearchList;
  };
  errors: Record<string, ErrorSample>;
};

/** One server-sent event of a captured stream, as the page reads it. */
export interface LiveEventSample {
  event: string;
  id: string | null;
  data: unknown;
}

/**
 * Canlı Akış (captured, Wave 2 contract 2.14.4, `samples_live_sources.py`): `POST /live/streams`
 * of the administrator and the auditor, `GET /live/status`, the events of whole streams —
 * `stream` (hello, backfill and new lines: the firewall's traffic, a line not in the spool format,
 * an unknown sender's first line), `dropped` (the rate limit), `gap` (a resume the hub cannot
 * serve), `end` (`time_limit`) — and the refusals.
 */
export const LIVE = load('live') as {
  meta: SampleMeta;
  open: { admin: LiveStreamOut; auditor: LiveStreamOut };
  status: LiveStatus;
  events: Record<'stream' | 'dropped' | 'gap' | 'end', LiveEventSample[]>;
  errors: Record<string, ErrorSample>;
};

/**
 * Kaynaklar (captured, Wave 2 contract 2.16 and 2.17): the overview (the core router, the firewall,
 * a silent switch, an unknown sender), the detail of the firewall, the core router and the
 * unknown sender, the core router's last lines and a source without a file today, the Panel's
 * summary with today's parse failures, and the refusals.
 */
export const SOURCES = load('sources') as {
  meta: SampleMeta;
  overview: SourcesOverview;
  detail: Record<string, SourceDetail>;
  tail: { 'core-r1': SourceTail; no_file_today: SourceTail };
  dashboard: DashboardSummary;
  errors: Record<string, ErrorSample>;
};

/**
 * Uyum Durumu (captured, Wave 4 contract 2.3, `samples_compliance.py`): the ten cards for the
 * administrator and the auditor (card 7 with its `R20-filter` alert, card 10 waiting for the
 * configuration copy), the report download (headers, size and hash), the backup confirmation and
 * the cards after it (card 10 ok), and the refusals (`forbidden`, `config_backup_unreadable`).
 */
export const COMPLIANCE = load('compliance') as {
  meta: SampleMeta;
  admin: ComplianceStatus;
  auditor: ComplianceStatus;
  report_pdf: FileSample;
  config_backup: ConfigBackupOut;
  after_backup: ComplianceStatus;
  errors: Record<string, ErrorSample>;
};

/**
 * Sistem (captured, Wave 4 contract 2.5, `samples_system.py`, a Windows host: data drive `D:`,
 * system drive `C:`): the summary with the processor measured and the first reading of a process
 * (`cpu_percent` null), and the auditor's refusal (`system.read` is the administrators' only).
 */
export const SYSTEM = load('system') as {
  meta: SampleMeta;
  summary: SystemSummary;
  summary_first_reading: SystemSummary;
  errors: Record<string, ErrorSample>;
};

/**
 * The drives alone (`GET /system/disk`, the Panel's disk card): `disk` with the data drive's
 * forecast `estimated` and the system drive's `insufficient` (three samples), `disk_not_growing`
 * with the data drive `not_growing`, and the auditor's refusal.
 */
export const SYSTEM_DISK = load('system-disk') as {
  meta: SampleMeta;
  disk: SystemDisk;
  disk_not_growing: SystemDisk;
  errors: Record<string, ErrorSample>;
};

/**
 * Services, scheduled tasks, worker jobs and the clock (captured, Wave 4 contract 2.6): the
 * tasks `ok`, `late`, `missing`, `not_used` and `not_observable`, the jobs `ok`, `failing`,
 * `skipped`, `running` and `never`, the clock samples of today (one probe no server answered),
 * and the auditor's refusal.
 */
export const SYSTEM_HEALTH = load('system-health') as {
  meta: SampleMeta;
  health: SystemHealth;
  errors: Record<string, ErrorSample>;
};

/** "Today" of the captured samples (the E2E browser clock is set to it). */
export const SAMPLE_TODAY = SEALS.meta.site_today;

/** A deep copy (tests change the mocked state freely). */
export function copy<T>(value: T): T {
  return structuredClone(value);
}
