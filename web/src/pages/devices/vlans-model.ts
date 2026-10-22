/**
 * The VLAN form (add and edit) of "Departmanlar ve VLAN'lar": its fields as typed, the
 * browser-side checks (a network address with its prefix length, host bits zero — the server's
 * rule in `routers/vlans.py`, which checks again) and the request bodies (no components here;
 * unit-tested in `vlans-model.test.ts`).
 */

import type { VlanCreate, VlanOut, VlanUpdate } from '@/lib/api-types';
import { codePointLength, validateDisplayName, type FieldErrors } from '@/lib/validation';

import { NO_DEPARTMENT } from './persons-model';

/** Control, format (bidi overrides, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
/** The API's limits of `VlanCreate` / `VlanUpdate`. */
export const VLAN_LIMITS = { purpose: 500, dhcpServer: 64, notes: 2000, vlanId: 4094 } as const;
/** A DHCP server's name as the router's log lines carry it (`dhcp20`): no spaces or colons. */
const DHCP_SERVER = /^[^\s:]+$/u;

export type VlanField = 'subnet' | 'vlanId' | 'name' | 'purpose' | 'dhcpServer' | 'notes';

export interface VlanFormValues {
  subnet: string;
  vlanId: string;
  name: string;
  purpose: string;
  dhcpServer: string;
  /** A department id or {@link NO_DEPARTMENT}. */
  department: string;
  guest: boolean;
  notes: string;
}

/** "VLAN 20", or the subnet of a network without a VLAN id (a transit network). */
export function vlanLabel(vlan: Pick<VlanOut, 'subnet' | 'vlan_id'>): string {
  return vlan.vlan_id === null ? vlan.subnet : `VLAN ${String(vlan.vlan_id)}`;
}

export function vlanFormValues(vlan: VlanOut | null): VlanFormValues {
  return {
    subnet: vlan?.subnet ?? '',
    vlanId: vlan && vlan.vlan_id !== null ? String(vlan.vlan_id) : '',
    name: vlan?.name_tr ?? '',
    purpose: vlan?.purpose ?? '',
    dhcpServer: vlan?.dhcp_server ?? '',
    department: vlan?.department ? String(vlan.department.id) : NO_DEPARTMENT,
    guest: vlan?.guest ?? false,
    notes: vlan?.notes ?? '',
  };
}

// -- networks -------------------------------------------------------------------------------------

/** An IPv4 address as a number, or `null` (no leading zeros, like Python's `ipaddress`). */
function ipv4(text: string): bigint | null {
  const parts = text.split('.');
  if (parts.length !== 4) return null;
  let value = 0n;
  for (const part of parts) {
    if (!/^(0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255) return null;
    value = value * 256n + BigInt(part);
  }
  return value;
}

/** An IPv6 address (`::` and a trailing IPv4 part allowed) as a number, or `null`. */
function ipv6(text: string): bigint | null {
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const groups = (part: string): bigint[] | null => {
    if (part === '') return [];
    const items = part.split(':');
    const result: bigint[] = [];
    for (const [index, item] of items.entries()) {
      if (index === items.length - 1 && item.includes('.')) {
        const tail = ipv4(item);
        if (tail === null) return null;
        result.push(tail >> 16n, tail & 0xffffn);
      } else if (/^[0-9a-f]{1,4}$/iu.test(item)) result.push(BigInt(`0x${item}`));
      else return null;
    }
    return result;
  };
  const head = groups(halves[0] ?? '');
  const tail = halves.length === 2 ? groups(halves[1] ?? '') : [];
  if (head === null || tail === null) return null;
  const missing = 8 - head.length - tail.length;
  if (halves.length === 2 ? missing < 1 : missing !== 0) return null;
  return [...head, ...Array<bigint>(missing).fill(0n), ...tail].reduce(
    (value, group) => (value << 16n) | group,
    0n,
  );
}

function formatIpv4(value: bigint): string {
  return [24n, 16n, 8n, 0n].map((shift) => String((value >> shift) & 0xffn)).join('.');
}

export type SubnetCheck =
  | { ok: true }
  | { ok: false; error: 'validation.required' | 'devices.vlans.errors.prefix' }
  | { ok: false; error: 'devices.vlans.errors.subnet' }
  | { ok: false; error: 'devices.vlans.errors.hostBits'; network: string | null }
  | { ok: false; error: 'devices.vlans.errors.ipv4Mapped'; network: string };

/** The upper 96 bits of `::ffff:0:0/96`, IPv4 addresses written as IPv6 (RFC 4291 2.5.5.2). */
const IPV4_MAPPED = 0xffffn;

/**
 * A network address with its prefix length and the host bits zero (`10.0.20.0/24`, not
 * `10.0.20.5/24`); for host bits the IPv4 network meant (IPv6: the server names it). An IPv4
 * network written as IPv6 (`::ffff:10.0.20.0/120`) is refused with the IPv4 network meant: the
 * addresses of leases, devices and logs are IPv4 and never fall into it (the server's rule).
 */
export function checkSubnet(text: string): SubnetCheck {
  const value = text.trim();
  if (!value) return { ok: false, error: 'validation.required' };
  if (!value.includes('/')) return { ok: false, error: 'devices.vlans.errors.prefix' };
  const [address = '', prefix = '', ...rest] = value.split('/');
  const four = address.includes(':') ? null : ipv4(address);
  const six = address.includes(':') && !address.includes('%') ? ipv6(address) : null;
  const bits = four !== null ? 32 : six !== null ? 128 : 0;
  if (rest.length > 0 || !bits || !/^\d{1,3}$/.test(prefix) || Number(prefix) > bits)
    return { ok: false, error: 'devices.vlans.errors.subnet' };
  const host = (1n << BigInt(bits - Number(prefix))) - 1n;
  const number = (four ?? six) as bigint;
  if ((number & host) !== 0n)
    return {
      ok: false,
      error: 'devices.vlans.errors.hostBits',
      network: four !== null ? `${formatIpv4(number & ~host)}/${prefix}` : null,
    };
  if (six !== null && Number(prefix) >= 96 && six >> 32n === IPV4_MAPPED)
    return {
      ok: false,
      error: 'devices.vlans.errors.ipv4Mapped',
      network: `${formatIpv4(six & 0xffffffffn)}/${String(Number(prefix) - 96)}`,
    };
  return { ok: true };
}

// -- checks ---------------------------------------------------------------------------------------

function optionalText(value: string, max: number): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (codePointLength(trimmed) > max) return 'validation.tooLong';
  if (FORBIDDEN.test(trimmed)) return 'validation.controlCharacters';
  return undefined;
}

/** The VLAN id typed (`null` when empty), or `undefined` when it is not 1…4094. */
function vlanIdOf(text: string): number | null | undefined {
  const value = text.trim();
  if (!value) return null;
  if (!/^\d{1,4}$/.test(value)) return undefined;
  const id = Number(value);
  return id >= 1 && id <= VLAN_LIMITS.vlanId ? id : undefined;
}

/** Problems of the form as i18n keys (the server checks again). */
export function validateVlan(values: VlanFormValues): FieldErrors<VlanField> {
  const errors: FieldErrors<VlanField> = {};
  const subnet = checkSubnet(values.subnet);
  if (!subnet.ok) errors.subnet = subnet.error;
  if (vlanIdOf(values.vlanId) === undefined) errors.vlanId = 'devices.vlans.errors.vlanId';
  const name = validateDisplayName(values.name);
  if (name) errors.name = name;
  const purpose = optionalText(values.purpose, VLAN_LIMITS.purpose);
  if (purpose) errors.purpose = purpose;
  const server = values.dhcpServer.trim();
  if (server) {
    if (codePointLength(server) > VLAN_LIMITS.dhcpServer) errors.dhcpServer = 'validation.tooLong';
    else if (!DHCP_SERVER.test(server) || FORBIDDEN.test(server))
      errors.dhcpServer = 'devices.vlans.errors.dhcpServer';
  }
  // Notes may have line breaks; other control characters are refused.
  if (codePointLength(values.notes.trim()) > VLAN_LIMITS.notes) errors.notes = 'validation.tooLong';
  else if (FORBIDDEN.test(values.notes.replace(/\r?\n/g, ' ')))
    errors.notes = 'validation.controlCharacters';
  return errors;
}

// -- request bodies -------------------------------------------------------------------------------

const orNull = (value: string): string | null => value.trim() || null;

function departmentId(value: string): number | null {
  return value === NO_DEPARTMENT ? null : Number(value);
}

/** `POST /vlans` of a valid form: empty optional fields are left out. */
export function vlanCreateBody(values: VlanFormValues): VlanCreate {
  const body: VlanCreate = { subnet: values.subnet.trim(), name_tr: values.name.trim() };
  const vlanId = vlanIdOf(values.vlanId);
  if (typeof vlanId === 'number') body.vlan_id = vlanId;
  const purpose = orNull(values.purpose);
  if (purpose !== null) body.purpose = purpose;
  const server = orNull(values.dhcpServer);
  if (server !== null) body.dhcp_server = server;
  const department = departmentId(values.department);
  if (department !== null) body.department_id = department;
  if (values.guest) body.guest = true;
  const notes = orNull(values.notes);
  if (notes !== null) body.notes = notes;
  return body;
}

/**
 * `PATCH /vlans` of a valid form: the VLAN's current `subnet` and only what changed (a cleared
 * field as `null`, a changed network as `new_subnet`). `null` when nothing changed.
 */
export function vlanChanges(vlan: VlanOut, values: VlanFormValues): VlanUpdate | null {
  const changes: Omit<VlanUpdate, 'subnet'> = {};
  const subnet = values.subnet.trim();
  if (subnet.toLowerCase() !== vlan.subnet.toLowerCase()) changes.new_subnet = subnet;
  const vlanId = vlanIdOf(values.vlanId) ?? null;
  if (vlanId !== vlan.vlan_id) changes.vlan_id = vlanId;
  const name = values.name.trim();
  if (name !== vlan.name_tr) changes.name_tr = name;
  if (orNull(values.purpose) !== vlan.purpose) changes.purpose = orNull(values.purpose);
  if (orNull(values.dhcpServer) !== vlan.dhcp_server)
    changes.dhcp_server = orNull(values.dhcpServer);
  const department = departmentId(values.department);
  if (department !== (vlan.department?.id ?? null)) changes.department_id = department;
  if (values.guest !== vlan.guest) changes.guest = values.guest;
  if (orNull(values.notes) !== vlan.notes) changes.notes = orNull(values.notes);
  return Object.keys(changes).length > 0 ? { subnet: vlan.subnet, ...changes } : null;
}
