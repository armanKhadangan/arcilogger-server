/**
 * Roles and permissions as the server enforces them.
 *
 * `src/generated/roles.json` is generated from `arcilogger_api.security` (ROLE_PERMISSIONS,
 * ROLE_LABELS, PERMISSIONS) by `web/scripts/export_api_contract.py`, so the menu filter and the
 * read-only role matrix cannot drift from the API. Hiding a menu entry is a convenience only:
 * every endpoint checks the permission again (403 `forbidden`).
 */

import contract from '@/generated/roles.json';

import type { Role } from './api-types';
import type { Lang } from './format';

export interface RoleInfo {
  id: Role;
  label_tr: string;
  label_en: string;
  permissions: readonly string[];
}

export interface PermissionInfo {
  id: string;
  label_tr: string;
  label_en: string;
}

/** Roles in the order of the spec 13 table (admin, auditor, hr, viewer). */
export const ROLES = contract.roles as readonly RoleInfo[];
/** Every permission, columns of the spec 13 table first. */
export const PERMISSIONS = contract.permissions as readonly PermissionInfo[];
/** Seeing any of these means seeing person-level data: the KVKK notice is required. */
export const PRIVACY_PERMISSIONS: readonly string[] = contract.privacy_permissions;
/** How long a sign-in or a re-authentication counts as recent (sensitive actions). */
export const RECENT_AUTH_MINUTES: number = contract.recent_auth_minutes;

export const ROLE_IDS: readonly Role[] = ROLES.map((role) => role.id);

export function isRole(value: string): value is Role {
  return (ROLE_IDS as readonly string[]).includes(value);
}

/** Label of a role in `lang`; unknown roles are shown as their id. */
export function roleLabel(role: string, lang: Lang): string {
  const found = ROLES.find((item) => item.id === role);
  if (!found) return role;
  return lang === 'en' ? found.label_en : found.label_tr;
}

export function permissionLabel(permission: PermissionInfo, lang: Lang): string {
  return lang === 'en' ? permission.label_en : permission.label_tr;
}

export function roleHas(role: string, permission: string): boolean {
  return ROLES.find((item) => item.id === role)?.permissions.includes(permission) ?? false;
}

/** True when `granted` holds at least one of `anyOf` (an empty list needs no permission). */
export function hasAny(granted: ReadonlySet<string>, anyOf: readonly string[]): boolean {
  return anyOf.length === 0 || anyOf.some((permission) => granted.has(permission));
}
