/**
 * Answers of the webhooks API (Wave 6 contract §2.4.4), written from the contract until W6-H's API
 * is captured: three webhooks of the example world (`*.ornek.example`) on the sample day
 * (13.10.2026), one of each format, the Slack one switched off, and the deliveries of `siem`.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-webhooks.ts`), so it imports nothing with
 * the `@/` alias and reads no file.
 */

import type { WebhookDeliveries, WebhookList, WebhookTestResult } from '../lib/webhook-types';

export const WEBHOOKS: WebhookList = {
  webhooks: [
    {
      name: 'siem',
      format: 'generic',
      host: 'siem.ornek.example',
      min_severity: 'high',
      rules: [],
      enabled: true,
      last_delivery: { time: '2026-10-13T08:43:40Z', status: 'sent' },
      counts_7d: { sent: 12, failed: 1, queued: 0 },
    },
    {
      name: 'teams-guvenlik',
      format: 'teams',
      host: 'ornek.webhook.office.example',
      min_severity: 'critical',
      rules: ['R06', 'R07'],
      enabled: true,
      last_delivery: null,
      counts_7d: { sent: 0, failed: 0, queued: 0 },
    },
    {
      name: 'slack-bt',
      format: 'slack',
      host: 'hooks.slack.ornek.example',
      min_severity: 'medium',
      rules: [],
      enabled: false,
      last_delivery: { time: '2026-10-10T14:02:11Z', status: 'failed' },
      counts_7d: { sent: 0, failed: 3, queued: 0 },
    },
  ],
};

export const SIEM_DELIVERIES: WebhookDeliveries = {
  name: 'siem',
  items: [
    {
      alert_id: 9001,
      rule: 'R01',
      severity: 'high',
      status: 'sent',
      attempts: 1,
      last_error: null,
      next_attempt_at: null,
      created_at: '2026-10-13T08:43:02Z',
      sent_at: '2026-10-13T08:43:40Z',
    },
    {
      alert_id: 8990,
      rule: 'R07',
      severity: 'high',
      status: 'failed',
      attempts: 9,
      last_error: '503 Service Unavailable: upstream is restarting',
      next_attempt_at: null,
      created_at: '2026-10-11T06:10:00Z',
      sent_at: null,
    },
    {
      alert_id: 8988,
      rule: 'R20-disk',
      severity: 'critical',
      status: 'queued',
      attempts: 2,
      last_error: '408 Request Timeout',
      next_attempt_at: '2026-10-13T09:05:00Z',
      created_at: '2026-10-13T08:49:00Z',
      sent_at: null,
    },
  ],
};

/** The probe's results (§2.4.4): the target's status code, or the error text of §2.4.2. */
export const WEBHOOK_TEST_OK: WebhookTestResult = { ok: true, status: 204, error_text: null };
export const WEBHOOK_TEST_FAILED: WebhookTestResult = {
  ok: false,
  status: 401,
  error_text: '401 Unauthorized: signature rejected',
};
