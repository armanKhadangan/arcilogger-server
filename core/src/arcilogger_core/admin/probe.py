"""Connection tests with the candidate values of a browser change (ADR-0014; contract 2.6).

Before a change of the time servers, the Kamu SM credentials or server, SMTP or the replica is
written, the agent tests it with exactly the values it would write:

* ``sntp`` — one SNTP exchange with a new time server (``clockcheck.sntp_query``, 5 s);
* ``credit`` — the Zamane client's credit query ``-k`` (90 s; it costs no credit);
* ``smtp`` — the trial e-mail (``arcilogger_core.smtp``) to the recipient the user chose;
* ``replica`` — the share connected with the candidate user (``smbshare.connected``) and the
  write, read and remove test of ``replica.probe`` (60 s);
* ``backup_mirror`` — the same test of the backup copy's target on the replica's NAS, with the
  replica's user (Wave 5 addendum C, C6; 60 s);
* ``snmp`` — a walk of ``ifName`` with a source's candidate SNMPv3 settings and keys, then the
  operational status, speed and 64-bit counters of the configured WAN interfaces
  (:mod:`arcilogger_core.snmp`, 15 s; Wave 5 §6 N5). Once the walk answered, its
  ``interfaces`` list what the device reports (name, ``ifOperStatus``, ``ifHighSpeed``), so the
  administrator can pick the WAN lines (W5-N-9); a failure names its N4 reason
  (``snmp_timeout``, ``snmp_auth_failed``, ``snmp_no_such_interface``, ``snmp_no_hc_counters``).

Each runs in a child process (``python -I -m arcilogger_core.admin.probe``,
:class:`ChildProber`): a hanging client or share is killed at its timeout and leaves nothing in
the agent. The request, secrets included, goes to the child on its standard input, never on its
command line; the child answers one JSON line ``{"ok", "code", "message", "interfaces"}`` on its
standard output, which the agent reads capped and bounded (:func:`bounded_interfaces`). A message
or an interface name never contains a secret (each one is replaced by ``***`` as well). The
Zamane client itself takes the password on its command line (residual risk 4 of contract 2.18,
as the evidence tasks do).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol, get_args

from pydantic import JsonValue

from arcilogger_core.admin.contract import (
    PROBE_INTERFACE_NAME_MAX,
    PROBE_INTERFACES_MAX,
    OperStatusName,
    ProbeInterface,
)
from arcilogger_core.clockcheck import ClockError, sntp_query
from arcilogger_core.config import SmtpConfig, WebhookConfig
from arcilogger_core.replica import probe as replica_probe
from arcilogger_core.smbshare import ShareApi, connected
from arcilogger_core.smtp import SmtpSender, SmtpSendError, trial_message
from arcilogger_core.snmp import PysnmpClient, SnmpClient, SnmpError, Target, list_interfaces
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.zamane import ZamaneCliProvider
from arcilogger_core.webhook import Target as WebhookTarget
from arcilogger_core.webhook import WebhookAlert, WebhookError, encode, payload
from arcilogger_core.webhook import send as send_webhook

ProbeKind = Literal["sntp", "credit", "smtp", "replica", "snmp", "backup_mirror", "webhook"]
#: The failure code of each probe (contract 2.12).
FAILURE_CODES: Final[Mapping[str, str]] = {
    "sntp": "ntp_unreachable",
    "credit": "credit_query_failed",
    "smtp": "smtp_test_failed",
    "replica": "replica_test_failed",
    "snmp": "snmp_timeout",
    "backup_mirror": "backup_mirror_test_failed",
    "webhook": "webhook_test_failed",
}
#: The SNMP probe's failure codes: N4's reasons (Wave 5 §6 N5), each with TR/EN text.
SNMP_CODES: Final[Mapping[str, str]] = {
    status: status if status.startswith("snmp_") else f"snmp_{status}"
    for status in (
        "timeout",
        "auth_failed",
        "no_such_interface",
        "snmp_no_hc_counters",
    )
}
#: Seconds each probe may take (the child is killed a little later).
TIMEOUTS: Final[Mapping[str, float]] = {
    "sntp": 5,
    "credit": 90,
    "smtp": 60,
    "replica": 60,
    "snmp": 15,
    "backup_mirror": 60,
    # ``[[webhooks]] timeout_seconds`` is at most 60.
    "webhook": 70,
}
CHILD_MARGIN: Final = 10.0
#: Bytes read from the child, characters kept of a message.
MAX_OUTPUT: Final = 64 * 1024
MAX_MESSAGE: Final = 500
MAX_INPUT: Final = 256 * 1024


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    #: The failure code (``ntp_unreachable`` …), None when ``ok``.
    code: str | None
    #: A short technical description for the log (never a secret).
    message: str
    #: What the device reported to the ``snmp`` probe once its walk answered (W5-N-9).
    interfaces: tuple[ProbeInterface, ...] = ()


class Prober(Protocol):
    def run(
        self, kind: ProbeKind, params: Mapping[str, JsonValue], secrets: Mapping[str, str]
    ) -> ProbeResult: ...


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """``text`` without any of the ``secrets`` (each replaced by ``***``), capped."""
    for value in secrets.values():
        if value and len(value) >= 3:
            text = text.replace(value, "***")
    return text[:MAX_MESSAGE]


_OPER_STATUSES: Final = frozenset(get_args(OperStatusName))


def bounded_interfaces(raw: object, secrets: Mapping[str, str]) -> tuple[ProbeInterface, ...]:
    """An interface list as the child sent it, bounded (W5-N-9): at most
    :data:`PROBE_INTERFACES_MAX` entries; a name without the ``secrets``, its control characters
    replaced by ``?`` and cut to :data:`PROBE_INTERFACE_NAME_MAX` characters; a status other than
    an RFC 2863 name or a speed other than a whole number from 0 is null; anything else is left
    out."""
    if not isinstance(raw, list):
        return ()
    found: list[ProbeInterface] = []
    for item in raw[:PROBE_INTERFACES_MAX]:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str):
            continue
        name = "".join(c if c.isprintable() else "?" for c in redact(name, secrets))
        name = name[:PROBE_INTERFACE_NAME_MAX]
        if not name:
            continue
        oper, speed = item.get("oper_status"), item.get("speed_mbps")
        found.append(
            ProbeInterface(
                name=name,
                oper_status=oper if oper in _OPER_STATUSES else None,
                speed_mbps=speed if type(speed) is int and speed >= 0 else None,
            )
        )
    return tuple(found)


def _failed(kind: str, message: str, secrets: Mapping[str, str]) -> ProbeResult:
    return ProbeResult(False, FAILURE_CODES[kind], redact(message, secrets))


# -- the probes (they run in the child) ------------------------------------------------------------


def probe_sntp(params: Mapping[str, Any]) -> ProbeResult:
    server, port = str(params["server"]), int(params.get("port", 123))
    try:
        result = sntp_query(server, port, timeout=TIMEOUTS["sntp"])
    except ClockError as exc:
        return _failed("sntp", str(exc), {})
    return ProbeResult(True, None, f"{server}: offset {result.offset_s * 1000:.1f} ms")


def probe_credit(
    params: Mapping[str, Any],
    secrets: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> ProbeResult:
    """``-k`` with the candidate client, server and credentials (``java`` and ``jar`` are the
    ones ``execpolicy`` checked in the agent)."""
    extra: dict[str, Any] = {"runner": runner} if runner is not None else {}
    provider = ZamaneCliProvider(
        java=[str(item) for item in params["java"]],
        jar=Path(str(params["jar"])),
        url=str(params["url"]),
        port=int(params["port"]),
        customer=secrets.get("kamusm.customer", ""),
        password=secrets.get("kamusm.password", ""),
        legal_grade=False,
        timeout_seconds=float(params.get("timeout_seconds", TIMEOUTS["credit"])),
        verify_flag=str(params.get("verify_flag", "-CC")),
        credit_regex=str(params["credit_regex"]),
        **extra,
    )
    try:
        credits = provider.credits()
    except TsaError as exc:
        return _failed("credit", str(exc), secrets)
    return ProbeResult(True, None, f"credits: {credits}")


def probe_smtp(
    params: Mapping[str, Any], secrets: Mapping[str, str], *, now: datetime | None = None
) -> ProbeResult:
    """The trial e-mail with the candidate ``[smtp]`` to ``params["to"]``."""
    settings = SmtpConfig.model_validate(params["smtp"])
    password = secrets.get("smtp.password")
    sender = SmtpSender(settings, password)
    message = trial_message(
        sender=str(settings.from_address),
        recipient=str(params["to"]),
        web_url=str(params.get("web_url", "")),
        now=now or utc_now(),
    )
    try:
        sender.send(message, str(params["to"]))
    except SmtpSendError as exc:
        return _failed("smtp", str(exc), secrets)
    return ProbeResult(True, None, f"accepted by {settings.host}:{settings.port}")


def probe_replica(
    params: Mapping[str, Any],
    secrets: Mapping[str, str],
    *,
    api: ShareApi | None = None,
    kind: ProbeKind = "replica",
) -> ProbeResult:
    """The share connected with the candidate user and a file written, read and removed: the
    replica's target, or (``backup_mirror``, Wave 5 addendum C) the backup copy's target on the
    same NAS with the replica's user."""
    target = Path(str(params["target"]))
    user = params.get("user")
    try:
        with connected(
            target, str(user) if user else None, secrets.get("replica.password"), api=api
        ):
            message = replica_probe(target)
    except OSError as exc:  # ShareError is an OSError; it never contains the password
        return _failed(kind, f"{type(exc).__name__}: {exc}", secrets)
    return ProbeResult(True, None, message)


def probe_snmp(
    params: Mapping[str, Any], secrets: Mapping[str, str], *, client: SnmpClient | None = None
) -> ProbeResult:
    """The candidate ``[sources.snmp]`` of one source against its device (N5)."""
    import asyncio

    target = Target(
        host=str(params["host"]),
        port=int(params["port"]),
        user=str(params["user"]),
        auth=params["auth"],
        priv=params["priv"],
        auth_key=secrets.get("auth_key", ""),
        priv_key=secrets.get("priv_key", ""),
    )
    wanted = [str(name) for name in params.get("interfaces", ())]
    try:
        interfaces = asyncio.run(list_interfaces(client or PysnmpClient(), target, wanted))
    except SnmpError as exc:
        return ProbeResult(False, SNMP_CODES.get(exc.status, "snmp_timeout"), exc.detail)
    found = "; ".join(
        f"{item.name}: {item.oper_status or '?'}, "
        f"{'?' if item.speed_mbps is None else item.speed_mbps} Mbit/s"
        for item in interfaces
    )
    listed = bounded_interfaces(
        [
            {"name": item.name, "oper_status": item.oper_status, "speed_mbps": item.speed_mbps}
            for item in interfaces
        ],
        secrets,
    )
    names = {item.name: item for item in interfaces}
    missing = [name for name in wanted if name not in names]
    if missing:
        message = f"not found: {', '.join(missing)}. found: {found}"
        return ProbeResult(False, "snmp_no_such_interface", message, listed)
    if any(not names[name].counters for name in wanted):
        return ProbeResult(False, "snmp_no_hc_counters", found, listed)
    return ProbeResult(True, None, found, listed)


#: The test notification of a webhook (Wave 6 contract §2.4.4).
WEBHOOK_TEST_RULE: Final = "TEST"
WEBHOOK_TEST_TITLES: Final = ("ARCILOGGER deneme bildirimi", "ARCILOGGER test notification")


def probe_webhook(
    params: Mapping[str, Any], secrets: Mapping[str, str], *, now: datetime | None = None
) -> ProbeResult:
    """The test notification ("Test gönder", §2.4.4) to one ``[[webhooks]]`` entry
    (``params["webhook"]``), through ``params["https_proxy"]``: ``ok`` with the status code, or
    ``webhook_test_failed`` with the error text of §2.4.2. ``secrets``: ``secret`` (the HMAC
    secret of a ``generic`` webhook) or ``url`` (the URL of a ``teams`` or ``slack`` one)."""
    webhook = WebhookConfig.model_validate(params["webhook"])
    moment = now or utc_now()
    web_url = str(params.get("web_url", "")).rstrip("/")
    target = WebhookTarget(
        url=webhook.url if webhook.format == "generic" else secrets.get("url", ""),
        format=webhook.format,
        secret=secrets.get("secret"),
        timeout_seconds=webhook.timeout_seconds,
        ca_file=webhook.ca_file,
        https_proxy=str(params.get("https_proxy", "")),
    )
    test = WebhookAlert(
        id=0,
        rule=WEBHOOK_TEST_RULE,
        severity="info",
        state="open",
        title_tr=WEBHOOK_TEST_TITLES[0],
        title_en=WEBHOOK_TEST_TITLES[1],
        time=moment,
        count=1,
        url=web_url,
    )
    version = str(params.get("version", ""))
    document = payload(webhook.format, test, version=version, site=str(params.get("site", "")))
    try:
        status = send_webhook(target, encode(document), version=version, now=moment)
    except WebhookError as exc:
        return _failed("webhook", str(exc), secrets)
    return ProbeResult(True, None, f"HTTP {status}")


def run_probe(kind: str, params: Mapping[str, Any], secrets: Mapping[str, str]) -> ProbeResult:
    """One probe in this process (the child's work)."""
    if kind == "sntp":
        return probe_sntp(params)
    if kind == "credit":
        return probe_credit(params, secrets)
    if kind == "smtp":
        return probe_smtp(params, secrets)
    if kind == "replica":
        return probe_replica(params, secrets)
    if kind == "backup_mirror":
        return probe_replica(params, secrets, kind="backup_mirror")
    if kind == "snmp":
        return probe_snmp(params, secrets)
    if kind == "webhook":
        return probe_webhook(params, secrets)
    raise ValueError(f"unknown probe {kind!r}")


# -- the parent ------------------------------------------------------------------------------------


class ChildProber:
    """Runs each probe in ``python -I -m arcilogger_core.admin.probe`` (contract 2.1).
    ``timeouts`` and ``margin``: how long the child may take (tests shorten them)."""

    def __init__(
        self,
        python: str = sys.executable,
        *,
        timeouts: Mapping[str, float] = TIMEOUTS,
        margin: float = CHILD_MARGIN,
    ) -> None:
        self._python = python
        self._timeouts = timeouts
        self._margin = margin

    def run(
        self, kind: ProbeKind, params: Mapping[str, JsonValue], secrets: Mapping[str, str]
    ) -> ProbeResult:
        request = json.dumps({"kind": kind, "params": params, "secrets": dict(secrets)})
        limit = self._timeouts[kind] + self._margin
        try:
            completed = subprocess.run(  # noqa: S603 - our own interpreter and module
                [self._python, "-I", "-m", "arcilogger_core.admin.probe"],
                input=request.encode("utf-8"),
                capture_output=True,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _failed(kind, f"no answer within {limit:.0f} s", {})
        except OSError as exc:
            return _failed(kind, f"the probe could not start: {exc}", secrets)
        output = completed.stdout[:MAX_OUTPUT].decode("utf-8", "replace").strip()
        try:
            answer = json.loads(output.splitlines()[-1]) if output else None
        except (ValueError, IndexError):
            answer = None
        if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
            error = completed.stderr[:MAX_OUTPUT].decode("utf-8", "replace").strip()
            return _failed(kind, f"exit code {completed.returncode}: {error[-300:]}", secrets)
        message = redact(str(answer.get("message", "")), secrets)
        listed = bounded_interfaces(answer.get("interfaces"), secrets) if kind == "snmp" else ()
        if answer["ok"]:
            return ProbeResult(True, None, message, listed)
        code = answer.get("code")
        if kind == "snmp" and code in SNMP_CODES.values():
            # N4's reason, not one generic code, and what the device reported
            return ProbeResult(False, str(code), message, listed)
        return ProbeResult(False, FAILURE_CODES[kind], message)


def main() -> int:
    """The child: one request on standard input, one JSON line on standard output."""
    try:
        request = json.loads(sys.stdin.buffer.read(MAX_INPUT).decode("utf-8"))
        kind = str(request["kind"])
        secrets = {str(k): str(v) for k, v in dict(request.get("secrets", {})).items()}
        result = run_probe(kind, dict(request["params"]), secrets)
    except Exception as exc:  # the parent reads a failure, never a traceback with values
        print(json.dumps({"ok": False, "code": None, "message": type(exc).__name__}))
        return 1
    interfaces = [item.model_dump(mode="json") for item in result.interfaces]
    answer = {"ok": result.ok, "code": result.code, "message": result.message}
    print(json.dumps({**answer, "interfaces": interfaces}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
