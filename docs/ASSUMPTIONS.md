# Varsayımlar / Assumptions

Ürün sahibi bunları düzeltebilir; düzeltilen her madde burada güncellenir. Kararların tarihleri ve
gerekçeleri korunur; bir kuruluşun ağına ait değerler kurulumda girilen ayarlardır (aşağıda
"Installation-time settings").
The product owner can correct any of these; this file is updated with every correction. The
decisions keep their dates and reasons; what belongs to an organisation's network is a setting
entered at installation ("Installation-time settings" below).

## Decided by the owner

| # | Decision | Date |
|---|---|---|
| D1 | ARCILOGGER runs on **Windows** as a server; users need **no client software**, the web interface is enough ([ADR-0001](adr/0001-windows-server-browser-only.md)) | 2026-10-12 |
| D2 | Hardware guidance: a **dedicated server** for ARCILOGGER, recommended at least **4 cores and 16 GB RAM**, running Windows; not hosted on the device that holds the replica (A6), so one failure cannot take both copies | product guidance |
| D3 | **psycopg 3 (LGPL-3.0) accepted for internal use**: unmodified, separately installed, replaceable wheel shipped with its licence text ([ADR-0005](adr/0005-database-and-mvp-contracts.md)); legal review before ARCILOGGER is distributed to other organisations | during the MVP (recorded 2026-10-13; exact date: the owner) |
| D4 | The **auditor does not create evidence packages**: `evidence.create` is admin-only; the auditor verifies and downloads ([roles.md](roles.md), [KANIT-PAKETI.md](KANIT-PAKETI.md)) | 2026-10-12 |
| D5 | **Non-administrators see only their own 5651 lookups** (another user's lookup answers 404); administrators see all ([MVP-CONTRACTS.md](MVP-CONTRACTS.md) §6.1). Evidence packages built from a lookup follow the same rule | during the MVP (recorded 2026-10-13; exact date: the owner) |
| Owner 2026-10-20 | **Usage reports with names and maximum detail** (the shipped default; each organisation must establish its own legal basis, e.g. consent or an information text — legal review recommended). Every role that may see usage (`usage.person` or `usage.anonymous`) sees person and device names, MAC and IP addresses and host names; reports contain person-level detail by default (full site and host name lists, URLs the FortiGate logged, every blocked attempt, per-person timelines and hourly tables, per-device breakdowns, applications). Pseudonyms only as the optional setting `usage.pseudonymize_anonymous_role` (default off). Kept: role checks, the audit log of views, exports and changes, the one-time KVKK notice on person-level endpoints, `persons.usage_excluded`; every duration is an estimate ([USAGE.md](USAGE.md), [adr/0011-usage-analytics.md](adr/0011-usage-analytics.md)). No new legal claim either way: legal review recommended | 2026-10-20 |
| Owner 2026-10-20 (product) | **A product for many organisations:** no data of a particular organisation in the repository; the organisation's values are entered at installation (P1–P8 below) | 2026-10-20 |
| Owner 2026-10-20 (licensing) | **Offline licensing:** perpetual licence plus annual maintenance, paid modules beside the always-on 5651 Temel, a 14-day trial, activation by request code and signed licence file without any network call (L1–L8 and C-1–C-16 below, [ADR-0013](adr/0013-offline-licensing.md)) | 2026-10-20 |

## Product decisions (owner, 2026-10-20)

ARCILOGGER is a product for many organisations. The repository carries **no data of a
particular organisation**: no name, e-mail address, device name or model, firmware version,
address, VLAN plan or log sample that identifies one. What belongs to an organisation's network
is an installation-time setting (next table); tests and examples use a fictional organisation and
network (`Örnek A.Ş.`, the `ornek.example` domain, the example plan of the tests and of
`deploy/windows/arcilogger.example.toml`).

| # | Decision | Where |
|---|---|---|
| P1 | **Organisation name** `site_name` is empty by default and a required setup value: the installer takes `-SiteName` (asked at the console; required on a first non-interactive installation; an upgrade keeps the configured name), `arcilogger-core config-check` reports it missing as an error; reports, e-mails, evidence packages and the web leave an empty name out | [KURULUM.md](KURULUM.md) §7.1 |
| P2 | **Time source** `[clock] ntp_server` is empty by default; the installer takes `-NtpServer` (asked when missing). Empty: the clock check and `Set-ArciTime.ps1` use only `[clock] fallback_server`, and the preflight and `config-check` warn | [KURULUM.md](KURULUM.md) §1.3, §7.1 |
| P3 | **Admin address** `-AdminAddress` of the installer and of `Set-ArciFirewall.ps1` has no default: asked at the console, otherwise a clear Turkish/English stop; examples use `10.0.0.10` / `10.0.0.0/24` | [KURULUM.md](KURULUM.md) §7.1 |
| P4 | **No network plan is shipped:** the production seed writes only the default settings; VLANs, departments and log sources are the organisation's own data. Departments and VLANs are entered on the Cihazlar ve Kişiler page (tab Departmanlar ve VLAN'lar). The tests use a fictional example plan explicitly (`arcilogger_db.testing`) | [DATA-MODEL.md](DATA-MODEL.md) "Seed data", [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §5, §12 |
| P5 | **Example configuration** with placeholders and a clearly marked fictional example network; the installer fills in what belongs to the organisation. Its `[[sources]]` are comments: a first installation configures **no log source** (514 stays closed until the organisation adds its devices; the preflight, the next steps and `config-check` warn) | `deploy/windows/arcilogger.example.toml`, [DEVICE-SETUP.md](DEVICE-SETUP.md) §4 |
| P6 | Setup publisher `ARCILOGGER`; texts name device roles ("ana yönlendirici", "DHCP kaydı (MikroTik)"), never a host name of a particular network | [ADR-0007](adr/0007-windows-installer.md) |
| P7 | The legal drafts are templates with `[Şirket unvanı]`, `[adres]`, `[VERBİS kayıt no]` placeholders, still marked "Taslak — hukuk müşaviri onayı gerekir" | [legal/](legal/aydinlatma-metni-taslak.md) |
| P8 | The log format samples are synthetic, modelled on FortiOS 7.6 / RouterOS 7 output (field names, order, quoting, separators); all values fictional (`tests/fixtures/format/README.md`); the product name, the package names and the 5651 wording stay; no claim of legal compliance | `tests/fixtures/format/` |

## Licensing (owner 2026-10-20)

Offline licensing ([ADR-0013](adr/0013-offline-licensing.md); customer guide
[LISANS.md](LISANS.md) / [LICENSING.md](LICENSING.md)). Rows L1–L8 are the owner's brief of
2026-10-20; rows C-1 to C-16 are the decisions the licensing contract added. The owner confirmed
C-1, C-6 and C-11 (with **3 users**) explicitly and accepted every other one as written. Prices are
not in the code. ARCILOGGER stays *designed to support* 5651 obligations; the licence never
switches off a 5651 function.

| # | Decision | Status | Where |
|---|---|---|---|
| L1 | **Perpetual licence plus annual maintenance:** a licence never expires; `maintenance_until` limits only upgrades and support (the installer refuses a version released after it; the web shows "bakım süresi doldu" as information, nothing switches off); an optional `valid_until` serves a time-limited demo or an extended trial, normal sales do not use it | owner decision | [LISANS.md](LISANS.md) §1, §7 |
| L2 | **Packages:** every module has its own package and price; a licence lists the enabled modules and two limits, the number of **log sources** and of **user accounts**; **Kullanım analizi** is a module of its own; the other modules are mapped from the real features of the code (`usage_reports`, `bulk_import`, C-1); **5651 Temel** is in every licence | owner decision | [LISANS.md](LISANS.md) §2 |
| L3 | **Trial: 14 days**, every module on, banner "Deneme sürümü — N gün kaldı" | owner decision | [LISANS.md](LISANS.md) §3 |
| L4 | **Offline activation:** the installation shows a request code (server id, installation id, product version, organisation name, checksum); the customer e-mails it to the licensor, Arman Khadangan, arman@khadangan.com; the licensor signs a `.lic` for that server; the customer uploads it in the web interface or passes it to the installer (`-LicenseFile`). **No network call to the vendor, ever** (it would be telemetry) | owner decision | [LISANS.md](LISANS.md) §4 |
| L5 | **Never block a 5651 obligation**, whatever the licence state: collection, spool, ingest, sealing, retention, replica, backups, chain verification, the 5651 lookup, evidence packages and the standalone verifier, the audit log, sign-in, users and roles, the operational alerts, the dashboard health boxes. An expired, missing, invalid or other-server licence locks only paid modules and shows banners. Limits are soft for 5651 data: sources over the limit are still collected and sealed; over the users limit only creating a user is refused | owner decision | [LISANS.md](LISANS.md) §2.1, §6 |
| L6 | **Never alter evidence:** no licence mark in raw logs, sealed days, evidence packages or the evidence PDF; the licensee name may appear in the UI, the licence/About page and usage report e-mails/PDFs. Every licence installation, removal, refusal and limit crossing is an audit entry | owner decision | [LISANS.md](LISANS.md) §12 |
| L7 | **No secrets in the repository:** the private signing key never enters it; the owner generates the key pair offline with the issuer tool (not shipped, with an append-only register kept outside the repository) and commits only the public key; until then the production key set is empty, so every installation stays in trial or unlicensed mode; no environment variable, configuration file or API adds a key. Ed25519 over the RFC 8785 canonical JSON of the payload, with key ids for rotation | owner decision | [ADR-0013](adr/0013-offline-licensing.md) §1–3 |
| L8 | **Trusted time and server binding:** trial end, `valid_until` and maintenance use the later of the system clock and the newest verified TSA time of the sealed days; the trial start survives a reinstall through the oldest sealed day. The server id comes from the Windows MachineGuid and the SMBIOS UUID (`XXXX-XXXX-XXXX-XXXX`); a changed server needs a re-issued licence. Anti-tamper hardening (compiled module, signed file manifest, Authenticode) is phase 2, with one place each for "is module X enabled" and for the trusted time | owner decision | [LISANS.md](LISANS.md) §9, §10; [ADR-0013](adr/0013-offline-licensing.md) |
| C-1 | `bulk_import` (the CSV import of persons and assignments) is a **paid module**; devices, persons, departments, VLANs and assignments stay in 5651 Temel (ADR-0008, KVKK corrections); manual entry always works | **confirmed by the owner** (2026-10-20) | [LISANS.md](LISANS.md) §2 |
| C-2 | No module keys for features not built yet; a licence may list keys a version does not know: they are kept and ignored, so a future module can be sold before a customer upgrades. Planned 5651 features (sources, system, live tail, search) will belong to 5651 Temel | accepted as written (owner 2026-10-20) | [ADR-0013](adr/0013-offline-licensing.md) |
| C-3 | The installation id is informational (request code, register); only the server id binds a licence, so a database restore or a reinstallation on the same server keeps it | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §9 |
| C-4 | "Built after" means the version's **release date** (`RELEASE_DATE`, also `release_date` in the bundle manifest), not the build machine's clock; a running version released after `maintenance_until` has its paid modules off (`version_not_covered`), so the installer's rule is also enforced at run time | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §7 |
| C-5 | No trial fall-back after an unusable licence and no grace period after a server change | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §5 |
| C-6 | `-AllowUncoveredUpgrade` installs a version the maintenance does not cover with **5651 Temel only**, so a fix for a 5651 obligation can always be installed | **confirmed by the owner** (2026-10-20) | [LISANS.md](LISANS.md) §7, [KURULUM.md](KURULUM.md) §13 |
| C-7 | Trial clock check: a trial start (or a database trace) more than 10 minutes after the trusted time makes the trial unavailable (`trial_expired`, `trial_clock_inconsistent`); an honest customer whose clock was wrong at installation gets a `valid_until` demo licence | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §3 |
| C-8 | The trusted time also uses the end of the newest vault day by its name, and the trial's vault bound is the end of the oldest vault day by its name instead of its token's time (needs no TSA roots; never weaker than the brief's formula) | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §10 |
| C-9 | A log source is a configured device or any sender (configured or not, enabled or not) that sent logs in the last **30 days** | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §6.1 |
| C-10 | Two more time floors: the start of the version's release day and the verified active licence's `issued_at` | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §10 |
| C-11 | Limits outside `licensed`: the licence's own limits in `wrong_server`, `expired` and `version_not_covered`; none during the trial; **3 users** and no source limit in `trial_expired` and `invalid` | **confirmed by the owner** (2026-10-20), with 3 users | [LISANS.md](LISANS.md) §6.2 |
| C-12 | Existing installations upgrade into `trial_expired` (their sealed days are older than 14 days) unless a licence is installed with or before the upgrade; the installer warns, the documents tell existing customers what to do | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §8 |
| C-13 | Single licences are revoked by licence id (`revoked.py`), effective on installations that upgrade; used for a server change, a leaked or a mistaken licence | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §9 |
| C-14 | `arcilogger-api create-admin` respects the users limit, except for true recovery (no administrator can sign in) or with `--deactivate USERNAME` (net zero) | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §6.2 |
| C-15 | The issuer refuses weak bindings (Linux, or Windows without SMBIOS UUID) unless `--allow-weak-binding`, and requires `--supersedes` or `--additional-server` when its register already holds a licence for the same licensee, organisation, installation or server | accepted as written (owner 2026-10-20) | [ADR-0013](adr/0013-offline-licensing.md) |
| C-16 | The trial start is also pulled back by the oldest spool day, the oldest audit entry, the earliest `license.trial_start` entry and the oldest licence event; a missing installation row is recreated with the earliest of the database traces | accepted as written (owner 2026-10-20) | [LISANS.md](LISANS.md) §3 |

## Installation-time settings

| # | Setting | Set by | Notes |
|---|---|---|---|
| G1 | Organisation name (`site_name`) | installer `-SiteName` | P1 |
| G2 | Local time source (`[clock] ntp_server`), usually the core router | installer `-NtpServer` | P2; `time.ume.tubitak.gov.tr` stays the shipped fallback |
| G3 | Admin PC address or network (443, 3389) | installer / `Set-ArciFirewall.ps1` `-AdminAddress` (remembered) | P3 |
| G4 | Server address (`[collector] bind`, web address) | installer `-ServerAddress` (remembered, else detected) | I2, I3 |
| G5 | Log sources (`[[sources]]`: names, addresses or networks, `heartbeat_minutes`, `transport`) | `arcilogger.toml`, by the administrator after the installation (none on a new installation) | the example's sources are fictional and commented out (P5); [DEVICE-SETUP.md](DEVICE-SETUP.md) §4 |
| G6 | VLANs (subnet, VLAN id, name, DHCP server, guest flag) and departments | administrator: both on the Cihazlar ve Kişiler page (`/api/v1/vlans`, `/api/v1/departments`) | P4; a guest network that does not leave through the FortiGate is a VLAN with `guest` set ([DEVICE-SETUP.md](DEVICE-SETUP.md) §5) |
| G7 | Lease time of DHCP servers with other leases than one day (e.g. a guest network) | `lease.server_seconds` (empty by default) | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §6 |
| G8 | NAT: public addresses, FortiGate WAN address, ISP port behaviour | `nat.public_ips`, `nat.wan_transip`, `lookup.port_hint` | A9; [CORRELATION.md](CORRELATION.md) §8 |
| G9 | Replica target (a NAS or another server, WORM if available) | `[replica] target`, `user` | A6 |
| G10 | Infrastructure MAC addresses (router, firewall, switches) | `devices.infrastructure_macs` | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §6 |
| G11 | Licence file (`.lic`) issued for this server from its request code | web Ayarlar → Lisans, `arcilogger-db license install`, or installer `-LicenseFile` | L4; [LISANS.md](LISANS.md) §4 |

## From the build specification (section 20)

1. Per installation: one site, one FortiGate, one core MikroTik; ≤ 300 devices; ≤ 10 ARCILOGGER
   users.
2. E-mail goes through an SMTP server the admin provides (setting); no other external services.
3. Persons are entered by hand or CSV; no Active Directory yet (an LDAP/AD sync interface is designed
   for later).
4. Guest Wi-Fi users stay anonymous until a hotspot module exists (placeholder only).
5. If the ISP router translates again (double NAT), 5651 lookups by public IP + port return ranked
   candidates, not an exact match, until the NAT settings say otherwise (A9, G8).

## Defaults proposed on 2026-10-12 (not yet confirmed)

| # | Default | Open question |
|---|---|---|
| A1 | Windows Server 2022 or 2025 Standard on the physical server; system disk `C:` ≥ 100 GB, separate data disk `D:` ≥ 1 TB, ideally two mirrored disks (RAID 1); the server on a UPS | Windows edition? Disk layout? |
| A2 | Private GitHub repository, proprietary licence (`LICENSE` at the repository root; rights holder Arman Khadangan; each organisation is licensed by its own agreement), GitHub Actions enabled | Confirm |
| A3 | **No general internet access** on the server. Permanently allowed outbound, through the FortiGate: Kamu SM time stamps (`zd.kamusm.gov.tr`, test `tzd.kamusm.gov.tr`, TCP 80; only hashes are sent), the Kamu SM certificate host `sertifikalar.kamusm.gov.tr` (TCP 80/443), NTP to the local time source (`[clock] ntp_server`, G2; fallback `time.ume.tubitak.gov.tr`), DNS to the site's resolvers; later SMTP and the replica target. Windows Update and other downloads only in maintenance windows ([DEVICE-SETUP.md](DEVICE-SETUP.md) flows 5–12). The installation itself needs no internet (offline bundle) | Confirm |
| A4 | Web address `https://<server address>` (G4) and `arcilogger.internal`, certificate from Caddy's internal CA; its root certificate is installed on the admin PC | Confirm the name |
| A5 | Kamu SM: request a **test user** first ("Zamane test kullanıcı talebi"), buy 10,000 kontör before go-live | Is there already a customer number? |
| A6 | Second copy: WORM shared folder on a NAS or another server (`[replica] target`, G9; an independent device, since the server is physical) + monthly encrypted copy to an external disk kept off site | Confirm per installation |
| A7 | Only the admin sees person names in usage reports until the lawyer approves the employee information text | Confirm |
| A8 | SMTP server filled in later; development uses Mailpit | Which server? |
| A9 | Public IP and ISP bridge mode: double NAT assumed until `nat.public_ips` / `nat.wan_transip` are set (G8) | Static IP? Bridge mode planned? (per installation) |

## MVP defaults proposed on 2026-10-13 (not yet confirmed)

Engineering defaults of the MVP that touch personal data or the evidence; each is implemented as
described in the linked document and waits for the owner's confirmation.

| # | Default | Where |
|---|---|---|
| B1 | A VLAN without a linked department (e.g. a staff Wi-Fi; in the tests' example plan VLAN 60) has the VLAN's name as its department; departments are linked by the administrator (none are shipped, P4) | [DATA-MODEL.md](DATA-MODEL.md) "Seed data" |
| B2 | ARP intervals are opened for any IP + MAC without an open interval, also for DHCP IPs (deviation from the literal spec 9.1 text) | [CORRELATION.md](CORRELATION.md) §1 |
| B3 | Scheduled sealing and retention runs reach the audit log through the worker's `audit.import`, up to 15 minutes after the run | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §8 |
| B4 | Evidence packages do **not** contain ARCILOGGER's own audit log by default (KVKK data minimisation); an administrator can include it explicitly for internal audits | [KANIT-PAKETI.md](KANIT-PAKETI.md) |
| B5 | An evidence package contains the complete sealed files of the cited days (needed for independent verification), so it also holds records of people outside the request | [KANIT-PAKETI.md](KANIT-PAKETI.md), [legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md) |
| B6 | TimescaleDB: only the Apache-2.0 part unless the operating organisation approves the Timescale License (TSL) | [KURULUM.md](KURULUM.md) §2.1 |

## Defaults proposed on 2026-10-19 (not yet confirmed)

| # | Default | Where |
|---|---|---|
| B7 | Scheduled database backup **daily at 01:40** local time (task `\ARCILOGGER\Backup`, after sealing at 00:10; stopped after 4 hours) | [ADR-0009](adr/0009-database-backup.md), `deploy/windows/tasks/Backup.xml` |
| B8 | Retention of the scheduled dumps: the newest of each of the last **14 days, 8 ISO weeks and 12 months** (`[backup] keep_daily`, `keep_weekly`, `keep_monthly`) | [YEDEKLEME.md](YEDEKLEME.md) §2 |
| B9 | Location **`D:\ARCILOGGER\backups\scheduled`** (`[backup] dir`), on the same disk as the database; copies off the server (external disk or a separate share on a NAS) are made by hand, weekly | [YEDEKLEME.md](YEDEKLEME.md) §4 |
| B10 | A backup is refused while less than **10 GB** (`[backup] min_free_gb`) plus the size of the newest dump is free | [ADR-0009](adr/0009-database-backup.md) §3 |
| B11 | Restores go only into a new database; a restore drill **every three months** | [YEDEKLEME.md](YEDEKLEME.md) §5–6 |
| B12 | `secret.key`, `arcilogger.toml` and the Caddy CA are copied by hand to an encrypted offline medium kept apart from the dumps, never next to them | [YEDEKLEME.md](YEDEKLEME.md) §3 |
| E1 | R15 thresholds: the source's `[[sources]] heartbeat_minutes` (commented example sources of the example configuration: firewall 10, core router 15; every other source 15 — each MikroTik sends `ARCI-HEARTBEAT` every 5 minutes); no R15 within 15 minutes after the worker started, a source row was created or its first message came (`alerts.r15_startup_grace_minutes`); a configured source that never sent alerts after 60 minutes (`alerts.r15_never_seen_minutes`); silence judged by the later of ingest's `last_seen` and the collector's last receive time | [ALERTS.md](ALERTS.md) §2–3 |
| E2 | R20 thresholds: disk more than 80 % used (critical from 95 %, resolved 2 points below), a closed day not sealed by 09:00 the next day, replica without success for 48 hours, more than 1 % unparsed lines of a source in an hour from 1,000 messages on, a worker job failing 3 times in a row, a database backup that failed or is older than 36 hours, no backup 48 hours after the installation (= the first `job_runs` row) | [ALERTS.md](ALERTS.md) §2–3 |
| E3 | Alert e-mail: high and critical alerts only (`alerts.email_min_severity`), also when resolved; alerts within 300 seconds in one e-mail; at most 20 e-mails per recipient per hour, then one summary; one e-mail per recipient; only alerts whose problem was last seen within the last 24 hours | [ALERTS.md](ALERTS.md) §6, [adr/0010-alerts-and-email.md](adr/0010-alerts-and-email.md) |
| E4 | Delivery: retries after 1, 5, 15, 60, 180 minutes, 6 attempts, then the e-mail is dropped and one `R20-email` alert opens; a 5xx refusal fails at once; authentication and TLS errors count as temporary | [ALERTS.md](ALERTS.md) §6 |
| E5 | E-mail content: only the rule catalogue and shape-checked facts (source name, day, count, threshold, drive letter, job name, a new sender's IP address, the sender address of a message refused for its transport (`wrong-transport`), the conflicting IP address of an IP conflict; the source name of a sender inside a configured network or of a sender that is not configured holds its IP address, e.g. `ap-<address>`, `unknown-<address>`); no alert title, no MAC address, no error text | [ALERTS.md](ALERTS.md) §5 |
| E6 | Link in the e-mails: `https://<[collector] bind>` when that is the server's own address, else `https://arcilogger.internal`, the installer's default server name (`alerts.web_base_url` empty) | [ALERTS.md](ALERTS.md) §3 |
| E7 | An `acknowledged` alert still counts as active: no new alert for the same problem, and it is resolved automatically when the problem is gone | [ALERTS.md](ALERTS.md) §1 |
| I1 | Data root of a new installation without `-DataRoot`: `D:\ARCILOGGER` on a ready, fixed NTFS `D:`, else `<system drive>\ARCILOGGER` with a warning that the evidence shares the system disk; removable, optical, network and RAM drives refused, a disk on USB warned; `-MinFreeGB` stays 100 (two years of evidence) | [KURULUM.md](KURULUM.md) §1.1, §17 |
| I2 | `-ServerAddress` / `-ServerName` remembered in `install\state.json` (a given parameter wins); without either, the address of the only up adapter with an IPv4 default gateway, else the preflight stops; a DHCP warning only when the address was detected; for installations from before this change `[collector] bind` of the existing configuration counts as the remembered address when it names one | [KURULUM.md](KURULUM.md) §1.2, §7; [ADR-0007](adr/0007-windows-installer.md) amendment |
| I3 | A new `arcilogger.toml` gets the server address as `[collector] bind` (not `0.0.0.0`); the preflight fails on a bind that is not an address of this computer and only warns on a loopback bind | [ADR-0007](adr/0007-windows-installer.md) amendment A2 |
| I4 | Desktop Windows (ProductType 1): AC sleep and hibernate timeouts never, hibernation and Fast Startup off, `HiberbootEnabled = 0`, network adapter power saving off where supported; battery values untouched; previous values restored on removal; Windows Update set by the operator to download and notify, restarts in the maintenance window | [KURULUM.md](KURULUM.md) §17 |
| I5 | Supported: Windows Server 2022/2025 and Windows 11 Pro/Enterprise/Education from build 26200; older Windows 11 builds warn (cut-off table reviewed with every release); Windows 10 and Home stop unless `-AllowUnsupportedWindows`; ARM64 warns (that `Win32_Processor.Architecture` reports ARM64 under emulation: verify on such a machine); a licence note for any client Windows, no conclusion | [ADR-0007](adr/0007-windows-installer.md) amendment A3 |
| I6 | The setup token is shown on a first installation only when a person sits at the console, after the transcript is closed; the Kamu SM question defaults to yes (`[E/h]`) and is asked only while neither credential is stored | [KURULUM.md](KURULUM.md) §7, §9 |
| I7 | New databases are created with `LOCALE 'C'` (code-point order in SQL comparisons and sorting); existing ones are not changed; the EDB installer's locale "C" is recommended | [KURULUM.md](KURULUM.md) §2 |
| I8 | Release build on GitHub Actions for `v*` tags: setup `.exe`, bundle zip, fingerprint, SBOM and `SHA256SUMS.txt`, published as a **draft** release that a person checks and publishes; the bundle carries the operator installers (Temurin 8 JRE) as the admin-PC build does | [ADR-0007](adr/0007-windows-installer.md) amendment A1 |
| I9 | Strict outbound mode derives the SMTP rule from the `[smtp]` section of the configuration (enabled and a host, port default 587) and remembers `-SmtpServer` | [KURULUM.md](KURULUM.md) §12 |
| D6 | The device-person **assignment history is only appended to**: a new assignment may not start before the current one (or the end of the last one) nor in the future; a wrong past entry cannot be corrected in the interface, only by a database administrator (ids D6–D13 continue after the owner decisions D1–D5) | [ADR-0008](adr/0008-device-assignment-history.md), [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |
| D7 | A 5651 lookup for a moment at which a device **with** assignment history was assigned to nobody names **no person** (not the current owner); only a device without any history falls back to its current owner, with the note `person_link_at_lookup_time` | [CORRELATION.md](CORRELATION.md) §2 |
| D8 | Links that existed before the history start at the device's first sighting (`backfilled`; the report says the real start is not recorded) | [DATA-MODEL.md](DATA-MODEL.md) |
| D9 | No department history: the department in a lookup is the person's current department (the report says so) | [ADR-0008](adr/0008-device-assignment-history.md) |
| D10 | The assignment CSV import never creates a device: a MAC that has not been seen on the network is a line error (a typo cannot add a device) | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |
| D11 | Deactivating a person does not unassign their devices (a separate step); inactive persons get no new devices | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |
| D12 | Devices and persons stay **administrator-only** (`devices.*`); reading the lists is not audited, changes, applied imports and CSV exports are — with ids and counts, never names or free text; import previews (dry runs) are not audited | [roles.md](roles.md) |
| D13 | Device effective department: the owner's, else the department linked to the device's VLAN; a VLAN without a linked department (e.g. a staff Wi-Fi) gives none in the device list (the 5651 chain still shows the VLAN name) | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |

## Defaults proposed on 2026-10-20 (not yet confirmed)

| # | Default | Where |
|---|---|---|
| U1 | Usage buckets of **1 minute** (site time); a minute is **active** with at least **2048 bytes** of non-background traffic (`usage.active_bytes_threshold`); a session below **2 KB** in total is background (`usage.background_session_bytes`) — the specification's starting values | [USAGE.md](USAGE.md) §1-2 |
| U2 | Destination naming: web filter host name → application control / SSL host name (same `sessionid`) → the newest DNS answer containing the address at most **600 s** before the session (`usage.dns_window_seconds`; an answer logged up to 2 s after the derived start still counts) → `dstinetsvc` → the address | [USAGE.md](USAGE.md) §2.2 |
| U3 | **No reverse DNS** for unnamed addresses (spec 10.2 step 5): the server sends no DNS queries about its users' destinations; such sessions show their address | [adr/0011-usage-analytics.md](adr/0011-usage-analytics.md) |
| U4 | The application-category background rule (`Update`, `Network.Service`) applies **only to sessions without a host name** — FortiOS 7.6 can log ordinary HTTPS browsing as `SSL_TLSv1.3.PQC` / `Network.Service` (verify on site) | [USAGE.md](USAGE.md) §2.4 |
| U5 | Background host patterns: the spec 10.3 list (`*.windowsupdate.com`, `*.update.microsoft.com`, `*.events.data.microsoft.com`, `*.push.apple.com`, `*.icloud.com`, `*.googleapis.com`), OCSP/CRL (`ocsp*.*`, `crl*.*`) and, **to verify on site**, `*.fortiguard.net`, `*.fortinet.net`, `upgrade.mikrotik.com`, `download.mikrotik.com`; ports 53, 123, 853. The update and cloud hosts of the organisation's other products (IP phone system, antivirus …) depend on the product and are added by the administrator; `*.googleapis.com` may hide app traffic (e.g. the YouTube app) | [USAGE.md](USAGE.md) §2.4, §9 |
| U6 | Refused connections (`action` `deny`, `ip-conn`) are no sessions; blocked attempts are web filter `blocked`/`block`, DNS filter `block`/`blocked`, application control `block`/`blocked`/`reset` (verify the values on site); a blocked web attempt that is also refused by the DNS filter counts twice (one event per log, as spec 10.3 lists them) | [USAGE.md](USAGE.md) §1, §9 |
| U7 | Work hours Monday-Friday **08:00-18:00**; holidays: only the **fixed-date national holidays** (1 Jan, 23 Apr, 1 May, 19 May, 15 Jul, 30 Aug, 29 Oct) are shipped — the religious holidays change every year and are entered by the administrator; half days (eves) cannot be expressed | [USAGE.md](USAGE.md) §2.5 |
| U8 | Logs up to **30 minutes** after midnight count for the day before (`usage.session_overlap_seconds`); yesterday is rebuilt final at **00:45**; a session without interim logs ending later loses its part before midnight | [adr/0011-usage-analytics.md](adr/0011-usage-analytics.md) |
| U9 | Changed assignments, leases, late logs and `usage.*` settings are applied automatically to the last **7 days** (`usage.auto_rebuild_days`); older days need a manual rebuild | [USAGE.md](USAGE.md) §5 |
| U10 | Rollups kept **400 days** (`usage.retention_days`); per subject and day at most **1000 URLs** and **5000 blocked-attempt rows** (the daily counts stay complete) | [USAGE.md](USAGE.md) §4 |
| U11 | Department of a person without one, of a device and of a bare address: the department of the VLAN with the most online minutes (linked department, else the VLAN's name; a subject with only blocked attempts or URLs that day: the VLAN of those events); none → `Bilinmiyor`. Excluded persons (`usage_excluded`) still count in company, department, site and category totals | [USAGE.md](USAGE.md) §2.1, §6 |
| U12 | Registrable domains from the ICANN section of the Public Suffix List only (`usage.psl_private_suffixes` off) | [USAGE.md](USAGE.md) §2.2 |
| U13 | The `users` lists of a site or category carry each user's metrics **on that site / category only**: active = the time on it, with its off-hours part, volumes and sessions; online = a site's raw time, and **0 for a category** (the rollups keep no minutes with traffic on any of its sites); blocked attempts 0 and no first/last activity (neither is kept per site); the detail lists the 200 users with the most time (`users_total` counts all), the exports all of them | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §12 |
| U14 | A session counts once per subject, in the part (work / off hours) of its first minute; a session over midnight counts on both days; "active users" counts subjects (person, else device, else address) with active time | [USAGE.md](USAGE.md) §1 |
| U15 | Report "notable changes": at least **50 %** against the average of the rolled-up days among the 7 before the period, with at least **1 hour** or **1 GiB** on one side, at most 10 | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §12 |
| U16 | Viewing one person's or device's usage detail is audited (`usage.view`, spec 8.5: "every person-level usage report viewed"); lists are not | [USAGE.md](USAGE.md) §6 |
| U17 | The role **Rapor İzleyici** (viewer: `usage.anonymous`, no `usage.person`) sees names by the owner decision, and the owner decision keeps the KVKK notice on person-level endpoints: so the viewer accepts the notice once per version like the other roles (`usage.anonymous` is in `PRIVACY_PERMISSIONS`; all four roles accept it) | [roles.md](roles.md) |
| U18 | Pseudonym key: the administrator stores `usage.pseudonym_key` (`arcilogger-core secret set usage.pseudonym_key`); without it pseudonymised views answer 409 instead of falling back to names | [USAGE.md](USAGE.md) §6 |
| S1 | Alert transitions: Yeni → İnceleniyor / Kapandı / Yanlış alarm; İnceleniyor → any; Kapandı ↔ Yanlış alarm; a closed alert only back to Yeni (re-open), and only while no other active alert holds the same dedup key | [ADR-0010](adr/0010-alerts-and-email.md) amendment A3, [ALERTS.md](ALERTS.md) §8 |
| S2 | A person closing an alert whose problem persists does not silence it: the check's next run opens a new alert (and a new e-mail from `alerts.email_min_severity`); operators are told to use İnceleniyor while they work on it | [ALERTS.md](ALERTS.md) §8 |
| S3 | Notes: 1–2000 characters, any state, never edited or deleted; the audit log keeps only their length; no assignee field yet | [ADR-0010](adr/0010-alerts-and-email.md) amendment A2 |
| S4 | Alerts stay **administrator-only** (`alerts.read`, `alerts.manage`); on the dashboard the auditor sees log intake, seal, credit and clock, the report viewer only the log intake, HR gets no dashboard (only its usage widgets) | [roles.md](roles.md) |
| S5 | Mean time to close: from `created_at` to the newest closing event (active → resolved/dismissed, by a person or a check; a later resolved ↔ dismissed switch is no closing) of the alerts closed now and last closed within 30 days; alerts closed before revision `0012_alert_events` do not count | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| S6 | Dashboard log intake: messages counted by device time over the 60 whole minutes before the current one (`messages_per_minute` = their mean); a watched source is silent as soon as its heartbeat passed (the alert's grace periods do not apply to the box) | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| S7 | Dashboard seal calendar: the 30 days ending yesterday; late counts as sealed, today's open day as pending, deleted as no data; the serial shown as its first 8 hex digits | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| S8 | Days the Kamu SM credit lasts: the use per day measured over the 30 days of checks before the newest (purchases ignored), else one stamp a day | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| S9 | Clock box: a clock sample older than 3 hours (the probe runs hourly) says nothing about now (`ok: null`) | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| S10 | Alert details on the web: secret-looking keys and `password=…` pairs redacted, e-mail addresses masked (`a***@firma.com.tr`), texts cut at 1,000 characters, lists at 100 items | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §14 |
| R1 | The four shipped report schedules: Gün sonu raporu daily **18:30** (today until then), Tam gün raporu daily **07:00** (previous day), weekly **Monday 07:15** (previous ISO week), monthly **day 1 07:30** (previous month); disabled until recipients are set; all three formats on | [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md) §1, migration `0011_reports` |
| R2 | **One e-mail per recipient** (no Bcc): no recipient sees the others, a refused address fails alone; retries after 1, 5, 15, 60, 180 minutes, 6 attempts, then `R20-report` (medium, local) | [ADR-0012](adr/0012-scheduled-usage-reports.md) §5 |
| R3 | Attachments up to **15 MB** in total (`reports.max_attachment_mb`, the size before encoding: the e-mail is about 1.37 times larger, so at most two thirds of the mail server's limit), above that a download link instead | [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md) §3 |
| R4 | Report files kept **400 days** (`reports.keep_days`) in `exports\usage-reports\`, never in the vault; run rows and hashes kept | [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md) §4 |
| R5 | After downtime a schedule runs **once** for its newest missed occurrence (`reports.catch_up` true); with catch-up off, more than 30 minutes late is skipped | [ADR-0012](adr/0012-scheduled-usage-reports.md) §8 |
| R6 | A report lists the alerts of its period by catalogue name and sentence only (never an alert's title or details) and the sealing status of the day before the run | [ADR-0012](adr/0012-scheduled-usage-reports.md) §9 |
| R7 | The e-mail shows the top **10** company sites and categories and per person the top 5 sites and 3 categories; the PDF per person the top 20 sites and at most 100 blocked attempts; the workbook everything (blocked attempts: at most 50 000 per report, oldest first, a cut list says so; of a day over `usage.blocked_per_day` only the stored rows are listed, and a part-day report can count only those: a data quality note says so) | [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md) §2 |
| R8 | "Run now" and the preview: a period given by hand covers at most **92 days** and ends at most now; a time without an offset is Türkiye time; a schedule has at most 50 recipients | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §13 |
| R9 | Excel volumes are **bytes** with a thousands format (`#,##0`), not converted to MB/GB (Excel formats cannot divide by 1024; the PDF and e-mail show binary units) | [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md) §2 |
| WR1 | The Güvenlik page offers only the changes S1 allows, with spec 11's names (Yeni, İnceleniyor, Kapandı, Yanlış alarm); the note of a change is optional; next to closing a hint says that a persisting problem opens a new alert (S2); a re-open refused with `alert_key_active` links to the active alert | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §15 |
| WR2 | Panel: the live blocks refresh every 30 seconds and the usage blocks every 5 minutes, as background requests and never while the tab is hidden; a block the API leaves out is not shown (no error); company time sums are shown in whole hours from one day on ("186 sa") | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §15 |
| WR3 | "Şimdi çalıştır" and "Önizle" default to the kind's own period now, as the API computes it without `from`/`to` (end of day: today until now; full day: yesterday; the previous week or month); a chosen period without an end time ends with that day (now, when it is today) and makes a `custom` run | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §15 |
| WR4 | The Raporlar page checks R8's limits (50 recipients, 92 days, not after now) before sending; the server decides | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §15 |
| WU1 | The **Kullanım** page opens on **today** (site day) and all hours; tab, period, hours and the opened department / person / device / site / category are kept in the address (bookmarkable, Back returns), a table's search text never | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §16 |
| WU2 | Period presets resolve in Europe/Istanbul; **weeks start on Monday** ("Bu hafta" = Monday to today, "Geçen hafta" = the Monday–Sunday before); a custom range never goes past today | `web/src/pages/usage/usage-model.ts` |
| WU3 | Usage lists are read in full in one request (`limit` 100,000, `MAX_LIMIT` in `routers/usage.py`: the period is aggregated once) and searched and sorted in the browser; the search matches what the API's `q` matches (Turkish lower case only, the whole text in the name, MAC, IP or host name; a site's domain), so a searched export holds the rows on the screen; a view whose period includes today refreshes every **5 minutes** | `web/src/lib/api-hooks.ts` |
| WU4 | The 30-day heat map uses a **fixed scale**: 60 active minutes in an hour is the darkest step, steps of 12 minutes, so colours compare across persons and periods | `web/src/pages/usage/heatmap-model.ts` |
| WU5 | The day timeline gives the seven categories with the most minutes of that day their own colour, the rest share "Diğer" | `web/src/pages/usage/timeline-model.ts` |
| WU6 | Every view but Şirket waits for the KVKK notice (as the API requires); the Şirket tab exports `view=company` (no notice needed). A table that is not its view's main table offers only the Excel export (the CSV holds the main table only) | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §16 |
| V1 | A VLAN's **subnet can be changed** (`PATCH /vlans` `new_subnet`): a corrected network keeps the VLAN's name, department and flags; nothing refers to a `vlans` row (leases, devices and rollups keep VLAN numbers), so the change is a plain update of the row | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |
| V2 | **Nested subnets are allowed** (a `/25` inside a `/24`, a `/16` around several `/24`): an address belongs to the most specific one, the rule every consumer already uses (`arcilogger_db.network.find_vlan`); only the same network twice is refused (409 `vlan_exists`). Networks are written with their prefix length and the host bits zero; IPv6 zone ids and IPv4 networks written as IPv6 (`::ffff:10.0.20.0/120`, which no IPv4 address falls into) are refused | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |
| V3 | **Deleting a VLAN** is allowed without a check of its devices (no history depends on the row): its devices lose the VLAN name and the VLAN's department, raw logs, leases and stored 5651 results keep what they hold; the confirmation says so | [ILK-ADIMLAR.md](ILK-ADIMLAR.md) §12 |
| V4 | The audit entries of VLANs keep subnets, VLAN ids, department ids and the guest flag; the texts (name, purpose, DHCP server, notes) only as field names, like a department's name | [MVP-CONTRACTS.md](MVP-CONTRACTS.md) §11 |

## Technical assumptions to verify on site

- MikroTik remote syslog wire format (BSD header, ISO-8601 time, topics string) — parser stays
  tolerant until the wire format has been seen on a device.
- The core router sends syslog from the address its `[[sources]]` entry names (a router with
  several VLAN interfaces may use another one: check with a test message, DEVICE-SETUP §4).
- TimescaleDB Windows build for PostgreSQL 16 installs cleanly on the chosen Windows version.
- The replica target supports WORM (write once) shared folders and lets a freshly written file be
  renamed, where one is used (KURULUM §10, "Replika").
- Reports "Önizle": the PDF opens as a `blob:` page in a new tab; with Caddy's Content Security
  Policy (`object-src 'none'`) check on the office browsers that the built-in PDF viewer shows it
  (a blocked tab falls back to a download).

## Deviations from the specification (technical)

- **No `/api/docs` page** (spec 2: "OpenAPI docs at `/api/docs` (admin only)"): FastAPI's Swagger
  UI loads its scripts from a public CDN, which spec 0.4 forbids, so the API serves neither the
  page nor an unauthenticated `/api/openapi.json`. The schema is exported in the repository
  (`web/scripts/export_api_contract.py` → `web/src/generated/api-schemas.json`); a bundled,
  admin-only page can be added later if wanted. / Swagger sayfası dış CDN'den yüklendiği için
  sunulmuyor; API şeması depodaki dışa aktarılmış sözleşmededir.
