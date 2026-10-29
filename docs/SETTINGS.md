# Settings: changing the server's configuration from the browser

> Türkçe: [AYARLAR.md](AYARLAR.md). Decision: [adr/0014-browser-administration.md](adr/0014-browser-administration.md).
> Installation: [INSTALL.md](INSTALL.md); security details: [SECURITY.md](SECURITY.md) section 14.
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance.

This document describes how the **Settings** pages of the web interface change the server's
configuration (`arcilogger.toml`, the secret store, the Kamu SM root certificates, the Zamane
client, the firewall, Windows Time), what deliberately stays on the server console, and what to
do when something goes wrong. Installation stays on the console; day-to-day changes need no
Remote Desktop session.

## 1. What changes from the browser?

The **Settings** pages are open to the **Administrator** role only (`settings.manage`; **Sources**
also needs `sources.manage`). **Seal now** and **Query credit** on the **Sealing** page need
`seal.manage`. Each page changes only the values below; every other field is shown with a lock
("only the server console can change this value, for security reasons", section 5). The API
endpoints the pages use are in section 10.

| Page (address) | Changed from the browser | Deliberately console only |
|---|---|---|
| **General** (`/settings/general`) | organisation name (`site_name`), local and fallback time source (`[clock] ntp_server`, `[clock] fallback_server`), clock offset alert threshold (`[clock] alert_offset_ms`); the last measured offset is shown, a new time server is tried before it is saved | `[clock] ntp_port` |
| **Application settings** (`/settings/application`) | the database settings by group (alerts and e-mail, 5651 lookup and lease timeline, search and live tail, usage analytics with the work hours and holidays, usage reports, evidence packages, sessions, passwords and privacy, database); in the 5651 lookup group **Rebuild the leases** (at most 31 days) | `devices.oui_file`, `usage.psl_file`, which name files |
| **Kamu SM** (`/settings/kamusm`) | a wizard: Java (one of those the administration service found on the server), Zamane client (upload), root certificates (add) and legal root fingerprints (installed roots only), customer number and password (tried with a credit query when saved), **Stamp test (uses 1 credit)**, **Switch to production** (and back to test), credit alert threshold and timeout; `[seal]` read-only | the mock or another provider, a free URL or port, the digest, removing a root or a fingerprint, a free `java` path, approving an unknown Zamane release (`approve-jar`, section 4) |
| **Sources** (`/settings/sources`) | add a log source, edit it (the name is fixed), switch it on or off (`[[sources]] enabled`), remove it (typing its name); the table shows the last message and the firewall state | the whole `[collector]` section (bind address, ports, TLS, `unknown_senders`) |
| **E-mail** (`/settings/email`) | `[smtp]` (on/off, server, port, security, user, sender, timeout), the SMTP password, a CA file for the server's certificate (upload, or back to the Windows certificate store), the alert e-mail settings (`alerts.email_*`: recipients, lowest severity, hourly limit …); a trial e-mail before saving; **Send a test e-mail** (with the saved settings, at most 5 an hour) | — |
| **Replica** (`/settings/replica`) | on/off, target (`\\SERVER\share` only), user, password; the connection is tried when saving; **Test the connection** (the saved target, at most once a minute) | a local folder as target |
| **Retention** (`/settings/retention`) | deleting expired days on/off, the retention period (at least 730 days); the number of days it deletes is shown first and typed to confirm | — |
| **Backup** (`/settings/backup`) | on/off, `[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly`, `[backup] min_free_gb`; this version does not show the last backup's details on the page, a failed backup is an `R20-backup` alert | `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore` |
| **Change history** (`/settings/history`) | the requests, their states and changes, a link to the audit log; **Cancel**, **Revert**; the configuration versions; **Download the diagnostics package** (a ZIP for support, no secrets) | restoring a version (`versions restore`, the page shows the command) |

No page changes `data_dir`, `secrets_dir`, `[database]`, `[api]`, `[ingest]`, `[worker]`,
`[seal]`, the database password or the usage pseudonym key. They belong to the installer and the
administrator.

**ARP times** (Application settings, 5651 lookup and lease timeline): "Ignore ARP after a release"
(`arp.ignore_after_release_seconds`, 300 seconds) cannot be longer than the "ARP log interval"
(`arp.interval_seconds`); the browser and the console (`arcilogger-db settings set`) both refuse a
change that breaks this. When you lower the interval below 300 seconds, change both settings in
one save. A time written outside this rule (by hand in SQL) is read by the lease job as the
default (300 seconds, at most the interval), with a warning in the worker log.

**Switching a source off** (`enabled = false`): the firewall removes the sender's 514 opening;
the silent-source alert (R15) and the sealer's gap check no longer expect it. A message that
still arrives from it is **recorded** (raw logs are never dropped). A switched-off source still
counts for the licence's source number. A **removed** source closes the same way; its row stays in
the database for the history, switched off and no longer expected, so R15 and the dashboard stop
waiting for it.

**Secret values** (the Kamu SM customer number and password, the SMTP and replica passwords)
never reach the screen: the fields show "Stored ✓" and the time of the last change; **Change**
writes a new one. The password is asked again (within the last 5 minutes) for the Kamu SM settings
(except the credit threshold and the timeout; the customer number and password included), every
SMTP, replica, retention and backup change, the clock sources and alert threshold, switching a
log source off or removing it; the application settings of sessions and passwords (`auth.*`),
privacy (`privacy.*`), the retention periods and the pseudonyms of the anonymous role, the alert
e-mail recipients and lowest severity; cancelling a held change, **Revert** and the diagnostics
package ([SECURITY.md](SECURITY.md) section 7).

The **Search and live tail** group (`search.*`, `live.*`; [ADR-0015](adr/0015-search-live-sources.md))
holds the limits of the Arama and Canlı Akış pages: every request has a time budget and every
user a share, so that no search or tail competes with loading the logs (ingest). The values are
starting values, not measured facts; no value can switch a limit or a budget off. Every request
reads them again (a live tail when it opens):

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `search.max_days` | 31 | 1–92 | longest range of one search (days, device time) |
| `search.page_size_max` | 500 | 50–1000 | most rows of one request (the web interface asks for 100) |
| `search.request_budget_ms` | 4000 | 500–30000 | time for rows; then what was found so far is shown with the interval scanned |
| `search.stats_budget_ms` | 8000 | 1000–60000 | time for the chart and the facets |
| `search.facet_sample_rows` | 100000 | 1000–1000000 | most matches for facets counted directly |
| `search.max_concurrent` | 3 | 1–10 | search requests running at once; at most `[database] pool_size` minus 2 |
| `search.runs_per_minute` | 30 | 1–600 | new searches per user and minute |
| `search.export_max_rows` | 100000 | 1–1000000 | most rows of one export file |
| `search.export_max_per_hour` | 10 | 1–100 | exports per user and hour |
| `search.export_budget_seconds` | 120 | 10–900 | time to build one export file |
| `search.saved_max_per_user` | 100 | 1–1000 | saved searches per user (personal) |
| `live.max_streams_total` | 4 | 1–20 | live tails open on the server at a time |
| `live.max_streams_per_user` | 2 | 1–10 | live tails open per user |
| `live.max_lines_per_second` | 100 | 10–500 | lines a second shown per tail; the rest are counted and reported |
| `live.parse_lines_per_second` | 1000 | 50–10000 | lines a second parsed for the live tails |
| `live.buffer_lines` | 5000 | 500–50000 | newest lines the server keeps for a resume |
| `live.max_minutes` | 60 | 5–480 | duration of one tail (unattended screen); Resume starts it again |
| `live.backfill_lines` | 50 | 0–200 | recent lines shown at once when a tail opens |

## 2. How it works

1. The page first shows the change for review (the changes, their effects, warnings: a dry run
   of the API, nothing is queued). **Save** makes the API check the change with the same rules
   (field limits, permission, a re-entered password where needed) and drop it as a signed
   request into `C:\ProgramData\ARCILOGGER\admin\inbox`. The API **cannot write** the
   configuration file, the secret store or the certificate folders.
2. The **ArciAdmin** service (`NT SERVICE\ArciAdmin`, "ARCILOGGER Yönetim (Admin)") checks the
   request's signature, expiry and rules again; where needed it first tries the connection with
   the new values (a Kamu SM credit query, a trial e-mail, the replica share, the time server:
   "Testing the connection"), keeps the previous version (`admin\versions`), writes the change
   and follows the services taking it. The services take it within seconds without a restart
   (the collector and ingest the log sources, the API and the worker the sections they read);
   the scheduled tasks at their next run.
3. The firewall, Windows Time and activating a Zamane client are the work of the task
   `\ARCILOGGER\ApplySystem`, which runs as **SYSTEM**. It takes no data from a request: it derives
   everything itself from the validated `arcilogger.toml` and from `admin\policy.json`, which only
   administrators can write, and checks every value again; ArciAdmin only wakes it (event 4101),
   and it also runs every 5 minutes on its own. In the firewall only the `ARCILOGGER Syslog UDP/TCP`
   rules (in strict outbound mode the `ARCILOGGER Out …` rules) are updated in place; no other
   rule, no profile, nor 443, 3389 or 5985 changes. For a new source, 514 opens once the collector
   knows it; for a source switched off or removed, it closes at once. The task sets the Windows
   Time peer list from `[clock]` at its first run too, even after an installation with
   `-SkipTimeSetup`; on a server whose time is managed another way (a domain, for example), switch
   the browser administration off with `-DisableBrowserAdministration` (section 6).
4. **Seal now** (Sealing page) and **Query credit** take the same channel: ArciAdmin writes a
   signed task request, and the task `\ARCILOGGER\Seal` (event 4110) or `\ARCILOGGER\Requests`
   (event 4111, LOCAL SERVICE) does the work. Sealing stays in its own task as always; the browser
   only brings the oldest unsealed day forward.
5. Every applied change goes into the audit log (`admin.request`, `admin.applied`, …; firewall,
   time and client changes as `admin.system`). Values are masked; a secret value, or a hash of
   one, is never written.

## 3. Status chip and request states

The chip in the Settings header gives ArciAdmin's state: **Administration service: running /
late / not running / switched off** (last answer younger than 30 seconds / younger than 2 minutes
/ older or none / browser administration switched off). While the service is not running, or the
browser administration is switched off (section 6), the machine settings are read-only and the
page names the console path; the application settings (database), the test e-mail and the lease
rebuild (worker) always work. **Seal now** and **Query credit** on the Sealing page go through
ArciAdmin as well and are unavailable then: the console path is the `\ARCILOGGER\Seal` or
`\ARCILOGGER\Credit` task in Task Scheduler (the nightly runs go on as always).

A request's state is shown on the page. The important ones:

| State | Meaning | What to do |
|---|---|---|
| **Written, services loading** | the file is written, the services are taking the new setting | wait (at most 2 minutes) |
| **Applied** | every service runs with the new setting | — ; with the warning "the administration service can no longer write the configuration files" (`acl_drift`) the change is in force, but the next one fails until you run `Set-ArciVaultAcl.ps1` (section 8) |
| **Done** | an action (sealing, credit query, a test) has finished; its result is in the request | — |
| **Held** | a guarded change, applied when the countdown ends (section 4); a Zamane client that is not a known release is never applied on its own: the page shows from when it can be approved on the console (`approve-jar`) | **Cancel** if needed |
| **Partly applied** | a service did not take the setting within 2 minutes, or the firewall could not be updated | the services and the **Security** page; ApplySystem tries again at its next run |
| **Connection test failed, nothing changed** | the server could not be reached with the new values | read the reason on the page, correct the value |
| **Not applied, the previous settings are in use** | a step failed, ArciAdmin put the previous version back | read the reason on the page, fix and save again; **Revert** for a change that was applied |
| **Manual action needed** | a step failed and putting the previous version back failed too (`rollback_failed`) | the steps on the page (section 8); note the version number |
| **Conflict: changed elsewhere** | the file changed meanwhile, by hand or by another request | reload the page and save again |
| **Rejected** | ArciAdmin did not accept the request (signature, expiry, a rule) | read the reason; a signature or installation error is an `R20-admin` alert |
| **Expired, nothing changed** | ArciAdmin did not take the request in time | check the service (section 8) |
| **Timed out** | no task (Seal, Requests) took the request within 15 minutes; it was withdrawn, nothing ran (a request the task took is waited for while the task may run it, e.g. a long Seal run) | the task's last result (Task Scheduler, `\ARCILOGGER\`) |

The short request number on the page is for support.

## 4. Held changes and the Zamane client

Most changes apply at once. These are **guarded**: they ask for a typed confirmation and raise an
`R20-config-change` alert (by e-mail when it is set up) and a Windows Event Log entry (event 4104):

| Change | Hold |
|---|---|
| switching retention on, or lowering its days | `hold_hours` of `admin\policy.json` (default 0: at once) |
| switching an enabled replica off, or changing its target | `hold_hours` (default 0) |
| a Zamane client that is not a known release | `approve-jar` on the console, at the earliest after `jar_hold_hours` (default 24) |

The holds are chosen at installation with `-AdminChangeHoldHours` and `-ZamaneHoldHours`
(section 6). A held change is stopped with **Cancel** on the **Change history** page or on its own
page.
First-time setup is never held (switching the replica on the first time, test → production).

**Zamane client:** a file uploaded in the Kamu SM wizard first waits in `zamane\web` and **never**
runs from there. If it is a known release, ApplySystem copies it into `zamane\approved`
(event 4107). An unknown release is only recorded (event 4104); an administrator approves it on
the console after comparing its SHA-256 with the value on the Kamu SM page. Time alone never makes
a client runnable. This version knows no Zamane release, so a client uploaded in the browser
always waits for `approve-jar`, at the earliest `jar_hold_hours` after the upload; meanwhile the
other steps of the wizard (Java, roots, credentials, the threshold) can be saved, and a second
client only once the first is approved or cancelled. To avoid the wait, install the client with
the installer's `-ZamaneJar` ([INSTALL.md](INSTALL.md) section 7). The command:

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
& $py -I -m arcilogger_core.admin approve-jar <SHA256> --config $cfg
```

## 5. What stays on the server console

These are deliberately not offered in the browser; each touches the evidence path or the server's
security directly:

- installation, upgrade, rollback, removal; PostgreSQL; a database restore;
- `[database]`, `[api]`, `[collector]`, `[ingest]`, `[worker]`, `[seal]`, `data_dir`,
  `secrets_dir` (`data_dir` must equal the installer's record
  `C:\ProgramData\ARCILOGGER\data-root.txt`; otherwise the services and tasks refuse the
  configuration and the installer stops in its preflight; to move the data folder deliberately,
  run the installer with `-DataRoot`);
- a provider other than Zamane, a free URL, port, digest, credit pattern, verify flag,
  `roots_dir`, a free `java` path; removing a root or a legal fingerprint;
- `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore`; the two database settings that name
  files, `devices.oui_file` and `usage.psl_file`;
- `admin\policy.json` (source networks, holds, trusted program folders, the off switch);
- the firewall's administrator addresses, strict outbound mode, WinRM and kept rules
  (`Set-ArciFirewall.ps1`);
- the database password, the usage pseudonym key, renewing `secret.key` and the administration
  keys; `approve-jar`; `setup-token` / `create-admin`;
- restarting a service after a hand edit of a key that is not reloaded live (section 7).

## 6. The installer's limits (`admin\policy.json`)

The installer writes the limits of the browser administration into
`C:\ProgramData\ARCILOGGER\admin\policy.json`, which only administrators and SYSTEM can change.
The values are chosen with installer parameters and remembered by later runs
([INSTALL.md](INSTALL.md) section 7.1):

| Parameter | Default | Meaning |
|---|---|---|
| `-SourceNetwork` | `10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16` | a log source's address must lie in one of these networks (at widest a /16) |
| `-AdminChangeHoldHours` | 0 | the hold of retention and replica changes (0–168 hours) |
| `-ZamaneHoldHours` | 24 | how long before an unknown Zamane client can be approved (1–168 hours) |
| `-DisableBrowserAdministration` | off | switches the browser administration off: ArciAdmin refuses every request, ApplySystem does nothing; the 5651 functions (collection, sealing, verification) are not affected at all. **Seal now** and **Query credit** are unavailable then (start the `\ARCILOGGER\Seal` or `\ARCILOGGER\Credit` task in Task Scheduler), and after a hand edit of `[[sources]]` run `Set-ArciFirewall.ps1` yourself (the **Sources** table then shows the firewall as **Not managed**) |

The installer compares every enabled log source of the configuration with these networks: when
one lies outside them it stops before any change and names the `-SourceNetwork` value to give
(otherwise ApplySystem would take that sender out of the 514 rule at its first run). Such a
source added by hand later stays open where the firewall has it open already and is not opened
otherwise; either way an `R20-admin` alert and the code `source_outside_policy` show it.

Running the installer again with `-Upgrade` and no parameter does not switch the browser
administration back on (the remembered choice applies); pass `-DisableBrowserAdministration:$false`.
The file can also be edited by hand: the installer keeps the hand-written values of a valid file;
an invalid file switches the browser administration off (the same meaning for every reader) and
the installer rewrites it with the defaults, with a warning.

The installer also records the programs the tasks run: a `java` outside Program Files, a
`[tsa] java` command with extra arguments, `pg_dump` / `pg_restore` (with their SHA-256) and the
Zamane client. The tasks run no other program; run the installer again when a recorded program
changes.

## 7. Hand edits and the browser together

**Use one way at a time.** `arcilogger.toml` can still be edited in Notepad; the browser notices
at the next save ("Conflict") and does not overwrite it. Hand edits are reloaded live too: the log
sources (collector, ingest) and the sections the API and the worker read need no restart. For the
sections that are not reloaded live (`[database]`, `[api]`, `[collector]`, `[ingest]`,
`[worker]`, `data_dir`, `secrets_dir`), restart the service concerned; the page shows "A change
waits for a restart". A hand edit is not listed on the **Change history** page ("The configuration
was changed by hand").
The first browser change of sources moves every `[[sources]]` table into a marked block; comment
lines inside those tables are lost then (the previous file stays in `admin\versions`).

**Secrets on the console.** `arcilogger-core secret set` / `secret delete` replace `secrets.json`
with a new file, which lacks ArciAdmin's write right. Afterwards run
`& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` in an administrator PowerShell (or
run the installer again); otherwise the next change made in the browser is applied with the
warning "the administration service can no longer write the configuration files" (`acl_drift`, a
critical `R20-admin` alert), and the change after it fails until the rights are repaired. The
installer does this itself after the values it stores (the database password, the Kamu SM
credentials at the end of the first installation).

## 8. Troubleshooting

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
Get-Service ArciAdmin                                    # running?
& $py -I -m arcilogger_core.admin status --config $cfg   # heartbeat, held changes, versions
& $py -I -m arcilogger_core.admin versions list --config $cfg
& $py -I -m arcilogger_core.admin recover --config $cfg  # finish an apply a crash interrupted
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'ARCILOGGER-Admin' } -MaxEvents 20
Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName ApplySystem   # bring the firewall in step now
```

- **The administration service is not running:** `Start-Service ArciAdmin`; its log is in
  `D:\ARCILOGGER\logs`. While it is stopped the 5651 functions go on; only the browser cannot
  change settings.
- **"Manual action needed"** (`rollback_failed`): put the previous version back on the console
  with the version number from the page (`& $py -I -m arcilogger_core.admin versions restore <N>
  --config $cfg`; the page shows the command with the full path); the services load the restored
  file by themselves. Restart a service that shows "A change waits for a restart".
- **For support:** **Download the diagnostics package** on the **Change history** page (with the
  password re-entered): the services' status files, the last log lines and the configuration,
  every secret replaced by `***`; no raw log, no vault, no export.
- **`acl_drift`:** the change was applied, but ArciAdmin can no longer write `arcilogger.toml` or
  `secrets.json` (most often after a console `secret set`, section 7): run
  `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` before the next change. Do not
  restore a version: that would undo the applied change and repair nothing.
- **The firewall could not be updated:** the last result of the ApplySystem task and event 4106;
  after an installation with `-SkipFirewall` the firewall is not ARCILOGGER's
  (`firewall_not_managed`) and its rules are kept by hand.
- Alerts: `R20-admin` ("Browser administration problem": a refused request, a stopped service, a
  failed sync, a rollback, manual action needed) and `R20-config-change` ("Significant
  configuration change": guarded changes) — [ALERTS.md](ALERTS.md) section 2.

Event IDs (source `ARCILOGGER-Admin`, Application log): 4100/4199 service started/stopping, 4101
system sync requested, 4102 applied, 4103 failed, 4104 held or a client recorded, 4105 cancelled,
4106 sync result, 4107 client promoted, 4110 seal request, 4111 task request, 4120 request refused,
4121 stored credential changed (its name only). An event only wakes a task; the authority is the
signed request.

## 9. Verify on site

Windows CI (the end-to-end job of the installer workflow, on a clean Windows server) tries:
ArciAdmin running as `NT SERVICE\ArciAdmin` and writing the Application log, the rights of 2.2
(the API cannot write the configuration, the secret store, `zamane` or the vault; ArciAdmin's own
entry stays on `arcilogger.toml` after it replaced the file), a site name change through Caddy
reaching "Applied", a source added in the browser reaching both syslog rules within 2 minutes,
**Seal now** making the Seal task (LOCAL SERVICE) seal a day, **Query credit** running in the
Requests task, and sealing going on after a rollback to a version without ArciAdmin. On site
also:

- that ArciAdmin can add and delete only its own files in `kamusm-roots`;
- that the event triggers wake the tasks without waiting for their 5-minute run (Seal, Requests,
  ApplySystem);
- that no syslog message is lost while the firewall rule is updated in place (with real devices);
- that a credit query costs no credit on the Kamu SM test server, and the customer number format;
- the format of the Zamane distribution (jar or zip), and that Java is found under
  `NT SERVICE\ArciAdmin`.

## 10. The API endpoints the pages use

The web interface uses only the endpoints below (all under `/api/v1`, with the session and CSRF
protection; the formats in detail: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) section 16). Every
endpoint that changes a machine setting or starts an action answers `202` with the request's
number and its `status_url`; the state is followed with `GET /admin/requests/{id}`. Every error
carries `code`, `message_tr` and `message_en`.

| Page | Endpoints |
|---|---|
| Status chip, every page | `GET /admin/status`, `GET /admin/config` (values, `etag`s, locked fields) |
| General | `POST /admin/config/general/check`, `PUT /admin/config/general` |
| Application settings | `GET /settings`, `PATCH /settings`, `POST /settings/{key}/reset`; `POST /admin/actions/lease-rebuild` |
| Kamu SM | `POST /admin/config/tsa/check`, `PUT /admin/config/tsa`; `POST /admin/uploads?kind=…` then `POST /admin/config/tsa/zamane`, `POST /admin/config/tsa/roots`, `POST /admin/config/tsa/legal-roots`; `POST /admin/actions/tsa-test` |
| Sources | `POST /admin/config/sources/check`, `POST /admin/config/sources`, `PUT /admin/config/sources/{name}`, `DELETE /admin/config/sources/{name}` |
| E-mail | `POST /admin/config/smtp/check`, `PUT /admin/config/smtp`, `POST /admin/config/smtp/ca`, `DELETE /admin/config/smtp/ca`; `POST /admin/actions/test-email`; the alert e-mail settings `PATCH /settings` |
| Replica | `POST /admin/config/replica/check`, `PUT /admin/config/replica`, `POST /admin/actions/replica-test` |
| Retention | `POST /admin/config/retention/check`, `PUT /admin/config/retention` |
| Backup | `POST /admin/config/backup/check`, `PUT /admin/config/backup` |
| Change history | `GET /admin/requests`, `GET /admin/requests/{id}`, `DELETE /admin/requests/{id}` (Cancel), `POST /admin/requests/{id}/revert` (Revert), `GET /admin/config/versions`, `GET /admin/diagnostics` |
| Sealing | `POST /seals/{day}/seal` (Seal now), `GET /credits`, `POST /credits/query` (Query credit) |

The application settings and the worker's actions (the test e-mail, the lease rebuild) go
through the database and do not wait for the administration service; everything else reaches
ArciAdmin as a signed request.
