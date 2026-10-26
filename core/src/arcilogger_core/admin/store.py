"""The administration agent's files (ADR-0014; contract 2.2, 2.4.8-2.4.10, 2.5.3, 2.7).

:class:`Installation` names the files of the installation folder the agent works on. They all
come from the fixed ``--config`` path the service is started with, never from a key of the
configuration (``secrets_dir`` is one the agent itself writes; contract Day 0 note D0-2).

The agent keeps, below ``admin\\`` (only it can write there):

* ``state\\ledger.json`` — the ids of the requests it took (30 days): a request is applied at most
  once;
* ``state\\txn.json`` — the write-ahead record of the apply in progress (:class:`TxnFile`);
* ``state\\waits.json`` — the dispatched task requests and the system syncs it waits for
  (:class:`WaitsFile`), so waiting never blocks the loop;
* ``pending\\<id>.json`` — a held change with its ops, tokens still encrypted
  (:class:`PendingFile`);
* ``versions\\`` — the configuration pair before each change (:class:`VersionStore`);
* ``status\\`` and ``journal\\`` — what the API and the worker read (contract formats).

Every file is written atomically and parsed strictly (``contract.parse_document``).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import AwareDatetime, Field, JsonValue

from arcilogger_core.admin.contract import (
    JOURNAL_DAYS,
    KEEP_CONFIG_VERSIONS,
    KEEP_SECRETS_VERSIONS,
    LEDGER_DAYS,
    STATUS_DAYS,
    AdminLayout,
    AdminRequest,
    AgentStatus,
    DocumentError,
    Id,
    JournalLine,
    Requester,
    RequestStatus,
    SecretMeta,
    SecretMetaEntry,
    Sha256,
    TaskKind,
    VersionEntry,
    VersionIndex,
    _Model,
    check_id,
    dump_document,
    dump_line,
    parse_document,
)
from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.config import Config
from arcilogger_core.execpolicy import TrustAnchors, ZamanePaths
from arcilogger_core.fsutil import atomic_write_bytes, fsync_dir
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import site_day

#: The name of the configuration file inside a version folder (contract 2.4.10).
VERSION_TOML: Final = "arcilogger.toml"
VERSION_SECRETS: Final = "secrets.json"
VERSION_META: Final = "meta.json"

LEDGER_SCHEMA: Final = "arcilogger.admin-ledger/1"
PENDING_SCHEMA: Final = "arcilogger.admin-pending/1"
WAITS_SCHEMA: Final = "arcilogger.admin-waits/1"
TXN_SCHEMA: Final = "arcilogger.admin-txn/1"
#: The installer's record of the data root, next to the configuration (contract note WP2-1).
DATA_ROOT_RECORD: Final = "data-root.txt"


@dataclass(frozen=True)
class Installation:
    """The installation folder (``C:\\ProgramData\\ARCILOGGER``) of the fixed ``--config`` path."""

    config_path: Path

    @classmethod
    def of(cls, config_path: Path) -> Installation:
        return cls(Path(os.path.abspath(config_path)))  # noqa: PTH100 - lexical, no link followed

    @property
    def root(self) -> Path:
        return self.config_path.parent

    @property
    def toml(self) -> Path:
        return self.config_path

    @property
    def secrets(self) -> Path:
        return self.root / "secrets.json"

    @property
    def secret_key(self) -> Path:
        return self.root / "secret.key"

    @property
    def admin(self) -> AdminLayout:
        return AdminLayout(self.root / "admin")

    @property
    def roots_dir(self) -> Path:
        """``kamusm-roots\\``: the agent adds a root here and never changes or deletes one."""
        return self.root / "kamusm-roots"

    @property
    def smtp_ca(self) -> Path:
        return self.root / "smtp-ca"

    @property
    def zamane(self) -> ZamanePaths:
        return ZamanePaths(self.root / "zamane")

    @property
    def firewall_state(self) -> Path:
        """``install\\firewall-state.json`` of ``Set-ArciFirewall.ps1`` (absent: not managed)."""
        return self.root / "install" / "firewall-state.json"

    def anchors(self) -> TrustAnchors:
        """What ``execpolicy`` trusts, read now (a new ``policy.json`` counts at once)."""
        return TrustAnchors.of(self.config_path)

    def location_problem(self, config: Config) -> str | None:
        """Why ``config`` is not followed, or None: its ``secrets_dir`` is not this folder, or
        its ``data_dir`` is not the data root the installer recorded in ``data-root.txt`` (no
        record: not checked). Nothing the browser can change may move either (contract Day 0
        notes D0-2 and WP2-1)."""
        if not _same_path(config.secrets_dir, self.root):
            return "secrets_dir is not the folder of the configuration file"
        try:
            recorded = (self.root / DATA_ROOT_RECORD).read_text(encoding="utf-8-sig").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            return f"{DATA_ROOT_RECORD} cannot be read"
        if not recorded or not _same_path(config.data_dir, Path(recorded)):
            return f"data_dir is not the data root recorded in {DATA_ROOT_RECORD}"
        return None

    def secret_store(self) -> SecretStore:
        return SecretStore(self.secret_key, self.secrets)

    def temp_file(self, target: Path, request_id: str) -> Path:
        """``.<name>.<id>.tmp`` next to ``target`` (same volume, so the replace is atomic)."""
        return target.with_name(f".{target.name}.{check_id(request_id)}.tmp")


def _same_path(a: Path, b: Path) -> bool:
    """Lexically the same absolute path (Windows: case-insensitive); no link is followed."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))  # noqa: PTH100


def read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def write_atomic(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def append_line(path: Path, data: bytes) -> None:
    """Append one line and flush it to disk; a torn last line stays a line of its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                data = b"\n" + data
            handle.seek(0, os.SEEK_END)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


# -- the ledger ----------------------------------------------------------------------------------


class LedgerFile(_Model):
    schema_: Literal["arcilogger.admin-ledger/1"] = Field(default=LEDGER_SCHEMA, alias="schema")
    #: request id -> when it was taken.
    ids: dict[Id, AwareDatetime] = Field(default_factory=dict)


class Ledger:
    """The ids of the requests taken; written (fsync) before a request has any effect."""

    def __init__(self, layout: AdminLayout) -> None:
        self._path = layout.ledger

    def _read(self) -> LedgerFile:
        data = read_bytes_or_none(self._path)
        return LedgerFile() if data is None else parse_document(LedgerFile, data)

    def __contains__(self, request_id: object) -> bool:
        return isinstance(request_id, str) and request_id in self._read().ids

    def add(self, request_id: str, at: datetime) -> None:
        current = self._read()
        ids = {**current.ids, check_id(request_id): at}
        write_atomic(self._path, dump_document(LedgerFile(ids=ids)))

    def prune(self, now: datetime) -> int:
        current = self._read()
        keep = {
            key: at for key, at in current.ids.items() if now - at <= timedelta(days=LEDGER_DAYS)
        }
        if len(keep) != len(current.ids):
            write_atomic(self._path, dump_document(LedgerFile(ids=keep)))
        return len(current.ids) - len(keep)


# -- statuses, journal, secret meta, alerts --------------------------------------------------------


def write_status(layout: AdminLayout, status: RequestStatus) -> None:
    write_atomic(layout.request_status(status.id), dump_document(status))


def read_status(layout: AdminLayout, request_id: str) -> RequestStatus | None:
    data = read_bytes_or_none(layout.request_status(request_id))
    if data is None:
        return None
    try:
        return parse_document(RequestStatus, data)
    except DocumentError:
        return None


def write_heartbeat(layout: AdminLayout, status: AgentStatus) -> None:
    write_atomic(layout.agent_status, dump_document(status))


def append_journal(layout: AdminLayout, line: JournalLine) -> None:
    """One outcome into ``journal\\<site day>.jsonl`` (fsync before the agent moves on)."""
    append_line(layout.journal_file(site_day(line.at)), dump_line(line))


def read_journal(layout: AdminLayout, day: date) -> list[JournalLine]:
    path = layout.journal_file(day)
    data = read_bytes_or_none(path)
    if data is None:
        return []
    lines = []
    for raw in data.splitlines():
        try:
            lines.append(parse_document(JournalLine, raw))
        except DocumentError:
            continue  # a torn line after a power cut
    return lines


def update_secret_meta(
    layout: AdminLayout, names: Iterable[str], *, changed_by: str, at: datetime
) -> None:
    """Record who changed which stored secret, when (names only)."""
    names = list(names)
    if not names:
        return
    data = read_bytes_or_none(layout.secret_meta)
    try:
        current = SecretMeta() if data is None else parse_document(SecretMeta, data)
    except DocumentError:
        current = SecretMeta()
    entries = dict(current.secrets)
    for name in names:
        entries[name] = SecretMetaEntry(changed_at=at, changed_by=changed_by)
    write_atomic(layout.secret_meta, dump_document(SecretMeta(secrets=entries)))


def admin_alert(
    layout: AdminLayout,
    rule: str,
    severity: Severity,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """An alert line in ``status\\alerts.jsonl`` (the worker's ``alerts.import`` reads it)."""
    raise_alert(layout.alerts, rule, severity, message, details)


# -- versions ----------------------------------------------------------------------------------


class VersionStore:
    """``versions\\<n>\\``: the pair before request *n* (contract 2.4.10). The newest
    :data:`~arcilogger_core.admin.contract.KEEP_CONFIG_VERSIONS` configurations and the newest
    :data:`~arcilogger_core.admin.contract.KEEP_SECRETS_VERSIONS` secret stores are kept."""

    def __init__(self, layout: AdminLayout) -> None:
        self._layout = layout

    def index(self) -> VersionIndex:
        data = read_bytes_or_none(self._layout.versions_index)
        return VersionIndex() if data is None else parse_document(VersionIndex, data)

    def entry(self, number: int) -> VersionEntry | None:
        return next((v for v in self.index().versions if v.n == number), None)

    def for_request(self, request_id: str) -> VersionEntry | None:
        """The version saved before ``request_id`` was applied."""
        return next((v for v in self.index().versions if v.request == request_id), None)

    def save(
        self,
        *,
        toml: bytes,
        secrets: bytes | None,
        request: str | None,
        actor: str,
        paths: Sequence[str],
        at: datetime,
        toml_sha256: str,
        secrets_sha256: str | None,
    ) -> VersionEntry:
        """Keep the pair as the next version and prune the old ones."""
        index = self.index()
        number = max((v.n for v in index.versions), default=0) + 1
        folder = self._layout.version_dir(number)
        remove_tree(folder)  # a leftover of an interrupted save
        folder.mkdir(parents=True)
        write_atomic(folder / VERSION_TOML, toml)
        if secrets is not None:
            write_atomic(folder / VERSION_SECRETS, secrets)
        entry = VersionEntry(
            n=number,
            at=at,
            request=request,
            actor=actor,
            paths=tuple(paths),
            toml_sha256=toml_sha256,
            secrets_sha256=secrets_sha256,
            has_secrets=secrets is not None,
        )
        write_atomic(folder / VERSION_META, dump_document(entry))
        self._write(VersionIndex(versions=(*index.versions, entry)))
        return entry

    def read(self, number: int) -> tuple[bytes, bytes | None]:
        """The configuration and (while kept) the secret store of version ``number``."""
        folder = self._layout.version_dir(number)
        toml = (folder / VERSION_TOML).read_bytes()
        return toml, read_bytes_or_none(folder / VERSION_SECRETS)

    def _write(self, index: VersionIndex) -> None:
        versions = sorted(index.versions, key=lambda v: v.n)
        drop = versions[:-KEEP_CONFIG_VERSIONS] if len(versions) > KEEP_CONFIG_VERSIONS else []
        kept = versions[len(drop) :]
        with_secrets = [v for v in kept if v.has_secrets]
        strip = {v.n for v in with_secrets[:-KEEP_SECRETS_VERSIONS]} if with_secrets else set()
        final = []
        for version in kept:
            if version.n in strip:
                remove_file(self._layout.version_dir(version.n) / VERSION_SECRETS)
                version = version.model_copy(update={"has_secrets": False})
                write_atomic(
                    self._layout.version_dir(version.n) / VERSION_META, dump_document(version)
                )
            final.append(version)
        write_atomic(
            self._layout.versions_index, dump_document(VersionIndex(versions=tuple(final)))
        )
        for version in drop:
            remove_tree(self._layout.version_dir(version.n))
        if drop or strip:
            fsync_dir(self._layout.versions)


# -- held changes --------------------------------------------------------------------------------


class PendingFile(_Model):
    """A held change: its verified request (tokens still encrypted) until it takes effect or is
    cancelled."""

    schema_: Literal["arcilogger.admin-pending/1"] = Field(default=PENDING_SCHEMA, alias="schema")
    id: Id
    #: ``hold``: ``policy.hold_hours``; ``jar``: a Zamane client waiting for its approval.
    kind: Literal["hold", "jar"]
    #: When it takes effect (a jar: the earliest time; it waits for ``jars.json``).
    effective_at: AwareDatetime
    jar_sha256: Sha256 | None = None
    held_at: AwareDatetime
    request: AdminRequest


def write_pending(layout: AdminLayout, pending: PendingFile) -> None:
    write_atomic(layout.pending_file(pending.id), dump_document(pending))


def read_pending(layout: AdminLayout) -> list[PendingFile]:
    found = []
    if layout.pending.is_dir():
        for path in sorted(layout.pending.glob("*.json")):
            try:
                found.append(parse_document(PendingFile, path.read_bytes()))
            except (OSError, DocumentError):
                continue
    return sorted(found, key=lambda p: p.held_at)


def remove_pending(layout: AdminLayout, request_id: str) -> bool:
    path = layout.pending_file(request_id)
    if not path.exists():
        return False
    path.unlink()
    return True


# -- waits ---------------------------------------------------------------------------------------


class TaskWait(_Model):
    """A dispatched task request: its result folds into the origin request."""

    task: Id
    origin: Id
    kind: TaskKind
    dispatched_at: AwareDatetime
    #: Who asked for the action (its journal line names them).
    requested_by: Requester


class SyncWait(_Model):
    """A change of the syslog senders: ApplySystem is woken again (Event 4101) once the
    collector reports the new configuration, so a new sender is let in only then."""

    request: Id
    config_sha256: Sha256
    since: AwareDatetime


class WaitsFile(_Model):
    schema_: Literal["arcilogger.admin-waits/1"] = Field(default=WAITS_SCHEMA, alias="schema")
    tasks: tuple[TaskWait, ...] = ()
    syncs: tuple[SyncWait, ...] = ()


def waits_path(layout: AdminLayout) -> Path:
    return layout.state / "waits.json"


def read_waits(layout: AdminLayout) -> WaitsFile:
    data = read_bytes_or_none(waits_path(layout))
    try:
        return WaitsFile() if data is None else parse_document(WaitsFile, data)
    except DocumentError:
        return WaitsFile()


def write_waits(layout: AdminLayout, waits: WaitsFile) -> None:
    write_atomic(waits_path(layout), dump_document(waits))


# -- the write-ahead record ----------------------------------------------------------------------


class Digests(_Model):
    toml_sha256: Sha256
    #: None: there is no ``secrets.json`` (before), or it is not rewritten (after).
    secrets_sha256: Sha256 | None


class TxnFile(_Model):
    """``state\\txn.json``: an apply in progress (contract 2.7.1 steps 6-12).

    ``prepared``: nothing replaced yet; ``written``: the replace of the files has begun, so a
    crash leaves the old pair, the new pair or (between the two replaces) a mix that recovery
    resolves from the hashes."""

    schema_: Literal["arcilogger.admin-txn/1"] = Field(default=TXN_SCHEMA, alias="schema")
    id: Id
    #: The request; None for a console ``versions restore``.
    request: Id | None
    version: Annotated[int, Field(ge=1)]
    old: Digests
    new: Digests
    stage: Literal["prepared", "written"]
    #: Files this apply installs (roots, a CA file) that did not exist before.
    files: tuple[str, ...] = ()
    #: Names of the secrets the apply changes (checked with ``SecretStore.get``).
    secret_names: tuple[str, ...] = ()
    #: The journal line to write when the apply completes (without ``line``, ``state``, ``at``).
    journal: dict[str, JsonValue] | None = None
    started_at: AwareDatetime


def read_txn(layout: AdminLayout) -> TxnFile | None:
    data = read_bytes_or_none(layout.txn)
    return None if data is None else parse_document(TxnFile, data)


def write_txn(layout: AdminLayout, txn: TxnFile) -> None:
    write_atomic(layout.txn, dump_document(txn))


def remove_txn(layout: AdminLayout) -> None:
    remove_file(layout.txn)
    if layout.state.is_dir():
        fsync_dir(layout.state)


# -- cleanup -------------------------------------------------------------------------------------


def prune_statuses(layout: AdminLayout, now: datetime) -> int:
    """Request status files older than 30 days (``agent.json``, ``secret-meta.json`` and the
    alert file stay)."""
    removed = 0
    if not layout.status.is_dir():
        return 0
    limit = (now - timedelta(days=STATUS_DAYS)).timestamp()
    for path in layout.status.glob("*.json"):
        if path.name in {layout.agent_status.name, layout.secret_meta.name}:
            continue
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def prune_journal(layout: AdminLayout, today: date) -> int:
    removed = 0
    if not layout.journal.is_dir():
        return 0
    for path in layout.journal.glob("*.jsonl"):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if (today - day).days > JOURNAL_DAYS:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
