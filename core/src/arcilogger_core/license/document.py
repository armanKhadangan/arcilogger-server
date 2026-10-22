"""Licence files (``.lic``): strict parsing, Ed25519 verification, signing (contract 2.1).

A licence file is UTF-8 JSON ``{"payload": {...}, "signature": {"alg", "key_id", "value"}}``;
the Ed25519 signature covers exactly ``canonical_json(payload)`` (RFC 8785), so whitespace and
key order in the file do not matter. :func:`load_license` runs the published verification steps
in order, the first failure wins:

1. more than 65,536 bytes: ``license_too_large``;
2. not UTF-8, not JSON, duplicate keys, floats, wrong document or signature shape:
   ``license_malformed`` (every exception of parsing and canonicalisation becomes this code);
3. ``key_id`` not trusted: ``license_unknown_key``;
4. signature does not verify: ``license_signature_invalid``;
5. payload field rules fail: ``license_malformed`` (correctly signed, wrong content);
6. licence id revoked: ``license_revoked``.

The trusted keys are ``keys.TRUSTED_KEYS`` and the revoked ids ``revoked.REVOKED_LICENSE_IDS``,
both looked up at call time. ``keys=`` exists for the issuer tool and tests only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from arcilogger_core.canonical import CanonicalJsonError, canonical_json
from arcilogger_core.license import keys as _keys
from arcilogger_core.license import revoked as _revoked
from arcilogger_core.license._constants import PRODUCT
from arcilogger_core.license.codec import b64url_decode_strict, b64url_encode
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.fingerprint import SERVER_ID_PATTERN
from arcilogger_core.license.modules import CORE_MODULE
from arcilogger_core.license.request import (
    UUID_PATTERN,
    format_utc_seconds,
    one_line_text,
    parse_utc_seconds,
)

LICENSE_SCHEMA: Final = "arcilogger.license/1"
MAX_LICENSE_BYTES: Final = 65_536
SIGNATURE_ALG: Final = "Ed25519"
MAX_SERVER_IDS: Final = 16
MAX_LIMIT: Final = 100_000

LICENSE_ID_PATTERN: Final = re.compile(r"ARCI-L-[0-9]{8}-[0-9A-HJKMNP-TV-Z]{8}")
KEY_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9-]{2,31}")
MODULE_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{1,40}")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_BOM: Final = b"\xef\xbb\xbf"

PAYLOAD_KEYS: Final = frozenset(
    {
        "schema",
        "product",
        "license_id",
        "licensee",
        "issued_at",
        "server_ids",
        "installation_id",
        "modules",
        "limits",
        "maintenance_until",
        "valid_until",
        "request_sha256",
    }
)
LIMIT_KEYS: Final = frozenset({"log_sources", "users"})
_DOCUMENT_KEYS: Final = frozenset({"payload", "signature"})
_SIGNATURE_KEYS: Final = frozenset({"alg", "key_id", "value"})


class PayloadError(ValueError):
    """A licence payload breaks a field rule (section 2.1)."""


@dataclass(frozen=True)
class LicensePayload:
    license_id: str
    product: str
    licensee: str
    issued_at: datetime
    server_ids: tuple[str, ...]
    installation_id: str | None
    modules: tuple[str, ...]  # as listed (incl. keys this version does not know)
    log_sources: int | None
    users: int | None
    maintenance_until: date
    valid_until: date | None
    request_sha256: str | None


@dataclass(frozen=True)
class LicenseDocument:
    payload: LicensePayload
    key_id: str
    sha256: str  # of the file bytes as given
    payload_sha256: str  # sha256(canonical_json(payload)), lower-case hex
    raw: bytes  # the file bytes as given


# -- strict JSON --------------------------------------------------------------------------------


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _refuse_number(text: str) -> Any:
    raise ValueError(f"number {text!r} is not allowed (no floats, NaN or Infinity)")


def parse_json_strict(text: str) -> Any:
    """``json.loads`` refusing duplicate keys, floats, ``NaN`` and ``Infinity``."""
    return json.loads(
        text,
        object_pairs_hook=_refuse_duplicates,
        parse_float=_refuse_number,
        parse_constant=_refuse_number,
    )


# -- payload rules ------------------------------------------------------------------------------


def _date(value: object, field: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise PayloadError(f"{field} must be a date YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PayloadError(f"{field} is not a valid date") from exc


def _limit(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise PayloadError(f"limits.{field} must be an integer 1-{MAX_LIMIT} or null")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PayloadError(f"{field} must be a list of strings")
    if len(set(value)) != len(value):
        raise PayloadError(f"{field} must not repeat an entry")
    return value


def validate_payload(payload: Mapping[str, Any]) -> LicensePayload:
    """The field rules of section 2.1; :class:`PayloadError` names the first broken rule."""
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise PayloadError("payload must have exactly the published keys")
    if payload["schema"] != LICENSE_SCHEMA:
        raise PayloadError(f"schema must be {LICENSE_SCHEMA!r}")
    if payload["product"] != PRODUCT:
        raise PayloadError(f"product must be {PRODUCT!r}")
    license_id = payload["license_id"]
    if not isinstance(license_id, str) or not LICENSE_ID_PATTERN.fullmatch(license_id):
        raise PayloadError("license_id must be ARCI-L-YYYYMMDD-XXXXXXXX")
    licensee = payload["licensee"]
    if not one_line_text(licensee, minimum=1, maximum=200):
        raise PayloadError("licensee must be one line of 1-200 characters")
    try:
        issued_at = parse_utc_seconds(payload["issued_at"])
    except ValueError as exc:
        raise PayloadError("issued_at must be YYYY-MM-DDTHH:MM:SSZ") from exc
    server_ids = _string_list(payload["server_ids"], "server_ids")
    if not 1 <= len(server_ids) <= MAX_SERVER_IDS or not all(
        SERVER_ID_PATTERN.fullmatch(item) for item in server_ids
    ):
        raise PayloadError(f"server_ids must hold 1-{MAX_SERVER_IDS} server ids")
    installation_id = payload["installation_id"]
    if installation_id is not None and (
        not isinstance(installation_id, str) or not UUID_PATTERN.fullmatch(installation_id)
    ):
        raise PayloadError("installation_id must be a lower-case UUID or null")
    modules = _string_list(payload["modules"], "modules")
    if not all(MODULE_KEY_PATTERN.fullmatch(item) for item in modules):
        raise PayloadError("modules must be module keys")
    if CORE_MODULE not in modules:
        raise PayloadError(f"modules must contain {CORE_MODULE}")
    limits = payload["limits"]
    if not isinstance(limits, Mapping) or set(limits) != LIMIT_KEYS:
        raise PayloadError("limits must have exactly log_sources and users")
    log_sources = _limit(limits["log_sources"], "log_sources")
    users = _limit(limits["users"], "users")
    maintenance_until = _date(payload["maintenance_until"], "maintenance_until")
    valid_until_raw = payload["valid_until"]
    valid_until = None if valid_until_raw is None else _date(valid_until_raw, "valid_until")
    request_hash = payload["request_sha256"]
    if request_hash is not None and (
        not isinstance(request_hash, str) or not _SHA256.fullmatch(request_hash)
    ):
        raise PayloadError("request_sha256 must be 64 lower-case hex digits or null")
    return LicensePayload(
        license_id=license_id,
        product=PRODUCT,
        licensee=licensee,
        issued_at=issued_at,
        server_ids=tuple(server_ids),
        installation_id=installation_id,
        modules=tuple(modules),
        log_sources=log_sources,
        users=users,
        maintenance_until=maintenance_until,
        valid_until=valid_until,
        request_sha256=request_hash,
    )


def payload_to_json(p: LicensePayload) -> dict[str, Any]:
    """The JSON payload of a parsed licence (what was signed, up to key order)."""
    return {
        "schema": LICENSE_SCHEMA,
        "product": p.product,
        "license_id": p.license_id,
        "licensee": p.licensee,
        "issued_at": format_utc_seconds(p.issued_at),
        "server_ids": list(p.server_ids),
        "installation_id": p.installation_id,
        "modules": list(p.modules),
        "limits": {"log_sources": p.log_sources, "users": p.users},
        "maintenance_until": p.maintenance_until.isoformat(),
        "valid_until": p.valid_until.isoformat() if p.valid_until is not None else None,
        "request_sha256": p.request_sha256,
    }


# -- verification -------------------------------------------------------------------------------


def _readable_id(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def load_license(data: bytes, *, keys: Mapping[str, str] | None = None) -> LicenseDocument:
    """Steps 1-6 of 2.1; keys=None reads keys.TRUSTED_KEYS at call time (keys= only for the
    issuer and tests). Raises LicenseError and nothing else."""
    # 1. size
    try:
        raw = bytes(data)
    except Exception as exc:
        raise LicenseError("license_malformed", reason=f"not bytes: {type(exc).__name__}") from exc
    if len(raw) > MAX_LICENSE_BYTES:
        raise LicenseError("license_too_large", reason=f"{len(raw)} bytes")

    # 2. decode, parse, shape, canonical form of the payload
    try:
        text = raw[len(_BOM) :] if raw.startswith(_BOM) else raw
        document = parse_json_strict(text.decode("utf-8"))
        if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
            raise ValueError("the file must have exactly payload and signature")
        signature = document["signature"]
        if not isinstance(signature, dict) or set(signature) != _SIGNATURE_KEYS:
            raise ValueError("signature must have exactly alg, key_id and value")
        if signature["alg"] != SIGNATURE_ALG:
            raise ValueError("signature alg must be Ed25519")
        key_id = _readable_id(signature["key_id"], KEY_ID_PATTERN)
        if key_id is None:
            raise ValueError("signature key_id has the wrong form")
        value = b64url_decode_strict(signature["value"], 64)
        payload = document["payload"]
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        message = canonical_json(payload)
    except Exception as exc:  # UnicodeDecodeError, ValueError, RecursionError, CanonicalJsonError
        raise LicenseError("license_malformed", reason=f"{type(exc).__name__}: {exc}") from exc
    license_id = _readable_id(payload.get("license_id"), LICENSE_ID_PATTERN)

    # 3. trusted key
    trusted = _keys.TRUSTED_KEYS if keys is None else keys
    encoded = trusted.get(key_id)
    if encoded is None:
        raise LicenseError(
            "license_unknown_key",
            {"key_id": key_id},
            details={"key_id": key_id},
            license_id=license_id,
            key_id=key_id,
        )
    try:
        public_key = Ed25519PublicKey.from_public_bytes(b64url_decode_strict(encoded, 32))
    except Exception as exc:
        raise LicenseError(
            "license_unknown_key",
            {"key_id": key_id},
            details={"key_id": key_id},
            reason=f"the trusted key {key_id} is not an Ed25519 public key",
            license_id=license_id,
            key_id=key_id,
        ) from exc

    # 4. signature
    try:
        public_key.verify(value, message)
    except InvalidSignature as exc:
        raise LicenseError(
            "license_signature_invalid", license_id=license_id, key_id=key_id
        ) from exc

    # 5. payload field rules
    try:
        parsed = validate_payload(payload)
    except PayloadError as exc:
        raise LicenseError(
            "license_malformed", reason=str(exc), license_id=license_id, key_id=key_id
        ) from exc

    # 6. revocation
    if parsed.license_id in _revoked.REVOKED_LICENSE_IDS:
        raise LicenseError(
            "license_revoked",
            {"license_id": parsed.license_id},
            details={"license_id": parsed.license_id},
            license_id=license_id,
            key_id=key_id,
        )
    return LicenseDocument(
        payload=parsed,
        key_id=key_id,
        sha256=hashlib.sha256(raw).hexdigest(),
        payload_sha256=hashlib.sha256(message).hexdigest(),
        raw=raw,
    )


# -- signing (issuer tool and tests) --------------------------------------------------------------


def _plain(value: Any) -> Any:
    """A JSON-native copy (mappings to dicts, sequences to lists)."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_plain(item) for item in value]
    return value


def sign_unchecked(
    payload: Mapping[str, Any], private_key: Ed25519PrivateKey, key_id: str
) -> bytes:
    """Sign any payload without the field rules (tests of step 5 need such files)."""
    plain = _plain(payload)
    signature = private_key.sign(canonical_json(plain))
    document = {
        "payload": plain,
        "signature": {"alg": SIGNATURE_ALG, "key_id": key_id, "value": b64url_encode(signature)},
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sign_payload(payload: Mapping[str, Any], private_key: Ed25519PrivateKey, key_id: str) -> bytes:
    """The complete .lic file bytes (issuer and tests). Validates the payload rules first."""
    plain = _plain(payload)
    validate_payload(plain)
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise PayloadError("key_id must match ^[a-z0-9][a-z0-9-]{2,31}$")
    try:
        canonical_json(plain)
    except CanonicalJsonError as exc:
        raise PayloadError(f"the payload has no canonical form: {exc}") from exc
    data = sign_unchecked(payload, private_key, key_id)
    if len(data) > MAX_LICENSE_BYTES:
        raise PayloadError(f"the licence file would exceed {MAX_LICENSE_BYTES} bytes")
    return data
