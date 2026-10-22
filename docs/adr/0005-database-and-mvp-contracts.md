# ADR-0005: PostgreSQL 16 (+ optional TimescaleDB), SQLAlchemy 2, Alembic, psycopg 3, reportlab; MVP contracts

- Status: accepted. Proposed 2026-10-12; the owner accepted the psycopg licence point below for
  internal use during the MVP (recorded 2026-10-13, [ASSUMPTIONS.md](../ASSUMPTIONS.md) D3).
  Decision 9 (reportlab) recorded 2026-10-13.
- Context: milestone M2 starts with several work packages in parallel (parsers, ingest,
  correlation/5651 lookup, authentication). They can only work in parallel if the schema, the
  interfaces between components, all third-party dependencies and the test fixtures exist first.

## Decision

1. **PostgreSQL 16** on the Windows server (EnterpriseDB build), **TimescaleDB optional**. The
   same migration produces hypertables when TimescaleDB is installed and **natively partitioned
   tables** otherwise (`PARTITION BY RANGE (time)`, monthly partitions `<table>_pYYYYMM`, a
   default partition for outliers); `[database] timescale = auto | require | off`. Reason: the
   TimescaleDB Windows build must still be verified on the chosen Windows version
   (ASSUMPTIONS), so plain PostgreSQL is the likely production mode at first. At the expected
   volume (spec 14: 1–5 million messages/day, 400 days in the database, i.e. 0.4–2 billion rows
   in `messages` alone) unpartitioned heaps with `DELETE`-based retention would mean vacuum
   storms and index bloat on the 4-core/16 GB server; monthly partitions keep indexes per month,
   let 1-day and 30-day searches prune to one or two partitions and make retention a `DROP
   TABLE`. Monthly rather than daily: ~210 partitions for 15 tables over 14 months instead of
   ~6,000; the price is retention granularity (up to one extra month stays until the month has
   passed the cutoff). The decision is made before the baseline is frozen, because converting
   tables later would be a data migration. TimescaleDB, where available, adds compression.
2. **Time-series primary keys `(time, id)`** so the schema is valid for hypertables and native
   partitions alike (every unique index must contain the partitioning column).
3. **SQLAlchemy 2** (typed declarative models: `Mapped[...]`, `mapped_column`) as the single
   description of the schema; **Alembic** for migrations, run **programmatically**
   (`arcilogger-db upgrade`; migrations ship inside the wheel, no `alembic.ini`), with a test that
   fails when models and migrations disagree and a test that requires one head.
4. **psycopg 3** (`psycopg[binary]`, libpq bundled in the wheel; Windows wheels exist) as the
   driver; it is also what the ingest service needs for fast `COPY`. The driver stays **behind
   `arcilogger_db`**: bulk loading is `arcilogger_db.bulk.copy_rows` (driver errors surface as
   SQLAlchemy exceptions) and a test fails when any module outside `arcilogger_db/bulk.py`
   imports psycopg, so a switch to another driver (see the licence point) touches one package.
5. INET/CIDR values are returned as **strings** (`native_inet_types=False`; SQLAlchemy 2.1 would
   return `ipaddress` objects), every session uses `TimeZone=UTC` and `client_encoding=UTF8`,
   bound parameters are hidden from logs and error messages, and the password comes only from the
   encrypted secret store (`database.password`).
6. **Contracts as code** (docs/MVP-CONTRACTS.md): the parser interface
   (`arcilogger_ingest.parsers`), the 5651 lookup types (`arcilogger_worker.lookup`), the API
   security, error format and audit helpers (`arcilogger_api.security`, `.errors`, `.audit`),
   auto-discovered routers, and pytest fixtures with **one fresh database per test**
   (`CREATE DATABASE … TEMPLATE`, ~0.1 s) instead of transaction rollback, because API code
   commits and the audit chain needs real concurrency.
7. Every MVP dependency is added now, so parallel work never touches `uv.lock`. The API depends
   on `arcilogger-worker` (it stores and renders the 5651 lookup types and calls the engine); a
   test checks that every package declares the ARCILOGGER packages it imports.
8. **No floats in settings or audit details**: the audit chain hashes canonical JSON (RFC 8785
   without floats), and every settings change is audited with old and new value, so settings
   are integers (`lease.timeout_percent = 110` for the spec's "× 1.1") and `set_setting` refuses
   anything that is not canonical JSON.
9. **PDF reports with reportlab** instead of the specification's WeasyPrint (spec 4: "use these
   unless you have a strong reason; then explain"). Reasons: WeasyPrint needs the Pango/GLib
   native libraries, which on Windows must be installed separately — one more third-party binary
   stack on the server; headless Chromium printing would ship a browser. reportlab is a
   pure-Python BSD wheel (table below). The official 5651 report must be **byte-identical** when
   rendered again from the same stored inputs (its SHA-256 is handed out and audited): reportlab
   runs in its invariant mode, the fonts (DejaVu Sans / Sans Mono, permissive licence) are
   bundled in the package and never loaded from the system or the network (`arcilogger_api.
   reports.fonts`, docs/RAPOR.md). Consequence: the layout is written in Python
   (`arcilogger_api.reports.render`), not as HTML/CSS templates.

## Dependencies added (licences checked from the package metadata, 2026-10-12)

| Package | Version | Licence | Windows | Used by |
|---|---|---|---|---|
| SQLAlchemy | 2.1.4 | MIT | cp312 win_amd64 wheel (pure-Python fallback) | db |
| Alembic | 1.20.0 | MIT | pure Python | db |
| Mako (Alembic) | 1.4.3 | MIT | wheel | db |
| MarkupSafe (Mako) | 3.0.4 | BSD-3-Clause | cp312 win_amd64 wheel | db |
| **psycopg / psycopg-binary** | 3.3.6 | **LGPL-3.0-only** | cp312 win_amd64 wheel (bundles libpq) | db |
| argon2-cffi | 25.1.0 | MIT | pure Python | api |
| argon2-cffi-bindings | 26.1.0 | MIT | abi3 win_amd64 wheel | api |
| cffi / pycparser | 2.1.1 / 3.11 | MIT-0 / BSD-3-Clause | wheel / pure | api (already used by cryptography) |
| pyotp | 2.10.0 | MIT | pure Python | api |
| segno | 1.6.6 | BSD | pure Python | api (TOTP QR code as SVG) |
| reportlab | 5.0.1 | BSD | pure-Python wheel | api (PDF reports) |
| pillow (reportlab) | 12.3.0 | MIT-CMU (HPND) | cp312 win_amd64 wheel | api |
| charset-normalizer (reportlab) | 3.5.2 | MIT | wheel | api |

Already present and relevant: FastAPI pulls `opentelemetry-api` (Apache-2.0); ARCILOGGER
switches FastAPI's built-in telemetry hooks off and installs no exporter.

### The psycopg licence (decided: accepted for internal use)

psycopg 3 is **LGPL-3.0**, which is not on the "fine" list (MIT/BSD/Apache/PSF/ISC/MPL) but is
also not GPL/AGPL. ARCILOGGER uses it unmodified, as a separately installed, dynamically imported
library, and the installer ships it as its own wheel that can be replaced, with its licence text
(`THIRD-PARTY-NOTICES.txt`, `licenses\`). **Decision:** the owner accepted psycopg for ARCILOGGER's
internal use (ASSUMPTIONS D3). A legal review is still recommended before ARCILOGGER is
distributed to other organisations (the plan since 2026-10-20, ASSUMPTIONS "Owner 2026-10-20
(product)"). Fallback if that review refuses LGPL: `pg8000` (BSD-3-Clause,
pure Python) — SQLAlchemy supports it; only `arcilogger_db.bulk` (COPY, slower) and the driver
name in `arcilogger_db.engine` would change, because no other module may import the driver
(enforced by a test).

## Consequences

- Production needs PostgreSQL 16 (+ optionally TimescaleDB) installed by the Windows installer;
  the database must be created with `ENCODING 'UTF8'` (the migration refuses anything else) and
  `timescaledb.telemetry_level = off` must be set server-wide.
- With TimescaleDB, `shared_preload_libraries = 'timescaledb'` must be set **before the first
  `arcilogger-db upgrade`**: otherwise `auto` falls back to native partitions for good (the
  baseline runs once; `arcilogger-db check` then warns). The migration creates 15 compression
  jobs; in a test container with default settings they repeatedly failed to start with "out of
  background workers" — size `timescaledb.max_background_workers` (≥ 16 for one database) and
  `max_worker_processes` per the TimescaleDB documentation of the installed version
  (*verify on site*).
- Without TimescaleDB, partitions must exist ahead of time: `arcilogger-db upgrade` creates them
  for the whole `db.retention_days` window (400 days: 13–14 months back) up to three months
  ahead, and the worker runs `ensure_partitions` daily (`arcilogger-db partitions` does the same
  by hand). The ingest vault re-load (after a database loss) covers the same window, so a
  re-loaded day always finds its month. Missing partitions are not an outage — rows go to the
  default partition and are moved when their month's partition is created (only for months
  inside the window; every new partition scans the default partition under an exclusive lock,
  so it must stay small) — and `arcilogger-db check` warns.
- CI runs the database tests for real: Linux against `timescale/timescaledb:2.30.2-pg16`
  (hypertables expected), Windows against the runner's preinstalled PostgreSQL (plain tables).
- A new schema change is a new Alembic revision; parallel branches each add one and are
  re-chained when merged (a test enforces a single head).
