"""Scheduled database backups and restores (``arcilogger-db backup``, ADR-0009).

* **run** — the ``\\ARCILOGGER\\Backup`` task, daily at 01:40 as LOCAL SERVICE: ``pg_dump -Fc``
  of the ARCILOGGER database as the ARCILOGGER role (``[database]``, password from the secret
  store) into ``<data_dir>/backups/scheduled`` (``[backup] dir``) as
  ``arcilogger-YYYYmmdd-HHMMSS.dump`` (site time). The dump is written under a temporary name,
  fsync'ed, read back with ``pg_restore --list`` and only then renamed, so a file with the final
  name is always a complete archive; a sidecar ``<name>.json`` records its SHA-256, size and the
  versions it came from. Every attempt writes ``state/backup/last.json`` (MVP-CONTRACTS section
  9, read by the alert checks); a failed or refused one also raises ``R20-backup``. A successful
  run then applies the retention, which always keeps the dump it has just made (``prune``: the
  dump ``last.json`` names).
* **prune** — keeps the newest dump of each of the last ``keep_daily`` days, ``keep_weekly`` ISO
  weeks and ``keep_monthly`` months that have one (counted by periods that have a dump, never by
  the clock: a clock that jumps forward deletes nothing early). It deletes only files named like
  ARCILOGGER's scheduled dumps, their sidecars and the leftovers of interrupted runs, inside that
  one folder — never the installer's dumps in ``backups/db`` or anything else.
* **restore** — into a **new** database (refused when it exists or is the configured one,
  created like the installer creates the live one: ``template0``, UTF-8, locale ``C``) as the
  PostgreSQL superuser, whose password the command line reads from stdin; one transaction per
  object, stopping at the first error; TimescaleDB's ``timescaledb_pre_restore()`` /
  ``timescaledb_post_restore()`` around ``pg_restore`` when the dump contains the extension. The
  live database is never dropped or overwritten. ``pg_restore`` runs the archive's SQL with the
  superuser's rights and the sidecar lies next to the dump (a LOCAL SERVICE process can replace
  both): the sidecar only detects accidental damage, ``--sha256`` checks the dump against a hash
  recorded off the server.

A configured ``[backup] pg_dump`` / ``pg_restore`` runs only when the executable policy trusts
it (ADR-0014: below ``admin\\policy.json`` ``executable_roots`` or recorded by the installer with
its SHA-256, :func:`find_tool`); a refused one fails the run like a missing one.

Passwords reach ``pg_dump`` and ``pg_restore`` only through a password file (``PGPASSFILE``)
created with owner-only rights in a private temporary folder and deleted when the tool has
finished — never on a command line, in the tool's environment or in a log. Error texts are short
English sentences (they end up in ``last.json`` and the alert) and never contain the password.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, Final, NamedTuple, Protocol

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.config import BackupConfig, Config, default_config_path
from arcilogger_core.execpolicy import ExecutableRefusedError, TrustAnchors, check_executable
from arcilogger_core.fsutil import atomic_write_text, fsync_dir, fsync_file
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, to_site, utc_now
from arcilogger_db import __version__
from arcilogger_db.engine import database_password, make_engine
from arcilogger_db.migrate import current_revisions

log = logging.getLogger("arcilogger.backup")

ALERT_RULE: Final = "R20-backup"
STATE_SCHEMA: Final = 1
SIDECAR_SCHEMA: Final = 1
#: ``arcilogger-20261019-014002.dump``: site date and time of the start.
BACKUP_NAME: Final = re.compile(r"^arcilogger-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\.dump$")
SIDECAR_SUFFIX: Final = ".json"
SIDECAR_NAME: Final = re.compile(r"^(arcilogger-\d{8}-\d{6}\.dump)\.json$")
#: What an interrupted run leaves behind: the dump being written, a sidecar being written
#: (``arcilogger_core.fsutil.atomic_write_bytes``).
LEFTOVER_NAME: Final = re.compile(r"^\.arcilogger-\d{8}-\d{6}\.dump(?:\.partial|\.json\.tmp)$")
LEFTOVER_AGE: Final = timedelta(hours=12)
#: The task's ExecutionTimeLimit is 4 hours: give up early enough to record the failure.
DUMP_TIMEOUT_SECONDS: Final = 3 * 3600 + 30 * 60
CHECK_TIMEOUT_SECONDS: Final = 3600
RESTORE_TIMEOUT_SECONDS: Final = 24 * 3600
VERSION_TIMEOUT_SECONDS: Final = 60
#: Names ``backup restore`` creates (the installer's rule for database names).
DATABASE_NAME: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ROLE_NAME: Final = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")
RESERVED_DATABASES: Final = frozenset({"postgres", "template0", "template1"})
#: ``--sha256``: as ``Get-FileHash`` prints it (upper case) or ``sha256sum`` (lower case).
SHA256_HEX: Final = re.compile(r"^[0-9A-Fa-f]{64}$")
GIB: Final = 1024**3
#: Inherited libpq variables that could change where the password comes from or what is
#: connected to; host, port, user and database are always given explicitly.
_DROPPED_ENV: Final = frozenset(
    {"PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR", "PGOPTIONS"}
)
_TOOL_VERSION: Final = re.compile(r"\(PostgreSQL\)\s+(\d+(?:\.\d+)*)")
#: ``"C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe" runservice -N "postgresql-x64-16" -D …``
_PG_CTL: Final = re.compile(r'^\s*"?(?P<exe>[^"]*?[\\/]pg_ctl\.exe)"?(?:\s|$)', re.IGNORECASE)
_TOC_TIMESCALE: Final = re.compile(r"^\d+;\s.*\bEXTENSION - timescaledb\b", re.MULTILINE)


class BackupError(RuntimeError):
    """A backup, check, prune or restore step failed (the message holds no secret)."""


class RestoreRefusedError(BackupError):
    """``restore`` refused before anything was changed (name, existing database, file)."""


# -- the PostgreSQL tools -------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    path: Path
    #: How it was found: ``config``, ``service <name>``, ``versions folder`` or ``PATH``.
    found_by: str


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


if sys.platform == "win32":
    import winreg

    def registry_services() -> dict[str, str]:
        """``ImagePath`` of every ``postgresql*`` service (HKLM\\SYSTEM\\...\\Services)."""
        found: dict[str, str] = {}
        root = r"SYSTEM\CurrentControlSet\Services"
        try:
            services = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
        except OSError:
            return found
        with services:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(services, index)
                except OSError:  # no more subkeys
                    break
                index += 1
                if not name.lower().startswith("postgresql"):
                    continue
                try:
                    with winreg.OpenKey(services, name) as key:
                        value, _ = winreg.QueryValueEx(key, "ImagePath")
                except OSError:
                    continue
                found[name] = str(value)
        return found

    def default_versions_root() -> Path:
        return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "PostgreSQL"

else:

    def registry_services() -> dict[str, str]:
        return {}  # Windows services are a Windows matter

    def default_versions_root() -> Path:
        """The Debian/Ubuntu layout (development machines and CI)."""
        return Path("/usr/lib/postgresql")


def service_bin_dirs(services: Mapping[str, str]) -> list[tuple[str, Path]]:
    """``(service, bin folder)`` of the PostgreSQL services, in the installer's order
    (``Install-Arcilogger.ps1`` ``Find-PostgresService``): a PostgreSQL 16 service first, then
    the others by name. ``services`` maps a service name to its ``ImagePath``."""
    found: list[tuple[bool, str, Path]] = []
    for name in sorted(services, key=str.lower):
        if not name.lower().startswith("postgresql"):
            continue
        image = os.path.expandvars(services[name])
        match = _PG_CTL.match(image)
        if match is None:
            continue
        preferred = bool(
            re.search(r"-16$", name, re.IGNORECASE)
            or re.search(r"\\16\\bin\\", image, re.IGNORECASE)
        )
        found.append((preferred, name, Path(str(PureWindowsPath(match.group("exe")).parent))))
    found.sort(key=lambda item: not item[0])  # stable: by name within each group
    return [(name, folder) for _, name, folder in found]


def versioned_bin_dirs(root: Path) -> list[Path]:
    """``<root>/<version>/bin`` folders, the highest version first."""
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    versions = []
    for child in children:
        parts = child.name.split(".")
        if child.is_dir() and all(part.isdigit() for part in parts):
            versions.append((tuple(int(part) for part in parts), child / "bin"))
    return [folder for _, folder in sorted(versions, reverse=True)]


def find_tool(
    name: str,
    configured: str = "",
    *,
    anchors: TrustAnchors | None = None,
    services: Mapping[str, str] | None = None,
    versions_root: Path | None = None,
    search_path: str | None = None,
) -> Tool:
    """``pg_dump`` / ``pg_restore``: ``[backup] <name>``, else the bin folder of the PostgreSQL
    service, else the newest ``C:\\Program Files\\PostgreSQL\\<version>\\bin``, else ``PATH``.

    A configured path runs only when the executable policy trusts it (ADR-0014, contract 2.8.4:
    inside ``policy.executable_roots`` or recorded by the installer with its SHA-256), checked
    against ``anchors`` — the folder of the fixed ``--config`` path, by default
    :func:`~arcilogger_core.config.default_config_path` — never against a folder the
    configuration names: the administration agent may write ``arcilogger.toml``, and the Backup
    task runs the tool as LOCAL SERVICE, a restore as the PostgreSQL superuser."""
    exe = _exe(name)
    if configured.strip():
        path = Path(configured.strip())
        if not path.is_file():
            raise BackupError(f"[backup] {name} = {configured.strip()!r}: file not found")
        try:
            checked = check_executable(
                str(path),
                anchors or TrustAnchors.of(default_config_path()),
                what=f"backup.{name}",
            )
        except ExecutableRefusedError as exc:
            raise BackupError(f"[backup] {name}: {exc.reason} ({exc.code})") from exc
        return Tool(Path(checked), "config")
    for service, folder in service_bin_dirs(registry_services() if services is None else services):
        if (folder / exe).is_file():
            return Tool(folder / exe, f"service {service}")
    for folder in versioned_bin_dirs(versions_root or default_versions_root()):
        if (folder / exe).is_file():
            return Tool(folder / exe, "versions folder")
    found = shutil.which(exe, path=search_path)
    if found:
        return Tool(Path(found), "PATH")
    raise BackupError(f"{name} not found: set [backup] {name} to its full path")


def tool_env(config: Config | None = None) -> dict[str, str]:
    """Environment for a PostgreSQL tool: UTF-8, English messages, no inherited password."""
    env = {key: value for key, value in os.environ.items() if key.upper() not in _DROPPED_ENV}
    env["PGCLIENTENCODING"] = "UTF8"
    env["LC_MESSAGES"] = "C"
    if config is not None:
        env["PGCONNECT_TIMEOUT"] = str(config.database.connect_timeout_seconds)
        env["PGAPPNAME"] = "arcilogger-backup"
        if config.database.sslmode:
            env["PGSSLMODE"] = config.database.sslmode
    return env


def _pgpass_field(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


@contextmanager
def password_file(
    env: Mapping[str, str], user: str, password: str | None
) -> Iterator[dict[str, str]]:
    """``env`` plus ``PGPASSFILE`` naming a temporary password file for ``user``.

    The file lies in a folder that ``tempfile.mkdtemp`` creates for the current account only
    (mode 0700; on Windows an ACL for the owner, SYSTEM and administrators, Python 3.12.4 and
    later), is created exclusively with mode 0600 and is deleted when the block ends, also on an
    error. Without a password (a local trust or SSPI setup) no file is written.
    """
    child = dict(env)
    if not password:
        yield child
        return
    if "\n" in password or "\r" in password:
        raise BackupError("the password contains a line break and cannot be passed to libpq")
    with tempfile.TemporaryDirectory(prefix="arcilogger-pg-") as folder:
        path = Path(folder) / "pgpass.conf"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                line = f"*:*:*:{_pgpass_field(user)}:{_pgpass_field(password)}\n"
                handle.write(line.encode("utf-8"))
            child["PGPASSFILE"] = str(path)
            yield child
        finally:
            path.unlink(missing_ok=True)


def _redact(value: str, secret: str | None) -> str:
    return value.replace(secret, "***") if secret else value


def _run(
    command: Sequence[str],
    env: Mapping[str, str],
    timeout: int,
    *,
    discard_stdout: bool = False,
) -> subprocess.CompletedProcess[str]:
    name = Path(command[0]).name
    try:
        return subprocess.run(  # noqa: S603 - a PostgreSQL tool, arguments are validated values
            list(command),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{name} did not finish within {timeout // 60} minutes") from exc
    except OSError as exc:
        raise BackupError(f"{name} could not be started: {exc.strerror or exc}") from exc


def _failure(what: str, result: subprocess.CompletedProcess[str], secret: str | None) -> str:
    """The tool's first ``error:`` line (warnings, details and hints may follow it), else its
    last line of output."""
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    errors = [line for line in lines if "error:" in line.lower()]
    detail = (errors or lines or ["no message"])[0 if errors else -1]
    return f"{what} failed (exit code {result.returncode}): {_redact(detail, secret)[:300]}"


def tool_version(tool: Tool) -> tuple[str, int]:
    """``("16.4", 16)`` from ``<tool> --version``."""
    result = _run([str(tool.path), "--version"], tool_env(), VERSION_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise BackupError(_failure(f"{tool.path.name} --version", result, None))
    match = _TOOL_VERSION.search(result.stdout or "")
    if match is None:
        output = (result.stdout or "").strip()[:80]
        raise BackupError(f"{tool.path.name} --version: unexpected output {output!r}")
    version = match.group(1)
    return version, int(version.split(".")[0])


class ArchiveCheck(NamedTuple):
    entries: int
    timescaledb: bool


def check_archive(tool: Tool, path: Path) -> ArchiveCheck:
    """``pg_restore --list``: the archive's table of contents is complete and readable."""
    result = _run([str(tool.path), "--list", str(path)], tool_env(), CHECK_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise BackupError(_failure("pg_restore --list", result, None))
    listing = result.stdout or ""
    entries = sum(1 for line in listing.splitlines() if line.strip() and not line.startswith(";"))
    if entries == 0:
        raise BackupError("pg_restore --list: the archive lists no objects")
    return ArchiveCheck(entries, _TOC_TIMESCALE.search(listing) is not None)


def read_archive(tool: Tool, path: Path) -> None:
    """Read every byte of the archive (``pg_restore --file=-``, output discarded): proves the
    data, not only the table of contents, can be read. Takes about as long as a restore."""
    result = _run(
        [str(tool.path), "--file=-", str(path)],
        tool_env(),
        RESTORE_TIMEOUT_SECONDS,
        discard_stdout=True,
    )
    if result.returncode != 0:
        raise BackupError(_failure("pg_restore (full read)", result, None))


# -- the database ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerInfo:
    version: str
    major: int
    #: Alembic revision(s) of the schema, ``None`` for a database never migrated.
    revision: str | None
    #: Version of the ``timescaledb`` extension in the database, ``None`` without it.
    timescaledb: str | None


class Probe(Protocol):
    def __call__(self, config: Config, password: str | None) -> ServerInfo: ...


def _server_info(connection: Connection) -> ServerInfo:
    version = str(connection.execute(text("SHOW server_version")).scalar_one())
    number = int(connection.execute(text("SHOW server_version_num")).scalar_one())
    revisions = current_revisions(connection)
    timescale = connection.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar_one_or_none()
    return ServerInfo(
        version=version,
        major=number // 10000,
        revision=",".join(revisions) or None,
        timescaledb=None if timescale is None else str(timescale),
    )


def probe_server(config: Config, password: str | None) -> ServerInfo:
    """Server version, schema revision and TimescaleDB version of the configured database."""
    engine = make_engine(config, application="backup", password=password, pool_size=1)
    try:
        with engine.connect() as connection:
            return _server_info(connection)
    finally:
        engine.dispose()


def _describe(exc: BaseException) -> str:
    """One line: the driver's message without SQL or parameters (never the password)."""
    original = getattr(exc, "orig", None)
    lines = str(original or exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


# -- files ----------------------------------------------------------------------------------------


def backup_dir(config: Config) -> Path:
    """The folder of the scheduled dumps (``[backup] dir``, relative to ``data_dir``).

    Refused inside the evidence and job folders (``spool``, ``vault``, ``state``, ``exports``,
    ``logs``) and as the data directory itself: dumps never mix with evidence.
    """
    configured = config.backup.dir
    folder = configured if configured.is_absolute() else config.data_dir / configured
    resolved = folder.resolve()
    layout = Layout(config.data_dir)
    if resolved == config.data_dir.resolve():
        raise BackupError("[backup] dir must not be the data directory itself")
    for protected in (layout.spool, layout.vault, layout.state, layout.exports, layout.logs):
        root = protected.resolve()
        if resolved == root or root in resolved.parents:
            raise BackupError(f"[backup] dir must not be inside {protected.name}/")
    return folder


def backup_name(started: datetime) -> str:
    return f"arcilogger-{to_site(started):%Y%m%d-%H%M%S}.dump"


def sidecar_path(dump: Path) -> Path:
    return dump.with_name(dump.name + SIDECAR_SUFFIX)


def last_state_path(layout: Layout) -> Path:
    """``state/backup/last.json`` (MVP-CONTRACTS section 9)."""
    return layout.state / "backup" / "last.json"


def taken_at(name: str) -> datetime | None:
    """The site time in a scheduled dump's name, ``None`` for any other name."""
    match = BACKUP_NAME.fullmatch(name)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=SITE_TZ)
    except ValueError:  # e.g. month 13: not one of ours
        return None


def file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def read_sidecar(dump: Path) -> dict[str, Any] | None:
    """The dump's sidecar, ``None`` when it is missing or unreadable."""
    try:
        data = json.loads(sidecar_path(dump).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class BackupFile:
    path: Path
    taken: datetime
    size: int
    sidecar: dict[str, Any] | None


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def list_backups(folder: Path) -> list[BackupFile]:
    """The scheduled dumps in ``folder`` (not its subfolders), newest first."""
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        taken = taken_at(path.name)
        if taken is None or not _is_plain_file(path):
            continue
        found.append(BackupFile(path, taken, path.stat().st_size, read_sidecar(path)))
    return sorted(found, key=lambda backup: backup.taken, reverse=True)


# -- retention ------------------------------------------------------------------------------------


def kept_by_retention(
    backups: Sequence[BackupFile],
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
    protect: Collection[Path] = (),
) -> set[Path]:
    """The dumps the retention keeps: the newest of each of the last ``keep_daily`` days,
    ``keep_weekly`` ISO weeks and ``keep_monthly`` months that have a dump (site time), always
    the newest dump of all, and always those in ``protect`` — the dump a run has just made,
    whatever its name says (a clock set back names it older than the dumps already there)."""
    ordered = sorted(backups, key=lambda backup: backup.taken, reverse=True)
    periods: tuple[tuple[int, Callable[[datetime], object]], ...] = (
        (keep_daily, lambda taken: taken.date()),
        (keep_weekly, lambda taken: taken.isocalendar()[:2]),
        (keep_monthly, lambda taken: (taken.year, taken.month)),
    )
    kept: set[Path] = {ordered[0].path} if ordered else set()
    kept.update(backup.path for backup in ordered if backup.path in protect)
    for count, period in periods:
        seen: set[object] = set()
        for backup in ordered:
            key = period(backup.taken)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(key)
            kept.add(backup.path)
    return kept


@dataclass
class PruneResult:
    kept: list[Path] = field(default_factory=list)
    #: Dumps deleted (each with its sidecar).
    deleted: list[Path] = field(default_factory=list)
    #: Orphaned sidecars and leftovers of interrupted runs.
    cleaned: list[Path] = field(default_factory=list)


def prune_backups(
    folder: Path,
    settings: BackupConfig,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    protect: Collection[Path] = (),
) -> PruneResult:
    """Apply the retention to ``folder``; deletes nothing but ARCILOGGER's own file names and
    never a dump in ``protect``."""
    result = PruneResult()
    if not folder.is_dir():
        return result
    backups = list_backups(folder)
    kept = kept_by_retention(
        backups, settings.keep_daily, settings.keep_weekly, settings.keep_monthly, protect
    )
    for backup in backups:
        if backup.path in kept:
            result.kept.append(backup.path)
            continue
        result.deleted.append(backup.path)
        if not dry_run:
            backup.path.unlink()
            sidecar_path(backup.path).unlink(missing_ok=True)
            log.info("retention: deleted %s", backup.path.name)
    moment = now or utc_now()
    deleted = set(result.deleted)
    for path in sorted(folder.iterdir()):
        if not _is_plain_file(path):
            continue
        sidecar = SIDECAR_NAME.fullmatch(path.name)
        if sidecar is not None:
            dump = folder / sidecar.group(1)
            if dump in deleted or _is_plain_file(dump):
                continue  # goes (or went) with its dump, or belongs to a kept one
        elif LEFTOVER_NAME.fullmatch(path.name):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if moment - modified < LEFTOVER_AGE:
                continue  # possibly a run in progress
        else:
            continue
        result.cleaned.append(path)
        if not dry_run:
            path.unlink(missing_ok=True)
            log.info("retention: removed %s", path.name)
    return result


# -- run ------------------------------------------------------------------------------------------


@dataclass
class BackupOutcome:
    ok: bool
    started: datetime
    finished: datetime
    file: Path | None = None
    size: int | None = None
    sha256: str | None = None
    error: str | None = None
    #: ``None`` when the retention did not run (failed backup) or failed (``prune_error``).
    pruned: PruneResult | None = None
    prune_error: str | None = None
    sidecar: dict[str, Any] = field(default_factory=dict)


def relative_to_data(config: Config, path: Path) -> str:
    """``backups/scheduled/<name>`` (POSIX separators); an absolute path outside ``data_dir``."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(config.data_dir.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def write_last_state(
    layout: Layout,
    *,
    finished: datetime,
    ok: bool,
    file: str | None,
    size: int | None,
    sha256: str | None,
    error: str | None,
) -> None:
    """``state/backup/last.json`` (MVP-CONTRACTS section 9), written atomically."""
    state = {
        "schema": STATE_SCHEMA,
        "finished": isoformat_site(finished, "seconds"),
        "ok": ok,
        "file": file,
        "size": size,
        "sha256": sha256,
        "error": error,
    }
    atomic_write_text(last_state_path(layout), json.dumps(state, ensure_ascii=False) + "\n")


def _check_free_space(
    folder: Path, min_free_gb: int, newest: int, disk_usage: Callable[[Path], Any]
) -> None:
    free = int(disk_usage(folder).free)
    needed = min_free_gb * GIB + newest
    if free < needed:
        raise BackupError(
            f"not enough free space in {folder}: {free / GIB:.1f} GiB free, "
            f"{needed / GIB:.1f} GiB needed ([backup] min_free_gb = {min_free_gb} plus the size "
            "of the newest dump)"
        )


@dataclass
class _Attempt:
    """What a failed attempt must clean up (and the password its messages must not show)."""

    password: str | None = None
    partial: Path | None = None
    final: Path | None = None


@dataclass(frozen=True)
class _Dumped:
    folder: Path
    file: Path
    size: int
    sha256: str
    finished: datetime
    sidecar: dict[str, Any]
    #: The newest dump already in the folder, when its name is later than the new one's.
    later: str | None = None


def run_backup(
    config: Config,
    *,
    clock: Callable[[], datetime] = utc_now,
    probe: Probe = probe_server,
    services: Mapping[str, str] | None = None,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    anchors: TrustAnchors | None = None,
) -> BackupOutcome:
    """One scheduled backup attempt: dump, check, rename, sidecar, ``last.json``, retention.

    Never raises for a failed attempt: it is recorded in ``last.json`` and as an
    ``R20-backup`` alert, and returned with ``ok = False``. ``anchors``: what a configured
    ``pg_dump`` / ``pg_restore`` is checked against (:func:`find_tool`).
    """
    layout = Layout(config.data_dir)
    started = clock()
    attempt = _Attempt()
    try:
        dumped = _dump(config, started, clock, probe, services, disk_usage, attempt, anchors)
    except (BackupError, OSError, SQLAlchemyError, SecretStoreError) as exc:
        if isinstance(exc, SQLAlchemyError):
            error = _describe(exc)
        elif isinstance(exc, OSError):
            where = f" ({exc.filename})" if exc.filename else ""
            error = f"file error: {exc.strerror or exc}{where}"
        else:
            error = str(exc)
        _discard(attempt.partial, attempt.final)
        return _failed(config, layout, started, clock(), _redact(error, attempt.password))
    log.info("backup %s: ok, %d bytes, SHA-256 %s", dumped.file.name, dumped.size, dumped.sha256)
    try:
        write_last_state(
            layout,
            finished=dumped.finished,
            ok=True,
            file=relative_to_data(config, dumped.file),
            size=dumped.size,
            sha256=dumped.sha256,
            error=None,
        )
    except OSError as exc:  # the checked dump stays; the run is still reported as failed
        error = f"the dump was made but {last_state_path(layout).name} was not written: " + str(
            exc.strerror or exc
        )
        log.error("backup %s: %s", dumped.file.name, error)
        # Under R20-backup (no key): the last.json check resolves it only after a backup that
        # succeeded after this alert, not because an earlier last.json still says "ok".
        _alert(
            layout,
            "high",
            "Veritabanı yedeği alındı ama durumu yazılamadı [database backup made, its state "
            f"file could not be written]: {error}",
            {"error": error, "file": relative_to_data(config, dumped.file)},
        )
        return BackupOutcome(
            ok=False, started=started, finished=dumped.finished, file=dumped.file, error=error
        )
    outcome = BackupOutcome(
        ok=True,
        started=started,
        finished=dumped.finished,
        file=dumped.file,
        size=dumped.size,
        sha256=dumped.sha256,
        sidecar=dumped.sidecar,
    )
    if dumped.later is not None:
        log.warning(
            "backup %s: named earlier than %s in the folder (clock set back?); kept",
            dumped.file.name,
            dumped.later,
        )
        _alert(
            layout,
            "medium",
            f"Yeni veritabanı yedeğinin adı ({dumped.file.name}) klasördeki {dumped.later} "
            "yedeğinden eski; sunucu saati geri alınmış olabilir, yeni yedek saklandı [the new "
            f"database backup {dumped.file.name} is named earlier than {dumped.later} in the "
            "folder; the server clock may have been set back, the new backup was kept]",
            {"file": relative_to_data(config, dumped.file), "later": dumped.later, "key": "clock"},
        )
    try:
        # The dump just made always stays, also when a clock set back names it older than the
        # dumps already there: last.json points at it.
        outcome.pruned = prune_backups(
            dumped.folder, config.backup, now=dumped.finished, protect={dumped.file}
        )
    except OSError as exc:
        outcome.prune_error = f"retention failed: {exc.strerror or exc}"
        log.error("backup retention: %s", outcome.prune_error)
        _alert(
            layout,
            "medium",
            "Eski veritabanı yedekleri silinemedi [old database backups could not be deleted]: "
            + outcome.prune_error,
            # Its own dedup key (R20-backup:prune): not the state of the last backup, which is
            # fine (ok in last.json); resolved by the worker once a later run raises it no more.
            {
                "key": "prune",
                "error": outcome.prune_error,
                "dir": relative_to_data(config, dumped.folder),
            },
        )
    return outcome


def _dump(
    config: Config,
    started: datetime,
    clock: Callable[[], datetime],
    probe: Probe,
    services: Mapping[str, str] | None,
    disk_usage: Callable[[Path], Any],
    attempt: _Attempt,
    anchors: TrustAnchors | None,
) -> _Dumped:
    database = config.database
    folder = backup_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    existing = list_backups(folder)
    newest = existing[0].size if existing else 0
    _check_free_space(folder, config.backup.min_free_gb, newest, disk_usage)
    dump_tool = find_tool("pg_dump", config.backup.pg_dump, anchors=anchors, services=services)
    restore_tool = find_tool(
        "pg_restore", config.backup.pg_restore, anchors=anchors, services=services
    )
    dump_version, dump_major = tool_version(dump_tool)
    attempt.password = database_password(config)
    server = probe(config, attempt.password)
    if dump_major < server.major:
        raise BackupError(
            f"{dump_tool.path} is version {dump_version}, older than the server "
            f"({server.version}); set [backup] pg_dump to the server's pg_dump"
        )
    final = folder / backup_name(started)
    if final.exists():
        raise BackupError(f"{final.name} already exists")
    taken = taken_at(final.name)
    later = existing[0].path.name if existing and taken and existing[0].taken > taken else None
    partial = folder / f".{final.name}.partial"
    attempt.partial = partial
    log.info(
        "backup %s: %s %s (%s), server %s",
        final.name,
        dump_tool.path,
        dump_version,
        dump_tool.found_by,
        server.version,
    )
    command = [
        str(dump_tool.path),
        f"--host={database.host}",
        f"--port={database.port}",
        f"--username={database.user}",
        "--no-password",
        "--format=custom",
        f"--file={partial}",
        f"--dbname={database.name}",
    ]
    with password_file(tool_env(config), database.user, attempt.password) as env:
        result = _run(command, env, DUMP_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise BackupError(_failure("pg_dump", result, attempt.password))
    if not partial.is_file() or partial.stat().st_size == 0:
        raise BackupError("pg_dump reported success but wrote no data")
    fsync_file(partial)
    archive = check_archive(restore_tool, partial)
    digest, size = file_sha256(partial)
    attempt.final = final
    partial.replace(final)
    fsync_dir(folder)
    finished = clock()
    sidecar: dict[str, Any] = {
        "schema": SIDECAR_SCHEMA,
        "file": final.name,
        "sha256": digest,
        "size": size,
        "started": isoformat_site(started, "seconds"),
        "finished": isoformat_site(finished, "seconds"),
        "database": database.name,
        "format": "custom",
        "pg_dump_version": dump_version,
        "server_version": server.version,
        "alembic_revision": server.revision,
        "timescaledb_version": server.timescaledb,
        "toc_entries": archive.entries,
        "arcilogger_db_version": __version__,
    }
    atomic_write_text(sidecar_path(final), json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n")
    return _Dumped(folder, final, size, digest, finished, sidecar, later)


def _discard(partial: Path | None, final: Path | None) -> None:
    """Remove what a failed attempt wrote: never leave a dump that was not fully checked."""
    for path in (partial, final, sidecar_path(final) if final else None):
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # reported, the next prune removes leftovers
            log.warning("could not remove %s: %s", path, exc)


def _alert(layout: Layout, severity: Severity, message: str, details: dict[str, Any]) -> None:
    try:
        raise_alert(layout.alerts, ALERT_RULE, severity, message, details)
    except OSError as exc:
        log.error("could not write alert %s (%s): %s", ALERT_RULE, exc, message)


def _failed(
    config: Config, layout: Layout, started: datetime, finished: datetime, error: str
) -> BackupOutcome:
    log.error("backup failed: %s", error)
    try:
        write_last_state(
            layout, finished=finished, ok=False, file=None, size=None, sha256=None, error=error
        )
    except OSError as exc:
        log.error("could not write %s: %s", last_state_path(layout), exc)
    _alert(
        layout,
        "high",
        f"Veritabanı yedeği alınamadı [database backup failed]: {error}",
        {
            "error": error,
            "database": config.database.name,
            "started": isoformat_site(started, "seconds"),
        },
    )
    return BackupOutcome(ok=False, started=started, finished=finished, error=error)


# -- verify ---------------------------------------------------------------------------------------


@dataclass
class Verification:
    path: Path
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    entries: int | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_backup(path: Path, tool: Tool, *, deep: bool = False) -> Verification:
    """Size and SHA-256 against the sidecar, ``pg_restore --list``; ``deep`` also reads all data."""
    check = Verification(path)
    if not path.is_file():
        check.problems.append("file not found")
        return check
    sidecar = read_sidecar(path)
    if sidecar is None:
        check.notes.append("no sidecar: size and SHA-256 not checked")
    else:
        digest, size = file_sha256(path)
        if size != sidecar.get("size"):
            check.problems.append(f"size {size} differs from the sidecar ({sidecar.get('size')})")
        if digest != sidecar.get("sha256"):
            check.problems.append("SHA-256 differs from the sidecar: damaged or incomplete")
    try:
        check.entries = check_archive(tool, path).entries
        if deep and not check.problems:
            read_archive(tool, path)
            check.notes.append("all data read")
    except BackupError as exc:
        check.problems.append(str(exc))
    return check


# -- restore --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RestoreOutcome:
    database: str
    tables: int
    revision: str | None
    #: The schema revision the sidecar recorded (``None`` without a sidecar).
    expected_revision: str | None
    timescaledb: bool
    #: SHA-256 of the restored dump; checked against ``--sha256``, against a sidecar.
    sha256: str
    sha256_given: bool
    sidecar: bool


def _superuser_engine(config: Config, user: str, password: str | None, database: str) -> Engine:
    target = config.model_copy(
        update={"database": config.database.model_copy(update={"user": user, "name": database})}
    )
    # "" rather than None: make_engine would otherwise use ARCILOGGER's own stored password.
    return make_engine(
        target,
        application="restore",
        password=password or "",
        pool_size=1,
        isolation_level="AUTOCOMMIT",
    )


def check_restore_target(config: Config, target: str) -> str:
    """The new database's name, or :class:`RestoreRefusedError`."""
    name = target.strip()
    if not DATABASE_NAME.fullmatch(name):
        raise RestoreRefusedError(
            f"invalid database name {target!r}: lower-case letters, digits and _ only"
        )
    if name == config.database.name.lower() or name in RESERVED_DATABASES:
        raise RestoreRefusedError(
            f"{name} is the configured (live) or a system database; restore only into a new one"
        )
    return name


def check_expected_sha256(value: str | None) -> str | None:
    """``--sha256`` in lower case, or :class:`RestoreRefusedError` for anything but 64
    hexadecimal digits (an empty PowerShell variable included)."""
    if value is None:
        return None
    cleaned = value.strip()
    if not SHA256_HEX.fullmatch(cleaned):
        raise RestoreRefusedError(
            "--sha256 needs the 64 hexadecimal characters of the dump's SHA-256 "
            "(Get-FileHash -Algorithm SHA256)"
        )
    return cleaned.lower()


def restore_backup(
    config: Config,
    dump: Path,
    target: str,
    *,
    superuser: str,
    password: str | None,
    sha256: str | None = None,
    services: Mapping[str, str] | None = None,
    anchors: TrustAnchors | None = None,
) -> RestoreOutcome:
    """Restore ``dump`` into the new database ``target`` (never into an existing one).

    ``pg_restore`` runs every statement of the archive with the superuser's rights. The sidecar
    lies in the same folder as the dump, and whoever can replace one can replace the other
    (every LOCAL SERVICE process, ADR-0009 section 4): it only detects accidental damage.
    ``sha256``, recorded off the server when the dump was copied, is the check against a
    replaced dump. Both are compared before anything is changed.
    """
    name = check_restore_target(config, target)
    expected = check_expected_sha256(sha256)
    if not ROLE_NAME.fullmatch(superuser):
        raise RestoreRefusedError(f"invalid user name {superuser!r}")
    if not dump.is_file():
        raise RestoreRefusedError(f"{dump}: file not found")
    digest, size = file_sha256(dump)
    if expected is not None and digest != expected:
        raise RestoreRefusedError(
            f"{dump.name}: SHA-256 {digest} differs from --sha256 {expected}; do not restore it"
        )
    sidecar = read_sidecar(dump)
    if sidecar is not None and (digest != sidecar.get("sha256") or size != sidecar.get("size")):
        raise RestoreRefusedError(
            f"{dump.name}: SHA-256 or size differs from its sidecar (damaged or incomplete)"
        )
    tool = find_tool("pg_restore", config.backup.pg_restore, anchors=anchors, services=services)
    archive = check_archive(tool, dump)
    owner = config.database.user
    admin = _superuser_engine(config, superuser, password, "postgres")
    try:
        with admin.connect() as connection:
            quote = connection.dialect.identifier_preparer.quote
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).scalar_one_or_none()
            if exists:
                raise RestoreRefusedError(f"database {name} already exists; choose a new name")
            role = connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": owner}
            ).scalar_one_or_none()
            if not role:
                raise RestoreRefusedError(
                    f"role {owner} ([database] user) does not exist on this server"
                )
            # As the installer creates the live database (Install-Arcilogger.ps1): the C locale,
            # not the cluster's (an update can rename a Windows locale, PostgreSQL BUG #18196,
            # and a Turkish one changes lower() and the sort order). pg_restore without
            # --create does not carry the dumped database's locale.
            connection.execute(
                text(
                    f"CREATE DATABASE {quote(name)} OWNER {quote(owner)} "
                    "TEMPLATE template0 ENCODING 'UTF8' LOCALE 'C'"
                )
            )
            log.info("restore: created database %s (owner %s) for %s", name, owner, dump.name)
            connection.execute(text(f"REVOKE ALL ON DATABASE {quote(name)} FROM PUBLIC"))
            connection.execute(
                text(f"GRANT CONNECT, TEMPORARY ON DATABASE {quote(name)} TO {quote(owner)}")
            )
            if archive.timescaledb:
                # No TimescaleDB telemetry (ADR-0005), set before the extension starts its jobs.
                connection.execute(
                    text(f"ALTER DATABASE {quote(name)} SET timescaledb.telemetry_level = 'off'")
                )
    finally:
        admin.dispose()
    try:
        _restore_into(config, tool, dump, name, superuser, password, archive, sidecar)
        engine = _superuser_engine(config, superuser, password, name)
        try:
            with engine.connect() as connection:
                tables = int(
                    connection.execute(
                        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
                    ).scalar_one()
                )
                revision = ",".join(current_revisions(connection)) or None
        finally:
            engine.dispose()
    except (BackupError, SQLAlchemyError) as exc:
        message = _describe(exc) if isinstance(exc, SQLAlchemyError) else str(exc)
        raise BackupError(
            f"{_redact(message, password)} (the new database {name} is incomplete: drop it "
            f"with DROP DATABASE {name})"
        ) from exc
    log.info(
        "restore: %s restored into %s (%d tables, revision %s)", dump.name, name, tables, revision
    )
    recorded = sidecar.get("alembic_revision") if sidecar else None
    return RestoreOutcome(
        database=name,
        tables=tables,
        revision=revision,
        expected_revision=str(recorded) if recorded else None,
        timescaledb=archive.timescaledb,
        sha256=digest,
        sha256_given=expected is not None,
        sidecar=sidecar is not None,
    )


def _restore_into(
    config: Config,
    tool: Tool,
    dump: Path,
    name: str,
    superuser: str,
    password: str | None,
    archive: ArchiveCheck,
    sidecar: dict[str, Any] | None,
) -> None:
    """``pg_restore`` one transaction per object, stopping at the first error, inside
    TimescaleDB's restore mode when needed.

    Not ``--single-transaction``: that holds a lock on every restored table, index and TOAST
    table until the commit, and a TimescaleDB database (a chunk per hypertable and day, the
    compressed chunks, their indexes) outgrows PostgreSQL's shared lock table
    (``max_locks_per_transaction``, 64 per connection by default) within months — the restore
    would fail with "out of shared memory". The target is always a new database: a failed
    restore is dropped (the error says how) and repeated.
    """
    engine = _superuser_engine(config, superuser, password, name)
    restoring = False
    try:
        if archive.timescaledb:
            version = str((sidecar or {}).get("timescaledb_version") or "")
            clause = f" VERSION '{version}'" if re.fullmatch(r"[0-9A-Za-z.\-]+", version) else ""
            with engine.connect() as connection:
                connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS timescaledb{clause}"))
                connection.execute(text("SELECT timescaledb_pre_restore()"))
            restoring = True
        engine.dispose()  # pg_restore's own session must see the restore mode
        database = config.database
        command = [
            str(tool.path),
            f"--host={database.host}",
            f"--port={database.port}",
            f"--username={superuser}",
            "--no-password",
            f"--dbname={name}",
            "--exit-on-error",
            str(dump),
        ]
        with password_file(tool_env(config), superuser, password) as env:
            result = _run(command, env, RESTORE_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise BackupError(_failure("pg_restore", result, password))
    finally:
        if restoring:
            with engine.connect() as connection:
                connection.execute(text("SELECT timescaledb_post_restore()"))
        engine.dispose()
