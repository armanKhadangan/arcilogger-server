/// <reference types="vite/client" />

/** Product version, injected by Vite from package.json at build time. */
declare const __APP_VERSION__: string;

/**
 * The API's `EvidenceCreate` accepts `include_audit_log` (from src/generated/api-schemas.json at
 * build time; read through `evidenceAuditLogAccepted` in src/lib/api-features.ts).
 */
declare const __API_EVIDENCE_AUDIT_LOG__: boolean;

/**
 * The developer UI kit (`/ui-kit`) is built in: the dev server, the unit tests and the E2E build,
 * never the bundle the installer ships (vite.config.ts; read through `UI_KIT_ITEM` in src/nav.ts).
 */
declare const __UI_KIT__: boolean;
