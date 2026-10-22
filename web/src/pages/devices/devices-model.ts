/**
 * State of the "Cihazlar ve Kişiler" page that lives in the address bar, and its translation
 * into the API's query parameters (no components here; unit-tested in `devices-model.test.ts`).
 *
 * The address keeps the tab and the device list's filters and sort order, so a view such as
 * "Bilinmeyen cihazlar" can be reloaded, bookmarked or linked. It never holds the search text:
 * a person's name typed into the search box must not end up in the browser history or in the
 * web server's access log (KVKK data minimisation; same rule as the top bar's global search).
 */

import type {
  DeviceFilters,
  DeviceOut,
  DeviceSort,
  DeviceType,
  DeviceUpdate,
  PersonFilters,
  SortOrder,
} from '@/lib/api-types';
import { codePointLength, type FieldErrors } from '@/lib/validation';

export const DEVICE_TABS = ['devices', 'persons', 'departments', 'transfer'] as const;
export type DevicesTab = (typeof DEVICE_TABS)[number];

export const DEVICE_TYPES: readonly DeviceType[] = [
  'pc',
  'phone',
  'printer',
  'ip_phone',
  'ap',
  'server',
  'camera',
  'other',
];

export const DEVICE_SORTS: readonly DeviceSort[] = [
  'last_seen',
  'first_seen',
  'mac',
  'last_ip',
  'name',
];

/** `?tab=` of the page (unknown values: the first tab). */
export function tabFrom(params: URLSearchParams): DevicesTab {
  const value = params.get('tab');
  return (DEVICE_TABS as readonly string[]).includes(value ?? '')
    ? (value as DevicesTab)
    : 'devices';
}

/** The address with another tab (the default tab is left out); other parameters stay. */
export function withTab(params: URLSearchParams, tab: DevicesTab): URLSearchParams {
  const next = new URLSearchParams(params);
  if (tab === 'devices') next.delete('tab');
  else next.set('tab', tab);
  return next;
}

export function isDeviceType(value: string | null | undefined): value is DeviceType {
  return (DEVICE_TYPES as readonly (string | null | undefined)[]).includes(value);
}

/** Filters and sort order of the device list (everything but the search text). */
export interface DeviceView {
  /** "Bilinmeyen cihazlar": no current owner and not infrastructure. */
  unknown: boolean;
  type: DeviceType | null;
  vlan: number | null;
  /** `null`: both randomised and fixed MAC addresses. */
  randomized: boolean | null;
  infrastructure: boolean | null;
  sort: DeviceSort;
  order: SortOrder;
}

export const DEFAULT_VIEW: DeviceView = {
  unknown: false,
  type: null,
  vlan: null,
  randomized: null,
  infrastructure: null,
  sort: 'last_seen',
  order: 'desc',
};

/** Query parameter names of the view in the page address. */
const KEYS = {
  unknown: 'unknown',
  type: 'type',
  vlan: 'vlan',
  randomized: 'randomized',
  infrastructure: 'infrastructure',
  sort: 'sort',
  order: 'order',
} as const;

function flag(value: string | null): boolean | null {
  if (value === 'true' || value === '1') return true;
  if (value === 'false' || value === '0') return false;
  return null;
}

function vlanId(value: string | null): number | null {
  if (!value || !/^\d{1,4}$/.test(value)) return null;
  const id = Number(value);
  return id >= 1 && id <= 4094 ? id : null;
}

/** The view in the address; anything unknown or malformed falls back to the default. */
export function viewFromSearch(params: URLSearchParams): DeviceView {
  const type = params.get(KEYS.type);
  const sort = params.get(KEYS.sort);
  const order = params.get(KEYS.order);
  return {
    unknown: flag(params.get(KEYS.unknown)) === true,
    type: isDeviceType(type) ? type : null,
    vlan: vlanId(params.get(KEYS.vlan)),
    randomized: flag(params.get(KEYS.randomized)),
    infrastructure: flag(params.get(KEYS.infrastructure)),
    sort: (DEVICE_SORTS as readonly (string | null)[]).includes(sort)
      ? (sort as DeviceSort)
      : DEFAULT_VIEW.sort,
    order: order === 'asc' || order === 'desc' ? order : DEFAULT_VIEW.order,
  };
}

/** `params` with `view` written into it (defaults left out); other parameters (tab) stay. */
export function viewToSearch(view: DeviceView, params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of Object.values(KEYS)) next.delete(key);
  if (view.unknown) next.set(KEYS.unknown, 'true');
  if (view.type) next.set(KEYS.type, view.type);
  if (view.vlan !== null) next.set(KEYS.vlan, String(view.vlan));
  if (view.randomized !== null) next.set(KEYS.randomized, String(view.randomized));
  if (view.infrastructure !== null) next.set(KEYS.infrastructure, String(view.infrastructure));
  if (view.sort !== DEFAULT_VIEW.sort || view.order !== DEFAULT_VIEW.order) {
    next.set(KEYS.sort, view.sort);
    next.set(KEYS.order, view.order);
  }
  return next;
}

/** True when the list is filtered by more than its sort order (search text included). */
export function isFiltered(view: DeviceView, search: string): boolean {
  return (
    search.trim() !== '' ||
    view.unknown ||
    view.type !== null ||
    view.vlan !== null ||
    view.randomized !== null ||
    view.infrastructure !== null
  );
}

/** Parameters of `GET /devices` for one page (`page` counts from 0). */
export function deviceQuery(
  view: DeviceView,
  search: string,
  page: number,
  pageSize: number,
): DeviceFilters {
  const q = search.trim();
  return {
    ...(q ? { q } : {}),
    ...(view.unknown ? { unknown: true } : {}),
    ...(view.type ? { type: view.type } : {}),
    ...(view.vlan !== null ? { vlan: view.vlan } : {}),
    ...(view.randomized !== null ? { randomized: view.randomized } : {}),
    ...(view.infrastructure !== null ? { infrastructure: view.infrastructure } : {}),
    sort: view.sort,
    order: view.order,
    limit: pageSize,
    offset: Math.max(0, page) * pageSize,
  };
}

/** Parameters of `GET /persons` for one page. */
export function personQuery(
  filters: { search: string; active: boolean | null; departmentId: number | null },
  page: number,
  pageSize: number,
): PersonFilters {
  const q = filters.search.trim();
  return {
    ...(q ? { q } : {}),
    ...(filters.active !== null ? { active: filters.active } : {}),
    ...(filters.departmentId !== null ? { department_id: filters.departmentId } : {}),
    limit: pageSize,
    offset: Math.max(0, page) * pageSize,
  };
}

/** Table sort state (TanStack `SortingState`) of a view, and back. */
export function sortingOf(view: DeviceView): { id: DeviceSort; desc: boolean }[] {
  return [{ id: view.sort, desc: view.order === 'desc' }];
}

export function viewWithSorting(
  view: DeviceView,
  sorting: readonly { id: string; desc: boolean }[],
): DeviceView {
  const first = sorting[0];
  if (!first || !(DEVICE_SORTS as readonly string[]).includes(first.id))
    return { ...view, sort: DEFAULT_VIEW.sort, order: DEFAULT_VIEW.order };
  return { ...view, sort: first.id as DeviceSort, order: first.desc ? 'desc' : 'asc' };
}

/** "1–50" of "312": the rows of a page (`null` for an empty list). */
export function pageRange(
  offset: number,
  shown: number,
  total: number,
): { first: number; last: number; total: number } | null {
  if (total === 0 || shown === 0) return null;
  return { first: offset + 1, last: Math.min(offset + shown, total), total };
}

/** Comma- or line-separated tags, trimmed, empty ones and duplicates dropped. */
export function parseTags(text: string): string[] {
  const tags: string[] = [];
  for (const part of text.split(/[,\n]/)) {
    const tag = part.trim();
    if (tag && !tags.includes(tag)) tags.push(tag);
  }
  return tags;
}

/** Select value of "type unknown" (a select item cannot have an empty value). */
export const UNKNOWN_TYPE = 'unknown';

/** The device edit form's fields as typed. */
export interface DeviceEditValues {
  name: string;
  /** A {@link DeviceType} or {@link UNKNOWN_TYPE}. */
  type: string;
  infrastructure: boolean;
  notes: string;
  /** Comma-separated. */
  tags: string;
}

export function deviceEditValues(device: DeviceOut): DeviceEditValues {
  return {
    name: device.name ?? '',
    type: device.type ?? UNKNOWN_TYPE,
    infrastructure: device.infrastructure,
    notes: device.notes ?? '',
    tags: device.tags.join(', '),
  };
}

/** The API's limits of `DeviceUpdate` (characters; `tags`: how many). */
export const EDIT_LIMITS = { name: 200, notes: 2000, tags: 20, tag: 50 } as const;
/** Control, format (bidi overrides, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;

/** Problems of the edit form as i18n keys (`{{max}}`: the limit of the field). */
export function deviceEditProblems(
  values: DeviceEditValues,
): FieldErrors<'name' | 'tags' | 'notes'> {
  const errors: FieldErrors<'name' | 'tags' | 'notes'> = {};
  const name = values.name.trim();
  if (codePointLength(name) > EDIT_LIMITS.name) errors.name = 'devices.detail.tooLong';
  else if (FORBIDDEN.test(name)) errors.name = 'validation.controlCharacters';
  const tags = parseTags(values.tags);
  if (tags.length > EDIT_LIMITS.tags) errors.tags = 'devices.detail.tooManyTags';
  else if (tags.some((tag) => codePointLength(tag) > EDIT_LIMITS.tag))
    errors.tags = 'devices.detail.tagTooLong';
  else if (tags.some((tag) => FORBIDDEN.test(tag))) errors.tags = 'validation.controlCharacters';
  const notes = values.notes.trim();
  if (codePointLength(notes) > EDIT_LIMITS.notes) errors.notes = 'devices.detail.tooLong';
  // Line breaks are fine in notes; other control characters are not.
  else if (FORBIDDEN.test(notes.replace(/\r?\n/g, ' ')))
    errors.notes = 'validation.controlCharacters';
  return errors;
}

/** Only the fields that changed (`PATCH /devices/{mac}` changes what it is sent). */
export function deviceChanges(device: DeviceOut, values: DeviceEditValues): DeviceUpdate {
  const changes: DeviceUpdate = {};
  const name = values.name.trim() || null;
  if (name !== device.name) changes.name = name;
  const type = isDeviceType(values.type) ? values.type : null;
  if (type !== device.type) changes.type = type;
  if (values.infrastructure !== device.infrastructure)
    changes.infrastructure = values.infrastructure;
  const notes = values.notes.trim() || null;
  if (notes !== device.notes) changes.notes = notes;
  const tags = parseTags(values.tags);
  if (tags.join('\n') !== device.tags.join('\n')) changes.tags = tags;
  return changes;
}
