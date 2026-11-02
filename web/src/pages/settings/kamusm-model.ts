/**
 * The Kamu SM wizard (contract 2.17): its steps, what each step's state is from the
 * configuration's `readiness` and `seal_ready`, the body of `PUT /admin/config/tsa`, and the
 * browser's checks of the two credentials (contract 2.6 "tsa"). The server decides again.
 */

import type { SecretEdit } from '@/components/admin/secret-edit';
import type { TsaBody, TsaSection, TsaServer } from '@/lib/settings-types';
import { codePointLength } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

export const STEPS = [
  'java',
  'zamane',
  'roots',
  'credentials',
  'test',
  'production',
  'credit',
] as const;
export type StepId = (typeof STEPS)[number];

/** `ok`, `missing` (to do), `error` (stops sealing now), `pending` (waits), `optional`. */
export type StepStatus = 'ok' | 'missing' | 'error' | 'pending' | 'optional';

export function isStep(value: string | null): value is StepId {
  return value !== null && (STEPS as readonly string[]).includes(value);
}

/** What a step shows (contract 2.17: "each step shows ok / missing / error"). */
export function stepStatus(step: StepId, tsa: TsaSection, tested: boolean): StepStatus {
  const missing = new Set<string>(tsa.readiness.missing);
  const causes = new Set(tsa.seal_ready.causes);
  switch (step) {
    case 'java':
      if (tsa.java && causes.has('executable')) return 'error';
      return !tsa.java || missing.has('java') ? 'missing' : 'ok';
    case 'zamane':
      if (causes.has('zamane-jar') || (tsa.zamane.present && tsa.zamane.trusted === false))
        return 'error';
      if (tsa.zamane.staged.length > 0) return 'pending';
      return !tsa.zamane.present || missing.has('zamane_jar') ? 'missing' : 'ok';
    case 'roots':
      if (causes.has('roots')) return 'error';
      return tsa.roots.length === 0 || missing.has('roots') ? 'missing' : 'ok';
    case 'credentials': {
      if (causes.has('credentials')) return 'error';
      const set =
        (tsa.secrets['kamusm.customer']?.set ?? false) &&
        (tsa.secrets['kamusm.password']?.set ?? false);
      return set && !missing.has('credentials') ? 'ok' : 'missing';
    }
    case 'test':
      return tested ? 'ok' : 'optional';
    case 'production':
      return tsa.provider === 'zamane' && tsa.server === 'production' ? 'ok' : 'optional';
    case 'credit':
      return 'ok';
  }
}

/** The values the wizard edits for `PUT /admin/config/tsa` (numbers as typed). */
export interface TsaForm {
  java: string | null;
  threshold: string;
  timeout: string;
}

export function tsaForm(tsa: TsaSection | undefined): TsaForm | undefined {
  if (!tsa) return undefined;
  return {
    java: tsa.java,
    threshold: String(tsa.credit_alert_threshold),
    timeout: String(tsa.timeout_seconds),
  };
}

export interface Credentials {
  customer: SecretEdit;
  password: SecretEdit;
  /** "Sil": both credentials are deleted (`clear_credentials`). */
  clear: boolean;
}

/**
 * The body of `PUT /admin/config/tsa`: everything the wizard edits at once (the Java chosen in
 * the first step is saved with the credentials, so a first setup from the mock provider can
 * switch to Zamane in one change that leaves sealing working).
 */
export function tsaBody(
  tsa: TsaSection,
  etag: string,
  form: TsaForm,
  credentials: Credentials,
  options: { server?: TsaServer; confirm?: boolean; probe: boolean },
): TsaBody {
  const customer = credentials.customer;
  const password = credentials.password;
  return {
    etag,
    server: options.server ?? tsa.server ?? 'test',
    credit_alert_threshold: parseInteger(form.threshold) ?? tsa.credit_alert_threshold,
    timeout_seconds: parseInteger(form.timeout) ?? tsa.timeout_seconds,
    java: form.java,
    ...(customer.editing && customer.value !== '' ? { customer: customer.value } : {}),
    ...(password.editing && password.value !== '' ? { password: password.value } : {}),
    ...(credentials.clear ? { clear_credentials: true } : {}),
    ...(options.confirm ? { confirm: true } : {}),
    probe: options.probe,
  };
}

/** Control and format characters: never part of a credential. */
const CONTROL = /[\p{Cc}\p{Cf}]/u;

/** `kamusm.customer`: 1–64 printable characters, no white space (format confirmed on site). */
export function validateCustomer(value: string): string | undefined {
  if (value === '') return 'validation.required';
  if (/\s/u.test(value) || CONTROL.test(value) || codePointLength(value) > 64)
    return 'settings.kamusm.errors.customer';
  return undefined;
}

/** `kamusm.password`: 1–128 characters, no control characters. */
export function validatePassword(value: string): string | undefined {
  if (value === '') return 'validation.required';
  if (CONTROL.test(value) || codePointLength(value) > 128) return 'settings.kamusm.errors.password';
  return undefined;
}

/** The threshold and the timeout (`credit_alert_threshold` 0–1 000 000, `timeout_seconds` 5–600). */
export function validateNumbers(form: TsaForm): { threshold?: string; timeout?: string } {
  const threshold = integerError(form.threshold, 0, 1_000_000);
  const timeout = integerError(form.timeout, 5, 600);
  return { ...(threshold ? { threshold } : {}), ...(timeout ? { timeout } : {}) };
}

/** 64 hexadecimal characters (the SHA-256 Kamu SM publishes), spaces and case ignored. */
export function normalizeSha256(text: string): string | null {
  const value = text.replace(/\s+/g, '').toLowerCase();
  return /^[0-9a-f]{64}$/.test(value) ? value : null;
}

/**
 * The causes of `seal_ready` (`seal.CONFIG_CAUSES`, with WP2's `executable` and `location`). Each
 * has a text; `kamusm-model.test.ts` checks the list against `core/.../seal.py`.
 */
export const SEAL_CAUSES = [
  'zamane-jar',
  'credentials',
  'roots',
  'secret-store',
  'provider',
  'executable',
  'location',
] as const;

/** The step that shows a cause as its error (`stepStatus`); the others have none. */
const CAUSE_STEPS: Partial<Record<string, StepId>> = {
  executable: 'java',
  'zamane-jar': 'zamane',
  roots: 'roots',
  credentials: 'credentials',
};

/**
 * The causes that stop sealing but are no step's error (a moved secret store or data folder, an
 * unreadable secret store, an invalid provider): every step may read "Tamam" while the Seal task
 * cannot seal, so the page names them above the steps.
 */
export function unplacedCauses(tsa: TsaSection): string[] {
  return tsa.seal_ready.causes.filter((cause) => CAUSE_STEPS[cause] === undefined);
}

/** The text key of a sealing cause (`SEAL_CAUSES`; an unknown one shows its code). */
export function causeKey(cause: string): string {
  return `settings.kamusm.causes.${cause.replace(/-/g, '_')}`;
}
