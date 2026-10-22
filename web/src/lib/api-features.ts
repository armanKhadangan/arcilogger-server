/**
 * Request fields the web app may send only once the API accepts them.
 *
 * Every request model of the API is `extra="forbid"`: a body with a field the API does not know is
 * refused with 422 `validation_error`, so an option whose field the API lacks would fail every
 * time. The flags are therefore read from the generated contract `src/generated/api-schemas.json`
 * when the bundle is built (`define` in vite.config.ts), never set by hand: that file is the API's
 * OpenAPI document (`web/scripts/export_api_contract.py --check` keeps it current) and
 * `api-types.ts` must match it (`api-types.test.ts`). An option appears with the API release that
 * accepts its field, and not before.
 */

/**
 * `include_audit_log` of `POST /evidence` (orchestrator decision 1): an administrator may add the
 * days' ARCILOGGER audit log to a package for an internal audit; off by default (KVKK data
 * minimisation). Whether the evidence wizard may offer "ARCILOGGER denetim kaydını ekle" (to
 * administrators) and send the field. A function, so tests can switch it.
 */
export function evidenceAuditLogAccepted(): boolean {
  return __API_EVIDENCE_AUDIT_LOG__;
}
