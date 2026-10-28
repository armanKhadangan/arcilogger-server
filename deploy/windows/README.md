# Windows server deployment

ARCILOGGER is installed on one Windows server (or a Windows 11 Pro PC dedicated to it) from an
**offline bundle**; nothing is installed on user PCs. The bundle and `ARCILOGGER-Setup-<version>.exe`
are built on the admin PC or, for a `v*` tag, by the release job of
`.github/workflows/installer.yml`. Step-by-step guide: [docs/KURULUM.md](../../docs/KURULUM.md) (Turkish) /
[docs/INSTALL.md](../../docs/INSTALL.md) (English). Design: [ADR-0007](../../docs/adr/0007-windows-installer.md),
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).

```text
Admin PC (internet)                                   ARCILOGGER server (offline)
installer\Build-ArciBundle.ps1                        installer\Install-Arcilogger.ps1
  components.json -> download + SHA-256                 bundle checksums, preflight
  uv build, pip download --require-hashes   ──copy──►   Python + venv + wheels (--no-index)
  npm ci + npm run build                                 web, Caddy, WinSW services
  dist\ARCILOGGER-<version>\ + SHA256SUMS                rights, database, firewall, tasks
  (ARCILOGGER.iss -> ARCILOGGER-Setup.exe)               health check
```

| File | Purpose |
|---|---|
| `installer/components.json` | Third-party components: version, licence, licence text (tag-pinned URL + SHA-256), download URL, SHA-256 (only values computed from a downloaded file), role (bundled / operator-installed) |
| `installer/Build-ArciBundle.ps1` | Builds `dist\ARCILOGGER-<version>\` (with `licenses\` and `THIRD-PARTY-NOTICES.txt`) on a machine with internet, and the setup `.exe` when Inno Setup is installed (`-RequireInno`: stop without it); TimescaleDB only with `-IncludeTimescaleDB`. `bundle-manifest.json` carries `version` and `release_date` (`RELEASE_DATE` of `core/src/arcilogger_core/__init__.py`, the day a licence's maintenance must cover; `built_at` is the build machine's clock, informational only); the build stops when the ARCILOGGER wheels contain the licence test helpers (`arcilogger_core/license/testing.py`, `arcilogger_db/pytest_plugin.py`). The licence issuer (`tools/license-issuer`) is never part of the bundle |
| `installer/Install-Arcilogger.ps1` | Install, `-Upgrade`, `-Rollback` (with `-RestoreDatabase`), `-Uninstall` (only its own program folders; never data), `-DryRun` / `-WhatIf`; automatic recovery when a step fails; data root, server address and name chosen or remembered without parameters; the organisation's values without a default, asked at the console when missing: `-SiteName` (required on a first installation), `-NtpServer` (empty: only the public fallback, with a warning), `-AdminAddress` (remembered); setup token and Kamu SM credentials at the end of a first installation (console only); licence: `-LicenseFile`, `-AllowUncoveredUpgrade` and the request code at the end (see [Licence](#licence)); browser administration: `-SourceNetwork`, `-AdminChangeHoldHours`, `-ZamaneHoldHours`, `-DisableBrowserAdministration` (see [Browser administration](#browser-administration)) |
| `installer/ArciInstall.psm1` | Shared helpers: checksums, service SIDs, SCRAM verifier, rights plan, strict address/CIDR checks, collector bind and server address detection, data drive and operating system checks, program-folder markers, process runner (with a time limit), HTTPS health check, licence file / status file / release date readers and the maintenance comparison (no Python, no trust decision), the bundle's wheel check |
| `installer/ARCILOGGER.iss` | Inno Setup 6.3+ wrapper (Turkish and English) around the bundle: runs `Install-Arcilogger.ps1 -Auto`, plus `-LicenseFile "<full path>"` for `/LICENSEFILE="<file>"` and `-AllowUncoveredUpgrade` for `/ALLOWUNCOVEREDUPGRADE` on the setup's command line (see [Licence](#licence)) |
| `services/*.xml` | WinSW 2.12 definitions: `ArciCollector`, `ArciIngest`, `ArciApi`, `ArciWorker`, `ArciAdmin` (the venv's `python.exe -I -X utf8 -u -m arcilogger_<package>`, the agent `-m arcilogger_core.admin serve`), `ArciWeb` (Caddy) (`{{...}}` filled in by the installer); a definition whose wrapper is missing from a program folder is skipped with a warning (a rollback to a version without ArciAdmin) |
| `caddy/Caddyfile` | HTTPS on 443 with Caddy's internal CA, SPA, `/api` → `[api]` host:port (default 127.0.0.1:8000, written into `ArciWeb.xml` by the installer), security headers; compression for everything except the live tail's server-sent events under `/api/v1/live/` (compressing would hold events back; no `flush_interval`, so a closed browser tab also ends the API's stream — `tests/test_caddy_streaming.py`) |
| `tasks/*.xml` | Task Scheduler definitions: Seal (also on event 4110), VerifyDaily/Weekly/Monthly, Replica, Retention, Credit, ClockCheck, Backup (database dump, ADR-0009), Requests (event 4111 and every 5 minutes); each runs `python -I -m …`; ApplySystem (SYSTEM, event 4101 and every 5 minutes) runs `Sync-ArciSystem.ps1` |
| `scripts/Register-ArciTasks.ps1` | Registers the tasks under `\ARCILOGGER\` (LOCAL SERVICE, venv Python, isolated; ApplySystem as SYSTEM, the named exception), filling in `{{PYTHON}}` and `{{SCRIPTS}}` (`-ScriptDirectory`) |
| `scripts/Sync-ArciSystem.ps1` | ApplySystem (ADR-0014, contract 2.8.3): no input from a request; runs the read-only `system-plan` of the installed program, checks every value against `admin\policy.json`, then the firewall (`Set-ArciFirewall.ps1 -SyncFromConfig`), Windows Time (`Set-ArciTime.ps1`) and the promotion of known Zamane clients; `admin\system\status.json`, journal, Event 4106/4107; `-DryRun -PlanPath -ReportPath` for tests |
| `scripts/Set-ArciTime.ps1` | Time zone Türkiye; Windows Time → `[clock] ntp_server` (the local time source, usually the core router), `time.ume.tubitak.gov.tr` as fallback; without a local source the fallback only |
| `scripts/Set-ArciVaultAcl.ps1` | NTFS rights per service account, vault auditing (SACL); `-PlanOnly` prints them |
| `scripts/Set-ArciFirewall.ps1` | Windows Defender Firewall: 514 from the enabled `[[sources]]`, 443/3389 from the admin PC (`-AdminAddress`, no default), every other inbound allow rule disabled except Core Networking; `-StrictOutbound` (SMTP rule from `[smtp]`), `-AllowWinRM`, `-KeepRule`, `-SmtpServer` (remembered), `-Remove` (restores what it changed), `-DryRun`; `-SyncFromConfig` (ApplySystem): the syslog rules and, in strict outbound mode, the `ARCILOGGER Out …` rules updated in place after a check against `policy.json`, nothing else (exit 0 done, 2 refused, 3 not managed) |
| `scripts/Set-ArciPower.ps1` | Desktop Windows only: no sleep or hibernation on AC, Fast Startup off, network adapter power saving off; previous values recorded, `-Remove` restores them; `-DryRun` |
| `arcilogger.example.toml` | Example configuration: the installer's template for a new `arcilogger.toml` (no organisation name, no local time source, **no log source**); its `[[sources]]` are commented-out examples (the devices of a fictional example network): the administrator adds the organisation's own (`docs/DEVICE-SETUP.md` section 4) |

## Layout on the server

| Path | Content | Written by |
|---|---|---|
| `C:\Program Files\ARCILOGGER\` | `python\`, `venv\`, `web\`, `caddy\`, `services\` (WinSW + XML), `installer\`, `scripts\`, `tasks\`, `licenses\`, `THIRD-PARTY-NOTICES.txt`, `VERSION.json` (`INSTALL-IN-PROGRESS.json` while being written) | installer only (replaced on upgrade; previous kept as `ARCILOGGER.previous`, a failed new version as `ARCILOGGER.failed-<time>`) |
| `C:\ProgramData\ARCILOGGER\` | `arcilogger.toml`, `data-root.txt`, `secret.key` + `secrets.json`, `kamusm-roots\`, `smtp-ca\`, `zamane\` (`web\` staging, `approved\`), `caddy\` (internal CA), `install\` (protected: `state.json` with the remembered server address and name, `firewall-state.json`, `power-state.json` on desktop Windows), `admin\` (protected: `policy.json`, `keys\`, `inbox\`, `versions\`, `status\`, `journal\`, `system\`) | administrators; ArciAdmin writes `arcilogger.toml`, `secrets.json`, new files in `kamusm-roots\` / `smtp-ca\` and its `admin\` folders; read by services and tasks |
| `D:\ARCILOGGER\` (`C:\ARCILOGGER\` without a data disk) | `spool\` (with the protected `_audit\`), `vault\`, `exports\`, `state\` (with the protected `seal\`, `replica\`, `verify\`, `clock\`), `logs\`, `backups\db\` and `backups\scheduled\` (database dumps), `db\` (PostgreSQL) | services and tasks, each only where it needs to (ADR-0007 section 4) |

`state\system-history.json` holds one disk sample a day: the worker job `system.sample` (daily
after 00:30, `worker/src/arcilogger_worker/system_jobs.py`) writes `{"schema": 1, "days": [{"day",
"drives": [{"path", "total", "used"}]}]}` for the data drive and, when it is another device, the
system drive (drive letter, total and used bytes; nothing else), replaces an entry of the same day,
keeps the newest 120 days and writes the file atomically. ArciWorker writes it under the existing
rights of `state\` (no new rights entry); ArciApi reads it for the disk forecast of the Sistem page
and the Panel's disk card (linear over the newest 30 samples, shown from 7:
`api/src/arcilogger_api/system/forecast.py`). A damaged file is started again from the next sample
and the forecast waits for seven new ones; deleting it loses only the forecast. The installer
neither creates nor removes it (`-Uninstall` keeps the data folder).

## Services and accounts

| Service | Program | Account | Start |
|---|---|---|---|
| `ArciCollector` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_collector run --config …` | `NT SERVICE\ArciCollector` | automatic |
| `ArciIngest` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_ingest run --config …` | `NT SERVICE\ArciIngest` | automatic (delayed) |
| `ArciApi` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_api serve --config …` (`[api]` host:port, default 127.0.0.1:8000; `forwarded_allow_ips` must cover that host, which Caddy connects from: the preflight checks it) | `NT SERVICE\ArciApi` | automatic (delayed) |
| `ArciWorker` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_worker run --config …` | `NT SERVICE\ArciWorker` | automatic (delayed) |
| `ArciWeb` | `caddy run --config …\Caddyfile` | `NT SERVICE\ArciWeb` | automatic (delayed) |
| `ArciAdmin` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_core.admin serve --config …` (no listener, no database) | `NT SERVICE\ArciAdmin` | automatic (delayed); started last, stopped first |
| Task Scheduler `\ARCILOGGER\*` | `python -I -m arcilogger_core <command>`; Backup: `python -I -m arcilogger_db backup run`; Requests: `python -I -m arcilogger_core requests run` | LOCAL SERVICE (a shared Windows account: other LocalService services share its rights, ADR-0007 section 8) | per task |
| Task Scheduler `\ARCILOGGER\ApplySystem` | `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File …\scripts\Sync-ArciSystem.ps1` | SYSTEM (`HighestAvailable`) | event 4101, every 5 minutes |

All services restart on failure and write rolling logs to `D:\ARCILOGGER\logs`.

The Python services and tasks run the interpreter **isolated** (`-I`): no environment variable,
machine-wide or per service (`HKLM\SYSTEM\CurrentControlSet\Services\<id>\Environment`), can put
a `sitecustomize.py` or another module on their path (`PYTHONPATH`, `PYTHONSTARTUP`, user
site-packages). `-I` also ignores `PYTHONUTF8` and `PYTHONUNBUFFERED`, hence `-X utf8 -u`;
`ARCILOGGER_CONFIG` still applies. A `.pth` file dropped into the venv's site-packages is not
stopped by this (file tampering; a later signed file manifest is to cover it).

## Licence

The installer installs every program file, service, task and table whatever the licence says;
the product decides at run time which paid modules run, and the 5651 functions always run
(customer guide: `docs/LISANS.md` / `docs/LICENSING.md`).

| Parameter / step | Effect |
|---|---|
| `-LicenseFile <file.lic>` | Preflight (no Python): the file exists, is at most 65,536 bytes, is JSON (a byte order mark is tolerated) with `payload.license_id`, `payload.maintenance_until` (`YYYY-MM-DD`) and `payload.valid_until` (null or a date); otherwise the run stops with exit code 2 before any change. Step `license.install` right after the schema migration runs `arcilogger-db license install <file> --json --config …`: a refused licence (exit 1) or an unreachable database (exit 3) is a warning with the product's message, the installation goes on and ends with exit code 1, the product runs in its current licence state. With `-SkipDatabase` it is not installed and the command to run later is printed |
| Preflight `license` (install and upgrade) | The bundle's `release_date` (`bundle-manifest.json`; missing: stop) against the maintenance date of `-LicenseFile`, else of the installed licence as `<data_dir>\state\license-status.json` reports it (states `licensed`, `version_not_covered`). A release date after it (the date itself is covered) stops the run with exit code 2 before any change; running the bundle that is already installed (a repair) only warns. Without a licence it is information only; an upgrade without a licence whose vault holds sealed days older than 14 days warns that the trial counts as over (paid modules off, more than 3 active users: no new user) |
| `-AllowUncoveredUpgrade` | Installs a version the maintenance does not cover anyway, with 5651 Temel only (the paid modules stay off until a covering licence is installed), so a fix for a 5651 obligation can always be installed |
| `ARCILOGGER-Setup-<version>.exe /LICENSEFILE="<file>"`, `/ALLOWUNCOVEREDUPGRADE` | The setup's switches for the two parameters above (any case, `/` or `-`; read in `InitializeSetup`, passed on after `-Auto`). A relative `/LICENSEFILE` is resolved against the current folder, then the folder of the `.exe`, and the script always gets the full path in quotes (Windows file names cannot contain `"`); a `/LICENSEFILE` that names no file stops the setup before anything is copied. Without them the command line is `-Auto` only, as before |
| Next steps, step 6 | `arcilogger-db license status --json` and `license request-code` (5 s each): the licensee when licensed, otherwise the request code (not secret) to e-mail to the licensor, or the command to run later |

The rule is **advisory**: the licence file and the status file are read without checking any
signature and the customer can edit them. The product enforces the maintenance itself: a running
version whose release date is after `maintenance_until` is in the state `version_not_covered`
(paid modules off) whatever the installer did. `VERSION.json` records `release_date` too.

## Browser administration

The Settings pages of the web interface change the server's configuration (ADR-0014; operator
guide `docs/AYARLAR.md` / `docs/SETTINGS.md`; security `docs/SECURITY.md` section 14). The
installer prepares it on every run, idempotently:

| Step | Effect |
|---|---|
| `config.data-root` | `data-root.txt`: the data root every service and task checks `data_dir` against (Day 0 note D0-2), deleted and written anew from the record itself, `-DataRoot` or the choice of a first installation (`Resolve-ArciDataRoot`: a `data_dir` that differs from the record stops the preflight); protected by the ACL step |
| `executables.record` | the programs the tasks run outside the trusted folders (`tsa.java` with its whole command when it has more than one element, `backup.pg_dump`, `backup.pg_restore`) go into `policy.trusted_executables` with their SHA-256, only from folders nobody but administrators, SYSTEM and TrustedInstaller can write (else the preflight stops); a Zamane client outside `zamane\` is copied in and `tsa.zamane_jar` pointed at the copy; its hash is appended to `zamane\zamane-jar.sha256` |
| `secrets.file` | `secrets.json` (`{}`) before its file entry for ArciAdmin |
| `admin.protect`, `admin.keys` | `admin\` and `admin\keys\` protected (administrators and SYSTEM only) **before** `python -I -m arcilogger_core.admin keys init` writes the channel's keys (never replaced); the ACL step then grants each key its one reader |
| `admin.policy` | `admin\policy.json` (`New-ArciAdminPolicy`): the parameters, else the values of a valid existing file (hand edits are kept), else those remembered in `install\state.json`, else the defaults; an invalid file is rewritten with a warning |
| `admin.event-source` | the Application log source `ARCILOGGER-Admin` (`Register-ArciEventSource`; removed by `-Uninstall`) |
| `config.before-upgrade` | before an upgrade changes anything: `arcilogger.toml.before-upgrade-<UTC>`, its name in `state.json` (`config_before_upgrade`) |
| `acl.apply`, then `database.provision` | after the ACL step `secret set database.password` replaces `secrets.json`: `Restore-ConfigFileAccess` puts ArciAdmin's file entries back (also after the Kamu SM credentials at the end of a first installation) |
| `config.compatible` | a rollback (and the automatic recovery) first loads the configuration with the previous program's Python (`Test-ArciProgramConfig`); when that refuses it, the copy from before the upgrade is put back and the refused file kept as `arcilogger.toml.refused-<time>` |
| `system.restore` | a rollback (and the automatic recovery when the configuration was put back) runs the restored program's `Set-ArciFirewall.ps1` (full mode, the remembered choices) and `Set-ArciTime.ps1`: ApplySystem leaves with the newer tasks; a failure is a warning with the command to run |

## Server requirements

- Windows Server 2022 or 2025 Standard (recommended), or Windows 11 Pro/Enterprise/Education
  25H2 or later on a dedicated machine (docs/KURULUM.md section 17; Windows 10 and Home are not
  supported).
- Dedicated physical server: 4 cores, 16 GB RAM; `C:` ≥ 100 GB, `D:` ≥ 1 TB NTFS for logs
  (two mirrored disks recommended), connected to a UPS.
- PostgreSQL 16 installed with the EDB installer (TimescaleDB optional).
- A static IP address (or a DHCP reservation) in the network the devices send syslog to; the
  installer detects it or takes `-ServerAddress`.
- Network flows to open on the core router and the firewall: `docs/DEVICE-SETUP.md`.

Users connect with a browser to `https://<server address>`. Nothing is installed on user PCs.

## Tests

`tests/test_installer_scripts.py` checks `components.json`, parses every script with Windows
PowerShell 5.1 and PowerShell 7 (and rejects PowerShell 7-only syntax and script parameter
defaults that use `$PSScriptRoot`, which Windows PowerShell 5.1 leaves empty under
`powershell -File`: docs/adr/0007, section 7), runs the installer in dry-run mode against a fake
bundle, runs its real upgrade, repair, rollback, uninstall and recovery functions against
simulated Windows services and tasks (failures injected at the folder move, the program
installation, the schema migration and the task registration), checks the rights plan, the
firewall plan and the service definitions, runs the HTTPS health check against a local TLS 1.2
server and serves the Caddyfile with the pinned Caddy. The desktop and easier-install helpers
(server address detection, collector bind, data drive and operating system checks, powercfg
parsing) are pure functions checked with synthetic inputs; the power script runs in dry-run mode;
the end of a first installation (setup token, Kamu SM credentials on stdin) runs against simulated
programs. The licence part runs against fixture files and a simulated `arcilogger-db license`: the
licence file, status file and release-date readers, the maintenance rule in every mode (refused,
`-AllowUncoveredUpgrade`, a covering `-LicenseFile`, no licence, an edited status file, the
upgrade warning for old sealed days), the `license.install` step after the migration (a refused
licence is a warning, never a recovery), the request code in the next steps, the wheel check of
the build, and that every Python service and task runs isolated (`-I`; a `sitecustomize.py` on
`PYTHONPATH` is not loaded). Client Windows itself is not on the CI runners:
docs/KABUL-TESTI-WINDOWS11.md is the manual acceptance test. Windows PowerShell is started with
PowerShell 7's module folders removed from `PSModulePath`, as pwsh does when it starts
`powershell.exe` itself (pytest started from a pwsh window or a `shell: pwsh` CI step would
otherwise hand them on; about_PSModulePath); every script that imports `ArciInstall.psm1` must
call `Initialize-ArciModulePath` next, and on Windows an upgrade simulation runs with PowerShell
7's folders in front. It checks that the setup `.exe` accepts exit code 0 or 1 only when
`VERSION.json` names its bundle's fingerprint, and compiles `ARCILOGGER.iss` where Inno Setup 6.3+
is installed. On a Windows administrator runner it also applies the NTFS rights and the vault
auditing to temporary folders and reads them back. CI: `.github/workflows/installer.yml`
(Windows); its end-to-end job builds a real bundle with `Build-ArciBundle.ps1` and installs,
upgrades, rolls back, survives a broken upgrade and removes ARCILOGGER on the throw-away runner
(`ARCILOGGER_E2E_BUNDLE`; never set it on a machine you care about). With the runner's
PostgreSQL it also drives the browser administration through Caddy: "Şimdi mühürle" wakes the
Seal task (LOCAL SERVICE), a site name change reaches "active" and the rights of contract 2.2 are
read back (ArciAdmin's entry on `arcilogger.toml` kept by `ReplaceFileW`), a source added in the
browser reaches both syslog rules within 2 minutes, "Kontör sorgula" runs in the Requests task,
ArciAdmin's events are in the Application log, and the rollback to a program without ArciAdmin
removes it and its two tasks while the Seal task keeps running. Locally:
`ARCILOGGER_PWSH=<pwsh>`, `ARCILOGGER_CADDY=<caddy>` and `ARCILOGGER_ISCC=<ISCC.exe>` enable those
parts.

The browser administration's deployment runs on Linux too: every ACL row of contract 2.2 as pure
data (and the inheritance flags Windows gets), `New-ArciAdminPolicy` / `Read-ArciAdminPolicy`
against the core's `AdminPolicy` model, the event source helpers against a fake
`System.Diagnostics.EventLog`, the executable records and the Zamane copy checked afterwards with
`arcilogger_core.execpolicy`, the rollback check against a fake older venv, and dry runs of
`Set-ArciFirewall.ps1 -SyncFromConfig` and of `Sync-ArciSystem.ps1` (fast path, narrowing,
widening, refused values, jar promotion, a staged client behind a link) with the system plan
example of `core/tests/test_admin_contract.py`.
