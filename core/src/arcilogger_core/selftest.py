"""The check after setup (0.4.0 P3): ``arcilogger-core --config <toml> selftest [--json]``.

Seven rows, each ``ok``, ``warn``, ``fail`` or ``skip``, with a fixed Turkish and English text
and, for a row that is not ok, its cause (an error text without any stored secret):

* ``config`` — the configuration loads (``fail``; every other row is then skipped);
* ``secrets`` — the secret store opens (``fail``) and the credentials the configuration uses are
  stored (``warn``: the browser's setup wizard asks for the time-stamp provider's, the e-mail
  server's and the replica's after the installation);
* ``database`` — ``arcilogger-db check`` connects and the schema is at the head (``fail``); it
  runs as a child process, so this module imports nothing of the database stack (ADR-0001);
* ``roots`` — a root certificate is installed and, for TÜRKTRUST, its H7 root (``fail``;
  the mock's test root is skipped);
* ``tsa.reach`` — TÜRKTRUST: an HTTPS request without credentials gets any HTTP answer (401 is
  the expected one, contract S6); Kamu SM and other RFC 3161 servers: a TCP connection; the mock
  is skipped (``warn``);
* ``smtp`` — with e-mail switched on: connect, TLS, EHLO and AUTH, then QUIT; nothing is sent
  (``warn``, with the explanation of a known Google or Microsoft 365 refusal);
* ``clock`` — the offset to the first time server that answers is within
  ``clock.alert_offset_ms`` (``warn``).

Nothing is written: no clock sample, no alert, no stamp (no credit is used), no e-mail. The exit
code is 0 when every row is ok or skipped, 1 with a warning and 2 with a failure. The installer
runs it at its end and shows the rows; a warning or failure never fails the installation.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from arcilogger_core.clockcheck import ClockError, NtpResult, sntp_query
from arcilogger_core.config import (
    TURKTRUST_ROOT_FINGERPRINTS,
    Config,
    check_fixed_location,
    load_config,
)
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import (
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    REPLICA_PASSWORD,
    SMTP_PASSWORD,
    TURKTRUST_PASSWORD,
    TURKTRUST_USERNAME,
    SecretStore,
    SecretStoreError,
)
from arcilogger_core.smtp import SmtpSender, SmtpSendError, explain
from arcilogger_core.tsa import trusted_roots, url_with_port
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.certparse import fingerprint
from arcilogger_core.tsa.rfc3161 import timestamp_opener

Status = Literal["ok", "warn", "fail", "skip"]

#: The rows in the order they are printed.
ROWS: Final = ("config", "secrets", "database", "roots", "tsa.reach", "smtp", "clock")
#: Longest cause added to a row's text.
CAUSE_MAX: Final = 300
#: The longest wait of one network check (seconds).
NETWORK_TIMEOUT: Final = 15.0
#: ``arcilogger-db check`` connects, reads the revision and the partitions.
DATABASE_TIMEOUT: Final = 120

#: The fixed texts: ``(row, status)`` → (Turkish, English); ``{cause}`` and the other fields are
#: filled in when the row is made.
TEXTS: Final[Mapping[tuple[str, str], tuple[str, str]]] = {
    ("config", "ok"): ("Yapılandırma okundu.", "The configuration loads."),
    ("config", "fail"): (
        "Yapılandırma okunamadı: {cause}",
        "The configuration does not load: {cause}",
    ),
    ("any", "skip"): (
        "Denenmedi: yapılandırma okunamadı.",
        "Not tried: the configuration does not load.",
    ),
    ("secrets", "ok"): (
        "Gizli depo açıldı; yapılandırmanın kullandığı bilgiler kayıtlı.",
        "The secret store opens; the credentials the configuration uses are stored.",
    ),
    ("secrets", "warn"): (
        "Kayıtlı değil: {names}. Tarayıcıdaki kurulum sihirbazı bunları ister.",
        "Not stored: {names}. The setup wizard in the browser asks for them.",
    ),
    ("secrets", "fail"): (
        "Gizli depo açılamadı: {cause}",
        "The secret store does not open: {cause}",
    ),
    ("database", "ok"): (
        "Veritabanı bağlantısı çalışıyor, şema güncel.",
        "The database answers and its schema is current.",
    ),
    ("database", "fail"): (
        "Veritabanı: {cause}",
        "Database: {cause}",
    ),
    ("database", "skip"): (
        "Veritabanı paketi bu kurulumda yok.",
        "This installation has no database package.",
    ),
    ("roots", "ok"): (
        "Kök sertifikalar kurulu ({count}).",
        "Root certificates are installed ({count}).",
    ),
    ("roots", "fail"): (
        "Kök sertifika: {cause}",
        "Root certificate: {cause}",
    ),
    ("roots", "skip"): (
        "Deneme sağlayıcısı (mock) kendi deneme kökünü kullanır.",
        "The trial provider (mock) uses its own test root.",
    ),
    ("tsa.reach", "ok"): (
        "Zaman damgası sunucusu cevap veriyor ({target}).",
        "The time-stamp server answers ({target}).",
    ),
    ("tsa.reach", "warn"): (
        "Zaman damgası sunucusuna ulaşılamadı ({target}): {cause}",
        "The time-stamp server cannot be reached ({target}): {cause}",
    ),
    ("tsa.reach", "skip"): (
        "Deneme sağlayıcısı (mock): sunucu yok.",
        "The trial provider (mock) has no server.",
    ),
    ("smtp", "ok"): (
        "E-posta sunucusu bağlantıyı ve girişi kabul etti ({target}); e-posta gönderilmedi.",
        "The mail server accepts the connection and the sign-in ({target}); nothing was sent.",
    ),
    ("smtp", "warn"): (
        "E-posta sunucusu ({target}): {cause}",
        "Mail server ({target}): {cause}",
    ),
    ("smtp", "skip"): ("E-posta gönderimi kapalı.", "Sending e-mail is switched off."),
    ("clock", "ok"): (
        "Saat farkı {offset} ms ({server}; sınır {limit} ms).",
        "The clock is {offset} ms off ({server}; limit {limit} ms).",
    ),
    ("clock", "warn"): (
        "Saat: {cause}",
        "Clock: {cause}",
    ),
}


@dataclass(frozen=True)
class Row:
    id: str
    status: Status
    message_tr: str
    message_en: str


@dataclass(frozen=True)
class DatabaseAnswer:
    """What ``arcilogger-db check`` said: its exit code, its report and its last error line."""

    exit_code: int
    report: Mapping[str, Any] | None
    error: str


def run_database_check(config_path: Path) -> DatabaseAnswer | None:
    """``python -P -m arcilogger_db --config <toml> check`` with this interpreter; None when the
    database package is not installed. The child prints UTF-8: its Turkish warnings would not fit
    the Windows code page of a pipe."""
    try:
        completed = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
            [sys.executable, "-P", "-m", "arcilogger_db", "--config", str(config_path), "check"],
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=DATABASE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DatabaseAnswer(-1, None, f"no answer within {DATABASE_TIMEOUT} s")
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    if "No module named arcilogger_db" in stderr:
        return None
    try:
        report = json.loads(completed.stdout.decode("utf-8", "replace") or "null")
    except json.JSONDecodeError:
        report = None
    lines = [line for line in stderr.splitlines() if line.strip()]
    return DatabaseAnswer(
        completed.returncode,
        report if isinstance(report, dict) else None,
        lines[-1] if lines else "",
    )


def http_status(url: str, timeout: float, https_proxy: str) -> int:
    """The HTTP status a request without credentials gets (an error status is an answer too);
    OSError when no answer came."""
    request = urllib.request.Request(url, method="GET")  # noqa: S310 - https checked by caller
    opener = timestamp_opener(
        https_proxy, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        with opener.open(request, timeout=timeout) as reply:
            return int(reply.status)
    except urllib.error.HTTPError as exc:
        exc.close()
        return int(exc.code)
    except urllib.error.URLError as exc:
        reason = exc.reason
        raise OSError(str(reason)) from None
    except http.client.HTTPException as exc:  # an answer that is not HTTP (a middlebox)
        raise OSError(f"{type(exc).__name__}: {exc}") from None


def tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def smtp_check(config: Config, password: str | None) -> None:
    """Connect, TLS, EHLO and AUTH with the network timeout at most (``[smtp] timeout_seconds``
    may be 300 s per step, more than the installer waits for the whole self-test)."""
    timeout = min(config.smtp.timeout_seconds, int(NETWORK_TIMEOUT))
    SmtpSender(config.smtp.model_copy(update={"timeout_seconds": timeout}), password).check()


@dataclass
class Probes:
    """The parts that leave this machine; tests replace them."""

    database: Callable[[Path], DatabaseAnswer | None] = run_database_check
    http: Callable[[str, float, str], int] = http_status
    tcp: Callable[[str, int, float], None] = tcp_connect
    smtp: Callable[[Config, str | None], None] = smtp_check
    ntp: Callable[[str, int], NtpResult] = field(
        default=lambda server, port: sntp_query(server, port, timeout=5.0)
    )


def _row(row_id: str, status: Status, **values: object) -> Row:
    tr, en = TEXTS[(row_id, status)]
    return Row(row_id, status, tr.format(**values), en.format(**values))


def _cause(text: str, secrets: Iterable[str]) -> str:
    """``text`` on one line, without any stored secret, capped."""
    for value in secrets:
        if value and len(value) >= 3:
            text = text.replace(value, "***")
    return " ".join(text.split())[:CAUSE_MAX] or "?"


def _needed(config: Config) -> list[str]:
    """The stored credentials the configuration uses (the database's is optional: a local trust
    or SSPI setup has none)."""
    names: list[str] = []
    if config.tsa.provider == "turktrust":
        names += [TURKTRUST_USERNAME, TURKTRUST_PASSWORD]
    elif config.tsa.provider == "zamane" and config.tsa.legal_grade:
        names += [KAMUSM_CUSTOMER, KAMUSM_PASSWORD]
    if config.smtp.enabled and config.smtp.username:
        names.append(SMTP_PASSWORD)
    if config.replica.enabled and config.replica.user:
        names.append(REPLICA_PASSWORD)
    return names


def run(config_path: Path, probes: Probes | None = None) -> list[Row]:
    """The rows of :data:`ROWS` for the configuration at ``config_path``."""
    probes = probes or Probes()
    try:
        config = load_config(config_path)
        check_fixed_location(config, config_path)
    except (OSError, ValidationError, ValueError) as exc:
        skipped = TEXTS[("any", "skip")]
        rows = [_row("config", "fail", cause=_cause(str(exc), ()))]
        return rows + [Row(row_id, "skip", *skipped) for row_id in ROWS[1:]]
    stored: dict[str, str] = {}
    secrets_row, secret_values = _secrets(config, stored)
    return [
        _row("config", "ok"),
        secrets_row,
        _database(config_path, probes, secret_values),
        _roots(config),
        _tsa_reach(config, probes, secret_values),
        _smtp(config, probes, stored.get(SMTP_PASSWORD), secret_values),
        _clock(config, probes),
    ]


def _secrets(config: Config, stored: dict[str, str]) -> tuple[Row, list[str]]:
    """The secrets row; fills ``stored`` with the values the other rows use and returns every
    readable value (to keep them out of the causes)."""
    key = config.secrets_dir / "secret.key"
    store = SecretStore(key, config.secrets_dir / "secrets.json")
    try:
        if not key.is_file():
            raise SecretStoreError(f"secret key not found: {key}")
        names = store.names()
        values = {name: store.get(name) for name in names}
    except (SecretStoreError, OSError, ValueError) as exc:
        return _row("secrets", "fail", cause=_cause(str(exc), ())), []
    readable = {name: value for name, value in values.items() if value}
    stored.update(readable)
    missing = [name for name in _needed(config) if name not in readable]
    if missing:
        return _row("secrets", "warn", names=", ".join(missing)), list(readable.values())
    return _row("secrets", "ok"), list(readable.values())


def _database(config_path: Path, probes: Probes, secrets: list[str]) -> Row:
    answer = probes.database(config_path)
    if answer is None:
        return _row("database", "skip")
    report = answer.report or {}
    if answer.exit_code in (0, 1) and report.get("up_to_date") is True:
        return _row("database", "ok")
    if answer.exit_code in (0, 1) and report:
        cause = "şema güncel değil, arcilogger-db upgrade [schema not current]"
    else:
        cause = answer.error or f"arcilogger-db check: exit {answer.exit_code}"
    return _row("database", "fail", cause=_cause(cause, secrets))


def _roots(config: Config) -> Row:
    if config.tsa.provider == "mock":
        # its test root is made on its first stamp; asking for it here would write it
        return _row("roots", "skip")
    try:
        roots = trusted_roots(config, Layout(config.data_dir))
    except (TsaError, OSError, ValueError) as exc:
        return _row("roots", "fail", cause=_cause(str(exc), ()))
    if config.tsa.provider == "turktrust":
        found = {fingerprint(root) for root in roots}
        if not found & set(TURKTRUST_ROOT_FINGERPRINTS):
            cause = (
                f"TÜRKTRUST H7 kökü yok [TÜRKTRUST's H7 root is missing]: {config.tsa.roots_dir}"
            )
            return _row("roots", "fail", cause=cause)
    return _row("roots", "ok", count=len(roots))


def _tsa_reach(config: Config, probes: Probes, secrets: list[str]) -> Row:
    settings = config.tsa
    if settings.provider == "mock":
        return _row("tsa.reach", "skip")
    url = url_with_port(settings.url, settings.port)
    timeout = min(float(settings.timeout_seconds), NETWORK_TIMEOUT)
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        if settings.provider == "turktrust":
            status = probes.http(url, timeout, config.network.https_proxy)
            return _row("tsa.reach", "ok", target=f"{url} → HTTP {status}")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        probes.tcp(host, port, timeout)
        return _row("tsa.reach", "ok", target=f"{host}:{port}")
    except (OSError, ValueError) as exc:
        return _row("tsa.reach", "warn", target=host or url, cause=_cause(str(exc), secrets))


def _smtp(config: Config, probes: Probes, password: str | None, secrets: list[str]) -> Row:
    settings = config.smtp
    if not settings.enabled:
        return _row("smtp", "skip")
    target = f"{settings.host}:{settings.port}"
    try:
        probes.smtp(config, password)
    except SmtpSendError as exc:
        text = _cause(str(exc), secrets)
        hint = explain(text, settings.host)
        row = _row("smtp", "warn", target=target, cause=text)
        if hint:  # the explanation first, as the browser shows it (0.4.0 P6)
            return Row(
                "smtp",
                "warn",
                f"{row.message_tr} — {hint[0]}",
                f"{row.message_en} — {hint[1]}",
            )
        return row
    return _row("smtp", "ok", target=target)


def _clock(config: Config, probes: Probes) -> Row:
    limit = config.clock.alert_offset_ms
    if not config.clock.servers:
        return _row("clock", "warn", cause="saat sunucusu ayarlanmamış [no time server is set]")
    errors = []
    for server in config.clock.servers:
        try:
            result = probes.ntp(server, config.clock.ntp_port)
        except ClockError as exc:
            errors.append(str(exc))
            continue
        offset = round(result.offset_s * 1000)
        if abs(offset) > limit:
            cause = f"{server}: {offset} ms > {limit} ms"
            return _row("clock", "warn", cause=cause)
        return _row("clock", "ok", offset=offset, server=server, limit=limit)
    return _row("clock", "warn", cause=_cause("; ".join(errors), ()))


def exit_code(rows: Iterable[Row]) -> int:
    statuses = {row.status for row in rows}
    return 2 if "fail" in statuses else 1 if "warn" in statuses else 0


LABELS: Final = {"ok": "TAMAM", "warn": "UYARI", "fail": "HATA", "skip": "ATLANDI"}


def render(rows: Iterable[Row]) -> str:
    """The table the console and the installer print."""
    lines = ["ARCILOGGER denetimi [self-test]"]
    for row in rows:
        label = f"{LABELS[row.status]} [{row.status.upper()}]"
        lines.append(f"  {label:<18} {row.id:<10} {row.message_tr} [{row.message_en}]")
    return "\n".join(lines)


def as_json(rows: Iterable[Row]) -> dict[str, Any]:
    return {"rows": [asdict(row) for row in rows]}
