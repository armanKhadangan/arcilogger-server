"""Test helpers of the browser administration (ADR-0014; contract 2.1) — never used by a service.

* :func:`make_installation` builds an installation folder the way the installer would: a
  configuration file, ``secret.key`` and ``secrets.json``, the channel keys (``keys init``: real
  Ed25519 keys made for the test, never a fixed key), ``policy.json`` and a data root;
  :class:`Installed` signs and queues requests exactly as the API does (``.tmp``, then rename),
  stages uploads and reads statuses and journals;
* :class:`FakeAgent` runs the real :class:`~arcilogger_core.admin.agent.Agent` synchronously
  (:meth:`FakeAgent.process`), with a settable clock, :class:`FakeEventLog`,
  :class:`FakeProber` and, when given, :class:`FakeTasks` (the Seal and Requests tasks) and
  :class:`FakeSystem` (ApplySystem), so a request reaches its terminal state in one call;
* :class:`FakeSystem` applies the real ``system-plan`` (it records the syslog senders it would
  let in, promotes known Zamane releases and writes ``admin\\system\\``), and approves a client
  the way the console's ``approve-jar`` does;
* :class:`SmtpTestServer` is a plain SMTP server on 127.0.0.1 for the trial e-mail.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import socketserver
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from arcilogger_core.admin.agent import Agent
from arcilogger_core.admin.contract import (
    MAX_REQUEST_VALIDITY,
    POLICY_SCHEMA,
    AdminLayout,
    AdminRequest,
    ApprovedJar,
    CreditResult,
    DocumentError,
    Event,
    FirewallState,
    JarCounts,
    JarsFile,
    JournalLine,
    ReplicaTestResult,
    RequestKind,
    RequestStatus,
    SealResult,
    Section,
    StagedJar,
    SystemStatus,
    TaskKind,
    TaskRequest,
    TaskResult,
    TimeState,
    TsaTestResult,
    UploadKind,
    UploadMeta,
    dump_document,
    new_id,
    parse_document,
)
from arcilogger_core.admin.envelope import EnvelopeError, open_task_request, sign_admin_request
from arcilogger_core.admin.keys import SigningKey, init_keys, load_keys, load_signing_key
from arcilogger_core.admin.probe import ProbeKind, ProbeResult
from arcilogger_core.admin.store import read_journal, read_status
from arcilogger_core.admin.systemplan import peer_list, read_inputs
from arcilogger_core.admin.systemplan import plan as system_plan
from arcilogger_core.admin.winapi import EventLevel
from arcilogger_core.config import Config, load_config
from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.layout import Layout, RequestQueue
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import site_day, utc_now

#: The configuration of :func:`make_installation` (the fictional example world).
DEFAULT_TOML = """\
# ARCILOGGER test configuration (arcilogger_core.admin.testing)
site_name = "Örnek A.Ş."           # the organisation's name
data_dir = "{data_dir}"

[collector]
bind = "0.0.0.0"

[tsa]
provider = "mock"
credit_alert_threshold = 500       # credits

[retention]
enabled = false
days = 760

[replica]
enabled = false

[clock]
ntp_server = "172.20.10.1"
fallback_server = "time.ume.tubitak.gov.tr"
alert_offset_ms = 1000

[smtp]
enabled = false
port = 587
security = "starttls"
timeout_seconds = 30
"""


class FakeEventLog:
    """Records the events the agent writes."""

    def __init__(self) -> None:
        self.events: list[tuple[Event, EventLevel, str]] = []

    def write(self, event: Event, level: EventLevel, message: str) -> None:
        self.events.append((event, level, message))

    def ids(self) -> list[int]:
        return [int(event) for event, _, _ in self.events]


@dataclass
class ProbeCall:
    kind: ProbeKind
    params: Mapping[str, JsonValue]
    secrets: Mapping[str, str] = field(repr=False)


class FakeProber:
    """Answers every probe with ``results[kind]`` (default: success) and records the calls."""

    def __init__(self, results: Mapping[str, ProbeResult] | None = None) -> None:
        self.results: dict[str, ProbeResult] = dict(results or {})
        self.calls: list[ProbeCall] = []

    def run(
        self, kind: ProbeKind, params: Mapping[str, JsonValue], secrets: Mapping[str, str]
    ) -> ProbeResult:
        self.calls.append(ProbeCall(kind, dict(params), dict(secrets)))
        return self.results.get(kind, ProbeResult(True, None, "ok"))


@dataclass
class Installed:
    """A test installation (the folder of the configuration file is ``root``)."""

    root: Path
    config_path: Path
    data_dir: Path

    @property
    def layout(self) -> AdminLayout:
        return AdminLayout(self.root / "admin")

    @property
    def data(self) -> Layout:
        return Layout(self.data_dir)

    @property
    def store(self) -> SecretStore:
        return SecretStore(self.root / "secret.key", self.root / "secrets.json")

    @property
    def api_key(self) -> SigningKey:
        return load_signing_key(self.layout.keys, "api")

    @property
    def agent_key(self) -> SigningKey:
        return load_signing_key(self.layout.keys, "agent")

    @property
    def instance(self) -> str:
        return load_keys(self.layout.keys).instance

    def config(self) -> Config:
        return load_config(self.config_path)

    def write_policy(self, **values: Any) -> None:
        """``policy.json`` (enabled by default) with ``values`` changed."""
        document: dict[str, Any] = {
            "schema": POLICY_SCHEMA,
            "enabled": True,
            "written_at": "2026-10-25T10:00:00+03:00",
            "written_by": "test",
            **values,
        }
        atomic_write_bytes(self.layout.policy, json.dumps(document).encode("utf-8"))

    def request(
        self,
        kind: RequestKind = "config.apply",
        *,
        section: Section | None = None,
        ops: Sequence[Mapping[str, Any]] = (),
        options: Mapping[str, Any] | None = None,
        now: datetime | None = None,
        reauth: bool = True,
        probe: bool = True,
        username: str = "ayse",
        request_id: str | None = None,
    ) -> AdminRequest:
        """A request as the API would make it (user 3, ``ayse``, from 172.20.10.37)."""
        now = now or utc_now()
        return AdminRequest.model_validate(
            {
                "id": request_id or new_id(),
                "instance": self.instance,
                "issued_at": now,
                "expires_at": now + MAX_REQUEST_VALIDITY,
                "kind": kind,
                "section": section,
                "actor": {
                    "user_id": 3,
                    "username": username,
                    "session": "3f9c0a1b2c4d",
                    "ip": "172.20.10.37",
                    "reauth_at": now - timedelta(seconds=30) if reauth else None,
                },
                "audit_id": 4812,
                "ops": [dict(op) for op in ops],
                "probe": probe,
                "options": dict(options or {}),
            }
        )

    def submit(self, request: AdminRequest, *, data: bytes | None = None) -> Path:
        """Sign ``request`` with the API key and queue it (or queue ``data`` as the file)."""
        payload = data if data is not None else sign_admin_request(request, self.api_key)
        target = self.layout.request_file(request.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_bytes(payload)
        temp.replace(target)
        return target

    def upload(
        self, kind: UploadKind, data: bytes, *, now: datetime | None = None, user_id: int = 3
    ) -> dict[str, Any]:
        """Stage an upload as ``POST /admin/uploads`` does; returns ``{upload, sha256, size}``
        for the op."""
        upload_id = new_id()
        meta = UploadMeta(
            id=upload_id,
            kind=kind,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            created_at=now or utc_now(),
            user_id=user_id,
        )
        self.layout.uploads.mkdir(parents=True, exist_ok=True)
        self.layout.upload_data(upload_id).write_bytes(data)
        self.layout.upload_meta(upload_id).write_bytes(dump_document(meta))
        return {"upload": upload_id, "sha256": meta.sha256, "size": meta.size}

    def status(self, request_id: str) -> RequestStatus | None:
        return read_status(self.layout, request_id)

    def journal(self, day: date | None = None) -> list[JournalLine]:
        return read_journal(self.layout, day or site_day(utc_now()))


def make_installation(
    root: Path,
    *,
    toml: str | None = None,
    policy: Mapping[str, Any] | None = None,
    secrets: Mapping[str, str] | None = None,
) -> Installed:
    """An installation in ``root`` (configuration folder) with its data root in ``root/data``.
    ``toml`` may use ``{data_dir}``; ``secrets`` are stored (name -> value)."""
    root.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    config_path = root / "arcilogger.toml"
    text = (toml or DEFAULT_TOML).replace("{data_dir}", data_dir.as_posix())
    config_path.write_bytes(text.encode("utf-8"))
    SecretStore.create_key(root / "secret.key")
    (root / "secrets.json").write_text("{}\n", encoding="utf-8")
    installed = Installed(root=root, config_path=config_path, data_dir=data_dir)
    for name, value in (secrets or {}).items():
        installed.store.set(name, value)
    init_keys(installed.layout.keys)
    installed.write_policy(**dict(policy or {}))
    return installed


# -- the task side ---------------------------------------------------------------------------------


def default_result(request: TaskRequest, now: datetime) -> TaskResult:
    """A successful result of each kind (a sealed day, 1000 credits, a mock stamp, a share)."""
    result: Any
    if request.kind == "seal":
        day = request.params.day if request.params.day is not None else site_day(now)
        result = SealResult(day=day, status="sealed", message="sealed")
    elif request.kind == "credit":
        result = CreditResult(credits=1000, threshold=500, below_threshold=False)
    elif request.kind == "tsa_test":
        result = TsaTestResult(
            provider="mock",
            legal_grade_claimed=False,
            legal_grade=False,
            root_sha256=None,
            root=None,
            verified=True,
            errors=(),
            warnings=(),
            gen_time=now,
            serial="1",
            tsa="ARCILOGGER Mock TSA (TEST ONLY)",
            tsa_cert_not_after=None,
            container="TimeStampResp",
        )
    else:
        result = ReplicaTestResult(ok=True, message="ok")
    return TaskResult(
        id=request.id, kind=request.kind, ok=True, result=result, started_at=now, finished_at=now
    )


class FakeTasks:
    """The Seal and Requests tasks: verify each task request, answer it, delete it."""

    def __init__(
        self,
        installed: Installed,
        *,
        now: Callable[[], datetime] = utc_now,
        outcomes: Mapping[TaskKind, Callable[[TaskRequest, datetime], TaskResult | None]]
        | None = None,
    ) -> None:
        self.installed = installed
        self.now = now
        #: kind -> result maker (None: the task leaves the request alone, as a stopped task).
        self.outcomes: dict[TaskKind, Callable[[TaskRequest, datetime], TaskResult | None]] = dict(
            outcomes or {}
        )
        self.taken: list[TaskRequest] = []
        self.refused: list[str] = []

    def run(self) -> int:
        data = self.installed.data
        keys = load_keys(self.installed.layout.keys)
        count = 0
        queues: tuple[RequestQueue, ...] = ("seal", "tasks")
        for queue in queues:
            folder = data.request_dir(queue)
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                now = self.now()
                try:
                    request = open_task_request(path.read_bytes(), keys, now=now)
                except EnvelopeError as exc:
                    self.refused.append(exc.code)
                    path.unlink()
                    continue
                if (data.request_seen / request.id).exists():
                    path.unlink()
                    continue
                make = self.outcomes.get(request.kind, default_result)
                result = make(request, now)
                if result is None:
                    continue
                data.request_seen.mkdir(parents=True, exist_ok=True)
                (data.request_seen / request.id).touch()
                atomic_write_bytes(
                    data.request_results / f"{request.id}.json", dump_document(result)
                )
                path.unlink()
                self.taken.append(request)
                count += 1
        return count


# -- ApplySystem -----------------------------------------------------------------------------------


class FakeSystem:
    """ApplySystem without Windows: the real plan, applied to memory and ``admin\\system\\``."""

    def __init__(
        self,
        installed: Installed,
        *,
        now: Callable[[], datetime] = utc_now,
        managed: bool = True,
        strict_outbound: bool = False,
    ) -> None:
        self.installed = installed
        self.now = now
        self.syslog_addresses: tuple[str, ...] = ()
        self.peers: str | None = None
        self.plans: list[Any] = []
        if managed:
            state = installed.root / "install" / "firewall-state.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(
                f'{{"schema": 1, "strict_outbound": {str(strict_outbound).lower()}}}\n',
                encoding="utf-8",
            )

    def _jars(self) -> JarsFile:
        try:
            return parse_document(JarsFile, self.installed.layout.jars.read_bytes())
        except (OSError, DocumentError):
            return JarsFile()

    def _write_jars(self, jars: JarsFile) -> None:
        atomic_write_bytes(self.installed.layout.jars, dump_document(jars))

    def promote(self, sha256: str, approved_by: str) -> Path:
        """Copy a staged client into ``zamane\\approved\\`` and record it (as ApplySystem for
        a known release, or ``approve-jar`` on the console)."""
        root = self.installed.root / "zamane"
        source = root / "web" / f"{sha256}.jar"
        target = root / "approved" / f"{sha256}.jar"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
            target.unlink()
            raise ValueError("the copy does not have the staged SHA-256")
        jars = self._jars()
        approved = (
            *jars.approved,
            ApprovedJar(
                sha256=sha256,
                path=str(target),
                approved_at=self.now(),
                approved_by=approved_by,
            ),
        )
        self._write_jars(jars.model_copy(update={"approved": approved}))
        return target

    def run(self) -> Any:
        """One ApplySystem run; returns the plan it applied."""
        now = self.now()
        planned = system_plan(read_inputs(self.installed.config_path, now=now))
        self.plans.append(planned)
        self.syslog_addresses = planned.firewall.syslog_addresses
        if planned.time.changed:
            self.peers = peer_list(planned.time.primary, planned.time.fallback)
        jars = self._jars()
        staged_known = {entry.sha256 for entry in jars.staged}
        staging = self.installed.root / "zamane" / "web"
        names = sorted(path.stem for path in staging.glob("*.jar")) if staging.is_dir() else []
        new_staged = [
            StagedJar(sha256=name, first_seen=now) for name in names if name not in staged_known
        ]
        if new_staged:
            self._write_jars(jars.model_copy(update={"staged": (*jars.staged, *new_staged)}))
        for promotion in planned.jars.promote:
            self.promote(promotion.sha256, "known")
        jars = self._jars()
        status = SystemStatus(
            at=now,
            ok=True,
            applied_config_sha256=planned.config_sha256,
            collector_ack_sha256=planned.collector_ack_sha256,
            firewall=FirewallState(
                managed=planned.firewall.managed,
                strict_outbound=planned.firewall.strict_outbound,
                syslog_addresses=planned.firewall.syslog_addresses,
                outbound_hosts=tuple(sorted({rule.host for rule in planned.firewall.outbound})),
            ),
            time=TimeState(
                peers=self.peers or "", changed_at=now if planned.time.changed else None
            ),
            jars=JarCounts(staged=len(jars.staged), promoted=len(jars.approved)),
        )
        atomic_write_bytes(self.installed.layout.system_status, dump_document(status))
        return planned


# -- an SMTP server for the trial e-mail -----------------------------------------------------------


@dataclass
class ReceivedMail:
    sender: str
    recipients: list[str]
    data: bytes
    #: ``(user, password)`` of ``AUTH PLAIN`` on that connection, if any.
    login: tuple[str, str] | None = field(default=None, repr=False)


@dataclass
class SmtpState:
    host: str = "127.0.0.1"
    port: int = 0
    received: list[ReceivedMail] = field(default_factory=list)
    #: ``RCPT TO`` of these addresses: ``550`` (permanent) / ``451`` (temporary).
    refuse: set[str] = field(default_factory=set)
    defer: set[str] = field(default_factory=set)
    #: Required ``AUTH PLAIN`` credentials (None: no authentication needed).
    credentials: tuple[str, str] | None = field(default=None, repr=False)
    #: Seconds before the greeting (a timeout test).
    delay: float = 0.0
    #: Appended to a failed ``AUTH`` answer (a server that echoes what it was sent).
    echo: str = ""


class _SmtpHandler(socketserver.StreamRequestHandler):
    server: _SmtpServer

    def reply(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8") + b"\r\n")

    def handle(self) -> None:
        state = self.server.state
        if state.delay:
            time.sleep(state.delay)
        self.reply("220 smtp.ornek.example ESMTP")
        sender, login = "", None
        recipients: list[str] = []
        while raw := self.rfile.readline():
            verb, _, rest = raw.decode("utf-8", "replace").rstrip("\r\n").partition(" ")
            verb = verb.upper()
            if verb == "EHLO":
                self.wfile.write(b"250-smtp.ornek.example\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
            elif verb == "AUTH":
                login = self._auth(rest)
                if login is None:
                    self.reply(f"535 5.7.8 authentication failed {state.echo}".rstrip())
                else:
                    self.reply("235 2.7.0 accepted")
            elif verb == "MAIL":
                if state.credentials is not None and login is None:
                    self.reply("530 5.7.0 authentication required")
                    continue
                sender, recipients = rest.partition(":")[2].split()[0].strip("<>"), []
                self.reply("250 2.1.0 ok")
            elif verb == "RCPT":
                address = rest.partition(":")[2].strip().strip("<>")
                if address in state.refuse:
                    self.reply("550 5.1.1 mailbox unavailable")
                elif address in state.defer:
                    self.reply("451 4.3.0 try again later")
                else:
                    recipients.append(address)
                    self.reply("250 2.1.5 ok")
            elif verb == "DATA":
                self.reply("354 end with <CRLF>.<CRLF>")
                lines = []
                while (line := self.rfile.readline()) not in (b".\r\n", b".\n", b""):
                    lines.append(line[1:] if line.startswith(b"..") else line)
                mail = ReceivedMail(sender, list(recipients), b"".join(lines), login)
                state.received.append(mail)
                self.reply("250 2.0.0 queued")
            elif verb in ("RSET", "NOOP"):
                self.reply("250 2.0.0 ok")
            elif verb == "QUIT":
                self.reply("221 2.0.0 bye")
                return
            else:
                self.reply("502 5.5.2 not implemented")

    def _auth(self, rest: str) -> tuple[str, str] | None:
        mechanism, _, initial = rest.partition(" ")
        if mechanism.upper() != "PLAIN":
            return None
        if not initial:
            self.reply("334 ")
            initial = self.rfile.readline().decode("ascii").strip()
        try:
            _, user, password = base64.b64decode(initial).decode("utf-8").split("\0")
        except ValueError:
            return None
        expected = self.server.state.credentials
        return (user, password) if expected is None or expected == (user, password) else None


class _SmtpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, state: SmtpState) -> None:
        super().__init__((state.host, 0), _SmtpHandler)
        self.state = state


class SmtpTestServer:
    """``with SmtpTestServer() as smtp:`` — a plain SMTP server on ``smtp.host``:``smtp.port``
    for ``[smtp] security = "none"`` (EHLO, AUTH PLAIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT)."""

    def __init__(self) -> None:
        self.state = SmtpState()
        self._server = _SmtpServer(self.state)
        self.state.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> SmtpState:
        self._thread.start()
        return self.state

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


# -- the agent -------------------------------------------------------------------------------------


class FakeAgent:
    """The real agent of ``installed``, run synchronously with fakes (see the module text)."""

    def __init__(
        self,
        installed: Installed,
        *,
        now: datetime | None = None,
        prober: FakeProber | None = None,
        tasks: FakeTasks | bool = True,
        system: FakeSystem | bool = False,
        java_candidates: Sequence[str] = (),
        local_addresses: Sequence[str] = ("172.20.10.5",),
        **agent_options: Any,
    ) -> None:
        self.installed = installed
        self.time = now
        self.events = FakeEventLog()
        self.prober = prober or FakeProber()
        self.agent = Agent(
            installed.config_path,
            now=self.now,
            event_log=self.events,
            prober=self.prober,
            java_candidates=lambda: tuple(java_candidates),
            local_addresses=lambda: tuple(local_addresses),
            **agent_options,
        )
        self.tasks = FakeTasks(installed, now=self.now) if tasks is True else tasks or None
        self.system = FakeSystem(installed, now=self.now) if system is True else system or None
        self._started = False

    def now(self) -> datetime:
        return self.time or utc_now()

    def advance(self, delta: timedelta) -> None:
        self.time = self.now() + delta

    def start(self) -> None:
        if not self._started:
            self.agent.start()
            self._started = True

    def process(self, rounds: int = 3) -> None:
        """Take the inbox and everything it leads to (tasks, ApplySystem, folding)."""
        self.start()
        for _ in range(rounds):
            self.agent.run_once()
            if self.tasks is not None:
                self.tasks.run()
            if self.system is not None:
                self.system.run()
        self.agent.run_once()
        self.agent.heartbeat()
