"""The archive reload samples (``web/e2e/samples/archive.json``), captured from the real API
(Wave 5 contract §5, G4 to G9).

``capture_api_samples.py`` calls :func:`capture` on its main site after every other page's
samples, with the capture (the app, its database and data directory, the request helpers
``json``, ``error``) and three signed-in clients: an administrator, an auditor and an administrator
who has not accepted the KVKK notice. It returns ``{sample file name: data}``.

The main site keeps every October day in its database, so the module narrows the window for the
time of its capture: ``db.retention_days`` 7 (the smallest; at the capture's "now", 13.10.2026,
the window starts on 06.10.2026) and the applied cutoff 06.10.2026 on record (the status row
``db.window_dropped_before``), as the window job leaves them. 04. and 05.10 (sealed) and the days
before them are then gone from the database; the rows themselves are not touched.

What ``archive.json`` holds:

* ``lookup_required``: ``POST /lookup/5651`` for 09.10.2026 11:23:41 (its days 01.-10.10), 409
  ``archive_reload_required`` with what a reload brings back (04.-05.10: the sealed ones);
* ``search_archived``: a search of 04.-07.10 with the rows of the window and ``archived``;
  ``search_required``: a search of 04.-05.10 only, 409;
* ``created`` (``POST /archive-reloads``, 202, ``queued``), then the same request as
  ArciIngest moves it on: ``loading`` (half of its bytes), ``ready`` (with ``expires_at``);
  ``existing``: the same days asked again while it is ready (the answer is that request);
  then it expires (as the window job marks it), a second request is ``failed``
  (``archive_file_unreadable``) and a third one waits ``queued``; ``list`` (``GET
  /archive-reloads``);
* ``errors``: ``archive_reload_too_large`` (15 days), ``archive_day_not_in_vault`` (03.10, before
  the sealed days), ``archive_reload_disk_low`` (a disk sample of a nearly full database drive),
  ``validation_error`` (the days backwards; and ``in_window``: days the window holds whole),
  ``forbidden`` (hr), ``privacy_notice_required`` and ``not_found``.

Afterwards the setting, the status row, the requests and the disk samples are put back; the audit
entries stay (the audit log is a chain: nothing is ever deleted from it), as does the hr user.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, insert, update

from arcilogger_core.timeutil import day_window, utc_now
from arcilogger_db.models import ArchiveReload, Setting
from arcilogger_db.partitions import DROPPED_KEY
from arcilogger_db.settings import DEFAULT_SETTINGS, SYSTEM_AUTHOR
from arcilogger_worker.alerts.system import drives

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's file compares differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: The pages map a request's reason to the asking function.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "archive": (frozenset(), frozenset({"reason", "error_code"})),
}

API = "/api/v1/archive-reloads"
WINDOW_DAYS = 7
#: The cutoff the window job applied (the capture's "now" minus 7 days).
CUTOFF = date(2026, 10, 6)
#: A day before the sealed ones: not in the vault.
UNSEALED = date(2026, 10, 3)
FIRST, LAST = date(2026, 10, 4), date(2026, 10, 5)
REQUEST = {
    "authority": "İzmir Cumhuriyet Başsavcılığı",
    "file_no": "2026/48213 Soruşturma",
    "request_date": "2026-10-10",
    "reason": "5651 sayılı Kanun kapsamında erişim kayıtları talebi (müzekkere no 2026/771).",
}
PC = "172.20.42.57"


def _days(first: date, last: date, reason: str = "lookup") -> dict[str, Any]:
    return {"reason": reason, "first_day": first.isoformat(), "last_day": last.isoformat()}


def _search(first: date, end: date) -> dict[str, Any]:
    return {"q": "", "from": day_window(first)[0].isoformat(), "to": day_window(end)[0].isoformat()}


@contextmanager
def narrow_window(capture: Capture) -> Iterator[None]:
    """``db.retention_days`` 7 and the applied cutoff :data:`CUTOFF` (module text), put back
    afterwards."""
    capture.setting("db.retention_days", WINDOW_DAYS)
    with capture.factory() as session:
        value = {"cutoff": CUTOFF.isoformat(), "at": utc_now().isoformat(timespec="seconds")}
        session.execute(
            insert(Setting).values(key=DROPPED_KEY, value=value, updated_by=SYSTEM_AUTHOR)
        )
        session.commit()
    try:
        yield
    finally:
        capture.setting("db.retention_days", DEFAULT_SETTINGS["db.retention_days"])
        with capture.factory() as session:
            session.execute(delete(Setting).where(Setting.key == DROPPED_KEY))
            session.execute(delete(ArchiveReload))
            session.commit()


@contextmanager
def full_drive(capture: Capture) -> Iterator[None]:
    """A disk sample of the database (data) drive with 15 % free: any reload would leave less."""
    path = capture.layout.system_history
    before = path.read_bytes() if path.exists() else None
    label = drives(capture.config.data_dir)[0][0]
    total = 500 * 1024**3
    sample = {"path": label, "total": total, "used": total * 85 // 100}
    path.parent.mkdir(parents=True, exist_ok=True)
    days = [{"day": utc_now().date().isoformat(), "drives": [sample]}]
    path.write_text(json.dumps({"schema": 1, "days": days}), encoding="utf-8")
    try:
        yield
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_bytes(before)


def _move(capture: Capture, reload_id: int, **values: Any) -> None:
    """The request as ArciIngest moves it on (``archive_reloads``)."""
    with capture.factory() as session:
        session.execute(update(ArchiveReload).where(ArchiveReload.id == reload_id).values(values))
        session.commit()


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The archive reload samples (see the module text)."""
    with narrow_window(capture):
        data = _capture(capture, admin, auditor, newcomer)
    return {"archive": data}


def _capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    lookup = {"request": REQUEST, "query": {"at": "2026-10-09T11:23:41", "internal_ip": PC}}
    out["lookup_required"] = capture.error(
        auditor.post("/api/v1/lookup/5651", json=lookup), 409, "archive_reload_required"
    )
    details = out["lookup_required"]["body"]["error"]["details"]
    if (details["first_day"], details["last_day"]) != (FIRST.isoformat(), LAST.isoformat()):
        raise SystemExit(f"archive: unexpected days of the lookup's reload: {details}")
    archived = capture.json(
        auditor.post("/api/v1/search", json=_search(FIRST, FIRST + timedelta(days=4))), 200
    )
    if archived["archived"] != {"first_day": FIRST.isoformat(), "last_day": LAST.isoformat()}:
        raise SystemExit(f"archive: the search did not answer archived: {archived['archived']}")
    out["search_archived"] = archived
    out["search_required"] = capture.error(
        auditor.post("/api/v1/search", json=_search(FIRST, LAST + timedelta(days=1))),
        409,
        "archive_reload_required",
    )

    # Before any request: none answers for these days yet.
    with full_drive(capture):
        errors["disk_low"] = capture.error(
            admin.post(API, json=_days(FIRST, LAST)), 422, "archive_reload_disk_low"
        )

    created = capture.json(auditor.post(API, json=_days(FIRST, LAST)), 202)
    out["created"] = created
    reload_id = created["id"]
    half = created["bytes_total"] // 2
    _move(capture, reload_id, status="loading", bytes_done=half)
    out["loading"] = capture.json(auditor.get(f"{API}/{reload_id}"), 200)
    ready_at = utc_now()
    _move(
        capture,
        reload_id,
        status="ready",
        bytes_done=created["bytes_total"],
        ready_at=ready_at,
        expires_at=ready_at + timedelta(days=int(DEFAULT_SETTINGS["db.reload_keep_days"])),
    )
    out["ready"] = capture.json(auditor.get(f"{API}/{reload_id}"), 200)
    out["existing"] = capture.json(auditor.post(API, json=_days(FIRST, LAST)), 202)
    if out["existing"]["id"] != reload_id:
        raise SystemExit("archive: the same days asked again queued a second load")
    _move(capture, reload_id, status="expired")  # as the window job marks it
    second = capture.json(admin.post(API, json=_days(LAST, LAST, "search")), 202)
    _move(capture, second["id"], status="failed", error_code="archive_file_unreadable")
    out["failed"] = capture.json(admin.get(f"{API}/{second['id']}"), 200)
    capture.json(admin.post(API, json=_days(FIRST, FIRST, "search")), 202)  # waits, queued
    out["list"] = capture.json(admin.get(API), 200)

    errors["too_large"] = capture.error(
        admin.post(API, json=_days(FIRST, FIRST + timedelta(days=14))),
        422,
        "archive_reload_too_large",
    )
    errors["not_in_vault"] = capture.error(
        admin.post(API, json=_days(UNSEALED, UNSEALED)), 422, "archive_day_not_in_vault"
    )
    errors["validation"] = capture.error(
        admin.post(API, json=_days(LAST, FIRST)), 422, "validation_error"
    )
    errors["in_window"] = capture.error(
        admin.post(API, json=_days(CUTOFF, CUTOFF)), 422, "validation_error"
    )
    hr = capture.client("hr", "ik.arsiv", "İK Arşiv")
    errors["forbidden"] = capture.error(hr.post(API, json=_days(FIRST, LAST)), 403, "forbidden")
    errors["privacy_notice_required"] = capture.error(
        newcomer.post(API, json=_days(FIRST, LAST)), 403, "privacy_notice_required"
    )
    errors["not_found"] = capture.error(admin.get(f"{API}/999999"), 404, "not_found")
    out["errors"] = errors
    return out
