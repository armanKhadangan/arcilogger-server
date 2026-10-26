"""Signed request files of the administration channel (ADR-0014; contract 2.4.3, 2.4.5, 2.5.2).

Round trip, tampering, wrong and unknown keys, domain separation, expiry, size limits, strict
parsing, and the signature checked before anything of the payload is parsed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.admin.contract import (
    MAX_REQUEST_BYTES,
    MAX_STRING_BYTES,
    Actor,
    AdminRequest,
    KeyRole,
    KeysFile,
    PublicKeyEntry,
    RequestOptions,
    SecretSetOp,
    SetOp,
    TaskParams,
    TaskRequest,
    b64url_decode,
    b64url_encode,
    key_id_for,
)
from arcilogger_core.admin.envelope import (
    ADMIN_REQUEST,
    TASK_REQUEST,
    Domain,
    EnvelopeError,
    open_admin_request,
    open_task_request,
    sign_admin_request,
    sign_payload,
    sign_task_request,
    verify,
)
from arcilogger_core.admin.keys import SigningKey
from arcilogger_core.canonical import canonical_json
from arcilogger_core.timeutil import SITE_TZ

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
INSTANCE = "5e0c" * 8
REQUEST_ID = "6f1c1a2b3c4d5e6f708192a3b4c5d6e7"
TOKEN = "gAAAAABn" + "A" * 80 + "=="


def signing_key(role: KeyRole, seed: int) -> SigningKey:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return SigningKey(role, key_id_for(role, raw), private_key)


API = signing_key("api", 1)
AGENT = signing_key("agent", 2)
#: Keys the installation does not know (another installation's, or forged).
FOREIGN_API = signing_key("api", 3)


def keyring(*keys: SigningKey, instance: str = INSTANCE) -> KeysFile:
    entries = []
    for key in keys:
        raw = key.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        entries.append(PublicKeyEntry(id=key.key_id, role=key.role, public=b64url_encode(raw)))
    return KeysFile(instance=instance, keys=tuple(entries))


KEYS = keyring(API, AGENT)


def admin_request(**changes: Any) -> AdminRequest:
    fields: dict[str, Any] = {
        "id": REQUEST_ID,
        "instance": INSTANCE,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
        "kind": "config.apply",
        "section": "smtp",
        "actor": Actor(
            user_id=3,
            username="ayse",
            session="3f9c0a1b2c4d",
            ip="172.20.10.37",
            reauth_at=NOW - timedelta(seconds=40),
        ),
        "audit_id": 4812,
        "ops": (
            SetOp(
                op="set", path="smtp.host", expect="mail.ornek.example", value="smtp.ornek.example"
            ),
            SecretSetOp(op="secret_set", name="smtp.password", expect="set", token=TOKEN),
        ),
        "options": RequestOptions(test_to="bt@ornek.example"),
    }
    fields.update(changes)
    return AdminRequest(**fields)


def admin_payload(**changes: Any) -> dict[str, Any]:
    """The JSON payload of :func:`admin_request`, changeable beyond what the model allows."""
    payload = admin_request().model_dump(mode="json")
    payload.update(changes)
    return payload


def task_request(**changes: Any) -> TaskRequest:
    fields: dict[str, Any] = {
        "id": "0a" * 16,
        "origin": REQUEST_ID,
        "kind": "seal",
        "params": TaskParams(day=NOW.date() - timedelta(days=1)),
        "issued_at": NOW,
        "expires_at": NOW + timedelta(hours=6),
    }
    fields.update(changes)
    return TaskRequest(**fields)


def signed(payload: dict[str, Any], key: SigningKey = API, domain: Domain = ADMIN_REQUEST) -> bytes:
    return sign_payload(canonical_json(payload), key, domain)


def admin_code(data: bytes, keys: KeysFile = KEYS, now: datetime = NOW) -> str:
    with pytest.raises(EnvelopeError) as caught:
        open_admin_request(data, keys, now=now)
    return caught.value.code


def task_code(data: bytes, keys: KeysFile = KEYS, now: datetime = NOW) -> str:
    with pytest.raises(EnvelopeError) as caught:
        open_task_request(data, keys, now=now)
    return caught.value.code


def envelope_of(data: bytes) -> dict[str, Any]:
    document = json.loads(data)
    assert isinstance(document, dict)
    return document


def rebuilt(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope).encode("utf-8")


# -- round trip ---------------------------------------------------------------------------------


def test_an_admin_request_round_trips() -> None:
    request = admin_request()
    data = sign_admin_request(request, API)
    assert open_admin_request(data, KEYS, now=NOW) == request
    envelope = envelope_of(data)
    assert set(envelope) == {"v", "key_id", "payload", "signature"}
    assert envelope["v"] == 1
    assert envelope["key_id"] == API.key_id
    payload = b64url_decode(envelope["payload"])
    assert payload == canonical_json(request.model_dump(mode="json"))
    assert json.loads(payload)["schema"] == "arcilogger.admin-request/1"
    # The signature covers the domain prefix and the payload bytes.
    API.private_key.public_key().verify(
        b64url_decode(envelope["signature"], 64), b"arcilogger.admin-request/1\n" + payload
    )


@pytest.mark.parametrize(
    ("kind", "params", "validity"),
    [
        ("seal", TaskParams(day=NOW.date() - timedelta(days=1)), timedelta(hours=6)),
        ("credit", TaskParams(), timedelta(minutes=30)),
        ("tsa_test", TaskParams(), timedelta(minutes=30)),
        ("replica_test", TaskParams(), timedelta(minutes=1)),
    ],
)
def test_a_task_request_round_trips(kind: str, params: TaskParams, validity: timedelta) -> None:
    request = task_request(kind=kind, params=params, expires_at=NOW + validity)
    data = sign_task_request(request, AGENT)
    assert open_task_request(data, KEYS, now=NOW + validity - timedelta(seconds=1)) == request


def test_every_action_kind_round_trips() -> None:
    for kind, options in [
        ("action.seal", RequestOptions(day=NOW.date() - timedelta(days=1))),
        ("action.credit", RequestOptions()),
        ("action.tsa_test", RequestOptions()),
        ("action.replica_test", RequestOptions()),
        ("config.cancel", RequestOptions(target="ab" * 16)),
    ]:
        request = admin_request(kind=kind, section=None, ops=(), options=options)
        assert open_admin_request(sign_admin_request(request, API), KEYS, now=NOW) == request


# -- tampering ----------------------------------------------------------------------------------


def test_a_changed_payload_is_refused() -> None:
    envelope = envelope_of(sign_admin_request(admin_request(), API))
    payload = json.loads(b64url_decode(envelope["payload"]))
    payload["ops"][0]["value"] = "smtp.example.net"
    envelope["payload"] = b64url_encode(canonical_json(payload))
    assert admin_code(rebuilt(envelope)) == "signature_invalid"


def test_a_changed_signature_is_refused() -> None:
    envelope = envelope_of(sign_admin_request(admin_request(), API))
    signature = bytearray(b64url_decode(envelope["signature"], 64))
    signature[0] ^= 1
    envelope["signature"] = b64url_encode(bytes(signature))
    assert admin_code(rebuilt(envelope)) == "signature_invalid"


@settings(max_examples=300, deadline=None)
@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=0, max_value=7))
def test_any_flipped_bit_is_refused(position: int, bit: int) -> None:
    data = sign_admin_request(admin_request(), API)
    index = position % len(data)
    changed = data[:index] + bytes([data[index] ^ (1 << bit)]) + data[index + 1 :]
    with pytest.raises(EnvelopeError):
        open_admin_request(changed, KEYS, now=NOW)


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=2048))
def test_arbitrary_bytes_raise_only_envelope_errors(data: bytes) -> None:
    with pytest.raises(EnvelopeError):
        open_admin_request(data, KEYS, now=NOW)


# -- keys -----------------------------------------------------------------------------------------


def test_an_unknown_key_id_is_refused() -> None:
    envelope = envelope_of(sign_admin_request(admin_request(), API))
    envelope["key_id"] = "api-00000000"
    assert admin_code(rebuilt(envelope)) == "signature_invalid"


def test_a_key_of_another_installation_is_refused() -> None:
    assert admin_code(sign_admin_request(admin_request(), FOREIGN_API)) == "signature_invalid"


def test_a_foreign_key_using_a_known_key_id_is_refused() -> None:
    impostor = SigningKey("api", API.key_id, FOREIGN_API.private_key)
    with pytest.raises(EnvelopeError) as caught:
        open_admin_request(sign_admin_request(admin_request(), impostor), KEYS, now=NOW)
    assert caught.value.code == "signature_invalid"
    assert caught.value.key_id == API.key_id


def test_only_the_signer_role_is_accepted() -> None:
    """The agent key cannot sign an administration request, the API key no task request."""
    with pytest.raises(ValueError, match="api key"):
        sign_admin_request(admin_request(), AGENT)
    with pytest.raises(ValueError, match="agent key"):
        sign_task_request(task_request(), API)
    # Forged with the other role's key all the same: that key id is not a signer of the domain.
    forged_admin = SigningKey("api", AGENT.key_id, AGENT.private_key)
    assert admin_code(sign_admin_request(admin_request(), forged_admin)) == "signature_invalid"
    forged_task = SigningKey("agent", API.key_id, API.private_key)
    assert task_code(sign_task_request(task_request(), forged_task)) == "signature_invalid"


# -- domain separation ----------------------------------------------------------------------------


def test_the_domain_prefix_is_part_of_the_signed_message() -> None:
    """One key for both domains would still not let one kind pass as the other."""
    both = keyring(API, SigningKey("agent", API.key_id.replace("api", "agent"), API.private_key))
    as_task = Domain(TASK_REQUEST.schema, "api", TASK_REQUEST.max_bytes)
    data = sign_payload(canonical_json(admin_payload()), API, as_task)
    assert admin_code(data, both) == "signature_invalid"


def test_a_task_request_is_not_an_admin_request() -> None:
    data = sign_task_request(task_request(), AGENT)
    assert admin_code(data) == "signature_invalid"  # an agent key, not an API key
    # Signed in the right domain with the right key, the payload still names its kind.
    payload = task_request().model_dump(mode="json")
    assert admin_code(signed(payload)) == "schema_invalid"


# -- times and installation -----------------------------------------------------------------------


def test_an_expired_request_is_refused() -> None:
    data = sign_admin_request(admin_request(), API)
    assert open_admin_request(data, KEYS, now=NOW + timedelta(minutes=9, seconds=59))
    assert admin_code(data, now=NOW + timedelta(minutes=10)) == "request_expired"


def test_an_expired_task_request_is_refused() -> None:
    data = sign_task_request(task_request(), AGENT)
    assert task_code(data, now=NOW + timedelta(hours=6)) == "request_expired"


def test_a_future_dated_request_is_refused() -> None:
    data = sign_admin_request(admin_request(), API)
    assert open_admin_request(data, KEYS, now=NOW - timedelta(minutes=5))
    assert admin_code(data, now=NOW - timedelta(minutes=5, seconds=1)) == "schema_invalid"


@pytest.mark.parametrize(
    "validity", [timedelta(minutes=10, seconds=1), timedelta(0), -timedelta(1)]
)
def test_a_validity_outside_ten_minutes_is_refused(validity: timedelta) -> None:
    payload = admin_payload(expires_at=(NOW + validity).isoformat())
    assert admin_code(signed(payload)) == "schema_invalid"


def test_a_seal_request_may_wait_six_hours_another_task_thirty_minutes() -> None:
    seal = task_request().model_dump(mode="json")
    seal["expires_at"] = (NOW + timedelta(hours=6, seconds=1)).isoformat()
    credit = task_request(
        kind="credit", params=TaskParams(), expires_at=NOW + timedelta(minutes=30)
    ).model_dump(mode="json")
    credit["expires_at"] = (NOW + timedelta(minutes=31)).isoformat()
    for payload in (seal, credit):
        assert task_code(signed(payload, AGENT, TASK_REQUEST)) == "schema_invalid"


def test_a_request_for_another_installation_is_refused() -> None:
    other = keyring(API, AGENT, instance="7d" * 16)
    assert admin_code(sign_admin_request(admin_request(), API), other) == "installation_mismatch"


# -- size and strict parsing ----------------------------------------------------------------------


def test_an_oversize_file_is_refused_before_parsing() -> None:
    data = sign_admin_request(admin_request(), API)
    assert admin_code(data + b" " * (MAX_REQUEST_BYTES - len(data) + 1)) == "schema_invalid"
    assert admin_code(b"{" * (MAX_REQUEST_BYTES + 1)) == "schema_invalid"
    assert task_code(b" " * (TASK_REQUEST.max_bytes + 1)) == "schema_invalid"


def test_an_oversize_envelope_is_never_written() -> None:
    payload = b"x" * MAX_REQUEST_BYTES
    with pytest.raises(ValueError, match="exceed"):
        sign_payload(payload, API, ADMIN_REQUEST)


def test_a_long_string_is_refused() -> None:
    long = "a" * (MAX_STRING_BYTES + 1)
    payload = admin_payload()
    payload["ops"][0]["value"] = long
    assert admin_code(signed(payload)) == "schema_invalid"
    with pytest.raises(ValueError, match="longer"):
        sign_admin_request(
            admin_request(ops=(SetOp(op="set", path="site_name", expect="", value=long),)), API
        )


@pytest.mark.parametrize(
    "payload",
    [
        # whitespace and unsorted keys: valid JSON, but not the canonical bytes
        json.dumps(admin_payload(), indent=1).encode("utf-8"),
        json.dumps(dict(reversed(admin_payload().items())), separators=(",", ":")).encode(),
        # duplicate key (the second "id" would win in a lax parser)
        canonical_json(admin_payload())[:-1] + b',"id":"' + b"ff" * 16 + b'"}',
        # a float
        canonical_json(admin_payload())[:-1] + b',"probe_seconds":1.5}',
    ],
    ids=["indented", "unsorted", "duplicate-key", "float"],
)
def test_a_signed_but_non_canonical_payload_is_refused(payload: bytes) -> None:
    assert admin_code(sign_payload(payload, API, ADMIN_REQUEST)) == "schema_invalid"


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": True},
        {"schema": "arcilogger.admin-request/2"},
        {"kind": "config.delete"},
        {"audit_id": True},
        {"ops": [{"op": "set", "path": "smtp.host", "expect": None}]},
        {
            "ops": [
                {"op": "secret_set", "name": "database.password", "expect": "unset", "token": TOKEN}
            ]
        },
        {
            "ops": [
                {
                    "op": "secret_set",
                    "name": "smtp.password",
                    "expect": "unset",
                    "token": "plain-text-password",
                }
            ]
        },
        {"ops": []},
        {"issued_at": "2026-10-25T10:15:00"},
        {"id": "6F1C1A2B3C4D5E6F708192A3B4C5D6E7"},
    ],
    ids=[
        "extra-field",
        "schema",
        "kind",
        "bool-as-int",
        "op-without-value",
        "secret-not-for-the-browser",
        "secret-in-plain-text",
        "apply-without-ops",
        "naive-time",
        "upper-case-id",
    ],
)
def test_a_signed_payload_breaking_the_model_is_refused(changes: dict[str, Any]) -> None:
    assert admin_code(signed(admin_payload(**changes))) == "schema_invalid"


@pytest.mark.parametrize(
    "envelope",
    [
        {"v": 1, "key_id": API.key_id, "payload": "e30"},
        {"v": 1, "key_id": API.key_id, "payload": "e30", "signature": "AA", "x": 1},
        {"v": True, "key_id": API.key_id, "payload": "e30", "signature": "A" * 86},
        {"v": 2, "key_id": API.key_id, "payload": "e30", "signature": "A" * 86},
        {"v": 1, "key_id": 7, "payload": "e30", "signature": "A" * 86},
        {"v": 1, "key_id": API.key_id, "payload": "e30=", "signature": "A" * 86},
        {"v": 1, "key_id": API.key_id, "payload": "e3+", "signature": "A" * 86},
        {"v": 1, "key_id": API.key_id, "payload": "e30", "signature": "A" * 85},
        [1, 2, 3],
    ],
    ids=[
        "no-signature",
        "extra-key",
        "bool-version",
        "version-2",
        "key-id-not-text",
        "padded-base64",
        "standard-base64",
        "short-signature",
        "not-an-object",
    ],
)
def test_a_malformed_envelope_is_refused(envelope: Any) -> None:
    assert admin_code(json.dumps(envelope).encode()) == "schema_invalid"


@pytest.mark.parametrize(
    "data",
    [b"", b"\xef\xbb\xbf{}", b"\xff\xfe{}", b"not json", b'{"v": 1, "v": 1}', b"[" * 100_000],
    ids=["empty", "bom", "utf-16", "text", "duplicate-key", "deep"],
)
def test_unreadable_envelope_bytes_are_refused(data: bytes) -> None:
    assert admin_code(data) == "schema_invalid"


def test_the_signature_is_checked_before_the_payload_is_parsed() -> None:
    """A payload that would crash or mislead a parser is refused by its signature first."""
    for payload in (b"[" * 100_000, b"\xff\xfe not utf-8", b'{"schema": 1e999}'):
        assert admin_code(sign_payload(payload, FOREIGN_API, ADMIN_REQUEST)) == (
            "signature_invalid"
        )
        assert admin_code(sign_payload(payload, API, ADMIN_REQUEST)) == "schema_invalid"


def test_verify_returns_the_signed_bytes() -> None:
    request = admin_request()
    data = sign_admin_request(request, API)
    verified = verify(data, KEYS.public_keys("api"), ADMIN_REQUEST)
    assert verified.key_id == API.key_id
    assert verified.payload == canonical_json(request.model_dump(mode="json"))
    assert verified.document["id"] == REQUEST_ID
