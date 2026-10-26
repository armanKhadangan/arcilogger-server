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

The **Settings** pages are open to the **Administrator** role only (`settings.manage`;
`seal.manage` for **Seal now** and **Query credits**). Each page changes only the values below;
every other field is shown with a lock ("only the server console can change this value, for
security reasons", section 5).

| Page | Changed from the browser | Deliberately console only |
|---|---|---|
| **General** | organisation name (`site_name`), local and fallback time source (`[clock] ntp_server`, `[clock] fallback_server`), clock alert threshold (`[clock] alert_offset_ms`) | `[clock] ntp_port` |
| **Application settings** | every database setting (work hours, holidays, privacy, usage, alert e-mail recipients …) | — |
| **Kamu SM** | test / production server, Java (one of those found on the server), Zamane client (upload), root certificates (add), legal root fingerprints (add, installed roots only), customer number and password, credit alert threshold, timeout; `[seal]` read-only | the mock or another provider, a free URL or port, the digest, removing a root or a fingerprint, a free `java` path |
| **Sources** | add a log source, edit it (the name is fixed), switch it on or off (`[[sources]] enabled`), remove it | the whole `[collector]` section (bind address, ports, TLS, `unknown_senders`) |
| **E-mail** | `[smtp]` (on/off, server, port, security, user, sender, timeout, CA file upload), the SMTP password, the alert e-mail settings; **Send test e-mail** | — |
| **Replica** | on/off, target (`\\SERVER\share` only), user, password; **Test the connection** | a local folder as target |
| **Retention and deletion** | on/off, number of days (at least 730) | — |
| **Backup** | on/off, `[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly`, `[backup] min_free_gb` | `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore` |
| **History** | the requests and their states, versions, a link to the audit log | — |

No page changes `data_dir`, `secrets_dir`, `[database]`, `[api]`, `[ingest]`, `[worker]`,
`[seal]`, the database password or the usage pseudonym key. They belong to the installer and the
administrator.

**Switching a source off** (`enabled = false`): the firewall removes the sender's 514 opening;
the silent-source alert (R15) and the sealer's gap check no longer expect it. A message that
still arrives from it is **recorded** (raw logs are never dropped). A switched-off source still
counts for the licence's source number.

**Secret values** (the Kamu SM password, the SMTP and replica passwords) never reach the screen:
the fields show "Stored ✓" and the time of the last change; **Change** writes a new one. The password is
asked again (within the last 5 minutes) for the Kamu SM settings (except the credit threshold and
the timeout; the customer number and password included), every SMTP and replica change, the
retention settings, a clock source, switching the backup off, and switching a log source off or
removing it ([SECURITY.md](SECURITY.md) section 7).

## 2. How it works

1. **Save** makes the API check the change (field limits; the Kamu SM and SMTP connections are
   tried first where needed) and drop it as a signed request into
   `C:\ProgramData\ARCILOGGER\admin\inbox`. The API **cannot write** the configuration file, the
   secret store or the certificate folders.
2. The **ArciAdmin** service (`NT SERVICE\ArciAdmin`, "ARCILOGGER Yönetim (Admin)") checks the
   request's signature, expiry and rules again, keeps the previous version (`admin\versions`),
   writes the change and follows the services taking it. The services take it without a restart:
   the collector and ingest the log sources within 5 seconds, the API within 2 seconds, the worker
   within a minute; the scheduled tasks at their next run.
3. The firewall, Windows Time and activating a Zamane client are the work of the task
   `\ARCILOGGER\ApplySystem`, which runs as **SYSTEM**. It takes no data from a request: it derives
   everything itself from the validated `arcilogger.toml` and from `admin\policy.json`, which only
   administrators can write, and checks every value again; ArciAdmin only wakes it (event 4101),
   and it also runs every 5 minutes on its own. In the firewall only the `ARCILOGGER Syslog UDP/TCP`
   rules (in strict outbound mode the `ARCILOGGER Out …` rules) are updated in place; no other
   rule, no profile, nor 443, 3389 or 5985 changes. For a new source, 514 opens once the collector
   knows it; for a source switched off or removed, it closes at once.
4. **Seal now** (Sealing page) and **Query credits** take the same channel: ArciAdmin writes a
   signed task request, and the task `\ARCILOGGER\Seal` (event 4110) or `\ARCILOGGER\Requests`
   (event 4111, LOCAL SERVICE) does the work. Sealing stays in its own task as always; the browser
   only brings the oldest unsealed day forward.
5. Every applied change goes into the audit log (`admin.request`, `admin.applied`, …; firewall,
   time and client changes as `admin.system`). Values are masked; a secret value, or a hash of
   one, is never written.

## 3. Status chip and request states

The chip in the Settings header gives ArciAdmin's state: **administration service: running /
late / not running**. While the service is not running, or the browser administration is
switched off (section 6), the machine settings are read-only and the page names the console path;
the application settings (database) can always be changed.

A request's state is shown on the page. The important ones:

| State | Meaning | What to do |
|---|---|---|
| **Applied** | every service runs with the new setting | — |
| **Held** | a guarded change, applied when the countdown ends (section 4) | **Cancel** if needed |
| **Partly applied** | a service did not take the setting within 2 minutes, or the firewall could not be updated | the services and the **Security** page; ApplySystem tries again at its next run |
| **Not applied, the previous settings are in use** | a step failed, ArciAdmin put the previous version back | read the reason on the page, fix and save again |
| **Manual action needed** | putting it back failed too | section 8; note the version number |
| **Conflict: changed elsewhere** | the file changed meanwhile, by hand or by another request | reload the page and save again |
| **Expired, nothing changed** | ArciAdmin did not take the request in time | check the service (section 8) |

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
(section 6). A held change is stopped with **Cancel** on the **History** page or on its own page.
First-time setup is never held (switching the replica on the first time, test → production).

**Zamane client:** a file uploaded in the Kamu SM wizard first waits in `zamane\web` and **never**
runs from there. If it is a known release, ApplySystem copies it into `zamane\approved`
(event 4107). An unknown release is only recorded (event 4104); an administrator approves it on
the console after comparing its SHA-256 with the value on the Kamu SM page. Time alone never makes
a client runnable:

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
  `secrets_dir`;
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
| `-DisableBrowserAdministration` | off | switches the browser administration off: ArciAdmin refuses every request, ApplySystem does nothing; the 5651 functions (collection, sealing, verification) are not affected at all |

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
`[worker]`, `data_dir`, `secrets_dir`), restart the service concerned; the page shows "a hand edit
has not been loaded yet".
The first browser change of sources moves every `[[sources]]` table into a marked block; comment
lines inside those tables are lost then (the previous file stays in `admin\versions`).

## 8. Troubleshooting

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
Get-Service ArciAdmin                                    # running?
& $py -I -m arcilogger_core.admin status --config $cfg   # last requests, heartbeat
& $py -I -m arcilogger_core.admin versions list --config $cfg
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'ARCILOGGER-Admin' } -MaxEvents 20
Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName ApplySystem   # bring the firewall in step now
```

- **The administration service is not running:** `Start-Service ArciAdmin`; its log is in
  `D:\ARCILOGGER\logs`. While it is stopped the 5651 functions go on; only the browser cannot
  change settings.
- **"Manual action needed":** put the previous version back on the console with the version
  number from the page (`versions restore <N>`), then
  `Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker`.
- **The firewall could not be updated:** the last result of the ApplySystem task and event 4106;
  after an installation with `-SkipFirewall` the firewall is not ARCILOGGER's
  (`firewall_not_managed`) and its rules are kept by hand.
- Alerts: `R20-admin` (a refused request, a stopped service, a failed sync, manual action needed)
  and `R20-config-change` (guarded changes) — [ALERTS.md](ALERTS.md) section 2.

Event IDs (source `ARCILOGGER-Admin`, Application log): 4100/4199 service started/stopping, 4101
system sync requested, 4102 applied, 4103 failed, 4104 held or a client recorded, 4105 cancelled,
4106 sync result, 4107 client promoted, 4110 seal request, 4111 task request, 4120 request refused,
4121 stored credential changed (its name only). An event only wakes a task; the authority is the
signed request.

## 9. Verify on site

- that ArciAdmin can add and delete only its own files in `kamusm-roots`, and that a virtual
  account can write the Application log;
- that event triggers wake LOCAL SERVICE and SYSTEM tasks;
- that no syslog message is lost while the firewall rule is updated in place;
- that a credit query costs no credit on the Kamu SM test server;
- the format of the Zamane distribution (jar or zip), and that Java is found under
  `NT SERVICE\ArciAdmin`.
