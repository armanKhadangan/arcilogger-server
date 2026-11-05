"""Response models of ``/api/v1/compliance`` (Wave 4 contract 2.3.1).

``web/src/lib/compliance-types.ts`` mirrors them: the same names, fields and optionality, every
field required. Times are site time with offset (``to_site``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ComplianceTone = Literal["ok", "warning", "error", "neutral"]
ComplianceCardId = Literal[
    "sources",
    "seal_yesterday",
    "chain",
    "retention",
    "tsa",
    "clock",
    "content_filter",
    "lease_evidence",
    "replica",
    "backup",
]
#: Nav key of the page with the details (``web/src/nav.ts``).
ComplianceLinkPage = Literal["sources", "sealing", "search", "settings", "system"]
AlertSeverity = Literal["info", "low", "medium", "high", "critical"]


class ComplianceFact(BaseModel):
    key: str
    label_tr: str
    label_en: str
    value_tr: str
    value_en: str


class ComplianceAlertRef(BaseModel):
    id: int
    rule: str
    severity: AlertSeverity
    opened_at: datetime


class ComplianceCard(BaseModel):
    id: ComplianceCardId
    number: int
    tone: ComplianceTone
    title_tr: str
    title_en: str
    reason_tr: str
    reason_en: str
    facts: list[ComplianceFact]
    #: The card's active alerts (``open``, ``acknowledged``), newest first, at most 5.
    alerts: list[ComplianceAlertRef]
    page: ComplianceLinkPage
    doc_tr: str
    doc_en: str
    verify_on_site_tr: str | None
    verify_on_site_en: str | None
    checked_at: datetime


class ComplianceCounts(BaseModel):
    ok: int
    warning: int
    error: int
    neutral: int


class ComplianceStatus(BaseModel):
    generated_at: datetime
    #: ``None`` when the site name is not set.
    site_name: str | None
    counts: ComplianceCounts
    #: The ten cards in the order of contract 2.3.3 (``number`` 1-10).
    cards: list[ComplianceCard]
    notice_tr: str
    notice_en: str


class ConfigBackupOut(BaseModel):
    confirmed_at: datetime
    config_sha256: str
