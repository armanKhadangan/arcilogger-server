"""Application settings stored in the ``settings`` table (JSON values).

:data:`DEFAULT_SETTINGS` is the single list of known keys with their shipped defaults; the seed
step writes them (``updated_by = ':seed'``) and never overwrites a value an administrator changed.
Readers fall back to the default when a row is missing, so code never depends on seeding order.

Values must be **canonical JSON** (RFC 8785 as implemented by ``arcilogger_core.canonical``):
``str``, ``int``, ``bool``, ``None``, lists and objects — **no floats**. Every settings change is
audited with ``{"old": …, "new": …}`` in the hash-chained audit log (spec 8.5), which only accepts
canonical JSON; :func:`set_setting` therefore refuses anything else (use integers such as
percentages instead of fractions).

``updated_by`` is the administrator's user name, or one of the internal authors ``:seed``,
``:system`` and ``:migration``. A ``:`` can never appear in a user name (``users.username`` check),
so a user can never be mistaken for the seed.

An administrator's value goes through :func:`validate_setting`, from the console and from the
web interface alike (``GET/PATCH /settings``, ADR-0014). A refusal is a
:class:`SettingValueError`: the console prints its Turkish text, the web interface shows its
``code`` with ``message_tr`` and ``message_en``. :data:`REAUTH_KEYS` need a recent
re-authentication in the web interface, :data:`LOCKED_KEYS` are changed on the console only;
labels and help texts are in :mod:`arcilogger_db.settings_meta`.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_core.canonical import CanonicalJsonError, canonical_json
from arcilogger_core.config import EMAIL_ADDRESS
from arcilogger_core.iputil import canonical_ip
from arcilogger_db.models import ALERT_SEVERITIES, ROLES, Setting

#: Internal authors of ``settings.updated_by`` (never a valid user name).
SEED_AUTHOR: Final = ":seed"
SYSTEM_AUTHOR: Final = ":system"
MIGRATION_AUTHOR: Final = ":migration"

#: Every known setting with its shipped default (build specification sections in comments).
DEFAULT_SETTINGS: Final[Mapping[str, Any]] = {
    # 2.3 NAT. The FortiGate's public/WAN address(es). Empty = the ISP router does a second NAT
    # ("double NAT"): 5651 lookups by public IP + port can only return ranked candidates.
    "nat.public_ips": [],
    # The FortiGate's own WAN address as seen in ``transip`` (e.g. a private address behind the
    # ISP router). Unknown until configured on site; never assumed.
    "nat.wan_transip": "",
    # 9.5 lookup tolerance (± seconds) and the maximum number of candidates returned.
    "lookup.tolerance_seconds": 60,
    "lookup.max_candidates": 100,
    # 9.3 longest FortiGate session a lookup considers: bounds the time-range scan for sessions
    # that were running at T but logged later (verify against the FortiGate session TTLs).
    "lookup.max_session_seconds": 86400,
    # 14 "5651 lookup < 5 s for a ±60 s window": the API stops a lookup after this many seconds
    # (PostgreSQL statement timeout of the remaining time) and answers 504 lookup_timeout.
    "lookup.timeout_seconds": 5,
    # 9.5 ranking hint: favour candidates whose FortiGate port equals the requested public port.
    # Assumes the ISP router keeps source ports — verify on site before switching it on.
    "lookup.port_hint": False,
    # 9.5 a DNS answer this old (seconds) still links a requested domain to an address.
    "lookup.dns_lookback_seconds": 3600,
    # 9.5 time-stamp the SHA-256 of every official PDF report with the configured [tsa]
    # provider (1 kontör per report with Kamu SM).
    "lookup.report_timestamp": False,
    # 9.1 lease timeline: lease time per DHCP server (default 1 day; a server with shorter
    # leases, e.g. a guest network's, is entered on site: {"<server name>": seconds}) and the
    # factor (x 1.1, as an integer percentage) applied after the last evidence before an open
    # interval times out.
    "lease.default_seconds": 86400,
    "lease.server_seconds": {},
    "lease.timeout_percent": 110,
    # 2.2 "the DHCP server name contains the VLAN": dhcp60 -> 60 (first group = VLAN id).
    "dhcp.server_vlan_regex": r"^dhcp(\d+)$",
    # 9.1 ARP-LOG interval of the MikroTik router's scheduler script.
    "arp.interval_seconds": 3600,
    # 9.1 ARP sightings this many seconds after a DHCP release of the same IP+MAC are stale ARP
    # cache entries, not evidence of use (0 switches the rule off; at most arp.interval_seconds).
    "arp.ignore_after_release_seconds": 300,
    # 9.1 infrastructure MAC addresses (routers etc.) excluded from IP-conflict detection.
    "devices.infrastructure_macs": [],
    # 9.2 path of an IEEE OUI listing the administrator placed on the server ("" = no vendor
    # names; ARCILOGGER does not bundle or download the registry, docs/CORRELATION.md).
    "devices.oui_file": "",
    # 9.1 the lease timeline leaves evidence younger than this (seconds) for the next run.
    "worker.leases_lag_seconds": 120,
    # 13 sessions, lockout and passwords.
    "auth.idle_minutes": 15,
    "auth.absolute_hours": 12,
    "auth.lockout_attempts": 5,
    "auth.lockout_minutes": 15,
    "auth.password_min_length": 12,
    "auth.totp_required_roles": ["admin", "auditor"],
    # 3.3 KVKK: version of the privacy notice users must accept.
    "privacy.notice_version": "1",
    # 8.1 evidence packages are kept 30 days.
    "evidence.keep_days": 30,
    # 13 "rate limits on exports", 14 disk sizing: limits on evidence package requests
    # (arcilogger_api.evidence_limits; docs/KANIT-PAKETI.md). Packages share the data disk with
    # the spool and the vault, so a request is refused before it is queued when it would pass a
    # limit. Starting values, not measured facts: adjust them on site to the disk size.
    "evidence.max_unfinished_per_user": 2,
    "evidence.max_unfinished_total": 5,
    "evidence.max_requests_per_hour": 10,
    # Free space that must remain after the package and every waiting one: this percentage of
    # the data disk, at least evidence.min_free_gb GiB (integers: settings are canonical JSON).
    "evidence.min_free_percent": 10,
    "evidence.min_free_gb": 20,
    # Largest total size of exports\ (GiB).
    "evidence.exports_max_gb": 100,
    # 7.3 the database keeps less than the vault (default 400 days).
    "db.retention_days": 400,
    # --- Area E: alerts (spec 11 R15/R20) and their e-mail (docs/ALERTS.md, ADR-0010) ---------
    # Who gets alert e-mails and which; the SMTP connection itself is [smtp] in arcilogger.toml
    # (password: secret smtp.password). Starting values, not measured facts (ASSUMPTIONS E1-E6).
    "alerts.email_recipients": [],
    "alerts.email_min_severity": "high",
    "alerts.email_max_per_hour": 20,
    # Alerts raised within this many seconds go into one e-mail (0: each run sends at once).
    "alerts.email_digest_seconds": 300,
    "alerts.email_resolved": True,
    # Link in the e-mails; "" = https://<[collector] bind> when that is the server's own
    # address, else the worker's fallback (arcilogger_worker.alerts.notify.web_base_url).
    "alerts.web_base_url": "",
    # R15 log source silent: no alert this long after the worker or a source row started, and
    # a configured source that never sent alerts this long after its row was created.
    "alerts.r15_startup_grace_minutes": 15,
    "alerts.r15_never_seen_minutes": 60,
    # R20 system checks (every 5 minutes).
    "alerts.disk_used_percent": 80,
    "alerts.seal_overdue_hour": 9,
    "alerts.replica_stale_hours": 48,
    "alerts.parse_failure_percent": 1,
    "alerts.parse_min_messages": 1000,
    "alerts.job_failures": 3,
    "alerts.backup_stale_hours": 36,
    "alerts.backup_missing_hours": 48,
    # --- Area U: usage analytics (spec 10, docs/KULLANIM.md, ADR-0011) ------------------------
    # Starting values of spec 10.2-10.4, not measured facts (ASSUMPTIONS U1-U12). Every number
    # the rollups produce is an estimate; a changed value applies to the days rebuilt after it
    # (the last usage.auto_rebuild_days days automatically, older ones with a manual rebuild).
    # A minute counts as active with at least this much non-background traffic (bytes).
    "usage.active_bytes_threshold": 2048,
    # Sessions with less traffic in total (sent + received bytes) are background traffic.
    "usage.background_session_bytes": 2048,
    # A DNS answer this old (seconds) still names a session's destination (spec 10.2 step 3).
    "usage.dns_window_seconds": 600,
    # Logs this long (seconds) after the end of a day still count for it (sessions that run
    # over midnight), and filter logs this long before its start still name its sessions.
    "usage.session_overlap_seconds": 1800,
    # Background traffic (spec 10.3): host name patterns (a "*.x" pattern also matches "x"),
    # FortiGate application categories (only for sessions without a host name) and ports.
    # Shipped: operating system services, certificate checks and the update hosts of the two
    # supported log sources (FortiGate, MikroTik); the administrator adds the update and cloud
    # hosts of the organisation's own products (IP phone system, antivirus, …).
    "usage.background_domains": [
        "*.windowsupdate.com",
        "*.update.microsoft.com",
        "*.events.data.microsoft.com",
        "*.push.apple.com",
        "*.icloud.com",
        "*.googleapis.com",
        "ocsp*.*",
        "crl*.*",
        "*.fortiguard.net",
        "*.fortinet.net",
        "upgrade.mikrotik.com",
        "download.mikrotik.com",
    ],
    "usage.background_appcats": ["Update", "Network.Service"],
    "usage.background_ports": [53, 123, 853],
    # Work hours (ISO weekdays 1 = Monday, local time "HH:MM", end exclusive); everything else
    # and the holidays is "mesai dışı".
    "usage.work_hours": {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"},
    # Holidays: "MM-DD" every year (only the fixed-date national holidays of Türkiye are
    # shipped) or "YYYY-MM-DD" once — the religious holidays move every year: the administrator
    # enters their dates.
    "usage.holidays": ["01-01", "04-23", "05-01", "05-19", "07-15", "08-30", "10-29"],
    # A newer public_suffix_list.dat placed on the server ("" = the list bundled with the
    # publicsuffixlist package); never downloaded. Private suffixes (github.io, …) only when on.
    "usage.psl_file": "",
    "usage.psl_private_suffixes": False,
    # Days (before today) the worker rebuilds by itself when what they were computed from
    # changed (device assignments, leases, late logs, these settings).
    "usage.auto_rebuild_days": 7,
    # Rollups older than this are deleted (the logs themselves stay db.retention_days).
    "usage.retention_days": 400,
    # Kept per subject and day: newest URLs and blocked attempts.
    "usage.urls_per_day": 1000,
    "usage.blocked_per_day": 5000,
    # Owner decision 2026-10-20: names for every role that may see usage. Only when this is on,
    # viewers with usage.anonymous but without usage.person see pseudonyms.
    "usage.pseudonymize_anonymous_role": False,
    # Report "notable changes": at least this change against the 7-day average, and at least
    # this much time (seconds) or volume (bytes) on one side.
    "usage.change_min_percent": 50,
    "usage.change_min_seconds": 3600,
    "usage.change_min_bytes": 1073741824,
    # --- Area R: scheduled usage reports (docs/KULLANIM-RAPORLARI.md, ADR-0012) ----------------
    # The schedules themselves are rows of report_schedules (Raporlar page, /api/v1/reports).
    # Report files in exports/usage-reports/ are deleted after this many days (the run rows and
    # their SHA-256 stay).
    "reports.keep_days": 400,
    # Attachments larger than this in total (MiB) are left out of the e-mail, which then links
    # to the download on the Raporlar page instead (many mail servers refuse ~20-25 MB).
    "reports.max_attachment_mb": 15,
    # After the worker was stopped: run a missed schedule once, for its newest occurrence
    # (false: skip occurrences more than 30 minutes late).
    "reports.catch_up": True,
}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    """Value of ``key``; the shipped default (or ``default``) when the row is missing."""
    value = session.scalar(select(Setting.value).where(Setting.key == key))
    if value is not None:
        return value
    if key in DEFAULT_SETTINGS:
        return copy.deepcopy(DEFAULT_SETTINGS[key])
    return default


def get_settings(
    session: Session, *, prefix: str | None = None, keys: Iterable[str] | None = None
) -> dict[str, Any]:
    """Several settings at once (defaults merged in), filtered by ``prefix`` or ``keys``."""
    wanted = set(keys) if keys is not None else None

    def selected(key: str) -> bool:
        if wanted is not None and key not in wanted:
            return False
        return prefix is None or key.startswith(prefix)

    result = {k: copy.deepcopy(v) for k, v in DEFAULT_SETTINGS.items() if selected(k)}
    query = select(Setting.key, Setting.value)
    if prefix is not None:
        query = query.where(Setting.key.startswith(prefix, autoescape=True))
    if wanted is not None:
        query = query.where(Setting.key.in_(sorted(wanted)))
    for key, value in session.execute(query):
        result[key] = value
    return result


#: Why a value is refused (``SettingValueError.code``) -> (Turkish, English) for the web
#: interface, Turkish first; ``{…}`` are filled from the setting's rules (bounds, limits, names),
#: never from the value given, so a text never repeats what was submitted.
SETTING_ERRORS: Final[Mapping[str, tuple[str, str]]] = {
    "setting_unknown": ("Bilinmeyen ayar.", "Unknown setting."),
    "setting_type": (
        "Değer türü yanlış: {expected_tr} bekleniyor.",
        "Wrong type of value: {expected_en} expected.",
    ),
    "setting_not_canonical": (
        "Değer denetim kaydına tam olarak yazılamaz (ondalıklı sayı kullanmayın).",
        "The value cannot be recorded exactly in the audit log (do not use decimal numbers).",
    ),
    "setting_below_minimum": ("En az {low} olmalı.", "Must be at least {low}."),
    "setting_out_of_range": (
        "{low} ile {high} arasında olmalı.",
        "Must be between {low} and {high}.",
    ),
    "setting_ip_invalid": ("Geçersiz IP adresi.", "Invalid IP address."),
    "setting_mac_invalid": ("Geçersiz MAC adresi.", "Invalid MAC address."),
    "setting_role_unknown": (
        "Bilinmeyen rol; geçerli roller: {roles}.",
        "Unknown role; the valid roles are {roles}.",
    ),
    "setting_totp_required": (
        "{roles} rolleri için iki adımlı doğrulama (TOTP) zorunludur (şartname 13): listeye rol "
        "eklenebilir, bu roller çıkarılamaz.",
        "Two-step verification (TOTP) is mandatory for the {roles} roles (specification 13): "
        "roles may be added, these cannot be removed.",
    ),
    "setting_regex_invalid": ("Geçersiz düzenli ifade.", "Invalid regular expression."),
    "setting_regex_group": (
        "İlk grup VLAN numarasını yakalamalı, ör. ^dhcp(\\d+)$.",
        "The first group must capture the VLAN number, e.g. ^dhcp(\\d+)$.",
    ),
    "setting_lease_seconds": (
        "Her DHCP sunucusu için en az 60 saniye gerekir.",
        "Each DHCP server needs at least 60 seconds.",
    ),
    "setting_empty": ("Boş olamaz.", "Cannot be empty."),
    "setting_email_invalid": ("Geçersiz e-posta adresi.", "Invalid e-mail address."),
    "setting_too_many": ("En çok {limit} öğe girilebilir.", "At most {limit} items are allowed."),
    "setting_severity_invalid": (
        "Önem derecesi {severities} değerlerinden biri olmalı.",
        "The severity must be one of {severities}.",
    ),
    "setting_url_invalid": (
        "https:// ile başlayan bir sunucu adresi girin, ör. https://172.20.10.5 (boş: "
        "yapılandırmadan).",
        "Enter a server address starting with https://, e.g. https://172.20.10.5 (empty: from "
        "the configuration).",
    ),
    "setting_domain_pattern_invalid": (
        "Geçersiz alan adı kalıbı (ör. *.example.com).",
        "Invalid host name pattern (e.g. *.example.com).",
    ),
    "setting_appcat_invalid": (
        "Geçersiz uygulama kategorisi (boş ya da 100 karakterden uzun).",
        "Invalid application category (empty or longer than 100 characters).",
    ),
    "setting_ports_invalid": (
        "1-65535 arası port numaraları bekleniyor.",
        "Port numbers from 1 to 65535 expected.",
    ),
    "setting_work_hours_fields": (
        "days, start ve end alanları bekleniyor.",
        "The fields days, start and end are expected.",
    ),
    "setting_work_hours_days": (
        "Günler 1 (Pazartesi) - 7 (Pazar) arası gün numaraları olmalı.",
        "The days must be day numbers from 1 (Monday) to 7 (Sunday).",
    ),
    "setting_work_hours_time": (
        "Başlangıç ve bitiş SS:DD biçiminde olmalı (ör. 08:00).",
        "Start and end must be given as HH:MM (e.g. 08:00).",
    ),
    "setting_work_hours_order": (
        "Mesai başlangıcı bitişten önce olmalı (gece vardiyası yok).",
        "Work hours must start before they end (no night shift).",
    ),
    "setting_holiday_invalid": (
        "Geçersiz tarih (AA-GG ya da YYYY-AA-GG).",
        "Invalid date (MM-DD or YYYY-MM-DD).",
    ),
}


class SettingValueError(ValueError):
    """A value :func:`validate_setting` refuses for ``key``.

    ``str(exc)`` is the console's Turkish text (``arcilogger-db settings set``): it names the key
    and may repeat the value given. ``code``, ``message_tr`` and ``message_en`` are what the web
    interface shows next to the field (``PATCH /settings``: 422 ``setting_invalid`` with
    ``details[{key, code, message_tr, message_en}]``, contract 2.13); they never repeat a
    submitted value (:data:`SETTING_ERRORS`).
    """

    def __init__(self, key: str, code: str, text: str, **facts: Any) -> None:
        super().__init__(text)
        message_tr, message_en = SETTING_ERRORS[code]
        self.key = key
        self.code = code
        self.message_tr = message_tr.format(**facts)
        self.message_en = message_en.format(**facts)


def check_setting_value(key: str, value: Any) -> None:
    """:class:`SettingValueError` unless ``value`` can be stored **and audited** (canonical
    JSON)."""
    try:
        canonical_json(value)
    except CanonicalJsonError as exc:
        raise SettingValueError(
            key, "setting_not_canonical", f"setting {key}: value cannot be audited exactly: {exc}"
        ) from exc


# -- changes from the web interface (ADR-0014, contract 2.13) -------------------------------------

#: Keys whose change needs a recent re-authentication in the web interface (``PATCH /settings``,
#: ``POST /settings/{key}/reset``): sessions, passwords and privacy, how long personal data and
#: evidence copies are kept, and who gets which alert e-mails (a stolen session cannot silence
#: the alerts).
REAUTH_KEYS: Final[frozenset[str]] = frozenset(
    {key for key in DEFAULT_SETTINGS if key.startswith(("auth.", "privacy."))}
    | {
        "db.retention_days",
        "usage.retention_days",
        "reports.keep_days",
        "evidence.keep_days",
        "usage.pseudonymize_anonymous_role",
        "alerts.email_recipients",
        "alerts.email_min_severity",
    }
)
#: Keys the web interface shows but never changes: paths of files on the server. The console
#: (``arcilogger-db settings set``) still changes them.
LOCKED_KEYS: Final[frozenset[str]] = frozenset({"devices.oui_file", "usage.psl_file"})


# -- validation of administrator changes ----------------------------------------------------------

#: Smallest (and largest) accepted value of integer settings; other integers must be >= 0.
#: The ``auth.*`` bounds keep the spec 13 protections in force (a session idle timeout, an
#: absolute session limit, an account lockout, passwords of at least 12 characters): a typing
#: error or a careless value cannot switch them off. The upper bounds are conservative choices,
#: not spec values; widen them in code after an owner decision.
INTEGER_RANGES: Final[Mapping[str, tuple[int, int | None]]] = {
    "lookup.tolerance_seconds": (0, 3600),
    "lookup.max_candidates": (1, 10_000),
    "lookup.max_session_seconds": (60, None),
    "lookup.timeout_seconds": (1, 300),
    "lease.default_seconds": (60, None),
    "lease.timeout_percent": (100, 1000),
    "arp.interval_seconds": (60, None),
    "worker.leases_lag_seconds": (0, 86400),
    "auth.idle_minutes": (1, 120),
    "auth.absolute_hours": (1, 24),
    "auth.lockout_attempts": (1, 20),
    "auth.lockout_minutes": (1, 1440),
    "auth.password_min_length": (12, 128),
    "evidence.keep_days": (1, None),
    "evidence.max_unfinished_per_user": (1, None),
    "evidence.max_unfinished_total": (1, None),
    "evidence.max_requests_per_hour": (1, None),
    "evidence.min_free_percent": (0, 99),
    "evidence.exports_max_gb": (1, None),
    "db.retention_days": (1, None),
    # Area E (alerts): a value that would switch a check or the e-mail off is not accepted.
    "alerts.email_max_per_hour": (1, 1000),
    "alerts.email_digest_seconds": (0, 3600),
    "alerts.r15_startup_grace_minutes": (0, 1440),
    "alerts.r15_never_seen_minutes": (1, 10_080),
    "alerts.disk_used_percent": (50, 99),
    "alerts.seal_overdue_hour": (1, 23),
    "alerts.replica_stale_hours": (1, 720),
    "alerts.parse_failure_percent": (1, 100),
    "alerts.parse_min_messages": (1, None),
    "alerts.job_failures": (1, 100),
    "alerts.backup_stale_hours": (1, 720),
    "alerts.backup_missing_hours": (1, 720),
    # Area U (usage analytics).
    "usage.active_bytes_threshold": (1, 1_000_000_000),
    "usage.background_session_bytes": (0, 1_000_000_000),
    "usage.dns_window_seconds": (0, 86_400),
    "usage.session_overlap_seconds": (0, 86_400),
    "usage.auto_rebuild_days": (0, 62),
    "usage.retention_days": (1, None),
    "usage.urls_per_day": (0, 100_000),
    "usage.blocked_per_day": (0, 100_000),
    "usage.change_min_percent": (1, 10_000),
    # Area R (usage reports).
    "reports.keep_days": (1, None),
    "reports.max_attachment_mb": (1, 100),
}
#: Area E: most addresses ``alerts.email_recipients`` may list.
MAX_EMAIL_RECIPIENTS: Final = 20
#: Area E: the alert severities, lowest first (``alerts.email_min_severity``).
ALERT_SEVERITY_ORDER: Final = ALERT_SEVERITIES
_EMAIL = re.compile(EMAIL_ADDRESS)
_WEB_URL = re.compile(
    r"^https://[A-Za-z0-9.\-\[\]:]{1,255}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?$"
)
#: Roles a setting may name (``users.role``; :data:`arcilogger_db.models.ROLES`).
_ROLES: Final = ROLES
#: Roles for which spec 13 makes the second factor mandatory: ``auth.totp_required_roles`` may
#: add roles, never drop these.
TOTP_MANDATORY_ROLES: Final = ("admin", "auditor")
_MAC_DIGITS = re.compile(r"^[0-9a-f]{12}$")
_TYPE_NAMES: Final[Mapping[type, str]] = {
    bool: "true/false",
    int: "tam sayı",
    str: "metin",
    list: "liste",
    dict: "nesne",
    float: "ondalıklı sayı",
    type(None): "null",
}
#: The English names of :data:`_TYPE_NAMES` (``SettingValueError.message_en``).
_TYPE_NAMES_EN: Final[Mapping[type, str]] = {
    bool: "true/false",
    int: "an integer",
    str: "a text",
    list: "a list",
    dict: "an object",
}


def _ip(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise SettingValueError(
            key, "setting_ip_invalid", f"{key}: IP adresi bekleniyor, verilen {value!r}"
        )
    try:
        return canonical_ip(value.strip())
    except ValueError as exc:
        raise SettingValueError(
            key, "setting_ip_invalid", f"{key}: geçersiz IP adresi {value!r}"
        ) from exc


def _mac(key: str, value: Any) -> str:
    digits = re.sub(r"[:.\-]", "", value.strip()).lower() if isinstance(value, str) else ""
    if not _MAC_DIGITS.match(digits):
        raise SettingValueError(key, "setting_mac_invalid", f"{key}: geçersiz MAC adresi {value!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def validate_setting(key: str, value: Any) -> Any:
    """The value an administrator may store for ``key``, normalised; :class:`SettingValueError`
    (a ``ValueError``) otherwise.

    Only known keys (:data:`DEFAULT_SETTINGS`) are accepted, with the JSON type of their
    default (a ``bool`` is not an integer), within :data:`INTEGER_RANGES`; addresses are checked
    and written in canonical form (IP addresses as PostgreSQL prints them, MAC addresses
    ``aa:bb:cc:dd:ee:ff``), so a typing error cannot stop the lookups that read them.
    """
    if key not in DEFAULT_SETTINGS:
        raise SettingValueError(key, "setting_unknown", f"bilinmeyen ayar: {key}")
    default = DEFAULT_SETTINGS[key]
    kind = type(default)
    if type(value) is not kind:
        raise SettingValueError(
            key,
            "setting_type",
            f"{key}: {_TYPE_NAMES[kind]} bekleniyor (varsayılan "
            f"{canonical_json(default).decode()}), verilen: "
            f"{_TYPE_NAMES.get(type(value), 'başka')}",
            expected_tr=_TYPE_NAMES[kind],
            expected_en=_TYPE_NAMES_EN[kind],
        )
    check_setting_value(key, value)
    if isinstance(value, int) and not isinstance(value, bool):
        low, high = INTEGER_RANGES.get(key, (0, None))
        if value < low or (high is not None and value > high):
            upper = "" if high is None else f", en çok {high}"
            code = "setting_below_minimum" if high is None else "setting_out_of_range"
            raise SettingValueError(
                key, code, f"{key}: en az {low}{upper} olmalı", low=low, high=high
            )
    if key == "nat.public_ips":
        return _unique([_ip(key, item) for item in value])
    if key == "nat.wan_transip":
        return _ip(key, value) if value.strip() else ""
    if key == "devices.infrastructure_macs":
        return _unique([_mac(key, item) for item in value])
    if key == "auth.totp_required_roles":
        unknown = [item for item in value if item not in _ROLES]
        if unknown:
            raise SettingValueError(
                key,
                "setting_role_unknown",
                f"{key}: bilinmeyen rol {unknown} (geçerli: {', '.join(_ROLES)})",
                roles=", ".join(_ROLES),
            )
        missing = [role for role in TOTP_MANDATORY_ROLES if role not in value]
        if missing:
            raise SettingValueError(
                key,
                "setting_totp_required",
                f"{key}: {', '.join(missing)} rolü için iki adımlı doğrulama (TOTP) zorunludur "
                "(şartname 13); listeye rol eklenebilir, bu roller çıkarılamaz",
                roles=", ".join(TOTP_MANDATORY_ROLES),
            )
        return _unique(value)
    if key == "dhcp.server_vlan_regex":
        try:
            pattern = re.compile(value)
        except re.error as exc:
            raise SettingValueError(
                key, "setting_regex_invalid", f"{key}: geçersiz düzenli ifade: {exc}"
            ) from exc
        if pattern.groups < 1:
            raise SettingValueError(
                key,
                "setting_regex_group",
                f"{key}: ilk grup VLAN numarasını yakalamalı, örn. ^dhcp(\\d+)$",
            )
    if key == "lease.server_seconds":
        for server, seconds in value.items():
            if type(seconds) is not int or seconds < 60:
                raise SettingValueError(
                    key, "setting_lease_seconds", f"{key}: {server!r} için en az 60 saniye gerekir"
                )
    if key == "privacy.notice_version" and not value.strip():
        raise SettingValueError(key, "setting_empty", f"{key}: boş olamaz")
    if key.startswith("alerts."):
        return _validate_alert_setting(key, value)
    if key.startswith("usage."):
        return _validate_usage_setting(key, value)
    return value


def email_addresses(
    key: str, value: Iterable[Any], *, limit: int = MAX_EMAIL_RECIPIENTS
) -> list[str]:
    """E-mail recipients as stored: trimmed, the domain lower-case, duplicates dropped, at most
    ``limit``; :class:`SettingValueError` naming ``key`` for anything that is not an address.
    Shared by
    ``alerts.email_recipients`` and the recipients of a report schedule (area R)."""
    addresses = []
    for item in value:
        address = item.strip() if isinstance(item, str) else ""
        if not _EMAIL.match(address) or len(address) > 254:
            raise SettingValueError(
                key, "setting_email_invalid", f"{key}: geçersiz e-posta adresi {item!r}"
            )
        local, _, domain = address.partition("@")
        addresses.append(f"{local}@{domain.lower()}")
    addresses = _unique(addresses)
    if len(addresses) > limit:
        raise SettingValueError(
            key, "setting_too_many", f"{key}: en çok {limit} adres", limit=limit
        )
    return addresses


def _validate_alert_setting(key: str, value: Any) -> Any:
    """Area E: e-mail addresses, the severity name and the web address of the alert e-mail."""
    if key == "alerts.email_recipients":
        return email_addresses(key, value)
    if key == "alerts.email_min_severity":
        if value not in ALERT_SEVERITY_ORDER:
            severities = ", ".join(ALERT_SEVERITY_ORDER)
            raise SettingValueError(
                key,
                "setting_severity_invalid",
                f"{key}: {severities} değerlerinden biri olmalı",
                severities=severities,
            )
        return value
    if key == "alerts.web_base_url":
        url = value.strip().rstrip("/")
        if url and not _WEB_URL.match(url):
            raise SettingValueError(
                key,
                "setting_url_invalid",
                f"{key}: https:// ile başlayan sunucu adresi bekleniyor, ör. "
                "https://10.0.0.10 (boş: yapılandırmadan)",
            )
        return url
    return value


#: Area U: a host name pattern of ``usage.background_domains`` (``fnmatch`` wildcards ``*``
#: and ``?``, lower case, at most 253 characters).
_DOMAIN_PATTERN = re.compile(r"^[a-z0-9*?._-]{1,253}$")
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_HOLIDAY = re.compile(r"^(?:(\d{4})-)?(\d{2})-(\d{2})$")
#: Area U: most entries of the list settings (a typing error cannot slow every rollup down).
MAX_USAGE_LIST: Final = 500


def _validate_usage_setting(key: str, value: Any) -> Any:
    """Area U: patterns, application categories, ports, work hours and holidays."""
    if key == "usage.background_domains":
        patterns = []
        for item in value:
            pattern = item.strip().lower().rstrip(".") if isinstance(item, str) else ""
            if not _DOMAIN_PATTERN.match(pattern) or pattern.strip("*?.") == "":
                raise SettingValueError(
                    key,
                    "setting_domain_pattern_invalid",
                    f"{key}: geçersiz alan adı kalıbı {item!r} (ör. *.example.com)",
                )
            patterns.append(pattern)
        return _limited(key, _unique(patterns))
    if key == "usage.background_appcats":
        names = []
        for item in value:
            name = item.strip() if isinstance(item, str) else ""
            if not name or len(name) > 100:
                raise SettingValueError(
                    key, "setting_appcat_invalid", f"{key}: geçersiz uygulama kategorisi {item!r}"
                )
            names.append(name)
        return _limited(key, _unique(names))
    if key == "usage.background_ports":
        if any(type(item) is not int or not 1 <= item <= 65535 for item in value):
            raise SettingValueError(
                key, "setting_ports_invalid", f"{key}: 1-65535 arası port numaraları bekleniyor"
            )
        return _limited(key, sorted(set(value)))
    if key == "usage.work_hours":
        return _work_hours(key, value)
    if key == "usage.holidays":
        days = []
        for item in value:
            day = item.strip() if isinstance(item, str) else ""
            if not _valid_holiday(day):
                raise SettingValueError(
                    key,
                    "setting_holiday_invalid",
                    f"{key}: geçersiz tarih {item!r} (AA-GG ya da YYYY-AA-GG)",
                )
            days.append(day)
        return _limited(key, sorted(set(days)))
    if key == "usage.psl_file":
        return value.strip()
    return value


def _limited(key: str, items: list[Any]) -> list[Any]:
    if len(items) > MAX_USAGE_LIST:
        raise SettingValueError(
            key, "setting_too_many", f"{key}: en çok {MAX_USAGE_LIST} öğe", limit=MAX_USAGE_LIST
        )
    return items


def _valid_holiday(text: str) -> bool:
    match = _HOLIDAY.match(text)
    if match is None:
        return False
    year = int(match.group(1)) if match.group(1) else 2000  # a leap year: 02-29 is a date
    try:
        date(year, int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def _work_hours(key: str, value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {"days", "start", "end"}:
        raise SettingValueError(
            key, "setting_work_hours_fields", f"{key}: days, start ve end alanları bekleniyor"
        )
    days, start, end = value["days"], value["start"], value["end"]
    if not isinstance(days, list) or any(type(d) is not int or not 1 <= d <= 7 for d in days):
        raise SettingValueError(
            key,
            "setting_work_hours_days",
            f"{key}: days 1 (Pazartesi) - 7 (Pazar) arası gün numaraları olmalı",
        )
    if not isinstance(start, str) or not isinstance(end, str):
        raise SettingValueError(
            key, "setting_work_hours_time", f"{key}: start ve end SS:DD biçiminde olmalı"
        )
    if not _HHMM.match(start) or not _HHMM.match(end):
        raise SettingValueError(
            key,
            "setting_work_hours_time",
            f"{key}: start ve end SS:DD biçiminde olmalı (ör. 08:00)",
        )
    if start >= end:
        raise SettingValueError(
            key,
            "setting_work_hours_order",
            f"{key}: mesai başlangıcı bitişten önce olmalı (gece vardiyası yok)",
        )
    return {"days": sorted(set(days)), "start": start, "end": end}


def set_setting(session: Session, key: str, value: Any, *, updated_by: str) -> None:
    """Insert or replace a setting (the caller commits and writes the audit entry).

    Raises ``ValueError`` for values that are not canonical JSON (e.g. floats).
    """
    if not updated_by:
        raise ValueError("updated_by is required")
    check_setting_value(key, value)
    statement = insert(Setting).values(key=key, value=value, updated_by=updated_by)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Setting.key],
            set_={
                "value": statement.excluded.value,
                "updated_by": updated_by,
                "updated_at": func.now(),
            },
        )
    )
