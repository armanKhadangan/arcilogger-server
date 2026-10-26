/**
 * Browser checks of the settings forms, before a change is sent (contract 2.6).
 *
 * They mirror the server's rules (`arcilogger_core.admin.rules`, `arcilogger_db.settings`) only
 * as far as the browser can know them, for immediate feedback; the API runs the rules again and
 * the administration service once more on the server. Validators return i18n keys.
 */

import { codePointLength, type FieldErrors } from '@/lib/validation';

/** `site_name`: one line, at most 200 characters, no control or format characters. */
export const SITE_NAME_MAX = 200;
/** Control, format (bidi overrides, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
/** `_HOST` of `arcilogger_core.config`. */
export const HOST_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

export function validateSiteName(value: string): string | undefined {
  const text = value.trim();
  if (!text) return 'settings.general.errors.siteNameRequired';
  // Code points, as Python's len() counts them on the server.
  if (codePointLength(text) > SITE_NAME_MAX) return 'settings.general.errors.siteNameTooLong';
  if (FORBIDDEN.test(text)) return 'validation.controlCharacters';
  return undefined;
}

/** A host name (`_HOST`) or a dotted-quad IPv4 address; `''` passes when `optional`. */
export function validateHost(value: string, optional: boolean): string | undefined {
  const text = value.trim();
  if (!text) return optional ? undefined : 'validation.required';
  // Four numbers are an address and must be one (not a host name of digits).
  if (/^[\d.]+$/.test(text)) return IPV4.test(text) ? undefined : 'admin.errors.hostInvalid';
  return HOST_PATTERN.test(text) ? undefined : 'admin.errors.hostInvalid';
}

export interface ClockValues {
  ntp_server: string;
  fallback_server: string;
  alert_offset_ms: string;
}

export function validateClock(values: ClockValues): FieldErrors<keyof ClockValues> {
  const errors: FieldErrors<keyof ClockValues> = {};
  const ntp = validateHost(values.ntp_server, true);
  if (ntp) errors.ntp_server = ntp;
  const fallback = validateHost(values.fallback_server, true);
  if (fallback) errors.fallback_server = fallback;
  if (!values.ntp_server.trim() && !values.fallback_server.trim())
    errors.fallback_server = 'settings.general.errors.clockServersRequired';
  const offset = integerError(values.alert_offset_ms, 1, 60_000);
  if (offset) errors.alert_offset_ms = offset;
  return errors;
}

/** An integer typed into a field, `null` when it is not one. */
export function parseInteger(text: string): number | null {
  const value = text.trim();
  return /^-?\d+$/.test(value) ? Number(value) : null;
}

/** `validation.integer` / `.min` / `.max` style keys for a typed integer. */
export function integerError(
  text: string,
  min: number | null,
  max: number | null,
): string | undefined {
  const value = parseInteger(text);
  if (value === null) return 'admin.errors.integer';
  if (min !== null && value < min) return 'admin.errors.min';
  if (max !== null && value > max) return 'admin.errors.max';
  return undefined;
}

/** `EMAIL_ADDRESS` of `arcilogger_core.config` (at most 254 characters). */
const EMAIL_ADDRESS = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;

/** An e-mail address; `''` passes when `optional`. */
export function validateEmail(value: string, optional: boolean): string | undefined {
  const text = value.trim();
  if (!text) return optional ? undefined : 'validation.required';
  return EMAIL_ADDRESS.test(text) && text.length <= 254 ? undefined : 'admin.errors.emailInvalid';
}

/** The hosts an SMTP user name may be sent to without TLS (`smtp_plaintext_auth_refused`). */
export const LOCAL_HOSTS: readonly string[] = ['localhost', '127.0.0.1', '::1'];

const SHARE = /^[A-Za-z0-9$_.-]{1,80}$/;
const PATH_SEGMENT = /^[^:*?"<>|/]+$/;

/**
 * The replica target (contract 2.6 "replica"): `\\host\share[\path]` with a host name or an
 * IPv4 address, a share of `[A-Za-z0-9$_.-]`, path segments without `..`; no `\\?\`, `\\.\` or
 * drive letter.
 */
export function validateUnc(value: string): string | undefined {
  const text = value.trim();
  if (!text) return 'validation.required';
  if (!text.startsWith('\\\\') || text.startsWith('\\\\?\\') || text.startsWith('\\\\.\\'))
    return 'settings.replica.errors.target';
  const [host = '', share = '', ...path] = text.slice(2).split('\\');
  if (validateHost(host, false) || !SHARE.test(share)) return 'settings.replica.errors.target';
  const bad = path.some(
    (segment) => segment === '..' || segment === '.' || !PATH_SEGMENT.test(segment),
  );
  return bad ? 'settings.replica.errors.target' : undefined;
}

/** The replica user: `HOST\user`, `DOMAIN\user` or `user@domain`, at most 256 characters. */
export function validateReplicaUser(value: string): string | undefined {
  const text = value.trim();
  if (!text) return 'validation.required';
  const shaped =
    /^[A-Za-z0-9][A-Za-z0-9.-]*\\[^\\@\s]+$/.test(text) ||
    /^[^\\@\s]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(text);
  return shaped && codePointLength(text) <= 256 ? undefined : 'settings.replica.errors.user';
}
