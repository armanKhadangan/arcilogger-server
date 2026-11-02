"""Keys of the administration channel (ADR-0014; contract 2.4.2), in ``admin\\keys``.

* ``api-ed25519.key`` signs administration requests (readable by ArciApi only);
* ``agent-ed25519.key`` signs task requests (readable by ArciAdmin only);
* ``keys.json`` (:class:`~arcilogger_core.admin.contract.KeysFile`) holds both public keys and
  the installation's instance id; the API, the agent, the LOCAL SERVICE tasks and SYSTEM read it.

A private key file is the raw 32-byte Ed25519 key in base64 on one line. :func:`init_keys`
(``python -I -m arcilogger_core.admin keys init``, run by the installer as administrator) creates
a missing key with ``O_EXCL`` and never overwrites one; replacing a key is a rotation, a console
command of its own. A key id is ``<role>-`` and the first 8 hex digits of the SHA-256 of the raw
public key, so a request names the key that signed it.

A new private key file is readable by its owner only from the moment it exists: mode ``0600``
on POSIX, and on Windows a protected security descriptor given to ``CreateFileW`` (SYSTEM,
Administrators and the file's owner; nothing inherited from ``ProgramData\\ARCILOGGER``, whose
inheritable Read for the services and LOCAL SERVICE would otherwise reach the key until the
installer's ACL step). The installer creates ``admin\\`` and ``admin\\keys\\`` with their
protected rights before ``keys init`` and then adds each key's one Read entry (contract 2.2).
"""

from __future__ import annotations

import base64
import binascii
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from arcilogger_core.admin.contract import (
    DocumentError,
    KeyRole,
    KeysFile,
    PublicKeyEntry,
    b64url_encode,
    dump_document,
    key_id_for,
    new_id,
    parse_document,
)
from arcilogger_core.fsutil import atomic_write_bytes

KEY_FILES: Final[Mapping[KeyRole, str]] = MappingProxyType(
    {"api": "api-ed25519.key", "agent": "agent-ed25519.key"}  # gitleaks:allow (file names)
)
KEYS_FILE: Final = "keys.json"
#: The order of the keys in keys.json.
ROLES: Final[tuple[KeyRole, ...]] = ("api", "agent")


class KeysError(RuntimeError):
    """A key file of the channel is missing, unreadable or does not match keys.json."""


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    #: Protected (no inherited entry): SYSTEM, Administrators and the owner (the creator).
    _KEY_FILE_SDDL: Final = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)"
    _SDDL_REVISION_1: Final = 1
    _GENERIC_WRITE: Final = 0x40000000
    _CREATE_NEW: Final = 1
    _FILE_ATTRIBUTE_NORMAL: Final = 0x00000080
    _INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _local_free = _kernel32.LocalFree
    _local_free.argtypes = [wintypes.HLOCAL]
    _local_free.restype = wintypes.HLOCAL
    _sddl_to_descriptor = _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    _sddl_to_descriptor.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    ]
    _sddl_to_descriptor.restype = wintypes.BOOL

    def _windows_error(code: int, path: Path) -> OSError:
        # OSError maps the Windows error code to FileExistsError, PermissionError, …
        return OSError(None, ctypes.FormatError(code).strip(), str(path), code)

    def _open_new_private(path: Path) -> int:
        """A new file (``CREATE_NEW``, like ``O_EXCL``) whose protected security descriptor is
        set as it is created, so no other account can open it even for a moment."""
        descriptor = wintypes.LPVOID()
        if not _sddl_to_descriptor(
            _KEY_FILE_SDDL, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise _windows_error(ctypes.get_last_error(), path)
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = _create_file(
                str(path),
                _GENERIC_WRITE,
                0,
                ctypes.byref(attributes),
                _CREATE_NEW,
                _FILE_ATTRIBUTE_NORMAL,
                None,
            )
            code = ctypes.get_last_error()
        finally:
            _local_free(descriptor)
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            raise _windows_error(code, path)
        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except OSError:
            _close_handle(handle)
            raise

else:

    def _open_new_private(path: Path) -> int:
        """A new file (``O_EXCL``) readable by its owner only."""
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


@dataclass(frozen=True)
class SigningKey:
    role: KeyRole
    key_id: str
    private_key: Ed25519PrivateKey = field(repr=False)  # gitleaks:allow (a type name, no key)


def key_file(keys_dir: Path, role: KeyRole) -> Path:
    return keys_dir / KEY_FILES[role]


def keys_file(keys_dir: Path) -> Path:
    return keys_dir / KEYS_FILE


def _raw_public(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(role: KeyRole, public_key: Ed25519PublicKey) -> str:
    return key_id_for(role, _raw_public(public_key))


def _read_private(path: Path) -> Ed25519PrivateKey:
    try:
        text = path.read_text(encoding="ascii").strip()
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(text, validate=True))
    except FileNotFoundError as exc:
        raise KeysError(f"channel key not found: {path} (run: keys init)") from exc
    except (OSError, UnicodeDecodeError, binascii.Error, ValueError) as exc:
        raise KeysError(f"channel key unreadable: {path}: {exc}") from exc


def _create_private(path: Path) -> Ed25519PrivateKey:
    """A new key in a new file: ``O_EXCL`` refuses to replace an existing one."""
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(_open_new_private(path), "wb") as handle:
        handle.write(base64.b64encode(raw) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return key


def load_keys(keys_dir: Path) -> KeysFile:
    """``keys.json``; :class:`KeysError` when it is missing or invalid."""
    path = keys_file(keys_dir)
    try:
        return parse_document(KeysFile, path.read_bytes())
    except FileNotFoundError as exc:
        raise KeysError(f"{path} not found (run: keys init)") from exc
    except (OSError, DocumentError) as exc:
        raise KeysError(f"{path} unreadable: {exc}") from exc


def load_signing_key(keys_dir: Path, role: KeyRole) -> SigningKey:
    """The private key of ``role`` (the API reads ``api``, the agent ``agent``). It must be the
    one keys.json lists, or every request it signs would be refused as possible tampering."""
    private_key = _read_private(key_file(keys_dir, role))
    identifier = key_id(role, private_key.public_key())
    if identifier not in load_keys(keys_dir).public_keys(role):
        raise KeysError(f"{KEY_FILES[role]} ({identifier}) is not listed in {KEYS_FILE}")
    return SigningKey(role, identifier, private_key)


def init_keys(keys_dir: Path) -> KeysFile:
    """Create what is missing, change nothing that exists (idempotent; the installer runs it on
    every installation and upgrade). A missing private key is created; keys.json is (re)written
    only when it does not list exactly the two public keys, keeping its instance id."""
    publics: list[PublicKeyEntry] = []
    for role in ROLES:
        path = key_file(keys_dir, role)
        private_key = _read_private(path) if path.exists() else _create_private(path)
        raw = _raw_public(private_key.public_key())
        publics.append(
            PublicKeyEntry(id=key_id_for(role, raw), role=role, public=b64url_encode(raw))
        )
    path = keys_file(keys_dir)
    current: KeysFile | None = None
    if path.exists():
        try:
            current = parse_document(KeysFile, path.read_bytes())
        except DocumentError as exc:
            raise KeysError(
                f"{path} is invalid ({exc}); remove it and run keys init again"
            ) from exc
    if current is not None and current.keys == tuple(publics):
        return current
    wanted = KeysFile(instance=current.instance if current else new_id(), keys=tuple(publics))
    atomic_write_bytes(path, dump_document(wanted))
    return wanted
