"""Server fingerprint and server id (licensing contract 2.3; test A-T8)."""

from __future__ import annotations

import struct
import sys
import uuid
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.license import fingerprint
from arcilogger_core.license.fingerprint import (
    SERVER_ID_PATTERN,
    FingerprintError,
    ServerIdentity,
    current_identity,
    current_server_id,
    identity_bytes,
    parse_smbios_uuid,
    read_identity,
    read_linux_identity,
    server_id_of,
)
from arcilogger_core.license.testing import fixed_identity

GOLDEN = ServerIdentity(
    "windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "6F9619FF-8B86-D011-B42D-00C04FC964FF"
)
GOLDEN_ID = "XW68-7K1Z-5FGV-AGT8"
SMBIOS_UUID = uuid.UUID("6F9619FF-8B86-D011-B42D-00C04FC964FF")


# -- golden vector and format -------------------------------------------------------------------


def test_golden_server_id_vector() -> None:
    assert identity_bytes(GOLDEN) == (
        b"arcilogger.server-id/1\n"
        b"platform=windows\n"
        b"machine_id=0f8fad5b-d9cb-469f-a165-70867728950e\n"
        b"smbios_uuid=6F9619FF-8B86-D011-B42D-00C04FC964FF\n"
    )
    assert server_id_of(GOLDEN) == GOLDEN_ID


def test_every_part_of_the_identity_changes_the_id() -> None:
    ids = {
        server_id_of(GOLDEN),
        server_id_of(ServerIdentity("linux", GOLDEN.machine_id, GOLDEN.smbios_uuid)),
        server_id_of(ServerIdentity("windows", GOLDEN.machine_id, "")),
        server_id_of(
            ServerIdentity("windows", "0f8fad5b-d9cb-469f-a165-70867728950f", GOLDEN.smbios_uuid)
        ),
    }
    assert len(ids) == 4


@settings(max_examples=200, deadline=None)
@given(
    st.sampled_from(["windows", "linux"]),
    st.text(alphabet="0123456789abcdef-", min_size=1, max_size=40),
    st.text(alphabet="0123456789ABCDEF-", max_size=36),
)
def test_server_ids_have_the_published_format(platform: str, machine: str, smbios: str) -> None:
    identity = ServerIdentity("windows" if platform == "windows" else "linux", machine, smbios)
    assert SERVER_ID_PATTERN.fullmatch(server_id_of(identity))


def test_line_breaks_in_an_identity_are_refused() -> None:
    with pytest.raises(ValueError, match="line breaks"):
        identity_bytes(ServerIdentity("windows", "a\nplatform=linux", ""))


# -- SMBIOS -------------------------------------------------------------------------------------


def _structure(kind: int, formatted: bytes, strings: tuple[bytes, ...] = ()) -> bytes:
    header = struct.pack("<BBH", kind, 4 + len(formatted), 0x0100 + kind)
    tail = b"".join(item + b"\x00" for item in strings) + b"\x00" if strings else b"\x00\x00"
    return header + formatted + tail


def _type1(uuid_bytes: bytes, *, length: int = 0x1B) -> bytes:
    # manufacturer, product, version, serial (string numbers), the UUID, wake-up type, SKU, family
    formatted = bytes([1, 2, 3, 4]) + uuid_bytes + bytes([6, 5, 6])
    return _structure(1, formatted[: length - 4], (b"Maker", b"Product", b"1.0", b"SN-1", b"Fam"))


def raw_smbios(table: bytes, *, major: int = 2, minor: int = 8, length: int | None = None) -> bytes:
    size = len(table) if length is None else length
    return bytes([0, major, minor, 0]) + struct.pack("<I", size) + table


def table_with(type1: bytes) -> bytes:
    bios = _structure(0, bytes(range(1, 21)), (b"Vendor", b"BIOS 1.0", b"01/01/2026"))
    end = _structure(127, b"")
    return bios + type1 + end


def test_smbios_2_6_and_later_store_the_first_fields_little_endian() -> None:
    raw = raw_smbios(table_with(_type1(SMBIOS_UUID.bytes_le)), major=2, minor=8)
    assert parse_smbios_uuid(raw) == "6F9619FF-8B86-D011-B42D-00C04FC964FF"
    raw = raw_smbios(table_with(_type1(SMBIOS_UUID.bytes_le)), major=3, minor=4)
    assert parse_smbios_uuid(raw) == "6F9619FF-8B86-D011-B42D-00C04FC964FF"


def test_smbios_before_2_6_is_big_endian() -> None:
    raw = raw_smbios(table_with(_type1(SMBIOS_UUID.bytes)), major=2, minor=4)
    assert parse_smbios_uuid(raw) == "6F9619FF-8B86-D011-B42D-00C04FC964FF"


@pytest.mark.parametrize("value", [b"\x00" * 16, b"\xff" * 16])
def test_an_unset_uuid_is_empty(value: bytes) -> None:
    assert parse_smbios_uuid(raw_smbios(table_with(_type1(value)))) == ""


def test_no_system_structure_or_a_short_one_is_empty() -> None:
    bios = _structure(0, bytes(range(1, 21)), (b"Vendor",))
    assert parse_smbios_uuid(raw_smbios(bios + _structure(127, b""))) == ""
    assert parse_smbios_uuid(raw_smbios(bios)) == ""
    short = _structure(1, bytes([1, 2, 3, 4]), (b"Maker",))  # SMBIOS 2.0: no UUID field
    assert parse_smbios_uuid(raw_smbios(table_with(short), major=2, minor=0)) == ""


def test_structures_after_the_end_of_table_are_ignored() -> None:
    table = _structure(127, b"") + _type1(SMBIOS_UUID.bytes_le)
    assert parse_smbios_uuid(raw_smbios(table)) == ""


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00\x02\x08\x00",
        raw_smbios(table_with(_type1(SMBIOS_UUID.bytes_le)), length=10_000),  # table truncated
        raw_smbios(table_with(_type1(SMBIOS_UUID.bytes_le)))[:-40],
        raw_smbios(_type1(SMBIOS_UUID.bytes_le)[:20]),  # structure cut inside its formatted area
        raw_smbios(_structure(0, b"\x01\x02", (b"Vendor",))[:-2]),  # no string terminator
        raw_smbios(struct.pack("<BBH", 1, 2, 0) + b"\x00\x00"),  # length below the header
    ],
    ids=["empty", "header only", "length", "cut", "formatted", "terminator", "short header"],
)
def test_a_truncated_or_broken_table_is_a_fingerprint_error(raw: bytes) -> None:
    with pytest.raises(FingerprintError):
        parse_smbios_uuid(raw)


# -- Linux ----------------------------------------------------------------------------------------


def test_linux_machine_id(tmp_path: Path) -> None:
    first, second = tmp_path / "machine-id", tmp_path / "dbus-machine-id"
    first.write_text("4C4C4544004C4B108039B3C04F4B3732\n", encoding="ascii")
    identity = read_linux_identity([first, second])
    assert identity == ServerIdentity("linux", "4c4c4544004c4b108039b3c04f4b3732", "")
    first.write_text("not a machine id\n", encoding="ascii")
    second.write_text("0123456789abcdef0123456789abcdef", encoding="ascii")
    assert read_linux_identity([first, second]).machine_id == "0123456789abcdef0123456789abcdef"
    second.write_text("0123456789abcdef0123456789abcde", encoding="ascii")  # 31 digits
    with pytest.raises(FingerprintError):
        read_linux_identity([first, second])
    with pytest.raises(FingerprintError):
        read_linux_identity([tmp_path / "missing"])
    first.write_bytes(b"\xff" * 32)
    with pytest.raises(FingerprintError):
        read_linux_identity([first])


@pytest.mark.skipif(sys.platform == "win32", reason="Linux reader")
def test_read_identity_on_linux_has_no_smbios_uuid() -> None:
    if not any(path.is_file() for path in fingerprint.MACHINE_ID_PATHS):
        pytest.skip("no machine id on this machine")
    identity = read_identity()
    assert identity.platform == "linux"
    assert identity.smbios_uuid == ""


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows readers are tested on Windows")
def test_the_windows_readers_refuse_on_other_platforms() -> None:
    with pytest.raises(FingerprintError):
        fingerprint.read_windows_identity()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_read_identity_on_windows() -> None:  # pragma: no cover - runs in the Windows CI job
    identity = read_identity()
    assert identity.platform == "windows"
    assert len(identity.machine_id) == 36
    assert identity.machine_id == identity.machine_id.lower()
    assert identity.smbios_uuid in ("", identity.smbios_uuid.upper())


def test_machine_guid_normalisation() -> None:
    normalize = fingerprint._normalize_machine_guid
    assert normalize("{0F8FAD5B-D9CB-469F-A165-70867728950E}") == GOLDEN.machine_id
    assert normalize(" 0f8fad5b-d9cb-469f-a165-70867728950e ") == GOLDEN.machine_id
    for bad in ("", "0f8fad5b", 42, None):
        with pytest.raises(FingerprintError):
            normalize(bad)


# -- the provider and the cache -------------------------------------------------------------------


def test_fixed_identity_swaps_and_restores() -> None:
    original = fingerprint.IDENTITY_PROVIDER
    assert original is read_identity
    with fixed_identity() as server_id:
        assert server_id == GOLDEN_ID
        assert current_server_id() == GOLDEN_ID
        assert current_identity() == GOLDEN
        with fixed_identity(machine_id="11111111-2222-4333-8444-555555555555") as other:
            assert other != GOLDEN_ID
            assert current_server_id() == other
        assert current_server_id() == GOLDEN_ID
        with fixed_identity(smbios_uuid="", platform="linux") as weak:
            assert current_identity().platform == "linux"
            assert current_server_id() == weak
    assert fingerprint.IDENTITY_PROVIDER is original
    assert fingerprint._cached is None


def test_the_identity_is_read_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def provider() -> ServerIdentity:
        calls.append(1)
        return GOLDEN

    monkeypatch.setattr(fingerprint, "IDENTITY_PROVIDER", provider)
    fingerprint.clear_cache()
    try:
        assert current_server_id() == current_server_id() == GOLDEN_ID
        assert len(calls) == 1
        fingerprint.clear_cache()
        assert current_server_id() == GOLDEN_ID
        assert len(calls) == 2
    finally:
        fingerprint.clear_cache()


def test_every_provider_failure_is_a_fingerprint_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> ServerIdentity:
        raise OSError("ctypes failed")

    def junk() -> ServerIdentity:
        return "XW68"  # type: ignore[return-value]

    for provider in (broken, junk, lambda: ServerIdentity("macos", "x", "")):  # type: ignore[arg-type]
        monkeypatch.setattr(fingerprint, "IDENTITY_PROVIDER", provider)
        fingerprint.clear_cache()
        with pytest.raises(FingerprintError):
            current_server_id()
        assert fingerprint._cached is None  # a failure is not cached
    fingerprint.clear_cache()


def test_no_environment_variable_changes_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    with fixed_identity():
        for name in ("ARCILOGGER_SERVER_ID", "ARCILOGGER_MACHINE_ID", "MACHINE_ID"):
            monkeypatch.setenv(name, "0000-0000-0000-0000")
        fingerprint.clear_cache()
        assert current_server_id() == GOLDEN_ID
