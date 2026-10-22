/**
 * The generated contract files (`src/generated/`, from `web/scripts/export_api_contract.py`) for
 * the E2E tests. Read with `fs`: Playwright loads the specs as native ES modules, where a JSON
 * import would need an import attribute.
 */

import { readFileSync } from 'node:fs';

import type { AuditEntry, Role } from '../src/lib/api-types';

function load(name: string): unknown {
  return JSON.parse(readFileSync(new URL(`../src/generated/${name}`, import.meta.url), 'utf8'));
}

export const AUDIT_VECTORS = load('audit-vectors.json') as {
  entries: { api: AuditEntry; canonical: string }[];
};

export const ROLE_CONTRACT = load('roles.json') as {
  roles: { id: Role; label_tr: string; label_en: string; permissions: string[] }[];
  privacy_permissions: string[];
};

/** The API's OpenAPI schemas (`components.schemas`), for the request bodies the mock accepts. */
export const API_SCHEMAS = (
  load('api-schemas.json') as {
    schemas: Record<
      string,
      { properties?: Record<string, unknown>; required?: string[]; additionalProperties?: boolean }
    >;
  }
).schemas;
