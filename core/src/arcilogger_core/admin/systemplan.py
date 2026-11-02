"""What ApplySystem applies as SYSTEM, derived without input from any request
(ADR-0014; contract 2.8.3, Day 0 notes D0-15 and D0-16).

``python -I -m arcilogger_core.admin system-plan --config <fixed path>`` prints one
:class:`~arcilogger_core.admin.contract.SystemPlan`; ``Sync-ArciSystem.ps1`` re-checks every value
and applies it. The plan reads only the validated configuration, the administrator-only
``admin\\policy.json``, the installer's ``install\\firewall-state.json``, the collector's status
file, the version store and ApplySystem's own state in ``admin\\system\\``; the staged Zamane
clients are hashed, never opened. :func:`plan` is a pure function of :class:`PlanInputs`
(:func:`read_inputs` gathers them), so the rules below are tested without a file system:

* **syslog senders** — ``E(c)``: the enabled source addresses of configuration ``c``, each
  inside ``policy.source_networks`` and no wider than ``policy.min_prefix_v4`` (any other is
  left out and listed in ``problems`` as ``source_outside_policy``). The rule lets in
  ``E(current)`` once the collector reports the current configuration (or is stopped: its status
  file is older than 60 seconds); before that, ``E(current) ∩ E(acknowledged)``, and when the
  acknowledged version is unknown, ``E(current) ∩`` the addresses applied last. **The collector's
  file can only narrow the rule, never widen it**: a removed sender is closed at once, a new one
  opens only after the collector knows it. Nothing applied before (the first run after the
  installer, which opened ``E(current)`` itself): ``E(current)``;
* **outbound** (strict mode only) — one entry per host and port of the NTP, Kamu SM, SMTP and
  replica rules;
* **time** — the local source and the fallback, ``changed`` when the peer list differs from the
  one applied last (``Get-ArciTimePeerList``'s format);
* **jars** — every staged ``zamane\\web\\<sha256>.jar`` whose recomputed SHA-256 is that name and
  a known release (``tsa.zamane_releases``) not yet approved. Any other client waits for the
  console's ``approve-jar``: time alone never makes one runnable (D0-16).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from arcilogger_core.admin.contract import (
    AdminPolicy,
    DocumentError,
    FirewallPlan,
    JarPlan,
    JarPromotion,
    JarsFile,
    OutboundRule,
    PlanProblem,
    SystemPlan,
    SystemStatus,
    TimePlan,
    VersionIndex,
    load_policy,
    parse_document,
)
from arcilogger_core.admin.store import Installation
from arcilogger_core.config import (
    KAMUSM_PRODUCTION_HOST,
    KAMUSM_TEST_HOST,
    Config,
    parse_config,
)
from arcilogger_core.configwatch import config_status_of
from arcilogger_core.layout import Layout
from arcilogger_core.tsa.zamane_releases import find_release

#: The collector's status file older than this: the collector is stopped (counts as current).
COLLECTOR_FRESH: Final = timedelta(seconds=60)
_HOST: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_STAGED: Final = re.compile(r"^([0-9a-f]{64})\.jar$")
_CHUNK: Final = 1024 * 1024
KAMUSM_HOSTS: Final = (KAMUSM_PRODUCTION_HOST, KAMUSM_TEST_HOST)
SMB_PORT: Final = 445


class PlanError(RuntimeError):
    """The plan cannot be made (``system_plan_failed``): nothing is applied."""


@dataclass(frozen=True)
class PlanInputs:
    config: Config
    config_sha256: str
    policy: AdminPolicy
    #: ``install\\firewall-state.json`` exists (False: ``-SkipFirewall``).
    firewall_managed: bool = False
    strict_outbound: bool = False
    #: ``host:port`` of ``Set-ArciFirewall.ps1 -SmtpServer`` (remembered in its state file).
    smtp_server: str | None = None
    #: ``config.sha256`` the collector reports; None when it reports none.
    collector_ack: str | None = None
    #: The collector's status file is younger than 60 seconds.
    collector_fresh: bool = True
    #: The configuration the collector acknowledged, when the version store has it.
    acknowledged: Config | None = None
    #: The syslog addresses ApplySystem applied last (None: never).
    applied_addresses: tuple[str, ...] | None = None
    #: The Windows Time peer list applied last (None: never).
    applied_peers: str | None = None
    #: SHA-256 -> path of each staged client whose content has that SHA-256.
    staged: Mapping[str, Path] = field(default_factory=dict)
    #: SHA-256 of the clients ``jars.json`` already lists as approved.
    approved: frozenset[str] = frozenset()


def peer_list(primary: str | None, fallback: str | None) -> str:
    """``Get-ArciTimePeerList``: the local source in client mode (0x8), the fallback only as a
    fallback (0xA); without a local source the fallback alone."""
    local, public = (primary or "").strip(), (fallback or "").strip()
    if local and public and local != public:
        return f"{local},0x8 {public},0xA"
    if local:
        return f"{local},0x8"
    if public:
        return f"{public},0x8"
    return ""


def _sender(address: str) -> str:
    """The canonical text of a source address as the firewall takes it."""
    if "/" in address:
        network = ipaddress.IPv4Network(address, strict=False)
        if network.prefixlen == 32:
            return str(network.network_address)
        return str(network)
    return str(ipaddress.IPv4Address(address))


def enabled_senders(config: Config, policy: AdminPolicy) -> tuple[list[str], list[PlanProblem]]:
    """``E(config)``: the enabled source addresses inside the policy, in file order."""
    networks = [ipaddress.IPv4Network(item) for item in policy.source_networks]
    found: list[str] = []
    problems: list[PlanProblem] = []
    for source in config.sources:
        if not source.enabled:
            continue
        try:
            sender = _sender(source.address)
            network = ipaddress.IPv4Network(sender)
        except ValueError:
            problems.append(PlanProblem(code="source_outside_policy", value=source.address))
            continue
        inside = any(network.subnet_of(item) for item in networks)
        if not inside or ("/" in sender and network.prefixlen < policy.min_prefix_v4):
            problems.append(PlanProblem(code="source_outside_policy", value=sender))
            continue
        if sender not in found:
            found.append(sender)
    return found, problems


def _host_ok(host: str | None) -> bool:
    return bool(host) and _HOST.fullmatch(str(host)) is not None


def _outbound(inputs: PlanInputs, problems: list[PlanProblem]) -> list[OutboundRule]:
    config = inputs.config
    rules: list[OutboundRule] = []

    def add(rule: str, host: str | None, port: int) -> None:
        if not host:
            return
        if not _host_ok(host):
            problems.append(PlanProblem(code="outbound_host_invalid", value=host[:1000]))
            return
        entry = OutboundRule(rule=rule, host=host, port=port)
        if entry not in rules:
            rules.append(entry)

    for server in config.clock.servers:
        add("ARCILOGGER Out NTP", server, config.clock.ntp_port)
    tsa_hosts = list(KAMUSM_HOSTS)
    url = urlparse(config.tsa.url)
    if url.hostname and url.hostname not in tsa_hosts:
        tsa_hosts.append(url.hostname)
    ports = [config.tsa.port] + ([url.port] if url.port and url.port != config.tsa.port else [])
    for host in tsa_hosts:
        for port in ports:
            add("ARCILOGGER Out Kamu SM", host, port)
    smtp_targets: list[tuple[str, int]] = []
    if config.smtp.enabled and config.smtp.host:
        smtp_targets.append((config.smtp.host, config.smtp.port))
    if inputs.smtp_server:
        remembered_host, _, remembered_port = inputs.smtp_server.partition(":")
        remembered = (
            remembered_host,
            int(remembered_port) if remembered_port.isdigit() else 587,
        )
        if remembered not in smtp_targets and 1 <= remembered[1] <= 65535:
            smtp_targets.append(remembered)
    for index, (smtp_host, smtp_port) in enumerate(smtp_targets[:9], start=1):
        name = "ARCILOGGER Out SMTP" if index == 1 else f"ARCILOGGER Out SMTP {index}"
        add(name, smtp_host, smtp_port)
    replica = config.replica.target
    if config.replica.enabled and replica is not None:
        text = str(replica).replace("\\", "/").lstrip("/")
        add("ARCILOGGER Out Replica", text.split("/")[0] or None, SMB_PORT)
    return rules


def _time(inputs: PlanInputs, problems: list[PlanProblem]) -> TimePlan:
    values: list[str | None] = []
    for server in (inputs.config.clock.ntp_server, inputs.config.clock.fallback_server):
        if server and not _host_ok(server):
            problems.append(PlanProblem(code="outbound_host_invalid", value=server[:1000]))
            values.append(None)
        else:
            values.append(server or None)
    primary, fallback = values
    changed = inputs.applied_peers is None or peer_list(primary, fallback) != inputs.applied_peers
    return TimePlan(primary=primary, fallback=fallback, changed=changed)


def plan(inputs: PlanInputs) -> SystemPlan:
    """The system plan of ``inputs`` (see the module text)."""
    current, problems = enabled_senders(inputs.config, inputs.policy)
    ack = inputs.collector_ack
    if not inputs.collector_fresh or ack == inputs.config_sha256:
        allowed = current
    elif ack is not None and inputs.acknowledged is not None:
        acknowledged, _ = enabled_senders(inputs.acknowledged, inputs.policy)
        allowed = [address for address in current if address in acknowledged]
    elif inputs.applied_addresses is not None:
        allowed = [address for address in current if address in inputs.applied_addresses]
    else:
        allowed = current  # nothing applied yet: the installer opened E(current) itself
    outbound = _outbound(inputs, problems) if inputs.strict_outbound else []
    promote = [
        JarPromotion(sha256=sha256, staged=str(path), reason="known")
        for sha256, path in sorted(inputs.staged.items())
        if sha256 not in inputs.approved and find_release(sha256) is not None
    ]
    return SystemPlan(
        config_sha256=inputs.config_sha256,
        collector_ack_sha256=ack,
        firewall=FirewallPlan(
            managed=inputs.firewall_managed,
            strict_outbound=inputs.strict_outbound,
            syslog_addresses=tuple(allowed),
            outbound=tuple(outbound),
        ),
        time=_time(inputs, problems),
        jars=JarPlan(promote=tuple(promote)),
        problems=tuple(problems),
    )


# -- reading the inputs ----------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def staged_clients(staging: Path) -> dict[str, Path]:
    """SHA-256 -> path of every ``<sha256>.jar`` in ``staging`` whose content has that SHA-256
    (a file is hashed, never opened as an archive; a link or a renamed file is left out)."""
    found: dict[str, Path] = {}
    if not staging.is_dir():
        return found
    for path in sorted(staging.iterdir()):
        matched = _STAGED.fullmatch(path.name)
        if matched is None or path.is_symlink() or not path.is_file():
            continue
        if _file_sha256(path) == matched.group(1):
            found[matched.group(1)] = path
    return found


def _acknowledged(versions: Path, sha256: str, path: Path) -> Config | None:
    """The configuration of the version whose ``toml_sha256`` is ``sha256``."""
    try:
        index = parse_document(VersionIndex, (versions / "index.json").read_bytes())
    except (OSError, DocumentError):
        return None
    for entry in index.versions:
        if entry.toml_sha256 == sha256:
            try:
                data = (versions / str(entry.n) / "arcilogger.toml").read_bytes()
                return parse_config(data, path)
            except (OSError, ValueError):
                return None
    return None


def read_inputs(config_path: Path, *, now: datetime) -> PlanInputs:
    """Everything :func:`plan` needs, read now from the installation of ``config_path``
    (:class:`PlanError` when the configuration is unusable)."""
    root = Path(os.path.abspath(config_path)).parent  # noqa: PTH100 - no link followed
    try:
        data = Path(config_path).read_bytes()
        config = parse_config(data, Path(config_path))
    except (OSError, ValueError) as exc:
        raise PlanError(f"the configuration does not load: {type(exc).__name__}") from exc
    # A configuration that moves the secret store or the data root is not followed (D0-2).
    problem = Installation.of(Path(config_path)).location_problem(config)
    if problem is not None:
        raise PlanError(problem)
    admin = root / "admin"
    policy = load_policy(admin / "policy.json")
    firewall = _json(root / "install" / "firewall-state.json")
    managed = isinstance(firewall, dict)
    strict = managed and firewall.get("strict_outbound") is True
    smtp_server = firewall.get("smtp_server") if managed else None
    layout = Layout(config.data_dir)
    ack: str | None = None
    fresh = True
    try:
        age = now.timestamp() - layout.collector_status.stat().st_mtime
        fresh = age <= COLLECTOR_FRESH.total_seconds()
        status = _json(layout.collector_status)
        block = config_status_of(status) if isinstance(status, dict) else None
        ack = block.sha256 if block is not None else None
    except OSError:
        fresh = False  # no status file: the collector is not running
    config_sha256 = hashlib.sha256(data).hexdigest()
    acknowledged = (
        _acknowledged(admin / "versions", ack, Path(config_path))
        if ack and ack != config_sha256
        else None
    )
    applied_addresses: tuple[str, ...] | None = None
    applied_peers: str | None = None
    try:
        system = parse_document(SystemStatus, (admin / "system" / "status.json").read_bytes())
        applied_addresses = system.firewall.syslog_addresses
        applied_peers = system.time.peers
    except (OSError, DocumentError):
        pass
    approved: frozenset[str] = frozenset()
    try:
        jars = parse_document(JarsFile, (admin / "system" / "jars.json").read_bytes())
        approved = frozenset(entry.sha256 for entry in jars.approved)
    except (OSError, DocumentError):
        pass
    return PlanInputs(
        config=config,
        config_sha256=config_sha256,
        policy=policy,
        firewall_managed=managed,
        strict_outbound=strict,
        smtp_server=smtp_server if isinstance(smtp_server, str) and smtp_server else None,
        collector_ack=ack,
        collector_fresh=fresh,
        acknowledged=acknowledged,
        applied_addresses=applied_addresses,
        applied_peers=applied_peers,
        staged=staged_clients(root / "zamane" / "web"),
        approved=approved,
    )


def system_plan(config_path: Path, *, now: datetime) -> SystemPlan:
    return plan(read_inputs(config_path, now=now))
