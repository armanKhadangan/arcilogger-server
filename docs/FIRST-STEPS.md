# First steps after the installation

> Türkçe (primary): [ILK-ADIMLAR.md](ILK-ADIMLAR.md). Installation: [INSTALL.md](INSTALL.md).
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it does not claim legal compliance. Have the record keeping and the processing
> of personal data reviewed by a lawyer.

For this release (MVP): what to do, in order, from the first administrator to the first 5651
lookup and the daily checks once the installer has finished. Most of the server's settings
(organisation name, time, Kamu SM, log sources, e-mail, replica, retention, backup) can also be
changed on the **Ayarlar** (Settings) pages of the web interface: [SETTINGS.md](SETTINGS.md). The
commands in this document are the console path, valid also while the browser administration is
switched off (`-DisableBrowserAdministration`). Database backup and restore:
[BACKUP.md](BACKUP.md). Day-to-day operations, the meaning of every alert and replacing the
server will be collected in `ISLETIM.md` / `OPERATIONS.md` (M7).

## 0. How to run the commands

- Run them on the server in **Windows PowerShell opened as Administrator**: only administrators
  and the ARCILOGGER services can read `arcilogger.toml`, `secret.key` and the audit spool.
- The ARCILOGGER programs are **not on `PATH`**. Once per window (change the first line if you
  chose another `-InstallDir`):

  ```powershell
  $arci   = "C:\Program Files\ARCILOGGER\venv\Scripts"
  $cfg    = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
  $core   = "$arci\arcilogger-core.exe"
  $api    = "$arci\arcilogger-api.exe"
  $ingest = "$arci\arcilogger-ingest.exe"
  $db     = "$arci\arcilogger-db.exe"
  ```

- The configuration is read from `C:\ProgramData\ARCILOGGER\arcilogger.toml` by default.
- Check that the organisation's own values (the organisation name `site_name`, the local time
  source `[clock] ntp_server`) are set; the installer takes them as `-SiteName` and
  `-NtpServer` ([INSTALL.md](INSTALL.md) section 7.1). It also warns while there is no log
  source (`[[sources]]`): a new installation has none, section 5 adds them:

  ```powershell
  & $core config-check                   # ERROR: exit code 1; WARNING: information only
  ```

## 1. First administrator

Details: [INSTALL.md](INSTALL.md) section 9.

1. Setup token (valid 24 h, single use, printed once):

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

2. `https://<server address>` (the web address the installer printed at the end) → **first-run
   setup** page: token, user name, display name, password.
3. Sign in, enrol TOTP, keep the **10 recovery codes** safe and away from the server, accept the
   KVKK notice.
4. Recovery (every administrator locked out): `& $api create-admin --config $cfg`.
5. **Licence:** the trial lasts 14 days. Send the request code printed at the end of the
   installation (or the one on Settings → Licence) to the licensor at once (section 17,
   [LICENSING.md](LICENSING.md)).

## 2. Other users

**Ayarlar → Kullanıcılar ve Roller** (administrators; changes need a password confirmation within
the last 5 minutes):

- Create users by role ([roles.md](roles.md)), e.g. an **auditor** for 5651 requests. Hand the
  temporary password (shown **once**) over **in person**: it must be changed at the first
  sign-in, and whoever signs in first enrols the second factor.
- Every user can change their own password later: user menu at the top right → **Parolayı
  değiştir** (it asks for the current password; the user's sessions in other browsers and on
  other devices end, and wrong current passwords lock the account like at sign-in).
- TOTP is mandatory for administrators and auditors. Roles that see personal data — in this
  release all four roles, the report viewer too (it sees names in Kullanım) — accept the KVKK
  notice at the first sign-in (audited).
- Non-administrators see only their **own** 5651 lookups; administrators see all of them (owner
  decision, [ASSUMPTIONS.md](ASSUMPTIONS.md) D5). Evidence packages built from another user's
  lookup are hidden too. The auditor cannot create evidence packages (owner decision, D4) and can
  download the packages it can see.
- **Users limit** (licence): once the number of active users reaches the licence's limit, no user
  can be added and no inactive user re-enabled (`license_limit`); existing users keep working.
  Without a valid licence (trial over) the limit is 3; during the trial there is none. Deactivate
  a user to free a place ([LICENSING.md](LICENSING.md) section 6).

## 3. Kamu SM: before the account, test and production

**Without Kamu SM credentials** the installed configuration is `provider = "zamane"` with the test
server. The `\ARCILOGGER\Seal` task runs every night (every 5 minutes from 00:10) and **fails
every time** without credentials or roots: Task Scheduler shows the last run result `0x1`,
`pending_days` in `& $core status` grows by one every day and the error is written to
`D:\ARCILOGGER\logs\core.log`. This is a configuration error: an `R20-seal-config` alert is
written to `alerts.jsonl` **once** per day and cause (section 9; also e-mailed when the e-mail is
set up). No raw log is lost; it waits in the spool.

Choose one of two options **before the first successful seal** (and do not change it
afterwards). The choice is **the owner's**, to be reviewed with a lawyer. With either option the
days before go-live get **no** production stamp (stamps of the test server have no legal validity
— Kamu SM); the difference is whether those days get integrity evidence at all:

- **A — The chain starts on the go-live day:** add `first_day = <go-live day>` to `[seal]` in
  `arcilogger.toml` (e.g. `first_day = 2026-11-01`) and switch the Seal task off during the test:

  ```powershell
  Disable-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName Seal
  # at go-live, once the production settings are in place:
  Enable-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName Seal
  ```

  Result: spool days before `first_day` are not part of the chain, are **never sealed** (nothing
  proves their integrity) and are **not deleted**; they stay in the spool as they are (what to do
  with them is the owner's decision). Ingest keeps loading them. When the task is switched on
  again, sealing writes one critical `R20-clock` alert **once** for each of these files ("a day
  before the start of the chain"; a general check against clock steps): expected with this
  option, the files stay where they are.
- **B — Seal during the test as well (pilot):** the Seal task stays on; when the test user
  arrives the waiting days are stamped by the test server. Result: those days get a hash chain
  and test stamps (integrity evidence), but they stay in the chain **permanently** as test stamps
  and never count as legal grade; days stamped more than `late_after_hours` (48) after their end
  are also marked **permanently** as stamped late (`stamped_late`). The production days continue
  the same chain. Verification trusts only the roots in `C:\ProgramData\ARCILOGGER\kamusm-roots`:
  the root the test stamps chain to (the test server's may differ from the production one —
  [kamusm.md](kamusm.md) K10, **verify on site**) must **stay** in that folder for as long as
  test-stamped days exist; without it those days fail verification (`R20-verify`). Legal grade
  comes only from the roots listed in `legal_root_fingerprints`; never list the test root there.

**Do not use `provider = "mock"` on the server.** The trial provider ([kamusm.md](kamusm.md)
section 4) is for development and trial servers only: its root is trusted only while
`provider = "mock"`. As soon as the provider is switched to Kamu SM, mock-stamped days **no longer
verify** (`R20-verify`, the monthly whole-chain check fails); there is no supported way to make
them verifiable again.

**Test account and switching to production:** the **Ayarlar → Kamu SM** wizard of the web
interface ([SETTINGS.md](SETTINGS.md)) or, on the console, [INSTALL.md](INSTALL.md) section 10 —
roots (fingerprint comparison), `secret set`, `credit`, `tsa-test` in that order; for production:
buy kontör, the production credentials, `url = "http://zd.kamusm.gov.tr"`, `[tsa]
legal_root_fingerprints`. The API loads the new setting by itself within 2 seconds; when the
Settings page shows "A change waits for a restart", or on a version before this one,
`Restart-Service ArciApi` (until it is loaded the red "test stamp" banner and the report stamps
keep the old setting).

## 4. After editing `arcilogger.toml`

The services take changes made on the Settings pages without a restart ([SETTINGS.md](SETTINGS.md)
section 2). After a hand edit too, the collector and ingest load the log sources, and the API
and the worker the sections they read, within seconds; scheduled tasks read the file on every run; the `ApplySystem` task brings the firewall in step within 5
minutes. **Use one way at a time** (the browser or a hand edit). For the sections that are not
reloaded live (`[collector]`, `[api]`, `[ingest]`, `[worker]`, `[database]`) and while the browser
administration is off:

```powershell
# [[sources]] or the [tsa] URL/port changed: the firewall first
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
# then the services (UDP syslog can be lost for a few seconds while the collector restarts)
Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
```

## 5. Devices and a check per source

**Log sources first.** The `arcilogger.toml` of a new installation has **no log source**: the
`[[sources]]` lines of the example configuration's fictional example network are comments only.
That is a valid state, but until sources are added the installer's firewall opens 514 to no
sender and nothing is recorded (with `-SkipFirewall` every sender is recorded as `unknown-<ip>`,
its parser chosen by content); the silent-source alert (R15) watches no source and the Panel's
"Log alımı" box says "İzlenen kaynak yok". Add one `[[sources]]` entry for every device that sends
syslog, at least your firewall and your core router (the DHCP server): remove the `#` in front of
the example lines in the file and use your device's name and address
([DEVICE-SETUP.md](DEVICE-SETUP.md) section 4). Then run the installer again or the commands of
section 4; `& $core config-check` no longer warns about sources.

Device setup: [DEVICE-SETUP.md](DEVICE-SETUP.md). After a test message from every device, **every
expected source** must have today's file:

```powershell
Get-ChildItem D:\ARCILOGGER\spool\*\$(Get-Date -Format yyyy-MM-dd).log | Format-Table Directory, Length, LastWriteTime
Get-Content D:\ARCILOGGER\state\collector-status.json
& $ingest status                       # per source: loaded day, lag, last error
```

The firewall admits 514 only from the `[[sources]]` addresses. A source without a file sends from
another address than the configured one: [DEVICE-SETUP.md](DEVICE-SETUP.md) section 4. Check
the core router in particular (MikroTik; DHCP and ARP-LOG, the core of the 5651 access
records).

### Persons and device assignments

A 5651 lookup names a person only when the device was assigned to that person **at the moment of
the event** (assignment history,
[adr/0008-device-assignment-history.md](adr/0008-device-assignment-history.md)). As an
administrator, on the **Cihazlar ve Kişiler** page:

1. Enter the departments and the network: ARCILOGGER ships no departments and no VLAN plan,
   both are empty on a new installation. On the **Departmanlar ve VLAN'lar** tab add the
   departments first (persons and the person CSV refer to them), then every VLAN or subnet of
   the organisation with **VLAN ekle** (add a VLAN): the network address with its prefix length
   (e.g. `10.0.20.0/24`, host bits zero), the VLAN id (1–4094, its number on the network: the
   VLAN number of a lease comes from the DHCP server's name, `dhcp.server_vlan_regex`, e.g.
   `dhcp20` → 20; empty for a network without one such as a transit network), a name and,
   optionally, the purpose, the DHCP server's name on the router (e.g. `dhcp20`), a department,
   the **Misafir ağı** (guest network) flag (a guest network that leaves through another exit
   than the FortiGate, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 5) and notes. A device is in
   the VLAN of its VLAN number or of the network its IP address is in: it takes that VLAN's
   name and, without a person, its department; a smaller network inside another one (e.g. a
   `/25` inside a `/24`) comes first.
   The network, the VLAN id and the DHCP server are unique. Collection, sealing, 5651 lookups
   and usage work without VLANs; devices then get no VLAN name and no VLAN department.
2. Add persons, or import them from CSV: download the template
   (`name;department;title;email;employee_no;active;notes;usage_excluded`; only `name` is required,
   `usage_excluded` evet/hayır leaves the person out of the usage reports), save it in Excel as
   **"CSV UTF-8"**, **preview** first (nothing is written, errors are shown per line), then
   **apply** (all lines or none). CSV import needs the Bulk import module; manual entry always works
   (the assignment CSV belongs to that module too; the CSV exports of the person and device lists
   always work, [LICENSING.md](LICENSING.md)).
3. Assign devices: search by MAC, host name, IP or VLAN (filter "Bilinmeyen cihazlar"), select one
   or many devices and assign them to a person — or import an assignment CSV
   (`mac;employee_no;person_name;valid_from;note`). If the person has had the device for some time,
   enter that date as the start (`valid_from`): the assignment history cannot be changed backwards
   later, only extended.
4. When a device changes hands, assign it to the new person **the same day**. Do not delete a
   person who leaves: deactivate them (the history stays) and assign their devices to the new
   holder, or unassign them.

A person may hold several devices (a PC, a phone, the phone's next randomised MAC). Every
assignment, applied import and CSV export is written to the audit log (person numbers and counts,
not names).

## 6. Settings in the database

Database settings are read and changed on **Ayarlar → Uygulama ayarları** (Settings →
Application settings) in the web interface or, on the console, with `arcilogger-db settings`. Every change is written to the **hash-chained audit log**
(`settings.change`) with the old and the new value; `--by` is the ARCILOGGER user name of whoever
made the change (`tolga` in the examples below). Upgrades and the seed never reset a changed
value.

```powershell
& $db settings list
& $db settings get nat.wan_transip
& $db settings set lookup.tolerance_seconds 60 --by tolga
& $db settings set nat.wan_transip '<FortiGate WAN address>' --by tolga
'["<MAC 1>", "<MAC 2>"]' | & $db settings set devices.infrastructure_macs - --by tolga
```

Values are JSON (no floats). A text value may be written without JSON quotes (like the WAN address
above). A list or an object — any value with double quotes — is **piped in**, with `-` in place of
the value: Windows PowerShell 5.1 drops the double quotes of command-line arguments it passes to
programs. Always check the result with `settings get`. Settings are read from the database when
they are used; no service restart is needed. To be set on site ([CORRELATION.md](CORRELATION.md)
section 8):

| Setting | When |
|---|---|
| `nat.public_ips`, `nat.wan_transip` | once the FortiGate's public / WAN address is confirmed (double NAT, [ASSUMPTIONS.md](ASSUMPTIONS.md) A9); with several ISP lines, each behind its own router, `nat.wan_transip` stays empty ([INSTALL.md](INSTALL.md) section 0.1) |
| `devices.infrastructure_macs` | MAC addresses of the core router, the FortiGate and the switches (no IP-conflict alarms for them) |
| `lease.server_seconds` | DHCP servers whose lease time differs from one day (`lease.default_seconds`), e.g. the guest network's: `{"<server name>": <seconds>}` (piped in) |
| `devices.oui_file` | optional vendor names: put the file at `C:\ProgramData\ARCILOGGER\oui.csv` ([CORRELATION.md](CORRELATION.md) section 1, "Vendor") |
| `lookup.port_hint` | only after the ISP router is confirmed to keep source ports |

## 7. Limits of this release

- **No department history:** the person in a 5651 result is the assignment at the moment of the
  event, the department is the person's current one (the report says so). A past assignment
  cannot be corrected in the interface (section 5).
- **Kaynaklar, Canlı Akış, Arama, Uyum Durumu and Sistem** are still placeholders. Working pages:
  Panel, 5651 Sorgu, Kullanım (section 16), Güvenlik, Mühürleme, Kanıt Paketleri, Cihazlar ve
  Kişiler (section 12), Raporlar (Panel, Güvenlik and Raporlar: section 15), Denetim Kaydı,
  Ayarlar (users and roles, licence: section 17).
- **Paid modules** (licence, [LICENSING.md](LICENSING.md)): Usage analytics (the Kullanım page,
  the dashboard's usage sections), Scheduled usage reports (the Raporlar page) and Bulk import
  (CSV). All are on during the trial; a module that is off shows its page locked. Everything else
  is 5651 Temel and never switches off, in any licence state.
- **Alerts are on the Güvenlik (Security) page** (administrators only in this release): the
  list, state and severity filters, the detail with step-by-step advice, the state (Yeni,
  İnceleniyor, Kapandı, Yanlış alarm) and notes; the Panel shows the newest open alerts
  (section 15, [ALERTS.md](ALERTS.md) section 8). While you work on a problem, set its alert to
  **İnceleniyor**: an alert closed while its problem persists is replaced by a new one at the
  check's next run. Alerts no check can resolve (e.g. `R20-spool`, replica differs) are closed by
  hand after looking into them, with the cause as a note. There is no rule engine (YAML rules),
  rule editor or webhook; `arcilogger-worker alerts list --open` still works on the server and
  e-mail is sent once `[smtp]` is set up.
- **Sealing configuration errors** (no Kamu SM credentials, no root in the `kamusm-roots` folder,
  `[tsa] zamane_jar` not set) stop the Seal task before sealing starts: the task's last run result
  is `0x1`, the error goes to `D:\ARCILOGGER\logs\core.log` on every run and an
  `R20-seal-config` alert is written once per day and cause.
- "Seal now" is not available on the web; run the `\ARCILOGGER\Seal` task in Task Scheduler.
- The database dumps lie on the server's own `D:` disk; copying them off the server (external
  disk, a NAS) is done by hand (section 10).

## 8. First 5651 lookup (trial)

1. Browse to a site from a known PC (you know its IP and MAC); note the time and the destination
   (domain, or IP and port).
2. A few minutes later open **5651 Sorgu** and enter the request details. Every lookup is stored
   **permanently** with its request details and audited: mark the trial clearly (e.g. authority
   "DENEME", reason "installation trial").
3. Enter the noted time and destination. The result must show the PC's IP, MAC, its DHCP lease
   and the evidence lines.
4. Download the **official report (PDF)** and open the raw line of an evidence row.
5. Optionally build an **evidence package** from the lookup (administrator) and verify it with
   `verify.py` on another PC ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) section 5). Packages need
   sealed days (the next day).

## 9. Daily and weekly checks

Every day:

- **Panel**: log intake, yesterday's seal, credit, clock offset and open alerts at a glance.
  **Güvenlik** page: look into the new alerts, set the ones you work on to İnceleniyor.
- **Mühürleme** page: was yesterday verified? Is there a red "test stamp" banner?
- On the server:

  ```powershell
  & $core status                         # pending_days, last_sealed_day, legal_grade, credit
  Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
  Get-Service Arci* | Format-Table Name, Status
  & "$arci\arcilogger-worker.exe" alerts list --open   # open alerts (alerts.jsonl included)
  Get-Content D:\ARCILOGGER\state\alerts.jsonl -Tail 20
  Get-Content D:\ARCILOGGER\state\backup\last.json   # nightly dump: "ok": true and today's date
  Get-Volume D | Format-Table DriveLetter, SizeRemaining, Size
  ```

- Alerts (`alerts.jsonl` and the worker's checks; thresholds, settings and e-mail:
  [ALERTS.md](ALERTS.md)):

  | Code | Meaning |
  |---|---|
  | `R15` | Log source silent: no message from a source for longer than its heartbeat (`[[sources]] heartbeat_minutes`; in the example configuration's commented-out sources firewall 10, core router 15 min; no source, no R15); resolved by itself when the source sends again |
  | `R20-seal` | A sealing attempt failed (Kamu SM unreachable, stamp did not verify, …), or a closed day is still not sealed at 09:00 the next morning; the day is retried |
  | `R20-seal-config` | The Seal task could not start sealing at all: Kamu SM credentials, the `kamusm-roots` roots or `[tsa] zamane_jar` missing (once per day and cause; section 3) |
  | `R20-seal-skip` | A run of more than `[seal] max_consecutive_empty_days` (7) consecutive days **without any log**: those days were not stamped one by one; the next sealed day's manifest lists them as a gap (`gaps`). The collector stopped, the server was off or the clock jumped forward |
  | `R20-clock` | Clock offset (`clock-check`) or a clock step seen by sealing (a spool file of a day before the chain start, of a sealed day or of a future day; a clock that went back). Sealing leaves such files alone |
  | `R20-verify` | Verification error (weekly/monthly verification) |
  | `R20-replica` | Second copy failed |
  | `R20-credit` | Credit below the threshold, or unreadable |
  | `R20-backup` | The nightly database dump failed or did not start (`high`: not enough free space, `pg_dump` missing or older than the server, database unreachable, the dump failed its check). The dump was made but: old dumps could not be deleted (`R20-backup:prune`, `medium`; resolved an hour after a later good backup that no longer reports it), `last.json` could not be written (`R20-backup`, `high`), or it is named earlier than a dump already there, the clock was set back (`R20-backup:clock`, `medium`); these two are resolved by a later good backup. Details: `state\backup\last.json`, `logs\backup.log` ([BACKUP.md](BACKUP.md) section 7) |
  | `R20-retention` | A deletion run was refused or left incomplete; a completed deletion is also written at `info` level with the name of its deletion record |
  | `R20-spool` | Raw logs cannot be written to disk (full disk, hardware); after recovery a second alert says how many messages were lost |
  | `R20-spool-torn` | An unterminated last line (power cut, crash, full disk) was closed with a line break (no byte is changed; the fragment stays one damaged line) |
  | `R20-disk` | Data or system drive more than 80 % full (`alerts.disk_used_percent`) |
  | `R20-parse` | More than 1 % of a source's lines of the last hour could not be parsed |
  | `R20-job` | A worker job failed 3 times in a row (`logs\worker.log`) |
  | `R20-backup` | Database backup failed, older than 36 hours, or still none 48 hours after the installation |
  | `R20-email` | An alert e-mail could not be sent (`[smtp]`, `smtp.password`, firewall); never e-mailed itself |
  | `R20-report` | A scheduled usage report could not be made or not sent to a recipient (the run history on the Raporlar page, `[smtp]`; sections 14 and 15). Not raised while the Scheduled usage reports module is off in the licence; resolved by itself when the module goes off |
  | `R20-license` | Paid modules ending or off: the trial ends within 3 days, a time-limited licence within 14 days, or the paid modules are off because of the licence state (trial over, licence invalid or for another server, expired, version not covered); the 5651 functions keep working. Settings → Licence ([LICENSING.md](LICENSING.md)) |
  | `R20-license-limit` | Users (no new user can be added) or log sources (still collected and sealed) above the licence's limit |
  | `R20-license-maintenance` | Maintenance ends within 30 days or has ended (`info`: upgrades and support only, the licence stays valid) |
  | `R20-admin` | Browser administration problem: the administration service (ArciAdmin) has not answered for more than 2 minutes, a request whose signature or installation does not match was refused (possible tampering), a change was rolled back, manual action is needed, the file rights changed (after a console `secret set`: `Set-ArciVaultAcl.ps1`), `ApplySystem` could not bring the firewall, the time or the Zamane client in step, or it found a log source outside the policy networks. The 5651 functions go on; [SETTINGS.md](SETTINGS.md) section 8 |
  | `R20-config-change` | Significant configuration change (a guarded setting): retention switched on or its days lowered, the replica switched off or retargeted, a log source switched off or removed, production → test, the Kamu SM credentials changed, a root or a legal fingerprint added, a Zamane client uploaded or promoted (high); SMTP or the backup switched off (medium). If no known administrator made it, **Cancel** a held change or **Revert** an applied one on the **Change history** page and look at the audit log |
  | `new-log-source` | A sender that is not in the configuration (if the firewall let it through) |
  | `wrong-transport` | A message arrived over a transport its source does not allow (`[[sources]] transport`): a forged UDP message or a wrong device setting; the message was not recorded |

  Look into `R15`, `R20-seal`, `R20-seal-config`, `R20-seal-skip`, `R20-clock`, `R20-verify`,
  `R20-spool` and `R20-disk` **the same day**. Check the Seal task's `LastTaskResult` and
  `pending_days` every day as well; details in `D:\ARCILOGGER\logs\core.log`. With the e-mail
  set up ([ALERTS.md](ALERTS.md) section 4) high and critical alerts arrive by e-mail too; if none
  arrives, look for an `R20-email` alert.
- Status files: `D:\ARCILOGGER\state\collector-status.json`, `ingest-status.json`,
  `worker-status.json`.

Every week: the `VerifyWeekly` result in Task Scheduler, the credit (`& $core credit`), free disk
space. Every month: the `VerifyMonthly` (whole chain) result.

## 10. Backups

Details, copies off the server and restore: [BACKUP.md](BACKUP.md).

- **The database** holds users, 5651 lookups, the records of the stored reports, person and device
  details and the audit log, which cannot be rebuilt from the raw logs. The `\ARCILOGGER\Backup`
  task dumps it every night at 01:40 into `D:\ARCILOGGER\backups\scheduled\` (`pg_dump`; one dump
  each of the last 14 days, 8 weeks and 12 months is kept) and writes the result to
  `D:\ARCILOGGER\state\backup\last.json`; a failed dump raises an `R20-backup` alert. Before an
  upgrade the installer also dumps the database into `D:\ARCILOGGER\backups\db\`. Manual dump and
  checks:

  ```powershell
  & $db backup run
  & $db backup list
  & $db backup verify
  ```

- The dumps lie on the **same disk** as the database: copy them once a week to an external disk or
  a separate share on a NAS ([BACKUP.md](BACKUP.md) section 4) and run a restore drill **every
  three months** (section 6). Record the SHA-256 of every copy off the server (`SHA256.txt`,
  section 4): a restore runs the dump's SQL as the PostgreSQL superuser and is checked against it
  (`--sha256`). A restore always goes into a new database (`& $db backup restore`); the live
  database is never touched.
- **`C:\ProgramData\ARCILOGGER\secret.key`** decrypts the TOTP secrets and the stored
  credentials. Without it every user enrols TOTP again and the credentials are entered again. Do
  not keep it **unencrypted in the same place** as the database backup: copy `secret.key`,
  `arcilogger.toml` and the Caddy CA folder (`caddy\`) once after the installation to an
  encrypted offline medium kept apart from the dumps ([BACKUP.md](BACKUP.md) section 3).
- **The vault** (`D:\ARCILOGGER\vault`): the second copy (`[replica]`) is **off** by default;
  switch it on once its target (e.g. a WORM folder on a NAS, `[replica] target`) is verified
  ([INSTALL.md](INSTALL.md) section 10, "Replica"; [ASSUMPTIONS.md](ASSUMPTIONS.md) A6).

## 11. Legal preparation

Before the pilot, give these drafts to the company's lawyer ([ASSUMPTIONS.md](ASSUMPTIONS.md) A7):

- [legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md) — employee information text
- [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md) — log
  retention and deletion policy

## 12. The "Cihazlar ve Kişiler" page

What to do when is in section 5 ("Persons and device assignments"); this section describes the page
itself. In this release only administrators open it; as it shows person names, the KVKK notice
must be accepted first. Devices are not entered by hand, they come from the DHCP and ARP records.
Every change, import and export is written to the audit log.

- **Cihazlar** (devices): the search looks at the MAC address (also without separators:
  `f01faf`), the host name, the last IP and the device name. The filters (type, VLAN, randomised
  MAC, infrastructure) and **Bilinmeyen cihazlar** (no owner, not infrastructure) stay in the
  address bar, so the view can be bookmarked; the search text is never put in the address.
  **CSV indir** downloads every page of the list shown. Select devices and use **Kişiye ata** /
  **Atamayı kaldır**; a start in the past cannot be before the start of the device's current
  assignment or the end of its last one. When the server refuses, the dialog says why and no device
  is changed.
- **Device details** (click the MAC address): name, type, the **Altyapı cihazı** (infrastructure)
  flag, tags, notes; the assignment history and the recent leases. The history is append-only: a
  record is never corrected or deleted, a new assignment closes the previous one. A device with
  the **Rastgele MAC** warning is mostly a phone; assign every MAC address of the same phone to the
  same person.
- **Kişiler** (persons): the search looks at the name, e-mail, employee number and job title.
  Persons are never deleted but deactivated (**Pasifleştir**): an inactive person cannot be given
  devices, the history and the link in past 5651 results are kept, and their current devices stay
  assigned (remove them on the devices tab if needed).
- **Departmanlar ve VLAN'lar**: add and rename departments; add VLANs and subnets (**VLAN
  ekle**), edit and delete them (the row's **İşlemler** menu) and choose the department of each
  VLAN (the select in the table). Without VLANs the page says "Henüz VLAN yok: ağınızı ekleyin"
  (no VLANs yet: add your network). A device without a person takes its VLAN's department; when
  the VLAN has none, it has none. An edit sends only the fields that changed; correcting the
  network keeps the VLAN's name, department and flag. Deleting asks for a confirmation: the
  devices of that network lose the VLAN name and the VLAN's department, also on the usage pages;
  raw logs, leases and stored 5651 results do not change. A change takes effect like a
  department change, at once: the device list and the next 5651 lookups use the new table, the
  usage pages take the VLAN's department by its VLAN number when they are read. The VLAN number
  of the usage is that of the VLAN holding the address; when no VLAN holds it, the lease's (from
  the DHCP server's name, else the network, when the lease was built). When this number changes
  (a renumbered VLAN, a new one or one with a new network; for the addresses that leave a VLAN's
  network — a deleted or moved VLAN — only the hours without a lease), the usage of the recent
  days is recomputed by itself (`usage.reattribute`, `usage.auto_rebuild_days`); older days need
  `usage rebuild` (section 13).
- **İçe/Dışa aktar** (import/export): the whole lists as CSV (`;`-separated for Excel, times in
  local time, evet/hayır for yes/no); the files hold personal data, keep them only as long as
  needed. An exported person list imports back as updates: its `id` column names each person,
  also one without an employee number. On import, **Kontrol et (deneme)** writes nothing and
  lists the errors per line; **Uygula** writes every line or none. When the data changed after
  the check (e.g. a person was deactivated), the apply is refused and the page shows the new
  errors: fix them and check again. An assignment file never adds devices.

In a 5651 result the person step shows the assignment valid at the requested moment (e.g. "Atama:
01.10.2026 09:00:00 itibarıyla"); lookups stored before the assignment history have no such line.

## 13. Usage reports: what the numbers mean

The usage pages and reports **estimate** daily use per person, device, department and site from
connection logs (method, settings and blind spots: [USAGE.md](USAGE.md)). They need the Usage
analytics module ([LICENSING.md](LICENSING.md)); while it is off the `usage rebuild` command is
refused too.

- **Online time**: minutes with a connection open. **Active time (estimated)**: minutes with at
  least 2 KB of non-background traffic; the time per site splits these minutes by bytes and adds up
  to the active time. A tab left open in the background may count as active; no number is exact.
- Usage is written to the person the device was assigned to **at that moment** (the same rule as
  the 5651 lookup). Devices assigned to nobody show as "device", addresses without a lease as "IP".
- Names are shown by default (the owner's decision of 2026-10-20); a person can be left out of the
  person-level reports with the person's **Kullanım raporlarından hariç tut** box on the Kişiler
  tab (`usage_excluded`); 5651 lookups are not affected. Viewing a detail and exporting are audited.
- Enter the religious holiday dates every year in the setting `usage.holidays` (only the fixed-date
  national holidays are shipped; [USAGE.md](USAGE.md) section 2.5).

**After linking devices to persons** (section 5) the usage of the last 7 days moves to the new
person within 10 minutes by itself. For older days (e.g. a backdated hand-over) rebuild those days:

```powershell
& "$arci\arcilogger-worker.exe" usage rebuild --from 2026-10-01 --to 2026-10-19 --config $cfg
```

## 14. Usage reports (scheduled e-mail)

Details: [USAGE-REPORTS.md](USAGE-REPORTS.md). The reports need the Scheduled usage reports
module ([LICENSING.md](LICENSING.md)); while it is off no report is sent.

- Four schedules come with the installation — **Gün sonu raporu** (end of day, daily 18:30, the
  day so far), **Tam gün raporu** (full day, daily 07:00, the previous day), **Haftalık rapor**
  (weekly, Monday 07:15, the previous week), **Aylık rapor** (monthly, the 1st 07:30, the
  previous month) — all without recipients and **disabled**. By the owner's decision reports are
  person-level, with names; a schedule can switch the person level off or use pseudonyms.
- Set up and test the alert e-mail first (section 9 and [ALERTS.md](ALERTS.md) section 4): the
  report e-mail uses the same `[smtp]` connection. Then enter the recipients of a schedule on the
  Raporlar page and enable it; check the content with **preview** and the delivery with **run
  now**.
- Every recipient gets an e-mail of their own: the HTML report with the PDF and the workbook
  attached (above 15 MB in total a download link instead, `reports.max_attachment_mb`). The files
  stay 400 days under `D:\ARCILOGGER\exports\usage-reports\` (`reports.keep_days`); they never
  go into the vault.
- Usage times are estimates (from connection logs); every report says so. A report that cannot
  be delivered opens an `R20-report` alert.

## 15. The Panel, Güvenlik and Raporlar pages

Every page and every block is shown only to those allowed to see it; the server checks the
permission again on every request. Usage figures are **estimates** computed from connection logs
(info icon).

- **Panel** (the start page): log intake (active sources, messages per minute over the last 60
  minutes, silent sources), yesterday's seal (state, time, serial, verification), the Kamu SM
  credit and how long it lasts, the clock offset (state "Bilinmiyor" — unknown — when the last
  measurement is older than 3 hours); today's company usage (active users, active time,
  downloaded and uploaded, an hourly chart and its table), open security alerts (counts per
  severity and the newest five), the sites with the most time, categories (the six largest with
  their share of the active time, the rest "Diğer"; the day's active time in the middle) and the
  last 30 days of sealing. A block the role may not see is not shown at all (HR sees only the
  usage blocks). The link in a block's title opens its page, a calendar day opens that day's
  sealing detail, an alert opens it on the Güvenlik page. The live blocks refresh every 30
  seconds and the usage every 5 minutes by themselves; nothing is refreshed while the tab is in
  the background, and these refreshes do not keep the session alive.
- **Güvenlik:** the **Güvenlik Özeti** on top (open alerts per severity, being investigated, mean
  time to close over the last 30 days), the alert list below: state, severity (buttons), rule
  (e.g. `R15`), time range and text search (title, rule, source); the filters and the open alert
  stay in the address, the search text does not. Clicking an alert opens its summary, details,
  **Ne yapmalı?** (in the interface language), history (the system's own changes with their
  reason) and the e-mails sent (recipient addresses shortened). A user with the alert management
  permission changes the state — **Yeni**, **İnceleniyor**, **Kapandı**, **Yanlış alarm**; the
  page offers only the allowed changes — and adds notes; every change goes into the alert's
  history and the audit log. An alert closed while its problem persists makes the next check open
  a **new** alert: use **İnceleniyor** while you work on it. A closed alert is reopened with
  **Yeniden aç** when no other active alert holds the same problem; otherwise the page links to
  that alert. When the alert changed in the meantime (another user or the system closed it) the
  page says so and loads its current state.
- **Raporlar** (administrators only): scheduled reports — end of day (18:30, from 00:00 of the
  day), full day (07:00, the previous day), weekly (Monday 07:15, the previous week), monthly (the
  1st at 07:30, the previous month). In **Yeni zamanlama** / **Düzenle** choose the kind, day and
  time, the recipients (type an address and press Enter; a comma-separated list can be pasted),
  the formats (e-mail body, PDF, Excel) and the detail; the dialog shows the next run and the
  period it covers. Reports contain **person-level detail (with names)** by default, the
  pseudonym option is off ([ASSUMPTIONS.md](ASSUMPTIONS.md), owner decision); choose the
  recipients accordingly. A schedule without recipients cannot be enabled. **Şimdi çalıştır**
  makes the report over its own period (end of day: today until now; full day: yesterday; the
  previous week or month) or a chosen one of at most 92 days, e-mail is optional (the page says
  so when e-mail is switched off on the server); **Önizle** shows the
  report without storing or sending it (the PDF in a new tab, the e-mail on the page). The **run
  history** shows every recipient's delivery and the files; downloading a file is written to the
  audit log. Deleting a schedule keeps its past runs and their files. The link in a report e-mail
  opens that run directly.

## 16. The "Kullanım" (usage) page

This page needs the Usage analytics module ([LICENSING.md](LICENSING.md)); while it is off the
page and the dashboard's usage sections show as locked. The **Kullanım** page (`usage.person` or
`usage.anonymous`; every role has one in this release)
shows the internet usage by company, department, person, device, site and category. By the
owner's decision of 20.10.2026 person and device names are shown by default; every tab but
**Şirket** needs the accepted KVKK notice first. Every duration is an **estimate** (ⓘ "tahmini"):
computed from the connection logs in 1-minute slices, not exact screen time; tabs open in the
background may count too ([USAGE.md](USAGE.md) explains the method). The line above each view
says how far the data has been processed ("Veriler 14:35'e kadar işlendi"; for a finished day
"Veriler 12.10.2026 sonuna kadar işlendi").

- **Period and hours:** Bugün (today, the default), Dün, Bu hafta, Geçen hafta, Bu ay, Geçen ay or
  a custom range in the calendar (weeks start on Monday); ‹ › previous/next period. **Tüm saatler
  / Mesai içi / Mesai dışı** (all / work / off hours) follow the work-hours setting of the server
  (default Monday–Friday 08:00–18:00). Tab, period, hours and the opened detail stay in the
  address bar: the page can be bookmarked, the browser's Back button returns to the previous view.
  The search text of a table is never put in the address.
- **Şirket** (company): summary tiles (active users, online and active time, off hours,
  downloaded, uploaded, sessions, blocked), hourly downloaded/uploaded and active-user charts
  (each with a table view), the sites with the most time, categories (the ten largest; the
  donut shows the six largest and "Diğer", the period's active time in the middle), applications
  and departments (traffic with neither a person's department nor a VLAN's is under
  "Bilinmiyor").
  Clicking a row opens its details.
- **Departman**: departments → a department's persons, the devices of its VLANs assigned to
  nobody and IP addresses without a known MAC, with every metric; its sites and categories.
- **Kişi** (person): every person. **Cihaz** (device): the devices assigned to nobody and the IP
  addresses without a known MAC — the traffic of a device assigned to a person counts for that
  person (its share is in the person's **Cihazlar** section). The search finds a name, MAC, IP
  address or host name as typed (Turkish letters included, e.g. "ayşe"). → details: summary (a
  device's MAC, IP and host name); for a single day the timeline of the day (active minutes
  coloured by category, hover shows the time and the top site); for a period the table per day
  with the total; hourly charts; the hourly heat map of the last 30 days; **Siteler** (click a
  site for its host names and URLs), **URL'ler** (addresses logged by the web filter, blocked ones
  marked, searchable), categories, applications, every blocked attempt and the person's devices.
- **Site / Kategori**: every site (search by domain, category filter; background traffic such as
  updates is marked **arka plan**: it counts as online time, never as active time) → its host
  names and who used it for how long (their time on that site); categories → their sites and
  users.
- **Dışa aktar** (export, right of the tables): the view shown and its period. The **CSV** holds
  the view's main table, the **Excel** file every section on its own sheet and the sheet
  **Yöntem** (the method note); for a table that is not its view's main table only Excel is
  offered. The files may contain personal data, keep them only as long as needed; every export
  is written to the audit log. Scheduled PDF reports are on the Raporlar page (section 15).
- **Yeniden hesapla** (recompute; administrators only, the ⋯ menu at the top right): the usage
  summaries of the chosen days are computed again from the traffic logs — e.g. after devices were
  assigned to persons. The dialog shows the progress; when it is done every view is refreshed. Raw
  logs never change. Days before the oldest log in the database cannot be rebuilt; the server then
  names the earliest day and those days keep their summaries.

When the optional setting `usage.pseudonymize_anonymous_role` is on (default off), roles with only
`usage.anonymous` see pseudonyms instead of names ("Kullanıcı-7F3A", **anonim** badge). The
pseudonyms need a key on the server: until it is stored those roles see the server's instruction
instead of the lists; store a long random text with `arcilogger-core secret set
usage.pseudonym_key` (the command asks for it; do not keep it in a text file). The auditor then
sees persons and devices by their pseudonyms in Denetim Kaydı too; but with search and 5651
lookups it can reach the addresses, so pseudonyms are a weak protection for the auditor
([USAGE.md](USAGE.md) section 6).

## 17. Licence

Details: [LICENSING.md](LICENSING.md) (modules, states, limits, maintenance, a server change).

1. After the installation ARCILOGGER runs **14 days** with every module; every page shows the
   banner "Deneme sürümü — N gün kaldı." (trial version, N days left). When the trial is over, or
   the licence has a problem, only the paid modules (Usage analytics, Scheduled usage reports,
   Bulk import (CSV)) switch off; the 5651 functions (log collection, sealing, the 5651 lookup,
   evidence packages, alerts, the audit log, users) work in every state.
2. Check that the organisation name (`site_name`) is set (section 0): it goes into the request
   code.
3. Get the request code: step 6 at the end of the installation, **Ayarlar → Lisans →
   Etkinleştirme** (Settings → Licence → activation; Kopyala / İndir (.txt)) in the web interface,
   or:

   ```powershell
   & $db license request-code --config $cfg
   ```

4. E-mail the code to **arman@khadangan.com**, with the modules you want and the number of log
   sources and users. The server never connects anywhere.
5. Upload the `.lic` you receive under **Ayarlar → Lisans → Lisans dosyası** (licence file; your
   password is asked again), or:

   ```powershell
   & $db license verify "C:\Kurulum\ARCI-L-<number>.lic" --config $cfg
   & $db license install "C:\Kurulum\ARCI-L-<number>.lic" --config $cfg
   ```

6. Check the state: the licence page, or `& $db license status --config $cfg`. The web interface
   and the worker follow the new state within a minute, without a restart.

Without a valid licence (trial over, an invalid file) no new user can be added once 3 users are
active; existing users keep working (section 2). The log-source limit is soft: the logs of sources over the limit are still collected
and sealed. When the server changes (new hardware, Windows reinstalled), send a new request code.
Maintenance limits upgrades: [INSTALL.md](INSTALL.md) section 13.
