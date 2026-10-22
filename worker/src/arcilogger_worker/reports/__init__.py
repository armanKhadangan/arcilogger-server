"""Scheduled usage reports (spec 10.5, group 2 area R, ADR-0012; docs/KULLANIM-RAPORLARI.md).

The worker makes and sends them; the API (``/api/v1/reports``) edits the schedules, queues
"run now", previews and serves the stored files — with these same modules, as the API depends
on the worker and never the other way round.

==================  ======================================================================
:mod:`.schedule`    when a schedule runs and which period it covers (Europe/Istanbul)
:mod:`.content`     the report document: the usage engine's report data
                    (``arcilogger_worker.usage.report_data``), the alerts of the period, the
                    sealing status of the previous day
:mod:`.html`        the HTML e-mail (tables and inline styles for Outlook and Gmail)
:mod:`.pdf`         the PDF (reportlab, bundled DejaVu fonts, "Sayfa n / N")
:mod:`.xlsx`        the Excel workbook (seven sheets, typed cells, no formulas)
:mod:`.mail`        the e-mail of a run (one per recipient, attachments or a download link)
:mod:`.store`       the files in ``exports/usage-reports/<run>/`` and their retention
:mod:`.runner`      the jobs ``reports.run_due`` (every minute) and ``reports.purge``
==================  ======================================================================
"""
