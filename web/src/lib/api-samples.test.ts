/**
 * The TypeScript types of the lookup, evidence, sealing and credit answers must describe what the
 * real API sends. `web/e2e/samples/*.json` are answers captured from the real API
 * (`web/scripts/capture_api_samples.py`, whose `--check` compares them with the API again); here
 * every sample is checked field by field against the interfaces of `api-types.ts`,
 * `lookup-types.ts`, `seal-types.ts`, `settings-types.ts`, `search-types.ts`, `live-types.ts` and
 * `sources-types.ts` — required fields present, no unknown fields, value types and string literal
 * unions as declared. The untyped parts of the OpenAPI document (`outcome`, `query`, the seal day
 * detail, the `result` of an administration request, the events of a live stream) are covered
 * only here.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import ts from 'typescript';

import { LICENSE_DOCUMENT_NAMES, LICENSE_ERROR_NAMES } from '@/test/license-samples';

const LIB = join(process.cwd(), 'src', 'lib');
const SAMPLES = join(process.cwd(), 'e2e', 'samples');

interface Named<T> {
  node: T;
  source: ts.SourceFile;
}

const interfaces = new Map<string, Named<ts.InterfaceDeclaration>>();
const aliases = new Map<string, Named<ts.TypeNode>>();
for (const file of [
  'api-types.ts',
  'lookup-types.ts',
  'seal-types.ts',
  'settings-types.ts',
  'search-types.ts',
  'live-types.ts',
  'sources-types.ts',
]) {
  const path = join(LIB, file);
  const source = ts.createSourceFile(
    path,
    readFileSync(path, 'utf8'),
    ts.ScriptTarget.ES2022,
    true,
  );
  source.forEachChild((node) => {
    if (ts.isInterfaceDeclaration(node)) interfaces.set(node.name.text, { node, source });
    if (ts.isTypeAliasDeclaration(node)) aliases.set(node.name.text, { node: node.type, source });
  });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function describe_(value: unknown): string {
  return value === undefined ? 'undefined' : JSON.stringify(value).slice(0, 80);
}

function members(
  list: ts.NodeArray<ts.TypeElement>,
  source: ts.SourceFile,
): Map<string, { optional: boolean; type: ts.TypeNode; source: ts.SourceFile }> {
  const found = new Map<string, { optional: boolean; type: ts.TypeNode; source: ts.SourceFile }>();
  for (const member of list) {
    if (!ts.isPropertySignature(member) || !member.type) continue;
    found.set(member.name.getText(source), {
      optional: Boolean(member.questionToken),
      type: member.type,
      source,
    });
  }
  return found;
}

function interfaceMembers(name: string) {
  const declaration = interfaces.get(name);
  if (!declaration) throw new Error(`no interface ${name}`);
  const found = new Map<string, { optional: boolean; type: ts.TypeNode; source: ts.SourceFile }>();
  for (const clause of declaration.node.heritageClauses ?? []) {
    for (const parent of clause.types) {
      for (const [key, value] of interfaceMembers(parent.expression.getText(declaration.source)))
        found.set(key, value);
    }
  }
  for (const [key, value] of members(declaration.node.members, declaration.source))
    found.set(key, value);
  return found;
}

function checkMembers(
  value: unknown,
  fields: Map<string, { optional: boolean; type: ts.TypeNode; source: ts.SourceFile }>,
  path: string,
): string[] {
  if (!isObject(value)) return [`${path}: expected an object, got ${describe_(value)}`];
  const problems: string[] = [];
  for (const key of Object.keys(value)) {
    if (!fields.has(key)) problems.push(`${path}.${key}: not declared in the TypeScript type`);
  }
  for (const [key, field] of fields) {
    if (!(key in value)) {
      if (!field.optional) problems.push(`${path}.${key}: required, missing in the sample`);
      continue;
    }
    problems.push(...check(value[key], field.type, field.source, `${path}.${key}`));
  }
  return problems;
}

/** Problems of `value` against the type `type` (empty when it conforms). */
function check(value: unknown, type: ts.TypeNode, source: ts.SourceFile, path: string): string[] {
  if (ts.isParenthesizedTypeNode(type)) return check(value, type.type, source, path);
  if (ts.isUnionTypeNode(type)) {
    const results = type.types.map((member) => check(value, member, source, path));
    if (results.some((problems) => problems.length === 0)) return [];
    return [`${path}: ${describe_(value)} matches no member of ${type.getText(source)}`];
  }
  if (ts.isLiteralTypeNode(type)) {
    if (type.literal.kind === ts.SyntaxKind.NullKeyword)
      return value === null ? [] : [`${path}: expected null`];
    if (type.literal.kind === ts.SyntaxKind.FalseKeyword)
      return value === false ? [] : [`${path}: expected false`];
    if (type.literal.kind === ts.SyntaxKind.TrueKeyword)
      return value === true ? [] : [`${path}: expected true`];
    if (ts.isStringLiteral(type.literal))
      return value === type.literal.text ? [] : [`${path}: expected "${type.literal.text}"`];
  }
  if (ts.isArrayTypeNode(type)) {
    if (!Array.isArray(value)) return [`${path}: expected an array`];
    return value.flatMap((item, index) =>
      check(item, type.elementType, source, `${path}[${index}]`),
    );
  }
  if (ts.isTypeLiteralNode(type)) return checkMembers(value, members(type.members, source), path);
  switch (type.kind) {
    case ts.SyntaxKind.StringKeyword:
      return typeof value === 'string' ? [] : [`${path}: expected a string`];
    case ts.SyntaxKind.NumberKeyword:
      return Number.isInteger(value) ? [] : [`${path}: expected an integer (no floats)`];
    case ts.SyntaxKind.BooleanKeyword:
      return typeof value === 'boolean' ? [] : [`${path}: expected a boolean`];
    case ts.SyntaxKind.UnknownKeyword:
      return [];
  }
  if (ts.isTypeReferenceNode(type)) {
    const name = type.typeName.getText(source);
    // The one number type that may carry a fraction (`api-types.ts`).
    if (name === 'Float')
      return typeof value === 'number' && Number.isFinite(value)
        ? []
        : [`${path}: expected a number`];
    if (name === 'Record') {
      if (!isObject(value)) return [`${path}: expected an object`];
      const [keyType, valueType] = type.typeArguments ?? [];
      return Object.entries(value).flatMap(([key, item]) => [
        // A map whose keys are a fixed list (`Record<ServiceName, …>`): every key is one of them.
        ...(keyType ? check(key, keyType, source, `${path}{${key}}`) : []),
        ...(valueType ? check(item, valueType, source, `${path}.${key}`) : []),
      ]);
    }
    // `Partial<Record<…>>`: the same map, not every key present.
    const [argument] = type.typeArguments ?? [];
    if (name === 'Partial' && argument && ts.isTypeReferenceNode(argument))
      return check(value, argument, source, path);
    const alias = aliases.get(name);
    if (alias) return check(value, alias.node, alias.source, path);
    if (interfaces.has(name)) return checkMembers(value, interfaceMembers(name), path);
  }
  throw new Error(`unsupported type: ${type.getText(source)}`);
}

function conforms(value: unknown, typeName: string, path: string): string[] {
  const alias = aliases.get(typeName);
  if (alias) return check(value, alias.node, alias.source, path);
  return checkMembers(value, interfaceMembers(typeName), path);
}

function load(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(SAMPLES, `${name}.json`), 'utf8')) as Record<string, unknown>;
}

/** How each top-level sample key is typed. */
type Rule = string | ((value: unknown, path: string) => string[]) | null;

const each =
  (typeName: string) =>
  (value: unknown, path: string): string[] =>
    isObject(value)
      ? Object.entries(value).flatMap(([key, item]) => conforms(item, typeName, `${path}.${key}`))
      : [`${path}: expected an object`];

const errors = each('ErrorSample');

const list =
  (typeName: string) =>
  (value: unknown, path: string): string[] =>
    Array.isArray(value)
      ? value.flatMap((item, index) => conforms(item, typeName, `${path}[${index}]`))
      : [`${path}: expected an array`];

/** An import answer whose untyped preview rows are `previewType` (`PersonPreview`, …). */
const importSample =
  (previewType: string) =>
  (value: unknown, path: string): string[] => {
    const problems = conforms(value, 'ImportResult', path);
    if (isObject(value)) problems.push(...list(previewType)(value.preview, `${path}.preview`));
    return problems;
  };

function lookupSample(value: unknown, path: string): string[] {
  const problems = conforms(value, 'LookupOut', path);
  if (!isObject(value)) return problems;
  problems.push(...conforms(value.query, 'StoredQuery', `${path}.query`));
  if (value.outcome !== null)
    problems.push(...conforms(value.outcome, 'LookupOutcome', `${path}.outcome`));
  const report = value.report as Record<string, unknown> | null;
  if (report?.timestamp)
    problems.push(...conforms(report.timestamp, 'ReportTimestamp', `${path}.report.timestamp`));
  return problems;
}

/** The type of an administration request's `result` by its kind (`null` until there is one). */
const REQUEST_RESULTS: Record<string, string> = {
  'action.seal': 'SealActionResult',
  'action.credit': 'CreditActionResult',
  'action.tsa_test': 'TsaTestResult',
  'action.replica_test': 'ReplicaTestResult',
  'worker.test_email': 'TestEmailResult',
  'worker.lease_rebuild': 'LeaseRebuildResult',
};

/** An administration request with the result of its kind (a change has none). */
function requestSample(value: unknown, path: string): string[] {
  const problems = conforms(value, 'AdminRequestOut', path);
  if (!isObject(value) || value.result === null) return problems;
  const type = REQUEST_RESULTS[String(value.kind)];
  if (!type) return [...problems, `${path}.result: a ${String(value.kind)} has no result`];
  return [...problems, ...conforms(value.result, type, `${path}.result`)];
}

function requestPage(value: unknown, path: string): string[] {
  const problems = conforms(value, 'AdminRequestPage', path);
  if (isObject(value) && Array.isArray(value.items))
    value.items.forEach((item, index) =>
      problems.push(...requestSample(item, `${path}.items[${index}]`)),
    );
  return problems;
}

/** The `details` of a refusal by its code (the field problems of 2.11, the settings' lists). */
const ERROR_DETAILS: Record<string, string> = {
  host_invalid: 'FieldProblem',
  legal_root_confirm_required: 'FieldProblem',
  replica_target_invalid: 'FieldProblem',
  retention_below_minimum: 'FieldProblem',
  retention_confirm_mismatch: 'FieldProblem',
  site_name_required: 'FieldProblem',
  smtp_password_required: 'FieldProblem',
  source_address_taken: 'FieldProblem',
  source_confirm_mismatch: 'FieldProblem',
  source_network_not_allowed: 'FieldProblem',
  tsa_mock_refused: 'FieldProblem',
  zamane_sha256_required: 'FieldProblem',
  setting_invalid: 'SettingProblem',
  setting_conflict: 'SettingConflict',
};

/** Refusals whose `details` list has the type of {@link ERROR_DETAILS}. */
function adminErrors(value: unknown, path: string): string[] {
  const problems = errors(value, path);
  if (!isObject(value)) return problems;
  for (const [key, sample] of Object.entries(value)) {
    const error = (sample as { body?: { error?: { code?: string; details?: unknown } } }).body
      ?.error;
    const type = ERROR_DETAILS[error?.code ?? ''];
    if (type) problems.push(...list(type)(error?.details, `${path}.${key}.body.error.details`));
  }
  return problems;
}

/** A group of samples, each key with its own rule (like the files of {@link RULES}). */
const group =
  (rules: Record<string, Rule>) =>
  (value: unknown, path: string): string[] => {
    if (!isObject(value)) return [`${path}: expected an object`];
    const problems = Object.keys(value)
      .filter((key) => !(key in rules))
      .map((key) => `${path}.${key}: no type rule (add one here)`);
    for (const [key, rule] of Object.entries(rules)) {
      if (!(key in value)) problems.push(`${path}.${key}: missing in the sample`);
      else if (typeof rule === 'string')
        problems.push(...conforms(value[key], rule, `${path}.${key}`));
      else if (rule) problems.push(...rule(value[key], `${path}.${key}`));
    }
    return problems;
  };

/**
 * A captured live stream: its events in order (`{event, id, data}`), each `data` of the type
 * `LiveEvents` gives its event; only a `line` has an `id` (`<epoch>:<seq>`).
 */
function liveEvents(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) return [`${path}: expected an array`];
  const events = interfaceMembers('LiveEvents');
  return value.flatMap((item, index) => {
    const at = `${path}[${index}]`;
    if (!isObject(item)) return [`${at}: expected an object`];
    const problems = Object.keys(item)
      .filter((key) => !['event', 'id', 'data'].includes(key))
      .map((key) => `${at}.${key}: not part of an event`);
    const event = events.get(String(item.event));
    if (!event) return [...problems, `${at}.event: ${describe_(item.event)} is no live event`];
    const id = item.event === 'line' ? typeof item.id === 'string' : item.id === null;
    if (!id) problems.push(`${at}.id: ${describe_(item.id)} (only a line has an id)`);
    return [...problems, ...check(item.data, event.type, event.source, `${at}.data`)];
  });
}

const FILE_SAMPLE = null; // downloads: headers, size and hash only
const RULES: Record<string, Record<string, Rule>> = {
  lookups: {
    exact: lookupSample,
    candidates: lookupSample,
    conflict: lookupSample,
    internal: lookupSample,
    none: lookupSample,
    vault_only: lookupSample,
    truncated: lookupSample,
    auditor_run: lookupSample,
    exact_with_report: lookupSample,
    candidates_with_report: lookupSample,
    list_admin: 'LookupPage',
    list_admin_mine: 'LookupPage',
    list_auditor: 'LookupPage',
    report_pdf: FILE_SAMPLE,
    report_pdf_stamped: FILE_SAMPLE,
    report_token: FILE_SAMPLE,
    errors,
  },
  raw: { lines: each('RawLineOut'), odd_line: null, errors },
  evidence: {
    created: 'EvidenceCreated',
    created_password: 'EvidenceCreated',
    created_queued: 'EvidenceCreated',
    list: 'EvidencePage',
    list_auditor: 'EvidencePage',
    details: each('EvidenceDetail'),
    ready_expired: 'EvidenceDetail',
    verify_tampered: 'VerifyResult',
    verify_restored: 'VerifyResult',
    download: FILE_SAMPLE,
    status_labels: null,
    errors,
  },
  seals: {
    calendars: each('Calendar'),
    calendar_current: 'Calendar',
    days: each('SealDayDetail'),
    verify: 'VerifyResult',
    verify_quick: 'VerifyResult',
    manifest_file: FILE_SAMPLE,
    token_file: FILE_SAMPLE,
    seal_now: 'AdminAccepted',
    seal_now_queued: requestSample,
    seal_now_done: requestSample,
    errors,
  },
  credits: {
    mock: 'Credits',
    kamusm: 'Credits',
    kamusm_low: 'Credits',
    query: 'AdminAccepted',
    query_done: requestSample,
    query_limited: 'ErrorSample',
  },
  // Browser administration (contract 2.11): the configuration in three stages of the capture
  // (installed; Kamu SM set up; e-mail, replica, retention and backup changed), every answer of
  // a change and the history.
  admin: {
    config: 'AdminConfig',
    status: 'AdminStatus',
    status_after: 'AdminStatus',
    status_agent_missing: 'AdminStatus',
    status_disabled: 'AdminStatus',
    check_general: 'AdminCheck',
    check_clock: 'AdminCheck',
    accepted: 'AdminAccepted',
    request_queued: requestSample,
    request_active: requestSample,
    requests: requestPage,
    kamusm: group({
      config: 'AdminConfig',
      check_credentials: 'AdminCheck',
      credentials_accepted: 'AdminAccepted',
      credentials_active: requestSample,
      upload_root: 'UploadPreview',
      root_accepted: 'AdminAccepted',
      root_active: requestSample,
      upload_zamane: 'UploadPreview',
      zamane_accepted: 'AdminAccepted',
      zamane_pending: requestSample,
      tsa_test_accepted: 'AdminAccepted',
      tsa_test_done: requestSample,
    }),
    sources: group({
      check_add: 'AdminCheck',
      check_update: 'AdminCheck',
      add_accepted: 'AdminAccepted',
      add_applied: requestSample,
      add_active: requestSample,
      remove_accepted: 'AdminAccepted',
      remove_active: requestSample,
    }),
    sections: group({
      config: 'AdminConfig',
      check_smtp: 'AdminCheck',
      smtp_accepted: 'AdminAccepted',
      smtp_done: requestSample,
      upload_smtp_ca: 'UploadPreview',
      smtp_ca_accepted: 'AdminAccepted',
      smtp_ca_done: requestSample,
      test_email_accepted: 'AdminAccepted',
      test_email_done: requestSample,
      check_replica: 'AdminCheck',
      replica_accepted: 'AdminAccepted',
      replica_done: requestSample,
      replica_test_accepted: 'AdminAccepted',
      replica_test_done: requestSample,
      check_retention: 'AdminCheck',
      retention_accepted: 'AdminAccepted',
      retention_done: requestSample,
      check_backup: 'AdminCheck',
      backup_accepted: 'AdminAccepted',
      backup_done: requestSample,
      lease_rebuild_accepted: 'AdminAccepted',
      lease_rebuild_done: requestSample,
    }),
    history: group({
      held_accepted: 'AdminAccepted',
      held_pending: requestSample,
      cancel_held_accepted: 'AdminAccepted',
      cancel_held_done: requestSample,
      held_cancelled: requestSample,
      cancel_queued: 'AdminAccepted',
      cancelled_queued: requestSample,
      revert_accepted: 'AdminAccepted',
      revert_done: requestSample,
      requests: requestPage,
      versions: list('ConfigVersion'),
    }),
    errors: adminErrors,
  },
  settings: {
    list: 'SettingsOut',
    list_changed: 'SettingsOut',
    patch: 'SettingsPatchResult',
    reset: 'SettingsPatchResult',
    errors: adminErrors,
  },
  devices: {
    list: 'DevicePage',
    list_unknown: 'DevicePage',
    list_search: 'DevicePage',
    list_page: 'DevicePage',
    detail: 'DeviceDetail',
    detail_phone: 'DeviceDetail',
    device_updated: 'DeviceOut',
    assigned: 'AssignResult',
    reassigned: 'AssignResult',
    unassigned: 'AssignResult',
    persons: 'PersonPage',
    persons_search: 'PersonPage',
    person_detail: 'PersonDetail',
    person_created: 'PersonOut',
    person_updated: 'PersonOut',
    departments: list('DepartmentOut'),
    department_created: 'DepartmentOut',
    department_updated: 'DepartmentOut',
    vlans: list('VlanOut'),
    vlan_updated: 'VlanOut',
    vlan_created: 'VlanOut',
    vlan_edited: 'VlanOut',
    import_persons_preview: importSample('PersonPreview'),
    import_persons_errors: importSample('PersonPreview'),
    import_assignments_preview: importSample('AssignmentPreview'),
    import_assignments_errors: importSample('AssignmentPreview'),
    import_assignments_applied: importSample('AssignmentPreview'),
    devices_csv: FILE_SAMPLE,
    persons_csv: FILE_SAMPLE,
    template_persons: FILE_SAMPLE,
    template_assignments: FILE_SAMPLE,
    errors,
  },
  alerts: {
    list: 'AlertPage',
    list_active: 'AlertPage',
    list_page: 'AlertPage',
    list_search: 'AlertPage',
    detail: 'AlertDetail',
    detail_auto_resolved: 'AlertDetail',
    detail_dismissed: 'AlertDetail',
    detail_seal: 'AlertDetail',
    state_acknowledged: 'AlertDetail',
    state_dismissed: 'AlertDetail',
    state_reopened: 'AlertDetail',
    state_resolved: 'AlertDetail',
    note_created: 'AlertEvent',
    summary: 'AlertSummary',
    errors,
  },
  dashboard: {
    admin: 'DashboardSummary',
    auditor: 'DashboardSummary',
    viewer: 'DashboardSummary',
    errors,
  },
  reports: {
    schedules: list('ScheduleOut'),
    schedules_shipped: list('ScheduleOut'),
    schedule_created: 'ScheduleOut',
    schedule_updated: 'ScheduleOut',
    schedule_deleted: null,
    runs: 'RunPage',
    runs_filtered: 'RunPage',
    run_partial: 'RunOut',
    run_files_only: 'RunOut',
    run_queued: 'RunOut',
    file_html: FILE_SAMPLE,
    file_pdf: FILE_SAMPLE,
    file_xlsx: FILE_SAMPLE,
    preview_html: FILE_SAMPLE,
    preview_pdf: FILE_SAMPLE,
    errors,
  },
  usage: {
    company: 'CompanyUsage',
    company_work_hours: 'CompanyUsage',
    company_off_hours: 'CompanyUsage',
    company_today: 'CompanyUsage',
    company_week: 'CompanyUsage',
    viewer_company: 'CompanyUsage',
    subjects_persons: 'SubjectPage',
    subjects_devices: 'SubjectPage',
    subjects_page: 'SubjectPage',
    subjects_search: 'SubjectPage',
    subjects_department_filter: 'SubjectPage',
    subjects_departments: 'DepartmentPage',
    viewer_subjects: 'SubjectPage',
    viewer_subjects_pseudonymous: 'SubjectPage',
    hr_subjects_with_pseudonyms_on: 'SubjectPage',
    subject_person: 'SubjectDetail',
    subject_device: 'SubjectDetail',
    subject_ip: 'SubjectDetail',
    subject_week: 'SubjectDetail',
    subject_reassigned_before: 'SubjectDetail',
    subject_reassigned_after: 'SubjectDetail',
    viewer_subject_pseudonymous: 'SubjectDetail',
    sites: 'SitePage',
    sites_search: 'SitePage',
    sites_category_filter: 'SitePage',
    sites_department_filter: 'SitePage',
    site: 'SiteDetail',
    site_department_filter: 'SiteDetail',
    viewer_sites_pseudonymous: 'SiteDetail',
    categories: 'CategoryList',
    categories_department_filter: 'CategoryList',
    category: 'CategoryDetail',
    category_department_filter: 'CategoryDetail',
    category_unknown: 'CategoryDetail',
    rebuild_requested: 'RebuildJob',
    rebuild_queued: 'RebuildJob',
    rebuild_done: 'RebuildJob',
    pseudonyms: list('PseudonymRow'),
    pseudonyms_search: list('PseudonymRow'),
    export_company_csv: FILE_SAMPLE,
    export_company_xlsx: FILE_SAMPLE,
    export_subjects_csv: FILE_SAMPLE,
    export_subjects_department_csv: FILE_SAMPLE,
    export_departments_csv: FILE_SAMPLE,
    export_subject_csv: FILE_SAMPLE,
    export_subject_xlsx: FILE_SAMPLE,
    export_sites_xlsx: FILE_SAMPLE,
    export_site_csv: FILE_SAMPLE,
    export_categories_xlsx: FILE_SAMPLE,
    export_category_csv: FILE_SAMPLE,
    errors,
  },
  // Arama (contract 2.10): runs and pages, statistics in every mode, the parser, the catalogue,
  // records, references, saved searches; the exports are downloads.
  search: {
    search: each('SearchPage'),
    stats: each('SearchStats'),
    parse: each('ParseResult'),
    fields: 'FieldCatalogue',
    event: each('SearchEvent'),
    refs: 'SearchRefs',
    export: group({ csv: FILE_SAMPLE, xlsx: FILE_SAMPLE, truncated: FILE_SAMPLE }),
    saved: group({
      list: 'SavedSearchList',
      auditor_list: 'SavedSearchList',
      created: 'SavedSearch',
      created_preset: 'SavedSearch',
      updated: 'SavedSearch',
      run: 'SearchPage',
      deleted_status: null,
      list_after_delete: 'SavedSearchList',
    }),
    errors,
  },
  // Canlı Akış (contract 2.14.4): opened streams, the hub's status and four captured streams.
  live: {
    open: each('LiveStreamOut'),
    status: 'LiveStatus',
    events: group({
      stream: liveEvents,
      dropped: liveEvents,
      gap: liveEvents,
      end: liveEvents,
    }),
    errors,
  },
  // Kaynaklar (contract 2.16) and the Panel with today's parse failures (2.17).
  sources: {
    overview: 'SourcesOverview',
    detail: each('SourceDetail'),
    tail: each('SourceTail'),
    dashboard: 'DashboardSummary',
    errors,
  },
  // Every licence document is `GET /license` (also the answer of `POST /license`), every other
  // key an error answer (refusals, re-authentication, locked modules, users limit, 503).
  license: {
    ...Object.fromEntries(LICENSE_DOCUMENT_NAMES.map((name) => [name, 'LicenseOut'])),
    ...Object.fromEntries(LICENSE_ERROR_NAMES.map((name) => [name, 'ErrorSample'])),
  },
};

// The captured error answers: `{status, headers, body}` with the standard error body.
interfaces.set(
  'ErrorSample',
  (() => {
    const text =
      'interface ErrorSample { status: number; headers: Record<string, string>; body: ApiErrorBody }';
    const source = ts.createSourceFile('sample.ts', text, ts.ScriptTarget.ES2022, true);
    const node = source.statements[0] as ts.InterfaceDeclaration;
    return { node, source };
  })(),
);

describe('captured API answers match the TypeScript types', () => {
  it.each(Object.keys(RULES))('%s.json', (name) => {
    const sample = load(name);
    const rules = RULES[name] ?? {};
    const problems: string[] = [];
    for (const key of Object.keys(sample)) {
      if (key === 'meta') continue;
      if (!(key in rules)) problems.push(`${name}.${key}: no type rule (add one here)`);
    }
    for (const [key, rule] of Object.entries(rules)) {
      if (!(key in sample)) {
        problems.push(`${name}.${key}: missing in the sample`);
        continue;
      }
      const path = `${name}.${key}`;
      if (typeof rule === 'string') problems.push(...conforms(sample[key], rule, path));
      else if (rule) problems.push(...rule(sample[key], path));
    }
    expect(problems).toEqual([]);
  });

  it('licence: users without settings.manage get the same document without details', () => {
    // The unit tests and the E2E mock derive the users' form of every state from this rule.
    const license = load('license');
    for (const [admin, viewer] of [
      ['trial', 'trial_viewer'],
      ['licensed', 'licensed_viewer'],
    ] as const) {
      expect(license[viewer]).toEqual({ ...(license[admin] as object), details: null });
      expect((license[admin] as { details: unknown }).details).not.toBeNull();
    }
  });

  it('describes a lookup stored before ADR-0016 (GET /lookup/{id} returns it as stored)', () => {
    // A lookup stored before the upgrade has none of the fields ADR-0016 added (contract 2.10;
    // only the PDF and the evidence package read the outcome through `from_dict` defaults), so
    // the types declare them optional and the result page reads an absent one as its default.
    const added = new Set([
      'egress',
      'unconverted_dhcp',
      'untrusted_sources',
      'mac_source',
      'srcintf',
      'dstintf',
      'srcmac',
      'source_table',
      'lease_seconds',
      'last_dhcp_evidence',
    ]);
    const removed = new Set<string>();
    const stored = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(stored);
      if (!isObject(value)) return value;
      const kept = Object.entries(value).filter(([key]) => {
        if (added.has(key)) removed.add(key);
        return !added.has(key);
      });
      return Object.fromEntries(kept.map(([key, item]) => [key, stored(item)]));
    };
    const lookups = load('lookups');
    for (const name of ['exact', 'internal'])
      expect(lookupSample(stored(lookups[name]), name)).toEqual([]);
    expect([...removed].sort()).toEqual([...added].sort()); // every field was there to remove
  });

  it('notices a wrong field (the checker itself works)', () => {
    const lookups = load('lookups');
    const broken = structuredClone(lookups.exact) as Record<string, unknown>;
    (broken.outcome as Record<string, unknown>).mode = 'approximate';
    delete (broken.outcome as Record<string, unknown>).truncated;
    (broken.outcome as Record<string, unknown>).surprise = 1;
    const problems = lookupSample(broken, 'x');
    expect(problems).toEqual(
      expect.arrayContaining([
        'x.outcome.surprise: not declared in the TypeScript type',
        'x.outcome.truncated: required, missing in the sample',
        expect.stringContaining('x.outcome.mode'),
      ]),
    );
    expect(conforms({ score: 1.5 }, 'NatMatch', 'm')).toContain(
      'm.score: expected an integer (no floats)',
    );
    const live = load('live').events as Record<string, Record<string, unknown>[]>;
    const line = structuredClone(live.stream?.find((event) => event.event === 'line'));
    expect(line).toBeDefined();
    const shown = line?.data as Record<string, unknown>;
    delete shown.seq;
    expect(
      liveEvents(
        [
          { ...line, id: null },
          { event: 'paused', id: null, data: {} },
        ],
        'e',
      ),
    ).toEqual(
      expect.arrayContaining([
        'e[0].id: null (only a line has an id)',
        'e[0].data.seq: required, missing in the sample',
        'e[1].event: "paused" is no live event',
      ]),
    );
  });
});
