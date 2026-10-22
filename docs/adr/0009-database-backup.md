# ADR-0009: Scheduled database backup and restore

- Status: accepted (2026-10-19); the defaults (01:40, 14/8/12, the folder) wait for the owner's
  confirmation ([ASSUMPTIONS.md](../ASSUMPTIONS.md) B7–B12)
- Related: [ADR-0005](0005-database-and-mvp-contracts.md) (PostgreSQL, TimescaleDB),
  [ADR-0007](0007-windows-installer.md) (task identity, NTFS rights, pre-upgrade dump);
  runbook [YEDEKLEME.md](../YEDEKLEME.md) / [BACKUP.md](../BACKUP.md)

## Context

Until now ARCILOGGER had no scheduled database backup: the installer dumps the database only
before an upgrade (`D:\ARCILOGGER\backups\db`, `Install-Arcilogger.ps1` `Invoke-DatabaseBackup`).

Part of the database is **derived** and comes back by itself: the parsed log tables (`messages`,
`fg_*`, `mt_*`) are loaded by ingest from the spool and the sealed vault — sealed days within
`db.retention_days` (400) are reloaded automatically when their `ingest_offsets` rows are missing
or unfinished, older sealed days are not ("arşivden yeniden yükle" is a later feature,
[INGEST.md](../INGEST.md)) — and the worker rebuilds the lease timeline from them
([CORRELATION.md](../CORRELATION.md)). The rest exists **only** in the database: users and roles
(Argon2id password hashes, TOTP), the 5651 lookups with their request details, the records of the
stored reports and evidence packages and their downloads, the settings operators changed,
persons, device names and notes, alert states, privacy-notice acceptances and the audit log (its
daily projection is also sealed into the vault as `audit.jsonl.gz`, but the database is where it
is continued).

Constraints: the server is offline and runs Windows; secrets never appear on a command line, in a
log or in a plain file; no third-party binaries are shipped; the scheduled jobs run as the shared
`LOCAL SERVICE` account (ADR-0007 section 8); the raw logs are written to the same `D:` disk and
must never be crowded out.

## Decision

### 1. A daily logical dump, `pg_dump -Fc`

`arcilogger-db backup run` dumps the ARCILOGGER database with the operator-installed PostgreSQL's
own `pg_dump` in the **custom format** (compressed, one file, selective restore with
`pg_restore`) — the format and options of the installer's pre-upgrade dump. Physical backups
(`pg_basebackup`, WAL archiving for point-in-time recovery) were not chosen: they need server
configuration, a superuser and much more disk, and the derived part of the data comes back from
the raw logs anyway. A plain SQL dump was not chosen (larger, no selective restore); nor
`pg_dumpall` (needs a superuser and dumps the role password verifiers; the installer creates the
role).

### 2. Who runs it: the `\ARCILOGGER\Backup` task

A Task Scheduler definition like the evidence jobs (`deploy/windows/tasks/Backup.xml`): daily at
**01:40** local time — after sealing (00:10) and the nightly lease rebuild — `StartWhenAvailable`,
`IgnoreNew`, `LOCAL SERVICE`, `ExecutionTimeLimit` 4 hours, `python -m arcilogger_db backup run`.
`Register-ArciTasks.ps1` registers every XML file of the folder, so installations and upgrades add
it. The worker service was not chosen: its account would then need the dumps' rights, and a
scheduled task also runs when the services are stopped.

The dump connects as the ARCILOGGER role (`[database] user`, owner of the database, no superuser)
with the password from the secret store. The password reaches `pg_dump` only through a password
file (`PGPASSFILE`) in a private temporary folder (`tempfile.mkdtemp`: mode 0700, on Windows an
ACL for the owner, SYSTEM and administrators), created with mode 0600 and deleted as soon as the
tool ends; inherited `PGPASSWORD`/`PGSERVICE` variables are removed from the child's environment.
`PGPASSWORD` was not chosen: PostgreSQL's documentation advises against it because other
processes can see a process's environment.

The tools come from `[backup] pg_dump` / `pg_restore`, else from the bin folder of the PostgreSQL
service (registry `ImagePath` of the `postgresql*` services, the installer's choice: a PostgreSQL 16
service first), else from the newest `C:\Program Files\PostgreSQL\<version>\bin`, else from
`PATH`. A `pg_dump` older than the server is refused.

### 3. Files and state

- Folder `<data_dir>\backups\scheduled` (`[backup] dir`; refused inside `spool`, `vault`,
  `state`, `exports`, `logs`), name `arcilogger-YYYYmmdd-HHMMSS.dump` in site time.
- The dump is written as `.<name>.partial`, fsync'ed, read back with `pg_restore --list` and only
  then renamed: a file with the final name is always an archive whose table of contents is
  complete. A sidecar `<name>.json` (schema 1) records SHA-256, size, start and end, the
  `pg_dump` and server versions, the Alembic revision and the TimescaleDB version (or `null`).
- Every attempt writes `state\backup\last.json` atomically (contract: MVP-CONTRACTS section 9);
  a failed or refused attempt also raises `R20-backup` (`high`) in `state\alerts.jsonl` with a
  Turkish/English message and removes what it wrote. A good dump whose retention fails raises
  `R20-backup:prune` (`medium`, `key` "prune": resolved an hour after a later good backup that
  no longer reports it), one whose `last.json` cannot be written `R20-backup` (`high`, resolved
  only by a backup that succeeds after it), one named earlier than a dump already there (a clock
  set back) `R20-backup:clock` (`medium`, resolved by a later good backup).
  `logs\backup.log` keeps one line per step.
- Before it starts, the run requires `[backup] min_free_gb` (10) **plus the size of the newest
  dump** free on the backup disk: a dump must never fill the disk the collector writes raw logs to.

### 4. Rights

`backups\scheduled` is a **protected** folder: SYSTEM and Administrators full control, the tasks
(`LOCAL SERVICE`) Modify — they create, rename and prune dumps. No ARCILOGGER service has any
right there: a compromised API or worker cannot read the dumps. `backups` and `backups\db` stay
administrators-only (inherited from the data root). As with `secret.key` (ADR-0007 section 8),
every process that runs as `LOCAL SERVICE` holds the tasks' rights and can therefore read the
dumps — and replace a dump together with its sidecar. The sidecar is therefore a check against
accidental damage, not against a deliberate change, and `pg_restore` runs every statement of an
archive with the superuser's rights: a dump forged by a `LOCAL SERVICE` process would run its SQL
as the superuser at the next restore. The runbook has the administrator record each dump's
SHA-256 off the server when it is copied (`SHA256.txt` next to the copies) and restore a copy with
`--sha256` (section 8). The fixes proposed in ADR-0007 (per-task SIDs or a dedicated account)
cover this folder too.
`state\backup` inherits the rights of `state`, where the services may write: `last.json` is a
status file, not evidence, but a forged `"ok": true` could hide a failure from the alert check
(open point below).

### 5. Retention

The newest dump of each of the last `keep_daily` (14) days, `keep_weekly` (8) ISO weeks and
`keep_monthly` (12) months **that have a dump** is kept, and always the newest dump. Periods are
counted, not ages, so a clock that jumps forward deletes nothing early. `backup run` prunes after a
successful dump (never after a failed one) and always keeps the dump it has just made: after a
clock set back its name is older than the dumps already there, whose periods would otherwise all
be taken, and it would be deleted at once while `last.json` points at it (`R20-backup:clock`
says so). `backup prune [--dry-run]` does it on demand and keeps the dump a good `last.json`
names. It deletes only files with ARCILOGGER's scheduled-dump name, their sidecars, orphaned
sidecars and leftovers of interrupted runs older than 12 hours, in that one folder and not in its
subfolders — never the installer's dumps (`backups\db`, a different name pattern, deleted by hand)
or any other file.

### 6. What a dump contains — and what it does not

A dump contains the whole database: the Argon2id **password hashes**, the **TOTP secrets
encrypted** with the key of the secret store (`secret.key`), the hashes of recovery codes and
session tokens, the personal data (persons, devices, 5651 lookups, the audit log, the parsed log
rows) and the settings. It does **not** contain any stored credential: the database password, the
Kamu SM customer number and password and the replica password live in `secrets.json`, encrypted
with `secret.key`, outside the database. Without `secret.key` the TOTP secrets in a dump are
useless (every user enrols again, SECURITY.md section 3).

Not in the dump: the raw logs and the vault (evidence with their own second copy, `[replica]`),
`exports` (evidence packages, the stored report PDFs in `exports\reports`), `arcilogger.toml`,
`secret.key` and `secrets.json`, Caddy's internal CA (`C:\ProgramData\ARCILOGGER\caddy`),
`kamusm-roots` and the Zamane client.

### 7. `secret.key` and the Caddy CA are copied offline, separately, by hand

They are never copied next to the dumps automatically. A dump together with `secret.key` reveals
the TOTP secrets; `secret.key` with `secrets.json` reveals the Kamu SM credentials and the database
password; the CA's private key lets its holder impersonate the server to every PC that trusts the
root. They change rarely, so the runbook has the administrator copy `secret.key`,
`arcilogger.toml` and the `caddy` folder once (and after every change) to an encrypted offline
medium kept apart from the dump copies.

### 8. Restore: always into a new database

`arcilogger-db backup restore --file F --target-db NAME [--superuser postgres] [--sha256 HEX]`
reads the superuser password from stdin (a prompt on a console), never from the command line. It
refuses the configured (live) database, the system databases, a name that exists, a dump whose
SHA-256 or size differs from its sidecar, and one whose SHA-256 differs from `--sha256` (the hash
recorded off the server, section 4) — before anything is changed; it prints the dump's SHA-256
for the restore record. It creates the database like the installer does (owner
`[database] user`, `template0`, UTF-8, locale `C` — not the cluster's, which may be a Windows
locale; `pg_restore` without `--create` does not carry the dumped database's locale —
`REVOKE ALL … FROM PUBLIC`), and runs `pg_restore --exit-on-error` as the superuser (object owners
are kept): one transaction per statement, stopping at the first error. Not
`--single-transaction`: it holds a lock on every restored table, index and TOAST table until the
commit, and a TimescaleDB database (a chunk per hypertable and day, the compressed chunks, their
indexes) outgrows PostgreSQL's shared lock table (`max_locks_per_transaction`, 64 per connection
by default) within months. On PostgreSQL 16 with the default settings, a test archive of 3,500
tables (about 17,500 relations) failed with "out of shared memory" in one transaction and
restored without error per statement. The target is always a new database, so a failed restore
is dropped (the error names the `DROP DATABASE`) and repeated. When the dump contains
TimescaleDB, it sets telemetry off for the new database, creates the extension (the version the
sidecar recorded) and wraps `pg_restore` in `timescaledb_pre_restore()` /
`timescaledb_post_restore()`. It then prints how to switch ARCILOGGER to the new database
(`[database] name`, services restarted, `arcilogger-db upgrade` if the dump's schema is older).
Nothing ever drops or overwrites the live database: an operator mistake cannot destroy it,
both databases can be compared, and switching back is changing the name back.

### 9. Copies off the server are made by hand

The dumps lie on the same disk as the database: they protect against operator errors, failed
upgrades and logical damage, not against the loss of the disk or the server. The runbook copies
them to an external disk or a share on a NAS (a separate share — the WORM folder belongs to the
vault replica). Automating that copy is left to the owner (below).

## Consequences

- At most one day of the non-derived data is lost in a restore; parsed logs written after the dump
  are loaded again from the spool and the vault (within `db.retention_days`).
- Disk: about (`keep_daily` + `keep_weekly` + `keep_monthly`, minus overlaps — about 30) × the
  dump size on `D:`. `backup list` shows the total; adjust the numbers after the first weeks.
- Restoring an older dump rewinds the audit log in the database: the entries written after the
  dump stay in the sealed daily audit files of the vault and today's `spool\_audit`, and the
  restored chain continues from the dump's last entry. Record every restore in writing (date,
  file, reason); verify the audit chain on site after the first drill.
- Upgrades pause the tasks and wait for running ones (`-TaskWaitMinutes`): an upgrade started
  during the backup waits for it. After a failed migration (ADR-0007 section 6) the Backup task of
  the previous program may dump the half-migrated schema; restore the pre-upgrade dump from
  `backups\db`, not those.
- `pg_dump` takes a lock on every table in one transaction, and with TimescaleDB every chunk and
  compressed chunk is a table: once their number nears `max_locks_per_transaction` ×
  `max_connections`, the nightly dump fails with "out of shared memory" (`R20-backup`). The
  installation guide sets `max_locks_per_transaction = 256` with TimescaleDB (KURULUM.md 2.1,
  to be verified) and the runbook says how to raise it (BACKUP.md section 7); the restore does
  not need it.
- Verify on site: `pg_dump` as the ARCILOGGER role on a database with TimescaleDB (read rights on
  its catalog); `timescaledb_pre_restore()` / `post_restore()` around `pg_restore` on the
  installed TimescaleDB; the number of tables against `max_locks_per_transaction`; the private
  temporary folder of `LOCAL SERVICE`
  (`C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp`); the `ImagePath` of the EDB
  service; duration and size on real data within the 4-hour limit; a full restore drill.

## Open points (owner decision)

- An automatic copy of the dumps to a NAS (a separate share and account) or to an external
  disk; encryption of the off-server copies (for example BitLocker To Go, verify on site).
- A protected `state\backup` folder (tasks write, services read) like `state\seal`, so no service
  can fake `last.json`.
- The task identity (`LOCAL SERVICE`, ADR-0007 section 8) also decides who can read the dumps,
  and who can replace one.
- `pg_restore` connected as the owner role (the session user the owner, after the superuser has
  created the database and the extension), so that a forged dump cannot run SQL as the
  superuser — whether TimescaleDB's catalog restores that way is to be verified on site; until
  then `--sha256`.
