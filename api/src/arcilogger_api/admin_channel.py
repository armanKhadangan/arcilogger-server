"""The API's side of the browser administration (ADR-0014; contract 2.5.1, 2.10-2.12, 2.14).

The API never writes ``arcilogger.toml``, ``secrets.json`` or the vault. A change of the machine
configuration and an evidence action ("Şimdi mühürle", "Kontör sorgula", the stamp and replica
tests) become a request of the administration agent ``ArciAdmin``: a file in
``admin\\inbox`` signed with the API's Ed25519 key (:mod:`arcilogger_core.admin.envelope`),
whose secrets are Fernet tokens under ``secret.key`` and never plain text. The test e-mail and
the lease rebuild need no privilege and no secret: they are ``admin_requests`` rows the worker
takes itself (``channel = 'worker'``).

:meth:`Channel.submit` writes one request, in this order (contract 2.5.1):

1. the checks of the caller (the section rules of :mod:`arcilogger_core.admin.rules`, the
   re-authentication, the permission) are done; browser administration must be switched on
   (``policy.json``, 503 ``admin_disabled``), the API key readable (503
   ``admin_channel_key_missing``) and the agent alive (heartbeat older than two minutes or none:
   503 ``admin_agent_unavailable``, nothing queued; older than 30 seconds: queued with the
   warning ``agent_stale``); at most 20 requests and 100 MiB wait in the inbox (429
   ``admin_queue_full``) and one configuration request per section is open (409
   ``admin_section_busy``; a Zamane client held for its console approval does not count, it
   may wait for days and is checked again when it is applied);
2. the payload is built and signed;
3. **one transaction** inserts the ``admin_requests`` row and the audit entry ``admin.request``
   (with the changes, secrets only as ``set``/``unset``), and is committed;
4. only then ``<id>.req.tmp`` is written, flushed to disk and renamed to ``<id>.req``; when that
   fails the row is marked ``failed`` (``write_failed``), the temporary file is removed and the
   answer is 503 ``admin_inbox_write_failed``;
5. the answer is 202 with the request's ``status_url``.

:meth:`Channel.view` reads a request back: the agent's ``admin\\status\\<id>.json`` (codes only)
or, for the worker channel, the row. The API itself decides four states (contract 2.10):
``waiting_for_agent`` (queued for more than a minute, or the heartbeat is stale), ``expired`` (not
taken by ``expires_at`` plus two minutes: the file is removed, nothing changed), ``active``
(every running service reports the new configuration's SHA-256 and the system step is done) and
``partially_active`` (two minutes later a service still has not taken it, or the system step
failed). Every state it sees is mirrored into the row, so the history outlives the agent's
30-day cleanup. Every code is shown with its Turkish and English text (:func:`texts`): the API's
own catalogue (:data:`TEXTS`), then :mod:`arcilogger_core.admin.messages`.

No licence state affects any of this, and nothing here stops or waits for the 5651 pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.accounts import session_id
from arcilogger_api.deps import AppState, get_state
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, ensure_recent_auth
from arcilogger_core.admin import rules
from arcilogger_core.admin.contract import (
    EXPIRED_GRACE,
    INBOX_MAX_BYTES,
    INBOX_MAX_REQUESTS,
    MAX_REQUEST_VALIDITY,
    QUEUED_WAIT,
    SERVICE_FRESH,
    SYSTEM_TASK_TIMEOUT,
    TERMINAL_STATES,
    UPLOAD_LIFETIME,
    AdminLayout,
    AdminRequest,
    AgentStatus,
    DocumentError,
    JarsFile,
    JournalLine,
    RequestKind,
    RequestStatus,
    SystemStatus,
    UploadKind,
    UploadMeta,
    dump_document,
    heartbeat_state,
    new_id,
    parse_document,
)
from arcilogger_core.admin.envelope import sign_admin_request
from arcilogger_core.admin.keys import KeysError, SigningKey, load_keys, load_signing_key
from arcilogger_core.admin.messages import MESSAGES, STATE_LABELS, WARNINGS
from arcilogger_core.admin.policy import PolicyFile, read_policy
from arcilogger_core.admin.roots import installed_roots
from arcilogger_core.admin.store import Installation, read_journal, read_status
from arcilogger_core.config import Config, parse_config
from arcilogger_core.configwatch import ConfigStatus, config_status_of
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.fsutil import fsync_dir
from arcilogger_core.layout import Layout
from arcilogger_core.retention import plan as retention_plan
from arcilogger_core.secretstore import SecretStore, SecretStoreError
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, to_site, utc_now
from arcilogger_core.tsa.preflight import seal_preflight
from arcilogger_db.models import AdminRequest as AdminRequestRow
from arcilogger_db.models import User
from arcilogger_db.settings import get_setting

log = logging.getLogger("arcilogger.api.admin")

# -- texts --------------------------------------------------------------------------------------

#: The API's own codes of the browser administration (contract 2.12) -> (Turkish, English). The
#: agent's and the rules' codes are in ``arcilogger_core.admin.messages``.
TEXTS: Final[Mapping[str, tuple[str, str]]] = {
    "admin_agent_unavailable": (
        "Yönetim hizmeti (ArciAdmin) yanıt vermiyor; değişiklik kuyruğa alınmadı. Sunucuda "
        '"ARCILOGGER Yönetim" hizmetini başlatın.',
        "The administration service (ArciAdmin) is not responding; the change was not queued. "
        'Start the "ARCILOGGER Yönetim" service on the server.',
    ),
    "admin_channel_key_missing": (
        "Yönetim kanalının anahtarı bulunamadı; kurulumu onarın (Install-Arcilogger.ps1).",
        "The administration channel key is missing; repair the installation "
        "(Install-Arcilogger.ps1).",
    ),
    "admin_queue_full": (
        "Bekleyen değişiklik sayısı sınıra ulaştı; öncekilerin bitmesini bekleyip tekrar deneyin.",
        "Too many changes are waiting; wait for the earlier ones to finish and try again.",
    ),
    "admin_rate_limited": (
        "Bu işlem kısa süre önce yapıldı; biraz sonra tekrar deneyin.",
        "This action ran a moment ago; try again shortly.",
    ),
    "admin_section_busy": (
        "Bu bölüm için bekleyen bir değişiklik var; bitmesini bekleyin ya da iptal edin.",
        "A change to this section is still open; wait for it to finish or cancel it.",
    ),
    "admin_request_not_found": ("Değişiklik isteği bulunamadı.", "Change request not found."),
    "admin_inbox_write_failed": (
        "Değişiklik isteği yönetim hizmetine iletilemedi (dosya yazılamadı); hiçbir şey "
        "değişmedi. Kurulumu onarın (Install-Arcilogger.ps1).",
        "The change request could not be handed to the administration service (the file could "
        "not be written); nothing changed. Repair the installation (Install-Arcilogger.ps1).",
    ),
    "config_conflict": (
        "Bu ayarlar siz düzenlerken başka bir yerden değiştirildi; sayfayı yenileyip tekrar "
        "deneyin.",
        "These settings were changed elsewhere while you were editing; reload the page and try "
        "again.",
    ),
    "config_unchanged": (
        "Kaydedilecek bir değişiklik yok; ayarlar zaten bu değerlerde.",
        "There is nothing to save; the settings already have these values.",
    ),
    "config_unreadable": (
        "arcilogger.toml şu anda okunamıyor ya da geçersiz (sunucuda elle yapılmış bir "
        "değişiklik?); dosya düzeltilene kadar tarayıcıdan değişiklik yapılamaz.",
        "arcilogger.toml cannot be read or is invalid right now (a change made by hand on the "
        "server?); nothing can be changed from the browser until the file is put right.",
    ),
    "retention_confirm_mismatch": (
        "Onaylamak için önizlemedeki silinecek gün sayısını aynen yazın.",
        "To confirm, type exactly the number of days to be deleted shown in the preview.",
    ),
    "source_confirm_mismatch": (
        "Silmeyi onaylamak için kaynağın adını aynen yazın.",
        "Type the source's name exactly to confirm the removal.",
    ),
    "test_email_recipient_not_allowed": (
        "Deneme e-postası yalnızca uyarı alıcılarına ya da kendi e-posta adresinize "
        "gönderilebilir.",
        "A trial e-mail can only go to the alert recipients or to your own address.",
    ),
    "tsa_confirm_required": (
        "Üretimden test sunucusuna geçiş üretim damgalarını durdurur; onaylayın.",
        "Switching from production to the test server stops production stamps; confirm to "
        "continue.",
    ),
    "legal_root_confirm_required": (
        "Parmak izini Kamu SM'nin yayımladığı değerle karşılaştırdığınızı onaylayın.",
        "Confirm that you compared the fingerprint with the value Kamu SM publishes.",
    ),
    "zamane_sha256_required": (
        "Bu sürüm bilinen sürümler arasında değil; Kamu SM indirme sayfasındaki SHA-256 değerini "
        "girin.",
        "This is not a known release; enter the SHA-256 shown on the Kamu SM download page.",
    ),
    "zamane_sha256_mismatch": (
        "Dosyanın SHA-256 değeri girdiğiniz değerle aynı değil; dosya kullanılmadı.",
        "The file's SHA-256 does not match the value you entered; the file was not used.",
    ),
    "upload_too_large": ("Dosya izin verilen boyuttan büyük.", "The file is larger than allowed."),
    "upload_kind_invalid": ("Bilinmeyen yükleme türü.", "Unknown upload kind."),
    "upload_not_found": (
        "Yüklenen dosya bulunamadı ya da süresi doldu (30 dakika); yeniden yükleyin.",
        "The uploaded file was not found or has expired (30 minutes); upload it again.",
    ),
    "seal_not_due": (
        "Şimdi yalnızca sıradaki (en eski) mühürlenmemiş kapalı gün mühürlenebilir; bu gün henüz "
        "sırada değil ya da gece yarısından hemen sonraki beklemede.",
        "Only the next (oldest) closed unsealed day can be sealed now; this day is not next yet, "
        "or it is in the wait just after midnight.",
    ),
    "seal_day_sealed": ("Bu gün zaten mühürlü.", "This day is already sealed."),
    "seal_not_applicable": (
        "Bu gün mühürlenmez (veri yok, atlandı ya da imha edildi).",
        "This day is not sealed (no data, skipped or deleted).",
    ),
    "seal_request_pending": (
        "Bu gün için bir mühürleme isteği zaten bekliyor.",
        "A sealing request for this day is already waiting.",
    ),
    "tsa_test_confirm_required": (
        "Damga testi 1 kontör harcar; onaylayın.",
        "The stamp test uses 1 credit; confirm to continue.",
    ),
    "tsa_test_limit": (
        "Bugünkü damga testi sınırına ulaşıldı.",
        "Today's stamp test limit has been reached.",
    ),
    "lease_rebuild_range": (
        "Geçersiz aralık: en çok 31 gün, gelecekte değil ve ilk logdan önce değil.",
        "Invalid range: at most 31 days, not in the future and not before the first log.",
    ),
}

#: HTTP status of every code the administration endpoints answer with (other codes: 422).
STATUS: Final[Mapping[str, int]] = {
    **rules.HTTP_STATUS,
    "admin_agent_unavailable": 503,
    "admin_disabled": 503,
    "admin_channel_key_missing": 503,
    "admin_inbox_write_failed": 503,
    "admin_queue_full": 429,
    "admin_rate_limited": 429,
    "tsa_test_limit": 429,
    "admin_section_busy": 409,
    "admin_request_not_found": 404,
    "request_not_cancellable": 409,
    "request_not_revertible": 409,
    "config_conflict": 409,
    "config_unreadable": 409,
    "smtp_not_configured": 409,
    "seal_not_due": 409,
    "seal_day_sealed": 409,
    "seal_not_applicable": 409,
    "seal_request_pending": 409,
    "upload_too_large": 413,
    "upload_not_found": 404,
}


def texts(code: str) -> tuple[str, str]:
    """``(message_tr, message_en)`` of a code of the browser administration (the API's own
    catalogue, then the agent's outcomes and warnings); a code without a text says so."""
    found = TEXTS.get(code) or MESSAGES.get(code) or WARNINGS.get(code)
    if found is None:  # every code the rules give has a text (a test checks it)
        log.error("administration code without a text: %s", code)
        return code, code
    return found


def admin_error(code: str, *, status: int | None = None, details: Any = None) -> ApiError:
    """An error answer of the browser administration (``{code, message_tr, message_en}``)."""
    message_tr, message_en = texts(code)
    return ApiError(status or STATUS.get(code, 422), code, message_tr, message_en, details=details)


def rate_limited(code: str, retry_after: timedelta) -> ApiError:
    """429 with ``details.retry_after`` (seconds) and the ``Retry-After`` header."""
    seconds = max(1, int(retry_after.total_seconds() + 0.999))
    error = admin_error(code, details={"retry_after": seconds})
    error.headers["Retry-After"] = str(seconds)
    return error


class CodeMessage(BaseModel):
    code: str
    message_tr: str
    message_en: str


def code_message(code: str) -> CodeMessage:
    message_tr, message_en = texts(code)
    return CodeMessage(code=code, message_tr=message_tr, message_en=message_en)


def problem_details(problems: Sequence[rules.Problem]) -> list[dict[str, Any]]:
    """The refused fields of a 422 (``[{field, code, message_tr, message_en}]``): never a
    submitted value."""
    found: list[dict[str, Any]] = []
    for problem in problems:
        message_tr, message_en = texts(problem.code)
        item: dict[str, Any] = {
            "field": problem.field,
            "code": problem.code,
            "message_tr": message_tr,
            "message_en": message_en,
        }
        item.update(problem.details)
        found.append(item)
    return found


def check_error(check: rules.SectionCheck) -> ApiError:
    """A refused section check: the first problem's code and status; ``details`` lists every
    field, or carries the facts of the first problem (``missing``, ``causes``)."""
    first = check.errors[0]
    details: Any = dict(first.details) if first.details else problem_details(check.errors)
    return admin_error(first.code, status=first.http_status, details=details)


# -- models of the answers -------------------------------------------------------------------------


class AdminAccepted(BaseModel):
    """202: the request is queued; follow it at ``status_url``."""

    request_id: str
    short_id: str
    #: ``cancelled``: a queued request removed from the inbox before the agent took it.
    state: Literal["queued", "cancelled"]
    status_url: str
    guarded: bool
    effective_at: datetime | None
    #: ``{path: {old, new}}``; a secret only ``{secret, old, new, changed}``.
    changes: dict[str, dict[str, JsonValue]]
    effects: list[str]
    warnings: list[CodeMessage]


class RequestStepOut(BaseModel):
    step: str
    state: str
    at: datetime | None = None
    code: str | None = None
    message_tr: str | None = None
    message_en: str | None = None


class RequestVersion(BaseModel):
    """The version store entry of an applied change: version ``number`` keeps the pair before
    it; ``from``/``to`` are the configuration's SHA-256 before and after."""

    model_config = ConfigDict(serialize_by_alias=True)

    number: int
    from_: str = Field(alias="from")
    to: str


class RequestError(BaseModel):
    code: str
    message_tr: str
    message_en: str
    field: str | None = None


class AdminRequestOut(BaseModel):
    """One request of the browser administration (contract 2.11.2)."""

    id: str
    short_id: str
    kind: str
    section: str | None
    state: str
    terminal: bool
    label_tr: str
    label_en: str
    requested_by: str
    created_at: datetime
    updated_at: datetime
    effective_at: datetime | None
    version: RequestVersion | None
    steps: list[RequestStepOut]
    changes: dict[str, dict[str, JsonValue]]
    #: The outcome of an action (``SealResult``, ``CreditResult``, …), or None until there is one.
    result: dict[str, JsonValue] | None
    error: RequestError | None
    rolled_back: bool
    warnings: list[CodeMessage]
    manual_steps_tr: str | None
    manual_steps_en: str | None


# -- what the API reads ----------------------------------------------------------------------------

#: The services whose ``config`` block tells when a change reached them (contract 2.4.12).
SERVICES: Final[Mapping[str, Callable[[Layout], Path]]] = {
    "ArciCollector": lambda layout: layout.collector_status,
    "ArciIngest": lambda layout: layout.ingest_status,
    "ArciWorker": lambda layout: layout.worker_status,
}
#: Effect -> the service that must report the new configuration.
RELOADS: Final[Mapping[str, str]] = {
    "reload.collector": "ArciCollector",
    "reload.ingest": "ArciIngest",
    "reload.worker": "ArciWorker",
    "reload.api": "ArciApi",
}
SYSTEM_EFFECTS: Final = ("system.firewall", "system.time")
#: How far back the API looks for the newest change the agent applied (``config_origin``).
ORIGIN_DAYS: Final = 400
#: The Retention task starts at this site time (``first_run_at`` of the preview).
RETENTION_START: Final = time(2, 0)
#: The actor address of a request whose client address is not known (``Actor.ip`` is required).
UNKNOWN_ADDRESS: Final = "0.0.0.0"  # noqa: S104 - an address in a record, never bound


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _describe(exc: Exception) -> str:
    """Why a file is not used, in one line without the file's values."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            found = errors(include_input=False, include_url=False)
        except TypeError:
            found = None
        if found:
            return "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '(file)'}: {error['msg']}"
                for error in found
            )
    return f"{type(exc).__name__}: {exc}".splitlines()[0]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class CurrentFiles:
    """``arcilogger.toml`` and the names in ``secrets.json`` as they are on disk now."""

    #: Of the file's bytes (None: it cannot be read).
    sha256: str | None
    #: The file's configuration, or the API's own when the file cannot be used (``error``).
    config: Config
    #: The names of the stored secrets, never their values.
    stored: frozenset[str]
    error: str | None = None


@dataclass(frozen=True)
class ServiceView:
    """A service's ``config`` block and whether it runs (status file younger than 2 minutes)."""

    config: ConfigStatus | None
    running: bool


def _taken(
    sha256: str | None, loaded_at: datetime | None, version: Any, applied_at: datetime
) -> bool:
    """Whether a reader runs the configuration of ``version`` (or a later one: it loaded a file
    other than the one before the change after the change was written)."""
    if sha256 is None:
        return False
    if sha256 == version.to:
        return True
    return loaded_at is not None and loaded_at >= applied_at and sha256 != version.from_


def _next_retention_run(after: datetime) -> datetime:
    """The Retention task's next start (02:00 site time) after ``after``."""
    local = to_site(after)
    start = datetime.combine(local.date(), RETENTION_START, tzinfo=SITE_TZ)
    return start if start > local else start + timedelta(days=1)


def _changes_list(changes: Mapping[str, Mapping[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """The changes as a list for ``admin_requests.params``: the database refuses an object key
    named like a secret (a source called ``token-gw`` would be one) unless it is masked."""
    return [{"path": path, **dict(change)} for path, change in changes.items()]


def _changes_dict(items: Any) -> dict[str, dict[str, JsonValue]]:
    changes: dict[str, dict[str, JsonValue]] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            changes[item["path"]] = {key: value for key, value in item.items() if key != "path"}
    return changes


#: The installation's interpreter: the ARCILOGGER programs are not on PATH (INSTALL 10), and
#: ``-I`` keeps a system Python's paths out. The console takes the installation's
#: configuration path by default.
CONSOLE_PYTHON: Final = '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m'
#: The script that gives ArciAdmin its rights on the configuration files again.
VAULT_ACL_SCRIPT: Final = '& "C:\\Program Files\\ARCILOGGER\\scripts\\Set-ArciVaultAcl.ps1"'


def manual_steps(version: int | None, code: str | None) -> tuple[str | None, str | None]:
    """What an administrator does on the server for a change that needs attention, one command
    a line (copied as it is). For ``acl_drift`` the change is in force and only the rights are
    repaired (putting the previous version back would undo it, and the next change would fail
    again)."""
    status = f"{CONSOLE_PYTHON} arcilogger_core.admin status"
    if code == "acl_drift":
        return (
            "Değişiklik yürürlükte; yalnızca yönetim hizmetinin ayar dosyalarına yazma izni "
            "onarılmalı.\n1. Sunucuda yönetici olarak bir PowerShell penceresi açın.\n2. İzinleri "
            f"onarın (ya da kurulumu onarım olarak yeniden çalıştırın):\n   {VAULT_ACL_SCRIPT}\n"
            f"3. Durumu denetleyin:\n   {status}",
            "The change is in force; only the administration service's write access to the "
            "configuration files needs repair.\n1. Open a PowerShell window as an administrator "
            "on the server.\n2. Repair the rights (or run the installer again as a repair):\n"
            f"   {VAULT_ACL_SCRIPT}\n3. Check the state:\n   {status}",
        )
    if version is None:
        return None, None
    restore = f"{CONSOLE_PYTHON} arcilogger_core.admin versions restore {version}"
    return (
        "1. Sunucuda yönetici olarak bir PowerShell penceresi açın.\n2. Önceki ayarları geri "
        f"yükleyin (yedek sürüm {version}):\n   {restore}\n3. Durumu denetleyin:\n   {status}",
        "1. Open a PowerShell window as an administrator on the server.\n2. Put the previous "
        f"settings back (backup version {version}):\n   {restore}\n3. Check the state:\n"
        f"   {status}",
    )


def _held_client(row: AdminRequestRow) -> bool:
    """A Zamane client held for its console approval (``approve-jar``): it may wait for days,
    so it does not keep the ``tsa`` section busy; once approved the agent checks it again
    against the file in force (``tsa_would_break`` and every other rule of the section)."""
    files = (row.params or {}).get("files") or []
    return row.state == "pending" and any(
        isinstance(item, dict) and item.get("kind") == "zamane_client" for item in files
    )


@dataclass
class Channel:
    """The browser administration of this API, read for one HTTP request (``ChannelDep``).

    Every file comes from the folder of the API's fixed configuration path
    (``AppState.config_path``), never from a key of the configuration (contract Day 0 note D0-2).
    Without that path (a configuration given in memory) browser administration is off."""

    state: AppState
    config: Config
    now: datetime = field(default_factory=lambda: to_site(utc_now()))

    # -- the folders ---------------------------------------------------------------------------

    @cached_property
    def installation(self) -> Installation | None:
        path = self.state.config_path
        return Installation.of(path) if path is not None else None

    @cached_property
    def layout(self) -> AdminLayout | None:
        return self.installation.admin if self.installation is not None else None

    @property
    def data(self) -> Layout:
        return Layout(self.config.data_dir)

    def _installed(self) -> Installation:
        if self.installation is None:
            raise admin_error("admin_disabled")
        return self.installation

    # -- what is on disk -----------------------------------------------------------------------

    @cached_property
    def policy(self) -> PolicyFile:
        """``policy.json`` (a missing or invalid one: browser administration off)."""
        if self.layout is None:
            return read_policy(Path(os.devnull) / "policy.json")
        return read_policy(self.layout.policy)

    @cached_property
    def agent(self) -> AgentStatus | None:
        """The agent's heartbeat file, None when it is missing or not valid."""
        if self.layout is None:
            return None
        try:
            return parse_document(AgentStatus, self.layout.agent_status.read_bytes())
        except (OSError, DocumentError):
            return None

    @property
    def heartbeat(self) -> Literal["ok", "stale", "missing"]:
        return heartbeat_state(self.agent.heartbeat_at if self.agent else None, self.now)

    @property
    def agent_state(self) -> Literal["ok", "stale", "missing", "disabled"]:
        return "disabled" if not self.policy.enabled else self.heartbeat

    @cached_property
    def system(self) -> SystemStatus | None:
        """ApplySystem's ``admin\\system\\status.json`` (None: it never ran, or unreadable)."""
        if self.layout is None:
            return None
        try:
            return parse_document(SystemStatus, self.layout.system_status.read_bytes())
        except (OSError, DocumentError):
            return None

    @cached_property
    def stored(self) -> frozenset[str]:
        if self.installation is None:
            return frozenset()
        try:
            return frozenset(self.installation.secret_store().names())
        except (OSError, ValueError, SecretStoreError):
            return frozenset()

    @cached_property
    def files(self) -> CurrentFiles:
        """The configuration file now; the API's own configuration when it cannot be used."""
        installation = self.installation
        if installation is None:
            return CurrentFiles(None, self.config, frozenset(), "no configuration file")
        try:
            data = installation.toml.read_bytes()
        except OSError as exc:
            return CurrentFiles(None, self.config, self.stored, _describe(exc))
        try:
            config = parse_config(data, installation.toml)
        except ValueError as exc:  # TOMLDecodeError, ValidationError, UnicodeDecodeError
            return CurrentFiles(_sha256(data), self.config, self.stored, _describe(exc))
        problem = installation.location_problem(config)
        if problem is not None:
            return CurrentFiles(_sha256(data), self.config, self.stored, problem)
        return CurrentFiles(_sha256(data), config, self.stored)

    def usable_files(self) -> CurrentFiles:
        """:attr:`files`, or 409 ``config_unreadable`` when nothing may be changed."""
        files = self.files
        if files.error is not None:
            log.warning("arcilogger.toml not usable: %s", files.error)
            raise admin_error("config_unreadable")
        return files

    def store(self) -> SecretStore:
        return self._installed().secret_store()

    def anchors(self) -> TrustAnchors:
        return self._installed().anchors()

    def roots(self, config: Config | None = None) -> list[Any]:
        """The roots in ``tsa.roots_dir`` (else the installation's ``kamusm-roots``)."""
        config = config or self.files.config
        folder = config.tsa.roots_dir
        if folder is None and self.installation is not None:
            folder = self.installation.roots_dir
        return installed_roots(folder)

    def approved_jars(self) -> frozenset[str]:
        if self.layout is None:
            return frozenset()
        try:
            jars = parse_document(JarsFile, self.layout.jars.read_bytes())
        except (OSError, DocumentError):
            return frozenset()
        return frozenset(entry.sha256 for entry in jars.approved)

    @property
    def firewall_managed(self) -> bool:
        return self.installation is not None and self.installation.firewall_state.is_file()

    @property
    def strict_outbound(self) -> bool:
        if self.installation is None:
            return False
        state = _read_json(self.installation.firewall_state)
        return state is not None and state.get("strict_outbound") is True

    def service(self, name: str) -> ServiceView:
        """A service's configuration as its status file reports it (the API: its own)."""
        if name == "ArciApi":
            watch = self.state.config_watch
            if watch is None:
                return ServiceView(None, True)
            own = ConfigStatus.model_validate(
                {**watch.status_block(), "loaded_at": watch.loaded_at}
            )
            return ServiceView(own, True)
        path = SERVICES[name](self.data)
        try:
            age = self.now.timestamp() - path.stat().st_mtime
        except OSError:
            return ServiceView(None, False)
        status = _read_json(path)
        block = config_status_of(status) if status is not None else None
        return ServiceView(block, age <= SERVICE_FRESH.total_seconds())

    def journal(self) -> Iterator[JournalLine]:
        """The agent's journal of the last 400 days (as long as it keeps it), newest first."""
        if self.layout is None:
            return
        today = site_day(self.now)
        for back in range(ORIGIN_DAYS + 1):
            yield from reversed(read_journal(self.layout, today - timedelta(days=back)))

    def config_origin(self) -> Literal["browser", "outside"]:
        """``browser`` when the file is the one the agent wrote last (the newest applied
        change of its journal), else ``outside`` (a hand edit, or a console restore)."""
        current = self.files.sha256
        if current is None:
            return "outside"
        for line in self.journal():
            if line.config_sha256 is not None and line.state in ("applied", "recovered"):
                return "browser" if line.config_sha256.to == current else "outside"
        return "outside"

    # -- the rules' context --------------------------------------------------------------------

    def retention_preview(self, candidate: Config) -> rules.RetentionPreview:
        """What retention would delete with ``candidate`` (``retention.plan``): the sealed days
        before the cutoff, and when the Retention task would start first."""
        today = site_day(self.now)
        cutoff, days = retention_plan(Layout(candidate.data_dir), candidate, today)
        return rules.RetentionPreview(
            cutoff=cutoff,
            days_to_delete=len(days),
            oldest=min(days) if days else None,
            newest=max(days) if days else None,
            first_run_at=_next_retention_run(self.now),
        )

    def test_recipients(self, db: Session, user: CurrentUser) -> frozenset[str]:
        """``alerts.email_recipients`` and the user's own e-mail (contract 2.9)."""
        listed = get_setting(db, "alerts.email_recipients")
        found = {str(item) for item in listed} if isinstance(listed, list) else set()
        own = db.scalar(select(User.email).where(User.id == user.id))
        if own:
            found.add(own.strip())
        return frozenset(found)

    def production_tested(self, db: Session) -> bool:
        """A stamp test on the Kamu SM production server succeeded (warning
        ``production_not_tested``)."""
        rows = db.scalars(
            select(AdminRequestRow.result).where(
                AdminRequestRow.kind == "action.tsa_test", AdminRequestRow.state == "succeeded"
            )
        )
        return any(
            isinstance(result, dict)
            and result.get("verified") is True
            and result.get("legal_grade_claimed") is True
            for result in rows
        )

    def context(
        self,
        db: Session,
        user: CurrentUser,
        *,
        uploads: Mapping[str, rules.UploadInfo] | None = None,
        encrypt: bool = False,
    ) -> rules.RuleContext:
        """What the section rules need, read now. ``encrypt``: secrets become Fernet tokens
        (a request to sign; a dry run shows the ops with a placeholder)."""
        anchors = self.anchors()
        store = self.store()
        agent = self.agent
        return rules.RuleContext(
            policy=self.policy.policy,
            anchors=anchors,
            stored_secrets=self.files.stored,
            now=self.now,
            java_candidates=agent.java_candidates if agent else (),
            local_addresses=agent.local_addresses if agent else (),
            installed_roots=frozenset(root.sha256 for root in self.roots()),
            approved_jars=self.approved_jars(),
            uploads=dict(uploads or {}),
            preflight=lambda candidate, names: seal_preflight(
                candidate, anchors=anchors, secrets=store, stored=names
            ),
            strict_outbound=self.strict_outbound,
            production_tested=self.production_tested(db),
            retention_preview=self.retention_preview,
            test_recipients=self.test_recipients(db, user),
            encrypt=store.encrypt_value if encrypt else None,
        )

    # -- the checks before a request is queued ---------------------------------------------------

    def require_enabled(self) -> None:
        """503 ``admin_disabled``: switched off (``-DisableBrowserAdministration``), or no
        valid ``policy.json``."""
        if self.layout is None or not self.policy.enabled:
            raise admin_error("admin_disabled")

    def signing_key(self) -> tuple[SigningKey, str]:
        """The API key and the installation's instance id (503 ``admin_channel_key_missing``)."""
        layout = self.layout
        if layout is None:
            raise admin_error("admin_disabled")
        try:
            return load_signing_key(layout.keys, "api"), load_keys(layout.keys).instance
        except KeysError as exc:
            log.error("administration requests cannot be signed: %s", exc)
            raise admin_error("admin_channel_key_missing") from exc

    def require_agent(self) -> list[str]:
        """503 ``admin_agent_unavailable`` without a heartbeat of the last 2 minutes; the
        warning ``agent_stale`` for one older than 30 seconds."""
        state = self.heartbeat
        if state == "missing":
            raise admin_error("admin_agent_unavailable")
        return ["agent_stale"] if state == "stale" else []

    def check_inbox(self) -> None:
        """429 ``admin_queue_full``: 20 requests or 100 MiB already wait in the inbox."""
        layout = self.layout
        if layout is None:
            return
        count = size = 0
        for folder in (layout.inbox, layout.uploads):
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for path in entries:
                try:
                    if path.is_file():
                        size += path.stat().st_size
                        count += folder == layout.inbox and path.suffix == ".req"
                except OSError:
                    continue
        if count >= INBOX_MAX_REQUESTS or size >= INBOX_MAX_BYTES:
            raise admin_error("admin_queue_full")

    def open_rows(
        self, db: Session, *, section: str | None = None, kind: str | None = None
    ) -> list[AdminRequestRow]:
        """The requests not finished yet (each brought up to date first)."""
        query = select(AdminRequestRow).where(AdminRequestRow.terminal.is_(False))
        if section is not None:
            query = query.where(
                AdminRequestRow.section == section, AdminRequestRow.kind.like("config.%")
            )
        if kind is not None:
            query = query.where(AdminRequestRow.kind == kind)
        rows = list(db.scalars(query.order_by(AdminRequestRow.created_at)))
        for row in rows:
            self.view(db, row)
        return [row for row in rows if not row.terminal]

    def check_section_free(self, db: Session, section: str, *, clients: bool = False) -> None:
        """409 ``admin_section_busy``: one open configuration request per section. A held
        Zamane client counts only for another client (``clients``)."""
        rows = self.open_rows(db, section=section)
        if any(clients or not _held_client(row) for row in rows):
            raise admin_error("admin_section_busy")

    def check_rate(
        self, db: Session, kind: str, *, every: timedelta, code: str = "admin_rate_limited"
    ) -> None:
        """429: a request of ``kind`` was made less than ``every`` ago."""
        newest = db.scalar(
            select(AdminRequestRow.created_at)
            .where(AdminRequestRow.kind == kind)
            .order_by(AdminRequestRow.created_at.desc())
            .limit(1)
        )
        if newest is not None and self.now - newest < every:
            raise rate_limited(code, every - (self.now - newest))

    def check_count(
        self, db: Session, kind: str, *, limit: int, since: datetime, until: datetime, code: str
    ) -> None:
        """429: ``limit`` requests of ``kind`` were made since ``since`` (retry at ``until``)."""
        made = list(
            db.scalars(
                select(AdminRequestRow.created_at).where(
                    AdminRequestRow.kind == kind, AdminRequestRow.created_at >= since
                )
            )
        )
        if len(made) >= limit:
            raise rate_limited(code, until - self.now)

    def check_window(
        self, db: Session, kind: str, *, limit: int, window: timedelta, code: str
    ) -> None:
        """429: ``limit`` requests of ``kind`` within the last ``window`` (retry once the oldest
        of them is older)."""
        made = sorted(
            db.scalars(
                select(AdminRequestRow.created_at).where(
                    AdminRequestRow.kind == kind, AdminRequestRow.created_at > self.now - window
                )
            )
        )
        if len(made) >= limit:
            raise rate_limited(code, made[-limit] + window - self.now)

    # -- writing a request -----------------------------------------------------------------------

    def _write(self, request_id: str, data: bytes) -> bool:
        """``<id>.req.tmp``, flushed, renamed to ``<id>.req`` (contract 2.5.1 step 4)."""
        layout = self.layout
        if layout is None:
            return False
        target = layout.request_file(request_id)
        temp = target.with_name(f"{target.name}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
            fsync_dir(target.parent)
        except OSError as exc:
            log.error("request %s not written to the inbox: %s", request_id, exc)
            temp.unlink(missing_ok=True)
            return False
        return True

    def submit(
        self,
        db: Session,
        user: CurrentUser,
        request: Request,
        *,
        kind: RequestKind,
        section: str | None = None,
        ops: Sequence[Mapping[str, JsonValue]] = (),
        options: Mapping[str, JsonValue] | None = None,
        probe: bool = True,
        changes: Mapping[str, Mapping[str, JsonValue]] | None = None,
        effects: Iterable[str] = (),
        warnings: Iterable[str] = (),
        guarded: bool = False,
        effective_at: datetime | None = None,
        files: Sequence[Mapping[str, JsonValue]] = (),
        params: Mapping[str, JsonValue] | None = None,
        busy_section: str | None = None,
    ) -> AdminAccepted:
        """Queue one agent request (the steps of the module text). ``section`` goes into the
        signed payload; ``busy_section`` (default ``section``) is the section the request keeps
        busy (a revert names its target's)."""
        self.require_enabled()
        key, instance = self.signing_key()
        warned = [*self.require_agent(), *warnings]
        self.check_inbox()
        busy = busy_section or section
        if busy is not None and kind.startswith("config."):
            self.check_section_free(db, busy)
        request_id = new_id()
        issued = self.now
        masked = {path: dict(change) for path, change in (changes or {}).items()}
        effect_list = list(dict.fromkeys(effects))
        reauth_at = user.reauthenticated_at
        audit = record_audit(
            db,
            actor=user,
            action="admin.request",
            target=f"admin-request:{request_id}",
            details={
                "kind": kind,
                "section": busy,
                "changes": masked,
                "files": [dict(item) for item in files],
                "guarded": guarded,
                "effective_at": isoformat_site(effective_at, "seconds") if effective_at else None,
                "reauth_at": isoformat_site(reauth_at, "seconds") if reauth_at else None,
                "effects": effect_list,
                **dict(params or {}),
            },
            ip=client_ip(request),
        )
        payload = AdminRequest.model_validate(
            {
                "id": request_id,
                "instance": instance,
                "issued_at": issued,
                "expires_at": issued + MAX_REQUEST_VALIDITY,
                "kind": kind,
                "section": section,
                "actor": {
                    "user_id": user.id,
                    "username": user.username,
                    "session": session_id(user.session_token_hash),
                    "ip": client_ip(request) or UNKNOWN_ADDRESS,
                    "reauth_at": reauth_at,
                },
                "audit_id": audit.id,
                "ops": [dict(op) for op in ops],
                "probe": probe,
                "options": dict(options or {}),
            }
        )
        data = sign_admin_request(payload, key)
        row = AdminRequestRow(
            id=request_id,
            kind=kind,
            section=busy,
            channel="agent",
            state="queued",
            terminal=False,
            requested_by_id=user.id,
            requested_by=user.username,
            created_at=issued,
            updated_at=issued,
            expires_at=payload.expires_at,
            effective_at=effective_at,
            params={
                "changes": _changes_list(masked),
                "effects": effect_list,
                "warnings": list(dict.fromkeys(warned)),
                "files": [dict(item) for item in files],
                **dict(params or {}),
            },
            audit_id=audit.id,
        )
        db.add(row)
        db.commit()  # the request and its audit entry exist before the agent can see the file
        if not self._write(request_id, data):
            row.state, row.terminal, row.error_code = "failed", True, "write_failed"
            row.updated_at = self.now
            db.commit()
            raise admin_error("admin_inbox_write_failed")
        return self.accepted(row, warnings=warned, guarded=guarded)

    def submit_worker(
        self,
        db: Session,
        user: CurrentUser,
        request: Request,
        *,
        kind: Literal["worker.test_email", "worker.lease_rebuild"],
        params: Mapping[str, JsonValue],
        audited: Mapping[str, JsonValue],
        expires_at: datetime | None = None,
    ) -> AdminAccepted:
        """Queue a worker request (``channel = 'worker'``; no file, no secret). ``audited``:
        what ``admin.request`` records of ``params`` (an address masked)."""
        request_id = new_id()
        reauth_at = user.reauthenticated_at
        audit = record_audit(
            db,
            actor=user,
            action="admin.request",
            target=f"admin-request:{request_id}",
            details={
                "kind": kind,
                "section": None,
                "changes": {},
                "files": [],
                "guarded": False,
                "effective_at": None,
                "reauth_at": isoformat_site(reauth_at, "seconds") if reauth_at else None,
                "effects": [],
                **dict(audited),
            },
            ip=client_ip(request),
        )
        row = AdminRequestRow(
            id=request_id,
            kind=kind,
            channel="worker",
            state="queued",
            terminal=False,
            requested_by_id=user.id,
            requested_by=user.username,
            created_at=self.now,
            updated_at=self.now,
            expires_at=expires_at,
            params=dict(params),
            audit_id=audit.id,
        )
        db.add(row)
        db.flush()
        return self.accepted(row)

    def accepted(
        self,
        row: AdminRequestRow,
        *,
        warnings: Iterable[str] = (),
        guarded: bool = False,
        state: Literal["queued", "cancelled"] = "queued",
    ) -> AdminAccepted:
        params = row.params or {}
        return AdminAccepted(
            request_id=row.id,
            short_id=row.id[:8],
            state=state,
            status_url=f"/api/v1/admin/requests/{row.id}",
            guarded=guarded,
            effective_at=row.effective_at,
            changes=_changes_dict(params.get("changes")),
            effects=list(params.get("effects") or []),
            warnings=[code_message(code) for code in dict.fromkeys(warnings)],
        )

    # -- uploads (contract 2.4.4) ---------------------------------------------------------------

    def stage_upload(
        self, db: Session, user: CurrentUser, request: Request, kind: UploadKind, data: bytes
    ) -> tuple[UploadMeta, rules.UploadInfo]:
        """``<id>.bin`` (``.tmp`` then rename) and ``<id>.json`` in ``admin\\inbox\\uploads``,
        inspected for its kind (a file that is not one is removed again: 422) and audited
        ``admin.upload``."""
        self.require_enabled()
        self.check_inbox()
        layout = self._installed().admin
        upload_id = new_id()
        meta = UploadMeta(
            id=upload_id,
            kind=kind,
            sha256=_sha256(data),
            size=len(data),
            created_at=self.now,
            user_id=user.id,
        )
        target = layout.upload_data(upload_id)
        temp = target.with_name(f"{target.name}.tmp")
        try:
            layout.uploads.mkdir(parents=True, exist_ok=True)
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
            layout.upload_meta(upload_id).write_bytes(dump_document(meta))
        except OSError as exc:
            log.error("upload %s not staged: %s", upload_id, exc)
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise admin_error("admin_inbox_write_failed") from exc
        info = rules.inspect_upload(upload_id, kind, target, now=self.now)
        refused = (
            ("root_certificate_invalid" if info.root is None or not info.root.ok else None)
            if kind == "kamusm_root"
            else ("root_certificate_invalid" if info.bundle is None or not info.bundle.ok else None)
            if kind == "smtp_ca"
            else ("zamane_archive_invalid" if info.zamane is None or not info.zamane.ok else None)
        )
        if refused is not None:
            target.unlink(missing_ok=True)
            layout.upload_meta(upload_id).unlink(missing_ok=True)
            raise admin_error(refused)
        record_audit(
            db,
            actor=user,
            action="admin.upload",
            target=f"admin-upload:{upload_id}",
            details={"kind": kind, "sha256": meta.sha256, "size": meta.size},
            ip=client_ip(request),
        )
        return meta, info

    def uploads(self, user: CurrentUser, ids: Iterable[str]) -> dict[str, rules.UploadInfo]:
        """The user's own uploads ``ids`` that are not older than 30 minutes, inspected (an
        unknown, expired or someone else's upload is left out: the rules answer
        ``upload_not_found``)."""
        found: dict[str, rules.UploadInfo] = {}
        layout = self.layout
        if layout is None:
            return found
        for upload_id in ids:
            try:
                meta = parse_document(UploadMeta, layout.upload_meta(upload_id).read_bytes())
            except (OSError, ValueError):  # ValueError: not an id, DocumentError
                continue
            data = layout.upload_data(upload_id)
            if (
                meta.user_id != user.id
                or self.now - meta.created_at > UPLOAD_LIFETIME
                or not data.is_file()
                or data.stat().st_size != meta.size
            ):
                continue
            found[upload_id] = rules.inspect_upload(upload_id, meta.kind, data, now=self.now)
        return found

    # -- reading a request back ----------------------------------------------------------------

    def remove_queued(self, request_id: str) -> bool:
        """Remove a request file the agent has not taken yet (a cancel, an expiry)."""
        if self.layout is None:
            return False
        try:
            self.layout.request_file(request_id).unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.error("request file %s not removed: %s", request_id, exc)
            return False
        return True

    def view(self, db: Session, row: AdminRequestRow) -> AdminRequestOut:
        """The request as the browser sees it; a state the API sees first is written into the
        row (``admin_requests`` keeps the history after the agent's cleanup)."""
        if row.channel == "worker":
            return self._out(row, row.state, [], None, row.result, row.error_code, [], None)
        status = read_status(self.layout, row.id) if self.layout is not None else None
        steps = [
            RequestStepOut(step=entry.step, state=entry.state, at=entry.at, code=entry.code)
            for entry in (status.steps if status is not None else ())
        ]
        warnings = [ref.code for ref in status.warnings] if status is not None else []
        warnings += [
            code for code in (row.params or {}).get("warnings") or [] if code not in warnings
        ]
        version = status.version if status is not None else None
        state: str
        if row.terminal:
            state = row.state
        elif status is None:
            state = self._unclaimed(row)
        else:
            state = status.state
        if state in ("applied", "active", "partially_active") and status is not None:
            activation, extra, notes = self._activation(row, status)
            steps += extra
            warnings += [code for code in notes if code not in warnings]
            if not row.terminal:
                state = activation
        result = status.result.model_dump(mode="json") if status and status.result else None
        error = status.error.code if status is not None and status.error is not None else None
        field_path = status.error.field if status is not None and status.error is not None else None
        if row.terminal and row.error_code and error is None:
            error = row.error_code
        self._mirror(db, row, state, status, result, error)
        return self._out(row, state, steps, version, result, error, warnings, field_path)

    def _unclaimed(self, row: AdminRequestRow) -> str:
        if row.expires_at is not None and self.now > row.expires_at + EXPIRED_GRACE:
            self.remove_queued(row.id)
            return "expired"
        if self.now - row.created_at > QUEUED_WAIT or self.heartbeat != "ok":
            return "waiting_for_agent"
        return "queued"

    def _activation(
        self, row: AdminRequestRow, status: RequestStatus
    ) -> tuple[str, list[RequestStepOut], list[str]]:
        """``applied`` → ``active`` / ``partially_active`` from what the services report."""
        version = status.version
        if version is None:
            return "active", [], []
        applied_at = status.updated_at
        effects = [str(effect) for effect in (row.params or {}).get("effects") or []]
        waited = self.now - applied_at
        steps: list[RequestStepOut] = []
        notes: list[str] = []
        pending = partial = False

        def step(name: str, state: str, code: str | None = None) -> None:
            message = texts(code) if code else (None, None)
            steps.append(
                RequestStepOut(
                    step=name,
                    state=state,
                    at=self.now,
                    code=code,
                    message_tr=message[0],
                    message_en=message[1],
                )
            )

        for effect, name in RELOADS.items():
            if effect not in effects:
                continue
            service = self.service(name)
            block = service.config
            if not service.running:
                step(effect, "skipped")
            elif block is not None and _taken(block.sha256, block.loaded_at, version, applied_at):
                step(effect, "done")
            elif waited > SERVICE_FRESH:
                step(effect, "warning", "service_not_reloaded")
                notes.append("service_not_reloaded")
                partial = True
            else:
                step(effect, "running")
                pending = True
        system = self.system
        for effect in SYSTEM_EFFECTS:
            if effect not in effects:
                continue
            synced = system is not None and _taken(
                system.applied_config_sha256, system.at, version, applied_at
            )
            if effect == "system.firewall" and synced and system is not None:
                collector = self.service("ArciCollector")
                acknowledged = (
                    "reload.collector" not in effects
                    or not collector.running
                    or system.collector_ack_sha256 == version.to
                    or (
                        system.collector_ack_sha256 is not None
                        and system.at >= applied_at
                        and system.collector_ack_sha256 != version.from_
                    )
                )
                synced = acknowledged
            if system is not None and not system.ok and system.at >= applied_at:
                step(effect, "failed", "system_sync_failed")
                partial = True
            elif synced and system is not None:
                if effect == "system.firewall" and not system.firewall.managed:
                    step(effect, "skipped", "firewall_not_managed")
                    notes.append("firewall_not_managed")
                else:
                    step(effect, "done")
            elif waited > SYSTEM_TASK_TIMEOUT:
                step(effect, "failed", "system_sync_timeout")
                partial = True
            else:
                step(effect, "running")
                pending = True
        if partial:
            return "partially_active", steps, notes
        return ("applied" if pending else "active"), steps, notes

    def _mirror(
        self,
        db: Session,
        row: AdminRequestRow,
        state: str,
        status: RequestStatus | None,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        if row.terminal or (state == row.state and status is None):
            return
        effective = status.effective_at if status is not None else row.effective_at
        if (
            state == row.state
            and error == row.error_code
            and result == row.result
            and effective == row.effective_at
        ):
            return
        row.state = state
        row.terminal = state in TERMINAL_STATES
        row.error_code = error
        row.result = result
        row.rolled_back = bool(status.rolled_back) if status is not None else row.rolled_back
        row.effective_at = effective
        row.updated_at = self.now
        db.flush()

    def _out(
        self,
        row: AdminRequestRow,
        state: str,
        steps: list[RequestStepOut],
        version: Any,
        result: dict[str, Any] | None,
        error: str | None,
        warnings: Sequence[str],
        field_path: str | None,
    ) -> AdminRequestOut:
        label_tr, label_en = STATE_LABELS.get(state, (state, state))
        for item in steps:
            if item.code and item.message_tr is None:
                item.message_tr, item.message_en = texts(item.code)
        problem = None
        if error is not None:
            message_tr, message_en = texts(error)
            problem = RequestError(
                code=error, message_tr=message_tr, message_en=message_en, field=field_path
            )
        manual_tr, manual_en = (
            manual_steps(version.number if version else None, error)
            if state == "needs_attention"
            else (None, None)
        )
        params = row.params or {}
        return AdminRequestOut(
            id=row.id,
            short_id=row.id[:8],
            kind=row.kind,
            section=row.section,
            state=state,
            terminal=state in TERMINAL_STATES,
            label_tr=label_tr,
            label_en=label_en,
            requested_by=row.requested_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
            effective_at=row.effective_at,
            version=(
                RequestVersion.model_validate(
                    {"number": version.number, "from": version.from_, "to": version.to}
                )
                if version is not None
                else None
            ),
            steps=steps,
            changes=_changes_dict(params.get("changes")),
            result=result,
            error=problem,
            rolled_back=row.rolled_back,
            warnings=[code_message(code) for code in dict.fromkeys(warnings) if code in WARNINGS],
            manual_steps_tr=manual_tr,
            manual_steps_en=manual_en,
        )


def get_channel(request: Request) -> Channel:
    state = get_state(request)
    return Channel(state, state.require_config())


ChannelDep = Annotated[Channel, Depends(get_channel)]


def require_reauth(user: CurrentUser, needed: bool) -> None:
    """403 ``reauth_required`` when the change needs a recent re-authentication."""
    if needed:
        ensure_recent_auth(user)


def find_row(db: Session, request_id: str) -> AdminRequestRow:
    """The row of a request id (404 ``admin_request_not_found``)."""
    row = db.get(AdminRequestRow, request_id) if len(request_id) == 32 else None
    if row is None:
        raise admin_error("admin_request_not_found")
    return row


def day_of(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
