# Architecture decision records

| ADR | Title |
|---|---|
| [0001](0001-windows-server-browser-only.md) | Windows server, browser-only access |
| [0002](0002-own-syslog-collector.md) | Own syslog collector; the raw spool is the queue |
| [0003](0003-integrity-on-windows.md) | Evidence integrity and immutability on Windows |
| [0004](0004-web-tooling.md) | Web interface tooling; amendment 2026-10-25: the shipped bundle has no UI kit page and no source maps |
| [0005](0005-database-and-mvp-contracts.md) | PostgreSQL 16 (+ optional TimescaleDB), SQLAlchemy 2, Alembic, psycopg 3, reportlab for PDF reports; MVP contracts |
| [0006](0006-standalone-verifier.md) | Standalone evidence verifier: one file, Python standard library only |
| [0007](0007-windows-installer.md) | Windows installer: offline bundle, WinSW services, operator-installed PostgreSQL; amendment 2026-10-20: the organisation's values (`-SiteName`, `-NtpServer`, `-AdminAddress`) have no default |
| [0008](0008-device-assignment-history.md) | Device-person assignment history: the person at the moment of a 5651 lookup |
| [0009](0009-database-backup.md) | Scheduled database backup (`pg_dump`, task `\ARCILOGGER\Backup`) and restore into a new database |
| [0010](0010-alerts-and-email.md) | Alerts in the database, R15/R20 checks, alert e-mail without log content; amendment 2026-10-20: state workflow, notes, dashboard |
| [0011](0011-usage-analytics.md) | Usage analytics: estimated time per person, site and category from connection logs (rollups, attribution at T, pseudonyms optional) |
| [0012](0012-scheduled-usage-reports.md) | Scheduled usage reports: made and sent by the worker, shared PDF building blocks |
| [0013](0013-offline-licensing.md) | Offline licensing: Ed25519-signed licence files, request codes, paid modules (usage analytics, scheduled reports, bulk CSV import), 5651 Temel never locked, trusted time, threat model |
| [0014](0014-browser-administration.md) | Browser administration: an unprivileged administration agent (ArciAdmin) applies Ed25519-signed request files of the API, an input-free SYSTEM step (ApplySystem) syncs the firewall and the clock, executables checked at run time, live reload without a restart |
