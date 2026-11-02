"""``/api/v1/credits`` — Kamu SM kontör geçmişi (spec 6.1, 12.3 page 9 "Kontör" widget).

``seal.read``. The scheduled ``\\ARCILOGGER\\Credit`` task (``arcilogger-core credit``) asks the
time-stamp provider for the remaining credit once a day and appends the answer to
``state/credit.jsonl`` (:mod:`arcilogger_core.credit`); ``GET /credits`` only reads that file.
The alert threshold is ``[tsa] credit_alert_threshold`` of ``arcilogger.toml``.

``POST /credits/query`` (``seal.manage``) is "Kontör sorgula" (ADR-0014; contract 2.9): the
administration agent hands the query to the ``\\ARCILOGGER\\Requests`` task (LOCAL SERVICE),
which asks the provider with the stored Kamu SM credentials and appends the answer to
``state/credit.jsonl`` as the daily run does. At most one query a minute (429
``admin_rate_limited``); answer 202 with the request to follow, whose result is ``{credits,
threshold, below_threshold}``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from arcilogger_api.admin_channel import AdminAccepted, ChannelDep
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.sealinfo import credit_history
from arcilogger_api.security import CurrentUser, require

router = APIRouter(prefix="/credits", tags=["seals"])

ReaderDep = Annotated[CurrentUser, Depends(require("seal.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require("seal.manage"))]
#: "Kontör sorgula" at most this often (contract 2.5.1).
QUERY_EVERY: Final = timedelta(minutes=1)


class CreditCheck(BaseModel):
    time: datetime
    provider: str | None
    #: ``None``: the provider could not be asked, or it has no credit concept (mock, RFC 3161).
    credits: int | None
    error: str | None


class Credits(BaseModel):
    provider: str
    legal_grade: bool
    threshold: int
    latest: CreditCheck | None
    #: The last known number (the newest check that returned one).
    remaining: int | None
    below_threshold: bool
    history: list[CreditCheck]
    unreadable_lines: int


@router.get("")
def credits(user: ReaderDep, layout: LayoutDep, config: ConfigDep) -> Credits:
    """Credit checks, oldest first (at most about a year), and the alert threshold."""
    entries, bad = credit_history(layout)
    history = [CreditCheck.model_validate(entry) for entry in entries]
    known = [entry.credits for entry in history if entry.credits is not None]
    remaining = known[-1] if known else None
    threshold = config.tsa.credit_alert_threshold
    return Credits(
        provider=config.tsa.provider,
        legal_grade=config.tsa.legal_grade,
        threshold=threshold,
        latest=history[-1] if history else None,
        remaining=remaining,
        below_threshold=remaining is not None and remaining < threshold,
        history=history,
        unreadable_lines=bad,
    )


@router.post("/query", status_code=status.HTTP_202_ACCEPTED)
def query_credits(
    request: Request, db: DbSession, user: ManagerDep, channel: ChannelDep
) -> AdminAccepted:
    """ "Kontör sorgula": a credit query now, through the Requests task."""
    channel.check_rate(db, "action.credit", every=QUERY_EVERY)
    return channel.submit(db, user, request, kind="action.credit")
