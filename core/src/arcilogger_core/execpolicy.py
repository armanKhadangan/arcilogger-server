"""Which programs ARCILOGGER runs (ADR-0014; contract 2.8.4), checked when it runs them.

The browser administration lets the administration agent write ``arcilogger.toml``. The
programs named in it run as LOCAL SERVICE (the evidence tasks) or with the API's rights, so the
code that starts a program checks it here first, every time, against the installer-written
``policy.json`` (:class:`~arcilogger_core.admin.contract.AdminPolicy`) and files only
administrators and SYSTEM write. A compromised agent can then change the configuration but
cannot make a task run a program of its choice.

* ``tsa.java`` (:func:`check_java`): exactly one element, unless the whole command equals an
  ``argv`` the installer recorded (an extra JVM argument such as ``-XX:OnError=`` would run a
  command). The element is the bare name ``java`` / ``java.exe`` (found through the system
  ``PATH``, which only administrators set) or an absolute path to ``java.exe`` that resolves
  (junctions and links followed) inside ``policy.executable_roots``, or whose SHA-256 is in
  ``policy.trusted_executables``.
* ``backup.pg_dump``, ``backup.pg_restore`` when set (:func:`check_executable`): an absolute
  path inside ``policy.executable_roots`` or with a trusted SHA-256.
* ``tsa.zamane_jar`` (:func:`check_zamane_jar`): inside ``<secrets_dir>\\zamane\\`` but not in
  its staging folder ``web\\`` (uploads wait there and never run), and its SHA-256 is a known
  release (:mod:`arcilogger_core.tsa.zamane_releases`), approved by ApplySystem
  (``admin\\system\\jars.json``), recorded by the installer (``zamane\\zamane-jar.sha256``) or in
  ``policy.trusted_executables``.

A refusal is :class:`ExecutableRefusedError` (code ``executable_refused``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    DocumentError,
    JarsFile,
    parse_document,
)
from arcilogger_core.config import Config

#: The names ``tsa.java`` may give (a bare name, or the file name of an absolute path).
JAVA_NAMES: Final = frozenset({"java", "java.exe"})
_CHUNK: Final = 1024 * 1024


class ExecutableRefusedError(RuntimeError):
    """A configured program may not run. ``what`` names the setting (``tsa.java``), ``reason``
    explains the refusal for the log; the user-facing text is that of ``executable_refused``."""

    code: Final = "executable_refused"

    def __init__(self, what: str, reason: str) -> None:
        super().__init__(f"{what}: {reason}")
        self.what = what
        self.reason = reason


@dataclass(frozen=True)
class ZamanePaths:
    """The Zamane client folders below ``secrets_dir`` (contract 2.2)."""

    root: Path

    @classmethod
    def of(cls, config: Config) -> ZamanePaths:
        return cls(config.secrets_dir / "zamane")

    @property
    def staging(self) -> Path:
        """Uploaded clients waiting for their hold (``web\\<sha256>.jar``); never run."""
        return self.root / "web"

    @property
    def approved(self) -> Path:
        """Clients ApplySystem promoted (``approved\\<sha256>.jar``)."""
        return self.root / "approved"

    @property
    def installer_record(self) -> Path:
        """``<sha256>  <file name>`` lines the installer writes for ``-ZamaneJar``."""
        return self.root / "zamane-jar.sha256"


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _trusted_hashes(policy: AdminPolicy) -> set[str]:
    return {entry.sha256 for entry in policy.trusted_executables}


def _resolve(text: str, what: str) -> Path:
    path = Path(text)
    if not path.is_absolute():
        raise ExecutableRefusedError(what, f"{text!r} is not an absolute path")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:  # RuntimeError: a link loop
        raise ExecutableRefusedError(what, f"{text!r} does not exist") from exc


def _inside(path: Path, roots: Collection[str]) -> bool:
    for root in roots:
        base = Path(root)
        if base.is_absolute() and path.is_relative_to(base.resolve()):
            return True
    return False


def check_executable(
    path_or_name: str, policy: AdminPolicy, *, names: Collection[str] = (), what: str = "program"
) -> None:
    """Refuse a program outside the trusted locations (:class:`ExecutableRefusedError`).

    ``names``: the bare names allowed (looked up in the system ``PATH``) and, for an absolute
    path, the file names allowed (case-insensitive); empty: no bare name, any file name."""
    allowed = {name.lower() for name in names}
    text = path_or_name.strip()
    if not text:
        raise ExecutableRefusedError(what, "no program given")
    if "/" not in text and "\\" not in text and not Path(text).is_absolute():
        if text.lower() in allowed:
            return
        raise ExecutableRefusedError(what, f"the bare program name {text!r} is not allowed")
    resolved = _resolve(text, what)
    if allowed and resolved.name.lower() not in allowed:
        raise ExecutableRefusedError(what, f"{text!r} is not one of {sorted(allowed)}")
    if not resolved.is_file():
        raise ExecutableRefusedError(what, f"{text!r} is not a file")
    if _inside(resolved, policy.executable_roots):
        return
    if file_sha256(resolved) in _trusted_hashes(policy):
        return
    raise ExecutableRefusedError(
        what, f"{text!r} is neither in policy.executable_roots nor a trusted SHA-256"
    )


def check_java(java: Sequence[str], policy: AdminPolicy) -> None:
    """``tsa.java``: one trusted program, or a whole command the installer recorded."""
    command = tuple(java)
    if not command:
        raise ExecutableRefusedError("tsa.java", "no program given")
    recorded = any(
        entry.argv is not None and entry.argv == command for entry in policy.trusted_executables
    )
    if len(command) != 1 and not recorded:
        raise ExecutableRefusedError(
            "tsa.java", "extra arguments are allowed only as a command the installer recorded"
        )
    check_executable(command[0], policy, names=JAVA_NAMES, what="tsa.java")


def _installer_hashes(record: Path) -> set[str]:
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()
    found = set()
    for line in lines:
        fields = line.strip().lstrip("﻿").split()
        if fields and not fields[0].startswith("#"):
            found.add(fields[0].lower())
    return found


def _approved_hashes(config: Config) -> set[str]:
    try:
        jars = parse_document(JarsFile, AdminLayout(config.admin_dir).jars.read_bytes())
    except (OSError, DocumentError):
        return set()  # no promotion yet (or a damaged file: nothing counts as approved)
    return {entry.sha256 for entry in jars.approved}


def check_zamane_jar(path: Path, config: Config, policy: AdminPolicy) -> str:
    """``tsa.zamane_jar``: its SHA-256 when it may run, else :class:`ExecutableRefusedError`."""
    # arcilogger_core.tsa imports this module (make_provider), so the release list is looked
    # up here and not at import time.
    from arcilogger_core.tsa.zamane_releases import KNOWN_RELEASES

    what = "tsa.zamane_jar"
    folders = ZamanePaths.of(config)
    resolved = _resolve(str(path), what)
    root = folders.root.resolve()
    if not resolved.is_relative_to(root) or resolved.is_relative_to(folders.staging.resolve()):
        raise ExecutableRefusedError(what, f"{str(path)!r} is not an installed client in {root}")
    if not resolved.is_file():
        raise ExecutableRefusedError(what, f"{str(path)!r} is not a file")
    digest = file_sha256(resolved)
    if (
        digest in KNOWN_RELEASES
        or digest in _approved_hashes(config)
        or digest in _installer_hashes(folders.installer_record)
        or digest in _trusted_hashes(policy)
    ):
        return digest
    raise ExecutableRefusedError(
        what, f"the client's SHA-256 {digest} is not known, approved or installer-recorded"
    )
