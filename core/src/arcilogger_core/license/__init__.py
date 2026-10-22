"""Offline licensing of ARCILOGGER (ADR-0013; the licensing contract).

A perpetual licence with annual maintenance, signed offline by the licensor (Ed25519 over the
RFC 8785 canonical JSON of the payload) for one or more server ids. No network call, ever.

The single entry points (phase 2 compiles this package into one module):

* is module X enabled: :meth:`state.LicenseStatus.module_enabled`;
* what is the state: :func:`state.evaluate`;
* what time is it: :func:`trusted_time.trusted_now`;
* which keys are trusted: :data:`keys.TRUSTED_KEYS` (and :data:`revoked.REVOKED_LICENSE_IDS`);
* which server is this: :data:`fingerprint.IDENTITY_PROVIDER` /
  :func:`fingerprint.current_server_id`.

Whatever the state, the 5651 obligations (collection, sealing, retention, lookups, evidence,
audit, users, alerts) are never locked: only the paid modules are. The evidence path
(collector, ingest, sealing, retention, replica, evidence packages, the standalone verifier)
never imports this package.
"""

from __future__ import annotations

from arcilogger_core.license._constants import (
    LICENSOR_EMAIL,
    LICENSOR_NAME,
    PRODUCT_EPOCH,
    TRIAL_DAYS,
    release_date,
)
from arcilogger_core.license.document import (
    LicenseDocument,
    LicensePayload,
    load_license,
    payload_to_json,
    sign_payload,
)
from arcilogger_core.license.errors import HTTP_STATUS, MESSAGES, LicenseError
from arcilogger_core.license.fingerprint import (
    FingerprintError,
    ServerIdentity,
    current_identity,
    current_server_id,
    server_id_of,
)
from arcilogger_core.license.modules import CORE_MODULE, MODULES, Module, module, module_keys
from arcilogger_core.license.request import (
    LicenseRequest,
    make_request_code,
    new_request,
    parse_request_code,
    request_sha256,
)
from arcilogger_core.license.state import (
    SOURCE_ACTIVE_DAYS,
    TRIAL_CLOCK_TOLERANCE,
    UNLICENSED_USERS,
    Installation,
    LicenseInputs,
    LicenseStateName,
    LicenseStatus,
    LimitUse,
    check_applicable,
    evaluate,
    status_override,
)
from arcilogger_core.license.trusted_time import (
    TrustedTime,
    file_lower_bounds,
    read_only_roots,
    trusted_now,
)

__all__ = [
    "CORE_MODULE",
    "HTTP_STATUS",
    "LICENSOR_EMAIL",
    "LICENSOR_NAME",
    "MESSAGES",
    "MODULES",
    "PRODUCT_EPOCH",
    "SOURCE_ACTIVE_DAYS",
    "TRIAL_CLOCK_TOLERANCE",
    "TRIAL_DAYS",
    "UNLICENSED_USERS",
    "FingerprintError",
    "Installation",
    "LicenseDocument",
    "LicenseError",
    "LicenseInputs",
    "LicensePayload",
    "LicenseRequest",
    "LicenseStateName",
    "LicenseStatus",
    "LimitUse",
    "Module",
    "ServerIdentity",
    "TrustedTime",
    "check_applicable",
    "current_identity",
    "current_server_id",
    "evaluate",
    "file_lower_bounds",
    "load_license",
    "make_request_code",
    "module",
    "module_keys",
    "new_request",
    "parse_request_code",
    "payload_to_json",
    "read_only_roots",
    "release_date",
    "request_sha256",
    "server_id_of",
    "sign_payload",
    "status_override",
    "trusted_now",
]
