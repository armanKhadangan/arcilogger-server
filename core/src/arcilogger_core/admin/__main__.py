"""``python -I -m arcilogger_core.admin``: the administration agent and its console commands
(ADR-0014; contract 2.7.2, 2.8.3, Day 0 note D0-16).

::

    serve --config PATH                 run ArciAdmin (the Windows service ArciAdmin)
    keys init|show --config PATH        create the channel keys (installer) / show the public ones
    status --config PATH [--json]       the agent's heartbeat, held changes, versions, journal
    versions list --config PATH [--json]
    versions restore N --config PATH    put version N back (manual recovery; console only)
    recover --config PATH               finish or undo an apply a crash interrupted
    approve-jar SHA256 --config PATH [--user NAME]
                                        approve a staged Zamane client (administrator)
    system-plan --config PATH           ApplySystem's plan as JSON (read-only, SYSTEM)

``--config`` is the fixed path every service is started with (``C:\\ProgramData\\ARCILOGGER\\
arcilogger.toml``); its folder is the installation folder of every file used here. Exit codes:
0 success, 1 a problem that needs attention, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import logging
import re
import signal
import stat
import sys
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from arcilogger_core import __version__
from arcilogger_core.admin.agent import Agent
from arcilogger_core.admin.apply import Applier, ChangePlan
from arcilogger_core.admin.contract import (
    AgentStatus,
    ApprovedJar,
    DocumentError,
    Event,
    JarChange,
    JarsFile,
    SystemJournalLine,
    dump_document,
    dump_line,
    heartbeat_state,
    load_policy,
    new_id,
    parse_document,
)
from arcilogger_core.admin.keys import KeysError, init_keys, load_keys
from arcilogger_core.admin.policy import jar_approvable_at, read_policy
from arcilogger_core.admin.store import (
    Installation,
    append_line,
    read_bytes_or_none,
    read_journal,
    read_pending,
    read_txn,
    read_waits,
)
from arcilogger_core.admin.systemplan import PlanError, system_plan
from arcilogger_core.admin.winapi import default_event_log
from arcilogger_core.config import default_config_path, load_config, parse_config
from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.timeutil import isoformat_site, site_day, to_site, utc_now
from arcilogger_core.tsa.zamane_releases import find_release

log = logging.getLogger("arcilogger.admin")

#: How often ``serve`` looks at the inbox.
POLL_SECONDS: Final = 1.0
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT: Final = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class ConsoleError(Exception):
    """A console command cannot do what was asked (the message says why)."""


def _now() -> datetime:
    return to_site(utc_now())


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# -- serve -----------------------------------------------------------------------------------------


def _setup_logging(config_path: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        logs = load_config(config_path).data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logs / "admin.log", encoding="utf-8"))
    except (OSError, ValueError):
        pass  # the agent reports an unusable configuration itself
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_serve(config_path: Path) -> int:
    _setup_logging(config_path)
    stop = threading.Event()
    if sys.platform != "win32":  # Windows: the service wrapper sends Ctrl+C (KeyboardInterrupt)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
    agent = Agent(config_path)
    log.info("ArciAdmin %s starting (%s)", __version__, agent.installation.toml)
    agent.start()
    try:
        while not stop.is_set():
            agent.run_once()
            stop.wait(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
        log.info("ArciAdmin stopped")
    return 0


# -- keys ------------------------------------------------------------------------------------------


def cmd_keys(action: str, installation: Installation) -> int:
    keys_dir = installation.admin.keys
    try:
        keys = init_keys(keys_dir) if action == "init" else load_keys(keys_dir)
    except KeysError as exc:
        print(f"Hata [error]: {exc}", file=sys.stderr)
        return 2
    print(f"Kurulum [instance]: {keys.instance}")
    for entry in keys.keys:
        print(f"{entry.role:6} {entry.id}")
    return 0


# -- status ----------------------------------------------------------------------------------------


def _heartbeat(path: Path) -> AgentStatus | None:
    data = read_bytes_or_none(path)
    try:
        return None if data is None else parse_document(AgentStatus, data)
    except DocumentError:
        return None


def _agent_summary(installation: Installation, now: datetime) -> dict[str, Any]:
    layout = installation.admin
    heartbeat = _heartbeat(layout.agent_status)
    policy = read_policy(layout.policy)
    versions = Applier(installation, now=_now).versions.index().versions
    today = site_day(now)
    journal = read_journal(layout, today)
    return {
        "agent": {
            "state": heartbeat_state(heartbeat.heartbeat_at if heartbeat else None, now),
            "heartbeat_at": heartbeat.heartbeat_at.isoformat() if heartbeat else None,
            "version": heartbeat.version if heartbeat else None,
            "seal_ready": heartbeat.seal_ready.model_dump() if heartbeat else None,
        },
        "policy": {"enabled": policy.enabled, "sha256": policy.sha256, "error": policy.error},
        "pending": [
            {"id": item.id, "kind": item.kind, "effective_at": item.effective_at.isoformat()}
            for item in read_pending(layout)
        ],
        "tasks_waiting": len(read_waits(layout).tasks),
        "transaction_in_progress": read_txn(layout) is not None,
        "versions": [entry.n for entry in versions],
        "journal_today": [
            {"request": line.request, "state": line.state, "kind": line.kind, "code": line.code}
            for line in journal[-10:]
        ],
    }


def cmd_status(installation: Installation, *, as_json: bool) -> int:
    now = _now()
    try:
        summary = _agent_summary(installation, now)
    except (OSError, DocumentError) as exc:
        print(f"Hata [error]: {exc}", file=sys.stderr)
        return 2
    if as_json:
        _print_json(summary)
    else:
        agent = summary["agent"]
        print(f"ArciAdmin: {agent['state']} (heartbeat {agent['heartbeat_at'] or '-'})")
        policy = summary["policy"]
        enabled = "açık [on]" if policy["enabled"] else "kapalı [off]"
        print(f"Tarayıcıdan yönetim [browser administration]: {enabled}")
        if agent["seal_ready"] is not None:
            ready = agent["seal_ready"]
            print(f"Mühürleme hazır [sealing ready]: {ready['ok']} {', '.join(ready['causes'])}")
        print(f"Bekletilen [held]: {len(summary['pending'])}")
        for item in summary["pending"]:
            print(f"  {item['id']}  {item['kind']:4}  {item['effective_at']}")
        print(f"Bekleyen görev [tasks waiting]: {summary['tasks_waiting']}")
        if summary["transaction_in_progress"]:
            print("UYARI: yarım kalmış bir uygulama var [an apply is in progress]: recover")
        print(f"Sürümler [versions]: {', '.join(str(n) for n in summary['versions']) or '-'}")
        for line in summary["journal_today"]:
            kind, code = line["kind"] or "-", line["code"] or ""
            print(f"  {line['request'][:8]}  {line['state']:16} {kind}  {code}")
    healthy = summary["agent"]["state"] == "ok" and not summary["transaction_in_progress"]
    return 0 if healthy else 1


# -- versions --------------------------------------------------------------------------------------


def cmd_versions_list(installation: Installation, *, as_json: bool) -> int:
    versions = Applier(installation, now=_now).versions.index().versions
    if as_json:
        _print_json([entry.model_dump(mode="json") for entry in versions])
        return 0
    if not versions:
        print("Saklanan sürüm yok [no version kept].")
    for entry in versions:
        secrets = "secrets.json" if entry.has_secrets else "-"
        request = entry.request[:8] if entry.request else "console"
        print(
            f"{entry.n:4}  {isoformat_site(entry.at, 'seconds')}  {request:8}  {entry.actor:12}  "
            f"{secrets:12}  {', '.join(entry.paths)}"
        )
    return 0


def cmd_versions_restore(installation: Installation, number: int) -> int:
    """Put version ``number`` back as one transaction (not journaled: it is no request; the
    version store records it with ``actor: console``)."""
    applier = Applier(installation, now=_now)
    entry = applier.versions.entry(number)
    if entry is None:
        print(f"Sürüm yok [no such version]: {number}", file=sys.stderr)
        return 2
    if read_txn(installation.admin) is not None:
        print(
            "Yarım kalmış bir uygulama var; önce 'recover' çalıştırın [an apply is in progress: "
            "run 'recover' first].",
            file=sys.stderr,
        )
        return 1
    toml, secrets = applier.versions.read(number)
    try:
        parse_config(toml, installation.toml)
    except ValueError as exc:
        print(f"Sürüm {number} yüklenemiyor [does not load]: {exc}", file=sys.stderr)
        return 1
    current_toml = installation.toml.read_bytes()
    current_secrets = read_bytes_or_none(installation.secrets)
    if secrets is None and entry.secrets_sha256 is not None:
        print(
            f"Not: sürüm {number} için secrets.json saklanmıyor; yalnızca arcilogger.toml geri "
            "yüklenir [only arcilogger.toml is restored].",
        )
    plan = ChangePlan(
        id=new_id(),
        request=None,
        actor="console",
        paths=entry.paths,
        old_toml=current_toml,
        old_secrets=current_secrets,
        new_toml=toml,
        new_secrets=secrets if secrets != current_secrets else None,
    )
    outcome = applier.apply(plan)
    if outcome.state == "applied":
        applier.commit()
        saved = outcome.version.n if outcome.version else "-"
        print(
            f"Sürüm {number} geri yüklendi [restored]; önceki hali sürüm {saved} "
            f"[the previous pair is version {saved}]."
        )
        if outcome.code == "acl_drift":
            print(
                "Not: ayar dosyalarından biri artık yazılamıyor; Set-ArciVaultAcl.ps1 betiğini "
                "çalıştırın [a configuration file is no longer writable: run Set-ArciVaultAcl.ps1]."
            )
        return 0
    if outcome.state == "needs_attention":
        applier.commit()  # not journaled: the printed code is the report
    print(f"Geri yükleme başarısız [restore failed]: {outcome.code}", file=sys.stderr)
    return 1


def cmd_recover(installation: Installation) -> int:
    agent = Agent(installation.config_path, event_log=default_event_log())
    outcome = agent.recover()
    if outcome is None:
        print("Yarım kalmış uygulama yok [nothing to recover].")
        return 0
    print(f"{outcome.state} {outcome.code or ''}".strip())
    return 0 if outcome.state == "applied" or outcome.code == "interrupted" else 1


# -- approve-jar -----------------------------------------------------------------------------------


def _is_link(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & _REPARSE_POINT)


def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_jars(path: Path) -> JarsFile:
    data = read_bytes_or_none(path)
    return JarsFile() if data is None else parse_document(JarsFile, data)


def approve_jar(installation: Installation, sha256: str, *, user: str, now: datetime) -> Path:
    """Make a staged Zamane client runnable (Day 0 note D0-16): at the earliest
    ``jar_hold_hours`` after ApplySystem first saw it (a known release at once), copied into
    ``zamane\\approved\\``, its hash checked, ``approved_by: console:<user>`` recorded."""
    if not _SHA256.fullmatch(sha256):
        raise ConsoleError("SHA-256 64 küçük onaltılık hane olmalı [64 lower-case hex digits]")
    layout = installation.admin
    jars = _read_jars(layout.jars)
    approved = installation.zamane.approved / f"{sha256}.jar"
    if any(entry.sha256 == sha256 for entry in jars.approved):
        return approved
    staged = next((entry for entry in jars.staged if entry.sha256 == sha256), None)
    if staged is None:
        raise ConsoleError(
            "ApplySystem bu istemciyi henüz görmedi; 5 dakika içinde tekrar deneyin [ApplySystem "
            "has not recorded this client yet; try again within 5 minutes]"
        )
    policy = load_policy(layout.policy)
    earliest = jar_approvable_at(policy, staged.first_seen)
    if find_release(sha256) is None and now < earliest:
        raise ConsoleError(
            f"Bekletme süresi dolmadı; en erken {isoformat_site(earliest, 'seconds')} "
            f"[the hold has not ended; at the earliest {isoformat_site(earliest, 'seconds')}]"
        )
    source = installation.zamane.staging / f"{sha256}.jar"
    if not source.is_file() or _is_link(source) or _sha256_of(source) != sha256:
        raise ConsoleError(f"{source} yok ya da SHA-256 tutmuyor [missing or another SHA-256]")
    approved.parent.mkdir(parents=True, exist_ok=True)
    temp = approved.with_name(f".{approved.name}.tmp")
    temp.write_bytes(source.read_bytes())
    if _sha256_of(temp) != sha256:
        temp.unlink()
        raise ConsoleError("kopyanın SHA-256 değeri farklı [the copy has another SHA-256]")
    temp.replace(approved)
    by = f"console:{user}"
    current = _read_jars(layout.jars)  # ApplySystem may have written meanwhile: keep its entries
    entry = ApprovedJar(sha256=sha256, path=str(approved), approved_at=now, approved_by=by)
    atomic_write_bytes(
        layout.jars,
        dump_document(current.model_copy(update={"approved": (*current.approved, entry)})),
    )
    line = SystemJournalLine(
        line=new_id(), at=now, change="jar", jar=JarChange(sha256=sha256, approved_by=by)
    )
    append_line(layout.system_journal, dump_line(line))
    default_event_log().write(
        Event.JAR_PROMOTED,
        "warning",
        f"Zamane istemcisi onaylandı [client approved]: {sha256} ({by})",
    )
    return approved


def cmd_approve_jar(installation: Installation, sha256: str, user: str | None) -> int:
    try:
        path = approve_jar(installation, sha256.lower(), user=user or getpass.getuser(), now=_now())
    except (ConsoleError, OSError, DocumentError) as exc:
        print(f"Onaylanmadı [not approved]: {exc}", file=sys.stderr)
        return 1
    print(f"Onaylandı [approved]: {path}")
    return 0


# -- system-plan -----------------------------------------------------------------------------------


def cmd_system_plan(installation: Installation) -> int:
    try:
        planned = system_plan(installation.config_path, now=_now())
    except PlanError as exc:
        print(f"system_plan_failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(dump_document(planned).decode("utf-8"))
    return 0


# -- the parser ------------------------------------------------------------------------------------


def _utf8_console() -> None:
    """Turkish text must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arcilogger_core.admin",
        description="ARCILOGGER browser administration agent (ArciAdmin) and console commands",
    )
    parser.add_argument("--version", action="version", version=f"ARCILOGGER admin {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
        return sub

    command("serve", "run the administration agent until stopped")
    keys = command("keys", "the channel keys")
    keys.add_argument("action", choices=("init", "show"))
    status = command("status", "the agent's heartbeat, held changes and versions")
    status.add_argument("--json", action="store_true")
    versions = command("versions", "the configuration versions")
    versions.add_argument("action", choices=("list", "restore"))
    versions.add_argument("number", nargs="?", type=int)
    versions.add_argument("--json", action="store_true")
    command("recover", "finish or undo an apply a crash interrupted")
    approve = command("approve-jar", "approve a staged Zamane client")
    approve.add_argument("sha256")
    approve.add_argument("--user", default=None, help="who approves (default: this user)")
    command("system-plan", "print ApplySystem's plan (JSON)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    config_path: Path = args.config or default_config_path()
    if args.command == "serve":
        return cmd_serve(config_path)
    installation = Installation.of(config_path)
    if args.command == "keys":
        return cmd_keys(args.action, installation)
    if args.command == "status":
        return cmd_status(installation, as_json=args.json)
    if args.command == "versions":
        if args.action == "list":
            return cmd_versions_list(installation, as_json=args.json)
        if args.number is None:
            print("versions restore N: sürüm numarası gerekli [a version number]", file=sys.stderr)
            return 2
        return cmd_versions_restore(installation, args.number)
    if args.command == "recover":
        return cmd_recover(installation)
    if args.command == "approve-jar":
        return cmd_approve_jar(installation, args.sha256, args.user)
    return cmd_system_plan(installation)


if __name__ == "__main__":
    raise SystemExit(main())
