"""ARCILOGGER configuration file (TOML).

The evidence jobs read their settings from a file, never from the database, so they keep working
when the database is down. Default location:

* Windows: ``C:\\ProgramData\\ARCILOGGER\\arcilogger.toml``
* elsewhere: ``/etc/arcilogger/arcilogger.toml``

``ARCILOGGER_CONFIG`` or ``--config`` override it. See ``deploy/windows/arcilogger.example.toml``.
Secrets (Kamu SM customer number and password) are **not** in this file; they live in the
encrypted secret store (:mod:`arcilogger_core.secretstore`).
"""

from __future__ import annotations

import ipaddress
import os
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_RETENTION_DAYS = 730  # two years: İnternet Toplu Kullanım Sağlayıcıları Yönetmeliği md. 4/1-b
DEFAULT_RETENTION_DAYS = 760  # two years plus a margin

KAMUSM_PRODUCTION_HOST = "zd.kamusm.gov.tr"
KAMUSM_TEST_HOST = "tzd.kamusm.gov.tr"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceConfig(_Model):
    """A device that sends syslog, identified by its sender IP address.

    ``address`` may also be a network (e.g. ``172.20.10.0/24`` for the access points and other
    network devices of one VLAN): each sender in it becomes its own source ``<name>-<ip>``.
    """

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    type: Literal["fortigate", "mikrotik", "generic"]
    address: str
    heartbeat_minutes: int = Field(default=15, ge=1, le=1440)
    #: Expected to send every day; a silent expected source is a gap in the manifest.
    expected: bool = True
    #: Transports the collector accepts from this sender: ``any`` (default), ``udp`` or
    #: ``tcp``. UDP source addresses can be forged inside the LAN; once the device sends over
    #: TCP 514, ``tcp`` makes the collector drop (count + alert) UDP datagrams claiming its
    #: address (docs/DEVICE-SETUP.md).
    transport: Literal["any", "udp", "tcp"] = "any"
    #: ``false`` switches a source off without removing it (ADR-0014): the firewall no longer
    #: lets the sender reach 514, ingest marks it disabled (R15 and the dashboard skip it) and
    #: the sealer's gap check no longer expects it. A message that still arrives is recorded
    #: under the source's name as before (raw logs are never dropped), and the source still
    #: counts for the licence's log-source limit (ADR-0013).
    enabled: bool = True

    @field_validator("address")
    @classmethod
    def _valid_ip(cls, value: str) -> str:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        return str(ipaddress.ip_address(value))

    @property
    def is_network(self) -> bool:
        return "/" in self.address


class CollectorConfig(_Model):
    bind: str = "0.0.0.0"  # noqa: S104 - the server firewall limits senders
    #: ``None`` disables the listener; 0 picks a free port (tests).
    udp_port: int | None = Field(default=514, ge=0, le=65535)
    tcp_port: int | None = Field(default=514, ge=0, le=65535)
    unknown_senders: Literal["write", "drop"] = "write"
    max_message_bytes: int = Field(default=65536, ge=1024, le=1048576)
    fsync_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    udp_receive_buffer_bytes: int = Field(default=8 * 1024 * 1024, ge=65536)
    status_interval_seconds: float = Field(default=10.0, gt=0)


class SealConfig(_Model):
    gzip_level: int = Field(default=9, ge=1, le=9)
    #: A closed day is sealed only when its spool files were not written for this long.
    quiet_seconds: int = Field(default=30, ge=0)
    #: No message from an expected source for longer than this is recorded as a gap.
    gap_minutes: int = Field(default=60, ge=1)
    #: Waits between failed attempts; the last value repeats.
    retry_minutes: tuple[int, ...] = (5, 15, 60, 180, 360)
    #: A day stamped later than this after its end is marked ``stamped_late``.
    late_after_hours: int = Field(default=48, ge=1)
    #: First day of the chain before anything is sealed (default: the oldest spool day). Spool
    #: files of earlier days are not sealed; they stay in the spool and raise one ``info``
    #: alert each (test messages before go-live). Once days are sealed the chain start comes
    #: from the sealing records and never moves back; this value can then only move it forward.
    first_day: date | None = None
    #: At most this many consecutive days without any log (no raw file, no audit entry) are
    #: stamped one by one. A longer run (collector stopped, server off, clock jumped forward)
    #: is not stamped day by day: a critical alert is raised and the run is listed in the
    #: ``gaps`` of the next sealed day's manifest, which links to the last sealed day before it.
    #: Such a skip is provisional until that day is stamped: a skipped day that gets data, has
    #: not ended by the corrected clock, or no longer belongs to a long enough run is sealed
    #: normally after all.
    max_consecutive_empty_days: int = Field(default=7, ge=0, le=366)


class TsaConfig(_Model):
    provider: Literal["mock", "rfc3161", "zamane"] = "mock"
    #: Time-stamp server URL (Zamane: address passed to the console client).
    url: str = f"http://{KAMUSM_TEST_HOST}"
    port: int = Field(default=80, ge=1, le=65535)
    digest: Literal["sha256", "sha512"] = "sha256"
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    #: Java 8 command for the Zamane console client, e.g. ["C:/Program Files/.../java.exe"].
    java: tuple[str, ...] = ("java",)
    zamane_jar: Path | None = None
    #: "-CC" also checks the server certificate signature (Kamu SM documentation).
    zamane_verify_flag: Literal["-c", "-CC"] = "-CC"
    #: How the credit is read from the client output — verify on the test server.
    credit_regex: str = r"(?i)(?:kontör|kontor|credit)\D{0,40}?(\d+)"
    credit_alert_threshold: int = Field(default=500, ge=0)
    #: Directory with the pinned root certificates (PEM/DER) the verifier trusts.
    roots_dir: Path | None = None
    #: SHA-256 fingerprints of the Kamu SM root certificates whose stamps count as "legal grade"
    #: (compare them with the values Kamu SM publishes before entering them). A day is shown as
    #: legal grade only when its time stamp verifies **and** chains to one of these roots **and**
    #: the day was stamped by the Kamu SM production server. Empty (the default): no day is
    #: shown as legal grade.
    legal_root_fingerprints: tuple[str, ...] = ()
    #: RFC 3161 2.3: the TSA certificate's extended key usage must be critical. K6 in
    #: docs/kamusm.md: set false only after the Kamu SM certificate profile is confirmed to
    #: mark it non-critical; the choice is sealed into every manifest.
    require_critical_eku: bool = True

    @field_validator("legal_root_fingerprints")
    @classmethod
    def _fingerprints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = []
        for value in values:
            text = value.replace(":", "").replace(" ", "").lower()
            if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
                raise ValueError(f"not a SHA-256 fingerprint: {value!r}")
            normalized.append(text)
        return tuple(normalized)

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    @property
    def legal_grade(self) -> bool:
        """Whether the configuration points at the Kamu SM production server (Zamane client,
        zd.kamusm.gov.tr). This is only what the site **claims**: a day counts as legal grade
        when its verified token also chains to a root of ``legal_root_fingerprints``
        (:func:`arcilogger_core.tsa.verify.confirmed_legal_grade`)."""
        return self.provider == "zamane" and self.host == KAMUSM_PRODUCTION_HOST


class RetentionConfig(_Model):
    enabled: bool = False
    days: int = Field(default=DEFAULT_RETENTION_DAYS, ge=MIN_RETENTION_DAYS)


class ReplicaConfig(_Model):
    enabled: bool = False
    #: Mounted share or UNC path, e.g. "\\\\NAS-01\\arcilogger-worm".
    target: Path | None = None
    #: User for an SMB (UNC) target, e.g. "NAS-01\\arcireplica"; its password is the stored
    #: secret ``replica.password``. The Replica task runs as LOCAL SERVICE, which reaches the
    #: network anonymously, so a share for "the replica user only" needs this.
    user: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _target_when_enabled(self) -> ReplicaConfig:
        if self.enabled and self.target is None:
            raise ValueError("replica.target is required when replica.enabled = true")
        return self


class ClockConfig(_Model):
    #: The local time source (usually the core router); the installer asks for it
    #: (``-NtpServer``). Empty: none is set, the clock check and ``Set-ArciTime.ps1`` use only
    #: ``fallback_server`` and the pre-flight check warns (:func:`setup_problems`).
    ntp_server: str = Field(default="", max_length=253)
    fallback_server: str = Field(default="time.ume.tubitak.gov.tr", max_length=253)
    ntp_port: int = Field(default=123, ge=1, le=65535)
    alert_offset_ms: int = Field(default=1000, ge=1)

    @field_validator("ntp_server", "fallback_server")
    @classmethod
    def _server(cls, value: str) -> str:
        text = value.strip()
        if any(c.isspace() for c in text):
            raise ValueError(f"not a host name or address: {value!r}")
        return text

    @property
    def servers(self) -> tuple[str, ...]:
        """The time servers to ask, in this order: the local source when set, then the
        fallback (when set)."""
        return tuple(server for server in (self.ntp_server, self.fallback_server) if server)


class DatabaseConfig(_Model):
    """PostgreSQL connection (used by ingest, worker and API — never by the evidence jobs).

    The password is **not** here: it lives in the encrypted secret store under
    ``database.password`` (``arcilogger-core secret set database.password``). Without it the
    connection is attempted without a password (e.g. a local trust or SSPI setup).
    """

    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = Field(default="arcilogger", pattern=r"^[A-Za-z0-9_-]{1,63}$")
    user: str = Field(default="arcilogger", pattern=r"^[A-Za-z0-9_.-]{1,63}$")
    #: libpq ``sslmode``; ``None`` keeps the libpq default (the server is on 127.0.0.1).
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] | None = (
        None
    )
    connect_timeout_seconds: int = Field(default=10, ge=1, le=300)
    pool_size: int = Field(default=5, ge=1, le=50)
    #: TimescaleDB during migration: ``auto`` uses it when installed, ``require`` fails without
    #: it, ``off`` keeps plain tables even when it is installed.
    timescale: Literal["auto", "require", "off"] = "auto"


class ApiConfig(_Model):
    """HTTP API (Windows service ``ArciApi``); Caddy terminates HTTPS in front of it."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    #: ``Secure`` flag on cookies; only switch off for local development over plain HTTP.
    secure_cookies: bool = True
    #: Proxies whose ``X-Forwarded-For`` is trusted (Caddy on the same machine).
    forwarded_allow_ips: str = "127.0.0.1"


class IngestConfig(_Model):
    """Ingest service (spec 7.2: batch COPY every 500 ms or 5,000 rows)."""

    batch_rows: int = Field(default=5000, ge=1, le=1_000_000)
    batch_seconds: float = Field(default=0.5, gt=0, le=60)
    #: How often the spool files are checked for new lines.
    poll_seconds: float = Field(default=0.5, gt=0, le=60)
    #: Parse worker processes (0 = parse in the service process). Measured on the 4-vCPU
    #: development VM with the real parsers (docs/INGEST.md, repeated runs): about 4,700-5,300
    #: lines/s with 0 and 7,400-10,500 with 2 (bench mix; 7,200-7,900 with the load-generator
    #: mix). 2 is about 1.7x faster than 0; verify on the server.
    workers: int = Field(default=2, ge=0, le=16)


class WorkerConfig(_Model):
    """Worker service (lease timeline, NAT index, … ; spec 9.1: incremental every minute)."""

    interval_seconds: int = Field(default=60, ge=5, le=3600)


# -- Scheduled database backup (area B, ADR-0009) -------------------------------------------------


class BackupConfig(_Model):
    """Scheduled database backup: the ``\\ARCILOGGER\\Backup`` task runs ``arcilogger-db backup
    run`` daily (ADR-0009). Never needed by the evidence jobs."""

    enabled: bool = True
    #: Folder of the scheduled dumps; a relative path is below ``data_dir``. The installer sets
    #: the rights (Backup task writes, the services cannot read) on the default folder only.
    dir: Path = Path("backups/scheduled")
    #: Retention: the newest dump of each of the last N days, ISO weeks and months that have one.
    keep_daily: int = Field(default=14, ge=1, le=3660)
    keep_weekly: int = Field(default=8, ge=0, le=520)
    keep_monthly: int = Field(default=12, ge=0, le=1200)
    #: Full paths of the PostgreSQL tools; empty: the bin folder of the PostgreSQL service, then
    #: the newest ``C:\\Program Files\\PostgreSQL\\<version>\\bin``, then ``PATH``.
    pg_dump: str = ""
    pg_restore: str = ""
    #: A run is refused (alert ``R20-backup``) while the backup folder's disk has less free space
    #: than this plus the size of the newest dump: a dump must never fill the evidence disk.
    min_free_gb: int = Field(default=10, ge=0, le=100_000)


# --- Area E: alert e-mail (docs/ALERTS.md, ADR-0010) ---------------------------------------------

#: An e-mail address as the alert e-mail accepts it (``local@domain.tld``, no display name).
EMAIL_ADDRESS = r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
_HOST = r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"


class SmtpConfig(_Model):
    """The SMTP server the worker sends alert e-mails through (``arcilogger-worker`` job
    ``alerts.notify``). Who receives what is a database setting (``alerts.email_*``).

    The password is **not** here: it lives in the encrypted secret store under
    ``smtp.password`` (``arcilogger-core secret set smtp.password``). The server certificate is
    always verified for ``starttls`` and ``ssl`` (the system trust store, or ``ca_file``).
    ``none`` sends in plain text: only for a relay on this server or a trusted segment.
    """

    enabled: bool = False
    host: str | None = Field(default=None, pattern=_HOST)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str | None = Field(default=None, min_length=1, max_length=256)
    from_address: str | None = Field(default=None, pattern=EMAIL_ADDRESS, max_length=254)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    #: PEM file with the certificate(s) to trust instead of the system store (a company CA).
    ca_file: Path | None = None

    @model_validator(mode="after")
    def _complete_when_enabled(self) -> SmtpConfig:
        if self.enabled and (self.host is None or self.from_address is None):
            raise ValueError("smtp.host and smtp.from_address are required when smtp.enabled")
        return self


class Config(_Model):
    #: The organisation that runs ARCILOGGER, shown in reports, e-mails, evidence packages and
    #: the web. Required for production (the installer asks for it, ``-SiteName``) but empty by
    #: default: a consumer that finds it empty leaves the name out (:attr:`site_name_set`).
    site_name: str = Field(default="", max_length=200)
    data_dir: Path
    #: Holds ``secret.key`` and ``secrets.json``; defaults to the config file's directory.
    secrets_dir: Path
    #: Empty on a new installation (the example's are commented out): every sender is then
    #: unknown (``unknown-<ip>``, or dropped: ``[collector] unknown_senders``) and
    #: :func:`setup_problems` warns.
    sources: tuple[SourceConfig, ...] = ()
    collector: CollectorConfig = CollectorConfig()
    seal: SealConfig = SealConfig()
    tsa: TsaConfig = TsaConfig()
    retention: RetentionConfig = RetentionConfig()
    replica: ReplicaConfig = ReplicaConfig()
    clock: ClockConfig = ClockConfig()
    database: DatabaseConfig = DatabaseConfig()
    api: ApiConfig = ApiConfig()
    ingest: IngestConfig = IngestConfig()
    worker: WorkerConfig = WorkerConfig()
    backup: BackupConfig = BackupConfig()
    smtp: SmtpConfig = SmtpConfig()

    @field_validator("site_name")
    @classmethod
    def _site_name(cls, value: str) -> str:
        # One line of text: it goes into sentences, PDF headers and e-mail subjects.
        text = value.strip()
        if any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in text):
            raise ValueError("site_name must be a single line without control characters")
        return text

    @property
    def site_name_set(self) -> bool:
        """Whether the organisation name is configured (:func:`setup_problems` reports it)."""
        return bool(self.site_name)

    @property
    def admin_dir(self) -> Path:
        """The browser administration's folder (ADR-0014): ``admin`` next to the secret store,
        ``C:\\ProgramData\\ARCILOGGER\\admin`` on the server. Not a key of its own (an older
        version would refuse the key after a rollback), but it follows ``secrets_dir``, which
        the administration agent can write. A reader that must not be pointed at other guard
        rails (``policy.json``, the Zamane records of :mod:`arcilogger_core.execpolicy`) takes
        them from the folder of its fixed ``--config`` path instead
        (``execpolicy.TrustAnchors``)."""
        return self.secrets_dir / "admin"

    @model_validator(mode="after")
    def _unique_sources(self) -> Config:
        names = [source.name for source in self.sources]
        addresses = [source.address for source in self.sources]
        if len(set(names)) != len(names):
            raise ValueError("source names must be unique")
        if len(set(addresses)) != len(addresses):
            raise ValueError("source addresses must be unique")
        if any(name.startswith("unknown-") for name in names):
            raise ValueError("source names starting with 'unknown-' are reserved")
        return self

    def source_for(self, address: str) -> SourceConfig | None:
        """The source configured for exactly this sender address."""
        return next((s for s in self.sources if s.address == address), None)

    def source_config_for(self, address: str) -> SourceConfig | None:
        """The source a sender belongs to: exact address first, then the first matching
        network."""
        exact = self.source_for(address)
        if exact is not None:
            return exact
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        for source in self.sources:
            if source.is_network and ip in ipaddress.ip_network(source.address):
                return source
        return None

    def source_name_for(self, address: str) -> str | None:
        """Spool name for a sender: exact address first, then the first matching network."""
        source = self.source_config_for(address)
        if source is None:
            return None
        if not source.is_network:
            return source.name
        # IPv6 ':' and zone '%' are not allowed in Windows folder names.
        ip = str(ipaddress.ip_address(address))
        return f"{source.name}-" + ip.replace(":", "-").replace("%", "-")


@dataclass(frozen=True)
class SetupProblem:
    """A setup value of a production configuration that is missing (``error``: the installation
    is not complete) or advisable (``warning``). ``message`` is "Türkçe [English]"."""

    level: Literal["error", "warning"]
    key: str
    message: str


def setup_problems(config: Config) -> list[SetupProblem]:
    """What a production configuration still lacks (``arcilogger-core config-check``, the
    installer's pre-flight check). Development and test configurations may lack these; every
    consumer of the configuration copes with the empty values."""
    problems = []
    if not config.site_name_set:
        problems.append(
            SetupProblem(
                "error",
                "site_name",
                "Kuruluş adı (site_name) ayarlanmamış: raporlar, e-postalar ve kanıt paketleri "
                "kuruluş adı olmadan oluşturulur; kurulumda -SiteName ile girin [the "
                "organisation name (site_name) is not set: reports, e-mails and evidence "
                "packages are made without it; enter it with -SiteName]",
            )
        )
    if not config.clock.servers:
        problems.append(
            SetupProblem(
                "error",
                "clock.fallback_server",
                "Saat sunucusu ayarlanmamış: clock.ntp_server ve clock.fallback_server boş "
                "[no time server is set: clock.ntp_server and clock.fallback_server are empty]",
            )
        )
    elif not config.clock.ntp_server:
        problems.append(
            SetupProblem(
                "warning",
                "clock.ntp_server",
                "Yerel saat kaynağı (clock.ntp_server) ayarlanmamış: saat yalnızca yedek "
                f"sunucuyla ({config.clock.fallback_server}) eşitlenir; kurulumda -NtpServer ile "
                "girin [no local time source (clock.ntp_server) is set: the clock follows only "
                f"the fallback server ({config.clock.fallback_server}); enter it with -NtpServer]",
            )
        )
    if not config.sources:
        # A valid state (a new installation starts with none), but no device is recorded as a
        # source: the firewall opens 514 to no sender and R15 watches nothing.
        problems.append(
            SetupProblem(
                "warning",
                "sources",
                "Log kaynağı ([[sources]]) tanımlanmamış: güvenlik duvarınızın ve ana "
                "yönlendiricinizin [[sources]] kayıtlarını arcilogger.toml dosyasına ekleyin "
                "(docs/DEVICE-SETUP.md bölüm 4); o zamana kadar kurulumun güvenlik duvarı 514'ü "
                "hiçbir göndericiye açmaz ve sessiz kaynak uyarısı (R15) hiçbir kaynağı izlemez "
                "[no log source ([[sources]]) is configured: add the [[sources]] of your firewall "
                "and core router to arcilogger.toml (docs/DEVICE-SETUP.md section 4); until then "
                "the installer's firewall opens 514 to no sender and the silent-source alert "
                "(R15) watches no source]",
            )
        )
    return problems


def default_config_path() -> Path:
    override = os.environ.get("ARCILOGGER_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ARCILOGGER"
    else:
        path = Path("/etc/arcilogger")
    return path / "arcilogger.toml"


def parse_config(data: bytes, path: Path) -> Config:
    """Validate the bytes of the configuration file at ``path`` (``secrets_dir`` defaults to
    its folder). :func:`load_config` and the live reload (:mod:`arcilogger_core.configwatch`)
    share it, so a reloaded file means exactly what it means at start-up."""
    raw = tomllib.loads(data.decode("utf-8"))
    raw.setdefault("secrets_dir", str(path.parent))
    return Config.model_validate(raw)


def load_config(path: Path | None = None) -> Config:
    """Read and validate the configuration file."""
    config_path = path or default_config_path()
    return parse_config(config_path.read_bytes(), config_path)
