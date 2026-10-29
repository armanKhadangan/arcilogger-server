"""The Uyum Durumu samples (``web/e2e/samples/compliance.json``), captured from the real API.

``capture_api_samples.py`` calls :func:`capture` on its main site after every other page's
samples (Wave 4 contract 2.2, 2.3; it reads the whole site as the pages before it left it), with
the capture (the app, its database and data directory, the request helpers ``json``, ``error``,
``file``) and three signed-in clients: an administrator, an auditor and an administrator who has
not accepted the KVKK notice. It returns ``{sample file name: data}``.

The cards are computed from what the site holds at the capture's "now" (13.10.2026 07:00): the
sealed and waiting days of October, the clock samples, the alerts of the Security page, the
replica copies. For the time of the capture the module adds what a running site would have:

* the site day 09.10.2026 as ingest leaves it (``samples_search.site_day_ingested``), so card 7
  counts that day's web and DNS filter records in ``search_counts``;
* the worker's ``R20-filter`` check (contract 2.4) run at "now", as the system checks run it: no
  filter record for 24 hours on expected days, so card 7 is red and carries the alert;
* ``state/backup/last.json`` of a backup that succeeded at 01:52 today, so card 10 waits only for
  the configuration copy to be confirmed.

What ``compliance.json`` holds: ``admin`` and ``auditor`` (``GET /compliance``; the auditor holds
``compliance.read``, contract D15), ``report_pdf`` (``GET /compliance/report.pdf``: headers, size
and SHA-256, no content), ``config_backup`` (``POST /compliance/config-backup``), ``after_backup``
(the cards after that confirmation: card 10 turned ``ok``) and ``errors``: ``forbidden`` (a
report viewer; hr and the auditor's confirmation get the same refusal) and
``config_backup_unreadable`` (the configuration file could not be read: the 409).

The 30-second answer cache is cleared after every change the module makes; the confirmation
clears it itself (the module checks that ``after_backup`` shows it). Afterwards the alert, the
backup state and the site day's rows are removed again; the audit entries of the report and of
the confirmations stay (the audit log is a chain: nothing is ever deleted from it), as do the two
users it signs in.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest import mock

from sqlalchemy import delete, select

from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, utc_now
from arcilogger_db.models import Alert
from arcilogger_db.settings import get_settings
from arcilogger_worker.alerts.system import CheckContext, apply, backup_state_path, check_filter

import samples_search

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's file compares differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: The page maps the card ids and their link pages, the alerts' rules and severities.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "compliance": (frozenset(), frozenset({"id", "page", "rule", "severity"})),
}

API = "/api/v1"
URL = f"{API}/compliance"
REPORT = f"{URL}/report.pdf"
CONFIRM = f"{URL}/config-backup"
#: The alert card 7 lists (contract 2.4).
FILTER_RULE = "R20-filter"
#: When today's backup finished (the Backup task runs at 01:40) and its size.
BACKUP_FINISHED = time(1, 52, 10)
BACKUP_SIZE = 1_288_490_189
#: The download's name (contract 2.3.4; the minute is the capture's running clock).
FILENAME = re.compile(r'^attachment; filename="ARCILOGGER-uyum-durumu-\d{8}-\d{4}\.pdf"$')


def open_filter_alert(capture: Capture) -> list[int]:
    """The worker's ``R20-filter`` check at "now" and its alert, as the system checks run it;
    the ids of the alerts it opened."""
    now = utc_now()
    with capture.factory() as session:
        settings = get_settings(session, prefix="alerts.")
        context = CheckContext(session, capture.layout, capture.config, now, settings)
        result = apply(session, check_filter(context), now=now)
        session.commit()
        opened = list(
            session.scalars(
                select(Alert.id).where(Alert.dedup_key.in_(result["opened"]), Alert.state == "open")
            )
        )
    if not opened:
        raise SystemExit(f"compliance: the R20-filter check opened no alert: {result}")
    return opened


@contextmanager
def backup_state(capture: Capture) -> Iterator[None]:
    """``state/backup/last.json`` of today's successful backup (the fields the Backup task
    writes) for the block; removed afterwards (the main site has none)."""
    path = backup_state_path(capture.layout)
    if path.exists():
        raise SystemExit(f"compliance: {path} exists already")
    created = not path.parent.exists()
    finished = datetime.combine(site_day(utc_now()), BACKUP_FINISHED, tzinfo=SITE_TZ)
    state = {
        "schema": 1,
        "finished": isoformat_site(finished, "seconds"),
        "ok": True,
        "file": "D:\\ARCILOGGER\\backups\\scheduled\\arcilogger-20261013-0140.dump",
        "size": BACKUP_SIZE,
        "sha256": "0" * 64,
        "error": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        yield
    finally:
        path.unlink()
        if created:
            path.parent.rmdir()


def card(status: dict[str, Any], card_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(item for item in status["cards"] if item["id"] == card_id)
    return found


def fresh(capture: Capture) -> None:
    """Drop the kept answer (``app.state.arci_compliance``): the next one computes the cards."""
    capture.app.state.arci_compliance = None


def config_unreadable(capture: Capture) -> Any:
    """The configuration file cannot be read for the block (access denied, as a file another
    program holds open); every other file reads as before."""
    config = capture.installed.config_path
    read_bytes = Path.read_bytes

    def locked(path: Path) -> bytes:
        if path == config:
            raise PermissionError(13, "Permission denied", str(path))
        return read_bytes(path)

    return mock.patch.object(Path, "read_bytes", locked)


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The Uyum Durumu samples (see the module text)."""
    with samples_search.site_day_ingested(capture), backup_state(capture):
        alerts = open_filter_alert(capture)
        fresh(capture)
        try:
            data = _capture(capture, admin, auditor)
        finally:
            with capture.factory() as session:
                session.execute(delete(Alert).where(Alert.id.in_(alerts)))
                session.commit()
            fresh(capture)
    return {"compliance": data}


def _capture(capture: Capture, admin: Any, auditor: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    out["admin"] = capture.json(admin.get(URL), 200)
    out["auditor"] = capture.json(auditor.get(URL), 200)
    filter_card = card(out["admin"], "content_filter")
    if filter_card["tone"] != "error" or FILTER_RULE not in {
        alert["rule"] for alert in filter_card["alerts"]
    }:
        raise SystemExit(f"compliance: card 7 without its R20-filter alert: {filter_card}")

    report = admin.get(REPORT)
    out["report_pdf"] = capture.file(report)
    headers = out["report_pdf"]["headers"]
    if (
        not report.content.startswith(b"%PDF")
        or headers.get("content-type") != "application/pdf"
        or not FILENAME.match(headers.get("content-disposition", ""))
        or headers.get("x-arci-sha256") != hashlib.sha256(report.content).hexdigest()
    ):
        raise SystemExit(f"compliance: unexpected report download: {headers}")
    capture.file(auditor.get(REPORT))  # the auditor downloads it too (D15)

    before = card(out["admin"], "backup")
    out["config_backup"] = capture.json(admin.post(CONFIRM), 200)
    out["after_backup"] = capture.json(admin.get(URL), 200)  # the confirmation cleared the cache
    after = card(out["after_backup"], "backup")
    if (before["tone"], after["tone"]) != ("warning", "ok"):
        raise SystemExit(f"compliance: card 10 did not turn ok when confirmed: {before} {after}")

    viewer = capture.client("viewer", "rapor.okuyucu", "Rapor Okuyucu")
    hr = capture.client("hr", "ik.personel", "İK Personeli")
    errors["forbidden"] = capture.error(viewer.get(URL), 403, "forbidden")
    capture.error(hr.get(URL), 403, "forbidden")
    capture.error(hr.get(REPORT), 403, "forbidden")
    capture.error(auditor.post(CONFIRM), 403, "forbidden")  # settings.manage only
    with config_unreadable(capture):
        errors["config_backup_unreadable"] = capture.error(
            admin.post(CONFIRM), 409, "config_backup_unreadable"
        )
    out["errors"] = errors
    return out
