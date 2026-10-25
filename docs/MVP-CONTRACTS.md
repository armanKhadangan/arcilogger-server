# MVP sözleşmeleri / MVP contracts

> Paralel çalışan iş paketleri (B1 ayrıştırıcılar, B2 ingest, B3 korelasyon + 5651 sorgusu,
> B5 kimlik doğrulama, sonraki API/web/kurulum paketleri) **yalnızca bu sözleşmelere** dayanır.
> Bir sözleşmeyi değiştirmek gerekiyorsa kendi dalınızda değiştirmeyin; raporunuzda
> "shared changes needed" altında belirtin.

Everything below exists as code with tests. File paths are relative to the repository root.

## 1. Packages and commands

| Package (import) | Owns | Command (all accept `--config PATH`) |
|---|---|---|
| `arcilogger-core` | evidence path (never imports the database) | `arcilogger-core …` (Task Scheduler) |
| `arcilogger-db` (`arcilogger_db`) | models, migrations, seed, settings, partitions, bulk loading, test databases, licence state (`licensing`, `license_guard`) | `arcilogger-db upgrade \| current \| check \| partitions \| version \| license status\|request-code\|verify\|install\|remove` |
| `arcilogger-collector` | syslog → raw spool | `arcilogger-collector run` |
| `arcilogger-ingest` | spool → parsers → PostgreSQL | `arcilogger-ingest run [--once] [--workers N] \| status \| version` |
| `arcilogger-worker` | leases, devices, lookups, evidence purge, audit import; later NAT index, usage, rules | `arcilogger-worker run \| rebuild --day D [--to D] \| version` |
| `arcilogger-api` | HTTP API (depends on `arcilogger-worker` for the lookup types/engine) | `arcilogger-api serve [--host H] [--port P] \| setup-token \| create-admin [--deactivate USERNAME] \| version` |

Exit codes. `arcilogger-db`: 0 ok, 1 needs attention (no/old schema, warnings), 2 configuration
error, 3 database unreachable. `arcilogger-worker` and `arcilogger-api setup-token` /
`create-admin`: the same scheme (1 = a job failed / refused), plus `setup-token` 4 = an
administrator exists already, no token (the installer tells it apart from a failure).
`arcilogger-ingest run`: 0 stopped on request, 1 internal error, 2 configuration error, 3 and 4
only with `--once` (database unreachable, another instance running; docs/INGEST.md). The
installer runs `arcilogger-db upgrade` (idempotent; migrates, seeds and creates the monthly
partitions). Every package must declare the ARCILOGGER packages it imports
(`tests/test_package_dependencies.py`).

## 2. Configuration and secrets

`arcilogger.toml` (`arcilogger_core.config.Config`, unknown keys are rejected):

| Section | Keys (defaults) |
|---|---|
| `[database]` | `host` 127.0.0.1, `port` 5432, `name` arcilogger, `user` arcilogger, `sslmode` (libpq, optional), `connect_timeout_seconds` 10, `pool_size` 5, `timescale` auto\|require\|off |
| `[api]` | `host` 127.0.0.1, `port` 8000, `secure_cookies` true, `forwarded_allow_ips` 127.0.0.1 |
| `[ingest]` | `batch_rows` 5000, `batch_seconds` 0.5, `poll_seconds` 0.5, `workers` 2 (0–16; 0 = parse in the service process) |
| `[worker]` | `interval_seconds` 60 |

The database password is **only** in the secret store: `arcilogger-core secret set
database.password` (name `arcilogger_core.secretstore.DATABASE_PASSWORD`). Engines:
`arcilogger_db.engine.make_engine(config, application="ingest")` (password from the store, UTC
sessions, INET as `str`, parameters hidden in logs); sessions:
`arcilogger_db.engine.session_factory(engine)`.

## 3. Database

- Models: `arcilogger_db.models` (one class per table; see `docs/DATA-MODEL.md`).
  `TIME_SERIES_TABLES`, `COMMON_COLUMNS`, enum tuples (`ROLES`, `LEASE_END_REASONS`, …).
- **Time-series storage:** TimescaleDB hypertables, or (plain PostgreSQL) native monthly
  partitions `<table>_pYYYYMM` + `<table>_default`. Always insert into / query the table itself,
  never a partition. `arcilogger_db.partitions.ensure_partitions(connection)` (in a transaction)
  creates missing months; **the worker (B3) calls it once a day**. Retention (later):
  `drop_before(connection, cutoff_day)`. Both are no-ops with TimescaleDB.
- Python value types per column type and a validator: `arcilogger_db.rowcheck.check_row(table,
  values, complete=True)` → list of problems (INET/CIDR/MACADDR as `str`, times aware, no NUL,
  ints in range, JSON without NaN). It is never looser than PostgreSQL (no IPv6 zone ids, only
  the documented `macaddr` layouts; a database test cross-checks).
- **Bulk loading:** `arcilogger_db.bulk.copy_rows(connection, table, rows) -> int` (`COPY …
  FROM STDIN` in the caller's transaction; JSONB handled; driver errors as SQLAlchemy
  `DataError`/`IntegrityError`). **No module outside `arcilogger_db/bulk.py` imports psycopg**
  (test-enforced; the driver may change, ADR-0005).
- Settings: `arcilogger_db.settings.get_setting(session, key)`, `get_settings(session,
  prefix="auth.")`, `set_setting(session, key, value, updated_by=username)`; known keys and
  defaults in `DEFAULT_SETTINGS`. Values must be **canonical JSON — no floats** (`set_setting`
  raises `ValueError`), because every change is audited with `{"old", "new"}`. Internal authors
  `:seed`, `:system`, `:migration` (`:` never occurs in a user name). Internal keys:
  `db.timescale` (migration; `storage` = `hypertables` | `partitions`), `seed.*`,
  `audit.spool_watermark`.
- VLAN and department of an address: `arcilogger_db.network.department_for_ip(session, ip)` →
  `VlanDepartment(vlan_id, vlan_name, department, source)`; the department is the linked one,
  else the VLAN's Turkish name (spec 2.2). Use it wherever a department is derived from a VLAN.
- `lookups`: `authority`, `file_no`, `request_date` and `reason` are NOT NULL and must not be
  blank (spec 12.3 page 6) — the API refuses a lookup without them before running it.
- **Schema changes:** the baseline `0001_baseline` is frozen. If your package needs a column or
  table, add the model change **and** one new revision file
  `db/src/arcilogger_db/migrations/versions/v<NNNN>_<slug>.py` (`down_revision = "0001_baseline"`
  or the head at your branch point). Tests fail when models and migrations disagree and when
  there is more than one head; the orchestrator re-chains parallel revisions on merge.

## 4. Parsers (B1) and ingest (B2)

`arcilogger_ingest.parsers`:

```python
LinePosition(source, source_type, raw_day, raw_line, raw_offset)   # source_type None = unknown sender
outcome = parse_line(record: RawRecord, position)                   # never raises for bad input
outcome.message  -> dict for table "messages" (complete)
outcome.rows     -> tuple[ParsedRow(table, values)] (complete, keys = column names)
outcome.error / outcome.detail -> pattern name / detail for parse_failures (None when parsed)
```

- **Detection:** configured source type wins; unknown senders: `devname=` or `logid=` →
  `fortigate`, else `generic`.
- **Writing a parser** (`parsers/fortigate.py`, `parsers/mikrotik.py` are the real ones; docs/PARSERS.md): subclass
  `Parser`, set `name` and `tables` (the typed tables it may emit), implement
  `parse_record(record, position) -> Parsed(message={type, subtype, severity, facility, program,
  host, text, extra}, rows=(ParsedRow(...),), time=<aware device time | None>, error=<pattern>)`.
  Return only parser-specific columns (+ optional `time`, `extra`); `Parser.parse` adds
  `time`, `time_received`, `source`, `sender`, `raw_day`, `raw_line`, `raw_offset`, `extra` and
  a parser can never override the raw reference. An exception becomes error
  `<name>.exception` with the generic view of the line.
- Derive all text from `decode_message(bytes)` (UTF-8 with replacement, NUL → U+FFFD).
  `messages.text` ≤ 2,000 characters; `fg_web.url` ≤ 2,000 (`base.URL_MAX`). Device times
  without an offset are not guessed (`extra["device_time"]`). Header helpers:
  `parsers/syslog.py`.
- **Tests that must keep passing:** `ingest/tests/test_parser_contract.py` (declared tables exist
  with raw-reference columns; every outcome of the real fixtures, synthetic MikroTik lines,
  junk and fuzzed bytes passes `check_row(..., complete=True)`). Add your fixtures there or in
  your own tests and run the same check.
- **Ingest obligations (B2):** every spool line → one `messages` row; resume from
  `ingest_offsets` (byte offset + line number of complete lines, per source and day), updated
  **in the same transaction as the COPY** (`arcilogger_db.bulk.copy_rows`) so a crash never
  loads a line twice; run `check_row` before `COPY` (a failing row becomes a parse failure,
  never a stuck batch);
  aggregate failures with `INSERT … ON CONFLICT (source, day, pattern) DO UPDATE SET count =
  parse_failures.count + EXCLUDED.count`; keep `sources` up to date and raise the alert
  `new_source` (`dedup_key = "new_source:<name>"`) for `unknown-<ip>` senders.

## 5. 5651 lookup (B3)

`arcilogger_worker.lookup` — frozen dataclasses with `to_dict()` / `from_dict()` (plain JSON, UTC
ISO times, **no floats**):

| Type | Purpose |
|---|---|
| `LookupRequest(at, public_ip, public_port, protocol, dest_ip, dest_port, dest_domain, tolerance_seconds, internal_ip, mac)` | validated, normalised (IPs, MAC `aa:bb:…`, domain lower-case, `protocol` stored as the number: `"tcp"`/`"6"` → 6, comparable with `fg_traffic.proto`) |
| `RawRef(source, day, line, offset)` | `.spool_path(layout)`, `.vault_path(layout)` |
| `EvidenceRef(table, row_id, time, raw, summary)` | one supporting row; `to_dict()` is also the element format of `leases.evidence` |
| `LeaseInfo`, `DeviceInfo`, `PersonInfo` | chain steps, each with `confidence` and `evidence` |
| `AttributionChain` | internal IP → lease → device → person/department (`department_source` person\|vlan\|none, overall `confidence` = `weakest(...)`). **IP conflict** (two MACs held the IP at T, spec 9.1): the most likely chain carries the others in `alternatives`; it and every alternative have `confidence="low"` and the `ip_conflict` explanation (enforced for the main chain) — a report never silently names one person. The person step is the device's assignment **valid at T** (section 11; `PersonInfo.link` `history`\|`current`, `assigned_from`, `assigned_to`); `person_link_at_lookup_time` only for a device without any assignment history |
| `TrafficInfo`, `NatMatch(kind exact\|candidate, score 0–100, time_distance_ms, destination_match, traffic, chain)` | one match; `destination_match` ∈ `ip_port`, `ip`, `ip_other_port` (requested IP, another port), `domain`, `domain_other_port`, `domain_address` (only the address is known for the name, from another client's logs — possibly a shared CDN address), `domain_unverified` (exact mode, domain-only request: the NAT port identifies the session, no log ties it to the name), `port` (only a destination port was requested, and it matched), `none`, `not_requested`; outcomes stored before the detailed kinds use only the coarse ones |
| `LookupOutcome(request, mode exact\|candidates\|internal\|none, matches, double_nat, attribution, explanations, data_window, tolerance_seconds, generated_at, truncated)` | stored in `lookups.outcome` |
| `Explanation(key, text_tr, text_en, params)` | standard texts in `EXPLANATIONS` (`double_nat` = spec 12.4 wording); the engine's own keys in `arcilogger_worker.lookup.texts.ENGINE_EXPLANATIONS` |
| `LookupSettings.from_settings(get_settings(session))` | `double_nat` (no public IP configured), `exact_addresses()` (validated, canonical text as PostgreSQL returns INET), `max_session_seconds`, `lease_timeout_seconds(server)` (lease time × `lease.timeout_percent` / 100) |

Entry points (B3, implemented): `run_lookup(session, request, settings, *, layout=None, now=None)
-> LookupOutcome` (``layout`` lets the data window say whether a day missing from the database is
in the sealed vault; ``now`` for tests) and `attribute(session, ip, at) -> AttributionChain |
None`. Both only read; `run_lookup` switches JIT off for the caller's transaction (`SET LOCAL jit
= off`: compiling cost more than the bounded queries). The API stores the lookup and writes the
audit entry. Exact = `transip ∈ settings.exact_addresses()`.

- **Modes.** A request that names a destination (IP, port or domain) lists only sessions to that
  destination — in candidate **and** exact mode (sessions reusing the NAT port for a logged
  destination IP/port other than the requested one are only counted, `exact_other_destinations`
  / `exact_only_other_destinations`); when there is none the answer is `none` with
  `destination_not_found` — unrelated clients are never listed (data minimisation, spec 3.3); a
  request without destination lists the whole window on purpose
  (`candidates_without_destination`). In exact mode a domain-only request is never answered
  "no session" just because no DNS/web/app log names the domain: the sessions on the port are
  then listed as `domain_unverified` (`exact_domain_unverified`), or, when a session tied to the
  name exists, the others are counted neutrally (`exact_unverified_destinations`). A requested
  configured NAT address that the logs do not show as `transip` around T, while they show
  another address that is not in `nat.public_ips`, is answered as double NAT
  (`public_ip_not_transip`); the FortiGate's other configured public addresses are left out of
  that search. A `nat.wan_transip` not seen then is not used to narrow a double-NAT search
  (`wan_transip_not_seen`). The spec 12.4 text `double_nat`
  ("… aşağıda … adaylar listelenmiştir") appears **only with candidates**; with nothing to list
  the key is `double_nat_no_candidates`.
- **Lease ids are stable** across rebuilds (a recomputed interval keeps the id of the stored row
  with the same ip, mac and start), so `LeaseInfo.lease_id` in a stored outcome and in audit
  entries keeps pointing at its row; an id disappears only when its interval disappears.
- **Settings read by the worker** are all in `DEFAULT_SETTINGS` (changed with
  `arcilogger-db settings set KEY JSON_VALUE --by NAME` until the M6 settings page; validated, audited as
  `settings.change`):
  `lookup.port_hint` (false), `lookup.dns_lookback_seconds` (3600), `devices.oui_file` (""),
  `arp.ignore_after_release_seconds` (300), `worker.leases_lag_seconds` (120); the API adds
  `lookup.timeout_seconds` (5) and `lookup.report_timestamp` (false), section 6.

**Session matching (spec 9.3, no `nat_index` table yet).** `fg_traffic.time` is the time of the
log line (session end or interim update). A row's session interval is `[time − duration s,
time]` (no `duration` → the instant `time`); it matches when that interval overlaps `[at −
tolerance, at + tolerance]`; `time_distance_ms` = 0 inside, else the distance to the nearer end.
Every query must bound the scan: `time BETWEEN at − tolerance AND at + tolerance +
lookup.max_session_seconds` (default 86400), so the `(transip, transport, time)` index range
stays small (an unbounded upper end scans every later chunk/partition). Interim rows count like
final rows. *Verify on site that FortiGate interim logs carry the cumulative `duration`.*

## 6. API (B5 and later packages)

- **App:** `create_app(config=None, engine=None)`. Endpoints go into
  `api/src/arcilogger_api/routers/<module>.py` (`router = APIRouter(prefix=…, tags=[…])`); every
  module there is mounted under `/api/v1` automatically — never edit `app.py`.
- **Database:** `db: DbSession` (`arcilogger_api.deps`) = one session per request, committed
  before the response is sent, rolled back on error. Own transaction (e.g. auditing a failed
  login that is then refused): `factory: SessionFactoryDep`.
- **Authentication:** `user: CurrentUserDep` (session + MFA passed);
  `PendingMfaUserDep` only for the TOTP endpoints. Requests the web app makes on its own
  (30-second dashboard refresh, polling, TanStack Query `refetchInterval`) must send
  **`X-Arci-Background: 1`**: they are authenticated but do not reset the idle timer; permissions:
  `user: Annotated[CurrentUser, Depends(require("lookup.run"))]` (403 `forbidden`; 403
  `password_change_required` while `must_change_password`). `client_ip(request)`.
  **Activity is committed before the endpoint runs:** authentication updates
  `user_sessions.last_seen_at` and commits the request's session at once (on the same
  connection — a request never holds two pooled connections); the endpoint's queries then start
  a new transaction. A dependency that runs before authentication must therefore not write to
  the request's session.
- **KVKK notice (mandatory on person-level endpoints):** every endpoint that returns
  person-level data (search, live tail, 5651 lookups and their report, raw lines, person-level
  usage) uses `Depends(require_privacy_notice("lookup.run"))` (`arcilogger_api.auth.privacy`;
  `recent=True` combines it with a recent re-authentication) instead of `require(...)`, or calls
  `ensure_privacy_notice(db, user)` in its own dependency. Permissions are checked first (403
  `forbidden`), then the notice (403 **`privacy_notice_required`** until the user accepted the
  current `privacy.notice_version` with `POST /auth/privacy-notice`).
- **Recent re-authentication** (spec 13: credentials, retention, evidence download):
  `Depends(require_recent_auth("evidence.download"))`, or `ensure_recent_auth(user)` where only
  some cases need it; 403 **`reauth_required`** unless the password (+ TOTP) was confirmed within
  5 minutes (`POST /auth/reauth`, or a complete sign-in).
- **Cookies and headers:** `arci_session` (HttpOnly, SameSite=Strict, Secure) holds a random
  token, the database only its SHA-256 (`user_sessions.token_hash`); `arci_csrf` (readable by
  JS) + header `X-CSRF-Token` on every POST/PUT/PATCH/DELETE under `/api/`; for a signed-in user
  `sha256(header)` must equal `user_sessions.csrf_token_hash`. The API hands out an `arci_csrf`
  cookie to clients without one. Session ends: revoked, idle > `auth.idle_minutes`, older than
  `auth.absolute_hours` or `expires_absolute`. Helpers for B5: `create_session(db, user,
  mfa_passed=…, ip=…, user_agent=…) -> IssuedSession(token, csrf_token, row)`,
  `set_session_cookies(response, issued, secure=config.api.secure_cookies)`, `hash_token`,
  `new_token`, `tokens_equal`.
- **Roles → permissions** (`security.ROLE_PERMISSIONS`, spec 13 table):

| Permission | admin | auditor | hr | viewer | Spec column |
|---|---|---|---|---|---|
| `dashboard.view` | ✔ | ✔ | — | ✔ | Panel |
| `dashboard.usage` | ✔ | ✔ | ✔ | ✔ | Panel (hr: "usage only") |
| `search.read` | ✔ | ✔ | — | — | Arama (auditor: "read") |
| `search.export` | ✔ | — | — | — | Arama |
| `lookup.run` | ✔ | ✔ | — | — | 5651 Sorgu (incl. PDF report) |
| `usage.person` | ✔ | — | ✔ | — | Kişi raporları |
| `usage.anonymous` | ✔ | ✔ | ✔ | ✔ | Anonim raporlar |
| `seal.read`, `seal.verify` | ✔ | ✔ | — | — | Mühür/Kanıt (auditor: "verify/download") |
| `seal.manage` | ✔ | — | — | — | Mühür/Kanıt ("Şimdi mühürle") |
| `evidence.read`, `evidence.download` | ✔ | ✔ | — | — | Mühür/Kanıt |
| `evidence.create` | ✔ | — | — | — | Mühür/Kanıt (auditor may not create) |
| `settings.manage` | ✔ | — | — | — | Ayarlar |
| `users.manage` | ✔ | — | — | — | Kullanıcılar |
| `audit.read` | ✔ | ✔ | — | — | Denetim |
| `sources.*`, `devices.*`, `alerts.*`, `reports.manage`, `compliance.read`, `system.read` | ✔ | — | — | — | not in the table: admin only until the owner decides |

  TOTP is mandatory for `auth.totp_required_roles` (admin, auditor).
- **Errors:** `raise ApiError(status, code, message_tr=None, message_en=None)`; every error
  response is `{"error": {"code", "message_tr", "message_en"}}` (422 adds `details` without the
  submitted values; 503 `unavailable` when the database is down; 500 reveals nothing). Standard
  codes and texts: `errors.MESSAGES`.
- **Audit:** `record_audit(db, actor=user, action="lookup.run", target="lookup:17",
  details={...}, ip=client_ip(request))` in the request's transaction, right before it ends
  (the chain lock is held until commit). The transaction must be **READ COMMITTED** (the
  default; anything else raises `RuntimeError`, and `prev_hash` is unique, so the chain cannot
  fork). Canonical entry `{"v":1, "id", "time" (UTC, microseconds), "actor_id", "actor",
  "action", "target", "details", "ip"}`; `hash = sha256(prev_hash || RFC 8785 JSON)`, genesis
  `"0"*64`; `details` must not contain floats or NUL (ValueError). Settings changes:
  `details={"old": old, "new": new}` (always canonical, see section 3). After commit — once the
  session has returned its connection to the pool (`after_transaction_end`), so the flush never
  holds a second pooled connection next to the request's (a pool of one connection works) — the
  entry is appended to `spool/_audit/<day>.jsonl` (the day it is written; sealed nightly); a
  savepoint commit or rollback neither flushes nor drops pending entries. The API also
  flushes at start-up and every minute, so a failed write is caught up without a new entry, and
  an incomplete last line from an interrupted write is cut off before the next append.
  `verify_audit_chain(session)` for the "Denetim Kaydı" page, `verify_audit_lines(lines)` for
  spool/vault files. Action names: `<area>.<verb>` (`auth.login`, `auth.login_failed`,
  `user.create`, `settings.change`, `lookup.run`, `evidence.create`, `evidence.download`,
  `usage.person.view`, `seal.manual`, …).
- **One audit writer (W3-I):** the chain step has a single implementation,
  `arcilogger_db.audit.append_entry(session, actor_id=, actor=, action=, target=, details=,
  ip=)` — canonical entry, hash, `pg_advisory_xact_lock`, READ COMMITTED check, `jsonable`
  details (times, dates, IPs, UUIDs, paths, sets), NUL/float refusal, an address that is not
  an IP stored as `null`. The spool projection (`flush_audit_spool`, `repair_torn_tail`) and the
  verification (`verify_audit_chain`, `verify_audit_lines`) live there too (the spool path comes
  from `arcilogger_core.layout`). `arcilogger_api.audit` keeps its public API unchanged:
  `record_audit` (signed-in user, the request's session, after-commit flush) calls
  `append_entry`, and every other name is re-exported. The worker (which may not import the API
  and may not write `spool/_audit`, ADR-0007) writes with `arcilogger_db.audit.append_audit(session,
  action=…)` (actor `system`); the API's flush copies those entries into the spool with its own,
  so they are sealed the same night. Tested: entries of both writers interleave into one gap-free
  chain, in the database and in the spool (`api/tests/test_evidence_audit_writer.py`).
- **Scheduled jobs in the audit log:** the database-free core jobs (sealing, retention) cannot
  write the chain; the worker's `audit.import` (every 15 minutes, `arcilogger_worker.
  evidence_jobs`) copies their results into it as `seal.run` / `retention.run` entries, each event
  once (docs/KANIT-PAKETI.md), and every credential change made with `arcilogger-core secret
  set|delete` (`state\seal\secret-changes.jsonl`: name, action, operator, time — never a value) as
  `settings.secret_change`. A retention run writes its deletion record and token as
  `<date>-imha.json.part` / `….part.zd` and renames them only after the token verified
  (`arcilogger_core.retention`), so only complete records carry the final name; a failed or
  unverifiable stamp removes them and raises `R20-retention` (imported as `refused`); a run
  interrupted after the rename is finished by the next run and is `incomplete` in the audit log
  until then. Per deleted day the data files go first, then the chain anchor moves to that day,
  then its manifest and token: a day that could not be deleted at all keeps the anchor on the
  day before (the remaining chain verifies), a day stopped in the middle is reported incomplete
  until the next run. `R20-retention` messages are Turkish with the English in brackets; the
  facts (`stage`, `provider`, `retryable`, `day`, `record`, `error`) are machine-readable.
- **Router ownership:** `auth`, `users`, `audit` → B5; `lookup`, `lookups`, `raw` → W2-A
  (below); `evidence`, `seals`, `credits` → W2/W3 (implemented, docs/KANIT-PAKETI.md);
  `devices`, `persons`, `departments`, `vlans`, `imports` → devices and persons (section 11);
  `license` → offline licensing (section 15); `sources` → a later package (still an empty router).

### 6.1 5651 lookup, official report and raw lines (W2-A)

All need a session, the KVKK notice and `lookup.run` (raw lines: `search.read` **or**
`lookup.run`); today that is admin and auditor. Errors use the standard format.

| Endpoint | Does | Audit (`details`) |
|---|---|---|
| `POST /lookup/5651` | body `{"request": {authority, file_no, request_date, reason}, "query": {at, time_zone?, public_ip?, public_port?, protocol?, dest_ip?, dest_port?, dest_domain?, tolerance_seconds?, internal_ip?, mac?}}` → 201 `LookupOut` (`id`, `request`, `query`, `mode`, `result_count`, `outcome` = `LookupOutcome.to_dict()`, `report`, `elapsed_ms`) | `lookup.run` (`lookup_id`, `request` {authority, file_no, request_date, `reason_sha256`}, `query`, `mode`, `result_count`, `truncated`, `results` [{kind, score, traffic row ids, lease id, person id, alternatives}] ≤ 100) |
| `GET /lookup/{id}` | one lookup (`LookupOut`, `report` = stored report facts or `null`) | `lookup.view` (`lookup_id`, `own`) |
| `GET /lookups` | newest first, `cursor` (= `next_cursor`), `limit` ≤ 200, `mine`, `username`, `mode`, `from`/`to`; items without outcome (`report_sha256` when a report exists); `all_users` | not audited (no person-level data) |
| `GET /lookup/{id}/report.pdf` | the official report; generated and stored on the first call, the **same bytes** on every call; headers `Content-Disposition: attachment`, `X-Arci-Sha256` | first call `lookup.report` (`sha256`, `size`, `pages`, `version`, `file`, `timestamp` {status, provider, legal_grade, serial, gen_time, verified, error}); every call `lookup.report_download` (`file: "pdf"`, `sha256`, `download` n) |
| `GET /lookup/{id}/report.pdf.zd` | the RFC 3161 token of the report's hash (404 unless stamped) | `lookup.report_download` (`file: "zd"`) |
| `GET /raw?source=&day=&line=[&offset=]` | one raw line, byte-exact (`raw_base64`), `text` (display: spool `#ooo` escapes visible, `\xNN`, `\uXXXX` for control/bidi), `sha256`, `offset`, `origin` spool\|vault, `file`, `received`, `sender` | `raw.view` (reference, `origin`, line `sha256` — never the content) |

- **Request details** are required and never blank (trimmed; no control/format characters;
  `reason` may contain line breaks; `request_date` not in the future); 422 otherwise, before the
  lookup runs. **`at`** is ISO-8601: with an offset (`+03:00`, `Z`) it is used as given, without
  one it is local time in `time_zone` (IANA, default **Europe/Istanbul**); `query.time_input`
  (`text`, `offset_given`, `time_zone`) records how it was interpreted and the report prints it.
  A local time that occurs twice (clocks back) or does not exist (clocks forward) in `time_zone`
  is refused with 422 (`loc` `["body", "query", "at"]`): the request must then give the UTC
  offset (Europe/Istanbul has no clock changes; this concerns other zones).
  `lookups.query` = `LookupRequest.to_dict()` + `time_input` (`from_dict` ignores the extra key).
- **Numbers:** `{id}` and `cursor` are 1 … 2^63−1 (PostgreSQL `bigint`) in every router (also
  `/users/{id}` and the `/audit` cursor); larger values answer 422 `validation_error`, never a
  database error.
- **Time budget (spec 14):** setting `lookup.timeout_seconds` (default 5). Every statement of the
  lookup gets the **remaining** time as `statement_timeout` (`set_config(…, true)` on the
  cursor before each statement, `routers.lookup.statement_deadline`); past it → 504
  **`lookup_timeout`**, the attempt is audited as `lookup.run_failed` (`reason: timeout`) and no
  `lookups` row is stored. 503 `lookup_settings_invalid` when the NAT settings are unusable.
- **Visibility (spec 13 + KVKK):** administrators see every lookup; every other role with
  `lookup.run` (the auditor) only its own — results are shown only to whoever entered a reason
  (spec 9.5). Another user's lookup answers **404** (not 403). The rule also covers evidence
  packages, which carry a lookup's report: `GET /evidence` leaves out packages of other users'
  lookups (also those whose lookup was deleted), `GET /evidence/{id}` and `/download` answer
  404 for them, and `POST /evidence` refuses a lookup the requester may not see
  (`routers.evidence.visible_packages`). Owner decision (ASSUMPTIONS D5); should it ever change,
  a `lookup.read_all` permission would replace the role check (`routers.lookup.sees_all_lookups`)
  for both.
- **Report storage:** table **`lookup_reports`** (revision `0006_lookup_reports`): one row per
  lookup — `path` (relative to `exports/`, `reports/ARCILOGGER-5651-rapor-<id>.pdf`), `sha256`,
  `size`, `pages`, `version`, `inputs` (everything the PDF was rendered from; rendering them
  again gives the same bytes), `timestamp_status` `not_requested`\|`stamped`\|`failed`,
  `timestamp` (provider, legal_grade, serial, gen_time, tsa_name, token_file, token_sha256,
  verified, errors | error), `download_count`. A missing or changed file answers 409
  `report_missing` / `report_damaged` and is **never rendered again** (the handed-out hash must
  stay valid). **Nothing is stored** (the next request tries again) when the report cannot be
  made now: a cited file (raw file, manifest, token) exists but cannot be read — 503
  **`report_access_denied`** (the API service has no right on it: Windows folder permissions)
  or 503 **`report_evidence_unreadable`** (other read error), a cited day is being sealed
  right now and its state did not settle over three collection passes — 503
  **`report_seal_in_progress`** — or the layout fails, 500 **`report_render_failed`**. A day
  sealed while the report is collected is read again as sealed, never stored as a lost seal.
  Damage that is a property of the evidence (damaged gzip data, a file changed after sealing, a
  lost seal) is recorded in the report instead. Every cited raw line is re-parsed with the ingest
  parser and compared with the values the report shows (`inputs.evidence[].check`
  `{ok, mismatches: [{field, report, raw}], error}`, `inputs.consistency`
  `{checked, ok, contradicted}`); a contradicted result is printed as such (docs/RAPOR.md). With
  `lookup.report_timestamp` the PDF's hash is stamped with the configured
  `[tsa]` provider (1 kontör with Kamu SM); the PDF states the provider and, when it is not the
  Kamu SM production server, that the stamp is not legally valid.
- **Seal states in the report** (`inputs.files[].status`, `inputs.days[].status`): `sealed`
  (manifest and token in the vault; verified again with `verify_day` and the chain start of
  `python -m arcilogger_core verify`: the oldest sealed day, none after retention left an
  anchor); `unsealed` (raw file still in the spool: "henüz mühürlenmedi", with the spool hash at
  report time; `late` when the day ended two or more days before the report); `seal_missing`
  (raw file in the vault but manifest and/or token missing: "MÜHÜR BULUNAMADI", in red — never
  shown as "not sealed yet"). The double-NAT paragraph says "the result is a candidate list"
  only in mode `candidates`; in mode `none` it says that no candidate was found either. Raw lines
  longer than 72 KiB (`texts.RAW_DISPLAY_CHARS`) are cut in the PDF with a warning; their hash
  and length describe the whole line. Long values (reason, raw lines) split across pages.
- **For the evidence package (9.6):** `arcilogger_api.reports.read_stored(layout, row)` returns
  the stored PDF checked against its SHA-256 (`ReportError` otherwise); `read_token(layout,
  row)` the `.zd`. Do not render a new PDF for a package.
- **The report in the evidence package (W3-I):** `POST /evidence` with `lookup_id` makes sure
  the lookup's report is stored — the stored one, or rendered once now through
  `reports.load_or_create` (audited `lookup.report`, actor = the package's creator, exactly as
  on a first download, and **committed before** the request takes the evidence lock and checks
  the limits, so a slow rendering or time stamp never holds up other users' requests; a request
  refused by a limit afterwards leaves the stored report, as a download would); a stored file
  that is missing or changed refuses the request with the report's own error (409
  `report_missing` / `report_damaged`; 503 `report_access_denied` …,
  `routers.lookup.report_error`) and nothing is queued. The build reads the stored bytes again
  (`EvidenceBuilder._report_bytes`: same row, same SHA-256, else the build `failed`) and passes
  them to `arcilogger_core.evidence.build_package(report_pdf=, report_name=, report_token=,
  report_note=)`: the ZIP holds `rapor/ARCILOGGER-5651-rapor-<id>.pdf` and its `….pdf.zd` only
  when that token verified when the report was stored (`lookup_reports.timestamp.verified`)
  **and** still verifies against the roots the package carries. Otherwise (stamp `failed`, did
  not verify, roots changed) the report goes in without its token and `paket.json`
  `report_note` (bilingual), the guides and the verifier (warning `report-token-omitted`, exit
  code 0) say why — the stored report is never rendered again, so a bad token must not block
  every package of the lookup. `paket.json` has `report`, `report_token` and `report_note` and
  lists the files with size and SHA-256. The report's SHA-256 is the same in the package,
  `lookup_reports.sha256`, the `X-Arci-Sha256` header and the audit entries `lookup.report`,
  `evidence.request` and `evidence.create` (`details.report` = `{lookup_id, file, sha256, size,
  token_file, token_sha256, token_note}`; `evidence.create` describes the package as built).
  The standalone verifier (version 3) checks the PDF against `paket.json`, prints its SHA-256
  for comparison with the lookup record, verifies the `.zd` against the PDF and the package's
  roots (`report-ok`, or failures), and fails when `paket.json`'s `report` / `report_token`
  name a file that is missing (`report-missing`, `report-token-missing`) or a `rapor/` file is
  not the one they name (`report-unlisted`, `report-token-unlisted`, `report-token-orphan`).
- **The audit log in the evidence package (decision 1):** `POST /evidence` takes
  `include_audit_log` (default `false`) and passes it to `build_package(include_audit_log=)`
  through `BuildOrder`; the days' `audit.jsonl.gz` is otherwise left out (`paket.json` lists it as
  excluded). Only the administrator role may set it (`routers.evidence.may_include_audit_log`;
  403 `audit_log_admin_only` before any work, even for a role that is one day granted
  `evidence.create`). It is stored in `evidence_packages.scope.include_audit_log` (shown as
  `scope.include_audit_log`, `false` for older rows), audited in `evidence.request` and
  `evidence.create`, and the size estimate counts the sealed audit files only when they go in.
  Such a package holds the stored entries (real usage targets, hashes): `visible_packages` hides
  it, like a package of another user's lookup, from a reader who sees usage pseudonyms
  (`routers.usage.sees_pseudonyms`).
- **Raw lines:** `arcilogger_api.rawlines` (`read_line`, `scan_file`, `display_text`); the source
  must match `[a-z0-9][a-z0-9.-]{0,80}` and be configured (also `<network source>-<ip>`) or in
  `sources`; the file must lie inside `spool/` or `vault/`. The line number is always verified
  by counting line feeds (spool files: from the nearest line-start checkpoint of the in-memory
  `SPOOL_INDEX`; sealed `.log.gz`: from the nearest decompressor checkpoint of `VAULT_INDEX`,
  about every 64 MiB — so only the first view in a file reads up to the line; at most two sealed
  files are decompressed at the same time); a given `offset` must match (409
  `raw_reference_mismatch`). Codes: 422 `raw_source_invalid`, 404 `raw_source_unknown`, 404
  `raw_not_found`, 409 `raw_unreadable` (damaged compressed data before the line), 503
  **`raw_access_denied`** (the API service has no read right on the file — the file is fine; on
  Windows the installer grants `NT SERVICE\ArciApi` read on `spool`), 503 `raw_io_error`.
- **Test helpers** (test support only, never imported by the service):
  `arcilogger_api.lookup_testing` (`build_site`, `accept_privacy`, `seal_day` with the mock TSA)
  and `arcilogger_api.pdftext.pdf_text` (text of the PDFs ARCILOGGER renders, through their
  `ToUnicode` maps).

## 7. Tests

| Variable | Meaning |
|---|---|
| `ARCILOGGER_TEST_PG` | Admin DSN of a throwaway server, e.g. `postgresql://arci_test:arci_test@127.0.0.1:5432/postgres` (role needs `CREATEDB`) |
| `ARCILOGGER_REQUIRE_DB=1` | Missing/unusable database → **fail** instead of skip (CI) |
| `ARCILOGGER_TEST_TIMESCALE` | `auto` (default), `require`, `off` for the template database |
| `ARCILOGGER_EXPECT_TIMESCALE=1` | the TimescaleDB test fails unless hypertables were created (CI Linux) |

Fixtures (root `conftest.py` loads `arcilogger_db.pytest_plugin` for every package):
`pg_server` (session), `pg_template` (session: migrated + seeded, plus the fictional example
site of `arcilogger_db.testing.seed_example_network`), `pg_dsn` / `db_engine` /
`db_session_factory` / `db_session` (**a fresh database per test**, copied from the template in
about 0.1-0.3 s; commit freely). On plain PostgreSQL the template has only the current month's
partitions (fewer relations, faster copies); rows of other months go to the default partition,
which behaves the same for tests. API (`api/tests/conftest.py`): `api_config`, `layout`, `app`,
`app_session_factory`, `client`, `make_user(role, password=None, **columns)`,
`login_as(client, role, mfa_passed=True, **columns) -> LoggedIn` (user + valid session row,
cookies and CSRF header set on that client). Outside `api/tests`, use
`arcilogger_api.testing.make_user` / `login`. Names of test databases are unique per host,
process and random suffix; several agents can share one server.

Coverage gates (all run in CI): `uv run poe test-core` (≥ 90 %), `test-ingest` (≥ 85 %) and
`test-api` (≥ 80 %).

## 8. Verify on site / open questions

- MikroTik wire format (the parser contract test uses **synthetic** wire lines built from the
  `/log print` sample).
- psycopg is LGPL-3.0 (ADR-0005): **decided** — accepted for internal use (ASSUMPTIONS D3); if a
  later legal review refuses it, switch `arcilogger_db.bulk` + the engine driver to pg8000
  (nothing else imports it).
- No departments or VLANs are seeded (owner decision 2026-10-20, ASSUMPTIONS P4); the
  administrator links departments to VLANs. A VLAN without a linked department (e.g. a staff
  Wi-Fi; in the tests' example plan VLAN 60) uses its VLAN name as department through
  `arcilogger_db.network`.
- Auditor and evidence packages: **decided 2026-10-12** — the auditor has no `evidence.create`
  (roles.md, ASSUMPTIONS D4); the web app shows the "Kanıt Paketi" button only with
  `evidence.create`.
- `lookup.max_session_seconds` (86400) bounds the session scan: verify against the FortiGate's
  session TTLs and interim-log settings on site.
- Audit entries for scheduled sealing and retention runs (spec 8.5): imported by the worker's
  `audit.import` every 15 minutes (section 6), so they reach the audit log up to 15 minutes
  after the run — acceptable? (owner decision; the sealed manifests and deletion records are the
  evidence either way).
- A network without a VLAN id (e.g. the transit network between the core router and the
  FortiGate) is stored in `vlans` with `vlan_id` NULL (the table is keyed by subnet for that
  reason).
- TimescaleDB on the Windows server (installer: `shared_preload_libraries = 'timescaledb'`
  **before the first `arcilogger-db upgrade`**, `timescaledb.telemetry_level = off`, enough
  background workers for 15 compression jobs — `timescaledb.max_background_workers` ≥ 16 and
  `max_worker_processes` per the TimescaleDB documentation; verify). Without TimescaleDB the
  native partitions need no server setting.

## 9. Database backup (area B, ADR-0009)

Commands (`arcilogger_db.backup`, runbook [YEDEKLEME.md](YEDEKLEME.md)):
`arcilogger-db backup run | list [--json] | verify [FILE …] [--all] [--deep] | prune [--dry-run]
| restore --file F --target-db NAME [--superuser NAME] [--sha256 HEX]`. The task
`\ARCILOGGER\Backup` runs `python -m arcilogger_db backup run` daily at 01:40. Exit codes as for
`arcilogger-db` (1 = the backup, check or prune failed — `run` also when the dump was made but
its retention failed or `last.json` could not be written; `restore`: 2 = refused before any
change, among them a SHA-256 that differs from the sidecar or from `--sha256`, 3 = server
unreachable). `restore` creates the new database with `TEMPLATE template0 ENCODING 'UTF8'
LOCALE 'C'` (as the installer) and runs `pg_restore --exit-on-error` (one transaction per
statement, not `--single-transaction`: TimescaleDB's chunks outgrow the lock table).

`[backup]` (`arcilogger_core.config.BackupConfig`): `enabled` true, `dir` backups/scheduled
(relative to `data_dir`), `keep_daily` 14, `keep_weekly` 8, `keep_monthly` 12, `pg_dump` and
`pg_restore` "" (found automatically), `min_free_gb` 10.

**State file** (written by `backup run`, read by the alert checks):
`<data_dir>/state/backup/last.json` (`arcilogger_db.backup.last_state_path(layout)`), written
atomically after every attempt — not when `[backup] enabled = false`:

```json
{"schema": 1, "finished": "2026-10-19T01:41:07+03:00", "ok": true,
 "file": "backups/scheduled/arcilogger-20261019-014002.dump", "size": 123456,
 "sha256": "<64 hex>", "error": null}
```

`finished` is site time with its offset; `file` is relative to `data_dir` with `/` (absolute when
`[backup] dir` lies outside it). On a failure `ok` is false, `file`, `size` and `sha256` are null
and `error` is a short English text without secrets; the attempt also appends `R20-backup`
(`high`, details `error`, `database`, `started`) to `state/alerts.jsonl` through
`arcilogger_core.alerts.raise_alert`. A good dump whose retention fails keeps `ok` true and appends
`R20-backup` (`medium`, details `key` "prune", `error`, `dir`: dedup key `R20-backup:prune`, apart
from the backup state). A dump made whose `last.json` could not be written appends `R20-backup`
(`high`, no key), resolved only by a backup that succeeds after it; one named earlier than a dump
already in the folder (a clock set back; it is kept all the same) appends `R20-backup` (`medium`,
`key` "clock": `R20-backup:clock`), resolved by a later successful backup.

**Sidecar** `<dump>.json` (schema 1): `file`, `sha256`, `size`, `started`, `finished`, `database`,
`format` ("custom"), `pg_dump_version`, `server_version`, `alembic_revision`,
`timescaledb_version` (or null), `toc_entries`, `arcilogger_db_version`.

## 10. Alerts and alert e-mail (group 1, area E)

Decision: ADR-0010; operator view: docs/ALERTS.md (TR: docs/UYARILAR.md).

- **Raising.** Code without the database raises with
  `arcilogger_core.alerts.raise_alert(layout.alerts, rule, severity, message, details)` — the
  signature and the JSONL format of `state/alerts.jsonl` are unchanged. Rule ids: `R15` (log
  source silent), `R20-<check>` (system: `R20-seal`, `R20-seal-config`, `R20-clock`,
  `R20-backup`, `R20-disk`, `R20-credit`, `R20-replica`, `R20-verify`, `R20-parse`, `R20-job`,
  `R20-email`, …). The import derives the dedup key from `details`
  (`arcilogger_worker.alerts.importer.import_key`): `R20-seal` by `day`, `R20-seal-config` by
  `cause`; every other rule by a `key` (`<rule>:<key>`), and without one `R20-clock`,
  `R20-credit`, `R20-verify`, `R20-replica` and `R20-backup` keep one active alert each,
  `new-log-source` and `wrong-transport` one per `address`. Every other line is an alert of its
  own (`source`, or `day` outside `R20-seal`, does not group lines): put a `key` into `details`
  when repeats of a problem should count on one alert. `raise_alert` starts on a fresh line when
  the file does not end with a newline (a torn tail after a power cut). Code with the database
  writes through `arcilogger_worker.alerts.store.open_alert` / `resolve` (one active alert per
  dedup key).
- **Worker jobs.** `alerts.import` (1 min), `alerts.r15` (1 min), `alerts.system` (5 min),
  `alerts.notify` (1 min); see docs/CORRELATION.md section 4. Commands:
  `arcilogger-worker alerts test-email --to ADDRESS` and `arcilogger-worker alerts list [--open]
  [--limit N]` (exit codes as `run`: 0 ok, 1 not sent, 2 configuration, 3 database unreachable).
- **Tables** (revision `0009_alert_delivery`): `alerts.created_at`, `alerts.resolved_at`;
  `alert_deliveries` (`alert_id`, `kind` new / resolved, `recipient`, `status` queued / sent /
  failed / suppressed, `attempts`, `last_error`, `next_attempt_at`, `message_id`, `created_at`,
  `sent_at`; unique `(alert_id, kind, recipient)`).
- **Configuration** (`arcilogger.toml`):

  | Section | Keys (defaults) |
  |---|---|
  | `[smtp]` | `enabled` false, `host`, `port` 587, `security` starttls\|ssl\|none, `username`, `from_address`, `timeout_seconds` 30, `ca_file` (optional; the system trust store otherwise) |

  The password is only in the secret store: `arcilogger-core secret set smtp.password`
  (`arcilogger_core.secretstore.SMTP_PASSWORD`). Certificate verification is always on for
  `starttls` and `ssl`.
- **Settings** (`DEFAULT_SETTINGS`, validated with ranges): `alerts.email_recipients`,
  `alerts.email_min_severity`, `alerts.email_max_per_hour`, `alerts.email_digest_seconds`,
  `alerts.email_resolved`, `alerts.web_base_url`, `alerts.r15_startup_grace_minutes`,
  `alerts.r15_never_seen_minutes`, `alerts.disk_used_percent`, `alerts.seal_overdue_hour`,
  `alerts.replica_stale_hours`, `alerts.parse_failure_percent`, `alerts.parse_min_messages`,
  `alerts.job_failures`, `alerts.backup_stale_hours`, `alerts.backup_missing_hours`. Internal:
  `worker.alerts_import` (the import offset).
- **Database backup state** (written by area B): `<data_dir>/state/backup/last.json`
  `{"schema": 1, "finished", "ok", "file", "size", "sha256", "error"}` is read by `R20-backup`
  only; it is optional (missing: medium `R20-backup:missing` once the installation is older than
  `alerts.backup_missing_hours`).
- **E-mail content.** Built only from `arcilogger_worker.alerts.texts` (catalogue + whitelisted,
  shape-checked facts); never an alert's title or details. A new rule that should say more in an
  e-mail adds a describer there, with a test that its e-mail carries no log content.

## 11. Devices, persons and assignment history (spec 9.2, ADR-0008)

**Table** `device_assignments` (revision `0008_device_assignments`): `id`, `mac` (FK `devices`,
RESTRICT), `person_id` (FK `persons`, RESTRICT, NOT NULL), `valid_from`, `valid_to` (NULL = the
current assignment), `assigned_by` (user name; `:migration` for the backfill), `note`,
`backfilled`, `created_at`. At most one open row per MAC (partial unique index
`uq_device_assignments_open_mac`) and no overlapping intervals per MAC (exclusion constraint
`ex_device_assignments_no_overlap` with `btree_gist`, or a trigger with the same name and SQLSTATE
`23P01` where the extension cannot be created). `devices.person_id` is the denormalised current
owner. **Only `arcilogger_db.assignments` writes either**: `owners = lock_devices(session,
macs)` (row locks in MAC order) → `Planner(load_history(session, macs), now=…, owners=owners)
.assign(mac, person_id, valid_from, note=…)` → `apply(session, planner.changes,
assigned_by=…)`. `valid_from` not more than 2 minutes in the future (a start within those 2
minutes is stored as now, so it cannot hold back the next change) and not before the current
assignment's start (or the end of the last one) — the history is appended to, never rewritten;
the same owner again or unassigning a device without one changes nothing. The `note` is stored
on the row opened; an unassign opens none and refuses a note (`ValueError`). An owner in
`devices.person_id` without an open row (written outside the module) is replaced or removed
like any other. Read with
`load_history(session, macs)` + `covering(slots, at)`.

**Lookup:** the person of a chain is the row covering T (`valid_from <= T < coalesce(valid_to,
∞)`): `PersonInfo.link = "history"`, `assigned_from`, `assigned_to`. A device with history but no
row at T → no person (`person_unassigned_at_time`). A device without any history but with
`devices.person_id` → that owner, `link = "current"`, outcome explanation
`person_link_at_lookup_time` (kept only for this case). A backfilled row adds
`person_link_backfilled`; a history link with the person's department adds
`person_department_current` (the department is the person's current one — no department history).
The three fields have defaults (`None`, `None`, `"current"`): stored outcomes and report inputs
load unchanged. The report's person line prints `atama: <from> tarihinden itibaren` or
`atama: <from> - <to>` for history links only.

**API** (base `/api/v1`, group contract 4.3; errors in the standard format; times ISO-8601; MACs
`aa:bb:cc:dd:ee:ff`, accepted in any notation `normalize_mac` reads). `devices.read` for reads,
`devices.manage` for writes, plus `require_privacy_notice` wherever a person's name is returned
(devices, persons, imports); departments, VLANs and templates need no notice.

| Endpoint | Does | Audit (`details`) |
|---|---|---|
| `GET /devices?q=&unknown=&type=&person_id=&vlan=&randomized=&infrastructure=&sort=&order=&limit=&offset=` | `DevicePage` `{items: [DeviceOut], total, limit, offset}`; `q` = part of MAC (also without separators), host name, last IP, device name (Turkish letters folded, `arcilogger_api.textfold`); `unknown=true`: no owner and not infrastructure; `sort` `last_seen` (default) \| `first_seen` \| `mac` \| `last_ip` \| `name`, `order` `desc` (default) \| `asc`, empty values last; `limit` 1–500 (50) | — |
| `GET /devices/{mac}` | `DeviceDetail` = `DeviceOut` + `assignments` (newest first, ≤ 200) + `recent_leases` (`LeaseBrief`, newest first, ≤ 20) | — |
| `PATCH /devices/{mac}` | `name`, `type` (`DEVICE_TYPES` or null), `infrastructure`, `notes`, `tags` (≤ 20, duplicates dropped) → `DeviceOut` | `device.update` (`changed` fields; old/new of `type`, `infrastructure`) |
| `POST /devices/assign` | `AssignRequest {macs (1–500), person_id (null = unassign), valid_from?, note?}` → `AssignResult {updated, devices}`; all or nothing; 404 `device_not_found` (`details.macs`), 422 unknown person, 422 `validation_error` for a `note` with `person_id: null` (`loc` `["body", "note"]`: the note is kept on the assignment opened, an unassign opens none), 409 `person_inactive`, 409 `assignment_in_future` / `assignment_overlap` (`details`: `[{mac, reason, earliest}]`); a `valid_from` up to 2 minutes ahead is taken as now | `device.assign` (`person_id`, `valid_from`, `requested`, `updated`, `changes` `[{mac, from, to}]` — person ids) |
| `GET /devices/export.csv` | the list's filters and sort as CSV | `device.export` (`rows`, `filters` — `q` only as `true`, `sort`, `order`) |
| `GET /persons?q=&active=&department_id=&limit=&offset=` | `PersonPage` of `PersonOut` (by name; `q`: name, e-mail, employee number, title) | — |
| `POST /persons`, `PATCH /persons/{id}` | `PersonCreate` / `PersonUpdate` → `PersonOut` (201 on create); `employee_no` unique (409 `employee_no_taken`), e-mail format, existing `department_id` (422); empty strings are null; `usage_excluded` (default `false`; `null` refused like `name` and `active`) leaves the person out of person-level usage answers and reports (section 12), never out of a 5651 lookup; no DELETE (deactivate) | `person.create` (`person_id`, `department_id`, `active`, `usage_excluded`, `fields`), `person.update` (`changed`; old/new of `department_id`, `active`, `usage_excluded`) |
| `GET /persons/{id}` | `PersonDetail` = `PersonOut` + `devices` (held now) + `assignments` (newest first) | — |
| `GET /persons/export.csv` | CSV of the filtered persons, with `usage_excluded` as `evet`/`hayır` (imports again unchanged: its `id` column names each person, also one without `employee_no`) | `person.export` (`rows`, `filters`) |
| `GET /departments`, `POST /departments`, `PATCH /departments/{id}` | `DepartmentOut {id, name, notes, person_count, vlan_ids}`; names unique with Turkish letters folded (409 `department_exists`) | `department.create` (`department_id`, `fields` given), `department.update` (`department_id`, `changed` fields) — never the name or the notes |
| `GET /vlans` | `[VlanOut {subnet, vlan_id, name_tr, purpose, dhcp_server, department, guest, notes}]`, VLAN id order, networks without an id last; ARCILOGGER ships none (empty on a new installation) | — |
| `POST /vlans` | `VlanCreate {subnet, name_tr, vlan_id?, purpose?, dhcp_server?, department_id?, guest?, notes?}` → 201 `VlanOut`; `subnet` a network with its prefix length and the host bits zero (`10.0.20.0/24`; no prefix, host bits set — the error names the network meant —, an IPv6 zone id, an IPv4 network written as IPv6 `::ffff:10.0.20.0/120` — no IPv4 address falls into it, the error names the IPv4 network: 422), stored canonical; `vlan_id` 1–4094; `dhcp_server` (≤ 64, no spaces or colons, the name the router logs); unique subnet / VLAN id / DHCP server: 409 `vlan_exists` / `vlan_id_taken` / `dhcp_server_taken` (TR/EN texts name the network that holds it, also for a concurrent insert); unknown `department_id` 422; a subnet inside or around another one is allowed (an address belongs to the most specific, `arcilogger_db.network.find_vlan`) | `vlan.create` (`subnet`, `vlan_id`, `department_id`, `guest`, `fields` sent with a value) |
| `PATCH /vlans` | `VlanUpdate {subnet, new_subnet?, vlan_id?, name_tr?, purpose?, dhcp_server?, department_id?, guest?, notes?}` → `VlanOut`; `subnet` names the VLAN (404 unknown), only the other fields sent change (`{subnet, department_id}` changes only the department, as before), `null` clears an optional field (422 for `name_tr`, `guest`, `new_subnet`); `new_subnet` moves the VLAN to another network (name, department and flags stay; the same network in another notation is no change; an IPv4-mapped one is 422 as for `POST`, while `subnet` and `DELETE ?subnet=` still name a stored one); the same 409/422 as `POST` | `vlan.update` (`subnet` and `vlan_id` after the change, `changed` fields, `previous_subnet` / `previous_vlan_id` when changed, old/new `department_id`, `guest`); nothing changed: no entry |
| `DELETE /vlans?subnet=` | 204; 404 unknown, 422 not a network. Its devices lose the VLAN name and the VLAN's department; leases, devices, rollups and stored 5651 results keep their VLAN numbers and texts | `vlan.delete` (`subnet`, `vlan_id`, `department_id`, `guest`) |
| `POST /imports/{kind}?dry_run=true\|false` | multipart field `file`; `ImportResult {kind, dry_run, rows, created, updated, unchanged, errors: [ImportLineError {line, message_tr, message_en}], preview (≤ 50)}`; default dry run; an apply writes every line or — if any line has an error — none; ≤ 200 errors listed (+ one "… and n more") | applied imports only: `import.persons` / `import.assignments` (`file_sha256`, `size`, `rows`, `created`, `updated`, `unchanged`) |
| `GET /imports/{kind}/template.csv` | header + one example row | — |

Response schema names (OpenAPI, `web/src/generated/api-schemas.json`): `PersonRef`,
`DepartmentRef`, `DeviceOut`, `AssignmentOut`, `LeaseBrief`, `DeviceDetail`, `PersonOut`,
`PersonDetail`, `DepartmentOut`, `VlanOut`, `DevicePage`, `PersonPage`, `AssignResult`,
`ImportResult`, `ImportLineError`; requests `DeviceUpdate`, `AssignRequest`, `PersonCreate`,
`PersonUpdate`, `DepartmentCreate`, `DepartmentUpdate`, `VlanCreate`, `VlanUpdate` (all
`extra="forbid"`).
`DeviceOut.department` is the owner's department, else the department linked to the device's
VLAN (`last_vlan`, else the subnet of `last_ip`); `department_source` `person` \| `vlan`; both
null when neither exists (a VLAN without a linked department — the VLAN name is in `vlan_name`).

**VLAN changes reach every consumer like a department change** (nothing refers to a `vlans`
row; each consumer reads the table when it uses it): the device and department lists per
request; a 5651 lookup when it runs (the chain's `vlan`, `vlan_name`, VLAN department and the
`guest_vlan` note; stored lookups and reports keep what they showed); the usage pages and reports
join the VLAN's department by VLAN number when they are read, so a deleted VLAN's department
is gone from them at once. The VLAN number of an address is the `vlans` row's holding it (most
specific), else, in the hours with a lease, the lease's: lease rows keep the VLAN number they were
built with (the DHCP record's, else the DHCP server's name by `dhcp.server_vlan_regex`, else the
subnet's at that time). A change of that *number* (a renumbered VLAN, a new one or one with a new
network; for the addresses leaving a VLAN's network — a deleted or moved VLAN — only the hours
without a lease, the leased hours keep the lease's number) changes the attribution fingerprint of
the day, so `usage.reattribute` rebuilds the last `usage.auto_rebuild_days` days by itself; older
days need `arcilogger-worker usage rebuild`. Raw logs are never touched.

**CSV** (`arcilogger_api.csvfiles`): written UTF-8 with BOM, `;`, CRLF; cells starting with
`= + - @` TAB CR get a leading `'` (removed again on import); times in site time with offset;
booleans `evet`/`hayır`. Read: UTF-8 only (BOM optional; another encoding answers 422
`csv_encoding`), `;` or `,` (detected from the header), ≤ 5 MB (413 `csv_too_large`, also while
the body streams in) and ≤ 10,000 rows (422 `csv_too_many_rows`), header names case-insensitive;
a missing required, repeated or unknown column is 422 `csv_header`; no file in the field `file`
is 422 `upload_invalid` (multipart is read by `arcilogger_api.uploads`, no extra dependency).
Columns and matching rules per kind: `arcilogger_api.csvimport` (persons: by the export's `id`
column — an unknown id, an id whose line carries another person's `employee_no` and a person on
two lines are line errors —, else by `employee_no`, else create; the optional `usage_excluded`
column is read like `active`, an empty cell keeps the value; assignments: person by
`employee_no`, else by exact unique name, an apply locks those persons `FOR SHARE` in id order
before the devices, like `POST /devices/assign`, and checks `active` again; the device must
exist; `valid_from` ISO-8601 or `dd.mm.yyyy[ HH:MM[:SS]]`, site time without offset). The
expected columns named in a `csv_header` error are the template's (`id` is the export's).

Test support: `arcilogger_api.directory_testing` (`add_device`, `add_person`, `department_id`).

*Verify on site:* that the Windows PostgreSQL 16 build on the server ships the contrib module
`btree_gist` (`SELECT * FROM pg_available_extensions WHERE name = 'btree_gist'`); without it the
migration installs the equivalent trigger (`db/tests/test_assignments.py` covers both), so this
changes how the overlap is refused, not whether.

## 12. Usage analytics (group 2, area U, spec 10, ADR-0011)

**Engine** (`arcilogger_worker.usage`; method and settings: docs/USAGE.md / docs/KULLANIM.md):
subject = the person the device was assigned to at the moment, else the device (lease MAC), else
the bare IP (`usage.subjects`, the lookup engine's `Attributor`); 1-minute buckets per site day;
interim deltas + final remainder, spread evenly; destination by web filter host name → application
control / SSL host name → DNS answer (`usage.dns_window_seconds`) → `dstinetsvc` → address;
registrable domain by the bundled Public Suffix List; background rules of spec 10.3 (application
categories only for sessions without a host name). Rollup tables (revision `0010_usage`):
`usage_days`, `usage_daily`, `usage_device_daily`, `usage_domain_daily`, `usage_app_daily`,
`usage_hourly`, `usage_blocked`, `usage_urls`, `domain_categories`, `usage_rebuilds`;
`persons.usage_excluded`. Jobs `usage.today`, `usage.daily`, `usage.reattribute`,
`usage.requests`, `usage.retention`; command `arcilogger-worker usage rebuild --from --to`.

**API** (base `/api/v1/usage`, group 2 contract section 2 plus the lead's addition of
2026-10-20). Common query parameters: `from`, `to` (site days, inclusive; `to` defaults to today
and `from` to `to`, so `from` alone runs to today and `to` alone is one day; at most 400 days and
`from` not after `to`, else 422 `usage_period`), `hours` = `all` | `work` | `off`; pages take
`limit` 1-100,000 (default 50; the web reads a whole list in one request, so the period is
aggregated once) and `offset`; `department=<DepartmentRow.department>` (VLAN-derived names included)
restricts `GET /subjects` (person, device), `/sites`, `/sites/{domain}`, `/categories`,
`/categories/{category}` and the matching exports to the subjects of that department (the
person's department, else the VLAN's — the `DepartmentRow.source` rule; `Bilinmiyor` for subjects
with neither, whose `SubjectRow.department` is `null`). `{domain}` and `{category}` are matched
exactly as the lists name them: a domain is lower case, a FortiGuard service keeps FortiGuard's
spelling (`Microsoft-Azure`), and the category of sites without one is `Bilinmiyor` in every UI
language (`SiteRow.category` `null`). Every answer:
`period {from, to, hours}`, `estimated: true`, `computed_until` (end of the newest computed day of
the period; for today the moment of the last refresh), `notice_key: "usage.method_note"`.

| Endpoint | Access | Answer |
|---|---|---|
| `GET /company` | `dashboard.usage` \| `usage.person` \| `usage.anonymous` | `CompanyUsage` (metrics, `hourly` — every hour of the period —, `top_sites` ≤ 10, `top_categories` ≤ 10, `top_apps` ≤ 10, `departments`) |
| `GET /subjects?kind=person\|device\|department&q&sort&order&limit&offset&department` | `usage.person` \| `usage.anonymous`, notice | `SubjectPage` (`kind=device` lists devices and bare addresses) or `DepartmentPage`; `sort` `active_seconds` (default) \| `online_seconds` \| `download_bytes` \| `upload_bytes` \| `blocked` \| `label`, `order` `desc` (default) \| `asc` |
| `GET /subjects/{key}` | same | `SubjectDetail` (timeline only when `from == to`; `hourly` every hour of the period; `heatmap` 30 × 24 cells ending `to`; `urls` ≤ 1000 newest first; `blocked` ≤ 5000 newest first; `per_device[].metrics.blocked` counts every attempt of the device, `usage_device_daily.blocked`, and adds up to the person's count); 404 `usage_subject_not_found`; audited `usage.view` (target the real subject key, also for a pseudonymous viewer, like a `view=subject` export; `GET /audit` shows such a target to a reader who sees pseudonyms as `usage:<token>` with `target_masked: true`, `usage:masked` without the key, compares its `target` filter with that and sends it no hashes: `prev_hash`, `hash` and the `last_hash` of `/audit/verify` are `null`) |
| `GET /sites?q&category&sort&order&limit&offset&department` | same | `SitePage`; `sort` `seconds` (default) \| `raw_seconds` \| `download_bytes` \| `upload_bytes` \| `sessions` \| `users` \| `domain`; `category=Bilinmiyor` selects sites without a category |
| `GET /sites/{domain}?department` | same | `SiteDetail` (`hostnames` ≤ 50, `users` ≤ 200 most time first, `users_total` all of them; the users' metrics are **this site's only**: online = raw time, active = time on the site, `blocked` 0 and no first/last activity — not kept per site); 404 `usage_site_not_found` |
| `GET /categories?department` | same | `CategoryList` |
| `GET /categories/{category}?department` | same | `CategoryDetail` (`sites` ≤ 100 and `users` ≤ 200, most time first; `sites_total`, `users_total` all of them; the users' metrics are the category's only: active = time in it, online 0 — the rollups keep no minutes with traffic on any of its sites, and their raw times added up would count a minute once per site —, `blocked` 0, no first/last activity); 404 `usage_category_not_found` |
| `GET /export.csv`, `GET /export.xlsx` | `/company` rules for `view=company`, else the subjects rules | the view's rows (lists whole: no paging and none of the detail answers' caps; the users of a site or category with their own columns — time, a site's raw time, off-hours time, volumes, sessions, a category's top sites): `view=company`; `view=subjects&kind=…`; `view=subject&key=…`; `view=sites`; `view=site&domain=…`; `view=categories`; `view=category&category=…` (plus `from`, `to`, `hours`, `department`, `q`, `sort`, `order` where the view takes them; a missing `key`/`domain`/`category` is 422 `usage_export_view`). CSV: the main table (company: departments with a company total first; subject: its sites; site: its users; category: its sites); Excel: one sheet per section and a "Yöntem" sheet; audited `usage.export` (view, format, period, rows, the filters used — never their text) |
| `POST /rebuild {from, to}` | `settings.manage` | 202 `RebuildJob` (queued; run by the worker job `usage.requests`); 422 `usage_rebuild_range` (end before start, in the future, more than 400 days); 422 `usage_rebuild_no_logs` (`from` before the site day of the oldest `fg_traffic` row, or before today while there is none: those days would be rewritten empty); audited `usage.rebuild` |
| `GET /rebuild/{job_id}` | `settings.manage` | `RebuildJob {job_id, from, to, status: queued\|running\|done\|failed, progress 0..1, error, requested_by, created_at, started_at, finished_at}`; 404 `usage_rebuild_not_found` |
| `GET /pseudonyms?q=` | `users.manage`, notice | `[PseudonymRow {pseudonym, kind, subject (real SubjectRef)}]`; 409 `pseudonym_key_missing`; audited `usage.pseudonyms` (row count, whether `q` was given) |

Schemas (`web/src/generated/api-schemas.json`): `UsagePeriod`, `SubjectRef` (+ detail `mac`,
`ip`, `hostname`), `Metrics`, `SiteRow` (+ `kind` `domain`\|`service`\|`ip`, `background`),
`CategoryRow`, `AppRow`, `HourPoint`, `BlockedRow` (+ `hostname`, `url`, `app`), `TimelineSpan`,
`HeatCell`, `TopSite`, `TopCategory`, `SubjectRow`, `DepartmentRow`, `CompanyUsage`,
`SubjectPage`, `DepartmentPage`, `HostnameRow`, `UrlRow` (+ `blocked`), `SubjectHour`,
`DeviceUsage`, `SubjectDetail`, `SitePage`, `SiteHost` (+ `sessions`), `SiteDetail`,
`CategoryList`, `CategoryDetail`, `RebuildRequest`, `RebuildJob`, `PseudonymRow`. Durations are
whole seconds (rounded), volumes bytes, `share` 0..1 of the active time of the same scope.
Captured answers: `web/e2e/samples/usage.json` (golden usage site of 12.10.2026).

**Names and pseudonyms** (owner decision 2026-10-20): names, MAC and IP addresses for every role
with `usage.person` or `usage.anonymous`; `SubjectRef.pseudonymous` true (key `p:<16 hex>` /
`d:<16 hex>`, label `Kullanıcı-XXXX` / `Cihaz-XXXX`, no MAC/IP/host name) only when
`usage.pseudonymize_anonymous_role` is on and the viewer has `usage.anonymous` without
`usage.person`; the real keys are then 404 for that viewer. Persons with `usage_excluded` are
404 / absent in person-level answers and counted in the totals.

**Report data** (`arcilogger_worker.usage.report_data`, group 2 contract section 3):
`build_report_data(connection_or_session, *, start, end, kind, person_level, pseudonymize,
generated_at=None, pseudonym_key=None)` — schema 1 with the detail lists `person_sites`,
`person_hours`, `blocked` (≤ 50,000 of the visible subjects, oldest first; `blocked_total` = how
many the period holds, with a `data_quality` note when the list is cut) and `devices` (every
device with traffic or with blocked attempts — one without an online minute has the address and
VLAN of those attempts — and the bare addresses; `person` = the persons it was attributed to in
the period, comma-separated). `persons`, `person_sites` and `blocked` rows carry `subject` (the
`SubjectRef` key: `person:<id>`, or the pseudonym's token) — labels are not unique, the
renderers match a person's rows by it.
`generated_at` defaults to now (pass it for byte-identical output); `pseudonym_key` defaults to
the secret `usage.pseudonym_key` of the default configuration's secret store
(`PseudonymKeyMissingError` without it). Part-day windows: times, volumes and `person_hours`
cover the hours that start inside `[start, end)`, `blocked` and the blocked counts the attempts
inside it, `first_activity` / `last_activity` the active minutes that start inside it (the days'
timelines; the end cut at `end`); sites, categories, sessions, `person_sites` and `devices` whole
days (noted in `data_quality`). Blocked counts of whole days come from `usage_daily` (every
attempt), of a part-day window from the stored `usage_blocked` rows (at most
`usage.blocked_per_day` per subject and day); a `data_quality` note says how many subjects had a
day with fewer rows than attempts (whole days: only with `person_level`, among the visible
subjects). `changes`: company and department `active_seconds`, `download_bytes`,
`category:<name>` against the average of the rolled-up days among the 7 before the period (the
same hours of the day for a part-day window of one day). `sample_report_data()` returns a fixed
example with the same keys.

## 13. Scheduled usage reports (group 2, area R, ADR-0012)

Operator view: docs/KULLANIM-RAPORLARI.md (EN: docs/USAGE-REPORTS.md).

**Tables** (revision `0011_reports`, after `0010_usage`):

- `report_schedules`: `id`, `name` (not blank), `kind` `end_of_day` \| `full_day` \| `weekly` \|
  `monthly`, `enabled`, `time` `HH:MM` (site time), `weekday` 1–7 (only and always for `weekly`),
  `monthday` 1–28 (only and always for `monthly`), `recipients` text[], `formats` JSONB
  `{email_html, pdf, xlsx}`, `person_level` (default true), `pseudonymize` (default false),
  `next_run_at` (NULL while disabled or without recipients), `created_at`, `updated_at`,
  `updated_by` (`:seed` for the four shipped schedules, inserted once by the migration, disabled).
- `report_runs`: `id`, `schedule_id` (FK, `ON DELETE SET NULL`), `name` (the schedule's name then),
  `kind` (schedule kinds + `custom`), `period_from`, `period_to` (exclusive), `person_level`,
  `pseudonymize`, `formats`, `recipients` (snapshot), `send`, `status` `queued` \| `running` \|
  `sent` \| `partial` \| `failed`, `triggered_by` `schedule` \| `manual`, `requested_by`, `due_at`,
  `catch_up`, `started_at` (= queued), `picked_at`, `render_attempts`, `rendered_at`,
  `finished_at`, `error`, `files` JSONB `[{name, format, size, sha256}]`, `deliveries` JSONB
  `[{recipient, status queued|sent|failed|skipped, attempts, error, sent_at, next_attempt_at,
  message_id}]`, `summary` JSONB (subject, attachments, counts), `expired_at`.

**Report data** (group 2 contract section 3): `arcilogger_worker.usage.report_data.
build_report_data` and `sample_report_data()` of the usage engine (section 12). Should
`build_report_data` raise `NotImplementedError` (an installation without the usage engine), a run
fails with `R20-report` ("kullanım motoru yok / usage engine not installed") and the preview
answers 503 `usage_engine_unavailable`. The report adds `alerts` (rows of `alerts` with `time`
in the period: time, rule, severity, state and the catalogue's name and sentence —
`arcilogger_worker.alerts.texts` — never the stored title or details) and `seal` (the previous
site day of the generation time: `sealed` \| `pending` \| `failed` \| `skipped`, from
`state/seal/<day>.json` and the vault). The renderers take one document:
`arcilogger_worker.reports.content.build_document` → `{schema: 1, report, data, alerts, seal}`;
`reports.html.render_html`, `reports.pdf.render_pdf`, `reports.xlsx.render_xlsx`. Test data:
`arcilogger_worker.reports.testing.report_sample()` (golden files in `worker/tests/snapshots/`,
rewritten with `ARCILOGGER_UPDATE_SNAPSHOTS=1`).

**Shared PDF building blocks:** `arcilogger_worker.pdf.fonts` (the DejaVu files in
`worker/src/arcilogger_worker/fonts/`, `register_fonts`, `SANS`, `SANS_BOLD`, `MONO`),
`arcilogger_worker.pdf.styles` (colours, `report_styles`, `fit`, `PagedCanvas`),
`arcilogger_worker.pdf.text` (`pdf_text`, tests only). `arcilogger_api.reports.fonts` and
`arcilogger_api.pdftext` re-export them; the 5651 report renders the same bytes as before.

**Worker jobs:** `reports.run_due` (every minute: due schedules → one queued run for the newest
occurrence; at most 2 renders; deliveries) and `reports.purge` (daily after 03:20). Files:
`<data_dir>/exports/usage-reports/<run id>/` — `ARCILOGGER-kullanim-<kind>-<days>-r<id>.html|pdf|
xlsx` plus the e-mail as rendered (`mail.html`, `mail.txt`, not listed in `files`). Delivery: one
e-mail per recipient through `arcilogger_worker.alerts.mail.SmtpSender`; lease → send → record;
backoff and `MAX_ATTEMPTS` of `arcilogger_worker.alerts.notify`; a failure opens the local alert
`R20-report` (medium, dedup key `R20-report`, details `recipients`, `runs`, `render_failed`).

**Settings:** `reports.keep_days` (400, ≥ 1), `reports.max_attachment_mb` (15, 1–100),
`reports.catch_up` (true); the link uses `alerts.web_base_url`. Recipients are validated by
`arcilogger_db.settings.email_addresses` (the validator of `alerts.email_recipients`, at most 50).

**API** (base `/api/v1/reports`, permission `reports.manage`; `require_privacy_notice` on "run
now", file downloads and the preview; errors in the standard format; times ISO-8601, a time
without an offset is site time):

| Endpoint | Does | Audit (`details`) |
|---|---|---|
| `GET /schedules` | `[ScheduleOut]` by id, each with `last_run` | — |
| `POST /schedules` | `ScheduleCreate {name, kind, enabled?, time, weekday?, monthday?, recipients (≤ 50), formats, person_level?, pseudonymize?}` → 201 `ScheduleOut`; 422 `validation_error` (`loc` `weekday` / `monthday` / `recipients` / `formats` / `time`; `time` also for `end_of_day` at `00:00`, an empty period), 422 `report_recipients_required` (enabled without recipients), 409 `pseudonym_key_missing` (`pseudonymize` without the secret `usage.pseudonym_key`) | `report.schedule.create` (`schedule_id`, fields; `recipients` as a count) |
| `PATCH /schedules/{id}` | `ScheduleUpdate` (any field; `null` refused for required ones; a new `kind` clears a `weekday` / `monthday` it does not use) → `ScheduleOut`; `next_run_at` recomputed when `kind`, `time`, `weekday`, `monthday` or `enabled` change or the recipients become (no longer) empty — other edits keep a passed occurrence the worker has not run yet; 409 `pseudonym_key_missing` when `pseudonymize` is switched on without the key | `report.schedule.update` (`changed`; old/new of `enabled`, `person_level`, `pseudonymize`; recipient counts) — none when nothing changed |
| `DELETE /schedules/{id}` | 204; runs and files kept (`schedule_id` null) | `report.schedule.delete` (`schedule_id`, `name`, `kind`, `runs`) |
| `POST /schedules/{id}/run` | optional `RunRequest {from?, to?, send? (true)}` → 202 `RunOut` (`queued`); no from/to: the kind's own period now (`end_of_day`: today until now); both: `custom`, ≤ 92 days, `to` cut at now; 422 `report_recipients_required` (send without recipients), 409 `report_smtp_disabled` (send while `[smtp] enabled = false`), 409 `pseudonym_key_missing` (a schedule with pseudonyms, no key) | `report.run` (`run_id`, `schedule_id`, `kind`, `from`, `to`, `send`, recipient count, `person_level`, `pseudonymize`) |
| `GET /runs?schedule_id&status&limit (1–200, 50)&offset` | `RunPage {items: [RunOut], total, limit, offset}`, newest first | — |
| `GET /runs/{id}` | `RunOut` | — |
| `GET /runs/{id}/files/{name}` | the file (`application/pdf`, the xlsx type, `text/html`), `X-Arci-Sha256`; 404 unknown name, 409 `report_missing` / `report_damaged` (hash), 410 `report_expired` (after `reports.keep_days`) | `report.download` (`run_id`, `file`, `format`, `sha256`, `size`) |
| `GET /preview?kind&from&to&format=html\|pdf&person_level&pseudonymize` | the report rendered now; not stored, not sent (`kind=custom` needs from/to); HTML with `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`; 409 `pseudonym_key_missing` (`pseudonymize=true` without the key), 503 `usage_engine_unavailable` | `report.preview` (`kind`, `from`, `to`, `format`, `person_level`, `pseudonymize`) |

Schemas: `ScheduleOut`, `RunOut`, `RunPage`, `ReportFormats`, `ReportPeriod` (`{from, to}`),
`ReportFile`, `ReportDelivery`; requests `ScheduleCreate`, `ScheduleUpdate`, `RunRequest` (all
`extra="forbid"`). `RunOut.deliveries` lists decided and tried deliveries only: a delivery not
tried yet is left out, one waiting for its retry is shown as `failed` with the last error while
the run is `running`. `RunOut.started_at` is the moment the run was queued. A finished run is
`sent` also when no e-mail was due (`send` false: deliveries `skipped`). The web link of an
e-mail is `<alerts.web_base_url>/reports?run=<id>` (the Raporlar page may open that run).

## 14. Alerts API, state workflow and the dashboard (group 2, area S)

Decision: ADR-0010 amendment 2026-10-20; operator view: docs/UYARILAR.md / docs/ALERTS.md
section 8. Base `/api/v1`; errors in the standard format; DB times ISO-8601 in UTC, file-based
dashboard times in site time.

**Table** `alert_events` (revision `0012_alert_events`): `id`, `alert_id` (FK `alerts`,
CASCADE), `kind` `state` \| `note`, `from_state` / `to_state` (both set and different for
`state`, both NULL for `note`), `text` (a note: 1–2000 characters; a `system` change: its reason
code, ≤ 200), `actor` (user name or `system`; the API's `by`), `actor_id` (FK `users`, SET
NULL), `at`. Nothing backfilled.

**Writers** (`arcilogger_worker.alerts.store`, the only ones): `resolve(alert, reason=, now=)`
and `reopen(alert, reason=, now=)` (checks, IP-conflict rebuild; `system` events);
`change_state(session, alert, state, by=, user_id=, now=, audit=, note=None)` and
`add_note(session, alert, text, by=, user_id=, now=, audit=)` for a person. The `audit` hook
writes `alert.state` (`{from, to, rule}`) and `alert.note` (`{length}` — never the text);
`change_state` raises `WorkflowError` (`unchanged`, `not_allowed`, `key_active` with `other`)
or `ValueError` (the note) before changing anything. Transitions: `store.TRANSITIONS`
(ADR-0010 amendment A3). A person's change sets `updated_at`, sets `resolved_at` for
`resolved` and clears it otherwise, and drops `details.auto_resolved`.

**Permissions:** `alerts.read` for reads, `alerts.manage` for changes (administrators only);
`/dashboard/summary` needs `dashboard.view` and each box its own permission (below). No KVKK
notice gate (no person-level data). Reads are not audited.

| Endpoint | Does |
|---|---|
| `GET /alerts?state=&severity=&rule=&q=&from=&to=&sort=&order=&limit=&offset=` | `AlertPage {items: [AlertOut], total, limit, offset}`; `state`, `severity`, `rule` repeatable or comma-separated (≤ 20 values, unknown values 422); `q` (≤ 200) part of the title, the rule id or `details.source` (Turkish letters folded, `%`/`_` literal); `from`/`to` on `time` (with offset; from inclusive, to exclusive); `sort` `time` (default) \| `created_at` \| `updated_at` \| `severity` (info < … < critical) \| `state` (open, acknowledged, resolved, dismissed) \| `rule`, then `time` and `id` in the same direction; `order` `desc` (default) \| `asc`; `limit` 1–200 (50), `offset` ≤ 1,000,000 |
| `GET /alerts/summary` | `AlertSummary {open_by_severity: OpenBySeverity {critical, high, medium, low, info} (state open only), acknowledged, latest: [AlertOut] (≤ 5 open/acknowledged, newest `time` first), mean_time_to_close_seconds_30d}` — mean of `newest closing event.at − created_at` over the alerts closed now whose newest closing event (open/acknowledged → resolved/dismissed; resolved ↔ dismissed is no closing) is within 30 days; `null` without one |
| `GET /alerts/{id}` | `AlertDetail` = `AlertOut` + `details` (sanitised: keys like password/secret/token/api key/cookie/authorization redacted to `[gizlendi / redacted]`, `password=…` pairs in texts masked, e-mail addresses masked — whole values and inside texts, as in `title` and `summary_tr`/`summary_en` of every `AlertOut` —, texts > 1,000 characters cut with `… (+n)`, lists > 100 items cut, nesting > 6 levels `…`), `events: [AlertEvent]` (oldest first), `runbook_tr`, `runbook_en` (`texts.runbook`, steps one per line), `deliveries: [AlertDeliveryOut {kind, status, attempts, sent_at, recipient_masked}]` (`a***@firma.com.tr`, `***` if not an address) |
| `POST /alerts/{id}/state` | `AlertStateRequest {state, note?}` → `AlertDetail`; the note is a second event; 409 `alert_state_unchanged` (`details.state`), `alert_transition_not_allowed` (`details: {from, to, allowed}`), `alert_key_active` (`details.alert_id`: the active alert with the same dedup key); 404 `alert_not_found`; 422 for an unknown state or a bad note |
| `POST /alerts/{id}/notes` | `AlertNoteRequest {text}` → 201 `AlertEvent`; trimmed, 1–2000 characters, no control characters but tab, CR, LF (422 otherwise); any state |
| `GET /dashboard/summary` | `DashboardSummary {generated_at, ingest, seal, credit, clock, alerts}` (below) |

`AlertOut {id, time, created_at, updated_at, resolved_at, rule, rule_name_tr, rule_name_en,
severity, state, title, summary_tr, summary_en, auto_resolved, notes_count}`: names from
`texts.RULES` (unknown rules: "ARCILOGGER uyarısı"), the summary from `texts.facts` (the
e-mail's shape-checked sentence), else the title and `details.title_en` (else the English rule
name); `auto_resolved` = state `resolved` and `details.auto_resolved` present; `notes_count` =
note events. `AlertEvent {id, kind, from_state, to_state, text, by, at}`.

**Dashboard boxes** (`null` without the permission):

| Box | Permission | Content |
|---|---|---|
| `ingest` | `sources.read` or `dashboard.view` | `DashboardIngest {sources_total, sources_active, silent: [SilentSource {name, last_seen}], messages_per_minute, sparkline: [MinuteCount {minute, count}]}` — sources = the ones R15 watches (`r15.watched_sources`: enabled, expected, configured); active = newest message (the later of `last_seen` and the collector's receive time, `r15.newest_message`) within `heartbeat_minutes`; the others are silent (never seen: `last_seen` null; no grace period, unlike the alert). Sparkline = the 60 whole minutes before the current one (site time, oldest first, empty minutes 0) of `messages` by `time` (device time); `messages_per_minute` = their mean, one decimal |
| `seal` | `seal.read` | `DashboardSeal {yesterday: SealYesterday {day, status, sealed_at, serial_short, verified}, calendar: [SealCalendarDay {day, status}]}` — the 30 days ending yesterday, oldest first, from the sealing page's day states (`sealinfo.VaultView`): verified/sealed/late → `sealed`, pending/open → `pending`, failed → `failed`, no_data/deleted → `no_data`; `serial_short` = first 8 hex digits + `…`; `verified` = the newest verification passed (`null`: none) |
| `credit` | `seal.read` | `DashboardCredit {remaining, estimated_days, threshold}` — newest known credit (`sealinfo.credit_history`), `remaining ÷ use per day` measured over the 30 days of checks before the newest (only decreases count; one stamp a day when nothing is measurable or the checks span less than a day), `[tsa] credit_alert_threshold` |
| `clock` | `system.read` or `seal.read` | `DashboardClock {offset_ms, ntp_server, checked_at, ok}` — the newest sample of `state/clock/<today or yesterday>.jsonl`; `ok` = within `[clock] alert_offset_ms`, `false` when no server answered, `null` when the sample is older than 3 hours or missing; `ntp_server` = the answering server, else `[clock] ntp_server` |
| `alerts` | `alerts.read` | `DashboardAlerts {open_by_severity, latest}` (as `/alerts/summary`) |

Today: administrators see every box, auditors all but `alerts`, report viewers only `ingest`;
`hr` (only `dashboard.usage`) gets 403 — its usage widgets come from `/usage`.

**Performance:** the messages per minute are one index-only range scan of the primary key
`(time, id)` of the month's partition, grouped by minute (no new index). Guard:
`api/tests/test_dashboard_api.py::test_message_rate_stays_within_its_budget` (`perf`, run by `poe
test-perf`): under `MESSAGE_RATE_BUDGET_SECONDS` = 1 s (an order-of-magnitude guard for shared
CI runners) for 100,000 and for 300,000 messages in the hour (measured about 30–50 ms and
80–150 ms on the development machine; the dashboard refreshes every 30 s).

Response schema names: `AlertOut`, `AlertEvent`, `AlertDeliveryOut`, `AlertDetail`, `AlertPage`,
`OpenBySeverity`, `AlertSummary`, `DashboardSummary`, `DashboardIngest`, `SilentSource`,
`MinuteCount`, `DashboardSeal`, `SealYesterday`, `SealCalendarDay`, `DashboardCredit`,
`DashboardClock`, `DashboardAlerts`; requests `AlertStateRequest`, `AlertNoteRequest`
(`extra="forbid"`). Captured answers: `web/e2e/samples/alerts.json`, `dashboard.json`.

## 15. Licence API (offline licensing, ADR-0013)

Decision: [ADR-0013](adr/0013-offline-licensing.md); customer guide: docs/LISANS.md /
docs/LICENSING.md. Base `/api/v1`, router `routers/license.py` (tag `license`); errors in the
standard format with the texts of `arcilogger_core.license.errors.MESSAGES` (HTTP statuses
`errors.HTTP_STATUS`). The router imports no licence code at module level; when the licence
package cannot be imported every licence route answers 503 `license_unavailable`.

| Endpoint | Access | Does |
|---|---|---|
| `GET /license` | any signed-in user (`require()`; no KVKK notice; not audited) | `LicenseOut` (below) |
| `POST /license` | `require_recent_auth("settings.manage")` | `multipart/form-data`, field `file` (the `.lic`), read with `uploads.read_body(request, 65_536 + OVERHEAD, too_large_code="license_too_large")`; order: parse + signature (`load_license`) → `check_applicable` → store + `install` event + `license.install` → cache invalidated → 200 `LicenseOut` (admin form) of the new state. The same licence again (same `payload_sha256` as the active one, also reformatted): 200, event with `"unchanged": true`. Any `LicenseError` (also a body refused before it was read: `sha256` null, `size` = `Content-Length` or the bytes read) is recorded as a `refuse` event + `license.refuse` audit that are committed although the request fails, then answered with its code. A missing `file` part is 422 `upload_invalid` (no event, no audit) |
| `DELETE /license` | `require_recent_auth("settings.manage")` | 204; 404 `license_not_installed`; `remove` event + `license.remove`; cache invalidated; the state becomes `trial` (within the trial window) or `trial_expired` |

`LicenseOut {status, problem, modules: [LicenseModuleOut {key, name_tr, name_en, description_tr,
description_en, enabled, always, licensed}], trial: LicenseTrialOut {started_at, ends_at,
days_left} | null (states trial and trial_expired), licensee, maintenance: LicenseMaintenanceOut
{until, days_left, expired, ends_soon, covers_version} | null, valid_until, limits:
LicenseLimitsOut {log_sources, users} (each a LicenseLimitOut {max, used, unconfigured,
exceeded, full}), product: LicenseProductOut {name, version, release_date}, licensor: LicensorOut {name,
email}, checked_at, details: LicenseDetailsOut | null}`. `status` is one of `trial`,
`trial_expired`, `licensed`, `invalid`, `wrong_server`, `expired`, `version_not_covered`;
`problem` the code of the state (incl. `trial_clock_inconsistent`, `license_revoked`) or null;
`modules` every registry module in registry order (`enabled` = `module_enabled`, `licensed` =
listed in a verified licence document). `details` only with `settings.manage`: `license_id`,
`key_id`, `issued_at`, `server_ids`, `installed_at`, `installed_by` (null without an active
licence), `server_id`, `server_id_error`, `installation_id`, `organization` (`site_name`),
`request_code` (null when the server id cannot be read), `time: LicenseTimeOut {now, source,
tsa_time, vault_day_end, system_time, clock_behind}` (`source` one of `system`, `tsa`,
`vault_day`, `release_date`, `license_issued`), `history: [LicenseEventOut {at, kind, source,
actor, license_id, details}]` (newest 20 licence events, newest first). Every licence field comes from the stored file, verified again, never from
the index columns of `license_files`. Times in site time with offset, dates `YYYY-MM-DD`.
Captured answers: `web/e2e/samples/license.json`.

**Gating** (`arcilogger_api.licensing`): `require_module(key)` is a router-level dependency of
`routers/usage.py` (`usage_analytics`), `routers/reports.py` (`usage_reports`) and
`routers/imports.py` (`bulk_import`); its callable is named `require_module_<key>` and carries
`__arci_module__ = key` (the route classification test compares the markers with its table). It
needs a signed-in user (401 first), then answers **403 `module_not_licensed`** with
`details {"module", "license_status"}` (`"unavailable"` when the licence subsystem failed) unless
the module is enabled — before parameter and model validation, for any well-formed request. The
status is cached on `AppState.license_cache` for `LICENSE_CACHE_SECONDS` = 30 s and read through
`arcilogger_db.license_guard.safe_status` (a failure: `core_5651` only, never cached). Every other
route (5651 Temel, 65 routes incl. the three of the licence) is never gated, in any state; the
dashboard summary never changes with the licence.

**Users limit** (`routers/users.py`): after `lock_user_management`, `POST /users` and a `PATCH
/users/{id}` that changes `active` from false to true take `max` from the cached status
(`users_limit`; `None` → no limit), **recount** `used = count(users WHERE active)` in the same
transaction and refuse with **409 `license_limit`** (`details {"limit": "users", "max", "used"}`)
when `max is not None and used >= max`; the refusal is audited in a separate transaction as
`license.limit_refused` (`{"limit", "max", "used", "username", "operation": "create" |
"reactivate"}`, target `user:<username>`); a successful create or reactivate invalidates the
cache. `max` per state: trial none; `licensed`, `wrong_server`, `expired`, `version_not_covered`
the licence's; `trial_expired`, `invalid` 3 (`UNLICENSED_USERS`). Never limited: `POST
/auth/setup/admin`, sign-in, role changes, deactivation, and `arcilogger-api create-admin` in true
recovery (no administrator can sign in) or with `--deactivate USERNAME` (otherwise refused at the
limit, exit 1, `"operation": "create_admin"`). Log sources over their limit are never refused
anywhere.

**Audit actions:** `license.trial_start`, `license.install`, `license.remove`, `license.refuse`,
`license.limit_refused`, `license.limit_exceeded`, `license.limit_ok`, `license.state` (details:
docs/SECURITY.md section 11; never the licensee name or server ids, which stay in
`license_events`).

**CLI** (`arcilogger-db license status | request-code | verify FILE | install FILE | remove
[--yes]`, each with `--json` but `remove`, and `--config`): exit 0 done, 1 refused (also
`license_unavailable`, no licence to remove, `request-code` without a server id), 2 usage or
configuration, 3 database unreachable; `--json`: `status` the admin `LicenseOut`, `install` /
`verify` `{"ok": true, "status"}` or `{"ok": false, "code", "message_tr", "message_en",
"details"}`, `request-code` `{"request_code", "server_id", "installation_id", "organization",
"product_version"}`; `request-code` without `--json` prints exactly one line, the code. `status`,
`install` and `remove` write `state/license-status.json` (schema `arcilogger.license-status/1`:
`written_at`, `status`, `license_id`, `licensee`, `server_id`, `maintenance_until`,
`valid_until`, `modules` (enabled), `version`, `release_date`), which the worker job
`license.check` rewrites every 5 minutes; only the installer reads it.

Response schema names: `LicenseOut`, `LicenseModuleOut`, `LicenseTrialOut`,
`LicenseMaintenanceOut`, `LicenseLimitsOut`, `LicenseLimitOut`, `LicenseProductOut`,
`LicensorOut`, `LicenseDetailsOut`, `LicenseTimeOut`, `LicenseEventOut`.
