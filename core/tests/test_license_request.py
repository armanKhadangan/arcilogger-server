"""The request code (licensing contract 2.2; test A-T7) and the Crockford/base64url codecs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.canonical import canonical_json
from arcilogger_core.license.codec import (
    CROCKFORD_ALPHABET,
    b64url_decode_strict,
    b64url_encode,
    crockford_decode,
    crockford_encode,
    group,
)
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.fingerprint import ServerIdentity
from arcilogger_core.license.request import (
    REQUEST_SCHEMA,
    LicenseRequest,
    make_request_code,
    new_request,
    parse_request_code,
    request_body,
    request_sha256,
)

WINDOWS = ServerIdentity(
    "windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "6F9619FF-8B86-D011-B42D-00C04FC964FF"
)
WINDOWS_WITHOUT_SMBIOS = ServerIdentity("windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "")
LINUX = ServerIdentity("linux", "4c4c4544004c4b108039b3c04f4b3732", "")
INSTALLATION = "3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4"
NOW = datetime(2026, 10, 20, 9, 30, 15, tzinfo=UTC)
TURKISH = "Örnek Ağ Şirketi İçin Çalışma ığüşöç ĞÜŞİÖÇ"


def request(
    identity: ServerIdentity = WINDOWS, organization: str = "Örnek A.Ş.", now: datetime = NOW
) -> LicenseRequest:
    return new_request(identity, installation_id=INSTALLATION, organization=organization, now=now)


def code_of(text: str) -> str:
    with pytest.raises(LicenseError) as caught:
        parse_request_code(text)
    return caught.value.code


def data_part(code: str) -> str:
    return code.removeprefix("ARCIREQ1-").replace("-", "")


def code_from_data(data: bytes) -> str:
    return "ARCIREQ1-" + group(crockford_encode(data), 5)


def code_from_body(body: bytes) -> str:
    return code_from_data(body + hashlib.sha256(body).digest()[:5])


# -- format -------------------------------------------------------------------------------------


def test_the_published_payload_and_encoding() -> None:
    req = request()
    payload = json.loads(request_body(req))
    assert payload == {
        "schema": REQUEST_SCHEMA,
        "server_id": "XW68-7K1Z-5FGV-AGT8",
        "installation_id": INSTALLATION,
        "product_version": "0.1.0",
        "organization": "Örnek A.Ş.",
        "platform": "windows",
        "smbios": True,
        "created_at": "2026-10-20T00:00:00Z",
    }
    body = request_body(req)
    assert body == canonical_json(payload)
    code = make_request_code(req)
    assert code.startswith("ARCIREQ1-")
    groups = code.removeprefix("ARCIREQ1-").split("-")
    assert all(len(item) == 5 for item in groups[:-1])
    assert 1 <= len(groups[-1]) <= 5
    assert set("".join(groups)) <= set(CROCKFORD_ALPHABET)
    data = crockford_decode(data_part(code))
    assert data == body + hashlib.sha256(body).digest()[:5]
    assert request_sha256(req) == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("identity", [WINDOWS, WINDOWS_WITHOUT_SMBIOS, LINUX])
@pytest.mark.parametrize("organization", ["Örnek A.Ş.", TURKISH, ""])
def test_round_trip_with_platform_and_smbios(identity: ServerIdentity, organization: str) -> None:
    req = request(identity, organization)
    parsed = parse_request_code(make_request_code(req))
    assert parsed == req
    assert parsed.platform == identity.platform
    assert parsed.smbios is (identity.smbios_uuid != "")
    assert parsed.organization == organization
    assert parsed.organization_set is bool(organization)
    assert request_body(parsed) == request_body(req)
    assert request_sha256(parsed) == request_sha256(req)


def test_the_code_is_the_same_for_a_whole_utc_day() -> None:
    first = make_request_code(request(now=datetime(2026, 10, 20, 0, 0, 1, tzinfo=UTC)))
    last = make_request_code(request(now=datetime(2026, 10, 20, 23, 59, 59, 999, tzinfo=UTC)))
    assert first == last == make_request_code(request())
    assert parse_request_code(first).created_at == datetime(2026, 10, 20, tzinfo=UTC)
    following = make_request_code(request(now=datetime(2026, 10, 21, tzinfo=UTC)))
    assert following != first
    assert parse_request_code(following).created_at == datetime(2026, 10, 21, tzinfo=UTC)
    # The UTC day, not the site day: 02:59 Istanbul on 21.10 is still 20.10 in UTC.
    late = datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
    assert make_request_code(request(now=late)) == first
    assert parse_request_code(make_request_code(request(now=late + timedelta(minutes=1)))) == (
        parse_request_code(following)
    )


# -- copying through e-mail ---------------------------------------------------------------------


def test_case_spaces_line_breaks_hyphens_and_look_alikes_are_accepted() -> None:
    req = request()
    code = make_request_code(req)
    data = data_part(code)
    wrapped = "\r\n".join(code[i : i + 40] for i in range(0, len(code), 40))
    variants = [
        code.lower(),
        "  " + code + "  \n",
        code.replace("-", " "),
        code.replace("-", ""),
        "ARCIREQ1" + data,
        "arcireq1-" + data.lower(),
        wrapped,
        "\t" + "\n\n".join(code[i : i + 30] for i in range(0, len(code), 30)) + "\t",
        "ARCIREQ1-" + data.replace("0", "O"),
        "ARCIREQ1-" + data.replace("1", "I"),
        "ARCIREQ1-" + data.replace("1", "l"),
        "ARCIREQ1-" + data.replace("1", "L"),
        code.replace("-", "\u2013"),  # a word processor's en dash
        code.replace("-", "\u00a0"),  # non-breaking space
    ]
    for text in variants:
        assert parse_request_code(text) == req, text


def test_every_single_character_substitution_is_detected() -> None:
    code = make_request_code(request(organization=TURKISH))
    data = data_part(code)
    same = {"0": "0O", "1": "1IL"}
    tried = 0
    for index, original in enumerate(data):
        for char in CROCKFORD_ALPHABET + "OIL":
            if char in same.get(original, original):
                continue
            changed = "ARCIREQ1-" + data[:index] + char + data[index + 1 :]
            assert code_of(changed) in {"request_checksum", "request_malformed"}, (index, char)
            tried += 1
    assert tried > 30 * len(data)


def test_wrong_prefix_and_foreign_characters() -> None:
    code = make_request_code(request())
    data = data_part(code)
    assert code_of("ARCIREQ2-" + data) == "request_malformed"
    assert code_of(data) == "request_malformed"
    assert parse_request_code("ARCI-REQ1" + data) == request()  # hyphens never matter
    assert code_of("ARCIREQ1-" + data + "U") == "request_malformed"
    assert code_of("ARCIREQ1-" + data[:10] + "*" + data[11:]) == "request_malformed"
    assert code_of("") == "request_malformed"
    assert code_of("ARCIREQ1-") == "request_malformed"
    assert code_of("ARCIREQ1-0000") == "request_malformed"  # fewer than 6 bytes
    assert code_of("ARCIREQ1-" + "0" * 5000) == "request_malformed"  # far longer than any code
    longest = request(organization="Ğ" * 200)
    assert parse_request_code(make_request_code(longest)) == longest


def test_a_truncated_code_fails_the_checksum_or_the_decoding() -> None:
    code = make_request_code(request())
    for cut in (1, 2, 5, 8, 40):
        assert code_of(code[:-cut]) in {"request_checksum", "request_malformed"}


def test_non_zero_padding_bits_are_malformed() -> None:
    organization = "Örnek"
    while (len(request_body(request(organization=organization))) + 5) * 8 % 5 == 0:
        organization += "x"
    code = make_request_code(request(organization=organization))
    data = data_part(code)
    last = CROCKFORD_ALPHABET.index(data[-1])
    assert last & 1 == 0  # the padding bit is zero in a real code
    changed = "ARCIREQ1-" + data[:-1] + CROCKFORD_ALPHABET[last | 1]
    assert code_of(changed) == "request_malformed"


def test_checksum_mismatch() -> None:
    body = request_body(request())
    wrong = body + bytes(a ^ 0xFF for a in hashlib.sha256(body).digest()[:5])
    assert code_of(code_from_data(wrong)) == "request_checksum"
    assert parse_request_code(code_from_body(body)) == request()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"schema": "arcilogger.license-request/1"}',
        json.dumps(json.loads(request_body(request())), indent=1).encode(),  # not canonical
        canonical_json({**json.loads(request_body(request())), "extra": 1}),
        canonical_json({**json.loads(request_body(request())), "smbios": "true"}),
        canonical_json({**json.loads(request_body(request())), "platform": "macos"}),
        canonical_json({**json.loads(request_body(request())), "schema": "arcilogger.request/2"}),
        canonical_json({**json.loads(request_body(request())), "server_id": "XW68"}),
        canonical_json({**json.loads(request_body(request())), "installation_id": None}),
        canonical_json({**json.loads(request_body(request())), "created_at": "2026-10-20"}),
        canonical_json({**json.loads(request_body(request())), "organization": "a\nb"}),
        canonical_json({**json.loads(request_body(request())), "product_version": ""}),
        canonical_json(
            {k: v for k, v in json.loads(request_body(request())).items() if k != "smbios"}
        ),
        request_body(request()).replace(b'"smbios":true', b'"smbios":1.0'),
        request_body(request()).replace(b'{"created_at"', b'{"smbios":true,"created_at"'),
        b"\xff\xfe",
        b"[" * 5000,
    ],
)
def test_a_body_that_is_not_a_canonical_request_is_malformed(body: bytes) -> None:
    assert code_of(code_from_body(body)) == "request_malformed"


def test_request_errors_have_both_texts() -> None:
    with pytest.raises(LicenseError) as caught:
        parse_request_code("ARCIREQ1-XXXXX")
    error = caught.value
    assert error.message_tr == "İstek kodu okunamadı."
    assert error.message_en == "The request code cannot be read."
    body = request_body(request())
    with pytest.raises(LicenseError) as caught:
        parse_request_code(code_from_data(body + b"\x00" * 5))
    assert caught.value.code == "request_checksum"
    assert "denetim toplamı" in caught.value.message_tr
    assert "checksum" in caught.value.message_en


def test_invalid_request_fields_are_refused() -> None:
    good = request()
    for field, value in [
        ("server_id", "XW68-7K1Z-5FGV-AGTU"),
        ("installation_id", INSTALLATION.upper()),
        ("product_version", "1 0"),
        ("organization", " x"),
        ("platform", "macos"),
        ("smbios", 1),
        ("created_at", datetime(2026, 10, 20)),  # noqa: DTZ001 - a naive datetime on purpose
        ("created_at", NOW.replace(microsecond=5)),
    ]:
        fields = {**vars(good), field: value}
        with pytest.raises(ValueError, match=field):
            LicenseRequest(**fields)


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=400))
def test_arbitrary_text_raises_only_licence_errors(text: str) -> None:
    codes = set()
    try:
        parse_request_code(text)
    except LicenseError as exc:
        codes.add(exc.code)
    assert codes <= {"request_checksum", "request_malformed"}


# -- codecs -------------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=200))
def test_crockford_round_trip(data: bytes) -> None:
    text = crockford_encode(data)
    assert len(text) == -(-len(data) * 8 // 5)
    assert crockford_decode(text) == data


def test_crockford_vectors_and_strictness() -> None:
    assert crockford_encode(b"") == ""
    assert crockford_encode(b"\x00") == "00"
    assert crockford_encode(b"\xff") == "ZW"
    assert crockford_encode(b"\xff" * 5) == "ZZZZZZZZ"
    assert crockford_decode("ZW") == b"\xff"
    with pytest.raises(ValueError, match="padding"):
        crockford_decode("ZZ")  # the last 2 bits are padding and must be zero
    with pytest.raises(ValueError, match="superfluous"):
        crockford_decode("000")  # 15 bits: one byte and 7 bits of padding
    for bad in ("U", "o", "I", "L", "-", " "):
        with pytest.raises(ValueError, match="alphabet"):
            crockford_decode("0" + bad)


def test_base64url_strict() -> None:
    raw = bytes(range(64))
    text = b64url_encode(raw)
    assert len(text) == 86
    assert b64url_decode_strict(text, 64) == raw
    for bad in (text + "==", text[:-1], text + "A", text[:-1] + "B", text.replace("A", "+", 1)):
        with pytest.raises(ValueError, match="base64url"):
            b64url_decode_strict(bad, 64)
    with pytest.raises(ValueError, match="32 bytes"):
        b64url_decode_strict(text, 32)


def test_group() -> None:
    assert group("ABCDEFGHIJK", 5) == "ABCDE-FGHIJ-K"
    assert group("ABCD", 4) == "ABCD"
    assert group("", 4) == ""
    with pytest.raises(ValueError, match="positive"):
        group("A", 0)
