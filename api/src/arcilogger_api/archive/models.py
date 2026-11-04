"""Request and response models of the archive reload (Wave 5 contract §5, G4, G5 and G7 to G9).

``web/src/lib/archive-reload-types.ts`` mirrors them: the same names, fields and optionality.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

#: The function that asks for the reload; its permission is needed (G6).
ArchiveReloadReason = Literal["lookup", "search"]
ArchiveReloadStatus = Literal["queued", "loading", "ready", "failed", "expired"]
#: The error codes of the area: 409 for the first, 422 for the refusals of G5.
ArchiveReloadErrorCode = Literal[
    "archive_reload_required",  # a lookup or search needs days outside the window (G7, G8)
    "archive_reload_too_large",  # more than 14 days in one request
    "archive_day_not_in_vault",  # a day with no sealed manifest (not sealed, or removed)
    "archive_reload_disk_low",  # the load would leave less than 15 % free on the database drive
]
#: ``error_code`` of a ``failed`` reload, set by ArciIngest while it loads.
ArchiveReloadFailureCode = Literal[
    "archive_day_not_in_vault",  # vault retention removed a day after the request was queued
    "archive_file_unreadable",  # a sealed file kept failing to read or no longer matched
]


class ArchiveReloadRequest(BaseModel):
    """Body of ``POST /archive-reloads`` (G9)."""

    reason: ArchiveReloadReason
    first_day: date
    #: At most 14 days from ``first_day``, both included (G5).
    last_day: date
    #: The lookup that asked, for ``reason`` ``lookup`` (written to the audit entry, G6).
    lookup_id: int | None = None


class ArchiveReload(BaseModel):
    """One reload (G4): the 202 answer of ``POST /archive-reloads`` and of ``GET …/{id}``."""

    id: int
    #: The user name of the person who asked.
    requested_by: str
    requested_at: datetime
    reason: ArchiveReloadReason
    lookup_id: int | None
    first_day: date
    last_day: date
    status: ArchiveReloadStatus
    #: Σ ``bytes_uncompressed`` of the sealed files to load.
    bytes_total: int
    #: Σ committed ingest offset; progress is ``bytes_done / bytes_total``.
    bytes_done: int
    #: Set when ``status`` is ``failed``: one of :data:`ArchiveReloadFailureCode`.
    error_code: str | None
    ready_at: datetime | None
    #: ``ready_at`` + ``db.reload_keep_days``; then the window job drops the days again.
    expires_at: datetime | None


class ArchiveReloadList(BaseModel):
    """``GET /archive-reloads``: the active reloads first, then the recent ones."""

    items: list[ArchiveReload]


class ArchiveReloadRequired(BaseModel):
    """``details`` of 409 ``archive_reload_required`` (G7, G8): what a reload would bring back."""

    first_day: date
    last_day: date
    #: Σ ``bytes_uncompressed`` of the sealed files of those days.
    bytes_total: int
    #: The estimated database growth: ``bytes_total`` times the measured factor (G5).
    estimated_bytes: int
    #: ``db.reload_keep_days`` at the time of the answer: the days a reload keeps them (C23; a
    #: lookup or search user cannot read ``/settings``).
    keep_days: int


class ArchivedRange(BaseModel):
    """The search answer's ``archived`` field (G8): the part of the range outside the window."""

    first_day: date
    last_day: date
    #: ``db.reload_keep_days`` at the time of the answer, as in the 409's ``details`` (C23): a
    #: search that answers with ``archived`` names the real keep time to a user who cannot read
    #: ``/settings``.
    keep_days: int
