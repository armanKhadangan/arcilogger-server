"""Zamane clients uploaded through the browser (ADR-0014; contract 2.6 tsa, Day 0 note D0-16).

An upload is a ``.jar`` (a ZIP with ``META-INF/MANIFEST.MF``) or a ``.zip`` as Kamu SM may
distribute it, holding exactly one ``tss-client-console-*.jar``. :func:`inspect_client` checks it
without extracting anything to disk:

* the SHA-256 of the uploaded file is computed **before** the file is opened as an archive;
* a ``.zip``: at most 100 entries, no absolute path or ``..`` in any name, the client member at
  most 30 MiB uncompressed and at most 20 times its compressed size, and the member itself a jar;
* the release is looked up in :mod:`arcilogger_core.tsa.zamane_releases` by the SHA-256 of the jar
  or of the archive.

Only the client's bytes are kept (:func:`client_bytes`): the agent stages them as
``zamane\\web\\<jar sha256>.jar``, which no task ever runs. ApplySystem promotes a known release;
any other waits for ``approve-jar`` on the console (D0-16).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

from arcilogger_core.admin.contract import UPLOAD_LIMITS
from arcilogger_core.tsa.zamane_releases import find_release

ARCHIVE_INVALID: Final = "zamane_archive_invalid"
MANIFEST: Final = "META-INF/MANIFEST.MF"
CLIENT_PATTERN: Final = "tss-client-console-*.jar"
MAX_ENTRIES: Final = 100
MAX_MEMBER_BYTES: Final = 30 * 1024 * 1024
MAX_RATIO: Final = 20
_CHUNK: Final = 1024 * 1024


@dataclass(frozen=True)
class ZamaneInspection:
    ok: bool
    #: SHA-256 of the uploaded file.
    sha256: str
    kind: Literal["jar", "zip"] | None = None
    #: The client's file name inside a .zip (None for a bare jar).
    jar_name: str | None = None
    #: SHA-256 of the jar that would run.
    jar_sha256: str | None = None
    #: The known release (its version), or None.
    release: str | None = None
    code: str | None = None
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.release is not None


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _unsafe_name(name: str) -> bool:
    if name.startswith(("/", "\\")) or PureWindowsPath(name).drive:
        return True
    return ".." in PurePosixPath(name.replace("\\", "/")).parts


def _is_jar(archive: zipfile.ZipFile) -> bool:
    return MANIFEST in archive.namelist()


def _member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """The one client member of a distribution .zip (``ValueError`` with the reason)."""
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"{len(entries)} entries, at most {MAX_ENTRIES}")
    if any(_unsafe_name(entry.filename) for entry in entries):
        raise ValueError("an entry with an absolute path or '..'")
    clients = [
        entry
        for entry in entries
        if not entry.is_dir() and fnmatch(PurePosixPath(entry.filename).name, CLIENT_PATTERN)
    ]
    if len(clients) != 1:
        raise ValueError(f"{len(clients)} {CLIENT_PATTERN} files, exactly one is needed")
    member = clients[0]
    if member.file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"the client is larger than {MAX_MEMBER_BYTES} bytes uncompressed")
    if member.file_size > MAX_RATIO * max(member.compress_size, 1):
        raise ValueError(f"the client is compressed more than {MAX_RATIO} times")
    return member


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    with archive.open(member) as handle:
        data = handle.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:  # the header understated the size
        raise ValueError(f"the client is larger than {MAX_MEMBER_BYTES} bytes uncompressed")
    return data


def _check_jar(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as jar:
            manifest = _is_jar(jar)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError(f"the client is not a jar: {exc}") from exc
    if not manifest:
        raise ValueError(f"the client has no {MANIFEST}")


def inspect_client(path: Path) -> ZamaneInspection:
    """Whether the file at ``path`` is a Zamane client (see the module text)."""
    digest = _sha256(path)  # before anything parses the file

    def refuse(reason: str) -> ZamaneInspection:
        return ZamaneInspection(ok=False, sha256=digest, code=ARCHIVE_INVALID, reason=reason)

    try:
        size = path.stat().st_size
        if size > UPLOAD_LIMITS["zamane_client"]:
            return refuse(f"larger than {UPLOAD_LIMITS['zamane_client']} bytes")
        with zipfile.ZipFile(path) as archive:
            if _is_jar(archive):
                kind: Literal["jar", "zip"] = "jar"
                jar_name = None
                jar_digest = digest
            else:
                kind = "zip"
                member = _member(archive)
                data = _read_member(archive, member)
                _check_jar(data)
                jar_name = PurePosixPath(member.filename).name
                jar_digest = hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        return refuse(str(exc) or type(exc).__name__)
    release = find_release(jar_digest) or find_release(digest)
    return ZamaneInspection(
        ok=True,
        sha256=digest,
        kind=kind,
        jar_name=jar_name,
        jar_sha256=jar_digest,
        release=release.version if release is not None else None,
    )


def client_bytes(path: Path) -> bytes:
    """The bytes of the client to stage: the jar itself, or the one member of a .zip
    (``ValueError`` when the file is not a client)."""
    try:
        with zipfile.ZipFile(path) as archive:
            if _is_jar(archive):
                return path.read_bytes()
            data = _read_member(archive, _member(archive))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise ValueError(str(exc) or type(exc).__name__) from exc
    _check_jar(data)
    return data
