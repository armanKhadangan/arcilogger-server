/**
 * Ayarlar → Kaynaklar (contract 2.6 "sources"): the `[[sources]]` table as the browser edits it
 * and the browser's checks of a source before it is sent. The API runs the rules again (with the
 * server's own addresses, which the browser does not know) and the administration service once
 * more on the server. Validators return i18n keys.
 */

import type {
  AdminPolicyOut,
  SourceConfig,
  SourceTransport,
  SourceType,
} from '@/lib/settings-types';
import type { FieldErrors } from '@/lib/validation';

import { integerError, parseInteger } from './settings-validation';

export const SOURCE_TYPES: readonly SourceType[] = ['fortigate', 'mikrotik', 'generic'];
export const TRANSPORTS: readonly SourceTransport[] = ['any', 'udp', 'tcp'];
export const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;
export const HEARTBEAT_MIN = 1;
export const HEARTBEAT_MAX = 1440;

/** The form of a source: the heartbeat as typed. */
export interface SourceForm {
  name: string;
  type: SourceType;
  address: string;
  heartbeat: string;
  expected: boolean;
  transport: SourceTransport;
  enabled: boolean;
}

export type SourceField = 'name' | 'address' | 'heartbeat';

export const NEW_SOURCE: SourceForm = {
  name: '',
  type: 'generic',
  address: '',
  heartbeat: '15',
  expected: true,
  transport: 'any',
  enabled: true,
};

export function toSourceForm(source: SourceConfig): SourceForm {
  return {
    name: source.name,
    type: source.type,
    address: source.address,
    heartbeat: String(source.heartbeat_minutes),
    expected: source.expected,
    transport: source.transport,
    enabled: source.enabled,
  };
}

export function fromSourceForm(form: SourceForm): SourceConfig {
  return {
    name: form.name.trim(),
    type: form.type,
    address: form.address.trim(),
    heartbeat_minutes: parseInteger(form.heartbeat) ?? 0,
    expected: form.expected,
    transport: form.transport,
    enabled: form.enabled,
  };
}

/** Only the fields of `SourceConfig` (the table rows carry the firewall state and last message). */
export function sourceConfig(source: SourceConfig): SourceConfig {
  return {
    name: source.name,
    type: source.type,
    address: source.address,
    heartbeat_minutes: source.heartbeat_minutes,
    expected: source.expected,
    transport: source.transport,
    enabled: source.enabled,
  };
}

const OCTET = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;

/** A dotted-quad IPv4 address as a 32-bit number, else `null`. */
export function parseIpv4(text: string): number | null {
  const parts = text.split('.');
  if (parts.length !== 4 || !parts.every((part) => OCTET.test(part))) return null;
  return parts.reduce((value, part) => value * 256 + Number(part), 0);
}

export interface Network {
  base: number;
  prefix: number;
}

/** An address (`/32`) or a network in CIDR form; `hostBits`: the address is not the network's. */
export function parseNetwork(text: string): (Network & { hostBits: boolean }) | null {
  const [address = '', prefixText, ...rest] = text.trim().split('/');
  if (rest.length > 0) return null;
  const value = parseIpv4(address);
  if (value === null) return null;
  const prefix =
    prefixText === undefined ? 32 : /^\d{1,2}$/.test(prefixText) ? Number(prefixText) : NaN;
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32) return null;
  const size = 2 ** (32 - prefix);
  const base = Math.floor(value / size) * size;
  return { base, prefix, hostBits: base !== value };
}

function contains(outer: Network, inner: Network): boolean {
  if (inner.prefix < outer.prefix) return false;
  const size = 2 ** (32 - outer.prefix);
  return Math.floor(inner.base / size) * size === outer.base;
}

function overlaps(a: Network, b: Network): boolean {
  return contains(a, b) || contains(b, a);
}

/** Addresses no source can have: loopback, multicast, unspecified, link-local, broadcast, reserved. */
const RESERVED: readonly Network[] = [
  { base: parseIpv4('0.0.0.0') ?? 0, prefix: 8 },
  { base: parseIpv4('127.0.0.0') ?? 0, prefix: 8 },
  { base: parseIpv4('169.254.0.0') ?? 0, prefix: 16 },
  { base: parseIpv4('224.0.0.0') ?? 0, prefix: 4 },
  { base: parseIpv4('240.0.0.0') ?? 0, prefix: 4 },
];

/**
 * The browser's checks of a source (contract 2.6): the name (`^[a-z0-9][a-z0-9-]{0,62}$`, not
 * `unknown-*`, unique), an IPv4 host or network inside the policy's networks with a long enough
 * prefix, not reserved, not taken and not overlapping another source's network, the heartbeat
 * 1–1440 minutes. `editing`: the name of the source being changed (it is not compared with itself).
 */
export function validateSource(
  form: SourceForm,
  sources: readonly SourceConfig[],
  policy: AdminPolicyOut | undefined,
  editing: string | null,
): FieldErrors<SourceField> {
  const errors: FieldErrors<SourceField> = {};
  const others = sources.filter((source) => source.name !== editing);
  const name = form.name.trim();
  if (editing === null) {
    if (!name) errors.name = 'validation.required';
    else if (!NAME_PATTERN.test(name)) errors.name = 'settings.sources.errors.name';
    else if (name.startsWith('unknown-')) errors.name = 'settings.sources.errors.nameUnknown';
    else if (others.some((source) => source.name === name))
      errors.name = 'settings.sources.errors.nameTaken';
  }
  const address = form.address.trim();
  const network = parseNetwork(address);
  if (!address) errors.address = 'validation.required';
  else if (address.includes(':')) errors.address = 'settings.sources.errors.ipv6';
  else if (!network) errors.address = 'settings.sources.errors.address';
  else if (network.hostBits) errors.address = 'settings.sources.errors.hostBits';
  else if (
    RESERVED.some((reserved) => overlaps(reserved, network)) ||
    (network.prefix === 32 && network.base === 0xffffffff)
  )
    errors.address = 'settings.sources.errors.reserved';
  else if (policy && network.prefix < 32 && network.prefix < policy.min_prefix_v4)
    errors.address = 'settings.sources.errors.prefix';
  else if (
    policy &&
    policy.source_networks.length > 0 &&
    !policy.source_networks.some((text) => {
      const allowed = parseNetwork(text);
      return allowed !== null && contains(allowed, network);
    })
  )
    errors.address = 'settings.sources.errors.notAllowed';
  else if (others.some((source) => source.address === address))
    errors.address = 'settings.sources.errors.addressTaken';
  else if (
    network.prefix < 32 &&
    others.some((source) => {
      const other = parseNetwork(source.address);
      return other !== null && other.prefix < 32 && overlaps(other, network);
    })
  )
    errors.address = 'settings.sources.errors.overlap';
  const heartbeat = integerError(form.heartbeat, HEARTBEAT_MIN, HEARTBEAT_MAX);
  if (heartbeat) errors.heartbeat = heartbeat;
  return errors;
}
