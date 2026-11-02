"""Signed request files of the administration channel (ADR-0014; contract 2.4.3, 2.4.5).

An envelope is UTF-8 JSON ``{"v": 1, "key_id", "payload", "signature"}``. ``payload`` is the
base64url text of the RFC 8785 canonical JSON of the payload
(:func:`arcilogger_core.canonical.canonical_json`); ``signature`` is the base64url Ed25519
signature over the domain prefix followed by exactly those payload bytes. Two domains use it:

* administration requests (``inbox\\<id>.req``, at most 1 MiB): prefix
  ``b"arcilogger.admin-request/1\\n"``, signed with the API key;
* task requests (``state\\requests\\…\\<id>.json``, at most 64 KiB): prefix
  ``b"arcilogger.task-request/1\\n"``, signed with the agent key.

The prefix keeps a signature of one domain from being valid in the other, and each domain
accepts only keys of its signer's role, so the API key cannot sign a task request. The payload
states its ``schema`` too (equal to the prefix without the newline).

:func:`verify` runs these steps in order (the first failure wins) and parses nothing of the
payload before its signature is checked:

1. the size limit and the envelope's shape (strict JSON, exactly these keys) → ``schema_invalid``;
2. ``key_id`` names a key of the signer's role in keys.json → else ``signature_invalid``;
3. the signature over prefix + payload bytes → ``signature_invalid``;
4. the payload bytes are canonical JSON (no duplicate keys, no floats), every string at most
   4 KiB, ``schema`` of this domain → ``schema_invalid``.

:func:`open_admin_request` and :func:`open_task_request` continue with

5. the payload model (contract ``AdminRequest`` / ``TaskRequest``) → ``schema_invalid``;
6. ``instance`` equals keys.json's (administration requests) → ``installation_mismatch``;
7. ``issued_at`` at most 5 minutes ahead of the reader's clock → ``schema_invalid``; ``expires_at``
   passed → ``request_expired``.

Claiming the file before reading it and the id ledger (contract 2.5.2 steps 1 and 4, the
``seen`` folder of the tasks) are the reader's job: they need state this module does not keep.
Every refusal is an :class:`EnvelopeError` with a code of :mod:`arcilogger_core.admin.messages`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from arcilogger_core.admin.contract import (
    CLOCK_SKEW,
    MAX_REQUEST_BYTES,
    MAX_STRING_BYTES,
    MAX_TASK_REQUEST_BYTES,
    REQUEST_SCHEMA,
    TASK_REQUEST_SCHEMA,
    AdminRequest,
    DocumentError,
    KeyRole,
    KeysFile,
    TaskRequest,
    b64url_decode,
    b64url_encode,
    parse_document,
    parse_json_strict,
)
from arcilogger_core.admin.keys import SigningKey
from arcilogger_core.canonical import canonical_json
from arcilogger_core.timeutil import require_aware

ENVELOPE_VERSION: Final = 1
_ENVELOPE_KEYS: Final = frozenset({"v", "key_id", "payload", "signature"})
_SIGNATURE_BYTES: Final = 64


@dataclass(frozen=True)
class Domain:
    """What a signature is for: the payload ``schema``, the signer's key role, the size limit."""

    schema: str
    signer: KeyRole
    max_bytes: int

    @property
    def prefix(self) -> bytes:
        return self.schema.encode("ascii") + b"\n"


ADMIN_REQUEST: Final = Domain(REQUEST_SCHEMA, "api", MAX_REQUEST_BYTES)
TASK_REQUEST: Final = Domain(TASK_REQUEST_SCHEMA, "agent", MAX_TASK_REQUEST_BYTES)


class EnvelopeError(ValueError):
    """A request file is refused. ``code`` is the request's outcome code
    (``signature_invalid``, ``schema_invalid``, ``installation_mismatch``, ``request_expired``),
    ``reason`` a technical explanation for the log (never shown as the message), ``key_id`` the
    key the envelope names when it could be read."""

    def __init__(self, code: str, reason: str, *, key_id: str | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.key_id = key_id


@dataclass(frozen=True)
class Verified:
    """An envelope whose signature verified."""

    key_id: str
    #: The canonical payload bytes that were signed.
    payload: bytes
    #: The parsed payload (checked only for its ``schema`` so far).
    document: dict[str, Any]


def _check_strings(value: Any) -> None:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError(f"a string is longer than {MAX_STRING_BYTES} bytes")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_strings(key)
            _check_strings(item)
    elif isinstance(value, list):
        for item in value:
            _check_strings(item)


def sign_payload(payload: bytes, key: SigningKey, domain: Domain) -> bytes:
    """The envelope bytes for ``payload`` exactly as given. Producers use
    :func:`sign_admin_request` / :func:`sign_task_request`; this one exists for them and for
    tests that need a signed payload the readers must refuse."""
    if key.role != domain.signer:
        raise ValueError(f"a {domain.schema} is signed with the {domain.signer} key")
    envelope = {
        "v": ENVELOPE_VERSION,
        "key_id": key.key_id,
        "payload": b64url_encode(payload),
        "signature": b64url_encode(key.private_key.sign(domain.prefix + payload)),
    }
    data = canonical_json(envelope) + b"\n"
    if len(data) > domain.max_bytes:
        raise ValueError(f"the envelope would exceed {domain.max_bytes} bytes")
    return data


def sign_admin_request(request: AdminRequest, key: SigningKey) -> bytes:
    """The ``inbox\\<id>.req`` bytes of ``request`` (the API)."""
    return _sign_model(request.model_dump(mode="json"), key, ADMIN_REQUEST)


def sign_task_request(request: TaskRequest, key: SigningKey) -> bytes:
    """The ``state\\requests\\<queue>\\<id>.json`` bytes of ``request`` (the agent)."""
    return _sign_model(request.model_dump(mode="json"), key, TASK_REQUEST)


def _sign_model(document: dict[str, Any], key: SigningKey, domain: Domain) -> bytes:
    _check_strings(document)
    return sign_payload(canonical_json(document), key, domain)


def verify(data: bytes, keys: Mapping[str, Ed25519PublicKey], domain: Domain) -> Verified:
    """Steps 1-4 of the module docstring. ``keys``: key id -> public key of the domain's signer
    role (``KeysFile.public_keys``)."""
    # 1. size and envelope shape
    if len(data) > domain.max_bytes:
        raise EnvelopeError("schema_invalid", f"{len(data)} bytes, at most {domain.max_bytes}")
    try:
        envelope = parse_json_strict(data)
        if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_KEYS:
            raise ValueError("an envelope has exactly v, key_id, payload and signature")
        version = envelope["v"]
        if isinstance(version, bool) or version != ENVELOPE_VERSION:
            raise ValueError(f"envelope version {version!r} is not {ENVELOPE_VERSION}")
        key_id = envelope["key_id"]
        if not isinstance(key_id, str) or len(key_id) > 64:
            raise ValueError("key_id must be a short string")
        payload = b64url_decode(envelope["payload"])
        signature = b64url_decode(envelope["signature"], _SIGNATURE_BYTES)
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError, JSONDecodeError
        raise EnvelopeError("schema_invalid", f"not an envelope: {exc}") from exc

    # 2. a key of the signer's role
    public_key = keys.get(key_id)
    if public_key is None:
        raise EnvelopeError(
            "signature_invalid", f"no {domain.signer} key {key_id!r} in keys.json", key_id=key_id
        )

    # 3. the signature over the domain prefix and the payload bytes
    try:
        public_key.verify(signature, domain.prefix + payload)
    except InvalidSignature as exc:
        raise EnvelopeError(
            "signature_invalid", "the signature does not verify", key_id=key_id
        ) from exc

    # 4. the signed bytes: canonical JSON of this domain
    try:
        document = parse_json_strict(payload)
        if canonical_json(document) != payload:
            raise ValueError("the payload is not canonical JSON (RFC 8785)")
        if not isinstance(document, dict) or document.get("schema") != domain.schema:
            raise ValueError(f"the payload is not a {domain.schema}")
        _check_strings(document)
    except (ValueError, TypeError, RecursionError) as exc:  # CanonicalJsonError is a TypeError
        raise EnvelopeError("schema_invalid", str(exc), key_id=key_id) from exc
    return Verified(key_id=key_id, payload=payload, document=document)


def _check_times(issued_at: datetime, expires_at: datetime, now: datetime, key_id: str) -> None:
    if issued_at > require_aware(now) + CLOCK_SKEW:
        raise EnvelopeError(
            "schema_invalid", f"issued_at {issued_at.isoformat()} lies in the future", key_id=key_id
        )
    if now >= expires_at:
        raise EnvelopeError(
            "request_expired", f"expired at {expires_at.isoformat()}", key_id=key_id
        )


def open_admin_request(data: bytes, keys: KeysFile, *, now: datetime) -> AdminRequest:
    """An administration request file after steps 1-7 (the agent, after claiming it)."""
    verified = verify(data, keys.public_keys(ADMIN_REQUEST.signer), ADMIN_REQUEST)
    try:
        request = parse_document(AdminRequest, verified.payload)
    except DocumentError as exc:
        raise EnvelopeError("schema_invalid", str(exc), key_id=verified.key_id) from exc
    if request.instance != keys.instance:
        raise EnvelopeError(
            "installation_mismatch",
            f"made for installation {request.instance}, this is {keys.instance}",
            key_id=verified.key_id,
        )
    _check_times(request.issued_at, request.expires_at, now, verified.key_id)
    return request


def open_task_request(data: bytes, keys: KeysFile, *, now: datetime) -> TaskRequest:
    """A task request file after steps 1-5 and 7 (the Seal and Requests tasks)."""
    verified = verify(data, keys.public_keys(TASK_REQUEST.signer), TASK_REQUEST)
    try:
        request = parse_document(TaskRequest, verified.payload)
    except DocumentError as exc:
        raise EnvelopeError("schema_invalid", str(exc), key_id=verified.key_id) from exc
    _check_times(request.issued_at, request.expires_at, now, verified.key_id)
    return request
