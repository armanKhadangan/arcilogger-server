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
| `installer/Install-Arcilogger.ps1` | Install, `-Upgrade`, `-Rollback` (with `-RestoreDatabase`), `-Uninstall` (only its own program folders; never data), `-DryRun` / `-WhatIf`; automatic recovery when a step fails; data root, server address and name chosen or remembered without parameters; the organisation's values without a default, asked at the console when missing: `-SiteName` (required on a first installation), `-NtpServer` (empty: only the public fallback, with a warning), `-AdminAddress` (remembered); setup token and Kamu SM credentials at the end of a first installation (console only); licence: `-LicenseFile`, `-AllowUncoveredUpgrade` and the request code at the end (see [Licence](#licence)) |
| `installer/ArciInstall.psm1` | Shared helpers: checksums, service SIDs, SCRAM verifier, rights plan, strict address/CIDR checks, collector bind and server address detection, data drive and operating system checks, program-folder markers, process runner (with a time limit), HTTPS health check, licence file / status file / release date readers and the maintenance comparison (no Python, no trust decision), the bundle's wheel check |
| `installer/ARCILOGGER.iss` | Inno Setup 6.3+ wrapper (Turkish and English) around the bundle: runs `Install-Arcilogger.ps1 -Auto`, plus `-LicenseFile "<full path>"` for `/LICENSEFILE="<file>"` and `-AllowUncoveredUpgrade` for `/ALLOWUNCOVEREDUPGRADE` on the setup's command line (see [Licence](#licence)) |
| `services/*.xml` | WinSW 2.12 definitions: `ArciCollector`, `ArciIngest`, `ArciApi`, `ArciWorker` (the venv's `python.exe -I -X utf8 -u -m arcilogger_<package>`), `ArciWeb` (Caddy) (`{{...}}` filled in by the installer) |
| `caddy/Caddyfile` | HTTPS on 443 with Caddy's internal CA, SPA, `/api` → `[api]` host:port (default 127.0.0.1:8000, written into `ArciWeb.xml` by the installer), security headers |
| `tasks/*.xml` | Task Scheduler definitions: Seal, VerifyDaily/Weekly/Monthly, Replica, Retention, Credit, ClockCheck, Backup (database dump, ADR-0009); each runs `python -I -m …` |
| `scripts/Register-ArciTasks.ps1` | Registers the tasks under `\ARCILOGGER\` (LOCAL SERVICE, venv Python, isolated) |
| `scripts/Set-ArciTime.ps1` | Time zone Türkiye; Windows Time → `[clock] ntp_server` (the local time source, usually the core router), `time.ume.tubitak.gov.tr` as fallback; without a local source the fallback only |
| `scripts/Set-ArciVaultAcl.ps1` | NTFS rights per service account, vault auditing (SACL); `-PlanOnly` prints them |
| `scripts/Set-ArciFirewall.ps1` | Windows Defender Firewall: 514 from `[[sources]]`, 443/3389 from the admin PC (`-AdminAddress`, no default), every other inbound allow rule disabled except Core Networking; `-StrictOutbound` (SMTP rule from `[smtp]`), `-AllowWinRM`, `-KeepRule`, `-SmtpServer` (remembered), `-Remove` (restores what it changed), `-DryRun` |
| `scripts/Set-ArciPower.ps1` | Desktop Windows only: no sleep or hibernation on AC, Fast Startup off, network adapter power saving off; previous values recorded, `-Remove` restores them; `-DryRun` |
| `arcilogger.example.toml` | Example configuration: the installer's template for a new `arcilogger.toml` (no organisation name, no local time source, **no log source**); its `[[sources]]` are commented-out examples (the devices of a fictional example network): the administrator adds the organisation's own (`docs/DEVICE-SETUP.md` section 4) |

## Layout on the server

| Path | Content | Written by |
|---|---|---|
| `C:\Program Files\ARCILOGGER\` | `python\`, `venv\`, `web\`, `caddy\`, `services\` (WinSW + XML), `installer\`, `scripts\`, `tasks\`, `licenses\`, `THIRD-PARTY-NOTICES.txt`, `VERSION.json` (`INSTALL-IN-PROGRESS.json` while being written) | installer only (replaced on upgrade; previous kept as `ARCILOGGER.previous`, a failed new version as `ARCILOGGER.failed-<time>`) |
| `C:\ProgramData\ARCILOGGER\` | `arcilogger.toml`, `secret.key` + `secrets.json`, `kamusm-roots\`, `zamane\`, `caddy\` (internal CA), `install\` (`state.json` with the remembered server address and name, `firewall-state.json`, `power-state.json` on desktop Windows) | administrators; read by services and tasks |
| `D:\ARCILOGGER\` (`C:\ARCILOGGER\` without a data disk) | `spool\` (with the protected `_audit\`), `vault\`, `exports\`, `state\` (with the protected `seal\`, `replica\`, `verify\`, `clock\`), `logs\`, `backups\db\` and `backups\scheduled\` (database dumps), `db\` (PostgreSQL) | services and tasks, each only where it needs to (ADR-0007 section 4) |

## Services and accounts

| Service | Program | Account | Start |
|---|---|---|---|
| `ArciCollector` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_collector run --config …` | `NT SERVICE\ArciCollector` | automatic |
| `ArciIngest` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_ingest run --config …` | `NT SERVICE\ArciIngest` | automatic (delayed) |
| `ArciApi` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_api serve --config …` (`[api]` host:port, default 127.0.0.1:8000; `forwarded_allow_ips` must cover that host, which Caddy connects from: the preflight checks it) | `NT SERVICE\ArciApi` | automatic (delayed) |
| `ArciWorker` | `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_worker run --config …` | `NT SERVICE\ArciWorker` | automatic (delayed) |
| `ArciWeb` | `caddy run --config …\Caddyfile` | `NT SERVICE\ArciWeb` | automatic (delayed) |
| Task Scheduler `\ARCILOGGER\*` | `python -I -m arcilogger_core <command>`; Backup: `python -I -m arcilogger_db backup run` | LOCAL SERVICE (a shared Windows account: other LocalService services share its rights, ADR-0007 section 8) | per task |

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
(`ARCILOGGER_E2E_BUNDLE`; never set it on a machine you care about). Locally:
`ARCILOGGER_PWSH=<pwsh>`, `ARCILOGGER_CADDY=<caddy>` and `ARCILOGGER_ISCC=<ISCC.exe>` enable those
parts.
