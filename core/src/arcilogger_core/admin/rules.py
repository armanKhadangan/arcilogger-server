"""What the browser may change in ``arcilogger.toml``, and how every change is checked
(ADR-0014; contract 2.3, 2.6).

:data:`EDITABLE` is the single source of truth of the browser administration: every other path
of the configuration is locked (:func:`locked_paths`, with its reason) and refused with
``config_key_locked``. The same checks run twice:

* the API calls :func:`validate_section` with the body of a form (contract 2.11.1): it gets the
  ops of the request to sign, the masked changes, the field errors with their codes, warnings,
  whether the change is held or needs a recent re-authentication, and its effects. The typed
  confirmations (``confirm``, ``confirm_delete_days``, the name of a removed source …) and the
  test e-mail recipient are checked only here, where a person typed them;
* the agent calls :func:`check_request` with the verified request against the file as it is at
  apply time; this check is the authoritative one. It also returns what to write: the
  :mod:`~arcilogger_core.admin.tomledit` edits, the secret changes (tokens), the files to install
  and the probes to run.

The module is pure (it reads no file and runs nothing itself); what it needs from outside comes in
:class:`RuleContext`, so the API can import it without side effects. Field codes are those of
contract 2.12 (Turkish and English texts in :mod:`arcilogger_core.admin.messages` for the codes
the agent reports, in the API's tables for the others); a problem never echoes a submitted value.
``data_dir`` and ``secrets_dir`` are never written (contract Day 0 note D0-2).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import ipaddress
import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, JsonValue, ValidationError

from arcilogger_core.admin.contract import (
    AdminPolicy,
    AdminRequest,
    BrowserSecret,
    LegalRootAddOp,
    Op,
    RootAddOp,
    SecretDeleteOp,
    SecretSetOp,
    Section,
    SetOp,
    SmtpCaClearOp,
    SmtpCaSetOp,
    SourceAddOp,
    SourceRemoveOp,
    SourceUpdateOp,
    UploadKind,
    ZamaneStageOp,
)
from arcilogger_core.admin.roots import (
    CaBundleInspection,
    RootInspection,
    inspect_ca_bundle,
    inspect_root,
    root_file_name,
)
from arcilogger_core.admin.tomledit import Edit, SetSources, SetValue
from arcilogger_core.admin.zamane import ZamaneInspection, inspect_client
from arcilogger_core.canonical import canonical_json
from arcilogger_core.config import (
    EMAIL_ADDRESS,
    KAMUSM_PRODUCTION_HOST,
    KAMUSM_TEST_HOST,
    MIN_RETENTION_DAYS,
    Config,
    SourceConfig,
)
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.secretstore import SecretValueError, check_secret_value
from arcilogger_core.smbshare import unc_root
from arcilogger_core.tsa.preflight import executables_trusted, production_missing
from arcilogger_core.tsa.zamane_releases import find_release

# -- the allow-list ------------------------------------------------------------------------------

#: How the browser changes a path: a plain value, the time-stamp server choice (provider, URL
#: and port together), a stored secret, an upload (a CA file), the promotion of a staged Zamane
#: client, a legal root fingerprint, or a source table.
Via = Literal["value", "server", "secret", "upload", "promotion", "legal_root", "source"]
LockReason = Literal["security", "service", "evidence", "expert", "collector"]


@dataclass(frozen=True)
class Editable:
    section: Section
    via: Via
    #: A change needs a recent re-authentication (contract 2.6; sources: disable and remove).
    reauth: bool


def _paths(section: Section, via: Via, reauth: bool, *paths: str) -> dict[str, Editable]:
    return {path: Editable(section, via, reauth) for path in paths}


EDITABLE: Final[Mapping[str, Editable]] = MappingProxyType(
    {
        **_paths("general", "value", False, "site_name"),
        **_paths(
            "general",
            "value",
            True,
            "clock.ntp_server",
            "clock.fallback_server",
            "clock.alert_offset_ms",
        ),
        **_paths("tsa", "server", True, "tsa.provider", "tsa.url", "tsa.port"),
        **_paths("tsa", "value", False, "tsa.credit_alert_threshold", "tsa.timeout_seconds"),
        **_paths("tsa", "value", True, "tsa.java"),
        **_paths("tsa", "promotion", True, "tsa.zamane_jar"),
        **_paths("tsa", "legal_root", True, "tsa.legal_root_fingerprints"),
        **_paths("tsa", "secret", True, "kamusm.customer", "kamusm.password"),
        **_paths(
            "smtp",
            "value",
            True,
            "smtp.enabled",
            "smtp.host",
            "smtp.port",
            "smtp.security",
            "smtp.username",
            "smtp.from_address",
            "smtp.timeout_seconds",
        ),
        **_paths("smtp", "upload", True, "smtp.ca_file"),
        **_paths("smtp", "secret", True, "smtp.password"),
        **_paths("replica", "value", True, "replica.enabled", "replica.target", "replica.user"),
        **_paths("replica", "secret", True, "replica.password"),
        **_paths("retention", "value", True, "retention.enabled", "retention.days"),
        **_paths(
            "backup",
            "value",
            True,
            "backup.enabled",
            "backup.keep_daily",
            "backup.keep_weekly",
            "backup.keep_monthly",
            "backup.min_free_gb",
        ),
        **_paths("sources", "source", False, "sources"),
    }
)
#: The secret of each section the browser may set (``kamusm.*`` belong to ``tsa``).
SECTION_SECRETS: Final[Mapping[Section, tuple[BrowserSecret, ...]]] = MappingProxyType(
    {
        "tsa": ("kamusm.customer", "kamusm.password"),
        "smtp": ("smtp.password",),
        "replica": ("replica.password",),
    }
)
#: Stored secrets the browser never sets.
LOCKED_SECRETS: Final = ("database.password", "usage.pseudonym_key")


@dataclass(frozen=True)
class LockedPath:
    path: str
    reason: LockReason


def _lock_reason(path: str) -> LockReason:
    top = path.split(".")[0]
    if top in ("data_dir", "secrets_dir", "database", "usage") or path in (
        "backup.dir",
        "backup.pg_dump",
        "backup.pg_restore",
    ):
        return "security"
    if top in ("api", "ingest", "worker"):
        return "service"
    if top == "collector":
        return "collector"
    if top == "seal":
        return "evidence"
    return "expert"


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    paths = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths += _leaf_paths(annotation, f"{prefix}{name}.")
        else:
            paths.append(f"{prefix}{name}")
    return paths


def locked_paths() -> tuple[LockedPath, ...]:
    """Every path of the configuration (and stored secret) the browser may not change, with the
    reason ``GET /admin/config`` shows (``security``, ``service``, ``evidence``, ``expert``,
    ``collector``)."""
    paths = [p for p in _leaf_paths(Config) if p not in EDITABLE]
    return tuple(LockedPath(p, _lock_reason(p)) for p in [*paths, *LOCKED_SECRETS])


SECTIONS: Final[tuple[Section, ...]] = get_args(Section)
#: The plain paths of each section (its values in ``GET /admin/config`` and its etag).
SECTION_PATHS: Final[Mapping[Section, tuple[str, ...]]] = MappingProxyType(
    {
        section: tuple(
            path
            for path, item in EDITABLE.items()
            if item.section == section and item.via not in ("secret", "source")
        )
        for section in SECTIONS
    }
)

#: The two Zamane servers the browser may choose: provider, URL, port.
TSA_SERVERS: Final[Mapping[str, tuple[str, str, int]]] = MappingProxyType(
    {
        "test": ("zamane", f"http://{KAMUSM_TEST_HOST}", 80),
        "production": ("zamane", f"http://{KAMUSM_PRODUCTION_HOST}", 80),
    }
)
#: Hosts an SMTP password may go to without encryption (a relay on this server).
LOCAL_SMTP_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})

#: HTTP status of each rule code (contract 2.12); every other code is 422.
HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "config_unsupported_format": 409,
        "source_not_found": 404,
        "source_name_taken": 409,
        "source_address_taken": 409,
        "source_overlap": 409,
        "tsa_not_ready": 409,
        "tsa_would_break": 409,
        "root_exists": 409,
        "upload_not_found": 404,
        "conflict": 409,
        "payload_mismatch": 409,
    }
)
#: A token placeholder of :func:`validate_section` without ``RuleContext.encrypt`` (a dry run:
#: the ops are shown, never signed).
UNENCRYPTED: Final = "gAAAAA-not-encrypted"

_HOST: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")  # config._HOST
_NUMERIC: Final = re.compile(r"^[0-9.]+$")
_EMAIL: Final = re.compile(EMAIL_ADDRESS)
_SHARE: Final = re.compile(r"^[A-Za-z0-9$_.-]{1,80}$")
_DOMAIN_USER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\\[A-Za-z0-9._ $-]{1,104}$")
_UPN_USER: Final = re.compile(r"^[A-Za-z0-9._-]{1,104}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_REFUSED_TEXT: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


# -- results -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    """An error or a warning: a code of contract 2.12, the configuration path or form field it
    is about, and details that never repeat a submitted value (``missing``, ``causes``)."""

    code: str
    field: str | None = None
    details: Mapping[str, JsonValue] = dataclasses.field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 422)


Probes = Literal["sntp", "credit", "smtp", "replica"]


@dataclass(frozen=True)
class Probe:
    """A connection test with the candidate values (``probe.py``): the SNTP server to ask, or
    the credit query, the trial e-mail, the replica share."""

    kind: Probes
    target: str | None = None


@dataclass(frozen=True)
class Install:
    """A file the change installs under a new name (a root, an SMTP CA file)."""

    target: Path
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class SectionCheck:
    """The outcome of a check. ``ok`` when there is no error."""

    section: Section
    #: The ops of the request (contract ``AdminRequest.ops``, as JSON).
    ops: tuple[dict[str, JsonValue], ...] = ()
    #: ``{path: {old, new}}``, secrets as ``{secret, old, new, changed}`` (journal format).
    changes: Mapping[str, Mapping[str, JsonValue]] = field(default_factory=dict)
    errors: tuple[Problem, ...] = ()
    warnings: tuple[Problem, ...] = ()
    #: Held for ``policy.hold_hours`` (when it is not 0): retention, replica.
    guarded: bool = False
    #: The SHA-256 of a Zamane client that waits for its approval (``jars.json``).
    jar_hold: str | None = None
    reauth: bool = False
    effects: tuple[str, ...] = ()
    probes: tuple[Probe, ...] = ()
    #: ``R20-config-change`` severity, None: no alert.
    alert: Literal["medium", "high"] | None = None
    #: Tasks to queue after the change (a replica test the probe could not do first).
    followups: tuple[str, ...] = ()
    #: Options of the request (``test_to``), as JSON.
    options: Mapping[str, JsonValue] = field(default_factory=dict)
    #: What the agent writes (empty while there are errors).
    edits: tuple[Edit, ...] = ()
    secret_changes: Mapping[str, str | None] = field(default_factory=dict)
    installs: tuple[Install, ...] = ()
    #: The upload of a Zamane client to stage, and its jar SHA-256.
    stage_jar: tuple[str, str] | None = None
    #: The configuration the change gives (None while there are errors).
    candidate: Config | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def http_status(self) -> int:
        return self.errors[0].http_status if self.errors else 200

    @property
    def system_sync(self) -> bool:
        """ApplySystem must run after the change (firewall or time)."""
        return any(effect in ("system.firewall", "system.time") for effect in self.effects)

    def effective_at(self, policy: AdminPolicy, now: datetime) -> datetime | None:
        """When a held change takes effect (None: at once)."""
        if not self.guarded or policy.hold_hours == 0:
            return None
        return now + timedelta(hours=policy.hold_hours)


@dataclass(frozen=True)
class UploadInfo:
    """An upload the request refers to, as inspected by :func:`inspect_upload`."""

    id: str
    kind: UploadKind
    sha256: str
    size: int
    root: RootInspection | None = None
    bundle: CaBundleInspection | None = None
    zamane: ZamaneInspection | None = None
    #: The bytes of an SMTP CA file (installed as they are).
    data: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RetentionPreview:
    cutoff: date
    days_to_delete: int
    oldest: date | None
    newest: date | None
    first_run_at: datetime | None = None


@dataclass(frozen=True)
class RuleContext:
    """What a check needs from outside (the API and the agent build it)."""

    policy: AdminPolicy
    #: Of the installation folder of the fixed ``--config`` path.
    anchors: TrustAnchors
    #: The names in ``secrets.json`` now.
    stored_secrets: frozenset[str]
    now: datetime
    java_candidates: tuple[str, ...] = ()
    local_addresses: tuple[str, ...] = ()
    #: SHA-256 (DER) of the roots in ``kamusm-roots\\``.
    installed_roots: frozenset[str] = frozenset()
    #: SHA-256 of the Zamane clients ``admin\\system\\jars.json`` lists as approved.
    approved_jars: frozenset[str] = frozenset()
    uploads: Mapping[str, UploadInfo] = field(default_factory=dict)
    #: ``tsa.preflight.seal_preflight`` for a configuration and the secret names to assume.
    preflight: Callable[[Config, frozenset[str]], Sequence[str]] | None = None
    strict_outbound: bool = False
    #: A successful stamp test on the production server is recorded.
    production_tested: bool = False
    retention_preview: Callable[[Config], RetentionPreview] | None = None
    #: The API: who may receive the trial e-mail (alert recipients, the user's own address).
    test_recipients: frozenset[str] | None = None
    #: The API: ``SecretStore.encrypt_value`` (without it, ops carry :data:`UNENCRYPTED`).
    encrypt: Callable[[str], str] | None = field(default=None, repr=False)


# -- values ----------------------------------------------------------------------------------------


def _get(document: Mapping[str, Any], path: str) -> Any:
    node: Any = document
    for part in path.split("."):
        node = node[part]
    return node


def current_value(config: Config, path: str) -> JsonValue:
    """The value of ``path`` as JSON (an op's ``expect``); ``KeyError`` for an unknown path."""
    value: JsonValue = _get(config.model_dump(mode="json"), path)
    return value


def secret_state(name: str, stored: frozenset[str]) -> Literal["set", "unset"]:
    return "set" if name in stored else "unset"


def tsa_server(provider: Any, url: Any, port: Any) -> str:
    """``test``, ``production``, ``mock`` or ``custom`` (an RFC 3161 server, another URL)."""
    for name, server in TSA_SERVERS.items():
        if (provider, url, port) == server:
            return name
    return "mock" if provider == "mock" else "custom"


def section_values(
    section: Section, config: Config, stored: frozenset[str]
) -> dict[str, JsonValue]:
    """The values of a section the browser edits (secrets as ``set``/``unset``)."""
    if section == "sources":
        return {"sources": [source.model_dump(mode="json") for source in config.sources]}
    values = {path: current_value(config, path) for path in SECTION_PATHS[section]}
    for name in SECTION_SECRETS.get(section, ()):
        values[name] = secret_state(name, stored)
    return values


def etag(section: Section, config: Config, stored: frozenset[str]) -> str:
    """``<section>:<sha256 of the canonical editable values>`` (contract 2.11.1)."""
    digest = hashlib.sha256(canonical_json(section_values(section, config, stored))).hexdigest()
    return f"{section}:{digest}"


def conflicts(ops: Sequence[Op], config: Config, stored: frozenset[str]) -> list[str]:
    """The paths whose ``expect`` no longer holds (contract 2.7.1 step 1: ``conflict``)."""
    found: list[str] = []
    sources = {source.name: source for source in config.sources}
    for op in ops:
        if isinstance(op, SetOp):
            if op.path not in EDITABLE:
                continue  # refused as locked, whatever it expects
            if current_value(config, op.path) != op.expect:
                found.append(op.path)
        elif isinstance(op, SecretSetOp | SecretDeleteOp):
            if secret_state(op.name, stored) != op.expect:
                found.append(op.name)
        elif isinstance(op, SourceAddOp):
            if op.source.name in sources:
                found.append(f"sources.{op.source.name}")
        elif isinstance(op, SourceUpdateOp | SourceRemoveOp):
            if sources.get(op.name) != op.expect:
                found.append(f"sources.{op.name}")
        elif isinstance(op, SmtpCaClearOp) and current_value(config, "smtp.ca_file") != op.expect:
            found.append("smtp.ca_file")
    return found


def _text_ok(value: str) -> bool:
    return not any(unicodedata.category(char) in _REFUSED_TEXT for char in value)


def host_ok(value: str) -> bool:
    """A host name as ``config._HOST`` allows it, or a dotted-quad IPv4 address (a shorthand
    such as ``10.0.3`` would resolve to another host)."""
    if len(value) > 253:
        return False
    if _NUMERIC.fullmatch(value):
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            return False
        return True
    return _HOST.fullmatch(value) is not None


def unc_host(target: Any) -> str | None:
    """The host of a UNC target (``\\\\host\\share\\…``), lower case."""
    if not isinstance(target, str):
        return None
    text = target.replace("/", "\\")
    if not text.startswith("\\\\"):
        return None
    return text[2:].split("\\")[0].lower()


def unc_ok(target: str) -> bool:
    """``\\\\host\\share[\\path]`` only: host ``config._HOST`` or IPv4, share
    ``[A-Za-z0-9$_.-]{1,80}``, path segments without ``..``; no ``\\\\?\\``, ``\\\\.\\`` or drive
    letter (contract 2.6 replica)."""
    text = target.replace("/", "\\")
    if not text.startswith("\\\\") or text.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    if len(text) > 260 or not _text_ok(text) or ":" in text:
        return False
    parts = text[2:].split("\\")
    if len(parts) < 2:
        return False
    host, share, *rest = parts
    if not host_ok(host) or _SHARE.fullmatch(share) is None:
        return False
    if rest and rest[-1] == "":
        rest = rest[:-1]  # a trailing backslash
    if any(segment in ("", ".", "..") or segment.endswith((" ", ".")) for segment in rest):
        return False
    if any(set(segment) & set('*?"<>|') for segment in rest):
        return False
    return unc_root(target) is not None


def replica_user_ok(user: str) -> bool:
    """``HOST\\user``, ``DOMAIN\\user`` or ``user@domain``, at most 256 characters."""
    if len(user) > 256:
        return False
    return bool(_DOMAIN_USER.fullmatch(user) or _UPN_USER.fullmatch(user))


def _integer(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _posix(path: Path) -> str:
    return path.as_posix()


def _same_path(first: Any, second: Path) -> bool:
    if not isinstance(first, str):
        return False
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(  # noqa: PTH100
        os.path.abspath(second)  # noqa: PTH100
    )


# -- the shared check ------------------------------------------------------------------------------


class _Run:
    """One check: the ops applied to a copy of the configuration's values, then the rules of
    the section on the candidate."""

    def __init__(self, section: Section, current: Config, context: RuleContext) -> None:
        self.section = section
        self.current = current
        self.context = context
        self.before: dict[str, Any] = current.model_dump(mode="json")
        self.doc: dict[str, Any] = copy.deepcopy(self.before)
        self.stored = frozenset(context.stored_secrets)
        self.secrets_after = set(self.stored)
        self.secret_changes: dict[str, str | None] = {}
        self.errors: list[Problem] = []
        self.warnings: list[Problem] = []
        self.changes: dict[str, dict[str, JsonValue]] = {}
        #: Paths set by ops, and paths the agent derives (fingerprints, CA file, roots dir, jar).
        self.set_paths: dict[str, JsonValue] = {}
        self.derived: dict[str, JsonValue] = {}
        self.sources: list[SourceConfig] | None = None
        self.source_ops = 0
        self.installs: list[Install] = []
        self.roots_added: set[str] = set()
        self.jar_hold: str | None = None
        self.stage_jar: tuple[str, str] | None = None
        self.guarded = False
        self.reauth = False
        self.alerts: set[Literal["medium", "high"]] = set()
        self.effects: set[str] = set()
        self.probes: list[Probe] = []
        self.followups: list[str] = []

    # -- helpers ---------------------------------------------------------------------------------

    def error(self, code: str, path: str | None = None, **details: Any) -> None:
        problem = Problem(code, path, details)
        if problem not in self.errors:
            self.errors.append(problem)

    def warn(self, code: str, path: str | None = None) -> None:
        problem = Problem(code, path)
        if problem not in self.warnings:
            self.warnings.append(problem)

    def value(self, path: str) -> Any:
        return _get(self.doc, path)

    def old(self, path: str) -> Any:
        return _get(self.before, path)

    def changed(self, path: str) -> bool:
        return path in self.set_paths or path in self.derived

    def _put(self, path: str, value: JsonValue) -> None:
        table, _, key = path.rpartition(".")
        target = self.doc[table] if table else self.doc
        target[key] = copy.deepcopy(value)

    def derive(self, path: str, value: JsonValue) -> None:
        """A path the change sets that the browser never gives as a value."""
        self.derived[path] = value
        self._put(path, value)

    def alert(self, severity: Literal["medium", "high"]) -> None:
        self.alerts.add(severity)

    def probe(self, kind: Probes, target: str | None = None) -> None:
        item = Probe(kind, target)
        if item not in self.probes:
            self.probes.append(item)

    # -- ops -------------------------------------------------------------------------------------

    def apply(self, op: Op) -> None:
        if isinstance(op, SetOp):
            self._set(op)
        elif isinstance(op, SecretSetOp | SecretDeleteOp):
            self._secret(op)
        elif isinstance(op, SourceAddOp | SourceUpdateOp | SourceRemoveOp):
            self._source(op)
        elif isinstance(op, RootAddOp):
            self._root(op)
        elif isinstance(op, LegalRootAddOp):
            self._legal_root(op.sha256)
        elif isinstance(op, ZamaneStageOp):
            self._zamane(op)
        elif isinstance(op, SmtpCaSetOp):
            self._smtp_ca(op)
        else:
            self._in_section("smtp", "smtp.ca_file")
            self.derive("smtp.ca_file", None)

    def _in_section(self, section: Section, path: str) -> bool:
        if self.section != section:
            self.error("config_key_locked", path)
            return False
        return True

    def _set(self, op: SetOp) -> None:
        editable = EDITABLE.get(op.path)
        if (
            editable is None
            or editable.section != self.section
            or editable.via not in ("value", "server")
        ):
            self.error("config_key_locked", op.path)
            return
        if op.path in self.set_paths:
            self.error("config_invalid", op.path)
            return
        self.set_paths[op.path] = op.value
        self._put(op.path, op.value)

    def _secret(self, op: SecretSetOp | SecretDeleteOp) -> None:
        if not self._in_section(EDITABLE[op.name].section, op.name):
            return
        if op.name in self.secret_changes:
            self.error("config_invalid", op.name)
            return
        if isinstance(op, SecretSetOp):
            self.secrets_after.add(op.name)
            self.secret_changes[op.name] = op.token
        else:
            self.secrets_after.discard(op.name)
            self.secret_changes[op.name] = None

    def _upload(self, upload: str, sha256: str, size: int, kind: UploadKind) -> UploadInfo | None:
        info = self.context.uploads.get(upload)
        if info is None:
            self.error("upload_not_found")
            return None
        if info.kind != kind or info.sha256 != sha256 or info.size != size:
            self.error("payload_mismatch")
            return None
        return info

    def _root(self, op: RootAddOp) -> None:
        if not self._in_section("tsa", "tsa.roots_dir"):
            return
        info = self._upload(op.upload, op.sha256, op.size, "kamusm_root")
        if info is None:
            return
        inspection = info.root
        if inspection is None or not inspection.ok or inspection.certificate is None:
            self.error("root_certificate_invalid", "tsa.roots_dir")
            return
        fingerprint = inspection.certificate.sha256
        if fingerprint in self.context.installed_roots or fingerprint in self.roots_added:
            self.error("root_exists", "tsa.roots_dir")
            return
        for code in inspection.warnings:
            self.warn(code, "tsa.roots_dir")
        roots_dir = self.context.anchors.root / "kamusm-roots"
        configured = self.value("tsa.roots_dir")
        if configured is None:
            self.derive("tsa.roots_dir", _posix(roots_dir))
        elif not _same_path(configured, roots_dir):
            # Roots are added to the installation's folder only; a moved roots_dir is a
            # console matter.
            self.error("config_key_locked", "tsa.roots_dir")
            return
        self.roots_added.add(fingerprint)
        self.installs.append(
            Install(roots_dir / root_file_name(fingerprint), inspection.pem or b"")
        )
        self.changes[f"tsa.roots.{fingerprint}"] = {"old": None, "new": "installed"}
        self.alert("high")
        if op.legal:
            self._legal_root(fingerprint)

    def _legal_root(self, fingerprint: str) -> None:
        if not self._in_section("tsa", "tsa.legal_root_fingerprints"):
            return
        if fingerprint not in self.context.installed_roots and fingerprint not in self.roots_added:
            self.error("root_not_installed", "tsa.legal_root_fingerprints")
            return
        listed = list(self.value("tsa.legal_root_fingerprints"))
        if fingerprint not in listed:
            self.derive("tsa.legal_root_fingerprints", [*listed, fingerprint])
            self.alert("high")

    def _zamane(self, op: ZamaneStageOp) -> None:
        if not self._in_section("tsa", "tsa.zamane_jar"):
            return
        info = self._upload(op.upload, op.sha256, op.size, "zamane_client")
        if info is None:
            return
        inspection = info.zamane
        if inspection is None or not inspection.ok:
            self.error("zamane_archive_invalid", "tsa.zamane_jar")
            return
        if inspection.jar_sha256 != op.jar_sha256 or inspection.release != op.release:
            self.error("payload_mismatch")
            return
        self.stage_jar = (op.upload, op.jar_sha256)
        self.effects.add("jar.promotion")
        self.alert("high")
        approved = self.context.anchors.zamane.approved / f"{op.jar_sha256}.jar"
        if op.jar_sha256 in self.context.approved_jars:
            self.derive("tsa.zamane_jar", _posix(approved))
        else:
            self.jar_hold = op.jar_sha256
            if find_release(op.jar_sha256) is None:
                self.warn("unknown_jar_held", "tsa.zamane_jar")
        self.changes["tsa.zamane_jar"] = {
            "old": self.old("tsa.zamane_jar"),
            "new": _posix(approved),
        }

    def _smtp_ca(self, op: SmtpCaSetOp) -> None:
        if not self._in_section("smtp", "smtp.ca_file"):
            return
        info = self._upload(op.upload, op.sha256, op.size, "smtp_ca")
        if info is None:
            return
        if info.bundle is None or not info.bundle.ok or info.data is None:
            self.error("root_certificate_invalid", "smtp.ca_file")
            return
        target = self.context.anchors.root / "smtp-ca" / f"{op.sha256}.pem"
        self.installs.append(Install(target, info.data))
        self.derive("smtp.ca_file", _posix(target))

    def _source(self, op: SourceAddOp | SourceUpdateOp | SourceRemoveOp) -> None:
        if not self._in_section("sources", "sources"):
            return
        self.source_ops += 1
        if self.source_ops > 1:
            self.error("source_invalid", "sources")  # one source per request
            return
        current = list(self.current.sources)
        names = {source.name: source for source in current}
        if isinstance(op, SourceAddOp):
            name = op.source.name
            if name in names:
                self.error("source_name_taken", f"sources.{name}.name")
                return
            if len(current) >= self.context.policy.max_sources:
                self.error("source_limit", "sources")
                return
            if self._source_ok(op.source, current):
                self.sources = [*current, op.source]
                self._source_change(name, None, op.source)
            return
        if op.name not in names:
            self.error("source_not_found", f"sources.{op.name}")
            return
        before = names[op.name]
        others = [source for source in current if source.name != op.name]
        if isinstance(op, SourceRemoveOp):
            self.sources = others
            self._source_change(op.name, before, None)
            self._source_gone(before)
            return
        if op.source.name != op.name:
            self.error("source_rename_not_allowed", f"sources.{op.name}.name")
            return
        if self._source_ok(op.source, others):
            self.sources = [op.source if source.name == op.name else source for source in current]
            self._source_change(op.name, before, op.source)
            if before.enabled and not op.source.enabled:
                self._source_gone(before)

    def _source_change(
        self, name: str, before: SourceConfig | None, after: SourceConfig | None
    ) -> None:
        self.changes[f"sources.{name}"] = {
            "old": before.model_dump(mode="json") if before else None,
            "new": after.model_dump(mode="json") if after else None,
        }

    def _source_gone(self, before: SourceConfig) -> None:
        """A source disabled or removed: re-authentication, an alert, and the sealer no longer
        expects it."""
        self.reauth = True
        self.alert("high")
        if before.expected and before.enabled:
            self.warn("source_disable_gaps", f"sources.{before.name}")

    def _source_ok(self, source: SourceConfig, others: Sequence[SourceConfig]) -> bool:
        problem = source_address_problem(
            source.address,
            self.context.policy,
            self.context.local_addresses,
            others,
        )
        if problem is not None:
            self.error(problem, f"sources.{source.name}.address")
            return False
        if source.name.startswith("unknown-"):
            self.error("source_invalid", f"sources.{source.name}.name")
            return False
        return True

    # -- result ----------------------------------------------------------------------------------

    def result(self, ops: Sequence[Op], options: Mapping[str, JsonValue]) -> SectionCheck:
        for path in sorted(self.set_paths.keys() | self.derived.keys()):
            if path in self.changes:
                continue
            old, new = self.old(path), self.value(path)
            if old != new:
                self.changes[path] = {"old": old, "new": new}
        for name, token in self.secret_changes.items():
            self.changes[name] = {
                "secret": True,
                "old": secret_state(name, self.stored),
                "new": "set" if token is not None else "deleted",
                "changed": True,
            }
        candidate = None
        edits: list[Edit] = []
        if not self.errors:
            if self.sources is not None:
                self.doc["sources"] = [source.model_dump(mode="json") for source in self.sources]
            try:
                candidate = Config.model_validate(self.doc)
            except ValidationError as exc:
                for error in exc.errors(include_input=False, include_url=False):
                    self.error("config_invalid", _error_path(error["loc"], self.doc))
        if not self.errors:
            edits = [
                SetValue(path, value)
                for path, value in {**self.set_paths, **self.derived}.items()
                if self.old(path) != self.value(path)
            ]
            if self.sources is not None:
                edits.append(SetSources(tuple(self.sources)))
        alert: Literal["medium", "high"] | None = (
            "high" if "high" in self.alerts else "medium" if self.alerts else None
        )
        ok = not self.errors
        return SectionCheck(
            section=self.section,
            ops=tuple(op.model_dump(mode="json") for op in ops),
            changes=self.changes,
            errors=tuple(self.errors),
            warnings=tuple(self.warnings),
            guarded=self.guarded,
            jar_hold=self.jar_hold,
            reauth=self.reauth,
            effects=tuple(sorted(self.effects)),
            probes=tuple(self.probes),
            alert=alert,
            followups=tuple(self.followups),
            options=options,
            edits=tuple(edits),
            secret_changes=dict(self.secret_changes) if ok else {},
            installs=tuple(self.installs) if ok else (),
            stage_jar=self.stage_jar if ok else None,
            candidate=candidate,
        )


def _error_path(location: Sequence[int | str], document: Mapping[str, Any]) -> str | None:
    """A pydantic error location as a configuration path (a source by its name)."""
    parts: list[str] = []
    node: Any = document
    for part in location:
        if isinstance(part, int):
            name = node[part].get("name") if isinstance(node, list) and part < len(node) else None
            if isinstance(name, str):
                parts.append(name)
            node = node[part] if isinstance(node, list) and part < len(node) else None
            continue
        parts.append(part)
        node = node.get(part) if isinstance(node, dict) else None
    path = ".".join(parts)
    return path if re.fullmatch(r"[a-z_]+(\.[a-z0-9][a-z0-9_-]*)*", path) else None


def _sender_set(address: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    try:
        return ipaddress.ip_network(address, strict=False)
    except ValueError:
        return None


def source_address_problem(
    address: str,
    policy: AdminPolicy,
    local_addresses: Sequence[str],
    others: Sequence[SourceConfig],
) -> str | None:
    """The code of a source address the browser may not add (contract 2.6 sources), or None."""
    try:
        network = ipaddress.ip_network(address, strict=False)
    except ValueError:
        return "source_invalid"
    if not isinstance(network, ipaddress.IPv4Network):
        return "source_ipv6_unsupported"
    host = "/" not in address
    if (
        network.is_loopback
        or network.is_multicast
        or network.is_link_local
        or network.is_reserved
        or network.is_unspecified
        or network.network_address == ipaddress.IPv4Address("255.255.255.255")
    ):
        return "source_reserved_address"
    if network.num_addresses == 1 and str(network.network_address) in local_addresses:
        return "source_own_address"
    allowed = [ipaddress.IPv4Network(item) for item in policy.source_networks]
    if not any(network.subnet_of(item) for item in allowed):
        return "source_network_not_allowed"
    if not host and network.prefixlen < policy.min_prefix_v4:
        return "source_prefix_too_short"
    for other in others:
        if _sender_set(other.address) == network:  # 172.20.10.1/32 is 172.20.10.1
            return "source_address_taken"
    if not host:
        for other in others:
            if other.is_network and network.overlaps(ipaddress.ip_network(other.address)):
                return "source_overlap"
    return None


# -- the rules of each section ---------------------------------------------------------------------


def _general(run: _Run) -> None:
    if run.changed("site_name"):
        site = run.value("site_name")
        if not isinstance(site, str):
            run.error("config_invalid", "site_name")
        elif not site.strip():
            run.error("site_name_required", "site_name")
        elif len(site.strip()) > 200 or not _text_ok(site):
            run.error("config_invalid", "site_name")
        run.effects |= {"reload.api", "reload.worker"}
    clock = [path for path in ("clock.ntp_server", "clock.fallback_server") if run.changed(path)]
    for path in clock:
        server = run.value(path)
        if not isinstance(server, str) or (server and not host_ok(server)):
            run.error("host_invalid", path)
    if run.changed("clock.alert_offset_ms") and not _integer(
        run.value("clock.alert_offset_ms"), 1, 60_000
    ):
        run.error("config_invalid", "clock.alert_offset_ms")
    if not any(path.startswith("clock.") for path in run.set_paths):
        return
    run.reauth = True
    run.effects |= {"system.time", "task.next_run:ClockCheck"}
    if run.context.strict_outbound and clock:
        run.effects.add("system.firewall")
    if run.errors:
        return
    if not run.value("clock.ntp_server") and not run.value("clock.fallback_server"):
        run.error("clock_servers_required", "clock.fallback_server")
        return
    if not run.value("clock.ntp_server"):
        run.warn("ntp_server_empty", "clock.ntp_server")
    for path in clock:
        server = run.value(path)
        if server and server != run.old(path):
            if run.context.strict_outbound:
                run.warn("probe_skipped", path)  # unreachable until ApplySystem opens it
            else:
                run.probe("sntp", server)


def _tsa(run: _Run) -> None:
    server_paths = [p for p in ("tsa.provider", "tsa.url", "tsa.port") if run.changed(p)]
    before = tsa_server(run.old("tsa.provider"), run.old("tsa.url"), run.old("tsa.port"))
    after = tsa_server(run.value("tsa.provider"), run.value("tsa.url"), run.value("tsa.port"))
    if server_paths and after not in TSA_SERVERS:
        code = "tsa_mock_refused" if run.value("tsa.provider") == "mock" else "tsa_server_invalid"
        run.error(code, "tsa.provider")
    if run.changed("tsa.credit_alert_threshold") and not _integer(
        run.value("tsa.credit_alert_threshold"), 0, 1_000_000
    ):
        run.error("config_invalid", "tsa.credit_alert_threshold")
    if run.changed("tsa.timeout_seconds") and not _integer(
        run.value("tsa.timeout_seconds"), 5, 600
    ):
        run.error("config_invalid", "tsa.timeout_seconds")
    if run.changed("tsa.java"):
        java = run.value("tsa.java")
        if (
            not isinstance(java, list)
            or len(java) != 1
            or java[0] not in run.context.java_candidates
        ):
            run.error("java_not_detected", "tsa.java")
    credentials = [name for name in SECTION_SECRETS["tsa"] if name in run.secret_changes]
    sensitive = [
        path
        for path in [*run.set_paths, *run.derived]
        if path not in ("tsa.credit_alert_threshold", "tsa.timeout_seconds")
    ]
    if sensitive or credentials or run.roots_added or run.stage_jar:
        run.reauth = True
    if run.set_paths or run.derived or credentials or run.stage_jar:
        run.effects |= {"reload.api", "reload.worker", "task.next_run:Seal"}
    if credentials:
        run.alert("high")
    if server_paths and after in TSA_SERVERS:
        if before == "production" and after == "test":
            run.warn("production_stamps_stop", "tsa.provider")
            run.alert("high")
        if after == "production" and before != "production":
            if not run.value("tsa.legal_root_fingerprints"):
                run.warn("legal_roots_empty", "tsa.legal_root_fingerprints")
            if not run.context.production_tested:
                run.warn("production_not_tested", "tsa.provider")
        if run.context.strict_outbound:
            run.effects.add("system.firewall")


def _tsa_candidate(run: _Run, candidate: Config) -> None:
    """The checks that need the whole candidate: readiness and do-not-break (contract 2.6)."""
    context = run.context
    stored = frozenset(run.secrets_after)
    before = tsa_server(run.old("tsa.provider"), run.old("tsa.url"), run.old("tsa.port"))
    after = tsa_server(candidate.tsa.provider, candidate.tsa.url, candidate.tsa.port)
    if after == "production" and before != "production":
        missing = production_missing(
            candidate,
            anchors=context.anchors,
            stored=stored,
            roots=len(context.installed_roots | run.roots_added),
        )
        if missing:
            run.error("tsa_not_ready", "tsa.provider", missing=list(missing))
    if context.preflight is not None and not context.preflight(run.current, run.stored):
        causes = list(context.preflight(candidate, stored))
        if causes:
            run.error("tsa_would_break", "tsa.provider", causes=causes)
    credentials = any(name in run.secret_changes for name in SECTION_SECRETS["tsa"])
    server = any(run.changed(p) for p in ("tsa.provider", "tsa.url", "tsa.port"))
    if (credentials or server) and candidate.tsa.provider == "zamane":
        if not {"kamusm.customer", "kamusm.password"} <= stored:
            return
        if executables_trusted(candidate, context.anchors):
            run.probe("credit")
        else:
            run.warn("probe_skipped", "tsa.zamane_jar")  # no client to ask with yet


def _smtp(run: _Run) -> None:
    smtp = run.doc["smtp"]
    before = run.before["smtp"]
    checks: list[tuple[str, Callable[[Any], bool]]] = [
        ("enabled", lambda v: isinstance(v, bool)),
        ("host", lambda v: v is None or (isinstance(v, str) and host_ok(v))),
        ("port", lambda v: _integer(v, 1, 65535)),
        ("security", lambda v: v in ("starttls", "ssl", "none")),
        (
            "username",
            lambda v: v is None or (isinstance(v, str) and 1 <= len(v) <= 256 and _text_ok(v)),
        ),
        (
            "from_address",
            lambda v: v is None or (isinstance(v, str) and len(v) <= 254 and bool(_EMAIL.match(v))),
        ),
        ("timeout_seconds", lambda v: _integer(v, 1, 300)),
    ]
    for key, valid in checks:
        if run.changed(f"smtp.{key}") and not valid(smtp[key]):
            run.error("config_invalid", f"smtp.{key}")
    changed = bool(
        [p for p in run.set_paths if p.startswith("smtp.")]
        or run.derived
        or "smtp.password" in run.secret_changes
    )
    if not changed:
        return
    run.reauth = True
    run.effects.add("reload.worker")
    if run.errors:
        return
    if smtp["enabled"]:
        for key in ("host", "from_address"):
            if smtp[key] is None:
                run.error("config_invalid", f"smtp.{key}")
    has_password = "smtp.password" in run.secrets_after
    new_password = run.secret_changes.get("smtp.password") is not None
    # The CA file decides which server may pose as the destination: part of the endpoint.
    endpoint = any(
        smtp[key] != before[key] for key in ("host", "port", "security", "username", "ca_file")
    )
    if smtp["username"]:
        if not has_password:
            run.error("smtp_password_required", "smtp.password")
        elif endpoint and not new_password:
            run.error("secret_rebind_required", "smtp.password")
        if smtp["security"] == "none" and str(smtp["host"]).lower() not in LOCAL_SMTP_HOSTS:
            run.error("smtp_plaintext_auth_refused", "smtp.security")
    elif smtp["security"] == "none" and smtp["enabled"]:
        run.warn("smtp_plaintext", "smtp.security")
    if before["enabled"] and not smtp["enabled"]:
        run.warn("smtp_disabled", "smtp.enabled")
        run.alert("medium")
    if run.context.strict_outbound and any(
        smtp[key] != before[key] for key in ("enabled", "host", "port")
    ):
        run.effects.add("system.firewall")
    if smtp["enabled"] and (
        not before["enabled"]
        or endpoint
        or new_password
        or smtp["from_address"] != before["from_address"]
        or smtp["ca_file"] != before["ca_file"]
    ):
        run.probe("smtp")


def _replica(run: _Run) -> None:
    replica = run.doc["replica"]
    before = run.before["replica"]
    if run.changed("replica.enabled") and not isinstance(replica["enabled"], bool):
        run.error("config_invalid", "replica.enabled")
    target = replica["target"]
    if run.changed("replica.target") and not (
        target is None or (isinstance(target, str) and unc_ok(target))
    ):
        run.error("replica_target_invalid", "replica.target")
    user = replica["user"]
    if run.changed("replica.user") and not (
        user is None or (isinstance(user, str) and replica_user_ok(user))
    ):
        run.error("replica_user_invalid", "replica.user")
    if not (run.set_paths or "replica.password" in run.secret_changes):
        return
    run.reauth = True
    run.effects |= {"task.next_run:Replica", "reload.worker"}
    if run.errors:
        return
    if replica["enabled"] and target is None:
        run.error("config_invalid", "replica.target")
        return
    has_password = "replica.password" in run.secrets_after
    new_password = run.secret_changes.get("replica.password") is not None
    host_changed = unc_host(target) != unc_host(before["target"])
    if user:
        if not has_password:
            run.error("replica_password_required", "replica.password")
        elif (host_changed or user != before["user"]) and not new_password:
            run.error("secret_rebind_required", "replica.password")
    if before["enabled"] and (not replica["enabled"] or target != before["target"]):
        run.guarded = True
        run.alert("high")
    if run.context.strict_outbound and (host_changed or replica["enabled"] != before["enabled"]):
        run.effects.add("system.firewall")
    renewed = target != before["target"] or user != before["user"] or new_password
    if replica["enabled"] and (not before["enabled"] or renewed):
        # Without a user the agent would reach the share as the computer account, which is
        # neither the identity the Replica task writes with (LOCAL SERVICE) nor one to offer
        # any host: the Requests task tests it instead, as the Replica task would.
        if not user or (run.context.strict_outbound and host_changed):
            run.warn("probe_skipped", "replica.target")
            run.followups.append("replica_test")
        else:
            run.probe("replica", unc_host(target))


def _retention(run: _Run) -> None:
    retention = run.doc["retention"]
    before = run.before["retention"]
    if run.changed("retention.enabled") and not isinstance(retention["enabled"], bool):
        run.error("config_invalid", "retention.enabled")
    days = retention["days"]
    if run.changed("retention.days"):
        if isinstance(days, int) and not isinstance(days, bool) and days < MIN_RETENTION_DAYS:
            run.error("retention_below_minimum", "retention.days")
        elif not _integer(days, MIN_RETENTION_DAYS, 36_500):
            run.error("config_invalid", "retention.days")
    if not run.set_paths:
        return
    run.reauth = True
    run.effects |= {"task.next_run:Retention", "reload.api"}
    if run.errors:
        return
    if retention["enabled"] and (not before["enabled"] or days < before["days"]):
        run.guarded = True
        run.alert("high")


def _backup(run: _Run) -> None:
    backup = run.doc["backup"]
    before = run.before["backup"]
    ranges = {
        "keep_daily": (1, 3660),
        "keep_weekly": (0, 520),
        "keep_monthly": (0, 1200),
        "min_free_gb": (0, 100_000),
    }
    if run.changed("backup.enabled") and not isinstance(backup["enabled"], bool):
        run.error("config_invalid", "backup.enabled")
    for key, (low, high) in ranges.items():
        if run.changed(f"backup.{key}") and not _integer(backup[key], low, high):
            run.error("config_invalid", f"backup.{key}")
    if not run.set_paths:
        return
    run.reauth = True
    run.effects.add("task.next_run:Backup")
    if before["enabled"] and backup["enabled"] is False:
        run.warn("backup_disabled", "backup.enabled")
        run.alert("medium")


def _sources(run: _Run) -> None:
    if run.sources is not None:
        run.effects |= {"reload.collector", "reload.ingest", "reload.api", "system.firewall"}


_RULES: Final[Mapping[Section, Callable[[_Run], None]]] = MappingProxyType(
    {
        "general": _general,
        "tsa": _tsa,
        "smtp": _smtp,
        "replica": _replica,
        "retention": _retention,
        "backup": _backup,
        "sources": _sources,
    }
)


def _run(
    section: Section,
    current: Config,
    ops: Sequence[Op],
    context: RuleContext,
    *,
    test_to: str | None,
    probe: bool,
) -> SectionCheck:
    run = _Run(section, current, context)
    for op in ops:
        run.apply(op)
    _RULES[section](run)
    options: dict[str, JsonValue] = {"test_to": test_to} if test_to else {}
    if probe and any(item.kind == "smtp" for item in run.probes) and not test_to:
        run.error("smtp_test_recipient_required", "test_to")
    check = run.result(ops, options)
    if check.ok and check.candidate is not None and section == "tsa":
        _tsa_candidate(run, check.candidate)
        if run.errors:
            return replace(
                check,
                errors=tuple(run.errors),
                warnings=tuple(run.warnings),
                edits=(),
                secret_changes={},
                installs=(),
                stage_jar=None,
                candidate=None,
            )
        return replace(check, warnings=tuple(run.warnings), probes=tuple(run.probes))
    return check


def check_request(request: AdminRequest, current: Config, context: RuleContext) -> SectionCheck:
    """The agent's authoritative check of a ``config.apply`` request against the configuration
    in force (the request's ``expect`` values are checked by :func:`conflicts` first)."""
    if request.section is None:
        raise ValueError(f"{request.kind} has no section to check")
    return _run(
        request.section,
        current,
        request.ops,
        context,
        test_to=request.options.test_to,
        probe=request.probe,
    )


# -- the API: a form body to ops -------------------------------------------------------------------


class _Body:
    """Builds the ops of a form body (absent keys stay as they are) and checks what only a
    person types: confirmations, the trial recipient, the expected SHA-256 of a client."""

    def __init__(self, section: Section, current: Config, context: RuleContext) -> None:
        self.section = section
        self.current = current
        self.context = context
        self.stored = frozenset(context.stored_secrets)
        self.ops: list[Op] = []
        self.errors: list[Problem] = []
        self.test_to: str | None = None

    def error(self, code: str, path: str | None = None) -> None:
        self.errors.append(Problem(code, path))

    def set(self, path: str, value: Any) -> None:
        expect = current_value(self.current, path)
        if value != expect or type(value) is not type(expect):  # 1 is no "true", 800.0 no 800
            self.ops.append(SetOp(op="set", path=path, expect=expect, value=value))

    def secret(self, name: BrowserSecret, value: Any) -> None:
        if not isinstance(value, str):
            self.error("secret_invalid", name)
            return
        try:
            check_secret_value(name, value)
        except SecretValueError:
            self.error("secret_invalid", name)
            return
        token = self.context.encrypt(value) if self.context.encrypt is not None else UNENCRYPTED
        self.ops.append(
            SecretSetOp(
                op="secret_set", name=name, expect=secret_state(name, self.stored), token=token
            )
        )

    def delete(self, name: BrowserSecret) -> None:
        if name in self.stored and not any(
            isinstance(op, SecretSetOp | SecretDeleteOp) and op.name == name for op in self.ops
        ):
            self.ops.append(SecretDeleteOp(op="secret_delete", name=name, expect="set"))

    def upload(self, value: Any, kind: UploadKind) -> UploadInfo | None:
        upload = value.get("upload_id") if isinstance(value, Mapping) else None
        info = self.context.uploads.get(upload) if isinstance(upload, str) else None
        if info is None or info.kind != kind:
            self.error("upload_not_found")
            return None
        return info


def _optional_text(value: Any) -> Any:
    """A text field: surrounding whitespace dropped, empty as None (the key is removed)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _body_general(body: _Body, data: Mapping[str, Any]) -> None:
    if "site_name" in data:
        site = data["site_name"]
        body.set("site_name", site.strip() if isinstance(site, str) else site)
    clock = data.get("clock")
    if clock is None:
        return
    if not isinstance(clock, Mapping):
        body.error("config_invalid", "clock")
        return
    for key in ("ntp_server", "fallback_server"):
        if key in clock:
            value = clock[key]
            body.set(f"clock.{key}", value.strip() if isinstance(value, str) else value)
    if "alert_offset_ms" in clock:
        body.set("clock.alert_offset_ms", clock["alert_offset_ms"])


def _body_tsa(body: _Body, data: Mapping[str, Any]) -> None:
    current = body.current.tsa
    before = tsa_server(current.provider, current.url, current.port)
    if "server" in data:
        server = data["server"]
        if server == "mock":
            body.error("tsa_mock_refused", "tsa.provider")
        elif server not in TSA_SERVERS:
            body.error("tsa_server_invalid", "tsa.provider")
        else:
            provider, url, port = TSA_SERVERS[server]
            body.set("tsa.provider", provider)
            body.set("tsa.url", url)
            body.set("tsa.port", port)
            if before == "production" and server == "test" and data.get("confirm") is not True:
                body.error("tsa_confirm_required", "tsa.provider")
    for key in ("credit_alert_threshold", "timeout_seconds"):
        if key in data:
            body.set(f"tsa.{key}", data[key])
    if "java" in data:
        java = data["java"]
        body.set("tsa.java", [java] if isinstance(java, str) else java)
    if data.get("clear_credentials") is True:
        if data.get("customer") or data.get("password"):
            body.error("config_invalid", "kamusm.customer")
        body.delete("kamusm.customer")
        body.delete("kamusm.password")
    else:
        names: tuple[tuple[str, BrowserSecret], ...] = (
            ("customer", "kamusm.customer"),
            ("password", "kamusm.password"),
        )
        for key, name in names:
            if data.get(key) is not None:
                body.secret(name, data[key])
    root = data.get("root")
    if root is not None:
        info = body.upload(root, "kamusm_root")
        legal = isinstance(root, Mapping) and root.get("legal") is True
        if legal and root.get("confirmed") is not True:
            body.error("legal_root_confirm_required", "tsa.legal_root_fingerprints")
        if info is not None:
            body.ops.append(
                RootAddOp(
                    op="root_add", upload=info.id, sha256=info.sha256, size=info.size, legal=legal
                )
            )
    legal_root = data.get("legal_root")
    if legal_root is not None:
        sha = legal_root.get("sha256") if isinstance(legal_root, Mapping) else None
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha.lower()):
            body.error("root_not_installed", "tsa.legal_root_fingerprints")
        else:
            if legal_root.get("confirmed") is not True:
                body.error("legal_root_confirm_required", "tsa.legal_root_fingerprints")
            body.ops.append(LegalRootAddOp(op="legal_root_add", sha256=sha.lower()))
    client = data.get("zamane")
    if client is not None:
        _body_zamane(body, client)


def _body_zamane(body: _Body, client: Any) -> None:
    info = body.upload(client, "zamane_client")
    if info is None:
        return
    inspection = info.zamane
    if inspection is None or not inspection.ok or inspection.jar_sha256 is None:
        body.error("zamane_archive_invalid", "tsa.zamane_jar")
        return
    if inspection.release is None:
        expected = client.get("expected_sha256") if isinstance(client, Mapping) else None
        if not isinstance(expected, str) or not _SHA256.fullmatch(expected.strip().lower()):
            body.error("zamane_sha256_required", "tsa.zamane_jar")
            return
        if expected.strip().lower() not in (info.sha256, inspection.jar_sha256):
            body.error("zamane_sha256_mismatch", "tsa.zamane_jar")
            return
    body.ops.append(
        ZamaneStageOp(
            op="zamane_stage",
            upload=info.id,
            sha256=info.sha256,
            size=info.size,
            jar_sha256=inspection.jar_sha256,
            release=inspection.release,
        )
    )


def _body_smtp(body: _Body, data: Mapping[str, Any]) -> None:
    for key in ("enabled", "port", "security", "timeout_seconds"):
        if key in data:
            body.set(f"smtp.{key}", data[key])
    for key in ("host", "username", "from_address"):
        if key in data:
            body.set(f"smtp.{key}", _optional_text(data[key]))
    if data.get("password") is not None:
        body.secret("smtp.password", data["password"])
    username = (
        _optional_text(data["username"]) if "username" in data else body.current.smtp.username
    )
    if data.get("clear_password") is True or username is None:
        body.delete("smtp.password")
    if data.get("ca") is not None:
        info = body.upload(data["ca"], "smtp_ca")
        if info is not None:
            body.ops.append(
                SmtpCaSetOp(op="smtp_ca_set", upload=info.id, sha256=info.sha256, size=info.size)
            )
    elif data.get("clear_ca") is True and body.current.smtp.ca_file is not None:
        body.ops.append(
            SmtpCaClearOp(op="smtp_ca_clear", expect=current_value(body.current, "smtp.ca_file"))
        )
    test_to = data.get("test_to")
    if test_to is not None:
        if not isinstance(test_to, str) or _EMAIL.match(test_to) is None:
            body.error("config_invalid", "test_to")
        elif body.context.test_recipients is not None and test_to.lower() not in {
            item.lower() for item in body.context.test_recipients
        }:
            body.error("test_email_recipient_not_allowed", "test_to")
        else:
            body.test_to = test_to


def _body_replica(body: _Body, data: Mapping[str, Any]) -> None:
    if "enabled" in data:
        body.set("replica.enabled", data["enabled"])
    for key in ("target", "user"):
        if key in data:
            body.set(f"replica.{key}", _optional_text(data[key]))
    if data.get("password") is not None:
        body.secret("replica.password", data["password"])
    user = _optional_text(data["user"]) if "user" in data else body.current.replica.user
    if data.get("clear_password") is True or user is None:
        body.delete("replica.password")


def _body_retention(body: _Body, data: Mapping[str, Any]) -> None:
    for key in ("enabled", "days"):
        if key in data:
            body.set(f"retention.{key}", data[key])


def _body_backup(body: _Body, data: Mapping[str, Any]) -> None:
    for key in ("enabled", "keep_daily", "keep_weekly", "keep_monthly", "min_free_gb"):
        if key in data:
            body.set(f"backup.{key}", data[key])


def _body_sources(body: _Body, data: Mapping[str, Any]) -> None:
    current = {source.name: source for source in body.current.sources}
    name = data.get("name")
    if name is not None and (not isinstance(name, str) or name not in current):
        body.error("source_not_found", "sources")
        return
    if data.get("remove") is True:
        if name is None:
            body.error("source_not_found", "sources")
            return
        if data.get("confirm") != name:
            body.error("source_confirm_mismatch", f"sources.{name}")
        body.ops.append(SourceRemoveOp(op="source_remove", name=name, expect=current[name]))
        return
    raw = data.get("source")
    if not isinstance(raw, Mapping):
        body.error("source_invalid", "sources")
        return
    fields = dict(raw)
    if name is not None:
        fields.setdefault("name", name)  # an edit takes the name from the path
    try:
        source = SourceConfig.model_validate(fields)
    except ValidationError as exc:
        label = fields.get("name")
        known = isinstance(label, str) and _SOURCE_NAME.fullmatch(label) is not None
        for error in exc.errors(include_input=False, include_url=False):
            key = str(error["loc"][0]) if error["loc"] else ""
            body.error("source_invalid", f"sources.{label}.{key}" if known and key else "sources")
        return
    if name is None:
        body.ops.append(SourceAddOp(op="source_add", source=source))
    elif source.name != name:
        body.error("source_rename_not_allowed", f"sources.{name}.name")
    elif source != current[name]:
        body.ops.append(
            SourceUpdateOp(op="source_update", name=name, expect=current[name], source=source)
        )


_BODIES: Final[Mapping[Section, Callable[[_Body, Mapping[str, Any]], None]]] = MappingProxyType(
    {
        "general": _body_general,
        "tsa": _body_tsa,
        "smtp": _body_smtp,
        "replica": _body_replica,
        "retention": _body_retention,
        "backup": _body_backup,
        "sources": _body_sources,
    }
)


def validate_section(
    section: Section, current: Config, body: Mapping[str, Any], context: RuleContext
) -> SectionCheck:
    """The API's check of a form body (contract 2.11.1; ``etag`` is the API's, ``probe``
    defaults to true). Absent keys stay as they are. Body keys per section:

    * ``general``: ``site_name``, ``clock`` {``ntp_server``, ``fallback_server``,
      ``alert_offset_ms``};
    * ``tsa``: ``server`` (``test``/``production``), ``credit_alert_threshold``,
      ``timeout_seconds``, ``java``, ``customer``, ``password``, ``clear_credentials``,
      ``confirm`` (production to test), ``root`` {``upload_id``, ``legal``, ``confirmed``},
      ``legal_root`` {``sha256``, ``confirmed``}, ``zamane`` {``upload_id``, ``expected_sha256``};
    * ``smtp``: ``enabled``, ``host``, ``port``, ``security``, ``username``, ``from_address``,
      ``timeout_seconds``, ``password``, ``clear_password``, ``test_to``, ``ca`` {``upload_id``},
      ``clear_ca``;
    * ``replica``: ``enabled``, ``target``, ``user``, ``password``, ``clear_password``;
    * ``retention``: ``enabled``, ``days``, ``confirm_delete_days``;
    * ``backup``: ``enabled``, ``keep_daily``, ``keep_weekly``, ``keep_monthly``, ``min_free_gb``;
    * ``sources``: ``source`` (add), ``name`` + ``source`` (edit), ``name`` + ``remove: true`` +
      ``confirm`` (remove).

    A check without ops (nothing differs) is ``ok`` with empty ``ops``: there is nothing to
    queue."""
    builder = _Body(section, current, context)
    _BODIES[section](builder, body)
    probe = body.get("probe", True) is not False
    check = _run(section, current, builder.ops, context, test_to=builder.test_to, probe=probe)
    errors = list(builder.errors)
    if section == "retention" and check.ok and check.guarded and check.candidate is not None:
        preview = context.retention_preview
        confirmed = body.get("confirm_delete_days")
        if preview is not None and (
            not _integer(confirmed, 0, 10**7)
            or confirmed != preview(check.candidate).days_to_delete
        ):
            errors.append(Problem("retention_confirm_mismatch", "retention.days"))
    # A field the form check already refused is named once (``test_to``: not allowed, then
    # not "required").
    refused = {problem.field for problem in errors if problem.field is not None}
    errors += [
        problem
        for problem in check.errors
        if problem not in errors and (problem.field is None or problem.field not in refused)
    ]
    if errors:
        return replace(
            check,
            errors=tuple(errors),
            edits=(),
            secret_changes={},
            installs=(),
            stage_jar=None,
            candidate=None,
        )
    return check


def inspect_upload(
    upload_id: str, kind: UploadKind, path: Path, *, now: datetime | None = None
) -> UploadInfo:
    """The upload at ``path`` checked for its kind (the API's preview, the agent's claimed copy)."""
    size = path.stat().st_size
    if kind == "zamane_client":
        client = inspect_client(path)
        return UploadInfo(upload_id, kind, client.sha256, size, zamane=client)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if kind == "kamusm_root":
        return UploadInfo(upload_id, kind, digest, size, root=inspect_root(data, now=now))
    bundle = inspect_ca_bundle(data)
    return UploadInfo(upload_id, kind, digest, size, bundle=bundle, data=data)
