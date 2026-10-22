"""The server fingerprint and its short id (licensing contract 2.3).

A licence names the servers it may run on by their **server id**, 16 Crockford base32
characters ``XXXX-XXXX-XXXX-XXXX``: the first 80 bits of SHA-256 over the identity bytes::

    arcilogger.server-id/1
    platform=<platform>
    machine_id=<machine_id>
    smbios_uuid=<smbios_uuid>

* Windows: ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid`` (64-bit view) and the
  SMBIOS type-1 system UUID from ``GetSystemFirmwareTable('RSMB')``. Both are readable without
  administrator rights, so the services' virtual accounts and an administrator's console compute
  the same id. A failure is a :class:`FingerprintError`, never a silent fallback.
* Linux and other platforms (development and tests only): ``/etc/machine-id`` (else
  ``/var/lib/dbus/machine-id``); no SMBIOS UUID (``/sys/class/dmi/id/product_uuid`` is
  readable by root only, so processes would disagree).

Tests swap :data:`IDENTITY_PROVIDER` (``testing.fixed_identity``); no environment variable or
configuration value changes the identity.
"""

from __future__ import annotations

import hashlib
import re
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from arcilogger_core.license.codec import crockford_encode, group

Platform = Literal["windows", "linux"]

IDENTITY_SCHEMA: Final = "arcilogger.server-id/1"
SERVER_ID_PATTERN: Final = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}")
MACHINE_ID_PATHS: Final = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))

_GUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MACHINE_ID: Final = re.compile(r"[0-9a-f]{32}")
_RSMB: Final = 0x52534D42  # 'RSMB': the raw SMBIOS firmware table provider
_SMBIOS_HEADER: Final = 8  # RawSMBIOSData: 4 bytes of versions, DWORD Length
_SMBIOS_END_OF_TABLE: Final = 127


class FingerprintError(Exception):
    """The server identity cannot be read (the licence cannot be checked against the server)."""


@dataclass(frozen=True)
class ServerIdentity:
    platform: Platform
    machine_id: str  # windows: MachineGuid, lower-case, no braces; linux: /etc/machine-id (32 hex)
    smbios_uuid: str  # windows: SMBIOS type-1 UUID, upper-case 8-4-4-4-12, or ""; linux: ""


def identity_bytes(identity: ServerIdentity) -> bytes:
    """The bytes the server id is the hash of (UTF-8, ``\\n`` line ends, final ``\\n``)."""
    fields = (identity.platform, identity.machine_id, identity.smbios_uuid)
    if any("\n" in value or "\r" in value for value in fields):
        raise ValueError("identity fields must not contain line breaks")
    return (
        f"{IDENTITY_SCHEMA}\n"
        f"platform={identity.platform}\n"
        f"machine_id={identity.machine_id}\n"
        f"smbios_uuid={identity.smbios_uuid}\n"
    ).encode()


def server_id_of(identity: ServerIdentity) -> str:
    """The short readable id ``XXXX-XXXX-XXXX-XXXX`` of an identity."""
    digest = hashlib.sha256(identity_bytes(identity)).digest()
    return group(crockford_encode(digest[:10]), 4)


# -- SMBIOS -------------------------------------------------------------------------------------


def parse_smbios_uuid(raw: bytes) -> str:
    """The system UUID of a ``RawSMBIOSData`` buffer, upper-case ``8-4-4-4-12``.

    ``""`` when there is no type-1 structure, it is too short to hold a UUID (SMBIOS 2.0), or
    the UUID is all zero / all ``FF`` (not set). A truncated or unparsable table is a
    :class:`FingerprintError`. SMBIOS 2.6 and later store the first three fields little-endian.
    """
    if len(raw) < _SMBIOS_HEADER:
        raise FingerprintError("SMBIOS data shorter than its header")
    version = (raw[1], raw[2])
    length = int.from_bytes(raw[4:8], "little")
    table = raw[_SMBIOS_HEADER : _SMBIOS_HEADER + length]
    if len(table) != length:
        raise FingerprintError("SMBIOS table truncated")
    offset = 0
    while offset + 4 <= len(table):
        kind, size = table[offset], table[offset + 1]
        if size < 4 or offset + size > len(table):
            raise FingerprintError(f"SMBIOS structure at {offset} has an invalid length")
        strings_end = table.find(b"\x00\x00", offset + size)
        if strings_end < 0:
            raise FingerprintError(f"SMBIOS structure at {offset} has no string terminator")
        if kind == 1:
            if size < 8 + 16:
                return ""
            data = table[offset + 8 : offset + 24]
            if data in (b"\x00" * 16, b"\xff" * 16):
                return ""
            value = uuid.UUID(bytes_le=data) if version >= (2, 6) else uuid.UUID(bytes=data)
            return str(value).upper()
        if kind == _SMBIOS_END_OF_TABLE:
            break
        offset = strings_end + 2
    return ""


def _normalize_machine_guid(value: object) -> str:
    text = value.strip().strip("{}").lower() if isinstance(value, str) else ""
    if not _GUID.fullmatch(text):
        raise FingerprintError("MachineGuid is not a GUID")
    return text


if sys.platform == "win32":
    import ctypes
    import winreg
    from ctypes import wintypes

    def _read_machine_guid() -> str:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "MachineGuid")
        except OSError as exc:
            raise FingerprintError(f"MachineGuid cannot be read: {exc}") from exc
        return _normalize_machine_guid(value)

    def _read_smbios_table() -> bytes:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        call = kernel32.GetSystemFirmwareTable
        call.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        call.restype = wintypes.UINT
        size = call(_RSMB, 0, None, 0)
        if not size:
            raise FingerprintError(f"GetSystemFirmwareTable failed ({ctypes.get_last_error()})")
        buffer = ctypes.create_string_buffer(size)
        written = call(_RSMB, 0, buffer, size)
        if not written or written > size:
            raise FingerprintError(f"GetSystemFirmwareTable failed ({ctypes.get_last_error()})")
        return bytes(buffer.raw[:written])

else:

    def _read_machine_guid() -> str:
        raise FingerprintError("MachineGuid exists only on Windows")

    def _read_smbios_table() -> bytes:
        raise FingerprintError("GetSystemFirmwareTable exists only on Windows")


def read_windows_identity() -> ServerIdentity:
    return ServerIdentity(
        platform="windows",
        machine_id=_read_machine_guid(),
        smbios_uuid=parse_smbios_uuid(_read_smbios_table()),
    )


def read_linux_identity(paths: Sequence[Path] = MACHINE_ID_PATHS) -> ServerIdentity:
    """The machine id of the first readable file holding 32 hex digits."""
    for path in paths:
        try:
            text = path.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeDecodeError):
            continue
        if _MACHINE_ID.fullmatch(text):
            return ServerIdentity(platform="linux", machine_id=text, smbios_uuid="")
    raise FingerprintError("no machine id (/etc/machine-id, /var/lib/dbus/machine-id)")


def read_identity() -> ServerIdentity:
    """This server's identity; :class:`FingerprintError` when it cannot be read."""
    if sys.platform == "win32":
        identity = read_windows_identity()
    else:
        identity = read_linux_identity()
    return identity


#: Where the identity comes from: the real reader. Tests swap it (``testing.fixed_identity``).
IDENTITY_PROVIDER: Callable[[], ServerIdentity] = read_identity

_lock = threading.Lock()
_cached: ServerIdentity | None = None


def current_identity() -> ServerIdentity:
    """This server's identity, read once per process (cached until :func:`clear_cache`).

    Every failure of the provider is a :class:`FingerprintError` (not cached: the next call
    tries again).
    """
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        try:
            identity = IDENTITY_PROVIDER()
            if not isinstance(identity, ServerIdentity) or identity.platform not in (
                "windows",
                "linux",
            ):
                raise FingerprintError("the identity provider returned no server identity")
            identity_bytes(identity)
        except FingerprintError:
            raise
        except Exception as exc:
            raise FingerprintError(f"{type(exc).__name__}: {exc}") from exc
        _cached = identity
        return identity


def current_server_id() -> str:
    """This server's id ``XXXX-XXXX-XXXX-XXXX``; :class:`FingerprintError` when unreadable."""
    return server_id_of(current_identity())


def clear_cache() -> None:
    """Forget the cached identity (tests; a new provider)."""
    global _cached
    with _lock:
        _cached = None
