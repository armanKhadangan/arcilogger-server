"""``/api/v1/admin/actions`` — the tests and the lease rebuild of Ayarlar (ADR-0014; contract
2.11.3).

Permission ``settings.manage``; nothing here is licence-gated. Each action is a request like a
change of the configuration (202 with ``status_url``; :mod:`arcilogger_api.admin_channel`):

* ``POST /admin/actions/tsa-test`` ``{"confirm_credit": true}`` — "Damga testi": one real time
  stamp from the configured server, verified (``action.tsa_test``, run by the Requests task).
  It uses one Kamu SM credit, so the body must say so (422 ``tsa_test_confirm_required``); one
  every 5 minutes (429 ``admin_rate_limited``) and ``policy.tsa_test_per_day`` per site day (429
  ``tsa_test_limit``). The result is the JSON of ``arcilogger-core tsa-test`` without the token.
* ``POST /admin/actions/replica-test`` ``{}`` — "Replika testi": the Requests task connects to
  the share as the Replica task does (``action.replica_test``); one a minute.
* ``POST /admin/actions/test-email`` ``{"to"}`` — "Test e-postası" through ``[smtp]``, sent by
  the worker (``worker.test_email``, a row of the worker channel: no file, no secret). ``to``
  must be one of ``alerts.email_recipients`` or the user's own address (422
  ``test_email_recipient_not_allowed``); the server and the sender must be set (409
  ``smtp_not_configured``); five an hour. The audit entry and the result hold the address
  masked (``b***@ornek.example``).
* ``POST /admin/actions/lease-rebuild`` ``{"from", "to"}`` — the lease timeline of at most 31
  days rebuilt by the worker (``worker.lease_rebuild``), not in the future and not before the
  first DHCP or ARP record (422 ``lease_rebuild_range``); the result counts the days as they are
  done.

"Şimdi mühürle" and "Kontör sorgula" are ``POST /seals/{day}/seal`` and ``POST /credits/query``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from arcilogger_api.admin_channel import AdminAccepted, ChannelDep, admin_error
from arcilogger_api.deps import DbSession
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.timeutil import day_window, site_day
from arcilogger_worker.admin_jobs import LEASE_REBUILD_DAYS, mask_address
from arcilogger_worker.correlation.leases import first_evidence_time

router = APIRouter(prefix="/admin/actions", tags=["admin"])

SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
#: One stamp test every 5 minutes, one replica test a minute, five test e-mails an hour
#: (contract 2.5.1).
TSA_TEST_EVERY: Final = timedelta(minutes=5)
REPLICA_TEST_EVERY: Final = timedelta(minutes=1)
TEST_EMAILS: Final = 5
TEST_EMAIL_WINDOW: Final = timedelta(hours=1)
#: A test e-mail the worker has not sent by then is ``expired`` (the person stopped waiting).
TEST_EMAIL_VALIDITY: Final = timedelta(minutes=10)


class TsaTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: "The test uses one Kamu SM credit": must be true.
    confirm_credit: bool = False


@router.post("/tsa-test", status_code=status.HTTP_202_ACCEPTED)
def tsa_test(
    body: TsaTestBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """One real time stamp from the configured server, verified (uses one credit)."""
    if not body.confirm_credit:
        raise admin_error("tsa_test_confirm_required")
    channel.require_enabled()
    channel.check_rate(db, "action.tsa_test", every=TSA_TEST_EVERY)
    start, end = day_window(site_day(channel.now))
    channel.check_count(
        db,
        "action.tsa_test",
        limit=channel.policy.policy.tsa_test_per_day,
        since=start,
        until=end,
        code="tsa_test_limit",
    )
    return channel.submit(db, user, request, kind="action.tsa_test")


@router.post("/replica-test", status_code=status.HTTP_202_ACCEPTED)
def replica_test(
    request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Connect to the replica share as the Replica task does."""
    channel.require_enabled()
    channel.check_rate(db, "action.replica_test", every=REPLICA_TEST_EVERY)
    return channel.submit(db, user, request, kind="action.replica_test")


class TestEmailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(max_length=254)


@router.post("/test-email", status_code=status.HTTP_202_ACCEPTED)
def test_email(
    body: TestEmailBody, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """A test e-mail through the configured server, sent by the worker."""
    to = body.to.strip()
    if to.lower() not in {item.lower() for item in channel.test_recipients(db, user)}:
        raise admin_error("test_email_recipient_not_allowed")
    smtp = channel.files.config.smtp
    if not smtp.host or not smtp.from_address:
        raise admin_error("smtp_not_configured")
    channel.check_window(
        db,
        "worker.test_email",
        limit=TEST_EMAILS,
        window=TEST_EMAIL_WINDOW,
        code="admin_rate_limited",
    )
    return channel.submit_worker(
        db,
        user,
        request,
        kind="worker.test_email",
        params={"to": to},
        audited={"to": mask_address(to)},
        expires_at=channel.now + TEST_EMAIL_VALIDITY,
    )


class LeaseRebuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


@router.post("/lease-rebuild", status_code=status.HTTP_202_ACCEPTED)
def lease_rebuild(
    body: LeaseRebuildBody,
    request: Request,
    db: DbSession,
    user: SettingsDep,
    channel: ChannelDep,
) -> AdminAccepted:
    """Rebuild the lease timeline of ``from`` … ``to`` (site days), done by the worker."""
    first = first_evidence_time(db)
    if (
        body.to < body.from_
        or (body.to - body.from_).days >= LEASE_REBUILD_DAYS
        or body.to > site_day(channel.now)
        or first is None
        or body.from_ < site_day(first)
    ):
        raise admin_error("lease_rebuild_range")
    span = {"from": body.from_.isoformat(), "to": body.to.isoformat()}
    return channel.submit_worker(
        db, user, request, kind="worker.lease_rebuild", params=span, audited=span
    )
