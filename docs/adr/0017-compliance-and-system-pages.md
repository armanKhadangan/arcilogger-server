# ADR-0017: Uyum Durumu, Sistem and the first-run wizard — ten server-side checks, read-only pages, a guided checklist

- Status: accepted (2026-10-28). Decided by the developer on the owner's delegation; the owner
  answered two questions on 2026-10-28 (who sees Uyum Durumu, Q1; card 7's expected days, Q10)
  and may change any other part (open points at the end, with the default each one has now).
- Related: [ADR-0001](0001-windows-server-browser-only.md) (browser-only access),
  [ADR-0003](0003-integrity-on-windows.md) (sealing, verification, replica),
  [ADR-0005](0005-database-and-mvp-contracts.md) (reportlab, the MVP contracts),
  [ADR-0009](0009-database-backup.md) (the scheduled backup card 10 reads),
  [ADR-0010](0010-alerts-and-email.md) (R15/R20 checks), [ADR-0012](0012-scheduled-usage-reports.md)
  (Raporlar and the shared PDF building blocks), [ADR-0013](0013-offline-licensing.md) (5651 Temel
  is never locked), [ADR-0014](0014-browser-administration.md) (the settings pages the wizard
  opens, the agent and ApplySystem Sistem reports), [ADR-0015](0015-search-live-sources.md) (the
  summary table card 7 counts), [ADR-0016](0016-fortigate-dhcp-and-internet-exits.md) (the lease
  evidence card 8 follows). The names this decision fixes are code: the cards, their texts, the
  PDF and the routes (`arcilogger_api.compliance`), the resources and the disk forecast
  (`arcilogger_api.system`), the services, tasks, jobs and clock (`arcilogger_api.system_health`),
  CPU, memory and uptime (`arcilogger_core.hostinfo`), the content-filter evidence
  (`arcilogger_worker.compliance_filter`), the daily disk sample (`arcilogger_worker.system_jobs`,
  job `system.sample`), the three settings `alerts.filter_*` (`arcilogger_db.settings`,
  `arcilogger_db.settings_meta`), the web mirrors (`web/src/lib/compliance-types.ts`,
  `system-types.ts`, `system-health-types.ts`) and the wizard (`web/src/pages/setup-wizard/`).
  The endpoints are in [MVP-CONTRACTS.md](../MVP-CONTRACTS.md); the operator guide is
  [UYUM-DURUMU.md](../UYUM-DURUMU.md) / [COMPLIANCE-STATUS.md](../COMPLIANCE-STATUS.md); the
  permissions are in [roles.md](../roles.md) and [SECURITY.md](../SECURITY.md) section 16. The
  design is the Wave 4 contract (`design/wave-4-contract.md`, final 2026-10-28).
- Wording: nothing here claims legal compliance; ARCILOGGER is designed to support 5651
  obligations ("5651 yükümlülüklerini desteklemek için tasarlanmıştır"). "Uyum Durumu" is the
  page's name, not a verdict: the cards and the Uyum Durumu Raporu are technical checks of log
  collection, sealing and backups, not a legal assessment ("teknik denetim, hukuki değerlendirme
  değil"), and their results are to be confirmed with legal counsel. Examples use the
  repository's fictional site only (Örnek A.Ş., `172.20.x`, `00:00:5E:00:53:xx`, RFC 5737 public
  addresses).

## Context

The specification asks for a page of ten technical checks with a printable report (SPEC 3.4,
page "Uyum Durumu"), a page of the server's own health (SPEC 12.3, page "Sistem") and a first-run
wizard (roadmap 1.8 and 1.9). Until Wave 4:

- **Both pages were placeholders.** The menu entries `compliance` (`/compliance`,
  `compliance.read`) and `system` (`/system`, `system.read`) existed and rendered the placeholder
  page; the permissions existed, held by the administrator only.
- **The first run ended at the account.** `/setup` (public) created the first administrator; the
  rest of the configuration was found by walking through Ayarlar, and the setup page said the
  other wizard steps would follow later.
- **The facts were scattered.** The Panel showed ingest, seal, credit and clock boxes; the alert
  checks (R15, R20) watched sources, seals, verification, retention, credit, clock, replica,
  backup and lease evidence; the tasks and services wrote their status files under `state\` and
  `admin\`. Nothing showed whether content-filter records (web and DNS filter logs of the
  firewall) kept arriving.

The constraints:

- **Wave 3 was still being implemented** and owned many of the files Wave 4 had to change
  (README, LISANS/LICENSING, CORRELATION, ILK-ADIMLAR/FIRST-STEPS, the permission specs, the
  migrations test). A new API area, worker job, built page or alert code fails a test whose other
  half is one of those files.
- **5651 Temel is never locked** (ADR-0013): neither page, nor the report, may depend on a paid
  module.
- **KVKK least privilege:** neither page may become a way around the permissions of the person,
  device and log pages.
- **The offline bundle and the virtual accounts:** no new third-party dependency without need;
  the API runs as an unprivileged virtual account and cannot query the Service Control Manager or
  the Task Scheduler; the documents are bundled on the server, not served.
- **Read, never act:** the pages must not ask a device or Kamu SM, download or install anything.

## Decision

### Part A — how Wave 4 was delivered

- **D1 Two phases.** Five packages (W4-A…W4-E) start at once and merge into `wave-4` while
  Wave 3 is still running; they never edit a file a Wave 3 package owns and never cross a coupling
  point. Two late packages do every edit of a file Wave 3 owns today, after Wave 3 has merged into
  `wave-4`: W4-F (switch-on: mounting, job registration, `R20-filter`, the auditor's permission,
  hooks, routes, samples, end-to-end tests) and W4-G (the documents Wave 3 owns, this record).
  *Alternative:* wait for Wave 3 to finish before starting Wave 4.
- **D2 Unmounted API packages until the switch-on.** The API code of Uyum Durumu and Sistem lives
  in new packages that `arcilogger_api.routers` does not discover (`arcilogger_api.compliance`,
  `arcilogger_api.system`, `arcilogger_api.system_health`); W4-F mounts them with three two-line
  modules under `routers/`. Until then their tests mount the router themselves. `routers/` mounts
  every module it finds, and a mounted area needs its row in LISANS/LICENSING (a Wave 3 file).

### Part B — Uyum Durumu and its report

- **D3 One server function computes the ten cards; the page and the PDF use it.** Cards come back
  fully worded in Turkish and English (title, reason, facts, the verify-on-site note), so the web
  shows the server's text and the PDF says the same (SPEC 3.4). A card whose computation raises is
  returned `neutral` with "Bu denetim şu anda yapılamadı: {error_code}" (the exception's class
  name, never its message); the other cards are unaffected. The ten cards: log sources, yesterday
  sealed and verified, seal chain, retention, Kamu SM credit and certificate, server clock,
  content-filter records, DHCP and ARP records (lease evidence), replica, configuration and
  database backups. *Alternative:* card texts in the web app's locales (the page and the PDF
  would drift).
- **D4 Four tones, the sealing page's vocabulary.** `ok`, `warning`, `error`, `neutral`
  ("Tamam", "Dikkat", "Sorun", "Bilgi yok" / OK, Attention, Problem, No data); `neutral` means "no
  data yet / does not apply" and always carries its reason (owner question Q3). A status is never
  shown by colour alone: icon and text everywhere, in the PDF as text in brackets.
- **D6 Card 7's settings are three `alerts.*` keys** in the existing settings group "Uyarılar ve
  e-posta": `alerts.filter_days` (ISO weekdays, default `[1,2,3,4,5,6,7]`),
  `alerts.filter_skip_holidays` (default `true`: the days of `usage.holidays` are not expected)
  and `alerts.filter_quiet_hours` (default `24`, 6–168). No new settings group: the group list is
  pinned by tests of other packages, and the keys are also the thresholds of `R20-filter` (D7).
  The evidence (`compliance_filter.filter_evidence`) counts `fg_web` and `fg_dns` rows of the
  configured FortiGate sources from the exact hourly summary table (ADR-0015) and is shared by the
  card and the alert. *Alternative:* a settings group of its own.
- **D7 A new alert `R20-filter`** (dedup key `R20-filter:none`, severity high: owner question
  Q10) opens when no web or DNS filter record arrived within `alerts.filter_quiet_hours` on
  expected days, and closes by itself when one arrives. It is added only by W4-F, in the existing
  system alert job; before that no Python string constant has the whole value `R20-filter`, because
  the documentation test turns every such constant into a required row of ILK-ADIMLAR section 9
  (a Wave 3 file).
- **D8 The Uyum Durumu Raporu is a PDF made on request** (`GET /compliance/report.pdf`): computed
  fresh, not stored, not time-stamped, audited `compliance.report_export` with its SHA-256 (also
  sent as `X-Arci-Sha256`) and the four tone counts. It names the signed-in account that exported
  it, the one exception to the rule that the pages carry no names. No schedule in Wave 4 (owner
  question Q14): Raporlar is the licence module `usage_reports`, a 5651 report must not depend on
  it, and a new schedule kind needs a migration (D5). *Alternatives:* e-mail it on a schedule;
  time-stamp each export (one credit per export).
- **D12 Cards name their documentation as text** ("UYUM-DURUMU.md bölüm 2.{n}" /
  "COMPLIANCE-STATUS.md section 2.{n}"); the web app has no documentation route (owner question
  Q15). The documents are bundled on the server, not served. *Alternative:* serve the bundled
  documents in the web app.
- **D13 Card links are nav keys** (`sources`, `sealing`, `search`, `settings`, `system`); the web
  hides a link the user may not open (`linkTarget`), the card is still shown. The answer is the
  same for every user and is cached for 30 s in the API process; the backup confirmation clears
  the cache.

### Part C — Sistem and the first-run wizard

- **D9 System health comes from files the API may already read**: the status files of the
  services, the agent's heartbeat, ApplySystem's status, the evidence files of the scheduled tasks,
  `job_runs`, the clock samples. No Service Control Manager or Task Scheduler query (owner question
  Q19). A task is `ok`, `late` (evidence older than its cadence plus a grace), `missing` (no
  evidence yet), `not_used` (switched off in the configuration) or `not_observable` (Retention and
  Requests leave nothing ArciApi may read). *Alternative:* a SYSTEM-written status file of the
  services and tasks (an installer, ACL and PowerShell change).
- **D10 CPU and memory through the standard library** in `arcilogger_core.hostinfo` (`ctypes` on
  Windows, `/proc` on Linux); the CPU percentage is computed between two readings kept in the API
  process, so the first request answers `null` ("ölçülüyor"). No `psutil` (owner question Q16):
  no new third-party dependency and notice in the offline bundle. *Alternative:* `psutil`, with
  per-process figures.
- **D11 The first-run wizard is a guided checklist** at `/settings/setup` (`settings.manage`)
  that opens the existing Ayarlar pages: administrator, site settings, log sources, Kamu SM test
  server, Kamu SM production, replica, e-mail, summary. Each step's state ("Tamam" / "Eksik") is
  derived from the server's configuration and request history; nothing is stored, and a skipped
  step ("Bu adımı şimdilik atla") simply stays open (owner question Q18). Wizard mode starts with
  `wizard=1` in the URL and is kept for the browser tab; an Ayarlar page opened from it leads
  back with "Kurulum sihirbazına dön". The entries are the card "İlk kurulum sihirbazı" on Ayarlar,
  "İlk kurulum sihirbazını aç" on Uyum Durumu when a card is not `ok`, and the third step
  "Kurulum sihirbazı" ("Girişten sonra, Ayarlar'da") of `/setup`; there is no automatic redirect
  after the first sign-in. The wizard and Ayarlar cannot drift (they are the same forms), and
  `/setup/*` cannot be a sign-in target. The wizard pre-fills nothing and shows no device commands:
  the sources step refers to DEVICE-SETUP.md, whose commands are verified on site.
  *Alternative:* a separate wizard that remembers skipped steps and redirects the first
  administrator.

### Part D — shared

- **D5 No new table, no migration in Wave 4.** Disk samples go to `state\system-history.json`
  (`{"schema": 1, "days": [...]}`, the newest 120 days, written daily after 00:30 by the worker
  job `system.sample`, 5651 Temel, always runs); the confirmation that the configuration was
  copied to offline media is an audit entry (`compliance.config_backup_confirm` with the
  configuration's SHA-256, which card 10 compares with the current file: owner question Q13). A
  migration would move the migrations test's head and need data-model rows, both owned by a Wave 3
  package; the worker may already write `state\`. *Alternative:* a disk-sample table and a
  confirmation table.
- **D14 The placeholder mechanism is deleted once both pages exist** (the placeholder page, its
  route factory and its locale keys). No menu entry is a placeholder any more.
- **D15 Permissions (owner questions Q1, Q2).** `compliance.read` is held by `admin` and `auditor`
  (Q1, answered 2026-10-28): the auditor opens Uyum Durumu with its cards and downloads the Uyum
  Durumu Raporu (`GET /compliance/report.pdf` needs `compliance.read` only). Everything that
  changes state stays admin-only: the backup confirmation (`settings.manage`), every setting and
  the wizard. `system.read` (Sistem, the Panel's disk card) stays admin-only (Q2). The cards'
  alert references stay in the auditor's answer although `alerts.read` stays admin-only; the web
  hides their link (D13). hr and viewer hold none of these permissions. The cards and the PDF carry
  no data about log subjects (no client address or MAC, no person attributed to a device, no
  device host name, no sender address, no replica target path; card 8 lists lease-alert titles,
  which name causes, counts and configured networks only), so there is no KVKK notice gate and
  viewing is not audited, like the Panel; the PDF export and the backup confirmation are audited,
  the auditor's export like the administrator's. Both pages are 5651 Temel: no licence state
  closes them. *Alternative:* Uyum Durumu for the administrator only (the previous answer of
  roles.md open question 5).

## Owner decisions (2026-10-28)

| # | Question | Answer |
|---|---|---|
| Q1 | Who sees Uyum Durumu? Should the auditor (roles.md open question 5)? | admin and auditor (`compliance.read`); the auditor opens the page and downloads the Uyum Durumu Raporu; the backup confirmation, the settings and the wizard stay admin-only (D15) |
| Q10 | Card 7: expected days, holidays, window, web **or** DNS, `R20-filter` severity | every day of the week is expected; the days listed in `usage.holidays` are not; a rolling window of 24 hours; a web or a DNS filter record is enough; `R20-filter` is high (D6, D7) |

## Deviations recorded during implementation

Each package recorded its deviations in the contract; the ones that refine a decision above (the
complete list, with texts, is in MVP-CONTRACTS.md and ASSUMPTIONS.md):

- **From the specification and roadmap** (contract, 2026-10-28): Sistem shows the own collector's
  statistics, not rsyslog's (ADR-0002); card 3 judges the verification tasks as they run (2 days
  daily, 90 days weekly, the whole chain monthly), not a daily 30-day and a weekly full check
  (Q6); card 8 follows the configured lease evidence instead of "DHCP and ARP" literally (Q11);
  no DNS filter profile is stored, so card 7 names web filter profiles only and says so, "sahada
  doğrula"; no `compliance` schedule kind in Raporlar (D8, Q14); the wizard pre-fills no source
  from a device list (D11, the product ships no customer data); card 10 reports the dumps and the
  hand-made configuration copy that exist, not an encrypted nightly dump to the replica target.
- **D3 (W4-A):** card 4 reads its active alerts by rule (`R20-retention`, severity other than
  `info`), not by a dedup-key prefix, because the retention task's alerts are imported without a
  key; card 5 reads a credit of 0 or below as used up; card 10 takes its error row for a
  `state\backup\last.json` that does not parse, while a file that cannot be read at all leaves the
  card `neutral` ("could not be checked"), as every unreadable file of every card.
- **D13 (W4-A):** an answer holding a card from that error path is not kept in the 30 s cache.
- **D9 (W4-D):** a job is `running` from a second bounded probe of `job_runs` per job (the worker's
  status file never shows a job in progress), and `failing` also when its last run is `failed`
  without a counted failure; Seal's evidence skips the seal states Retention rewrote for deleted
  days.
- **D11 (W4-C):** the e-mail step is done only when sending is switched on (`[smtp] enabled`),
  not merely when a server and a sender are set; switched off, it lists "E-posta gönderimi
  kapalı." / "Sending e-mail is switched off."
- **Disk forecast (Q17, W4-B):** a date more than 100 years away is `null` while the state stays
  `estimated`; whether a drive is already above the warning mark is read from its measured use,
  never from a `null` date.

Text-only deviations (the English singular of "Every 1 minute", the Turkish suffix of a time read
aloud) change no decision.

## Consequences

- **API:** six endpoints (`GET /compliance`, `GET /compliance/report.pdf`,
  `POST /compliance/config-backup`, `GET /system`, `GET /system/disk`, `GET /system/health`), one
  error code (`config_backup_unreadable`, 409), two audit actions (`compliance.report_export`,
  `compliance.config_backup_confirm`). `GET /system` and `GET /system/health` answer within 1 s on
  the performance data; `GET /system` caches the database sizes for 5 minutes.
- **No new permission, table, migration or third-party dependency**; the auditor gains
  `compliance.read`. `GET /settings` shows three more settings in "Uyarılar ve e-posta"; the
  worker has one more job (`system.sample`) and the system alert job one more check
  (`R20-filter`); the data folder one more state file (`state\system-history.json`).
- **The menu has no placeholder page any more** (D14); the Panel gains the disk card (admin only)
  and its clock card's link to Sistem starts to work.
- **Lawyer review:** the Uyum Durumu Raporu texts are on the lawyer review list, like the lookup
  report's (owner question Q20). The report is not time-stamped: its integrity after export rests
  on the SHA-256 in the audit log.
- Residual risks (kept, documented): a task is judged by its evidence file, so a task disabled in
  the Task Scheduler shows only when its evidence becomes `late`, and Retention and Requests are
  `not_observable`; the credit and the clock are the newest *recorded* values, shown with their
  time; card 7 counts arriving records and cannot tell whether every policy that should filter
  does (sahada doğrula); the configuration backup confirmation is the administrator's statement,
  compared with the current file by SHA-256, not a check of the offline media; the CPU percentage
  is per API process; the disk forecast is a straight line over the last 30 samples; `job_runs`
  grows by about 17,000 rows a day without a purge (Q21).

## Open points (owner decision)

Each has the default implemented above; changing one is a small follow-up. Q1 and Q10 are
answered (above).

| | Question | Default |
|---|---|---|
| Q2 | Who sees Sistem and the Panel's disk card? | `system.read` = admin only (D15) |
| Q3 | A fourth grey state "no data / does not apply"? | yes, `neutral` (D4) |
| Q4 | Card 1 with no watched source: amber or red? | amber |
| Q5 | A stamp not from the Kamu SM production server (mock, RFC 3161, test server): which card, which colour? | card 5 red, card 2 amber |
| Q6 | Card 3: change the verification tasks to the specification or judge the existing 2/90/monthly results? | judge the existing results |
| Q7 | Card 4: "system age" = first sealed day (deleted days included)? Retention off with days past the configured maximum: amber or red? | first sealed day; amber |
| Q8 | Card 5: an extra "days of credit left" threshold? Certificate red only when expired? | no extra threshold; red only when expired |
| Q9 | Card 6: an offset equal to the limit is a problem (`<`) while the Panel uses `<=`; align the Panel? | card `<`, Panel unchanged |
| Q11 | Card 8: follow the configured lease evidence instead of "DHCP and ARP" literally? | yes |
| Q12 | Card 9: replica switched off = red or amber? | red |
| Q13 | Card 10: is an administrator's confirmation of the offline configuration copy acceptable evidence? | yes, compared with the current file's SHA-256 (D5) |
| Q14 | Uyum Durumu Raporu: e-mail it on a schedule (to whom)? Time-stamp it (one credit per export)? | on request only; not time-stamped, SHA-256 in the audit log (D8) |
| Q15 | Documentation links: name document and section as text, or serve the bundled documents? | text (D12) |
| Q16 | CPU and memory: standard library or `psutil`? | standard library (D10) |
| Q17 | Disk forecast: linear over the last 30 daily samples, shown from 7 samples? | yes |
| Q18 | Wizard: remember skipped steps and redirect the first administrator to it after the first sign-in? | no: state derived from the configuration, entry from Ayarlar and Uyum Durumu (D11) |
| Q19 | Read Windows service and Task Scheduler states (needs a SYSTEM-written status file)? | no; tasks judged by their evidence files (D9) |
| Q20 | Put the Uyum Durumu Raporu texts on the lawyer review list? | yes |
| Q21 | Purge `job_runs` (about 17,000 rows a day), after how many days? | no purge in Wave 4 |
