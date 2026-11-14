/**
 * The mocked webhooks API of Wave 6 (contract §2.4.4) for the E2E tests, from the contract's
 * JSON (`src/test/webhook-samples.ts`) until W6-H's API is captured:
 *
 * - `GET /webhooks` and `GET /webhooks/{name}/deliveries`: `alerts.read`; 404
 *   `webhook_not_found` for a name not configured;
 * - `POST /webhooks/{name}/test`: `settings.manage`; queues the probe as an administration
 *   request (`mock-admin.ts`), which succeeds, or fails with `test: 'failed'`.
 */

import type { RecordedCall } from './mock-api';
import type { AdminMock } from './mock-admin';
import type { Answer, MockHost } from './mock-records';
import { copy } from './samples';
import {
  SIEM_DELIVERIES,
  WEBHOOK_TEST_FAILED,
  WEBHOOK_TEST_OK,
  WEBHOOKS,
} from '../src/test/webhook-samples';

export interface WebhooksOptions {
  /** No webhook configured. */
  none?: boolean;
  /** The next tests fail (the target refuses the signature). */
  test?: 'failed';
}

export class WebhooksMock {
  constructor(
    private readonly admin: AdminMock,
    private readonly options: WebhooksOptions = {},
  ) {}

  private list() {
    return this.options.none ? [] : WEBHOOKS.webhooks;
  }

  answer(call: RecordedCall, host: MockHost): Answer | null {
    const { method, path } = call;
    if (method === 'GET' && path === '/webhooks') {
      const account = host.requirePermission(call, 'alerts.read');
      return Array.isArray(account) ? account : [200, { webhooks: copy(this.list()) }];
    }
    const match = /^\/webhooks\/([^/]+)\/(deliveries|test)$/.exec(path);
    if (!match) return null;
    const name = decodeURIComponent(match[1] ?? '');
    if (method === 'GET' && match[2] === 'deliveries') {
      const account = host.requirePermission(call, 'alerts.read');
      if (Array.isArray(account)) return account;
      if (!this.list().some((item) => item.name === name)) return this.notFound();
      return [200, name === SIEM_DELIVERIES.name ? copy(SIEM_DELIVERIES) : { name, items: [] }];
    }
    if (method === 'POST' && match[2] === 'test') {
      const account = host.requirePermission(call, 'settings.manage');
      if (Array.isArray(account)) return account;
      if (!this.list().some((item) => item.name === name)) return this.notFound();
      const failed = this.options.test === 'failed';
      return this.admin.webhookTest(
        account,
        { ...(failed ? WEBHOOK_TEST_FAILED : WEBHOOK_TEST_OK) },
        failed,
      );
    }
    return null;
  }

  private notFound(): Answer {
    return [
      404,
      {
        error: {
          code: 'webhook_not_found',
          message_tr: 'Böyle bir webhook yok.',
          message_en: 'No such webhook.',
        },
      },
    ];
  }
}
