"""Test helpers of the licensing package (licensing contract 5.3).

**Never shipped**: ``core/pyproject.toml`` excludes this file from the wheel, so a production
installation cannot import it and :func:`arcilogger_core.license.state.status_override` stays
inert there. Imported only by tests, ``web/scripts/capture_api_samples.py`` and the issuer's
tests.

The test key is deterministic (stable samples) and its key id starts with ``test-``, which the
production key set never contains.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import arcilogger_core
from arcilogger_core.license import fingerprint, keys
from arcilogger_core.license._constants import PRODUCT, release_date
from arcilogger_core.license.codec import b64url_encode
from arcilogger_core.license.document import LICENSE_SCHEMA, sign_unchecked
from arcilogger_core.license.fingerprint import ServerIdentity, server_id_of
from arcilogger_core.license.state import (
    Installation,
    LicenseInputs,
    LicenseStatus,
    evaluate,
)
from arcilogger_core.license.trusted_time import TrustedTime, site_day_start
from arcilogger_core.timeutil import to_utc, utc_now

TEST_KEY_ID: Final = "test-key-1"
TEST_INSTALLATION_ID: Final = "00000000-0000-4000-8000-000000000001"
DEFAULT_MACHINE_ID: Final = "0f8fad5b-d9cb-469f-a165-70867728950e"
DEFAULT_SMBIOS_UUID: Final = "6F9619FF-8B86-D011-B42D-00C04FC964FF"

#: The status :func:`override_status` set (read by ``state.status_override``).
_override: LicenseStatus | None = None


# The published name starts with "test_": not a pytest test (``__test__`` below).
def test_private_key(seed: bytes = b"arcilogger test key 1") -> Ed25519PrivateKey:  # noqa: PT028
    """Ed25519PrivateKey.from_private_bytes(sha256(seed).digest()): deterministic."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest())


test_private_key.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    """The raw public key as base64url without padding (the form of ``keys.TRUSTED_KEYS``)."""
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return b64url_encode(raw)


@contextmanager
def trusted_test_keys(*pairs: tuple[str, Ed25519PrivateKey]) -> Iterator[None]:
    """Swap ``keys.TRUSTED_KEYS`` (default: the test key) and restore it afterwards."""
    chosen = pairs or ((TEST_KEY_ID, test_private_key()),)
    previous = keys.TRUSTED_KEYS
    keys.TRUSTED_KEYS = MappingProxyType(  # type: ignore[misc]  # tests swap the Final set
        {key_id: public_key_b64(private_key) for key_id, private_key in chosen}
    )
    try:
        yield
    finally:
        keys.TRUSTED_KEYS = previous  # type: ignore[misc]


@contextmanager
def fixed_identity(
    machine_id: str = DEFAULT_MACHINE_ID,
    smbios_uuid: str = DEFAULT_SMBIOS_UUID,
    platform: str = "windows",
) -> Iterator[str]:
    """Swap the identity provider; yields the server id (default ``XW68-7K1Z-5FGV-AGT8``)."""
    if platform not in ("windows", "linux"):
        raise ValueError(f"unknown platform {platform!r}")
    identity = ServerIdentity(
        platform="windows" if platform == "windows" else "linux",
        machine_id=machine_id,
        smbios_uuid=smbios_uuid,
    )
    previous = fingerprint.IDENTITY_PROVIDER
    fingerprint.IDENTITY_PROVIDER = lambda: identity
    fingerprint.clear_cache()
    try:
        yield server_id_of(identity)
    finally:
        fingerprint.IDENTITY_PROVIDER = previous
        fingerprint.clear_cache()


def license_payload(
    *,
    server_ids: Sequence[str],
    modules: Sequence[str] = ("core_5651",),
    licensee: str = "Örnek A.Ş.",
    log_sources: int | None = None,
    users: int | None = None,
    maintenance_until: date = date(2099, 12, 31),
    valid_until: date | None = None,
    issued_at: datetime = datetime(2026, 10, 1, tzinfo=UTC),
    license_id: str = "ARCI-L-20261001-TEST0001",
    installation_id: str | None = None,
    request_sha256: str | None = None,
) -> dict[str, Any]:
    """The payload :func:`make_license` signs (a plain dict, for tests that change it)."""
    return {
        "schema": LICENSE_SCHEMA,
        "product": PRODUCT,
        "license_id": license_id,
        "licensee": licensee,
        "issued_at": to_utc(issued_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server_ids": list(server_ids),
        "installation_id": installation_id,
        "modules": list(modules),
        "limits": {"log_sources": log_sources, "users": users},
        "maintenance_until": maintenance_until.isoformat(),
        "valid_until": valid_until.isoformat() if valid_until is not None else None,
        "request_sha256": request_sha256,
    }


def sign_raw(
    payload: Mapping[str, Any],
    *,
    key_id: str = TEST_KEY_ID,
    private_key: Ed25519PrivateKey | None = None,  # gitleaks:allow (a type name, no key)
) -> bytes:
    """Sign any payload, even one breaking the field rules (step 5 tests)."""
    return sign_unchecked(payload, private_key or test_private_key(), key_id)


def make_license(
    *,
    server_ids: Sequence[str],
    modules: Sequence[str] = ("core_5651",),
    licensee: str = "Örnek A.Ş.",
    log_sources: int | None = None,
    users: int | None = None,
    maintenance_until: date = date(2099, 12, 31),
    valid_until: date | None = None,
    issued_at: datetime = datetime(2026, 10, 1, tzinfo=UTC),
    license_id: str = "ARCI-L-20261001-TEST0001",
    installation_id: str | None = None,
    key_id: str = TEST_KEY_ID,
    private_key: Ed25519PrivateKey | None = None,  # gitleaks:allow (a type name, no key)
    extra: Mapping[str, Any] | None = None,
) -> bytes:
    """A whole ``.lic`` file signed with the test key (or ``private_key``); ``extra`` adds or
    replaces payload fields (signed as given, without the field rules)."""
    payload = license_payload(
        server_ids=server_ids,
        modules=modules,
        licensee=licensee,
        log_sources=log_sources,
        users=users,
        maintenance_until=maintenance_until,
        valid_until=valid_until,
        issued_at=issued_at,
        license_id=license_id,
        installation_id=installation_id,
    )
    payload.update(extra or {})
    return sign_raw(payload, key_id=key_id, private_key=private_key)


def _step(container: Any, part: str) -> Any:
    return container[int(part)] if isinstance(container, list) else container[part]


def tamper(data: bytes, path: str, value: Any) -> bytes:
    """Change one payload field and keep the signature (``path``: ``licensee``,
    ``limits.users``, ``server_ids.0``, …; ``signature.key_id`` etc. address the signature)."""
    document = json.loads(data.decode("utf-8-sig"))
    parts = path.split(".")
    container = document if parts[0] == "signature" else document["payload"]
    for part in parts[:-1]:
        container = _step(container, part)
    last = parts[-1]
    if isinstance(container, list):
        container[int(last)] = value
    else:
        container[last] = value
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@contextmanager
def override_status(status: LicenseStatus | None) -> Iterator[None]:
    """``load_status`` returns ``status`` while active (None: real evaluation)."""
    global _override
    previous = _override
    _override = status
    try:
        yield
    finally:
        _override = previous


def _synthetic(moment: datetime, trial_started_at: datetime) -> LicenseStatus:
    trusted = TrustedTime(
        now=moment,
        system=moment,
        tsa=None,
        tsa_day=None,
        vault_day_end=None,
        release_floor=min(moment, site_day_start(release_date())),
        license_issued=None,
    )
    return evaluate(
        LicenseInputs(
            installation=Installation(TEST_INSTALLATION_ID, trial_started_at),
            stored=None,
            server_id=server_id_of(
                ServerIdentity("windows", DEFAULT_MACHINE_ID, DEFAULT_SMBIOS_UUID)
            ),
            server_id_error=None,
            trusted=trusted,
            lower_bounds={},
            users_used=0,
            log_sources_used=0,
            log_sources_unconfigured=0,
            version=arcilogger_core.__version__,
            release_date=release_date(),
        )
    )


def all_modules_status(*, now: datetime | None = None) -> LicenseStatus:
    """State ``trial``: every module on, no limits."""
    moment = to_utc(now) if now is not None else utc_now()
    status = _synthetic(moment, moment)
    assert status.state == "trial"  # noqa: S101 - a helper bug, not user input
    return status


def base_only_status(*, now: datetime | None = None) -> LicenseStatus:
    """State ``trial_expired``: ``core_5651`` only, users max ``UNLICENSED_USERS`` (3.2)."""
    moment = to_utc(now) if now is not None else utc_now()
    status = _synthetic(moment, moment - timedelta(days=30))
    assert status.state == "trial_expired"  # noqa: S101 - a helper bug, not user input
    return status
