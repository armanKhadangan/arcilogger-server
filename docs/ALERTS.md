# Alerts and alert e-mail

> Türkçe: [UYARILAR.md](UYARILAR.md). Decision: [adr/0010-alerts-and-email.md](adr/0010-alerts-and-email.md).
>
> ARCILOGGER is *designed to support* Law 5651 obligations ("5651 yükümlülüklerini desteklemek
> için tasarlanmıştır"); it makes no claim of legal compliance.

This document lists every alert code with its meaning, threshold and what to do, explains how to
set up the alert e-mail, what an e-mail contains and **never** contains, and the states and
notes of the **Güvenlik** (Security) page (section 8). The rule engine (YAML rules, rule editor)
and webhooks come later (M5).

## 1. How it works

- The database-free evidence tasks (sealing, verification, replica, retention, clock, credit) and
  the collector write their alerts to `D:\ARCILOGGER\state\alerts.jsonl`. Every minute the worker
  copies new lines into the `alerts` table **once** (`alerts.import`): the end of the last
  complete line is stored in the same database transaction; a torn last line waits for its line
  break. After a power cut the next alert starts on a new line: the torn fragment is skipped (and
  reported in the job's details), the alert after it is imported.
- The worker also checks by itself: R15 every minute (`alerts.r15`), the R20 system checks every
  5 minutes (`alerts.system`), and the licence check (`license.check`, every 5 minutes) raises the
  licence alerts (`R20-license…`, section 2).
- **One active alert per problem:** every rule has a dedup key (`R15:fortigate`,
  `R20-seal:2026-10-18`, `R20-disk:D:` …). While an alert is `open` or `acknowledged`, the same
  problem opens no second alert; a repeat is counted. Once the problem is gone, the check
  **resolves** the alert itself (`resolved`, `resolved_at`, `details.auto_resolved`). A check
  never re-opens an alert a person closed; a problem that persists or comes back is a new alert
  (section 8).
- The alerts are on the **Güvenlik** (Security) page of the web interface (section 8); they can
  also be listed on the server:

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-worker.exe" alerts list --open
  ```

- High and critical alerts are e-mailed once `[smtp]` is set up (`alerts.notify`, section 4).

## 2. Alert codes

Every alert belongs to **5651 Temel** (5651 Core) and stays on in every licence state; the one
exception is `R20-report` (the Scheduled usage reports module, [LICENSING.md](LICENSING.md)
section 2.1). The licence alerts (`R20-license`, `R20-license-limit`, `R20-license-maintenance`)
are 5651 Temel too: they run in every licence state and only report on the licence.

| Code | Meaning | Severity | Threshold (setting) | What to do |
|---|---|---|---|---|
| `R15` | **Log source silent:** no message from an enabled, expected, configured source for longer than its heartbeat (compliance-critical). A source that never sent alerts `alerts.r15_never_seen_minutes` after its row was created. No alert within `alerts.r15_startup_grace_minutes` after the worker started, the source's row was created or its first message. Resolved as soon as the source sends again | High | `[[sources]] heartbeat_minutes` (default 15; in the example configuration's commented-out sources firewall 10, core router 15; without any source, as on a new installation, R15 watches nothing); `alerts.r15_never_seen_minutes` (60), `alerts.r15_startup_grace_minutes` (15) | Is the device on and connected, does its syslog point to ARCILOGGER ([DEVICE-SETUP.md](DEVICE-SETUP.md)); is the collector running (`Get-Service ArciCollector`) |
| `R20-seal` | A sealing attempt failed (`alerts.jsonl`), or a closed day is still not sealed the next morning (one alert per day). Resolved once the day is sealed | High (critical after 48 h) | `alerts.seal_overdue_hour` (09:00) | **Mühürleme** page, `D:\ARCILOGGER\logs\core.log`; Kamu SM connection and credit |
| `R20-seal-config` | The Seal task could not **start** sealing: no Kamu SM credentials, no root in `kamusm-roots`, `[tsa] zamane_jar` not set, or the secret store key unreadable. Written **once** per day and cause (the task runs every 5 minutes). Resolved after the next successful seal | High | — | Complete what is missing ([FIRST-STEPS.md](FIRST-STEPS.md) section 3, [kamusm.md](kamusm.md)) |
| `R20-seal-skip` | More than `[seal] max_consecutive_empty_days` consecutive days without any log; the days were not stamped one by one | Critical | `[seal] max_consecutive_empty_days` (7) | Collector, server clock |
| `R20-clock` | Clock offset beyond the limit or time server unreachable (`clock-check`): resolved by the next clock sample within the limit. A clock step or stray spool file seen by sealing (`R20-clock:<key>`, one alert per file or step) is **not** resolved automatically: it stays open until a person closes it | High / critical | `[clock] alert_offset_ms` (1000) | Windows Time service, NTP server (`Set-ArciTime.ps1`); for sealing's alerts the file named in the alert, which must not be deleted ([INSTALL.md](INSTALL.md) section 1) |
| `R20-verify` | The **newest** verification of a sealed day failed (scheduled, or "Doğrula" on the sealing page). Resolved when the day verifies again | Critical | — | Look into it the same day ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md)) |
| `R20-credit` | Kamu SM credit below the threshold (newest `credit` entry), or unreadable. Resolved by a reading at or above the threshold | High (unreadable: medium) | `[tsa] credit_alert_threshold` (500) | Buy credit ([kamusm.md](kamusm.md)) |
| `R20-replica` | A copy failed, or (`R20-replica:stale`) the replica is enabled and a sealed day has had no replica for longer than `alerts.replica_stale_hours`, counted from its seal — or from the worker's start when that is later, so switching the replica on with older sealed days leaves time for the first copies. A failed copy is resolved once a later copy succeeded and every day sealed by then has its replica; the stale alert once no day waits longer than the threshold. **Replica differs** (critical) is never resolved automatically | High / critical | `alerts.replica_stale_hours` (48) | Replica target (`[replica] target`): `& $core replica --test` |
| `R20-retention` | A deletion run was refused or left incomplete; a completed one at info level | Info … critical | `[retention]` | Deletion record, `core.log` |
| `R20-spool` | Raw logs cannot be written to disk; after recovery a second alert gives the number of lost messages | Critical | — | Data disk (free space, hardware) **at once** |
| `R20-spool-torn` | An unterminated last line was closed (no byte changed) | Medium | — | Find the cause of the power cut / crash |
| `R20-disk` | Data drive or system drive fuller than the threshold (one alert per drive); critical from 95 %. Resolved 2 points below the threshold | High / critical | `alerts.disk_used_percent` (80) | Free space or enlarge the disk; **never** delete raw logs or the vault |
| `R20-parse` | More than the threshold of a source's messages of the last hour could not be parsed (per source) | Medium | `alerts.parse_failure_percent` (1), `alerts.parse_min_messages` (1000) | The device firmware or log format may have changed ([PARSERS.md](PARSERS.md)) |
| `R20-job` | The last N runs of a worker job failed (per job). Resolved by the next successful run. Skipped runs of a module that is off in the licence (`skipped`) do not count; when the licence component cannot be loaded the job `license.check` fails and, after N failures, raises this alert ([LICENSING.md](LICENSING.md)) | High | `alerts.job_failures` (3) | `D:\ARCILOGGER\logs\worker.log`, `state\worker-status.json` |
| `R20-backup` | The last database backup failed, or the last successful one is older than `alerts.backup_stale_hours` (`state\backup\last.json`), or a dump was made but its state file could not be written; resolved by a backup that succeeds after the alert. `R20-backup:missing`: still no backup `alerts.backup_missing_hours` after the installation (medium). `R20-backup:prune`: old dumps could not be deleted (medium); resolved an hour after a later successful backup that did not report it again. `R20-backup:clock`: a new dump named earlier than one already in the folder (a clock set back; the dump is kept, medium); resolved by a later successful backup. With `[backup] enabled = false` (read when ArciWorker starts) no backup alert opens and the open ones are resolved | High / medium | `alerts.backup_stale_hours` (36), `alerts.backup_missing_hours` (48) | Backup task and backup folder ([FIRST-STEPS.md](FIRST-STEPS.md) section 10) |
| `R20-email` | An alert e-mail could not be sent (after 6 attempts, or a permanent 5xx refusal). **One** local alert, never e-mailed itself; resolved once an e-mail reached each recipient it names, or the failing addresses were taken out of `alerts.email_recipients` | High | — | `[smtp]`, `smtp.password`, firewall; `alerts test-email` |
| `R20-report` | A scheduled usage report could not be made, or not sent to a recipient (after 6 attempts, or a permanent 5xx refusal; [USAGE-REPORTS.md](USAGE-REPORTS.md)). One local alert; resolved once a later report reaches the recipients it names (or they are removed from every schedule) and a later report is made. It belongs to the **Scheduled usage reports** module: not raised while the module is off in the licence; when the module goes off an open alert is resolved by itself with the note "Zamanlanmış raporlar lisansta kapalı / Scheduled reports are off in the licence" | Medium | — | The run's error on the Reports page; `[smtp]`, `alerts test-email` |
| `R20-license` | **Paid modules ending or off** (the licence check, every 5 minutes, on the licence's trusted time: [LICENSING.md](LICENSING.md) section 10): `R20-license:trial-ending` the trial ends within 3 days; `R20-license:expiring` a time-limited licence ends within 14 days; `R20-license:off` the paid modules are off because of the licence state (`trial_expired`, `invalid`, `wrong_server`, `expired`, `version_not_covered`) — only while a module the trial or the licence gave is off (a licence of 5651 Temel only raises nothing). The alert names the modules; the 5651 functions keep working in every state. Resolved by itself once the licence is valid (`licensed`) or the situation changed (`license_changed`: e.g. the trial's warning when the trial is over, which opens `R20-license:off`) | High: with the default `alerts.email_min_severity` it is e-mailed, before and when the paid modules (the scheduled usage reports among them) stop, once per alert | 3 days (trial), 14 days (`valid_until`) | Settings → Licence: send the request code, upload the new `.lic` ([LICENSING.md](LICENSING.md) section 4); after a server change, a new licence (section 9) |
| `R20-license-limit` | Users (`R20-license-limit:users`: no new user can be added) or log sources (`R20-license-limit:log_sources`: logs are still collected and sealed) above the licence's limit, from the crossing's licence event (`license.limit_exceeded`) on. Resolved once the use is within the limit (`limit_ok`) | Medium | the licence's `limits` | Deactivate unused users or ask for a licence with a higher limit; a source stops counting 30 days after its last log ([LICENSING.md](LICENSING.md) section 6) |
| `R20-license-maintenance` | A valid licence whose maintenance ends within 30 days, or has ended (information: upgrades and support only; the licence and the installed version stay valid). Resolved by renewed maintenance (`maintenance_renewed`) or once the licence is no longer valid (`license_changed`) | Info | 30 days | Renew maintenance ([LICENSING.md](LICENSING.md) section 7) |
| `new-log-source` | A sender that is not in the configuration (collector) | Medium | — | If it is a known device, add it to `[[sources]]` ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 4) |
| `new_source` | The database alert of the same event (ingest) | Medium | — | As above |
| `wrong-transport` | A message arrived over a transport its source does not allow; it was not recorded | High | `[[sources]] transport` | Device setting or a forged message ([DEVICE-SETUP.md](DEVICE-SETUP.md) 1.1) |
| `ip_conflict` | The same IP held by two MAC addresses at the same time ([CORRELATION.md](CORRELATION.md)) | Medium | `devices.infrastructure_macs` | Look into both devices |

## 3. Database settings

How to change them: [FIRST-STEPS.md](FIRST-STEPS.md) section 6 (`& $db settings set …`; list
values through a pipe). Settings are read when used; no restart is needed.

| Setting | Default | Range | Meaning |
|---|---|---|---|
| `alerts.email_recipients` | `[]` | at most 20 addresses | Who gets alert e-mails (empty: no e-mail) |
| `alerts.email_min_severity` | `"high"` | info … critical | Alerts of this severity and above are e-mailed |
| `alerts.email_max_per_hour` | `20` | 1–1000 | E-mails per recipient per hour; beyond it a summary |
| `alerts.email_digest_seconds` | `300` | 0–3600 | Alerts within this window go into one e-mail |
| `alerts.email_resolved` | `true` | — | Also e-mail when an alert is resolved |
| `alerts.web_base_url` | `""` | `https://…` | The link in the e-mails; empty: `https://<[collector] bind>` (when that is the server's own address), else `https://arcilogger.internal` (the installer's default server name) |
| `alerts.r15_startup_grace_minutes` | `15` | 0–1440 | R15 waits this long after the worker or a source started |
| `alerts.r15_never_seen_minutes` | `60` | 1–10080 | R15 delay for a source that never sent |
| `alerts.disk_used_percent` | `80` | 50–99 | R20-disk threshold |
| `alerts.seal_overdue_hour` | `9` | 1–23 | R20-seal when a closed day is not sealed by this hour of the next day |
| `alerts.replica_stale_hours` | `48` | 1–720 | R20-replica threshold |
| `alerts.parse_failure_percent` | `1` | 1–100 | R20-parse threshold (percent, above) |
| `alerts.parse_min_messages` | `1000` | ≥ 1 | R20-parse: least messages per hour to judge |
| `alerts.job_failures` | `3` | 1–100 | R20-job: failed runs in a row |
| `alerts.backup_stale_hours` | `36` | 1–720 | R20-backup: oldest acceptable last successful backup |
| `alerts.backup_missing_hours` | `48` | 1–720 | R20-backup:missing: time after the installation until the first backup |

## 4. Setting up the e-mail

1. **Connection** in `C:\ProgramData\ARCILOGGER\arcilogger.toml` (nothing is sent while
   `[smtp] enabled` is `false`):

   ```toml
   [smtp]
   enabled = true
   host = "<SMTP server>"
   port = 587                    # 587 + starttls (recommended) or 465 + ssl
   security = "starttls"         # "starttls" | "ssl" | "none" (only a relay on this server)
   username = "<user>"           # remove the line when the server needs no authentication
   from_address = "<sender address>"
   timeout_seconds = 30
   # ca_file = 'C:\ProgramData\ARCILOGGER\smtp-ca.pem'   # a company CA; default: system store
   ```

   With `starttls` and `ssl` the server certificate is **always** verified; there is no switch to
   turn that off. After changing `[smtp]`, `Restart-Service ArciWorker` (the worker reads the
   configuration only when it starts).
2. **Password** only in the encrypted secret store (asked on the console, never on the command
   line):

   ```powershell
   & $core secret set smtp.password
   ```

3. **Recipients** (a list value goes through a pipe):

   ```powershell
   '["<e-mail address>"]' | & $db settings set alerts.email_recipients - --by <your user name>
   ```

4. **Test** (also works with `[smtp] enabled = false`; the password is never printed):

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-worker.exe" alerts test-email --to <e-mail address>
   ```

   It prints "Gönderildi / sent" or the SMTP error (e.g. `recipient refused: 550 …`, `timeout`).
5. **Firewall, strict outbound mode:** with `-StrictOutbound`, `Set-ArciFirewall.ps1` adds the
   outbound rule to the `[smtp]` server itself (when `enabled = true` and `host` is set; the
   section's `port`, default 587) on every run, installations and upgrades included. It resolves
   a host name **when it runs**: run the script again when the provider's addresses change:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
   ```

   Another mail server can be allowed with `-SmtpServer <server:port>`, which is remembered
   ([INSTALL.md](INSTALL.md) section 12). To forget it, pass it empty with `-Command` (Windows
   PowerShell 5.1 drops an empty argument after `-File`):

   ```powershell
   powershell -ExecutionPolicy Bypass -Command "& 'C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1' -SmtpServer ''"
   ```

   Without a rule, e-mail attempts fail and `R20-email` opens.

## 5. What an e-mail contains, and never contains

**No log content leaves the server** (specification 0): an e-mail is made only of fixed Turkish
and English texts and a **few shape-checked facts** from the alert's details:

- rule code and name, severity, time (Europe/Istanbul), source name (the configured name, or a name
  with an IP address in it as below), one short sentence (day, threshold, count, drive letter, job
  name, licence state and module names, or one of the IP addresses below), what to do (one line),
  the ARCILOGGER web address (the only link);
- an **IP address** only in these cases: a new sender's IP address (`new-log-source`, `new_source`);
  the sender address of a message refused for its transport (`wrong-transport`: an address of a
  configured source — its address or one inside its network —, though the message may come from a
  sender forging it); the conflicting IP address of an IP conflict (`ip_conflict`); and inside a
  source name: the name of a sender inside a configured network, or of a sender that is not
  configured, holds its IP address (`ap-<address>`, `unknown-<address>`; in the subject line
  too);
- Turkish first, then English; plain text and simple HTML (inline styles, no images or external
  resources).

An e-mail **never** contains a raw log line, a person's name, a device owner's name, an IP-to-person
link, a MAC address, an error text, a file path, the licensee, the server id, a password or any
other secret. An alert's title and details are never copied into an e-mail. The details are in
ARCILOGGER (`alerts list`).

## 6. Delivery

- One e-mail per recipient; one refusing address does not affect the others.
- Alerts within `alerts.email_digest_seconds` go into one e-mail (most severe first, at most 50
  listed, the rest counted).
- Temporary failures (connection, timeout, TLS, authentication, 4xx) are retried after 1, 5, 15,
  60, 180 minutes; after 6 attempts, or on a permanent refusal (5xx), those alerts are not
  e-mailed and **one** `R20-email` alert is opened.
- A crash between sending and recording repeats that e-mail **at most once**.
- Beyond the hourly limit alerts are `suppressed`; the first e-mail the limit allows says how many
  (and of which rules) were not sent.
- Only alerts whose problem was last seen within the last 24 hours are e-mailed — when it began,
  a repeated line in `alerts.jsonl`, or a check that finds it again (recorded at most once an
  hour, `details.seen_at`): the first import of old `alerts.jsonl` lines never mails history,
  and a problem that is still going on is e-mailed even when it began earlier (after a database
  or worker outage, or when the e-mail is set up later).

## 7. To verify on site

- SMTP server, port and security type ([ASSUMPTIONS.md](ASSUMPTIONS.md) A8, E1–E6); `ca_file` if
  the server uses a company CA.
- If the mail provider is outside the company: an e-mail holds source names, days, counts and,
  where needed, one IP address per alert (a new sender, a sender refused for its transport, an IP
  conflict, a source whose name holds its address; section 5) — one e-mail can list several alerts
  (section 6) —, no log content; review it with the lawyer for KVKK.

## 8. The Security page: states, notes, who changes them

Alerts are on the **Güvenlik** (Security) page of the web interface: the list (filters for state,
severity and rule; search in the title, the rule code and the source name), the detail and the
**security summary** (open alerts per severity, the number being investigated, the mean time to
close over the last 30 days). The **Panel** (dashboard) box "Açık güvenlik uyarıları" shows the
five newest active alerts.

| State (code) | Meaning |
|---|---|
| **Yeni** (`open`) | Opened by a check, nobody has taken it yet |
| **İnceleniyor** (`acknowledged`) | Someone is on it; the alert stays active, the same problem opens no second alert, and the check closes it by itself once the problem is gone |
| **Kapandı** (`resolved`) | The problem is fixed — closed by a person or by the check itself ("closed automatically" mark) |
| **Yanlış alarm** (`dismissed`) | Not a real problem |

Allowed changes: Yeni → İnceleniyor, Kapandı or Yanlış alarm; İnceleniyor → any of the other
three; Kapandı ↔ Yanlış alarm; Kapandı or Yanlış alarm → Yeni (**re-open**). A closed alert
cannot go straight to İnceleniyor: re-open it first.

- **Who changes them:** in this release only the **administrator** sees and changes alerts
  (`alerts.read`, `alerts.manage`; [roles.md](roles.md)). The auditor sees the sealing, credit
  and clock boxes of the dashboard, not the alerts.
- **Notes:** a note can be added in any state (1–2000 characters, line breaks allowed), also
  together with a change of state. Notes are never edited or deleted; a correction is a new note.
  A note may hold personal data (KVKK): write only what is needed, factually.
- **Timeline:** every change of state and every note is recorded with who made it and when. The
  checks' automatic closing and re-opening appear as `system` with a reason code (e.g.
  `messages_again`: the source sends again; `condition_cleared`, `below_threshold`,
  `credit_ok`, `job_ok`, `backup_ok`, `verified_again`, `sealed`, `email_sent`,
  `lease_rebuild`, `conflict_found_again`; the licence alerts: `licensed`, `license_changed`,
  `limit_ok`, `maintenance_renewed`).
- **Audit log:** every change of state is written as `alert.state` (previous and new state),
  every note as `alert.note`. The audit log holds **only the length** of a note, never its text;
  notes never go into an e-mail either.
- **Closing while the problem persists:** a dedup key is unique among **active** (Yeni,
  İnceleniyor) alerts only. If you set an alert to Kapandı or Yanlış alarm while its problem is
  still there, the check's next run (R15 every minute, R20 every five minutes) opens a **new
  alert**, and high and critical alerts are e-mailed again. The alert you closed can then not be
  re-opened ("An active alert covers the same problem (#…)"): continue with the new one. So set
  an alert to **İnceleniyor** while you work on the problem; the check closes it by itself once
  the problem is fixed. The licence alerts are the exception (`R20-license`, `R20-license-limit`,
  `R20-license-maintenance`): a licence situation can last for good (a site that runs 5651 Temel
  only), so an alert a person closed is not raised (or e-mailed) again for the same situation,
  only for the next one (another licence state, a new crossing of the limit, other dates); the
  Güvenlik page says so next to the buttons of a licence alert.
- **Alerts that never close by themselves** (`R20-spool`, replica differs, `R20-seal-skip`,
  `R20-spool-torn`, `R20-retention`, `new-log-source`, `new_source`, `wrong-transport`): after
  looking into them, add the cause as a note and set Kapandı or Yanlış alarm. `ip_conflict`
  stays open when the conflict is real; it closes by itself (Yeni or İnceleniyor) only when a
  rebuild of its day no longer finds the conflict after late evidence.
- **Detail:** the alert's details (fields that look like secrets are hidden, e-mail addresses
  shown as `a***@firma.com.tr` — also inside texts such as an SMTP reply, and in the alert's
  title and summary; `R20-email` and `R20-report` name the recipient), what to do (step by step,
  the same advice as section 2) and the e-mail deliveries (masked recipient, status, attempts).
  The full addresses are in the setting `alerts.email_recipients` and among the schedule's
  recipients on the Raporlar page.
- **Mean time to close:** the mean time from opening to the last closing (a change from Yeni or
  İnceleniyor to Kapandı or Yanlış alarm; switching between Kapandı and Yanlış alarm is no
  closing) of the alerts that are closed now and were last closed within the last 30 days
  (closed by a person or by a check). Alerts closed before this release have no timeline and do
  not count.
