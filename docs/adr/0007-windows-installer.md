# ADR-0007: Windows installer — offline bundle, WinSW services, operator-installed PostgreSQL

- Status: accepted (2026-10-12); amended 2026-10-19 (desktop Windows, release build; proposed)
- Related: [ADR-0001](0001-windows-server-browser-only.md), [ADR-0002](0002-own-syslog-collector.md),
  [ADR-0003](0003-integrity-on-windows.md)

## Context

ARCILOGGER runs on one dedicated Windows server (Windows Server 2022/2025). The server has **no
general internet access** — only the Kamu SM time-stamp and certificate, NTP and DNS flows of
DEVICE-SETUP.md section 1 (assumption A3) — and the installer itself needs **no network at all**;
people use only a browser, and nothing may be
installed on user PCs. Third-party binaries must never be committed to the repository; everything
that is downloaded must be checked against a SHA-256 that was really verified. Secrets (the
PostgreSQL superuser password, ARCILOGGER's own database password, Kamu SM credentials) must never
appear in plain text in files, logs or command lines.

## Decision

### 1. Two scripts and an offline bundle

- `deploy/windows/installer/Build-ArciBundle.ps1` runs on a Windows machine **with** internet (the
  admin PC or a CI runner). It downloads the components in `components.json`, refuses any
  file whose SHA-256 differs, builds the ARCILOGGER wheels (`uv build`), downloads every dependency
  wheel pinned in `uv.lock` for `win_amd64` / CPython 3.12 with `pip download --require-hashes`
  (run by the very Python that is shipped, so environment markers are evaluated for the server),
  builds the web interface and writes `dist/ARCILOGGER-<version>/` with `bundle-manifest.json`
  (every file and its SHA-256) and `SHA256SUMS`. The SHA-256 of `SHA256SUMS` is the **bundle
  fingerprint**; the build prints it and the installer prints it again on the server.
- `deploy/windows/installer/Install-Arcilogger.ps1` runs **on the server, offline**, as
  Administrator. It verifies the bundle, runs a preflight, and installs, upgrades, rolls back or
  removes ARCILOGGER. Every step is idempotent and `-DryRun` / `-WhatIf` prints (and with
  `-PlanPath` writes as JSON) the plan without changing anything; a dry run exits with the code
  the real run would return at the preflight (2 if it would stop). Everything the later steps
  could refuse (admin addresses, `[[sources]]`, an open remote-desktop session that the firewall
  would cut off) is checked in the preflight, before any change.
- `ARCILOGGER.iss` (Inno Setup 6.3+) only wraps the bundle into `ARCILOGGER-Setup-<version>.exe`
  and runs the same script (`-Auto`: install or upgrade). Installing with or without the `.exe`
  gives the same result.

### 2. Components

| Component | Choice | Why |
|---|---|---|
| Python 3.12 | python-build-standalone `install_only` archive (Astral), checksum from its `SHA256SUMS` | Relocatable, no MSI or registry, contains `venv` and `pip`; python.org could not be reached for verification from the build environment |
| Service wrapper | **WinSW 2.12.0, `WinSW.NET461.exe`** | MIT, XML-configured, restart-on-failure, log rolling. The `WinSW-x64.exe` of the same release embeds its own .NET 6.0.13 runtime (unsupported since 2024-11, never patched by Windows Update); the .NET 4.6.1 build runs on the .NET Framework 4.8 that Windows Server 2022/2025 ships and Windows Update services. NSSM was rejected (no release since 2017); writing `pywin32` services was rejected because Caddy needs a wrapper anyway |
| Web server | Caddy 2.11.7 (Apache-2.0) | HTTPS with its own internal CA, static files, reverse proxy, security headers in one small binary; vendor SHA-512 also checked |
| Database | PostgreSQL 16, **installed by the operator** with the EDB installer | See section 3 |
| TimescaleDB | Optional, operator-installed (official Windows zip 2.30.2 exists); added to the bundle only with `Build-ArciBundle.ps1 -IncludeTimescaleDB` | ARCILOGGER works on plain PostgreSQL. The `-tsl` part is under the Timescale License (not OSI): owner decision needed, so it is not shipped by default |
| Java | Eclipse Temurin 8 JRE MSI, operator-installed (for the Kamu SM Zamane client only) | Copied into the bundle for offline use; installed with `INSTALLDIR=...\jre-8\` so the path in `arcilogger.toml` survives Java updates |
| Zamane jar | Downloaded by the administrator from Kamu SM, never bundled | `-ZamaneJar` shows its SHA-256, compares it with `-ZamaneJarSha256` or asks for confirmation against the Kamu SM page, and records it |

A checksum appears in `components.json` only if it was computed from a file actually downloaded
(and compared with the vendor's checksum file where one exists). PostgreSQL's installer could not
be downloaded from the build environment: its `sha256` is `null`, and the build refuses it unless
the operator passes `-ComponentUrl` and `-ComponentSha256` after checking the vendor site.

Licence texts travel with the programs: every component that goes into the bundle has a
tag-pinned `licence_url` and a `licence_sha256` (computed from the downloaded text). The build
downloads and checks them into `licenses\`, writes `THIRD-PARTY-NOTICES.txt`, and the installer
copies both into `C:\Program Files\ARCILOGGER` (MIT and Apache-2.0 require the notice with every
copy; WinSW is installed once per service). Caddy's and CPython's own licence files also stay
inside their archives.

### 3. PostgreSQL is installed by the operator

The EDB installer creates the service account, the data folder, `pg_hba.conf`, the Windows service
and the uninstaller, and it is how PostgreSQL itself is upgraded (minor releases every quarter).
Running it unattended would put the superuser password on a command line. ARCILOGGER therefore only
**configures** an existing PostgreSQL 16: the preflight stops with Turkish instructions when the
service is missing; the installer asks for the superuser password **once** (or takes
`-PgSuperuserCredential`), never stores or logs it, and passes it to `psql` / `pg_dump` only in
the child process's environment. It generates a 40-character random password for ARCILOGGER's
own role, writes it to the encrypted secret store on **stdin** (`arcilogger-core secret set
database.password`), and sends PostgreSQL only its **SCRAM-SHA-256 verifier**
(`CREATE ROLE ... PASSWORD 'SCRAM-SHA-256$4096:...'`) — the plain password never reaches the
server, its statement log or its error log. The database is created with `ENCODING 'UTF8'`,
`LOCALE 'C'` (amendment 2026-10-19) and `REVOKE ALL ... FROM PUBLIC`. TimescaleDB is enabled only
if it is installed and preloaded.

### 4. Virtual service accounts and least privilege

Each service runs as `NT SERVICE\<name>` (`ArciCollector`, `ArciIngest`, `ArciApi`, `ArciWorker`,
`ArciWeb`): no password to manage, and its own SID for NTFS rights. WinSW 2.12 documents no option
for virtual accounts, so the installer sets them with `sc.exe sidtype <name> unrestricted` and
`sc.exe config <name> obj= "NT SERVICE\<name>"`. The scheduled evidence jobs keep running as
`LOCAL SERVICE` (M1 task definitions).

The installer writes rights by **SID**, computing the service SIDs itself (SHA-1 of the upper-case
service name, as Windows does — tested against the known `TrustedInstaller` SID). That works
before the services exist and on a Turkish Windows, where group names are localised.

| Folder | Who may do what |
|---|---|
| `D:\ARCILOGGER` | SYSTEM, Administrators full; services and tasks may only list this folder; inheritance from the drive removed (no `Users`) |
| `spool` | collector **read + write, no Delete**: it cannot delete or rename raw files (it can write into a file it opens — Python's append mode needs the write-data right — so the nightly seal, not the ACL, proves the content); ingest read; API read (`GET /raw`, the 5651 report and the sealing calendar read days not sealed yet; no write or delete); worker **list only** (`ListDirectory, ReadAttributes, Traverse`, inherited by sub-folders, not by files): it finds the oldest spool day by file name for the licence decision like the API — the trial's `spool` lower bound, licensing contract 3.4 — and never reads a raw file; tasks (seal) modify |
| `spool\_audit` | **protected** (the collector's spool rights do not reach it): API read + write without Delete (it appends; it may cut a torn last line); tasks (seal) modify; ingest read |
| `vault` | tasks modify (seal, retention); API, worker and ingest read (ingest catches up on days sealed while the database was down, ADR-0002); SACL audit of writes, deletes and permission changes for Everyone |
| `state` | collector, ingest, worker, tasks modify (status, alerts; ingest offsets are in the database); API read |
| `state\seal`, `state\replica`, `state\verify`, `state\clock` | **protected**: tasks modify only; API and worker read (ingest also `state\seal`). Core trusts `state\seal\<day>.json` ("sealed") when it picks the days to seal: a service that could write it could stop a day from ever being sealed |
| `exports` | API, worker modify |
| `logs` | all ARCILOGGER accounts modify |
| `backups` | Administrators only (also `backups\db`, the dumps before upgrades); `backups\scheduled` protected: tasks modify, no service ([ADR-0009](0009-database-backup.md)) |
| `C:\ProgramData\ARCILOGGER` | services and tasks read; inheritance removed |
| `...\secret.key` | ingest, API, worker, tasks read — **not the collector** (it parses untrusted input and needs no secret) |
| `...\caddy` | ArciWeb only (its internal CA private key) |

Explicit entries on sealed files — the deny entries of `arcilogger_core.immutability` — are never
removed; only the explicit entries of the listed folders are replaced. If the PostgreSQL data folder
lies inside `D:\ARCILOGGER`, its service account keeps full control there.

### 5. Network

`Set-ArciFirewall.ps1` (spec 13: 514 from the senders, 443 and remote administration only from the
admin PC, nothing else; `-AdminAddress`, without a default since 2026-10-20): default inbound
**Block**; UDP/TCP 514 only from the `[[sources]]` of `arcilogger.toml`; TCP 443 and TCP/UDP 3389
only from the admin PC (TCP 5985 for PowerShell remoting too with `-AllowWinRM`). Because an enabled
allow rule always wins over the default action, **every other enabled inbound allow rule is disabled
and recorded** — Windows' own "Remote Desktop", "Windows Remote Management (HTTP-In)" (enabled by
default on Windows Server), "File and Printer Sharing" and whatever other programs added — except
the rule groups in `-KeepRuleGroup` (default "Core Networking": DHCP, IPv6 neighbour discovery, ICMP
errors) and the rules named in `-KeepRule`. The script stops before any change if no rule of those
groups exists (the group's name is an on-site assumption), and it refuses to cut off an open
remote-desktop session from an address outside `-AdminAddress` (CIDR-aware) unless `-Force`.
Addresses are validated strictly (four decimal octets, optional prefix; .NET's `TryParse` alone
accepts shorthand such as `10.1.2`, which it reads as 10.1.0.2).

The strict outbound mode is optional (default action Block; NTP, DNS, Kamu SM — both servers plus
the host and port of `[tsa]` — the certificate host, SMTP and the replica host; other enabled
outbound allow rules are disabled and recorded like the inbound ones; rules need IP addresses, so
host names are resolved when the script runs). The state file
(`C:\ProgramData\ARCILOGGER\install\firewall-state.json`) records the disabled rules, the profile
settings found before the first run and the operator's choices (strict mode, admin addresses,
WinRM, kept rules): a later run that does not name them — an upgrade, for example — keeps them,
and `-Remove` restores the rules and profile settings exactly. Caddy listens on 443 only (no port
80 redirect), has its admin API switched off and does not touch the server's trust store; its root
certificate is installed on the admin PC.

### 6. Upgrade and rollback

`-Upgrade`: verify bundle → preflight → `pg_dump -Fc` to `D:\ARCILOGGER\backups\db` (checked with
`pg_restore --list`; a failed backup stops the upgrade with exit 2 before any change) → pause the
scheduled tasks and wait for running ones → stop services (collector last) → move
`C:\Program Files\ARCILOGGER` to `ARCILOGGER.previous` (while the services are still registered,
so a refused move changes nothing else) → remove the service registrations → fresh program folder
→ configuration, rights, role, services → **start the collector and Caddy** (they need no
database, so UDP syslog is lost only while the program and its rights are replaced) → time,
firewall, tasks → **`arcilogger-db upgrade` as the last change** → start ingest, API and worker →
health check (polls the API at `[api]` host:port — Caddy's upstream follows the same setting — and
Caddy for up to `-HealthWaitSeconds`, 90 s, before warning).

Everything after the backup runs inside one recovery block:

- **A failure before the migration** (including a refused folder move, a failed program
  installation, a firewall or task error) puts the system back: the half-installed folder is set
  aside as `ARCILOGGER.failed-<time>`, `ARCILOGGER.previous` becomes the program folder again, its
  services are registered and started, the paused tasks are enabled again (those the operator had
  switched off stay off). Exit code 3, "previous state restored".
- **A failure during the migration**: the schema may be half-migrated, so the previous program
  never runs against it. The previous program folder comes back, but only `ArciCollector`,
  `ArciWeb` and the scheduled evidence jobs run — none of them needs the database, so no syslog is
  lost and sealing goes on. `ArciIngest`, `ArciApi` and `ArciWorker` are set to manual start and
  stay stopped. The installer prints the exact `-Rollback -RestoreDatabase -DatabaseDump <dump>`
  command and records the dump in `install\state.json` (`pending_database_restore`).
- Whatever the recovery could not do is listed as "manual action needed" and recorded in
  `install\state.json` (`last_failure`).

`-Rollback` swaps `ARCILOGGER.previous` back on request (both renames or neither); with
`-RestoreDatabase` it also restores the pre-upgrade dump (`pg_restore --clean --single-transaction`,
so a failed restore leaves the database as it was; TimescaleDB's `timescaledb_pre_restore()` /
`post_restore()` around it — verify on site). Without a previous program folder,
`-Rollback -RestoreDatabase` restores only the database: it stops the three database services
(the collector keeps running), restores, sets them back to delayed automatic start and starts them.
A repair (the same bundle installed again) recovers the same way. A fresh installation that stops
while copying program files leaves an `INSTALL-IN-PROGRESS.json` marker; running the installer (or
the setup `.exe`) again removes that folder and starts over. The configuration, the secret store
and all data stay where they are in every case; the program folder is the only thing replaced.

`-Uninstall` first pauses the scheduled tasks and waits for running ones like an upgrade (they run
`python.exe` from the program folder, which Windows would not let it delete; a task still running
after `-TaskWaitMinutes` stops the removal with exit code 2 before any change). It then removes
services, tasks, the firewall rules (restoring the recorded rules and profile settings) and only
folders this installer created (each one's `installer\` and `VERSION.json` last, so a refused file
leaves the script that finishes the removal) — `<InstallDir>`, `.previous`, `.failed-*`,
`.rolled-back-*` holding its `VERSION.json` or `INSTALL-IN-PROGRESS.json`. It refuses, before any
change, a folder without them, a drive root, a Windows folder, the data root and the configuration
folder.

The tests run the real upgrade, repair, rollback, uninstall and recovery functions of the
installer (dot-sourced) against simulated Windows services and scheduled tasks on Linux and
Windows CI, with failures injected at the folder move, the program installation, the migration
and the task registration.

### 7. Windows PowerShell 5.1 rules

The first run on a Windows runner (CI run 38310210886, commit 343dea6) showed three differences
from PowerShell 7 that the Linux tests could not see. The scripts follow these rules:

- **No `$PSScriptRoot` in a script's parameter defaults.** Windows PowerShell 5.1 left it empty
  while evaluating `[string]$BundleRoot = (Split-Path -Parent $PSScriptRoot)` of a script started
  with `powershell -File` ("Split-Path : Cannot bind argument to parameter 'Path' because it is an
  empty string"), so the installer stopped before its first line with exit code 1 — the way the
  setup `.exe` starts it, too. Such defaults are set in the script body (`-BundleRoot`,
  `Build-ArciBundle.ps1 -RepoRoot`, `Register-ArciTasks.ps1 -TaskDirectory`); the parse test rejects
  `$PSScriptRoot`, `$PSCommandPath` and `$MyInvocation` in script parameter defaults.
- **PowerShell 7's module folders are removed from `PSModulePath`.** pwsh removes its user,
  system and `$PSHOME` module folders from the `PSModulePath` it hands to a `powershell.exe` it
  starts itself, but a `powershell.exe` started by another program that pwsh started (cmd.exe,
  Python, a setup `.exe`, pytest in a CI step with `shell: pwsh`) inherits pwsh's unmodified
  module path. Microsoft documents both in about_PSModulePath, section "Starting Windows
  PowerShell from PowerShell 7" (PowerShell 7.4 and 7.5 reference, MicrosoftDocs/PowerShell-Docs,
  read on 2026-10-12). That `powershell.exe` then finds the PowerShell 7 copies of its own modules
  first: "The 'ConvertTo-SecureString' command was found in the module
  'Microsoft.PowerShell.Security', but the module could not be loaded" (`Get-Acl` likewise). A
  console opened from the Start menu normally does not have these folders; one started from a
  pwsh window through another program (for example the setup `.exe`) does. Every script calls
  `Initialize-ArciModulePath` right after importing `ArciInstall.psm1`, which removes them in
  Windows PowerShell (PowerShell 7's user and all-users folders and every folder next to a
  `pwsh.dll`); the test harness starts `powershell.exe` with the same clean module path (Microsoft's
  example removes the variable altogether; filtering keeps folders other software added to the
  machine-wide path). A test checks the call in every script, and on Windows an upgrade
  simulation runs with PowerShell 7's folders in front.
- **The health check names TLS 1.2.** Caddy accepts TLS 1.2 and 1.3 only; .NET Framework's
  `SslStream` does not necessarily offer TLS 1.2 unless asked to.

An exit code of 1 therefore does not prove an installation: PowerShell returns 1 as well when a
script stops at a parse or parameter error. The documented codes (0 done, 1 done with warnings,
2, 3) stay; instead the setup `.exe` hashes the `SHA256SUMS` of the bundle it carries (the bundle
fingerprint, `GetSHA256OfFile`, Inno Setup 6.3+) before it starts the installer and, after exit
code 0 or 1, reports a failure unless `C:\Program Files\ARCILOGGER\VERSION.json` names that
fingerprint. This also catches an upgrade that never started (the previous version's file names
the previous bundle). It cannot tell a repair of the same bundle that stopped at a parse error
from a successful one, but a bundle whose installer cannot be parsed could not have been
installed in the first place. The end-to-end test checks `VERSION.json` and prints the installer
output of every run.

### 8. Open design points (owner decision)

- **Scheduled tasks run as the shared `LOCAL SERVICE` account** (M1 task definitions; a known
  limitation of this release, reviewed again in the final MVP review). Every Windows service that
  runs as LocalService (W32Time, EventLog, the firewall service, DHCP client, …) — or code that
  compromises one — therefore holds the tasks' rights: Modify on `spool`, `spool\_audit`,
  `vault`, `state` and the protected `state\seal`, `state\replica`, `state\verify`,
  `state\clock`, and Read on `secret.key`, which decrypts `secrets.json` (database password,
  Kamu SM customer number and password) and, with the database, the TOTP secrets. Files the Seal
  task creates are owned by LOCAL SERVICE, so such a process also has the owner's implicit right
  to change their ACL and could remove the deny entries of sealed files (the vault SACL still
  records it). The separation between the ARCILOGGER services (virtual accounts) is unaffected.
  Not changed in the MVP because both fixes change the identity the evidence jobs run as and
  must be verified on Windows Server 2022/2025 before they ship (a wrong SID stops sealing):
  - **Per-task SIDs**: `<ProcessTokenSidType>Unrestricted</ProcessTokenSidType>` in every task
    XML, the rights granted to the `NT TASK\…` SIDs (S-1-5-87-…) instead of S-1-5-19, an
    inheritable `OWNER RIGHTS` (S-1-3-4) read entry on `vault` plus explicit ChangePermissions for
    Seal and Retention only. To verify: how Windows derives the task SID (task name only or the
    `\ARCILOGGER\` path; `schtasks /showsid /tn "\ARCILOGGER\Seal"` against a token of a running
    task), that `immutability.lock`/`unlock` still work, and that a plain LocalService task is
    then refused `secret.key` and writes to `vault`/`state\seal`.
  - **A dedicated local account** (like the spec's Linux users `arci-vault`/`arci-tsa`): created by the
    installer with a random password stored only by Task Scheduler, "Log on as a batch job", no
    interactive/network/RDP logon; its SID replaces S-1-5-19 everywhere and owns the sealed files.
    To verify: password expiry policy, group policy on the server, Replica to the NAS share.
  Also open: the network-facing collector has Modify on all of `state` (it needs only its own
  status file); a `state\collector` folder needs a change of `Layout.collector_status` in core.
  The Windows end-to-end CI job runs the Seal task through Task Scheduler as LOCAL SERVICE under
  the installed rights, so either change is tested there before it reaches the server.
- **Ingest after a database outage across 00:10**: sealing deletes the spool day after sealing
  (core `seal.py`) — **resolved**: ingest continues such a day from the sealed `.gz` in the vault
  at the same byte offset (`arcilogger_ingest.sealed`, docs/INGEST.md "Vault continuation"),
  using the vault read right the installer grants.
- **Replica as LOCAL SERVICE** reaches SMB anonymously. Resolved in core: with `replica.user` set
  the replica connects to the share with the stored secret `replica.password`
  (`WNetAddConnection2W`, no drive letter, never remembered) and disconnects afterwards, so it
  works under any task account and survives upgrades; `arcilogger-core replica --test` checks it.

## Consequences

- One offline folder (or one `.exe`) installs the whole server; the build machine needs internet,
  uv, Node.js 22 / npm 11.21 and optionally Inno Setup.
- The operator installs PostgreSQL (and optionally TimescaleDB and Temurin) with the vendors'
  installers; ARCILOGGER documents the exact steps (`docs/KURULUM.md`).
- UDP syslog sent while the collector is stopped during an upgrade (while the program files and
  rights are replaced; it starts again before the schema migration) is lost; upgrades belong in a
  maintenance window. Only TCP senders retransmit, and the site's devices send UDP today.
- Inbound, the server answers only ARCILOGGER's rules and Core Networking. A monitoring or
  backup agent that needs an inbound port must be named with `-KeepRule` (remembered).
- Antivirus exclusions (Microsoft Defender: the PostgreSQL data folder and process) are set by
  the operator, not by the installer (KURULUM.md section 2).
- Verify on site: the WinSW virtual-account setup (`sc.exe config obj=`) and "Log on as a service"
  for `NT SERVICE\ALL SERVICES`; the EDB installer's rights on `D:\ARCILOGGER\db`; the `auditpol`
  subcategory GUID for "File System"; Inno Setup's `Turkish.isl`; the Temurin MSI `INSTALLDIR`
  property; TimescaleDB `setup.exe` with the installed PostgreSQL 16 minor version and its
  `timescaledb_pre_restore()` / `post_restore()` for restores; the "Core Networking" rule group
  (`Get-NetFirewallRule -DisplayGroup 'Core Networking' | Select-Object -First 1 Group` should
  print `@FirewallAPI.dll,-25000`; otherwise pass `-KeepRuleGroup`).

## Amendment 2026-10-19

- Status: proposed; the owner decisions at the end are open.
- Why: ARCILOGGER-Setup.exe stopped on a PC without a fixed NTFS `D:`; a new configuration bound the
  collector to one fixed server address, so on any other address it stopped at once
  and no syslog arrived; a desktop Windows sleeps and restarts for updates; the admin PC needed a
  whole build toolchain; every upgrade reset the server address and name.

### A1. Release build in CI: the trust shift

The release job of `.github/workflows/installer.yml` runs for a pushed tag `v<version>` (it must
equal `__version__` of `arcilogger_core`) or a manual run, once the installer tests and the
end-to-end installation of the same run have passed. On `windows-latest` it builds the same
bundle as the admin PC (`Build-ArciBundle.ps1 -RequireInno`, operator installers included) with
the Inno Setup of the runner image, zips the bundle folder, writes the SBOM (syft, SPDX, of the
tagged source before the build adds tools to it) and `SHA256SUMS.txt` (the `sha256sum` format),
keeps all of it as a workflow artifact for 30 days and, for a tag only, creates a **draft**
GitHub release with the same files (`gh release create --draft`, `GITHUB_TOKEN`). Only this job
has `contents: write`; every action is pinned by its commit SHA.

Release checklist (amended 2026-10-21 with licensing, ADR-0013):

1. **Bump `__version__` and `RELEASE_DATE` together** in `core/src/arcilogger_core/__init__.py`
   (and the version of every package, `tests/test_versions.py`), in the commit that is tagged.
   `RELEASE_DATE` is the release day the licence's `maintenance_until` is compared with
   (licensing contract 5.1, 9.2); a forgotten bump keeps an old date and so lets licences whose
   maintenance has ended cover the new version.
2. Tag that commit `v<version>` and push the tag. For a tag the release job first checks the
   date (`tools/release/check_release_date.py`, standard library only): it stops the release
   when `RELEASE_DATE` is more than 14 days before the tagged commit's day (Europe/Istanbul) or
   after it, and says why. A manual run from a branch skips the check (it publishes nothing).
3. Review the draft release (job log, `SHA256SUMS.txt`, fingerprint) and publish it.

Trust moves. Before, the operator built the bundle on the admin PC from a checkout under their own
control, and its fingerprint was what they compared on the server. Now the bundle is what a
GitHub-hosted runner built from the tagged commit: whoever can push a tag or change the workflow
decides what is built, and the runner image (Inno Setup, uv, Node.js) is GitHub's.
`SHA256SUMS.txt` and the fingerprint prove that the files did not change after the build (on the
way, on the admin PC, on a USB stick), not that the build was honest: they come from the same
release. What limits the risk: a person reviews and publishes the draft; the values also appear in
the job summary and the release notes; the build still checks every component against
`components.json`; the installer checks every file against `SHA256SUMS` again. Not done here: a
provenance attestation (it needs another pinned action, whose commit SHA was not verified for this
change) and code signing (not recommended for now). The build path on the admin PC stays
documented (KURULUM.md section 4.2).

### A2. Desktop profile and fewer manual steps

- **Data root:** without `-DataRoot` and without a configuration, `D:\ARCILOGGER` on a ready,
  fixed NTFS `D:`, otherwise `<system drive>\ARCILOGGER`. The preflight refuses removable,
  optical, network and RAM drives, warns about a disk on USB (Storage module, when it answers) and
  about data on the disk of Windows. `-MinFreeGB` stays 100: the evidence is kept for at least
  two years. `Set-ArciVaultAcl.ps1` run by hand takes `data_dir` of the configuration.
- **Server address and name:** a given parameter wins and is remembered in `install\state.json`;
  then the remembered value; for the address then `[collector] bind` of an existing configuration
  when it names one address (installations from before this amendment); then the address of the
  only adapter that is up with an IPv4 default gateway; otherwise the preflight stops and asks.
  A detected DHCP address gets a warning (static IP or reservation). `-AdminAddress` is unchanged
  (remembered by `Set-ArciFirewall.ps1`).
- **Collector bind:** a new configuration gets the server address as `[collector] bind`. The
  preflight **fails** when the effective bind (existing configuration, else the server address)
  is neither a wildcard nor an address of this computer, naming the file or parameter to change
  and the addresses that work; a loopback bind (the CI end-to-end test) only warns.
- **Power (client Windows only, ProductType 1):** `Set-ArciPower.ps1` sets "sleep" and
  "hibernate" after to never on AC, turns hibernation and so Fast Startup off, sets
  `HiberbootEnabled = 0` and switches network adapter power saving off where the driver and the
  `Set-NetAdapterPowerManagement` of that Windows support it. It records the previous values
  (power plan, timeouts, hibernation, `HiberbootEnabled`, the adapters changed) once in
  `install\power-state.json`; `-Uninstall` restores them. A failure is a warning, never a
  rollback. `-SkipPower` leaves everything alone. Windows Update is left to the operator
  (download and notify, restart in the maintenance window; KURULUM.md section 17).
- **End of a first installation:** after `Stop-Transcript` (the plan is already written), and
  only when a person sits at the console (`Test-ArciInteractiveConsole`: no `-NonInteractive`, no
  CI, no redirected stdin or stdout), the installer runs `arcilogger-api setup-token` and prints
  the token with the browser address, then offers to store the Kamu SM customer number and
  password (`secret set` on stdin, redacted, never on a command line). The token line goes to
  `[Console]::Out`, past the PowerShell host and its streams: a PowerShell transcription forced
  by Group Policy, a `Start-Transcript` of the operator or a `*>` redirection records the text
  around it, not the token; only a tool that records the console window itself would see it (a
  new `setup-token` voids it). `setup-token` exit code 4 (an administrator exists already, a
  reinstallation over a kept database) is reported as such, not as a failure to fix. With
  `-Auto` (the setup `.exe`) the window waits for Enter.
- **Database:** a database the installer creates gets `LOCALE 'C'`; an existing one is never
  altered. Text comparison and `ORDER BY` in SQL then use code-point order, as in the test
  databases (`arcilogger_db.testing`).
- **Strict outbound mode:** the SMTP rule follows `[smtp]` of the configuration (enabled and a
  host; port default 587) on every run, and `-SmtpServer` is remembered like the other choices.

### A3. Operating system policy

| Windows | Preflight |
|---|---|
| Windows Server 2022 (build 20348), 2025 (26100) | ok |
| Other Windows Server builds | warning |
| Windows 11 Pro, Enterprise, Education from build 26200 (25H2) | ok |
| The same editions, builds 22000–26199 | warning: may be out of servicing, check Microsoft's support dates |
| Windows 11 Home (`EditionID` Core…) | stops; `-AllowUnsupportedWindows` makes it a warning |
| Windows 10 (client build below 22000) | stops; `-AllowUnsupportedWindows` makes it a warning |
| An unknown client edition | warning |
| ARM64 | warning (the x64 programs run under emulation, untested); x86 and ARM fail |
| Any client Windows | information: verify the Windows licence terms with a Microsoft partner |

The decision is a pure function (`Get-ArciOsSupport` in `ArciInstall.psm1`). The cut-off is a
table (`$script:ArciClientServicedBuild`) reviewed with every release; it rests on Microsoft's
lifecycle dates as read for this amendment: Windows 11 24H2 Home/Pro had its last update on
2026-10-13, 25H2 is serviced until 2027-10-12. Enterprise and Education use the same cut-off,
although Microsoft may service their builds longer: their lifecycle dates were not checked for
this table, so on those editions the warning means "check", not "out of support". The Windows 11
and Windows 10 client licence terms
contain a clause on using the device as a server (section 2(c)(v)); how it applies here is not
settled, and the installer draws no conclusion.

### Consequences

- The setup `.exe` installs on a one-disk PC and on any address without parameters; upgrades need
  none either.
- On a one-disk desktop every run warns that the evidence shares the system disk (exit code 1):
  a persistent risk, not noise (the nightly database dumps of [ADR-0009](0009-database-backup.md)
  are written to `backups\scheduled` on the same disk unless copied elsewhere; the replica is off
  by default).
- Removal restores the power settings; the time zone, the time service settings and the audit
  policy stay as before this amendment.
- GitHub-hosted runners cannot test client Windows: the manual acceptance test
  ([KABUL-TESTI-WINDOWS11.md](../KABUL-TESTI-WINDOWS11.md)) comes before announcing desktop
  support; it also answers the open questions ("Log on as a service" for
  `NT SERVICE\ALL SERVICES` on Windows 11, `auditpol` on Pro, Smart App Control, the adapter
  power command, `Win32_Processor.Architecture` under ARM64 emulation).
- Owner decisions: the Windows client licence question (with a Microsoft partner); whether the
  release may carry the Temurin 8 JRE (GPL-2.0 with the Classpath exception: a source offer once the
  bundle is given to other organisations, the plan since 2026-10-20); the Inno Setup licence for
  commercial use (asked since Inno Setup 6.5.0); who may push `v*` tags and publish drafts.

## Amendment 2026-10-20: no values of a particular organisation

- Status: accepted (owner decision 2026-10-20, [ASSUMPTIONS](../ASSUMPTIONS.md) P1–P6).
- Why: ARCILOGGER becomes a product for many organisations. The installer and its scripts
  shipped one site's admin PC address, time source and organisation name as defaults, and its
  example configuration carried that site's devices.

### B1. The organisation's values have no default

- `-SiteName` (`site_name`): required on a first installation, asked at the console when a
  person sits there (`Test-ArciInteractiveConsole`), otherwise the preflight stops; an upgrade
  keeps the configured name. The rules are the core's (one line, at most 200 characters, no
  control or format characters).
- `-NtpServer` (`[clock] ntp_server`): asked on a first installation, kept on an upgrade; empty
  means Windows Time and the clock check use only `[clock] fallback_server`, and the preflight
  warns (the core's `arcilogger-core config-check` reports the same).
- `-AdminAddress` (installer and `Set-ArciFirewall.ps1`): given, remembered by
  `Set-ArciFirewall.ps1`, or typed at the console; otherwise a Turkish/English stop with
  `10.0.0.10` / `10.0.0.0/24` examples. Not needed with `-SkipFirewall`.
- Given on an upgrade, `-SiteName` and `-NtpServer` are written into the existing
  `arcilogger.toml`: only those keys change (no Python needed: a one-line TOML string
  writer that refuses dotted keys, inline tables and multi-line strings), the previous file is
  kept as `arcilogger.toml.before-<time>`.

### B2. Example configuration and texts

- `arcilogger.example.toml` has no organisation name, no local time source, a wildcard
  collector bind (the installer writes the server address) and a `<NAS>` replica placeholder.
- **No log source on a first installation** (2026-10-20): the example's `[[sources]]` (the
  documentation's fictional example network) are commented out, so the `arcilogger.toml` a first
  installation makes configures no device. Configured, devices the site does not have would be
  silent sources (R15) from the first hour, and a sender at one of their addresses would get
  their parser instead of the content-based choice of an `unknown-<ip>` sender. No source is a
  valid state: `Set-ArciFirewall.ps1` then makes no syslog rule (514 stays closed under the
  default Block action, nothing is opened in its place) and warns instead of stopping; the
  preflight (`preflight.sources`, a warning for a new or an existing configuration without
  sources, a stop only for an invalid address; an existing file is read with tomllib, or with a
  narrow pattern while no Python is installed yet, as on a new installation over the
  `arcilogger.toml` a removal kept) and the next steps say what to add, and
  `arcilogger-core config-check` warns (`sources`). An existing `arcilogger.toml` is never
  replaced, so an upgrade keeps its sources.
- The setup's publisher is `ARCILOGGER`; task descriptions name the configuration keys, not an
  address or a storage product.

### Consequences

- A first unattended installation needs `-SiteName` (and `-AdminAddress` unless
  `-SkipFirewall`); CI and the end-to-end tests pass the fictional example's values.
- Installations made before this amendment keep their configuration: the upgrade reads
  `site_name` and `[clock] ntp_server` from it and the remembered admin addresses from the
  firewall state file.
