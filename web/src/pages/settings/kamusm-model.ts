/**
 * The time-stamp wizard, Ayarlar → Zaman damgası (contract 2.17; Wave 5 contract 2.4): its steps,
 * what each step's state is from the configuration's `readiness` and `seal_ready`, the servers it
 * can choose, the body of `PUT /admin/config/tsa`, and the browser's checks of the credentials
 * (contract 2.6 "tsa"). The server decides again.
 */

import { SECRET_KEPT, secretChanged, type SecretEdit } from '@/components/admin/secret-edit';
import type { BrowserSecret, SecretMeta, TsaBody, TsaSection } from '@/lib/settings-types';
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

/**
 * `ok`, `missing` (to do), `error` (stops sealing now), `pending` (waits), `optional`, `unused`
 * (the provider in use does not need it: Java and the Zamane client with TÜRKTRUST).
 */
export type StepStatus = 'ok' | 'missing' | 'error' | 'pending' | 'optional' | 'unused';

export function isStep(value: string | null): value is StepId {
  return value !== null && (STEPS as readonly string[]).includes(value);
}

/**
 * The servers the browser can choose (Wave 5 contract 2.4 "Browser administration",
 * `TSA_SERVERS`): the Kamu SM Zamane test and production servers, TÜRKTRUST over HTTPS, and
 * TÜRKTRUST over plain HTTP, which is accepted only with a typed confirmation.
 *
 * Interim until W5-T widens `TsaServer` (`settings-types.ts`, checked against the API's schema):
 * the TÜRKTRUST choices are known here only.
 */
export const SERVER_CHOICES = ['test', 'production', 'turktrust', 'turktrust-http'] as const;
export type ServerChoice = (typeof SERVER_CHOICES)[number];

export function isServerChoice(value: string | null | undefined): value is ServerChoice {
  return typeof value === 'string' && (SERVER_CHOICES as readonly string[]).includes(value);
}

/** The provider of a server: Kamu SM's Zamane servers, or TÜRKTRUST's. */
export function choiceProvider(choice: ServerChoice): 'zamane' | 'turktrust' {
  return choice === 'turktrust' || choice === 'turktrust-http' ? 'turktrust' : 'zamane';
}

/**
 * The server in use, `null` when it is none of the choices (the mock, another RFC 3161 server, a
 * Zamane server at another address) or cannot be told: until W5-T names TÜRKTRUST's servers in
 * `server`, a TÜRKTRUST site reads `null` there.
 */
export function currentChoice(tsa: TsaSection): ServerChoice | null {
  const server: string | null = tsa.server;
  return isServerChoice(server) && choiceProvider(server) === tsa.provider ? server : null;
}

/** The providers the browser chooses: a site on one of them keeps its server unless one is chosen. */
const CHOSEN_PROVIDERS: ReadonlySet<string> = new Set(['zamane', 'turktrust']);

/** Interim until W5-T adds them to `BrowserSecret`: TÜRKTRUST's credentials (contract 2.4). */
export type TurktrustSecret = 'turktrust.username' | 'turktrust.password';

/** What `tsa.secrets` says of a credential (whether it is stored, never its value). */
export function secretMeta(
  tsa: TsaSection,
  name: BrowserSecret | TurktrustSecret,
): SecretMeta | undefined {
  return (tsa.secrets as Partial<Record<string, SecretMeta>>)[name];
}

/** What a step shows (contract 2.17: "each step shows ok / missing / error"). */
export function stepStatus(step: StepId, tsa: TsaSection, tested: boolean): StepStatus {
  const missing = new Set<string>(tsa.readiness.missing);
  const causes = new Set(tsa.seal_ready.causes);
  const turktrust = tsa.provider === 'turktrust';
  switch (step) {
    case 'java':
      if (turktrust) return 'unused';
      if (tsa.java && causes.has('executable')) return 'error';
      return !tsa.java || missing.has('java') ? 'missing' : 'ok';
    case 'zamane':
      if (turktrust) return 'unused';
      if (causes.has('zamane-jar') || (tsa.zamane.present && tsa.zamane.trusted === false))
        return 'error';
      if (tsa.zamane.staged.length > 0) return 'pending';
      return !tsa.zamane.present || missing.has('zamane_jar') ? 'missing' : 'ok';
    case 'roots':
      if (causes.has('roots')) return 'error';
      return tsa.roots.length === 0 || missing.has('roots') ? 'missing' : 'ok';
    case 'credentials': {
      if (causes.has('credentials')) return 'error';
      const names: (BrowserSecret | TurktrustSecret)[] = turktrust
        ? ['turktrust.username', 'turktrust.password']
        : ['kamusm.customer', 'kamusm.password'];
      const set = names.every((name) => secretMeta(tsa, name)?.set ?? false);
      return set && !missing.has('credentials') ? 'ok' : 'missing';
    }
    case 'test':
      return tested ? 'ok' : 'optional';
    case 'production':
      // TÜRKTRUST has no test server: its stamps are production stamps (contract D12).
      return turktrust || currentChoice(tsa) === 'production' ? 'ok' : 'optional';
    case 'credit':
      return 'ok';
  }
}

/** The step the page opens: the first error, else the first missing item, else the first in use. */
export function firstOpen(tsa: TsaSection, tested: boolean): StepId {
  const states = STEPS.map((step) => [step, stepStatus(step, tsa, tested)] as const);
  return (
    states.find(([, status]) => status === 'error')?.[0] ??
    states.find(([, status]) => status === 'missing')?.[0] ??
    states.find(([, status]) => status !== 'unused')?.[0] ??
    'java'
  );
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
  /** TÜRKTRUST's username and password (`turktrust.*`), write-only as well. */
  turktrust?: { username: SecretEdit; password: SecretEdit };
}

/** TÜRKTRUST's credentials as stored: nothing typed. */
export const TURKTRUST_KEPT = { username: SECRET_KEPT, password: SECRET_KEPT };

/** A new value was typed (an open field left empty keeps the stored one). */
function typed(edit: SecretEdit | undefined): edit is SecretEdit {
  return edit !== undefined && edit.editing && edit.value !== '';
}

/** The TÜRKTRUST credentials have a change the form would send. */
export function turktrustChanged(credentials: Credentials): boolean {
  const turktrust = credentials.turktrust;
  return (
    turktrust !== undefined &&
    (secretChanged(turktrust.username) ||
      secretChanged(turktrust.password) ||
      turktrust.username.editing ||
      turktrust.password.editing)
  );
}

/**
 * Interim until W5-T names them in `TsaBody` (`settings-types.ts`): the TÜRKTRUST fields of
 * `PUT /admin/config/tsa`, the username and password and `tsa.allow_plain_http`.
 */
export interface TurktrustFields {
  turktrust_username?: string;
  turktrust_password?: string;
  allow_plain_http?: boolean;
}

export type TsaRequest = TsaBody & TurktrustFields;

/**
 * The server a change sends: the one chosen, else the one in use. A site on another provider
 * (the mock, an RFC 3161 server) goes to the Kamu SM test server, as the page says; a site on a
 * provider the browser chooses whose server has no name here (TÜRKTRUST until W5-T, Zamane at
 * another address) sends none and keeps it, so a "Kaydet" never moves it to Kamu SM.
 */
function bodyServer(tsa: TsaSection, chosen: ServerChoice | undefined): string | undefined {
  if (chosen) return chosen;
  if (tsa.server) return tsa.server;
  return CHOSEN_PROVIDERS.has(tsa.provider) ? undefined : 'test';
}

/**
 * The body of `PUT /admin/config/tsa`: everything the wizard edits at once (the Java chosen in
 * the first step is saved with the credentials, so a first setup from the mock provider can
 * switch to Zamane in one change that leaves sealing working). Typed credentials are sent with
 * any change, as Kamu SM's are (TÜRKTRUST's can be stored before the switch to it), and a switch to
 * TÜRKTRUST says whether plain HTTP is allowed.
 */
export function tsaBody(
  tsa: TsaSection,
  etag: string,
  form: TsaForm,
  credentials: Credentials,
  options: { server?: ServerChoice; confirm?: boolean; probe: boolean },
): TsaRequest {
  const customer = credentials.customer;
  const password = credentials.password;
  const server = bodyServer(tsa, options.server);
  const username = credentials.turktrust?.username;
  const secret = credentials.turktrust?.password;
  return {
    etag,
    ...(server ? { server } : {}),
    credit_alert_threshold: parseInteger(form.threshold) ?? tsa.credit_alert_threshold,
    timeout_seconds: parseInteger(form.timeout) ?? tsa.timeout_seconds,
    java: form.java,
    ...(typed(customer) ? { customer: customer.value } : {}),
    ...(typed(password) ? { password: password.value } : {}),
    ...(credentials.clear ? { clear_credentials: true } : {}),
    ...(typed(username) ? { turktrust_username: username.value } : {}),
    ...(typed(secret) ? { turktrust_password: secret.value } : {}),
    ...(options.server && choiceProvider(options.server) === 'turktrust'
      ? { allow_plain_http: options.server === 'turktrust-http' }
      : {}),
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

/** `turktrust.username`: 1–128 characters, no control characters (Wave 5 contract 2.4). */
export function validateTurktrustUsername(value: string): string | undefined {
  if (value === '') return 'validation.required';
  if (CONTROL.test(value) || codePointLength(value) > 128)
    return 'settings.kamusm.errors.turktrustUsername';
  return undefined;
}

/** `turktrust.password`: 1–256 characters, no control characters (Wave 5 contract 2.4). */
export function validateTurktrustPassword(value: string): string | undefined {
  if (value === '') return 'validation.required';
  if (CONTROL.test(value) || codePointLength(value) > 256)
    return 'settings.kamusm.errors.turktrustPassword';
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

/**
 * The text key of a sealing cause (`SEAL_CAUSES`; an unknown one shows its code). With
 * TÜRKTRUST in use, the missing credentials are TÜRKTRUST's.
 */
export function causeKey(cause: string, provider?: string): string {
  if (provider === 'turktrust' && cause === 'credentials')
    return 'settings.kamusm.turktrust.causes.credentials';
  return `settings.kamusm.causes.${cause.replace(/-/g, '_')}`;
}

/**
 * The texts that name Kamu SM and have a TÜRKTRUST version (`settings.kamusm.turktrust.<key>`, in
 * the `tsaProvider` locale area), shown while TÜRKTRUST is the provider in use (contract 2.4:
 * "TÜRKTRUST kök sertifikası" when the provider is `turktrust`).
 */
export const TURKTRUST_TEXTS = [
  'steps.java.description',
  'steps.zamane.description',
  'steps.roots.description',
  'steps.credentials.description',
  'roots.caption',
  'roots.upload',
  'roots.compared',
  'roots.markBody',
  'credentials.probe',
  'probing',
] as const;
export type ProviderText = (typeof TURKTRUST_TEXTS)[number];

/** The key of a text of the page in the words of the provider in use. */
export function providerText(provider: string, key: ProviderText): string {
  return provider === 'turktrust' ? `settings.kamusm.turktrust.${key}` : `settings.kamusm.${key}`;
}
