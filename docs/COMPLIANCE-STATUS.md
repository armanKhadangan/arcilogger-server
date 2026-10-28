# Uyum Durumu, Sistem and the first-run wizard

> Türkçe: [UYUM-DURUMU.md](UYUM-DURUMU.md). Permissions: [SECURITY.md](SECURITY.md),
> [roles.md](roles.md). Alerts: [ALERTS.md](ALERTS.md). Backups: [BACKUP.md](BACKUP.md).
>
> ARCILOGGER is *designed to support* 5651 obligations ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance. Confirm the results of the cards and of
> the report with your legal counsel.

This document explains the **Uyum Durumu** (Compliance status) page of the web interface (ten check
cards: what each one checks, which file or table it reads, which condition gives which state, what
to do), the **Uyum Durumu Raporu** (compliance status report, PDF), the **Sistem** (System) page and
the **first-run wizard**. The cards refer to this document as "COMPLIANCE-STATUS.md section 2.{n}";
section 2.{n} is the section of card n.

Paths are those of a default installation: the data folder `D:\ARCILOGGER` (status files in
`D:\ARCILOGGER\state\`), the configuration `C:\ProgramData\ARCILOGGER\arcilogger.toml`.

## 1. What the Uyum Durumu page is, and what it is not

**Uyum Durumu** (`/compliance`) shows the technical checks of log collection, sealing and backups
on one page. Each of the ten cards is one check and is in one of four states; the state is always
shown by an icon **and** a text, never by colour alone:

| State | Meaning |
|---|---|
| **Tamam** (OK) | The check found no problem. |
| **Dikkat** (Attention) | Something needs looking after, but no evidence is lost yet (a job waiting, a threshold coming close, a setting missing). |
| **Sorun** (Problem) | Something affects the collection, sealing or protection of the evidence now. |
| **Bilgi yok** (No data) | No data yet, or the check does not apply to this installation; the reason is always given. |

This notice is always at the top of the page (and in the report):

> ARCILOGGER is designed to support 5651 obligations. These cards are the results of technical
> checks of log collection, sealing and backups; they are neither a legal assessment nor a
> statement of conformity. Confirm the results with your legal counsel.

**What it is not.** "Uyum Durumu" is the page's name, not a verdict. Ten cards in **OK** do not mean
that the organisation meets its obligations: the cards check only what ARCILOGGER itself records.
The page asks no device and not Kamu SM, downloads, installs and changes nothing; it only reads the
files under `state\` and `admin\`, the vault, the database and the active alerts. A value such as
the credit is the **newest recorded** one, shown with its time. Whether the devices are set up
correctly or the replica target is write-once cannot be checked from ARCILOGGER; the cards say so
with a "Verify on site" note (section 6).

**Who sees what.** Both pages belong to **5651 Core**: no licence state closes them.

| Action | Permission | Roles |
|---|---|---|
| Uyum Durumu page and its cards | `compliance.read` | Administrator, Auditor |
| Download the Uyum Durumu Raporu (PDF) | `compliance.read` | Administrator, Auditor |
| "Yapılandırma yedeğini aldım" (I have copied the configuration, card 10) | `settings.manage` | Administrator only |
| First-run wizard and every setting | `settings.manage` | Administrator only |
| Sistem page and the Disk card of the Panel | `system.read` | Administrator only |

The İK Raporları (`hr`) and Rapor İzleyici (`viewer`) roles hold none of these permissions (owner decision of
2026-10-28; [SECURITY.md](SECURITY.md)). The auditor sees every card and the PDF button; the backup
confirmation button and the wizard link are not shown to them. A card's link to its detail page is
hidden when the user cannot open that page (for the auditor: Kaynaklar, Güvenlik, Ayarlar and Sistem;
the links to Mühürleme and Arama stay); the card itself is always shown. The card's active alerts
(rule, severity, time opened) are shown to the auditor too, without a link to the Güvenlik page.

**No personal data, viewing is not audited.** The cards and the report carry no data about log
subjects: no client IP or MAC address, no name of a person assigned to a device, no device host
name, no sender address, no replica target path. They show only configured source names, counts,
times, drive letters, table names, web filter profile names and the addresses of the configured
time servers. So the page asks for no KVKK notice acknowledgement, and opening it (like the Panel) is
not written to the audit log. Written to the audit log are: downloading the report
(`compliance.report_export`, section 3) and confirming the configuration backup
(`compliance.config_backup_confirm`, section 2.10).

**Refresh.** The page refreshes itself in the background every 30 seconds; this automatic refresh
does not keep the session alive (the idle timeout keeps running). The **Yenile** (Refresh) button
counts as user activity. The server keeps the computed cards for 30 seconds for every user, so a
change shows within about a minute at most. The configuration backup confirmation drops the kept
result at once: card 10 changes right after the confirmation. The page shows "Güncellendi {time}"
(Updated).

**A card that cannot be computed** shows **No data** with "This check could not be run now: {error
code}" (the error code is the error's class name; the details go to the log of the ArciApi service).
The other cards are not affected.

**How a card is evaluated.** Each card's table in section 2 is read from top to bottom: the **first
row that holds** gives the state and the reason. If no row holds, the card is **OK**. Each card also
lists the active alerts of its subject (state `open` or `acknowledged`), newest first, at most five.

**The cards:**

| # | Card | Detail page | Alerts |
|---|---|---|---|
| 1 | Log sources are sending | Kaynaklar (Sources) | `R15` |
| 2 | Yesterday's records sealed and verified | Mühürleme (Sealing) | `R20-seal` (yesterday's) |
| 3 | Seal chain intact | Mühürleme (Sealing) | `R20-verify` |
| 4 | Retention period | Mühürleme (Sealing) | `R20-retention` |
| 5 | Kamu SM credit and time-stamp certificate | Mühürleme (Sealing) | `R20-credit` |
| 6 | Server clock | Sistem (System) | `R20-clock` |
| 7 | Content filter records | Arama (Search) | `R20-filter` |
| 8 | DHCP and ARP records (access records) | Kaynaklar (Sources) | `R20-lease` |
| 9 | Second copy (replica) | Mühürleme (Sealing) | `R20-replica` |
| 10 | Configuration and database backups | Ayarlar (Settings) | `R20-backup` |

The summary at the top reads "{ok} of {total} checks OK". When a card is not **OK** and the user is
an administrator, the page also shows the link **İlk kurulum sihirbazını aç** (Open the first-run
wizard, section 5).

## 2. The cards

The reasons in the tables are the texts the card shows (in English beside the Turkish); `{…}` is
filled by the card ("—": no value). Meaning and thresholds of the alert codes:
[ALERTS.md](ALERTS.md) section 2.

### 2.1 Card 1 — Log sources are sending

**What it checks.** That every watched log source sent messages within its heartbeat. A watched
source is a source R15 watches: enabled, expected and configured (not an unknown sender) (Settings →
Sources).

**What it reads.** The `sources` table of the database (the watched sources, the source's heartbeat
`[[sources]] heartbeat_minutes`, default 15 minutes, and the time ingest last loaded from it), the
collector's status file `state\collector-status.json` (the last message received) and the active
`R15:<source>` alerts. A source's newest message is the later of the two; the computation is the
same as the Panel's "Log alımı" (log intake) box.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | An active `R15` alert for a source | "{n} source(s) have sent nothing for longer than their heartbeat: {names}." |
| 2 | Attention | A watched source has been silent for longer than its heartbeat, but there is no `R15` alert yet | "{names} silent for longer than the heartbeat; the alert is still in its start-up grace period." |
| 3 | Attention | No watched source | "No log source is watched. Define the sources in Settings → Sources." |
| 4 | OK | — | "All {n} watched sources sent data within their heartbeat." |

Row 2 is R15's grace time: no alert opens for `alerts.r15_startup_grace_minutes` (15 minutes) after
the worker started or the source's row was created; a source that never sent alerts
`alerts.r15_never_seen_minutes` (60 minutes) after its row was created.

Facts: watched sources, sending, the names of the silent ones, heartbeat ("per source, default
15 min").

**What to do.**

- Silent source (rows 1-2): is the device on and connected, does its syslog setting point at
  ARCILOGGER ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 4, verify the commands on site); is the
  collector running (Sistem → Services)? The Kaynaklar page shows the source's last record and its
  gaps. Once the source sends again, `R15` resolves by itself and the card recovers.
- If the source was switched off on purpose or is no longer expected, switch it off in Settings →
  Sources; a disabled source is not watched ([SETTINGS.md](SETTINGS.md) section 1).
- No watched source (row 3): add the firewall and the core router in Settings → Sources (section 5,
  the wizard's "Log sources" step).

### 2.2 Card 2 — Yesterday's records sealed and verified

**What it checks.** That yesterday (Türkiye time) was sealed and verified and that its time stamp
came from the Kamu SM production server.

**What it reads.** The day state the calendar of the Mühürleme page shows for yesterday (the same
computation: the vault, `state\seal\<day>.json`, the verification results under `state\verify\` and
verifications started from the Mühürleme page); `alerts.seal_overdue_hour` (default 9, that is
09:00); whether `[tsa]` points at the Kamu SM production server. Yesterday's `R20-seal` alert is
listed on the card; the day state decides the card's state.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | The day "Failed" | "Sealing or verification of yesterday ({day}) failed: {message}" |
| 2 | Problem | The day is "Waiting to be sealed" and today's time is past `alerts.seal_overdue_hour` | "Yesterday ({day}) was not sealed by {hour}:00." |
| 3 | Problem | No raw records for the day and at least one watched source | "There are no raw records for yesterday ({day}); the sources may have sent nothing." |
| 4 | Attention | The day is "Waiting to be sealed", before the overdue hour | "Yesterday ({day}) is waiting to be sealed (the Seal task runs after 00:10)." |
| 5 | Attention | The day is sealed, not verified yet | "Yesterday ({day}) is sealed but not verified yet (VerifyDaily runs at 01:00)." |
| 6 | Attention | The day was time-stamped late | "Yesterday ({day}) was time-stamped late." |
| 7 | Attention | The day is sealed and verified, but `[tsa]` does not point at the Kamu SM production server | "Yesterday was sealed and verified, but the time stamp is not from the Kamu SM production server." |
| 8 | No data | No records for the day and no watched source | "No records for yesterday; no watched source is defined yet." |
| 9 | OK | The day is sealed and verified | "Yesterday ({day}) was sealed and verified." |

`{message}` is the error message the Mühürleme page shows for the day ("—": none). Facts: day,
state (with its label on the Mühürleme page), sealed at, verification ("{time}, passed|failed" or
"—"), time-stamp server (with the names of section 2.5).

**What to do.**

- Failed or overdue (rows 1-2): look at the day's details on the Mühürleme page and at
  `D:\ARCILOGGER\logs\core.log`; the `R20-seal` and `R20-seal-config` alerts on the Güvenlik page
  give the cause (access to Kamu SM, credit, credentials, root certificates: [kamusm.md](kamusm.md),
  [ALERTS.md](ALERTS.md) section 2). The Seal task retries every 5 minutes; **Şimdi mühürle** (Seal
  now) on the Mühürleme page works too.
- No raw records (row 3): the sources may have sent nothing yesterday; see card 1 and the Kaynaklar
  page.
- Waiting or not verified (rows 4-5): this is normal in the morning; the Seal task runs after 00:10,
  VerifyDaily at 01:00. If it is still so in the afternoon, look at the tasks' last evidence in
  Sistem → Scheduled tasks (section 4.6).
- Time-stamped late (row 6): look at the day's details on the Mühürleme page and find out why the
  server or the connection to Kamu SM failed that night. The card judges yesterday only: it recovers
  once the following days are sealed on time.
- Not the production server (row 7): see card 5 (section 2.5).

### 2.3 Card 3 — Seal chain intact

**What it checks.** That the sealed days of the 30 days ending yesterday are verified and that the
monthly full chain check passed.

**What it reads.** The day states the Mühürleme page shows for the last 30 days; the result of the
full chain check `state\verify\last-full.json` (`at`, `ok`; the VerifyMonthly task, the 1st of the
month at 04:00); the first sealed day in the vault; an active `R20-verify` alert. A sealed day counts
as "verified" when a verification of it passed within the last 35 days. The verification tasks are
unchanged: VerifyDaily verifies the last 2 days every day, VerifyWeekly the last 90 days on Sundays,
VerifyMonthly the whole chain every month; the card judges their results.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | One of the 30 days "Failed" | "{n} day(s) of the last 30 failed: {days}." |
| 2 | Problem | `last-full.json` says `"ok": false` or cannot be read, or an `R20-verify` alert is active | "The full chain check ({at}) failed." |
| 3 | Attention | A sealed day was not verified in the last 35 days | "{n} sealed day(s) of the last 30 were not verified in the last 35 days." |
| 4 | Attention | A day older than yesterday is still waiting to be sealed | "{n} day(s) of the last 30 are not sealed yet: {days}." |
| 5 | Attention | The last full chain check is more than 35 days old; or it never ran and the first sealed day is at least 35 days ago | "The last full chain check was on {at}, more than 35 days ago." — never ran: "The full chain check has never run." |
| 6 | No data | No sealed day among the 30 | "No sealed day in the last 30 days." |
| 7 | OK | — | "All {n} sealed days of the last 30 are verified; the full chain check of {at} passed." — before the first full check: "… the full chain check runs on the 1st of each month and has not run yet." |

An unreadable `last-full.json` (damaged, or a file the API may not read) counts as a failed full
check (row 2, time "—"); it is not passed over as "could not be checked". Days time-stamped late,
days without data, deleted days and the running day do not change the state; they are only counted
(a day time-stamped late but verified within 35 days counts as "verified"). Facts: the numbers of
verified, not verified, failed and "no data or deleted" days, the last full chain check ("{time},
passed|failed"), the verification tasks ("daily the last 2 days, weekly the last 90 days, monthly
the whole chain").

**What to do.**

- Failed day or failed full check (rows 1-2): look into it the same day. Open the day on the
  Mühürleme page and verify it again with **Doğrula** (Verify); **do not delete or change** raw logs
  or the vault. Independent verification: [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md). `R20-verify`
  resolves by itself once the day verifies again. If the file cannot be read, check the Windows
  rights (section 6).
- Day not verified (row 3): are the VerifyWeekly and VerifyMonthly tasks running (Sistem →
  Scheduled tasks)? **Doğrula** on the Mühürleme page verifies at once.
- Day waiting (row 4): as for card 2, see the Seal task and the `R20-seal` alerts.
- Full check old or never run (row 5): check the VerifyMonthly task; you can start it by hand in an
  elevated PowerShell (it reads the whole chain and may take long):

  ```powershell
  Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName VerifyMonthly
  ```

### 2.4 Card 4 — Retention period

**What it checks.** That the vault holds at least 730 days of sealed records (once the installation
is old enough) and that, with retention off, days older than needed do not pile up in the vault.

**What it reads.** `[retention] enabled` (default `false`) and `[retention] days` (default 760, at
least 730); the oldest sealed day in the vault; the retention task's deletion records
(`vault\deletions`); active `R20-retention` alerts. The "first sealed day" is the earlier of the
oldest day in the vault and the oldest day retention deleted.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | An active `R20-retention` alert other than `info` | "The retention task has an open alert." |
| 2 | Problem | The first sealed day is at least 730 days ago, but the oldest sealed day in the vault is younger than 730 days | "The installation is older than 730 days, but the oldest sealed day is {oldest} ({age} days ago)." |
| 3 | Attention | Retention is off and the vault holds sealed days older than `[retention] days` | "Retention is off and {n} sealed day(s) older than {days} days are in the vault (the oldest {oldest}). Turn retention on so that personal data is not kept longer than needed." |
| 4 | No data | No sealed day | "No sealed day yet." |
| 5 | OK | — | Oldest day at least 730 days ago: "The oldest sealed day is {oldest} ({age} days ago); the vault holds the 730 days." — otherwise: "… the installation is younger than 730 days." |

The card reads its alerts by **rule**, not by dedup key: the retention task's alerts are imported
without a key and every refused run is an alert of its own. The `info` note of a completed deletion
does not affect the card. The card judges sealed days only; the retention of database tables
(`db.retention_days`) is not its subject. Facts: oldest sealed day, retention ("on, {days} days" or
"off"), days deleted by retention.

**What to do.**

- Retention alert (row 1): review the deletion record and `D:\ARCILOGGER\logs\core.log`; remove the
  cause of a refused or incomplete run, the next run tries again. These alerts never resolve by
  themselves: add the cause as a note and close them on the Güvenlik page.
- Days of the retention period missing from the vault (row 2): days younger than 730 days, which
  must be kept, were deleted from the vault or lost. Review the deletion records, the retention alerts on the Güvenlik page and the audit
  log; with the replica on, the second copy of those days is on the replica target. Assess the
  missing period with your legal counsel.
- Retention off (row 3): so that personal data is not kept longer than needed, turn retention on in
  Settings → Retention; the number of days to be deleted is shown first and confirmed by typing
  ([SETTINGS.md](SETTINGS.md) section 1). Set the retention period with your legal counsel (at least
  730 days).

### 2.5 Card 5 — Kamu SM credit and time-stamp certificate

**What it checks.** That the time stamps come from the Kamu SM production server, that the credit
lasts and that the TSA certificate of the newest time stamp is valid.

**What it reads.** The `[tsa]` settings (provider and server: the Zamane client and the Kamu SM
production server?); the credit history `state\credit.jsonl` (the Credit task daily at 06:00, and
**Kontör sorgula** (Query credit) on the Mühürleme page); `[tsa] credit_alert_threshold` (default
500); the estimated days from the use of the last 30 days (the Panel's computation);
`tsa_cert_not_after` from the state file of the newest sealed day (`state\seal\<day>.json`). The
`R20-credit` alert is listed on the card; the conditions below decide the state.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | The time stamps are not taken from the Kamu SM production server (trial, RFC 3161 or the Kamu SM test server) | "Time stamps are not taken from the Kamu SM production server (provider: {provider}). Complete the Kamu SM connection in Settings → Kamu SM." |
| 2 | Problem | The newest known credit is 0 or less | "The Kamu SM credit is used up." |
| 3 | Problem | The TSA certificate of the newest time stamp has expired | "The TSA certificate of the newest time stamp expired on {date}." |
| 4 | Attention | The credit is at or below the threshold | "{credits} credits left; threshold {threshold}. Estimated to last {days} days." |
| 5 | Attention | No credit reading at all, or the last one is more than two days old | "The credit is unknown or the last reading ({at}) is more than two days old." |
| 6 | Attention | The certificate expires in 180 days or less | "The TSA certificate of the newest time stamp expires on {date} ({days} days left)." |
| 7 | No data | No sealed day yet | "No day has been stamped with Kamu SM yet." |
| 8 | OK | — | "{credits} credits left (threshold {threshold}); the TSA certificate of the newest time stamp is valid for {days} more days." |

Row 2 also counts a negative reading in the credit log as used up. A new installation shows
**Problem** on this card until the Kamu SM production connection is complete (row 1); in the same
situation card 2 shows only **Attention** (section 2.2 row 7). "Two days" are two missed runs of the
daily Credit task; the 180 days are the specification's number. Time-stamp server names: "Kamu SM
production", "Kamu SM test", "Trial (mock)", "RFC 3161". Facts: time-stamp server, credits left,
estimated days, threshold, last reading, TSA certificate expires, days left.

**Verify on site** (the card's note): "The credit is read from the Kamu SM client's output; verify
the format on site." ([kamusm.md](kamusm.md) section 3, K4; section 6.)

**What to do.**

- Not the production server (row 1): complete the Settings → Kamu SM wizard: a trial with the test
  server, comparing the root certificate fingerprints with the values Kamu SM publishes, then
  **Üretime geç** (Switch to production) ([kamusm.md](kamusm.md), [INSTALL.md](INSTALL.md)
  section 10).
- Credit used up or low (rows 2, 4): buy credit ([kamusm.md](kamusm.md) section 1). Without credit
  no day can be sealed; card 2 and `R20-seal` show it.
- Credit unknown (row 5): read it with **Kontör sorgula** on the Mühürleme page (`seal.manage`);
  look at the Credit task's last evidence in Sistem → Scheduled tasks. If reading keeps failing,
  verify the output format on site (section 6).
- Certificate expired or expiring (rows 3, 6): check the newest sealed day on the Mühürleme page and
  the Seal task. Long-term validity and the renewal manifest: [kamusm.md](kamusm.md) section 6;
  assess what to do with your legal counsel.

### 2.6 Card 6 — Server clock

**What it checks.** That the server clock differs from the time server by less than the limit and
that the measurement is recent.

**What it reads.** The newest clock sample in `state\clock\<day>.jsonl` (today's file, else
yesterday's; the ClockCheck task, hourly); `[clock] ntp_server` (local time source, default empty),
`[clock] fallback_server` (fallback, default `time.ume.tubitak.gov.tr`), `[clock] alert_offset_ms`
(the limit, default 1000 ms); active `R20-clock` alerts (every key starting with `R20-clock`).

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | A sample within the last 3 hours whose absolute offset is equal to or larger than the limit | "Clock offset {offset} ms; limit {limit} ms." |
| 2 | Problem | The sample of the last 3 hours got no answer, or an `R20-clock` alert is active | "The time server could not be reached or a clock alert is open ({server})." |
| 3 | Attention | No sample at all, or the newest is more than 3 hours old | "The last clock measurement was at {at}, more than 3 hours ago (ClockCheck runs hourly)." — none: "No clock measurement yet." |
| 4 | Attention | `[clock] ntp_server` is empty | "No local time source is set; the clock follows only the fallback server ({fallback})." |
| 5 | OK | — | "Clock offset {offset} ms ({server}, {at}); limit {limit} ms." |

The card counts an offset **equal** to the limit as a problem ("less than 1 second"); the Panel's
clock box still accepts an equal value. Facts: last measurement, server, offset, limit, local time
source ("set" or "none").

**What to do.**

- Offset or unreachable server (rows 1-2): check Windows Time and the time server
  ([INSTALL.md](INSTALL.md) section 1); the Sistem → Clock block shows the last samples and the
  servers ApplySystem set in Windows Time. The next sample within the limit resolves the alert. The
  `R20-clock:<key>` alerts raised for a clock jump seen by sealing or for a spool file that will not
  be sealed never resolve by themselves: look into the file named in the alert without deleting it,
  add the cause as a note and close the alert ([ALERTS.md](ALERTS.md) section 2). Until then the card
  shows **Problem**.
- Sample old (row 3): look at the ClockCheck task's last evidence in Sistem → Scheduled tasks.
- No local time source (row 4): enter the local time source (usually the core router) in Settings →
  General; it is tried before it is saved ([SETTINGS.md](SETTINGS.md) section 1).

### 2.7 Card 7 — Content filter records

**What it checks.** That FortiGate's web filter and DNS filter records keep arriving on the days on
which records are expected. It applies only when an enabled, configured (not an unknown sender)
source of type `fortigate` exists; content filter records of other vendors are not its subject.

**What it reads.**

- The hourly counts of the `search_counts` table (`fg_web`, `fg_dns`; the FortiGate sources, the
  last 8 days): today's and the last 7 days' number of web and DNS filter records;
- the newest record time in the `fg_web` and `fg_dns` tables (last 8 days);
- the names of the web filter profiles: from the newest 10,000 `fg_web` rows of the last 24 hours;
- the settings (Settings → Application settings → Alerts and e-mail):

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `alerts.filter_days` | `[1,2,3,4,5,6,7]` (every day of the week) | 1–7 (1 = Monday, 7 = Sunday); may be empty | The ISO weekdays on which content filter records are expected |
| `alerts.filter_skip_holidays` | `true` (on) | — | On: no records are expected on the days listed in `usage.holidays`; off: public holidays count as expected days |
| `alerts.filter_quiet_hours` | `24` | 6–168 | The longest time without a record on expected days (hours) |

**Expected day:** a day whose weekday is in `alerts.filter_days` and that is not (while
`alerts.filter_skip_holidays` is on) in the `usage.holidays` list. By default content filter records
are expected on **every day of the week**, except the days listed in `usage.holidays` (owner
decision of 2026-10-28); both are configurable. `usage.holidays` (Settings → Application settings →
Usage analytics → Holidays) ships only the fixed-date public holidays ("MM-DD" every year,
"YYYY-MM-DD" once); the administrator enters the dates of the religious holidays every year.

**Window:** the last `alerts.filter_quiet_hours` hours. Records are missing when every day the window
touches is an expected day **and** neither a web nor a DNS filter record arrived in it; either kind
is enough.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | No data | No enabled, configured FortiGate source | "No FortiGate source is defined; no content filter records are expected." |
| 2 | Problem | No record in the window, and every day the window touches is an expected day | "No web or DNS filter record in the last {hours} hours of expected days (last record: {last})." |
| 3 | Attention | No record in the window, but the window touches a day on which none is expected | "No content filter record in the last {hours} hours; this period includes a day on which none is expected (last record: {last})." |
| 4 | Attention | One kind (web or DNS) arrived in the last 7 days but not in the window; the other still arrives | "No {kind} filter record for {hours} hours; the other kind still arrives." |
| 5 | OK | — | "Last record {last}; today {web} web and {dns} DNS filter records." |

`{last}` is "—" when there is no record in 8 days. Example: with the defaults, 1 January (`01-01`)
is not an expected day; when a 24-hour window that touches 1 January has no record, the card shows
**Attention**, not **Problem**. Facts: last record, today's numbers of web and DNS filter records,
the last 7 days ("13.10: 1234 · 12.10: …"), web filter profiles (at most 10 names), DNS filter
profile ("No profile name is read from FortiGate DNS filter records (verify on site)."), expected
days (default "Mon, Tue, Wed, Thu, Fri, Sat, Sun"), public holidays ("not expected" or "count as
expected days").

**Alert:** the same condition (row 2) opens the `R20-filter` alert (severity high, one key
`R20-filter:none`); it resolves by itself when a new content filter record arrives
([ALERTS.md](ALERTS.md) section 2).

**Verify on site** (the card's note): "Verify on site that the firewall policies log web and DNS
filter events and send them to ARCILOGGER."

**What to do.**

1. See the last record and the expected days in the card's facts; search the web and DNS filter
   records on the Arama (Search) page.
2. Check on the firewall that the policies using web and DNS filtering log, and that the records are
   sent to ARCILOGGER ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2; verify on site). Card 1 shows
   whether the source itself is silent.
3. If the organisation does not work on these days (for example, closed at weekends with no traffic
   at all), correct the expected days and the holiday setting in Settings → Application settings →
   Alerts and e-mail; enter the religious holidays in `usage.holidays`.
4. The alert closes by itself and the card recovers when a new content filter record arrives.

### 2.8 Card 8 — DHCP and ARP records (access records)

**What it checks.** That the lease evidence that ties IP addresses to devices (DHCP and ARP records)
arrived in the last 24 hours and that no lease evidence problem is open. The card follows the
configured lease evidence, not "DHCP and ARP" word for word: a site that uses FortiGate only has no
ARP log.

**What it reads.** The `search_counts` rows of the last 24 hours for `mt_dhcp` (MikroTik DHCP),
`mt_arp` (MikroTik ARP) and `fg_dhcp` (FortiGate DHCP), from the watched sources only; active
`R20-lease:` alerts (the lease evidence checks: one alert per cause).

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | No data | No watched source | "No watched source; no access records are expected." |
| 2 | Problem | An active `R20-lease` alert of severity high or critical | "{n} lease evidence problem(s) open." |
| 3 | Attention | An active `R20-lease` alert of another severity | "{n} lease evidence problem(s) open." |
| 4 | Attention | No row of the three tables in the last 24 hours | "No DHCP or ARP record arrived in the last 24 hours." |
| 5 | OK | — | "{tables} records arrived in the last 24 hours; no lease evidence alert is open." |

Facts: per table "{n} rows (24 h)"; open lease evidence problems: the alerts' titles (causes, counts
and configured networks; never the dedup keys), at most 5.

**What to do.**

- Lease evidence problem (rows 2-3): open the alert on the Güvenlik page; the cause and what to do
  are in its details (Kaynaklar → Lease evidence; [ALERTS.md](ALERTS.md) section 2, `R20-lease`).
- No records (row 4): check that the DHCP server (the core router or FortiGate) is defined as a
  source and is not silent (card 1) and that the device sends its DHCP and ARP records
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 3: MikroTik DHCP and the hourly `ARP-LOG` lines;
  section 4: source and test; verify the commands on site).

### 2.9 Card 9 — Second copy (replica)

**What it checks.** That a second copy of the sealed days was made on the replica target and checked.

**What it reads.** `[replica] enabled` (default `false`); for the sealed days of the 7 days ending
yesterday, the replica state files `state\replica\<day>.json` (the copy's status and the time it was
checked); `alerts.replica_stale_hours` (default 48, as a fact); active `R20-replica` alerts (every
key starting with `R20-replica`). The replica target's path is never shown.

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | The replica is off | "The replica is off: no second copy of the sealed days is made." |
| 2 | Problem | An `R20-replica` alert is active | "A replica alert is open: a copy is late or copying failed." |
| 3 | Attention | A sealed day of the 7 has no checked copy | "{n} sealed day(s) not copied yet (the oldest {day})." |
| 4 | No data | No sealed day among the 7 | "No sealed day in the last 7 days." |
| 5 | OK | — | "All {n} sealed days of the last 7 were copied to the replica target and checked." |

The replica is off by default, so a new installation shows **Problem** on this card. The Replica task
runs hourly (from 00:40): yesterday's copy may wait for a short while in the morning (row 3). Facts:
replica ("on" or "off"), last copy, days waiting, delay threshold (hours).

**Verify on site** (the card's note): "Whether the replica target is write-once (WORM) cannot be
checked from ARCILOGGER; verify it on site."

**What to do.**

- Replica off (row 1): in Settings → Replica enter the target (`\\SERVER\share`), the user and the
  password and switch it on; the connection is tried when you save ([INSTALL.md](INSTALL.md) section
  10, "Replica").
- Alert or day waiting (rows 2-3): check access to the target (Settings → Replica → **Bağlantıyı
  test et** (Test the connection), or on the server):

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" replica --test
  ```

  A successful copy closes the alert by itself. "Replica differs" (critical) never closes by itself:
  look into the difference, add the result as a note and close it ([ALERTS.md](ALERTS.md) section 2).

### 2.10 Card 10 — Configuration and database backups

**What it checks.** That the nightly database backup is on and recent, and that the offline copy of
the configuration files was confirmed for the configuration as it is now.

**What it reads.** `state\backup\last.json` (the Backup task daily at 01:40: result, finish time,
size); `[backup] enabled` (default `true`); `alerts.backup_stale_hours` (36) and
`alerts.backup_missing_hours` (48); the age of the installation (the first worker job row of the
database, `job_runs`); the newest `compliance.config_backup_confirm` entry of the audit log with its
SHA-256 against the current SHA-256 of `arcilogger.toml`; active `R20-backup` alerts (every key
starting with `R20-backup`, including `:missing`, `:prune`, `:clock`).

| Row | State | Condition | Reason |
|---|---|---|---|
| 1 | Problem | `[backup] enabled = false` | "The nightly database backup is off." |
| 2 | Problem | An `R20-backup` alert is active, or `last.json` says `"ok": false` or cannot be read | "The last database backup failed or a backup alert is open." |
| 3 | No data | No `last.json` and the installation is younger than `alerts.backup_missing_hours` | "The first database backup has not run yet (the Backup task runs at 01:40)." |
| 4 | Attention | The last successful backup is older than `alerts.backup_stale_hours` (or there is none) | "The last successful database backup was at {at}, more than {hours} hours ago." |
| 5 | Attention | The configuration copy was never confirmed | "The offline copy of the configuration files was never confirmed (BACKUP.md section 3)." |
| 6 | Attention | The last confirmation was for another SHA-256 of the configuration | "The configuration changed after the last confirmation ({at}); make a new offline copy." |
| 7 | OK | — | "Last database backup {at} ({size}); configuration copy confirmed on {confirmed}." |

An unreadable `last.json` (damaged, or a file the API may not read) counts as a failed backup
(row 2), as the worker's backup check reads it. The card does not show who confirmed (the audit log
does). Facts: last database backup, size, nightly backup ("on" or "off"), configuration copy
confirmed (time or "never").

**The "Yapılandırma yedeğini aldım" (I have copied the configuration) button.** On card 10, shown to
administrators only (`settings.manage`). The confirmation dialog says:

> You confirm that arcilogger.toml, secret.key and the Caddy CA folder (with its private key) were
> copied to encrypted offline media kept apart from the backups (secrets.json optional; BACKUP.md
> section 3). The confirmation is written to the audit log with the configuration's current SHA-256.

On confirmation the server reads the current SHA-256 of `arcilogger.toml` and writes the audit entry
`compliance.config_backup_confirm` with it ("Configuration backup confirmed"); the card changes at
once. The confirmation may be repeated (each one is a new entry). If the file cannot be read, nothing
is recorded: "The configuration file could not be read; the backup confirmation was not recorded."
(409 `config_backup_unreadable`).

The card sees only changes of `arcilogger.toml`: every change made in Settings that is written to
this file (for example General, Sources, Kamu SM, Replica, Backup) turns the card to **Attention**;
the application settings in the database do not. The card does not see a renewed `secret.key` or
Caddy CA: make a new offline copy and confirm again then as well. The button only records what you
did; ARCILOGGER does not make or check the copy.

**What to do.**

- Backup off (row 1): switch it on in Settings → Backup; if another product backs up the database,
  this card keeps showing **Problem** ([BACKUP.md](BACKUP.md) section 2).
- Failed backup or alert (row 2): look at `D:\ARCILOGGER\state\backup\last.json`,
  `D:\ARCILOGGER\logs\backup.log` and the `R20-backup` alert on the Güvenlik page
  ([BACKUP.md](BACKUP.md) sections 2 and 7).
- Old backup (row 4): look at the Backup task's last evidence in Sistem → Scheduled tasks.
- Not confirmed or configuration changed (rows 5-6): as in [BACKUP.md](BACKUP.md) section 3, copy
  `arcilogger.toml`, `secret.key` and the Caddy CA folder (with its private key) to encrypted offline
  media kept apart from the copies of the backups (`secrets.json` optional), then press
  **Yapılandırma yedeğini aldım**.

## 3. The Uyum Durumu Raporu (PDF)

The **Uyum Durumu Raporu (PDF)** button on the Uyum Durumu page downloads the cards as they are now
as an A4 PDF. Administrators and auditors may download it (`compliance.read`).

- **On request, not stored:** every download computes the cards afresh (the page's 30-second result
  is not used). The report is not stored on the server, not produced on a schedule and not sent by
  e-mail; it is not a report kind of the Raporlar (Reports) page.
- **No time stamp:** the report is not time-stamped by Kamu SM (it uses no credit). Its proof is the
  SHA-256 in the audit log.
- **Audit log:** before the report is sent, the entry `compliance.report_export` ("Compliance status
  report exported") is written with the file's SHA-256 and the number of cards in each state (OK,
  attention, problem, no data). The response header `X-Arci-Sha256` carries the same value. The file
  name is `ARCILOGGER-uyum-durumu-YYYYMMDD-HHMM.pdf` (Türkiye time).
- **Content, in this order:** the title ("Uyum Durumu Raporu / Compliance status report"); the
  organisation's name ("Kurum / Site: …", "—" when not set); "Oluşturulma / Generated: {time}
  (Europe/Istanbul)"; "Oluşturan / Exported by: {display name} ({username})": the ARCILOGGER account
  that downloaded it; the summary line ("{ok} of {total} checks OK" with the Turkish beside it); the
  notice of section 1 (Turkish and English); then for each card: "{number}. {Turkish title} /
  {English title}", the state as text in brackets (**[TAMAM] / [OK]**, **[DİKKAT] / [ATTENTION]**,
  **[SORUN] / [PROBLEM]**, **[BİLGİ YOK] / [NO DATA]**), the Turkish and English reason, a two-column
  table of facts (Turkish label and value), the verify-on-site note if any, the active alerts ("rule,
  severity, opened") and the time of the check.
- **On every page:** the title and the organisation's name at the top; at the bottom the ARCILOGGER
  version and the time of generation, "Sayfa n / N" and the note of the 5651 report: "Designed to
  support 5651 obligations; this report is not a statement of legal compliance."
- **Personal data:** like the cards, the report carries no data about log subjects. The one
  exception is the display name and the username of the account that downloaded it.

The report is a listing of technical checks; it is not a legal assessment. Before you present it in
an audit, review its texts with your legal counsel.

## 4. The Sistem page

**Sistem** (`/system`) shows the server's resources, services, scheduled tasks and clock; it is open
to administrators only (`system.read`) and only reads. Viewing it is not written to the audit log.
The page refreshes in the background every 30 seconds. Service and task states are not asked of the
Windows Service Control Manager or the Task Scheduler: they are derived from the status files,
evidence files and the database ARCILOGGER can already read. The blocks are in this order:

### 4.1 Resource use

Processor (%), memory (used and total) and uptime, read through the Windows API (no extra program).
Processor use is the difference between two readings: the first request shows "ölçülüyor"
(measuring), later requests the use since the previous reading. A value that cannot be read shows
"bu platformda okunamıyor" (cannot be read on this platform).

### 4.2 Drives and the disk forecast

The data drive and (when it is another drive) the system drive, by their drive letters: total, used
and free space, the used percentage as text and a meter marked at 80 % (warning,
`alerts.disk_used_percent`) and 95 % (critical); the same drives and thresholds as the `R20-disk`
alert.

**Disk history:** every day after 00:30 the worker job `system.sample` writes each drive's total and
used bytes to `D:\ARCILOGGER\state\system-history.json` (one entry per day, the same day's entry is
replaced, the newest 120 days are kept). The file holds nothing but drive labels and byte counts. A
damaged file is started again from the new sample; the forecast then waits for seven new samples.

**Forecast:** per drive, a straight line is fitted to the newest 30 daily samples (least squares).

| State | When | Text shown |
|---|---|---|
| Insufficient | Fewer than 7 samples | "A forecast needs at least 7 days of samples ({samples}/7)." |
| Not growing | Slope zero or negative | "Disk use did not grow in the last 30 days." |
| Estimated | Slope positive | "At this rate the {warning}% mark is reached on {warning_on}, full on {full_on} (~{days} days)." |

- When the drive is **already** above the warning mark, no warning date is given; the measurement
  of that moment (else the newest sample) decides this, not the line. When the line has passed the
  mark in the past while no measurement has, the warning date is "today".
- A date more than 100 years (36,500 days) away is not given: a drive that grows by a few bytes a
  day stays "estimated", but without a date.
- A straight line is a simple model on purpose: weeks ahead it is a rough guide.

When the drive is full, raw logs cannot be written (`R20-spool`): **never delete** raw logs or the
vault to make room; enlarge the drive or move other files ([ALERTS.md](ALERTS.md) section 2,
`R20-disk`).

### 4.3 Database

The total size of the database and the 10 largest tables (with their partitions; with TimescaleDB
the size of the hypertables). The values are kept for 5 minutes and shown with the time they were
measured; if they cannot be measured, the block stays empty.

### 4.4 Collector and ingest

Two blocks, from the status files of the collector and of ingest (`state\collector-status.json`,
`state\ingest-status.json`): "running" when the file is younger than 2 minutes, "stale" when older,
"missing" when there is none.

- **Collector:** for every configured source the number of messages and bytes and the time last
  received; senders not in the configuration are only counted (senders, messages, bytes), their
  addresses are not shown (they are under the **Bilinmeyen** (Unknown) filter of the Kaynaklar page);
  messages dropped for an unknown sender or for the transport, write failures and when the error
  began, closed torn lines, TCP connections and framing errors.
- **Ingest:** the service state, the database connection, the configured and active parse worker
  processes, lines per second; per source the lag (bytes and seconds), lines per second, the number of unfinished days
  and whether there is an error (its text is not shown); sources not in the configuration are only
  counted.

The members of a network source are summed under the source's name. Only configured sources that
have an entry in the status file are listed: a source that has not sent yet has no row (the Kaynaklar
page lists every configured source).

### 4.5 Services

- **ArciCollector, ArciIngest, ArciWorker:** "running" when their status file is younger than
  2 minutes, "stale" when older, "missing" when there is none. **ArciApi** is always "running" (it
  answers the page).
- **Administration service (ArciAdmin):** by its heartbeat "ok", "stale", "missing", or "disabled"
  when the browser administration is switched off.
- **ApplySystem:** its last result (`admin\system\status.json`): "ok", "failed" (with its code),
  "never" or "disabled" when the browser administration is switched off.

### 4.6 Scheduled tasks

One row per task of `deploy\windows\tasks\`. The state is derived from the task's **evidence file**
(paths relative to the data folder): "ok"; "late" (the evidence is older than the limit); "missing"
(no evidence yet); "not used" (the task does nothing in this configuration); "not observable" (no
evidence ArciApi can read). The limits are the task's cadence plus a margin; they are not settings.

| Task | Schedule | Evidence | Late after | Not used when |
|---|---|---|---|---|
| Seal | Every 5 minutes from 00:10 | modification time of the newest `state\seal\<YYYY-MM-DD>.json` whose `status` is not `deleted` (states Retention rewrote for the days it deleted do not count) | 26 h | — |
| VerifyDaily | Daily at 01:00 | `at` of the newer of `state\verify\last-last-1.json` and `last-last-2.json` | 26 h | — |
| VerifyWeekly | Sundays at 03:00 | newest `at` of `state\verify\last-last-<N>.json`, N ≥ 3 | 8 days | — |
| VerifyMonthly | 1st of the month at 04:00 | `at` of `state\verify\last-full.json` | 32 days | — |
| Replica | Hourly from 00:40 | newest `verified_at` of `state\replica\<day>.json` (last 3 days) | 26 h | `[replica] enabled = false` |
| Retention | Daily at 02:00 | no readable evidence → "not observable" | — | `[retention] enabled = false` |
| Credit | Daily at 06:00 | newest `time` of `state\credit.jsonl` | 26 h | `[tsa] provider` is not `zamane` |
| ClockCheck | Hourly from 00:05 | newest `time` of `state\clock\<day>.jsonl` (today, yesterday) | 2 h | — |
| Backup | Daily at 01:40 | `finished` of `state\backup\last.json` | 26 h | `[backup] enabled = false` |
| Requests | On event and every 5 minutes | ArciApi cannot read `state\requests` → "not observable" | — | — |
| ApplySystem | On event and every 5 minutes | `at` of `admin\system\status.json` | 15 min | browser administration off |

The file names of the verification tasks come from the command itself: `verify --last 2` writes
`last-last-2.json`, `verify --last 90` writes `last-last-90.json`; on a chain with fewer sealed days
than requested the name carries the number of days verified (`last-last-<days>.json`, e.g.
`last-last-30.json`); only `verify --all` writes `last-full.json`. To see the last result of the tasks in the Task Scheduler on the server:

```powershell
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
```

### 4.7 Worker jobs

For each job of the worker: its schedule ("Every {n} seconds/minutes/hours" or "Daily after
{HH:MM}"), its state ("never", "running" (a run in progress in `job_runs`), "failing" (at least one
failure in a row, or a failed last run, also one the worker could not count because the database
was unreachable), "skipped", "ok"), the last status, the last start and finish, the duration (from the newest finished run of the
last 7 days), the number of failures in a row and the next run. Sources: `state\worker-status.json`
and the `job_runs` table of the database. The error text is not shown: see
`D:\ARCILOGGER\logs\worker.log`; 3 failures in a row open the `R20-job` alert.

### 4.8 Clock

The local time source (`[clock] ntp_server`), the fallback server (`[clock] fallback_server`), the
alert limit (`[clock] alert_offset_ms`), the servers ApplySystem set in Windows Time and when (empty
without ApplySystem), and the clock samples of the last 24 hours, newest first: time, server, whether
it answered, offset and delay (ms), stratum. Card 6 judges the newest of these samples.

### 4.9 Version and upgrade

Product name, API version, core version, release date and the licence's maintenance period (as in
Settings → Licence). The page also says: "To upgrade, copy the new offline bundle to the server and
follow INSTALL.md section 13. ARCILOGGER downloads no updates." ([INSTALL.md](INSTALL.md)
section 13.)

### 4.10 The Disk card of the Panel

On the Panel, for administrators only (`system.read`), a **Disk** card shows each drive's used
percentage with an icon and a text and the forecast in one line; the link **Sistem sayfasını aç**
(Open the System page) leads here.

## 5. The first-run wizard

The **first-run wizard** (`/settings/setup`) is a checklist of what is to be done after the
installation, in order; it is open to administrators only (`settings.manage`). It has no forms of its
own: each step opens the matching **Ayarlar** (Settings) page, where changes are saved and checked as
usual ([SETTINGS.md](SETTINGS.md)). The wizard stores nothing: each step's state is derived from the
server's configuration and the request history.

**Where it opens.** From the **İlk kurulum sihirbazı** (First-run wizard) card on the Settings
overview and from the link **İlk kurulum sihirbazını aç** on the Uyum Durumu page (while a card is not
**OK**). It does not open by itself after the first sign-in. The account page (`/setup`) shows it as
its third step, "Kurulum sihirbazı — Girişten sonra, Ayarlar'da" (First-run wizard — After sign-in,
in Settings).

| Step | Done when | Opens |
|---|---|---|
| Administrator and two-step verification | always (the user is signed in) | — |
| Site settings | no configuration problem of level error about `site_name` or `clock.*` | Settings → General |
| Log sources | at least one enabled, configured source | Settings → Sources |
| Kamu SM: test server | a successful "Damga testi" (stamp test) in the request history, or the production server is selected | Settings → Kamu SM (test step) |
| Kamu SM: production | the production server is selected and sealing is ready | Settings → Kamu SM (production step) |
| Replica | the replica is on | Settings → Replica |
| E-mail | SMTP server and sender are set | Settings → E-mail |
| Summary | — | — |

- Opening the wizard selects the first step that is not done. Each step says what it is for, its
  state ("Tamam" / "Eksik", done / missing) and what is still missing; **Ayarlar'da aç** (Open in
  Settings) opens the matching page, **Bu adımı şimdilik atla** (Skip this step for now) goes on to
  the next step (the step stays "Eksik").
- On a Settings page opened from the wizard, the way-back link reads **Kurulum sihirbazına dön**
  (Back to the first-run wizard). This is remembered in that browser tab only; the wizard page or
  the Settings overview ends it.
- The **Log sources** step shows a note chosen by each enabled source's firewall state:
  ARCILOGGER's firewall rule opens port 514 to the addresses of the enabled sources by itself; when
  the firewall is not managed by ARCILOGGER, open the port by hand ([INSTALL.md](INSTALL.md)
  section 12). For the device settings it says "DEVICE-SETUP.md sections 1 and 4 (verify the commands
  on site)" ([DEVICE-SETUP.md](DEVICE-SETUP.md)); the web interface has no device commands and no
  pre-filled device list. For each configured source it shows whether data arrived.
- The **Kamu SM** steps remind you: "Stamps of the test server have no legal validity (Kamu SM);
  before switching to production compare the root fingerprints with the values Kamu SM publishes
  (verify on site)." ([kamusm.md](kamusm.md).)
- **Summary:** every step with its state, all configuration problems (every level, in Turkish and
  English), "Checks after installation: INSTALL.md section 12" and a link to the Uyum Durumu page.

## 6. To verify on site

What ARCILOGGER cannot check by itself and what has to be verified on site once at installation (and
again when devices or the server change):

| Subject | Why on site | Where |
|---|---|---|
| DNS filter profile | No profile name is read from FortiGate DNS filter records; the table of DNS filter records has no profile column. Card 7 lists web filter profiles only. Verify on the device which DNS filter profile is used in which policy. | Card 7; [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2 |
| Firewall filter logging | Card 7 sees only the records that arrive. Verify on the device that every policy using web and DNS filtering logs these events and sends them to ARCILOGGER; if the organisation produces no traffic at all on some days, set the expected days accordingly (section 2.7). | Card 7; [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2 (verify the commands on site) |
| Credit format | The credit is read from the Zamane client's output with `[tsa] credit_regex`; the output format is not confirmed with Kamu SM (K4). Compare the value read with **Kontör sorgula** on the test server with the one Kamu SM shows. | Card 5; [kamusm.md](kamusm.md) sections 3 and 4 |
| WORM (replica target) | ARCILOGGER makes and checks the copy, but cannot see whether the target is write-once (WORM). Verify the target device's WORM setting and retention on site. | Card 9; [INSTALL.md](INSTALL.md) section 10, "Replica" |
| Windows rights | The pages read the status files with the read right the installer gives `NT SERVICE\ArciApi`; processor, memory and uptime are read from the Windows API with the same account. The Requests task's evidence cannot be read on purpose ("not observable"). An unreadable `last.json` or `last-full.json` turns card 10 or card 3 to **Problem**. After the first night, check on the Sistem page that the values show (not "bu platformda okunamıyor") and that no task's evidence is "missing"; if the rights are broken, run `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` in an elevated PowerShell. | Section 4; [BACKUP.md](BACKUP.md) section 7 |
| Legal assessment | The choice of the cards, their thresholds and the report's texts are technical assumptions. Confirm with your legal counsel how you meet the 5651 obligations and how you use the report. | Sections 1 and 3 |
