"""``/api/v1/credits`` — Kamu SM kontör geçmişi (spec 6.1, 12.3 page 9 "Kontör" widget).

``seal.read``. The scheduled ``\\ARCILOGGER\\Credit`` task (``arcilogger-core credit``) asks the
time-stamp provider for the remaining credit once a day and appends the answer to
``state/credit.jsonl`` (:mod:`arcilogger_core.credit`); this endpoint only reads that file. The
alert threshold is ``[tsa] credit_alert_threshold`` of ``arcilogger.toml``.

"Kontör sorgula" (query now) is not offered through the API: it needs the Kamu SM credentials
and writes ``state/``, which the API service account may not (ADR-0007) — run the Credit task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from arcilogger_api.deps import ConfigDep, LayoutDep
from arcilogger_api.sealinfo import credit_history
from arcilogger_api.security import CurrentUser, require

router = APIRouter(prefix="/credits", tags=["seals"])

ReaderDep = Annotated[CurrentUser, Depends(require("seal.read"))]


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
