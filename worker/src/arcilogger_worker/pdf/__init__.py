"""PDF building blocks shared by every ARCILOGGER report (ADR-0012).

* :mod:`.fonts` — the bundled DejaVu fonts (``arcilogger_worker/fonts/``) and their registration;
* :mod:`.styles` — colours, paragraph styles, the page margin, cutting a header line to fit and
  the canvas that numbers pages "Sayfa n / N" once the page count is known;
* :mod:`.text` — text extraction from the PDFs reportlab writes (tests only).

The official 5651 report (``arcilogger_api.reports``) and the scheduled usage reports
(:mod:`arcilogger_worker.reports`) both use them; the API depends on the worker, never the other
way round, so the shared parts live here.
"""
