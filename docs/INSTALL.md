# Installing ARCILOGGER (Windows Server and Windows 11 Pro)

> Türkçe (primary): [KURULUM.md](KURULUM.md). Design decisions: [ADR-0007](adr/0007-windows-installer.md).
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); have the setup and the retention arrangement reviewed by a lawyer.

From a fresh Windows Server, or a Windows 11 Pro PC dedicated to ARCILOGGER, to a working
ARCILOGGER. What differs on desktop Windows (hardware, power, updates, single disk, licence) is in
section 17; the acceptance test is [KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md) (Turkish,
with an English summary). The **installation** needs no internet access on the server: everything is
installed from an **offline bundle** built on the admin PC. **In operation** the server has no
general internet access, but the nightly sealing needs **permanent** access to the Kamu SM
time-stamp server (`zd.kamusm.gov.tr`, test `tzd.kamusm.gov.tr`, TCP 80; only hashes are sent) and
to the Kamu SM certificate host (`sertifikalar.kamusm.gov.tr`) ([DEVICE-SETUP.md](DEVICE-SETUP.md)
section 1, flows 6–7). Nothing is installed on user PCs; people use a browser only. Allow about
1–1.5 hours (without Windows itself).

After the installation (first administrator, Kamu SM test → production, first 5651 lookup, daily
checks): [FIRST-STEPS.md](FIRST-STEPS.md). Drafts for the company's lawyer, before the pilot:
[employee information text](legal/aydinlatma-metni-taslak.md),
[retention and deletion policy](legal/saklama-imha-politikasi-taslak.md) (Turkish).

**Licence:** a new installation starts with a 14-day trial, every module on. The installer
installs everything whatever the licence says; the product decides at run time which paid modules
run, and the 5651 functions always run. Request code, licence file, modules and maintenance:
[LICENSING.md](LICENSING.md).

| # | Step | Where |
|---|---|---|
| 1 | Disks, network | Server |
| 2 | PostgreSQL 16 (+ optional TimescaleDB) | Server |
| 3 | Java 8 for the Kamu SM Zamane client | Server |
| 4 | Download the release (GitHub) or build the bundle | Admin PC (internet) |
| 5 | Download the Zamane client from Kamu SM | Admin PC |
| 6 | Copy and verify the bundle | Server |
| 7 | Install ARCILOGGER | Server |
| 8 | Install the root certificate on the admin PC | Admin PC |
| 9 | First login (first administrator) | Server + browser |
| 10 | Kamu SM connection (test → production) | Server |
| 11 | Device setup | FortiGate, MikroTik |
| 12 | Checks after installation; then [FIRST-STEPS.md](FIRST-STEPS.md) | Server |

## 0. Requirements

- **Server:** dedicated physical machine, Windows Server 2022 or 2025 Standard (recommended) or
  Windows 11 Pro, Enterprise or Education on a PC dedicated to ARCILOGGER (25H2, build 26200 or
  later; section 17), 4 cores, 16 GB RAM, `C:` ≥ 100 GB, data disk `D:` ≥ 1 TB NTFS (RAID 1
  recommended), on a UPS. Windows 10 and Windows 11 Home are not supported: the preflight stops
  (`-AllowUnsupportedWindows` makes it a warning). Without a `D:` the installer uses
  `C:\ARCILOGGER` and warns that the evidence shares the disk of Windows (section 17). At least
  100 GB must be free: the raw logs and the vault are kept for at least two years (730 days) and
  grow.
- **Network:** a static IP address (or a DHCP reservation) for the server, in the network the
  devices can send syslog to; the switch port untagged in that network's VLAN; flows in
  [DEVICE-SETUP.md](DEVICE-SETUP.md) section 1.
- **The organisation's own values** (not shipped, entered at installation; section 7.1): the
  organisation name (`-SiteName`), the local time source (`-NtpServer`, usually the core
  router), the admin PC's address or network (`-AdminAddress`). After the installation: the
  addresses of the log sources (`[[sources]]`), the departments ([FIRST-STEPS.md](FIRST-STEPS.md)
  section 5).
- **Admin PC** (the computer that uses the web interface and remote desktop): internet to download
  the setup from the GitHub release or to build the bundle; only to build it yourself Git, uv,
  Node.js 22 with npm 11.21, optionally Inno Setup 6.3+ (section 4).
- **Kamu SM:** request a test user ([kamusm.md](kamusm.md) section 4).
- Windows Update applied. General internet access (Windows Update) is opened only in a
  maintenance window; the Kamu SM flows (time stamps, certificate host), NTP and DNS stay open
  **permanently** ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 1).

### 0.1 Supported network setups

Before installing, check that your network fits what this version supports. Whatever the
network, the logs of every device in `[[sources]]` are collected, kept **unchanged** and sealed
every night; the points below decide whether a 5651 lookup reaches the device and the person, and
what the usage analytics show.

- **One site:** an installation covers one site: an IPv4 network that reaches the internet through
  one FortiGate and gets its addresses from the DHCP server of a MikroTik router or of the
  FortiGate. Several branches that use the same private address range are not supported in one
  installation: the same network cannot be entered twice in the VLAN list, and the same address in
  two branches counts as one address (it looks like an IP conflict). A network segment that
  reaches the internet through another line or a VPN tunnel (e.g. guest Wi-Fi) has no FortiGate
  records: only its leases (DHCP, `ARP-LOG`) arrive; a 5651 lookup by public IP and port cannot
  find a device in it, and the usage analytics do not see that traffic
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 5).
- **Person and device matching (lease evidence):** the step from the internal IP address to
  the MAC address, the device and the person rests on the DHCP server's lease records:
  - **MikroTik as DHCP server:** the main MikroTik router's lease records (`assigned` /
    `deassigned`) are used; the hourly `ARP-LOG` script on the main router (optional, written
    on site; [DEVICE-SETUP.md](DEVICE-SETUP.md) section 3) adds static-IP devices and the gaps
    between lease records with **medium confidence**.
  - **FortiGate as DHCP server (FortiGate-only networks):** the FortiGate DHCP server's event
    records (DHCP Ack / Release) feed the lease timeline. Their format comes from the FortiOS
    documentation and one community sample; it is verified on site at the first installation
    ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4), and until then reports say so. Only
    records of sources configured as a FortiGate are used. Every network the FortiGate serves
    by DHCP is entered on the Cihazlar ve Kişiler page, even without a VLAN number: chains of
    addresses outside the entered networks are shown with low confidence. A network added or
    changed later applies to past leases only once the leases are rebuilt
    ([FIRST-STEPS.md](FIRST-STEPS.md) section 5). Static-IP devices have no lease: the chain
    ends at the internal IP; on interfaces whose gateway is the FortiGate, after a site check
    with two clients, the srcmac field of the FortiGate session record can optionally give the
    MAC address as **partial evidence**, with low confidence
    ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5).
  - **Both together:** when the two DHCP servers serve different networks, both are used.
  - **Another DHCP server (e.g. Windows):** this version does not read its records; leases come
    only from MikroTik `ARP-LOG`, with medium confidence. Without `ARP-LOG` a 5651 lookup ends
    at the FortiGate session and the internal IP address (no MAC, device or person), and usage
    analysis counts by IP address.
- **Internet exits (NAT):** the FortiGate's source NAT is supported. Each internet line is
  defined in Settings → Application settings → 5651 lookup and lease timeline → Internet exits
  (NAT): the public IP (and whether it may change), the FortiGate's NAT address on that line,
  its interfaces, the NAT type and port preservation. When the public address is on the
  FortiGate itself (direct), public IP + port identify the session **exactly**. When the ISP
  router does a second NAT, lookups for that line rank **candidate** sessions
  ([CORRELATION.md](CORRELATION.md) section 3); when the public IP is shared with other
  customers (CGNAT), the candidate list may not contain the right session, and an exact answer
  needs the ISP's NAT record. 5651 lookups by IPv6 address are not supported: lease records
  (DHCP, `ARP-LOG`) cover IPv4 addresses only.
- **Usage analytics** (paid module, [LICENSING.md](LICENSING.md)) are computed from FortiGate logs
  only: time and bytes from the traffic logs (`logtraffic all`; with interim logs for long
  sessions), site names from the web filter, DNS filter, application control and SSL inspection
  logs, blocked attempts from the first three of them ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2,
  [USAGE.md](USAGE.md)). Only traffic from private source addresses (the RFC 1918 blocks and IPv6
  ULA) is counted: internal networks on other ranges (e.g. 100.64.0.0/10 or public addresses used
  internally) are not analysed. Logs of other vendors' firewalls are not analysed.
- **Devices of other vendors** (`type = "generic"` in `[[sources]]`): their logs are collected,
  kept unchanged, sealed and included in full-day evidence packages; they are loaded into the
  database as plain log lines (time, host name, program, message text) but not parsed into
  fields, and neither the 5651 lookup nor the usage analytics use them.

**Not supported:** IPv6 lookups; two DHCP servers on one network (ARCILOGGER warns when it
notices and gives the chains of those moments with low confidence); FortiGate leases where the
FortiGate is a DHCP relay (lease evidence must come from the actual DHCP server); several
branches using the same private address range; lookups for lines that do not pass the
FortiGate (they are only marked "does not pass the FortiGate", and a lookup lists nobody);
srcmac behind a router or an L3 switch (there it is that device's MAC address; with only
layer-2 switches in between an interface is used only after a site check with two clients).

## 1. Prepare the server (elevated PowerShell)

Data disk — **this erases the selected disk**, check the number twice:

```powershell
Get-Disk
Initialize-Disk -Number 1 -PartitionStyle GPT
New-Partition -DiskNumber 1 -UseMaximumSize -DriveLetter D
Format-Volume -DriveLetter D -FileSystem NTFS -NewFileSystemLabel ARCIDATA -Confirm:$false
```

The data disk must be a fixed (internal) NTFS disk: the installer refuses removable drives (USB
sticks, memory cards), optical, network and RAM drives, warns about a disk on USB and wants at least
100 GB free (the evidence is kept for two years; `-MinFreeGB` lowers it once you have checked the
sizing). Without `-DataRoot` it takes `D:\ARCILOGGER` on a fixed NTFS `D:`, otherwise
`C:\ARCILOGGER`, and writes it into the new `arcilogger.toml`. On a PC with one disk skip this step
(risks and recommendations: section 17).

Network and name:

```powershell
# replace <...> with the values of your network (prefix length e.g. 24)
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress <server address> -PrefixLength <prefix> -DefaultGateway <gateway>
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses <DNS server>
Rename-Computer -NewName ARCILOGGER -Restart
```

The devices send their logs to this address: if it changes, nothing is recorded. A DHCP reservation
on the DHCP server (e.g. the core router) works too. Without `-ServerAddress` the installer takes
the address remembered from an earlier run, else the address of the **only** network adapter with a
default gateway (with a warning when DHCP assigned it); with several such adapters it does not guess
and asks for `-ServerAddress`. A new `arcilogger.toml` gets this address as `[collector] bind`.

Time zone and time sync are set by the installer (`Set-ArciTime.ps1`): Windows Time follows the
local time source given with `-NtpServer` (`[clock] ntp_server`; usually the core router), with
`time.ume.tubitak.gov.tr` (`[clock] fallback_server`) only as the fallback. Without a local time
source the clock follows and is measured against that public server alone; the preflight and
`config-check` report it as a warning (section 7.1). The server must reach it on UDP 123
([DEVICE-SETUP.md](DEVICE-SETUP.md) section 1, flows 5 and 8).

Sealing does not trust the server clock blindly: the chain start comes from the sealing records
and never moves back; a spool file of an old day, of a sealed day or of a future date is not
sealed, is left in place and raises one critical `R20-clock` alert (check the clock and the file;
do not delete it). A day is sealed only when the time-stamp authority's time (genTime) says the
day has ended; during the first `seal.quiet_seconds` after midnight the task just waits (no failed
attempt, no alert). At most `seal.max_consecutive_empty_days` consecutive days without any log
(default 7) are stamped one by one; a longer run is not stamped, raises one critical
`R20-seal-skip` alert and is listed as `unsealed_empty_days` in the next sealed day's manifest
(the verifiers follow the chain across it). Because that skip rests on the server clock alone it
stays provisional until the next day is stamped: when data appears for a skipped day, when the
day has not ended by the corrected clock, or when the run is no longer longer than the limit, the
skip is withdrawn and the day is sealed normally (an `R20-seal-skip` alert with an `unskip:` key).
The skipped days are checked again just before and after the next day is stamped, so a manifest
never lists a day with data as empty. Once the next day is stamped the skip is final: data that
still appears for such a day is not sealed, stays in the spool and raises a critical `R20-clock`
alert — do not delete it; copy it, record its SHA-256 and document the clock problem (the chain
has passed that day, so ARCILOGGER cannot add the file to it afterwards).
Setting `seal.first_day` at installation is recommended: before the first seal, a back-dated
spool file can still set the chain start. Spool files of days before `seal.first_day` (test
messages before go-live, for example) are not sealed, stay in place and raise a single `info`
`R20-clock` alert.

## 2. PostgreSQL 16

ARCILOGGER does not install the database server; you install PostgreSQL with the vendor's (EDB)
installer and ARCILOGGER creates its own role and database (why: ADR-0007 section 3).

1. Download the latest PostgreSQL **16** Windows x64 installer from
   <https://www.enterprisedb.com/downloads/postgres-postgresql-downloads> (linked from
   postgresql.org's Windows page). Check its signature (`Get-AuthenticodeSignature`: `Valid`,
   signed by EnterpriseDB — **verify** the exact signer name). ARCILOGGER could not verify a SHA-256
   for it (`null` in `components.json`); to bundle it anyway pass `-ComponentUrl` and
   `-ComponentSha256` to `Build-ArciBundle.ps1` after checking the value on the vendor site.
2. Run it as Administrator: *PostgreSQL Server* and *Command Line Tools*; data directory
   `D:\ARCILOGGER\db` (`C:\ARCILOGGER\db` on a PC without `D:`); port 5432; a strong superuser
   password kept in a password manager, **not** on the server (ARCILOGGER asks for it once and
   never stores it). **Locale: choose `C`** in the list, not *[Default locale]*. The default is
   the Windows locale; on a Turkish Windows a Windows update has renamed it before
   (`Turkish_Turkey` → `Turkish_Türkiye`) and PostgreSQL clusters created with the old name then
   failed to start (PostgreSQL BUG #18196). The name `C` does not change. ARCILOGGER creates its
   own database with the `C` locale whatever the cluster uses, and never alters an existing one.
3. Set `listen_addresses = 'localhost'` in `D:\ARCILOGGER\db\postgresql.conf`, then
   `Restart-Service postgresql-x64-16`. The firewall script also disables every inbound allow
   rule other than ARCILOGGER's (EDB's 5432 rule included).
4. **Antivirus (Microsoft Defender).** Real-time scanning is on by default on Windows Server.
   PostgreSQL community reports (mailing lists, the PostgreSQL wiki's Windows page) describe
   "could not rename/open file … Permission denied" on WAL and data files while a scanner holds
   them, and recommend excluding the data directory and the server process. The ARCILOGGER
   installer does **not** do this; you do (**verify** the path and process name with the
   installed PostgreSQL 16):

   ```powershell
   Get-MpComputerStatus | Format-List AMServiceEnabled, RealTimeProtectionEnabled
   Add-MpPreference -ExclusionPath 'D:\ARCILOGGER\db'
   Add-MpPreference -ExclusionProcess 'C:\Program Files\PostgreSQL\16\bin\postgres.exe'
   (Get-MpPreference).ExclusionPath; (Get-MpPreference).ExclusionProcess
   ```

   With another antivirus or EDR product set the same two exclusions in its console. Keep them
   narrow (the database folder and the PostgreSQL process only). `spool` and `vault` stay scanned
   by default: the collector keeps the day's file open, and sealing and ingest retry on their own
   when a scan blocks them; excluding them, or scanning them only on a schedule, is the owner's
   decision. Defender exclusions do not change the vault's SACL auditing; tampering is still
   shown by the hashes, the time stamps and the Windows Security log.

**TimescaleDB (optional).** ARCILOGGER works on plain PostgreSQL. TimescaleDB's compression and
retention features are under the Timescale License (source-available, not OSI): the owner must
approve it first; otherwise set `timescaledb.license = 'apache'`. In any case add
`timescaledb.telemetry_level = off` to `postgresql.conf` (the installer also sets it for its
database before creating the extension, and server-wide with `ALTER SYSTEM` once it exists). The
nightly backup (`pg_dump`) locks every chunk in one transaction and their number grows every day:
add `max_locks_per_transaction = 256` too (default 64; **verify**, [BACKUP.md](BACKUP.md)
section 7). Built with
`Build-ArciBundle.ps1 -IncludeTimescaleDB` (and only then), the bundle carries the official
`timescaledb-postgresql-16-windows-amd64.zip` (2.30.2, checksum in `components.json`): unzip, run
`timescaledb\setup.exe` as Administrator (**verify** with the installed 16.x), restart PostgreSQL.
Added after ARCILOGGER? Run
`psql -U postgres -d arcilogger -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"`.

## 3. Java 8 (Kamu SM Zamane client)

```powershell
msiexec /i C:\Kurulum\ARCILOGGER-0.1.0\operator-installers\temurin8-jre\OpenJDK8U-jre_x64_windows_hotspot_8u502b07.msi INSTALLDIR="C:\Program Files\Eclipse Adoptium\jre-8\" /qn /norestart /l*v C:\Windows\Temp\temurin8.log
& "C:\Program Files\Eclipse Adoptium\jre-8\bin\java.exe" -version
```

A fixed folder keeps `[tsa] java` in `arcilogger.toml` valid across Java updates (**verify** the
MSI's `INSTALLDIR` property; its default is `...\jre-8.0.502.7-hotspot\`).

## 4. Download or build the bundle (admin PC)

### 4.1 Download the GitHub release (recommended)

When the repository gets a `v<version>` tag, GitHub Actions (`.github/workflows/installer.yml`,
job "Release build", after the installer tests and the end-to-end installation have passed) builds
the bundle and prepares a **draft** GitHub release; whoever checks the
job log and the values publishes it. Whoever releases bumps `__version__` and `RELEASE_DATE`
together in `core/src/arcilogger_core/__init__.py` in the tagged commit: the job stops a tag whose
`RELEASE_DATE` is more than 14 days before the commit's day or after it (the licence's
maintenance is compared with it; checklist in [ADR-0007](adr/0007-windows-installer.md), A1). The release carries:

| File | Content |
|---|---|
| `ARCILOGGER-Setup-<version>.exe` | Single-file setup (double-click; section 7) |
| `ARCILOGGER-<version>.zip` | The same bundle as a folder (`ARCILOGGER-<version>\`, for the script path) |
| `ARCILOGGER-<version>.fingerprint.txt` | Bundle fingerprint (SHA-256 of the bundle's `SHA256SUMS`) |
| `ARCILOGGER-<version>.spdx.json` | Software bill of materials (SBOM, SPDX) |
| `SHA256SUMS.txt` | SHA-256 of the files above |

The admin PC then needs no Git, uv, Node.js or Inno Setup. After the download check the hashes;
the values are in `SHA256SUMS.txt`, in the release notes and in the job summary (Actions → run →
*Summary*):

```powershell
Get-FileHash .\ARCILOGGER-Setup-0.1.0.exe, .\ARCILOGGER-0.1.0.zip -Algorithm SHA256 | Format-List Hash, Path
Get-Content .\SHA256SUMS.txt
```

(On Linux or Git Bash: `sha256sum -c SHA256SUMS.txt`.) If a value differs, do **not** use the file.
Write down the bundle fingerprint (`.fingerprint.txt`): the installer prints the same value on the
server (section 6). On this path trust moves to the build job that builds the bundle from the
tagged code on GitHub's runner ([ADR-0007](adr/0007-windows-installer.md), "Amendment
2026-10-19").

### 4.2 Build it yourself

Once: Git for Windows, uv (<https://docs.astral.sh/uv/>), Node.js 22 LTS then
`npm install --global npm@11.21.0`, optionally Inno Setup 6.3+.

```powershell
git clone <ARCILOGGER repository URL> arcilogger
cd arcilogger
powershell -ExecutionPolicy Bypass -File deploy\windows\installer\Build-ArciBundle.ps1
```

It downloads and verifies the components of `components.json` (stops on any checksum mismatch;
TimescaleDB only with `-IncludeTimescaleDB`) and their tag-pinned licence texts (into
`licenses\`, installed with the programs together with `THIRD-PARTY-NOTICES.txt`), builds the
ARCILOGGER wheels, downloads every dependency pinned in `uv.lock` for Windows /
CPython 3.12 with hash checking, builds the web interface, and writes `dist\ARCILOGGER-<version>\`
with `bundle-manifest.json` and `SHA256SUMS`. It prints the **bundle fingerprint** (SHA-256 of
`SHA256SUMS`, also in `dist\ARCILOGGER-<version>.fingerprint.txt`) — write it down. With Inno Setup
installed it also builds `dist\ARCILOGGER-Setup-<version>.exe` (`-RequireInno` stops when Inno
Setup is missing).

## 5. Zamane client

Download the ZIP from Kamu SM's "Ücretsiz Zaman Damgası İstemci Yazılımı" page, note the
**SHA-256** published there, extract `tss-client-console-3.1.33.jar`. It never goes into the
repository or the bundle.

## 6. Copy and verify

Copy the folder to e.g. `C:\Kurulum\ARCILOGGER-0.1.0` (not into `D:\ARCILOGGER`), with the jar:

```powershell
Get-ChildItem -Recurse C:\Kurulum | Unblock-File
Get-FileHash C:\Kurulum\ARCILOGGER-0.1.0\SHA256SUMS -Algorithm SHA256   # = bundle fingerprint
```

Copying only `ARCILOGGER-Setup-<version>.exe`? Compare its `Get-FileHash` with `SHA256SUMS.txt` on
the server too; the setup window prints the bundle fingerprint again.

## 7. Install

```powershell
cd C:\Kurulum\ARCILOGGER-0.1.0
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -DryRun `
    -SiteName "<organisation name>" -NtpServer <local time source> -AdminAddress <admin PC>
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 `
    -SiteName "<organisation name>" -NtpServer <local time source> -AdminAddress <admin PC> `
    -ZamaneJar C:\Kurulum\tss-client-console-3.1.33.jar -ZamaneJarSha256 <value from Kamu SM>
```

The organisation's own values (section 7.1) have no default; the dry run asks for the missing
ones too.

(or double-click `ARCILOGGER-Setup-<version>.exe`, which runs the same script with `-Auto` —
install, or upgrade when another version is installed — in a window that stays open until you
press **Enter**. The setup installs no Zamane client, and a jar copied into
`C:\ProgramData\ARCILOGGER\zamane\` by hand afterwards does not run: the tasks run only a client
the installer recorded, a known release or one approved on the console, so sealing stops with the
cause `executable`. Add the client with the script of the bundle,
`powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -ZamaneJar
C:\Kurulum\tss-client-console-3.1.33.jar -ZamaneJarSha256 <value from Kamu SM>` (it compares the
SHA-256, copies and records the file; `[tsa] zamane_jar` must name that file), or upload it in
**Ayarlar → Kamu SM**: this version knows no Zamane release, so an uploaded client waits for
`approve-jar` on the console, at the earliest after `jar_hold_hours` (default 24, section 7.2).)

The setup `.exe` passes the two licence options on to the script; give them from an
administrator command prompt or PowerShell (any case; `-` works as well as `/`):

```powershell
.\ARCILOGGER-Setup-<version>.exe /LICENSEFILE="C:\Kurulum\ARCI-L-<number>.lic"
.\ARCILOGGER-Setup-<version>.exe /ALLOWUNCOVEREDUPGRADE
```

`/LICENSEFILE="<file>"` reaches the script as `-LicenseFile "<full path>"` (a relative path is
looked up in the current folder, then in the folder of the setup `.exe`; a full path is
safest). When there is no such file the setup says so and closes before copying anything.
`/ALLOWUNCOVEREDUPGRADE` reaches it as `-AllowUncoveredUpgrade` (section 13). Both can be given
together. Without them the setup runs the script with `-Auto` only, as before.

The **postgres superuser password** is asked once, and the organisation's values that were not
given (section 7.1). At the end of a first installation, once
the install log is closed and only when someone sits at the window:

- the first administrator's **setup token** is shown in the window (section 9). The installer writes
  the token line straight to the console window, past PowerShell's output streams: it is not in the
  install log or the plan, and a PowerShell transcript or redirection started by you or by a Group
  Policy (`Start-Transcript`, PowerShell transcription, `*>`, `Tee-Object`) does not record it
  either. A tool that records the console window itself still sees it: then get a new token with the
  command of section 9, which voids the previous one. Over a kept database with an administrator (a
  reinstallation after section 14) no token is created and the installer says so;
- "Kamu SM müşteri numarası ve parolasını şimdi girmek ister misiniz? [E/h]" (store the Kamu SM
  customer number and password now?) is asked. `E` (or Enter) reads the customer number (visible)
  and the password (hidden) straight into the encrypted secret store (`secret set`, on stdin;
  never on a command line or in the log); `h` skips it (section 10). It is not asked when either
  of the two is already stored (enter the missing one with the commands of section 10).

Under `powershell -NonInteractive`, in CI or with redirected output neither happens; use the
command of section 9 for the token.

Parameters: `-ServerAddress` (remembered; otherwise the address of the only adapter with a default
gateway; the devices' syslog target, certificate and web address, and `[collector] bind` of a new
`arcilogger.toml`; remembered in `C:\ProgramData\ARCILOGGER\install\state.json`), `-ServerName`
(remembered, otherwise arcilogger.internal), `-SiteName` (no default; required on a first
installation), `-NtpServer` (no default; empty: the fallback time server only), `-AdminAddress`
(no default; addresses or networks such as `10.0.0.10` or `10.0.0.0/24`; remembered) — the last
three in section 7.1, `-DataRoot` (D:\ARCILOGGER on a fixed NTFS `D:`, otherwise
C:\ARCILOGGER; first install only), `-StrictOutbound` (remembered; `-StrictOutbound:$false` switches
it off), `-SkipFirewall`, `-SkipTimeSetup` (only the installer's time step: with the browser
administration on, `ApplySystem` sets Windows Time from `[clock]`; [SETTINGS.md](SETTINGS.md)
section 2), `-SkipDatabase`, `-SkipPower` (desktop Windows: leave the
power settings alone, section 17), `-AllowUnsupportedWindows` (Windows 10 / Windows 11 Home: warn
instead of stopping; not supported), `-HealthWaitSeconds` (90), `-DryRun`/`-WhatIf` with
`-PlanPath plan.json`, and for the licence ([LICENSING.md](LICENSING.md)) `-LicenseFile` (no
default; a `.lic` to install: read without Python in the preflight — the file exists, is at most
65,536 bytes and is JSON with the licence id, maintenance and validity dates — or the run stops with
`2` before any change; installed with `arcilogger-db license install` right after the schema
migration; a file the product refuses, or an unreachable database, is a warning: the installation
goes on and ends with `1`; with `-SkipDatabase` it is not installed and the command to run later is
printed) and `-AllowUncoveredUpgrade` (installs a version the licence's maintenance does not cover
anyway, with 5651 Temel only: the paid modules stay off until a licence that covers it is
installed, section 13). Both are for an installation or an upgrade only (`2` with `-Uninstall` or
`-Rollback`). The browser administration (section 7.2) takes `-SourceNetwork` (the networks a log
source added in the browser must lie in; default the RFC 1918 private networks, several
separated by commas), `-AdminChangeHoldHours` (the hold of retention and replica changes,
0–168 hours, default `0`), `-ZamaneHoldHours` (how long before an unknown Zamane client can be
approved, 1–168 hours, default `24`) and `-DisableBrowserAdministration` (switches it off; the 5651
functions are not affected; `-DisableBrowserAdministration:$false` switches it on again); all four
are remembered.

What it does: bundle checksums and preflight (Windows version and edition — Windows Server
2022/2025 and Windows 11 Pro/Enterprise/Education are supported, Windows 10 and Home stop it, ARM64
warns, desktop Windows gets a note to verify the licence terms — administrator, .NET 4.6.1+, the
data drive (fixed disk, NTFS, free space; a warning for a disk on USB and for the disk of
Windows), PostgreSQL service, ports 514/443, the server address and the address the collector
will listen on: a `[collector] bind` that is neither `0.0.0.0` nor an address of this computer
**stops** it with the fix — the organisation name and the time source (section 7.1), and
everything the firewall step could refuse: an admin address (given or remembered) and its
format, the `[[sources]]` of an existing configuration, an open remote-desktop session the
firewall would cut off; no log source at all, as on every new installation, is a warning, not a
stop, section 7.1; the licence's maintenance rule `preflight.license`: a bundle whose release date,
`release_date` in `bundle-manifest.json`, is after the maintenance date of `-LicenseFile` or of the
installed licence **stops** it with `2` — `-AllowUncoveredUpgrade` warns and goes on, section 13,
[LICENSING.md](LICENSING.md) section 7; without a licence it is information only) → folders `C:\Program Files\ARCILOGGER`, `C:\ProgramData\ARCILOGGER`, the
data folder (and the browser administration's `admin\` folder, `smtp-ca\`, `zamane\web\`,
`zamane\approved\`, `state\requests\`) → Python 3.12, venv, offline hash-checked wheels, web
interface, Caddy, WinSW →
`arcilogger.toml` only if absent (`data_dir` = data folder, `[collector] bind` = server address,
`site_name` = organisation name, `[clock] ntp_server` = local time source; the example
configures **no log source**, the `[[sources]]` of its fictional example network are comments,
section 7.1; an existing file is left alone but for a given `-SiteName` / `-NtpServer` line) →
the data root recorded in `C:\ProgramData\ARCILOGGER\data-root.txt` (the services and tasks
check `data_dir` against it; no browser change can move the data folder) → the programs the tasks
run recorded (section 7.2) → secret store key and an empty `secrets.json` → the browser
administration's keys (`keys init`, an existing key is never replaced), `admin\policy.json` and
the Event Log source `ARCILOGGER-Admin` → least-privilege NTFS rights and vault auditing (the collector cannot delete or
rename raw files and cannot reach the audit spool or the sealing state; only the scheduled tasks
write the nightly database dumps in `backups\scheduled`, which no service can read; limit: the
scheduled tasks run as Windows' shared LOCAL SERVICE account, so Windows services running as
LocalService share
their rights — ADR-0007 section 8; the administration service ArciAdmin writes only
`arcilogger.toml` and `secrets.json`, through entries on those files, and its own folders, and can
only add files to `kamusm-roots` and `smtp-ca`; the API only drops requests into `admin\inbox`,
[SECURITY.md](SECURITY.md) section 14) → database role and
database (random password into the encrypted secret store on stdin; PostgreSQL only sees its
SCRAM verifier; the database UTF8 with the `C` locale, created only if missing) → services
`ArciCollector`, `ArciIngest`, `ArciApi`, `ArciWorker`, `ArciWeb` and the administration service
`ArciAdmin` ("ARCILOGGER Yönetim (Admin)") under their virtual accounts (the collector and Caddy,
which need no database, start right away; ArciAdmin starts last and stops first; the Python services run the venv's
interpreter **isolated**, `venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_<package> …`, so no
environment variable — `PYTHONPATH`, `PYTHONSTARTUP`, machine-wide or the service's own — can make
them load another module; `ARCILOGGER_CONFIG` still applies) → time sync, on desktop Windows
the power settings (`Set-ArciPower.ps1`, section 17), firewall, scheduled tasks (including the nightly database dump at
01:40, [BACKUP.md](BACKUP.md), the task requests of the browser in `Requests` (LOCAL SERVICE) and
`ApplySystem` (SYSTEM), which keeps the firewall and Windows Time in step with the configuration;
a task you switched off stays off; the tasks run Python isolated too,
`python.exe -I -m arcilogger_core …`, `-I -m arcilogger_db backup run`) → `arcilogger-db
upgrade`, the **last** change (a first installation's trial starts here) → with `-LicenseFile`, the
step `license.install` (a refusal is only a warning) → start ingest, API and worker → health check (services,
`http://<[api] host:port>/api/v1/health` — default `127.0.0.1:8000`, which Caddy's `/api` upstream
follows too — and `https://localhost/api/v1/health` through Caddy with the
certificate pinned to Caddy's root; polled for up to 90 s). The next steps printed at the end
include, as step 6, the licence: the licensee when a licence is installed, otherwise the
**request code** to e-mail to the licensor (or, when it cannot be read, the command to run later;
[LICENSING.md](LICENSING.md) section 4).

Exit codes: `0` done, `1` done with warnings (also a `-LicenseFile` the product refused or could
not install), `2` stopped before changing anything (for a dry run: the real run would stop at the
preflight; also the licence's maintenance rule, an unreadable `-LicenseFile` and a bundle without a
release date), `3` failed while changing the system — the output
says what was put back and what is left to do (also in
`C:\ProgramData\ARCILOGGER\install\state.json`, `last_failure`). Fix the cause and run it (or the
setup `.exe`) again: a first installation that stopped while copying program files is started
over (its `INSTALL-IN-PROGRESS.json` marker identifies the folder), a later one is repaired; no
`-Force` needed. Log: first `%TEMP%\ARCILOGGER-<mode>-*.log`, copied into the `logs` folder of the
data folder once that exists (`D:\ARCILOGGER\logs\` or `C:\ARCILOGGER\logs\`); the window prints
the path at the end. Programs missing from the
bundle are skipped with a warning (this version's bundle has the programs of all six services);
without the `arcilogger-db` package the database steps are skipped.

The ARCILOGGER programs are **not on `PATH`**: always run them by their full path
(`C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-*.exe`, or the `venv\Scripts` folder of
your `-InstallDir`), in a Windows PowerShell **run as Administrator** — only administrators and
the ARCILOGGER services can read the configuration and the secret-store key.

### 7.1 The organisation's own values

ARCILOGGER carries no organisation's data: there is **no default** for the organisation name, the
time source or the admin PC's address. The installer takes them from its parameters; what is
missing it asks for when a person sits at the console (the setup `.exe`'s window, an elevated
PowerShell), otherwise the preflight stops or (for the time source) warns. Nothing is asked under
`-NonInteractive`, in CI or with redirected output.

| Value | Parameter | Written to | When not given |
|---|---|---|---|
| Organisation name (reports, e-mails, evidence packages, the web interface's sidebar) | `-SiteName "<organisation name>"` | `site_name` | Asked on a first installation; without a console the preflight stops. An upgrade keeps the configured name |
| Local time source (usually the core router or a local NTP server) | `-NtpServer <address or name>` | `[clock] ntp_server` | Asked on a first installation (Enter: none). Left empty, Windows Time and the hourly clock check use only `[clock] fallback_server` (`time.ume.tubitak.gov.tr`) and the preflight warns. An upgrade keeps the configured value |
| The admin PC's address or network (e.g. `10.0.0.10`, `10.0.0.0/24`) | `-AdminAddress` | `install\firewall-state.json` (firewall: 443 and 3389 only from it) | The remembered value; without one it is asked, without a console the preflight stops (not needed with `-SkipFirewall`) |

`-SiteName` and `-NtpServer` go into the new `arcilogger.toml`. Given on an upgrade or with an
existing configuration, only that line of the file changes; the previous file is kept as
`arcilogger.toml.before-<time>`. To check what is still missing after the installation:

```powershell
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" config-check
```

`HATA [ERROR]` lines (no organisation name; `[clock] ntp_server` and `[clock] fallback_server` both
empty) give exit code `1`; a `UYARI [WARNING]` (no local time source; no log source, `[[sources]]`)
leaves the exit code alone; `--json` prints the same result as JSON. ARCILOGGER runs without an
organisation name, but the reports, e-mails and evidence packages are then made without one.

**No network plan is shipped.** A new `arcilogger.toml` has **no log source**: the `[[sources]]`
lines of the example configuration's fictional example network are comments only, so a first
installation configures no device the organisation does not have (no silent-source alerts for
devices that never send, no wrong parser for a sender that happens to use one of their
addresses). That is a valid state, but until sources are added the firewall opens 514 to no sender
and nothing is recorded (with `-SkipFirewall` every sender is recorded as `unknown-<ip>`, its
parser chosen by content); the silent-source alert (R15) watches no source. The preflight, the
next steps at the end of the installation and `config-check` say so as a warning (the
installation ends with warnings, exit code `1`). Add the `[[sources]]` of your firewall, your core
router (the DHCP server) and your other devices that send syslog (remove the `#` in front of the
example lines in the file and change the names and addresses; [DEVICE-SETUP.md](DEVICE-SETUP.md)
section 4) and run the installer again (or `Set-ArciFirewall.ps1` and a restart of the services,
section 12); the firewall opens 514 only for them (section 11). After the installation sources
can also be added on **Ayarlar → Kaynaklar** (Settings → Sources) in the web interface: the
firewall follows by itself, no restart is needed ([SETTINGS.md](SETTINGS.md)). An upgrade keeps an existing
`arcilogger.toml` and its sources (only a given `-SiteName` / `-NtpServer` line changes). In the database the VLAN list and the departments are empty on a new installation: both
are entered on the **Departmanlar ve VLAN'lar** tab of the Cihazlar ve Kişiler page
([FIRST-STEPS.md](FIRST-STEPS.md) sections 5 and 12).

### 7.2 Browser administration

After the installation the server's settings change on the **Ayarlar** (Settings) pages of the
web interface: organisation name, time sources, Kamu SM (the test → production wizard), log
sources, e-mail, replica, retention, backup ([SETTINGS.md](SETTINGS.md)). The API cannot write the
configuration; the administration service `ArciAdmin` applies signed requests, and the SYSTEM task
`ApplySystem` derives the firewall and Windows Time settings from the configuration itself. The
console commands of this guide remain the console path.

The installer writes the limits into `C:\ProgramData\ARCILOGGER\admin\policy.json`, which only
administrators can change, and remembers them:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade `
    -SourceNetwork 172.16.0.0/12 -AdminChangeHoldHours 24 -ZamaneHoldHours 48
# switch the browser administration off (the 5651 functions are not affected):
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -DisableBrowserAdministration
```

The values of a valid hand-edited `policy.json` are kept; an invalid file switches the browser
administration off and the installer rewrites it, with a warning.

The installer compares the address of every enabled log source of the configuration with these
networks. When a source lies outside them (a remote site's public or CGNAT address, say, or a
network wider than /16), it stops before any change and names the `-SourceNetwork` value to
give: otherwise `ApplySystem` would take that sender out of the 514 rule at its first run, with
no change in the browser at all. With the browser administration off or with `-SkipFirewall`
this is a warning.

**The data root record:** the installer writes the data folder into
`C:\ProgramData\ARCILOGGER\data-root.txt` (only administrators can change it; deleted and created
anew on every run). The services and tasks refuse a configuration whose `data_dir` differs from
this record. Every later run takes the data folder from the record: when `data_dir` in
`arcilogger.toml` differs from it, or the record cannot be read, the installer stops before any
change and names both values. If you moved the data folder deliberately (after copying the
data), run the installer with `-DataRoot <new folder>`; only then does the record change.

**The programs the tasks run:** the tasks run programs from trusted places only
(`C:\Program Files`, `C:\Program Files (x86)`, the `java` on the system `PATH`). The installer
records a `java` outside Program Files, a `[tsa] java` command with extra arguments and the
`[backup] pg_dump` / `[backup] pg_restore` paths with their SHA-256 in `policy.json`, with a warning
for each; run the installer again when such a program changes. When anyone but administrators
and SYSTEM can write the folder of a program to record, the installer stops before any change and
asks you to move it below Program Files. The Zamane client runs only from
`C:\ProgramData\ARCILOGGER\zamane\`: when `[tsa] zamane_jar` names another place, the installer
copies the file there, points `zamane_jar` at the copy (the previous configuration stays as
`arcilogger.toml.before-<time>`) and appends its SHA-256 to `zamane\zamane-jar.sha256`.

## 8. Root certificate on the admin PC

Caddy issues ARCILOGGER's HTTPS certificates from its own internal CA. Copy
`C:\ProgramData\ARCILOGGER\caddy-root.crt` (public certificate only) to the admin PC, compare
`Get-FileHash .\caddy-root.crt -Algorithm SHA256` with the value the installer printed, then:

```powershell
Import-Certificate -FilePath .\caddy-root.crt -CertStoreLocation Cert:\LocalMachine\Root
```

(or double-click → Install Certificate → Local Machine → **Trusted Root Certification
Authorities**). Firefox: import under Settings → Certificates → Authorities, or set
`security.enterprise_roots.enabled = true`. For `arcilogger.internal` add
`<server address> arcilogger.internal` to the hosts file or a static entry on the network's DNS
server (with MikroTik DNS **verify**: `/ip dns static add name=arcilogger.internal
address=<server address>`). The root
is valid for about 10 years; Caddy renews the intermediate and leaf certificates itself.

## 9. First login

After the installation there are no users; the first **administrator** is created in the web
interface with a single-use **setup token** printed on the server console. The window of a first
installation shows this token itself once the install log is closed (section 7); then continue
with step 2. If you lost it, or ran the installer as a script with redirected output:

1. On the server open **Windows PowerShell as Administrator** (only administrators and the
   ARCILOGGER services can read the configuration, the secret-store key and the audit spool) and
   run:

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

   (Adjust the path if you chose another `-InstallDir`; the installer prints the exact command
   at the end.) The token is printed **once**, is valid for **24 hours** and works **once**; a
   new token replaces the previous one; the command refuses once an administrator exists. Exit
   codes: `0` done, `2` usage or configuration error, `3` database unreachable, `4` refused: an
   administrator exists already (sign in with it, or step 4). The Windows account that ran it is
   written to the audit log.
2. Open `https://<server address>` (the web address the installer printed; certificate warning:
   section 8; on the server itself
   `https://localhost`, root certificate: section 17). Without an administrator the
   **first-run setup** page opens: enter the token, a user name, a display name and a password
   (at least 12 characters; rules in [SECURITY.md](SECURITY.md) section 2).
3. Sign in with the new account. **TOTP** is mandatory for administrators and is enrolled at the
   first sign-in: scan the QR code with an authenticator app and store the **10 recovery codes**
   (shown once) safely, not on the server. Then accept the **KVKK notice**.
4. **Recovery** (every administrator lost the phone and the recovery codes): create another
   administrator on the console:

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" create-admin --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

Other users (the auditor etc.) and what comes next: [FIRST-STEPS.md](FIRST-STEPS.md).

## 10. Kamu SM connection (test → production)

The **Ayarlar → Kamu SM** wizard of the web interface does the steps below in the same order
(Java, the Zamane client, roots and fingerprints, the customer data with a credit query, the stamp
test, the switch to production; [SETTINGS.md](SETTINGS.md)). The console path:

When Kamu SM has issued the test user ([kamusm.md](kamusm.md) section 4), work **in this order**.
`tsa-test` first takes the stamp (1 kontör) and then verifies it against the pinned roots: without
roots it ends with "no pinned root certificates" after spending the credit. The nightly sealing
(`\ARCILOGGER\Seal` task) cannot seal any day without the roots either.

1. **Roots:** download the Kamu SM root (and, if needed, intermediate) certificates from
   `sertifikalar.kamusm.gov.tr` into `C:\ProgramData\ARCILOGGER\kamusm-roots\` (the exact URLs
   are still open: [kamusm.md](kamusm.md) K10 — **verify**). Compare each fingerprint with the
   value Kamu SM publishes and record it:
   `Get-ChildItem C:\ProgramData\ARCILOGGER\kamusm-roots | Get-FileHash -Algorithm SHA256`
   (for a DER file this is the certificate's SHA-256 fingerprint; convert a PEM file first, outside
   the roots folder: `certutil -decode root.pem $env:TEMP\root.der`, then hash that file. The "Thumbprint" in the Windows
   certificate dialog is SHA-1 and not suitable for this comparison).
2. **Credentials** (hidden input, stored encrypted). Entered at the end of the first installation
   (section 7)? Then `& $core secret list` (with `$core` below) shows both; skip this step:

   ```powershell
   $core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
   & $core secret set kamusm.customer
   & $core secret set kamusm.password
   & "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"   # then: the administration service's right
   ```

   Every console `secret set` / `secret delete` replaces `secrets.json` with a new file, which
   lacks the administration service's (`ArciAdmin`) write right. Run `Set-ArciVaultAcl.ps1` (or
   the installer) again afterwards; otherwise the next change made in the browser is applied
   with the warning "the administration service can no longer write the configuration files"
   (`acl_drift`, a critical `R20-admin` alert), and the change after it fails until you do. After
   the values entered at the end of the first installation the installer does this itself.
3. **Credit:** `& $core credit` (output format: open item K4).
4. **Connection test:** `& $core tsa-test` (stamps and verifies 1 KB of random data, 1 kontör).

Stamps of the test server have no legal validity; test-stamped days are flagged in the interface.
Decide **beforehand** whether the Seal task may seal days with test stamps during the test
([FIRST-STEPS.md](FIRST-STEPS.md) section 3: `[seal] first_day`).

**Switching to production:**

1. Corporate application on `zdportal.kamusm.gov.tr`, buy at least 10,000 kontör
   ([kamusm.md](kamusm.md) section 1).
2. Store the production customer number and password again with `secret set kamusm.customer` /
   `secret set kamusm.password` (the test user is a separate request; whether its credentials
   work in production is **not verified**).
3. In `C:\ProgramData\ARCILOGGER\arcilogger.toml`, `[tsa]`: `url = "http://zd.kamusm.gov.tr"`.
   ARCILOGGER shows a stamp as legal grade only when the stamp's certificate chain reaches a Kamu
   SM root whose SHA-256 fingerprint you listed in `legal_root_fingerprints` in the same section
   (the value recorded in step 1); with an empty list nothing is shown as legal grade.
4. The API loads the changed configuration by itself within 2 seconds; the red "test stamp"
   banner and the report stamps follow the new setting (scheduled tasks read it on every run). On
   a version before this one, or when the Settings page shows "A change waits for a restart": `Restart-Service ArciApi`.
5. Check with `& $core credit` and `& $core status` (`legal_grade`); the red banner on the sealing
   page must be gone.

ARCILOGGER marks as legal grade only stamps of Kamu SM's production server whose chain reaches a
root listed in `legal_root_fingerprints`; stamps of the test server have no legal validity (Kamu
SM). Have the stamps and the record keeping reviewed by a
lawyer.

Credential changes (`secret set` / `secret delete`) are recorded without their values in
`D:\ARCILOGGER\state\seal\secret-changes.jsonl`; the worker copies them into the audit log
(`settings.secret_change`) within 15 minutes. If the record cannot be written the command warns
and exits with code 1.

### Replica (e.g. a WORM share on a NAS)

The second copy's target (`[replica] target`) is an SMB share on a NAS or another server, if
possible a folder with WORM (write once) switched on. The Replica task runs as `LOCAL SERVICE`,
which reaches the network anonymously. For a share open to the replica user only, create a user
on the storage device with read-write on that folder only, then set
`[replica] target = '\\<NAS>\arcilogger-worm'` and `[replica] user = '<NAS>\arcireplica'` (with
`enabled`; `<NAS>` is the device's name), store the password with
`& $core secret set replica.password` (then `Set-ArciVaultAcl.ps1`, section 10) and run
`& $core replica --test` (connect, write, read and delete a probe file). ARCILOGGER connects to the share with that user for each run (no drive
letter, never remembered in a profile) and disconnects afterwards; the password never appears on
a command line or in a log. Copies are written to `<target>\.arcilogger-staging\` first and moved
into the day folder once their hash matches — **verify on site** that the WORM share allows
renaming a freshly written file. A half copy left by an interrupted run raises a high alert and is
completed; a file with other content raises a critical alert and is never overwritten.

## 11. Device setup

See [DEVICE-SETUP.md](DEVICE-SETUP.md). After the test messages **every expected source** must
have `D:\ARCILOGGER\spool\<source>\<day>.log` (per-source check: DEVICE-SETUP section 4). The
firewall admits 514 only from the `[[sources]]` addresses: the logs of a device whose address is
wrong in the configuration **never reach** the server; without any `[[sources]]` (a new
installation, section 7.1) 514 is closed.

## 12. Checks after installation

```powershell
Get-Service Arci* | Format-Table Name, Status, StartType
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Format-Table TaskName, State
Get-NetFirewallRule -Group ARCILOGGER | Format-Table DisplayName, Direction, Enabled
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" status
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" license status --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
```

The last command prints the licence state, the modules that are on and the limits (trial: the
days left; [LICENSING.md](LICENSING.md) section 5).

Firewall (`Set-ArciFirewall.ps1`):

- **Inbound:** default Block; allowed are only UDP/TCP 514 from `[[sources]]` (without any
  `[[sources]]` no rule is made for 514, it stays closed and the script says so) and TCP 443 and
  TCP/UDP 3389 from `-AdminAddress`. **Every other enabled inbound allow rule is disabled** and
  recorded — Windows' "Remote Desktop", "Windows Remote Management (HTTP-In)" (TCP 5985, enabled by
  default on Windows Server), "File and Printer Sharing" and rules other programs added — except
  the "Core Networking" group (DHCP, IPv6 neighbour discovery, ICMP errors). `-AllowWinRM` opens
  TCP 5985 from the admin PC only; `-KeepRule '<rule name>'` keeps a rule a monitoring or backup
  agent needs.
- **Outbound (optional):** `-StrictOutbound`: default Block; NTP, DNS, Kamu SM (zd/tzd and the
  `[tsa]` host on `[tsa] port`), the Kamu SM certificate host, SMTP and the replica only; Windows'
  other enabled outbound allow rules (except Core Networking) are disabled too. Switch it off for
  Windows Update with `-StrictOutbound:$false`.
- **SMTP (alert e-mail), strict outbound mode only:** with `-StrictOutbound`, and with
  `enabled = true` and a `host` in the `[smtp]` section of `arcilogger.toml`, the script adds the
  outbound rule to that server on the section's `port` (default 587) itself, on every run
  (upgrades included). A `host` written as a name is resolved to IP addresses when the script
  runs: run it again if the provider's addresses change. `-SmtpServer <host:port>` adds a rule of
  its own for another mail server, also in strict mode only, and is remembered. In the default
  mode outbound traffic is allowed and no SMTP rule is created. To forget a remembered
  `-SmtpServer`, pass the empty value with `-Command`: Windows PowerShell 5.1 drops an empty
  argument to a program, so after `-File` the script would get `-SmtpServer` without a value and
  stop.

  ```powershell
  powershell -ExecutionPolicy Bypass -Command "& 'C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1' -SmtpServer ''"
  ```
- The choices (`-StrictOutbound`, `-AdminAddress`, `-AllowWinRM`, `-KeepRule`, `-SmtpServer`) are
  remembered in `C:\ProgramData\ARCILOGGER\install\firewall-state.json`; an upgrade keeps them.
  Name a parameter again to change it.
- Changes of `[[sources]]` (on Ayarlar → Kaynaklar or by hand) are loaded by the collector and
  ingest within 5 seconds; the `ApplySystem` task updates the syslog rules in place (only
  `ARCILOGGER Syslog UDP/TCP` and, in strict outbound mode, the `ARCILOGGER Out …` rules; no other
  rule, no profile, nor 443, 3389, 5985), after a hand edit within 5 minutes. Run the script
  yourself when the browser administration is off (`-DisableBrowserAdministration`) or when the
  `[tsa]` URL/port or the admin addresses changed. Restart the services after a change of
  `[collector]`, `[api]`, `[ingest]`, `[worker]` or `[database]` (UDP syslog sent while the
  collector restarts can be lost for a few seconds):

  ```powershell
  powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
  Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
  ```
- `-Remove` (run by the uninstaller) deletes ARCILOGGER's rules and restores the disabled rules
  and the profile settings found before the first run.

## 13. Upgrade and rollback

Download or build and copy the new bundle, verify the fingerprint (`ARCILOGGER-Setup-<version>.exe`
upgrades the installed version by itself; the server address and name, the admin addresses and the
firewall choices are remembered, the organisation name and the time source are read from the
configuration — `-SiteName` / `-NtpServer` change them, section 7.1), then in a maintenance window
(the collector is stopped only while the program files and rights are replaced; UDP syslog sent then
is lost, UDP does not retransmit):

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -DryRun
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade
```

**Licence and maintenance** ([LICENSING.md](LICENSING.md) sections 7 and 8): before changing
anything, an upgrade compares the new bundle's release date with the maintenance date of the
installed licence (`state\license-status.json` in the data folder) or of the file given with
`-LicenseFile`; when the version is not covered it stops with `2` and says so in Turkish and
English. Renew maintenance and pass the licence file you receive with the upgrade:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -LicenseFile C:\Kurulum\ARCI-L-<number>.lic
```

Or install the version anyway with `-AllowUncoveredUpgrade`: it runs with 5651 Temel only, the
paid modules stay off until a licence that covers it is installed (so a fix needed for a 5651
obligation can always be installed). With only the setup `.exe`, give either one to it
(section 7):

```powershell
.\ARCILOGGER-Setup-<version>.exe /LICENSEFILE="C:\Kurulum\ARCI-L-<number>.lic"
.\ARCILOGGER-Setup-<version>.exe /ALLOWUNCOVEREDUPGRADE
```

Running the installed bundle again (a repair) only warns.
When an installation without a licence holds sealed days older than 14 days, the preflight
**warns** (it does not stop) that the trial counts as over right after the upgrade: the paid
modules switch off, with more than 3 active users no new user can be added, the 5651 functions
are not affected. What to do when upgrading from a version without licensing:
[LICENSING.md](LICENSING.md) section 8. The installer's rule is advisory; the running product
enforces it (`version_not_covered`).

The database is dumped first (`D:\ARCILOGGER\backups\db\*.dump`; no dump, no change), the
scheduled tasks are paused, the services stopped, the old program folder is kept as
`C:\Program Files\ARCILOGGER.previous`, the new version installed, the collector and Caddy (no
database) started again at once, the schema migrated **last**, and then ingest, API and worker
start, the administration service `ArciAdmin` last (it stops first). Right after the database
dump `arcilogger.toml` is copied to `arcilogger.toml.before-upgrade-<UTC time>` and the name
remembered in `install\state.json` (`config_before_upgrade`); the programs the tasks run are
recorded (section 7.2), and a program in a folder that is not trusted stops the upgrade before
any change. If a step fails (exit code `3`):

- **before the migration** (folder move refused, program installation, firewall, tasks): the
  previous version is put back and started automatically, the paused tasks run again; the
  half-installed folder is kept as `ARCILOGGER.failed-<time>`;
- **during the migration**: the schema may be half-migrated, so the previous program does not run
  against it. The previous program folder is back but only `ArciCollector`, `ArciWeb` and the
  scheduled evidence jobs run (none needs the database: no syslog is lost, sealing goes on);
  `ArciIngest`, `ArciApi` and `ArciWorker` are set to manual start. The installer prints the exact
  `-Rollback -RestoreDatabase -DatabaseDump <dump>` command (or fix the cause and run `-Upgrade`
  again);
- whatever could not be put back automatically is listed under "manual action needed".

Manual rollback: `Install-Arcilogger.ps1 -Rollback` (add `-RestoreDatabase` to restore the last
pre-upgrade dump too; the restore is one transaction). Without `ARCILOGGER.previous`,
`-Rollback -RestoreDatabase` restores only the database while the collector keeps running.

A rollback (and the automatic return after a failed upgrade) loads the configuration **with the
previous version's Python** before it starts that version's services. When the previous version
does not know a change made in the browser (for example a source switched off,
`[[sources]] enabled = false`), the copy from before the upgrade is put back, the refused file is
kept beside it as `arcilogger.toml.refused-<time>`, and the installer warns "Tarayıcıdan yapılan
sonraki değişiklikler geri alındı; denetim kaydında duruyorlar [later browser changes were rolled
back; they remain in the audit log]". Without the copy the rollback stops with `3` and asks for
the file to be fixed by hand. A rollback to a version from before the browser administration
removes the `ArciAdmin` service; sealing and the other tasks go on.

The `ApplySystem` task leaves with the newer version, but it left the firewall's 514 rule and
Windows Time set from the newer configuration. So before the services start, a rollback runs the
restored version's own `Set-ArciFirewall.ps1` (with the choices its state file remembers) and
`Set-ArciTime.ps1` (step `system.restore`; on the automatic return after a failed upgrade when the
configuration was put back): a source that is enabled again in the copy from before the upgrade,
or comes back with it, is let through again. If this step fails the rollback still completes,
ends with exit code `1` and prints the command to run by hand:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
```

The pre-upgrade dumps (`backups\db`) are separate from the nightly dumps (`backups\scheduled`,
[BACKUP.md](BACKUP.md)) and never deleted automatically; delete old ones by hand. An upgrade waits
for running tasks: do not start it while the nightly dump (01:40) runs.

**Upgrading to the version with Arama (search; schema revision `0016_search`).** The migration
builds one new index, `ix_fg_web_rhost_time` on the web filter table `fg_web` (the search for "this
domain and its subdomains"). On the development machine (4 vCPU, PostgreSQL 16 with its default
`maintenance_work_mem` of 64 MB, monthly partitions) it took **about 15 seconds per 10 million
`fg_web` rows** (13–15 s, index 553 MiB; measured 2026-10-26 while other work ran on the machine,
with the 3.6 GiB table in memory: a table read from disk, or a slower disk, takes longer). At 1
million log lines a day, about 18 % of them web filter lines, 400 days hold about 72 million
`fg_web` rows: about 2 minutes, more when they do not fit in memory; at 5 million lines a day five
times as long. Ingest is stopped while the schema is migrated; the collector keeps writing the
spool, so nothing is lost and ingest catches up afterwards ([INGEST.md](INGEST.md)). After the
upgrade the worker job `search.counts_backfill` counts the days loaded before it into Arama's
summary table every 10 minutes, newest day first: it starts no new file after a minute; one
file's recount takes about 9 s per million lines (development machine), and the worker's other
jobs wait for it. Until a day is counted, Arama counts that day's statistics directly (slower,
same result). What is still to count:

```powershell
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" search-counts status --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
```

**Many people searching at once.** Searches, live streams, reports and every page of the web
interface share the API's database connections (`[database] pool_size`, default 5). A site where
several people use Arama at the same time raises `pool_size` to 10 in `arcilogger.toml` and the
setting `search.max_concurrent` with it (at most `pool_size` minus 2; Ayarlar → Arama ve canlı
akış, [SETTINGS.md](SETTINGS.md)), then restarts the services (section 12).

**Upgrading to the version with internet exits (schema revisions `0017_fortigate_dhcp`,
`0018_nat_egresses`).** The migration adds a new table for the FortiGate DHCP records and one
column to the `fg_traffic` table (fast even on large tables: the column is added empty). The old
NAT settings are converted into one new setting, the **internet exits**: the `nat.public_ips`
addresses become a direct exit named "Genel IP (yükseltmede aktarıldı) [public IP, carried over
at the upgrade]", `nat.wan_transip` becomes an ISP NAT exit named "FortiGate WAN adresi
(yükseltmede aktarıldı) [FortiGate WAN address, carried over at the upgrade]" (no separate exit
when the address is one of the public IPs; a private or shared address in `nat.public_ips`, e.g.
100.64.x.x, becomes a NAT address of this exit too), an exit named "Genel adresi tanımlanmamış
hatlar [lines without a defined public address]" is added for the lines without a public
address when no "FortiGate WAN adresi" exit is made and a public IP or `lookup.port_hint` is
carried over, and `lookup.port_hint` becomes the port preservation value of the "FortiGate WAN
adresi" exit, or of this exit when there is none. The conversion is written to the audit log
as `settings.change` (`details.migration = "0018_nat_egresses"`; the dropped invalid, IPv6 or
reserved values in `details.dropped`). When the conversion cannot give a valid definition, the
exits stay empty and the alert `R20-nat:conversion-failed` opens.

**After the upgrade:**

- Enter each line's public address and interfaces (dstintf) in Settings → Application
  settings → 5651 lookup and lease timeline → Internet exits (NAT). On every installation with
  a carried-over "FortiGate WAN adresi" or "Genel adresi tanımlanmamış hatlar" exit, until they
  are entered every lookup and report for an address that no exit defines carries the warning
  `egress_public_unknown`; the carried-over exits keep the old NAT behaviour until interfaces are
  entered or changed, but the NAT addresses of an exit added later are outside that. If the line
  is still behind the ISP router and you add the FortiGate's NAT address on that line (e.g.
  100.64.x.x) as a new exit, move the line's public address from the direct exit to this exit
  and enter its interfaces; otherwise lookups of the direct exit may be answered with the
  warning "Doğrudan hat doğrulanamadı [direct line not confirmed]".
- When the NAT address of the carried-over "FortiGate WAN adresi" exit is the line's public
  address (the FortiGate holds the public address itself), set the exit's type to **Direct**;
  the editor warns about it.
- Once every line is defined, delete the "Genel adresi tanımlanmamış hatlar" exit: while it
  stays, a question about a public IP that belongs to no line lists unrelated sessions as
  candidates.
- One change: when an address that no exit defines appears at that moment in the FortiGate
  records as a source NAT address, the lookup now makes an exact match on that address instead
  of a candidate list, and says so with a warning (`egress_observed_not_configured`).
- With FortiGate DHCP, do the check of [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4 and write
  the outcome into section 16; repeat the check after every FortiOS upgrade. For moments before
  the upgrade 5651 lookups stop at the internal IP address: the FortiGate DHCP records of that
  time are not converted into the lease timeline; the lookup says so and, in an internal-IP
  lookup or an exact match, shows the record as evidence (`fortigate_dhcp_not_converted`). In a
  chain that rests on an older lease (MikroTik or FortiGate), when such a record exists for that
  address or for the lease's MAC address after the lease's last DHCP record (e.g. a client that
  moved from MikroTik to FortiGate DHCP before the upgrade got another address), the chain is
  shown with low confidence and the reason (`fortigate_dhcp_not_converted_since_lease`). **After
  the upgrade too**, no chain is built for a client until its first DHCP Ack record arrives in
  the new version: this can take up to half the lease time (3.5 days with the FortiOS default
  lease of 7 days; a client renews at half its lease). Plan the upgrade knowing this gap;
  installations that do not use FortiGate DHCP are not affected.
- The srcmac of FortiGate session records loaded before the upgrade (or by the previous version
  during a rollback) is not used by the 5651 lookup; no partial srcmac chain is built for those
  moments.
- A public NAT address that no exit defines (e.g. an IP pool) no longer counts as a sign of
  double NAT and is not taken into the candidate search: it is the FortiGate's own address and,
  when asked for, is answered as the address seen in the records. Enter the addresses of the
  FortiGate's NAT towards a DMZ, another VLAN, a VPN or a branch with the type "Not an internet
  exit".

**Rollback:** the old NAT rows are kept, unread, in this version; `-Rollback` runs no schema
downgrade, and the previous version reads these rows, that is the NAT values from **before the
upgrade**. If you changed the internet exits after the upgrade, then after the rollback compare
the previous version's `nat.public_ips` and `nat.wan_transip` values in Settings → Application
settings with the change you made in this version and enter them by hand (the changes are in the
audit log as `settings.change`); do not restore the database for this: `-RestoreDatabase`
deletes every record made after the upgrade (audit, lookups, reports, alerts, device and person
assignments). Before running `-Rollback`, download the official reports (PDF) of the lookups
made in this version: a report is rendered once, on its first download, and the previous
version does not print the rows this version adds (the unconfigured-sender warning, the "Kısmi
kanıt [partial evidence]" label, the exit and FortiGate lease rows). On an installation that
uses FortiGate DHCP the previous version cannot read the FortiGate DHCP records: it may keep a
FortiGate lease open after the address was released or given to another client. So, before the
program is swapped, `-Rollback` sets every FortiGate lease to **low confidence**; it prints how
many and writes the count to the installation log (only derived lease rows change; raw records
and seals are not touched; with `-RestoreDatabase` this step is skipped). Every chain that rests
on FortiGate DHCP is therefore shown with low confidence in the previous version. The previous
version closes the existing FortiGate leases by the times of its own settings (without looking
at the logged lease time, with the defaults about 26 hours after the lease's last DHCP record;
this moment may already be in the past), deletes in its own rebuilds those that started on the
rollback day or the day before (and those that started on the days it rebuilds for late
records) and creates no new one; MikroTik leases follow the previous version's own rules, as
before the upgrade. Make the 5651 lookups of FortiGate DHCP addresses for moments after the
rollback after the re-upgrade. After the re-upgrade the FortiGate DHCP records of the days in
between do not enter the lease timeline, and lookups say so: at a moment where no chain can be
built, with `fortigate_dhcp_not_converted` when the newest DHCP record covering the moment is
such a record; in a chain that rests on a DHCP lease, when such a record exists for that
address or for the lease's MAC address after the lease's last DHCP record, the chain is shown
with low confidence and the reason (`fortigate_dhcp_not_converted_since_lease`).

`-Rollback` sets the FortiGate leases to low confidence even when the version it returns to
reads FortiGate DHCP records: after a rollback between two versions that both read FortiGate
DHCP, FortiGate DHCP chains are also shown with low confidence until the next
`arcilogger-db upgrade`. That upgrade (e.g. the re-upgrade) restores them (below). This low
confidence never names a wrong person; it only shows the chain lower than needed.

**Re-upgrade:** since the database schema is not rolled back, the conversion does not run again
and the old NAT settings are not converted again. When `nat.public_ips`, `nat.wan_transip` or
`lookup.port_hint` was changed in the previous version during the rollback, the alert
`R20-nat:legacy-changed` names the changed settings: enter the change in the internet exits by
hand; the alert closes when the exits are saved. The re-upgrade restores the confidence of the
FortiGate leases the previous version did not change and prints how many came back, how many
stay low and the days whose leases to rebuild; those that stay low are the leases the previous
version closed or recomputed. On an installation that uses FortiGate DHCP, after the re-upgrade
rebuild the leases from the day one longest lease time before the rollback day (7 days before
with a 7-day lease) to the re-upgrade day in Settings → Application settings → 5651 lookup and
lease timeline → Rebuild the leases (at most 31 days per request): only so are the FortiGate
leases that the previous version closed early, deleted or left at low confidence rebuilt to the
FortiGate's own end and confidence; without it a lookup for moments in that range may give an
unexplained "no lease" or show the chain with low confidence. In the rollback range these
rebuilt leases are shown with low confidence and the reason
(`fortigate_dhcp_not_converted_since_lease`) when, after the lease's last DHCP record, the
previous version loaded a FortiGate DHCP record for that address or MAC address: the address may
have been released in between, given to another device, or the MAC address may have taken
another address.

## 14. Removal

`Install-Arcilogger.ps1 -Uninstall` first pauses the scheduled tasks and waits for a running one
(sealing, monthly verification; they run Python from the program folder) for up to
`-TaskWaitMinutes` (30); a task still running then stops it before anything is removed (exit code
`2`, tasks enabled again: run it later). It then removes services (`ArciAdmin` included), scheduled
tasks, the Event Log source `ARCILOGGER-Admin` (the events written stay in the log) and firewall
rules (restoring the Windows rules and profile settings it had changed), on desktop Windows puts
the power settings back to their values from before the installation
(`C:\ProgramData\ARCILOGGER\install\power-state.json`; the time zone, the time sync settings and
the "File System" audit policy are not reverted), and removes only the program folders this installer
created (`C:\Program Files\ARCILOGGER`, `.previous`, `.failed-*`, `.rolled-back-*` with its
`VERSION.json` or `INSTALL-IN-PROGRESS.json`). Any other folder — a mistyped `-InstallDir`, a drive
root, a Windows folder — stops it before anything is removed. It **never** deletes the raw logs,
the vault, exports, backups (`D:\ARCILOGGER`), the configuration, the secret store and the
browser administration's `admin\` folder (`C:\ProgramData\ARCILOGGER`) or the database.

## 15. Troubleshooting

| Symptom | Fix |
|---|---|
| Script cannot be loaded (execution policy) | `powershell -ExecutionPolicy Bypass -File ...` and `Unblock-File` |
| PostgreSQL 16 not found | Section 2; `-PgServiceName` / `-PgBinDir` for non-default installs |
| `[collector] bind = "…"` or `-ServerAddress …` "is not an address of this computer" | The collector could not listen there and would receive nothing. Write one of the addresses in the message (or `0.0.0.0`) as `[collector] bind` in `arcilogger.toml`, or run the installer again with the right `-ServerAddress` (section 1) |
| "server address not clear" / "not found" | Several adapters (a second card, a VPN, a virtual switch) have a default gateway, or none has: pass `-ServerAddress <address>`; it is remembered |
| "assigned by DHCP" (warning) | Give the PC a static IP or a reservation on the DHCP server (e.g. the core router): if the address changes no log arrives |
| "organisation name required" / "no organisation name" | Pass `-SiteName "<organisation name>"` or run the installer at the console and type it (section 7.1); an upgrade stops too while `site_name` in `arcilogger.toml` is empty |
| "no local time source is set" (warning) | The clock follows `[clock] fallback_server` only: pass the core router or a local NTP server with `-NtpServer` (section 7.1) |
| "admin PC address required" | Pass `-AdminAddress <address or network>` or run the installer at the console and type it; it is remembered (section 7.1) |
| "Windows 10 is not supported" / "Windows 11 Home is not supported" | Use Windows 11 Pro/Enterprise/Education or Windows Server (section 17); `-AllowUnsupportedWindows` only to try |
| "removable drive", "optical drive", "network drive", "RAM disk" | The evidence lives on a local, fixed NTFS disk: choose another one with `-DataRoot` (section 1) |
| "free … < 100 GB" | The evidence is kept for at least two years and grows: use a larger disk; `-MinFreeGB` once the sizing is checked |
| UDP 514 or TCP 443 in use | Stop the other syslog or web server |
| TCP 8000 (the API port) in use | Stop the other program, or change `[api] port` in `arcilogger.toml` and run the installer again (Caddy's upstream, the health check and the preflight follow it) |
| Removal ended with `2`: "task still running" | An evidence job (sealing, verification) is running; nothing was removed. Run it again later or raise `-TaskWaitMinutes` |
| Service "marked for deletion" | Close services.msc and run the script again |
| "An RDP session from … would be cut off" (preflight) | Connect from one of the `-AdminAddress` addresses or add your address or network with `-AdminAddress` (`-Force` cuts the session) |
| "-KeepRuleGroup matches no rule" | The Core Networking group has another name: find it with `Get-NetFirewallRule -DisplayGroup '<name>' \| Select-Object -First 1 Group, DisplayGroup` and pass `-KeepRuleGroup` |
| A monitoring or backup agent is unreachable | The firewall disabled its inbound rule: `Set-ArciFirewall.ps1 -KeepRule '<rule name>'` (remembered) |
| Upgrade ended with `3` | The output and `C:\ProgramData\ARCILOGGER\install\state.json` (`last_failure`, `pending_database_restore`); section 13 |
| "This version (…) was released after your licence's maintenance period (…); nothing was changed" | The bundle was released after the licence's maintenance date: renew maintenance and pass the new licence file with `-LicenseFile`, or `-AllowUncoveredUpgrade` to install the version with 5651 Temel only (section 13, [LICENSING.md](LICENSING.md) section 7) |
| "Lisans dosyası okunamadı [licence file unreadable]" | Check the `-LicenseFile` path, the file's size (at most 65,536 bytes) and content: the unchanged `.lic` from the licensor; nothing was changed |
| "Lisans dosyası yüklenmedi [licence file not installed]" (warning) | The product refused the file (the reason is in the message: another server, a damaged file, maintenance …) or the database was unreachable; the installation went on and ended with `1`. Fix the cause and install the file in the web interface (Settings → Licence) or with the command in the message ([LICENSING.md](LICENSING.md) sections 4.3 and 13) |
| "Paket yayın tarihi yok [bundle has no release_date]" | The bundle's `bundle-manifest.json` has no `release_date`: an old or damaged bundle; download or build it again (section 4) |
| "6. Lisans [licence]: istek kodu alınamadı [request code not available]" | Run the `license request-code` command of the message later in an administrator PowerShell, or open Settings → Licence in the web interface; `license_server_id_unavailable`: the server id cannot be read (section 16) |
| A service does not start | `D:\ARCILOGGER\logs\<Service>.out.log`, `.err.log`, `.wrapper.log` |
| Lost database password | `Install-Arcilogger.ps1 -ResetDatabasePassword` |
| `arcilogger-api` / `arcilogger-core` "is not recognized" | The programs are not on `PATH`: use the full path, e.g. `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"` (section 9) |
| `setup-token` or `secret set`: access denied / secret key unreadable | Open PowerShell **as Administrator** (section 9) |
| No nightly sealing, `pending_days` in `status` grows | The last run result of the `\ARCILOGGER\Seal` task in Task Scheduler and `D:\ARCILOGGER\logs\core.log`. Configuration errors — no Kamu SM credentials, no roots (`kamusm-roots`), `[tsa] zamane_jar` not set (section 10) — stop the task before sealing starts: result `0x1`, the error in `core.log` on every run and, once per day and cause, `R20-seal-config` in `D:\ARCILOGGER\state\alerts.jsonl`. A sealing attempt that started and failed (e.g. no access to Kamu SM) writes `R20-seal` ([ALERTS.md](ALERTS.md)) |
| "could not rename file" / "Permission denied" in the PostgreSQL log (`D:\ARCILOGGER\db\log`), or sealing keeps retrying | Antivirus exclusions (section 2 step 4): `Get-MpPreference`; detections in `Get-WinEvent -LogName 'Microsoft-Windows-Windows Defender/Operational'` (**verify** the event IDs). If Defender quarantined a file under `D:\ARCILOGGER`, record it and restore it before the next seal or verify run |
| `... 'Microsoft.PowerShell.Security' ... could not be loaded` (`ConvertTo-SecureString`, `Get-Acl`) | Windows PowerShell was started by another program (cmd.exe, a setup `.exe`, a tool) that was itself started from a PowerShell 7 (pwsh) window, and inherited PowerShell 7's module folders (pwsh removes them only from a `powershell` it starts itself; about_PSModulePath). The ARCILOGGER scripts remove them themselves; for your own commands open "Windows PowerShell" as Administrator from the Start menu |
| The installer ended at once with `1` and nothing was installed or upgraded | PowerShell also returns `1` when a script stops before its first line (a syntax or parameter error). Run the script from an administrator console (section 7, or section 13 with `-Upgrade`), where the red error stays on screen. Once the script has started, the setup's window also stays open until you press Enter; when it stopped before its first line the window closes. The setup `.exe` reports a failure whenever, after `0` or `1`, `C:\Program Files\ARCILOGGER\VERSION.json` does not name the fingerprint of the bundle it carries — for a first installation and for an upgrade |

## 16. To verify on site

The WinSW virtual-account setup (`sc.exe qc ArciCollector`) and "Log on as a service" for
`NT SERVICE\ALL SERVICES`; the EDB installer's rights on `D:\ARCILOGGER\db` and `pg_hba.conf`
(`127.0.0.1/32 scram-sha-256`); the `auditpol` "File System" subcategory GUID; the Temurin MSI
`INSTALLDIR` property; TimescaleDB `setup.exe` with the installed 16.x and
`timescaledb_pre_restore()` / `timescaledb_post_restore()` around a restore; Inno Setup's
`Turkish.isl`; `arcilogger-api serve` on 127.0.0.1:8000 and that `setup-token` runs by its full
path in an administrator PowerShell and prints a token (section 9); the Core Networking rule group
(`Get-NetFirewallRule -DisplayGroup 'Core Networking' | Select-Object -First 1 Group` should print
`@FirewallAPI.dll,-25000`) and, after the firewall is applied, that
`Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow` lists only ARCILOGGER's and
Core Networking's rules; Microsoft Defender's state and the two exclusions of section 2 step 4
after the installation (`Get-MpPreference`), and the owner's decision on `spool` and `vault`
(exclusion or scheduled scan only); the first night's `D:\ARCILOGGER\state\backup\last.json`, the
duration and size of the nightly database dump and the first restore drill ([BACKUP.md](BACKUP.md)
sections 6 and 8); on the upgrade to the version with Arama, how long the schema migration with
`ix_fg_web_rhost_time` takes on the real database (section 13) and, after the first days,
`arcilogger-db search-counts verify`: the summary table must match the rows and the counts of
names not stored in lower case (`fg_dns.qname`, `fg_web.hostname`) must be 0; that the Python
services start with the isolated interpreter (`Get-CimInstance
Win32_Process -Filter "Name = 'python.exe'" | Format-List CommandLine`: `python.exe -I -X utf8 -u
-m arcilogger_api serve …`; `sc.exe qc` shows the service wrapper) and that the server id reads the
same under the ArciApi service's virtual account and in an administrator PowerShell (the server
id on Settings → Licence must equal the one of `arcilogger-db license status`;
[LICENSING.md](LICENSING.md) section 4.1); for the browser administration
([SETTINGS.md](SETTINGS.md) section 9), that ArciAdmin's entry on `arcilogger.toml` survives a
change of the file (`Get-Acl`), that ArciAdmin can add and delete only its own files in
`kamusm-roots`, that the `ARCILOGGER-Admin` events wake the `Requests`, `Seal` and `ApplySystem`
tasks (`Get-WinEvent`, the Task Scheduler history), and that no syslog message is lost while a
firewall rule is updated in place.
On desktop Windows 11 Pro all of the above plus the power settings, Smart App Control and a
forced update restart, step by step:
[KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md).

**FortiGate DHCP, srcmac and internet exits.** The assumptions below rest on the FortiOS
documentation or depend on the organisation's network; they are verified on site (the V numbers
are this list's own; [CORRELATION.md](CORRELATION.md) section 8 describes the same assumptions by
topic). Write down each row's
outcome and the date of the check. Repeat the checks **after every FortiOS upgrade** (the record
fields may change between releases) and add the new outcome with its date; do not delete the old
one. When an outcome differs from what is expected, correct the setting by the branches of
[DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4. Without FortiGate DHCP, write "not used" in the
rows V1–V6, V14 and V17.

| # | To verify | How | Outcome to record | Outcome | Date |
|---|---|---|---|---|---|
| V1 | The real format of the FortiGate DHCP Ack record (logid `0100026001`) on syslog: the fields `ip`, `mac`, `dhcp_msg`, `lease`, `interface`, `hostname`, the quoting, how the MAC address is written; a missing or empty `hostname` | [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4 step 5: the Sources → Lease evidence card and a trial 5651 lookup; when it passes, `dhcp.fortigate_format_verified` is switched on (step 6) | passed / failed; the setting corrected | | |
| V2 | Whether a client's release (RELEASE) is logged; its logid (expected `0100026002`) and `dhcp_msg` value | DEVICE-SETUP.md section 2.4 step 5 (`ipconfig /release`, then `ipconfig /renew`) and its branches; another logid shows in the alert `R20-lease:fortigate-refused` as "unknown logid" | RELEASE logged: yes / no; logid; `dhcp_msg` | | |
| V3 | Whether renewals (at half the lease, the client staying connected) give a second DHCP Ack record | DEVICE-SETUP.md section 2.4 step 5, the renewal check | renewals logged: yes / no; if not, the organisation's choice of DEVICE-SETUP.md section 2.4 step 5 (keep it as it is, or switch on `dhcp.fortigate_traffic_extends`) and the date of the choice | | |
| V4 | Whether `lease` is in seconds; how an unlimited lease (`lease-time` `0`) is written | the `lease` value of the raw line in Arama (`logid:0100026001`) compared with the FortiGate's `lease-time` (DEVICE-SETUP.md section 2.4 step 7); the alerts `R20-lease:fortigate-lease` and `R20-lease:fortigate-lease-unlimited` | `lease` = `lease-time`: yes / no | | |
| V5 | Whether the DHCP event records reach syslog with the settings of DEVICE-SETUP.md (severity `information`, no filter that excludes events) | DEVICE-SETUP.md section 2.4 steps 3–4; the state of the Sources → Lease evidence card (`ok`, `none`, `refused`) | the card's state; with `none` or `refused`, the correction made | | |
| V6 | That interfaces where the FortiGate is a DHCP relay give no FortiGate DHCP record; that the interfaces of `server-type ipsec` servers (DHCP over IPsec, VPN pools) are excluded; whether a reply to a DHCPINFORM is logged as a DHCP Ack (its `dhcp_msg` value: V14); when the FortiGate serves several relayed networks on one interface, which `interface` and address the DHCP Ack record of a relayed client carries | DEVICE-SETUP.md section 2.4 steps 1, 2 and 9; `dhcp.fortigate_interfaces_excluded`; the alert `R20-lease:fortigate-dhcp-subnet` | relayed networks served on one interface: yes / no; if yes, whether the record's `interface` and address are of the client's own network; the excluded interfaces | | |
| V8 | That srcmac comes only on interfaces with device identification and is the own MAC address of directly connected clients; behind a layer-2 switch only if the two-client check shows it; which device-identification fields the unit fills | DEVICE-SETUP.md section 2.5: with two known clients on the interface, `max_ips_per_mac` 1 (IPv4 sources) in the srcmac interfaces table of the "Kayıtlarda görülen NAT adresleri [NAT addresses seen in the logs]" panel (Settings → Application settings → 5651 lookup and lease timeline), and the two MAC addresses in Arama (`srcmac:`) are the clients' own (not a switch's or router's) | per interface: check passed / failed, listed in `lookup.srcmac_interfaces` or not; the fields the unit fills | | |
| V11 | Whether each line's ISP router keeps source ports | DEVICE-SETUP.md section 2.6: the port preservation measurement | per line `preserved` (measured) or `unknown` | | |
| V14 | The `dhcp_msg` values FortiOS writes under each mapped logid | the raw lines in Arama (`logid:<logid>`); the alert `R20-lease:fortigate-refused` names the values seen with the reason "unexpected dhcp_msg"; `dhcp.fortigate_dhcp_msgs` | the `dhcp_msg` values seen per logid | | |
| V15 | Whether each line's public IP address is static | DEVICE-SETUP.md section 2.6: ask the ISP; a line whose address may change is marked `public_dynamic` (on an ISP NAT exit; a direct line's changing address is found from the logs, DEVICE-SETUP.md section 2.6) | per line static / may change; the ISP's answer | | |
| V16 | That FortiGate DHCP records carry a usable device time and the FortiGate's clock is right to within minutes: the FortiGate syncs with NTP | DEVICE-SETUP.md section 2.1 (the NTP step); on the FortiGate `diagnose sys ntp status` lists the NTP servers' information; the alert `R20-lease:source-clock` must not be open | FortiGate syncs with NTP: yes / no; the NTP server in the output of `diagnose sys ntp status` | | |
| V17 | Whether FortiOS logs a DHCPDECLINE or checks an address before it offers it; that a client holds one lease per server interface and network | there is no separate trial: static devices are kept out of the DHCP range (DEVICE-SETUP.md section 2.4 step 8); the alert `R20-lease:fortigate-decline:<net>` | whether the alert opened; if so, the network and the correction made (address reserved or device moved) | | |

## 17. Desktop Windows 11 Pro

ARCILOGGER also runs on a Windows 11 Pro (or Enterprise, Education) PC that is **dedicated** to it
and always on; no step needs Windows Server. This section covers what differs from a server. Run
the acceptance test before announcing desktop support:
[KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md).

**Licence.** The Windows client licence terms contain a clause that limits using the device as a
server (Windows 11 OEM terms, April 2024, section 2(c)(v); the Windows 10 terms have the same
clause). How ARCILOGGER's use falls under it has not been settled: verify the Windows licence
terms with a Microsoft partner. The preflight reminds you with an information line on desktop
Windows; this document draws no conclusion.

**Hardware and Windows.**

- Windows 11 hardware requirements: TPM 2.0, UEFI with **Secure Boot**, a supported processor
  (Microsoft's Windows 11 specifications). Old office PCs still on Windows 10 may not qualify.
- An x64 processor. On ARM64 PCs ARCILOGGER's x64 programs run under emulation; untested, the
  preflight warns.
- Windows 11 **25H2 (build 26200)** or later. On an older Windows 11 build the preflight warns: it
  may no longer get updates, check Microsoft's support dates. Windows 10 and Windows 11 Home are
  not supported (the preflight stops).
- **S mode off**: S mode runs only Microsoft Store apps, so neither the setup `.exe` nor the
  PowerShell script runs (the preflight cannot see it). Switching out of S mode is under Settings →
  System → Activation and cannot be undone (**verify**).
- **Smart App Control:** Microsoft documents that its enforce mode blocks unsigned code.
  `python.exe`, `python312.dll`, `caddy.exe` and WinSW in the bundle are unsigned; EDB's
  `postgres.exe` and `initdb.exe` are reported unsigned too. Check its state before installing:
  Windows Security → App & browser control → Smart App Control. Turning it off is the owner's
  decision; Microsoft's documentation says it can be turned on again only by a clean installation
  of Windows (another source says otherwise: **verify**). The acceptance test records the real
  behaviour.

**Power settings (done by the installer).** A desktop PC sleeps or hibernates; UDP syslog sent
meanwhile is lost and leaves no trace in ARCILOGGER. On desktop Windows the installer runs
`Set-ArciPower.ps1`, which records the previous values in
`C:\ProgramData\ARCILOGGER\install\power-state.json` (removal, section 14, restores them);
`-SkipPower` leaves everything alone.

- In the active power plan, "Sleep" and "Hibernate" after: never, when plugged in (AC). Battery
  (DC) values are not changed.
- Hibernation off (`powercfg /hibernate off`), which also turns Fast Startup off; and
  `HiberbootEnabled = 0`, so Fast Startup stays off even if hibernation is turned on again.
- On physical network adapters whose driver supports it, "Allow the computer to turn off this
  device to save power" off; it applies when the adapter or Windows restarts (an open remote
  desktop session is not cut off). Where this Windows lacks the command the script says so: untick
  it in Device Manager.
- By hand: "power on after AC loss" in the BIOS/UEFI, and a UPS.

**Windows Update.** Install updates, but UDP syslog sent during a restart is lost (ARCILOGGER shows
only a silence over 60 minutes of an expected single-address source as a gap in the manifest; a
shorter outage leaves no record). On Windows Pro set updates to download and notify: Local Group
Policy Editor (`gpedit.msc`) → Computer Configuration → Administrative Templates → Windows
Components → Windows Update → Manage end user experience → "Configure Automatic Updates" → **3 –
Auto download and notify for install** (**verify** the menu names). Restart by hand in the
maintenance window. Do **not** force restarts into the daytime active hours: that is when log
traffic, and so the loss, is largest. General internet access is opened only in the maintenance
window (section 0).

**BitLocker.** If Windows 11 device encryption is on, keep the recovery key off this PC, somewhere
separate and safe (the company's password vault, a printout in a locked cabinet). After a
mainboard, TPM or BIOS change the disk does not open without it: raw logs, vault and database
become unreachable together.

**Single disk.** Without a `D:` the data folder is `C:\ARCILOGGER` and the preflight warns: raw
logs, vault, database, the nightly database dumps (`C:\ARCILOGGER\backups\scheduled`,
[BACKUP.md](BACKUP.md)), the pre-upgrade dumps (`C:\ARCILOGGER\backups\db`) and the secret-store key
(`C:\ProgramData\ARCILOGGER\secret.key`) share the disk of Windows; one disk failure loses them
together. If you can, add a second internal disk as `D:` (section 1) or pass another fixed disk with
`-DataRoot`. At least enable the replica (e.g. to a NAS; section 10, "Replica") and copy the nightly
dumps off the server ([BACKUP.md](BACKUP.md) section 4); where the backups go is the owner's
decision. Windows feature updates need space on that disk too: check the free space regularly
([FIRST-STEPS.md](FIRST-STEPS.md)). A procedure to move to a Windows Server later has not been
written yet.

**Opening the interface on this PC.** Caddy does not touch the Windows certificate store. To use a
browser on the ARCILOGGER PC itself, install the root certificate there too and use
`https://localhost` (the certificate also covers the name `localhost`):

```powershell
Get-FileHash C:\ProgramData\ARCILOGGER\caddy-root.crt -Algorithm SHA256   # the value the installer printed
Import-Certificate -FilePath C:\ProgramData\ARCILOGGER\caddy-root.crt -CertStoreLocation Cert:\LocalMachine\Root
```
