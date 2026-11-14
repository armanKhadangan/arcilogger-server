/**
 * The webhooks API of Wave 6 (`design/wave-6-contract.md` §2.4.4, W6-H's
 * `arcilogger_api.routers.webhooks`): the webhooks configured on the server (D4: never from the
 * browser), their deliveries and "Test gönder".
 *
 * Written from the contract while W6-H's API is on its way; `api-types.test.ts` checks this file
 * against the OpenAPI document once the API is in the generated contract.
 */

import type { AlertSeverity, DateTimeString } from './api-types';

/** `generic` is signed (HMAC); `teams` and `slack` are "sahada doğrula" (V8). */
export type WebhookFormat = 'generic' | 'teams' | 'slack';

/** A row of `alert_deliveries` with `kind = 'webhook'` (D10). */
export type WebhookDeliveryStatus = 'queued' | 'sent' | 'failed';

export interface WebhookLastDelivery {
  time: DateTimeString;
  status: WebhookDeliveryStatus;
}

/** Deliveries of the last 7 days by status. */
export interface WebhookCounts {
  sent: number;
  failed: number;
  queued: number;
}

export interface WebhookOut {
  name: string;
  format: WebhookFormat;
  /** The URL's host only: never its path, query or a secret. */
  host: string;
  min_severity: AlertSeverity;
  /** The rules it is sent for; empty: every rule, R15 and R20 included. */
  rules: string[];
  enabled: boolean;
  last_delivery: WebhookLastDelivery | null;
  counts_7d: WebhookCounts;
}

/** `GET /webhooks` (`alerts.read`). */
export interface WebhookList {
  webhooks: WebhookOut[];
}

export interface WebhookDelivery {
  alert_id: number;
  rule: string;
  severity: AlertSeverity;
  status: WebhookDeliveryStatus;
  attempts: number;
  /** The status code and at most 300 characters of the reply; no URL query, secret or header. */
  last_error: string | null;
  next_attempt_at: DateTimeString | null;
  created_at: DateTimeString;
  sent_at: DateTimeString | null;
}

/** `GET /webhooks/{name}/deliveries?limit=50` (`alerts.read`), newest first. */
export interface WebhookDeliveries {
  name: string;
  items: WebhookDelivery[];
}

/** The `result` of the test's request (the probe `webhook`). */
export interface WebhookTestResult {
  ok: boolean;
  /** The HTTP status code of the answer, `null` when none came. */
  status: number | null;
  /** The error text of §2.4.2, `null` on success. */
  error_text: string | null;
}
