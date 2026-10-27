"""``/api/v1/raw`` — one raw log line by reference (spec 9.4 "links to the raw lines", 12.3 page 5
"Raw line view always available").

``GET /raw?source=fortigate&day=2026-10-12&line=17[&offset=2345]`` returns the line exactly as
stored — from ``spool/<source>/<day>.log`` while the day is not sealed, else from the sealed
``vault/<Y>/<M>/<D>/<source>.log.gz`` — as base64 (byte-exact) and as display text (the spool's
``#ooo`` escapes visible, undecodable bytes as ``\\xNN``, control and bidi characters as
``\\uXXXX``), with its SHA-256, receive time and sender.

* **Access:** ``search.read`` **or** ``lookup.run`` (today: administrators and auditors) and the
  accepted KVKK notice (raw lines are person-level data). Every view is audited (``raw.view``:
  the reference and the line's SHA-256, never its content).
* **Path safety:** the source must be a valid spool folder name (no separators, no leading dot,
  not ``_audit``) **and** a known source — configured in ``arcilogger.toml`` (also
  ``<network source>-<ip>`` for address ranges), seen by the ingest service (``sources``) or an
  unknown sender's ``unknown-<ip>`` folder that exists in ``spool/`` (Canlı Akış shows a new
  sender's lines before ingest has loaded a batch of them and added its ``sources`` row); the
  file must lie inside ``spool/`` or ``vault/``.
* **Errors:** ``raw_unreadable`` (409) — the sealed file's compressed data is damaged before the
  line; ``raw_access_denied`` (503) — the API service has no read right on the file (Windows
  folder permissions; the file itself is fine); ``raw_io_error`` (503) — any other read error.
* **Cost:** the line number is always verified by counting line feeds and a given ``offset``
  must match. Spool files keep line-start checkpoints in memory (the first view in a file reads
  up to the line, later views a few MiB); a sealed day is decompressed up to the line on every
  view. See :mod:`arcilogger_api.rawlines`.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
from datetime import date, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import ensure_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.rawlines import RawReferenceError, read_line, valid_source_name
from arcilogger_api.security import CurrentUser, CurrentUserDep, client_ip
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_db.models import Source

router = APIRouter(prefix="/raw", tags=["raw"])
log = logging.getLogger("arcilogger.api.raw")

#: Either permission opens the raw-line view (search results and lookup evidence link to it).
READ_PERMISSIONS: Final = ("search.read", "lookup.run")
#: Spool folder of an unknown sender: ``unknown-<ip>`` (the collector; ``:`` written as ``-``).
UNKNOWN_PREFIX: Final = "unknown-"
MAX_LINE_NUMBER: Final = 2**31 - 1

MESSAGES: Final = {
    "raw_source_invalid": (
        "Geçersiz kaynak adı.",
        "Invalid source name.",
    ),
    "raw_source_unknown": (
        "Bu adla bir log kaynağı tanımlı değil veya hiç görülmedi.",
        "No log source with this name is configured or has been seen.",
    ),
    "raw_not_found": (
        "Bu ham kayıt satırı bulunamadı (kaynak, gün veya satır numarası hatalı ya da dosya "
        "saklama süresi dolduğu için silinmiş olabilir).",
        "This raw log line was not found (wrong source, day or line number, or the file was "
        "deleted after the retention period).",
    ),
    "raw_reference_mismatch": (
        "Satır numarası ile bayt konumu birbirini tutmuyor; satır gösterilmedi.",
        "The line number and the byte offset do not agree; the line is not shown.",
    ),
    "raw_unreadable": (
        "Mühürlü ham kayıt dosyasının sıkıştırılmış verisi bu satırdan önce bozuk; satır "
        "okunamadı. Sistem yöneticisine bildirin (yedek kopyadan geri yüklenmeli).",
        "The compressed data of the sealed raw log file is damaged before this line; the line "
        "cannot be read. Tell the administrator (restore it from the replica).",
    ),
    "raw_access_denied": (
        "ARCILOGGER API hizmetinin bu ham kayıt dosyasını okuma izni yok (kurulumdaki klasör "
        "izinleri). Dosya bozuk değildir; sistem yöneticisine bildirin.",
        "The ARCILOGGER API service may not read this raw log file (folder permissions of the "
        "installation). The file is not damaged; tell the administrator.",
    ),
    "raw_io_error": (
        "Ham kayıt dosyası şu anda okunamadı (disk veya dosya sistemi hatası); biraz sonra "
        "tekrar deneyin, sürerse sistem yöneticisine bildirin.",
        "The raw log file cannot be read right now (disk or file system error); try again "
        "later and tell the administrator if it persists.",
    ),
}
_STATUS: Final = {
    "raw_source_invalid": 422,
    "raw_source_unknown": 404,
    "raw_not_found": 404,
    "raw_reference_mismatch": 409,
    "raw_unreadable": 409,
    "raw_access_denied": 503,
    "raw_io_error": 503,
}


def _error(code: str) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(_STATUS[code], code, text_tr, text_en)


def get_raw_reader(db: DbSession, user: CurrentUserDep) -> CurrentUser:
    """``search.read`` or ``lookup.run``, password changed, KVKK notice accepted."""
    if user.must_change_password:
        raise ApiError(403, "password_change_required")
    if not any(user.has(permission) for permission in READ_PERMISSIONS):
        raise ApiError(403, "forbidden")
    ensure_privacy_notice(db, user)
    return user


ReaderDep = Annotated[CurrentUser, Depends(get_raw_reader)]


def _unknown_sender_folder(layout: Layout, source: str) -> bool:
    """``unknown-<ip>`` (the collector's name for an unknown sender) with a folder in ``spool/``."""
    address = source.removeprefix(UNKNOWN_PREFIX)
    if address == source:
        return False
    try:
        ipaddress.ip_address(address if "." in address else address.replace("-", ":"))
    except ValueError:
        return False
    try:
        return (layout.spool / source).is_dir()
    except OSError:
        return False


def known_source(db: Session, config: Config, source: str, layout: Layout | None = None) -> bool:
    """Configured (exactly, or ``<network source>-<ip>`` of a configured range), seen, or —
    with ``layout`` — an unknown sender's ``unknown-<ip>`` spool folder."""
    if layout is not None and _unknown_sender_folder(layout, source):
        return True
    if any(configured.name == source for configured in config.sources):
        return True
    for configured in config.sources:
        prefix = f"{configured.name}-"
        if configured.is_network and source.startswith(prefix):
            address = source.removeprefix(prefix)
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if config.source_name_for(str(ip)) == source:
                return True
    return db.scalar(select(Source.name).where(Source.name == source)) is not None


class RawLineOut(BaseModel):
    source: str
    day: date
    line: int
    offset: int
    #: ``spool`` (not sealed yet) or ``vault`` (sealed day).
    origin: str
    #: Data-directory relative path of the file the line was read from.
    file: str
    #: Bytes of the line without its line feed.
    length: int
    sha256: str
    raw_base64: str
    #: Display text (escapes visible, see the module documentation).
    text: str
    received: datetime | None
    sender: str | None


@router.get("")
def raw_line(
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: ReaderDep,
    source: Annotated[str, Query(min_length=1, max_length=81)],
    day: date,
    line: Annotated[int, Query(ge=1, le=MAX_LINE_NUMBER)],
    offset: Annotated[int | None, Query(ge=0, le=2**62)] = None,
) -> RawLineOut:
    """One raw log line, byte-exact, with its position verified."""
    if not valid_source_name(source):
        raise _error("raw_source_invalid")
    if not known_source(db, config, source, layout):
        raise _error("raw_source_unknown")
    try:
        found = read_line(layout, source, day, line, offset)
    except RawReferenceError as exc:
        if exc.code in ("raw_access_denied", "raw_io_error", "raw_unreadable"):
            log.error("raw line %s/%s/%s: %s", source, day, line, exc)
        raise _error(exc.code) from None
    record = found.record()
    record_audit(
        db,
        actor=user,
        action="raw.view",
        target=f"raw:{source}/{day.isoformat()}/{line}",
        details={
            "source": source,
            "day": day.isoformat(),
            "line": line,
            "offset": found.offset,
            "origin": found.origin,
            "sha256": found.sha256,
        },
        ip=client_ip(request),
    )
    return RawLineOut(
        source=source,
        day=day,
        line=line,
        offset=found.offset,
        origin=found.origin,
        file=found.file,
        length=len(found.data),
        sha256=found.sha256,
        raw_base64=base64.b64encode(found.data).decode("ascii"),
        text=found.text,
        received=record.received if record else None,
        sender=record.sender if record else None,
    )
