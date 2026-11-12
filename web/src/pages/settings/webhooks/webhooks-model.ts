/**
 * The webhooks view (Wave 6 contract §2.4.4, W6-F): read-only, as the webhooks are configured on
 * the server only (D4). No components here; unit-tested in `webhooks-model.test.ts`.
 */

import type { LicenseOut } from '@/lib/api-types';
import { moduleOf } from '@/lib/license';
import type { AdminRequestOut } from '@/lib/settings-types';
import type { WebhookFormat, WebhookOut, WebhookTestResult } from '@/lib/webhook-types';

/** The licence module of the webhooks and the rules (D1). */
export const RULES_MODULE = 'security_rules';

/** The payloads of Teams and Slack are not confirmed on a real target ("sahada doğrula", V8). */
export function formatVerified(format: WebhookFormat): boolean {
  return format === 'generic';
}

/** Webhooks by name. */
export function sortWebhooks(list: readonly WebhookOut[]): WebhookOut[] {
  return [...list].sort((a, b) => a.name.localeCompare(b.name, 'en'));
}

/**
 * Whether "Test gönder" is locked by the licence: only when the licence names the module and it
 * is off (a licence of a version without the module locks nothing here; the API decides).
 */
export function testLocked(license: LicenseOut | null | undefined): boolean {
  const module = moduleOf(license, RULES_MODULE);
  return module !== undefined && !module.enabled;
}

/** The test's outcome from its request's `result`, `null` while there is none. */
export function testResultOf(request: Pick<AdminRequestOut, 'result'>): WebhookTestResult | null {
  const result = request.result as Partial<WebhookTestResult> | null;
  if (!result || typeof result.ok !== 'boolean') return null;
  return {
    ok: result.ok,
    status: typeof result.status === 'number' ? result.status : null,
    error_text: typeof result.error_text === 'string' ? result.error_text : null,
  };
}
