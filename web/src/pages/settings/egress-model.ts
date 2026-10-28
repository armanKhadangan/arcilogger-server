/**
 * The internet exits of `nat.egresses` in the browser (contract 2.8.1, 2.13; ADR-0016).
 *
 * `validateEgresses` mirrors the server's rules (`arcilogger_db.egress.validate_egresses`) with
 * the same codes and the same normalisation, so the editor names a problem on its field before
 * anything is sent; the server stays authoritative and its 422 is shown at the editor level (its
 * details carry no field path). Both suites read one case list (value, expected code or
 * normalised value). The editor keeps a draft of text rows (`EgressRow`), turns it into the value
 * with `valueFromRows` and warns about set-ups that work but answer worse (`egressWarnings`).
 * The observed panel's "Çıkış olarak ekle" prefills a row by the fixed rule of `prefillOptions`.
 */

import type { TFunction } from 'i18next';

// Relative imports: the E2E mocks import this module, outside the `@/` alias.
import type { DateTimeString } from '../../lib/api-types';
import type { EgressMode, PortPreservation } from '../../lib/lookup-types';
import { codePointLength } from '../../lib/validation';

export const EGRESSES_KEY = 'nat.egresses';

/** The modes in the order the editor offers them (`arcilogger_db.egress.EGRESS_MODES`). */
export const EGRESS_MODES: readonly EgressMode[] = ['direct', 'isp_nat', 'not_logged', 'internal'];
export const PORT_PRESERVATION: readonly PortPreservation[] = [
  'unknown',
  'preserved',
  'not_preserved',
];
/** The fields of one exit; `mode` is required, the others have defaults. */
export const EGRESS_FIELDS = [
  'label',
  'mode',
  'public',
  'public_dynamic',
  'transips',
  'interfaces',
  'port_preservation',
] as const;
export type EgressField = (typeof EGRESS_FIELDS)[number];

/** Limits of the value (`setting_too_many` beyond them). */
export const MAX_EGRESSES = 8;
export const MAX_ADDRESSES = 16;
export const MAX_INTERFACES = 8;
export const MAX_LABEL = 64;
export const MAX_INTERFACE_NAME = 35;
const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;
/** Control and format characters, surrogates, line and paragraph separators. */
const CONTROL = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u;

/** One exit as stored (the normalised value: every field present). */
export interface Exit {
  label: string;
  mode: EgressMode;
  public: string[];
  public_dynamic: boolean;
  transips: string[];
  interfaces: string[];
  port_preservation: PortPreservation;
}

export type Egresses = Record<string, Exit>;

// -- IPv4 addresses and networks ------------------------------------------------------------------

/** A dotted-quad IPv4 address as `ipaddress` reads it (no leading zeros), as a number. */
function addressNumber(text: string): number | null {
  const parts = text.split('.');
  if (parts.length !== 4) return null;
  let value = 0;
  for (const part of parts) {
    if (!/^(0|[1-9]\d{0,2})$/.test(part)) return null;
    const octet = Number(part);
    if (octet > 255) return null;
    value = value * 256 + octet;
  }
  return value;
}

function addressText(value: number): string {
  return [24, 16, 8, 0].map((shift) => Math.floor(value / 2 ** shift) % 256).join('.');
}

/** An IPv4 network: its first and last address. */
interface Range {
  first: number;
  last: number;
}

function network(text: string): Range | null {
  const [address, prefix, ...rest] = text.split('/');
  if (rest.length > 0 || address === undefined) return null;
  const first = addressNumber(address);
  // A prefix of digits, as `ipaddress` reads it (`/024` is `/24`); no netmask notation.
  const bits = prefix === undefined ? 32 : /^\d+$/.test(prefix) ? Number(prefix) : NaN;
  if (first === null || !(bits >= 0 && bits <= 32)) return null;
  const size = 2 ** (32 - bits);
  return first % size === 0 ? { first, last: first + size - 1 } : null;
}

function overlaps(a: Range, b: Range): boolean {
  return a.first <= b.last && b.first <= a.last;
}

const RANGES = (texts: readonly string[]): Range[] =>
  texts.map((text) => network(text)).filter((range): range is Range => range !== null);

/** RFC 1918 and RFC 6598: the FortiGate's address behind an ISP router is one of these. */
export const PRIVATE_OR_SHARED: readonly string[] = [
  '10.0.0.0/8',
  '172.16.0.0/12',
  '192.168.0.0/16',
  '100.64.0.0/10',
];
/** Reserved ranges, never an internet address of the organisation. */
export const RESERVED: readonly string[] = [
  '0.0.0.0/8',
  '127.0.0.0/8',
  '169.254.0.0/16',
  '224.0.0.0/4',
  '240.0.0.0/4',
];
/**
 * What a public address may not lie in or overlap: one explicit list (never a classification of
 * "global" addresses, which would refuse the RFC 5737 documentation ranges).
 */
export const NOT_PUBLIC: readonly string[] = [...PRIVATE_OR_SHARED, ...RESERVED];
const PRIVATE_RANGES = RANGES(PRIVATE_OR_SHARED);
const NOT_PUBLIC_RANGES = RANGES(NOT_PUBLIC);

/** `true` for an address (or network) in RFC 1918 or RFC 6598 space. */
export function isPrivateOrShared(text: string): boolean {
  const range = network(text);
  return range !== null && PRIVATE_RANGES.some((space) => overlaps(range, space));
}

/** Looks like an IPv6 address or network (refused with its own code). */
function looksIpv6(text: string): boolean {
  return /^[0-9A-Fa-f:.]+(\/\d{1,3})?$/.test(text) && (text.match(/:/g)?.length ?? 0) >= 2;
}

// -- the server's rules ---------------------------------------------------------------------------

/** A refusal: a code of `SETTING_ERRORS` with the facts of its text, and where it was found. */
export interface EgressRefusal {
  code: string;
  facts: Record<string, string | number>;
  /** The exit (its name as given) and the field, when the rule concerns one. */
  exit?: string;
  field?: EgressField | 'name';
}

/** The text of a refusal of the exit rules (`network.egress.codes.*`, the server's texts). */
export function refusalText(refusal: Pick<EgressRefusal, 'code' | 'facts'>, t: TFunction): string {
  return t(`network.egress.codes.${refusal.code}`, {
    ...refusal.facts,
    defaultValue: refusal.code,
  });
}

export type EgressResult =
  { value: Egresses; refusal?: undefined } | { value?: undefined; refusal: EgressRefusal };

class Refused extends Error {
  constructor(readonly refusal: EgressRefusal) {
    super(refusal.code);
  }
}

function refuse(
  code: string,
  where: { exit?: string; field?: EgressField | 'name' } = {},
  facts: Record<string, string | number> = {},
): never {
  throw new Refused({ code, facts, ...where });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Canonical addresses of a list (rule 5); `/32` written as the bare address, de-duplicated. */
function addresses(value: unknown, exit: string, field: 'public' | 'transips'): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) refuse('setting_egress_field', { exit, field });
  const found: string[] = [];
  for (const item of value as unknown[]) {
    if (typeof item !== 'string') refuse('setting_egress_address', { exit, field });
    if (looksIpv6(item)) refuse('setting_ipv6_not_supported', { exit, field });
    const range = network(item);
    if (range === null) refuse('setting_egress_address', { exit, field });
    const size = range.last - range.first + 1;
    if (size > 256) refuse('setting_egress_address', { exit, field });
    if (field === 'public' && NOT_PUBLIC_RANGES.some((space) => overlaps(range, space)))
      refuse('setting_egress_public_private', { exit, field });
    const prefix = 32 - Math.log2(size);
    const text = prefix === 32 ? addressText(range.first) : `${addressText(range.first)}/${prefix}`;
    if (!found.includes(text)) found.push(text);
  }
  if (found.length > MAX_ADDRESSES)
    refuse('setting_too_many', { exit, field }, { limit: MAX_ADDRESSES });
  return found;
}

/** The interface rule (`arcilogger_db.egress.interface_name_ok`). */
export function interfaceNameOk(name: unknown): boolean {
  return (
    typeof name === 'string' &&
    codePointLength(name) >= 1 &&
    codePointLength(name) <= MAX_INTERFACE_NAME &&
    name === name.trim() &&
    !CONTROL.test(name)
  );
}

function interfaces(value: unknown, exit: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) refuse('setting_egress_field', { exit, field: 'interfaces' });
  const found: string[] = [];
  for (const item of value as unknown[]) {
    if (!interfaceNameOk(item)) refuse('setting_interface_invalid', { exit, field: 'interfaces' });
    if (!found.includes(item as string)) found.push(item as string);
  }
  if (found.length > MAX_INTERFACES)
    refuse('setting_too_many', { exit, field: 'interfaces' }, { limit: MAX_INTERFACES });
  return found;
}

/** One exit, normalised (rules 2–8). */
function exitOf(name: string, value: unknown): Exit {
  if (!NAME.test(name)) refuse('setting_egress_name', { exit: name, field: 'name' });
  if (!isObject(value)) refuse('setting_egress_field', { exit: name });
  const fields = value;
  if (
    Object.keys(fields).some((key) => !(EGRESS_FIELDS as readonly string[]).includes(key)) ||
    !('mode' in fields)
  )
    refuse('setting_egress_field', { exit: name });
  if (!(EGRESS_MODES as readonly unknown[]).includes(fields.mode))
    refuse('setting_egress_mode', { exit: name, field: 'mode' });
  const mode = fields.mode as EgressMode;
  const label = fields.label ?? '';
  if (typeof label !== 'string' || codePointLength(label) > MAX_LABEL || CONTROL.test(label))
    refuse('setting_egress_label', { exit: name, field: 'label' });
  const exit: Exit = {
    label,
    mode,
    public: addresses(fields.public, name, 'public'),
    public_dynamic: false,
    transips: addresses(fields.transips, name, 'transips'),
    interfaces: interfaces(fields.interfaces, name),
    port_preservation: 'unknown',
  };
  const preservation = fields.port_preservation ?? 'unknown';
  if (!(PORT_PRESERVATION as readonly unknown[]).includes(preservation))
    refuse('setting_egress_port_preservation', { exit: name, field: 'port_preservation' });
  exit.port_preservation = preservation as PortPreservation;
  const dynamic = fields.public_dynamic ?? false;
  if (typeof dynamic !== 'boolean')
    refuse('setting_egress_field', { exit: name, field: 'public_dynamic' });
  exit.public_dynamic = dynamic;
  // Rule 8: what each mode takes.
  const where = { exit: name };
  if (mode === 'direct') {
    if (exit.public.length === 0) refuse('setting_egress_direct', { ...where, field: 'public' });
    if (exit.transips.length > 0) refuse('setting_egress_direct', { ...where, field: 'transips' });
    if (exit.port_preservation !== 'unknown')
      refuse('setting_egress_direct', { ...where, field: 'port_preservation' });
    if (exit.public_dynamic)
      refuse('setting_egress_dynamic', { ...where, field: 'public_dynamic' });
  } else if (mode === 'not_logged') {
    if (exit.public.length === 0)
      refuse('setting_egress_not_logged', { ...where, field: 'public' });
    if (exit.transips.length > 0)
      refuse('setting_egress_not_logged', { ...where, field: 'transips' });
    if (exit.interfaces.length > 0)
      refuse('setting_egress_not_logged', { ...where, field: 'interfaces' });
    if (exit.port_preservation !== 'unknown')
      refuse('setting_egress_not_logged', { ...where, field: 'port_preservation' });
    if (exit.public_dynamic)
      refuse('setting_egress_not_logged', { ...where, field: 'public_dynamic' });
  } else if (mode === 'internal') {
    if (exit.transips.length === 0)
      refuse('setting_egress_internal', { ...where, field: 'transips' });
    if (exit.public.length > 0) refuse('setting_egress_internal', { ...where, field: 'public' });
    if (exit.public_dynamic)
      refuse('setting_egress_internal', { ...where, field: 'public_dynamic' });
    if (exit.port_preservation !== 'unknown')
      refuse('setting_egress_internal', { ...where, field: 'port_preservation' });
  }
  return exit;
}

/** Rule 9: no address in two exits (a shared `transip` only on separate interfaces), one catch-all. */
function checkAcross(exits: [string, Exit][]): void {
  for (const [name, exit] of exits)
    if (
      exit.mode === 'isp_nat' &&
      RANGES(exit.public).some((a) => RANGES(exit.transips).some((b) => overlaps(a, b)))
    )
      refuse('setting_egress_isp_nat_direct', { exit: name, field: 'transips' });
  const pairs = exits.flatMap((first, index) =>
    exits.slice(index + 1).map((second) => [first, second] as const),
  );
  const any = (a: readonly string[], b: readonly string[]) =>
    RANGES(a).some((x) => RANGES(b).some((y) => overlaps(x, y)));
  for (const [[, a], [name, b]] of pairs)
    if (any(a.public, b.public)) refuse('setting_egress_overlap', { exit: name, field: 'public' });
  for (const [[, a], [name, b]] of pairs) {
    if (any(a.public, b.transips))
      refuse('setting_egress_overlap', { exit: name, field: 'transips' });
    if (any(b.public, a.transips))
      refuse('setting_egress_overlap', { exit: name, field: 'public' });
    const separate =
      a.interfaces.length > 0 &&
      b.interfaces.length > 0 &&
      !a.interfaces.some((item) => b.interfaces.includes(item));
    if (!separate && any(a.transips, b.transips))
      refuse('setting_egress_overlap', { exit: name, field: 'transips' });
  }
  const catchAll = exits.filter(
    ([, exit]) => exit.mode === 'isp_nat' && exit.transips.length === 0,
  );
  if (catchAll.length > 1)
    refuse('setting_egress_catch_all', { exit: catchAll[1]?.[0] ?? '', field: 'transips' });
}

/**
 * The value the server stores for `nat.egresses`, normalised (every field of every exit present,
 * lists de-duplicated in the order given, addresses in canonical text), or the first refusal in
 * the order of the server's rules.
 */
export function validateEgresses(value: unknown): EgressResult {
  try {
    if (!isObject(value))
      refuse('setting_type', {}, { expected_tr: 'nesne', expected_en: 'an object' });
    const entries = Object.entries(value);
    if (entries.length > MAX_EGRESSES) refuse('setting_too_many', {}, { limit: MAX_EGRESSES });
    const exits = entries.map(([name, item]): [string, Exit] => [name, exitOf(name, item)]);
    checkAcross(exits);
    return { value: Object.fromEntries(exits) };
  } catch (error) {
    if (error instanceof Refused) return { refusal: error.refusal };
    throw error;
  }
}

/** `{}` with keys sorted, as the server's canonical JSON writes it (for the equality check). */
export function sortedEgresses(value: Egresses): Egresses {
  return Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : 1)));
}

// -- the editor's draft ---------------------------------------------------------------------------

/** One exit as the editor shows it: the lists as text, one address or interface per line. */
export interface EgressRow {
  /** A stable key for React: the stored name, or `new-<n>` for an added row. */
  key: string;
  name: string;
  label: string;
  mode: EgressMode;
  public: string;
  public_dynamic: boolean;
  transips: string;
  interfaces: string;
  port_preservation: PortPreservation;
}

/**
 * The fields the editor shows for each mode. A field a mode does not take keeps what was typed
 * in the draft (it comes back when the mode is switched back) but is not sent.
 */
export const MODE_FIELDS: Readonly<Record<EgressMode, readonly EgressField[]>> = {
  direct: ['label', 'mode', 'public', 'interfaces'],
  isp_nat: [
    'label',
    'mode',
    'public',
    'public_dynamic',
    'transips',
    'interfaces',
    'port_preservation',
  ],
  not_logged: ['label', 'mode', 'public'],
  internal: ['label', 'mode', 'transips', 'interfaces'],
};

export function showsField(mode: EgressMode, field: EgressField): boolean {
  return MODE_FIELDS[mode].includes(field);
}

/** The entries of a list field: addresses split at line breaks, commas and spaces; interface
 * names (which may hold a space, `port 1`) at line breaks and commas only. */
export function listItems(text: string, field: 'public' | 'transips' | 'interfaces'): string[] {
  const separator = field === 'interfaces' ? /[\n,;]/ : /[\s,;]+/;
  return text
    .split(separator)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

function textOf(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join('\n') : '';
}

/** The rows of a stored value (tolerant: a value stored around the rules still opens). */
export function rowsFromValue(value: unknown): EgressRow[] {
  if (!isObject(value)) return [];
  return Object.entries(value).map(([name, item]) => {
    const exit = isObject(item) ? item : {};
    const mode = (EGRESS_MODES as readonly unknown[]).includes(exit.mode)
      ? (exit.mode as EgressMode)
      : 'direct';
    const preservation = (PORT_PRESERVATION as readonly unknown[]).includes(exit.port_preservation)
      ? (exit.port_preservation as PortPreservation)
      : 'unknown';
    return {
      key: name,
      name,
      label: typeof exit.label === 'string' ? exit.label : '',
      mode,
      public: textOf(exit.public),
      public_dynamic: exit.public_dynamic === true,
      transips: textOf(exit.transips),
      interfaces: textOf(exit.interfaces),
      port_preservation: preservation,
    };
  });
}

/** What a row sends: only the fields its mode takes (the others at their defaults). */
export function exitOfRow(row: EgressRow): Exit {
  const shown = (field: EgressField) => showsField(row.mode, field);
  return {
    label: row.label.trim(),
    mode: row.mode,
    public: shown('public') ? listItems(row.public, 'public') : [],
    public_dynamic: shown('public_dynamic') ? row.public_dynamic : false,
    transips: shown('transips') ? listItems(row.transips, 'transips') : [],
    interfaces: shown('interfaces') ? listItems(row.interfaces, 'interfaces') : [],
    port_preservation: shown('port_preservation') ? row.port_preservation : 'unknown',
  };
}

/** A refusal of the draft, with the row it belongs to. */
export interface RowRefusal extends EgressRefusal {
  row?: string;
}

export type RowsResult =
  { value: Egresses; refusal?: undefined } | { value?: undefined; refusal: RowRefusal };

/** Two rows with one name: not expressible as a JSON object (a browser-side code). */
export const DUPLICATE_NAME = 'egress_duplicate_name';

/** The value of the rows (normalised as the server stores it), or the first refusal. */
export function valueFromRows(rows: readonly EgressRow[]): RowsResult {
  const seen = new Set<string>();
  const raw: Record<string, Exit> = {};
  for (const row of rows) {
    const name = row.name.trim();
    if (seen.has(name))
      return { refusal: { code: DUPLICATE_NAME, facts: {}, row: row.key, field: 'name' } };
    seen.add(name);
    raw[name] = exitOfRow(row);
  }
  const result = validateEgresses(raw);
  if (result.refusal === undefined) return result;
  const exit = result.refusal.exit;
  const row = exit === undefined ? undefined : rows.find((item) => item.name.trim() === exit);
  return { refusal: { ...result.refusal, ...(row ? { row: row.key } : {}) } };
}

/** A key for a new row that no row has. */
export function newRowKey(rows: readonly EgressRow[]): string {
  const used = rows
    .map((row) => /^new-(\d+)$/.exec(row.key)?.[1])
    .map(Number)
    .filter(Boolean);
  return `new-${Math.max(0, ...used) + 1}`;
}

/** The first name `<prefix>-<n>` that no row has (`hat-2`, `nat-1`). */
export function freeName(rows: readonly EgressRow[], prefix: string): string {
  const names = new Set(rows.map((row) => row.name.trim()));
  let number = 1;
  while (names.has(`${prefix}-${number}`)) number += 1;
  return `${prefix}-${number}`;
}

export function emptyRow(rows: readonly EgressRow[], fields: Partial<EgressRow> = {}): EgressRow {
  return {
    key: newRowKey(rows),
    name: '',
    label: '',
    mode: 'direct',
    public: '',
    public_dynamic: false,
    transips: '',
    interfaces: '',
    port_preservation: 'unknown',
    ...fields,
  };
}

// -- warnings -------------------------------------------------------------------------------------

/**
 * Set-ups the rules accept but that answer worse (contract 2.13): `noPublic` an ISP NAT exit
 * without a public address (searched for every unclaimed public IP); `dynamic` an ISP NAT exit
 * whose public IP may change; `catchAll` the ISP NAT exit without NAT addresses; `publicNat` an
 * ISP NAT exit with a public NAT address (the line is probably direct); `converted` an exit the
 * upgrade converted, still without interfaces, beside its converted partner.
 */
export type RowWarning = 'noPublic' | 'dynamic' | 'catchAll' | 'publicNat' | 'converted';
/** `interfaces`: two or more internet lines (direct or ISP NAT) while one has no interfaces. */
export type EditorWarning = 'interfaces';

/**
 * The shape the upgrade's converter (`legacy_egresses`) gives an exit, or `null`. `GET /settings`
 * does not carry the conversion record, so "as converted" is judged by the converter's names and
 * fields: `genel-<n>` direct without interfaces; `wan` (NAT addresses) and `tanimsiz` (none) as
 * ISP NAT without public addresses or interfaces.
 */
function convertedShape(row: EgressRow): 'direct' | 'nat' | null {
  const exit = exitOfRow(row);
  const name = row.name.trim();
  if (exit.interfaces.length > 0 || exit.public_dynamic) return null;
  if (/^genel-\d+$/.test(name) && exit.mode === 'direct' && exit.public.length > 0) return 'direct';
  if (exit.mode !== 'isp_nat' || exit.public.length > 0) return null;
  if (name === 'wan' && exit.transips.length > 0) return 'nat';
  if (name === 'tanimsiz' && exit.transips.length === 0) return 'nat';
  return null;
}

export function egressWarnings(rows: readonly EgressRow[]): {
  rows: Record<string, RowWarning[]>;
  editor: EditorWarning[];
} {
  const shapes = rows.map(convertedShape);
  const byRow: Record<string, RowWarning[]> = {};
  rows.forEach((row, index) => {
    const exit = exitOfRow(row);
    const found: RowWarning[] = [];
    if (exit.mode === 'isp_nat') {
      if (exit.public.length === 0) found.push('noPublic');
      if (exit.public_dynamic) found.push('dynamic');
      if (exit.transips.length === 0) found.push('catchAll');
      else if (exit.transips.some((item) => network(item) !== null && !isPrivateOrShared(item)))
        found.push('publicNat');
    }
    const shape = shapes[index];
    if (shape && shapes.some((other, at) => at !== index && other !== null && other !== shape))
      found.push('converted');
    byRow[row.key] = found;
  });
  const lines = rows
    .map(exitOfRow)
    .filter((exit) => exit.mode === 'direct' || exit.mode === 'isp_nat');
  const editor: EditorWarning[] =
    lines.length >= 2 && lines.some((exit) => exit.interfaces.length === 0) ? ['interfaces'] : [];
  return { rows: byRow, editor };
}

// -- the observed NAT addresses (`GET /settings/fortigate/observed`, contract 2.12) -------------
//
// Interim types: WP-D adds them to `lib/api-types.ts` with the endpoint; until then they are
// declared here, exactly as 2.12 gives the JSON.

/** One `(transip, dstintf, trandisp)` of trusted `fg_traffic` rows of the window. */
export interface ObservedNatRow {
  transip: string;
  dstintf: string | null;
  trandisp: string | null;
  sessions: number;
  first_seen: DateTimeString;
  last_seen: DateTimeString;
  /** The exit that claims the address (by the stored `nat.egresses`), `null` when none does. */
  egress: string | null;
  /** `trandisp` is one of `nat.snat_trandisp`. */
  trandisp_known: boolean;
}

export interface ObservedNat {
  hours: number;
  /** More than 100 groups: the busiest are listed. */
  truncated: boolean;
  rows: ObservedNatRow[];
  unmapped_transips: string[];
  unknown_trandisp: string[];
  /** The stored `nat.egresses` cannot be read: every row has `egress: null`. */
  exits_invalid?: boolean;
}

/** srcmac per `srcintf` in the last hour: rows, distinct MACs and the most IPv4s of one MAC. */
export interface ObservedSrcmacInterface {
  srcintf: string;
  rows: number;
  macs: number;
  max_ips_per_mac: number;
  /** Listed in `lookup.srcmac_interfaces`. */
  configured: boolean;
}

export interface ObservedSrcmac {
  hours: number;
  truncated: boolean;
  interfaces: ObservedSrcmacInterface[];
}

/** `GET /settings/fortigate/observed?hours=24` (`settings.manage`; counts and the firewall's own
 * addresses only, not audited). */
export interface FortigateObserved {
  generated_at: DateTimeString;
  nat: ObservedNat;
  srcmac: ObservedSrcmac;
}

/**
 * What "Çıkış olarak ekle" offers for an observed address: `join` — add it to an exit of the
 * draft that lists the row's `dstintf` (a new NAT address on an existing line comes first, so the
 * line's own searches keep finding it); `direct` for a public address; `isp_nat` and `internal`
 * (not an internet exit) for a private or shared one.
 */
export type PrefillOption =
  | { kind: 'join'; row: string; name: string; field: 'public' | 'transips' }
  | { kind: 'direct' | 'isp_nat' | 'internal' };

export function prefillOptions(
  observed: Pick<ObservedNatRow, 'transip' | 'dstintf'>,
  rows: readonly EgressRow[],
): PrefillOption[] {
  const shared = isPrivateOrShared(observed.transip);
  const dstintf = observed.dstintf;
  const joins: PrefillOption[] = dstintf
    ? rows
        .filter((row) => {
          const exit = exitOfRow(row);
          const fits = shared
            ? exit.mode === 'isp_nat' || exit.mode === 'internal'
            : exit.mode === 'direct';
          return fits && exit.interfaces.includes(dstintf);
        })
        .map((row) => ({
          kind: 'join' as const,
          row: row.key,
          name: row.name.trim(),
          field: shared ? ('transips' as const) : ('public' as const),
        }))
    : [];
  return [
    ...joins,
    ...(shared
      ? [{ kind: 'isp_nat' as const }, { kind: 'internal' as const }]
      : [{ kind: 'direct' as const }]),
  ];
}

function appendLine(text: string, item: string): string {
  return text.trim() === '' ? item : `${text.replace(/\s+$/, '')}\n${item}`;
}

/** The rows after taking `option` for the observed address. */
export function applyPrefill(
  rows: readonly EgressRow[],
  observed: Pick<ObservedNatRow, 'transip' | 'dstintf'>,
  option: PrefillOption,
): EgressRow[] {
  const interfaces = observed.dstintf ?? '';
  if (option.kind === 'join')
    return rows.map((row) =>
      row.key === option.row &&
      !listItems(row[option.field], option.field).includes(observed.transip)
        ? { ...row, [option.field]: appendLine(row[option.field], observed.transip) }
        : row,
    );
  const name = freeName(rows, option.kind === 'internal' ? 'nat' : 'hat');
  const fields: Partial<EgressRow> =
    option.kind === 'direct'
      ? { name, mode: 'direct', public: observed.transip, interfaces }
      : { name, mode: option.kind, transips: observed.transip, interfaces };
  return [...rows, emptyRow(rows, fields)];
}

/**
 * The row of the draft that claims an observed address (its public or NAT addresses hold it;
 * of several, the one on the row's `dstintf`), `null` when none does.
 */
export function draftClaim(
  observed: Pick<ObservedNatRow, 'transip' | 'dstintf'>,
  rows: readonly EgressRow[],
): EgressRow | null {
  const address = network(observed.transip);
  if (address === null) return null;
  const claims = rows.filter((row) => {
    const exit = exitOfRow(row);
    return RANGES([...exit.public, ...exit.transips]).some((range) => overlaps(range, address));
  });
  return (
    claims.find(
      (row) => observed.dstintf !== null && exitOfRow(row).interfaces.includes(observed.dstintf),
    ) ??
    claims[0] ??
    null
  );
}
