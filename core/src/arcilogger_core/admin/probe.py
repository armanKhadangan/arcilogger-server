"""Connection tests with the candidate values of a browser change (ADR-0014; contract 2.6).

Before a change of the time servers, the Kamu SM credentials or server, SMTP or the replica is
written, the agent tests it with exactly the values it would write:

* ``sntp`` — one SNTP exchange with a new time server (``clockcheck.sntp_query``, 5 s);
* ``credit`` — the Zamane client's credit query ``-k`` (90 s; it costs no credit);
* ``smtp`` — the trial e-mail (``arcilogger_core.smtp``) to the recipient the user chose;
* ``replica`` — the share connected with the candidate user (``smbshare.connected``) and the
  write, read and remove test of ``replica.probe`` (60 s).

Each runs in a child process (``python -I -m arcilogger_core.admin.probe``,
:class:`ChildProber`): a hanging client or share is killed at its timeout and leaves nothing in
the agent. The request, secrets included, goes to the child on its standard input, never on its
command line; the child answers one JSON line ``{"ok", "code", "message"}`` on its standard
output, which the agent reads capped. A message never contains a secret (each one is replaced
by ``***`` as well). The Zamane client itself takes the password on its command line (residual
risk 4 of contract 2.18, as the evidence tasks do).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from pydantic import JsonValue

from arcilogger_core.clockcheck import ClockError, sntp_query
from arcilogger_core.config import SmtpConfig
from arcilogger_core.replica import probe as replica_probe
from arcilogger_core.smbshare import ShareApi, connected
from arcilogger_core.smtp import SmtpSender, SmtpSendError, trial_message
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.zamane import ZamaneCliProvider

ProbeKind = Literal["sntp", "credit", "smtp", "replica"]
#: The failure code of each probe (contract 2.12).
FAILURE_CODES: Final[Mapping[str, str]] = {
    "sntp": "ntp_unreachable",
    "credit": "credit_query_failed",
    "smtp": "smtp_test_failed",
    "replica": "replica_test_failed",
}
#: Seconds each probe may take (the child is killed a little later).
TIMEOUTS: Final[Mapping[str, float]] = {"sntp": 5, "credit": 90, "smtp": 60, "replica": 60}
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
    params: Mapping[str, Any], secrets: Mapping[str, str], *, api: ShareApi | None = None
) -> ProbeResult:
    target = Path(str(params["target"]))
    user = params.get("user")
    try:
        with connected(
            target, str(user) if user else None, secrets.get("replica.password"), api=api
        ):
            message = replica_probe(target)
    except OSError as exc:  # ShareError is an OSError; it never contains the password
        return _failed("replica", f"{type(exc).__name__}: {exc}", secrets)
    return ProbeResult(True, None, message)


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
        if answer["ok"]:
            return ProbeResult(True, None, message)
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
    print(json.dumps({"ok": result.ok, "code": result.code, "message": result.message}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
