/**
 * The 5651 lookup form: its values, the browser-side checks and the request body.
 *
 * The checks mirror `arcilogger_api.routers.lookup` (`RequestDetails`, `QueryIn`) and
 * `arcilogger_worker.lookup.LookupRequest` as far as the browser can know them, so mistakes show
 * at once next to the field; the server checks everything again (its 422 details are mapped back
 * to the fields by {@link serverFieldErrors}). Validators return i18n keys.
 */

import { isApiError } from '@/lib/api';
import type { LookupIn, LookupOut } from '@/lib/api-types';
import { dayText, parseDay, parseTime } from '@/lib/dates';
import { siteDateTime } from '@/lib/display';
import { queryOf } from '@/lib/lookup-types';
import { codePointLength, type FieldErrors } from '@/lib/validation';

export const SITE_TIME_ZONE = 'Europe/Istanbul';
/** Time zones offered for the requested moment (the API accepts any IANA name). */
export const TIME_ZONES = [SITE_TIME_ZONE, 'UTC'] as const;
export const DEFAULT_TOLERANCE = '60';
export const MAX_TOLERANCE_SECONDS = 3600;
export const LIMITS = { authority: 300, fileNo: 200, reason: 4000, domain: 253 } as const;
/** IP protocol numbers offered (the API also takes others). */
export const PROTOCOLS = ['6', '17', '1'] as const;

export interface LookupDraft {
  authority: string;
  fileNo: string;
  /** As typed: `10.10.2026` (also `2026-10-10`). */
  requestDate: string;
  reason: string;
  publicIp: string;
  publicPort: string;
  /** `''` (not given), `'6'`, `'17'`, `'1'`. */
  protocol: string;
  destIp: string;
  destPort: string;
  destDomain: string;
  /** Day of the requested moment as typed: `09.10.2026`. */
  date: string;
  /** 24-hour time as typed: `11:23` or `11:23:41`. */
  time: string;
  timeZone: string;
  tolerance: string;
  internalIp: string;
  mac: string;
}

/** Form fields, plus `target` for "nothing to look for". */
export type LookupField = keyof LookupDraft | 'target';

export const EMPTY_DRAFT: LookupDraft = {
  authority: '',
  fileNo: '',
  requestDate: '',
  reason: '',
  publicIp: '',
  publicPort: '',
  protocol: '',
  destIp: '',
  destPort: '',
  destDomain: '',
  date: '',
  time: '',
  timeZone: SITE_TIME_ZONE,
  tolerance: DEFAULT_TOLERANCE,
  internalIp: '',
  mac: '',
};

/** Control, format (bidi, zero-width) and surrogate characters: refused by the API. */
const CONTROL = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const CONTROL_EXCEPT_LINES = /[^\P{Cc}\n\t]|[\p{Cf}\p{Cs}]/u;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

/** An IPv4 or IPv6 address as Python's `ipaddress.ip_address` accepts it (no zone id). */
export function isIpAddress(value: string): boolean {
  const text = value.trim();
  if (IPV4.test(text)) return true;
  if (!text.includes(':') || text.includes('%') || /[^0-9a-fA-F:.]/.test(text)) return false;
  try {
    // The URL parser validates IPv6 literals (embedded IPv4 included) by the same rules.
    return new URL(`http://[${text}]/`).hostname.length > 2;
  } catch {
    return false;
  }
}

/** `02-00-5E-00-53-64` / `0200.5e00.5364` → `02:00:5e:00:53:64`, else `null`. */
export function normalizeMac(value: string): string | null {
  const digits = value.trim().replace(/[:.-]/g, '').toLowerCase();
  if (!/^[0-9a-f]{12}$/.test(digits)) return null;
  return digits.match(/../g)?.join(':') ?? null;
}

function isPort(value: string): boolean {
  return /^\d{1,5}$/.test(value.trim()) && Number(value.trim()) <= 65535;
}

function text(value: string, max: number, pattern: RegExp = CONTROL): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return 'validation.required';
  if (codePointLength(trimmed) > max) return 'validation.tooLong';
  if (pattern.test(trimmed)) return 'validation.controlCharacters';
  return undefined;
}

/** Browser-side checks; `today` is the site day (`YYYY-MM-DD`, Europe/Istanbul). */
export function validateLookup(draft: LookupDraft, today: string): FieldErrors<LookupField> {
  const errors: FieldErrors<LookupField> = {};
  const set = (field: LookupField, problem: string | undefined) => {
    if (problem) errors[field] = problem;
  };
  set('authority', text(draft.authority, LIMITS.authority));
  set('fileNo', text(draft.fileNo, LIMITS.fileNo));
  set('reason', text(draft.reason.replace(/\r\n/g, '\n'), LIMITS.reason, CONTROL_EXCEPT_LINES));
  const requestDate = parseDay(draft.requestDate);
  if (!draft.requestDate.trim()) errors.requestDate = 'validation.required';
  else if (requestDate === null) errors.requestDate = 'lookup.validation.date';
  else if (requestDate > today) errors.requestDate = 'lookup.validation.futureRequest';

  // No IPv6 identity evidence (NDP, DHCPv6): a public or internal address must be IPv4, an IPv6
  // one (the only kind with `:`, `::ffff:a.b.c.d` too) is refused by the API
  // (`ipv6_not_supported`). The destination is only a filter and may be IPv6.
  for (const field of ['publicIp', 'destIp', 'internalIp'] as const) {
    if (draft[field].trim() && !isIpAddress(draft[field]))
      errors[field] = field === 'destIp' ? 'lookup.validation.ip' : 'lookup.validation.ipv4';
  }
  for (const field of ['publicIp', 'internalIp'] as const) {
    if (!errors[field] && draft[field].includes(':')) errors[field] = 'lookup.validation.ipv6';
  }
  for (const field of ['publicPort', 'destPort'] as const) {
    if (draft[field].trim() && !isPort(draft[field])) errors[field] = 'lookup.validation.port';
  }
  const domain = draft.destDomain.trim();
  if (domain) {
    if (codePointLength(domain) > LIMITS.domain) errors.destDomain = 'validation.tooLong';
    else if (/[\s/\\@:]/.test(domain) || CONTROL.test(domain))
      errors.destDomain = 'lookup.validation.domain';
  }
  if (draft.mac.trim() && normalizeMac(draft.mac) === null) errors.mac = 'lookup.validation.mac';
  if (!draft.date.trim()) errors.date = 'validation.required';
  else if (parseDay(draft.date) === null) errors.date = 'lookup.validation.date';
  if (!draft.time.trim()) errors.time = 'validation.required';
  else if (parseTime(draft.time) === null) errors.time = 'lookup.validation.time';
  if (!(TIME_ZONES as readonly string[]).includes(draft.timeZone))
    errors.timeZone = 'lookup.validation.timeZone';
  const tolerance = draft.tolerance.trim();
  if (tolerance && (!/^\d{1,4}$/.test(tolerance) || Number(tolerance) > MAX_TOLERANCE_SECONDS))
    errors.tolerance = 'lookup.validation.tolerance';
  const target = [draft.publicIp, draft.internalIp, draft.mac, draft.destIp, draft.destDomain];
  if (target.every((value) => !value.trim())) errors.target = 'lookup.validation.target';
  return errors;
}

function optional(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  return trimmed ? Number(trimmed) : null;
}

/** The `POST /lookup/5651` body of a valid draft. */
export function toLookupBody(draft: LookupDraft): LookupIn {
  return {
    request: {
      authority: draft.authority.trim(),
      file_no: draft.fileNo.trim(),
      request_date: parseDay(draft.requestDate) ?? draft.requestDate,
      reason: draft.reason.replace(/\r\n/g, '\n').trim(),
    },
    query: {
      // Without an offset: a local time in `time_zone` (the API records how it was read).
      at: `${parseDay(draft.date) ?? draft.date}T${parseTime(draft.time) ?? draft.time}`,
      time_zone: draft.timeZone,
      public_ip: optional(draft.publicIp),
      public_port: optionalNumber(draft.publicPort),
      protocol: optionalNumber(draft.protocol),
      dest_ip: optional(draft.destIp),
      dest_port: optionalNumber(draft.destPort),
      dest_domain: optional(draft.destDomain),
      tolerance_seconds: optionalNumber(draft.tolerance),
      internal_ip: optional(draft.internalIp),
      mac: draft.mac.trim() ? normalizeMac(draft.mac) : null,
    },
  };
}

/** The form filled with a stored lookup (to run it again, changed or not). */
export function draftFrom(lookup: LookupOut): LookupDraft {
  const query = queryOf(lookup);
  const input = query.time_input;
  // As entered (local time in an offered zone), else the moment in site time.
  let { date, time } = siteDateTime(query.at);
  let timeZone: string = SITE_TIME_ZONE;
  const entered = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?)/.exec(input?.text ?? '');
  const zone = input?.time_zone ?? '';
  if (input && !input.offset_given && entered && (TIME_ZONES as readonly string[]).includes(zone)) {
    date = entered[1] ?? date;
    time = entered[2] ?? time;
    timeZone = zone;
  }
  const protocol = query.protocol === null ? '' : String(query.protocol);
  const text = (value: string | null | undefined) => value ?? '';
  const number = (value: number | null | undefined) =>
    value === null || value === undefined ? '' : String(value);
  return {
    authority: lookup.request.authority,
    fileNo: lookup.request.file_no,
    requestDate: dayText(lookup.request.request_date),
    reason: lookup.request.reason,
    publicIp: text(query.public_ip),
    publicPort: number(query.public_port),
    protocol: (PROTOCOLS as readonly string[]).includes(protocol) ? protocol : '',
    destIp: text(query.dest_ip),
    destPort: number(query.dest_port),
    destDomain: text(query.dest_domain),
    date: dayText(date),
    time,
    timeZone,
    tolerance: number(query.tolerance_seconds) || DEFAULT_TOLERANCE,
    internalIp: text(query.internal_ip),
    mac: text(query.mac),
  };
}

const SERVER_FIELDS: Record<string, LookupField> = {
  'request.authority': 'authority',
  'request.file_no': 'fileNo',
  'request.request_date': 'requestDate',
  'request.reason': 'reason',
  'query.at': 'time',
  'query.time_zone': 'timeZone',
  'query.public_ip': 'publicIp',
  'query.public_port': 'publicPort',
  'query.protocol': 'protocol',
  'query.dest_ip': 'destIp',
  'query.dest_port': 'destPort',
  'query.dest_domain': 'destDomain',
  'query.tolerance_seconds': 'tolerance',
  'query.internal_ip': 'internalIp',
  'query.mac': 'mac',
  query: 'target',
};

/**
 * Fields named by a 422 `validation_error` of the API (`details[].loc` = `["body", "query",
 * "public_ip"]` …), with the server's own message (English, technical) — the form shows its own
 * text for the field and the server's next to it.
 */
export function serverFieldErrors(error: unknown): Partial<Record<LookupField, string>> {
  if (!isApiError(error, 'validation_error') || !Array.isArray(error.details)) return {};
  const found: Partial<Record<LookupField, string>> = {};
  for (const item of error.details as unknown[]) {
    if (typeof item !== 'object' || item === null) continue;
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc)) continue;
    const key = loc.filter((part) => part !== 'body').join('.');
    const field = SERVER_FIELDS[key];
    if (field && !found[field]) found[field] = typeof msg === 'string' ? msg : '';
  }
  return found;
}
