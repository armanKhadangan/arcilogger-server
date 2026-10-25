# Usage reports (scheduled e-mail, PDF, Excel)

> Türkçe: [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md). Decision:
> [adr/0012-scheduled-usage-reports.md](adr/0012-scheduled-usage-reports.md).
>
> **Licence:** scheduled reports need the **Scheduled usage reports** module (Zamanlanmış kullanım raporları, which needs Usage analytics; [LICENSING.md](LICENSING.md)); while it is off the Raporlar page is locked, no report is made or sent and `R20-report` is not raised.
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance. Have the use of person-level reports
> reviewed by legal counsel.

This document describes the scheduled usage reports: when they run, which period they cover,
what they contain, how they are delivered, where the files are kept and how to test the set-up.

**Usage numbers are estimates:** they are computed from connection logs; tabs left open in the
background may count too. Every report (e-mail, PDF, Excel) prints this note; durations are never
presented as exact.

## 1. Schedules

Four schedules come with the installation. All are **disabled** (no recipients); add recipients
and enable them:

| Schedule | Kind | When | Period covered |
|---|---|---|---|
| Gün sonu raporu (end of day) | `end_of_day` | daily 18:30 | that day 00:00 – the run time |
| Tam gün raporu (full day) | `full_day` | daily 07:00 | the previous day (00:00 – 24:00) |
| Haftalık rapor (weekly) | `weekly` | Monday 07:15 | the previous ISO week (Monday – Sunday) |
| Aylık rapor (monthly) | `monthly` | 1st of the month 07:30 | the previous calendar month |

- Times are **Türkiye time** (Europe/Istanbul). Türkiye has no daylight saving time since 2016,
  so 07:00 is 04:00 UTC on every day of the year.
- The weekday of the weekly report (`weekday`, 1 = Monday … 7 = Sunday) and the day of the
  monthly one (`monthday`, 1–28: every month has it) can be changed; the period stays the same
  (previous week / month).
- The end-of-day report **cannot be set to 00:00**: its period (that day 00:00 – the run time)
  would be empty. Choose a time from 00:01 to 23:59; for the whole previous day use the full-day
  report.
- Each schedule has its own recipients, formats (HTML e-mail, PDF, Excel), **person level**
  (`person_level`, default on: with names) and **pseudonyms** (`pseudonymize`, default off).
  By default (owner decision 2026-10-20) reports are detailed and carry names; the organisation
  decides, with its legal counsel, whether that fits its employees' consent. A person to be left
  out of person-level reports is excluded with the person's **Kullanım raporlarından hariç tut**
  box on Cihazlar ve Kişiler (`persons.usage_excluded`, [USAGE.md](USAGE.md) section 6).
- **Reports missed while the server was down:** when the worker starts again each schedule runs
  **once**, for its most recent missed time (after five days down only yesterday's full-day
  report goes out, not five). With `reports.catch_up` set to `false` a report more than 30
  minutes late is not sent at all; the next time is awaited. Changing a schedule's name,
  recipients or formats while the worker is down does not drop the missed report; changing its
  kind, time, day or whether it is enabled computes the next time anew.
- Schedules are edited on the **Raporlar** (Reports) page of the web interface (or
  `/api/v1/reports`, permission `reports.manage`: administrators). Every change is audited
  (`report.schedule.create/update/delete`; recipients only as a count, never addresses).

## 2. What a report contains

All formats show the same content (they are made from one document). The organisation name
(`site_name`) is in the e-mail subject, the summary sentences, the HTML header, the PDF cover
and footer and the workbook's summary sheet (the "Site" row); while it is not set it is left
out of all of them (no empty brackets, no broken sentence; [INSTALL.md](INSTALL.md) section 7.1).

- **Company totals:** active users, total online and active time, off-hours active time, data
  downloaded / uploaded, sessions, blocked attempts; top sites and categories (10 in the e-mail,
  20 in the PDF and the workbook).
- **Departments:** active users, active time, download, upload, blocked attempts. Persons,
  devices and IP addresses without a department are counted together in the row **Bilinmiyor**
  (unknown), named alike in every format.
- **Persons — all of them** (every person with activity, by name): department, first / last
  activity (with their dates in a report over several days), online and active time, download /
  upload, sessions, top sites (with time) and categories, blocked attempts, off-hours time. The
  details of two persons with the same name (or of a person and a device named alike) are never
  mixed.
- **Notable changes:** large differences against the 7-day average (e.g. "streaming 180 %
  above"; thresholds are settings of the usage engine).
- **Security alerts of the period:** time, severity, rule, state and the name and one sentence of
  the alert catalogue ([ALERTS.md](ALERTS.md)). An alert's own title and details (error texts,
  log fields) never go into a report; no raw log line does.
- **Sealing of the previous day:** sealed (time, serial) / not sealed yet / failed / empty day;
  with a test stamp (mock) a "not legally valid" warning.
- **Data quality:** how far the data was computed, time not linked to a person, notes.

Formats:

- **HTML e-mail:** tables and inline styles only, so that Outlook and Gmail render it; no images,
  fonts, scripts or anything loaded from elsewhere. The only link is the ARCILOGGER web address
  (`alerts.web_base_url`; empty: `https://<[collector] bind>`). Turkish first, a one-line
  English summary under the title. The e-mail of a report with many persons is long (200
  persons: about 500 KB); Gmail clips messages longer than ~100 KB ("View entire message"). The
  full detail is in the PDF and the workbook.
- **PDF:** a cover (summary, method note, company, departments, changes, alerts, seal), then each
  department **on a new page** with its persons, and each person's detail (top 20 sites with time
  and category, categories, blocked attempts — the PDF lists at most 100 per person, the
  workbook every one the report lists; the limits below —, first / last activity, off-hours
  time). Every page carries "Sayfa n / N" (page n of N), the generation time and the method note.
  Turkish letters stay searchable and copyable (the embedded DejaVu fonts).
- **Excel:** the sheets **Özet** (summary), **Departmanlar**, **Kişiler** (persons),
  **Kişi-Site** (every person × site), **Kişi-Saat** (every person × hour), **Engellenenler**
  (the blocked attempts of the period, oldest first), **Cihazlar** (every device with traffic or
  with blocked attempts, also those without a person). Durations are Excel **time values** (`[h]:mm:ss`, they add up
  and sort), volumes are **numbers of bytes** (`#,##0`); no number is written as text. The header
  row is frozen and filtered. Texts starting with `=`, `+`, `-`, `@` get a leading `'`: no cell
  ever opens as a formula. Control characters an Excel file cannot hold (e.g. in a device's DHCP
  host name) are written visibly as `\xNN`. Times are written in Türkiye time.

**Limits of the blocked attempts:** the usage engine keeps at most `usage.blocked_per_day` (5000)
blocked-attempt rows per person, device or IP address and day, the newest; its daily counter
counts them all. Reports over whole days (full day, weekly, monthly) therefore count every
attempt, but of a day over the limit they list only the stored rows. Reports over part of a day
(the end-of-day report; a period given with hours in the preview or a run now) can count only the
stored rows: on a day over the limit the real number may be higher. When such a day leaves a
count or a list short, the data quality notes say so. A report also lists at most 50 000 attempts
(oldest first); a list cut at that limit says so in the last row of Engellenenler and in the data
quality notes, and the person and department counts include the ones not listed.

With `person_level` off a report contains the company and department totals only; with
`pseudonymize` on persons appear under pseudonyms (`Kullanıcı-7F3A`). Pseudonyms need the key
`usage.pseudonym_key` in the secret store ([USAGE.md](USAGE.md) section 6); while it is missing a
schedule with pseudonyms is not saved, previewed or run now: the API answers
`409 pseudonym_key_missing` and says how to store the key.

## 3. Delivery

- The worker sends (`reports.run_due`, every minute): it makes the due report, stores its files
  and sends **one e-mail per recipient** (`To` is that recipient only; nobody sees the other
  recipients, and a refused address affects only itself).
- The SMTP connection is the alert e-mail's: `[smtp]` and `smtp.password` in the secret store
  ([ALERTS.md](ALERTS.md) section 4). With `[smtp] enabled = false` the report is still made and
  stored, but its deliveries count as failed (`R20-report`).
- **Attachments:** the PDF and the workbook are attached. When together they exceed
  `reports.max_attachment_mb` (default 15 MB; 1 MB = 1,048,576 bytes) the e-mail goes without
  them and links to the download on the Raporlar page (`https://<server>/reports?run=<no>`).
- **The limit is the raw size of the attachments, not of the e-mail:** in the e-mail they are
  base64-encoded, about **1.37 times** larger, and the HTML body comes on top (200 persons: about
  500 KB). An e-mail with 15 MB of attachments is therefore about 22 MB. When the mail server
  refuses a message for its size (a permanent 5xx), that delivery fails at once and `R20-report`
  opens; it does not fall back to the download link. Set the value to **at most two thirds** of
  the server's message size limit (e.g. 13 for a 20 MB limit, 16 for 25 MB).
- **Retries:** temporary failures (connection, timeout, TLS, authentication, 4xx) are retried
  after 1, 5, 15, 60 and 180 minutes; after 6 attempts, or at once for a permanent 5xx refusal,
  that recipient's delivery **fails** and the local alert **`R20-report`** (medium) opens. It is
  resolved by itself once a later report reaches the same recipient (or the address is removed
  from every schedule). A crash between sending and recording sends that e-mail at most once
  more.
- **Run status:** `queued` → `running` (being made / sent, or waiting for a retry) → `sent`
  (finished: every e-mail went out, or none was asked for), `partial` (some recipients did not
  get it) or `failed` (the report could not be made, or no e-mail went out). A report that cannot
  be made (e.g. the usage engine failed) also opens `R20-report`; the next successful report
  resolves it.

## 4. Where the files are, and for how long

- `D:\ARCILOGGER\exports\usage-reports\<run number>\` — the PDF, the workbook and the HTML file;
  each file's size and SHA-256 are recorded in the database (`report_runs.files`). A download on
  the Raporlar page is checked against that hash (a changed file is refused) and audited
  (`report.download`). The folder also holds the e-mail as sent (`mail.html`, `mail.txt`), so a
  retry sends the same e-mail.
- Usage reports are **not evidence** and never go into the vault.
- After `reports.keep_days` (default **400** days) the `reports.purge` job (nightly after 03:20)
  deletes the files; the run record and the files' hashes stay, a download answers "expired"
  (410).
- The database backups contain the schedules and the run records; the files themselves are in
  the `exports` folder ([BACKUP.md](BACKUP.md)).

## 5. Settings

How to change them: [FIRST-STEPS.md](FIRST-STEPS.md) section 6 (`& $db settings set …`).

| Setting | Default | Meaning |
|---|---|---|
| `reports.keep_days` | 400 | Days the report files are kept (at least 1) |
| `reports.max_attachment_mb` | 15 | Limit of the attachments' total size before encoding (MB = 1,048,576 bytes, 1–100); above it the e-mail carries a download link. The e-mail itself is about 1.37 times larger: at most two thirds of the mail server's limit (section 3) |
| `reports.catch_up` | `true` | Send a report missed while the server was down once, for its latest time |
| `alerts.web_base_url` | empty | Address of the link in the e-mail (shared with the alert e-mail) |

## 6. Set-up and test

1. Set up and test the alert e-mail ([ALERTS.md](ALERTS.md) section 4,
   `arcilogger-worker alerts test-email --to <address>`). The report e-mail uses the same
   connection.
2. On the **Raporlar** page open "Tam gün raporu": enter recipients, choose the formats, set it
   **enabled**. A schedule without recipients cannot be enabled.
3. **Preview:** the page's preview (or `GET /api/v1/reports/preview?kind=full_day&format=pdf`)
   makes the report now, stores and sends nothing; review the content.
4. **Run now:** a schedule's "run now" (`POST /api/v1/reports/schedules/<no>/run`) queues a run;
   the worker makes and sends it within a minute. Use `send: false` for the files only; give
   `from` / `to` for a particular period (at most 92 days; without a time zone: Türkiye time).
5. Follow the status and each recipient's delivery in the run history; open the e-mail and the
   attachments as a recipient. On problems look for `R20-report` among the alerts and at
   `D:\ARCILOGGER\logs\worker.log`.

## 7. To verify on site

- The message size limit of the company's mail server (`reports.max_attachment_mb` at most two
  thirds of it, section 3).
- The addresses the reports go to and who receives person-level reports (KVKK, legal counsel).
- How long a large monthly report (many persons) takes to make and how large it is; the worker's
  other jobs wait for it (a month of 200 persons on the development machine: about 30 seconds for
  the PDF and the workbook together, PDF ~0.8 MB, workbook ~1.6 MB).
