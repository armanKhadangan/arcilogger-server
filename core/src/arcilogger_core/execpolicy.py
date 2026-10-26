"""Which programs ARCILOGGER runs (ADR-0014; contract 2.8.4), checked when it runs them.

The browser administration lets the administration agent write ``arcilogger.toml``. The
programs named in it run as LOCAL SERVICE (the evidence tasks) or with the API's rights, so the
code that starts a program checks it here first, every time. The checks trust only what
administrators and SYSTEM write, found through :class:`TrustAnchors`: the folder of the fixed
``--config`` path (``C:\\ProgramData\\ARCILOGGER``), its ``admin\\policy.json``
(:func:`~arcilogger_core.admin.contract.load_policy`), ``zamane\\`` and
``admin\\system\\jars.json``. They never follow a key of the configuration (``secrets_dir`` is
one the agent can write). A compromised agent can then change the configuration but cannot make
a task run a program of its choice, as long as the trusted locations are writable by
administrators and SYSTEM only (the installer records no other, contract 2.16).

* ``tsa.java`` (:func:`check_java`): exactly one element, unless the whole command equals an
  ``argv`` the installer recorded (an extra JVM argument such as ``-XX:OnError=`` would run a
  command). The element is the bare name ``java`` / ``java.exe`` (found through the system
  ``PATH``, which only administrators set) or an absolute path to ``java.exe`` inside
  ``policy.executable_roots``, or the path the installer recorded in
  ``policy.trusted_executables`` with the file's SHA-256.
* ``backup.pg_dump``, ``backup.pg_restore`` when set (:func:`check_executable`): an absolute
  path inside ``policy.executable_roots`` or recorded with its SHA-256.
* ``tsa.zamane_jar`` (:func:`check_zamane_jar`): inside ``zamane\\`` but not in its staging
  folder ``web\\`` (uploads wait there and never run), and its SHA-256 is a known release
  (:mod:`arcilogger_core.tsa.zamane_releases`), approved (``admin\\system\\jars.json``: a known
  release promoted by ApplySystem, or ``approve-jar`` on the console), recorded by the installer
  (``zamane\\zamane-jar.sha256``) or recorded with this path in ``policy.trusted_executables``.

A checked path is local (no UNC or device path) and free of links: its resolved form must be its
absolute form, so no symbolic link or junction lies anywhere on it (an 8.3 short name fails
closed too) and nobody can re-point it between the check and the start of the program. Every
check returns what it checked, and the caller runs exactly that. A refusal is
:class:`ExecutableRefusedError` (code ``executable_refused``).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    DocumentError,
    JarsFile,
    load_policy,
    parse_document,
)

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
    """The Zamane client folders of the installation folder (contract 2.2)."""

    root: Path

    @property
    def staging(self) -> Path:
        """Uploaded clients waiting for their approval (``web\\<sha256>.jar``); never run."""
        return self.root / "web"

    @property
    def approved(self) -> Path:
        """Approved clients (``approved\\<sha256>.jar``)."""
        return self.root / "approved"

    @property
    def installer_record(self) -> Path:
        """``<sha256>  <file name>`` lines the installer writes for ``-ZamaneJar``."""
        return self.root / "zamane-jar.sha256"


@dataclass(frozen=True)
class TrustAnchors:
    """What the checks trust: the installation folder and the policy read from it.

    ``root`` is the folder of the configuration file every service and task is started with
    (``--config``, or ``default_config_path()``), which only administrators set; never a value
    read from that file. Build it on every run (a task) or every use (the API's report stamps),
    so a new ``policy.json`` counts at once."""

    root: Path
    policy: AdminPolicy

    @classmethod
    def of(cls, config_path: Path) -> TrustAnchors:
        root = _absolute(config_path).parent
        return cls(root, load_policy(AdminLayout(root / "admin").policy))

    @property
    def zamane(self) -> ZamanePaths:
        return ZamanePaths(self.root / "zamane")

    @property
    def jars(self) -> Path:
        """``admin\\system\\jars.json`` (ApplySystem and the console write it)."""
        return AdminLayout(self.root / "admin").jars


class CheckedJar(NamedTuple):
    """The Zamane client to run (``java -jar <path>``) and its SHA-256."""

    path: Path
    sha256: str


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _absolute(path: Path) -> Path:
    """``path`` made absolute without following links (``Path.resolve`` follows them)."""
    return Path(os.path.abspath(path))  # noqa: PTH100 - lexical on purpose


def _key(path: Path) -> str:
    return os.path.normcase(_absolute(path))


def _local_path(text: str, what: str) -> Path:
    """``text`` as an existing absolute local path without a link on it (the path that is
    checked is the path that runs)."""
    if text.startswith(("\\\\", "//")):
        raise ExecutableRefusedError(what, f"{text!r} is a network or device path")
    path = Path(text)
    if not path.is_absolute():
        raise ExecutableRefusedError(what, f"{text!r} is not an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:  # RuntimeError: a link loop
        raise ExecutableRefusedError(what, f"{text!r} does not exist") from exc
    if _key(resolved) != _key(path):
        raise ExecutableRefusedError(
            what, f"{text!r} resolves to {str(resolved)!r} (a link, junction or short name on it)"
        )
    return resolved


def _inside(path: Path, roots: Collection[str]) -> bool:
    for root in roots:
        base = Path(root)
        if base.is_absolute() and path.is_relative_to(base.resolve()):
            return True
    return False


def _trusted_here(path: Path, digest: str, policy: AdminPolicy) -> bool:
    """The installer recorded this SHA-256 for this very path: a copy of a trusted program
    elsewhere would load its libraries from the folder it was copied to."""
    for entry in policy.trusted_executables:
        recorded = Path(entry.path)
        if entry.sha256 == digest and recorded.is_absolute() and _key(recorded) == _key(path):
            return True
    return False


def check_executable(
    path_or_name: str, anchors: TrustAnchors, *, names: Collection[str] = (), what: str = "program"
) -> str:
    """The program to run (the bare name, or the checked absolute path); refuse one outside
    the trusted locations (:class:`ExecutableRefusedError`).

    ``names``: the bare names allowed (looked up in the system ``PATH``) and, for an absolute
    path, the file names allowed (case-insensitive); empty: no bare name, any file name."""
    allowed = {name.lower() for name in names}
    text = path_or_name.strip()
    if not text:
        raise ExecutableRefusedError(what, "no program given")
    if "/" not in text and "\\" not in text and not Path(text).is_absolute():
        if text.lower() in allowed:
            return text
        raise ExecutableRefusedError(what, f"the bare program name {text!r} is not allowed")
    resolved = _local_path(text, what)
    if allowed and resolved.name.lower() not in allowed:
        raise ExecutableRefusedError(what, f"{text!r} is not one of {sorted(allowed)}")
    if not resolved.is_file():
        raise ExecutableRefusedError(what, f"{text!r} is not a file")
    if _inside(resolved, anchors.policy.executable_roots):
        return str(resolved)
    if _trusted_here(resolved, file_sha256(resolved), anchors.policy):
        return str(resolved)
    raise ExecutableRefusedError(
        what, f"{text!r} is neither in policy.executable_roots nor recorded with its SHA-256"
    )


def check_java(java: Sequence[str], anchors: TrustAnchors) -> tuple[str, ...]:
    """``tsa.java``: one trusted program, or a whole command the installer recorded. Returns
    the command to run (the checked program and the recorded arguments)."""
    command = tuple(java)
    if not command:
        raise ExecutableRefusedError("tsa.java", "no program given")
    recorded = any(
        entry.argv is not None and entry.argv == command
        for entry in anchors.policy.trusted_executables
    )
    if len(command) != 1 and not recorded:
        raise ExecutableRefusedError(
            "tsa.java", "extra arguments are allowed only as a command the installer recorded"
        )
    program = check_executable(command[0], anchors, names=JAVA_NAMES, what="tsa.java")
    return (program, *command[1:])


def _installer_hashes(record: Path) -> set[str]:
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()
    found = set()
    for line in lines:
        fields = line.strip().lstrip("\ufeff").split()
        if fields and not fields[0].startswith("#"):
            found.add(fields[0].lower())
    return found


def _approved_hashes(anchors: TrustAnchors) -> set[str]:
    try:
        jars = parse_document(JarsFile, anchors.jars.read_bytes())
    except (OSError, DocumentError):
        return set()  # nothing approved yet (or a damaged file: nothing counts as approved)
    return {entry.sha256 for entry in jars.approved}


def check_zamane_jar(path: Path | str, anchors: TrustAnchors) -> CheckedJar:
    """``tsa.zamane_jar``: the jar to run and its SHA-256, else :class:`ExecutableRefusedError`."""
    # arcilogger_core.tsa imports this module (make_provider), so the release list is looked
    # up here and not at import time.
    from arcilogger_core.tsa.zamane_releases import KNOWN_RELEASES

    what = "tsa.zamane_jar"
    folders = anchors.zamane
    resolved = _local_path(str(path), what)
    root = folders.root.resolve()
    if not resolved.is_relative_to(root) or resolved.is_relative_to(folders.staging.resolve()):
        raise ExecutableRefusedError(what, f"{str(path)!r} is not an installed client in {root}")
    if not resolved.is_file():
        raise ExecutableRefusedError(what, f"{str(path)!r} is not a file")
    digest = file_sha256(resolved)
    if (
        digest in KNOWN_RELEASES
        or digest in _approved_hashes(anchors)
        or digest in _installer_hashes(folders.installer_record)
        or _trusted_here(resolved, digest, anchors.policy)
    ):
        return CheckedJar(resolved, digest)
    raise ExecutableRefusedError(
        what, f"the client's SHA-256 {digest} is not known, approved or installer-recorded"
    )
