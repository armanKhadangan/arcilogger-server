/**
 * Ayarlar → Güvenlik kuralları (Wave 6 contract §2.3, W6-F): a rule's settings as the editor holds
 * them, the browser's checks (the same bounds as `arcilogger_worker.rules.settings`, the server
 * checks again: 422 `rule_param_invalid` with `field`) and the body of `PUT /rules/{id}`, which
 * holds only what differs from the registry's defaults (§2.1.4). No components here;
 * unit-tested in `rules-model.test.ts`.
 *
 * Numbers are typed as text and read only when sent. Lists are typed one entry per line; empty
 * lines are left out. Byte limits are typed in MiB (`1024` is 1 GiB).
 */

import type { AlertSeverity } from '@/lib/api-types';
import type { Lang } from '@/lib/format';
import { formatBytes, formatDuration, formatNumber } from '@/lib/format';
import type {
  RuleOut,
  RuleParam,
  RuleParamKind,
  RuleParamValue,
  RuleSettings,
  RuleSettingsIn,
} from '@/lib/rules-types';
import { codePointLength } from '@/lib/validation';

/** A rule's window: at least a minute, at most 7 days (§2.1.4). */
export const WINDOW_MIN = 60;
export const WINDOW_MAX = 7 * 86_400;
/** A cooldown of 0 opens the next alert of a key at once; at most 7 days. */
export const COOLDOWN_MIN = 0;
export const COOLDOWN_MAX = 7 * 86_400;
/** Longest list and longest entry the server accepts. */
export const LIST_MAX = 200;
export const ENTRY_MAX = 200;
/** Byte limits are typed in this unit. */
export const MIB = 1_048_576;

/** The kinds the editor has an input for; any other is shown and kept as it is. */
const KINDS: readonly RuleParamKind[] = [
  'int',
  'bytes',
  'seconds',
  'percent',
  'strings',
  'networks',
  'ints',
  'bool',
];
const LIST_KINDS: readonly RuleParamKind[] = ['strings', 'networks', 'ints'];

export function isKnownKind(kind: string): kind is RuleParamKind {
  return (KINDS as readonly string[]).includes(kind);
}

export function isListKind(kind: RuleParamKind): boolean {
  return LIST_KINDS.includes(kind);
}

export interface RuleForm {
  enabled: boolean;
  severity: AlertSeverity;
  window: string;
  cooldown: string;
  email: boolean;
  webhook: boolean;
  /** Text of a number or a list (one entry per line), or a flag. */
  params: Record<string, string | boolean>;
}

/** A form field: the window, the cooldown or a parameter (`param:threshold`). */
export type RuleField = 'window' | 'cooldown' | `param:${string}`;

/** A problem at a field: an i18n key with its values (bounds, the line of a list). */
export interface RuleProblem {
  key: string;
  values?: Record<string, string | number>;
}

export type RuleErrors = Partial<Record<RuleField, RuleProblem>>;

export function paramField(key: string): RuleField {
  return `param:${key}`;
}

/** The id of a field's control in the sheet. */
export function ruleFieldId(field: RuleField): string {
  return `rule-${field.replace(':', '-')}`;
}

/** `1024` MiB → `1024`; `1,5` GiB in MiB → `1536`; a part of a MiB with up to three decimals. */
function mibText(bytes: number): string {
  const mib = bytes / MIB;
  return Number.isInteger(mib) ? String(mib) : String(Number(mib.toFixed(3)));
}

function paramText(param: RuleParam, value: RuleParamValue | undefined): string | boolean {
  if (param.kind === 'bool') return value === true;
  if (value === undefined) return '';
  if (Array.isArray(value)) return value.map(String).join('\n');
  if (param.kind === 'bytes' && typeof value === 'number') return mibText(value);
  return String(value);
}

/** The form of a rule's effective settings. */
export function toRuleForm(rule: Pick<RuleOut, 'settings' | 'params'>): RuleForm {
  const { settings } = rule;
  return {
    enabled: settings.enabled,
    severity: settings.severity,
    window: String(settings.window_seconds),
    cooldown: String(settings.cooldown_seconds),
    email: settings.actions.email,
    webhook: settings.actions.webhook,
    params: Object.fromEntries(
      rule.params
        .filter((param) => isKnownKind(param.kind))
        .map((param) => [param.key, paramText(param, settings.params[param.key])]),
    ),
  };
}

/** The entries of a list as typed: one per line, blanks trimmed, empty lines left out. */
export function listLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

const INTEGER = /^-?\d+$/;
const DECIMAL = /^\d+(?:[.,]\d{1,3})?$/;
/** Control, format (bidi, zero-width) and surrogate characters, refused by the API. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const OCTET = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;

function ipv4(text: string): number | null {
  const parts = text.split('.');
  if (parts.length !== 4 || !parts.every((part) => OCTET.test(part))) return null;
  return parts.reduce((value, part) => value * 256 + Number(part), 0);
}

function ipv6(text: string): boolean {
  if (!text.includes(':') || /[^0-9a-fA-F:.]/.test(text)) return false;
  try {
    // The URL parser validates IPv6 literals (embedded IPv4 included) by the same rules.
    return new URL(`http://[${text}]/`).hostname.length > 2;
  } catch {
    return false;
  }
}

/**
 * Whether `text` is a network as PostgreSQL's `cidr` takes it (`172.20.0.0/16`, an address alone
 * is a `/32` or `/128`): `invalid`, `hostBits` (the address is not the network's: `172.20.1.0/16`)
 * or `ok`. Host bits of an IPv6 network are left to the server.
 */
export function networkProblem(text: string): 'invalid' | 'hostBits' | null {
  const [address = '', prefixText, ...rest] = text.split('/');
  if (rest.length > 0) return 'invalid';
  if (prefixText !== undefined && !/^\d{1,3}$/.test(prefixText)) return 'invalid';
  const prefix = prefixText === undefined ? null : Number(prefixText);
  const v4 = ipv4(address);
  if (v4 !== null) {
    if (prefix === null) return null;
    if (prefix > 32) return 'invalid';
    const size = 2 ** (32 - prefix);
    return Math.floor(v4 / size) * size === v4 ? null : 'hostBits';
  }
  if (!ipv6(address)) return 'invalid';
  return prefix !== null && prefix > 128 ? 'invalid' : null;
}

/** The network `text` names, for the "did you mean" of a network with host bits. */
export function networkOf(text: string): string | null {
  const [address = '', prefixText = ''] = text.split('/');
  const value = ipv4(address);
  const prefix = Number(prefixText);
  if (value === null || !Number.isInteger(prefix) || prefix < 0 || prefix > 32) return null;
  const size = 2 ** (32 - prefix);
  const base = Math.floor(value / size) * size;
  const octets = [24, 16, 8, 0].map((shift) => Math.floor(base / 2 ** shift) % 256);
  return `${octets.join('.')}/${prefix}`;
}

function bounded(
  value: number,
  min: number | null,
  max: number | null,
  show: (bound: number) => string,
): RuleProblem | undefined {
  if (min !== null && value < min) return { key: 'admin.errors.min', values: { min: show(min) } };
  if (max !== null && value > max) return { key: 'admin.errors.max', values: { max: show(max) } };
  return undefined;
}

/** An integer typed as text, or the problem. */
function integerProblem(
  text: string,
  min: number | null,
  max: number | null,
  lang: Lang,
): RuleProblem | undefined {
  const value = text.trim();
  if (!value) return { key: 'validation.required' };
  if (!INTEGER.test(value)) return { key: 'admin.errors.integer' };
  return bounded(Number(value), min, max, (bound) => formatNumber(bound, 0, lang));
}

function bytesProblem(text: string, param: RuleParam, lang: Lang): RuleProblem | undefined {
  const value = text.trim();
  if (!value) return { key: 'validation.required' };
  if (!DECIMAL.test(value)) return { key: 'rules.errors.mib' };
  const bytes = Math.round(Number(value.replace(',', '.')) * MIB);
  return bounded(
    bytes,
    param.min,
    param.max,
    (bound) => `${formatNumber(bound / MIB, 0, lang)} MiB`,
  );
}

function entryProblem(kind: RuleParamKind, entry: string): string | undefined {
  if (codePointLength(entry) > ENTRY_MAX) return 'rules.errors.entryTooLong';
  if (FORBIDDEN.test(entry)) return 'validation.controlCharacters';
  if (kind === 'ints' && !INTEGER.test(entry)) return 'rules.errors.entryInteger';
  if (kind === 'networks') {
    const problem = networkProblem(entry);
    if (problem === 'invalid') return 'rules.errors.entryNetwork';
    if (problem === 'hostBits') return 'rules.errors.entryHostBits';
  }
  return undefined;
}

function listProblem(text: string, param: RuleParam, lang: Lang): RuleProblem | undefined {
  const lines = text.split(/\r?\n/);
  let line = 0;
  for (const raw of lines) {
    line += 1;
    const entry = raw.trim();
    if (!entry) continue;
    const key = entryProblem(param.kind, entry);
    if (key) {
      const values: Record<string, string | number> = { line, max: ENTRY_MAX };
      if (key === 'rules.errors.entryHostBits') values.network = networkOf(entry) ?? entry;
      return { key, values };
    }
  }
  const count = listLines(text).length;
  const min = param.min ?? 0;
  const max = Math.min(param.max ?? LIST_MAX, LIST_MAX);
  if (count < min)
    return min === 1
      ? { key: 'validation.required' }
      : { key: 'rules.errors.listMin', values: { min: formatNumber(min, 0, lang) } };
  if (count > max)
    return { key: 'rules.errors.listMax', values: { max: formatNumber(max, 0, lang) } };
  return undefined;
}

/** The problem of one parameter's text, `undefined` when it may be sent. */
export function paramProblem(
  param: RuleParam,
  value: string | boolean | undefined,
  lang: Lang = 'tr',
): RuleProblem | undefined {
  if (!isKnownKind(param.kind) || param.kind === 'bool' || typeof value !== 'string')
    return undefined;
  switch (param.kind) {
    case 'bytes':
      return bytesProblem(value, param, lang);
    case 'strings':
    case 'networks':
    case 'ints':
      return listProblem(value, param, lang);
    default:
      return integerProblem(value, param.min, param.max, lang);
  }
}

/** The browser's checks of the whole form (every field, in the sheet's order). */
export function validateRule(
  form: RuleForm,
  params: readonly RuleParam[],
  lang: Lang = 'tr',
): RuleErrors {
  const errors: RuleErrors = {};
  const window = integerProblem(form.window, WINDOW_MIN, WINDOW_MAX, lang);
  if (window) errors.window = window;
  const cooldown = integerProblem(form.cooldown, COOLDOWN_MIN, COOLDOWN_MAX, lang);
  if (cooldown) errors.cooldown = cooldown;
  for (const param of params) {
    const problem = paramProblem(param, form.params[param.key], lang);
    if (problem) errors[paramField(param.key)] = problem;
  }
  return errors;
}

export function hasRuleErrors(errors: RuleErrors): boolean {
  return Object.values(errors).some(Boolean);
}

function paramValue(param: RuleParam, text: string | boolean | undefined): RuleParamValue {
  if (param.kind === 'bool') return text === true;
  const value = typeof text === 'string' ? text : '';
  switch (param.kind) {
    case 'strings':
    case 'networks':
      return listLines(value);
    case 'ints':
      return listLines(value).map(Number);
    case 'bytes':
      return Math.round(Number(value.trim().replace(',', '.')) * MIB);
    default:
      return Number(value.trim());
  }
}

/**
 * The settings the form describes (a valid form). A parameter of a kind the editor has no input
 * for keeps its stored value.
 */
export function fromRuleForm(
  form: RuleForm,
  rule: Pick<RuleOut, 'settings' | 'params'>,
): RuleSettings {
  const params: Record<string, RuleParamValue> = { ...rule.settings.params };
  for (const param of rule.params) {
    if (isKnownKind(param.kind)) params[param.key] = paramValue(param, form.params[param.key]);
  }
  return {
    enabled: form.enabled,
    severity: form.severity,
    window_seconds: Number(form.window.trim()),
    cooldown_seconds: Number(form.cooldown.trim()),
    actions: { email: form.email, webhook: form.webhook },
    params,
  };
}

function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b))
    return a.length === b.length && a.every((item, index) => same(item, b[index]));
  return a === b;
}

/**
 * `rules.<ID>` for `settings`: only what differs from `defaults` (an empty object: every default).
 * Saving the defaults back so removes the overrides instead of freezing today's defaults.
 */
export function ruleBody(settings: RuleSettings, defaults: RuleSettings): RuleSettingsIn {
  const body: RuleSettingsIn = {};
  if (settings.enabled !== defaults.enabled) body.enabled = settings.enabled;
  if (settings.severity !== defaults.severity) body.severity = settings.severity;
  if (settings.window_seconds !== defaults.window_seconds)
    body.window_seconds = settings.window_seconds;
  if (settings.cooldown_seconds !== defaults.cooldown_seconds)
    body.cooldown_seconds = settings.cooldown_seconds;
  const actions: Partial<RuleSettings['actions']> = {};
  if (settings.actions.email !== defaults.actions.email) actions.email = settings.actions.email;
  if (settings.actions.webhook !== defaults.actions.webhook)
    actions.webhook = settings.actions.webhook;
  if (Object.keys(actions).length > 0) body.actions = actions;
  const params: Record<string, RuleParamValue> = {};
  for (const [key, value] of Object.entries(settings.params)) {
    if (!same(value, defaults.params[key])) params[key] = value;
  }
  if (Object.keys(params).length > 0) body.params = params;
  return body;
}

/** Whether the form differs from the rule's stored settings (Save is offered then). */
export function isRuleChanged(form: RuleForm, rule: Pick<RuleOut, 'settings' | 'params'>): boolean {
  const stored = toRuleForm(rule);
  return JSON.stringify(stored) !== JSON.stringify(form);
}

/** The form of the registry's defaults ("Varsayılanlara dön"). */
export function defaultsForm(rule: Pick<RuleOut, 'defaults' | 'params'>): RuleForm {
  return toRuleForm({ settings: rule.defaults, params: rule.params });
}

/**
 * The field a 422 `rule_param_invalid` names (`details.field`: `window_seconds`,
 * `params.threshold`, …), `null` for one the form has no input for.
 */
export function fieldOfProblem(details: unknown, params: readonly RuleParam[]): RuleField | null {
  if (typeof details !== 'object' || details === null) return null;
  const field = (details as { field?: unknown }).field;
  if (typeof field !== 'string') return null;
  if (field === 'window_seconds') return 'window';
  if (field === 'cooldown_seconds') return 'cooldown';
  const found = /^params\.([^.[\]]+)/.exec(field);
  const key = found?.[1];
  if (key && params.some((param) => param.key === key && isKnownKind(param.kind)))
    return paramField(key);
  return null;
}

// -- the table --------------------------------------------------------------------------------

/** "Veri geliyor" / "Son 24 saatte veri yok" / "Hiç veri gelmedi" (an i18n key). */
export function dataStatusKey(rule: Pick<RuleOut, 'data_status'>): string {
  return `rules.dataStatus.${rule.data_status}`;
}

export type RuleState = 'on' | 'off' | 'setup';

/**
 * On or off; "Ayar gerekli" while the rule is idle until a parameter is set, whether it is
 * switched on or not (such a rule ships off, §2.1.4).
 */
export function ruleState(rule: Pick<RuleOut, 'settings' | 'needs_setup'>): RuleState {
  if (rule.needs_setup) return 'setup';
  return rule.settings.enabled ? 'on' : 'off';
}

export function ruleName(rule: Pick<RuleOut, 'name_tr' | 'name_en' | 'id'>, lang: Lang): string {
  const name = lang === 'en' ? rule.name_en : rule.name_tr;
  return name.trim() ? name : rule.id;
}

export function paramLabel(param: Pick<RuleParam, 'label_tr' | 'label_en' | 'key'>, lang: Lang) {
  const label = lang === 'en' ? param.label_en : param.label_tr;
  return label.trim() ? label : param.key;
}

/** A value as the "Varsayılan" line shows it (`t` names a flag "Açık" / "Kapalı"). */
export function shownValue(
  param: Pick<RuleParam, 'kind'>,
  value: RuleParamValue | undefined,
  lang: Lang,
  t: (key: string) => string,
): string {
  if (value === undefined) return '—';
  if (Array.isArray(value)) return value.map(String).join(', ');
  if (typeof value === 'boolean') return t(value ? 'rules.state.on' : 'rules.state.off');
  if (typeof value === 'number') {
    if (param.kind === 'bytes') return formatBytes(value, lang);
    if (param.kind === 'seconds') return formatDuration(value, lang);
    if (param.kind === 'percent') return lang === 'en' ? `${value}%` : `%${value}`;
    return formatNumber(value, 0, lang);
  }
  return String(value);
}

/** Rules in id order (`R2` before `R10`). */
export function sortRules<T extends Pick<RuleOut, 'id'>>(rules: readonly T[]): T[] {
  return [...rules].sort((a, b) => a.id.localeCompare(b.id, 'en', { numeric: true }));
}
