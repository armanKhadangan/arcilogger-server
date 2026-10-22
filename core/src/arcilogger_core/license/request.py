"""The request code: what an installation sends to the licensor (licensing contract 2.2).

``ARCIREQ1-`` + Crockford base32 of ``canonical_json(payload) + sha256(...)[:5]``, in groups of
five characters. It carries the server id, the installation id, the product version, the
organisation name, the platform and whether an SMBIOS UUID backs the server id, plus the UTC day
it was made (so the code stays the same for a whole day). It is not secret.

Copying through e-mail is forgiving: case, whitespace, line breaks and lost hyphens do not
matter, ``O`` reads as ``0`` and ``I``/``L`` as ``1``; a mistyped character is caught by the
40-bit checksum.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

import arcilogger_core
from arcilogger_core.canonical import canonical_json
from arcilogger_core.license.codec import CROCKFORD_ALPHABET, crockford_decode, crockford_encode
from arcilogger_core.license.codec import group as group_text
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.fingerprint import SERVER_ID_PATTERN, ServerIdentity, server_id_of
from arcilogger_core.timeutil import to_utc

REQUEST_SCHEMA: Final = "arcilogger.license-request/1"
REQUEST_PREFIX: Final = "ARCIREQ1"
CHECKSUM_BYTES: Final = 5
#: A real code has well under 2,000 characters (the organisation name is at most 200).
MAX_CODE_CHARACTERS: Final = 4096

UUID_PATTERN: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UTC_SECONDS: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}")
_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
_KEYS: Final = frozenset(
    {
        "schema",
        "server_id",
        "installation_id",
        "product_version",
        "organization",
        "platform",
        "smbios",
        "created_at",
    }
)
_ALIASES: Final = str.maketrans({"O": "0", "I": "1", "L": "1"})
#: Removed before decoding: whitespace, hyphens and the dashes word processors put instead.
_SEPARATORS: Final = re.compile("[\\s\\-\u00ad\u2010-\u2015]+")


def one_line_text(value: object, *, minimum: int, maximum: int) -> bool:
    """The ``site_name`` rule: one line, no control/format characters, no outer whitespace."""
    return (
        isinstance(value, str)
        and minimum <= len(value) <= maximum
        and value == value.strip()
        and not any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in value)
    )


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def format_utc_seconds(moment: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ``."""
    return to_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_seconds(text: object) -> datetime:
    """The aware UTC datetime of ``YYYY-MM-DDTHH:MM:SSZ``; ``ValueError`` otherwise."""
    if not isinstance(text, str) or not _UTC_SECONDS.fullmatch(text):
        raise ValueError(f"not a UTC time YYYY-MM-DDTHH:MM:SSZ: {text!r}")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class LicenseRequest:
    server_id: str
    installation_id: str
    product_version: str
    organization: str  # Config.site_name, may be ""
    platform: Literal["windows", "linux"]
    smbios: bool  # the server id is backed by an SMBIOS UUID (strong binding on Windows)
    created_at: datetime  # aware UTC, whole seconds (00:00:00 of the UTC day when made here)

    def __post_init__(self) -> None:
        values: dict[str, object] = dict(vars(self))
        created: object = values["created_at"]
        checks = {
            "server_id": _matches(values["server_id"], SERVER_ID_PATTERN),
            "installation_id": _matches(values["installation_id"], UUID_PATTERN),
            "product_version": _matches(values["product_version"], _VERSION),
            "organization": one_line_text(values["organization"], minimum=0, maximum=200),
            "platform": values["platform"] in ("windows", "linux"),
            "smbios": isinstance(values["smbios"], bool),
            "created_at": isinstance(created, datetime)
            and created.utcoffset() is not None
            and not created.microsecond,
        }
        problems = [name for name, ok in checks.items() if not ok]
        if problems:
            raise ValueError("invalid request fields: " + ", ".join(problems))

    @property
    def organization_set(self) -> bool:
        """False: "Kuruluş adı ayarlanmamış / organisation name not set" (a warning)."""
        return bool(self.organization)


def new_request(
    identity: ServerIdentity,
    *,
    installation_id: str,
    organization: str,
    now: datetime,
    product_version: str | None = None,
) -> LicenseRequest:
    """The request of this installation, made at ``now`` (kept to its UTC day)."""
    day = to_utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return LicenseRequest(
        server_id=server_id_of(identity),
        installation_id=installation_id,
        product_version=product_version or arcilogger_core.__version__,
        organization=organization,
        platform=identity.platform,
        smbios=identity.smbios_uuid != "",
        created_at=day,
    )


def request_payload(req: LicenseRequest) -> dict[str, Any]:
    return {
        "schema": REQUEST_SCHEMA,
        "server_id": req.server_id,
        "installation_id": req.installation_id,
        "product_version": req.product_version,
        "organization": req.organization,
        "platform": req.platform,
        "smbios": req.smbios,
        "created_at": format_utc_seconds(req.created_at),
    }


def request_body(req: LicenseRequest) -> bytes:
    """``canonical_json`` of the request payload: what the checksum and the licence's
    ``request_sha256`` cover."""
    return canonical_json(request_payload(req))


def request_sha256(req: LicenseRequest) -> str:
    """Lower-case hex SHA-256 of :func:`request_body` (the licence's ``request_sha256``)."""
    return hashlib.sha256(request_body(req)).hexdigest()


def make_request_code(req: LicenseRequest) -> str:
    """``ARCIREQ1-XXXXX-XXXXX-…`` (one line)."""
    body = request_body(req)
    text = crockford_encode(body + hashlib.sha256(body).digest()[:CHECKSUM_BYTES])
    return f"{REQUEST_PREFIX}-{group_text(text, 5)}"


def _malformed(reason: str) -> LicenseError:
    return LicenseError("request_malformed", reason=reason)


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _refuse_number(text: str) -> Any:
    raise ValueError(f"number {text!r} is not allowed")


def parse_request_code(text: str) -> LicenseRequest:
    """The request of a code, however an e-mail program reflowed it.

    ``LicenseError`` ``request_malformed`` (not a request code) or ``request_checksum``
    (incomplete or mistyped).
    """
    if not isinstance(text, str):
        raise _malformed("not text")
    compact = _SEPARATORS.sub("", text.strip()).upper()
    if not compact.startswith(REQUEST_PREFIX):
        raise _malformed(f"the code does not start with {REQUEST_PREFIX}")
    data_text = compact[len(REQUEST_PREFIX) :].translate(_ALIASES)
    if len(data_text) > MAX_CODE_CHARACTERS:
        raise _malformed("too long for a request code")
    if any(char not in CROCKFORD_ALPHABET for char in data_text):
        raise _malformed("character outside the Crockford base32 alphabet")
    try:
        data = crockford_decode(data_text)
    except ValueError as exc:
        raise _malformed(str(exc)) from exc
    if len(data) < CHECKSUM_BYTES + 1:
        raise _malformed("too short")
    body, check = data[:-CHECKSUM_BYTES], data[-CHECKSUM_BYTES:]
    if hashlib.sha256(body).digest()[:CHECKSUM_BYTES] != check:
        raise LicenseError("request_checksum")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_refuse_duplicates,
            parse_float=_refuse_number,
            parse_constant=_refuse_number,
        )
        if not isinstance(payload, dict) or set(payload) != _KEYS:
            raise ValueError("not exactly the request keys")
        if payload["schema"] != REQUEST_SCHEMA:
            raise ValueError("unknown request schema")
        if canonical_json(payload) != body:
            raise ValueError("the request body is not canonical JSON")
        request = LicenseRequest(
            server_id=payload["server_id"],
            installation_id=payload["installation_id"],
            product_version=payload["product_version"],
            organization=payload["organization"],
            platform=payload["platform"],
            smbios=payload["smbios"],
            created_at=parse_utc_seconds(payload["created_at"]),
        )
    except Exception as exc:  # UnicodeDecodeError, ValueError, RecursionError, CanonicalJsonError
        raise _malformed(f"{type(exc).__name__}: {exc}") from exc
    if request_body(request) != body:
        raise _malformed("the request body does not round-trip")
    return request
