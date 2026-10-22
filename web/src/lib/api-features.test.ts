/**
 * The flags of `api-features.ts` follow the API's OpenAPI document (`src/generated/api-schemas.json`,
 * read by vite.config.ts at build time): an option is offered exactly when the API accepts its
 * field.
 */

import contract from '@/generated/api-schemas.json';

import { evidenceAuditLogAccepted } from './api-features';

type Schema = { properties?: Record<string, unknown>; additionalProperties?: boolean };
const SCHEMAS = contract.schemas as Record<string, Schema>;

describe('API feature flags follow the OpenAPI document', () => {
  it('include_audit_log is offered exactly when the API’s EvidenceCreate accepts it', () => {
    const schema = SCHEMAS.EvidenceCreate;
    expect(schema, 'EvidenceCreate in api-schemas.json').toBeDefined();
    // The API refuses unknown fields: an option it does not accept would fail every request.
    expect(schema?.additionalProperties).toBe(false);
    const accepted = Object.hasOwn(schema?.properties ?? {}, 'include_audit_log');
    expect(evidenceAuditLogAccepted()).toBe(accepted);
  });
});
