# ARCILOGGER — Mimari / Architecture

> Durum: MVP (sürüm 0.1.0) — M1 (toplayıcı, mühürleme, doğrulama, replika, saklama) ve M2/M3'ün
> temel parçaları: FortiGate/MikroTik ayrıştırıcılarıyla ingest, kira zaman çizelgesi, 5651 sorgusu
> ve resmi PDF rapor, kanıt paketi; ayrıca kimlik doğrulama/TOTP/denetim kaydı (M6'dan) ve Windows
> kurulum programı (M7'den). Bu belge hedef mimariyi anlatır; her bölümün hangi aşamada (M1–M7)
> geldiği ve bugün neyin çalıştığı belirtilmiştir (depodaki `README.md`, "Durum"; sunucuda:
> [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 7, "Bu sürümün sınırları").

## Özet (TR)

- ARCILOGGER **tek bir Windows sunucuda** çalışır (Windows Server 2022/2025 önerilir).
- **Kullanıcılara istemci kurulmaz.** Yönetici, denetçi ve İK kullanıcıları ARCILOGGER'a yalnızca
  **web tarayıcısından** bağlanır (`https://<sunucu adresi>`). Çalışan bilgisayarlarına ajan da
  kurulmaz: bütün kayıtlar ağ cihazlarından (FortiGate, MikroTik) syslog ile gelir.
- Ham loglar önce değiştirilmeden diske yazılır (delil), her gece sıkıştırılır, SHA-256 ile
  özetlenir, önceki güne zincirlenir ve **Kamu SM zaman damgası** ile mühürlenir.
- Mühürleme; veritabanı veya web arayüzü çalışmasa bile Windows Görev Zamanlayıcı ile çalışır.
- Sunucu ayarları **tarayıcıdan** değişir ([AYARLAR.md](AYARLAR.md)): API yalnızca imzalı bir istek
  bırakır; yapılandırmayı ayrı bir hizmet (ArciAdmin) yazar, güvenlik duvarını ve saati SYSTEM
  görevi ApplySystem kendi türettiği değerlerle günceller. Bir ayar değişikliği mühürlemeyi
  hiçbir zaman durdurmaz.

## Summary (EN)

ARCILOGGER is a **single Windows server**. People use it **only through a web browser**; nothing is
installed on user PCs and no agent runs on employee computers — all data arrives as syslog from the
network devices. Raw logs are written unchanged first, then sealed nightly (gzip, SHA-256 chain,
Kamu SM qualified time stamp). The evidence path does not depend on the database or the web stack.
The server's settings change from the browser through a separate administration service and a
SYSTEM task that derives the firewall and time settings itself (ADR-0014); a settings change never
stops collection or sealing.

## Deployment model

| Item | Choice |
|---|---|
| Server OS | Windows Server 2022 or 2025 Standard (recommended); Windows 11 Pro on a dedicated machine also works |
| Placement | A dedicated server (recommended; not on the device that holds the replica) in the network the devices send syslog to (untagged switch port), on a UPS |
| Address | A static address of the site (`-ServerAddress`); time from the site's local time source (`-NtpServer`, `[clock] ntp_server`), else `time.ume.tubitak.gov.tr` |
| Organisation | Name, time source and admin PC entered at installation, no defaults ([KURULUM.md](KURULUM.md) §7.1); log sources in `arcilogger.toml`; departments and VLANs entered by the administrator on the Cihazlar ve Kişiler page; no VLAN plan shipped |
| Sizing | Recommended at least 4 cores, 16 GB RAM; `C:` ≥ 100 GB system, `D:` ≥ 1 TB NTFS data disk, RAID 1 recommended |
| Users | Any current browser (Edge, Chrome, Firefox) on the admin PC (`-AdminAddress`) |
| Client software | **None** |

## Components

Windows services (automatic start, restart on failure, each under its own virtual service account
`NT SERVICE\<name>` with only the folder rights it needs):

| Service | Program | Purpose | Listens on | Milestone |
|---|---|---|---|---|
| `ArciCollector` | `arcilogger_collector` (Python) | Receives syslog, writes the raw spool **first** | UDP/TCP 514 (and TLS 6514, optional) on the server address (`[collector] bind`) | M1 |
| `ArciIngest` | `arcilogger_ingest` | Tails the spool (or the sealed vault copy), parses, loads PostgreSQL | — | MVP (M2) |
| `ArciApi` | `arcilogger_api` (FastAPI, Uvicorn) | HTTP API for the web interface: auth/TOTP, users, audit log, 5651 lookup + PDF report, evidence packages, seals, devices and persons, alerts (state workflow, notes) and the dashboard summary | 127.0.0.1:8000 | MVP; more pages M4–M6 |
| `ArciWorker` | `arcilogger_worker` | Today: lease timeline, devices and IP conflicts, partitions, evidence purge, audit import, alerts (import of `alerts.jsonl`, R15/R20 checks, alert e-mail; ADR-0010), usage rollups (`usage.*` jobs, estimated time per person/site/category; ADR-0011, docs/USAGE.md), the licence check (`license.check`, ADR-0013). Later: NAT index, the rule engine and its detections (M5) | — | MVP (M3); M4–M5 |
| `ArciWeb` | Caddy | HTTPS (internal CA), serves the web interface, proxies `/api` | server address:443 | MVP (installer) |
| `ArciAdmin` | `arcilogger_core.admin` (Python) | The administration agent of the browser administration ([ADR-0014](adr/0014-browser-administration.md)): checks the API's signed requests in `admin\inbox` again and applies them (the only writer of `arcilogger.toml` and `secrets.json`), keeps the versions, follows the services reloading, writes signed task requests (Seal now, credit query, tests) | — (no listener, no database) | Wave 1 |
| `postgresql-x64-16` | PostgreSQL 16 (operator-installed); TimescaleDB optional, otherwise native monthly partitions ([ADR-0005](adr/0005-database-and-mvp-contracts.md)) | Derived, rebuildable data, plus users, lookups and the audit log | 127.0.0.1:5432 | MVP (M2) |

Scheduled tasks (Task Scheduler folder `\ARCILOGGER\`, "run as soon as possible after a missed
start" — the Windows equivalent of systemd's `Persistent=true`), running `arcilogger-core` (the
database backup runs `arcilogger-db`):

| Task | When | Purpose | Milestone |
|---|---|---|---|
| `Seal` | daily 00:10 | Compress, hash, manifest, chain, Kamu SM stamp, verify, lock, replica | M1 |
| `Verify` | daily; weekly (Sun 03:00, 90 days); monthly (1st, 04:00, full chain) | Chain verification | M1 |
| `Retention` | daily 02:00 (disabled by default) | Delete days older than retention, sealed deletion record | M1 |
| `Replica` | after `Seal`, retry daily | Copy the vault day to the replica share (`[replica] target`, e.g. a NAS) and verify hashes | M1 |
| `Credit` | daily 06:00 | Kamu SM credit query | M1 |
| `ClockCheck` | hourly | Clock offset against `[clock] ntp_server` (else only `[clock] fallback_server`) | M1 |
| `Backup` | daily 01:40 | `pg_dump` of the database into `backups\scheduled\`, check, retention (needs PostgreSQL, not part of the evidence path; [ADR-0009](adr/0009-database-backup.md)) | M7 |
| `Requests` | on event 4111, and every 5 minutes | The task requests of the browser administration (credit query, time-stamp test, replica test), signed by ArciAdmin; LOCAL SERVICE like the other evidence tasks | Wave 1 |
| `ApplySystem` | on event 4101, and every 5 minutes | **SYSTEM** (the named exception): brings the firewall's syslog rules (and, in strict outbound mode, its outbound rules), Windows Time and the promotion of known Zamane clients in step with the validated configuration; it takes no input from a request (`Sync-ArciSystem.ps1`) | Wave 1 |

`Seal` also starts on event 4110 (**Şimdi mühürle** on the Sealing page). The events come from the
Application log source `ARCILOGGER-Admin`; an event only wakes a task, the signed request file is
the authority ([SECURITY.md](SECURITY.md) section 14).

**Evidence path independence:** collector → spool → `arcilogger-core` (Task Scheduler) → Kamu SM.
None of these need PostgreSQL, the API, the worker or Caddy, nor the licence, nor the browser
administration: with ArciAdmin stopped or `-DisableBrowserAdministration`, collection and sealing
go on, and a settings change never stops them (the services reload the configuration and keep the
previous one when a new one does not load).

**Browser administration** ([ADR-0014](adr/0014-browser-administration.md),
[SETTINGS.md](SETTINGS.md)): browser → ArciApi (validates, signs, writes `admin\inbox`) →
ArciAdmin (checks again, writes the configuration, keeps a version) → the services reload
(`configwatch`: collector and ingest ≤ 5 s for `[[sources]]`, the API ≤ 2 s, the worker ≤ 60 s)
→ Event 4101 → ApplySystem (firewall, time, Zamane clients). The programs the tasks run are
checked by `arcilogger_core.execpolicy`.

The Python services run `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_<package> …` and the
tasks `python.exe -I -m …` (isolated: no `PYTHON*` environment variable can load other code into
them; ADR-0013).

## Licensing (where the licence is checked)

ARCILOGGER is licensed offline ([ADR-0013](adr/0013-offline-licensing.md),
[LICENSING.md](LICENSING.md)): a signed licence file (Ed25519 over the canonical JSON of its
payload, public keys in the code) lists the paid modules — `usage_analytics`, `usage_reports`,
`bulk_import` — beside `core_5651` (5651 Temel), which is on in every state. No check ever leaves
the server. The decision lives in one place, `arcilogger_core.license` (`state.evaluate`, the
trusted time `trusted_time.trusted_now`, the server id `fingerprint`), read through
`arcilogger_db.licensing.load_status` and, on every shared path, through the guard
`arcilogger_db.license_guard.safe_status` (any failure → 5651 Temel only, no users limit). It is
checked in exactly these places:

| Where | What |
|---|---|
| `ArciApi` | `require_module` on the routers `/usage`, `/reports`, `/imports` (403 `module_not_licensed`; status cached 30 s); the users limit on `POST /users` and on re-enabling a user (409 `license_limit`); `GET/POST/DELETE /license` (the licence page) |
| `ArciWorker` | the gate of the paid jobs (`usage.*` rollups, `reports.run_due`; skipped, status cached 60 s); the job `license.check` every 5 minutes (state changes and limit crossings audited, the licence alerts `R20-license…`, `state\license-status.json` for the installer) |
| `arcilogger-db license …`, `arcilogger-api create-admin`, `arcilogger-worker usage rebuild` | the licence commands; the users limit of the recovery command; the one gated worker command |
| Installer | the maintenance rule before any change (advisory: the bundle's `release_date` against `maintenance_until`), `-LicenseFile`, the request code at the end; it gates nothing by module |
| Web | the licence page (Ayarlar → Lisans), banners, lock marks and locked pages, from `GET /license` (the API decides) |

**Never touched by licensing:** the collector, ingest, the core evidence modules (sealing, chain,
retention, replica, clock check, credit, time stamps, evidence packages and the standalone
verifier) and every scheduled task never import the licence code, and no licence mark goes into
raw logs, sealed days, evidence packages or the 5651 report. The evidence path stays independent
of the database and of the licence.

## Data flow

```
FortiGate ─┐
MikroTik core ─┤ syslog UDP/TCP 514      ┌──────────────────────────────────────────────┐
switches, APs ─┘ ───────────────────────► │ ArciCollector                                │
                                          │  1. append raw line to                       │
                                          │     D:\ARCILOGGER\spool\<source>\<day>.log   │
                                          │     (receive time + sender IP + original)    │
                                          └──────────────┬───────────────────────────────┘
                                                         │ (file is the durable queue)
                        ┌────────────────────────────────┴───────────────┐
                        ▼                                                ▼
         ArciIngest (tail with saved offset)              Task Scheduler 00:10 → arcilogger-core seal
          parse → PostgreSQL (TimescaleDB optional)        gzip -n, SHA-256, manifest (JCS), chain,
                        │                                  Kamu SM stamp (only the hash leaves),
                        ▼                                  verify, read-only + NTFS ACL,
         ArciWorker: leases, NAT index, usage,             copy to the WORM replica
         detections, reports, e-mail                                     │
                        │                                                ▼
                        ▼                                  D:\ARCILOGGER\vault\YYYY\MM\DD\…
         ArciApi ◄── Caddy (HTTPS 443) ◄── browser on the admin PC
```

## Directories

| Path | Content |
|---|---|
| `C:\Program Files\ARCILOGGER\` | Program: Python runtime, wheels, web build, Caddy, service wrapper |
| `C:\ProgramData\ARCILOGGER\` | `arcilogger.toml` (example: `deploy/windows/arcilogger.example.toml`), `data-root.txt` (the data root the installer recorded), `secret.key` + `secrets.json`, `kamusm-roots\`, `smtp-ca\`, `zamane\` (`web\` uploaded clients waiting, `approved\` promoted ones), `install\` (installer state) |
| `C:\ProgramData\ARCILOGGER\admin\` | Browser administration: `policy.json` (the installer's limits, administrators only), `keys\`, `inbox\`, `versions\`, `status\`, `journal\`, `system\` (ApplySystem's status, `jars.json`) |
| `D:\ARCILOGGER\spool\<source>\<YYYY-MM-DD>.log` | Today's raw files (append-only) |
| `D:\ARCILOGGER\vault\<YYYY>\<MM>\<DD>\` | Sealed days: `<source>.log.gz`, `audit.jsonl.gz`, `manifest-<date>.json`, `.zd` token |
| `D:\ARCILOGGER\vault\renewals\`, `vault\deletions\` | Re-stamping and sealed deletion records |
| `D:\ARCILOGGER\exports\` | Evidence packages `ARCILOGGER-kanit-*.zip` (kept `evidence.keep_days`, 30 days; the purge touches only these) |
| `D:\ARCILOGGER\exports\reports\` | Stored official 5651 reports (`ARCILOGGER-5651-rapor-<id>.pdf` and `.zd`), rendered once and served unchanged; **not purged** (no retention job yet, [RAPOR.md](RAPOR.md)) |
| `D:\ARCILOGGER\state\` | Status files of the services, alerts, per-day job state; `license-status.json` (the licence state for the installer, never trusted by the product); `requests\` (signed task requests and their results) |
| `D:\ARCILOGGER\logs\`, `backups\` | Service logs (rotated); database dumps: `backups\db\` before upgrades, `backups\scheduled\` nightly ([YEDEKLEME.md](YEDEKLEME.md)) |
| `D:\ARCILOGGER\db\` | PostgreSQL data directory |

## The original Linux design, mapped to Windows

| Original spec (Debian) | Windows design | Decision |
|---|---|---|
| rsyslog + disk-assisted queue + omfwd | Own collector service; the spool file is the queue | [ADR-0002](adr/0002-own-syslog-collector.md) |
| systemd timers (`Persistent=true`) | Task Scheduler (`StartWhenAvailable`) | [ADR-0001](adr/0001-windows-server-browser-only.md) |
| systemd services + sandboxing | Windows services under virtual service accounts, least-privilege ACLs | ADR-0001 |
| `chattr +i` | Read-only attribute + NTFS deny-write/delete ACL + WORM replica (e.g. on a NAS); tampering is detected by the hash chain and time stamps | [ADR-0003](adr/0003-integrity-on-windows.md) |
| chrony | Windows Time service (w32time) to the local time source (`[clock] ntp_server`) + ARCILOGGER's own SNTP offset probe | ADR-0001 |
| nftables | Windows Defender Firewall inbound and outbound allow-lists | ADR-0001 |
| auditd on the vault | Object-access auditing (SACL) on the vault → Windows Security log | ADR-0003 |
| `/proc` `hidepid` for the Zamane password | Zamane runs under its own service account; other users cannot read its command line, administrators can (residual risk, documented) | ADR-0001 |
| Docker Compose in production | None; native services. Docker is used only for development | ADR-0001 |
| `install.sh` | Offline bundle + `Install-Arcilogger.ps1` (+ optional `ARCILOGGER-Setup.exe`, Inno Setup) — shipped in the MVP | [ADR-0007](adr/0007-windows-installer.md) |
| WeasyPrint (needs Pango/GTK) | reportlab (pure Python, BSD; bundled fonts; byte-identical PDFs for the same inputs, [RAPOR.md](RAPOR.md)) | [ADR-0005](adr/0005-database-and-mvp-contracts.md) decision 9 |

## Rules for code that must run on Windows

- Raw and sealed files are opened in **binary mode**; lines end with `\n`; no text-mode newline
  translation, ever. `.gitattributes` keeps fixtures byte-exact on Windows checkouts.
- Paths come from configuration and are handled with `pathlib`; no hard-coded separators.
- Time zones come from `zoneinfo` + the `tzdata` package (Windows has no IANA database);
  the machine's local time zone is never used.
- The collector opens today's spool file with shared read access so ingest can tail it; sealing
  only touches **closed** days, so no file is ever renamed while open.
- Services must survive Windows Update reboots: everything restarts automatically and the
  collector flushes before acknowledging TCP frames.

## Technology

| Layer | Choice |
|---|---|
| Python | 3.12 (bundled runtime on the server), uv workspace: `core`, `db`, `collector`, `ingest`, `api`, `worker` |
| Quality | ruff, mypy `--strict`, pytest + hypothesis; CI on Windows and Linux |
| Database | PostgreSQL 16; TimescaleDB optional (`[database] timescale = auto\|require\|off`), otherwise native monthly partitions; SQLAlchemy 2, Alembic, psycopg 3 ([ADR-0005](adr/0005-database-and-mvp-contracts.md)) |
| API | FastAPI, Pydantic v2; PDF reports with reportlab |
| Web | React 18.3, TypeScript (strict), Vite 7, Tailwind CSS 4, Radix UI, TanStack Query/Table/Virtual, Recharts, react-i18next; Vitest, Playwright + axe |
| Web server | Caddy (HTTPS with an internal CA) |
| Time stamping | Kamu SM Zamane console client (Java 8) by default; generic RFC 3161 client (development) and a mock TSA (tests and demos, never legal); a native Kamu SM client is planned behind a flag ([kamusm.md](kamusm.md)) |
| Installer | Offline bundle, WinSW services under virtual accounts, Caddy, PowerShell scripts ([ADR-0007](adr/0007-windows-installer.md)) |
| Evidence verifier | `verify.py`, one file, Python standard library only ([ADR-0006](adr/0006-standalone-verifier.md)) |
