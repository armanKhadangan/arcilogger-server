# ADR-0012: Scheduled usage reports — made and sent by the worker, shared PDF building blocks

- Status: proposed (2026-10-20)
- Deciders: the product owner; proposed with group 2, area R (scheduled reports), build
  specification 10.5 and 12.3 page 12, owner decision 2026-10-20 (person-level reports with names)

## Context

Specification 10.5 asks for a **Gün sonu raporu** (default 18:30, the day so far) and a **Tam gün
raporu** (07:00, the previous day), plus weekly and monthly reports, each with its recipients and
formats: an HTML e-mail, a PDF and optionally an Excel workbook. The content: company totals,
departments, **every person** (owner decision 2026-10-20: names by default; each organisation
establishes its own legal basis, legal review recommended), notable changes against the 7-day
average, the day's security alerts and the previous day's sealing status. Usage numbers are
estimates and must say so.

What already existed:

- the official 5651 PDF report in the **API** (`arcilogger_api.reports`, reportlab in invariant
  mode, the bundled DejaVu fonts in `arcilogger_api/fonts/`), byte-for-byte reproducible from its
  stored inputs — a property its tests check and its users rely on (the SHA-256 is handed out);
- the alert e-mail in the **worker** (ADR-0010): SMTP from the standard library through
  `[smtp]` and the secret `smtp.password`, one e-mail per recipient, leased deliveries with
  backoff, a local `R20-email` alert on giving up, and a local SMTP test server;
- the dependency direction **api → worker** (the API imports the lookup types and engine of the
  worker); the worker may not import the API.

The usage numbers come from area U's engine (`arcilogger_worker.usage.report_data.
build_report_data`, group 2 contract section 3), built in parallel.

## Decision

1. **The worker makes and sends the reports.** A worker job `reports.run_due` runs every minute:
   it queues due schedules, renders queued runs (also "run now" runs the API queued), stores the
   files and e-mails them; `reports.purge` (daily, 03:20) deletes old files. The worker already
   holds the SMTP configuration and may read the secret store, the vault (ADR-0007), the alerts
   and the seal states; the API never sends mail and never holds the SMTP password in a request.
   "Run now" therefore answers 202 with a queued run that is done within about a minute.
2. **Shared PDF building blocks move into the worker** (`arcilogger_worker.pdf`): the DejaVu
   files (`arcilogger_worker/fonts/`, same files, same README with the hashes), their
   registration under the same names, the colours, paragraph styles, the header-line cut and a
   page canvas that numbers "Sayfa n / N" once the count is known; also the test helper that
   extracts the text of a PDF. The API keeps `arcilogger_api.reports.fonts` and
   `arcilogger_api.pdftext` as re-exports, and its 5651 renderer imports the moved parts. **The
   5651 report renders the same bytes as before** — checked with the SHA-256 of reports rendered
   from fixed inputs before and after the move, and by its existing tests that re-render stored
   inputs. No file is duplicated; the worker gets `reportlab` (already in the lock) and
   `openpyxl` (new) as dependencies.
3. **One document, three renderings.** `arcilogger_worker.reports.content.build_document` joins
   the engine's report data (read-only), the alerts of the period and the previous day's seal
   status into one JSON object; `html`, `pdf` and `xlsx` render that same object, so the e-mail,
   the PDF and the workbook cannot disagree. Every rendering prints the method note ("Tahmini
   değer: …"). Golden files of all three are made from a fixed sample of the contract's data.
4. **E-mail that mail clients render**: tables and inline styles only (no flexbox, grid,
   classes or `<style>`), widths and `bgcolor` as attributes, system fonts, no images, scripts or
   external resources; the only link is the ARCILOGGER web address (`alerts.web_base_url`).
   Turkish first with an English summary line. A test checks the HTML for all of it.
5. **Delivery like the alert e-mail** (ADR-0010), through its `SmtpSender`: **one e-mail per
   recipient** (`To` only that address; no `Bcc`) — nobody sees who else receives the report, a
   refused address fails alone and every recipient has its own state; the delivery is leased
   before sending and recorded after (a crash repeats an e-mail at most once); retries after 1,
   5, 15, 60, 180 minutes, 6 attempts, a 5xx at once; then the delivery is `failed` and the
   local alert **`R20-report`** (medium) opens, resolved once those recipients receive a report
   again (or are removed from every schedule) and a later run renders. The rendered e-mail is
   stored with the files, so a retry sends the same e-mail.
6. **Size guard.** When the PDF and the workbook together exceed `reports.max_attachment_mb`
   (15 MiB), the e-mail is sent without them and links to the download on the Raporlar page.
   The guard compares the raw bytes; base64 makes the message about 1.37 times larger plus the
   body, so the operator docs ask for at most two thirds of the mail server's limit.
7. **Files**: `exports/usage-reports/<run>/` (ADR-0007: API and worker may write `exports`),
   each recorded in `report_runs.files` with its SHA-256; a download is checked against it.
   **Never in the vault**: a usage report is not evidence. After `reports.keep_days` (400) the
   files are deleted, the run row and the hashes stay.
8. **Schedules in the database** (`report_schedules`, migration `0011_reports`): the four
   schedules of the contract are inserted once by the migration — disabled until recipients are
   set, person-level with names. Times are local Europe/Istanbul times (`zoneinfo`; Türkiye has
   no daylight saving time since 2016, tested on the days Europe changes its clocks). After
   downtime a schedule runs **once**, for its newest occurrence (`reports.catch_up`, default on;
   off: occurrences more than 30 minutes late are skipped), never once per missed occurrence.
9. **What a report shows of the alerts**: time, rule, severity, state and the rule's name and one
   sentence from the alert catalogue (whitelisted facts) — never an alert's stored title or
   details, which may carry error texts or log fields. No raw log line reaches a report.

## Consequences

- Reports work while the API is down, and an SMTP outage never slows the web interface.
- A run's files are reproducible only as long as the database still has the same data; unlike
  the 5651 report, the inputs are not stored (they can be large: every person × site × hour).
  The files themselves are kept with their hashes for `reports.keep_days`.
- Person names and e-mail recipients' addresses are personal data: the API audits every change
  of a schedule, every run, download and preview (addresses only as a count), needs
  `reports.manage` (administrators) and the KVKK notice for content; the owner decision makes
  names the default, `pseudonymize` and `persons.usage_excluded` (area U) remain available.
- Until area U's engine is merged, `build_report_data` is a placeholder that refuses to run: a
  run fails visibly with `R20-report`, never with invented numbers.
- The worker grows: reportlab (BSD), openpyxl (MIT) and et-xmlfile (MIT) are installed with it.

## Dependencies added (licences checked from the package metadata, 2026-10-20)

| Package | Version | Licence | Windows | Used by |
|---|---|---|---|---|
| openpyxl | 3.1.5 | MIT | pure Python | worker (Excel workbook) |
| et-xmlfile (openpyxl) | 2.0.0 | MIT | pure Python | worker |
| reportlab (already used by the API) | 5.0.1 | BSD | pure-Python wheel | worker (PDF), api |

## Alternatives considered

- **Render in the API, send from the worker** — the worker would need the rendered files from
  somewhere and the API would render on a timer it does not have; scheduled reports would stop
  while the API is down. Rejected.
- **Copy the fonts into the worker** — two copies of 1.5 MB of fonts and their licence, which
  can drift; the 5651 report's hashes depend on them. Rejected for the move.
- **Fonts and styles in `arcilogger-core`** — core is the evidence path that runs from Task
  Scheduler and keeps its dependencies short (no reportlab). Rejected.
- **One e-mail with `Bcc` to every recipient** — fewer messages, but one refused address fails
  the e-mail for all and there is no per-recipient state. Rejected (as in ADR-0010).
- **A separate deliveries table** — the alert e-mail has one because alerts and recipients are
  many-to-many; a report run has a handful of recipients that belong to it: a JSON list on the
  run (`report_runs.deliveries`) is enough and keeps the run history one row.
- **WeasyPrint or a headless browser for HTML → PDF** — native dependencies (Pango/GTK) or a
  browser on the server (ADR-0005 chose reportlab). Rejected.
