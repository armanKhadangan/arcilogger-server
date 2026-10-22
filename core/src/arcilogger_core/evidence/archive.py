"""The ZIP container of an evidence package: deterministic, optionally AES-256 encrypted.

Deterministic means: the same inputs give the same ZIP bytes. Every entry has the same fixed
date (1980-01-01 00:00, the earliest a ZIP can hold), Unix permission bits (so the result does
not depend on the operating system that built it), a fixed compression level, and the builder
writes the entries in sorted order. Real times are in ``paket.json`` and in the time stamps.

Encryption is WinZip AES-256 (AE-2) through ``pyzipper`` (MIT licence); 7-Zip and most archive
tools open it. Encrypted packages are not byte-deterministic (every entry gets a random salt).
File *names* inside a ZIP are never encrypted — only the contents are.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import pyzipper  # type: ignore[import-untyped]

from arcilogger_core.evidence.model import EvidenceError
from arcilogger_core.evidence.standalone.verify import Package, PackageError, check_names

ZIP_DATE = (1980, 1, 1, 0, 0, 0)
CHUNK = 1024 * 1024
_UNIX = 3  # ZipInfo.create_system: permission bits are Unix mode bits
_FILE_MODE = 0o100644
_EXEC_MODE = 0o100755


class _HashingReader:
    """Reads a file for the ZIP writer and hashes exactly the bytes that went into the ZIP."""

    def __init__(self, path: Path) -> None:
        self._handle = path.open("rb")
        self.hasher = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self.hasher.update(data)
        self.size += len(data)
        return data

    def close(self) -> None:
        self._handle.close()


class ArchiveWriter:
    """Writes the entries of one package; every ``add_*`` returns ``(size, sha256)``."""

    def __init__(self, path: Path, password: str | None = None) -> None:
        self.path = path
        self.count = 0
        self._zip: Any
        if password is None:
            self._zip = zipfile.ZipFile(path, "w", allowZip64=True)
            self._info_class: Any = zipfile.ZipInfo
        else:
            self._zip = pyzipper.AESZipFile(path, "w", allowZip64=True)
            self._zip.setpassword(password.encode("utf-8"))
            self._zip.setencryption(pyzipper.WZ_AES, nbits=256)
            self._info_class = pyzipper.AESZipFile.zipinfo_cls

    def _info(self, name: str, size: int, *, executable: bool, compress: bool) -> Any:
        info = self._info_class(name, date_time=ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        info.create_system = _UNIX
        info.external_attr = (_EXEC_MODE if executable else _FILE_MODE) << 16
        info.file_size = size
        return info

    def add_bytes(
        self, name: str, data: bytes, *, executable: bool = False, compress: bool = True
    ) -> tuple[int, str]:
        info = self._info(name, len(data), executable=executable, compress=compress)
        self._zip.writestr(info, data, compresslevel=9 if compress else None)
        self.count += 1
        return len(data), hashlib.sha256(data).hexdigest()

    def add_file(
        self, name: str, source: Path, *, expected_sha256: str, compress: bool = False
    ) -> tuple[int, str]:
        """Stream a (possibly very large) file into the ZIP and check it while copying.

        ``expected_sha256`` is the value from the manifest: if the file on disk does not match
        it, the build stops — a package must never carry a file that differs from the sealed one.
        """
        size = source.stat().st_size
        info = self._info(name, size, executable=False, compress=compress)
        reader = _HashingReader(source)
        try:
            with self._zip.open(info, "w") as target:
                shutil.copyfileobj(reader, target, CHUNK)
        finally:
            reader.close()
        digest = reader.hasher.hexdigest()
        if digest != expected_sha256 or reader.size != size:
            raise EvidenceError(
                f"{source}: SHA-256 {digest} differs from the sealed value {expected_sha256}; "
                "the vault file was changed after sealing — package not built"
            )
        self.count += 1
        return reader.size, digest

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> ArchiveWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class ArchivePackage(Package):
    """A package ZIP read back through ``pyzipper`` (also when it is encrypted), so the
    standalone verifier's checks can run on exactly what was written."""

    def __init__(self, path: Path, password: str | None = None) -> None:
        self.label = str(path)
        self._zip: Any = pyzipper.AESZipFile(path)
        if password is not None:
            self._zip.setpassword(password.encode("utf-8"))
        infos = [info for info in self._zip.infolist() if not info.is_dir()]
        try:
            check_names([info.filename for info in infos])
        except PackageError:
            self._zip.close()
            raise
        self._infos = {info.filename: info for info in infos}

    def names(self) -> list[str]:
        return sorted(self._infos)

    def open(self, name: str) -> IO[bytes]:
        handle: IO[bytes] = self._zip.open(self._infos[name])
        return handle

    def close(self) -> None:
        self._zip.close()


def is_encrypted(path: Path) -> bool:
    """True if any entry of the ZIP is encrypted."""
    with zipfile.ZipFile(path) as archive:
        return any(info.flag_bits & 0x1 for info in archive.infolist())


#: Upper limit for what :func:`extract_archive` unpacks (the declared sizes of all entries).
MAX_EXTRACT_BYTES = 256 * 1024**3


def extract_archive(
    path: Path, target: Path, password: str | None = None, *, max_bytes: int = MAX_EXTRACT_BYTES
) -> None:
    """Unpack a package into ``target`` (used to verify an encrypted package).

    Everything is checked before anything is written: every name is safe and distinct as
    Windows sees names (the standalone verifier's rule: no absolute paths, no ``..``, no drive
    letters, no case-only differences), and the declared sizes fit both ``max_bytes`` and the
    free space of ``target`` (a ZIP bomb is refused, not unpacked). The ZIP reader stops every
    entry at its declared size.
    """
    with pyzipper.AESZipFile(path) as archive:
        if password is not None:
            archive.setpassword(password.encode("utf-8"))
        infos = archive.infolist()
        try:
            check_names(
                [info.filename for info in infos if not info.is_dir()],
                [info.filename.rstrip("/") for info in infos if info.is_dir()],
            )
        except PackageError as exc:
            raise EvidenceError(f"unsafe name in the package: {exc.en}") from exc
        total = sum(info.file_size for info in infos)
        target.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target).free
        if total > min(max_bytes, free):
            raise EvidenceError(
                f"the package would unpack to {total} bytes (limit {max_bytes}, free {free})"
            )
        for info in infos:
            parts = info.filename.rstrip("/").split("/")
            destination = target.joinpath(*parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, destination.open("wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
            except (RuntimeError, zipfile.BadZipFile) as exc:  # bad password / HMAC check
                raise EvidenceError(f"cannot decrypt the package: {exc}") from exc
