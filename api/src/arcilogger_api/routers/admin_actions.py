"""``/api/v1/admin/actions`` — the tests and the lease rebuild of Ayarlar (ADR-0014; contract
2.11.3).

Permission ``settings.manage``; nothing here is licence-gated. Each action is a request like a
change of the configuration (202 with ``status_url``; :mod:`arcilogger_api.admin_channel`):

* ``POST /admin/actions/tsa-test`` ``{"confirm_credit": true}`` — "Damga testi": one real time
  stamp from the configured server, verified (``action.tsa_test``, run by the Requests task).
  It uses one Kamu SM credit, so the body must say so (422 ``tsa_test_confirm_required``); one
  every 5 minutes (429 ``admin_rate_limited``) and ``policy.tsa_test_per_day`` per site day (429
  ``tsa_test_limit``). The result is the JSON of ``arcilogger-core tsa-test`` without the token.

"Şimdi mühürle" and "Kontör sorgula" are ``POST /seals/{day}/seal`` and ``POST /credits/query``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict

from arcilogger_api.admin_channel import AdminAccepted, ChannelDep, admin_error
from arcilogger_api.deps import DbSession
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.timeutil import day_window, site_day

router = APIRouter(prefix="/admin/actions", tags=["admin"])

SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
#: One stamp test every 5 minutes (contract 2.5.1).
TSA_TEST_EVERY: Final = timedelta(minutes=5)


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
