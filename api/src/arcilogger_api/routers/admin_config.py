"""``/api/v1/admin`` — Ayarlar: sunucu yapılandırması (ADR-0014; contract 2.11.1).

The machine configuration (``arcilogger.toml``, the stored credentials, the Kamu SM roots and the
Zamane client) as the browser may see and change it. Permission ``settings.manage``
(``sources.manage`` for the log sources); nothing here is licence-gated.

* ``GET /admin/config`` — every section the browser edits (secrets only as "set" with who changed
  them when, never a value), the console-only paths with their reason, the administration
  policy, held changes, the setup problems and a per-section ``etag``.
* ``GET /admin/status`` — the administration service's heartbeat, ApplySystem's last run and the
  configuration each service reports it loaded (the header chip, the history page).
* ``POST /admin/config/<section>/check`` — a dry run of a change: what it would change, its
  effects, warnings, whether it is held and needs a re-authentication; nothing is queued.
* ``PUT /admin/config/<section>`` (and the sources, uploads and roots endpoints) — the change as
  a signed request of the agent (:mod:`arcilogger_api.admin_channel`), answered 202.

Every change is checked with the agent's own rules (:mod:`arcilogger_core.admin.rules`) and the
``etag`` of its section (409 ``config_conflict`` with ``details.current_etag`` when the section
changed since it was read). A refused change is audited ``admin.refused`` (its codes, never a
value) in a transaction of its own.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.admin_channel import (
    AdminAccepted,
    Channel,
    ChannelDep,
    CodeMessage,
    CurrentFiles,
    admin_error,
    check_error,
    code_message,
    require_reauth,
    texts,
)
from arcilogger_api.audit import record_audit
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_api.uploads import MESSAGES as UPLOAD_TEXTS
from arcilogger_api.uploads import OVERHEAD, multipart_file, read_body
from arcilogger_core.admin import rules
from arcilogger_core.admin.contract import (
    UPLOAD_LIFETIME,
    UPLOAD_LIMITS,
    DocumentError,
    Section,
    UploadKind,
    parse_document,
)
from arcilogger_core.admin.contract import SecretMeta as SecretMetaFile
from arcilogger_core.config import MIN_RETENTION_DAYS, Config, setup_problems
from arcilogger_core.execpolicy import ExecutableRefusedError, check_zamane_jar, file_sha256
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.tsa.preflight import production_missing, seal_preflight
from arcilogger_core.tsa.zamane_releases import find_release
from arcilogger_db.models import AdminRequest as AdminRequestRow
from arcilogger_db.models import Source

router = APIRouter(prefix="/admin", tags=["admin"])

SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
SourcesDep = Annotated[CurrentUser, Depends(require("sources.manage"))]
#: ``Türkçe [English]`` of ``config.setup_problems``.
_BILINGUAL = re.compile(r"^(?P<tr>.*) \[(?P<en>[^\[\]]*)\]$", re.DOTALL)


# -- GET /admin/config ----------------------------------------------------------------------------


class SecretMeta(BaseModel):
    """A stored credential: whether it is set, and who changed it when; never its value."""

    set: bool
    changed_at: datetime | None
    #: ``web:<username>``, ``cli`` or ``installer``.
    changed_by: str | None


class ClockSection(BaseModel):
    ntp_server: str
    fallback_server: str
    alert_offset_ms: int


class GeneralSection(BaseModel):
    site_name: str
    clock: ClockSection


class StagedJar(BaseModel):
    """A Zamane client uploaded through the browser that waits for its approval."""

    sha256: str
    effective_at: datetime | None


class ZamaneClient(BaseModel):
    present: bool
    sha256: str | None
    #: A known Kamu SM release (``tsa.zamane_releases``), else None.
    release: str | None
    #: Why the client may run (``execpolicy``), ``false`` when it may not.
    trusted: Literal["installer", "known", "approved", False]
    staged: list[StagedJar]


class RootCertificate(BaseModel):
    sha256: str
    subject: str
    not_after: datetime
    #: Listed in ``tsa.legal_root_fingerprints``.
    legal: bool
    source: Literal["installer", "web"]


class Readiness(BaseModel):
    """What the switch to the Kamu SM production server still needs (``tsa_not_ready``)."""

    production: bool
    missing: list[str]


class SealReady(BaseModel):
    """``tsa.preflight.seal_preflight``: the sealing causes that would stop the Seal task."""

    ok: bool
    causes: list[str]


class TsaSection(BaseModel):
    #: ``mock``, ``rfc3161`` or ``zamane`` (only Zamane can be chosen in the browser).
    provider: str
    #: ``test`` / ``production`` (a Zamane server of Kamu SM), else None.
    server: Literal["test", "production"] | None
    credit_alert_threshold: int
    timeout_seconds: int
    java: str | None
    #: The Java 8 installations the administration service found (its heartbeat).
    java_choices: list[str]
    zamane: ZamaneClient
    roots: list[RootCertificate]
    secrets: dict[str, SecretMeta]
    readiness: Readiness
    seal_ready: SealReady


class SmtpCa(BaseModel):
    sha256: str | None


class SmtpSection(BaseModel):
    enabled: bool
    host: str | None
    port: int
    security: Literal["starttls", "ssl", "none"]
    username: str | None
    from_address: str | None
    timeout_seconds: int
    ca: SmtpCa
    secrets: dict[str, SecretMeta]


class ReplicaSection(BaseModel):
    enabled: bool
    #: ``\\\\host\\share[\\path]``.
    target: str | None
    user: str | None
    secrets: dict[str, SecretMeta]


class RetentionPreview(BaseModel):
    """``retention.plan``: the sealed days before the cutoff that retention would delete."""

    cutoff: date
    days_to_delete: int
    oldest: date | None
    newest: date | None
    #: The next 02:00 site time (the Retention task's start) after the change takes effect.
    first_run_at: datetime | None = None


class RetentionSection(BaseModel):
    enabled: bool
    days: int
    minimum_days: int
    preview: RetentionPreview


class BackupSection(BaseModel):
    enabled: bool
    keep_daily: int
    keep_weekly: int
    keep_monthly: int
    min_free_gb: int


class SourceOut(BaseModel):
    name: str
    type: Literal["fortigate", "mikrotik", "generic"]
    #: An IPv4 address or network.
    address: str
    heartbeat_minutes: int
    expected: bool
    transport: Literal["any", "udp", "tcp"]
    enabled: bool
    #: The syslog rule: ``open`` (lets it in), ``closed``, ``pending`` (ApplySystem has not
    #: caught up yet), ``not_managed`` (the installation does not manage the firewall).
    firewall: Literal["open", "closed", "pending", "not_managed"]
    last_seen: datetime | None


class AdminSections(BaseModel):
    general: GeneralSection
    tsa: TsaSection
    smtp: SmtpSection
    replica: ReplicaSection
    retention: RetentionSection
    backup: BackupSection
    sources: list[SourceOut]


class LockedPath(BaseModel):
    path: str
    #: Why only the server console changes it.
    reason: Literal["security", "service", "evidence", "expert", "collector"]


class AdminPolicyOut(BaseModel):
    """The installer's guard rails (``admin\\policy.json``), read only."""

    enabled: bool
    source_networks: list[str]
    min_prefix_v4: int
    max_sources: int
    hold_hours: int
    jar_hold_hours: int
    tsa_test_per_day: int


class PendingChange(BaseModel):
    request_id: str
    section: str | None
    effective_at: datetime | None


class SetupProblem(BaseModel):
    level: Literal["warning", "error"]
    key: str
    message_tr: str
    message_en: str


class AdminConfig(BaseModel):
    """The machine configuration as the browser shows it (contract 2.11.1)."""

    config_sha256: str
    #: ``<section>:<sha256>`` of each section: sent back with a change.
    etags: dict[str, str]
    sections: AdminSections
    locked: list[LockedPath]
    policy: AdminPolicyOut
    pending: list[PendingChange]
    problems: list[SetupProblem]
    #: A hand edit of a key the services take only at a restart is not in use yet.
    restart_required: bool


def _secret_meta(channel: Channel) -> dict[str, Any]:
    layout = channel.layout
    if layout is None:
        return {}
    try:
        return dict(parse_document(SecretMetaFile, layout.secret_meta.read_bytes()).secrets)
    except (OSError, DocumentError):
        return {}


def _secrets(
    channel: Channel, section: Section, files: CurrentFiles, meta: dict[str, Any]
) -> dict[str, SecretMeta]:
    found: dict[str, SecretMeta] = {}
    for name in rules.SECTION_SECRETS.get(section, ()):
        entry = meta.get(name)
        found[name] = SecretMeta(
            set=name in files.stored,
            changed_at=entry.changed_at if entry else None,
            changed_by=entry.changed_by if entry else None,
        )
    return found


def _zamane(channel: Channel, config: Config, staged: list[StagedJar]) -> ZamaneClient:
    jar = config.tsa.zamane_jar
    if jar is None or not jar.is_file():
        return ZamaneClient(present=False, sha256=None, release=None, trusted=False, staged=staged)
    try:
        digest = file_sha256(jar)
    except OSError:
        return ZamaneClient(present=False, sha256=None, release=None, trusted=False, staged=staged)
    release = find_release(digest)
    trusted: Literal["installer", "known", "approved", False]
    try:
        check_zamane_jar(jar, channel.anchors())
    except ExecutableRefusedError:
        trusted = False
    else:
        if release is not None:
            trusted = "known"
        elif digest in channel.approved_jars():
            trusted = "approved"
        else:
            trusted = "installer"
    return ZamaneClient(
        present=True,
        sha256=digest,
        release=release.version if release is not None else None,
        trusted=trusted,
        staged=staged,
    )


def _staged(db: Session) -> list[StagedJar]:
    """Zamane clients of held requests (the agent keeps them until their approval)."""
    rows = db.scalars(
        select(AdminRequestRow).where(
            AdminRequestRow.state == "pending", AdminRequestRow.section == "tsa"
        )
    )
    found = []
    for row in rows:
        for item in (row.params or {}).get("files") or []:
            if isinstance(item, dict) and item.get("kind") == "zamane_client":
                found.append(
                    StagedJar(sha256=str(item.get("jar_sha256")), effective_at=row.effective_at)
                )
    return found


def _tsa(
    channel: Channel, files: CurrentFiles, meta: dict[str, Any], staged: list[StagedJar]
) -> TsaSection:
    config = files.config
    tsa = config.tsa
    server = rules.tsa_server(tsa.provider, tsa.url, tsa.port)
    chosen: Literal["test", "production"] | None = (
        "test" if server == "test" else "production" if server == "production" else None
    )
    roots = channel.roots(config)
    legal = set(tsa.legal_root_fingerprints)
    anchors = channel.anchors()
    missing = production_missing(config, anchors=anchors, stored=files.stored, roots=len(roots))
    try:
        causes = seal_preflight(config, anchors=anchors, secrets=channel.store())
    except (OSError, ValueError, SecretStoreError):
        causes = ["secret-store"]
    agent = channel.agent
    return TsaSection(
        provider=tsa.provider,
        server=chosen,
        credit_alert_threshold=tsa.credit_alert_threshold,
        timeout_seconds=tsa.timeout_seconds,
        java=tsa.java[0] if tsa.java else None,
        java_choices=list(agent.java_candidates) if agent else [],
        zamane=_zamane(channel, config, staged),
        roots=[
            RootCertificate(
                sha256=root.sha256,
                subject=root.subject,
                not_after=root.not_after,
                legal=root.sha256 in legal,
                source=root.source,
            )
            for root in roots
        ],
        secrets=_secrets(channel, "tsa", files, meta),
        readiness=Readiness(production=not missing, missing=missing),
        seal_ready=SealReady(ok=not causes, causes=causes),
    )


def _ca_sha256(config: Config) -> str | None:
    if config.smtp.ca_file is None:
        return None
    try:
        return file_sha256(config.smtp.ca_file)
    except OSError:
        return None


def _canonical(address: str) -> str:
    if "/" in address:
        return str(ipaddress.ip_network(address, strict=False))
    return str(ipaddress.ip_address(address))


def _sources(channel: Channel, db: Session, config: Config) -> list[SourceOut]:
    seen = dict(db.execute(select(Source.name, Source.last_seen)).all())
    system = channel.system
    managed = channel.firewall_managed and (system is None or system.firewall.managed)
    allowed = set(system.firewall.syslog_addresses) if system is not None else set()
    found = []
    for source in config.sources:
        firewall: Literal["open", "closed", "pending", "not_managed"]
        present = _canonical(source.address) in allowed
        if not managed:
            firewall = "not_managed"
        elif source.enabled:
            firewall = "open" if present else "pending"
        else:
            firewall = "pending" if present else "closed"
        found.append(
            SourceOut(
                **source.model_dump(mode="json"), firewall=firewall, last_seen=seen.get(source.name)
            )
        )
    return found


def _problems(files: CurrentFiles) -> list[SetupProblem]:
    problems = []
    if files.error is not None:
        message_tr, message_en = texts("config_unreadable")
        problems.append(
            SetupProblem(
                level="error", key="arcilogger.toml", message_tr=message_tr, message_en=message_en
            )
        )
    for problem in setup_problems(files.config):
        match = _BILINGUAL.match(problem.message)
        message_tr, message_en = (
            (match["tr"], match["en"]) if match else (problem.message, problem.message)
        )
        problems.append(
            SetupProblem(
                level=problem.level, key=problem.key, message_tr=message_tr, message_en=message_en
            )
        )
    return problems


def restart_required(channel: Channel) -> bool:
    """A service reports keys it takes only at a restart (a hand edit)."""
    for name in ("ArciCollector", "ArciIngest", "ArciWorker", "ArciApi"):
        block = channel.service(name).config
        if block is not None and block.restart_keys:
            return True
    return False


@router.get("/config")
def get_config(db: DbSession, user: SettingsDep, channel: ChannelDep) -> AdminConfig:
    """The machine configuration (contract 2.11.1, ``AdminConfig``)."""
    files = channel.files
    config = files.config
    meta = _secret_meta(channel)
    pending = [row for row in channel.open_rows(db) if row.state == "pending"]
    preview = channel.retention_preview(config)
    policy = channel.policy.policy
    return AdminConfig(
        config_sha256=files.sha256 or "",
        etags={section: rules.etag(section, config, files.stored) for section in rules.SECTIONS},
        sections=AdminSections(
            general=GeneralSection(
                site_name=config.site_name,
                clock=ClockSection(
                    ntp_server=config.clock.ntp_server,
                    fallback_server=config.clock.fallback_server,
                    alert_offset_ms=config.clock.alert_offset_ms,
                ),
            ),
            tsa=_tsa(channel, files, meta, _staged(db)),
            smtp=SmtpSection(
                enabled=config.smtp.enabled,
                host=config.smtp.host,
                port=config.smtp.port,
                security=config.smtp.security,
                username=config.smtp.username,
                from_address=config.smtp.from_address,
                timeout_seconds=config.smtp.timeout_seconds,
                ca=SmtpCa(sha256=_ca_sha256(config)),
                secrets=_secrets(channel, "smtp", files, meta),
            ),
            replica=ReplicaSection(
                enabled=config.replica.enabled,
                target=str(config.replica.target) if config.replica.target else None,
                user=config.replica.user,
                secrets=_secrets(channel, "replica", files, meta),
            ),
            retention=RetentionSection(
                enabled=config.retention.enabled,
                days=config.retention.days,
                minimum_days=MIN_RETENTION_DAYS,
                preview=RetentionPreview(
                    cutoff=preview.cutoff,
                    days_to_delete=preview.days_to_delete,
                    oldest=preview.oldest,
                    newest=preview.newest,
                ),
            ),
            backup=BackupSection(
                enabled=config.backup.enabled,
                keep_daily=config.backup.keep_daily,
                keep_weekly=config.backup.keep_weekly,
                keep_monthly=config.backup.keep_monthly,
                min_free_gb=config.backup.min_free_gb,
            ),
            sources=_sources(channel, db, config),
        ),
        locked=[LockedPath(path=item.path, reason=item.reason) for item in rules.locked_paths()],
        policy=AdminPolicyOut(
            enabled=policy.enabled,
            source_networks=list(policy.source_networks),
            min_prefix_v4=policy.min_prefix_v4,
            max_sources=policy.max_sources,
            hold_hours=policy.hold_hours,
            jar_hold_hours=policy.jar_hold_hours,
            tsa_test_per_day=policy.tsa_test_per_day,
        ),
        pending=[
            PendingChange(request_id=row.id, section=row.section, effective_at=row.effective_at)
            for row in pending
        ],
        problems=_problems(files),
        restart_required=restart_required(channel),
    )


# -- GET /admin/status ----------------------------------------------------------------------------


class PendingRequestOut(BaseModel):
    id: str
    effective_at: datetime


class SystemTaskCheck(BaseModel):
    """The start-up ping of ApplySystem (its status must change within 5 minutes)."""

    ok: bool
    checked_at: datetime | None
    error: str | None


class AgentOut(BaseModel):
    #: ``ok`` under 30 s, ``stale`` 30-120 s, ``missing`` older or none, ``disabled`` by the
    #: administration policy.
    state: Literal["ok", "stale", "missing", "disabled"]
    version: str | None
    heartbeat_at: datetime | None
    queue: int
    pending: list[PendingRequestOut]
    java_candidates: list[str]
    seal_ready: SealReady | None
    system_task: SystemTaskCheck | None


class FirewallOut(BaseModel):
    managed: bool
    strict_outbound: bool
    syslog_addresses: list[str]


class TimeOut(BaseModel):
    peers: str


class SystemOut(BaseModel):
    """ApplySystem's last run (``admin\\system\\status.json``)."""

    state: Literal["ok", "error", "never", "stale"]
    at: datetime | None
    applied_config_sha256: str | None
    firewall: FirewallOut | None
    time: TimeOut | None
    code: str | None


class ServiceConfigOut(BaseModel):
    """The ``config`` block of a service (contract 2.4.12); all None for an older service."""

    sha256: str | None
    loaded_at: datetime | None
    error: str | None
    restart_keys: list[str]
    #: Its status file is younger than 2 minutes (the API itself always).
    running: bool


class AdminStatus(BaseModel):
    agent: AgentOut
    system: SystemOut
    services: dict[str, ServiceConfigOut]
    config_sha256: str
    #: ``outside``: the file was last written by someone other than the agent (a hand edit).
    config_origin: Literal["browser", "outside"]
    restart_required: bool


def _system(channel: Channel) -> SystemOut:
    system = channel.system
    if system is None:
        return SystemOut(
            state="never", at=None, applied_config_sha256=None, firewall=None, time=None, code=None
        )
    state: Literal["ok", "error", "never", "stale"] = "ok"
    if not system.ok:
        state = "error"
    elif system.applied_config_sha256 != channel.files.sha256 and channel.installation is not None:
        try:
            changed = channel.installation.toml.stat().st_mtime
        except OSError:
            changed = None
        if changed is not None and channel.now.timestamp() - changed > 300:
            state = "stale"
    return SystemOut(
        state=state,
        at=system.at,
        applied_config_sha256=system.applied_config_sha256,
        firewall=FirewallOut(
            managed=system.firewall.managed,
            strict_outbound=system.firewall.strict_outbound,
            syslog_addresses=list(system.firewall.syslog_addresses),
        ),
        time=TimeOut(peers=system.time.peers),
        code=system.code,
    )


def status_of(channel: Channel) -> AdminStatus:
    agent = channel.agent
    services = {}
    for name in ("ArciCollector", "ArciIngest", "ArciWorker", "ArciApi"):
        view = channel.service(name)
        block = view.config
        services[name] = ServiceConfigOut(
            sha256=block.sha256 if block else None,
            loaded_at=block.loaded_at if block else None,
            error=block.error if block else None,
            restart_keys=list(block.restart_keys) if block else [],
            running=view.running,
        )
    return AdminStatus(
        agent=AgentOut(
            state=channel.agent_state,
            version=agent.version if agent else None,
            heartbeat_at=agent.heartbeat_at if agent else None,
            queue=agent.queue if agent else 0,
            pending=[
                PendingRequestOut(id=item.id, effective_at=item.effective_at)
                for item in (agent.pending if agent else ())
            ],
            java_candidates=list(agent.java_candidates) if agent else [],
            seal_ready=(
                SealReady(ok=agent.seal_ready.ok, causes=list(agent.seal_ready.causes))
                if agent
                else None
            ),
            system_task=(
                SystemTaskCheck(
                    ok=agent.system_task.ok,
                    checked_at=agent.system_task.checked_at,
                    error=agent.system_task.error,
                )
                if agent
                else None
            ),
        ),
        system=_system(channel),
        services=services,
        config_sha256=channel.files.sha256 or "",
        config_origin=channel.config_origin(),
        restart_required=any(service.restart_keys for service in services.values()),
    )


@router.get("/status")
def get_status(user: SettingsDep, channel: ChannelDep) -> AdminStatus:
    """The administration service, ApplySystem and what each service loaded (``AdminStatus``)."""
    return status_of(channel)


# -- dry runs and changes -------------------------------------------------------------------------


class AdminCheck(BaseModel):
    """A dry run: what a change would do; nothing is queued."""

    changes: dict[str, dict[str, JsonValue]]
    #: ``reload.collector``, ``system.firewall``, ``task.next_run:Seal``, …
    effects: list[str]
    warnings: list[CodeMessage]
    guarded: bool
    effective_at: datetime | None
    reauth_required: bool
    retention_preview: RetentionPreview | None = None
    readiness: Readiness | None = None


class Body(BaseModel):
    """A change of one section: the ``etag`` of ``GET /admin/config`` and the form's fields
    (a field left out stays as it is)."""

    model_config = ConfigDict(extra="forbid")

    etag: str

    def form(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_unset=True, exclude={"etag"})


class ClockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ntp_server: str | None = None
    fallback_server: str | None = None
    alert_offset_ms: int | None = None


class GeneralBody(Body):
    site_name: str | None = None
    clock: ClockBody | None = None
    #: Test a changed time server first (SNTP).
    probe: bool = True

    def form(self) -> dict[str, Any]:
        data = super().form()
        clock = data.get("clock")
        if isinstance(clock, dict):
            data["clock"] = {key: value for key, value in clock.items() if value is not None}
        return data


def checked(
    channel: Channel,
    db: Session,
    user: CurrentUser,
    section: Section,
    body: Body,
    *,
    encrypt: bool,
    form: dict[str, Any] | None = None,
    uploads: dict[str, rules.UploadInfo] | None = None,
) -> rules.SectionCheck:
    """The rules of ``section`` on the form (409 ``config_conflict`` for a stale ``etag``)."""
    files = channel.usable_files()
    current = rules.etag(section, files.config, files.stored)
    if body.etag != current:
        raise admin_error("config_conflict", details={"current_etag": current})
    context = channel.context(db, user, uploads=uploads, encrypt=encrypt)
    return rules.validate_section(
        section, files.config, body.form() if form is None else form, context
    )


def held(channel: Channel, check: rules.SectionCheck) -> tuple[bool, datetime | None]:
    """Whether the change is held, and until when (contract 2.3)."""
    policy = channel.policy.policy
    if check.jar_hold is not None:
        if find_release(check.jar_hold) is not None:
            return False, None
        return True, channel.now + timedelta(hours=policy.jar_hold_hours)
    effective = check.effective_at(policy, channel.now)
    return effective is not None, effective


def dry_run(channel: Channel, section: Section, check: rules.SectionCheck) -> AdminCheck:
    if not check.ok:
        raise check_error(check)
    guarded, effective_at = held(channel, check)
    preview = None
    if section == "retention" and check.candidate is not None:
        found = channel.retention_preview(check.candidate)
        preview = RetentionPreview(
            cutoff=found.cutoff,
            days_to_delete=found.days_to_delete,
            oldest=found.oldest,
            newest=found.newest,
            first_run_at=found.first_run_at,
        )
    readiness = None
    if section == "tsa" and check.candidate is not None:
        files = channel.files
        stored = (
            files.stored | {name for name, token in check.secret_changes.items() if token}
        ) - {name for name, token in check.secret_changes.items() if token is None}
        missing = production_missing(
            check.candidate,
            anchors=channel.anchors(),
            stored=stored,
            roots=len(channel.roots(check.candidate)) + len(check.installs),
        )
        readiness = Readiness(production=not missing, missing=missing)
    return AdminCheck(
        changes={path: dict(change) for path, change in check.changes.items()},
        effects=list(check.effects),
        warnings=[code_message(problem.code) for problem in check.warnings],
        guarded=guarded,
        effective_at=effective_at,
        reauth_required=check.reauth,
        retention_preview=preview,
        readiness=readiness,
    )


def refused(
    db: Session, user: CurrentUser, request: Request, section: Section, check: rules.SectionCheck
) -> None:
    """``admin.refused`` in a transaction of its own (the codes, never a value)."""
    db.rollback()
    record_audit(
        db,
        actor=user,
        action="admin.refused",
        target=f"admin-config:{section}",
        details={
            "kind": "config.apply",
            "section": section,
            "codes": list(dict.fromkeys(problem.code for problem in check.errors)),
        },
        ip=client_ip(request),
    )
    db.commit()


def queue(
    channel: Channel,
    db: Session,
    user: CurrentUser,
    request: Request,
    section: Section,
    check: rules.SectionCheck,
    *,
    probe: bool = True,
    files: list[dict[str, JsonValue]] | None = None,
) -> AdminAccepted:
    """Queue a checked change (refusals audited, re-authentication when the rules ask)."""
    if not check.ok:
        refused(db, user, request, section, check)
        raise check_error(check)
    if not check.ops:
        raise admin_error("config_unchanged")
    require_reauth(user, check.reauth)
    guarded, effective_at = held(channel, check)
    return channel.submit(
        db,
        user,
        request,
        kind="config.apply",
        section=section,
        ops=check.ops,
        options=check.options,
        probe=probe,
        changes=check.changes,
        effects=check.effects,
        warnings=[problem.code for problem in check.warnings],
        guarded=guarded,
        effective_at=effective_at,
        files=files or [],
    )


@router.post("/config/general/check")
def check_general(
    body: GeneralBody, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminCheck:
    """Dry run of a change of the site name or the time servers."""
    return dry_run(channel, "general", checked(channel, db, user, "general", body, encrypt=False))


@router.put("/config/general", status_code=status.HTTP_202_ACCEPTED)
def put_general(
    body: GeneralBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """The site name (``site_name``) and the time servers (``[clock]``, re-authentication)."""
    channel.require_enabled()
    check = checked(channel, db, user, "general", body, encrypt=True)
    return queue(channel, db, user, request, "general", check, probe=body.probe)


# -- Kamu SM (the tsa section) --------------------------------------------------------------------


def files_of(ops: Sequence[Mapping[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """The uploaded files a request installs (``admin.request`` ``files``)."""
    kinds = {"root_add": "kamusm_root", "zamane_stage": "zamane_client", "smtp_ca_set": "smtp_ca"}
    found: list[dict[str, JsonValue]] = []
    for op in ops:
        kind = kinds.get(str(op.get("op")))
        if kind is None:
            continue
        item: dict[str, JsonValue] = {"kind": kind, "sha256": op["sha256"], "size": op["size"]}
        if "jar_sha256" in op:
            item["jar_sha256"] = op["jar_sha256"]
        found.append(item)
    return found


class TsaBody(Body):
    #: ``test`` or ``production`` (a Zamane server of Kamu SM; ``mock``: 422 ``tsa_mock_refused``).
    server: str | None = None
    credit_alert_threshold: int | None = None
    timeout_seconds: int | None = None
    #: One of the Java installations of ``java_choices``.
    java: str | None = None
    #: Write-only: the Kamu SM customer number and password (Fernet tokens to the agent).
    customer: str | None = None
    password: str | None = None
    clear_credentials: bool = False
    #: Production to test stops production stamps: say so.
    confirm: bool = False
    #: Test new credentials or a new server first (a credit query with the candidate values).
    probe: bool = True

    def form(self) -> dict[str, Any]:
        return {key: value for key, value in super().form().items() if value is not None}


class RootBody(Body):
    upload_id: str
    #: Also add its fingerprint to ``tsa.legal_root_fingerprints`` (needs ``confirmed``).
    legal: bool = False
    #: "I compared the fingerprint with the value Kamu SM publishes".
    confirmed: bool = False

    def form(self) -> dict[str, Any]:
        return {
            "root": {"upload_id": self.upload_id, "legal": self.legal, "confirmed": self.confirmed}
        }


class LegalRootBody(Body):
    #: The SHA-256 of an installed root (``roots[].sha256``).
    sha256: str
    confirmed: bool = False

    def form(self) -> dict[str, Any]:
        return {"legal_root": {"sha256": self.sha256, "confirmed": self.confirmed}}


class ZamaneBody(Body):
    upload_id: str
    #: The SHA-256 on the Kamu SM download page (needed for a release that is not known).
    expected_sha256: str | None = None

    def form(self) -> dict[str, Any]:
        client: dict[str, Any] = {"upload_id": self.upload_id}
        if self.expected_sha256 is not None:
            client["expected_sha256"] = self.expected_sha256
        return {"zamane": client}


def _uploaded(channel: Channel, user: CurrentUser, body: Body) -> dict[str, rules.UploadInfo]:
    upload = getattr(body, "upload_id", None)
    return channel.uploads(user, [upload] if isinstance(upload, str) else [])


@router.post("/config/tsa/check")
def check_tsa(body: TsaBody, db: DbSession, user: SettingsDep, channel: ChannelDep) -> AdminCheck:
    """Dry run of a Kamu SM change (with ``readiness`` of the result)."""
    return dry_run(channel, "tsa", checked(channel, db, user, "tsa", body, encrypt=False))


@router.put("/config/tsa", status_code=status.HTTP_202_ACCEPTED)
def put_tsa(
    body: TsaBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """The time-stamp server (Kamu SM test or production), credentials, Java, threshold and
    time limit (re-authentication for all but the threshold and the time limit)."""
    channel.require_enabled()
    check = checked(channel, db, user, "tsa", body, encrypt=True)
    return queue(channel, db, user, request, "tsa", check, probe=body.probe)


def _tsa_file_change(
    body: Body, request: Request, db: Session, user: CurrentUser, channel: Channel
) -> AdminAccepted:
    channel.require_enabled()
    uploads = _uploaded(channel, user, body)
    check = checked(channel, db, user, "tsa", body, encrypt=True, uploads=uploads)
    return queue(channel, db, user, request, "tsa", check, files=files_of(check.ops))


@router.post("/config/tsa/roots", status_code=status.HTTP_202_ACCEPTED)
def add_root(
    body: RootBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Install an uploaded Kamu SM root certificate (``kamusm-roots\\<sha256>.pem``), and with
    ``legal`` mark it a legal root."""
    return _tsa_file_change(body, request, db, user, channel)


@router.post("/config/tsa/legal-roots", status_code=status.HTTP_202_ACCEPTED)
def add_legal_root(
    body: LegalRootBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Mark an installed root a legal root (``tsa.legal_root_fingerprints``)."""
    return _tsa_file_change(body, request, db, user, channel)


@router.post("/config/tsa/zamane", status_code=status.HTTP_202_ACCEPTED)
def stage_zamane(
    body: ZamaneBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """An uploaded Zamane client: staged by the agent and held (``pending``) until ApplySystem
    promotes a known release, or an administrator approves another one on the console."""
    return _tsa_file_change(body, request, db, user, channel)


# -- uploads (contract 2.4.4) ---------------------------------------------------------------------


class CertificatePreview(BaseModel):
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    is_ca: bool
    self_signed: bool
    #: ``RSA 4096``, ``EC P-256`` …
    key: str
    #: The same root is installed already.
    installed: bool
    warnings: list[CodeMessage]


class ZamanePreview(BaseModel):
    kind: Literal["jar", "zip"]
    #: The client's file name inside a .zip (null for a .jar).
    jar_name: str | None
    jar_sha256: str
    #: The known Kamu SM release, else null (then ``expected_sha256`` is needed).
    release: str | None
    known: bool
    accepted: bool
    reason: str | None


class UploadPreview(BaseModel):
    upload_id: str
    kind: Literal["kamusm_root", "zamane_client", "smtp_ca"]
    size: int
    sha256: str
    #: An upload is usable for 30 minutes.
    expires_at: datetime
    certificate: CertificatePreview | None = None
    zamane: ZamanePreview | None = None


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    db: DbSession,
    user: SettingsDep,
    channel: ChannelDep,
    kind: Annotated[str, Query(max_length=40)],
) -> UploadPreview:
    """A root certificate, the SMTP CA file or a Zamane client (``multipart/form-data``,
    field ``file``), staged for a request and shown before it is made."""
    if kind not in UPLOAD_LIMITS:
        raise admin_error("upload_kind_invalid")
    upload_kind: UploadKind = kind  # type: ignore[assignment]
    limit = UPLOAD_LIMITS[upload_kind]
    body = await read_body(request, limit + OVERHEAD, too_large_code="upload_too_large")
    data = multipart_file(request.headers.get("content-type", ""), body)
    if len(data) > limit:
        raise admin_error("upload_too_large")
    if not data:
        raise ApiError(422, "upload_invalid", *UPLOAD_TEXTS["upload_invalid"])
    meta, info = channel.stage_upload(db, user, request, upload_kind, data)
    preview = UploadPreview(
        upload_id=meta.id,
        kind=meta.kind,
        size=meta.size,
        sha256=meta.sha256,
        expires_at=meta.created_at + UPLOAD_LIFETIME,
    )
    certificate = None
    warnings: tuple[str, ...] = ()
    if info.root is not None and info.root.certificate is not None:
        certificate, warnings = info.root.certificate, info.root.warnings
    elif info.bundle is not None and info.bundle.certificates:
        certificate = info.bundle.certificates[0]
    if certificate is not None:
        installed = {root.sha256 for root in channel.roots()}
        preview.certificate = CertificatePreview(
            subject=certificate.subject,
            issuer=certificate.issuer,
            not_before=certificate.not_before,
            not_after=certificate.not_after,
            is_ca=certificate.is_ca,
            self_signed=certificate.self_signed,
            key=certificate.key,
            installed=upload_kind == "kamusm_root" and certificate.sha256 in installed,
            warnings=[code_message(code) for code in warnings],
        )
    client = info.zamane
    if client is not None and client.kind is not None and client.jar_sha256 is not None:
        preview.zamane = ZamanePreview(
            kind=client.kind,
            jar_name=client.jar_name,
            jar_sha256=client.jar_sha256,
            release=client.release,
            known=client.known,
            accepted=True,
            reason=None if client.known else "unknown_jar_held",
        )
    return preview


# -- log sources ----------------------------------------------------------------------------------


class SourceIn(BaseModel):
    """A ``[[sources]]`` table as the browser edits it (the rules check the values)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["fortigate", "mikrotik", "generic"]
    #: An IPv4 address or network (``172.20.20.0/24``).
    address: str
    heartbeat_minutes: int = 15
    expected: bool = True
    transport: Literal["any", "udp", "tcp"] = "any"
    enabled: bool = True


class SourceBody(Body):
    source: SourceIn

    def form(self) -> dict[str, Any]:
        return {"source": self.source.model_dump(mode="json")}


class SourceCheckBody(SourceBody):
    #: Which change it is: the ``POST`` of a new source, or the ``PUT`` of ``source.name``.
    operation: Literal["add", "update"]

    def form(self) -> dict[str, Any]:
        data = super().form()
        if self.operation == "update":
            data["name"] = self.source.name
        return data


@router.post("/config/sources/check")
def check_sources(
    body: SourceCheckBody, db: DbSession, user: SourcesDep, channel: ChannelDep
) -> AdminCheck:
    """Dry run of adding (``operation: add``) or changing (``update``) one log source."""
    return dry_run(channel, "sources", checked(channel, db, user, "sources", body, encrypt=False))


@router.post("/config/sources", status_code=status.HTTP_202_ACCEPTED)
def add_source(
    body: SourceBody, request: Request, db: DbSession, user: SourcesDep, channel: ChannelDep
) -> AdminAccepted:
    """A new log source: the collector and ingest take it within seconds; the firewall opens
    it once the collector knows it."""
    channel.require_enabled()
    check = checked(channel, db, user, "sources", body, encrypt=True)
    return queue(channel, db, user, request, "sources", check)


SourceName = Annotated[str, PathParam(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


@router.put("/config/sources/{name}", status_code=status.HTTP_202_ACCEPTED)
def update_source(
    name: SourceName,
    body: SourceBody,
    request: Request,
    db: DbSession,
    user: SourcesDep,
    channel: ChannelDep,
) -> AdminAccepted:
    """Change a log source (its name cannot change; disabling needs a re-authentication)."""
    channel.require_enabled()
    form = {"name": name, **body.form()}
    check = checked(channel, db, user, "sources", body, encrypt=True, form=form)
    return queue(channel, db, user, request, "sources", check)


@router.delete("/config/sources/{name}", status_code=status.HTTP_202_ACCEPTED)
def remove_source(
    name: SourceName,
    request: Request,
    db: DbSession,
    user: SourcesDep,
    channel: ChannelDep,
    etag: Annotated[str, Query(max_length=100)],
    confirm: Annotated[str, Query(max_length=100)] = "",
) -> AdminAccepted:
    """Remove a log source (``confirm`` = its name; a re-authentication). Its raw logs and
    sealed days stay."""
    channel.require_enabled()
    form = {"name": name, "remove": True, "confirm": confirm}
    check = checked(channel, db, user, "sources", Body(etag=etag), encrypt=True, form=form)
    return queue(channel, db, user, request, "sources", check)


# -- e-mail, replica, retention and backup --------------------------------------------------------


class SmtpBody(Body):
    """``[smtp]``: a text field sent as null or empty is removed (no server, no user)."""

    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    security: Literal["starttls", "ssl", "none"] | None = None
    username: str | None = None
    from_address: str | None = None
    timeout_seconds: int | None = None
    #: Write-only (a Fernet token to the agent); a changed server or user needs it again.
    password: str | None = None
    clear_password: bool = False
    #: The probe sends its test e-mail here (``alerts.email_recipients`` or one's own address).
    test_to: str | None = None
    #: Test the server first (connect, log in, send to ``test_to``).
    probe: bool = True


class ReplicaBody(Body):
    enabled: bool | None = None
    #: ``\\\\host\\share[\\path]``.
    target: str | None = None
    #: ``HOST\\user`` or ``user@domain``.
    user: str | None = None
    password: str | None = None
    clear_password: bool = False
    #: Connect to the share first with the candidate values.
    probe: bool = True


class RetentionBody(Body):
    enabled: bool | None = None
    days: int | None = None
    #: The number of sealed days the change deletes (``retention_preview.days_to_delete``).
    confirm_delete_days: int | None = None


class BackupBody(Body):
    enabled: bool | None = None
    keep_daily: int | None = None
    keep_weekly: int | None = None
    keep_monthly: int | None = None
    min_free_gb: int | None = None


class SmtpCaBody(Body):
    upload_id: str
    #: The server is tried with the new trust first: its test e-mail goes here.
    test_to: str | None = None
    probe: bool = True

    def form(self) -> dict[str, Any]:
        return {"ca": {"upload_id": self.upload_id}, "test_to": self.test_to, "probe": self.probe}


class SmtpCaClear(BaseModel):
    """The optional body of ``DELETE /admin/config/smtp/ca``: the probe's recipient (an address
    never goes into a URL, which the service log records)."""

    model_config = ConfigDict(extra="forbid")

    test_to: str | None = None
    probe: bool = True


@router.post("/config/smtp/check")
def check_smtp(body: SmtpBody, db: DbSession, user: SettingsDep, channel: ChannelDep) -> AdminCheck:
    """Dry run of an e-mail server change."""
    return dry_run(channel, "smtp", checked(channel, db, user, "smtp", body, encrypt=False))


@router.put("/config/smtp", status_code=status.HTTP_202_ACCEPTED)
def put_smtp(
    body: SmtpBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """The e-mail server of the alerts and reports (re-authentication)."""
    channel.require_enabled()
    check = checked(channel, db, user, "smtp", body, encrypt=True)
    return queue(channel, db, user, request, "smtp", check, probe=body.probe)


@router.post("/config/smtp/ca", status_code=status.HTTP_202_ACCEPTED)
def set_smtp_ca(
    body: SmtpCaBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Trust an uploaded CA file for the e-mail server (``smtp.ca_file``; re-authentication)."""
    channel.require_enabled()
    uploads = channel.uploads(user, [body.upload_id])
    check = checked(channel, db, user, "smtp", body, encrypt=True, uploads=uploads)
    return queue(
        channel, db, user, request, "smtp", check, probe=body.probe, files=files_of(check.ops)
    )


@router.delete("/config/smtp/ca", status_code=status.HTTP_202_ACCEPTED)
def clear_smtp_ca(
    request: Request,
    db: DbSession,
    user: SettingsDep,
    channel: ChannelDep,
    etag: Annotated[str, Query(max_length=100)],
    body: SmtpCaClear | None = None,
) -> AdminAccepted:
    """Back to the system's trust store for the e-mail server (re-authentication)."""
    channel.require_enabled()
    options = body or SmtpCaClear()
    form = {"clear_ca": True, "test_to": options.test_to, "probe": options.probe}
    check = checked(channel, db, user, "smtp", Body(etag=etag), encrypt=True, form=form)
    return queue(channel, db, user, request, "smtp", check, probe=options.probe)


@router.post("/config/replica/check")
def check_replica(
    body: ReplicaBody, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminCheck:
    """Dry run of a replica change."""
    return dry_run(channel, "replica", checked(channel, db, user, "replica", body, encrypt=False))


@router.put("/config/replica", status_code=status.HTTP_202_ACCEPTED)
def put_replica(
    body: ReplicaBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """The replica share (re-authentication; switching it off or moving it is held for
    ``policy.hold_hours``)."""
    channel.require_enabled()
    check = checked(channel, db, user, "replica", body, encrypt=True)
    return queue(channel, db, user, request, "replica", check, probe=body.probe)


@router.post("/config/retention/check")
def check_retention(
    body: RetentionBody, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminCheck:
    """Dry run of a retention change, with the sealed days it would delete
    (``retention_preview``). Without ``confirm_delete_days`` the dry run takes the preview's
    number: it is what the person is then asked to confirm."""
    form = body.form()
    if body.confirm_delete_days is None:
        form["confirm_delete_days"] = _days_to_delete(channel, body)
    return dry_run(
        channel,
        "retention",
        checked(channel, db, user, "retention", body, encrypt=False, form=form),
    )


def _days_to_delete(channel: Channel, body: RetentionBody) -> int:
    config = channel.usable_files().config
    changed = {"enabled": body.enabled, "days": body.days}
    retention = config.retention.model_copy(
        update={key: value for key, value in changed.items() if value is not None}
    )
    candidate = config.model_copy(update={"retention": retention})
    return channel.retention_preview(candidate).days_to_delete


@router.put("/config/retention", status_code=status.HTTP_202_ACCEPTED)
def put_retention(
    body: RetentionBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Retention of the sealed days (re-authentication; at least 730 days; a change that
    deletes sealed days needs ``confirm_delete_days`` and is held for ``policy.hold_hours``)."""
    channel.require_enabled()
    check = checked(channel, db, user, "retention", body, encrypt=True)
    return queue(channel, db, user, request, "retention", check)


@router.post("/config/backup/check")
def check_backup(
    body: BackupBody, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminCheck:
    """Dry run of a change of the database backups."""
    return dry_run(channel, "backup", checked(channel, db, user, "backup", body, encrypt=False))


@router.put("/config/backup", status_code=status.HTTP_202_ACCEPTED)
def put_backup(
    body: BackupBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """The database backups: kept copies and the free space they need (re-authentication)."""
    channel.require_enabled()
    check = checked(channel, db, user, "backup", body, encrypt=True)
    return queue(channel, db, user, request, "backup", check)
