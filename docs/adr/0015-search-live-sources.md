# ADR-0015: Search, live tail and sources — one query language, typed tables, budgeted scans, a summary table, a spool-tail hub

- Status: accepted (2026-10-26). Decided by the developer on the owner's delegation; the owner
  may change any part of it (open points at the end).
- Related: [ADR-0001](0001-windows-server-browser-only.md), [ADR-0002](0002-own-syslog-collector.md)
  (the raw spool), [ADR-0005](0005-database-and-mvp-contracts.md) (PostgreSQL, TimescaleDB or
  native partitions), [ADR-0011](0011-usage-analytics.md), [ADR-0013](0013-offline-licensing.md)
  (5651 Temel is never locked), [ADR-0014](0014-browser-administration.md) (the settings pages).
  The names and rules this decision fixes are code: the query language
  `arcilogger_api.search.grammar`, the field catalogue `arcilogger_api.search.fields`, the Python
  predicate `arcilogger_api.search.matcher`, the texts `arcilogger_api.search.messages`, the kinds
  `arcilogger_db.kinds`, the summary table `arcilogger_db.searchcounts` (revision `0016_search`,
  [DATA-MODEL.md](../DATA-MODEL.md)) and the settings group `search`
  ([AYARLAR.md](../AYARLAR.md) / [SETTINGS.md](../SETTINGS.md)). The endpoints go into
  [MVP-CONTRACTS.md](../MVP-CONTRACTS.md) §17; the user guide is ARAMA.md / SEARCH.md.

## Context

The specification asks for three pages the menu has shown as placeholders since the MVP:
**Arama** (search the parsed records, spec 12.3 page 3), **Canlı Akış** (watch lines arrive,
page 4) and **Kaynaklar** (the health of every log source, page 5), plus the Panel's parse
failures. The constraints:

- **Ingest comes first.** One Windows server runs collector, ingest, worker, API and PostgreSQL.
  Ingest with the real parsers loads 7,200–9,400 lines a second, already below its 10,000 lines/s
  target ([INGEST.md](../INGEST.md)); nothing in the search may slow it down noticeably, and no
  query may run away and compete with it.
- **Volumes.** A site writes 1–5 million lines a day (spec 14); the database keeps up to 400 days
  in monthly partitions or 1-day TimescaleDB chunks compressed after 7 days. FortiGate bodies in
  `messages.text` are 690–930 characters.
- **KVKK.** Search results, statistics and the live tail are person-level data: every view is
  noticed (`require_privacy_notice`), audited and least-privilege; no personal data goes into a
  URL or an access log.
- **5651 Temel.** Search, live tail and sources belong to the basic functions: no licence state
  may block them (ADR-0013, ASSUMPTIONS C-2).
- **Evidence is the raw line.** Every result row must lead to its exact raw line (`GET /raw`,
  byte-exact with its SHA-256) and to an evidence package; nothing is modified.
- **Works when things are new or broken.** DEVICE-SETUP checks a new device in Canlı Akış, which
  must show its lines while ingest lags or is stopped.

## Decision

1. **One query language** for search, live tail, saved searches, export and the evidence extract:
   clauses joined by AND, a comma is OR inside one field, a leading `-` or `NOT` negates, no
   parentheses and no OR across fields (refused with a hint, never guessed). It is parsed once,
   by a hand-written linear parser bounded by limits (1,000 characters, 16 clauses, 20 values per
   clause, 5 free-text terms, 256 characters per value), into a syntax tree with character spans,
   and compiled twice: to SQLAlchemy Core with bound parameters (search) and to a Python predicate
   (live tail). A property test keeps the two equal. A word with `:` that names no field is an
   error, never free text.
2. **No trigram index in this wave.** `pg_trgm` is available and trusted on PostgreSQL 16 (checked
   locally, 1.3–1.6), but a GIN `gin_trgm_ops` index on `messages.text` costs at least about
   **120 µs per row** inside the ingest `COPY` on realistic 690–930-character FortiGate bodies
   (about **186 µs per row** with `fastupdate` on), the index is about **as large as the table**,
   and compressed TimescaleDB chunks could not use it. At 7,200–9,400 lines a second that would
   cost ingest most of its time. Free text is a **time-budgeted scan** that returns results
   newest first and says how far it got (`complete`, `scanned_from`). Terms without a letter
   (addresses, numbers) use `LIKE` instead of `ILIKE` (about three times cheaper).
3. **Typed tables drive a query with a typed field.** Any typed field (`srcip`, `domain`, `mac`,
   `action` …) makes the typed tables (`fg_*`, `mt_*`) the driver: one `UNION ALL` branch per
   table that has every typed field of the query. Without a typed field `messages` drives (one row
   per loaded line). `messages`-only conditions in a typed query (free text, `host`, `severity`,
   `error`, `subtype`) join `messages` by the raw reference. The kinds of `type:` (traffic, web,
   dns … wifi, heartbeat, log, syslog, unknown, other) are one table in `arcilogger_db.kinds`,
   shared by the planner, the matcher, the summary table and the live tail; a test fails when a
   parser emits a `(type, subtype)` no kind names.
4. **Every request has a time budget** (rows 4 s, statistics 8 s, settings). Windows are scanned
   newest first, starting at 1 hour and doubling while fast, each under a statement deadline; when
   the budget is used up the answer is partial with a `frontier` cursor. A first window that times
   out is halved down to 5 minutes before the request fails with 504.
5. **One audited search, many follow-ups.** `POST /search` runs the first page, writes
   `search.run` and returns a server-side `search_id`; pages, statistics, evidence references and
   export take only that id, so every row, count and file a user sees belongs to an audited
   search (statistics of `srcip:` or `user:` are a profile of one person).
6. **Exact hourly summary table** `search_counts (hour, source, raw_day, kind, tbl, action, net)`
   → `n`, written by the ingest loader in the same transaction as the rows (exactly-once is
   kept), recounted from the rows for files loaded before the upgrade by the worker job
   `search.counts_backfill`. `tbl` (the typed table, `''` for none) makes the summary equal to the
   typed driver even for a line whose typed row was dropped; `raw_day` makes a per-file rebuild
   possible; the private `/24` gives the VLAN and department facets. Totals, histogram and the
   five facets of a broad 30-day query come from it in well under a second; the partial hours at
   both edges are counted directly.
7. **One new index**, `fg_web (reverse(lower(hostname)), time)`: "this domain and its
   subdomains" becomes a prefix range (measured 1.6 ms on 2 million rows). Everything else uses
   existing indexes; `qname:` is the exact, indexed DNS name.
8. **Live tail reads the raw spool, not the database**, through one shared reader (the hub) per
   API process: it works while ingest lags or is stopped, shows `unknown-<ip>` folders the moment
   the collector creates them, gives every line its exact raw reference, never writes and never
   keeps a file open between polls (so the sealer can always delete a sealed day).
9. **Two-step stream**: `POST /live/streams` (JSON errors, audited `live.open`) then a `GET`
   read with `fetch`, a hand-built `StreamingResponse` that holds no database session.
   Keep-alives never touch the idle timer; the session is re-checked every 30 s and an idle viewer
   is signed out like on every page. Caddy does not compress the stream; the API gets a graceful
   shutdown timeout so an open stream cannot hang a service stop.
10. **No personal data in URLs or access logs.** Queries travel in POST bodies; hand-overs between
    pages use an in-memory store, not router state (which the browser may write to disk).
11. **Kaynaklar only reads**; configuration stays in Ayarlar → Kaynaklar, which "Kaynak olarak
    tanımla" opens with the add dialog filled in.
12. **Least privilege, no new permission key, no licence gate**: `search.read` (admin, auditor)
    for Arama and Canlı Akış; `search.export` (admin) plus a re-authentication within 5 minutes
    for export; `evidence.create` for "Kanıt paketine aktar"; `sources.read` (admin) for
    Kaynaklar. Every person-level endpoint requires the accepted privacy notice.

How values compare, in both compilations: text ignores case for ASCII letters only (the
database's locale C: `lower()`/`ILIKE`; Turkish İ/ı, Ş/ş … are matched as typed, and the
answer says so); `qname:` and `source:` compare exactly as stored (index-backed); a negated
clause is `NOT coalesce(…, false)`, so a record whose columns are empty matches it; `field:*`
means the field has a value (refused for `type`, `vlan` and `department`, which have no column of
their own: `-vlan:*` would not mean "outside every VLAN"). Token fields (`action`, `qtype`,
`level`, `logid`) compare the lower-cased column with the lower-cased input: devices write
`qtype=AAAA` and `action=Edit`, and the summary table lower-cases `action`, so only a
case-insensitive comparison keeps the rows, the counts and the live tail in agreement (none of
these columns is indexed).

## Consequences

- Ingest gains one step (`counts`: about 50–200 upserted keys per batch) and one index; the gate
  is at most 5 % slower in `bench_throughput.py --repeat 3` ([INGEST.md](../INGEST.md)).
- The upgrade to `0016_search` builds the `fg_web` index over the whole table (the services are
  stopped during an upgrade and the spool keeps every incoming line); the installation guide
  ([KURULUM.md](../KURULUM.md) / [INSTALL.md](../INSTALL.md)) is to state the build time measured
  per 10 M rows. On TimescaleDB it is a plain `CREATE INDEX` on the hypertable inside the
  upgrade's one transaction. TimescaleDB's source creates it on every chunk; on a compressed chunk
  it holds only the rows not compressed, so `domain:` reads compressed chunks (older than 7 days)
  by decompression, within the search's time budget. **Shown** on TimescaleDB 2.30.2 with
  PostgreSQL 16 (the CI image's version; 2026-10-27, `db/tests/test_migration_0016.py`): the
  statement is accepted with a compressed chunk present, every chunk gets the index (the
  compressed one included), a new chunk inherits it, and the downgrade drops it and keeps the
  rows. The Linux TimescaleDB CI job runs the same test; should a later TimescaleDB refuse the
  statement, the index is built after the upgrade's transaction instead, and this record is
  amended.
- **Plans on TimescaleDB** (2026-10-27, TimescaleDB 2.30.2, the search performance test on a
  tenth of its tier-1 data, chunks older than 7 days compressed): every query met its budget
  (`domain:` 30 days 235 ms, the 10,000-row export 6.7 s of 10 s), but for a rare value the
  planner reads two of them differently than on native partitions: `domain:` (fg_web) and
  `type:dhcp mac:` (mt_dhcp) scan the uncompressed chunks' primary keys backwards (TimescaleDB's
  ordered chunk append with the page's `LIMIT`) instead of `ix_fg_web_rhost_time` and the MAC
  index, so the test's plan check fails there. The time-budgeted windows bound such a scan, but
  at a million lines a day a 30-day domain search may come back partial. Tier 1 on the Linux CI
  job decides; if it fails there too, the remedy is in the query (an index-ordered branch per
  chunk or `enable_*` settings for the search's read-only transaction), not in the guard.
- Free text is slow by nature: the answer is honest about a partial scan, and the syntax help
  points to the fast fields (`srcip:`, `domain:`, `mac:`, `qname:`). `ip:` on the UTM tables is a
  scan, because their destination addresses have no index.
- The summary table could drift from the rows only through a bug: one shared key function
  (`searchcounts.count_key`), `arcilogger-db search-counts verify` and `rebuild`, and direct
  counting until a range is complete keep that visible and repairable.
- The registries (searches, streams) live in the one API process (ADR-0001: one Uvicorn worker)
  and are lost on a restart; the web interface reruns or reopens, which writes new audit entries.
- Live lines are parsed a second time, capped by a parse budget; lines over it are shown unparsed
  or counted as dropped, never hidden silently.

## Alternatives considered

- **A windowed `pg_trgm` GIN index** (the newest two months): rejected for the measured ingest
  cost above and because compressed chunks cannot use it. Revisit only with an index outside the
  ingest path (e.g. built on sealed days).
- **`messages` drives every query** with `EXISTS` into the typed tables: `dstport:3389
  action:deny` would probe six tables per `messages` row; it can neither use an index nor meet a
  budget.
- **Audit every page, statistics unaudited**: leaves the person profile of a statistics request
  unaudited; one audited `search_id` covers it, and pages of the same search are not audited
  again (documented).
- **A summary key without `tbl` or without `raw_day`**: the rollup and the typed driver would
  disagree for dropped typed rows, and a single file could not be rebuilt.
- **A backfill inside the migration** (up to 20 M rows): makes the upgrade long and its duration
  unpredictable; the worker job recounts finished files in the background instead, and the
  statistics count directly until a range is complete.
- **Live tail from the database**: `messages` has no index on insert order, so "what is new"
  would be a scan per viewer on the database ingest writes to, and it would show nothing while
  ingest lags.
- **One `POST` with a yield-based event stream**: the 200 is sent before the body runs, so a
  refusal (limit, privacy notice, syntax) could not be a JSON error, and the stream would hold
  the request's database session.
- **Router state for hand-overs and `localStorage` for highlight rules**: both can end up on the
  disk of a shared administrator PC; an in-memory store and `sessionStorage` cannot outlive the
  tab.

## Open points (owner decision)

The defaults below are implemented; each alternative is a small follow-up.

| | Question | Default | Alternative |
|---|---|---|---|
| D1 | What `department:` means | the VLAN's default department, labelled "Departman (VLAN'a göre)" | person attribution at the row's time (no summary facet) |
| D2 | Export needs a re-authentication | yes (5 minutes) | no |
| D3 | Auditors have Canlı Akış | yes (`search.read`) | a separate `live.read` |
| D4 | Auditors have Kaynaklar | no (`sources.read` admin only) | grant `sources.read` to auditors |
| D5 | Search on device time only | yes (partition key, indexed); receive time shown in every row | a receive-time index later |
| D6 | Domain input lower-cased | yes; verify on site that names are stored in lower case (`search-counts verify` counts the others) | `lower()` expression indexes |
| D7 | No text index in this wave | yes | an index outside the ingest path later |
| D8 | `search.run` stores the normalised query (it can hold addresses or text) | yes, like `lookup.run` | store only the field names and a hash |
| D9 | An idle Canlı Akış viewer is signed out at `auth.idle_minutes` | yes (unattended screens) | none recommended |
| D10 | Saved searches | personal only | administrators may share |
