"""``/api/v1/compliance`` — Uyum Durumu (Wave 4 contract 2.2, 2.3.4, 2.3.5).

Not mounted yet: ``routers/`` mounts every module it finds, and a mounted area needs its row in
LISANS/LICENSING, so W4-F mounts this router with a two-line module (contract D2); until then the
tests mount it themselves. 5651 Temel: nothing here is licence-gated (contract 0.1 item 2).

* ``GET /compliance`` — the ten cards (:func:`arcilogger_api.compliance.cards.compute_status`),
  permission ``compliance.read`` (admin, auditor). Not audited: the cards carry no data about
  log subjects (as the Panel). One answer is kept :data:`CACHE_SECONDS` seconds for every user of
  this process (the answer is the same for all; links are filtered by the web app, D13);
* ``GET /compliance/report.pdf`` — the Uyum Durumu Raporu (:mod:`.pdf`), ``compliance.read``,
  computed fresh, never stored nor time-stamped; audited ``compliance.report_export`` with the
  file's SHA-256 and the number of cards per tone before the response is sent;
* ``POST /compliance/config-backup`` — the administrator confirms the offline copy of the
  configuration files (``settings.manage``): audited ``compliance.config_backup_confirm`` with the
  current SHA-256 of ``arcilogger.toml``, which card 10 compares with the file. A file that
  cannot be read is refused (409 ``config_backup_unreadable``); a repeated confirmation is a new
  entry. The kept cards are dropped, so the page shows card 10 at once.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, Response

from arcilogger_api.admin_channel import ChannelDep
from arcilogger_api.audit import record_audit
from arcilogger_api.compliance.cards import CONFIG_BACKUP_CONFIRM, compute_status
from arcilogger_api.compliance.models import ComplianceStatus, ConfigBackupOut
from arcilogger_api.compliance.pdf import render_report
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep, TtlCache
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.timeutil import to_site, utc_now

router = APIRouter(prefix="/compliance", tags=["compliance"])

ReaderDep = Annotated[CurrentUser, Depends(require("compliance.read"))]
SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]

#: How long one computed answer of ``GET /compliance`` is kept (contract 2.2).
CACHE_SECONDS: Final = 30.0
#: Audit action of ``GET /compliance/report.pdf`` (contract 2.7).
REPORT_EXPORT: Final = "compliance.report_export"

#: The refusals of this router (contract 2.7), Turkish first.
MESSAGES = {
    "config_backup_unreadable": (
        "Yapılandırma dosyası okunamadı; yedek onayı kaydedilmedi.",
        "The configuration file could not be read; the backup confirmation was not recorded.",
    ),
}


def _error(status: int, code: str) -> ApiError:
    return ApiError(status, code, *MESSAGES[code])


def status_cache(request: Request) -> TtlCache[ComplianceStatus]:
    """The kept answer of this application (``app.state.arci_compliance``)."""
    cache: TtlCache[ComplianceStatus] | None = getattr(request.app.state, "arci_compliance", None)
    if cache is None:
        cache = request.app.state.arci_compliance = TtlCache[ComplianceStatus](CACHE_SECONDS)
    return cache


@router.get("")
def compliance_status(
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    channel: ChannelDep,
    user: ReaderDep,
) -> ComplianceStatus:
    """The ten cards (kept :data:`CACHE_SECONDS` seconds; not audited)."""
    return status_cache(request).get_or_compute(
        lambda: compute_status(db, layout, config, channel, utc_now())
    )


@router.get(
    "/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def compliance_report(
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    channel: ChannelDep,
    user: ReaderDep,
) -> Response:
    """The Uyum Durumu Raporu of the cards now (audited with its SHA-256)."""
    now = utc_now()
    status = compute_status(db, layout, config, channel, now)
    report = render_report(status, display_name=user.display_name, username=user.username)
    record_audit(
        db,
        actor=user,
        action=REPORT_EXPORT,
        target=None,
        details={"sha256": report.sha256, **status.counts.model_dump()},
        ip=client_ip(request),
    )
    stamp = to_site(now).strftime("%Y%m%d-%H%M")
    return Response(
        content=report.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ARCILOGGER-uyum-durumu-{stamp}.pdf"',
            "X-Arci-Sha256": report.sha256,
        },
    )


@router.post("/config-backup")
def confirm_config_backup(
    request: Request, db: DbSession, channel: ChannelDep, user: SettingsDep
) -> ConfigBackupOut:
    """Record the offline copy of the configuration files for the file as it is now."""
    sha256 = channel.files.sha256
    if sha256 is None:
        raise _error(409, "config_backup_unreadable")
    row = record_audit(
        db,
        actor=user,
        action=CONFIG_BACKUP_CONFIRM,
        target=None,
        details={"config_sha256": sha256},
        ip=client_ip(request),
    )
    confirmed_at = to_site(row.time)
    db.commit()  # committed first: a computation after the invalidation must see the entry
    status_cache(request).invalidate()
    return ConfigBackupOut(confirmed_at=confirmed_at, config_sha256=sha256)
