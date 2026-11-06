/**
 * Ayarlar → Kaynaklar → kaynak → SNMP (Wave 5 §6 N1, P4): `[sources.snmp]` as the browser edits
 * it, its two write-only keys and the browser's checks. The server runs the same rules again
 * (`SnmpConfig` in `arcilogger_core.config`) and then tries the device with the new values
 * before anything is written (N5). Validators return i18n keys.
 */

import { SECRET_KEPT, type SecretEdit } from '@/components/admin/secret-edit';
import type { SnmpAuth, SnmpCapacityIo, SnmpIn, SnmpOut, SnmpPriv } from '@/lib/settings-types';
import { codePointLength, type FieldErrors } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

export const SNMP_AUTHS: readonly SnmpAuth[] = ['sha1', 'sha256', 'sha512'];
/** `aes256` is "sahada doğrula" (X-B3): older firmware may not offer it. */
export const SNMP_PRIVS: readonly SnmpPriv[] = ['aes128', 'aes256'];
export const SNMP_PORT_MIN = 1;
export const SNMP_PORT_MAX = 65535;
export const SNMP_INTERVAL_MIN = 10;
export const SNMP_INTERVAL_MAX = 60;
/** 1 to 4 WAN lines per source. */
export const SNMP_LINES_MAX = 4;
/** `user` and each `ifName`: at most 64 characters. */
export const SNMP_TEXT_MAX = 64;
export const SNMP_CAPACITY_MIN = 1;
export const SNMP_CAPACITY_MAX = 1_000_000;
/** The keys: 8 to 64 characters (Turkish letters too: they are sent as UTF-8). */
export const SNMP_KEY_MIN = 8;
export const SNMP_KEY_MAX = 64;

/** A WAN line as typed: its `ifName` and, optionally, both capacities in whole Mbit/s. */
export interface SnmpLineForm {
  name: string;
  down: string;
  up: string;
}

/** `[sources.snmp]` as typed, with the keys' stored state and what the form does with them. */
export interface SnmpForm {
  /** The source has an `[sources.snmp]` table; without one, SNMP left off sends nothing. */
  stored: boolean;
  enabled: boolean;
  port: string;
  user: string;
  auth: SnmpAuth;
  priv: SnmpPriv;
  lines: SnmpLineForm[];
  interval: string;
  authKeySet: boolean;
  privKeySet: boolean;
  authKey: SecretEdit;
  privKey: SecretEdit;
}

export const NEW_LINE: SnmpLineForm = { name: '', down: '', up: '' };

/** No `[sources.snmp]` yet: the server's defaults (§6.3), one empty line ready to be typed. */
export const NEW_SNMP: SnmpForm = {
  stored: false,
  enabled: false,
  port: '161',
  user: '',
  auth: 'sha256',
  priv: 'aes128',
  lines: [NEW_LINE],
  interval: '15',
  authKeySet: false,
  privKeySet: false,
  authKey: SECRET_KEPT,
  privKey: SECRET_KEPT,
};

export function toSnmpForm(snmp: SnmpOut | null | undefined): SnmpForm {
  if (!snmp) return NEW_SNMP;
  const lines = snmp.interfaces.map((name) => {
    const capacity = snmp.capacity_mbps[name];
    return {
      name,
      down: capacity ? String(capacity.down) : '',
      up: capacity ? String(capacity.up) : '',
    };
  });
  return {
    stored: true,
    enabled: snmp.enabled,
    port: String(snmp.port),
    user: snmp.user,
    auth: snmp.auth,
    priv: snmp.priv,
    lines: lines.length > 0 ? lines : [NEW_LINE],
    interval: String(snmp.interval_seconds),
    authKeySet: snmp.auth_key === 'set',
    privKeySet: snmp.priv_key === 'set',
    authKey: SECRET_KEPT,
    privKey: SECRET_KEPT,
  };
}

/** A stored table as it goes back in a change body (the keys' state is not part of it). */
export function snmpIn(snmp: SnmpOut): SnmpIn {
  return {
    enabled: snmp.enabled,
    port: snmp.port,
    user: snmp.user,
    auth: snmp.auth,
    priv: snmp.priv,
    interfaces: [...snmp.interfaces],
    capacity_mbps: Object.fromEntries(
      Object.entries(snmp.capacity_mbps).map(([name, io]) => [name, { ...io }]),
    ),
    interval_seconds: snmp.interval_seconds,
  };
}

/**
 * The typed table for the change body; `undefined` when there is none and SNMP stays off, so a
 * source without SNMP is sent as before. Empty lines are left out; a line's capacity only when
 * both directions are typed (the checks refuse one without the other).
 */
export function fromSnmpForm(form: SnmpForm): SnmpIn | undefined {
  if (!form.stored && !form.enabled) return undefined;
  const lines = form.lines
    .map((line) => ({ ...line, name: line.name.trim() }))
    .filter((line) => line.name !== '');
  const capacity: Record<string, SnmpCapacityIo> = {};
  for (const line of lines) {
    const down = parseInteger(line.down);
    const up = parseInteger(line.up);
    if (down !== null && up !== null) capacity[line.name] = { down, up };
  }
  return {
    enabled: form.enabled,
    port: parseInteger(form.port) ?? 0,
    user: form.user.trim(),
    auth: form.auth,
    priv: form.priv,
    interfaces: lines.map((line) => line.name),
    capacity_mbps: capacity,
    interval_seconds: parseInteger(form.interval) ?? 0,
  };
}

/** A new key typed in the form (write-only; left out keeps the stored one). */
export function typedKey(edit: SecretEdit): string | undefined {
  return edit.editing && edit.value !== '' ? edit.value : undefined;
}

/**
 * The fields the checks name: `line-<i>`, `down-<i>` and `up-<i>` for the i-th line; `snmp` for
 * the section itself (a network address, no line).
 */
export type SnmpField =
  | 'snmp'
  | 'port'
  | 'user'
  | 'interval'
  | 'auth_key'
  | 'priv_key'
  | `line-${number}`
  | `down-${number}`
  | `up-${number}`;

/** A control's id in the source dialog (the page focuses the first refused one). */
export function snmpId(field: SnmpField): string {
  return `source-snmp-${field}`;
}

/** The bounds of a field, for the `{{min}}`/`{{max}}` of its message. */
export function snmpBounds(field: SnmpField): { min: number; max: number } {
  if (field === 'port') return { min: SNMP_PORT_MIN, max: SNMP_PORT_MAX };
  if (field === 'interval') return { min: SNMP_INTERVAL_MIN, max: SNMP_INTERVAL_MAX };
  if (field === 'auth_key' || field === 'priv_key') return { min: SNMP_KEY_MIN, max: SNMP_KEY_MAX };
  if (field.startsWith('line-') || field === 'user') return { min: 1, max: SNMP_TEXT_MAX };
  return { min: SNMP_CAPACITY_MIN, max: SNMP_CAPACITY_MAX };
}

function keyError(edit: SecretEdit, stored: boolean): string | undefined {
  if (edit.editing) {
    if (edit.value === '') return 'validation.required';
    const length = codePointLength(edit.value);
    if (length < SNMP_KEY_MIN || length > SNMP_KEY_MAX) return 'snmp.form.errors.keyLength';
    return undefined;
  }
  return stored ? undefined : 'snmp.form.errors.keyMissing';
}

/**
 * The browser's checks of `[sources.snmp]` (N1): only while SNMP is on, as the server checks
 * an enabled table (a table switched off keeps its values as they are). `network`: the source's
 * address is a network, whose SNMP the server refuses (`snmp_source_is_network`). Then a user, 1 to 4 distinct interface names, the port
 * 1–65535, the interval 10–60 s, each capacity both directions in 1–1 000 000 Mbit/s, and both
 * keys stored or typed (8–64 characters).
 */
export function validateSnmp(form: SnmpForm, network: boolean): FieldErrors<SnmpField> {
  const errors: FieldErrors<SnmpField> = {};
  const typedKeyError = (edit: SecretEdit, stored: boolean) =>
    edit.editing ? keyError(edit, stored) : undefined;
  if (!form.enabled) {
    // A key typed for an SNMP left off is still checked: the server would refuse it.
    const auth = typedKeyError(form.authKey, form.authKeySet);
    const priv = typedKeyError(form.privKey, form.privKeySet);
    if (auth) errors.auth_key = auth;
    if (priv) errors.priv_key = priv;
    return errors;
  }
  if (network) errors.snmp = 'snmp.form.errors.network';
  const port = integerError(form.port, SNMP_PORT_MIN, SNMP_PORT_MAX);
  if (port) errors.port = port;
  const user = form.user.trim();
  if (!user) errors.user = 'validation.required';
  else if (codePointLength(user) > SNMP_TEXT_MAX) errors.user = 'snmp.form.errors.tooLong';
  const interval = integerError(form.interval, SNMP_INTERVAL_MIN, SNMP_INTERVAL_MAX);
  if (interval) errors.interval = interval;
  const seen = new Set<string>();
  let named = 0;
  form.lines.forEach((line, index) => {
    const name = line.name.trim();
    const down = line.down.trim();
    const up = line.up.trim();
    if (!name) {
      if (down || up) errors[`line-${index}`] = 'validation.required';
    } else if (codePointLength(name) > SNMP_TEXT_MAX)
      errors[`line-${index}`] = 'snmp.form.errors.tooLong';
    else if (seen.has(name)) errors[`line-${index}`] = 'snmp.form.errors.lineTwice';
    if (name) {
      seen.add(name);
      named += 1;
    }
    if (down || up) {
      const downError = down
        ? integerError(down, SNMP_CAPACITY_MIN, SNMP_CAPACITY_MAX)
        : 'snmp.form.errors.capacityBoth';
      const upError = up
        ? integerError(up, SNMP_CAPACITY_MIN, SNMP_CAPACITY_MAX)
        : 'snmp.form.errors.capacityBoth';
      if (downError) errors[`down-${index}`] = downError;
      if (upError) errors[`up-${index}`] = upError;
    }
  });
  if (named === 0) errors['line-0'] = 'snmp.form.errors.noLine';
  if (form.lines.length > SNMP_LINES_MAX) errors.snmp = 'snmp.form.errors.tooManyLines';
  const auth = keyError(form.authKey, form.authKeySet);
  const priv = keyError(form.privKey, form.privKeySet);
  if (auth) errors.auth_key = auth;
  if (priv) errors.priv_key = priv;
  return errors;
}

/**
 * The configuration paths the API names for a field of the source `name` (422 `details`): a
 * refusal of the table (`snmp_secret_missing`, `snmp_source_is_network`, a value the model
 * refuses) is at `sources.<name>.snmp`; a key the store refuses (`secret_invalid`) at
 * `snmp.<name>.auth_key` / `.priv_key` (W5-N-6, W5-N-8).
 */
export function snmpPaths(name: string): {
  snmp: string[];
  auth_key: string[];
  priv_key: string[];
} {
  const source = name.trim();
  return {
    snmp: [`sources.${source}.snmp`],
    auth_key: [`snmp.${source}.auth_key`],
    priv_key: [`snmp.${source}.priv_key`],
  };
}
