"""Official 5651 report ("Resmi Rapor", build specification 9.5) — see docs/RAPOR.md.

* :mod:`.inputs` — collects everything a report shows (lookup, outcome, raw lines, file hashes,
  seals and time stamps with a fresh verification) into one JSON object;
* :mod:`.render` — turns that object into PDF bytes, deterministically (reportlab invariant
  mode, bundled DejaVu fonts from :mod:`.fonts`);
* :mod:`.store` — renders a lookup's report once, stores it under ``exports/reports/`` with its
  SHA-256 and inputs (``lookup_reports``), optionally time-stamps the hash, and serves the same
  bytes on every download. The evidence package (spec 9.6) takes its PDF from here
  (:func:`.store.read_stored`).
"""

from arcilogger_api.reports.inputs import Person, TimestampPlan, build_inputs
from arcilogger_api.reports.render import Rendered, render
from arcilogger_api.reports.store import (
    ReportError,
    StoredReport,
    load_or_create,
    read_stored,
    read_token,
    report_name,
)

__all__ = [
    "Person",
    "Rendered",
    "ReportError",
    "StoredReport",
    "TimestampPlan",
    "build_inputs",
    "load_or_create",
    "read_stored",
    "read_token",
    "render",
    "report_name",
]
