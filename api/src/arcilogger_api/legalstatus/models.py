"""Response model of ``GET /legal-status`` (Wave 5 contract 2.3.1).

``web/src/lib/legal-status-types.ts`` mirrors it: the same names, fields and optionality, every
field required. The contract types the times as plain strings, so they are ISO texts here too,
written by the server.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

LegalLevel = Literal["ok", "warning", "danger"]
LegalReasonCode = Literal[
    "provider_not_legal",  # mock, rfc3161 or a test server
    "credentials_in_clear",  # turktrust over plain HTTP (tsa.allow_plain_http)
    "legal_root_missing",  # legal-grade provider but no legal root fingerprint listed
    "unstamped_days",  # a closed day older than seal.late_after_hours is not stamped
    "renewal_due",  # tokens due for re-stamping
    "renewal_uncovered",  # tokens whose certificate expired with no counting renewal
    "certificate_revoked",  # a TSA certificate in use is on a CRL (crl-revoked)
    "crl_stale",  # no verified CRL newer than tsa.crl_max_age_days
]
#: Nav key of the page that can fix the reason (``web/src/nav.ts``); the web hides the link
#: without the permission.
LegalLink = Literal["sealing", "settings-tsa"]


class LegalReason(BaseModel):
    code: LegalReasonCode
    level: Literal["warning", "danger"]
    message_tr: str
    message_en: str
    #: ISO time the condition was first seen, if known.
    since: str | None
    #: Days or tokens concerned, if countable.
    count: int | None
    link: LegalLink | None


class LegalStatus(BaseModel):
    #: The worst reason's level, ``ok`` when there is none.
    level: LegalLevel
    #: ``tsa.provider``.
    provider: str
    #: "Kamu SM", "TÜRKTRUST", "Test sunucusu", "Deneme (mock)".
    provider_label: str
    #: Danger first, then by code.
    reasons: list[LegalReason]
    checked_at: str
