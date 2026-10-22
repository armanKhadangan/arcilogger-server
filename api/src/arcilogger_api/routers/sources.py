"""``/api/v1/sources`` — Log kaynakları ve sağlık durumu (spec 12.3 page 3).

Owner: a later work package. Endpoints are added here only; the router is mounted automatically
(:func:`arcilogger_api.routers.discover`). Protect every endpoint with
``Depends(require(...))`` from :mod:`arcilogger_api.security` and audit mutations with
:func:`arcilogger_api.audit.record_audit`.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/sources", tags=["sources"])
