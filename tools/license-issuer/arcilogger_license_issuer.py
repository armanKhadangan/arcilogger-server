"""ARCILOGGER licence issuer: the licensor's offline tool (licensing contract section 10).

Run it from a repository checkout on the owner's **offline** machine::

    uv run python tools/license-issuer/arcilogger_license_issuer.py keygen --key-id ID --out DIR
    uv run python tools/license-issuer/arcilogger_license_issuer.py issue --key DIR/ID.private.pem …
    uv run python tools/license-issuer/arcilogger_license_issuer.py inspect FILE.lic
    uv run python tools/license-issuer/arcilogger_license_issuer.py verify FILE.lic --committed-keys

It uses the product's own format code (``arcilogger_core.license``, ``arcilogger_core.canonical``)
so there is one definition of the licence and request formats. It is not shipped in the Windows
bundle and makes no network call. Owner guide: ``tools/license-issuer/README.md``.

* ``keygen`` makes an Ed25519 key pair: the private key as an encrypted PKCS#8 PEM (passphrase of
  at least 12 characters), the public key as the line to commit in
  ``core/src/arcilogger_core/license/keys.py``. It refuses a folder inside a git working tree.
* ``issue`` reads a customer's request code, refuses weak bindings unless
  ``--allow-weak-binding``, checks the registry for earlier licences of the same customer or
  server, signs the licence, verifies it with the public key, writes the ``.lic`` and appends a
  row to the registry (append-only CSV kept outside any repository).
* ``inspect`` prints a licence file or a request code without any trust decision.
* ``verify`` checks a licence against a public key, the private key file or the committed key
  set (and the committed revocation list), optionally against a server id and a date.

Exit codes: 0 done, 1 refused or invalid, 2 usage error. Texts: Turkish, then ``[English]``.

The commands are thin wrappers over plain functions with explicit parameters (``generate_key``,
``decode_request``, ``earlier_licences``, ``issue_inputs`` / ``prepare_issue`` /
``sign_licence`` / ``write_issued`` (together ``issue_license``), ``inspect_file``,
``verify_file``, ``read_registry``, ``create_registry``), which the graphical Lisans Yöneticisi
(``arcilogger_license_gui.py``) uses too: one implementation of every rule.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import io
import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from arcilogger_core.canonical import canonical_json
from arcilogger_core.license.codec import CROCKFORD_ALPHABET, b64url_decode_strict, b64url_encode
from arcilogger_core.license.document import (
    KEY_ID_PATTERN,
    LICENSE_ID_PATTERN,
    LICENSE_SCHEMA,
    MAX_LICENSE_BYTES,
    MAX_LIMIT,
    MAX_SERVER_IDS,
    LicenseDocument,
    PayloadError,
    load_license,
    parse_json_strict,
    sign_payload,
)
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.fingerprint import SERVER_ID_PATTERN
from arcilogger_core.license.modules import CORE_MODULE, MODULES
from arcilogger_core.license.request import (
    LicenseRequest,
    format_utc_seconds,
    one_line_text,
    parse_request_code,
    request_sha256,
)
from arcilogger_core.license.state import check_applicable
from arcilogger_core.license.trusted_time import TrustedTime, site_day_start

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_USAGE: Final = 2

MIN_PASSPHRASE: Final = 12
PRODUCT: Final = "ARCILOGGER"

#: The registry's header, in this order (contract 10). Nothing is ever rewritten.
REGISTRY_HEADER: Final = (
    "issued_at",
    "license_id",
    "licensee",
    "organization",
    "server_ids",
    "installation_id",
    "product_version",
    "platform",
    "binding",
    "modules",
    "max_sources",
    "max_users",
    "maintenance_until",
    "valid_until",
    "key_id",
    "file_sha256",
    "payload_sha256",
    "request_sha256",
    "supersedes",
    "note",
)
_FORMULA_START: Final = ("=", "+", "-", "@")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PRIVATE_KEY_NAME: Final = re.compile(r"(?P<key_id>[a-z0-9][a-z0-9-]{2,31})\.private\.pem")
_REGISTRY_KEYS: Final = {item.key: item for item in MODULES}


class ToolError(Exception):
    """A refusal (exit 1) or a usage error (exit 2) with Turkish and English texts."""

    def __init__(self, tr: str, en: str, *, exit_code: int = EXIT_REFUSED) -> None:
        super().__init__(en)
        self.tr = tr
        self.en = en
        self.exit_code = exit_code


def usage(tr: str, en: str) -> ToolError:
    return ToolError(tr, en, exit_code=EXIT_USAGE)


def say(tr: str, en: str = "") -> None:
    print(tr)
    if en:
        print(f"[{en}]")


def warn(tr: str, en: str) -> None:
    print(f"Uyarı: {tr}", file=sys.stderr)
    print(f"[Warning: {en}]", file=sys.stderr)


def _utf8_console() -> None:
    """Turkish names must print on a cp1252 Windows console too."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


# -- helpers --------------------------------------------------------------------------------------


def inside_git_tree(path: Path) -> bool:
    """``path`` or one of its parents holds ``.git`` (a repository or a worktree)."""
    resolved = path.expanduser().resolve()
    return any((candidate / ".git").exists() for candidate in (resolved, *resolved.parents))


def repository_texts(path: Path, what_tr: str, what_en: str) -> tuple[str, str]:
    """The Turkish and English text for ``path`` inside a git working tree."""
    return (
        f"{what_tr} bir git çalışma ağacının içinde ({path}): depo dışına taşıyın.",
        f"the {what_en} is inside a git working tree ({path}): move it out of the repository",
    )


def refuse_in_repository(path: Path, what_tr: str, what_en: str, *, folder: bool = False) -> None:
    """Refuse (exit 1) a file (or, with ``folder``, a folder) inside a git working tree."""
    if inside_git_tree(path if folder else path.parent):
        raise ToolError(*repository_texts(path, what_tr, what_en))


def check_passphrase(passphrase: str, *, new: bool) -> bytes:
    """A new passphrase has at least :data:`MIN_PASSPHRASE` characters; none may be empty."""
    if new and len(passphrase) < MIN_PASSPHRASE:
        raise usage(
            f"Parola en az {MIN_PASSPHRASE} karakter olmalı.",
            f"the passphrase must have at least {MIN_PASSPHRASE} characters",
        )
    if not passphrase:
        raise usage("Parola boş.", "the passphrase is empty")
    return passphrase.encode("utf-8")


def read_passphrase(path: Path | None, *, confirm: bool) -> bytes:
    """The first line of ``--passphrase-file``, else asked twice (``confirm``) or once."""
    if path is not None:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise usage(
                f"Parola dosyası okunamadı: {exc}", f"passphrase file unreadable: {exc}"
            ) from exc
        passphrase = lines[0] if lines else ""
    else:
        passphrase = getpass.getpass("Parola [passphrase]: ")
        if confirm and getpass.getpass("Parola, tekrar [passphrase again]: ") != passphrase:
            raise usage("Parolalar aynı değil.", "the passphrases differ")
    return check_passphrase(passphrase, new=confirm)


def key_id_of(pem: Path) -> str:
    match = _PRIVATE_KEY_NAME.fullmatch(pem.name)
    if match is None:
        raise usage(
            "Özel anahtar dosyasının adı <anahtar-kimliği>.private.pem olmalı (keygen'in "
            "yazdığı ad).",
            "the private key file must be named <key-id>.private.pem (as keygen writes it)",
        )
    return match.group("key_id")


def warn_if_in_repository(path: Path, what_tr: str, what_en: str) -> None:
    """A private key or a passphrase inside a git working tree is one commit away from a leak."""
    if inside_git_tree(path.parent):
        warn(*repository_texts(path, what_tr, what_en))


def read_key_file(pem: Path) -> bytes:
    try:
        return pem.read_bytes()
    except OSError as exc:
        raise usage(f"Anahtar dosyası okunamadı: {exc}", f"key file unreadable: {exc}") from exc


def private_key_from_pem(data: bytes, passphrase: bytes) -> Ed25519PrivateKey:
    """The Ed25519 key of an encrypted PKCS#8 PEM (exit 1 for a wrong passphrase)."""
    try:
        key = load_pem_private_key(data, password=passphrase)
    except (ValueError, TypeError) as exc:
        raise ToolError(
            "Özel anahtar açılamadı: parola yanlış ya da dosya bozuk.",
            "the private key cannot be opened: wrong passphrase or damaged file",
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ToolError("Anahtar bir Ed25519 anahtarı değil.", "the key is not an Ed25519 key")
    return key


def open_private_key(pem: Path, passphrase: str) -> Ed25519PrivateKey:
    """The key of ``pem`` (named ``<key-id>.private.pem``) opened with ``passphrase``."""
    key_id_of(pem)
    return private_key_from_pem(read_key_file(pem), check_passphrase(passphrase, new=False))


def load_private_key(pem: Path, passphrase_file: Path | None) -> Ed25519PrivateKey:
    """The command line's way: warnings, then the passphrase from a file or the console."""
    warn_if_in_repository(pem, "Özel anahtar", "private key")
    if passphrase_file is not None:
        warn_if_in_repository(passphrase_file, "Parola dosyası", "passphrase file")
    data = read_key_file(pem)
    passphrase = read_passphrase(passphrase_file, confirm=False)
    return private_key_from_pem(data, passphrase)


def public_b64(key: Ed25519PrivateKey) -> str:
    return b64url_encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def public_sha256(public: str) -> str:
    """SHA-256 (hex) of the raw 32-byte public key: the fingerprint keygen prints."""
    return hashlib.sha256(b64url_decode_strict(public, 32)).hexdigest()


def write_new_file(path: Path, data: bytes, *, mode: int, overwrite: bool = False) -> None:
    """Write ``data``; refuses an existing file unless ``overwrite``; flushed to disk."""
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    fd = os.open(path, flags, mode)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.name == "posix":
        path.chmod(mode)


def parse_day(value: str, option: str) -> date:
    if not _DATE.fullmatch(value):
        raise usage(f"{option} YYYY-AA-GG olmalı: {value!r}", f"{option} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise usage(f"{option} geçerli bir tarih değil.", f"{option} is not a valid date") from exc


def parse_limit(value: str, option: str) -> int | None:
    if value == "unlimited":
        return None
    if value.isascii() and value.isdigit() and 1 <= int(value) <= MAX_LIMIT:
        return int(value)
    raise usage(
        f"{option} 1-{MAX_LIMIT} arasında bir sayı ya da 'unlimited' olmalı.",
        f"{option} must be a number 1-{MAX_LIMIT} or 'unlimited'",
    )


def parse_modules(value: str) -> list[str]:
    """``core_5651`` plus the given keys, in registry order; unknown key / missing requires
    is a usage error."""
    wanted = {item.strip() for item in value.split(",") if item.strip()} | {CORE_MODULE}
    unknown = sorted(wanted - set(_REGISTRY_KEYS))
    if unknown:
        known = ", ".join(_REGISTRY_KEYS)
        raise usage(
            f"Bilinmeyen modül: {', '.join(unknown)} (bilinenler: {known}).",
            f"unknown module: {', '.join(unknown)} (known: {known})",
        )
    for key in sorted(wanted):
        missing = [required for required in _REGISTRY_KEYS[key].requires if required not in wanted]
        if missing:
            raise usage(
                f"{key} modülü {', '.join(missing)} modülünü gerektirir.",
                f"module {key} needs {', '.join(missing)}",
            )
    return [item.key for item in MODULES if item.key in wanted]


def check_licensee(licensee: str, option: str = "--licensee") -> str:
    """One line of 1-200 characters that canonical JSON can carry."""
    if not one_line_text(licensee, minimum=1, maximum=200):
        raise usage(
            f"{option} tek satır, 1-200 karakter olmalı (baş/son boşluk ve denetim karakteri yok).",
            f"{option} must be one line of 1-200 characters (no outer spaces, no control "
            "characters)",
        )
    try:
        canonical_json(licensee)
    except Exception as exc:
        raise usage(
            f"{option} geçersiz karakter içeriyor.", f"{option} has invalid characters"
        ) from exc
    return licensee


def check_server_ids(server_ids: Sequence[str]) -> list[str]:
    bad = [item for item in server_ids if not SERVER_ID_PATTERN.fullmatch(item)]
    if bad:
        raise usage(
            f"Geçersiz sunucu kimliği: {', '.join(bad)} (XXXX-XXXX-XXXX-XXXX).",
            f"invalid server id: {', '.join(bad)} (XXXX-XXXX-XXXX-XXXX)",
        )
    return list(server_ids)


def check_license_ids(license_ids: Sequence[str]) -> list[str]:
    bad = [item for item in license_ids if not LICENSE_ID_PATTERN.fullmatch(item)]
    if bad:
        raise usage(
            f"Geçersiz lisans kimliği: {', '.join(bad)}.", f"invalid licence id: {', '.join(bad)}"
        )
    return list(license_ids)


def check_note(note: str, option: str = "--note") -> str:
    if "\n" in note or "\r" in note:
        raise usage(f"{option} tek satır olmalı.", f"{option} must be one line")
    return note


def check_valid_until(valid_until: date | None, option: str = "--valid-until") -> date | None:
    today = datetime.now(UTC).date()
    if valid_until is not None and valid_until < today:
        raise usage(f"{option} bugünden önce olamaz.", f"{option} must not be before today")
    return valid_until


def check_out(out: Path, *, force: bool) -> Path:
    if not out.parent.is_dir():
        raise usage(f"{out.parent} klasörü yok.", f"the folder {out.parent} does not exist")
    if out.exists() and not force:
        raise ToolError(
            f"{out} zaten var: üzerine yazmak için --force verin.",
            f"{out} exists: pass --force to overwrite it",
        )
    return out


def binding_of(request: LicenseRequest) -> str:
    return "strong" if request.platform == "windows" and request.smbios else "weak"


def decode_request(text: str) -> LicenseRequest:
    """The request code (``ARCIREQ1-…``) as the customer's installation made it; raises
    :class:`LicenseError` ``request_malformed`` / ``request_checksum``."""
    return parse_request_code(text)


def read_request_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise usage(f"İstek dosyası okunamadı: {exc}", f"request file unreadable: {exc}") from exc


def read_request(args: argparse.Namespace) -> LicenseRequest:
    if args.request_file is not None:
        text = read_request_file(Path(args.request_file))
    else:
        text = args.request
    return decode_request(text)


def print_request(request: LicenseRequest) -> None:
    say("İstek kodu [request code]:")
    print(f"  server_id:       {request.server_id}")
    print(f"  installation_id: {request.installation_id}")
    print(f"  product_version: {request.product_version}")
    print(f"  organization:    {request.organization}")
    print(f"  platform:        {request.platform}")
    print(f"  smbios:          {'true' if request.smbios else 'false'}")
    print(f"  created_at:      {format_utc_seconds(request.created_at)}")
    print(f"  binding:         {binding_of(request)}")
    print(f"  request_sha256:  {request_sha256(request)}")
    if not request.organization_set:
        warn("Kuruluş adı ayarlanmamış.", "organisation name not set")


# -- registry -------------------------------------------------------------------------------------


def _escape(cell: str) -> str:
    return "'" + cell if cell.startswith(_FORMULA_START) else cell


def _unescape(cell: str) -> str:
    return cell[1:] if cell[:1] == "'" and cell[1:2] in _FORMULA_START else cell


@dataclass(frozen=True)
class Registry:
    path: Path
    rows: tuple[Mapping[str, str], ...]
    exists: bool


_REGISTRY_IN_REPOSITORY: Final = (
    "Kayıt defteri (registry) bir git çalışma ağacının içinde olamaz: depo dışında tutun.",
    "the registry must not be inside a git working tree: keep it outside the repository",
)


def read_registry(path: Path) -> Registry:
    if inside_git_tree(path.parent):
        raise ToolError(*_REGISTRY_IN_REPOSITORY)
    if not path.exists() or path.stat().st_size == 0:
        return Registry(path=path, rows=(), exists=False)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(
            f"Kayıt defteri okunamadı: {exc}", f"the registry cannot be read: {exc}"
        ) from exc
    records = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    if not records or tuple(records[0]) != REGISTRY_HEADER:
        raise ToolError(
            "Kayıt defterinin başlık satırı beklenen başlık değil: hiçbir şey yazılmadı.",
            "the registry's header is not the expected header: nothing was written",
        )
    rows = []
    for number, record in enumerate(records[1:], start=2):
        if not record:
            continue
        if len(record) != len(REGISTRY_HEADER):
            raise ToolError(
                f"Kayıt defterinin {number}. satırı bozuk: hiçbir şey yazılmadı.",
                f"registry line {number} is damaged: nothing was written",
            )
        rows.append(dict(zip(REGISTRY_HEADER, (_unescape(cell) for cell in record), strict=True)))
    return Registry(path=path, rows=tuple(rows), exists=True)


def _registry_lines(rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def create_registry(path: Path) -> Registry:
    """A new registry holding only the header (UTF-8 with BOM, CRLF), as the first ``issue``
    writes it; refuses a non-empty existing file and a path inside a git working tree."""
    if inside_git_tree(path.parent):
        raise ToolError(*_REGISTRY_IN_REPOSITORY)
    empty = path.exists() and path.stat().st_size == 0
    if path.exists() and not empty:
        raise ToolError(
            f"{path} zaten var: üzerine yazılmaz.", f"{path} exists: it is never overwritten"
        )
    text = "\ufeff" + _registry_lines([REGISTRY_HEADER])
    write_new_file(path, text.encode("utf-8"), mode=0o644, overwrite=empty)
    return read_registry(path)


def _same_text(left: str, right: str) -> bool:
    return bool(left.strip()) and left.strip().casefold() == right.strip().casefold()


def earlier_rows(
    registry: Registry, *, licensee: str, request: LicenseRequest, server_ids: Sequence[str]
) -> list[Mapping[str, str]]:
    """Rows of the same licensee, organisation, installation id or a common server id."""
    found = []
    for row in registry.rows:
        common = set(row["server_ids"].split()) & set(server_ids)
        if (
            _same_text(row["licensee"], licensee)
            or _same_text(row["organization"], request.organization)
            or (row["installation_id"] and row["installation_id"] == request.installation_id)
            or common
        ):
            found.append(row)
    return found


def earlier_licences(
    registry_path: Path,
    request: LicenseRequest,
    licensee: str,
    extra_server_ids: Sequence[str] = (),
) -> list[Mapping[str, str]]:
    """The registry rows that make a new licence for ``request`` need a reason (supersedes or
    additional server): same licensee, organisation, installation id or a common server id."""
    server_ids = list(dict.fromkeys([request.server_id, *extra_server_ids]))
    registry = read_registry(registry_path)
    return earlier_rows(registry, licensee=licensee, request=request, server_ids=server_ids)


def append_registry_row(registry: Registry, row: Mapping[str, str]) -> None:
    lines = [REGISTRY_HEADER] if not registry.exists else []
    text = _registry_lines([*lines, [_escape(row[name]) for name in REGISTRY_HEADER]])
    if not registry.exists:
        text = "\ufeff" + text
    elif not registry.path.read_bytes().endswith(b"\n"):
        text = "\r\n" + text
    with registry.path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


# -- shared operations (the command line and the Lisans Yöneticisi) ------------------------------


@dataclass(frozen=True)
class KeyResult:
    key_id: str
    private_path: Path
    public_path: Path
    public_key: str  # base64url, 43 characters
    public_sha256: str  # hex SHA-256 of the raw public key

    @property
    def line(self) -> str:
        """The line for ``TRUSTED_KEYS`` in ``keys.py`` (the content of ``.public.txt``)."""
        return f'"{self.key_id}": "{self.public_key}",'

    @property
    def verify_option(self) -> str:
        """The ``verify`` option, ready to paste: written with ``=``, since about one key in 64
        starts with ``-`` and argparse takes ``--public-key -…`` for a missing value."""
        return f"--public-key={self.public_key}"


def check_key_id(key_id: str) -> str:
    if not KEY_ID_PATTERN.fullmatch(key_id) or key_id.startswith("test-"):
        raise usage(
            "Anahtar kimliği ^[a-z0-9][a-z0-9-]{2,31}$ biçiminde olmalı ve test- ile başlamamalı.",
            "the key id must match ^[a-z0-9][a-z0-9-]{2,31}$ and must not start with test-",
        )
    return key_id


def keygen_paths(key_id: str, out_dir: Path) -> tuple[Path, Path, Path]:
    """The folder, private and public file of a new key pair; refuses a bad key id, a folder
    inside a git working tree and existing files."""
    check_key_id(key_id)
    out = Path(out_dir).expanduser().resolve()
    if inside_git_tree(out):
        raise ToolError(
            f"{out} bir git çalışma ağacının içinde: özel anahtar depoya hiç girmemeli. Çevrimdışı "
            "bir klasör seçin.",
            f"{out} is inside a git working tree: the private key must never enter a repository. "
            "Choose an offline folder.",
        )
    private_path = out / f"{key_id}.private.pem"
    public_path = out / f"{key_id}.public.txt"
    for path in (private_path, public_path):
        if path.exists():
            raise ToolError(
                f"{path} zaten var: üzerine yazılmaz.", f"{path} exists: it is never overwritten"
            )
    return out, private_path, public_path


def generate_key(key_id: str, out_dir: Path, passphrase: str | bytes) -> KeyResult:
    """A new Ed25519 key pair: ``<key-id>.private.pem`` (encrypted PKCS#8, mode 0600 on POSIX)
    and ``<key-id>.public.txt`` (the line for ``keys.py``). Never overwrites a file."""
    out, private_path, public_path = keygen_paths(key_id, out_dir)
    text = passphrase.decode("utf-8") if isinstance(passphrase, bytes) else passphrase
    secret = check_passphrase(text, new=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(secret))
    public = public_b64(key)
    del key, secret
    out.mkdir(parents=True, exist_ok=True)
    write_new_file(private_path, pem, mode=0o600)
    write_new_file(public_path, f'"{key_id}": "{public}",\n'.encode("ascii"), mode=0o644)
    return KeyResult(
        key_id=key_id,
        private_path=private_path,
        public_path=public_path,
        public_key=public,
        public_sha256=public_sha256(public),
    )


@dataclass(frozen=True)
class IssueInputs:
    modules: list[str]
    max_sources: int | None
    max_users: int | None
    maintenance_until: date
    valid_until: date | None
    extra_server_ids: list[str]
    supersedes: list[str]
    licensee: str
    key_id: str
    registry_path: Path
    out: Path | None
    note: str = ""
    additional_server: bool = False
    allow_weak_binding: bool = False
    force: bool = False


def issue_inputs(
    *,
    modules: str,
    max_sources: str,
    max_users: str,
    key_path: Path,
    licensee: str,
    maintenance_until: str,
    registry_path: Path,
    out: Path | None,
    valid_until: str | None = None,
    extra_server_ids: Sequence[str] = (),
    supersedes: Sequence[str] = (),
    note: str = "",
    additional_server: bool = False,
    allow_weak_binding: bool = False,
    force: bool = False,
) -> IssueInputs:
    """Every value of ``issue`` checked, in the command line's order (usage errors, exit 2)."""
    module_keys = parse_modules(modules)
    sources = parse_limit(max_sources, "--max-sources")
    users = parse_limit(max_users, "--max-users")
    key_id = key_id_of(Path(key_path))
    check_licensee(licensee)
    extra = check_server_ids(extra_server_ids)
    replaced = check_license_ids(supersedes)
    check_note(note)
    maintenance = parse_day(maintenance_until, "--maintenance-until")
    valid = parse_day(valid_until, "--valid-until") if valid_until else None
    check_valid_until(valid)
    if out is not None:
        check_out(Path(out), force=force)
    return IssueInputs(
        modules=module_keys,
        max_sources=sources,
        max_users=users,
        maintenance_until=maintenance,
        valid_until=valid,
        extra_server_ids=extra,
        supersedes=replaced,
        licensee=licensee,
        key_id=key_id,
        registry_path=Path(registry_path),
        out=Path(out) if out is not None else None,
        note=note,
        additional_server=additional_server,
        allow_weak_binding=allow_weak_binding,
        force=force,
    )


def _issue_inputs(args: argparse.Namespace) -> IssueInputs:
    return issue_inputs(
        modules=args.modules,
        max_sources=args.max_sources,
        max_users=args.max_users,
        key_path=Path(args.key),
        licensee=args.licensee,
        maintenance_until=args.maintenance_until,
        valid_until=args.valid_until,
        extra_server_ids=args.extra_server_id or [],
        supersedes=args.supersedes or [],
        note=args.note or "",
        registry_path=Path(args.registry),
        out=Path(args.out),
        additional_server=args.additional_server,
        allow_weak_binding=args.allow_weak_binding,
        force=args.force,
    )


def _new_license_id(now: datetime) -> str:
    random_part = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(8))
    return f"ARCI-L-{now.strftime('%Y%m%d')}-{random_part}"


def _print_rows(rows: Sequence[Mapping[str, str]]) -> None:
    say("Önceki lisanslar [earlier licences]:")
    for row in rows:
        print(
            f"  {row['issued_at']}  {row['license_id']}  {row['licensee']}  "
            f"servers={row['server_ids']}  installation={row['installation_id']}  "
            f"supersedes={row['supersedes'] or '-'}"
        )


def check_binding(request: LicenseRequest, *, allow_weak: bool) -> str:
    """``strong``, or ``weak`` when allowed on purpose; a weak request is refused otherwise."""
    binding = binding_of(request)
    if binding == "weak" and not allow_weak:
        raise ToolError(
            "Zayıf bağlama: istek Windows'tan SMBIOS UUID ile gelmiyor (platform "
            f"{request.platform}, smbios {'true' if request.smbios else 'false'}). Sunucu "
            "kimliği tek bir düzenlemeyle başka makineye taşınabilir. Bilerek vermek için "
            "--allow-weak-binding kullanın.",
            "weak binding: the request does not come from Windows with an SMBIOS UUID (platform "
            f"{request.platform}, smbios {'true' if request.smbios else 'false'}). The server id "
            "can be copied to another machine with one edit. Use --allow-weak-binding to issue "
            "it on purpose.",
        )
    return binding


@dataclass(frozen=True)
class IssuePlan:
    """A checked issue: everything is decided except the signature and the file name."""

    inputs: IssueInputs
    request: LicenseRequest
    registry: Registry
    server_ids: tuple[str, ...]
    earlier: tuple[Mapping[str, str], ...]
    binding: str


def prepare_issue(
    inputs: IssueInputs,
    request: LicenseRequest,
    registry: Registry | None = None,
    *,
    on_earlier: Callable[[Sequence[Mapping[str, str]]], None] | None = None,
) -> IssuePlan:
    """The checks between the request and the signature: the weak-binding refusal, the number
    of server ids and the earlier-licence rule (``supersedes`` or ``additional_server`` when the
    registry has a licence of the same customer or server; ``supersedes`` must be registered).
    ``on_earlier`` sees the earlier rows before the rule is applied."""
    if registry is None:
        registry = read_registry(inputs.registry_path)
    binding = check_binding(request, allow_weak=inputs.allow_weak_binding)
    server_ids = list(dict.fromkeys([request.server_id, *inputs.extra_server_ids]))
    if len(server_ids) > MAX_SERVER_IDS:
        raise usage(
            f"En çok {MAX_SERVER_IDS} sunucu kimliği.", f"at most {MAX_SERVER_IDS} server ids"
        )
    earlier = earlier_rows(
        registry, licensee=inputs.licensee, request=request, server_ids=server_ids
    )
    if earlier:
        if on_earlier is not None:
            on_earlier(earlier)
        if not inputs.supersedes and not inputs.additional_server:
            raise ToolError(
                "Bu müşteri ya da sunucu için daha önce lisans düzenlenmiş. Yerine geçiyorsa "
                "--supersedes LİSANS_KİMLİĞİ, bilerek ikinci bir sunucu ise --additional-server "
                "verin.",
                "a licence was issued for this customer or server before. Pass --supersedes "
                "LICENSE_ID for a replacement, or --additional-server for a second server sold on "
                "purpose.",
            )
    known_ids = {row["license_id"] for row in registry.rows}
    unknown = [item for item in inputs.supersedes if item not in known_ids]
    if unknown:
        raise ToolError(
            f"--supersedes kayıt defterinde olmayan lisans: {', '.join(unknown)}.",
            f"--supersedes names a licence not in the registry: {', '.join(unknown)}",
        )
    return IssuePlan(
        inputs=inputs,
        request=request,
        registry=registry,
        server_ids=tuple(server_ids),
        earlier=tuple(earlier),
        binding=binding,
    )


@dataclass(frozen=True)
class SignedLicence:
    """A signed and verified licence that is not written yet."""

    plan: IssuePlan
    data: bytes
    document: LicenseDocument

    @property
    def license_id(self) -> str:
        return self.document.payload.license_id

    def registry_row(self) -> dict[str, str]:
        inputs, request, document = self.plan.inputs, self.plan.request, self.document
        if inputs.supersedes:
            supersedes = " ".join(inputs.supersedes)
        else:
            supersedes = "additional" if inputs.additional_server else ""
        return {
            "issued_at": format_utc_seconds(document.payload.issued_at),
            "license_id": document.payload.license_id,
            "licensee": document.payload.licensee,
            "organization": request.organization,
            "server_ids": " ".join(document.payload.server_ids),
            "installation_id": request.installation_id,
            "product_version": request.product_version,
            "platform": request.platform,
            "binding": binding_of(request),
            "modules": " ".join(document.payload.modules),
            "max_sources": "" if inputs.max_sources is None else str(inputs.max_sources),
            "max_users": "" if inputs.max_users is None else str(inputs.max_users),
            "maintenance_until": inputs.maintenance_until.isoformat(),
            "valid_until": inputs.valid_until.isoformat() if inputs.valid_until else "",
            "key_id": inputs.key_id,
            "file_sha256": document.sha256,
            "payload_sha256": document.payload_sha256,
            "request_sha256": request_sha256(request),
            "supersedes": supersedes,
            "note": inputs.note,
        }


def sign_licence(
    plan: IssuePlan, private_key: Ed25519PrivateKey, *, now: datetime | None = None
) -> SignedLicence:
    """Build the payload, sign it and verify the result with the key's own public key."""
    inputs, request = plan.inputs, plan.request
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    known_ids = {row["license_id"] for row in plan.registry.rows}
    license_id = _new_license_id(moment)
    while license_id in known_ids:
        license_id = _new_license_id(moment)
    payload: dict[str, Any] = {
        "schema": LICENSE_SCHEMA,
        "product": PRODUCT,
        "license_id": license_id,
        "licensee": inputs.licensee,
        "issued_at": format_utc_seconds(moment),
        "server_ids": list(plan.server_ids),
        "installation_id": request.installation_id,
        "modules": inputs.modules,
        "limits": {"log_sources": inputs.max_sources, "users": inputs.max_users},
        "maintenance_until": inputs.maintenance_until.isoformat(),
        "valid_until": inputs.valid_until.isoformat() if inputs.valid_until else None,
        "request_sha256": request_sha256(request),
    }
    try:
        data = sign_payload(payload, private_key, inputs.key_id)
    except PayloadError as exc:
        raise usage(f"Lisans içeriği geçersiz: {exc}", f"invalid licence content: {exc}") from exc
    document = load_license(data, keys={inputs.key_id: public_b64(private_key)})
    return SignedLicence(plan=plan, data=data, document=document)


@dataclass(frozen=True)
class IssueResult:
    path: Path
    document: LicenseDocument
    row: Mapping[str, str]
    supersedes: tuple[str, ...]

    @property
    def revoked_lines(self) -> tuple[str, ...]:
        """The lines for ``REVOKED_LICENSE_IDS`` in ``revoked.py``, one per replaced licence."""
        return tuple(f'"{item}",' for item in self.supersedes)


def write_issued(signed: SignedLicence, out: Path, *, force: bool = False) -> IssueResult:
    """Append the registry row, then write the licence file (a licence file never exists
    without its record)."""
    if out.exists() and not force:
        raise ToolError(
            f"{out} zaten var: üzerine yazmak için --force verin.",
            f"{out} exists: pass --force to overwrite it",
        )
    registry = read_registry(signed.plan.registry.path)
    if any(row["license_id"] == signed.license_id for row in registry.rows):
        raise ToolError(
            f"Lisans kimliği kayıt defterinde zaten var: {signed.license_id}.",
            f"the licence id is in the registry already: {signed.license_id}",
        )
    row = signed.registry_row()
    append_registry_row(registry, row)
    write_new_file(out, signed.data, mode=0o644, overwrite=force)
    return IssueResult(
        path=out,
        document=signed.document,
        row=row,
        supersedes=tuple(signed.plan.inputs.supersedes),
    )


def issue_license(
    inputs: IssueInputs,
    private_key: Ed25519PrivateKey,
    *,
    request: LicenseRequest,
    registry: Registry | None = None,
    now: datetime | None = None,
) -> IssueResult:
    """The whole ``issue``: checks, signature, verification, registry row, licence file."""
    if inputs.out is None:
        raise usage("Lisans dosyasının yolu yok.", "no path for the licence file")
    plan = prepare_issue(inputs, request, registry)
    return write_issued(sign_licence(plan, private_key, now=now), inputs.out, force=inputs.force)


@dataclass(frozen=True)
class InspectResult:
    """A licence file's content without any trust decision."""

    path: Path | None
    size: int
    sha256: str
    payload_sha256: str
    signature: Mapping[str, Any] | None
    payload: Mapping[str, Any] | None


def inspect_licence(data: bytes, path: Path | None = None) -> InspectResult:
    try:
        text = data.decode("utf-8-sig")
        document = parse_json_strict(text)
        payload = document["payload"]
        signature = document["signature"]
        payload_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
    except Exception as exc:
        raise ToolError(
            f"Dosya JSON olarak okunamadı: {type(exc).__name__}: {exc}",
            f"the file cannot be read as JSON: {type(exc).__name__}: {exc}",
        ) from exc
    return InspectResult(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        payload_sha256=payload_hash,
        signature=signature if isinstance(signature, dict) else None,
        payload=payload if isinstance(payload, dict) else None,
    )


def read_licence_file(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise usage(f"Dosya okunamadı: {exc}", f"file unreadable: {exc}") from exc
    return data


def inspect_file(path: Path) -> InspectResult:
    return inspect_licence(read_licence_file(path), path)


def check_server_id(server_id: str, option: str = "--server-id") -> str:
    if not SERVER_ID_PATTERN.fullmatch(server_id):
        raise usage(
            f"{option} XXXX-XXXX-XXXX-XXXX olmalı.", f"{option} must be XXXX-XXXX-XXXX-XXXX"
        )
    return server_id


def check_public_key(public_key: str, option: str = "--public-key") -> str:
    try:
        b64url_decode_strict(public_key, 32)
    except ValueError as exc:
        raise usage(
            f"{option} 43 karakterlik base64url bir Ed25519 anahtarı olmalı.",
            f"{option} must be a 43-character base64url Ed25519 key",
        ) from exc
    return public_key


def keys_for_public_key(data: bytes, public_key: str) -> Mapping[str, str]:
    """``{key id of the file: public_key}`` (empty when the file names no readable key id)."""
    try:
        key_id = parse_json_strict(data.decode("utf-8-sig"))["signature"]["key_id"]
    except Exception:
        return {}
    return {key_id: public_key} if isinstance(key_id, str) else {}


@dataclass(frozen=True)
class VerifyResult:
    document: LicenseDocument
    checked: tuple[str, ...]


def verify_licence(
    data: bytes,
    *,
    keys: Mapping[str, str] | None,
    server_id: str | None = None,
    at: date | None = None,
) -> VerifyResult:
    """Signature (``keys``; None: the committed key set), the committed revocation list and,
    against ``server_id`` (else the licence's first) and ``at``, the server, ``valid_until``
    and maintenance. Raises :class:`LicenseError` with the product's code."""
    document = load_license(data, keys=keys)
    payload = document.payload
    moment = site_day_start(at) if at is not None else payload.issued_at
    trusted = TrustedTime(
        now=moment,
        system=moment,
        tsa=None,
        tsa_day=None,
        vault_day_end=None,
        release_floor=moment,
        license_issued=None,
    )
    check_applicable(
        document,
        server_id=server_id or payload.server_ids[0],
        server_id_error=None,
        trusted=trusted,
        release_date=at if at is not None else date.min,
    )
    checked = ["signature", "revocation"]
    if server_id:
        checked.append(f"server {server_id}")
    if at is not None:
        checked.append(f"valid_until and maintenance at {at.isoformat()}")
    return VerifyResult(document=document, checked=tuple(checked))


def verify_file(
    path: Path,
    *,
    public_key: str | None = None,
    private_key: Ed25519PrivateKey | None = None,  # gitleaks:allow (a type name, no key)
    committed: bool = False,
    server_id: str | None = None,
    at: date | None = None,
) -> VerifyResult:
    """:func:`verify_licence` of a file with one of: a public key, the private key's public
    key, or the committed key set."""
    if server_id is not None:
        check_server_id(server_id)
    data = read_licence_file(path)
    keys: Mapping[str, str] | None
    if committed:
        keys = None
    elif public_key is not None:
        keys = keys_for_public_key(data, check_public_key(public_key))
    elif private_key is not None:
        keys = keys_for_public_key(data, public_b64(private_key))
    else:
        raise usage("Doğrulama için bir anahtar seçin.", "choose a key for the verification")
    return verify_licence(data, keys=keys, server_id=server_id, at=at)


# -- commands -------------------------------------------------------------------------------------


def cmd_keygen(args: argparse.Namespace) -> int:
    key_id: str = args.key_id
    keygen_paths(key_id, Path(args.out))
    passphrase = read_passphrase(args.passphrase_file, confirm=True)
    result = generate_key(key_id, Path(args.out), passphrase)
    say(
        f"Anahtar çifti oluşturuldu: {key_id}",
        f"key pair created: {key_id}",
    )
    print(f"  {result.private_path}  (şifreli özel anahtar [encrypted private key])")
    print(f"  {result.public_path}  (keys.py satırı [line for keys.py])")
    print(f"  public key: {result.public_key}")
    print(f"  verify: {result.verify_option}")
    print(f"  public key sha256: {result.public_sha256}")
    say(
        "Özel anahtarı iki ayrı çevrimdışı ortama yedekleyin. Yalnızca .public.txt satırını "
        "core/src/arcilogger_core/license/keys.py dosyasına eklenmek üzere gönderin.",
        "Back up the private key on two separate offline media. Send only the .public.txt line, "
        "to be added to core/src/arcilogger_core/license/keys.py.",
    )
    return EXIT_OK


def cmd_issue(args: argparse.Namespace) -> int:
    inputs = _issue_inputs(args)
    registry = read_registry(inputs.registry_path)
    request = read_request(args)
    print_request(request)
    plan = prepare_issue(inputs, request, registry, on_earlier=_print_rows)
    private_key = load_private_key(Path(args.key), args.passphrase_file)
    signed = sign_licence(plan, private_key)
    del private_key
    result = write_issued(signed, Path(args.out), force=args.force)
    say(
        f"Lisans düzenlendi ve imzası doğrulandı: {result.path}",
        f"licence issued and its signature verified: {result.path}",
    )
    print_document(result.document)
    print(f"  file sha256:     {result.document.sha256}")
    if result.supersedes:
        say(
            "revoked.py dosyasına eklenecek satırlar (yeni sürümle yayımlanır; yükseltilen "
            "kurulumlarda geçerli olur):",
            "lines to add to revoked.py (released with the next version; effective on "
            "installations that upgrade):",
        )
        for line in result.revoked_lines:
            print(f"    {line}")
    return EXIT_OK


def print_document(document: LicenseDocument) -> None:
    payload = document.payload
    print(f"  license_id:      {payload.license_id}")
    print(f"  licensee:        {payload.licensee}")
    print(f"  issued_at:       {format_utc_seconds(payload.issued_at)}")
    print(f"  server_ids:      {' '.join(payload.server_ids)}")
    print(f"  installation_id: {payload.installation_id or '-'}")
    print(f"  modules:         {' '.join(payload.modules)}")
    print(f"  log_sources:     {payload.log_sources or 'unlimited'}")
    print(f"  users:           {payload.users or 'unlimited'}")
    print(f"  maintenance:     {payload.maintenance_until.isoformat()}")
    print(f"  valid_until:     {payload.valid_until.isoformat() if payload.valid_until else '-'}")
    print(f"  key_id:          {document.key_id}")
    print(f"  payload sha256:  {document.payload_sha256}")


def cmd_inspect(args: argparse.Namespace) -> int:
    if args.request is not None or args.request_file is not None:
        print_request(read_request(args))
        return EXIT_OK
    if args.file is None:
        raise usage(
            "Bir .lic dosyası ya da --request/--request-file verin.",
            "give a .lic file or --request/--request-file",
        )
    path = Path(args.file)
    data = read_licence_file(path)
    say(
        "İçerik (imza denetlenmeden) [content, signature not checked]:",
    )
    print(f"  file:            {path}")
    print(f"  size:            {len(data)} bytes (en çok [at most] {MAX_LICENSE_BYTES})")
    print(f"  file sha256:     {hashlib.sha256(data).hexdigest()}")
    result = inspect_licence(data, path)
    print(f"  payload sha256:  {result.payload_sha256}")
    if result.signature is not None:
        print(f"  signature alg:   {result.signature.get('alg')}")
        print(f"  signature key:   {result.signature.get('key_id')}")
    if result.payload is not None:
        for key, value in result.payload.items():
            print(f"  {key + ':':<17}{value}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    if args.server_id is not None:
        check_server_id(args.server_id)
    at = parse_day(args.at, "--at") if args.at else None
    data = read_licence_file(Path(args.file))
    keys: Mapping[str, str] | None
    if args.committed_keys:
        keys = None
    elif args.public_key is not None:
        keys = keys_for_public_key(data, check_public_key(args.public_key))
    else:
        keys = keys_for_public_key(
            data, public_b64(load_private_key(Path(args.key), args.passphrase_file))
        )
    result = verify_licence(data, keys=keys, server_id=args.server_id, at=at)
    say("Lisans geçerli.", "the licence is valid")
    print_document(result.document)
    print(f"  checked:         {', '.join(result.checked)}")
    return EXIT_OK


# -- command line ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcilogger_license_issuer.py",
        description=(
            "ARCILOGGER lisans düzenleyici [licence issuer], çevrimdışı [offline]: "
            "tools/license-issuer/README.md"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    keygen = commands.add_parser("keygen", help="new Ed25519 key pair")
    keygen.add_argument("--key-id", required=True, help="e.g. arci-2026a")
    keygen.add_argument("--out", required=True, help="offline folder (never in a repository)")
    keygen.add_argument("--passphrase-file", type=Path, help="first line = passphrase")

    issue = commands.add_parser("issue", help="sign a licence for a request code")
    issue.add_argument("--key", required=True, help="<key-id>.private.pem written by keygen")
    request = issue.add_mutually_exclusive_group(required=True)
    request.add_argument("--request", help="the request code ARCIREQ1-…")
    request.add_argument("--request-file", help="file holding the request code")
    issue.add_argument("--licensee", required=True, help="licensee name shown on the licence")
    issue.add_argument("--modules", required=True, help="comma separated; core_5651 is added")
    issue.add_argument("--max-sources", required=True, help="number or 'unlimited'")
    issue.add_argument("--max-users", required=True, help="number or 'unlimited'")
    issue.add_argument("--maintenance-until", required=True, help="YYYY-MM-DD (inclusive)")
    issue.add_argument("--valid-until", help="YYYY-MM-DD: demo / time-limited licence only")
    issue.add_argument("--extra-server-id", action="append", help="another allowed server id")
    issue.add_argument("--registry", required=True, help="append-only CSV, outside any repo")
    issue.add_argument("--out", required=True, help="the .lic file to write")
    issue.add_argument("--note", help="note for the registry")
    issue.add_argument("--passphrase-file", type=Path, help="first line = passphrase")
    issue.add_argument("--force", action="store_true", help="overwrite --out")
    reason = issue.add_mutually_exclusive_group()
    reason.add_argument("--supersedes", action="append", help="licence id this one replaces")
    reason.add_argument(
        "--additional-server", action="store_true", help="a second server sold on purpose"
    )
    issue.add_argument(
        "--allow-weak-binding",
        action="store_true",
        help="issue for Linux or Windows without SMBIOS UUID",
    )

    inspect = commands.add_parser("inspect", help="show a licence or request, no trust decision")
    inspect.add_argument("file", nargs="?", help="FILE.lic")
    source = inspect.add_mutually_exclusive_group()
    source.add_argument("--request", help="a request code")
    source.add_argument("--request-file", help="file holding a request code")

    verify = commands.add_parser("verify", help="verify a licence file")
    verify.add_argument("file", help="FILE.lic")
    key = verify.add_mutually_exclusive_group(required=True)
    key.add_argument("--public-key", help="base64url public key (43 characters)")
    key.add_argument("--key", help="<key-id>.private.pem")
    key.add_argument("--committed-keys", action="store_true", help="keys.py of this checkout")
    verify.add_argument("--passphrase-file", type=Path, help="first line = passphrase (--key)")
    verify.add_argument("--server-id", help="check that this server is allowed")
    verify.add_argument("--at", help="YYYY-MM-DD: check valid_until and maintenance at this day")
    return parser


HANDLERS: Final = {
    "keygen": cmd_keygen,
    "issue": cmd_issue,
    "inspect": cmd_inspect,
    "verify": cmd_verify,
}


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except ToolError as exc:
        print(
            ("Hata: " if exc.exit_code == EXIT_REFUSED else "Kullanım: ") + exc.tr, file=sys.stderr
        )
        print(
            f"[{'Error' if exc.exit_code == EXIT_REFUSED else 'Usage'}: {exc.en}]", file=sys.stderr
        )
        return exc.exit_code
    except LicenseError as exc:
        print(f"{exc.code}: {exc.message_tr}", file=sys.stderr)
        print(f"[{exc.message_en}]", file=sys.stderr)
        if exc.reason:
            print(f"  ({exc.reason})", file=sys.stderr)
        return EXIT_REFUSED
    except OSError as exc:
        print(f"Hata: dosya işlemi başarısız: {exc}", file=sys.stderr)
        print(f"[Error: file operation failed: {exc}]", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
