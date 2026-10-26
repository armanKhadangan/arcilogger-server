"""The administration channel's contract (ADR-0014): names, limits, paths and file formats.

Shared by the API (writes requests, reads the status), the administration agent ``ArciAdmin``
(processes requests), the LOCAL SERVICE tasks (run task requests), the worker (imports the
journals) and the installer (creates the folders). Everything here is an interface between
separately running programs: a change of a format is a new ``schema`` version.

The folder ``Config.admin_dir`` (``C:\\ProgramData\\ARCILOGGER\\admin``, :class:`AdminLayout`)::

    policy.json                           AdminPolicy (installer; only administrators write)
    keys/agent-ed25519.key, api-ed25519.key, keys.json      KeysFile (admin.keys)
    inbox/<id>.req                        signed AdminRequest (admin.envelope)
    inbox/uploads/<id>.bin, <id>.json     an uploaded file and its UploadMeta
    work/<id>/  pending/<id>.json  state/txn.json  state/ledger.json      the agent's own
    versions/<n>/  versions/index.json    VersionIndex
    status/agent.json                     AgentStatus (heartbeat)
    status/<id>.json                      RequestStatus
    status/secret-meta.json               SecretMeta
    status/alerts.jsonl                   alert lines (arcilogger_core.alerts format)
    journal/<YYYY-MM-DD>.jsonl            JournalLine
    system/status.json, jars.json         SystemStatus, JarsFile (ApplySystem)
    system/journal.jsonl, alerts.jsonl    SystemJournalLine, alert lines (ApplySystem)

``system-plan`` prints a :class:`SystemPlan` on its standard output (ApplySystem reads it), and
below the data root (:class:`arcilogger_core.layout.Layout`)::

    state/requests/seal|tasks/<id>.json   signed TaskRequest
    state/requests/results/<id>.json      TaskResult
    state/requests/seen/<id>              the tasks' ledger

Every file is UTF-8 JSON (JSON Lines for the journals), written atomically and read with
:func:`parse_document`: duplicate keys, floats, ``NaN`` and missing ``schema`` are refused,
then pydantic validates strictly with ``extra="forbid"``. Times are ISO 8601 with an offset,
ids 32 lower-case hex digits, hashes 64. Status files, journals and results carry codes only;
the Turkish and English texts are in :mod:`arcilogger_core.admin.messages`. No file holds a
secret value or a hash of one: secrets travel as Fernet tokens under ``secret.key`` and are
reported as ``set`` / ``unset``.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, get_args

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from arcilogger_core.admin.messages import MESSAGES, WARNINGS
from arcilogger_core.config import EMAIL_ADDRESS, SourceConfig
from arcilogger_core.layout import RequestQueue

# -- schemas ------------------------------------------------------------------------------------

POLICY_SCHEMA: Final = "arcilogger.admin-policy/1"
KEYS_SCHEMA: Final = "arcilogger.admin-keys/1"
#: Also the domain prefix of the signature (``b"arcilogger.admin-request/1\n"``).
REQUEST_SCHEMA: Final = "arcilogger.admin-request/1"
UPLOAD_SCHEMA: Final = "arcilogger.admin-upload/1"
#: Also the domain prefix of the signature (``b"arcilogger.task-request/1\n"``).
TASK_REQUEST_SCHEMA: Final = "arcilogger.task-request/1"
TASK_RESULT_SCHEMA: Final = "arcilogger.task-result/1"
AGENT_STATUS_SCHEMA: Final = "arcilogger.admin-agent/1"
SECRET_META_SCHEMA: Final = "arcilogger.admin-secret-meta/1"  # noqa: S105 - a schema name
VERSIONS_SCHEMA: Final = "arcilogger.admin-versions/1"
SYSTEM_STATUS_SCHEMA: Final = "arcilogger.admin-system/1"
JARS_SCHEMA: Final = "arcilogger.admin-jars/1"
SYSTEM_PLAN_SCHEMA: Final = "arcilogger.system-plan/1"

# -- names --------------------------------------------------------------------------------------

#: What an administration request asks for (``options.target``: config.revert / config.cancel,
#: ``options.day``: action.seal).
RequestKind = Literal[
    "config.apply",
    "config.revert",
    "config.cancel",
    "action.seal",
    "action.credit",
    "action.tsa_test",
    "action.replica_test",
]
#: Non-secret actions the worker runs from the database queue (``admin_requests``,
#: ``channel = 'worker'``); they never become a request file.
WorkerKind = Literal["worker.test_email", "worker.lease_rebuild"]
Channel = Literal["agent", "worker"]
#: The sections of ``arcilogger.toml`` the browser may change (contract 2.3).
Section = Literal["general", "tsa", "smtp", "replica", "retention", "backup", "sources"]
OpKind = Literal[
    "set",
    "secret_set",
    "secret_delete",
    "source_add",
    "source_update",
    "source_remove",
    "root_add",
    "legal_root_add",
    "zamane_stage",
    "smtp_ca_set",
    "smtp_ca_clear",
]
#: The stored secrets the browser may set or delete; every other name is ``schema_invalid``.
BrowserSecret = Literal["kamusm.customer", "kamusm.password", "smtp.password", "replica.password"]
UploadKind = Literal["kamusm_root", "zamane_client", "smtp_ca"]
TaskKind = Literal["seal", "credit", "tsa_test", "replica_test"]
KeyRole = Literal["api", "agent"]

#: Request states (labels: ``messages.STATE_LABELS``). ``waiting_for_agent``, ``expired``,
#: ``active`` and ``partially_active`` are decided by the API, the others by the agent.
State = Literal[
    "queued",
    "waiting_for_agent",
    "expired",
    "received",
    "rejected",
    "conflict",
    "probing",
    "probe_failed",
    "pending",
    "cancelled",
    "applying",
    "applied",
    "active",
    "partially_active",
    "failed",
    "needs_attention",
    "dispatched",
    "running",
    "succeeded",
    "timed_out",
]
Step = Literal[
    "received",
    "validate",
    "probe",
    "hold",
    "backup",
    "write_secrets",
    "write_config",
    "install_files",
    "verify",
    "reload.collector",
    "reload.ingest",
    "reload.worker",
    "reload.api",
    "system.firewall",
    "system.time",
    "jar.promotion",
    "dispatch",
    "task",
    "journal",
    "rollback",
]
StepState = Literal["pending", "running", "done", "skipped", "warning", "failed"]
JournalState = Literal[
    "pending",
    "applied",
    "failed",
    "needs_attention",
    "rejected",
    "cancelled",
    "recovered",
    "action",
]

REQUEST_KINDS: Final[tuple[RequestKind, ...]] = get_args(RequestKind)
#: Every ``admin_requests.kind`` (the database's check constraint).
ADMIN_REQUEST_KINDS: Final[tuple[str, ...]] = (*REQUEST_KINDS, *get_args(WorkerKind))
SECTIONS: Final[tuple[Section, ...]] = get_args(Section)
OP_KINDS: Final[tuple[OpKind, ...]] = get_args(OpKind)
BROWSER_SECRETS: Final[tuple[BrowserSecret, ...]] = get_args(BrowserSecret)
TASK_KINDS: Final[tuple[TaskKind, ...]] = get_args(TaskKind)
STATES: Final[tuple[State, ...]] = get_args(State)
TERMINAL_STATES: Final[frozenset[State]] = frozenset(
    {
        "expired",
        "rejected",
        "conflict",
        "probe_failed",
        "cancelled",
        "active",
        "partially_active",
        "failed",
        "needs_attention",
        "succeeded",
        "timed_out",
    }
)
STEPS: Final[tuple[Step, ...]] = get_args(Step)
STEP_STATES: Final[tuple[StepState, ...]] = get_args(StepState)
JOURNAL_STATES: Final[tuple[JournalState, ...]] = get_args(JournalState)

#: The task an action request is dispatched to.
ACTION_TASKS: Final[Mapping[RequestKind, TaskKind]] = MappingProxyType(
    {
        "action.seal": "seal",
        "action.credit": "credit",
        "action.tsa_test": "tsa_test",
        "action.replica_test": "replica_test",
    }
)


def task_queue(kind: TaskKind) -> RequestQueue:
    """The folder a task request goes to: the Seal task takes ``seal``, the Requests task the
    rest (``Layout.request_dir``)."""
    return "seal" if kind == "seal" else "tasks"


# -- Windows Application log (source ARCILOGGER-Admin, registered by the installer) -------------

EVENT_SOURCE: Final = "ARCILOGGER-Admin"


class Event(IntEnum):
    """Event IDs. An event only wakes a task (any local process can write one); the signed
    request file is the authority."""

    AGENT_STARTED = 4100
    SYSTEM_SYNC_REQUESTED = 4101  # wakes ApplySystem; carries no data
    CHANGE_APPLIED = 4102
    CHANGE_FAILED = 4103  # rolled back or manual action needed
    CHANGE_HELD = 4104  # a held change, or a Zamane client staged (effective time)
    HOLD_CANCELLED = 4105
    SYSTEM_SYNC_RESULT = 4106  # ApplySystem
    JAR_PROMOTED = 4107  # ApplySystem
    SEAL_REQUESTED = 4110  # wakes the Seal task
    TASK_REQUESTED = 4111  # wakes the Requests task
    REQUEST_REJECTED = 4120
    SECRET_CHANGED = 4121  # the name only
    AGENT_STOPPING = 4199


# -- limits -------------------------------------------------------------------------------------

#: An administration request file (envelope) at most 1 MiB; a task request at most 64 KiB.
MAX_REQUEST_BYTES: Final = 1024 * 1024
MAX_TASK_REQUEST_BYTES: Final = 64 * 1024
MAX_OPS: Final = 200
#: Every string of a signed payload (keys included), in UTF-8 bytes.
MAX_STRING_BYTES: Final = 4096
#: ``issued_at`` may lie at most this far ahead of the reader's clock.
CLOCK_SKEW: Final = timedelta(minutes=5)
#: ``expires_at - issued_at`` at most: administration requests, seal requests, other tasks.
MAX_REQUEST_VALIDITY: Final = timedelta(minutes=10)
MAX_SEAL_REQUEST_VALIDITY: Final = timedelta(hours=6)
MAX_TASK_REQUEST_VALIDITY: Final = timedelta(minutes=30)
#: The re-authentication of a change that needs it is at most this old at ``issued_at``.
REAUTH_WINDOW: Final = timedelta(minutes=5)
#: Upload size limits (contract 2.4.4); an upload is usable for 30 minutes.
UPLOAD_LIMITS: Final[Mapping[UploadKind, int]] = MappingProxyType(
    {"kamusm_root": 64 * 1024, "smtp_ca": 256 * 1024, "zamane_client": 25 * 1024 * 1024}
)
UPLOAD_LIFETIME: Final = timedelta(minutes=30)
#: Inbox quota of the API (429 ``admin_queue_full``).
INBOX_MAX_REQUESTS: Final = 20
INBOX_MAX_BYTES: Final = 100 * 1024 * 1024
#: Inbox ``.tmp`` files older than this are deleted by the agent.
TMP_MAX_AGE: Final = timedelta(minutes=10)
#: The agent writes ``status/agent.json`` this often; older than ``HEARTBEAT_STALE`` it is
#: stale, older than ``HEARTBEAT_MISSING`` (or absent) the agent counts as missing.
HEARTBEAT_INTERVAL: Final = timedelta(seconds=10)
HEARTBEAT_STALE: Final = timedelta(seconds=30)
HEARTBEAT_MISSING: Final = timedelta(seconds=120)
#: A queued request becomes ``waiting_for_agent`` after this; an unclaimed one ``expired`` this
#: long after its ``expires_at``.
QUEUED_WAIT: Final = timedelta(seconds=60)
EXPIRED_GRACE: Final = timedelta(minutes=2)
#: A service counts as running while its status file is younger than this; an ``applied``
#: request becomes ``partially_active`` when a running service has not taken it by then.
SERVICE_FRESH: Final = timedelta(minutes=2)
#: A dispatched task request without a result after this: ``timed_out`` (``task_timeout``).
TASK_TIMEOUT: Final = timedelta(minutes=15)
#: ApplySystem must answer the agent's start-up event (4101) within this.
SYSTEM_TASK_TIMEOUT: Final = timedelta(minutes=5)
#: Retention of the agent's and the tasks' files.
LEDGER_DAYS: Final = 30
STATUS_DAYS: Final = 30
JOURNAL_DAYS: Final = 400
SEEN_DAYS: Final = 7
RESULT_DAYS: Final = 7
KEEP_CONFIG_VERSIONS: Final = 20
KEEP_SECRETS_VERSIONS: Final = 5

HeartbeatState = Literal["ok", "stale", "missing"]


def heartbeat_state(heartbeat_at: datetime | None, now: datetime) -> HeartbeatState:
    """How the API and the worker judge the agent's last heartbeat (contract 2.5.1)."""
    if heartbeat_at is None:
        return "missing"
    age = now - heartbeat_at
    if age > HEARTBEAT_MISSING:
        return "missing"
    return "stale" if age > HEARTBEAT_STALE else "ok"


# -- identifiers and encodings ------------------------------------------------------------------

_HEX32: Final = re.compile(r"[0-9a-f]{32}")
_B64URL: Final = re.compile(r"[A-Za-z0-9_-]*")

#: A request, task request, upload, journal line or installation id.
Id = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
KeyId = Annotated[str, StringConstraints(pattern=r"^(api|agent)-[0-9a-f]{8}$")]
#: A dotted configuration path such as ``smtp.host``, ``site_name`` or ``sources.switch-2.address``
#: (a source by its name).
ConfigPath = Annotated[
    str, StringConstraints(pattern=r"^[a-z_]+(\.[a-z0-9][a-z0-9_-]*)*$", max_length=200)
]
SourceName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
#: A ``secrets.json`` value: a Fernet token under ``secret.key``.
FernetToken = Annotated[str, StringConstraints(pattern=r"^gAAAAA[A-Za-z0-9_-]+={0,2}$")]
#: The name of a stored secret (``secretstore``), e.g. ``smtp.password``.
SecretName = Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
_Count = Annotated[int, Field(ge=0)]


def new_id() -> str:
    """A new random id (32 lower-case hex digits)."""
    return secrets.token_hex(16)


def check_id(value: str) -> str:
    """``value`` when it is an id; ``ValueError`` otherwise (paths are built from ids)."""
    if not isinstance(value, str) or not _HEX32.fullmatch(value):
        raise ValueError(f"not an id of 32 lower-case hex digits: {value!r}")
    return value


def b64url_encode(data: bytes) -> str:
    """base64url without padding (RFC 4648 section 5): payloads, signatures, public keys."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str, length: int | None = None) -> bytes:
    """Decode base64url without padding strictly: the text must re-encode to itself (and give
    ``length`` bytes when one is given); ``ValueError`` otherwise."""
    if not isinstance(text, str) or not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise ValueError("not base64url without padding")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64url_encode(data) != text:
        raise ValueError("non-canonical base64url text")
    if length is not None and len(data) != length:
        raise ValueError(f"base64url text must encode {length} bytes, not {len(data)}")
    return data


def key_id_for(role: KeyRole, public_key: bytes) -> str:
    """``<role>-`` and the first 8 hex digits of the SHA-256 of the raw public key."""
    return f"{role}-{hashlib.sha256(public_key).hexdigest()[:8]}"


# -- strict documents ---------------------------------------------------------------------------


class DocumentError(ValueError):
    """A file of the channel is not a valid document of its kind."""


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _refuse_number(text: str) -> Any:
    raise ValueError(f"number {text!r} is not allowed (no floats, NaN or Infinity)")


def parse_json_strict(data: bytes) -> Any:
    """UTF-8 JSON (no byte order mark), refusing duplicate keys, floats, ``NaN`` and
    ``Infinity``; ``ValueError`` (``UnicodeDecodeError``, ``JSONDecodeError``) otherwise."""
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_refuse_duplicates,
        parse_float=_refuse_number,
        parse_constant=_refuse_number,
    )


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


def parse_document[M: _Model](model: type[M], data: bytes) -> M:
    """One file (or JSON line) of the channel as ``model``; :class:`DocumentError` for anything
    else. A model with a ``schema`` must find it stated in the document, not defaulted."""
    try:
        document = parse_json_strict(data)
        if "schema_" in model.model_fields and (
            not isinstance(document, dict) or "schema" not in document
        ):
            raise ValueError("the document does not state its schema")
        return model.model_validate_json(data, strict=True)
    except (ValueError, RecursionError) as exc:  # pydantic's ValidationError is a ValueError
        raise DocumentError(f"not a valid {model.__name__}: {exc}") from exc


def dump_document(model: _Model) -> bytes:
    """The bytes of a status or state file (indented, keys sorted, final newline)."""
    text = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def dump_line(model: _Model) -> bytes:
    """One line of a JSON Lines journal (keys sorted, final newline)."""
    text = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return (text + "\n").encode("utf-8")


# -- 2.4.1 admin\policy.json ----------------------------------------------------------------------


class TrustedExecutable(_Model):
    """A program the installer recorded outside the default trusted locations
    (``arcilogger_core.execpolicy``): its path, SHA-256 and, for ``tsa.java`` with more than one
    element, the whole command."""

    path: Text
    sha256: Sha256
    argv: Annotated[tuple[Text, ...], Field(min_length=1)] | None = None


class AdminPolicy(_Model):
    """The guard rails of the browser administration, written by the installer and only by
    administrators. A missing or invalid file means :data:`FALLBACK_POLICY`
    (:func:`load_policy`)."""

    schema_: Literal["arcilogger.admin-policy/1"] = Field(default=POLICY_SCHEMA, alias="schema")
    #: ``false`` (``-DisableBrowserAdministration``): the agent refuses every request.
    enabled: bool
    #: Networks a log source address must lie in (``-SourceNetwork``; default RFC 1918).
    source_networks: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    #: The widest source network allowed (``/16`` by default).
    min_prefix_v4: int = Field(default=16, ge=8, le=32)
    max_sources: int = Field(default=500, ge=1, le=5000)
    #: Hold of the most consequential changes (retention, replica); 0 applies them at once.
    hold_hours: int = Field(default=0, ge=0, le=168)
    #: Hold of a Zamane client that is not a known release.
    jar_hold_hours: int = Field(default=24, ge=1, le=168)
    #: Folders whose programs the tasks may run (``tsa.java``, ``pg_dump``, ``pg_restore``).
    executable_roots: tuple[Text, ...] = ("C:\\Program Files", "C:\\Program Files (x86)")
    trusted_executables: tuple[TrustedExecutable, ...] = ()
    tsa_test_per_day: int = Field(default=3, ge=0, le=20)
    written_at: AwareDatetime
    written_by: Text

    @field_validator("source_networks")
    @classmethod
    def _ipv4_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(str(ipaddress.IPv4Network(value, strict=True)) for value in values)


#: What a missing, unreadable or invalid ``policy.json`` means, for every reader alike (before
#: the installer writes it, on a Linux test machine, after a damaged hand edit): browser
#: administration off (fail closed: the agent refuses every request, the API answers
#: ``admin_disabled``) and, for :mod:`arcilogger_core.execpolicy`, the shipped
#: ``executable_roots`` with nothing else trusted. The default ``java``, a Java below Program
#: Files and the installer-recorded Zamane jar therefore keep sealing, credit and retention
#: running (contract 2.16); execpolicy never looks at ``enabled``.
FALLBACK_POLICY: Final = AdminPolicy(
    enabled=False, written_at=datetime(1970, 1, 1, tzinfo=UTC), written_by="fallback"
)


def load_policy(path: Path) -> AdminPolicy:
    """``policy.json`` at ``path`` (``AdminLayout.policy``), or :data:`FALLBACK_POLICY` when it
    is missing, unreadable or invalid. Never raises, so the API, the agent, ApplySystem's plan,
    the tasks and the backup give the file one meaning."""
    try:
        return parse_document(AdminPolicy, path.read_bytes())
    except (OSError, DocumentError):
        return FALLBACK_POLICY


# -- 2.4.2 admin\keys\keys.json -------------------------------------------------------------------


class PublicKeyEntry(_Model):
    id: KeyId
    role: KeyRole
    #: The raw 32-byte Ed25519 public key, base64url without padding.
    public: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]

    @model_validator(mode="after")
    def _id_of_the_key(self) -> PublicKeyEntry:
        if self.id != key_id_for(self.role, b64url_decode(self.public, 32)):
            raise ValueError(f"key id {self.id} does not belong to its public key and role")
        return self

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(b64url_decode(self.public, 32))


class KeysFile(_Model):
    """The channel's public keys and this installation's instance id (``admin.keys``)."""

    schema_: Literal["arcilogger.admin-keys/1"] = Field(default=KEYS_SCHEMA, alias="schema")
    instance: Id
    keys: tuple[PublicKeyEntry, ...] = Field(min_length=1)

    @field_validator("keys")
    @classmethod
    def _unique(cls, keys: tuple[PublicKeyEntry, ...]) -> tuple[PublicKeyEntry, ...]:
        if len({key.id for key in keys}) != len(keys):
            raise ValueError("key ids must be unique")
        return keys

    def public_keys(self, role: KeyRole) -> dict[str, Ed25519PublicKey]:
        """key id -> public key of every key with ``role``."""
        return {key.id: key.public_key() for key in self.keys if key.role == role}


# -- 2.4.3 the administration request (payload of inbox\<id>.req) ---------------------------------


class Actor(_Model):
    user_id: int = Field(ge=1)
    username: Text
    #: A short session reference (never the session token).
    session: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    ip: str
    #: The user's last re-authentication, when the change needs one.
    reauth_at: AwareDatetime | None = None

    @field_validator("ip")
    @classmethod
    def _address(cls, value: str) -> str:
        return str(ipaddress.ip_address(value))


class SetOp(_Model):
    op: Literal["set"]
    path: ConfigPath
    #: The value at edit time (per-key optimistic concurrency).
    expect: JsonValue
    value: JsonValue


class SecretSetOp(_Model):
    op: Literal["secret_set"]
    name: BrowserSecret
    expect: Literal["set", "unset"]
    #: ``SecretStore.encrypt_value(plaintext)``: the same format as a value of secrets.json.
    token: FernetToken


class SecretDeleteOp(_Model):
    op: Literal["secret_delete"]
    name: BrowserSecret
    expect: Literal["set", "unset"]


class SourceAddOp(_Model):
    op: Literal["source_add"]
    source: SourceConfig


class SourceUpdateOp(_Model):
    op: Literal["source_update"]
    name: SourceName
    expect: SourceConfig
    source: SourceConfig


class SourceRemoveOp(_Model):
    op: Literal["source_remove"]
    name: SourceName
    expect: SourceConfig


class RootAddOp(_Model):
    op: Literal["root_add"]
    upload: Id
    sha256: Sha256
    size: int = Field(ge=1)
    legal: bool


class LegalRootAddOp(_Model):
    op: Literal["legal_root_add"]
    sha256: Sha256


class ZamaneStageOp(_Model):
    op: Literal["zamane_stage"]
    upload: Id
    #: Of the uploaded file (a .jar or the .zip holding it) …
    sha256: Sha256
    size: int = Field(ge=1)
    #: … and of the jar that runs.
    jar_sha256: Sha256
    #: The known release (``tsa.zamane_releases``), or None: the client is held.
    release: Text | None


class SmtpCaSetOp(_Model):
    op: Literal["smtp_ca_set"]
    upload: Id
    sha256: Sha256
    size: int = Field(ge=1)


class SmtpCaClearOp(_Model):
    op: Literal["smtp_ca_clear"]
    #: ``smtp.ca_file`` at edit time.
    expect: Text | None


Op = Annotated[
    SetOp
    | SecretSetOp
    | SecretDeleteOp
    | SourceAddOp
    | SourceUpdateOp
    | SourceRemoveOp
    | RootAddOp
    | LegalRootAddOp
    | ZamaneStageOp
    | SmtpCaSetOp
    | SmtpCaClearOp,
    Field(discriminator="op"),
]


class RequestOptions(_Model):
    #: config.revert, config.cancel: the request to revert or cancel.
    target: Id | None = None
    #: action.seal: the day to seal.
    day: date | None = None
    #: The recipient of the trial e-mail of an SMTP change.
    test_to: Annotated[str, StringConstraints(pattern=EMAIL_ADDRESS, max_length=254)] | None = None


class AdminRequest(_Model):
    """What the API asks the agent to do; signed with the API key (``admin.envelope``)."""

    schema_: Literal["arcilogger.admin-request/1"] = Field(default=REQUEST_SCHEMA, alias="schema")
    id: Id
    #: ``keys.json`` ``instance`` of the installation the request is for.
    instance: Id
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    kind: RequestKind
    section: Section | None = None
    actor: Actor
    #: The ``audit_log`` row of ``admin.request`` written with the request.
    audit_id: int | None = Field(default=None, ge=1)
    ops: tuple[Op, ...] = Field(default=(), max_length=MAX_OPS)
    #: Test the candidate values first (connection probes of contract 2.6).
    probe: bool = True
    options: RequestOptions = RequestOptions()

    @model_validator(mode="after")
    def _shape(self) -> AdminRequest:
        validity = self.expires_at - self.issued_at
        if not timedelta(0) < validity <= MAX_REQUEST_VALIDITY:
            raise ValueError(f"expires_at must be 0-{MAX_REQUEST_VALIDITY} after issued_at")
        if self.kind == "config.apply":
            if self.section is None or not self.ops:
                raise ValueError("config.apply needs a section and at least one op")
        elif self.ops:
            raise ValueError(f"{self.kind} carries no ops")
        if self.kind in ("config.revert", "config.cancel") and self.options.target is None:
            raise ValueError(f"{self.kind} needs options.target")
        if self.kind.startswith("action.") and self.section is not None:
            raise ValueError("an action belongs to no section")
        if (self.kind == "action.seal") != (self.options.day is not None):
            raise ValueError("options.day is given with action.seal and only with it")
        return self


# -- 2.4.4 inbox\uploads\<id>.json ----------------------------------------------------------------


class UploadMeta(_Model):
    schema_: Literal["arcilogger.admin-upload/1"] = Field(default=UPLOAD_SCHEMA, alias="schema")
    id: Id
    kind: UploadKind
    sha256: Sha256
    size: int = Field(ge=1)
    created_at: AwareDatetime
    user_id: int = Field(ge=1)

    @model_validator(mode="after")
    def _within_limit(self) -> UploadMeta:
        if self.size > UPLOAD_LIMITS[self.kind]:
            raise ValueError(f"a {self.kind} upload is at most {UPLOAD_LIMITS[self.kind]} bytes")
        return self


# -- 2.4.5 / 2.4.6 task requests and results (state\requests\) ------------------------------------


class TaskParams(_Model):
    #: seal: the day to seal (the oldest due day; ``seal_due`` ignores its backoff once).
    day: date | None = None


class TaskRequest(_Model):
    """A task the agent asks the Seal or Requests task to run; signed with the agent key."""

    schema_: Literal["arcilogger.task-request/1"] = Field(
        default=TASK_REQUEST_SCHEMA, alias="schema"
    )
    id: Id
    #: The administration request it serves.
    origin: Id
    kind: TaskKind
    params: TaskParams = TaskParams()
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _shape(self) -> TaskRequest:
        limit = MAX_SEAL_REQUEST_VALIDITY if self.kind == "seal" else MAX_TASK_REQUEST_VALIDITY
        if not timedelta(0) < self.expires_at - self.issued_at <= limit:
            raise ValueError(f"expires_at must be 0-{limit} after issued_at")
        if (self.kind == "seal") != (self.params.day is not None):
            raise ValueError("params.day is given with a seal request and only with it")
        return self


class SealResult(_Model):
    day: date
    #: ``arcilogger_core.seal.SealOutcome.status``.
    status: Literal["sealed", "pending", "failed", "waiting", "skipped", "deleted"]
    message: str
    serial: str | None = None
    gen_time: AwareDatetime | None = None


class CreditResult(_Model):
    credits: int = Field(ge=0)
    threshold: int = Field(ge=0)
    below_threshold: bool


class TsaTestResult(_Model):
    """The JSON of ``arcilogger-core tsa-test`` without ``token`` (a file path)."""

    provider: str
    legal_grade_claimed: bool
    legal_grade: bool
    root_sha256: str | None
    root: str | None
    verified: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    gen_time: AwareDatetime | None
    serial: str | None
    tsa: str | None
    tsa_cert_not_after: AwareDatetime | None
    container: str | None


class ReplicaTestResult(_Model):
    ok: bool
    #: Technical detail; the request's texts come from its code.
    message: str


ActionResult = SealResult | CreditResult | TsaTestResult | ReplicaTestResult
_RESULT_TYPES: Final[Mapping[TaskKind, type[_Model]]] = MappingProxyType(
    {
        "seal": SealResult,
        "credit": CreditResult,
        "tsa_test": TsaTestResult,
        "replica_test": ReplicaTestResult,
    }
)


def _known_code(code: str | None, *catalogues: Mapping[str, tuple[str, str]]) -> str | None:
    if code is not None and not any(code in catalogue for catalogue in catalogues):
        raise ValueError(f"code {code!r} has no text in arcilogger_core.admin.messages")
    return code


class TaskResult(_Model):
    """Written by the task: never a secret, a token path or a file path."""

    schema_: Literal["arcilogger.task-result/1"] = Field(default=TASK_RESULT_SCHEMA, alias="schema")
    #: The task request's id.
    id: Id
    kind: TaskKind
    ok: bool
    code: str | None = None
    message_tr: str | None = None
    message_en: str | None = None
    result: ActionResult | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @field_validator("code")
    @classmethod
    def _code(cls, code: str | None) -> str | None:
        return _known_code(code, MESSAGES)

    @model_validator(mode="after")
    def _result_of_its_kind(self) -> TaskResult:
        if self.result is not None and not isinstance(self.result, _RESULT_TYPES[self.kind]):
            raise ValueError(f"a {self.kind} result must be a {_RESULT_TYPES[self.kind].__name__}")
        if self.ok == (self.code is not None):
            raise ValueError("a code is given when the task failed and only then")
        return self


# -- 2.4.7 status\agent.json ----------------------------------------------------------------------


class PendingRequest(_Model):
    id: Id
    effective_at: AwareDatetime


class SealReady(_Model):
    """``tsa.preflight.seal_preflight`` of the current configuration: the sealing causes
    (``seal.CONFIG_CAUSES``) that would stop the Seal task now."""

    ok: bool
    causes: tuple[str, ...] = ()


class SystemTaskCheck(_Model):
    """The start-up ping of ApplySystem (Event 4101, ``system\\status.json`` within 5 min)."""

    ok: bool
    checked_at: AwareDatetime | None = None
    error: str | None = None


class AgentStatus(_Model):
    schema_: Literal["arcilogger.admin-agent/1"] = Field(
        default=AGENT_STATUS_SCHEMA, alias="schema"
    )
    version: str
    pid: int = Field(ge=1)
    started_at: AwareDatetime
    heartbeat_at: AwareDatetime
    state: Literal["idle", "busy"]
    current_request: Id | None = None
    queue: _Count = 0
    pending: tuple[PendingRequest, ...] = ()
    #: Of the policy.json bytes in force; None while there is no valid policy.
    policy_sha256: Sha256 | None
    key_ids: tuple[KeyId, ...]
    java_candidates: tuple[str, ...] = ()
    local_addresses: tuple[str, ...] = ()
    seal_ready: SealReady
    system_task: SystemTaskCheck


# -- 2.4.8 status\<id>.json -----------------------------------------------------------------------


class StepEntry(_Model):
    step: Step
    state: StepState
    at: AwareDatetime | None = None
    code: str | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, code: str | None) -> str | None:
        return _known_code(code, MESSAGES, WARNINGS)


class Transition(_Model):
    """``{"from": <sha256>, "to": <sha256>}`` of the configuration file. Built with
    ``Transition.model_validate({"from": ..., "to": ...})``."""

    from_: Sha256 = Field(alias="from")
    to: Sha256


class VersionRef(_Model):
    """The version store entry of an applied change: ``number`` keeps the pair before it."""

    number: int = Field(ge=1)
    from_: Sha256 = Field(alias="from")
    to: Sha256


class ErrorRef(_Model):
    code: str
    #: The configuration path the code is about, when there is one.
    field: ConfigPath | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, code: str) -> str:
        _known_code(code, MESSAGES)
        return code


class WarningRef(_Model):
    code: str

    @field_validator("code")
    @classmethod
    def _code(cls, code: str) -> str:
        _known_code(code, WARNINGS)
        return code


def _check_unverified(state: str, kind: str | None, section: str | None, code: str | None) -> None:
    """A request rejected before its payload was verified and parsed (``signature_invalid``, a
    ``schema_invalid`` envelope or payload) is known only by the id of its file name: nothing of
    an unverified payload is trusted, so its kind, section and requester stay unknown. Every
    other outcome has them."""
    if kind is not None:
        return
    if state != "rejected" or code is None:
        raise ValueError("only a rejected request, with its code, may lack its kind")
    if section is not None:
        raise ValueError("a request without its kind has no section either")


class RequestStatus(_Model):
    """The agent's view of one request; the API adds the texts and the API-side states.
    ``kind`` (and ``section``) are None only for a rejection before verification."""

    id: Id
    kind: RequestKind | None = None
    section: Section | None = None
    state: State
    steps: tuple[StepEntry, ...] = ()
    version: VersionRef | None = None
    effective_at: AwareDatetime | None = None
    result: ActionResult | None = None
    error: ErrorRef | None = None
    warnings: tuple[WarningRef, ...] = ()
    rolled_back: bool = False
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _kind_known(self) -> RequestStatus:
        code = self.error.code if self.error is not None else None
        _check_unverified(self.state, self.kind, self.section, code)
        return self


# -- status\secret-meta.json ----------------------------------------------------------------------


class SecretMetaEntry(_Model):
    changed_at: AwareDatetime
    #: ``web:<username>``, ``cli`` or ``installer``.
    changed_by: Text


class SecretMeta(_Model):
    """When and by whom each stored secret was last changed (names only, never values)."""

    schema_: Literal["arcilogger.admin-secret-meta/1"] = Field(
        default=SECRET_META_SCHEMA, alias="schema"
    )
    secrets: dict[SecretName, SecretMetaEntry] = Field(default_factory=dict)


# -- 2.4.9 journal\<YYYY-MM-DD>.jsonl -------------------------------------------------------------


class Requester(_Model):
    user_id: int = Field(ge=1)
    username: Text


class ValueChange(_Model):
    old: JsonValue
    new: JsonValue


class SecretChange(_Model):
    """A secret in ``changes``: whether it was set before and after, never a value."""

    secret: bool
    old: Literal["set", "unset"]
    new: Literal["set", "unset", "deleted"]
    changed: bool

    @field_validator("secret")
    @classmethod
    def _marked(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("secret must be true")
        return value


class SecretRecord(_Model):
    """One ``settings.secret_change`` audit entry (``operator: "web:<username>"``)."""

    name: BrowserSecret
    action: Literal["set", "delete", "restore"]
    #: ``stored``, ``deleted``, ``absent`` (as ``secretstore.record_change``).
    result: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,32}$")]


class JournalLine(_Model):
    """One outcome of a request; the worker imports each line once into the audit chain.

    ``kind``, ``section`` and ``requested_by`` are None only for a rejection before
    verification (whoever wrote an unverified file must not name the requester in the audit
    chain). A console ``versions restore N`` is no request and writes no line: the version
    store records it (``actor: console``) and the API shows ``config_origin: outside``."""

    v: Annotated[int, Field(ge=1, le=1)] = 1
    line: Id
    request: Id
    state: JournalState
    at: AwareDatetime
    kind: RequestKind | None = None
    section: Section | None = None
    requested_by: Requester | None = None
    version: int | None = Field(default=None, ge=1)
    config_sha256: Transition | None = None
    changes: dict[ConfigPath, ValueChange | SecretChange] = Field(default_factory=dict)
    secrets: tuple[SecretRecord, ...] = ()
    code: str | None = None
    rolled_back: bool = False
    result: ActionResult | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, code: str | None) -> str | None:
        return _known_code(code, MESSAGES)

    @model_validator(mode="after")
    def _secrets_masked(self) -> JournalLine:
        for path, change in self.changes.items():
            if path in BROWSER_SECRETS and not isinstance(change, SecretChange):
                raise ValueError(f"{path} is a secret: only set/unset may be journaled")
        _check_unverified(self.state, self.kind, self.section, self.code)
        if self.kind is not None and self.requested_by is None:
            raise ValueError("a verified request names its requester")
        if self.kind is None and self.requested_by is not None:
            raise ValueError("an unverified request has no requester")
        return self


# -- 2.4.10 versions\index.json -------------------------------------------------------------------


class VersionEntry(_Model):
    """Version ``n``: the configuration pair **before** request ``request`` was applied."""

    n: int = Field(ge=1)
    at: AwareDatetime
    request: Id | None
    #: ``<username>`` of the request, or ``console`` for a restore on the server.
    actor: Text
    paths: tuple[ConfigPath, ...]
    toml_sha256: Sha256
    secrets_sha256: Sha256 | None
    #: Whether ``secrets.json`` of this version is still kept (the newest 5 are).
    has_secrets: bool


class VersionIndex(_Model):
    schema_: Literal["arcilogger.admin-versions/1"] = Field(default=VERSIONS_SCHEMA, alias="schema")
    versions: tuple[VersionEntry, ...] = ()


# -- 2.4.11 system\ (written by ApplySystem as SYSTEM) -------------------------------------------


class FirewallState(_Model):
    managed: bool
    strict_outbound: bool
    syslog_addresses: tuple[str, ...] = ()
    outbound_hosts: tuple[str, ...] = ()


class TimeState(_Model):
    #: The Windows Time peer list (``w32tm`` ``NtpServer`` value).
    peers: str
    changed_at: AwareDatetime | None = None


class JarCounts(_Model):
    staged: _Count
    promoted: _Count


class SystemStatus(_Model):
    schema_: Literal["arcilogger.admin-system/1"] = Field(
        default=SYSTEM_STATUS_SCHEMA, alias="schema"
    )
    at: AwareDatetime
    ok: bool
    code: str | None = None
    applied_config_sha256: Sha256 | None
    collector_ack_sha256: Sha256 | None
    firewall: FirewallState
    time: TimeState
    jars: JarCounts

    @field_validator("code")
    @classmethod
    def _code(cls, code: str | None) -> str | None:
        return _known_code(code, MESSAGES, WARNINGS)


#: Who made a staged Zamane client runnable: ``known`` (ApplySystem promoted a release of
#: ``tsa.zamane_releases``) or ``console:<user>`` (an administrator ran ``approve-jar``, at the
#: earliest ``policy.jar_hold_hours`` after ``first_seen``). Time alone never approves a client:
#: an upload from a compromised API or agent must not end up running as LOCAL SERVICE (ADR-0014).
JarApproval = Annotated[str, StringConstraints(pattern=r"^(known|console:.{1,200})$")]


class StagedJar(_Model):
    sha256: Sha256
    first_seen: AwareDatetime


class ApprovedJar(_Model):
    sha256: Sha256
    #: ``zamane\approved\<sha256>.jar`` of the installation folder.
    path: Text
    approved_at: AwareDatetime
    approved_by: JarApproval


class JarsFile(_Model):
    schema_: Literal["arcilogger.admin-jars/1"] = Field(default=JARS_SCHEMA, alias="schema")
    staged: tuple[StagedJar, ...] = ()
    approved: tuple[ApprovedJar, ...] = ()


class FirewallChange(_Model):
    syslog_addresses_count: _Count
    outbound_rules: _Count


class TimeChange(_Model):
    changed: bool
    peers: str


class JarChange(_Model):
    sha256: Sha256
    approved_by: JarApproval


class SystemJournalLine(_Model):
    """One applied firewall, time or jar change (``system\\journal.jsonl``), imported once as
    the audit action ``admin.system``; exactly the block named by ``change`` is given."""

    v: Annotated[int, Field(ge=1, le=1)] = 1
    line: Id
    at: AwareDatetime
    change: Literal["firewall", "time", "jar"]
    firewall: FirewallChange | None = None
    time: TimeChange | None = None
    jar: JarChange | None = None

    @model_validator(mode="after")
    def _one_block(self) -> SystemJournalLine:
        given = {name for name in ("firewall", "time", "jar") if getattr(self, name) is not None}
        if given != {self.change}:
            raise ValueError(f"a {self.change} line has exactly the {self.change} block")
        return self


# -- 2.8.3 the output of system-plan (read by Sync-ArciSystem.ps1 as SYSTEM) -----------------------

#: A host name or IPv4 address, as ``Sync-ArciSystem.ps1`` re-checks it before any change.
HostName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
]


def _syslog_sender(value: str) -> str:
    """An IPv4 address or network in its canonical text (``172.20.20.0/24``, not ``/24`` of a
    host address), as the firewall rule's ``RemoteAddress`` takes it."""
    canonical = (
        str(ipaddress.IPv4Network(value, strict=True))
        if "/" in value
        else str(ipaddress.IPv4Address(value))
    )
    if canonical != value:
        raise ValueError(f"{value!r} is not written as {canonical!r}")
    return value


class OutboundRule(_Model):
    """One host of a strict outbound rule; a rule with several hosts or ports has one entry
    each (``Set-ArciFirewall.ps1`` names the rules)."""

    rule: Annotated[
        str, StringConstraints(pattern=r"^ARCILOGGER Out (NTP|Kamu SM|SMTP( [2-9])?|Replica)$")
    ]
    host: HostName
    port: int = Field(ge=1, le=65535)


class FirewallPlan(_Model):
    #: False without ``install\firewall-state.json`` (``-SkipFirewall``): the step is skipped.
    managed: bool
    strict_outbound: bool
    #: The senders the syslog rules let in. May be empty: the rules are then removed (an empty
    #: ``RemoteAddress`` would mean any address).
    syslog_addresses: tuple[str, ...]
    #: Strict outbound mode only (empty otherwise).
    outbound: tuple[OutboundRule, ...] = ()

    @field_validator("syslog_addresses")
    @classmethod
    def _senders(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("syslog_addresses must be unique")
        return tuple(_syslog_sender(value) for value in values)

    @model_validator(mode="after")
    def _outbound_when_strict(self) -> FirewallPlan:
        if self.outbound and not self.strict_outbound:
            raise ValueError("outbound rules are planned in strict outbound mode only")
        return self


class TimePlan(_Model):
    #: ``clock.ntp_server`` and ``clock.fallback_server``; None when not set.
    primary: HostName | None
    fallback: HostName | None
    #: The peers differ from those applied last: ``Set-ArciTime.ps1`` runs.
    changed: bool


class JarPromotion(_Model):
    sha256: Sha256
    #: The full path of ``zamane\web\<sha256>.jar``.
    staged: Text
    #: Only a known release is promoted by the plan; another client needs ``approve-jar``.
    reason: Literal["known"]

    @model_validator(mode="after")
    def _staged_file(self) -> JarPromotion:
        found = re.search(r"[\\/]zamane[\\/]web[\\/]([0-9a-f]{64})\.jar$", self.staged)
        if found is None or found.group(1) != self.sha256:
            raise ValueError(f"staged must be zamane\\web\\{self.sha256}.jar")
        return self


class JarPlan(_Model):
    promote: tuple[JarPromotion, ...] = ()


class PlanProblem(_Model):
    """A value the plan left out, e.g. a source address outside ``policy.source_networks``."""

    code: str
    value: Text

    @field_validator("code")
    @classmethod
    def _code(cls, code: str) -> str:
        _known_code(code, WARNINGS)
        return code


class SystemPlan(_Model):
    """What ApplySystem applies, derived only from the validated configuration, the policy and
    ApplySystem's own state (``python -I -m arcilogger_core.admin system-plan --config ...``)."""

    schema_: Literal["arcilogger.system-plan/1"] = Field(default=SYSTEM_PLAN_SCHEMA, alias="schema")
    config_sha256: Sha256
    #: ``config.sha256`` of the collector's status file; None when it reports none (an older
    #: collector): the syslog addresses are then narrowed as for an unknown version.
    collector_ack_sha256: Sha256 | None
    firewall: FirewallPlan
    time: TimePlan
    jars: JarPlan
    problems: tuple[PlanProblem, ...] = ()


# -- the folder ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminLayout:
    """The files of ``Config.admin_dir`` (rights: contract 2.2; the installer creates them)."""

    root: Path

    @property
    def policy(self) -> Path:
        return self.root / "policy.json"

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    def request_file(self, request_id: str) -> Path:
        return self.inbox / f"{check_id(request_id)}.req"

    @property
    def uploads(self) -> Path:
        return self.inbox / "uploads"

    def upload_data(self, upload_id: str) -> Path:
        return self.uploads / f"{check_id(upload_id)}.bin"

    def upload_meta(self, upload_id: str) -> Path:
        return self.uploads / f"{check_id(upload_id)}.json"

    @property
    def work(self) -> Path:
        return self.root / "work"

    def work_dir(self, request_id: str) -> Path:
        """Where the agent moves a request it claims (nothing is read from the inbox again)."""
        return self.work / check_id(request_id)

    @property
    def pending(self) -> Path:
        return self.root / "pending"

    def pending_file(self, request_id: str) -> Path:
        return self.pending / f"{check_id(request_id)}.json"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def txn(self) -> Path:
        """The write-ahead record of the apply in progress."""
        return self.state / "txn.json"

    @property
    def ledger(self) -> Path:
        """The ids of the requests taken (30 days)."""
        return self.state / "ledger.json"

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    def version_dir(self, number: int) -> Path:
        if number < 1:
            raise ValueError(f"version numbers start at 1: {number}")
        return self.versions / str(number)

    @property
    def versions_index(self) -> Path:
        return self.versions / "index.json"

    @property
    def status(self) -> Path:
        return self.root / "status"

    @property
    def agent_status(self) -> Path:
        return self.status / "agent.json"

    def request_status(self, request_id: str) -> Path:
        return self.status / f"{check_id(request_id)}.json"

    @property
    def secret_meta(self) -> Path:
        return self.status / "secret-meta.json"

    @property
    def alerts(self) -> Path:
        return self.status / "alerts.jsonl"

    @property
    def journal(self) -> Path:
        return self.root / "journal"

    def journal_file(self, day: date) -> Path:
        return self.journal / f"{day.isoformat()}.jsonl"

    @property
    def system(self) -> Path:
        return self.root / "system"

    @property
    def system_status(self) -> Path:
        return self.system / "status.json"

    @property
    def jars(self) -> Path:
        return self.system / "jars.json"

    @property
    def system_journal(self) -> Path:
        return self.system / "journal.jsonl"

    @property
    def system_alerts(self) -> Path:
        return self.system / "alerts.jsonl"
