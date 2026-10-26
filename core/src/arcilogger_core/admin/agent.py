"""The administration agent ``ArciAdmin`` (ADR-0014; contract 2.5.2, 2.5.3, 2.7, 2.9, 2.10).

``python -I -X utf8 -u -m arcilogger_core.admin serve --config <path>`` runs it as the virtual
account ``NT SERVICE\\ArciAdmin``: no listener, no administrator rights, the only writer of
``arcilogger.toml`` and ``secrets.json``. Every second :meth:`Agent.run_once` takes what the API
put into ``admin\\inbox\\`` and works through it in this order (the first failure wins):

1. **claim** — ``inbox\\<id>.req`` is moved into ``work\\<id>\\``, which only the agent can write;
   nothing is read from the inbox again (a file changed after the claim is irrelevant);
2. **verify** — size, key id, signature over the raw payload, and only then the payload
   (``admin.envelope``): schema, installation, issue and expiry times;
3. **ledger** — a request id is taken once (``state\\ledger.json``, written before any effect);
   the uploads it names are copied into the work folder and must match their SHA-256 and size;
4. **validate** — the ``expect`` values against the files in force (``conflict``), the rules
   of the section (:mod:`~arcilogger_core.admin.rules`, authoritative), the re-authentication a
   change needs, the edited file (:mod:`~arcilogger_core.admin.tomledit`) and the new store;
5. **probe** — the candidate values are tested (:mod:`~arcilogger_core.admin.probe`);
6. **hold** — a guarded change waits for ``policy.hold_hours``; a Zamane client is staged and
   waits for its approval (``jars.json``); one that is due while browser administration is
   off fails (``admin_disabled``);
7. **apply** and **verify** — one transaction (:mod:`~arcilogger_core.admin.apply`);
8. **journal** — the outcome, with old and new values, secrets as ``set``/``unset`` only.

Evidence actions are not run here: the agent signs a task request for the Seal or the Requests
task (:mod:`~arcilogger_core.admin.dispatch`) and folds its result in when it appears. Waiting —
for a task, a hold, an approval or the collector's acknowledgement of new syslog senders — never
blocks the loop: the waits are files that each pass looks at. The agent reports every request in
``status\\<id>.json`` (codes only), writes its heartbeat ``status\\agent.json`` every 10 seconds,
the Application log (source ``ARCILOGGER-Admin``) and its alerts (``status\\alerts.jsonl``).

No licence state, database or network service is needed for any of it; the 5651 pipeline never
waits for the agent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import JsonValue

from arcilogger_core import __version__
from arcilogger_core.admin import rules, tomledit
from arcilogger_core.admin.apply import Applier, ApplyOutcome, ChangePlan
from arcilogger_core.admin.contract import (
    ACTION_TASKS,
    HEARTBEAT_INTERVAL,
    JOURNAL_DAYS,
    MAX_REQUEST_BYTES,
    MAX_SEAL_REQUEST_VALIDITY,
    MAX_TASK_REQUEST_VALIDITY,
    REAUTH_WINDOW,
    SYSTEM_TASK_TIMEOUT,
    TASK_TIMEOUT,
    TERMINAL_STATES,
    TMP_MAX_AGE,
    UPLOAD_LIFETIME,
    UPLOAD_LIMITS,
    AdminRequest,
    AgentStatus,
    DocumentError,
    ErrorRef,
    Event,
    JarsFile,
    JournalLine,
    JournalState,
    KeysFile,
    PendingRequest,
    Requester,
    RequestKind,
    RequestStatus,
    RootAddOp,
    SealReady,
    SecretDeleteOp,
    SecretRecord,
    SecretSetOp,
    Section,
    SetOp,
    SmtpCaClearOp,
    SmtpCaSetOp,
    SourceAddOp,
    SourceRemoveOp,
    SourceUpdateOp,
    State,
    Step,
    StepEntry,
    StepState,
    SystemTaskCheck,
    TaskKind,
    TaskParams,
    TaskResult,
    UploadKind,
    VersionEntry,
    VersionRef,
    WarningRef,
    ZamaneStageOp,
    check_id,
    new_id,
    parse_document,
    task_queue,
)
from arcilogger_core.admin.dispatch import (
    new_task_request,
    read_result,
    task_event,
    write_task_request,
)
from arcilogger_core.admin.envelope import (
    ADMIN_REQUEST,
    EnvelopeError,
    open_admin_request,
    verify,
)
from arcilogger_core.admin.keys import KeysError, load_keys, load_signing_key
from arcilogger_core.admin.messages import MESSAGES, WARNINGS
from arcilogger_core.admin.policy import read_policy
from arcilogger_core.admin.probe import ChildProber, Prober, ProbeResult, redact
from arcilogger_core.admin.roots import installed_roots
from arcilogger_core.admin.store import (
    Installation,
    Ledger,
    PendingFile,
    SyncWait,
    TaskWait,
    WaitsFile,
    admin_alert,
    append_journal,
    prune_journal,
    prune_statuses,
    read_bytes_or_none,
    read_journal,
    read_pending,
    read_status,
    read_waits,
    remove_pending,
    remove_tree,
    update_secret_meta,
    write_heartbeat,
    write_pending,
    write_status,
    write_waits,
)
from arcilogger_core.admin.winapi import (
    EventLevel,
    EventLog,
    can_write,
    default_event_log,
    replace_file,
)
from arcilogger_core.admin.zamane import client_bytes
from arcilogger_core.config import Config, parse_config
from arcilogger_core.configwatch import config_status_of
from arcilogger_core.execpolicy import ExecutableRefusedError, check_java, check_zamane_jar
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError, SecretValueError
from arcilogger_core.timeutil import isoformat_site, site_day, to_site, utc_now
from arcilogger_core.tsa.preflight import seal_preflight
from arcilogger_core.tsa.zamane_releases import find_release

log = logging.getLogger("arcilogger.admin.agent")

#: How often the agent cleans up the inbox, old uploads, statuses, the ledger and the journal.
CLEANUP_INTERVAL: Final = timedelta(minutes=1)
#: How long the agent waits for the collector to report a new configuration before it leaves
#: the widening of the syslog rule to ApplySystem's own 5-minute runs.
SYNC_WAIT_MAX: Final = timedelta(minutes=10)
#: A collector status file older than this means a stopped collector (counts as acknowledged).
COLLECTOR_FRESH: Final = timedelta(seconds=60)
#: How long a task may still run after it took a request (the execution time limits of the Seal
#: and Requests tasks): a request the task took is waited for until it expired plus this.
TASK_RUN_LIMITS: Final[Mapping[TaskKind, timedelta]] = {
    "seal": timedelta(hours=2),
    "credit": timedelta(minutes=15),
    "tsa_test": timedelta(minutes=15),
    "replica_test": timedelta(minutes=15),
}
#: After a request no task took within ``TASK_TIMEOUT`` is withdrawn, how long the agent still
#: looks for a task that read it just before (its ledger entry follows the read at once).
WITHDRAW_GRACE: Final = timedelta(seconds=30)
#: The address the trial e-mail links to when the server's own address is not known.
DEFAULT_WEB_URL: Final = "https://arcilogger.internal"
_PATH: Final = re.compile(r"^[a-z_]+(\.[a-z0-9][a-z0-9_-]*)*$")
_JAVA_PATTERNS: Final = ("Eclipse Adoptium/jre-8*/bin/java.exe", "Java/jre1.8*/bin/java.exe")
_UPLOAD_KINDS: Final[Mapping[str, UploadKind]] = {
    "root_add": "kamusm_root",
    "zamane_stage": "zamane_client",
    "smtp_ca_set": "smtp_ca",
}
#: The journal states that report the apply of a configuration change.
_APPLY_OUTCOMES: Final = frozenset({"applied", "recovered", "needs_attention"})
Clock = Callable[[], datetime]


def _program_files() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        roots.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")))  # noqa: SIM112
    return roots


def discover_java(program_files: Sequence[Path] | None = None) -> tuple[str, ...]:
    """The Java 8 runtimes the browser may choose for the Zamane client (contract 2.6 tsa):
    ``%ProgramFiles%\\Eclipse Adoptium\\jre-8*\\bin\\java.exe`` and
    ``%ProgramFiles%\\Java\\jre1.8*\\bin\\java.exe``, with forward slashes."""
    if program_files is None:
        program_files = _program_files()
    found: list[str] = []
    for root in program_files:
        for pattern in _JAVA_PATTERNS:
            found += [path.as_posix() for path in sorted(root.glob(pattern)) if path.is_file()]
    return tuple(dict.fromkeys(found))


def discover_addresses() -> tuple[str, ...]:
    """This server's own IPv4 addresses (a source may not be one of them)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(info[4][0]))
    except OSError:
        pass
    return tuple(sorted(a for a in found if not ipaddress.ip_address(a).is_loopback))


def web_url(config: Config) -> str:
    """``https://<[collector] bind>`` when that is one address of this server, else
    :data:`DEFAULT_WEB_URL` (as the worker's alert e-mails without ``alerts.web_base_url``)."""
    try:
        bind = ipaddress.ip_address(config.collector.bind)
    except ValueError:
        return DEFAULT_WEB_URL
    if bind.is_unspecified or bind.is_loopback:
        return DEFAULT_WEB_URL
    return f"https://[{bind}]" if bind.version == 6 else f"https://{bind}"


def _known(code: str | None, fallback: str) -> str:
    return code if code is not None and code in MESSAGES else fallback


def _field(path: str | None) -> str | None:
    return path if path is not None and _PATH.fullmatch(path) and len(path) <= 200 else None


def _recent_reauth(request: AdminRequest) -> bool:
    reauth = request.actor.reauth_at
    return reauth is not None and abs(request.issued_at - reauth) <= REAUTH_WINDOW


@dataclass
class _Files:
    """The configuration pair in force."""

    toml: bytes
    secrets: bytes | None
    config: Config | None
    stored: frozenset[str]
    error: str | None = None


@dataclass
class _Finish:
    """What the outcome of an apply reports (kept in ``txn.json`` for a recovery)."""

    status_kind: RequestKind
    section: Section | None
    requested_by: Requester
    changes: dict[str, Any]
    secrets: list[SecretRecord]
    warnings: list[str] = field(default_factory=list)
    alert: str | None = None
    fields: list[str] = field(default_factory=list)
    system_sync: bool = False
    sources: bool = False
    followups: list[str] = field(default_factory=list)
    installs: int = 0

    def dump(self) -> dict[str, JsonValue]:
        return {
            "kind": self.status_kind,
            "section": self.section,
            "requested_by": self.requested_by.model_dump(mode="json"),
            "changes": self.changes,
            "secrets": [record.model_dump(mode="json") for record in self.secrets],
            "warnings": list(self.warnings),
            "alert": self.alert,
            "fields": list(self.fields),
            "system_sync": self.system_sync,
            "sources": self.sources,
            "followups": list(self.followups),
            "installs": self.installs,
        }

    @classmethod
    def load(cls, data: Mapping[str, Any]) -> _Finish:
        return cls(
            status_kind=data["kind"],
            section=data["section"],
            requested_by=Requester.model_validate(data["requested_by"]),
            changes=dict(data["changes"]),
            secrets=[SecretRecord.model_validate(item) for item in data["secrets"]],
            warnings=list(data.get("warnings", [])),
            alert=data.get("alert"),
            fields=list(data.get("fields", [])),
            system_sync=bool(data.get("system_sync")),
            sources=bool(data.get("sources")),
            followups=list(data.get("followups", [])),
            installs=int(data.get("installs", 0)),
        )


class _Progress:
    """The status file of one request, rewritten at every step (codes only)."""

    def __init__(
        self, agent: Agent, request_id: str, kind: RequestKind | None, section: Section | None
    ) -> None:
        self.agent = agent
        self.id = request_id
        self.kind = kind
        self.section = section
        self.steps: dict[Step, StepEntry] = {}
        existing = read_status(agent.layout, request_id)
        if existing is not None and existing.kind == kind:
            self.steps = {entry.step: entry for entry in existing.steps}

    def step(self, name: Step, state: StepState, code: str | None = None) -> None:
        self.steps[name] = StepEntry(step=name, state=state, at=self.agent.now(), code=code)

    def write(
        self,
        state: State,
        *,
        error: str | None = None,
        field_path: str | None = None,
        version: VersionRef | None = None,
        effective_at: datetime | None = None,
        result: Any = None,
        warnings: Iterable[str] = (),
        rolled_back: bool = False,
    ) -> RequestStatus:
        status = RequestStatus(
            id=self.id,
            kind=self.kind,
            section=self.section,
            state=state,
            steps=tuple(self.steps.values()),
            version=version,
            effective_at=effective_at,
            result=result,
            error=ErrorRef(code=error, field=_field(field_path)) if error else None,
            warnings=tuple(
                WarningRef(code=code) for code in dict.fromkeys(warnings) if code in WARNINGS
            ),
            rolled_back=rolled_back,
            updated_at=self.agent.now(),
        )
        write_status(self.agent.layout, status)
        return status


class Agent:
    """One installation's administration agent (``serve`` runs :meth:`run_once` every second)."""

    def __init__(
        self,
        config_path: Path,
        *,
        now: Clock | None = None,
        event_log: EventLog | None = None,
        prober: Prober | None = None,
        java_candidates: Callable[[], tuple[str, ...]] = discover_java,
        local_addresses: Callable[[], tuple[str, ...]] = discover_addresses,
        replace: Callable[[Path, Path], None] = replace_file,
        writable: Callable[[Path], bool] = can_write,
        sleep: Callable[[float], None] = time.sleep,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.installation = Installation.of(config_path)
        self.layout = self.installation.admin
        clock = now or utc_now
        #: Site time, as every state file of the channel writes it (Day 0 note D0-10).
        self.now: Clock = lambda: to_site(clock())
        self.events = event_log or default_event_log()
        self.prober = prober or ChildProber()
        self._java = java_candidates
        self._addresses = local_addresses
        self.applier = Applier(
            self.installation,
            now=self.now,
            replace=replace,
            writable=writable,
            sleep=sleep,
            checkpoint=checkpoint,
        )
        self.ledger = Ledger(self.layout)
        self.started_at = self.now()
        self.data: Layout | None = None
        self._data_dir: Path | None = None
        self._java_candidates: tuple[str, ...] = ()
        self._local_addresses: tuple[str, ...] = ()
        self._last_heartbeat: datetime | None = None
        self._last_cleanup: datetime | None = None
        self._ping_at: datetime | None = None
        self._system_mark: float | None = None
        self._system_seen_at: datetime | None = None
        self._production_tested: bool | None = None
        self._keys_error: str | None = None
        self._busy: str | None = None

    # -- life cycle --------------------------------------------------------------------------------

    def start(self) -> None:
        """Before any request: the folders, a crash recovery, the start events, a heartbeat."""
        for folder in (
            self.layout.inbox,
            self.layout.uploads,
            self.layout.work,
            self.layout.pending,
            self.layout.state,
            self.layout.versions,
            self.layout.status,
            self.layout.journal,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        files = self._files()  # takes the data root when the configuration loads
        if files.config is None:
            log.error("configuration %s does not load: %s", self.installation.toml, files.error)
        self._java_candidates = self._java()
        self._local_addresses = self._addresses()
        self.recover()
        self._abandoned_work()
        self._event(Event.AGENT_STARTED, "info", f"ArciAdmin {__version__} başladı [started]")
        self._ping_system()
        self.heartbeat()

    def stop(self) -> None:
        self._event(Event.AGENT_STOPPING, "info", "ArciAdmin duruyor [stopping]")
        self.heartbeat()

    def run_once(self) -> None:
        """One pass: waits, held changes, the inbox, the heartbeat and the cleanup (never
        raises: a broken pass is logged and the next one tries again)."""
        try:
            self._pass()
        except Exception:
            log.exception("administration agent pass failed")
        finally:
            self._busy = None

    def _pass(self) -> None:
        now = self.now()
        if self._last_heartbeat is None or now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            self.heartbeat()
        self._fold_waits()
        self._due_pending()
        if self._take_inbox():
            self.heartbeat()
        if self._last_cleanup is None or now - self._last_cleanup >= CLEANUP_INTERVAL:
            self._cleanup(now)
            self._last_cleanup = now

    # -- events, alerts, journal ---------------------------------------------------------------

    def _event(self, event: Event, level: EventLevel, message: str) -> None:
        try:
            self.events.write(event, level, message)
        except Exception:  # an event never fails a change
            log.exception("event %d not written", int(event))

    def _alert(
        self,
        rule: str,
        severity: Literal["medium", "high", "critical"],
        message: str,
        details: dict[str, Any],
    ) -> None:
        try:
            admin_alert(self.layout, rule, severity, message, details)
        except OSError:
            log.exception("alert %s not written", rule)

    def _journal(
        self,
        request_id: str,
        state: JournalState,
        *,
        kind: RequestKind | None,
        section: Section | None,
        requested_by: Requester | None,
        version: int | None = None,
        transition: tuple[str, str] | None = None,
        changes: Mapping[str, Any] | None = None,
        secrets: Sequence[SecretRecord] = (),
        code: str | None = None,
        rolled_back: bool = False,
        result: Any = None,
    ) -> None:
        line = JournalLine.model_validate(
            {
                "line": new_id(),
                "request": request_id,
                "state": state,
                "at": self.now(),
                "kind": kind,
                "section": section,
                "requested_by": requested_by,
                "version": version,
                "config_sha256": (
                    {"from": transition[0], "to": transition[1]} if transition else None
                ),
                "changes": dict(changes or {}),
                "secrets": list(secrets),
                "code": code,
                "rolled_back": rolled_back,
                "result": result,
            }
        )
        append_journal(self.layout, line)

    # -- the heartbeat -------------------------------------------------------------------------

    def heartbeat(self) -> AgentStatus:
        now = self.now()
        policy = read_policy(self.layout.policy)
        try:
            key_ids = tuple(sorted(load_keys(self.layout.keys).public_keys("api")))
        except KeysError:
            key_ids = ()
        queue = (
            sum(1 for path in self.layout.inbox.glob("*.req")) if self.layout.inbox.is_dir() else 0
        )
        status = AgentStatus(
            version=__version__,
            pid=os.getpid(),
            started_at=self.started_at,
            heartbeat_at=now,
            state="busy" if self._busy else "idle",
            current_request=self._busy,
            queue=queue,
            pending=tuple(
                PendingRequest(id=item.id, effective_at=item.effective_at)
                for item in read_pending(self.layout)
            ),
            policy_sha256=policy.sha256,
            key_ids=key_ids,
            java_candidates=self._java_candidates,
            local_addresses=self._local_addresses,
            seal_ready=self._seal_ready(),
            system_task=self._system_task(now),
        )
        write_heartbeat(self.layout, status)
        self._last_heartbeat = now
        return status

    def _seal_ready(self) -> SealReady:
        files = self._files()
        if files.config is None:
            return SealReady(ok=False, causes=("provider",))
        causes = seal_preflight(
            files.config,
            anchors=self.installation.anchors(),
            secrets=self.installation.secret_store(),
        )
        return SealReady(ok=not causes, causes=tuple(causes))

    def _system_status_mark(self) -> float | None:
        try:
            return self.layout.system_status.stat().st_mtime
        except OSError:
            return None

    def _ping_system(self) -> None:
        """Wake ApplySystem once at start: its ``system\\status.json`` must change within
        5 minutes (contract 2.4.7 ``system_task``)."""
        self._ping_at = self.now()
        self._system_mark = self._system_status_mark()
        self._event(Event.SYSTEM_SYNC_REQUESTED, "info", "sistem eşitlemesi [system sync]")

    def _system_task(self, now: datetime) -> SystemTaskCheck:
        if self._system_seen_at is None and self._ping_at is not None:
            mark = self._system_status_mark()
            if mark is not None and mark != self._system_mark:
                self._system_seen_at = now
        if self._system_seen_at is not None:
            return SystemTaskCheck(ok=True, checked_at=self._system_seen_at)
        if self._ping_at is None or now - self._ping_at < SYSTEM_TASK_TIMEOUT:
            return SystemTaskCheck(ok=True)
        return SystemTaskCheck(ok=False, checked_at=now, error="system_sync_timeout")

    # -- the files in force --------------------------------------------------------------------

    def _files(self) -> _Files:
        try:
            toml = self.installation.toml.read_bytes()
            secrets = read_bytes_or_none(self.installation.secrets)
        except OSError as exc:
            return _Files(b"", None, None, frozenset(), f"{type(exc).__name__}: {exc}")
        stored: frozenset[str] = frozenset()
        try:
            if secrets is not None:
                data = json.loads(secrets.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("secrets.json is not an object")
                stored = frozenset(str(name) for name in data)
        except ValueError as exc:
            return _Files(toml, secrets, None, stored, f"secrets.json: {exc}")
        try:
            config = parse_config(toml, self.installation.toml)
        except ValueError as exc:
            return _Files(toml, secrets, None, stored, f"{type(exc).__name__}")
        problem = self._fixed_paths(config)
        if problem is not None:
            return _Files(toml, secrets, None, stored, problem)
        if self.data is None:
            self._adopt(config)
        return _Files(toml, secrets, config, stored)

    def _adopt(self, config: Config) -> None:
        """The data root (task requests and results, the collector's status), taken from the
        first configuration that passes the location check: at start, or later when the agent
        started while ``arcilogger.toml`` did not load. From then on it no longer moves."""
        self._data_dir = config.data_dir
        self.data = Layout(config.data_dir)
        log.info("data root: %s", config.data_dir)

    def _fixed_paths(self, config: Config) -> str | None:
        """Nothing the browser can change may move the secret store or the data root (contract
        Day 0 note D0-2): a configuration that does is not worked on."""
        problem = self.installation.location_problem(config)
        if problem is not None:
            return problem
        if self._data_dir is not None and config.data_dir != self._data_dir:
            return "data_dir differs from the one the agent started with"
        return None

    def _strict_outbound(self) -> bool:
        try:
            data = json.loads(self.installation.firewall_state.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and data.get("strict_outbound") is True

    def approved_jars(self) -> frozenset[str]:
        try:
            jars = parse_document(JarsFile, self.layout.jars.read_bytes())
        except (OSError, DocumentError):
            return frozenset()
        return frozenset(entry.sha256 for entry in jars.approved)

    def _tested_on_production(self) -> bool:
        if self._production_tested:
            return True
        today = site_day(self.now())
        for back in range(31):
            for line in read_journal(self.layout, today - timedelta(days=back)):
                result = line.result
                if (
                    line.kind == "action.tsa_test"
                    and result is not None
                    and getattr(result, "verified", False)
                    and getattr(result, "legal_grade_claimed", False)
                ):
                    self._production_tested = True
                    return True
        return False

    def context(
        self, files: _Files, uploads: Mapping[str, rules.UploadInfo] | None = None
    ) -> rules.RuleContext:
        """What the rules need, read now."""
        config = files.config
        anchors = self.installation.anchors()
        store = self.installation.secret_store()
        roots_dir = (config.tsa.roots_dir if config else None) or self.installation.roots_dir
        return rules.RuleContext(
            policy=read_policy(self.layout.policy).policy,
            anchors=anchors,
            stored_secrets=files.stored,
            now=self.now(),
            java_candidates=self._java_candidates,
            local_addresses=self._local_addresses,
            installed_roots=frozenset(root.sha256 for root in installed_roots(roots_dir)),
            approved_jars=self.approved_jars(),
            uploads=dict(uploads or {}),
            preflight=lambda candidate, names: seal_preflight(
                candidate, anchors=anchors, secrets=store, stored=names
            ),
            strict_outbound=self._strict_outbound(),
            production_tested=self._tested_on_production(),
        )

    # -- the inbox -------------------------------------------------------------------------------

    def _claim(self) -> list[tuple[str, Path]]:
        """Move every request file into its work folder (step 1)."""
        claimed: list[tuple[str, Path]] = []
        inbox = self.layout.inbox
        if not inbox.is_dir():
            return claimed
        for path in sorted(inbox.glob("*.req")):
            try:
                request_id = check_id(path.stem)
            except ValueError:
                path.unlink(missing_ok=True)
                log.warning("inbox file %s is not named by a request id; removed", path.name)
                continue
            folder = self.layout.work_dir(request_id)
            target = folder / "request.req"
            if target.exists():  # the id is still being worked on: this file repeats it
                target = folder / f"repeat-{new_id()}.req"
            folder.mkdir(parents=True, exist_ok=True)
            try:
                path.replace(target)
            except FileNotFoundError:
                continue  # removed by the API meanwhile (a cancelled queued request)
            claimed.append((request_id, target))
        # Claims of an earlier pass that never got verified (the agent stopped meanwhile).
        for folder in sorted(self.layout.work.iterdir()) if self.layout.work.is_dir() else []:
            target = folder / "request.req"
            if (
                folder.is_dir()
                and target.exists()
                and folder.name not in self.ledger
                and (folder.name, target) not in claimed
            ):
                claimed.append((folder.name, target))
        return claimed

    def _take_inbox(self) -> bool:
        claimed = self._claim()
        if not claimed:
            return False
        try:
            keys = load_keys(self.layout.keys)
            self._keys_error = None
        except KeysError as exc:
            if self._keys_error != str(exc):
                self._keys_error = str(exc)
                log.error("requests cannot be verified: %s", exc)
            return False
        accepted: list[tuple[AdminRequest, Path]] = []
        for request_id, target in claimed:
            request = self._verify(request_id, target, keys)
            if request is not None:
                accepted.append((request, target.parent))
        for request, folder in sorted(accepted, key=lambda item: item[0].issued_at):
            self._busy = request.id
            self.heartbeat()
            # No ``finally``: when the service stops in the middle of a request, its work folder
            # stays and the next start reports the request interrupted.
            try:
                keep = self._process(request, folder)
            except Exception:
                log.exception("request %s failed", request.id)
                self._fail_unexpected(request)
                keep = False
            if not keep:
                remove_tree(folder)
            self._busy = None
        return True

    def _verify(self, request_id: str, target: Path, keys: KeysFile) -> AdminRequest | None:
        """Steps 2-4 and the upload copies (5): the request, or None when it was rejected."""
        repeat = target.name != "request.req"
        try:
            with target.open("rb") as handle:  # one byte more than allowed: the envelope refuses
                data = handle.read(MAX_REQUEST_BYTES + 1)
        except OSError:
            return None
        # A file named by an id taken before is rejected without touching that request's
        # status: whoever can write the inbox cannot rewrite the outcome of an earlier request.
        taken = (
            repeat or request_id in self.ledger or read_status(self.layout, request_id) is not None
        )
        now = self.now()
        try:
            request = open_admin_request(data, keys, now=now)
        except EnvelopeError as exc:
            known = self._after_signature(data, keys) if exc.code != "signature_invalid" else None
            self._reject(request_id, exc.code, known, reason=exc.reason, status=not taken)
            self._drop(target, repeat)
            return None
        if request.id != request_id:
            self._reject(
                request_id,
                "schema_invalid",
                None,
                reason="file name and id differ",
                status=not taken,
            )
            self._drop(target, repeat)
            return None
        if repeat or request_id in self.ledger:
            self._reject(request_id, "request_replayed", request, reason="id taken", status=False)
            self._drop(target, repeat)
            return None
        self.ledger.add(request_id, now)
        policy = read_policy(self.layout.policy)
        if not policy.enabled:
            self._reject(request_id, "admin_disabled", request, reason="policy.enabled is false")
            self._drop(target, repeat)
            return None
        if not self._copy_uploads(request, target.parent):
            self._reject(request_id, "payload_mismatch", request, reason="an upload differs")
            self._drop(target, repeat)
            return None
        return request

    @staticmethod
    def _drop(target: Path, repeat: bool) -> None:
        if repeat:
            target.unlink(missing_ok=True)
        else:
            remove_tree(target.parent)

    def _after_signature(self, data: bytes, keys: KeysFile) -> AdminRequest | None:
        """The payload of an envelope whose signature verified (a rejection after
        verification names its kind and requester)."""
        try:
            verified = verify(data, keys.public_keys("api"), ADMIN_REQUEST)
            return parse_document(AdminRequest, verified.payload)
        except (EnvelopeError, DocumentError):
            return None

    def _copy_uploads(self, request: AdminRequest, folder: Path) -> bool:
        """Each upload into the work folder, read no further than its signed size (the API's
        quota is not the agent's limit) and checked there against its SHA-256."""
        for op in request.ops:
            if not isinstance(op, RootAddOp | ZamaneStageOp | SmtpCaSetOp):
                continue
            if op.size > UPLOAD_LIMITS[_UPLOAD_KINDS[op.op]]:
                return False
            try:
                with self.layout.upload_data(op.upload).open("rb") as source:
                    if os.fstat(source.fileno()).st_size != op.size:
                        return False
                    data = source.read(op.size + 1)
                if len(data) != op.size or hashlib.sha256(data).hexdigest() != op.sha256:
                    return False
                (folder / f"{op.upload}.bin").write_bytes(data)
            except OSError:
                return False
        return True

    def _reject(
        self,
        request_id: str,
        code: str,
        request: AdminRequest | None,
        *,
        reason: str,
        status: bool = True,
        kind: RequestKind | None = None,
    ) -> None:
        """State ``rejected`` (a request before verification is known by its file name only).
        ``kind``: the kind the status shows, when it is not the request's (a revert)."""
        log.warning("request %s rejected (%s): %s", request_id, code, reason)
        kind = kind or (request.kind if request else None)
        section = request.section if request else None
        requested_by = (
            Requester(user_id=request.actor.user_id, username=request.actor.username)
            if request
            else None
        )
        if status:
            progress = _Progress(self, request_id, kind, section)
            progress.step("received", "done")
            progress.step("validate", "failed", code)
            progress.write("rejected", error=code)
        self._journal(
            request_id, "rejected", kind=kind, section=section, requested_by=requested_by, code=code
        )
        self._event(
            Event.REQUEST_REJECTED,
            "warning",
            f"İstek reddedildi [request rejected]: {code} ({request_id[:8]})",
        )
        if code in ("signature_invalid", "installation_mismatch"):
            self._alert(
                "R20-admin",
                "high",
                "Yönetim isteği reddedildi, olası kurcalama [administration request rejected, "
                f"possible tampering]: {code}",
                {"code": code, "request": request_id, "key": code},
            )

    def _fail_unexpected(self, request: AdminRequest) -> None:
        status = read_status(self.layout, request.id)
        if status is not None and status.state in TERMINAL_STATES | {"pending", "dispatched"}:
            return
        progress = _Progress(self, request.id, request.kind, request.section)
        progress.write("failed", error="write_failed")
        self._journal(
            request.id,
            "failed",
            kind=request.kind,
            section=request.section,
            requested_by=Requester(user_id=request.actor.user_id, username=request.actor.username),
            code="write_failed",
        )

    def _process(self, request: AdminRequest, folder: Path) -> bool:
        """One verified request; True when its work folder must stay (a held Zamane client)."""
        if request.kind == "config.apply":
            return self._config(request, work=folder)
        if request.kind == "config.revert":
            self._revert(request)
        elif request.kind == "config.cancel":
            self._cancel(request)
        else:
            self._dispatch(request)
        return False

    # -- configuration changes -------------------------------------------------------------------

    def _uploads(self, request: AdminRequest, work: Path | None) -> dict[str, rules.UploadInfo]:
        uploads: dict[str, rules.UploadInfo] = {}
        if work is None:
            return uploads
        for op in request.ops:
            kind = _UPLOAD_KINDS.get(op.op)
            upload = getattr(op, "upload", None)
            path = work / f"{upload}.bin"
            if kind is None or upload is None or not path.exists():
                continue
            uploads[upload] = rules.inspect_upload(upload, kind, path, now=self.now())
        return uploads

    def _config(
        self,
        request: AdminRequest,
        *,
        work: Path | None,
        pending: PendingFile | None = None,
        status_kind: RequestKind | None = None,
    ) -> bool:
        """Steps 1-12 of contract 2.7.1 for one ``config.apply`` (also a revert's inverse and a
        held change that is due). True: the work folder is kept (a held Zamane client)."""
        kind: RequestKind = status_kind or request.kind
        requester = Requester(user_id=request.actor.user_id, username=request.actor.username)
        progress = _Progress(self, request.id, kind, request.section)
        progress.step("received", "done")
        if pending is None:
            progress.write("received")

        def fail(state: JournalState, status_state: State, code: str, path: str | None) -> bool:
            progress.write(status_state, error=code, field_path=path)
            self._journal(
                request.id,
                state,
                kind=kind,
                section=request.section,
                requested_by=requester,
                code=code,
            )
            if status_state == "failed":
                self._event(
                    Event.CHANGE_FAILED, "error", f"Değişiklik uygulanmadı [not applied]: {code}"
                )
            return False

        files = self._files()
        if files.config is None:
            log.error("request %s: the configuration is not usable: %s", request.id, files.error)
            progress.step("validate", "failed", "validation_failed")
            return fail("failed", "failed", "validation_failed", None)
        clashes = rules.conflicts(request.ops, files.config, files.stored)
        if clashes:
            progress.step("validate", "failed", "conflict")
            return fail("failed", "conflict", "conflict", clashes[0])
        context = self.context(files, self._uploads(request, work))
        check = rules.check_request(request, files.config, context)
        if check.reauth and not _recent_reauth(request):
            self._reject(
                request.id,
                "reauth_missing",
                request,
                reason="no recent re-authentication",
                kind=kind,
            )
            return False
        if not check.ok:
            problem = check.errors[0]
            code = _known(problem.code, "validation_failed")
            progress.step("validate", "failed", code)
            return fail("failed", "failed", code, problem.field)
        try:
            new_toml = tomledit.edit(files.toml, check.edits, path=self.installation.toml)
        except tomledit.TomlEditError as exc:
            log.warning("request %s: %s", request.id, exc)
            progress.step("validate", "failed", exc.code)
            return fail("failed", "failed", exc.code, None)
        new_secrets: bytes | None = None
        if check.secret_changes:
            try:
                new_secrets = self.installation.secret_store().render_with(check.secret_changes)
            except (SecretValueError, SecretStoreError, OSError) as exc:
                log.warning("request %s: a secret is refused: %s", request.id, exc)
                progress.step("validate", "failed", "secret_invalid")
                return fail("failed", "failed", "secret_invalid", None)
        warnings = [problem.code for problem in check.warnings]
        progress.step("validate", "warning" if warnings else "done")
        if pending is None:
            if check.probes and request.probe:
                progress.step("probe", "running")
                progress.write("probing", warnings=warnings)
                self.heartbeat()
                failure = self._probe(request, check, files)
                if failure is not None:
                    progress.step("probe", "failed", failure.code)
                    log.warning("request %s: probe failed: %s", request.id, failure.message)
                    return fail("failed", "probe_failed", failure.code or "probe_failed", None)
                progress.step("probe", "done")
            elif check.probes:
                progress.step("probe", "skipped", "probe_skipped")
            staged = check.stage_jar
            if staged is not None and work is not None and not self._stage_jar(work, *staged):
                progress.step("jar.promotion", "failed", "write_failed")
                return fail("failed", "failed", "write_failed", "tsa.zamane_jar")
            if check.jar_hold is not None or check.effective_at(context.policy, self.now()):
                return self._hold(request, check, progress, kind, warnings)
        finish = _Finish(
            status_kind=kind,
            section=request.section,
            requested_by=requester,
            changes={path: dict(change) for path, change in check.changes.items()},
            secrets=[
                SecretRecord.model_validate(
                    {
                        "name": name,
                        "action": "set" if token is not None else "delete",
                        "result": "stored" if token is not None else "deleted",
                    }
                )
                for name, token in check.secret_changes.items()
            ],
            warnings=warnings,
            alert=check.alert,
            fields=sorted(check.changes),
            system_sync=check.system_sync,
            sources=any(isinstance(item, tomledit.SetSources) for item in check.edits),
            followups=list(check.followups),
            installs=len(check.installs),
        )
        if "hold" not in progress.steps:
            progress.step("hold", "skipped")
        progress.write("applying", warnings=warnings)
        plan = ChangePlan(
            id=request.id,
            request=request.id,
            actor=request.actor.username,
            paths=tuple(sorted(check.changes)),
            old_toml=files.toml,
            old_secrets=files.secrets,
            new_toml=new_toml,
            new_secrets=new_secrets,
            installs=check.installs,
            secret_names=tuple(n for n, token in check.secret_changes.items() if token),
            journal=finish.dump(),
        )
        outcome = self.applier.apply(plan)
        self._finish(request.id, outcome, finish, progress)
        return False

    def _hold(
        self,
        request: AdminRequest,
        check: rules.SectionCheck,
        progress: _Progress,
        kind: RequestKind,
        warnings: list[str],
    ) -> bool:
        policy = read_policy(self.layout.policy).policy
        now = self.now()
        jar = check.jar_hold
        if jar is not None:
            # A known release is promoted by ApplySystem at once; any other client is never
            # applied on time alone (D0-16): its time is when "approve-jar" is first allowed.
            known = find_release(jar) is not None
            effective = now if known else now + timedelta(hours=policy.jar_hold_hours)
        else:
            effective = check.effective_at(policy, now) or now
        write_pending(
            self.layout,
            PendingFile(
                id=request.id,
                kind="jar" if jar else "hold",
                effective_at=effective,
                jar_sha256=jar,
                held_at=now,
                request=request,
            ),
        )
        progress.step("hold", "running")
        if jar:
            progress.step("jar.promotion", "pending")
        held = [*warnings, "change_held"] if not jar else warnings
        progress.write("pending", effective_at=effective, warnings=held)
        self._journal(
            request.id,
            "pending",
            kind=kind,
            section=request.section,
            requested_by=Requester(user_id=request.actor.user_id, username=request.actor.username),
            changes={path: dict(change) for path, change in check.changes.items()},
        )
        until = isoformat_site(effective, "seconds")
        self._event(
            Event.CHANGE_HELD,
            "warning",
            f"Değişiklik bekletiliyor [change held]: {request.section}, {until} ({request.id[:8]})",
        )
        if jar:
            self._event(Event.SYSTEM_SYNC_REQUESTED, "info", "Zamane istemcisi [client] staged")
        self._config_alert(
            request.section, sorted(check.changes), check.alert or "high", request.id
        )
        return jar is not None

    def _config_alert(
        self, section: Section | None, fields: Sequence[str], severity: str, request_id: str
    ) -> None:
        """``R20-config-change``: the section, the field names and the time only (ADR-0010);
        the worker's texts read ``details.section`` and ``details.paths``."""
        level: Literal["medium", "high"] = "medium" if severity == "medium" else "high"
        names = ", ".join(fields) or "-"
        self._alert(
            "R20-config-change",
            level,
            f"Yapılandırma değişikliği [configuration change]: {section} ({names})",
            {"section": section, "paths": list(fields), "request": request_id},
        )

    def _stage_jar(self, work: Path, upload: str, sha256: str) -> bool:
        """``zamane\\web\\<sha256>.jar`` (never run; ApplySystem or ``approve-jar`` promote it)."""
        try:
            data = client_bytes(work / f"{upload}.bin")
        except (OSError, ValueError) as exc:
            log.error("Zamane client %s not staged: %s", sha256[:12], exc)
            return False
        if hashlib.sha256(data).hexdigest() != sha256:
            return False
        target = self.installation.zamane.staging / f"{sha256}.jar"
        try:
            if target.exists() and target.read_bytes() == data:
                return True
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.tmp")
            temp.write_bytes(data)
            temp.replace(target)
        except OSError as exc:
            log.error("Zamane client %s not staged: %s", sha256[:12], exc)
            return False
        return True

    def _probe(
        self, request: AdminRequest, check: rules.SectionCheck, files: _Files
    ) -> ProbeResult | None:
        """Run the probes of the change with its candidate values; the first failure."""
        candidate = check.candidate
        if candidate is None:
            return None
        store = self.installation.secret_store()

        def secret(name: str) -> str | None:
            if name in check.secret_changes:
                token = check.secret_changes[name]
                return store.decrypt_value(token) if token is not None else None
            return store.get(name) if name in files.stored else None

        for probe in check.probes:
            secrets: dict[str, str] = {}
            params: dict[str, JsonValue]
            if probe.kind == "sntp":
                params = {"server": probe.target, "port": candidate.clock.ntp_port}
            elif probe.kind == "credit":
                anchors = self.installation.anchors()
                try:
                    java = check_java(candidate.tsa.java, anchors)
                    jar = check_zamane_jar(candidate.tsa.zamane_jar or "", anchors).path
                except ExecutableRefusedError as exc:
                    return ProbeResult(False, "credit_query_failed", exc.reason)
                params = {
                    "java": list(java),
                    "jar": str(jar),
                    "url": candidate.tsa.url,
                    "port": candidate.tsa.port,
                    "timeout_seconds": min(candidate.tsa.timeout_seconds, 90),
                    "verify_flag": candidate.tsa.zamane_verify_flag,
                    "credit_regex": candidate.tsa.credit_regex,
                }
                for name in ("kamusm.customer", "kamusm.password"):
                    value = secret(name)
                    if value:
                        secrets[name] = value
            elif probe.kind == "smtp":
                params = {
                    "smtp": candidate.smtp.model_dump(mode="json"),
                    "to": request.options.test_to,
                    "web_url": web_url(candidate),
                }
                password = secret("smtp.password")
                if password:
                    secrets["smtp.password"] = password
            else:
                params = {
                    "target": str(candidate.replica.target),
                    "user": candidate.replica.user,
                }
                password = secret("replica.password")
                if password:
                    secrets["replica.password"] = password
            result = self.prober.run(probe.kind, params, secrets)
            if not result.ok:  # its message is logged: never with a value the probe was given
                return ProbeResult(False, result.code, redact(result.message, secrets))
        return None

    def _finish(
        self,
        request_id: str,
        outcome: ApplyOutcome,
        finish: _Finish,
        progress: _Progress,
        *,
        journaled: bool = False,
    ) -> None:
        """Steps 12 and the reports of an apply's outcome. ``journaled``: a recovery whose
        journal line was written before the crash (it is not written twice)."""
        version: VersionRef | None = None
        if outcome.version is not None:
            version = VersionRef.model_validate(
                {"number": outcome.version.n, "from": outcome.from_sha256, "to": outcome.to_sha256}
            )
        number = outcome.version.n if outcome.version else None
        transition = (outcome.from_sha256, outcome.to_sha256)
        written = outcome.state == "applied"
        # In force, but the agent can no longer write a file (e.g. after a console ``secret
        # set``): applied with a critical warning; the next change fails until it is repaired.
        drift = written and outcome.code == "acl_drift"
        conflict = outcome.state == "conflict"  # the files changed while it was probed
        applicable: dict[Step, bool] = {
            "backup": True,
            "write_secrets": bool(finish.secrets),
            "write_config": True,
            "install_files": finish.installs > 0,
            "verify": True,
        }
        if conflict:
            progress.step("validate", "failed", outcome.code)
        for step, used in applicable.items():
            if not used or conflict:
                progress.step(step, "skipped")
            elif written:
                progress.step(step, "done")
            else:
                progress.step(step, "failed", outcome.code)
        if outcome.rolled_back:
            progress.step("rollback", "done")
        journal_state: JournalState
        if outcome.state == "applied":
            journal_state = "recovered" if outcome.recovered else "applied"
        elif outcome.state == "conflict":
            journal_state = "failed"
        else:
            journal_state = outcome.state
        if not journaled:
            self._journal(
                request_id,
                journal_state,
                kind=finish.status_kind,
                section=finish.section,
                requested_by=finish.requested_by,
                version=number,
                transition=None if conflict else transition,
                changes=finish.changes if written else {},
                secrets=finish.secrets if written else (),
                code=outcome.code,
                rolled_back=outcome.rolled_back,
            )
        progress.step("journal", "done")
        state: State = outcome.state
        # The status before txn.json goes: a stop in between is recovered at the next start and
        # reported again (with the journal line written once), never left at "applying".
        progress.write(
            state,
            error=None if drift else outcome.code,
            version=version,
            warnings=[*finish.warnings, "write_access_lost"] if drift else finish.warnings,
            rolled_back=outcome.rolled_back,
        )
        if written or outcome.state == "needs_attention":
            self.applier.commit()
        if written:
            update_secret_meta(
                self.layout,
                [record.name for record in finish.secrets],
                changed_by=f"web:{finish.requested_by.username}",
                at=self.now(),
            )
        short = request_id[:8]
        if outcome.state == "applied":
            self._event(
                Event.CHANGE_APPLIED,
                "info",
                f"Değişiklik uygulandı [change applied]: {finish.section}, {short}, "
                f"sürüm [version] {number}",
            )
            for record in finish.secrets:
                self._event(
                    Event.SECRET_CHANGED,
                    "info",
                    f"Kayıtlı bilgi değişti [stored credential changed]: {record.name}",
                )
            if finish.alert:
                self._config_alert(finish.section, finish.fields, finish.alert, request_id)
            if drift:
                self._alert(
                    "R20-admin",
                    "critical",
                    "Yönetim hizmeti ayar dosyalarına yazamıyor [the administration service can "
                    f"no longer write the configuration]: acl_drift, sürüm [version] {number}",
                    {"code": "acl_drift", "request": request_id, "version": number},
                )
            if finish.system_sync:
                self._event(Event.SYSTEM_SYNC_REQUESTED, "info", "sistem eşitlemesi [system sync]")
                if finish.sources:
                    self._wait_sync(request_id, outcome.to_sha256)
            for kind in finish.followups:
                self._followup(request_id, kind)
            return
        if conflict:
            return
        self._event(
            Event.CHANGE_FAILED,
            "error",
            f"Değişiklik uygulanmadı [change not applied]: {finish.section}, {short}, "
            f"{outcome.code}",
        )
        if outcome.state == "needs_attention":
            self._alert(
                "R20-admin",
                "critical",
                f"Yapılandırma elle müdahale bekliyor [configuration needs manual action]: "
                f"{outcome.code}, sürüm [version] {number}",
                {"code": outcome.code, "request": request_id, "version": number},
            )
        elif outcome.rolled_back:
            self._alert(
                "R20-admin",
                "medium",
                f"Yapılandırma değişikliği geri alındı [configuration change rolled back]: "
                f"{outcome.code}",
                {"code": outcome.code, "request": request_id},
            )

    def _wait_sync(self, request_id: str, sha256: str) -> None:
        waits = read_waits(self.layout)
        sync = SyncWait(request=request_id, config_sha256=sha256, since=self.now())
        write_waits(self.layout, waits.model_copy(update={"syncs": (*waits.syncs, sync)}))

    def _followup(self, origin: str, kind: str) -> None:
        if kind != "replica_test" or self.data is None:
            return
        try:
            key = load_signing_key(self.layout.keys, "agent")
            task = new_task_request(
                "replica_test", origin=origin, params=TaskParams(), now=self.now()
            )
            write_task_request(self.data, task, key)
        except (KeysError, OSError, ValueError) as exc:
            log.error("follow-up replica test not queued: %s", exc)
            return
        self._event(
            task_event("replica_test"), "info", f"görev isteği [task request] {task.id[:8]}"
        )

    # -- recovery ----------------------------------------------------------------------------------

    def recover(self) -> ApplyOutcome | None:
        """Finish or undo an apply a crash interrupted, and report it like any apply (at start,
        before any request; also the console's ``recover``)."""
        try:
            outcome = self.applier.recover()
        except (OSError, DocumentError, ValueError):
            log.exception("the write-ahead record cannot be recovered")
            return None
        if outcome is None or outcome.txn is None:
            return outcome
        txn = outcome.txn
        log.warning("recovered an interrupted apply %s: %s %s", txn.id, outcome.state, outcome.code)
        if txn.request is None or txn.journal is None:  # a console restore: nothing to report
            if outcome.state == "applied":
                self.applier.commit()
            return outcome
        finish = _Finish.load(txn.journal)
        progress = _Progress(self, txn.request, finish.status_kind, finish.section)
        # A crash between the journal line and the removal of txn.json: the outcome was
        # journaled already (an audit row, secret records); the rest is reported once.
        earlier = (
            self._outcome_line(txn.request, txn.started_at) if outcome.state == "applied" else None
        )
        if earlier is not None and earlier.state == "needs_attention":
            outcome = replace(outcome, state="needs_attention", code=earlier.code, recovered=False)
        self._finish(txn.request, outcome, finish, progress, journaled=earlier is not None)
        return outcome

    def _outcome_line(
        self, request_id: str, since: datetime, *, final: bool = False
    ) -> JournalLine | None:
        """The journal line that reported the apply of ``request_id`` (``applied``,
        ``recovered`` or ``needs_attention``; ``final``: a ``failed`` line too), looked for
        from the day of ``since`` on."""
        states = _APPLY_OUTCOMES | {"failed"} if final else _APPLY_OUTCOMES
        today = site_day(self.now())
        day = max(min(site_day(since), today), today - timedelta(days=JOURNAL_DAYS))
        while day <= today:
            for line in read_journal(self.layout, day):
                if line.request == request_id and line.state in states:
                    return line
            day += timedelta(days=1)
        return None

    def _report_journaled(self, progress: _Progress, line: JournalLine) -> None:
        """The status of a request whose outcome the journal holds (written once only)."""
        version = (
            VersionRef.model_validate(
                {"number": line.version, "from": line.config_sha256.from_,
                 "to": line.config_sha256.to}
            )
            if line.version is not None and line.config_sha256 is not None
            else None
        )  # fmt: skip
        if line.state in ("applied", "recovered"):
            drift = line.code == "acl_drift"
            progress.write(
                "applied", version=version, warnings=["write_access_lost"] if drift else ()
            )
        elif line.state == "needs_attention":
            progress.write("needs_attention", error=line.code, version=version)
        else:
            progress.write(
                "conflict" if line.code == "conflict" else "failed",
                error=line.code or "interrupted",
                rolled_back=line.rolled_back,
            )

    def _abandoned_work(self) -> None:
        """Work folders of requests the agent took but never finished (it stopped): failed
        ``interrupted`` (the transaction itself was recovered first), unless the journal
        already reports the outcome (the stop came between its line and the status)."""
        if not self.layout.work.is_dir():
            return
        pending = {item.id for item in read_pending(self.layout)}
        for folder in sorted(self.layout.work.iterdir()):
            if not folder.is_dir() or folder.name in pending or folder.name not in self.ledger:
                continue
            status = read_status(self.layout, folder.name)
            # The states the agent passes through; an apply in progress was recovered above.
            if status is not None and status.state in ("received", "probing", "applying"):
                progress = _Progress(self, status.id, status.kind, status.section)
                line = self._outcome_line(status.id, status.updated_at, final=True)
                if line is not None:
                    self._report_journaled(progress, line)
                    remove_tree(folder)
                    continue
                progress.write("failed", error="interrupted")
                requester = self._requester(status.id)
                if requester is not None:
                    self._journal(
                        status.id,
                        "failed",
                        kind=status.kind,
                        section=status.section,
                        requested_by=requester,
                        code="interrupted",
                    )
            remove_tree(folder)

    def _requester(self, request_id: str) -> Requester | None:
        """Who made a request, from its claimed file (verified again: nothing unverified is
        trusted)."""
        try:
            data = (self.layout.work_dir(request_id) / "request.req").read_bytes()
            keys = load_keys(self.layout.keys)
        except (OSError, KeysError):
            return None
        request = self._after_signature(data, keys)
        if request is None:
            return None
        return Requester(user_id=request.actor.user_id, username=request.actor.username)

    # -- held changes ------------------------------------------------------------------------------

    def _due_pending(self) -> None:
        now = self.now()
        approved: frozenset[str] | None = None
        enabled: bool | None = None
        for item in read_pending(self.layout):
            if item.kind == "hold" and now < item.effective_at:
                continue
            if item.kind == "jar":
                approved = self.approved_jars() if approved is None else approved
                if item.jar_sha256 not in approved:
                    continue
            if enabled is None:
                enabled = read_policy(self.layout.policy).enabled
            self._busy = item.id
            status = read_status(self.layout, item.id)
            kind = status.kind if status is not None and status.kind else item.request.kind
            work = self.layout.work_dir(item.id)
            if not enabled:
                # Switched off (or a damaged policy.json, which fails closed) while it was held:
                # turning browser administration off stops what it holds too.
                self._fail_held(item, kind)
            else:
                try:
                    self._config(
                        item.request,
                        work=work if work.is_dir() else None,
                        pending=item,
                        status_kind=kind,
                    )
                except Exception:
                    log.exception("held request %s failed", item.id)
                    self._fail_unexpected(item.request)
            remove_pending(self.layout, item.id)  # not on a stop: the hold is taken up again
            remove_tree(work)
            if not enabled:
                self._unstage(item)
            self._busy = None

    def _fail_held(self, held: PendingFile, kind: RequestKind) -> None:
        """A held change that is due while browser administration is off: ``failed``
        (``admin_disabled``), so switching it on again leaves nothing to take effect."""
        log.warning("held request %s not applied: browser administration is off", held.id)
        progress = _Progress(self, held.id, kind, held.request.section)
        progress.step("hold", "failed", "admin_disabled")
        progress.write("failed", error="admin_disabled")
        self._journal(
            held.id,
            "failed",
            kind=kind,
            section=held.request.section,
            requested_by=Requester(
                user_id=held.request.actor.user_id, username=held.request.actor.username
            ),
            code="admin_disabled",
        )
        self._event(
            Event.CHANGE_FAILED,
            "error",
            f"Bekletilen değişiklik uygulanmadı [held change not applied]: admin_disabled "
            f"({held.id[:8]})",
        )

    def _unstage(self, held: PendingFile) -> None:
        """The staged Zamane client of a held change that ended, unless another one waits
        for it."""
        if held.jar_sha256 and not any(
            item.jar_sha256 == held.jar_sha256 for item in read_pending(self.layout)
        ):
            (self.installation.zamane.staging / f"{held.jar_sha256}.jar").unlink(missing_ok=True)

    def _cancel(self, request: AdminRequest) -> None:
        target = request.options.target
        progress = _Progress(self, request.id, request.kind, None)
        progress.step("received", "done")
        requester = Requester(user_id=request.actor.user_id, username=request.actor.username)
        held = next((item for item in read_pending(self.layout) if item.id == target), None)
        if target is None or held is None:
            progress.step("validate", "failed", "request_not_cancellable")
            progress.write("failed", error="request_not_cancellable")
            self._journal(
                request.id,
                "failed",
                kind=request.kind,
                section=None,
                requested_by=requester,
                code="request_not_cancellable",
            )
            return
        if not _recent_reauth(request):
            self._reject(
                request.id, "reauth_missing", request, reason="no recent re-authentication"
            )
            return
        remove_pending(self.layout, held.id)
        remove_tree(self.layout.work_dir(held.id))
        self._unstage(held)
        status = read_status(self.layout, held.id)
        kind = status.kind if status is not None and status.kind else held.request.kind
        cancelled = _Progress(self, held.id, kind, held.request.section)
        cancelled.step("hold", "skipped")
        cancelled.write("cancelled")
        self._journal(
            held.id, "cancelled", kind=kind, section=held.request.section, requested_by=requester
        )
        self._event(
            Event.HOLD_CANCELLED,
            "info",
            f"Bekletilen değişiklik iptal edildi [cancelled]: {held.id[:8]}",
        )
        progress.step("validate", "done")
        progress.write("succeeded")

    def _revert(self, request: AdminRequest) -> None:
        """``config.revert``: the inverse ops of the target's paths from its version, through
        the same rules but without a connection test (contract 2.7.2). The version is saved
        before the files are replaced, so a request that failed, was rolled back or needs
        attention has one as well: only a target its journal reports applied is reverted."""
        target = request.options.target
        requester = Requester(user_id=request.actor.user_id, username=request.actor.username)
        entry = self.applier.versions.for_request(target) if target else None
        if entry is not None and entry.request is not None:
            line = self._outcome_line(entry.request, entry.at)
            if line is None or line.state not in ("applied", "recovered"):
                entry = None
        inverse = self._inverse(entry) if entry is not None else None
        if inverse is None:
            progress = _Progress(self, request.id, request.kind, None)
            progress.step("received", "done")
            progress.step("validate", "failed", "request_not_revertible")
            progress.write("failed", error="request_not_revertible")
            self._journal(
                request.id,
                "failed",
                kind=request.kind,
                section=None,
                requested_by=requester,
                code="request_not_revertible",
            )
            return
        section, ops = inverse
        apply = AdminRequest.model_validate(
            {
                **request.model_dump(mode="json", exclude={"options"}),
                "kind": "config.apply",
                "section": section,
                "ops": [op.model_dump(mode="json") for op in ops],
                # The values were in force before: they are not tested again (an e-mail
                # server's revert would otherwise need a trial recipient nobody can give it).
                "probe": False,
            }
        )
        self._config(apply, work=None, status_kind="config.revert")

    def _inverse(self, entry: VersionEntry) -> tuple[Section, list[Any]] | None:
        """The ops that bring the paths of ``entry``'s request back to their values in it."""
        files = self._files()
        if files.config is None or not entry.paths:
            return None
        try:
            old_toml, old_secrets = self.applier.versions.read(entry.n)
            old = parse_config(old_toml, self.installation.toml)
            old_store: dict[str, str] = json.loads(old_secrets) if old_secrets else {}
        except (OSError, ValueError):
            return None
        current = files.config
        sections: set[Section] = set()
        ops: list[Any] = []
        for path in entry.paths:
            if path.startswith("sources."):
                sections.add("sources")
                name = path.split(".", 1)[1]
                before = next((s for s in old.sources if s.name == name), None)
                now = next((s for s in current.sources if s.name == name), None)
                if before is None and now is not None:
                    ops.append(SourceRemoveOp(op="source_remove", name=name, expect=now))
                elif before is not None and now is None:
                    ops.append(SourceAddOp(op="source_add", source=before))
                elif before is not None and now is not None and before != now:
                    ops.append(
                        SourceUpdateOp(op="source_update", name=name, expect=now, source=before)
                    )
                continue
            editable = rules.EDITABLE.get(path)
            if editable is None:
                return None  # roots, legal fingerprints, the jar: console matters
            sections.add(editable.section)
            if editable.via == "secret":
                if not entry.has_secrets:
                    return None
                state = rules.secret_state(path, files.stored)
                if path in old_store:
                    token = old_store[path]
                    ops.append(
                        SecretSetOp.model_validate(
                            {"op": "secret_set", "name": path, "expect": state, "token": token}
                        )
                    )
                elif state == "set":
                    ops.append(
                        SecretDeleteOp.model_validate(
                            {"op": "secret_delete", "name": path, "expect": "set"}
                        )
                    )
            elif editable.via in ("value", "server"):
                value = rules.current_value(old, path)
                expect = rules.current_value(current, path)
                if value != expect:
                    ops.append(SetOp(op="set", path=path, expect=expect, value=value))
            elif path == "smtp.ca_file" and old.smtp.ca_file is None:
                expect_ca = rules.current_value(current, path)
                if expect_ca is not None:
                    ops.append(SmtpCaClearOp(op="smtp_ca_clear", expect=str(expect_ca)))
            else:
                return None
        if len(sections) != 1 or not ops:
            return None
        return sections.pop(), ops

    # -- actions -----------------------------------------------------------------------------------

    def _dispatch(self, request: AdminRequest) -> None:
        """An evidence action: a signed task request and the wake-up event (contract 2.9)."""
        task_kind = ACTION_TASKS[request.kind]
        progress = _Progress(self, request.id, request.kind, None)
        progress.step("received", "done")
        requester = Requester(user_id=request.actor.user_id, username=request.actor.username)
        params = TaskParams(day=request.options.day) if task_kind == "seal" else TaskParams()
        task = new_task_request(task_kind, origin=request.id, params=params, now=self.now())

        def failed(exc: Exception) -> None:
            log.error("task request for %s not written: %s", request.id, exc)
            progress.step("dispatch", "failed", "write_failed")
            progress.write("failed", error="write_failed")
            self._journal(
                request.id,
                "action",
                kind=request.kind,
                section=None,
                requested_by=requester,
                code="write_failed",
            )

        try:
            if self.data is None:
                raise OSError("the data root is not known (the configuration did not load)")
            key = load_signing_key(self.layout.keys, "agent")
        except (KeysError, OSError) as exc:
            failed(exc)
            return
        # The wait and the status first: whatever the task does with the request is folded in
        # (a stop before the request file is written ends in task_timeout, and nothing ran).
        waits = read_waits(self.layout)
        wait = TaskWait(
            task=task.id,
            origin=request.id,
            kind=task_kind,
            dispatched_at=self.now(),
            requested_by=requester,
        )
        write_waits(self.layout, waits.model_copy(update={"tasks": (*waits.tasks, wait)}))
        progress.step("dispatch", "done")
        progress.step("task", "pending")
        progress.write("dispatched")
        try:
            write_task_request(self.data, task, key)
        except (OSError, ValueError) as exc:
            waits = read_waits(self.layout)
            others = tuple(item for item in waits.tasks if item.task != task.id)
            write_waits(self.layout, waits.model_copy(update={"tasks": others}))
            failed(exc)
            return
        self._event(task_event(task_kind), "info", f"görev isteği [task request] {task.id[:8]}")

    def _fold_waits(self) -> None:
        waits = read_waits(self.layout)
        if not waits.tasks and not waits.syncs:
            return
        now = self.now()
        tasks: list[TaskWait] = []
        for wait in waits.tasks:
            result = read_result(self.data, wait.task, wait.kind) if self.data else None
            if result is not None:
                self._fold(wait, result)
            elif self._no_result(wait, now):
                self._fold(wait, None)
            else:
                tasks.append(wait)
        syncs: list[SyncWait] = [wait for wait in waits.syncs if not self._acknowledged(wait, now)]
        if len(tasks) != len(waits.tasks) or len(syncs) != len(waits.syncs):
            write_waits(self.layout, WaitsFile(tasks=tuple(tasks), syncs=tuple(syncs)))

    def _no_result(self, wait: TaskWait, now: datetime) -> bool:
        """No result will come for ``wait``. A request no task took within ``TASK_TIMEOUT`` is
        withdrawn (the agent deletes its own file), so ``timed_out`` means that nothing ran; a
        request the task took (its ledger entry ``seen\\<id>``) is waited for while the task
        may still run it: a Seal run that takes long seals the day later, and its result is
        still folded in."""
        waited = now - wait.dispatched_at
        if waited <= TASK_TIMEOUT:
            return False
        if self.data is None:
            return True
        validity = MAX_SEAL_REQUEST_VALIDITY if wait.kind == "seal" else MAX_TASK_REQUEST_VALIDITY
        if waited > validity + TASK_RUN_LIMITS[wait.kind]:
            return True
        try:
            if (self.data.request_seen / wait.task).exists():
                return False  # taken: the task is running it
            (self.data.request_dir(task_queue(wait.kind)) / f"{wait.task}.json").unlink(
                missing_ok=True
            )
        except OSError as exc:  # a task may still take it: wait until it has expired
            log.warning("task request %s not withdrawn: %s", wait.task, exc)
            return False
        return waited > TASK_TIMEOUT + WITHDRAW_GRACE

    def _fold(self, wait: TaskWait, result: TaskResult | None) -> None:
        """A task's result (None: none came, see :meth:`_no_result`) into the origin request."""
        kind: RequestKind = next(k for k, v in ACTION_TASKS.items() if v == wait.kind)
        progress = _Progress(self, wait.origin, kind, None)
        code: str | None
        if result is None:
            code, payload = "task_timeout", None
            progress.step("task", "failed", code)
            progress.write("timed_out", error=code)
        else:
            code, payload = result.code, result.result
            progress.step("task", "done" if result.ok else "failed", code)
            progress.write("succeeded" if result.ok else "failed", error=code, result=payload)
            if wait.kind == "tsa_test" and result.ok:
                self._production_tested = None  # look again: it may have been the production one
        self._journal(
            wait.origin,
            "action",
            kind=kind,
            section=None,
            requested_by=wait.requested_by,
            code=code,
            result=payload,
        )

    def _acknowledged(self, wait: SyncWait, now: datetime) -> bool:
        """The collector reports the new configuration (or is stopped): wake ApplySystem again
        to widen the syslog rule (contract 2.8.3)."""
        if now - wait.since > SYNC_WAIT_MAX:
            return True
        if self.data is None:
            return False
        path = self.data.collector_status
        try:
            stale = now.timestamp() - path.stat().st_mtime > COLLECTOR_FRESH.total_seconds()
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        block = config_status_of(status) if isinstance(status, dict) else None
        if stale or (block is not None and block.sha256 == wait.config_sha256):
            self._event(
                Event.SYSTEM_SYNC_REQUESTED, "info", "toplayıcı yeni ayarı aldı [collector ack]"
            )
            return True
        return False

    # -- cleanup -----------------------------------------------------------------------------------

    def _cleanup(self, now: datetime) -> None:
        """Contract 2.5.3: old temporary inbox files, uploads, statuses, ledger and journal."""
        limit_tmp = (now - TMP_MAX_AGE).timestamp()
        limit_upload = (now - UPLOAD_LIFETIME).timestamp()
        for folder, limit in ((self.layout.inbox, limit_tmp), (self.layout.uploads, limit_upload)):
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if not path.is_file() or (folder == self.layout.inbox and path.suffix == ".req"):
                    continue
                try:
                    if path.stat().st_mtime < limit:
                        path.unlink()
                except OSError:
                    continue
        prune_statuses(self.layout, now)
        self.ledger.prune(now)
        prune_journal(self.layout, site_day(now))
