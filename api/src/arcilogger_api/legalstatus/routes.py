"""``/api/v1/legal-status`` — the legal-validity status of the stamps (Wave 5 contract 2.3.1).

``GET /legal-status`` (:func:`arcilogger_api.legalstatus.compute.compute_legal_status`) is the
banner on every page, so every signed-in user may read it (D11). It is not audited and carries
no data about log subjects (as the Panel). One answer is kept :data:`CACHE_SECONDS` seconds for
every user of this process: it does not depend on the role. 5651 Temel: nothing here is
licence-gated (contract 0.1 item 6).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request

from arcilogger_api.deps import ConfigDep, LayoutDep, TtlCache
from arcilogger_api.legalstatus.compute import compute_legal_status
from arcilogger_api.legalstatus.models import LegalStatus
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.timeutil import utc_now

router = APIRouter(prefix="/legal-status", tags=["legal-status"])

SignedInDep = Annotated[CurrentUser, Depends(require())]

#: How long one computed answer is kept (contract 2.3.1).
CACHE_SECONDS: Final = 60.0


def status_cache(request: Request) -> TtlCache[LegalStatus]:
    """The kept answer of this application (``app.state.arci_legal_status``)."""
    cache: TtlCache[LegalStatus] | None = getattr(request.app.state, "arci_legal_status", None)
    if cache is None:
        cache = request.app.state.arci_legal_status = TtlCache[LegalStatus](CACHE_SECONDS)
    return cache


@router.get("")
def legal_status(
    request: Request, config: ConfigDep, layout: LayoutDep, user: SignedInDep
) -> LegalStatus:
    """The legal-validity status (kept 60 s; not audited; no data about log subjects)."""
    return status_cache(request).get_or_compute(
        lambda: compute_legal_status(layout, config, utc_now())
    )
