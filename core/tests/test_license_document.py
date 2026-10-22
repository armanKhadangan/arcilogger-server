"""Licence files: round trip, tampering, wrong keys, strict parsing, revocation (contract 2.1).

Test ids of the licensing contract: A-T1, A-T2, A-T4, A-T5, A-T5b, A-T6.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arcilogger_core.license import revoked
from arcilogger_core.license.codec import b64url_decode_strict, b64url_encode
from arcilogger_core.license.document import (
    MAX_LICENSE_BYTES,
    PayloadError,
    load_license,
    payload_to_json,
    sign_payload,
)
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.testing import (
    TEST_KEY_ID,
    license_payload,
    make_license,
    public_key_b64,
    sign_raw,
    tamper,
    test_private_key,
    trusted_test_keys,
)

pytestmark = pytest.mark.license_live

SERVER = "XW68-7K1Z-5FGV-AGT8"
OTHER_SERVER = "0000-1111-2222-3333"
INSTALLATION = "3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4"
REQUEST_HASH = "9f" * 32


@pytest.fixture(autouse=True)
def _test_key() -> Iterator[None]:
    with trusted_test_keys():
        yield


def full_payload(**changes: Any) -> dict[str, Any]:
    """A payload with every field set (nothing null), two server ids and two modules."""
    payload = license_payload(
        server_ids=[SERVER, OTHER_SERVER],
        modules=["core_5651", "usage_analytics"],
        licensee="Örnek A.Ş.",
        log_sources=10,
        users=25,
        maintenance_until=date(2027, 10, 20),
        valid_until=date(2030, 1, 1),
        issued_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
        license_id="ARCI-L-20261020-7Q3KX9MD",
        installation_id=INSTALLATION,
        request_sha256=REQUEST_HASH,
    )
    payload.update(changes)
    return payload


def full_license(**changes: Any) -> bytes:
    return sign_raw(full_payload(**changes))


def code_of(data: bytes, **kwargs: Any) -> str:
    with pytest.raises(LicenseError) as caught:
        load_license(data, **kwargs)
    return caught.value.code


# -- A-T1 round trip -----------------------------------------------------------------------------


def test_round_trip_keeps_the_payload_and_the_payload_hash() -> None:
    data = full_license()
    document = load_license(data)
    assert payload_to_json(document.payload) == json.loads(data)["payload"] == full_payload()
    assert document.key_id == TEST_KEY_ID
    assert document.raw == data
    assert document.sha256 == hashlib.sha256(data).hexdigest()
    assert document.payload.server_ids == (SERVER, OTHER_SERVER)
    assert document.payload.log_sources == 10
    assert document.payload.users == 25
    assert document.payload.issued_at == datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert document.payload.maintenance_until == date(2027, 10, 20)

    reformatted = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":")).encode()
    copy = load_license(reformatted)
    assert copy.payload == document.payload
    assert copy.payload_sha256 == document.payload_sha256
    assert copy.sha256 != document.sha256


def test_the_issuer_format_is_the_published_one() -> None:
    data = full_license()
    assert data == (json.dumps(json.loads(data), ensure_ascii=False, indent=2) + "\n").encode()
    document = json.loads(data)
    assert set(document) == {"payload", "signature"}
    assert set(document["signature"]) == {"alg", "key_id", "value"}
    assert document["signature"]["alg"] == "Ed25519"
    assert len(document["signature"]["value"]) == 86
    assert "Örnek" in data.decode("utf-8")  # ensure_ascii=False


def test_sign_payload_validates_and_signs_the_same_bytes_as_the_test_helper() -> None:
    payload = full_payload()
    assert sign_payload(payload, test_private_key(), TEST_KEY_ID) == full_license()
    with pytest.raises(PayloadError):
        sign_payload(full_payload(modules=["usage_analytics"]), test_private_key(), TEST_KEY_ID)
    with pytest.raises(PayloadError):
        sign_payload(payload, test_private_key(), "Bad Key")
    with pytest.raises(PayloadError):
        sign_payload(full_payload(licensee="\ud800x"), test_private_key(), TEST_KEY_ID)
    many = ["core_5651", *(f"module_{n:05d}_{'x' * 28}" for n in range(1800))]
    with pytest.raises(PayloadError, match="65536"):
        sign_payload(full_payload(modules=many), test_private_key(), TEST_KEY_ID)


def test_explicit_keys_and_the_empty_production_set() -> None:
    data = full_license()
    public = public_key_b64(test_private_key())
    assert load_license(data, keys={TEST_KEY_ID: public}).payload.license_id
    assert code_of(data, keys={}) == "license_unknown_key"


@pytest.mark.parametrize("value", ["not-a-key", "A" * 43 + "=", b64url_encode(b"\x00" * 31)])
def test_a_broken_trusted_key_value_trusts_nothing(value: str) -> None:
    assert code_of(full_license(), keys={TEST_KEY_ID: value}) == "license_unknown_key"


def test_the_production_key_set_trusts_no_test_licence() -> None:
    with trusted_test_keys(("arci-2026a", test_private_key(b"unrelated"))):
        with pytest.raises(LicenseError) as caught:
            load_license(full_license())
        error = caught.value
    assert error.code == "license_unknown_key"
    assert error.key_id == TEST_KEY_ID
    assert error.license_id == "ARCI-L-20261020-7Q3KX9MD"
    assert error.details == {"key_id": TEST_KEY_ID}
    assert TEST_KEY_ID in error.message_tr
    assert TEST_KEY_ID in error.message_en


def test_unknown_module_keys_are_kept() -> None:
    document = load_license(full_license(modules=["core_5651", "rule_engine"]))
    assert document.payload.modules == ("core_5651", "rule_engine")


# -- A-T2 tampering ------------------------------------------------------------------------------

TAMPERING = [
    ("schema", "arcilogger.license/2"),
    ("product", "ARCILOGGER-PRO"),
    ("license_id", "ARCI-L-20261020-7Q3KX9ME"),
    ("licensee", "Başka A.Ş."),
    ("issued_at", "2026-10-21T12:00:00Z"),
    ("server_ids", [SERVER]),
    ("server_ids", [SERVER, OTHER_SERVER, "AAAA-BBBB-CCCC-DDDD"]),
    ("server_ids.0", "AAAA-BBBB-CCCC-DDDD"),
    ("server_ids.1", "AAAA-BBBB-CCCC-DDDD"),
    ("installation_id", "00000000-0000-4000-8000-000000000000"),
    ("installation_id", None),
    ("modules", ["core_5651", "usage_analytics", "usage_reports"]),
    ("modules.0", "bulk_import"),
    ("modules.1", "usage_reports"),
    ("limits", {"log_sources": None, "users": None}),
    ("limits.log_sources", 11),
    ("limits.log_sources", None),
    ("limits.users", 26),
    ("limits.users", None),
    ("maintenance_until", "2099-12-31"),
    ("valid_until", None),
    ("valid_until", "2030-01-02"),
    ("request_sha256", "0" * 64),
    ("request_sha256", None),
]


@pytest.mark.parametrize(("path", "value"), TAMPERING, ids=[t[0] for t in TAMPERING])
def test_tampering_any_payload_field_breaks_the_signature(path: str, value: Any) -> None:
    data = full_license()
    tampered = tamper(data, path, value)
    assert tampered != data
    assert code_of(tampered) == "license_signature_invalid"


def test_every_payload_field_is_covered_by_the_tampering_table() -> None:
    covered = {path.split(".")[0] for path, _ in TAMPERING}
    assert covered == set(full_payload())


def test_a_changed_signature_byte_is_invalid() -> None:
    data = full_license()
    value = json.loads(data)["signature"]["value"]
    raw = bytearray(b64url_decode_strict(value, 64))
    for index in (0, 31, 63):
        changed = bytearray(raw)
        changed[index] ^= 0x01
        tampered = tamper(data, "signature.value", b64url_encode(bytes(changed)))
        assert code_of(tampered) == "license_signature_invalid"


def test_another_trusted_key_id_or_an_unknown_key_id() -> None:
    data = full_license()
    second = ("test-key-2", test_private_key(b"arcilogger test key 2"))
    with trusted_test_keys((TEST_KEY_ID, test_private_key()), second):
        assert load_license(data).key_id == TEST_KEY_ID
        assert code_of(tamper(data, "signature.key_id", "test-key-2")) == (
            "license_signature_invalid"
        )
        with pytest.raises(LicenseError) as caught:
            load_license(tamper(data, "signature.key_id", "test-key-9"))
        error = caught.value
    assert error.code == "license_unknown_key"
    assert "test-key-9" in error.message_tr


# -- A-T4 wrong key ------------------------------------------------------------------------------


def test_same_key_id_signed_by_another_private_key() -> None:
    forged = sign_raw(full_payload(), private_key=test_private_key(b"attacker"))
    assert code_of(forged) == "license_signature_invalid"


# -- A-T5 malformed ------------------------------------------------------------------------------


def _text(data: bytes) -> str:
    return data.decode("utf-8")


def _signature_value(data: bytes) -> str:
    value: str = json.loads(data)["signature"]["value"]
    return value


def test_bom_is_accepted() -> None:
    data = full_license()
    document = load_license(b"\xef\xbb\xbf" + data)
    assert document.payload == load_license(data).payload
    assert document.sha256 == hashlib.sha256(b"\xef\xbb\xbf" + data).hexdigest()


def test_too_large_comes_first() -> None:
    data = full_license()
    padded = data + b" " * (MAX_LICENSE_BYTES - len(data))
    assert load_license(padded).payload.license_id  # exactly the limit is fine
    assert code_of(padded + b" ") == "license_too_large"
    assert code_of(b"\xff" * (MAX_LICENSE_BYTES + 1)) == "license_too_large"


def _replace(data: bytes, old: str, new: str) -> bytes:
    text = _text(data)
    assert old in text
    return text.replace(old, new, 1).encode("utf-8")


def unsigned_variants() -> list[tuple[str, bytes]]:
    data = full_license()
    value = _signature_value(data)
    standard = base64.b64encode(b64url_decode_strict(value, 64)).decode()
    return [
        ("not utf-8", data.replace("Örnek".encode(), "Örnek".encode("latin-1"))),
        ("not json", b"not json"),
        ("empty", b""),
        ("top-level array", b"[]"),
        ("duplicate key", _replace(data, '"licensee":', '"licensee": "X",\n    "licensee":')),
        (
            "duplicate top-level key",
            _replace(data, '"signature":', '"payload": {},\n  "signature":'),
        ),
        ("float", _replace(data, '"users": 25', '"users": 25.0')),
        ("exponent", _replace(data, '"users": 25', '"users": 25e0')),
        ("nan", _replace(data, '"users": 25', '"users": NaN')),
        ("infinity", _replace(data, '"users": 25', '"users": Infinity')),
        ("padded base64", _replace(data, value, value + "==")),
        ("standard base64", _replace(data, value, standard)),
        ("plus sign", _replace(data, value, value[:10] + "+" + value[11:])),
        ("slash", _replace(data, value, value[:10] + "/" + value[11:])),
        ("whitespace in value", _replace(data, value, value[:10] + " " + value[10:-1])),
        ("85 characters", _replace(data, value, value[:-1])),
        ("87 characters", _replace(data, value, value + "A")),
        ("non-zero trailing bits", _replace(data, value, value[:-1] + "B")),
        ("extra top-level key", _replace(data, '"signature":', '"extra": 1,\n  "signature":')),
        ("extra signature key", _replace(data, '"alg":', '"extra": 1,\n    "alg":')),
        ("wrong alg", _replace(data, '"alg": "Ed25519"', '"alg": "EdDSA"')),
        ("alg not a string", _replace(data, '"alg": "Ed25519"', '"alg": 1')),
        (
            "key id with upper case",
            _replace(data, f'"key_id": "{TEST_KEY_ID}"', '"key_id": "Test"'),
        ),
        ("key id not a string", _replace(data, f'"key_id": "{TEST_KEY_ID}"', '"key_id": 1')),
        ("value not a string", _replace(data, f'"value": "{value}"', '"value": 1')),
        (
            "payload not an object",
            json.dumps({"payload": [], "signature": json.loads(data)["signature"]}).encode(),
        ),
        ("signature not an object", json.dumps({"payload": {}, "signature": []}).encode()),
        ("lone surrogate", _replace(data, '"licensee": "Örnek A.Ş."', '"licensee": "\\ud800"')),
        ("2**60", _replace(data, '"users": 25', f'"users": {2**60}')),
        ("5000 digits", _replace(data, '"users": 25', '"users": ' + "9" * 5000)),
        ("30000 nested arrays", b"[" * 30_000 + b"]" * 30_000),
        ("nested arrays in payload", _replace(data, '"users": 25', '"users": ' + "[" * 30_000)),
    ]


UNSIGNED = unsigned_variants()


@pytest.mark.parametrize(("name", "data"), UNSIGNED, ids=[name for name, _ in UNSIGNED])
def test_malformed_files_are_license_malformed(name: str, data: bytes) -> None:
    assert code_of(data) == "license_malformed", name


def signed_variants() -> list[tuple[str, dict[str, Any]]]:
    base = full_payload()
    missing = dict(base)
    del missing["request_sha256"]
    limits_extra = {"log_sources": 1, "users": 1, "devices": 1}
    return [
        ("extra payload key", {**base, "extra": 1}),
        ("missing payload key", missing),
        ("schema", {**base, "schema": "arcilogger.license/2"}),
        ("product", {**base, "product": "arcilogger"}),
        ("license id lower case", {**base, "license_id": "arci-l-20261020-7q3kx9md"}),
        ("license id with U", {**base, "license_id": "ARCI-L-20261020-7Q3KX9MU"}),
        ("license id short", {**base, "license_id": "ARCI-L-2026102-7Q3KX9MD"}),
        ("licensee empty", {**base, "licensee": ""}),
        ("licensee 201 chars", {**base, "licensee": "A" * 201}),
        ("licensee leading space", {**base, "licensee": " Örnek"}),
        ("licensee trailing space", {**base, "licensee": "Örnek "}),
        ("licensee line break", {**base, "licensee": "Örnek\nA.Ş."}),
        ("licensee zero width space", {**base, "licensee": "Örnek\u200bA.Ş."}),
        ("licensee line separator", {**base, "licensee": "Örnek\u2028A.Ş."}),
        ("licensee not a string", {**base, "licensee": 1}),
        ("issued_at with offset", {**base, "issued_at": "2026-10-20T12:00:00+00:00"}),
        ("issued_at fraction", {**base, "issued_at": "2026-10-20T12:00:00.5Z"}),
        ("issued_at invalid", {**base, "issued_at": "2026-13-20T12:00:00Z"}),
        ("issued_at date only", {**base, "issued_at": "2026-10-20"}),
        ("server ids empty", {**base, "server_ids": []}),
        ("server ids 17", {**base, "server_ids": [f"AAAA-BBBB-CCCC-{n:04d}" for n in range(17)]}),
        ("server ids repeated", {**base, "server_ids": [SERVER, SERVER]}),
        ("server id lower case", {**base, "server_ids": [SERVER.lower()]}),
        ("server id with U", {**base, "server_ids": ["XW68-7K1Z-5FGV-AGTU"]}),
        ("server id without hyphens", {**base, "server_ids": [SERVER.replace("-", "")]}),
        ("server ids not a list", {**base, "server_ids": SERVER}),
        ("installation id upper case", {**base, "installation_id": INSTALLATION.upper()}),
        ("installation id braces", {**base, "installation_id": "{" + INSTALLATION + "}"}),
        ("modules without core", {**base, "modules": ["usage_analytics"]}),
        ("modules repeated", {**base, "modules": ["core_5651", "core_5651"]}),
        ("module key upper case", {**base, "modules": ["core_5651", "Usage"]}),
        ("module key too short", {**base, "modules": ["core_5651", "u"]}),
        ("modules not a list", {**base, "modules": "core_5651"}),
        ("limits missing key", {**base, "limits": {"users": 1}}),
        ("limits extra key", {**base, "limits": limits_extra}),
        ("limit zero", {**base, "limits": {"log_sources": 0, "users": 1}}),
        ("limit negative", {**base, "limits": {"log_sources": -1, "users": 1}}),
        ("limit too big", {**base, "limits": {"log_sources": 100_001, "users": 1}}),
        ("limit as text", {**base, "limits": {"log_sources": "5", "users": 1}}),
        ("bool as int", {**base, "limits": {"log_sources": True, "users": 1}}),
        ("bool users", {**base, "limits": {"log_sources": 1, "users": False}}),
        ("limits not an object", {**base, "limits": None}),
        ("maintenance invalid day", {**base, "maintenance_until": "2027-02-30"}),
        ("maintenance compact", {**base, "maintenance_until": "20271020"}),
        ("maintenance null", {**base, "maintenance_until": None}),
        ("valid_until short", {**base, "valid_until": "2027-1-1"}),
        ("valid_until datetime", {**base, "valid_until": "2027-01-01T00:00:00Z"}),
        ("request hash upper case", {**base, "request_sha256": REQUEST_HASH.upper()}),
        ("request hash short", {**base, "request_sha256": "ab"}),
    ]


SIGNED = signed_variants()


@pytest.mark.parametrize(("name", "payload"), SIGNED, ids=[name for name, _ in SIGNED])
def test_correctly_signed_wrong_content_is_license_malformed(
    name: str, payload: dict[str, Any]
) -> None:
    with pytest.raises(LicenseError) as caught:
        load_license(sign_raw(payload))
    error = caught.value
    assert error.code == "license_malformed", name
    assert error.reason


def test_limits_bounds_are_inclusive() -> None:
    document = load_license(full_license(limits={"log_sources": 1, "users": 100_000}))
    assert (document.payload.log_sources, document.payload.users) == (1, 100_000)
    document = load_license(full_license(licensee="A" * 200, installation_id=None))
    assert document.payload.installation_id is None


def test_server_ids_up_to_sixteen() -> None:
    ids = [f"AAAA-BBBB-CCCC-{n:04d}" for n in range(16)]
    assert load_license(full_license(server_ids=ids)).payload.server_ids == tuple(ids)


_FIXTURES = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


@settings(max_examples=300, deadline=None, suppress_health_check=_FIXTURES)
@given(st.binary(max_size=2048))
def test_arbitrary_bytes_raise_only_license_errors(data: bytes) -> None:
    with pytest.raises(LicenseError):
        load_license(data)


@settings(max_examples=300, deadline=None, suppress_health_check=_FIXTURES)
@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=0, max_value=255))
def test_any_changed_byte_is_refused_or_harmless(position: int, byte: int) -> None:
    data = full_license()
    index = position % len(data)
    changed = data[:index] + bytes([byte]) + data[index + 1 :]
    try:
        document = load_license(changed)
    except LicenseError:
        return
    # Accepted only when the change is outside the signed meaning (whitespace, BOM-free form).
    assert document.payload == load_license(data).payload


def test_load_license_never_raises_another_exception_type() -> None:
    for value in ("text", None, 12, object()):
        with pytest.raises(LicenseError):
            load_license(value)  # type: ignore[arg-type]


# -- A-T5b revocation ----------------------------------------------------------------------------


def test_a_revoked_licence_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    data = full_license()
    monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({"ARCI-L-20261020-7Q3KX9MD"}))
    with pytest.raises(LicenseError) as caught:
        load_license(data)
    error = caught.value
    assert error.code == "license_revoked"
    assert "ARCI-L-20261020-7Q3KX9MD" in error.message_tr
    assert "ARCI-L-20261020-7Q3KX9MD" in error.message_en
    assert error.message_tr == "Bu lisans iptal edilmiş (ARCI-L-20261020-7Q3KX9MD)."
    assert error.message_en == "This licence has been revoked (ARCI-L-20261020-7Q3KX9MD)."
    assert error.details == {"license_id": "ARCI-L-20261020-7Q3KX9MD"}
    assert error.http_status == 422


def test_revocation_comes_after_the_signature_and_field_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({"ARCI-L-20261020-7Q3KX9MD"}))
    assert code_of(tamper(full_license(), "licensee", "X")) == "license_signature_invalid"
    assert code_of(full_license(modules=["usage_analytics"])) == "license_malformed"
    monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset())
    assert load_license(full_license()).payload.license_id == "ARCI-L-20261020-7Q3KX9MD"


# -- A-T6 canonical form ---------------------------------------------------------------------------


def _reordered(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reordered(value[key]) for key in reversed(list(value))}
    return value


def test_a_reformatted_file_still_verifies() -> None:
    data = full_license()
    original = load_license(data)
    document = json.loads(data)
    variants = [
        json.dumps(document, indent=4),
        json.dumps(_reordered(document), ensure_ascii=True),
        json.dumps(document, separators=(",", ":"), ensure_ascii=False),
        "\r\n".join(json.dumps(document, indent=1, ensure_ascii=False).splitlines()),
        _text(data).replace("Ö", "\\u00d6"),
    ]
    for text in variants:
        copy = load_license(text.encode("utf-8"))
        assert copy.payload == original.payload
        assert copy.payload_sha256 == original.payload_sha256


def test_the_signature_covers_the_canonical_payload() -> None:
    from arcilogger_core.canonical import canonical_json

    data = full_license()
    document = json.loads(data)
    message = canonical_json(document["payload"])
    signature = b64url_decode_strict(document["signature"]["value"], 64)
    test_private_key().public_key().verify(signature, message)  # raises when wrong
    assert load_license(data).payload_sha256 == hashlib.sha256(message).hexdigest()


def test_make_license_defaults() -> None:
    document = load_license(make_license(server_ids=[SERVER]))
    assert document.payload.license_id == "ARCI-L-20261001-TEST0001"
    assert document.payload.modules == ("core_5651",)
    assert document.payload.licensee == "Örnek A.Ş."
    assert document.payload.maintenance_until == date(2099, 12, 31)
    assert document.payload.issued_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert (document.payload.log_sources, document.payload.users) == (None, None)
