/**
 * The mocked Wave 4 API of the E2E tests: Uyum Durumu (`/compliance`, Wave 4 contract 2.2, 2.3)
 * on the answers captured from the real API (`samples/compliance.json`), with the permission rules
 * of its router: the cards and the report need `compliance.read` (administrator and auditor,
 * D15), the configuration backup confirmation `settings.manage` and the CSRF header (as every
 * change). Nothing here is licence-gated (5651 Temel), so the main mock's licence is never asked.
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
 * - refusals are the captured bodies (`forbidden`), other paths 404 and other methods 405.
 *
 * Sistem (`/system`, `/system/disk`, `/system/health`, contract 2.5, 2.6) joins this mock with
 * its captured samples: its URL pattern goes into {@link PATTERNS} and its paths into
 * {@link W4Mock.answer}, each behind `system.read`.
 */

import { createHash } from 'node:crypto';

import type { Page, Request, Route } from '@playwright/test';

import type { ApiErrorBody } from '../src/lib/api-types';
import type { ComplianceStatus, ConfigBackupOut } from '../src/lib/compliance-types';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import type { Answer } from './mock-records';
import { COMPLIANCE, copy } from './samples';

/** The URL patterns this mock answers (`page.route`). */
const PATTERNS = ['**/api/v1/compliance**'];

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
}

export class W4Mock {
  compliance: ComplianceStatus;
  afterBackup: ComplianceStatus;
  configUnreadable: boolean;
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

  /** A refusal as the API answered it (captured), else the main mock's. */
  private error(status: number, code: string): [number, unknown] {
    const sample = COMPLIANCE.errors[code];
    return sample ? [sample.status, copy(sample.body)] : this.api.error(status, code);
  }

  /** The signed-in user when they hold `permission`; the refusal otherwise. */
  private allowed(call: RecordedCall, permission: string): MockUser | [number, unknown] {
    const account = this.api.requirePermission(call, permission);
    if (!Array.isArray(account)) return account;
    const [status, body] = account;
    const code = (body as ApiErrorBody | null)?.error.code;
    return status === 403 && code === 'forbidden' ? this.error(403, code) : account;
  }

  private answer(call: RecordedCall): Answer {
    switch (call.path) {
      case '/compliance':
        return this.only(call, 'GET') ?? this.status(call);
      case '/compliance/report.pdf':
        return this.only(call, 'GET') ?? this.report(call);
      case '/compliance/config-backup':
        return this.only(call, 'POST') ?? this.confirm(call);
      // Sistem (`/system`, `/system/disk`, `/system/health`): with its captured samples.
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
