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
| `arcilogger-db` (`arcilogger_db`) | models, migrations, seed, settings, partitions, bulk loading, test databases, licence state (`licensing`, `license_guard`) | `arcilogger-db upgrade \| current \| check \| partitions \| version \| license status\|request-code\|verify\|install\|remove \| leases prepare-rollback` |
| `arcilogger-collector` | syslog → raw spool | `arcilogger-collector run` |
| `arcilogger-ingest` | spool → parsers → PostgreSQL | `arcilogger-ingest run [--once] [--workers N] \| status \| version` |
| `arcilogger-worker` | leases, devices, lookups, evidence purge, audit import; later NAT index, usage, rules | `arcilogger-worker run \| rebuild --day D [--to D] \| version` |
| `arcilogger-api` | HTTP API (depends on `arcilogger-worker` for the lookup types/engine) | `arcilogger-api serve [--host H] [--port P] \| setup-token \| create-admin [--deactivate USERNAME] \| version` |

Exit codes. `arcilogger-db`: 0 ok, 1 needs attention (no/old schema, warnings), 2 configuration
error, 3 database unreachable. `arcilogger-worker` and `arcilogger-api setup-token` /
`create-admin`: the same scheme (1 = a job failed / refused), plus `setup-token` 4 = an
administrator exists already, no token (the installer tells it apart from a failure).
`arcilogger-db leases prepare-rollback` (ADR-0016, section 18.4): 0 with the count of FortiGate
leases lowered, 1 on a database error (a timeout included), 3 database unreachable.
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
  `audit.spool_watermark`. **Status rows of Wave 3** (ADR-0016; no default, never listed by
  `GET /settings`, never editable, like `db.timescale`):
  - `nat.egresses_migration` `{migration, from, new, dropped [{value, reason}], conversion_failed,
    audited}`, written by revision `0018_nat_egresses` (author `:migration`) when it converted
    administrator-written legacy NAT keys; the seed only sets `audited` once it wrote the
    `settings.change` entry of the conversion; `from` keeps a legacy number that canonical JSON
    refuses (a float, an integer beyond ±(2^53 − 1)) as its text; `new` is what the lookup compares
    an exit with to tell whether it is still *as converted*;
  - `nat.egresses_changes`: per exit `{fingerprint, changed_at, changed_by, deleted}` (the content
    fingerprint of mode, public, transips, public_dynamic), seeded by v0018 from the newest
    administrator-written legacy row, kept by `settings_admin.change_setting` (author `:system`,
    `changed_at` the audit entry's UTC time); read by `run_lookup` for
    `egress_changed_after_event` (section 5);
  - `leases.rollback_lowered` `{at, count}`: written by `arcilogger-db leases prepare-rollback`
    at an installer rollback (every `fg_dhcp` lease set to `low`), read and deleted by the seed of
    the re-upgrade, which restores `high` on the rows still carrying that `at` and prints both
    counts with the rebuild range.
- **Wave 3 schema** (revisions `0017_fortigate_dhcp`, `0018_nat_egresses`; docs/DATA-MODEL.md):
  the time-series table `fg_dhcp` (FortiGate DHCP server events, appended last to
  `TIME_SERIES_MODELS`; indexes `ix_fg_dhcp_ip_time`, `ix_fg_dhcp_mac_time`),
  `fg_traffic.srcmac` (nullable `macaddr`, no index), `leases.lease_seconds` (300-8,640,000 or
  NULL, `ck_leases_lease_seconds`), `ck_leases_source_table` (`mt_dhcp`, `mt_arp`, `fg_dhcp`:
  `models.LEASE_SOURCE_TABLES`) and `ix_leases_source_table_end`. Revision 0018 changes settings
  only (section 5, "Internet exits").
- VLAN and department of an address: `arcilogger_db.network.department_for_ip(session, ip)` →
  `VlanDepartment(vlan_id, vlan_name, department, source)`; the department is the linked one,
  else the VLAN's Turkish name (spec 2.2). Use it wherever a department is derived from a VLAN.
- `lookups`: `authority`, `file_no`, `request_date` and `reason` are NOT NULL and must not be
  blank (spec 12.3 page 6) — the API refuses a lookup without them before running it.
- **Search** (revision `0016_search`, section 17): `search_counts` (hourly summary of the loaded
  lines per file, kind, typed table, lowercased action and private /24 of the client;
  `arcilogger_db.searchcounts`), `saved_searches` (personal; names unique per owner ignoring
  case), `ingest_offsets.counts_from_line` (lines before it were loaded before the upgrade and
  are not in `search_counts` yet), the expression index `ix_fg_web_rhost_time`
  (`reverse(lower(hostname)), time`) for `domain:`. `partitions.drop_before` also deletes the
  `search_counts` rows before the first day of the cutoff's month (the month it keeps whole
  keeps its counts). The kind of a line (`traffic`, `web`, `dhcp` …) is
  `arcilogger_db.kinds.kind_of(type, subtype)` everywhere (ingest, search, live tail).
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
- **FortiGate DHCP and srcmac (Wave 3, section 18.5):** an `event` line with `ip`, `mac` and
  `dhcp_msg` is an `fg_dhcp` row when `ip` and `mac` convert, else it stays `fg_event` with the
  failure `fortigate.dhcp`; `fg_traffic` gains the column `srcmac` (field kind `mac`).
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
- **Counts step (Wave 2, section 17.5):** the loader's steps are `offsets`, `copy`, `failures`,
  `sources`, `counts`, `commit`. `counts` adds the lines that really loaded (a salvaged line
  without its typed row counts with `tbl = ''`) to `search_counts` with
  `searchcounts.upsert_counts`, in the batch's transaction, so rows, offsets and counts agree
  after a crash at any step. Streams loaded before the upgrade carry
  `ingest_offsets.counts_from_line`; the worker job `search.counts_backfill` recounts them
  (`rebuild_stream`) and clears it, and `arcilogger-db search-counts status | rebuild | verify`
  does the same by hand (section 17.5).

## 5. 5651 lookup (B3)

`arcilogger_worker.lookup` — frozen dataclasses with `to_dict()` / `from_dict()` (plain JSON, UTC
ISO times, **no floats**):

| Type | Purpose |
|---|---|
| `LookupRequest(at, public_ip, public_port, protocol, dest_ip, dest_port, dest_domain, tolerance_seconds, internal_ip, mac)` | validated, normalised (IPs, MAC `aa:bb:…`, domain lower-case, `protocol` stored as the number: `"tcp"`/`"6"` → 6, comparable with `fg_traffic.proto`) |
| `RawRef(source, day, line, offset)` | `.spool_path(layout)`, `.vault_path(layout)` |
| `EvidenceRef(table, row_id, time, raw, summary)` | one supporting row; `to_dict()` is also the element format of `leases.evidence` |
| `LeaseInfo`, `DeviceInfo`, `PersonInfo` | chain steps, each with `confidence` and `evidence`. `LeaseInfo` (ADR-0016) also has `source_table` (`mt_dhcp` \| `fg_dhcp` \| `mt_arp`), `lease_seconds` (the lease time logged by the lease's own DHCP record at or before the chain's moment; `null` = the configured time) and `last_dhcp_evidence` (the lease's own newest DHCP record at or before the chain's moment, never the line that ended it, never a later renewal; for `fg_dhcp` from the rows, otherwise `refs.last_dhcp_evidence`) |
| `AttributionChain` | internal IP → lease → device → person/department (`department_source` person\|vlan\|none, overall `confidence` = `weakest(...)`). **IP conflict** (two MACs held the IP at T, spec 9.1): the most likely chain carries the others in `alternatives`; it and every alternative have `confidence="low"` and the `ip_conflict` explanation (enforced for the main chain) — a report never silently names one person. The person step is the device's assignment **valid at T** (section 11; `PersonInfo.link` `history`\|`current`, `assigned_from`, `assigned_to`); `person_link_at_lookup_time` only for a device without any assignment history. `mac_source` (ADR-0016) is `lease`, or `srcmac` for a chain whose MAC comes from the FortiGate session's srcmac field (no lease; always `low`, "kısmi kanıt") |
| `TrafficInfo`, `NatMatch(kind exact\|candidate, score 0–100, time_distance_ms, destination_match, traffic, chain)` | one match; `destination_match` ∈ `ip_port`, `ip`, `ip_other_port` (requested IP, another port), `domain`, `domain_other_port`, `domain_address` (only the address is known for the name, from another client's logs — possibly a shared CDN address), `domain_unverified` (exact mode, domain-only request: the NAT port identifies the session, no log ties it to the name), `port` (only a destination port was requested, and it matched), `none`, `not_requested`; outcomes stored before the detailed kinds use only the coarse ones. ADR-0016: `TrafficInfo.srcintf`, `dstintf` and `srcmac` (`srcmac` only on interfaces of `lookup.srcmac_interfaces`, so no other person's MAC is in the typed fields while the feature is off); `NatMatch.egress` (the exit that claims the transip of the match's row nearest T; on a shared transip the first exit by name whose interfaces hold the row's `dstintf`; `null` when unclaimed) and `NatMatch.untrusted_sources` (the sources of its rows that `trust.untrusted_sources` refuses, D24) |
| `LookupOutcome(request, mode exact\|candidates\|internal\|none, matches, double_nat, attribution, explanations, data_window, tolerance_seconds, generated_at, truncated, egress, unconverted_dhcp)` | stored in `lookups.outcome`. `egress`: `LookupEgress` of a public-IP request, `null` without a public IP; `unconverted_dhcp`: `EvidenceRef[]` of the D8 probe (FortiGate DHCP lines that stayed `fg_event` rows; at most one per IP, internal mode and exact matches only) |
| `LookupEgress(resolution, egresses, searched, changes, changed_after_event)`, `EgressEntry(name, label, mode, public, public_dynamic, transips, interfaces, port_preservation)`, `EgressSearched(transips, unclaimed, unclaimed_seen, outside_seen, interfaces, interface_filter)`, `EgressChange(name, changed_at, changed_by, deleted, after_event)` | how the requested public IP was resolved to an internet exit (ADR-0016; "Internet exits" below) |
| `Explanation(key, text_tr, text_en, params)` | standard texts in `EXPLANATIONS` (`double_nat` = spec 12.4 wording); the engine's own keys in `arcilogger_worker.lookup.texts.ENGINE_EXPLANATIONS` |
| `LookupSettings.from_settings(get_settings(session))` | values only (the signature every caller uses): `max_session_seconds`, the lease values, `nat_egresses` (the raw `nat.egresses`) with the lazy `egresses()` (`arcilogger_db.egress.parse_egresses`, cached; `ValueError` when it cannot be parsed, each caller decides what that means), `snat_trandisp`, `srcmac_interfaces`, `srcmac_max_ips`, `fortigate_logids`, `fortigate_dhcp_msgs`, `use_logged_lease`, `fortigate_traffic_extends`, `fortigate_interfaces_excluded`, `fortigate_format_verified`; `lease_timeout_seconds(server, lease_seconds=None)` (a logged FortiGate lease time as it is, D6; otherwise the server's configured time × `lease.timeout_percent` / 100), `server_lease_seconds(server)`. The lookup-only values (`nat.snat_trandisp`, `lookup.srcmac_*`) of another shape fall back to their shipped default, so the lease job and the usage rollups, which call it too, never stop on them; the `dhcp.fortigate_*` values stay strict. `run_lookup` reads the status rows `nat.egresses_changes`, `nat.egresses_migration` and the author and time of the `dhcp.fortigate_format_verified` row itself (one select at its start) |

Entry points (B3, implemented): `run_lookup(session, request, settings, *, layout=None, now=None)
-> LookupOutcome` (``layout`` lets the data window say whether a day missing from the database is
in the sealed vault; ``now`` for tests) and `attribute(session, ip, at) -> AttributionChain |
None`. Both only read; `run_lookup` switches JIT off for the caller's transaction (`SET LOCAL jit
= off`: compiling cost more than the bounded queries). The API stores the lookup and writes the
audit entry. Exact mode follows the exit resolution ("Internet exits" below): a `public`
address of a `direct` exit, an exit's NAT address (`transip`) or an `observed` address, each with
a port, is matched exactly on `transip = P AND transport = port`.

`from_dict` accepts every outcome stored before ADR-0016; an absent member reads as its default:
`LeaseInfo.source_table`, `lease_seconds`, `last_dhcp_evidence` → `null`;
`AttributionChain.mac_source` → `"lease"`; `TrafficInfo.srcintf`, `dstintf`, `srcmac` → `null`;
`NatMatch.egress` → `null`, `untrusted_sources` → `[]`; `LookupOutcome.egress` → `null`,
`unconverted_dhcp` → `[]`; inside `egress`, `searched.outside_seen` → `[]`. The API returns a
stored outcome as stored (section 6.1), so a client reads an absent member as that default.

- **Modes.** A request that names a destination (IP, port or domain) lists only sessions to that
  destination — in candidate **and** exact mode (sessions reusing the NAT port for a logged
  destination IP/port other than the requested one are only counted, `exact_other_destinations`
  / `exact_only_other_destinations`); when there is none the answer is `none` with
  `destination_not_found` — unrelated clients are never listed (data minimisation, spec 3.3); a
  request without destination lists the whole window on purpose
  (`candidates_without_destination`). In exact mode a domain-only request is never answered
  "no session" just because no DNS/web/app log names the domain: the sessions on the port are
  then listed as `domain_unverified` (`exact_domain_unverified`), or, when a session tied to the
  name exists, the others are counted neutrally (`exact_unverified_destinations`). Which NAT
  addresses a public-IP request searches is the exit resolution below. The spec 12.4 text
  `double_nat` ("… aşağıda … adaylar listelenmiştir") appears **only with candidates**; with
  nothing to list the key is `double_nat_no_candidates`. Only sessions that left with an address
  (`transip`, else the source) of the requested public IP's family are candidates, and the
  unclaimed predicate covers IPv4 private and shared space only: an IPv6 public IP lists no
  session at all, NAT66 included (the API refuses IPv6 lookups anyway, section 6.1).
- **Wave 2 NAT keys (historical).** `nat.public_ips`, `nat.wan_transip`, `lookup.port_hint` and
  the lookup's `exact_addresses()` are gone: revision `0018_nat_egresses` converted
  administrator-written values into `nat.egresses` (`arcilogger_db.egress.legacy_egresses`, a
  frozen copy in the revision), deleted the rows still owned by `:seed` and keeps the
  administrator's rows unread for one version (D18; `R20-nat:legacy-changed` names an edit of them
  made during a rollback). `public_ip_not_configured` and `wan_transip_not_seen` stay in the
  explanation catalogue so stored lookups still render, and are never emitted again.
- **Lease ids are stable** across rebuilds (a recomputed interval keeps the id of the stored row
  with the same ip, mac and start), so `LeaseInfo.lease_id` in a stored outcome and in audit
  entries keeps pointing at its row; an id disappears only when its interval disappears.
- **Settings read by the worker** are all in `DEFAULT_SETTINGS` (changed on Ayarlar → Uygulama
  ayarları or with `arcilogger-db settings set KEY JSON_VALUE --by NAME` on the console; validated,
  audited as `settings.change`):
  `lookup.dns_lookback_seconds` (3600), `devices.oui_file` (""),
  `arp.ignore_after_release_seconds` (300), `worker.leases_lag_seconds` (120); the API adds
  `lookup.timeout_seconds` (5) and `lookup.report_timestamp` (false), section 6. ADR-0016 adds
  ten keys to the group `lookup` ("5651 sorgu ve kira zaman çizelgesi" / "5651 lookup and lease
  timeline"; refusal codes in section 18.2):
  - `nat.egresses` (`{}`): at most 8 exits, validated by `arcilogger_db.egress.validate_egresses`
    ("Internet exits" below); `{}` = one implicit double-NAT exit;
  - `nat.snat_trandisp` (`["snat"]`): 1-8 values, each `^[a-z][a-z0-9+_-]{0,15}$`;
  - `lookup.srcmac_interfaces` (`[]`): at most 32 interface names (1-35 characters, no control
    characters, no leading or trailing space; de-duplicated before counting); `[]` = srcmac off;
  - `lookup.srcmac_max_ips` (1): integer 1-16;
  - `dhcp.fortigate_logids` (`{"0100026001": "assigned", "0100026002": "deassigned"}`): at most
    16 entries, keys of 10 digits, values `assigned` | `deassigned`; `{}` = FortiGate DHCP
    records are not used;
  - `dhcp.fortigate_dhcp_msgs` (`{"0100026001": ["Ack"]}`): at most 16 entries, keys of 10
    digits, values lists of 1-8 texts of 1-32 characters without control characters,
    de-duplicated case-insensitively; a logid without an entry is not restricted;
  - `dhcp.fortigate_use_logged_lease` (true), `dhcp.fortigate_traffic_extends` (false),
    `dhcp.fortigate_format_verified` (false): booleans;
  - `dhcp.fortigate_interfaces_excluded` (`[]`): at most 32 interface names, the rule above.

  `REAUTH_KEYS` and `LOCKED_KEYS` are unchanged. Revision 0018 converts the Wave 2 keys
  `nat.public_ips`, `nat.wan_transip` and `lookup.port_hint` (see "Wave 2 NAT keys" above).

**Internet exits** (ADR-0016; `arcilogger_db.egress`, `lookup/engine.py` `_resolve_egress`,
`lookup/sessions.py`). An exit of `nat.egresses` is `{label, mode, public, public_dynamic,
transips, interfaces, port_preservation}` under its name (`^[a-z0-9][a-z0-9-]{0,31}$`); modes
`direct` (the FortiGate holds the public address), `isp_nat` (an ISP router translates the
FortiGate's NAT address a second time), `not_logged` (the line does not pass the FortiGate) and
`internal` (a source-NAT address towards a DMZ, VLAN, VPN or branch, no internet exit);
`port_preservation` `unknown` | `preserved` | `not_preserved`. An `isp_nat` exit without
`transips` is the catch-all (at most one); `egress.IMPLICIT_CATCH_ALL` (name `""`, mode
`isp_nat`) stands in when no exit is configured and has no change entry. The outcome of a
public-IP request carries:

```json
"egress": {
  "resolution": "public | transip | fallback | observed | public_unknown | unconfigured | no_egress",
  "egresses": [{"name": "hat-2", "label": "Yedek hat", "mode": "isp_nat",
                "public": ["198.51.100.77"], "public_dynamic": true,
                "transips": ["100.64.13.9"], "interfaces": ["wan2"],
                "port_preservation": "unknown"}],
  "searched": {"transips": ["100.64.13.9"], "unclaimed": false,
               "unclaimed_seen": [], "outside_seen": [],
               "interfaces": ["wan2"], "interface_filter": true},
  "changes": [{"name": "hat-2", "changed_at": "2026-10-20T08:12:00Z",
               "changed_by": "yonetici", "deleted": false, "after_event": false}],
  "changed_after_event": false
}
```

- **Resolution** of a requested public IP P (first rule that applies; exits taken by name):
  - `public`: P lies in an exit's `public`. `direct`: with a port and the log check passing,
    exact on `transip = P AND transport = port` (`exact_nat`); without a port, candidates on
    `transip = P` (`port_missing`). `isp_nat`: candidates over the exit's `transips` (a
    catch-all: the unclaimed predicate) with `egress_search`, plus `egress_public_dynamic` when
    the exit is `public_dynamic`. `not_logged`: nothing is searched (mode `none`,
    `egress_not_logged`), unless P appears as a session's `transip` overlapping T ± 15 min, then
    `observed` with that exit and `egress_not_logged_but_seen`;
  - `transip`: P is an exit's NAT address: exact on P with a port, candidates on `transip = P`
    without (`egress_search`); for an `internal` exit nothing is searched (`none`,
    `egress_internal`);
  - `fallback`: a `public` `direct` or a `transip` resolution whose log check fails (P not seen
    as `transip` within ±max(900 s, tolerance) of T while a private or shared `transip` outside
    the exit's own set is seen), or passes "seen, but not alone" while no trusted session
    through P spans T and the search on P would find none: candidates over the exit's own
    addresses and the `transips` of the fallback exits F (the `isp_nat` exits without a public
    address that share an interface with it, or, when neither declares interfaces, both as
    converted by v0018; F empty or a catch-all: ∪ the unclaimed predicate), on its interfaces
    when it declares any; `public_ip_not_transip` (+ `egress_transip_not_seen`), never
    `egress_search`. `egresses` holds the failed exit, then F; `searched.outside_seen` the
    private or shared addresses that made the check fail (at most 5);
  - `observed`: P, claimed by no exit, appears as a session's `transip` overlapping T ± 15 min
    (the FortiGate itself used it): exact with a port, candidates without
    (`egress_observed_not_configured`), with or without exits; `egresses` holds the `not_logged`
    exit that claims P, if any, else nothing;
  - `unconfigured`: no exit is configured: candidates over the unclaimed predicate
    (`egress_unconfigured`), `egresses` = [the implicit catch-all];
  - `public_unknown`: some `isp_nat` exits have no public address or a `public_dynamic` one:
    candidates over their NAT addresses, each exit with its own interface rule and not-seen
    widening (`egress_public_unknown`);
  - `no_egress`: otherwise nothing is searched (`none`, `public_ip_no_egress`); `egresses` lists
    every configured exit (the list P was checked against).
- **Unclaimed predicate:** `transip` not inside any exit's `transips` (internal exits included)
  or any `direct` exit's `public`, inside the private or shared ranges (`egress.NOT_PUBLIC`:
  RFC 1918, RFC 6598, reserved) and with a `trandisp` of `nat.snat_trandisp`; always over the
  whole bounded search window. An unclaimed public `transip` is the FortiGate's own address:
  answered as `observed` when it is P, never a candidate for another address.
  `searched.unclaimed` says the predicate was searched; `searched.unclaimed_seen` lists at most 5
  unclaimed addresses the probe saw (public ones included).
- **Exit rules:** an `isp_nat` exit whose `transips` have no row within ±15 min of T while
  unclaimed rows exist is searched over its `transips` ∪ the unclaimed predicate
  (`egress_transip_not_seen`); an exit that declares `interfaces` adds `dstintf =
  ANY(interfaces)` to every part of its search (`searched.interface_filter`), dropped only when
  rows of its own addresses near T exist and none carries one of its interfaces
  (`egress_interface_not_seen`), kept for an exit that shares a transip with another
  (`egress_interface_not_seen_kept`); never in exact mode. Unclaimed addresses seen near T while
  exits are configured give `egress_unmapped_seen` (at most 5); unknown `trandisp` values of an
  unclaimed search give `nat_unknown_trandisp` (at most 5); a `preserved` exit ranks a candidate
  whose `transport` equals the requested port +5 (`port_hint` with the exits' names; never exact
  mode). A match whose rows resolve to different exits gets `session_changed_exit`. Rows of
  untrusted senders (`trust.untrusted_sources`, D24) never feed a probe; a match with such rows
  names them in `untrusted_sources` and the outcome gets `session_from_unknown_source`, while a
  sender configured later gives `source_configured_later` (info).
- **Changes after the event (D32):** `changes` lists, for each exit of `egresses`, and, when no
  exit claims P now (`public_unknown`, `no_egress`, `unconfigured`; not `observed`), for each exit
  (deleted ones included) whose content changed after T, its last content change from
  `nat.egresses_changes` (`changed_by` as stored; an exit without an entry falls back to the
  `nat.egresses` row's time and author); `changed_after_event` is true when an entry has
  `after_event`, and the outcome then gets `egress_changed_after_event` (`{changes_tr}` /
  `{changes_en}` joined with "; ", internal authors in words). A label-, interface- or
  port-preservation-only edit is no content change.
- **`double_nat`:** true for the `isp_nat` `public` and the `transip` resolutions of an
  `isp_nat` exit, `fallback`, `public_unknown` and `unconfigured`; false for `direct`,
  `not_logged`, `observed`, `internal` and `no_egress`. For a request without a public IP
  (internal IP, MAC, destination only) it means "no `direct` exit", and an unreadable
  `nat.egresses` counts as true.
- **`nat.egresses` that cannot be parsed:** the lookup router answers 503
  `lookup_settings_invalid` for a public-IP request only (section 6.1); the lease job, the lease
  alerts and the usage rollups never parse it; `R20-nat:invalid` names it (section 18.6); the
  observed endpoint answers with `exits_invalid` (section 16).

**Lease evidence and attribution (ADR-0016; CORRELATION.md, contract 2.6-2.9).** One line each:

- **FortiGate DHCP leases** (`source_table = fg_dhcp`): only rows that `lookup/trust.py` `kept`
  accepts are evidence (`refusal` names the first rule that refuses a row: untrusted source,
  excluded interface, IPv6, unmapped logid, unexpected `dhcp_msg`, receive time, bad MAC;
  `trust.REFUSAL_RULES`, `trust.CONFIGURATION` = the excluded-interface and IPv6 rules); a logged
  lease time (300-8,640,000 s) ends the lease that long after its last DHCP record, without the
  timeout margin (D6). A chain carries `fortigate_format_unverified`, or
  `fortigate_format_verified` with `at` and `by` read from the setting row at lookup time.
- **Lowered to `low` with a reason:** `lease_renewal_not_seen` (the moment lies more than half a
  lease after the lease's last DHCP record with no renewal in between);
  `fortigate_dhcp_not_converted_since_lease` (FortiGate DHCP lines of the chain's IP or MAC after
  its last DHCP record that the timeline did not apply: unconverted `fg_event` lines, the window
  capped at 604,800 s for `mt_dhcp` chains, and converted rows of a MAC move the build missed,
  8,640,000 s); `fortigate_dhcp_no_network` (an `fg_dhcp` chain outside every configured
  network); `dhcp_sources_overlap` (two DHCP origins in one network, D27; outside configured
  networks the address itself is `{network}`); `source_clock_offset` (the median offset of the
  rows nearest T on each side, at most 2,500 each, rounded to whole seconds; limit 120 s for
  internal lookups and lease tables, max(120 s, tolerance) for a source measured for the session
  search alone).
- **A RELEASE mapped after the build** (the newest kept `fg_dhcp` row of the pair in
  `[lease.start, at]` is `deassigned`) ends the lease in the lookup: no chain, no new key.
- **srcmac** (`lookup.srcmac_interfaces`): a session without a lease on a listed interface gives a
  chain with `mac_source = srcmac` at `low` (`srcmac_only`); a srcmac that differs from the
  lease's MAC gives `srcmac_mismatch` with the srcmac chain as a low alternative (`srcmac_agrees`
  when equal); guards `srcmac_shared` (more than `lookup.srcmac_max_ips` IPv4 addresses within
  ±1 h; a used row outside the hour counts its own address), `srcmac_other_lease`,
  `srcmac_ambiguous` (at most 3 MACs named) and `srcmac_infrastructure`.
- **D8 probe:** when no chain is built (internal `no_lease` / `no_lease_for_mac`, or a match
  with `chain_missing`), the newest DHCP-shaped trusted row before T per IP (at most 20 IPs;
  keyed on the MAC for a MAC-only lookup) within max(longest lease window, 604,800 s) counts
  when it is an `fg_event` row of an `assigned` logid that would have covered T:
  `fortigate_dhcp_not_converted` with `{ips}`; in internal mode and for an exact match the row
  is cited in `unconverted_dhcp` (summary "FortiGate DHCP kaydı (kira zaman çizelgesine
  girmedi; zincir kurulmadı, güven düzeyi verilmez)"), never for candidates.
- **`lease_evidence.<table>.last_hour`** (`GET /sources`, section 17.8) is the time of the
  newest usable row of that table within the last hour, `null` when none arrived in it.

**Session matching (spec 9.3, no `nat_index` table yet).** `fg_traffic.time` is the time of the
log line (session end or interim update). A row's session interval is `[time − duration s,
time]` (no `duration` → the instant `time`); it matches when that interval overlaps `[at −
tolerance, at + tolerance]`; `time_distance_ms` = 0 inside, else the distance to the nearer end.
Every query must bound the scan: `time BETWEEN at − tolerance AND at + tolerance +
lookup.max_session_seconds` (default 86400), so the `(transip, transport, time)` index range
stays small (an unbounded upper end scans every later chunk/partition). Interim rows count like
final rows. *Verify on site that FortiGate interim logs carry the cumulative `duration`.*

## 6. API (B5 and later packages)

- **App:** `create_app(config=None, engine=None, *, config_path=None)`. Endpoints go into
  `api/src/arcilogger_api/routers/<module>.py` (`router = APIRouter(prefix=…, tags=[…])`); every
  module there is mounted under `/api/v1` automatically — never edit `app.py`. With
  `config_path` (the fixed `--config` of `arcilogger-api serve`) the API reloads the file without a
  restart and finds the browser administration's folder next to it (section 16).
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
  `usage.person.view`, `seal.manual`, `search.run`, `search.event`, `search.refs`,
  `search.export`, `search.saved_create`, `search.saved_update`, `search.saved_delete`,
  `live.open`, `live.close`, `raw.tail`, …; section 17.9 lists the search details).
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
  `license` → offline licensing (section 15); `search` → Wave 2 WP-B, `live` and `sources` →
  Wave 2 WP-C (section 17).

### 6.1 5651 lookup, official report and raw lines (W2-A)

All need a session, the KVKK notice and `lookup.run` (raw lines: `search.read` **or**
`lookup.run`); today that is admin and auditor. Errors use the standard format.

| Endpoint | Does | Audit (`details`) |
|---|---|---|
| `POST /lookup/5651` | body `{"request": {authority, file_no, request_date, reason}, "query": {at, time_zone?, public_ip?, public_port?, protocol?, dest_ip?, dest_port?, dest_domain?, tolerance_seconds?, internal_ip?, mac?}}` → 201 `LookupOut` (`id`, `request`, `query`, `mode`, `result_count`, `outcome` = `LookupOutcome.to_dict()`, `report`, `elapsed_ms`) | `lookup.run` (`lookup_id`, `request` {authority, file_no, request_date, `reason_sha256`}, `query`, `mode`, `result_count`, `truncated`, `results` [{kind, score, traffic row ids, lease id, person id, alternatives}] ≤ 100; ADR-0016: `egress` `{resolution, names}` in machine form, `null` without a public IP) |
| `GET /lookup/{id}` | one lookup (`LookupOut`, `report` = stored report facts or `null`; `outcome` exactly as stored, so a lookup stored before ADR-0016 lacks the members of section 5, read as their defaults) | `lookup.view` (`lookup_id`, `own`) |
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
- **IPv6** (no NDP or DHCPv6 evidence is collected): a `public_ip` or `internal_ip` that is an
  IPv6 address (also an IPv4 address written as IPv6, `::ffff:a.b.c.d`) is refused before the
  lookup runs with 422 **`ipv6_not_supported`** (`details[].loc` names the fields; the TR/EN
  texts say that this version cannot look it up and the request must be answered by other
  means), audited as `lookup.run_failed` (`reason: ipv6_not_supported`, `query`, `request`); no
  `lookups` row is stored. `dest_ip` may be IPv6 (it only filters).
- **Numbers:** `{id}` and `cursor` are 1 … 2^63−1 (PostgreSQL `bigint`) in every router (also
  `/users/{id}` and the `/audit` cursor); larger values answer 422 `validation_error`, never a
  database error.
- **Time budget (spec 14):** setting `lookup.timeout_seconds` (default 5). Every statement of the
  lookup gets the **remaining** time as `statement_timeout` (`set_config(…, true)` on the
  cursor before each statement, `routers.lookup.statement_deadline`); past it → 504
  **`lookup_timeout`**, the attempt is audited as `lookup.run_failed` (`reason: timeout`) and no
  `lookups` row is stored. 503 **`lookup_settings_invalid`** when the stored `nat.egresses`
  cannot be parsed, for a public-IP request only (`settings.egresses()` is called inside the
  router's `try` only when `public_ip` is set; other lookups never parse it): "5651 sorgu
  ayarları geçersiz: İnternet çıkışları (nat.egresses) değeri okunamadı (Ayarlar → Uygulama
  ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)); sistem yöneticisi
  düzeltmelidir." / "The 5651 lookup settings are invalid: the internet exits value
  (nat.egresses) cannot be read (Settings → Application settings → 5651 lookup and lease
  timeline → Internet exits (NAT)); an administrator must fix it." The same 503 and text cover
  any other `TypeError`/`ValueError` of the settings read there (`LookupSettings.from_settings`,
  `lookup.timeout_seconds`).
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
  shown as "not sealed yet"). The double-NAT paragraph (variants: "Report changes of Wave 3"
  below) is followed by "the result is a candidate list" only in mode `candidates`; in mode
  `none` it says that no candidate was found either. Raw lines
  longer than 72 KiB (`texts.RAW_DISPLAY_CHARS`) are cut in the PDF with a warning; their hash
  and length describe the whole line. Long values (reason, raw lines) split across pages.
- **Report changes of Wave 3** (ADR-0016; `reports/render.py`, `reports/texts.py`,
  `reports/consistency.py`; docs/RAPOR.md). Everything is derived from the stored outcome, never
  from a setting at render time (a report is rendered on its first download).
  - **Exit row.** Section 4 of a public-IP report gains the row "Çıkış / Exit" with "{label TR} /
    {label EN} — {name} ({label}), {mode TR} / {mode EN}", one "{name} ({label}), {mode}" per exit
    of `outcome.egress.egresses` joined with "; " (the name alone for an exit without a label; no
    exit for `unconfigured` and `no_egress`; `render.exit_row`, `render.exit_label_key`); labels
    and mode names come from one TR/EN table shared with
    the web header line and the reading table (`texts.EXIT_LABELS`: `public` "Genel IP" / "Public
    IP", `transip` "NAT adresi" / "NAT address", `fallback` of a `direct` exit "Doğrudan hat
    doğrulanamadı" / "Direct line not confirmed", `fallback` of a `transip` resolution "NAT adresi
    doğrulanamadı" / "NAT address not confirmed", `observed` "Kayıtlarda görülen adres" /
    "Address seen in the logs", `public_unknown` "Genel adresi bilinmeyen hatlar" / "Lines with
    unknown public address", `unconfigured` "Çıkış tanımlı değil" / "No exits defined",
    `no_egress` "Çıkış yok" / "No exit"; `texts.EXIT_MODES`: "Doğrudan" / "Direct", "Servis
    sağlayıcı NAT'ı" / "ISP NAT", "FortiGate'ten geçmiyor" / "Does not pass the FortiGate",
    "İnternet çıkışı değil" / "Not an internet exit"). Every match row gains "Çıkış / Exit:
    {name} ({label}) · dstintf {dstintf}" from `NatMatch.egress` and the cited row ("-" when
    unclaimed). The outcome stores one exit per match, so a match of a session whose cited
    `fg_traffic` lines show more than one (`transip`, `dstintf`) while the outcome carries
    `session_changed_exit` adds "oturumun kayıtlarında ayrıca: {exits}" / "the session's records
    also show: {exits}" (the other exits named in the explanation) when the explanation counts
    one such session and only this match's lines vary, else "oturumun kayıtları birden çok çıkış
    gösteriyor: NAT {transip} · dstintf {dstintf}; …" / "the session's records show more than one
    exit: …" read from its raw lines (WP-D, "Deviations — d"). For `no_egress`, `not_logged`
    and an `internal` exit, sections 4 and 5 print "Oturum aranmadı: …" / "No session was
    searched: …" (the address and the exit) instead of the generic `none` texts.
  - **Method paragraphs.** The Yöntem / Method section prints one paragraph per (resolution,
    mode) under "İnternet çıkışı / Internet exit", eleven in all: `public` direct,
    `public`/`transip` isp_nat, `not_logged`, `transip` internal, `fallback` direct, `fallback`
    transip, the `fallback` addition when the search entered another exit's addresses,
    `observed` (the stored text of `egress_observed_not_configured`, or of
    `egress_not_logged_but_seen` when the answer names an exit), `public_unknown`,
    `unconfigured`, `no_egress` (the last with "Sorgu anında tanımlı çıkışlar: {configured}." /
    "Exits defined at the time of the lookup: {configured}.", each exit as "name (label): mode,
    public addresses", from the stored outcome). `{searched}` names the addresses actually
    searched (`egress.searched`); when `searched.unclaimed` is true it adds (or reads alone)
    "hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT adresleri (T çevresinde
    görülenler: {seen})" / "every private or shared source-NAT address no exit claims (seen near
    T: {seen})", "T" printed as the contract writes it and `{seen}` the private or shared ones of
    `searched.unclaimed_seen`; `{seen}` of the two `fallback` paragraphs is
    `searched.outside_seen`. The WP-D wording choices ("Deviations — d"):
    - the isp_nat paragraph says "Arama bu çıkışın NAT adresleriyle{ ve arayüzleriyle (…)}
      sınırlandı." / "The search was limited to this exit's NAT addresses{ and interfaces (…)}."
      only when the search stayed on the exit's own addresses; when it also covered the unclaimed
      ones (a catch-all exit, or the O5 widening) it says "Arama {searched} adresleriyle{ ve
      arayüzleriyle (…)} yapıldı." / "The search covered {searched}{ on the interfaces (…)}.",
      and the `public_unknown` paragraph adds the same sentence then; the interface clause only
      while the search kept its interface condition; a catch-all exit's `{transips}` reads
      "hiçbir çıkışa tanımlı olmayan özel ya da paylaşılan NAT adresleri" / "the private or
      shared NAT addresses no exit claims";
    - the direct `fallback` paragraph says "bu hatta" / "on this line" only when the exit
      declares interfaces;
    - the `fallback` addition has two variants: "Aranan adreslerin bir kısmı {other} çıkışına
      aittir: bu çıkış yükseltmede eski ayarlardan aktarıldı ve arayüz tanımlanmadığı için aynı
      hat sayıldı. Liste o hattın kullanıcılarını da içerebilir; her hattın arayüzleri girilince
      bu olmaz." when the failed exit declares no interfaces, and "Aranan adreslerin bir kısmı
      {other} çıkışına aittir: bu çıkış {exit} çıkışıyla aynı arayüzü ({interfaces}) paylaştığı
      için aynı hat sayıldı. Liste o hattın kullanıcılarını da içerebilir." / "Some of the
      searched addresses belong to exit {other}: it shares an interface ({interfaces}) with exit
      {exit}, so it was counted as the same line. The list may contain that line's users too."
      when it does;
    - the `no_egress` paragraph reads "… ve o sırada yapılandırılmış FortiGate kaynaklarının
      kayıtlarında da görünmüyor; …" / "… and does not appear in the records of the configured
      FortiGate sources at that time either, …".

    `source_clock_offset`, `egress_internal` and `egress_changed_after_event` (with what each
    changed exit holds now, or "silindi" / "deleted") are printed in the method section's
    explanation list; `egress_public_dynamic` is appended to the isp_nat paragraph of a `public`
    answer. Keys whose text a paragraph already quotes (`egress_observed_not_configured`,
    `egress_not_logged_but_seen`, `egress_public_dynamic`) are not repeated as bullets, nor the
    stored `double_nat` / `double_nat_no_candidates` beside a `fallback` paragraph; every other
    key stays a bullet (`render.py` module docstring). A chain with `fortigate_format_unverified`
    prints "FortiGate DHCP kayıt biçimi bu kurulumda sahada doğrulanmadı; kira kayıtları FortiOS
    belgelerindeki biçime göre okundu." / "The FortiGate DHCP record format has not been verified
    on site at this installation; the leases were read by the format of the FortiOS
    documentation.", one with `fortigate_format_verified` its stored text instead (each distinct
    stored text once, never both), once in the method section; neither key is repeated beside
    the chains.
  - **Double-NAT variants.** The double-NAT paragraph of a candidates or `none` answer with
    `double_nat` true has three variants and is never printed for resolution `transip`:
    configured `isp_nat` "Çift NAT: {exit} çıkışı servis sağlayıcı NAT'ı olarak tanımlı; …" /
    "Double NAT: exit {exit} is defined as ISP NAT; …"; assumed (`unconfigured`,
    `public_unknown`) "Çift NAT varsayımı: …" / "Double NAT assumed: …"; `fallback`: its method
    paragraph stands in for it. Every variant but `fallback` adds the CGNAT sentence ("Genel adres
    başka müşterilerle paylaşılıyorsa (CGNAT) aday listesi doğru oturumu içermeyebilir; …" / "If
    the public address is shared with other customers (CGNAT), the candidate list may not contain
    the right session; …"), then the result sentence and the advice: (a) the ISP's NAT record,
    (b) bridge mode with the exit defined as "Doğrudan" / "Direct", under "Çift NAT / Double
    NAT". `fallback` prints, under "Kesin eşleşme için / For an exact answer", the result
    sentence and (a) only: "Kesin eşleşme için (a) servis sağlayıcının ilgili zamana ait NAT
    kaydı (genel port → FortiGate çıkış portu) gerekir." / "An exact answer needs (a) the ISP's
    NAT record for that time (public port → FortiGate port).", for a failed `direct` exit with
    " ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması" / " or
    confirmation from the ISP that the line was in bridge mode on that date" before "gerekir";
    (b) is never printed for a `direct` exit or a `transip` fallback.
    The ISP modem is never stated as a fact. The variant and the CGNAT sentence are printed only
    when the request names a public IP (otherwise only the result sentence and the advice), and
    the stored `double_nat` / `double_nat_no_candidates` explanations are not repeated as bullets
    where the `fallback` paragraph is printed (WP-D deviations, contract "Deviations — d").
  - **srcmac rows.** A srcmac step prints the row "MAC adresi / MAC address" with "MAC {mac} ·
    MAC adresi (kısmi kanıt): FortiGate oturum kaydının srcmac alanı, {interface} arayüzü; …;
    kira kaydı yok" and "MAC address (partial evidence): srcmac field of the FortiGate session
    record, interface {interface}; …; no lease" (`texts.SRCMAC_STEP`, `{interface}` from the
    chain's `srcmac_only`); a
    `srcmac_mismatch` alternative is printed under "Olası diğer zincir (srcmac, kısmi kanıt)" /
    "Possible other chain (srcmac, partial evidence)" (the `ip_conflict` header stays for the
    others). A chain without a lease prints "Zincir kurulamadı: o anda bu iç IP adresi için kira
    kaydı yok." / "No chain: no lease covered this internal IP address at that moment."
  - **FortiGate lease row.** The lease row of an `fg_dhcp` lease (`source_table == "fg_dhcp"`
    only) adds "· kaynak: FortiGate DHCP kaydı · kira süresi (FortiGate kaydı): {lease} sn · son
    DHCP kanıtı: {last}" and, below it, "source: FortiGate DHCP record · lease time (FortiGate
    record): {lease} s · last DHCP evidence: {last}" (`lease_seconds`, `last_dhcp_evidence`;
    "ayarlardaki süre" / "configured time" when `lease_seconds` is null; `TABLES["fg_dhcp"]`,
    `TABLES_EN["fg_dhcp"]`). In section 6 the tables added by ADR-0016 are labelled in both
    languages, "FortiGate DHCP kaydı / FortiGate DHCP record" and "FortiGate olay kaydı /
    FortiGate event record" (`TABLES_EN` holds only these two); the older table labels stay
    Turkish only, as reports printed them. A chain at `low` through
    `lease_renewal_not_seen`, `fortigate_dhcp_not_converted_since_lease` or
    `fortigate_dhcp_no_network` prints that text beside its lease row (the second cites no
    `fg_event` line). Method item (2) names "MikroTik DHCP, FortiGate DHCP olayları" / "MikroTik
    DHCP, FortiGate DHCP events".
  - **Unconverted citation.** Each row of `outcome.unconverted_dhcp` is printed in section 6
    like every cited row (raw line, seal facts), marked "kira zaman çizelgesine girmedi; zincir
    kurulmadı, güven düzeyi verilmez" / "not in the lease timeline; no chain built; no confidence
    given", never as an attribution (`inputs.py` cites `outcome.unconverted_dhcp`); a candidate
    report carries only the `fortigate_dhcp_not_converted` text with the IPs.
  - **Untrusted senders and raw lines.** A match with `untrusted_sources` gets a "UYARI /
    Warning" row naming them. A cited session line of an interface with FortiGate device
    identification is printed as it is, with `srcmac` and the device identification fields,
    also for other people's candidate sessions and with `lookup.srcmac_interfaces` empty (D21;
    raw lines are never modified).
  - **Cross-check.** `fg_dhcp` lines are re-parsed and checked (`PARSER_OF_TABLE["fg_dhcp"] =
    "fortigate"`): a ref claims `ip` and `mac` like an `mt_dhcp` ref, except the line that ended
    a `superseded` lease (a DHCP ref with `time >= end`), which claims only the field it shares
    with the lease (`ip` when it names the lease's IP, `mac` when it names another IP, read with
    `refs.dhcp_ip`; a ref without a summary claims `ip`); a srcmac chain claims `{srcip,
    srcmac}` on its cited `fg_traffic` row (a mismatch names the field "kaynak MAC (srcmac)",
    `CHECK_FIELDS["srcmac"]`, and the check's paragraph in section 4 lists "srcmac zincirinde
    oturum kaydının kaynak IP ve srcmac alanı" / "source IP and srcmac of the session record of
    a srcmac chain"). `fg_event` lines (the unconverted citation) stay `not_checked`.
  - **Outcomes stored before the upgrade** (`egress` null, `TrafficInfo.dstintf` null,
    `LeaseInfo.source_table` null) print no exit row in section 4 or on their matches, no
    per-resolution method paragraph and no FortiGate lease additions; a `double_nat` answer
    prints the "Çift NAT varsayımı" variant with the CGNAT sentence, never the Wave 2 sentence
    that stated the ISP modem as a fact. Retired explanation keys keep their tone and text.
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
  `ToUnicode` maps). ADR-0016 (contract 3.5; the defaults write the same lines and rows as
  before):
  - `build_site(factory, layout, day=DAY, *, dhcp: Literal["mikrotik", "fortigate"] =
    "mikrotik", srcmac: bool = False, wan2: bool = False) -> Site`. `dhcp="fortigate"`: the
    leases rest on FortiGate DHCP ACKs (`fg_dhcp`, lease `FORTIGATE_LEASE` = 86400, through
    `Evidence.fortigate_dhcp`) instead of MikroTik DHCP rows: the PC at 10:42:18 and its renewal
    at 22:42:18 on `vlan42` (host `PLN-PC14`), the phone at 09:37:52 and 21:37:52 on `vlan60`
    (host `Telefon-12`); no `mt_dhcp` row, both `sources` rows kept. `srcmac=True`: every session
    row carries its client's `srcintf` and `srcmac` (the PC `vlan42` and `PC_MAC`, the phone
    `vlan60` and `PHONE_MAC`). `wan2=True`: one more phone session at 11:23:50 through `WAN2`
    (`100.64.13.9`, `dstintf` `wan2`), its row id in `Site.wan2` (`None` otherwise);
    `Site.dhcp_pc` / `dhcp_phone` are the `fg_dhcp` ids of the first ACKs with
    `dhcp="fortigate"`;
  - `unconverted_dhcp_line(evidence, time, ip, mac, *, hostname=None, lease=86400,
    interface="vlan42", source="fortigate", logid="0100026001", dhcp_msg=None) -> int`: a
    FortiGate DHCP ACK (a RELEASE with `logid="0100026002"`, `lease=None`) that stayed an
    `fg_event` row with every DHCP field in `extra` (the D8 citation);
  - `device_identified_traffic(evidence, time, srcip, srcport, dstip="203.0.113.10",
    dstport=443, *, srcintf="vlan42", srcmac=PC_MAC, identification=DEVICE_IDENTIFICATION,
    transip=WAN, dstintf=None) -> int`: a session line with `srcmac` and device identification
    fields (`srcname`, `osname`, `unauthuser`; fictional values), the D21 raw-line case;
  - `unknown_sender_traffic(evidence, time, srcip, srcport, dstip="203.0.113.10", dstport=443,
    *, sender=UNKNOWN_SENDER, transip=WAN, dstintf=None) -> int`: a session from
    `unknown-192.0.2.7`, a sender that is no configured source (`untrusted_sources`, D24);
  - `enter_exits(factory, value, *, at, by, changes=True) -> dict`: stores `nat.egresses`
    (normalised by `validate_egresses`) as entered by `by` at `at` and, with `changes`, the
    change record `nat.egresses_changes` as `change_setting` keeps it, so a second call at a
    later `at` is a change after the event.

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
  `alerts.notify` (1 min); `license.check` (5 min) opens and resolves the licence alerts
  (`R20-license`, `R20-license-limit`, `R20-license-maintenance`:
  `arcilogger_worker.alerts.license`, docs/ALERTS.md); see docs/CORRELATION.md section 4. Commands:
  `arcilogger-worker alerts test-email --to ADDRESS` and `arcilogger-worker alerts list [--open]
  [--limit N]` (exit codes as `run`: 0 ok, 1 not sent, 2 configuration, 3 database unreachable).
- **Lease evidence and internet exits (ADR-0016):** `alerts.system` also runs
  `check_lease_evidence` (`R20-lease:<cause>`) and `check_nat_exits` (`R20-nat:<cause>`), each in
  its own transaction; the causes, severities, dedup keys and details are listed in section 18.6
  (operator view: UYARILAR/ALERTS section 2).
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

ADR-0016: the usable FortiGate DHCP rows (`fg_dhcp` through `trust.kept`) are DHCP events of the
lease timeline like MikroTik DHCP rows, so their MACs and host names reach the device inventory
(`upsert_devices`) and leases with `source_table = fg_dhcp`, read under the existing permissions
(section 18.4). A srcmac chain names a device and its person at `low` without creating a lease or
a device sighting.

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
ADR-0016: FortiGate DHCP leases feed the subjects like every lease (no change in the usage
code); srcmac never feeds usage, and a bad `nat.egresses` never stops the rollups (they never
parse it).

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
route (5651 Temel: 103 routes, incl. the three of the licence and the 38 of the browser
administration, section 16) is never gated, in any state; the
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

## 16. Browser administration API (ADR-0014)

Decision: [ADR-0014](adr/0014-browser-administration.md). Base `/api/v1`; nothing here is
licence-gated (`api/tests/test_license_routes.py`, `test_license_5651.py`) and every change is
CSRF-checked (`api/tests/test_auth_security.py`). The API never writes `arcilogger.toml`,
`secrets.json` or the vault: a change of the machine configuration and an evidence action are a
request of the administration agent `ArciAdmin`, an Ed25519-signed file in `admin\inbox`
(`arcilogger_core.admin.envelope`); the test e-mail and the lease rebuild are worker rows of
`admin_requests` (`channel = 'worker'`). The channel's code is `api/src/arcilogger_api/
admin_channel.py`; its files come from the folder of the API's fixed configuration path
(`AppState.config_path`), never from a key of the configuration (ADR-0014 Day 0 note D0-2).
Without that path (a configuration given in memory) browser administration is off
(`admin_disabled`).

**Live reload** (`deps.AppState.require_config`): with `config_path` the API looks at the file
at most every `RELOAD_SECONDS` = 2 s and takes every key except `database`, `api`, `data_dir`,
`secrets_dir` (`configwatch.API_HOT`); the engine is never rebuilt; an invalid file keeps the
configuration in use; a file whose `secrets_dir` or `data_dir` is not the installation's folder
is not taken at all (the API keeps reporting the file it runs on, with the error).
`arcilogger-api serve` refuses such a file at start (`config.check_fixed_location`, exit 2).
`AppState.config_watch` is the API's `config` block in `GET /admin/status`.

**Writing a request** (`Channel.submit`, contract 2.5.1): after the endpoint's own checks (the
section rules `arcilogger_core.admin.rules` with the section's `etag`, the re-authentication,
the permission) browser administration must be on (`policy.json`, else 503 `admin_disabled`),
the API key readable (503 `admin_channel_key_missing`) and the agent's heartbeat younger than
120 s (else 503 `admin_agent_unavailable`, nothing queued; older than 30 s: queued with the
warning `agent_stale`); at most 20 requests and 100 MiB in the inbox (429 `admin_queue_full`);
one open configuration request per section (409 `admin_section_busy`; a held change keeps its
section busy until it is applied or cancelled, except a Zamane client held for the console's
`approve-jar`: it may wait for days, so it blocks only another client, and the agent checks it
again against the file in force when it is approved). One transaction then writes the audit entry
`admin.request` (target `admin-request:<id>`, details `kind`, `section`, `changes` (secrets only
`{secret, old, new, changed}`), `files [{kind, sha256, size}]`, `guarded`, `effective_at`,
`reauth_at`, `effects`, plus `day` of a seal request) and the `admin_requests` row (`params`:
the changes as a list of `{path, …}`, `effects`, `warnings`, `files`; `audit_id`; `expires_at`
= issued + 10 min), and commits; only then `<id>.req.tmp` is written, flushed and renamed to
`<id>.req`. A failed write marks the row `failed` (`write_failed`) and answers 503
`admin_inbox_write_failed`. The signed payload's `actor` is the user, the session reference
(first 12 hex digits of the token hash), the client address (`0.0.0.0` when unknown) and the
last re-authentication. Answer **202 `AdminAccepted`** `{request_id, short_id, state: "queued",
status_url, guarded, effective_at, changes, effects, warnings}`.

**Reading a request** (`Channel.view`): the agent's `admin\status\<id>.json` (codes only) and
the row; the API decides `waiting_for_agent` (queued > 60 s or heartbeat not fresh), `expired`
(not taken by `expires_at` + 2 min: the inbox file is removed), and for `applied` the services:
each `reload.*` effect is `done` when the service's status file (`state\<service>-status.json`,
younger than 2 min = running; the API: its own watch) reports the new SHA-256 (or a file loaded
after the change), `skipped` when it does not run, `warning` `service_not_reloaded` after 2 min;
`system.firewall` / `system.time` follow `admin\system\status.json` (`applied_config_sha256`,
for a sources change also the collector's acknowledgement; `failed` `system_sync_failed`, or
`system_sync_timeout` after 5 min; `skipped` with `firewall_not_managed`). All done or skipped:
`active`; a warning or failure: `partially_active`. Every state seen is mirrored into the row
(`state`, `terminal`, `error_code`, `result`, `rolled_back`, `effective_at`); once terminal, the
row's state stands. Texts: `admin_channel.texts(code)` (the API's `TEXTS`, then
`arcilogger_core.admin.messages`); state labels `messages.STATE_LABELS`.

| Endpoint | Access | Does |
|---|---|---|
| `GET /admin/config` | `settings.manage` | 200 `AdminConfig` (below) |
| `GET /admin/status` | `settings.manage` | 200 `AdminStatus` (below) |
| `POST /admin/config/general/check` | `settings.manage` | dry run of the `PUT`: 200 `AdminCheck`, nothing queued |
| `PUT /admin/config/general` | `settings.manage`; re-auth for a clock change | `{etag, site_name?, clock?: {ntp_server?, fallback_server?, alert_offset_ms?}, probe = true}` → 202 |
| `POST /admin/config/tsa/check`, `PUT /admin/config/tsa` | `settings.manage`; re-auth for all but the threshold and the time limit | `{etag, server?: test \| production, credit_alert_threshold?, timeout_seconds?, java?, customer?, password?, clear_credentials?, confirm? (production to test), probe = true}` → 200 `AdminCheck` (with `readiness`) / 202 |
| `POST /admin/uploads?kind=kamusm_root\|zamane_client\|smtp_ca` | `settings.manage` | `multipart/form-data`, field `file` → 201 `UploadPreview` (below); 422 `upload_kind_invalid`, `root_certificate_invalid`, `zamane_archive_invalid`; 413 `upload_too_large` |
| `POST /admin/config/tsa/roots` | `settings.manage`; re-auth | `{etag, upload_id, legal = false, confirmed = false}` → 202 (`kamusm-roots\<sha256>.pem`; `legal` also adds it to `tsa.legal_root_fingerprints` and needs `confirmed`: 422 `legal_root_confirm_required`) |
| `POST /admin/config/tsa/legal-roots` | `settings.manage`; re-auth | `{etag, sha256, confirmed}` → 202 (an installed root made a legal root) |
| `POST /admin/config/tsa/zamane` | `settings.manage`; re-auth | `{etag, upload_id, expected_sha256?}` → 202, held (`guarded`, `effective_at` = now + `policy.jar_hold_hours`) unless a known release; 422 `zamane_sha256_required` / `zamane_sha256_mismatch` for another one |
| `POST /admin/config/smtp/check`, `PUT /admin/config/smtp` | `settings.manage`; re-auth | `{etag, enabled?, host?, port?, security?: starttls \| ssl \| none, username?, from_address?, timeout_seconds?, password?, clear_password?, test_to?, probe = true}` (a text field sent as null or empty is removed) → 200 `AdminCheck` / 202 |
| `POST /admin/config/smtp/ca` | `settings.manage`; re-auth | `{etag, upload_id, test_to?, password?, probe = true}` → 202 (`smtp.ca_file`; with a user name the password again, else 422 `secret_rebind_required`) |
| `DELETE /admin/config/smtp/ca?etag=` | `settings.manage`; re-auth | optional body `{test_to?, password?, probe = true}` → 202 (back to the system's trust store) |
| `POST /admin/config/replica/check`, `PUT /admin/config/replica` | `settings.manage`; re-auth | `{etag, enabled?, target?, user?, password?, clear_password?, probe = true}` → 200 / 202 (switching it off or moving it is held for `policy.hold_hours`) |
| `POST /admin/config/retention/check`, `PUT /admin/config/retention` | `settings.manage`; re-auth | `{etag, enabled?, days?, confirm_delete_days?}` → 200 `AdminCheck` with `retention_preview` / 202 |
| `POST /admin/config/backup/check`, `PUT /admin/config/backup` | `settings.manage`; re-auth | `{etag, enabled?, keep_daily?, keep_weekly?, keep_monthly?, min_free_gb?}` → 200 / 202 |
| `POST /admin/config/sources/check` | `sources.manage` | `{etag, source, operation: add \| update}` → 200 `AdminCheck` |
| `POST /admin/config/sources` | `sources.manage` | `{etag, source: SourceIn}` → 202 |
| `PUT /admin/config/sources/{name}` | `sources.manage`; re-auth to switch one off | `{etag, source: SourceIn}` (the name cannot change: 422 `source_rename_not_allowed`) → 202 |
| `DELETE /admin/config/sources/{name}?etag=&confirm=<name>` | `sources.manage`; re-auth | → 202; 422 `source_confirm_mismatch` |
| `POST /admin/actions/tsa-test` | `settings.manage` | `{confirm_credit: true}` (else 422 `tsa_test_confirm_required`) → 202 (`action.tsa_test`); 429 `admin_rate_limited` within 5 min, `tsa_test_limit` after `policy.tsa_test_per_day` per site day |
| `POST /admin/actions/replica-test` | `settings.manage` | `{}` → 202 (`action.replica_test`, `{ok, message}`); 429 `admin_rate_limited` within a minute |
| `POST /admin/actions/test-email` | `settings.manage` | `{to}` → 202 (`worker.test_email`, `{ok, to, smtp_code, error_text}`, the address masked); 422 `test_email_recipient_not_allowed`, 409 `smtp_not_configured`, 429 `admin_rate_limited` after five an hour |
| `POST /admin/actions/lease-rebuild` | `settings.manage` | `{from, to}` → 202 (`worker.lease_rebuild`, `{done_days, total_days, failed_days}`); 422 `lease_rebuild_range` |
| `GET /admin/config/versions` | `settings.manage` | `[ConfigVersion {n, at, request_id, actor, paths, has_secrets}]`, newest first, at most the 20 kept |
| `GET /admin/diagnostics` | `settings.manage`; re-auth | the support ZIP (below), audited `admin.diagnostics {files}` |
| `GET /admin/requests?kind=&cursor=&limit=50` | `settings.manage` | `AdminRequestPage {items: [AdminRequestOut], next_cursor}`, newest first (cursor: the last id; `limit` 1-200) |
| `GET /admin/requests/{id}` | `settings.manage`; `seal.manage` for one's own `action.seal` / `action.credit` | `AdminRequestOut`; 404 `admin_request_not_found` |
| `DELETE /admin/requests/{id}` | `settings.manage`; re-auth for a held change | cancel → 202 (`state: cancelled` for a request the agent had not taken; a held change: the signed `config.cancel` to follow); 409 `request_not_cancellable` |
| `POST /admin/requests/{id}/revert` | `settings.manage`; re-auth | "Geri al" → 202 (`config.revert`, the changes the other way round); 409 `request_not_revertible` |
| `POST /seals/{day}/seal` | `seal.manage` | "Şimdi mühürle" → 202 (`action.seal`, `params.day`) |
| `POST /credits/query` | `seal.manage` | "Kontör sorgula" → 202 (`action.credit`); 429 `admin_rate_limited` within a minute |
| `GET /settings` | `settings.manage` | `SettingsOut` (below) |
| `PATCH /settings` | `settings.manage`; re-auth for `REAUTH_KEYS` | `SettingsPatch {changes: [{key, old, value}]}` → `SettingsPatchResult {changed: [{key, old, new}], unchanged: [key]}` |
| `POST /settings/{key}/reset` | `settings.manage`; re-auth for `REAUTH_KEYS` | the shipped default → `SettingsPatchResult` |
| `GET /settings/fortigate/observed?hours=24` | `settings.manage` | NAT addresses and srcmac interfaces seen in the FortiGate logs (below; ADR-0016) |

**A change of a section** (`routers/admin_config.py`): a field left out of the body stays as it
is. The `etag` (`"<section>:<sha256 of the canonical editable values>"`, `rules.etag`) must
equal the section's in the file now, else 409 `config_conflict` with `details.current_etag`.
The file must load and keep the installation's folders (else 409 `config_unreadable`). The
rules refuse with the first problem's code and status (`rules.HTTP_STATUS`, else 422);
`details` is the list of `{field, code, message_tr, message_en}` (field: the configuration path,
e.g. `clock.ntp_server`, `sources.<name>.address`, or the body field such as `test_to`), or the
first problem's facts (`details.missing` of `tsa_not_ready`, `details.causes` of
`tsa_would_break`); never a submitted value. Such a refusal is audited `admin.refused`
(target `admin-config:<section>`, `{kind, section, codes}`) in a transaction of its own. A body
that changes nothing is 422 `config_unchanged`. A change the rules mark is re-authenticated
(403 `reauth_required`). `AdminCheck {changes, effects, warnings: [{code, message_tr,
message_en}], guarded, effective_at, reauth_required, retention_preview?, readiness?}`.

`AdminConfig {config_sha256, etags: {section: etag}, sections: AdminSections {general:
GeneralSection {site_name, clock: ClockSection}, tsa: TsaSection {provider, server: test |
production | null, credit_alert_threshold, timeout_seconds, java, java_choices (the agent's),
zamane: ZamaneClient {present, sha256, release, trusted: installer | known | approved | false,
staged: [StagedJar {sha256, effective_at}]}, roots: [RootCertificate {sha256, subject,
not_after, legal, source: installer | web}], secrets: {name: SecretMeta {set, changed_at,
changed_by}}, readiness: Readiness {production, missing}, seal_ready: SealReady {ok, causes}},
smtp: SmtpSection {enabled, host, port, security, username, from_address, timeout_seconds, ca:
{sha256}, secrets}, replica: ReplicaSection {enabled, target, user, secrets}, retention:
RetentionSection {enabled, days, minimum_days: 730, preview: RetentionPreview {cutoff,
days_to_delete, oldest, newest}}, backup: BackupSection {enabled, keep_daily, keep_weekly,
keep_monthly, min_free_gb}, sources: [SourceOut {name, type, address, heartbeat_minutes,
expected, transport, enabled, firewall: open | closed | pending | not_managed, last_seen}]},
locked: [LockedPath {path, reason}] (`rules.locked_paths()`), policy: AdminPolicyOut {enabled,
source_networks, min_prefix_v4, max_sources, hold_hours, jar_hold_hours, tsa_test_per_day},
pending: [PendingChange {request_id, section, effective_at}], problems: [SetupProblem {level,
key, message_tr, message_en}] (`config.setup_problems`, and `arcilogger.toml` when the file is
not usable: then the sections show the API's configuration), restart_required}`. A secret is
never a value: `set` from the names of `secrets.json`, `changed_at` / `changed_by` from
`admin\status\secret-meta.json`. A source's `firewall` and the strict outbound mode come from
ApplySystem's `admin\system\status.json` (before its first report the firewall counts as
managed: an enabled source is `pending`); with browser administration off (`policy.json`)
ApplySystem keeps no rule and never reports, so every source is `not_managed`, whatever an
older report says. The API never opens `install\` (contract 2.2: NT SERVICE\ArciApi has no
right there). A file or folder the API finds but cannot read (on Windows: no right for NT
SERVICE\ArciApi) is never a 500: it is a `warning` problem with the path as `key` and the text
of `admin_file_unreadable`, and what depends on it is left out.

`AdminStatus {agent: AgentOut {state: ok | stale | missing | disabled, version, heartbeat_at,
queue, pending, java_candidates, seal_ready, system_task}, system: SystemOut {state: ok |
error | never | stale, at, applied_config_sha256, firewall, time, code}, services: {ArciCollector
| ArciIngest | ArciWorker | ArciApi: ServiceConfigOut {sha256, loaded_at, error, restart_keys,
running}}, config_sha256, config_origin: browser | outside, restart_required}`.
`config_origin` is `browser` when the file's SHA-256 is the `to` of the newest applied line of
the agent's journal, else `outside` (a hand edit, a console restore, or no browser change yet).

`AdminRequestOut {id, short_id, kind, section, state, terminal, label_tr, label_en,
requested_by, created_at, updated_at, effective_at, version: RequestVersion {number, from, to} |
null, steps: [RequestStepOut {step, state, at, code, message_tr, message_en}], changes, result
(the action's: `{day, status, message, serial, gen_time}`, `{credits, threshold,
below_threshold}`, …), error: RequestError {code, message_tr, message_en, field} | null,
rolled_back, warnings, manual_steps_tr, manual_steps_en}` (for `needs_attention` the manual
steps name `versions restore <n>` with the installation's interpreter,
`& "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe" -I -m arcilogger_core.admin`, since
the programs are not on PATH; for `acl_drift` they name `Set-ArciVaultAcl.ps1`, as the change
is in force). An apply after which ArciAdmin can no longer write a configuration file is
`applied` with the warning `write_access_lost` (journal `applied`, code `acl_drift`, a critical
`R20-admin`).

**Uploads** (contract 2.4.4): the file goes to `admin\inbox\uploads\<id>.bin` (`.tmp` then
rename) with `<id>.json` (`UploadMeta {id, kind, sha256, size, created_at, user_id}`), is
inspected for its kind (`rules.inspect_upload`; one that is not removed again: 422) and audited
`admin.upload {kind, sha256, size}`; it counts toward the inbox quota. It is usable for 30
minutes and only by the user who sent it (else the request is refused with 404
`upload_not_found`); the request's `files` list `{kind, sha256, size, jar_sha256?}`. Limits
`contract.UPLOAD_LIMITS` (root 64 KiB, SMTP CA file 256 KiB, Zamane client 25 MiB). `UploadPreview
{upload_id, kind, size, sha256, expires_at, certificate?: CertificatePreview {subject, issuer,
not_before, not_after, is_ca, self_signed, key, installed, warnings}, zamane?: ZamanePreview
{kind: jar | zip, jar_name, jar_sha256, release, known, accepted, reason: unknown_jar_held |
null}}`.

**Kamu SM** (`PUT /admin/config/tsa`): the customer number and password go to the agent only as
Fernet tokens of the installation's `secret.key` (`SecretStore.encrypt_value`) inside the signed
request; a response, `admin_requests`, the audit log and the API's log never hold them (a dry
run shows `{secret, old, new, changed}` only). Only Kamu SM's test and production servers can be
chosen (422 `tsa_mock_refused`, `tsa_server_invalid`); a switch to production needs everything
`readiness.missing` lists (409 `tsa_not_ready`, `details.missing`) and a switch that would stop
the Seal task is 409 `tsa_would_break` (`details.causes`, `seal_preflight`). With new
credentials or a new server the agent first asks for the credit balance with the candidate
values (`probe`). A Zamane client that is not a known release waits (`pending`) until an
administrator runs `python -I -m arcilogger_core.admin approve-jar <sha256>` on the
console; time alone never makes it
runnable.

**Log sources** (`SourceIn {name, type: fortigate | mikrotik | generic, address,
heartbeat_minutes = 15, expected = true, transport: any | udp | tcp = any, enabled = true}`):
one source per request; the address must lie in `policy.source_networks` (422
`source_network_not_allowed`), be IPv4 (`source_ipv6_unsupported`), no reserved or own address
(`source_reserved_address`, `source_own_address`), a network not wider than
`policy.min_prefix_v4` (`source_prefix_too_short`), not taken (409 `source_address_taken`,
`source_overlap`, `source_name_taken`). Effects `reload.collector`, `reload.ingest`,
`reload.api`, `system.firewall`: ApplySystem narrows the syslog rule at once and widens it only
once the collector reports the new file (a new sender is let in after the collector knows it).
Switching a source off or removing it warns `source_disable_gaps` (the sealer no longer expects
it); its raw logs and sealed days stay.

**E-mail, replica, retention, backups** (contract 2.6): every change is re-authenticated. A
change of the e-mail server is first tried by the agent (connect, log in, a test e-mail to
`test_to`, which must be one of `alerts.email_recipients` or the user's own address: 422
`smtp_test_recipient_required` without one); a user needs the password again when the server,
port, security, user or CA file changes (`secret_rebind_required`); a password over an
unencrypted connection is refused (`smtp_plaintext_auth_refused`). The replica share is tried
the same way (`probe` `replica`; without a user the Requests task tests it afterwards). Its
target is saved, shown and expected as one text on every platform: `\\host\share[\folder]`,
backslashes and no trailing separator (`//NAS-01/worm/` is saved as `\\NAS-01\worm`). A
retention change that enables it or shortens it must name the number of sealed days it deletes
(`confirm_delete_days` = `retention_preview.days_to_delete`, else 422
`retention_confirm_mismatch`); the dry run without it shows that number. Retention never goes
under 730 days (`retention_below_minimum`). Switching the replica off or moving it and a
retention change that deletes are held for `policy.hold_hours` (state `pending`, `effective_at`).

**Worker actions**: the test e-mail and the lease rebuild are `admin_requests` rows with
`channel = 'worker'` (no file, no secret; they need neither the agent nor `policy.enabled`),
worked by the worker's job `admin.requests` (every 15 s). The row's `params` keep the address
for the worker; `admin.request` and the result hold it masked (`b***@ornek.example`). A test
e-mail not sent within 10 minutes is `expired`. The lease rebuild takes at most 31 days, not in
the future and not before the first DHCP or ARP record (`leases.first_evidence_time`).

**History** (`routers/admin_requests.py`, M4): a request the agent has not taken yet (`queued`,
`waiting_for_agent`; a queued worker row likewise, taken with `FOR UPDATE SKIP LOCKED` so a row
the worker is sending is 409) is cancelled by the API itself: its inbox file is removed (the
API's CREATOR OWNER right) and the row is `cancelled` at once. A held change
(`pending`) is cancelled by the agent: a signed `config.cancel` (`options.target`) after a
re-authentication; the target then reports `cancelled` and its staged files are removed.
Anything else is 409 `request_not_cancellable`; both ways are audited `admin.request_cancel
{state_before}`. A revert is a signed `config.revert` of an applied `config.apply` or
`config.revert` with a version: the agent builds the inverse from the version it saved before the
change (the paths that change touched, secrets as that version's ciphertext while it is one of
the 5 kept) and puts it through the same rules, without a connection test (the values were in
force before, so an e-mail server's revert needs no trial recipient); it keeps the target's
section busy. The versions
come from the agent's journal (`applied`/`recovered` lines with a version), since only the agent
reads `admin\versions`; `actor` is the requester's username.

**Diagnostics** (`GET /admin/diagnostics`): `README.txt`, `config/arcilogger.toml` (every
`password`/`secret`/`token`/`customer` value and every stored credential replaced by `***`),
`config/config-check.json`, `status/admin-status.json` (`AdminStatus`), the status files of the
agent, ApplySystem, collector, ingest and worker, the newest 50 `requests/<id>.json` (codes only)
and the last 200 lines of each service log under `logs\` with the same removals. No file of the
spool, the vault or the exports: no raw log line.

**"Şimdi mühürle"** (`routers/seals.py`): 409 `seal_day_sealed` (sealed), `seal_not_applicable`
(skipped, deleted, no data), `seal_not_due` (not `Sealer.closed_unsealed_days()[0]`, not
`failed`/`pending` on the calendar, or in the quiet window after midnight),
`seal_request_pending` (an open `action.seal` of that day); the agent signs a seal request for
the Seal task, whose result is the request's `result`.

**Database settings** (`routers/settings.py`, contract 2.13): `SettingsOut {groups:
[SettingGroup {id, label_tr, label_en, settings: [SettingOut {key, value, default, is_default,
type, min, max, reauth, locked, label_tr, label_en, help_tr, help_en, updated_by,
updated_at}]}]}` (the groups and texts of `arcilogger_db.settings_meta`; `updated_by` null for
the shipped value). `PATCH` is all or nothing: 422 `setting_unknown` (the key is not repeated),
`setting_locked` (`LOCKED_KEYS`, `details [{key}]`), `setting_invalid` (`details [{key, code,
message_tr, message_en}]` from `SettingValueError`), 409 `setting_conflict` (`details [{key,
current}]` when `old` is not the value in force), then 403 `reauth_required`; each change goes
through `settings_admin.change_setting(actor=username, actor_id=, ip=)` (audit `settings.change
{old, new}`; a reset adds `reset: true`); a value equal to the one in force is `unchanged` and
not written.

**NAT addresses and srcmac interfaces seen in the logs** (`GET /settings/fortigate/observed`,
`routers/settings.py`, ADR-0016; the "Kayıtlarda görülen NAT adresleri" / "NAT addresses seen in
the logs" table under the exit editor): `hours` 1-72 (default 24); `settings.manage`
(administrators), the permission of the whole settings router; **not audited** (counts and the
firewall's own addresses only, no client address or MAC). The expensive counts are cached
5 minutes per `hours` (`ObservedState`); what depends on the current settings (each row's
`egress`, `trandisp_known`, `unmapped_transips`, `unknown_trandisp`, `exits_invalid`, an
interface's `configured`) is computed from them on every request, so a saved exit shows at once.
`generated_at` is when the counts were made, so it can be up to 5 minutes old while the claims
are current (WP-D, contract "Deviations — d"). The counts
run under `statement_deadline` of 5 s; past it 504 **`fortigate_observed_timeout`**: "FortiGate
kayıtları {seconds} saniyede özetlenemedi; biraz sonra yeniden deneyin." / "The FortiGate
records could not be summarised within {seconds} s; try again a little later."

```json
{"generated_at": "…",
 "nat": {"hours": 24, "truncated": false,
         "rows": [{"transip": "100.64.13.9", "dstintf": "wan2", "trandisp": "snat",
                   "sessions": 18234, "first_seen": "…", "last_seen": "…",
                   "egress": null, "trandisp_known": true}],
         "unmapped_transips": ["100.64.13.9"], "unknown_trandisp": []},
 "srcmac": {"hours": 1, "truncated": false,
            "interfaces": [{"srcintf": "vlan42", "rows": 4012, "macs": 37,
                            "max_ips_per_mac": 1, "configured": true}]}}
```

NAT part: `GROUP BY transip, dstintf, trandisp` over the trusted `fg_traffic` rows of the last
`hours` with `transip IS NOT NULL` (no address-family filter), `ORDER BY count DESC LIMIT 100`
(`truncated`: more than 100 groups exist and the 100 busiest are listed); `egress` is the exit
that claims the row's `transip` as a public or NAT address by the current `nat.egresses`
(`internal` exits included, as `R20-nat:unmapped-transip` reads it; of several claiming exits the
one whose interfaces hold the row's `dstintf`, else the first by name; `null` when none),
`trandisp_known` whether `nat.snat_trandisp` lists the value, `unmapped_transips` the listed
addresses no exit claims (in row order), `unknown_trandisp` the listed values the setting does
not name (sorted). When the stored `nat.egresses` cannot be parsed, every row has `egress: null`
and `nat` carries `"exits_invalid": true` (a 200, never a 500; the panel points to
`R20-nat:invalid`); otherwise `exits_invalid` is `false` (the key is always present in the
answer; optional in the OpenAPI schema because it has a default). srcmac part: the last hour,
trusted rows with `srcmac` and `srcintf` set and an **IPv4** `srcip` grouped by `srcintf`
(`rows`, distinct `macs`, and the most distinct IPv4 `srcip` per MAC, the rows and count of the
lookup's guard, `trust.srcmac_ip_counts` with `interfaces=None`), busiest first; at most 50
interfaces are listed (`truncated` when more exist); `configured` is whether
`lookup.srcmac_interfaces` lists the interface. Budget (perf guard): < 2 s at 1 M rows a day over
24 h.

Captured answers: `web/e2e/samples/admin.json` (`kamusm`, `sources` and the refusals in
`errors`), `settings.json` (with the ADR-0016 keys and a 422 `nat.egresses` refusal),
`settings-observed.json` (with an `exits_invalid` answer), the seal now and credit query samples
in `seals.json` / `credits.json`.

## 17. Search, live tail and sources API (Wave 2, ADR-0015)

Decision: [ADR-0015](adr/0015-search-live-sources.md). Operator guide: [ARAMA.md](ARAMA.md) /
[SEARCH.md](SEARCH.md) (query language, fields, speed, statistics, export, live tail, measured
performance). Base `/api/v1`; nothing here is licence-gated (5651 Temel,
`api/tests/test_license_routes.py`, `test_search_permissions.py`). Captured answers:
`web/e2e/samples/search.json` (`web/scripts/samples_search.py`), `live.json` and
`sources.json` (`samples_live_sources.py`).

### 17.1 Query language, kinds, field catalogue

- **Grammar** (`arcilogger_api.search.grammar`, `parse_query(text) -> Query`): clauses ANDed,
  `-`/`NOT ` negate, a bare `AND` is ignored, `field:a,b` is OR inside a field, `field:*`
  present; `(`, `)` and a bare `OR` → `search_or_unsupported`; a word with `:` and no such field
  → `search_unknown_field` (hint `mac:` for a MAC, quote a URL or IPv6). Limits `LIMITS`:
  1,000 characters, 16 clauses, 20 values per clause, 5 free-text terms, 256 characters per
  value (`search_too_complex`). Hand-written and linear; raises only `SearchQueryError(code,
  start, end, **params)`; `Query.normalized` parses again to the same tree (property tests).
  Time is never in `q`.
- **Kinds** (`arcilogger_db.kinds`, shared by ingest, the planner, the matcher and the live hub):
  `kind_of(type, subtype)`, `KINDS` (labels, `messages` predicate, typed tables, `mt_event.event`
  values), `CLIENT_IP` (table → client column), `TYPED_TABLES`, `tables_of(kinds)`.
- **Field catalogue** (`arcilogger_api.search.fields.FIELDS`, built from `Base.metadata`
  columns): name, aliases, kind, scope (messages-only, typed, source/sender/type), columns per
  table, `indexed` tables; `GET /search/fields` serves it, `tests/test_search_docs.py` compares
  ARAMA.md/SEARCH.md with it. Values: `parse_value(field, raw)` (IP/CIDR/wildcard, int ranges and
  comparisons, protocol, MAC and vendor prefix, domain with punycode, tokens, ASCII
  case-insensitive text with `*` patterns, severity, parse error pattern, VLAN ids, departments,
  free text ≥ 3 characters). `SiteNetwork.load(session, config)` resolves VLANs, departments and
  network sources (`ap` also matches `ap-<ip>`).
- **ADR-0016** (section 18.7): the field `srcmac:` (alias `kaynakmac:`, `fg_traffic.srcmac`, not
  indexed); `fg_dhcp` rows are kind `event` (found with `mac:`, `ip:`, `interface:`, `logid:`,
  `level:`); the `dhcp` kind is labelled "DHCP (MikroTik)".

### 17.2 Planner and SQL

- `plan_query(query, site) -> Plan` (`search.planner`): a **typed** plan when the query has a
  typed field (one branch per table of the intersection of the fields' tables and the kinds
  `type:` allows, else `search_fields_incompatible`), otherwise one `messages` branch with the
  kind predicates. Messages-only conditions of a typed plan join `messages` by raw reference
  (`time, source, raw_day, raw_line`). `Plan.selective` (some branch has a positive,
  index-covered conjunct) decides only the notice `query_not_selective`;
  `Plan.rollup_eligible(from, to)` the statistics mode. Notices of the plan: `text_case`,
  `text_truncated`, `query_not_selective`, `department_by_vlan`.
- **Safe by construction** (`search.sql`): identifiers only from the catalogue; every value is a
  bound parameter (`CAST(… AS INET|CIDR|MACADDR)`, `LIKE`/`ILIKE … ESCAPE '\'` with `\ % _`
  escaped in Python); no `text()` with input (the only constants: `READ_ONLY` and the deadline's
  `set_config`). Negation is `NOT coalesce(<predicate>, false)`. Domains on `fg_web` use the
  expression of `ix_fg_web_rhost_time` (`reverse(lower(hostname))`, an exact arm plus the
  `[r + '.', r + '/')` range). `api/tests/test_search_sql_safety.py` checks compiled SQL for
  user values, the identifier allow-list, the lint rule, a refused write inside a search
  transaction and that every accepted query answers 200, a known 4xx or 504.
- **Read-only snapshot** (`routers/search.reading`): the authentication's transaction is
  committed, a gate slot taken, then `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ
  ONLY` opens one snapshot for every window, fill-in query and count of the request; each
  statement runs in a savepoint under `statement_deadline` (`routers/lookup.py`), so a timed-out
  window keeps the snapshot; the transaction is rolled back and the audit entry written in a
  fresh one, committed before the response.

### 17.3 Window scan, registry, cursor, gates

- `search.windows.run_page(session, plan, from, to, cursor, limit, budget_seconds)`: windows
  newest first, 1 h, doubling while a window took less than a quarter of the budget left,
  clipped to `from`; `LIMIT` = rows missing + 1; stop when the page is full, `from` is reached
  or `search.request_budget_ms` is used. A first window that times out is halved down to 5 min
  (each try gets half the budget left); 5 min timing out, or the budget used up before a
  halved try could run → 504 `search_timeout` (audited,
  `outcome: "timeout"`, `search_id: null`); a later window that times out ends the page with a
  frontier. After a `last` cursor the first window always runs, also when its time is `from`
  itself (rows still due at that instant); a budget gone before any window returns the same
  cursor. `scan {complete, scanned_from, scanned_to, windows, elapsed_ms}`.
- **Registry** (`search.registry`, in memory, one worker): `search_id` =
  `secrets.token_urlsafe(16)`; user, session token hash, plan, range, limit, `saved_id`; sliding
  TTL 30 min, 20 per user, 1,000 in total (least recently used dropped). Another session's or
  user's, an evicted or unknown id → 409 `search_expired`.
- **Cursor** (`search.cursor`): base64url of `{"v":1,"k":"last","t":<UTC µs>,"b":<rank>,"i":<id>}`
  or `{"v":1,"k":"frontier","t":…}`, at most 256 characters, parsed strictly (aware time inside
  `[from, to]`, a rank of the plan, `0 ≤ id < 2^63`, nothing else) → 422
  `search_cursor_invalid`. Per branch after `last (t, R, i)`: rank `< R` → `time < t`, `= R` →
  `(time, id) < (t, i)`, `> R` → `time <= t`; after `frontier T`: `time < T`. Pages concatenate
  to the full ordered list (`test_search_cursor.py`).
- **Gates** (`search.gate`): `search.max_concurrent` slots for search, page, statistics, refs
  and export, 2 per user, 2 s wait, then 429 `search_busy` (`Retry-After: 2`);
  `search.runs_per_minute` per user for `POST /search` (429 `search_rate_limited`,
  `Retry-After`); `search.max_days` (`search_range_too_long`).

### 17.4 Endpoints

Every endpoint needs `require_privacy_notice("search.read")` unless stated; bodies are strict
(`extra="forbid"`); all are POST except the catalogue, the drawer and the saved list, so no query
travels in a URL.

| Endpoint | Request → response | Audit |
|---|---|---|
| `POST /search` | `SearchRequest {q ≤ 1000, from, to, limit?: 1..search.page_size_max (default min(100, max)), saved_id?}` (with `saved_id`: the saved query and range) → `SearchPage` | `search.run` |
| `POST /search/page` | `{search_id, cursor, limit?}` → `SearchPage` | — |
| `POST /search/stats` | `{search_id}` → `SearchStats` | — |
| `POST /search/parse` | `{q}` → `ParseResult` (`search.read` only, no database) | — |
| `GET /search/fields` | → `FieldCatalogue` (`search.read` only) | — |
| `GET /search/event` | `?table=&time=&id=` → `SearchEvent` (unknown table or row: 404 `search_event_not_found`; a read that ran out of time: 504 `search_event_timeout`; `extra` display-safe) | `search.event` |
| `POST /search/refs` | `{search_id, limit: 1..10000}` → `SearchRefs` (+ `evidence.create`) | `search.refs` |
| `POST /search/export` | `{search_id, format: "csv" \| "xlsx"}` → file (+ `search.export`, re-authentication within 5 min) | `search.export` |
| `GET /search/saved` | → `{items: SavedSearch[]}` (own only) | — |
| `POST /search/saved` | `SavedSearchIn {name 1..80, q ≤ 1000, range: {preset} \| {from, to}}` → 201 `SavedSearch` | `search.saved_create` |
| `PUT /search/saved/{id}` | `SavedSearchIn` → `SavedSearch` | `search.saved_update` |
| `DELETE /search/saved/{id}` | → 204 | `search.saved_delete` |

Answers (`search.schemas`): `SearchPage {search_id, expires_at, query {normalized, clauses
[{field, op, values, negated, start, end}], text}, range {from, to}, plan {driver: "messages" |
"typed", tables, kinds, selective, rollup_eligible}, items: SearchRow[], next_cursor, scan,
notices}`; `SearchRow {key "<table>:<time>:<id>", table, time, time_received, source, sender,
kind, type, subtype, summary {srcip, srcport, dstip, dstport, natip, natport, action, domain,
url, app, category, user, mac, proto, policy, interface, vlan_id}, text (≤ 500, display-safe),
raw {source, day, line, offset}, parse_error}`; `SearchStats {search_id, mode: "rollup" |
"hybrid" | "direct", complete, covered_from, covered_to, total {value, exact}, histogram
{bucket_seconds, buckets [{start, count}]}, facets {source, kind, action, vlan, department:
{status: "ok" | "sampled" | "unavailable", values [{value, label_tr, label_en, count}], other}},
sampled, sample_rows, notices, elapsed_ms}`; `ParseResult {normalized, clauses, kinds, selective,
errors [{code, message_tr, message_en, start, end}]}`; `FieldCatalogue {fields [{name, aliases,
kind, label_tr, label_en, help_tr, help_en, tables, indexed, examples}], kinds [{key, label_tr,
label_en}], sources [{name, type, network}], limits {max_chars, max_clauses, max_values,
max_terms, max_days, page_size_max}}`; `SearchEvent {table, kind, fields, extra, message {type,
subtype, severity, facility, program, host, text (≤ 2000), parse_error} | null, raw}`;
`SearchRefs {refs, truncated, complete, unsealed_days, sha256}`; `SavedSearch {id, name, q,
range, created_at, updated_at, last_run_at}`; `Notice {code, message_tr, message_en, params}`
with the codes `vault_only_before` (`from` before the oldest `messages.time`, cached 10 min),
`counts_incomplete`, `text_case`, `text_truncated`, `query_not_selective`, `department_by_vlan`.

### 17.5 Statistics and the summary table

- `search_counts (hour, source, raw_day, kind, tbl, action, net, n)` (unique key `NULLS NOT
  DISTINCT`): one row per hour, file, kind, typed table (`''` without one), lowercased action
  and private IPv4 /24 of the client (`arcilogger_db.searchcounts`: `count_key`, `upsert_counts`,
  `rebuild_stream(conn, source, day)`, `counts_complete(conn, from, to)`, `verify`). Filled by
  ingest's `counts` step (section 4), by the worker job `search.counts_backfill` for streams with
  `ingest_offsets.counts_from_line` set, and by `arcilogger-db search-counts rebuild`.
  `partitions.drop_before` deletes the rows with `hour` before the first day of the cutoff's
  month, in its transaction (the month it keeps whole keeps its counts; with TimescaleDB it drops
  nothing, so the counts stay too; `PartitionReport.counts_deleted`).
- **Backfill job** `search.counts_backfill` (listed last in `default_jobs`): each file in its own
  transaction with its `ingest_offsets` row locked `FOR UPDATE` and read again (a second worker
  or the command never counts a file twice); at least one file per run, no new file after 60 s,
  but the file in hand is always finished (about 9 s per million lines here; the worker's other
  jobs wait for it). Details `{files: [{source, day, lines, keys}], lines, waiting}` plus `more:
  true, left` when the time ended the run; `skipped` when nothing was counted and nothing is left.
- **Command** `arcilogger-db search-counts`: `status` (keys, lines, hours held, the files still
  marked with their lines not counted), `rebuild --source S --day YYYY-MM-DD` (locks the file's
  `ingest_offsets` row first, so a file still being loaded can be recounted; clears
  `counts_from_line`), `verify [--hours 2]` (0–744; one `REPEATABLE READ` read-only snapshot;
  prints the mixed-case name counts of D6 every time; on a difference lists at most 20 keys and
  the `rebuild` commands that repair them). Exit codes as the other commands: 0, 1 a difference,
  2 a usage error (a bad day), 3 the database cannot be reached.
- `search.stats.run_stats(session, plan, from, to, labels, budget_seconds, sample_rows)`:
  **rollup** when `Plan.rollup_eligible` (only `source`, `type`, `action`, `vlan`, `department`
  and negations, no free text, VLAN subnets private IPv4 ≤ /24, range > 12 h) and
  `counts_complete`; **hybrid** when the range has partial edge hours (counted directly);
  **direct** otherwise, windowed under `search.stats_budget_ms` (a cut count is `complete:
  false` with `covered_from`, never 504). Buckets: ≤ 2 h 1 min, ≤ 12 h 5 min, ≤ 3 days 1 h,
  ≤ 14 days 3 h, else 6 h, aligned with `date_bin` to `2000-01-01 00:00+03:00`. Facets (top 10
  plus `other`) in one `GROUPING SETS` query; direct facets over the newest
  `search.facet_sample_rows` matches (`sampled`); a `messages` plan counts only source and kind
  (`unavailable` for the others). VLAN of a /24: the most specific VLAN holding it, `multiple`
  ("Birden çok VLAN") when smaller VLANs lie inside it. Rollup and hybrid equal direct counting
  (`test_search_stats.py`, `test_search_oracle.py`).

### 17.6 Saved searches, export, evidence references

- **Saved** (`search.saved`): personal; `search.saved_max_per_user` (409
  `saved_search_limit`); names unique per owner ignoring case (409 `saved_search_name_taken`),
  without control, format or surrogate characters (422 `validation_error`);
  another user's id 404 `saved_search_not_found`. Presets `15m`, `1h`, `24h`, `today` (since
  site midnight), `yesterday`, `7d`, `30d`, resolved when the search runs; a run sets
  `last_run_at`.
- **Export** (`search.export`): the plan's rows by the window loop in chunks until
  `search.export_max_rows` or `search.export_budget_seconds`, the fill-in queries under the
  budget left (then `truncated` = `rows` | `time`, a fill-in timeout too, never empty columns:
  file name `arama-<from>-<to>-kismi.<ext>`, header `X-Arci-Export-Truncated`, the "Bilgi"
  sheet). CSV: UTF-8 BOM, `;`, CRLF, `csvfiles.neutralise` on every cell; XLSX: write-only,
  every cell text, sheets "Sonuçlar" and "Bilgi" (query, range, user, time, rows, status and
  "Bu dosya mühürlü değildir; bütünlük için Kanıt Paketi'ni kullanın."). Built in a
  `SpooledTemporaryFile` (16 MB), hashed, audited and committed before it is streamed with
  `Content-Length`, `Repr-Digest: sha-256=:…:` and `Cache-Control: no-store`. One export per user
  (409 `search_export_running`); `search.export_max_per_hour` counted from `audit_log` (429
  `search_export_rate_limited`, `Retry-After`).
- **Refs** (`search.refs`): the raw references of the whole search up to `limit` (≤ 10,000, the
  evidence extract maximum), `truncated`, `complete` (the statistics budget), `unsealed_days`
  (days without a vault manifest), `sha256` of the canonical JSON list (days as ISO dates). The
  web hands them to the evidence wizard; `POST /evidence` is unchanged.

### 17.7 Live tail (`routers/live.py`, `live/`)

- Lines come from the **spool** (`rawtail.py`: `line_anchor`, `tail_lines`, `Follower`), never
  from the database: one hub per API process (`live/hub.py`, on `app.state`) polls today's file
  of the wanted folders every 0.5 s and lists `spool/` every 5 s (network members and new
  `unknown-<ip>` folders join at once), reads at most 1 MiB per file per poll (more than 8 MiB
  behind: skipped and counted `behind`), follows the day change at site midnight, parses with
  `arcilogger_ingest.prepare.prepare_line` within `live.parse_lines_per_second` and filters with
  `search/matcher.py` (the planner's semantics, property-tested). Buffer `live.buffer_lines` with
  a sequence number and an `epoch`.
- `POST /live/streams` (`require_privacy_notice("search.read")`, CSRF) `{q, backfill?}` (`backfill`
  absent or `null`: `live.backfill_lines`; 0..200 by the schema, above the setting 422
  `validation_error`) → 201 `{stream_id, epoch, expires_at, folders, limits}` (422 query
  errors first, then 429 `live_stream_limit`, 503 `live_unavailable`), audited `live.open`; the
  endpoints are `async` (the hub lives on the event loop), their database work runs in the
  thread pool, and the GET holds no database connection while it streams;
  `GET /live/streams/{id}` (the user's and session's own, else 404 `live_stream_not_found`) →
  `text/event-stream` (`hello`, `line` with `id: <epoch>:<seq>`, `dropped` at most once a
  second, `gap`, `end`, a keep-alive comment every 15 s; `Last-Event-ID` resumes);
  `DELETE /live/streams/{id}` → 204, audited `live.close` (also on `end`, lingering out and
  shutdown; `reason` `closed`, `lingered`, `server_stopping` or the `end` code);
  `GET /live/status` (`search.read`, no person data).
- **Resume:** the first connection of a stream always gets its backfill and ignores a
  `Last-Event-ID` (nothing of the stream was sent yet); the oldest sequence number sent then
  (`first_seq`: the oldest backfill line, else the one after the stream opened) bounds every later
  resume, so `backfill` holds and `dropped`/`live.close` count only the stream's own lines.
  `gap.missed` is the number of matching lines counted as lost while the stream was paused,
  `null` for `hub_restarted` and when lines between `Last-Event-ID` and the pause left the buffer
  unseen; `dropped` counts since the previous `dropped` event.
- **Parse budget:** a seeded file end is parsed newest first; lines the budget left unparsed that
  the backfill could hold are counted `parse_budget` (never hidden); a line no open stream can
  match (free-text byte search) is kept unparsed, and a stream opened later whose terms occur in
  it counts it as `parse_budget`. A stream judges only the lines of the folders it reads.
- Limits: `live.max_lines_per_second` per stream (token bucket; the rest `rate_limited`), a
  queue of 500 (`slow_client`; full for 30 s ends the stream, and so does one write that waits
  30 s because the client stopped reading), `live.max_streams_total` and
  `live.max_streams_per_user` (checked and the stream registered with no `await` in between, so
  concurrent opens never overshoot), `live.max_minutes` (`end time_limit`); a paused stream
  lingers 120 s. Every 30 s the stream checks the session (`security.session_problem`: every code
  the authentication refuses with — `session_invalid`, `session_revoked`, `session_expired`,
  `session_idle`, `user_inactive`, `mfa_required` — plus `permission_changed`,
  `password_change_required`, `privacy_notice_required`), `search.read` and the KVKK notice; the
  GET and its reconnects send `X-Arci-Background: 1`. Caddy does not compress `/api/v1/live/*`;
  the API waits at most 5 s for open connections on shutdown (`timeout_graceful_shutdown`), and
  an open stream ends with `end server_stopping`.

### 17.8 Sources and the Panel box (`routers/sources.py`, `sourcehealth.py`)

- `GET /sources` (`sources.read`, counts only, cached 15 s) → `SourcesOverview {generated_at,
  today, collector, items: SourceHealth[]}` with the state (`active`, `silent`, `never`,
  `disabled`, `unknown`, `promoted`; the R15 definition), `messages_per_minute`, a 60-minute
  `sparkline`, `lines_today`, `pending_bytes` (the unloaded bytes of today's, yesterday's and any
  older unfinished spool file), parse failures, `suggested` for unknown senders
  (the kinds and the most common host of the sender's newest 1,000 messages of the last 24 h).
  Items by name; a configured network is followed by its members (both `configured: true`, the
  members with `network_of`; the network sums them and is `active` when a member is).
- **`lease_evidence`** (ADR-0016; the "Kira kanıtı" / "Lease evidence" card): `SourcesOverview`
  gains the required, never-null member

  ```json
  {"hours": 24,
   "mt_dhcp": {"rows": 1234, "last_hour": "…", "leases_current": 310},
   "fg_dhcp": {"rows": 0, "last_hour": null, "leases_current": 0, "refused_rows": 0,
               "excluded_rows": 0, "format_verified": false},
   "mt_arp": {"rows": 560, "last_hour": "…", "leases_current": 42},
   "srcmac": {"interfaces": [], "rows_last_hour": null},
   "state": "ok | none | refused", "none_tables": []}
  ```

  part of the cached overview, under `sources.read` (administrators), not audited; counts, times,
  interface names and the format state only, never a client MAC, IP or host name.
  - `rows`: the **usable** rows of the last 24 h (`mt_dhcp` `assigned`/`deassigned`, `mt_arp`,
    `fg_dhcp` through `trust.kept`; primary-key time ranges), not `search_counts`, which counts
    refused and unknown-sender rows too. `last_hour`: the time of the newest usable row within the
    last hour, `null` when none arrived in it. `leases_current`: the leases of that
    `source_table` open or ended in the last 24 h (`ix_leases_source_table_end`), for
    information.
  - `fg_dhcp.refused_rows`: the `fg_dhcp` rows of the last 24 h that rules 1 and 4-7 refuse
    (`trust.refusal` without `trust.CONFIGURATION`; untrusted senders' rows included), counted
    only while `dhcp.fortigate_logids` is not empty (the gate of `R20-lease:fortigate-refused`:
    with FortiGate evidence off every row would be refused);
    `excluded_rows`: those of the excluded-interface and IPv6 rules, shown as an information line
    ("hariç tutulan arayüzler: N kayıt" / "excluded interfaces: N records"), never as refused;
    `format_verified`: `dhcp.fortigate_format_verified`.
  - `state`, in this order: `refused` while `fg_dhcp.refused_rows > 0` (the live count, same
    predicate and 24-h window as `R20-lease:fortigate-refused`, so it clears as soon as the
    setting or source is corrected and the leases rebuilt; it **wins over `none`**: the records
    do arrive); else `none` while an `R20-lease:none:<table>` or `R20-lease:absent` alert is
    active (read from the active alerts, up to 5 minutes behind the check); else `ok`.
    `none_tables` lists the tables of the active `none` causes in card order (`mt_dhcp`,
    `fg_dhcp`, `mt_arp`; empty for `absent`) **whatever the state**, so it is filled under
    `refused` too; the web shows it only for `none`.
  - When the `dhcp.fortigate_*` values cannot be read (written around the validator,
    `LookupSettings.from_settings` refuses them), the member is still returned (never null):
    `fg_dhcp.rows` and `last_hour` count the rows of trusted senders (the one rule that needs no
    setting), `refused_rows` and `excluded_rows` are 0, `format_verified` is the stored value read
    as a boolean, and the state comes from the alerts only; a warning is logged.
  - `srcmac {interfaces, rows_last_hour}`: `interfaces` = `lookup.srcmac_interfaces`;
    `rows_last_hour` counts the `fg_traffic` rows of trusted senders with `srcmac` of the last
    hour whose `srcintf` is one of those interfaces (any address family), computed only when
    interfaces are configured, kept 60 s for the same interface list (a changed list recounts),
    in a savepoint under its own 2-s `statement_timeout` (`null` on a timeout, which is not
    kept, and without interfaces).
  - Budgets (perf guards): < 0.5 s for the counts, < 1 s for the srcmac count. Sample
    `sources.json` holds the states `ok`, `none` with `none_tables` and `refused`.
- `GET /sources/{name}` (cached 30 s) → `SourceDetail` (+ `hourly` 48 h from `search_counts`
  when complete, `daily` 14 days, `gaps` 30 days from the manifests of sealed days and, today, the
  open gap of an enabled, expected source silent longer than `[seal] gap_minutes`,
  `parse_failures` 7 days; a pattern's `error` only for `search.read` or `lookup.run` with the
  current KVKK notice accepted, else `null`; not audited); unknown → 404 `source_not_found`.
- `GET /sources/{name}/tail?limit=50` (≤ 200; `sources.read` and `search.read` or `lookup.run`
  and the KVKK notice; 30 a minute, then 429 `rate_limited` with `Retry-After`) → today's (else
  yesterday's) spool lines with offsets, `text` (the message part, display-safe), `sha256` of the
  whole line (as `GET /raw`) and `range_sha256` of the exact byte range `[from_offset,
  to_offset)`; `lines: []` with `reason: "no_file_today"` without a file (nothing read, nothing
  audited); audited `raw.tail` (range, count and `range_sha256`, never content).
- **Panel:** `GET /dashboard/summary` gains the box `parse_failures` (`sources.read` or
  `dashboard.view`; patterns and counts only): `{day, lines, failures, permille, by_source,
  top_patterns, last7} | null`.

### 17.9 Permissions, settings, codes, audit

- Permissions (no new key): search, pages, statistics, parse, fields, event, saved searches and
  the live tail `search.read` + KVKK (admin, auditor); export `search.export` + KVKK +
  re-authentication (admin); refs `search.read` + `evidence.create` + KVKK (admin); sources
  `sources.read` (admin), the tail also `search.read` or `lookup.run` + KVKK
  ([roles.md](roles.md)).
- Settings (group `search`, "Arama ve canlı akış"): `search.max_days` 31, `page_size_max` 500,
  `request_budget_ms` 4000, `stats_budget_ms` 8000, `facet_sample_rows` 100000,
  `max_concurrent` 3, `runs_per_minute` 30, `export_max_rows` 100000, `export_max_per_hour` 10,
  `export_budget_seconds` 120, `saved_max_per_user` 100; `live.max_streams_total` 4,
  `max_streams_per_user` 2, `max_lines_per_second` 100, `parse_lines_per_second` 1000,
  `buffer_lines` 5000, `max_minutes` 60, `backfill_lines` 50 (bounds in
  `arcilogger_db.settings`; read per request).
- Error codes and notices with their Turkish and English texts: `search.messages` (`MESSAGES`,
  `STATUS`, `NOTICES`), `live/messages.py`, `routers/sources.py`; the list with HTTP statuses is
  ARAMA.md/SEARCH.md section 14. Reused: `forbidden`, `privacy_notice_required`,
  `reauth_required`, `password_change_required`, `session_*`, `csrf_failed`,
  `validation_error`, `raw_*`.
- Audit (details are inputs, counts and hashes, never row content): `search.run`
  (`search_id`, `q` normalised, `from`, `to`, `kinds`, `driver`, `tables`, `rows`, `complete`,
  `outcome`, `elapsed_ms`, `saved_id`), `search.event` (`table`, `id`, `time`, `raw`),
  `search.refs` (`search_id`, `q`, `from`, `to`, `refs`, `truncated`, `complete`, `sha256`),
  `search.export` (`search_id`, `q`, `from`, `to`, `format`, `rows`, `truncated`, `bytes`,
  `sha256`), `search.saved_create` / `_update` (`saved_id`, `name`, `q`), `search.saved_delete`
  (`saved_id`, `name`), `live.open` (`stream_id`, `q`, `folders`, `backfill`), `live.close`
  (`stream_id`, `seconds`, `delivered`, `dropped`, `reason`, `ranges`), `raw.tail` (`source`,
  `day`, `origin`, `from_line`, `to_line`, `from_offset`, `to_offset`, `count`,
  `range_sha256`); how an auditor reads them: [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md)
  section 9. Not audited: `/search/page`, `/search/stats` (they serve
  an audited `search_id` only), `/search/parse`, `/search/fields`, `/live/status`, the sources
  list and detail.

### 17.10 Performance guards (`poe test-perf`)

`api/tests/test_search_perf.py`, tier `ARCILOGGER_SEARCH_PERF` = `1` (default: the newest day
2,000,000 lines plus 29 days of 100,000, and 4,320 `mt_event` rows a day (heartbeats, Wi-Fi, 1 %
DHCP offers), written with `generate_series`, `search_counts` rebuilt, `VACUUM ANALYZE`, TimescaleDB
chunks older than 7 days compressed, its compression policies paused while loading), `heavy` (30 ×
1,000,000, on site) or `0` (skipped). Budgets (best of three after a warm-up, through the API): Q1
browse first page 1 d / 30 d < 0.5 s; Q2 `srcip:<needle>` 1 d < 2 s, 30 d < 10 s; Q3 `srcip:<a /24>`
1 d < 2 s; Q4 `srcip:<needle> type:web action:blocked`, Q5 `domain:<rare> type:web`, Q6 `type:dhcp
mac:<rare>`, Q7 `qname:<rare>` 30 d < 10 s; Q8 statistics from the summary table 30 d < 0.5 s and
equal to direct counting; Q9 direct statistics 1 d < 2 s; Q10 export of 10,000 rows < 10 s; S1–S3
scans within the request budget + 0.5 s with an honest `complete`. For Q2 and Q4–Q7 the plan of the
whole-range window as the search sends it (on TimescaleDB one statement on each side of a
table's compression horizon; `EXPLAIN (FORMAT JSON)`, scans resolved to their table through
partitions and chunks) has no `Seq Scan` and no primary-key range scan of `messages`,
`fg_traffic`, `fg_web`, `fg_dns`, `fg_app` (compressed chunks excepted) and uses the field's index
(Q6: `mt_dhcp`'s MAC index and `mt_event`'s event index); Q3 refuses only a `Seq Scan`; Q10's rows
all have their client address and text. The measured numbers: ARAMA.md / SEARCH.md section 13.

### 17.11 Decided while building (where the Wave 2 plan of ADR-0015 was refined)

- **Query language:** `action`, `qtype`, `level` and `logid` compare ASCII case-insensitively
  (`lower(col) = :p`; the format fixtures hold `qtype="AAAA"` and `action="Edit"`, and the summary
  table lower-cases `action`), in the SQL and the matcher alike. `type:*`, `vlan:*` and
  `department:*` are refused (`search_value_invalid`); a quoted all-digit `policy:"12"` is a
  policy name, `policy:12` the number.
- **Summary table:** `drop_before` prunes by the months it drops, not by the cutoff day
  (section 17.5); the backfill job starts no new file after 60 s but finishes the file in hand.
- **Search API:** `SearchRequest.q` defaults to `""` and `from`/`to` may be left out with `saved_id`
  (without it a missing bound is 422 `search_range_invalid`); the `today` preset is `[site midnight,
  max(now, midnight + 1 s))`; on TimescaleDB a page's window ends at the end of a table's newest
  compressed chunk that it would hold (`timescaledb_information.chunks`, kept 60 s), so the
  compressed chunks are read by the next window only when the page is not full yet, and the
  search's transaction plans every statement with its values
  (`plan_cache_mode = force_custom_plan`, transaction-local); a page's fill-in queries carry its
  rows' time range as a constant (ADR-0015, "Plans on TimescaleDB"); direct statistics never
  answer 504 (a cut count is `complete: false` with `covered_from`); `GET /search/event` answers 404
  `search_event_not_found` for an unknown table too and 504 `search_event_timeout` when the read
  runs out of time; the export's fill-in queries run under the export's remaining budget, and a
  fill-in timeout cuts the file (`truncated: "time"`) rather than writing a row with empty columns;
  saved-search names refuse control, format and surrogate characters (422 `validation_error`); a
  row's `vlan_id` is the most specific VLAN subnet holding the address, as the VLAN facet counts it
  (a live line takes the same summary).
- **Live tail and sources:** `backfill` is optional; the stream's session check returns every
  refusal code of the authentication; stream limits hold under concurrent opens; the first
  connection always gets the backfill and later resumes never reach back before it; `live.close`
  names `closed`, `lingered`, `server_stopping` or the `end` code; an unknown sender's `suggested`
  is read from its newest 1,000 messages of the last 24 h; a parse failure's `error` needs the
  KVKK notice besides `search.read` or `lookup.run` (sections 17.7, 17.8).
- **Still to be shown on the CI jobs:** the `ix_fg_web_rhost_time` build on TimescaleDB with
  compressed chunks (`db/tests/test_migration_0016.py`, [ADR-0015](adr/0015-search-live-sources.md)
  "Consequences": pending; if TimescaleDB refuses the plain statement, the index is built
  `WITH (timescaledb.transaction_per_chunk)` after the upgrade's transaction), and
  `test_search_perf.py`, `test_live_perf.py` and the sources sparkline guard on the TimescaleDB
  and Windows jobs at tier 1 (recorded: native partitions at tier 1, TimescaleDB at a tenth and
  at half of it).

## 18. FortiGate DHCP, srcmac and internet exits (Wave 3, ADR-0016)

Decision: [ADR-0016](adr/0016-fortigate-dhcp-and-internet-exits.md); binding design:
`design/wave-3-contract.md`. This section collects what Wave 3 added, by kind, and points to the
section that describes each part. Base `/api/v1`; no new path checks the licence (parser, lease
job, lookup, alerts, endpoints: 5651 Temel). Nothing here claims legal compliance; FortiGate
format details that are not confirmed on site are settings or loud checks ("sahada doğrula",
ADR-0016).

### 18.1 Fields and explanation keys

- **Outcome** (`lookups.outcome`, `LookupOut.outcome`; section 5): `LeaseInfo.source_table`,
  `lease_seconds`, `last_dhcp_evidence`; `AttributionChain.mac_source`; `TrafficInfo.srcintf`,
  `dstintf`, `srcmac`; `NatMatch.egress`, `untrusted_sources`; `LookupOutcome.egress`
  (`LookupEgress {resolution, egresses: EgressEntry[], searched: EgressSearched, changes:
  EgressChange[], changed_after_event}`), `unconverted_dhcp`. Resolutions `public`, `transip`,
  `fallback`, `observed`, `public_unknown`, `unconfigured`, `no_egress`; exit modes `direct`,
  `isp_nat`, `not_logged`, `internal`. A lookup stored before the upgrade lacks these members;
  `from_dict` and every client read an absent member as its default (section 5), and the
  web's `web/src/lib/lookup-types.ts` declares them optional.
- **`LookupSettings`** (section 5): `nat_egresses` with `egresses()`, `snat_trandisp`,
  `srcmac_interfaces`, `srcmac_max_ips`, `fortigate_logids`, `fortigate_dhcp_msgs`,
  `use_logged_lease`, `fortigate_traffic_extends`, `fortigate_interfaces_excluded`,
  `fortigate_format_verified`, `lease_timeout_seconds(server, lease_seconds=None)`,
  `server_lease_seconds(server)`.
- **New explanation keys** (`arcilogger_worker.lookup.texts`, TR/EN texts there):
  `chain_missing`, `fortigate_dhcp_not_converted`, `srcmac_only`, `srcmac_agrees`,
  `srcmac_mismatch`, `srcmac_ambiguous`, `srcmac_shared`, `srcmac_other_lease`,
  `srcmac_infrastructure`, `egress_search`, `egress_public_unknown`, `public_ip_no_egress`,
  `egress_not_logged`, `egress_transip_not_seen`, `egress_interface_not_seen`,
  `egress_interface_not_seen_kept`, `egress_unmapped_seen`, `egress_unconfigured`,
  `nat_unknown_trandisp`, `fortigate_format_unverified`, `fortigate_format_verified`,
  `lease_renewal_not_seen`, `fortigate_dhcp_not_converted_since_lease`,
  `fortigate_dhcp_no_network`, `egress_observed_not_configured`, `egress_changed_after_event`,
  `session_changed_exit`, `egress_not_logged_but_seen`, `dhcp_sources_overlap`,
  `session_from_unknown_source`, `source_configured_later`, `egress_public_dynamic`,
  `egress_internal`, `source_clock_offset`; rewritten: `no_lease`, `no_lease_for_mac`,
  `port_hint` (`{egresses}`), `public_ip_not_transip`. Retired, kept for stored lookups and never
  emitted: `public_ip_not_configured`, `wan_transip_not_seen`. `srcmac_mismatch` is a conflict
  key like `ip_conflict` (`types.CONFLICT_KEYS`).
- **Report names** (`reports/texts.py`): `EXIT_LABELS` (keys `public`, `transip`,
  `fallback_direct`, `fallback_transip`, `observed`, `public_unknown`, `unconfigured`,
  `no_egress`), `EXIT_MODES`, `READING_TABLE_KEYS` (the keys of the reading table in
  docs/RAPOR.md, in the order of its rows), `TABLES["fg_dhcp"]`, `TABLES["fg_event"]` and
  `TABLES_EN` (English labels of those two only), `CHECK_FIELDS["srcmac"]`; `render.exit_label_key`
  maps an outcome's exit to its `EXIT_LABELS` key.

### 18.2 Settings, status rows and codes

- **Keys** (group `lookup`): `nat.egresses`, `nat.snat_trandisp`, `lookup.srcmac_interfaces`,
  `lookup.srcmac_max_ips`, `dhcp.fortigate_logids`, `dhcp.fortigate_dhcp_msgs`,
  `dhcp.fortigate_use_logged_lease`, `dhcp.fortigate_traffic_extends`,
  `dhcp.fortigate_interfaces_excluded`, `dhcp.fortigate_format_verified`, with defaults and
  bounds in section 5 ("Settings read by the worker"); the TR/EN labels and help texts are in
  `arcilogger_db.settings_meta`. `REAUTH_KEYS` and `LOCKED_KEYS` are unchanged.
- **Status rows** (section 3): `nat.egresses_migration`, `nat.egresses_changes`,
  `leases.rollback_lowered`.
- **Removed keys:** `nat.public_ips`, `nat.wan_transip`, `lookup.port_hint` (converted by
  revision `0018_nat_egresses`, administrator rows kept unread for one version), and with them
  the refusal code `setting_ip_invalid`, which no setting uses any more.
- **Refusal codes** (`arcilogger_db.settings.SETTING_ERRORS`; `PATCH /settings` answers 422
  `setting_invalid` with `details [{key, code, message_tr, message_en}]`, no field path; the
  texts never repeat a submitted value; ranges are written with a hyphen). The exit rules'
  codes are `arcilogger_db.egress.ERROR_CODES`:

  | Code | Türkçe | English |
  |---|---|---|
  | `setting_logid_invalid` | logid 10 rakamdan oluşmalı ve anlamı assigned ya da deassigned olmalı (en çok 16 kayıt). | A logid has 10 digits and its meaning is assigned or deassigned (at most 16 entries). |
  | `setting_dhcp_msg_invalid` | Her logid 10 rakamdan oluşmalı; değeri 1-8 ileti türünden (her biri 1-32 karakter, kontrol karakteri yok) oluşan bir liste olmalı. | Each logid has 10 digits; its value is a list of 1-8 message types (each 1-32 characters, no control characters). |
  | `setting_interface_invalid` | Arayüz adı 1-35 karakter olmalı; kontrol karakteri ve baş/son boşluk içeremez. | An interface name has 1-35 characters, without control characters or leading/trailing spaces. |
  | `setting_trandisp_invalid` | trandisp değerleri küçük harfle başlamalı (en çok 16 karakter); liste boş olamaz, en çok 8 değer. | trandisp values start with a lower-case letter (at most 16 characters); the list cannot be empty, at most 8 values. |
  | `setting_egress_name` | Çıkış adı küçük harf, rakam ve tire içermeli (en çok 32 karakter), ör. hat-1. | An exit name has lower-case letters, digits and hyphens (at most 32 characters), e.g. hat-1. |
  | `setting_egress_field` | Çıkışta bilinmeyen ya da eksik bir alan var; alanlar: label, mode (zorunlu), public, public_dynamic, transips, interfaces, port_preservation. | An exit has an unknown or missing field; the fields are label, mode (required), public, public_dynamic, transips, interfaces, port_preservation. |
  | `setting_egress_dynamic` | "Genel IP değişebilir" yalnızca servis sağlayıcı NAT'ı olan çıkışlarda seçilebilir. | "Public IP may change" can only be chosen for an ISP NAT exit. |
  | `setting_egress_mode` | NAT türü direct, isp_nat, not_logged ya da internal olmalı. | The NAT type must be direct, isp_nat, not_logged or internal. |
  | `setting_egress_label` | Açıklama en çok 64 karakter olabilir ve kontrol karakteri içeremez. | A label has at most 64 characters and no control characters. |
  | `setting_egress_address` | Geçersiz adres: IPv4 adresi ya da en az /24 bir IPv4 ağı bekleniyor (ağ adresi, ör. 203.0.113.208/29). | Invalid address: an IPv4 address or an IPv4 network of at least /24 is expected (the network address, e.g. 203.0.113.208/29). |
  | `setting_ipv6_not_supported` | IPv6 adresleri bu sürümde desteklenmiyor. | IPv6 addresses are not supported in this version. |
  | `setting_egress_overlap` | Bir genel adres ya da NAT adresi iki çıkışta birden olamaz; aynı transip yalnızca iki çıkışın arayüzleri ayrıysa kullanılabilir. | A public or NAT address cannot be in two exits; the same transip is allowed only when the two exits have separate interfaces. |
  | `setting_egress_direct` | Doğrudan çıkış en az bir genel adres ister; transip girilmez (FortiGate genel adresi kendisi kullanır) ve port koruma 'unknown' kalır. | A direct exit needs at least one public address; it has no transip (the FortiGate uses the public address itself) and port preservation stays 'unknown'. |
  | `setting_egress_not_logged` | FortiGate'ten geçmeyen çıkışa yalnızca genel adres ve açıklama girilir. | An exit that does not pass the FortiGate has only public addresses and a label. |
  | `setting_egress_internal` | İnternet çıkışı olmayan NAT tanımına yalnızca NAT adresleri (en az bir), arayüzler ve açıklama girilir. | An exit that is not an internet exit has only NAT addresses (at least one), interfaces and a label. |
  | `setting_egress_port_preservation` | Port koruma unknown, preserved ya da not_preserved olmalı. | Port preservation must be unknown, preserved or not_preserved. |
  | `setting_egress_catch_all` | NAT adresi boş (tanımsız adresleri kapsayan) en çok bir çıkış olabilir. | At most one exit may have no NAT address (the one that covers unclaimed addresses). |
  | `setting_egress_public_private` | Genel adres özel (RFC 1918), paylaşılan (RFC 6598) ya da ayrılmış bir adres olamaz; FortiGate'in servis sağlayıcı yönlendiricisinin arkasındaki adresini servis sağlayıcı NAT'ı çıkışının NAT adresi olarak girin. | A public address cannot be a private (RFC 1918), shared (RFC 6598) or reserved address; enter the FortiGate's address behind the ISP router as the NAT address of an ISP NAT exit. |
  | `setting_egress_isp_nat_direct` | NAT adresi genel adresiyle aynı olan hat doğrudandır: türünü Doğrudan yapın. | A line whose NAT address is its public address is direct: set its type to Direct. |
- `setting_too_many` (existing, "En çok {limit} öğe girilebilir." / "At most {limit} items are
  allowed.") covers more than 8 exits, more than 16 addresses or 8 interfaces per exit
  (`egress.MAX_EGRESSES`, `MAX_ADDRESSES`, `MAX_INTERFACES`) and more than 32 entries of the
  interface lists (`settings.MAX_INTERFACE_LIST`); `setting_type` (existing) a `nat.egresses`
  value that is no object. Where the contract names no code, the validator's choice is pinned by
  `tests/fixtures/egress-cases.json`: a field of the wrong JSON type, `null`, an unknown field, a
  missing mode or an exit that is no object → `setting_egress_field`; an address entry that is
  no text, netmask notation, host bits, an empty or /33 prefix, an octet with a leading zero, a
  network wider than /24 → `setting_egress_address`; an interface entry that is no text →
  `setting_interface_invalid`; `port_preservation` `null` or unknown →
  `setting_egress_port_preservation`; `public_dynamic` on `direct` → `setting_egress_dynamic`,
  on `not_logged` → `setting_egress_not_logged`, on `internal` → `setting_egress_internal`;
  limits count after de-duplication.
- **HTTP codes of the wave:**

  | Status | Code | Where |
  |---|---|---|
  | 422 | `setting_invalid` (the details above) | `PATCH /settings` |
  | 503 | `lookup_settings_invalid` (new text, section 6.1) | `POST /lookup/5651` with a public IP while `nat.egresses` cannot be parsed |
  | 504 | `fortigate_observed_timeout` (new, section 16) | `GET /settings/fortigate/observed` |
  | 422 | `ipv6_not_supported` (unchanged, section 6.1) | `POST /lookup/5651` |

### 18.3 Endpoints

| Endpoint | Access | Change | Section |
|---|---|---|---|
| `POST /lookup/5651`, `GET /lookup/{id}` | `lookup.run` + KVKK | the 2.10 fields; `lookup.run` details `egress {resolution, names}`; the new 503 text; a stored outcome returned as stored | 5, 6.1 |
| `GET /lookup/{id}/report.pdf` | `lookup.run` + KVKK | exit rows, method paragraphs, double-NAT variants, srcmac rows, FortiGate lease row, unconverted citation, cross-check of `fg_dhcp` and srcmac claims | 6.1 |
| `GET /settings/fortigate/observed?hours=24` | `settings.manage` | new; not audited; counts cached 5 min per `hours` (`generated_at` their time), claims current; 504 after 5 s | 16 |
| `GET /sources` | `sources.read` | gains `lease_evidence` (required, never null); not audited | 17.8 |
| `GET /settings`, `PATCH /settings` | `settings.manage` | the ten keys of group `lookup`; the codes of 18.2 | 5, 16 |

### 18.4 Audit and personal data

- **No new audit action.** `settings.change` covers every new key and the v0018 conversion,
  written once by the seed after `upgrade` (target `setting:nat.egresses`, `details` `{old:
  null, new, migration: "0018_nat_egresses", from, dropped [{value, reason}],
  conversion_failed}`); the change record `nat.egresses_changes` is a status row beside it, the
  audit log stays the record of every old value. `lookup.run` details gain `egress {resolution,
  names}`. Not audited: `GET /settings/fortigate/observed` and the `lease_evidence` member of
  `GET /sources` (counts and the firewall's own addresses only).
- **Command:** `arcilogger-db leases prepare-rollback` (the installer's rollback step; one
  transaction on derived `leases` rows and the status row `leases.rollback_lowered`; exit 0 with
  the count, 1 on a database error, 3 database unreachable). Raw and sealed data are not
  touched.
- **Personal data** ([roles.md](roles.md)): FortiGate DHCP host names and MACs (`fg_dhcp`,
  `leases`, the device inventory) under the existing permissions (`lookup.run`, `search.read`,
  the devices pages); srcmac chains under `lookup.run`; `srcmac:` under `search.read`;
  `TrafficInfo.srcmac` only for listed interfaces. On an interface with FortiGate device
  identification every session line carries the client's MAC, device name, OS, hardware vendor
  and version, device type and passively learned user names; raw lines in Search, the raw-line
  view, the PDF and evidence packages show them as they are, other people's candidate sessions
  included (D21). `srcmac_ambiguous` names at most 3 MACs.
- **Alert details** hold counts, VLAN numbers, a cause and a table code, a day, clock offsets in
  seconds and at most five logids, `dhcp_msg` values, interface, VDOM and source names or the
  firewall's own NAT addresses (log-supplied texts from trusted rows only, cut to 32 characters
  with MAC- and IPv4-looking parts replaced by "…", `alerts.system.masked`); never a client
  MAC; `fortigate-decline` lists at most five declined pool addresses (Security page only).
  E-mails carry only the rule, the cause's one-line action and the whitelisted counts, VLAN
  numbers, table and seconds (`alerts.texts` describers `_lease`, `_nat`).

### 18.5 Parsers and tables

- **Routing** (`arcilogger_ingest.parsers.fortigate`, docs/PARSERS.md): an `event` line with the
  keys `ip`, `mac` and `dhcp_msg` (`fortigate.DHCP_KEYS`) is an `fg_dhcp` row when `ip` converts
  to an address and `mac` to a MAC; otherwise it stays `fg_event` and the line is the parse
  failure `fortigate.dhcp` ("DHCP event without a usable ip/mac: ip=<valid|invalid|empty>,
  mac=<valid|invalid|empty>", never the values). The rule uses the line's shape only, never a
  setting; which logid means `assigned` or `deassigned` is decided in the worker
  (`dhcp.fortigate_logids`). An IPv6 `ip` with a MAC is an `fg_dhcp` row that the worker ignores.
- **`fg_dhcp` columns** (section 3, docs/DATA-MODEL.md): the time-series columns, `logid`,
  `subtype`, `level`, `logdesc`, `dhcp_msg`, `interface`, `ip` (NOT NULL), `mac` (NOT NULL),
  `hostname`, `lease` (seconds as logged), `msg`, `device_time` (NOT NULL: `true` when `time` is
  the FortiGate's event time, `false` when the receive time was used, `fortigate.time`; the
  worker never uses such a row as lease evidence), `extra`.
- **`fg_traffic`:** `srcmac` (field kind `mac`; an empty value is NULL, a value that does not
  convert stays in `extra` with `fortigate.value`) beside the existing `srcintf`/`dstintf`;
  `mastersrcmac`, `srcmacvendor` and the device identification fields stay in `extra`.
- **Kinds** (`arcilogger_db.kinds`): `fg_dhcp` belongs to the kind `event`; `CLIENT_IP["fg_dhcp"]
  = "ip"`; no `action` column.

### 18.6 Alerts `R20-lease` and `R20-nat`

`arcilogger_worker.alerts.system` `check_lease_evidence` / `check_nat_exits` (registered in
`CHECKS` as `lease_evidence` and `nat_exits`); one-line actions `alerts.texts.LEASE_CAUSES` /
`NAT_CAUSES`, titles and what-to-do texts `LEASE_TEXTS` / `NAT_TEXTS`; every finding's details
hold `cause`, `title_en`, `action_tr`, `action_en` besides the keys below. The dedup key is
`<rule>:<cause>` unless the table gives another; operator view: UYARILAR/ALERTS section 2.

| Dedup key | Severity | Details |
|---|---|---|
| `R20-lease:none:<table>` (`fg_dhcp`, `mt_dhcp`, `mt_arp`) | high (`mt_dhcp`: medium) | `table`, `hours` |
| `R20-lease:absent` | info | — |
| `R20-lease:fortigate-refused` | medium, high | `count`, `rows`, `kept`, `excluded`, `reasons`, `logids`, `dhcp_msgs`, `sources`, `first_day` |
| `R20-lease:fortigate-unconverted` | high | `count`, `rows`, `failures` |
| `R20-lease:fortigate-renewals-missing` | high | `count` |
| `R20-lease:fortigate-renewals-not-logged` | info | `count` |
| `R20-lease:fortigate-decline:<net>` | high | `count`, `vlan`, `network`, `addresses` |
| `R20-lease:dhcp-sources-overlap:<net>` | high | `vlan`, `network`, `origins` |
| `R20-lease:fortigate-lease-unlimited` | high | `count` |
| `R20-lease:fortigate-lease` | low | `count` |
| `R20-lease:fortigate-dhcp-subnet` | low | `count`, `interfaces`, `first_day` |
| `R20-lease:fortigate-unverified` | info | — |
| `R20-lease:source-clock` | high | `seconds`, `sources`, `offsets` |
| `R20-lease:fortigate-dhcp-mac` | medium | `count` |
| `R20-lease:srcmac-shared:<interface>` | medium | `count`, `interface`, `most`, `limit` |
| `R20-nat:conversion-failed` | high | — |
| `R20-nat:invalid` | high | `code` |
| `R20-nat:legacy-changed` | medium | `keys` |
| `R20-nat:unmapped-transip` | medium | `count`, `addresses [{address, kind}]` |

`<net>` is the configured network's VLAN number, else its subnet with `_` for `/`
(`Network.key`); `vlan` is present only for a network with a VLAN number. `fortigate-refused`'s
`reasons` counts the refused rows per rule, by the names `untrusted`, `unmapped`,
`unexpected_msg`, `receive_time`, `bad_mac` (`REFUSAL_NAMES`).

### 18.7 Search

- `srcmac:` (alias `kaynakmac:`): `fg_traffic.srcmac`, kind `mac`, typed, **not indexed**
  (scanned in time slices); `mac:` deliberately does not include it.
- `fg_dhcp` rows are found under `type:event` by `mac:` and `ip:` (both indexed),
  `interface:`, `logid:` (e.g. `logid:0100026001`) and `level:`.
- The `dhcp` kind is labelled "DHCP (MikroTik)" / "DHCP (MikroTik)"; ARAMA/SEARCH section 6
  points FortiGate DHCP records to `type:event`. Pinned by `api/tests/test_search_fields.py`,
  `test_kinds.py` and `tests/test_search_docs.py`.

### 18.8 Captured answers, mirrors and guards

- **Samples** (`web/scripts/capture_api_samples.py`; regenerate, then `--check`):
  `settings.json` with the new keys and a 422 `nat.egresses` refusal; `lookups.json` with a
  FortiGate-only internal and candidate chain, a chain at `low` through
  `lease_renewal_not_seen`, a srcmac-only chain, a srcmac conflict, a `hat-2` candidate list, a
  quiet `direct` → `none`, `fallback` (direct and transip), `observed` (with and without exits),
  `not_logged` and `not_logged` seen, `public_ip_no_egress` (listing every configured exit),
  `egress_public_unknown`, `egress_public_dynamic`, `egress_internal`,
  `egress_changed_after_event`, `source_clock_offset`, an `unconverted_dhcp` citation, a match
  with `untrusted_sources`; `sources.json` with `lease_evidence` (`ok`, `none` with
  `none_tables`, `refused`); `settings-observed.json`; `web/src/generated/api-schemas.json`.
  The capture enters `nat.egresses` with `updated_at` 2026-10-01 09:00 site time, so the samples
  carry no wall-clock change warning and `--check` is stable.
- **Mirrors:** `web/src/lib/lookup-types.ts`, `sources-types.ts` (`lease_evidence` required)
  and the observed answer in `api-types.ts`.
- **Budgets** (`poe test-perf`, contract 3.8): observed endpoint < 2 s at 1 M rows a day over
  24 h (deadline 5 s); `lease_evidence` < 0.5 s, its srcmac count < 1 s; each `R20-lease` query
  < 1 s, `source-clock` < 0.5 s, `R20-nat:unmapped-transip` < 0.2 s; the D8 probe for 20 IPs and
  the post-lease checks within the lookup's own guards.

### 18.9 Decided while building (where the plan of ADR-0016 was refined)

Recorded in the contract's "Deviations" sections; what touches the API, the outcome or the
report:

- **day0:** the ADR-0016 members of `lookup-types.ts` are optional, and the router returns a
  stored outcome as stored (no `from_dict` round trip); `LookupSettings.from_settings` reads the
  lookup-only values leniently; ranges in the new refusal texts use a hyphen.
- **c1:** "seen, but not alone" falls back only when no trusted session through P spans T and
  the search on P finds none; `public_unknown` keeps each exit's interface rule and the not-seen
  widening; `double_nat` of a request without a public IP is "no `direct` exit" (an unreadable
  `nat.egresses` counts as true); an IPv6 public IP lists no session, NAT66 included;
  `egress_changed_after_event` joins exits with "; " and words internal authors; the validator
  codes of 18.2; `setting_ip_invalid` is gone; the samples backdate the exits.
- **b:** ARP never moves the deadline of an `fg_dhcp` lease; `arcilogger-db leases
  prepare-rollback` exits 0/1/3 and keeps the recorded `at` when run again; the describers read
  only `cause`, `count`, `vlan`, `table`, `seconds`.
- **c2:** a RELEASE mapped after the build ends the lease in the lookup; D27 outside configured
  networks uses the address as `{network}`; the D8 probe also runs for a MAC-only lookup; the
  per-lookup clock check reads the rows nearest T with the 120-s limit for lease searches; the
  srcmac guard counts a used row outside the hour by its own address; the D8 probe window and
  the `mt_dhcp` cap of the `fg_event` post-lease check are 604,800 s (the converted-row check
  keeps 8,640,000 s).
- **e1:** `lease_evidence.<table>.last_hour` is the newest usable row within the last hour
  (section 17.8); the web judges "as converted" by the converter's shape.
- **WP-D:** `lease_evidence` is required and never null (the `dhcp.fortigate_*` values
  unreadable: `refused_rows`/`excluded_rows` 0, state from the alerts only); the observed
  endpoint caches the counts and computes the exit claims on every request; the double-NAT
  variant and the CGNAT sentence only for a request with a public IP; no `double_nat` bullets
  beside the `fallback` paragraph; `build_site(srcmac=True)` gives the phone `vlan60` (its own
  network) where the contract wrote `vlan42`. They are listed in the WP-D pull request and,
  where they depart from the contract, in its "Deviations — d".
