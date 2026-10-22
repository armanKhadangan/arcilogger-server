/**
 * The mocked 5651 lookup, raw line, evidence package, sealing and credit endpoints of the E2E
 * tests. Answers are the samples captured from the real API (`samples.ts`); this class only adds
 * the state a browser session changes (new lookups, generated reports, packages being built,
 * downloads, verifications) and the permission rules of the real routers:
 *
 * - lookups and raw lines: `lookup.run` (raw: or `search.read`) + accepted KVKK notice;
 *   administrators see every lookup, everybody else only their own (others answer 404);
 * - evidence: `evidence.read`; create `evidence.create` + re-authentication; download
 *   `evidence.download` + re-authentication + KVKK notice; a package of a lookup follows that
 *   lookup's visibility (administrators see every package, everybody else packages chosen by
 *   days and those of their own lookups; others are not listed and answer 404);
 * - seals and credits: `seal.read`; verify `seal.verify`; "seal now" `seal.manage` → 501.
 */

import { createHash } from 'node:crypto';

import type {
  Calendar,
  EvidenceCreate,
  EvidenceDetail,
  EvidenceOut,
  LookupIn,
  LookupOut,
  LookupSummary,
} from '../src/lib/api-types';

import type { MockUser, RecordedCall } from './mock-api';
import {
  copy,
  CREDITS,
  EVIDENCE,
  LOOKUPS,
  RAW,
  SAMPLE_TODAY,
  SEALS,
  type ErrorSample,
} from './samples';

export interface FileAnswer {
  body: Buffer;
  contentType: string;
  headers: Record<string, string>;
}

export type Answer = [number, unknown] | [number, unknown, FileAnswer];

/** What the records mock needs from the main mock (authentication, error texts). */
export interface MockHost {
  requirePermission(
    call: RecordedCall,
    permission: string,
    recent?: boolean,
  ): MockUser | [number, unknown];
  error(status: number, code: string, details?: unknown): [number, unknown];
  permissionsOf(account: MockUser): readonly string[];
}

/** Fake but stable file contents (the real PDF/ZIP are not needed to test the pages). */
const PDF = Buffer.from('%PDF-1.7\n% ARCILOGGER E2E sample report\n%%EOF\n');
const ZIP = Buffer.from('PK\u0005\u0006' + '\u0000'.repeat(18), 'latin1');

/** A copy of `value` without `keys` (fields the real endpoint does not send). */
function omit<T extends object>(value: T, ...keys: string[]): T {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key))) as T;
}

function sha256(data: Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

/** A captured refusal, with its headers (e.g. `Retry-After` of a 429). */
function sample(error: ErrorSample | undefined): Answer {
  if (!error) throw new Error('missing error sample');
  const body = copy(error.body);
  if (Object.keys(error.headers).length === 0) return [error.status, body];
  return [
    error.status,
    body,
    {
      body: Buffer.from(JSON.stringify(body)),
      contentType: 'application/json',
      headers: error.headers,
    },
  ];
}

/** Which captured lookup answers a request (by what it asks). */
function lookupSampleFor(body: LookupIn): LookupOut {
  const query = body.query;
  if (query.internal_ip || query.mac) return LOOKUPS.internal;
  if (query.public_ip === '100.64.12.9') return LOOKUPS.exact;
  if (query.dest_ip === '93.184.216.34') return LOOKUPS.conflict;
  if (query.dest_ip === '203.0.113.10') return LOOKUPS.candidates;
  if (!query.dest_ip && !query.dest_domain && query.dest_port == null) return LOOKUPS.truncated;
  return LOOKUPS.none;
}

/** Synthesised calendar of a month that was not captured: every day "no data" or "future". */
function emptyMonth(month: string): Calendar {
  const template = copy(SEALS.calendar_current);
  const noData = template.days.find((cell) => cell.state === 'no_data');
  const future = template.days.find((cell) => cell.state === 'future') ?? noData;
  if (!noData || !future) throw new Error('the calendar sample has no empty day');
  const [year, number] = month.split('-').map(Number);
  const length = new Date(year ?? 2026, number ?? 1, 0).getDate();
  const days = Array.from({ length }, (_, index) => {
    const day = `${month}-${String(index + 1).padStart(2, '0')}`;
    return { ...(day > template.today ? future : noData), day };
  });
  const counts: Record<string, number> = {};
  for (const cell of days) counts[cell.state] = (counts[cell.state] ?? 0) + 1;
  return { ...template, month, days, counts, problems: [] };
}

export interface RecordsOptions {
  /** Which credit sample `/credits` answers (default: Kamu SM production-like history). */
  credits?: 'kamusm' | 'mock' | 'kamusm_low';
  /** Milliseconds a new evidence package stays `queued`, then `building`, before `ready`. */
  buildMs?: number;
}

export class RecordsMock {
  readonly lookups = new Map<number, LookupOut>();
  readonly packages = new Map<number, EvidenceDetail & { readyAt?: number }>();
  nextLookupId: number;
  nextPackageId: number;
  credits: 'kamusm' | 'mock' | 'kamusm_low';
  buildMs: number;
  /** The next evidence creation answers with this captured refusal. */
  refuseNextCreate: ErrorSample | null = null;
  /** The next day verification answers with this captured result (default: ok). */
  nextVerify: 'ok' | 'failed' | null = null;

  constructor(options: RecordsOptions = {}) {
    for (const item of [
      LOOKUPS.exact,
      LOOKUPS.candidates,
      LOOKUPS.conflict,
      LOOKUPS.internal,
      LOOKUPS.none,
      LOOKUPS.vault_only,
      LOOKUPS.truncated,
      LOOKUPS.auditor_run,
    ])
      this.lookups.set(item.id, copy(item));
    for (const detail of Object.values(EVIDENCE.details))
      this.packages.set(detail.id, copy(detail));
    this.nextLookupId = Math.max(...this.lookups.keys()) + 1;
    this.nextPackageId = Math.max(...this.packages.keys()) + 1;
    this.credits = options.credits ?? 'kamusm';
    this.buildMs = options.buildMs ?? 2_500;
  }

  answer(call: RecordedCall, host: MockHost): Answer | null {
    const { method, path } = call;
    let match: RegExpExecArray | null;
    if (method === 'POST' && path === '/lookup/5651') return this.runLookup(call, host);
    if (method === 'GET' && path === '/lookups') return this.listLookups(call, host);
    if ((match = /^\/lookup\/(\d+)(\/report\.pdf(\.zd)?)?$/.exec(path)) && method === 'GET') {
      const id = Number(match[1]);
      if (match[3]) return this.reportToken(call, host, id);
      if (match[2]) return this.reportPdf(call, host, id);
      return this.getLookup(call, host, id);
    }
    if (method === 'GET' && path === '/raw') return this.raw(call, host);
    if (path === '/evidence' && method === 'GET') return this.listPackages(call, host);
    if (path === '/evidence' && method === 'POST') return this.createPackage(call, host);
    if ((match = /^\/evidence\/(\d+)(\/download)?$/.exec(path)) && method === 'GET') {
      const id = Number(match[1]);
      return match[2] ? this.downloadPackage(call, host, id) : this.getPackage(call, host, id);
    }
    if (method === 'GET' && path === '/seals/calendar') return this.calendar(call, host);
    if ((match = /^\/seals\/(\d{4}-\d{2}-\d{2})(?:\/(manifest|token|verify|seal))?$/.exec(path)))
      return this.seal(call, host, match[1] ?? '', match[2] ?? null);
    if (method === 'GET' && path === '/credits') {
      const account = host.requirePermission(call, 'seal.read');
      return Array.isArray(account) ? account : [200, copy(CREDITS[this.credits])];
    }
    return null;
  }

  // -- 5651 lookups -----------------------------------------------------------------------------

  /** `lookup.run` (or another permission) and the accepted KVKK notice. */
  private personLevel(
    call: RecordedCall,
    host: MockHost,
    permissions: readonly string[] = ['lookup.run'],
  ): MockUser | [number, unknown] {
    const account = host.requirePermission(call, permissions[0] ?? 'lookup.run');
    let found = account;
    if (Array.isArray(account) && permissions.length > 1) {
      for (const permission of permissions.slice(1)) {
        found = host.requirePermission(call, permission);
        if (!Array.isArray(found)) break;
      }
    }
    if (Array.isArray(found)) return found;
    if (!found.privacy_accepted) return host.error(403, 'privacy_notice_required');
    return found;
  }

  private visible(account: MockUser, lookup: LookupOut | undefined): boolean {
    return lookup !== undefined && (account.role === 'admin' || lookup.user_id === account.id);
  }

  private runLookup(call: RecordedCall, host: MockHost): Answer {
    const account = this.personLevel(call, host);
    if (Array.isArray(account)) return account;
    const body = call.body as LookupIn;
    if (body.request.request_date > SAMPLE_TODAY) return sample(LOOKUPS.errors.future_request_date);
    const template = lookupSampleFor(body);
    const id = this.nextLookupId++;
    const now = new Date().toISOString();
    const lookup: LookupOut = {
      ...copy(template),
      id,
      created_at: now,
      finished_at: now,
      user_id: account.id,
      username: account.username,
      request: { ...body.request },
      report: null,
    };
    this.lookups.set(id, lookup);
    return [201, lookup];
  }

  private getLookup(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = this.personLevel(call, host);
    if (Array.isArray(account)) return account;
    const lookup = this.lookups.get(id);
    if (!this.visible(account, lookup) || !lookup) return sample(LOOKUPS.errors.other_users_lookup);
    return [200, omit(lookup, 'elapsed_ms')];
  }

  private listLookups(call: RecordedCall, host: MockHost): Answer {
    const account = this.personLevel(call, host);
    if (Array.isArray(account)) return account;
    const allUsers = account.role === 'admin' && call.query.get('mine') !== 'true';
    const items: LookupSummary[] = [...this.lookups.values()]
      .filter((lookup) => allUsers || lookup.user_id === account.id)
      .sort((a, b) => b.id - a.id)
      .map((lookup) => ({
        id: lookup.id,
        created_at: lookup.created_at,
        user_id: lookup.user_id,
        username: lookup.username,
        authority: lookup.request.authority,
        file_no: lookup.request.file_no,
        request_date: lookup.request.request_date,
        mode: lookup.mode,
        result_count: lookup.result_count,
        report_sha256: lookup.report?.sha256 ?? null,
      }));
    return [200, { items, next_cursor: null, all_users: allUsers }];
  }

  private reportPdf(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = this.personLevel(call, host);
    if (Array.isArray(account)) return account;
    const lookup = this.lookups.get(id);
    if (!this.visible(account, lookup) || !lookup) return sample(LOOKUPS.errors.other_users_lookup);
    const hash = sha256(PDF);
    if (!lookup.report) {
      // The captured report of the same kind: stamped (mock TSA) for candidate lookups.
      const source =
        lookup.mode === 'candidates' ? LOOKUPS.candidates_with_report : LOOKUPS.exact_with_report;
      const report = copy(source.report);
      if (!report) throw new Error('report sample missing');
      lookup.report = {
        ...report,
        sha256: hash,
        size: PDF.length,
        created_at: new Date().toISOString(),
        username: account.username,
        download_count: 0,
      };
    }
    lookup.report.download_count += 1;
    return [
      200,
      null,
      {
        body: PDF,
        contentType: 'application/pdf',
        headers: {
          'Content-Disposition': `attachment; filename="ARCILOGGER-5651-rapor-${id}.pdf"`,
          'X-Arci-Sha256': hash,
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  private reportToken(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = this.personLevel(call, host);
    if (Array.isArray(account)) return account;
    const lookup = this.lookups.get(id);
    if (!this.visible(account, lookup) || lookup?.report?.timestamp_status !== 'stamped')
      return host.error(404, 'not_found');
    return [
      200,
      null,
      {
        body: Buffer.from(SEALS.token_file.base64, 'base64'),
        contentType: 'application/timestamp-reply',
        headers: {
          'Content-Disposition': `attachment; filename="ARCILOGGER-5651-rapor-${id}.pdf.zd"`,
        },
      },
    ];
  }

  private raw(call: RecordedCall, host: MockHost): Answer {
    const account = this.personLevel(call, host, ['lookup.run', 'search.read']);
    if (Array.isArray(account)) return account;
    const key = `${call.query.get('source') ?? ''}|${call.query.get('day') ?? ''}|${call.query.get('line') ?? ''}`;
    const line = RAW.lines[key];
    return line ? [200, copy(line)] : sample(RAW.errors.raw_not_found);
  }

  // -- evidence packages ------------------------------------------------------------------------

  /** The package as the API shows it now (a new one moves queued → building → ready). */
  private current(id: number): (EvidenceDetail & { readyAt?: number }) | undefined {
    const item = this.packages.get(id);
    if (!item?.readyAt) return item;
    const left = item.readyAt - Date.now();
    const status = left <= 0 ? 'ready' : left > this.buildMs / 2 ? 'queued' : 'building';
    if (status !== item.status) {
      item.status = status;
      const labels = EVIDENCE.status_labels[status];
      item.status_label_tr = labels?.tr ?? status;
      item.status_label_en = labels?.en ?? status;
      if (status === 'ready') {
        const ready = EVIDENCE.details['1'];
        if (!ready) throw new Error('ready package sample missing');
        Object.assign(item, {
          package_name: `KP-E2E-${String(id).padStart(4, '0')}`,
          file: `ARCILOGGER-kanit-KP-E2E-${id}.zip`,
          size: ZIP.length,
          sha256: sha256(ZIP),
          index_sha256: ready.index_sha256,
          verifier_sha256: ready.verifier_sha256,
          legal_grade: ready.legal_grade,
          finished_at: new Date().toISOString(),
          downloadable: true,
        });
        delete item.readyAt;
      }
    }
    return item;
  }

  /** The lookup visibility rule applied to a package (`routers.evidence.visible_packages`). */
  private packageVisible(account: MockUser, item: EvidenceOut): boolean {
    const lookupId = item.lookup_id ?? item.scope.lookup_id;
    if (account.role === 'admin' || lookupId === null) return true;
    return this.lookups.get(lookupId)?.user_id === account.id;
  }

  private out(item: EvidenceDetail): EvidenceOut {
    return omit(item, 'extract', 'downloads', 'readyAt');
  }

  private listPackages(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'evidence.read');
    if (Array.isArray(account)) return account;
    const items = [...this.packages.keys()]
      .sort((a, b) => b - a)
      .map((id) => this.out(this.current(id) as EvidenceDetail))
      .filter((item) => this.packageVisible(account, item));
    return [200, { items, next_cursor: null }];
  }

  private getPackage(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = host.requirePermission(call, 'evidence.read');
    if (Array.isArray(account)) return account;
    const item = this.current(id);
    if (!item || !this.packageVisible(account, item)) return host.error(404, 'not_found');
    return [200, copy(omit(item, 'readyAt'))];
  }

  private createPackage(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'evidence.create', true);
    if (Array.isArray(account)) return account;
    if (this.refuseNextCreate) {
      const refusal = this.refuseNextCreate;
      this.refuseNextCreate = null;
      return sample(refusal);
    }
    const body = call.body as EvidenceCreate;
    const template = copy(body.password ? EVIDENCE.created_password : EVIDENCE.created);
    const id = this.nextPackageId++;
    const now = new Date();
    const lookupId = body.lookup_id ?? null;
    const lookup = lookupId === null ? undefined : this.lookups.get(lookupId);
    if (lookupId !== null && !this.visible(account, lookup))
      return sample(LOOKUPS.errors.other_users_lookup);
    const pkg: EvidenceOut = {
      ...template.package,
      id,
      created_at: now.toISOString(),
      created_by: account.username,
      user_id: account.id,
      lookup_id: lookupId,
      status: 'queued',
      status_label_tr: EVIDENCE.status_labels.queued?.tr ?? 'Sırada',
      status_label_en: EVIDENCE.status_labels.queued?.en ?? 'Queued',
      password_protected: Boolean(body.password),
      scope: {
        ...template.package.scope,
        content: body.content ?? 'full_days',
        days: lookup
          ? template.package.scope.days
          : body.from && body.to
            ? [body.from, body.to].filter((day, index, all) => all.indexOf(day) === index)
            : template.package.scope.days,
        sources: body.sources ?? null,
        lookup_id: lookupId,
        reference:
          body.reference ??
          (lookup ? `${lookup.request.authority} — ${lookup.request.file_no}` : null),
        extract_lines: body.extract?.length ?? template.package.scope.extract_lines,
      },
    };
    this.packages.set(id, {
      ...pkg,
      extract: body.extract ?? [],
      downloads: [],
      readyAt: now.getTime() + this.buildMs,
    });
    return [
      202,
      {
        package: pkg,
        password: template.password,
        password_notice_tr: template.password_notice_tr,
        password_notice_en: template.password_notice_en,
      },
    ];
  }

  private downloadPackage(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = host.requirePermission(call, 'evidence.download', true);
    if (Array.isArray(account)) return account;
    if (!account.privacy_accepted) return host.error(403, 'privacy_notice_required');
    const item = this.current(id);
    if (!item || !this.packageVisible(account, item)) return host.error(404, 'not_found');
    if (item.status === 'failed') return sample(EVIDENCE.errors.download_failed_package);
    // Like the API: past its expiry a package is gone even before the purge marks it.
    if (item.status !== 'ready' || !item.downloadable) return sample(EVIDENCE.errors.download_gone);
    item.download_count += 1;
    item.downloads.unshift({
      time: new Date().toISOString(),
      username: account.username,
      ip: '172.20.10.37',
    });
    const body = item.size === ZIP.length ? ZIP : Buffer.alloc(item.size ?? 0, 0);
    return [
      200,
      null,
      {
        body,
        contentType: 'application/zip',
        headers: {
          'Content-Disposition': `attachment; filename="${item.file ?? `paket-${id}.zip`}"`,
          'Repr-Digest': `sha-256=:${createHash('sha256').update(body).digest('base64')}:`,
          'Accept-Ranges': 'none',
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  // -- sealing ----------------------------------------------------------------------------------

  private calendar(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'seal.read');
    if (Array.isArray(account)) return account;
    const month = call.query.get('month');
    if (!month) return [200, copy(SEALS.calendar_current)];
    return [200, copy(SEALS.calendars[month] ?? emptyMonth(month))];
  }

  private seal(call: RecordedCall, host: MockHost, day: string, action: string | null): Answer {
    const detail = SEALS.days[day];
    if (action === 'seal') {
      const account = host.requirePermission(call, 'seal.manage');
      return Array.isArray(account) ? account : sample(SEALS.errors.seal_now_unavailable);
    }
    if (action === 'verify') {
      const account = host.requirePermission(call, 'seal.verify');
      if (Array.isArray(account)) return account;
      if (!detail?.sealed) return sample(SEALS.errors.verify_not_sealed);
      const failed = this.nextVerify === 'failed';
      this.nextVerify = null;
      const result = copy(failed ? EVIDENCE.verify_tampered : SEALS.verify);
      const [year, month, date] = day.split('-');
      const shown = `${date ?? ''}.${month ?? ''}.${year ?? ''}`;
      const original = result.day.split('-').reverse().join('.');
      result.day = day;
      result.deep = call.query.get('deep') !== 'false';
      result.message_tr = result.message_tr.replace(original, shown);
      result.message_en = result.message_en.replace(original, shown);
      return [200, result];
    }
    const account = host.requirePermission(call, 'seal.read');
    if (Array.isArray(account)) return account;
    if (!detail) return host.error(404, 'not_found');
    if (action === 'manifest' || action === 'token') {
      if (!detail.sealed) return host.error(404, 'not_found');
      const body =
        action === 'manifest'
          ? Buffer.from(SEALS.manifest_file.text, 'utf8')
          : Buffer.from(SEALS.token_file.base64, 'base64');
      const name = action === 'manifest' ? `manifest-${day}.json` : `manifest-${day}.json.zd`;
      return [
        200,
        null,
        {
          body,
          contentType: action === 'manifest' ? 'application/json' : 'application/octet-stream',
          headers: { 'Content-Disposition': `attachment; filename="${name}"` },
        },
      ];
    }
    return [200, copy(detail)];
  }
}
