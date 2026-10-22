"""Where the files of a report run are kept: ``exports/usage-reports/<run id>/`` (ADR-0012).

* Next to the evidence packages and the 5651 reports in ``exports`` (the API and the worker may
  write there, ADR-0007), in their own folder; **never in the vault** — a usage report is not
  evidence and is deleted after ``reports.keep_days`` (default 400) by ``reports.purge``.
* Every file is written atomically and recorded with its size and SHA-256 in
  ``report_runs.files``; a download checks the bytes against that hash and refuses a changed
  file instead of rendering it again.
* The e-mail of a run is rendered once with its files (``mail.html`` and ``mail.txt``, not
  listed in ``files``): a retry hours later sends the same e-mail.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import to_site
from arcilogger_db.models import ReportRun
from arcilogger_db.settings import get_setting
from arcilogger_worker.reports import texts

log = logging.getLogger("arcilogger_worker.reports")

FOLDER: Final = "usage-reports"
#: Internal files of a run (the e-mail as rendered); not downloadable.
MAIL_HTML: Final = "mail.html"
MAIL_TEXT: Final = "mail.txt"
EXTENSIONS: Final = {"pdf": "pdf", "xlsx": "xlsx", "html": "html"}
MEDIA_TYPES: Final = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html; charset=utf-8",
}


class StoredFileError(RuntimeError):
    """A run's file cannot be served; ``code``: ``report_expired``, ``report_missing`` or
    ``report_damaged``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_folder(layout: Layout, run_id: int) -> Path:
    return layout.exports / FOLDER / str(int(run_id))


def file_stem(kind: str, start: datetime, end: datetime, run_id: int) -> str:
    """``ARCILOGGER-kullanim-tam-gun-20261019-r17`` (period in site days, ASCII only)."""
    first = to_site(start)
    last = to_site(end)
    if last.time() == datetime.min.time() and last > first:
        last -= timedelta(days=1)
    days = f"{first:%Y%m%d}" if first.date() == last.date() else f"{first:%Y%m%d}-{last:%Y%m%d}"
    return f"ARCILOGGER-kullanim-{texts.KIND_SLUGS.get(kind, 'rapor')}-{days}-r{int(run_id)}"


@dataclass(frozen=True)
class Output:
    """One rendered file of a run."""

    name: str
    format: str
    data: bytes

    @property
    def entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "format": self.format,
            "size": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


def write_run(
    layout: Layout, run_id: int, outputs: Sequence[Output], mail: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Write the files and the rendered e-mail of a run; their ``files`` entries."""
    folder = run_folder(layout, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        atomic_write_bytes(folder / output.name, output.data)
    for name, text in mail.items():
        atomic_write_bytes(folder / name, text.encode("utf-8"))
    return [output.entry for output in outputs]


def read_mail(layout: Layout, run_id: int) -> tuple[str, str]:
    """``(html, text)`` of the run's e-mail."""
    folder = run_folder(layout, run_id)
    html = (folder / MAIL_HTML).read_text(encoding="utf-8")
    text = (folder / MAIL_TEXT).read_text(encoding="utf-8")
    return html, text


def read_file(layout: Layout, run: ReportRun, name: str) -> tuple[dict[str, Any], bytes]:
    """The listed file ``name`` of ``run`` and its bytes, checked against the SHA-256."""
    entry = next(
        (item for item in run.files or [] if isinstance(item, dict) and item.get("name") == name),
        None,
    )
    if entry is None:
        raise StoredFileError("not_found", f"run {run.id} has no file {name!r}")
    if run.expired_at is not None:
        raise StoredFileError("report_expired", f"the files of run {run.id} were deleted")
    path = run_folder(layout, run.id) / str(entry["name"])
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise StoredFileError("report_missing", f"{path.name} is missing") from exc
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        log.error("report run %s: %s does not match its SHA-256", run.id, path.name)
        raise StoredFileError("report_damaged", f"{path.name} does not match its SHA-256")
    return entry, data


# -- retention ------------------------------------------------------------------------------------


@dataclass
class PurgeReport:
    deleted: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def details(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "bytes": self.bytes, "errors": self.errors[:20]}


def purge(session: Session, layout: Layout, *, now: datetime) -> PurgeReport:
    """Delete the files of runs finished more than ``reports.keep_days`` ago (``expired_at``)."""
    keep = get_setting(session, "reports.keep_days")
    days = keep if type(keep) is int and keep >= 1 else 400
    report = PurgeReport()
    runs = session.scalars(
        select(ReportRun)
        .where(
            ReportRun.expired_at.is_(None),
            ReportRun.finished_at.is_not(None),
            ReportRun.finished_at < now - timedelta(days=days),
        )
        .order_by(ReportRun.id)
        .with_for_update(skip_locked=True)
    ).all()
    for run in runs:
        folder = run_folder(layout, run.id)
        size = sum(path.stat().st_size for path in folder.glob("*") if path.is_file())
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            size = 0
        except OSError as exc:
            report.errors.append(f"run {run.id}: {type(exc).__name__}: {exc}"[:300])
            continue
        run.expired_at = now
        report.deleted += 1
        report.bytes += size
    session.commit()
    return report
