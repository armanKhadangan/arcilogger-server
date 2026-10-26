# Licence and activation

> Türkçe (primary): [LISANS.md](LISANS.md). Decision record:
> [adr/0013-offline-licensing.md](adr/0013-offline-licensing.md). Installation:
> [INSTALL.md](INSTALL.md).
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance. The licence only switches paid modules
> on or off; the 5651 functions never stop, whatever the licence state.

This document is for the organisation that runs ARCILOGGER: which modules depend on the licence,
the trial, how a licence is requested and installed, the states and their banners, the limits,
maintenance and upgrades, a server change, and what the licensing cannot protect against.

Licensor (rights holder): **Arman Khadangan, arman@khadangan.com**.

## 1. In short

- **Perpetual licence plus annual maintenance.** A licence never expires. The maintenance date
  (`maintenance_until`) limits only **upgrades and support**: a version released after it does not
  switch the paid modules on with that licence. The installed version keeps working.
- **14-day trial:** a new installation runs with every module for its first 14 days.
- **Offline activation:** the server shows a **request code**; you e-mail it to the licensor; you
  install the licence file (`.lic`) you receive in the web interface, with a command or with the
  installer.
- **No network connection at all:** ARCILOGGER never connects anywhere for the licence and sends no
  telemetry. The licence is checked entirely on the server; the request code and the licence file
  travel by e-mail, carried by a person.
- **5651 Temel always works:** without a licence, with an expired, invalid or other-server licence,
  log collection, sealing, retention, the 5651 lookup, evidence packages, the audit log, sign-in and
  users, the operational alerts and the dashboard's health boxes keep working. Only the paid
  modules switch off and a banner shows.

## 2. Modules

Every licence lists its modules and limits. **5651 Temel** (5651 Core) is in every licence and on
in every state. Prices are not part of the product.

| Key | Module | Türkçe | Content | Needs |
|---|---|---|---|---|
| `core_5651` | 5651 Core | 5651 Temel | Log collection, sealing, retention, 5651 lookups, evidence packages, devices and persons, alerts, audit log | — (always on) |
| `usage_analytics` | Usage analytics | Kullanım analizi | Estimated usage time per person, device, site and category, with exports: the **Kullanım** page and the dashboard's usage sections | — |
| `usage_reports` | Scheduled usage reports | Zamanlanmış kullanım raporları | Scheduled usage reports made and e-mailed by the server: the **Raporlar** page | Usage analytics |
| `bulk_import` | Bulk import (CSV) | Toplu içe aktarma (CSV) | Bulk import of persons and device assignments from CSV files (Cihazlar ve Kişiler → the import part of the **Aktarım** tab); manual entry and the CSV exports are always available | — |

**Data the usage modules need:** Usage analytics and Scheduled usage reports are computed from
FortiGate logs only: traffic logs (with interim logs for long sessions), web filter, DNS filter,
application control and SSL inspection logs. Only traffic from private source addresses
(the RFC 1918 blocks and IPv6 ULA) is counted: internal networks on other ranges (e.g. 100.64.0.0/10
or public addresses used internally) are not analysed. Traffic is tied to the device and the person
through the leases of the MikroTik DHCP server (and `ARP-LOG`); traffic without a lease is counted
per IP address. Logs of other vendors are stored and sealed but not analysed. Before you buy a
licence, check that your network fits ([INSTALL.md](INSTALL.md) section 0.1); the trial (section 3)
also shows the result on your own network.

### 2.1 What 5651 Temel always does

Whatever the licence state, these work and no licence state switches them off: the syslog
collector and the raw log files (spool), parsing and loading into the database, the nightly
sealing (Kamu SM time stamp), chain verification, retention and deletion, the replica, the
database backup; the 5651 lookup, its official PDF report and time stamp, raw lines, evidence
packages and the standalone verifier; the sealing page and the credit; the hash-chained audit log
and its verification; sign-in, two-factor authentication, users and roles; devices, persons,
departments, VLANs and device-person assignments (by hand); the operational alerts (silent
source, disk, sealing, credit, clock, replica, backup, alert e-mail, the Güvenlik page); the
dashboard's health boxes (log intake, seal, credit, clock, alerts) and the licence page.

Only one alert code belongs to a paid module: `R20-report` (a scheduled report could not be sent)
belongs to Scheduled usage reports; it is not raised while the module is off, and an open one is
resolved when the module goes off ([ALERTS.md](ALERTS.md)).

The licence alerts belong to 5651 Temel: the job `license.check` raises them in every state and
tells the administrators before and when the paid modules go off (`R20-license`: the trial or a
time-limited licence ends within 3 or 14 days, the modules are off), when a limit is crossed
(`R20-license-limit`) and when maintenance ends (`R20-license-maintenance`, information)
([ALERTS.md](ALERTS.md) section 2).

Deleting personal data on time does not depend on the licence either: the retention jobs of the
usage rollups and the report files (`usage.retention`, `reports.purge`) run in every state.

### 2.2 API areas

The web interface uses the server's API under `/api/v1`. The endpoints of a module that is off
answer 403 `module_not_licensed`; the endpoints of 5651 Temel never give that answer, in any
licence state.

| API area | What | Module |
|---|---|---|
| `/health` | Health check | 5651 Core |
| `/auth` | Sign-in, two-factor authentication, password, first administrator | 5651 Core |
| `/users` | Users and roles (the users limit applies to new users, section 6) | 5651 Core |
| `/audit` | Audit log and chain verification | 5651 Core |
| `/dashboard` | The dashboard's health boxes | 5651 Core |
| `/alerts` | Alerts, their states and notes (Güvenlik page) | 5651 Core |
| `/lookup` | 5651 lookup, official PDF report and time stamp | 5651 Core |
| `/lookups` | Saved 5651 lookups | 5651 Core |
| `/raw` | Raw log lines | 5651 Core |
| `/evidence` | Evidence packages | 5651 Core |
| `/seals` | Sealed days, manifest, time stamp, verification | 5651 Core |
| `/credits` | Kamu SM credit | 5651 Core |
| `/devices` | Devices and device-person assignments | 5651 Core |
| `/persons` | Persons | 5651 Core |
| `/departments` | Departments | 5651 Core |
| `/vlans` | VLANs and subnets | 5651 Core |
| `/license` | Licence state, upload and removal of the licence file | 5651 Core |
| `/usage` | The data of the Kullanım page, exports, rebuilds | Usage analytics |
| `/reports` | Scheduled reports, runs, preview | Scheduled usage reports |
| `/imports` | CSV import and its templates | Bulk import (CSV) |

### 2.3 Background jobs (worker)

A job of a module that is off does not run: it is recorded once a day as `skipped`, and the worker
log gets one line each time the module goes on or off. Skipped runs do not raise `R20-job`. The
data of a module that is off is not deleted.

| Job | Module | While the licence is off |
|---|---|---|
| `license.check` | — (recomputes the licence state every 5 minutes, raises the licence alerts) | always runs |
| `partitions.ensure` | 5651 Core | always runs |
| `leases.incremental` | 5651 Core | always runs |
| `leases.nightly` | 5651 Core | always runs |
| `leases.recheck_today` | 5651 Core | always runs |
| `leases.late_evidence` | 5651 Core | always runs |
| `evidence.purge` | 5651 Core | always runs |
| `audit.import` | 5651 Core | always runs |
| `admin.requests` | — (browser administration: the test e-mail, the lease timeline rebuild) | always runs |
| `alerts.import` | 5651 Core | always runs |
| `alerts.r15` | 5651 Core | always runs |
| `alerts.system` | 5651 Core | always runs |
| `alerts.notify` | 5651 Core | always runs |
| `usage.requests` | Usage analytics | skipped |
| `usage.daily` | Usage analytics | skipped |
| `usage.today` | Usage analytics | skipped |
| `usage.reattribute` | Usage analytics | skipped |
| `usage.retention` | — (deletion of personal data) | always runs |
| `reports.run_due` | Scheduled usage reports | skipped |
| `reports.purge` | — (deletion of report files) | always runs |

On the command line only `arcilogger-worker usage rebuild` is refused while Usage analytics is off
(exit code 1, the `module_not_licensed` message). `arcilogger-worker run`, `rebuild` (the lease
timeline, the repair of the 5651 chain) and `alerts …` always work.

### 2.4 While a module is off, and when it comes back

- The module's page stays in the menu for those with the permission, with a lock icon; the page
  says "… is not in your licence" and sends no request. The dashboard's usage sections turn into a
  small locked card; the health boxes do not change. On the Aktarım tab the import part is
  locked, the export buttons stay.
- Scheduled reports are not sent; queued rebuild requests wait.
- When the module comes back (a new licence file is installed) the API sees it within 30 seconds
  and the worker within 60 seconds; no restart is needed (the browser that uploaded the file
  updates at once). `usage.reattribute` rebuilds the missing or partial usage rollups of the last
  `usage.auto_rebuild_days` (default 7) days by itself; older days with **Yeniden hesapla** on the
  Kullanım page or the `usage rebuild` command ([USAGE.md](USAGE.md)). Every report schedule runs
  once for the newest period it missed.

## 3. Trial

- A new installation runs **14 days** with every module and without limits. The trial starts when
  the installer creates the database schema for the first time. A banner on every page says
  "Deneme sürümü — N gün kaldı." ("Trial version — N days left.").
- After the trial the state is **Unlicensed** (Lisanssız, `trial_expired`): the paid modules are
  off, new users can be added up to **3** active users (section 6), there is no log-source limit.
  The 5651 functions keep working.
- A reinstallation does not restart the trial. Its start is the earliest of the installation's
  first record and: the oldest sealed day in the vault, the oldest day in the spool, the first
  audit log entry, earlier trial starts and the licence history. A new database with the old data
  folder therefore counts the trial as long over, by the old days.
- **Clock check:** when the trial start (or a trace in the database) lies more than 10 minutes
  after the trusted time (section 10), the trial is unavailable (`trial_clock_inconsistent`): for
  example when the system clock was set years ahead during the installation. An organisation whose
  clock really was wrong at installation asks the licensor for a time-limited demo licence
  (`valid_until`).

## 4. Activation

Set the organisation name (`site_name`) first: it goes into the request code, and the licensor
writes the licensee name from it ([INSTALL.md](INSTALL.md) section 7.1). When it is empty, the web page, the command and the
installer warn "Kuruluş adı ayarlanmamış" (organisation name not set).

### 4.1 Getting the request code

The request code is a text starting with `ARCIREQ1-`. It holds the server id, the installation
id, the product version, the organisation name, the platform, whether the server's hardware id
(SMBIOS UUID) could be read, the day and a checksum. It is **not secret** (no password, no key).
Within one UTC day (03:00 to 03:00 Türkiye time) it is the same for the same server. Three places
show it:

- **Web interface:** Ayarlar → Lisans (Settings → Licence) → **Etkinleştirme** (activation): the
  code box, **Kopyala** (copy) and **İndir (.txt)** (download, `arcilogger-istek-kodu.txt`); the
  server id, installation id and organisation name are there too. Only administrators see the
  page.
- **Command** (in a Windows PowerShell run as Administrator; one line, the code only):

  ```powershell
  $db  = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe"
  $cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
  & $db license request-code --config $cfg
  ```

- **End of the installation:** the installer prints the code in step 6 of the next steps
  ("6. Lisans [licence]: deneme süresi 14 gündür [14-day trial]. İstek kodu [request code]:").

The server id has the form `XXXX-XXXX-XXXX-XXXX` and is derived from the Windows machine id
(MachineGuid) and the mainboard's SMBIOS UUID. When the server id cannot be read there is no
request code (`license_server_id_unavailable`).

### 4.2 Sending the code

E-mail the request code to **arman@khadangan.com**, with the modules you want and the number of
log sources and users. It does not matter if the mail program breaks the code into lines, adds
spaces or drops the hyphens. The licensor never connects to your server; nothing else is needed.
The answer is a licence file named `ARCI-L-….lic` (at most 64 KiB, signed JSON).

### 4.3 Installing the licence file

Three ways; all make the same checks (signature, server id, validity and maintenance date):

- **Web interface:** Ayarlar → Lisans → **Lisans dosyası** (licence file): choose the file and
  upload it (your password is asked again). A refusal shows its reason in Turkish and English.
- **Command:** check it first (writes nothing), then install it:

  ```powershell
  & $db license verify "C:\Kurulum\ARCI-L-<number>.lic" --config $cfg
  & $db license install "C:\Kurulum\ARCI-L-<number>.lic" --config $cfg
  ```

- **Installer:** with `-LicenseFile <file.lic>`; the file is installed right after the database
  schema migration ([INSTALL.md](INSTALL.md) section 7). A file the product refuses does not stop
  the installation: it warns and ends with exit code 1.

Installing the same licence again changes nothing (recorded as "unchanged"). A new licence replaces
the previous one. Every installation, removal and refusal goes into the audit log and the licence
history. The web interface and the worker follow the new state without a restart.

### 4.4 Removing the licence and seeing the state

- Removal: Ayarlar → Lisans → **Lisansı kaldır** (remove licence; asks for confirmation) or
  `& $db license remove --yes --config $cfg`. The state becomes the trial while still within the
  trial window, otherwise Unlicensed; the users limit is 3 (section 6).
- State: Ayarlar → Lisans, or `& $db license status --config $cfg` (`--json`: the data of the web
  page). When a licence is installed, the sidebar shows every user "Lisans: <licensee>"
  (Licence: …) under the version.

## 5. States and banners

| State | When | Modules on | Users limit | Banner (everybody sees it) |
|---|---|---|---|---|
| `trial` | No licence, within the trial | all | none | Trial version — N days left. |
| `trial_expired` (Unlicensed) | No licence, trial over | 5651 Core | 3 | The trial has ended: paid modules are off. 5651 log collection, sealing, lookups and evidence packages keep working. |
| `trial_expired` + `trial_clock_inconsistent` | The trial start lies ahead of the clock (section 3) | 5651 Core | 3 | the `trial_clock_inconsistent` message (section 13) |
| `licensed` | A valid licence | 5651 Core + the licence's modules | the licence's | — |
| `invalid` | The licence file cannot be verified (signature, unknown key, revoked licence) | 5651 Core | 3 | The licence file cannot be verified: paid modules are off. 5651 log collection, sealing, lookups and evidence packages keep working. |
| `wrong_server` | The licence is not for this server, or the server id cannot be read | 5651 Core | the licence's | The licence is not for this server (server id <id>): paid modules are off. The 5651 functions keep working. |
| `expired` | A time-limited licence (`valid_until`) has ended | 5651 Core | the licence's | The licence expired on <date>: paid modules are off. The 5651 functions keep working. |
| `version_not_covered` | This version was released after the maintenance date (section 7) | 5651 Core | the licence's | This version (<version>, <release date>) was released after the maintenance period (<until>): paid modules are off. Renew maintenance and upload the new licence file. The 5651 functions keep working. |

Banners only administrators see:

- users over the limit: "The number of users exceeds the licence limit (<used>/<max>): no new
  user can be added.";
- log sources over the limit: "The number of log sources exceeds the licence limit (<used>/<max>;
  <n> unconfigured). Logs are still collected and sealed.";
- maintenance ends within 30 days: "Maintenance ends in N days (<date>).";
- maintenance has ended (information; nothing switches off): "Maintenance has ended (<date>):
  renew it for new versions and support. The installed version keeps working."

At most two banners show at once. Administrators follow the banner to the licence page; other
users see "Contact your administrator." With a licence that holds no paid module (5651 Temel only),
`expired`, `version_not_covered` and `wrong_server` show users no banner, as no module goes off;
administrators get an information banner.

The same situations are also alerts on the Güvenlik page, e-mailed by the alert settings
(`R20-license`, `R20-license-limit`, `R20-license-maintenance`; [ALERTS.md](ALERTS.md)), and the
trial's end (3 days) and a time-limited licence's end (14 days) are announced before they come.

An unusable licence never falls back to the trial, and a server change has no grace period: a new
licence file is needed.

## 6. Limits

### 6.1 Log sources

A **log source** is a configured device, or any sender that sent logs in the last **30 days**.
Unconfigured senders (`unknown-<ip>`) count too (the page shows them as "unconfigured"
separately); every sender of a network source counts on its own. A device removed from the
configuration, or an address that no longer sends logs, stops counting 30 days after its last log:
nothing else is needed to get back under the limit.

The limit is **soft:** the logs of sources over the limit are still collected, sealed and
searchable; no data is lost. Administrators see a banner; crossing the limit and getting back
under it are written to the audit log (`license.limit_exceeded`, `license.limit_ok`).

### 6.2 Users

What counts are the **active** user accounts. At the limit, adding a user or re-enabling an
inactive one is refused (`license_limit`); existing users keep working and signing in. Deactivate
a user to free a place. Role changes and deactivation are never limited.

| State | Users limit |
|---|---|
| Trial | none |
| Licensed, `wrong_server`, `expired`, `version_not_covered` | the licence's number (removing the licence or upgrading does not lift the limit bought) |
| Unlicensed (`trial_expired`), `invalid` | **3** |

- Creating the first administrator (with the setup token) is never limited.
- The recovery command `arcilogger-api create-admin` refuses while the limit is full; it still
  works when no administrator can sign in (every administrator inactive or locked: true recovery).
  At a full limit, pass `--deactivate USERNAME` to deactivate a user in the same transaction:

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" create-admin --deactivate <user name> --config $cfg
  ```

- When the licence component cannot be loaded (`license_unavailable`) the paid modules are off,
  but no user operation is refused; the 5651 functions and user management keep working.

## 7. Maintenance and upgrades

- A version's **release date** is a fixed date that comes with the version (in the About section
  of the licence page, in the bundle's `bundle-manifest.json` and in the installed program's
  `VERSION.json` as `release_date`). It is not the day the bundle was built; rebuilding a version
  later does not change which licences cover it.
- A licence covers the versions released on or before its maintenance date. When a version
  released later is installed, the state is `version_not_covered`: the paid modules are off, the
  5651 functions work. Renewing maintenance and installing the new licence file switches the
  modules on.
- **The installer's maintenance rule:** on an installation and an upgrade, **before changing
  anything**, it compares the bundle's release date with the maintenance date of the
  `-LicenseFile`, or of the installed licence. It reads the installed licence from
  `state\license-status.json` in the data folder (the product rewrites that file every 5 minutes).
  When the version is not covered it stops (exit code 2):

  ```
  Bu sürüm (<version>, yayın tarihi <release_date>) lisansınızın bakım süresinden (<until> tarihine kadar) sonra yayımlandı; hiçbir şey değiştirilmedi. Bakımı yenileyip yeni lisans dosyasını -LicenseFile ile verin ya da yalnızca 5651 Temel ile devam etmek için -AllowUncoveredUpgrade kullanın.
  [This version (<version>, released <release_date>) was released after your licence's maintenance period (until <until>); nothing was changed. Renew maintenance and pass the new licence file with -LicenseFile, or use -AllowUncoveredUpgrade to continue with 5651 Temel only.]
  ```

  There are two ways on: renew maintenance and pass the new licence file with `-LicenseFile`, or
  install the version anyway with `-AllowUncoveredUpgrade`. With only
  `ARCILOGGER-Setup-<version>.exe`, give the same options to it as `/LICENSEFILE="<file>"` and
  `/ALLOWUNCOVEREDUPGRADE` ([INSTALL.md](INSTALL.md) section 7). Then the version runs with **5651
  Temel only** (the paid modules stay off until a licence that covers it is installed), so a fix
  for a 5651 obligation can always be installed. Running the same bundle again (a repair) only
  warns.
- The rule is **advisory:** the installer checks no signature and the status file can be edited.
  The running product enforces it (`version_not_covered`).
- **An older version cannot be reinstalled over a newer database** (the schema does not go back).
  The only way back right after an upgrade is the installer's `-Rollback -RestoreDatabase`, which
  puts the database back to the pre-upgrade dump ([INSTALL.md](INSTALL.md) section 13). The way to
  get the paid modules back is to renew maintenance and install the new licence file.

## 8. Existing installations

An installation upgraded from a version without licensing has days older than 14 days in its
vault and audit log. So the trial counts as over right after the upgrade: usage analytics,
scheduled reports and CSV import switch off; with more than 3 active users no new user can be
added. The 5651 functions are not affected. The installer warns before the upgrade (it does not
stop; when there is neither a licence file nor a readable licence status file and the vault
holds sealed days older than 14 days):

```
Bu kurulumun lisansı yok ve deneme süresi kasadaki eski mühürlü günler nedeniyle dolmuş sayılacak: Kullanım analizi, Zamanlanmış raporlar ve CSV içe aktarma kapanacak; etkin kullanıcı sayısı 3'ün üzerindeyse yeni kullanıcı eklenemez. 5651 işlevleri etkilenmez. Yükseltmeden sonra web arayüzünde Ayarlar → Lisans sayfası istek kodunu gösterir (kurulumun sonunda da yazılır); kodu lisans verene gönderin, gelen .lic dosyasını aynı sayfadan yükleyin. Lisans yüklenene kadar ücretli modüller kapalı kalır; lisans yüklenip Kullanım analizi açılınca son günlerin (varsayılan 7 gün, usage.auto_rebuild_days) kullanım özetleri kendiliğinden, daha eskileri Kullanım sayfasındaki Yeniden hesapla ile yeniden hesaplanır.
[This installation has no licence and its trial counts as over because of older sealed days: usage analytics, scheduled reports and CSV import will be off; with more than 3 active users no new user can be added. 5651 functions are not affected. After the upgrade the web interface shows the request code under Settings → Licence (the end of the installation prints it too); send it to the licensor and upload the .lic you receive on the same page. Until the licence is installed the paid modules stay off; once it is installed and usage analytics is on, the usage rollups of the recent days (7 by default, usage.auto_rebuild_days) are rebuilt by themselves, older days with Recompute on the Usage page.]
```

What to do:

- **When the installed version has licensing** (there is a Settings → Licence page, or the
  `arcilogger-db license` command): request the code and send it **before** the upgrade
  (section 4), then install the licence file you receive, or pass it with `-LicenseFile` on the
  upgrade. Its maintenance must cover the new version (section 7).
- **When the installed version has no licensing**, a request code can only be had from the
  version with licensing (the upgrade creates the installation id; the old version has no
  `license request-code` command), which is why the warning does not ask for `-LicenseFile`.
  Plan the upgrade with the licensor: upgrade, send the request code (on the Settings → Licence
  page; also printed at the end, step 6) at once, and upload the `.lic` you receive on the same
  page. Meanwhile the paid modules are off; once the module is
  back the usage rollups of the last `usage.auto_rebuild_days` (default 7) days are rebuilt by
  themselves, older days with **Yeniden hesapla** on the Kullanım page (section 2.4). The first `license.check` run writes to the audit log that the modules
  went off.

## 9. Server change

- The licence is bound to the server id. A new server, a reinstalled Windows (a new MachineGuid), a
  new mainboard (a new SMBIOS UUID) or a virtual machine whose ids were changed gives a new server
  id: the state becomes `wrong_server`, the paid modules switch off, the 5651 functions work. There
  is no grace period.
- Get the request code on the new server and send it; the licensor re-issues the licence for the
  new server. The old licence is revoked in a later release; the revocation reaches the
  installations that upgrade to that release (`license_revoked`). A revoked licence is `invalid`
  and is refused on upload.
- Restoring the database or reinstalling ARCILOGGER on the same server keeps the licence valid
  (the server id binds; the installation id is information only).

## 10. Clock and trusted time

ARCILOGGER does not judge the end of the trial, the end of a time-limited licence or the
maintenance date by the Windows clock alone. The **trusted time** is the latest of: the system
clock, the newest Kamu SM time stamp of the sealed days, the end of the newest sealed day in the
vault, the release day of the installed version and the issue time of the installed licence.
So while there are sealed days, setting the clock back extends nothing: the end of the newest
vault day and the newest time stamp stay the floor (with no sealed day yet, the floor is only the
version's release day and the installed licence's issue time). Setting it forward shortens the
periods; moving the trial start forward ends in the clock check of section 3.

The About section of the licence page names the one in use ("Zaman kaynağı" / time source: the
Kamu SM time stamp, the system clock, the newest sealed day in the vault, the release date of this
version or the issue date of the licence) and says when the system clock is behind. Keep the clock
right ([INSTALL.md](INSTALL.md) section 1): a clock that is behind stops the sealing too.

## 11. What the licensing does not protect against

ARCILOGGER runs offline; no check asks anyone outside. The server's administrator (Windows
administrator and owner of the database) can do the following, and no offline check can fully
prevent it:

- Spoof the server id: edit the MachineGuid; on virtual machines set the SMBIOS UUID, or clone a
  virtual machine with the same ids. Without a network this cannot be detected.
- Install everything from scratch for a new trial (a new database **and** an empty data folder):
  every sealed day and the audit log are lost.
- As the database owner, switch off the append-only protection of the licence tables: this changes
  no licence decision, because the licence is the signed file, verified again every time.
- Hide vault days and set the clock back: sealing stops and the sealing alerts fire, so it is
  visible in operation.
- Read usage tables already computed, with SQL, after the module is switched off.
- A determined administrator changing the program files (the Python code) or adding a file to the
  program folder. A later phase (a compiled licence module, a signed list of the program files,
  Authenticode signatures) will make that harder, not impossible.

The remedies for these are contractual: the licence agreement, the licensor's register of issued
licences, and the licensee name shown in the user interface.

What the licensing does protect against: a changed or made-up licence file (Ed25519 signature),
another server's licence on hardware whose ids are not spoofed, changes to the licence rows of the
database, setting the clock back while there are sealed days and setting it forward at the
installation, attempts to restart the trial (a new database with the old data folder, deleting
the installation row), adding a trusted key through an environment variable, a configuration
file, the database or the API (the services run Python isolated, `python -I`), and getting round
the limits (unconfigured senders count too; the recovery command and repeated user creation
respect the users limit; removing the licence does not lift the limit bought).

## 12. Evidence is never touched

The licence never changes evidence: no licence mark goes into raw logs, sealed days, manifests,
time stamps, evidence packages, the standalone verifier or the 5651 PDF report. The licensee name
shows in the web interface (sidebar, licence page) and in the output of the licence commands; it is
in no part of the evidence.

Licence actions are written to the audit log (which is itself sealed with its day every night).
The audit details carry neither the licensee name nor the server ids; those are in the licence
history (Ayarlar → Lisans → **Geçmiş**, history).

| Action | On the audit log page | Türkçe |
|---|---|---|
| `license.trial_start` | Trial started | Deneme başladı |
| `license.install` | Licence installed | Lisans yüklendi |
| `license.remove` | Licence removed | Lisans kaldırıldı |
| `license.refuse` | Licence refused | Lisans reddedildi |
| `license.limit_refused` | Licence limit: user not added | Lisans sınırı: kullanıcı eklenemedi |
| `license.limit_exceeded` | Licence limit exceeded | Lisans sınırı aşıldı |
| `license.limit_ok` | Back within licence limit | Lisans sınırı içinde |
| `license.state` | Licence state changed | Lisans durumu değişti |

## 13. Commands and error codes

The commands run in a Windows PowerShell run as Administrator, by their full path (the program is
not on `PATH`; `$db` and `$cfg` as in section 4.1):

```powershell
& $db license status --config $cfg             # the state (--json: the web page's data)
& $db license request-code --config $cfg       # the request code, one line (--json: with details)
& $db license verify <file.lic> --config $cfg  # checks, writes nothing
& $db license install <file.lic> --config $cfg
& $db license remove --yes --config $cfg
```

Exit codes: `0` done; `1` refused (the licence file was not accepted, no licence to remove, the
server id cannot be read, or the licence component could not be loaded); `2` usage or
configuration error (e.g. the file cannot be read, `remove` without `--yes`); `3` database
unreachable. `status`, `install` and `remove` write `state\license-status.json` for the installer.

Every message is in Turkish and English. The error answers of the web interface and the API carry
`code`, `message_tr` and `message_en`; `{…}` stands for the real value.

| Code | HTTP | Türkçe | English |
|---|---|---|---|
| `license_too_large` | 413 | Lisans dosyası çok büyük (en çok 64 KiB). | The licence file is too large (at most 64 KiB). |
| `license_malformed` | 422 | Dosya bir ARCILOGGER lisans dosyası değil ya da bozuk. | The file is not an ARCILOGGER licence file or it is damaged. |
| `license_unknown_key` | 422 | Lisans, bu sürümün tanımadığı bir anahtarla imzalanmış ({key_id}). | The licence is signed with a key this version does not know ({key_id}). |
| `license_signature_invalid` | 422 | Lisans dosyasının imzası geçersiz: dosya değiştirilmiş olabilir. | The licence file's signature is invalid: the file may have been changed. |
| `license_revoked` | 422 | Bu lisans iptal edilmiş ({license_id}). | This licence has been revoked ({license_id}). |
| `license_server_id_unavailable` | 409 | Sunucu kimliği okunamadı; lisans bu sunucuya göre denetlenemiyor. | The server id cannot be read; the licence cannot be checked against this server. |
| `license_other_server` | 409 | Lisans bu sunucu için düzenlenmemiş (bu sunucu: {server_id}). Yeni lisans için istek kodunu gönderin. | The licence was not issued for this server (this server: {server_id}). Send the request code for a new licence. |
| `license_expired` | 409 | Lisansın geçerlilik süresi {date} tarihinde doldu. | The licence expired on {date}. |
| `license_version_not_covered` | 409 | Bu sürüm ({version}, yayın tarihi {release_date}) lisansın bakım süresinden ({until} tarihine kadar) sonra yayımlandı. | This version ({version}, released {release_date}) was released after the licence's maintenance period (until {until}). |
| `license_not_installed` | 404 | Kurulu bir lisans yok. | No licence is installed. |
| `license_unavailable` | 503 | Lisans bileşeni yüklenemedi: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor. | The licence component could not be loaded: paid modules are off. The 5651 functions keep working. |
| `trial_clock_inconsistent` | — | Deneme başlangıcı sistem saatinden ileride: deneme süresi kullanılamıyor. Deneme lisansı için arman@khadangan.com ile iletişime geçin. | The trial start lies after the current time: the trial is unavailable. Contact arman@khadangan.com for a demo licence. |
| `module_not_licensed` | 403 | Bu özellik lisansınızda yok: {module_tr}. Ayrıntılar Ayarlar → Lisans sayfasında. | This feature is not part of your licence: {module_en}. See Settings → Licence. |
| `license_limit` | 409 | Kullanıcı sayısı lisans sınırında ({used}/{max}): yeni kullanıcı eklenemez ya da yeniden etkinleştirilemez. Var olan kullanıcılar çalışmaya devam eder. | The number of users is at the licence limit ({used}/{max}): no user can be added or re-enabled. Existing users keep working. |
| `request_malformed` | — | İstek kodu okunamadı. | The request code cannot be read. |
| `request_checksum` | — | İstek kodu eksik ya da hatalı kopyalanmış (denetim toplamı tutmuyor). | The request code is incomplete or mistyped (checksum mismatch). |

`trial_clock_inconsistent` is a state problem (licence page, banner, command); `request_malformed`
and `request_checksum` show in the licensor's tool for a request code that was copied badly: copy
the code again and send it. Dates are `DD.MM.YYYY` in Turkish and `YYYY-MM-DD` in English.
