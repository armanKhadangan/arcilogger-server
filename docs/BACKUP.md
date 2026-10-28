# Database backup and restore

> Türkçe (primary): [YEDEKLEME.md](YEDEKLEME.md). Decision and reasons:
> [ADR-0009](adr/0009-database-backup.md). First steps: [FIRST-STEPS.md](FIRST-STEPS.md) section 10.

ARCILOGGER backs its database up by itself every night. This document says what is backed up and
what is not, how a backup is checked, how it is copied off the server and how a backup is
restored. Run the commands on the server in **Windows PowerShell opened as Administrator**; the
programs are not on `PATH` ([FIRST-STEPS.md](FIRST-STEPS.md) section 0):

```powershell
$arci   = "C:\Program Files\ARCILOGGER\venv\Scripts"
$core   = "$arci\arcilogger-core.exe"
$ingest = "$arci\arcilogger-ingest.exe"
$db     = "$arci\arcilogger-db.exe"
```

## 1. What is backed up, what is not

| What | Where | Backup |
|---|---|---|
| Database: users and roles, 5651 lookups with their request details, records of stored reports and evidence packages, changed settings, persons, device names and notes, alert states, privacy-notice acceptances, the audit log, the parsed log rows | PostgreSQL (`D:\ARCILOGGER\db`) | **Every night at 01:40**, `D:\ARCILOGGER\backups\scheduled\` (this document) |
| Pre-upgrade database dump | `D:\ARCILOGGER\backups\db\` | The installer, before every upgrade; never deleted automatically |
| Raw logs and the vault (sealed days) | `D:\ARCILOGGER\spool`, `D:\ARCILOGGER\vault` | Evidence files, **not** in the database backup; their second copy is the replica (`[replica]`, e.g. a WORM folder on a NAS; off by default, [FIRST-STEPS.md](FIRST-STEPS.md) section 10) |
| Evidence packages, stored official reports (PDF) | `D:\ARCILOGGER\exports` | Not backed up (packages are kept 30 days; reports are never deleted) |
| `secret.key`, `secrets.json`, `arcilogger.toml`, Caddy CA (`caddy\`), `kamusm-roots`, Zamane | `C:\ProgramData\ARCILOGGER` | Not backed up: **by hand, once, separately** (section 3) |

**What the raw logs can rebuild:** the parsed log tables and the lease (DHCP/ARP) timeline. In a
restored database ingest loads the logs that arrived after the dump again from the spool and the
vault; sealed days within the database retention (`db.retention_days`, default 400 days) are
loaded automatically, older ones are not ("arşivden yeniden yükle" is a later feature). Users,
5651 lookups, person and device details, settings and the audit log of the database exist
**only** in the database and its backups.

**What is in a dump?** The users' password hashes (Argon2id), the TOTP secrets encrypted with
`secret.key`, the hashes of recovery codes and sessions, and personal data (persons, devices,
lookups, the audit log, log rows). Stored credentials (database password, Kamu SM customer number
and password, replica password) are **not** in the database; they are in `secrets.json`. Without
`secret.key` the TOTP secrets in a dump cannot be decrypted. Keep the dump files as carefully as
the evidence: they contain personal data (KVKK).

## 2. Schedule, retention, checks

- Task `\ARCILOGGER\Backup`, daily at 01:40 (Türkiye time), as LOCAL SERVICE; if the server is
  off at that time, it runs once it is on again. `pg_dump -Fc` with ARCILOGGER's own database role
  (password from the encrypted secret store, never on a command line).
- File: `D:\ARCILOGGER\backups\scheduled\arcilogger-YYYYmmdd-HHMMSS.dump` with `….dump.json` next
  to it (SHA-256, size, versions, schema revision). The dump is written under a temporary name,
  checked with `pg_restore --list` and renamed only then.
- Retention: the newest dump of each of the last **14** days, the last **8** weeks and the last
  **12** months (`[backup] keep_daily`, `keep_weekly`, `keep_monthly`); the newest dump and the
  dump of the last good run (the one `last.json` names) always stay — a clock set back gives a new
  dump an older name than the dumps already there (`R20-backup:clock`, `medium`). Deleting touches
  only ARCILOGGER's dump names in this folder — never the pre-upgrade dumps in `backups\db` or any
  other file.
- Disk: room for about 30 dumps. After the first weeks look at the total of `& $db backup list`.
  Without `[backup] min_free_gb` (10) **plus the size of the newest dump** free, the backup does
  not start (the raw logs are written to the same disk).
- Folder rights: only the scheduled task writes; the ARCILOGGER services (API, worker, …) cannot
  read the dumps. Limit: every Windows service running as LOCAL SERVICE holds the tasks' rights
  ([ADR-0007](adr/0007-windows-installer.md) section 8): it can read the dumps and **replace a
  dump together with its sidecar**. The sidecar check therefore finds accidental damage only, and
  `pg_restore` runs the SQL in a dump with the PostgreSQL superuser's rights: restore a copy whose
  SHA-256 you recorded off the server (sections 4 and 5).

Settings (`C:\ProgramData\ARCILOGGER\arcilogger.toml`, section `[backup]`; defaults without it):
`[backup] enabled` (true), `[backup] dir` ("backups/scheduled", relative to `data_dir`),
`[backup] keep_daily` (14), `[backup] keep_weekly` (8), `[backup] keep_monthly` (12),
`[backup] pg_dump` and `[backup] pg_restore` (empty: the PostgreSQL service's `bin` folder, then
the newest `C:\Program Files\PostgreSQL\<version>\bin`, then `PATH`), `[backup] min_free_gb` (10).
If you change `dir`, set the new folder's rights yourself (the installer sets them on the default
folder only). `enabled = false` (another product backs up the database, for example) makes the
task do nothing and switches the worker's `R20-backup` alerts off (the worker reloads the
configuration within seconds; on a version before this one `Restart-Service ArciWorker`;
[ALERTS.md](ALERTS.md)).

**From the browser:** the **Ayarlar → Yedekleme** (Settings → Backup) page of the web interface
changes `[backup] enabled`, `[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly`
and `[backup] min_free_gb`. This version does not show the last backup's details on the page
(`state\backup\last.json`, below): a failed or missing backup shows as an `R20-backup` alert on
the Security page. Every change asks for the password again; switching the backup off also
raises an `R20-config-change` (medium) alert. `[backup] dir`, `[backup] pg_dump` and `[backup] pg_restore` change on the console only: the
task runs programs from trusted places only, and a `pg_dump` or `pg_restore` path outside Program
Files is recorded with its SHA-256 by the installer ([SETTINGS.md](SETTINGS.md) section 6). A
restore and the drill (sections 5 and 6) always stay on the console.

**Every day** ([FIRST-STEPS.md](FIRST-STEPS.md) section 9):

```powershell
Get-Content D:\ARCILOGGER\state\backup\last.json
& $db backup list
```

`last.json` must show `"ok": true` and today's date (`finished`). A failed or refused backup
writes `"ok": false` and a short English error (`error`), and an `R20-backup` (`high`) alert in
`alerts.jsonl`. Details: `D:\ARCILOGGER\logs\backup.log`. A last task result of `0x1` means the
backup **or its retention** needs attention: with `"ok": false` no dump was made; with
`"ok": true` and today's date the dump was made and checked and only old dumps could not be
deleted (`R20-backup:prune`, `medium`); with an older date the dump may have been made but
`last.json` could not be written (`R20-backup`, `high`) — `backup.log` says which. `R20-backup` and
`R20-backup:clock` are resolved by a later good backup; `R20-backup:prune` an hour after a later
good backup that no longer reports it.

**Manual backup and checks:**

```powershell
& $db backup run                       # a backup now (the same as the task)
& $db backup verify                    # newest dump: size, SHA-256, pg_restore --list
& $db backup verify --all --deep       # every dump, all data read (takes long)
& $db backup prune --dry-run           # what the retention would delete
```

## 3. Offline copy: `secret.key`, Caddy CA, `arcilogger.toml`

These are **never copied next to the dumps automatically**: a dump plus `secret.key` reveals the
TOTP secrets, `secret.key` plus `secrets.json` the Kamu SM credentials and the database password,
and the Caddy CA's private key is enough to impersonate the server. Copy them **once** after the
installation and after every change (key renewed, configuration changed) to an encrypted offline
medium (e.g. a USB stick encrypted with BitLocker To Go — **verify**), kept **apart** from the dump
copies (a safe or locked cabinet):

```powershell
$usb = "E:\ARCILOGGER-keys-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Path $usb | Out-Null
Copy-Item C:\ProgramData\ARCILOGGER\secret.key, C:\ProgramData\ARCILOGGER\arcilogger.toml -Destination $usb
Copy-Item C:\ProgramData\ARCILOGGER\caddy -Destination $usb -Recurse
Get-FileHash $usb\secret.key, $usb\arcilogger.toml -Algorithm SHA256
```

`secrets.json` (the encrypted credentials) may go onto the same encrypted medium; otherwise the
Kamu SM credentials are entered again on a new server (`& $core secret set …`). Write the hashes
on paper and keep them with the medium.

**Confirming the copy (Uyum Durumu, card 10).** After making the copy, open the **Uyum Durumu**
page of the web interface and press **Yapılandırma yedeğini aldım** (I have copied the
configuration) on card 10 (**Configuration and database backups**). The confirmation dialog says:

> You confirm that arcilogger.toml, secret.key and the Caddy CA folder (with its private key) were
> copied to encrypted offline media kept apart from the backups (secrets.json optional; BACKUP.md
> section 3). The confirmation is written to the audit log with the configuration's current
> SHA-256.

Only administrators see the button (`settings.manage`); the auditor sees the card but cannot
confirm. ARCILOGGER cannot see the offline medium: the confirmation is the administrator's
statement that the copy was made, not a check of the copy itself. It is written to the audit log
as "Configuration backup confirmed" (`compliance.config_backup_confirm`) with the SHA-256 of
`arcilogger.toml` at that moment (`config_sha256`); every confirmation is a new entry. When the
file cannot be read, nothing is recorded (`config_backup_unreadable`: "The configuration file
could not be read; the backup confirmation was not recorded.").

Card 10 looks at the nightly database backup first (`[backup] enabled`, `state\backup\last.json`,
open `R20-backup` alerts; section 2): with the backup off, failed or `last.json` damaged the
card shows **Problem** (a file that cannot be read at all makes it **No data**: "This check could
not be run now"); while the installation is younger than `alerts.backup_missing_hours` (48)
and the first backup has not run, **No data**; when the last good backup is older than
`alerts.backup_stale_hours` (36) or there is none, **Attention**. With the backup in place it
turns to the confirmation:

| State | Card 10 |
|---|---|
| Never confirmed | **Attention**: "The offline copy of the configuration files was never confirmed (BACKUP.md section 3)." |
| `arcilogger.toml` changed after the last confirmation (on the Settings pages or by hand) | **Attention**: "The configuration changed after the last confirmation (…); make a new offline copy." |
| The SHA-256 of the last confirmation is the file's current one | **OK**: the time and size of the last backup, the time of the confirmation |

The comparison covers `arcilogger.toml` only. The application settings live in the database and
do not change the file; a changed `secret.key`, `secrets.json` or Caddy CA does not turn the card
to **Attention** by itself either. In those cases, too, make a new copy and confirm again. The
card never shows who confirmed (the audit log does). Details:
[COMPLIANCE-STATUS.md](COMPLIANCE-STATUS.md) section 2.10.

## 4. Copying the dumps off the server

The dumps lie on the **same disk** as the database: they protect against a wrong action, a failed
upgrade or logical damage, not against losing the disk or the server. Once a week (and before an
important change) copy the files with their sidecars to an external disk or to a **separate**
share on a NAS (the WORM folder belongs to the vault replica; `<NAS>` is the device's name):

```powershell
# External disk (F:):
robocopy D:\ARCILOGGER\backups\scheduled F:\ARCILOGGER-database *.dump *.dump.json /XO /R:1 /W:5
# A separate share on the NAS (name and rights to be decided on site):
robocopy D:\ARCILOGGER\backups\scheduled \\<NAS>\arcilogger-db-backup *.dump *.dump.json /XO /R:1 /W:5
# Check the copy (against the SHA-256 in its sidecar):
& $db backup verify (Get-ChildItem F:\ARCILOGGER-database\*.dump | Sort-Object Name | Select-Object -Last 1).FullName
# Record the SHA-256 of every new copy, once, next to the copies (off the server):
$list = "F:\ARCILOGGER-database\SHA256.txt"
Get-ChildItem F:\ARCILOGGER-database\*.dump | Where-Object { -not ((Test-Path $list) -and (Select-String -Path $list -SimpleMatch -Quiet -Pattern $_.Name)) } | Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash)  $(Split-Path $_.Path -Leaf)" } | Add-Content $list
```

`SHA256.txt` is what a restore is checked against (`--sha256`, section 5): the sidecar next to a
dump on the server can be replaced together with the dump (section 2). A dump never changes once
it is written, so a line once recorded stays valid; if a later `Get-FileHash` of the same name
gives another value, do not restore that file. Keep `SHA256.txt` where only administrators can
write (do the same on the NAS share).

The copies contain personal data too: use an encrypted disk or a share only administrators can
reach. You delete old copies outside the server yourself (ARCILOGGER cleans only its own folder).
Automating the copy is the owner's decision ([ADR-0009](adr/0009-database-backup.md), open
points).

## 5. Restore

A restore always goes into a **new database**: the live database is never dropped or
overwritten. A restore into an existing name, into the configured (live) database, of a dump
whose SHA-256 does not match its sidecar, or whose SHA-256 differs from the one given with
`--sha256` is refused before anything changes. The PostgreSQL superuser's (`postgres`) password
is asked for; it is never put on the command line.

**Which file?** `pg_restore` runs the SQL in a dump with the superuser's rights, and a dump in
`backups\scheduled` can be replaced together with its sidecar by any LOCAL SERVICE process
(section 2): the sidecar check finds accidental damage only. Restore, wherever you can, a copy off
the server with the SHA-256 recorded for it in `SHA256.txt` (section 4). A dump without such a
record (last night's, not copied yet) is checked against accidental damage only; the command says
so in its output.

1. Choose the dump and check it:

   ```powershell
   & $db backup list
   $file = "F:\ARCILOGGER-database\arcilogger-20261019-014002.dump"
   $hash = "<the SHA-256 recorded for this file in SHA256.txt>"
   & $db backup verify --deep $file
   ```

2. Restore it into a new database (name: lower-case letters, digits and `_`):

   ```powershell
   & $db backup restore --file $file --sha256 $hash --target-db arcilogger_20261019
   ```

   A dump in `backups\scheduled` or a pre-upgrade dump (`backups\db\*.dump`, no sidecar: only
   `pg_restore --list` is checked) is restored with the same command, without `--sha256` when no
   hash was recorded. The new database gets the `C` locale like the installer's (not the
   PostgreSQL cluster's default). With TimescaleDB, `timescaledb_pre_restore()` /
   `timescaledb_post_restore()` run automatically. The restore runs one statement after the other
   and stops at the first error: the new database is then **incomplete** — drop it with the
   `DROP DATABASE` the command prints and try again (with the same or another name).

3. The command prints how to switch ARCILOGGER to the new database:

   ```powershell
   Stop-Service ArciWorker, ArciApi, ArciIngest
   notepad C:\ProgramData\ARCILOGGER\arcilogger.toml   # [database] name = "arcilogger_20261019"
   & $db upgrade                                         # only if the command said "older schema"
   Start-Service ArciIngest, ArciApi, ArciWorker
   ```

   The collector and sealing use no database and keep running meanwhile. Once the services run,
   sign in to the web interface and try the **Denetim Kaydı** and **5651 Sorgu** pages;
   `& $ingest status` shows the days after the dump being loaded.
4. To go back, set `[database] name` to the old name and restart the services. Do **not** drop
   the old database before the new one is accepted; dropping it is the owner's decision
   (`& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE arcilogger"`
   — **verify** the name and the PostgreSQL path).

**Audit log and restore:** going back to an older dump rewinds the audit log in the database to
the dump. The entries written after it stay in the vault's sealed daily audit files and today's
`spool\_audit` file; the restored chain continues from the dump's last entry. Record every
restore (date, file, reason, who) in a written report.

**On a new server (after a disaster):** (1) install PostgreSQL and ARCILOGGER's prerequisites as
in [INSTALL.md](INSTALL.md); (2) copy `arcilogger.toml`, `secret.key` (and `secrets.json` if you
kept it) from the offline medium into `C:\ProgramData\ARCILOGGER\`, and the `caddy\` folder too —
the installer keeps an existing key and configuration; (3) bring the vault back from the replica
into `D:\ARCILOGGER\vault`; (4) run the installer; (5) restore the newest dump into a new database
as above and switch ARCILOGGER to it. This order is **to be verified on site**; try it in the
first drill.

## 6. Restore drill (every three months)

A backup is a backup only once it has been restored. **Every three months** restore the newest
copy off the server (section 4: it checks the copy too) into a trial database, count a few
tables, drop the trial database and write the result down (date, file, duration, counts):

```powershell
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"   # verify: the installed version
$last = (Get-ChildItem F:\ARCILOGGER-database\*.dump | Sort-Object Name | Select-Object -Last 1).FullName
$hash = "<the SHA-256 recorded for it in SHA256.txt>"
& $db backup restore --file $last --sha256 $hash --target-db arcilogger_drill
& $psql -h 127.0.0.1 -U postgres -d arcilogger_drill -c "SELECT count(*) FROM users" -c "SELECT count(*) FROM lookups" -c "SELECT count(*) FROM audit_log"
& $psql -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE arcilogger_drill"
```

Compare the counts with the same queries on the live database (it may have more: the records
added after the dump). The drill does not touch the live database; no service needs to stop.

## 7. Troubleshooting

| Symptom (`last.json` `error`, `backup.log`) | What to do |
|---|---|
| `not enough free space …` | Free space on `D:` or review `[backup] min_free_gb` / the retention numbers; the raw logs go to the same disk |
| `pg_dump not found …` | Set the full paths in `[backup] pg_dump` and `[backup] pg_restore` (e.g. `C:/Program Files/PostgreSQL/16/bin/pg_dump.exe`) |
| `… older than the server …` | Point `[backup] pg_dump` at the `pg_dump.exe` in the server's own `bin` folder |
| `password authentication failed …` | The database password does not match the secret store: run the installer again with `-ResetDatabasePassword` |
| `connection … refused` / `timeout` | Is the PostgreSQL service running (`Get-Service postgresql*`)? |
| `Permission denied` / `Access is denied` (`backups\scheduled`, `state\backup`) | Apply the rights again: `powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` |
| `retention failed: …` (`backup.log`, `R20-backup:prune`) | The new dump exists (`"ok": true`); old dumps could not be deleted (rights on `backups\scheduled`, a file held open by robocopy or antivirus). Remove the cause; the next run deletes them |
| `out of shared memory` … `max_locks_per_transaction` (`pg_dump`) | `pg_dump` locks every table in one transaction, and with TimescaleDB every chunk is a table (their number grows every day). PostgreSQL's lock table holds about `max_locks_per_transaction` (default 64) locks per connection (`max_connections`). As `postgres`: `ALTER SYSTEM SET max_locks_per_transaction = 256;`, stop the ARCILOGGER services and restart the PostgreSQL service — **verify** the value against the number of tables (`SELECT count(*) FROM pg_class WHERE relkind IN ('r', 'p')`). A restore runs one statement at a time and does not need it |
| `… differs from --sha256 …` (restore) | Do not restore this file: it is not the file whose hash was recorded. Take another copy and look into how the file changed (section 2) |
| Task result `0x1`, `last.json` not current | Read `logs\backup.log`; try by hand with `& $db backup run` |

## 8. To verify on site

- `pg_dump` as the ARCILOGGER role (no superuser) on a database with TimescaleDB;
  `timescaledb_pre_restore()` / `timescaledb_post_restore()` in a restore.
- With TimescaleDB: the number of tables (chunks and compressed chunks) against
  `max_locks_per_transaction` × `max_connections`, once a year (section 7); a restore drill with
  real data.
- The password file in LOCAL SERVICE's temporary folder is open to that account only
  (`C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp`).
- Duration (the task is stopped after 4 hours) and size of a backup with real data; the retention
  numbers fit on the disk.
- Copying to an external disk or a NAS share; BitLocker To Go; the disaster-recovery order.
