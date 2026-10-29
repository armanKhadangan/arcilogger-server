/**
 * The mocked Wave 4 API of the E2E tests: Uyum Durumu (`/compliance`, Wave 4 contract 2.2, 2.3)
 * and Sistem (`/system`, `/system/disk`, `/system/health`, 2.5, 2.6) on the answers captured from
 * the real API (`samples/compliance.json`, `system.json`, `system-disk.json`,
 * `system-health.json`), with the permission rules of their routers: the cards and the report need
 * `compliance.read` (administrator and auditor, D15), the configuration backup confirmation
 * `settings.manage` and the CSRF header (as every change), the Sistem endpoints `system.read`
 * (administrators only). Nothing here is licence-gated (5651 Temel), so the main mock's licence
 * is never asked.
 * Installed after `mockApi` (`page.route`: the newest route wins), it shares the main mock's
 * session, users and recorded calls (`background` is the `X-Arci-Background: 1` of a refresh).
 *
 * - `GET /compliance`: the captured cards (card 7 red with its `R20-filter` alert, card 10 waiting
 *   for the configuration copy); after a confirmation the captured cards after it (card 10 ok);
 * - `GET /compliance/report.pdf`: a small fixed PDF named as the capture's `Content-Disposition`,
 *   whose `X-Arci-Sha256` is computed here from its bytes (the capture keeps headers, size and
 *   hash of the real report, not the file), so the page's check of the download passes;
 * - `POST /compliance/config-backup`: the captured confirmation, or the captured 409
 *   `config_backup_unreadable` while {@link W4Mock.configUnreadable} is set;
 * - `GET /system`: the captured summary of a Windows host (drives `D:` and `C:`), or the first
 *   reading of a process (`cpu_percent` null) with {@link W4Options.firstReading};
 * - `GET /system/disk`: the captured drives (data drive `estimated`, system drive
 *   `insufficient`), or the data drive `not_growing` with {@link W4Options.diskNotGrowing} (the
 *   summary's drives then the same);
 * - `GET /system/health`: the captured services, tasks, jobs and clock;
 * - refusals are the captured bodies (`forbidden`), other paths 404 and other methods 405.
 */

import { createHash } from 'node:crypto';

import type { Page, Request, Route } from '@playwright/test';

import type { ApiErrorBody } from '../src/lib/api-types';
import type { ComplianceStatus, ConfigBackupOut } from '../src/lib/compliance-types';
import type { SystemHealth } from '../src/lib/system-health-types';
import type { SystemDisk, SystemSummary } from '../src/lib/system-types';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import type { Answer } from './mock-records';
import { COMPLIANCE, copy, type ErrorSample, SYSTEM, SYSTEM_DISK, SYSTEM_HEALTH } from './samples';

/** The URL patterns this mock answers (`page.route`). */
const PATTERNS = ['**/api/v1/compliance**', '**/api/v1/system**'];

/** The report's bytes: fixed, not the real PDF (the pages only download and check it). */
export const COMPLIANCE_PDF = Buffer.from('%PDF-1.7\n% ARCILOGGER E2E Uyum Durumu Raporu\n%%EOF\n');
/** Its SHA-256 (`X-Arci-Sha256`). */
export const COMPLIANCE_PDF_SHA256 = createHash('sha256').update(COMPLIANCE_PDF).digest('hex');

export interface W4Options {
  /** The cards before a confirmation (default: the capture's `admin`). */
  compliance?: ComplianceStatus;
  /** The cards after a confirmation (default: the capture's `after_backup`). */
  afterBackup?: ComplianceStatus;
  /** The configuration file cannot be read: the confirmation is refused (409). */
  configUnreadable?: boolean;
  /** The Sistem summary (default: the capture's `summary`, or `summary_first_reading`). */
  system?: SystemSummary;
  /** The first processor reading of the API process: `cpu_percent` null. */
  firstReading?: boolean;
  /** The drives (default: the capture's `disk`, or `disk_not_growing`). */
  disk?: SystemDisk;
  /** The data drive did not grow in the last 30 days (`not_growing`), in the summary too. */
  diskNotGrowing?: boolean;
  /** Services, tasks, jobs and clock (default: the capture's `health`). */
  health?: SystemHealth;
}

export class W4Mock {
  compliance: ComplianceStatus;
  afterBackup: ComplianceStatus;
  configUnreadable: boolean;
  system: SystemSummary;
  disk: SystemDisk;
  health: SystemHealth;
  /** Confirmations the mock recorded (each answered 200). */
  confirmations = 0;
  /** Reports the mock served. */
  reports = 0;

  constructor(
    private readonly api: MockApi,
    options: W4Options = {},
  ) {
    this.compliance = options.compliance ?? copy(COMPLIANCE.admin);
    this.afterBackup = options.afterBackup ?? copy(COMPLIANCE.after_backup);
    this.configUnreadable = options.configUnreadable ?? false;
    this.disk =
      options.disk ??
      copy(options.diskNotGrowing ? SYSTEM_DISK.disk_not_growing : SYSTEM_DISK.disk);
    this.system =
      options.system ?? copy(options.firstReading ? SYSTEM.summary_first_reading : SYSTEM.summary);
    if (options.diskNotGrowing && !options.system) this.system.drives = copy(this.disk.drives);
    this.health = options.health ?? copy(SYSTEM_HEALTH.health);
  }

  async install(page: Page): Promise<void> {
    for (const pattern of PATTERNS)
      await page.route(pattern, (route, request) => this.handle(route, request));
  }

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const headers = request.headers();
    const call: RecordedCall = {
      method: request.method(),
      path: url.pathname.replace(/^\/api\/v1/, ''),
      query: url.searchParams,
      body: undefined,
      raw: null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.api.calls.push(call);
    const [status, payload, file] = this.answer(call);
    if (file) {
      await route.fulfill({
        status,
        contentType: file.contentType,
        headers: file.headers,
        body: file.body,
      });
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify(payload),
    });
  }

  /** A refusal as the API answered it (the endpoint's captured samples), else the main mock's. */
  private error(
    status: number,
    code: string,
    errors: Record<string, ErrorSample> = COMPLIANCE.errors,
  ): [number, unknown] {
    const sample = errors[code];
    return sample ? [sample.status, copy(sample.body)] : this.api.error(status, code);
  }

  /** The signed-in user when they hold `permission`; the refusal otherwise. */
  private allowed(
    call: RecordedCall,
    permission: string,
    errors: Record<string, ErrorSample> = COMPLIANCE.errors,
  ): MockUser | [number, unknown] {
    const account = this.api.requirePermission(call, permission);
    if (!Array.isArray(account)) return account;
    const [status, body] = account;
    const code = (body as ApiErrorBody | null)?.error.code;
    return status === 403 && code === 'forbidden' ? this.error(403, code, errors) : account;
  }

  private answer(call: RecordedCall): Answer {
    switch (call.path) {
      case '/compliance':
        return this.only(call, 'GET') ?? this.status(call);
      case '/compliance/report.pdf':
        return this.only(call, 'GET') ?? this.report(call);
      case '/compliance/config-backup':
        return this.only(call, 'POST') ?? this.confirm(call);
      case '/system':
        return this.only(call, 'GET') ?? this.sistem(call, this.system, SYSTEM.errors);
      case '/system/disk':
        return this.only(call, 'GET') ?? this.sistem(call, this.disk, SYSTEM_DISK.errors);
      case '/system/health':
        return this.only(call, 'GET') ?? this.sistem(call, this.health, SYSTEM_HEALTH.errors);
    }
    return this.api.error(404, 'not_found');
  }

  private only(call: RecordedCall, method: string): [number, unknown] | null {
    return call.method === method ? null : this.api.error(405, 'method_not_allowed');
  }

  /** The cards (the same for every holder of `compliance.read`, as the API's kept answer). */
  private status(call: RecordedCall): Answer {
    const account = this.allowed(call, 'compliance.read');
    if (Array.isArray(account)) return account;
    return [200, copy(this.confirmations > 0 ? this.afterBackup : this.compliance)];
  }

  private report(call: RecordedCall): Answer {
    const account = this.allowed(call, 'compliance.read');
    if (Array.isArray(account)) return account;
    this.reports += 1;
    const disposition = COMPLIANCE.report_pdf.headers['content-disposition'];
    if (!disposition) throw new Error('report_pdf sample without Content-Disposition');
    return [
      200,
      null,
      {
        body: COMPLIANCE_PDF,
        contentType: 'application/pdf',
        headers: {
          'Content-Disposition': disposition,
          'X-Arci-Sha256': COMPLIANCE_PDF_SHA256,
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  /** A Sistem answer (`system.read`, administrators only; read fresh, never cached). */
  private sistem(
    call: RecordedCall,
    payload: SystemSummary | SystemDisk | SystemHealth,
    errors: Record<string, ErrorSample>,
  ): Answer {
    const account = this.allowed(call, 'system.read', errors);
    if (Array.isArray(account)) return account;
    return [200, copy(payload)];
  }

  private confirm(call: RecordedCall): Answer {
    const account = this.allowed(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    if (this.configUnreadable) return this.error(409, 'config_backup_unreadable');
    this.confirmations += 1;
    const confirmed: ConfigBackupOut = copy(COMPLIANCE.config_backup);
    return [200, confirmed];
  }
}

/** Install the Wave 4 mock on top of `api` (call after `mockApi`). */
export async function mockW4(page: Page, api: MockApi, options: W4Options = {}): Promise<W4Mock> {
  const w4 = new W4Mock(api, options);
  await w4.install(page);
  return w4;
}
