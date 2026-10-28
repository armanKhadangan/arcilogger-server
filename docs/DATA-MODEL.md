# Veri modeli / Data model

> Durum: MVP (sürüm 0.1.0). Kaynak: `db/src/arcilogger_db/models.py` ve
> `db/src/arcilogger_db/migrations/versions/` (`v0001_baseline`, `v0006_lookup_reports` …
> `v0017_fortigate_dhcp`). Bir test, modellerle migrasyonların birebir aynı olduğunu her
> çalıştırmada doğrular.

## Özet (TR)

- Veritabanındaki her şey **türetilmiş** veridir: ham spool dosyalarından ve mühürlü arşivden
  (vault) yeniden üretilebilir. Yasal delil veritabanı değil, mühürlü ham dosyalardır.
- PostgreSQL 16; TimescaleDB kuruluysa zaman serisi tabloları hypertable olur (1 günlük parçalar,
  7 günden eski parçalar sıkıştırılır). TimescaleDB yoksa aynı tablolar PostgreSQL'in kendi
  **aylık bölümlemesiyle** (`PARTITION BY RANGE (time)`) oluşturulur; eski veriler satır satır
  silinmez, ay ay düşürülür.
- Her ayrıştırılmış satır, ham satırına geri götüren bir **ham referans** taşır:
  `(source, raw_day, raw_line, raw_offset)`.
- Veritabanının arşivden **daha kısa** süre tutması hedeflenir (ayar `db.retention_days`,
  varsayılan 400 gün); ancak log tablolarının imha işi **sonraki bir aşamadadır**: bu sürümde
  ayrıştırılmış log kayıtları veritabanından silinmez. Kullanım özetleri (`usage_*` tabloları,
  `domain_categories`) ise her gün 03:30'dan sonra worker işi `usage.retention` ile silinir:
  `usage.retention_days` (varsayılan 400) günden eski olanlar; biten yeniden hesaplama istekleri
  (`usage_rebuilds`) 90 gün sonra. Arşiv en az 730 gün (varsayılan 760 gün) tutar; arşiv imhası
  varsayılan olarak kapalıdır. Ayrıntı: "Retention" bölümü ve
  [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md).
- Lisans tabloları (`license_installation`, `license_files`, `license_events`; revizyon
  `0014_license`) yalnızca eklenerek yazılır ve hiçbir zaman silinmez. Lisansın kendisi bu
  tablolar değil, her değerlendirmede imzası yeniden doğrulanan lisans dosyasıdır
  ([LISANS.md](LISANS.md)).
- Tarayıcıdan yönetim istekleri (`admin_requests`; revizyon `0015_admin_requests`, ADR-0014)
  hiçbir gizli bilgi tutmaz: adı parola, müşteri numarası ya da belirteç gibi olan bir alanda
  yalnızca "kayıtlı / kayıtlı değil" bilgisi bulunabilir; bunu veritabanının kendisi denetler.
- Arama (revizyon `0016_search`, [ADR-0015](adr/0015-search-live-sources.md)): `search_counts`
  her saat, spool dosyası, kayıt türü, tablo, eylem ve özel /24 ağı için **kesin** satır
  sayısını tutar; ingest bunu satırlarla aynı işlemde yazar, böylece geniş bir aramanın
  istatistikleri milyonlarca satır sayılmadan gelir. `saved_searches` kişisel kayıtlı
  aramalardır. `ingest_offsets.counts_from_line` yükseltmeden önce yüklenmiş ve henüz
  sayılmamış dosyaları işaretler; `fg_web` üzerindeki `ix_fg_web_rhost_time` indeksi "bu alan adı
  ve alt alan adları" aramasını hızlandırır.
- FortiGate DHCP (revizyon `0017_fortigate_dhcp`, ADR-0016): `fg_dhcp`, FortiGate'in DHCP
  sunucusu satırlarını (ACK/RELEASE) tutan yeni zaman serisi tablosudur; bir satırın ne anlama
  geldiğine `logid` ve ayarlara bakarak worker karar verir. `fg_traffic.srcmac` oturum kaydının
  kaynak MAC adresidir (dizinsiz; kira kanıtı değildir). `leases.lease_seconds`, FortiGate'in
  kaydettiği kira süresidir (300–8.640.000 sn); `leases.source_table` artık `mt_dhcp`, `mt_arp`
  ya da `fg_dhcp` olabilir. Yükseltmeden önce yüklenmiş FortiGate DHCP satırları `fg_event`
  satırı olarak kalır, dönüştürülmez.
- İnternet çıkışları (revizyon `0018_nat_egresses`, ADR-0016): eski `nat.public_ips`,
  `nat.wan_transip` ve `lookup.port_hint` ayarları tek `nat.egresses` ayarına (varsayılan `{}`:
  tek bir çift NAT çıkışı varsayılır) dönüştürülür. Düzenlenemeyen üç durum satırı vardır:
  `nat.egresses_migration` (dönüşümün kaydı), `nat.egresses_changes` (her çıkışın son içerik
  değişikliği: zaman ve yazan) ve `leases.rollback_lowered` (`-Rollback` sırasında düşük güvene
  indirilen FortiGate kiraları; yeniden yükseltmede okunup silinir).

## Summary (EN)

Everything in the database is derived and rebuildable from the raw spool and the sealed vault;
the legal evidence is the sealed raw files. PostgreSQL 16, with optional TimescaleDB
(hypertables for the time-series tables); without it the time-series tables are natively
partitioned by month. Every parsed row carries a raw reference to its line. The database is
meant to keep less than the vault (`db.retention_days`, 400 days, vs. at least 730 days, default
760), but the deletion job of the log tables comes in a later milestone: **this release deletes no
parsed log rows from the database**. The usage rollups (`usage_*` tables, `domain_categories`)
are deleted daily after 03:30 by the worker job `usage.retention` once older than
`usage.retention_days` (default 400), finished rebuild requests (`usage_rebuilds`) after 90 days
(see "Retention"). The licence tables (`license_*`, revision `0014_license`) are append-only and
never deleted; the licence itself is the signed file they hold, verified again on every
evaluation ("Licence tables"). The requests of the browser administration (`admin_requests`,
revision `0015_admin_requests`, ADR-0014) hold no secret: a field named like a password, a
customer number or a token may only say whether a secret is set, which the database checks.
Search (revision `0016_search`, [ADR-0015](adr/0015-search-live-sources.md)) adds the exact hourly
summary table `search_counts` (written by ingest in the transaction of the rows), the personal
`saved_searches`, `ingest_offsets.counts_from_line` (files loaded before the upgrade and not
counted yet) and the expression index `ix_fg_web_rhost_time` for "this domain and its
subdomains". FortiGate DHCP (revision `0017_fortigate_dhcp`, ADR-0016) adds the time-series
table `fg_dhcp` (the FortiGate DHCP server's ACK/RELEASE lines; the worker decides what a row
means from its `logid` and the settings), `fg_traffic.srcmac` (the session record's source MAC;
not indexed, never lease evidence) and `leases.lease_seconds` (the lease time the FortiGate
logged, 300–8,640,000 s); `leases.source_table` is `mt_dhcp`, `mt_arp` or `fg_dhcp`. FortiGate
DHCP lines loaded before the upgrade stay `fg_event` rows; they are not converted. Internet exits
(revision `0018_nat_egresses`, ADR-0016) replace the settings `nat.public_ips`, `nat.wan_transip`
and `lookup.port_hint` with the one setting `nat.egresses` (default `{}`: one implicit double-NAT
exit) and add three status rows that are never edited: `nat.egresses_migration`,
`nat.egresses_changes` and `leases.rollback_lowered` ("Status rows" below).

## Conventions

| Rule | Detail |
|---|---|
| Times | `TIMESTAMP WITH TIME ZONE`, always aware; every connection uses `TimeZone=UTC` |
| Addresses | `INET` (IPs), `CIDR` (networks), `MACADDR` (PostgreSQL prints `00:00:5e:00:53:57`) — returned to Python as **strings** |
| Unknown fields | `extra JSONB` (nothing a parser does not map is thrown away) |
| Ids | `BIGINT GENERATED BY DEFAULT AS IDENTITY` |
| Names | Column names of spec 7.3 where the spec names them; SQL keywords avoided (`user` → `user_name`). Exception from the spec: `leases.start` / `leases."end"` — quote `"end"` in raw SQL |
| Constraint names | Deterministic naming convention (`pk_<table>`, `ix_<table>_<cols>`, `ck_<table>_<name>` …) |
| Encoding | The migration refuses a non-UTF8 database (Windows clusters default to WIN1252) |

## Raw reference (ham referans)

Every time-series row has:

| Column | Meaning |
|---|---|
| `source` | Configured source name = spool folder (e.g. `fortigate`, `core-r1`, `ap-<ip>`, `unknown-<ip>`) |
| `raw_day` | Site day (Europe/Istanbul **receive** day) of the spool file |
| `raw_line` | 1-based line number in `spool/<source>/<raw_day>.log` |
| `raw_offset` | Byte offset of the line start in that (uncompressed) file |
| `time_received` | Collector receive time (prefix of the raw line) |
| `sender` | Sender IP as seen by the collector |
| `time` | Device/event time in UTC (FortiGate `eventtime` preferred); equals `time_received` when the message has no usable device time (a time without offset is never guessed; it stays in `extra.device_time`) |

Sealing compresses `spool/<source>/<day>.log` byte for byte into
`vault/<YYYY>/<MM>/<DD>/<source>.log.gz`, so **line `raw_line` of the decompressed file is the
same line** and `raw_offset` is still its byte offset. Today's lines are read from the spool,
older ones from the sealed file; either way the reference can be cited in an evidence package.

## Time-series tables

Primary key `(time, id)` on all of them: on a TimescaleDB hypertable and on a natively
partitioned table every unique index must contain the partitioning column `time`. Insert
through the table itself (never into a partition), so the identity column is used.

| Table | Content | Indexes besides the primary key |
|---|---|---|
| `messages` | Every received message (lightweight): severity, facility, program/topics, host, type, subtype, short text, `parse_error` | (source, time), (type, time) |
| `fg_traffic` | FortiGate traffic (incl. interim `0000000020`, `is_interim`), source NAT `transip:transport`; `srcmac` (`MACADDR`, revision `0017_fortigate_dhcp`): "MAC address associated with the Source IP", filled only where the FortiGate's device identification is on, NULL in older rows; never lease evidence (ADR-0016, D10) | (srcip, time), (transip, transport, time), (sessionid), (dstip, dstport, time) — none on `srcmac` (the search field `srcmac:` scans) |
| `fg_web` | Web filter: hostname, url (≤ 2,000 chars), cat, catdesc, action, eventtype, profile | (sessionid), (srcip, time), `ix_fg_web_rhost_time` (reverse(lower(hostname)), time) — the search field `domain:` (revision `0016_search`) |
| `fg_dns` | DNS: qname, qtype, answers `INET[]`, catdesc, action | (srcip, time), (qname, time) |
| `fg_app` | Application control: app, appid, appcat, hostname, url | (sessionid), (srcip, time) |
| `fg_ips` / `fg_virus` | IPS and antivirus events | (srcip, time) |
| `fg_event` | Admin logins, configuration changes (`cfgpath`, `cfgobj`, `cfgattr`), system/perf, VPN, user | (subtype, time) |
| `fg_dhcp` | FortiGate DHCP server events (revision `0017_fortigate_dhcp`, ADR-0016): an `event` line with `ip`, `mac` and `dhcp_msg` that convert (else it stays `fg_event`, parse failure `fortigate.dhcp`): logid, subtype, level, logdesc, dhcp_msg, interface (the DHCP server's interface), ip, mac (both NOT NULL), hostname, lease (`BIGINT`, seconds as logged, NULL when absent; verify on site), msg, `device_time` (`true`: `time` is the FortiGate's event time; `false`: the receive time, never lease evidence). What a row means (ACK, RELEASE) is decided by the worker from its `logid` (`dhcp.fortigate_logids`), so a settings change re-reads old rows; lines loaded before the upgrade are not converted | (ip, time), (mac, time) |
| `mt_dhcp` | `assigned`/`deassigned`: server, vlan, ip, mac, hostname | (ip, time), (mac, time) |
| `mt_arp` | Hourly `ARP-LOG ip mac interface` | (ip, time), (mac, time) |
| `mt_fw` | MikroTik firewall: prefix, chain, in/out interface, state, src-mac, proto, src:sport → dst:dport, len | (src, time), (dst, time) |
| `mt_account` | login / logout / login_failure, user, ip, via | (ip, time) |
| `mt_interface` | link up/down per interface | (source, time) |
| `mt_config` | `… changed/added/removed by <user>` (patterns configurable, verify on site) | (source, time) |
| `mt_event` | Everything else that matters: `heartbeat` (`ARCI-HEARTBEAT <identity>`), Wi-Fi, critical/error/warning, `dhcp_pool_empty` | (event, time), (source, time) |

Column lists per table: `models.py` (each column documented). Columns the specification lists
but which are not in a table go to `extra`.

## Reference and derived tables

| Table | Key | Content |
|---|---|---|
| `sources` | `name` | Type (fortigate/mikrotik/generic), address, enabled, expected, heartbeat minutes, `unknown` (sender not configured → alert "yeni log kaynağı"), first/last seen |
| `parse_failures` | id; unique (source, day, pattern) | Count of unparsed lines per source/day/pattern, first/last seen, **one example** raw reference (`example_line`, `example_offset`), last error |
| `ingest_offsets` | (source, day) | Ingest resume point: `byte_offset` and `line_number` of complete lines loaded; `finished_at` when the day is done; `counts_from_line` (revision `0016_search`): NULL when every loaded line of the file is in `search_counts`, else the lines before it were loaded before the upgrade and wait for the worker job `search.counts_backfill`, which recounts the finished file and clears it (the migration sets `line_number + 1` on every file with loaded lines) |
| `vlans` | `subnet` | The site's VLANs and subnets (none are shipped): `vlan_id` (unique, NULL for a network without one, e.g. a transit network), `name_tr`, `purpose`, `dhcp_server` (e.g. `dhcp60`), `department_id`, `guest` |
| `departments` | id | The site's departments, entered by its administrator (none are shipped, see "Seed data") |
| `persons` | id | Name, department, title, e-mail, employee number (unique), active (persons are deactivated, never deleted), `usage_excluded` (left out of person-level usage views and reports; revision `0010_usage`) |
| `devices` | `mac` | First/last seen, last IP/VLAN, `hostnames[]`, vendor, `randomized`, type (pc, phone, printer, ip_phone, ap, server, camera, other), `person_id` (the **current** owner, mirrored from `device_assignments`), `infrastructure`, notes, `tags[]`, label `name` |
| `device_assignments` | id; partial unique `mac` where `valid_to` IS NULL | Assignment history (revision `0008_device_assignments`, ADR-0008): `mac` → `person_id` for `[valid_from, valid_to)` (`valid_to` NULL = current), `assigned_by`, `note` (given when the row is opened; an unassign takes none), `backfilled` (made by the migration from an older link; `valid_from` = the device's first sighting). No two intervals of one MAC overlap — exclusion constraint `ex_device_assignments_no_overlap` (`btree_gist`) or, where the extension cannot be created, a trigger with the same rule. Rows are never deleted; foreign keys RESTRICT. The 5651 person step reads the row valid at T. Not a time-series table (small) |
| `leases` | id | IP ↔ MAC interval: vlan, server (a MikroTik DHCP server name, or the FortiGate interface of an `fg_dhcp` lease), hostname, `start`, `"end"` (NULL = open), `end_reason` (dhcp, timeout, superseded, arp, open), confidence (high/medium/low), `source_table` (`mt_dhcp`, `mt_arp` or `fg_dhcp`), `last_evidence`, `lease_seconds` (revision `0017_fortigate_dhcp`: the lease time a FortiGate ACK logged, when usable; NULL for every other lease), `evidence` (JSON list of `arcilogger_worker.lookup.EvidenceRef.to_dict()` objects: `{"table", "row_id", "time", "raw": {"source", "day", "line", "offset"} \| null, "summary"}`). Checks: `end ≥ start`; `end_reason = 'open'` exactly when `end` is NULL; `ck_leases_source_table` (the three tables); `ck_leases_lease_seconds` (NULL or 300–8,640,000, the FortiOS `lease-time` range). Indexes (ip, start), (mac, start), `ix_leases_source_table_end` (source_table, "end") (open and recently ended leases per table, revision `0017_fortigate_dhcp`). The downgrade of that revision deletes the `fg_dhcp` leases first (the previous code cannot read them; they are rebuildable) |
| `job_runs` | id | Worker job runs: job, started/finished, status (running/ok/failed/skipped), details |
| `settings` | `key` | Canonical-JSON value (no floats: every change is audited), `updated_at`, `updated_by` (`:seed` while it is the shipped default, else the admin's user name; `:system`, `:migration` for internal keys — `:` never occurs in a user name). Besides the keys of `DEFAULT_SETTINGS` it holds the **status rows** below, which are not settings: never listed by `GET /settings`, never editable (`settings set` refuses them) |
| `users` | id; unique `username` | Lower-case ASCII user name, display name, role (admin/auditor/hr/viewer), Argon2id hash, `must_change_password`, encrypted TOTP secret, `totp_enabled`, `totp_last_counter` (replay guard), failed attempts, `locked_until`, last login, active |
| `user_recovery_codes` | id | Hashed one-time recovery codes |
| `user_sessions` | `token_hash` | SHA-256 of the session cookie token; created, last seen, `expires_absolute`, ip, user agent, `mfa_passed`, `csrf_token_hash`, `revoked_at`, `reauthenticated_at` |
| `privacy_notice_acceptances` | id; unique (user, version) | KVKK notice acceptance |
| `audit_log` | id; unique `hash`, unique `prev_hash` | Hash-chained audit log (spec 8.5), see below |
| `lookups` | id | 5651 lookup: user, **makam** (`authority`), **dosya/evrak no** (`file_no`), **talep tarihi** (`request_date`), **gerekçe** (`reason`) — all four required (spec 12.3 page 6; NOT NULL, text not blank), `query` (JSON), `outcome` (JSON), mode, result count |
| `lookup_reports` | id; unique `lookup_id` | The stored official 5651 report of a lookup (revision `0006_lookup_reports`, docs/RAPOR.md): user, `path` (relative to `exports/`, `reports/ARCILOGGER-5651-rapor-<id>.pdf`), `sha256`, `size`, `pages`, `version`, `inputs` (everything the PDF was rendered from), `timestamp_status` / `timestamp` (the optional time stamp of the PDF's hash), `download_count`. Rendered once, never rendered again; no expiry yet |
| `evidence_packages` | id | Evidence ZIP in `exports/`: scope (JSON: content, days, sources, extract lines, lookup, reference, `estimated_bytes` — the size estimate used by the request limits), path (file name, empty until ready), SHA-256, size, password protected (the password is never stored), status (queued/building/ready/failed/expired/deleted), error, expiry (`evidence.keep_days`), download count, `package_name` (id in `paket.json`), `index_sha256`, `verifier_sha256` (SHA-256 of the package's `verify.py`), `legal_grade`, `finished_at` (revision `0007_evidence_seals`; `docs/KANIT-PAKETI.md`) |
| `evidence_downloads` | id | Each download (who, when, IP) |
| `seal_verifications` | id; index (day, finished_at) | "Doğrula" on the sealing page: day, started/finished, user, `deep`, outcome (ok/failed/timeout/error), errors, warnings, details. The scheduled verifications stay in `state/verify` (the API may not write there) |
| `audit_imports` | `key` | Events of the scheduled sealing/retention tasks already copied into `audit_log` (`seal:<day>:sealed`, `seal:<day>:attempt:<n>`, `retention:<record>`, `retention:<record>:incomplete`, `retention:alert:<sha256 of the alert line>`) and of the browser administration's journals (`admin:<request>:<state>`, `admin-secret:<line>:<name>`, `admin-system:<line>`) and the entry id: each event is imported once |
| `admin_requests` | id (32 hex); index (channel, state, created_at), (kind, created_at DESC), (section) where not terminal | Requests of the browser administration (ADR-0014, revision `0015_admin_requests`): `kind` (`config.apply`/`revert`/`cancel`, `action.seal`/`credit`/`tsa_test`/`replica_test`, `worker.test_email`/`lease_rebuild`), `section`, `channel` (`agent`: a signed request file for ArciAdmin; `worker`: taken by the worker job `admin.requests` with `FOR UPDATE SKIP LOCKED`), `state` and `terminal` (checked against each other), the requester (`requested_by_id`: users, SET NULL; `requested_by`: the name), `expires_at`, `effective_at`, `params` and `result` (never a secret: a key named like `password`, `customer` or `token` must hold a masked secret `{"secret": true, …}`, checked by the database), `error_code`, `rolled_back`, `audit_id` (the `admin.request` entry) |
| `alerts` | id | time (when it happened), rule, severity, state (open/acknowledged/resolved/dismissed), title, details, `dedup_key` (at most one *open* alert per key), `created_at` (when the row was written), `resolved_at`; written by ingest, correlation and the alert jobs (docs/ALERTS.md), resolved automatically by the check that owns the key (`details.auto_resolved`); a person changes the state on the Security page (`alert_events`) |
| `alert_deliveries` | id; unique (alert_id, kind, recipient) | Alert e-mail per recipient (ADR-0010): kind (new/resolved), status (queued/sent/failed/suppressed), attempts, last error (never a password), next attempt, `message_id` of the e-mail that carried it, created/sent |
| `usage_days` | `day` | Usage rollups (revision `0010_usage`, ADR-0011, [USAGE.md](USAGE.md)): one row per rolled-up site day — status (`partial` today, `final` after its late window), computed at, `computed_until`, `inputs_hash` (engine version, `usage.*` settings, suffix list, ingest progress of the day, taken before the logs are read), `attribution_hash` (who every source address belonged to when), the day's source `ips`, run details. The re-attribution job compares the hashes with the current state |
| `usage_daily` | (day, subject, off_hours) | Per subject (`person:<id>`, `device:<mac>`, `ip:<addr>`) and day, split into work hours / off hours: kind, person id, MAC, IP, VLAN (most online minutes, else of the blocked attempts and URLs), online/active seconds, download/upload bytes, sessions, blocked attempts, first/last activity, `timeline` (runs of active minutes with their dominant domain). Index (subject, day) |
| `usage_device_daily` | (day, mac, subject, off_hours) | The same per device (MAC) and subject (per-device breakdown of a person), main IP and VLAN (most online minutes, else of the blocked attempts and URLs), `top_domains` (≤ 20); `blocked` counts every attempt of the device (a device with blocked attempts only has a row too) |
| `usage_domain_daily` | (day, subject, domain, off_hours) | Per subject, registrable domain (or FortiGuard service, or address: `kind`) and day: estimated `seconds` (share of the active minutes), `raw_seconds`, bytes, sessions, `background`, `hosts` (`{hostname: [seconds, bytes, sessions]}`). Indexes (domain, day), (subject, day) |
| `usage_app_daily` | (day, subject, app, off_hours) | Per FortiGate application: category, seconds, bytes, sessions |
| `usage_hourly` | (hour, subject, off_hours) | Per subject and hour: online/active seconds, bytes, top domain (heat map, hourly tables, part-day report windows). Indexes (day), (subject, hour) |
| `usage_blocked` | id | Every blocked attempt (web filter, DNS filter, application control): time, subject, MAC, source, action, domain, host name, category, URL, application; at most `usage.blocked_per_day` per subject and day |
| `usage_urls` | id | URLs of the web filter log per subject and day: host name, domain, hits, first/last seen, category, blocked; the newest `usage.urls_per_day` |
| `domain_categories` | `domain` | FortiGuard category per registrable domain (web filter `catdesc`, else DNS filter), number, source, hits, `last_day` (an older day never overwrites a newer category) |
| `usage_rebuilds` | id | Rebuild requests of `POST /api/v1/usage/rebuild`: range, status (queued/running/done/failed), days done, error, requested by; run by the worker job `usage.requests` |
| `alert_events` | id; index (alert_id, at), (at) | Every change of an alert's state (`from_state` → `to_state`) and every note (`text`, 1–2000 characters) with `actor` (user name, or `system` for the checks' automatic resolution and re-opening, the reason code then in `text`), `actor_id` (users, SET NULL) and `at` (revision `0012_alert_events`, ADR-0010 amendment). Written only by `arcilogger_worker.alerts.store`; the audit log gets `alert.state` / `alert.note` (the note's length only) |
| `report_schedules` | id | Scheduled usage reports (revision `0011_reports`, ADR-0012): name, kind (end_of_day/full_day/weekly/monthly), `enabled`, `time` (HH:MM, site time), `weekday` (weekly) / `monthday` (monthly), `recipients`, `formats` (`email_html`, `pdf`, `xlsx`), `person_level` (default true: names), `pseudonymize`, `next_run_at`, `updated_by`. The four shipped schedules are inserted once by the migration, disabled |
| `search_counts` | id; unique `uq_search_counts_key` (hour, source, raw_day, kind, tbl, action, net) `NULLS NOT DISTINCT`; index (source, hour), (raw_day, source) | Exact hourly line counts of the search statistics (revision `0016_search`, ADR-0015, `arcilogger_db.searchcounts`): `hour` (`date_trunc('hour', time)`, device time, UTC), the spool file (`source`, `raw_day`), `kind` (`arcilogger_db.kinds.kind_of` of the `messages` row), `tbl` (the line's typed table, `''` without one), `action` (the typed row's, lower-cased), `net` (the /24 of the typed row's client address when it is private IPv4: 10/8, 172.16/12, 192.168/16, 100.64/10), `n` (> 0). One row per key, about 450 keys an hour for 150 users. The ingest loader adds a batch's counts in the transaction that loads its rows; `searchcounts.rebuild_stream` recounts one file from its rows (`arcilogger-db search-counts rebuild`); derived, like every table |
| `saved_searches` | id; unique `uq_saved_searches_owner_name` (owner_id, lower(name)) | Personal saved searches (revision `0016_search`): `owner_id` (users, CASCADE), `name` (1–80 characters, unique per owner ignoring case), `query` (≤ 1,000 characters, parsed again on every run), the range as a preset `range_preset` (`15m`, `1h`, `24h`, `today`, `yesterday`, `7d`, `30d`) **or** a fixed `[range_from, range_to)`, never both (`ck_saved_searches_range`), created/updated, `last_run_at` |
| `report_runs` | id; index (schedule_id, id), index (status) | Every report run (scheduled or "run now"): period, kind (+ `custom`), the schedule's settings then (formats, recipients, person level), status (queued/running/sent/partial/failed), `files` (name, format, size, SHA-256 of each file in `exports/usage-reports/<id>/`), `deliveries` (per recipient: status, attempts, error, sent), `expired_at` (files deleted after `reports.keep_days`). Kept when the schedule is deleted |

**Status rows** (`settings` rows outside `DEFAULT_SETTINGS`; each written only by the code
named, read by the code named):

| Key | Written by | Value | Read by |
|---|---|---|---|
| `db.timescale` | the baseline (`:migration`); revision `0017_fortigate_dhcp` appends `fg_dhcp` to its table list (and records `compression_reason = "fg_dhcp: <reason>"` when compressing `fg_dhcp` is refused in `auto` mode; the downgrade removes both) | storage outcome (`enabled`, `mode`, `storage`, `hypertables` or `partitioned`, `compression`, `reason`, …), see "TimescaleDB" | `arcilogger-db check` and `arcilogger-db upgrade` (printed), the search (`arcilogger_api.search.windows.read_storage`: `storage`, `compression`), the tests |
| `nat.egresses_migration` | revision `0018_nat_egresses` (`:migration`), only when an administrator had set a legacy NAT key; the seed sets `audited` once it wrote the audit entry | `{"migration": "0018_nat_egresses", "from": {the three legacy values}, "new": <the converted exits>, "dropped": [{"value", "reason": "ipv6" \| "invalid" \| "reserved"}], "conversion_failed": bool, "audited": bool}` | the seed (one `settings.change` entry, target `setting:nat.egresses`), the lookup (whether an exit is still *as converted*, ADR-0016 D15), the alert `R20-nat:conversion-failed` |
| `nat.egresses_changes` | revision `0018_nat_egresses` (per converted exit, with the legacy rows' author and time) and `settings_admin.change_setting` (`:system`, in the transaction of the `settings.change` entry) | per exit name `{"fingerprint": <SHA-256 of the canonical JSON of mode, public, transips and public_dynamic>, "changed_at", "changed_by", "deleted": bool}`; a label-, interface- or port-preservation-only edit changes nothing; no earlier contents (the audit log holds them) | the lookup (`egress_changed_after_event`, D32) |
| `leases.rollback_lowered` | `arcilogger-db leases prepare-rollback` (`:system`), the lease step of `Install-Arcilogger.ps1 -Rollback` | `{"at": <one timestamp>, "count": <FortiGate leases lowered to low>}` | the seed of the re-upgrade, which restores `high` on the `fg_dhcp` leases whose `updated_at` equals `at` and deletes the row |

The administrator-owned rows of the legacy keys `nat.public_ips`, `nat.wan_transip` and
`lookup.port_hint` are kept unread for one version after revision `0018_nat_egresses` (a
rollback without a database restore then runs the previous version with its own NAT values); the
new code never reads, lists or writes them, and the first revision after Wave 3 deletes them.

Not created yet (later milestones):
`nat_index` (the `fg_traffic` index `(transip, transport, time)` serves spec 9.3 for now; the
matching rule and the bounded time scan, setting `lookup.max_session_seconds`, are in
`docs/MVP-CONTRACTS.md` section 5).

## Audit log (`audit_log`)

`hash = sha256(prev_hash || canonical_json(entry))`, `prev_hash` of the first entry is 64 zeros.
The entry fields and the spool line format are specified in `docs/MVP-CONTRACTS.md` and
`api/src/arcilogger_api/audit.py`. A trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` on the
table; there is deliberately no foreign key on `actor_id`, so no change elsewhere can alter a
hashed row. `prev_hash` is unique, so the chain can never fork. Every entry is also written to
`spool/_audit/<day>.jsonl` (the day it is written; that file is never appended to after
midnight), which the nightly sealer puts into the vault and covers by the manifest. An
incomplete last line left by an interrupted write is cut off by the API before its next append
(the entry is rewritten from the database); the sealer refuses such a file and retries.

## Licence tables (`license_*`)

Revision `0014_license` (licensing contract 4.1, [ADR-0013](adr/0013-offline-licensing.md),
customer guide [LICENSING.md](LICENSING.md)). All three tables are **append-only**: one function
`arci_license_immutable()` and, per table, a `BEFORE UPDATE OR DELETE … FOR EACH ROW` trigger
(`<table>_no_update_delete`) and a `BEFORE TRUNCATE … FOR EACH STATEMENT` trigger
(`<table>_no_truncate`) reject every change with `insufficient_privilege` ("<table> is append-only
(<op> rejected)"), the pattern of the audit log. The tables are an index of what happened, not the
licence: every licence field that is displayed, audited, written to the status file or used for a
decision comes from `license_files.content`, verified again at that moment. A database owner who
disables the triggers therefore changes no licence decision.

| Table | Key | Content |
|---|---|---|
| `license_installation` | `id` = 1 (`ck_license_installation_single`) | The installation's identity and trial start (one row, never changed): `installation_id` (UUID4, unique; in the request code, informational), `trial_started_at` (Python clock; backdated to the earliest audit entry, `license.trial_start` entry or licence event when such traces exist), `created_at`. Created by the seed of `arcilogger-db upgrade`, else by the first status read |
| `license_files` | id; unique `sha256` | Licence files as installed (exact bytes; never changed or deleted): `content` (BYTEA, 1–65,536 bytes), `sha256` of the bytes, `payload_sha256` (SHA-256 of the canonical payload, the same for every reformatted copy; indexed), and as an index only `license_id` (indexed), `key_id`, `licensee`, `issued_at`, `first_installed_at`, `first_installed_by` (user name, or `os:<windows user>` for the CLI) |
| `license_events` | id; index `ix_license_events_kind_id` (kind, id) | Licence history: `at` (Python clock), `kind` (`install`, `remove`, `refuse`, `limit`, `state`), `source` (`web`, `cli`, `worker`, `seed`), `actor` (user name, `os:<user>` or `system`), `license_file_id` (FK `license_files`, RESTRICT; required for `install` / `remove`), `details` (JSONB: the full details, incl. licensee and server ids, which the audit entries leave out). The **active licence** is the file of the newest `install`/`remove` event when that event is `install` |

The licence state itself is never stored: it is computed (`arcilogger_db.licensing.load_status`)
from the active file, the server id, the installation row, the trusted time and the usage counts
(active `users`; `sources` configured or seen within 30 days). The worker job `license.check`
records state changes and limit crossings as events and audit entries and writes
`state/license-status.json` for the installer.

## TimescaleDB (optional) or native monthly partitions

The baseline migration checks `pg_available_extensions` **before** it creates the time-series
tables:

| `[database] timescale` | TimescaleDB installed | Result |
|---|---|---|
| `auto` (default) | yes | `CREATE EXTENSION timescaledb`; the 15 time-series tables of the baseline become hypertables (`time`, 1-day chunks); compression (`segmentby source`, `orderby time DESC, id DESC`) with a 7-day policy where the edition supports it; `timescaledb.telemetry_level = off` for the database. Revision `0017_fortigate_dhcp` creates the 16th, `fg_dhcp`, in the storage the baseline chose (a hypertable with the same chunks and compression when `messages` is one, else natively partitioned) and appends it to the `db.timescale` status |
| `auto` | no, or `CREATE EXTENSION` fails (e.g. not in `shared_preload_libraries`) | native partitions (below); the reason is recorded and `arcilogger-db check` warns when TimescaleDB is installed but unused |
| `require` | no | the migration fails |
| `off` | any | native partitions |

**Native partitions** (plain PostgreSQL, the likely Windows production mode until TimescaleDB on
Windows is verified): every time-series table is `PARTITION BY RANGE (time)` with a
`<table>_default` partition; `arcilogger_db.partitions.ensure_partitions` creates monthly
partitions `<table>_pYYYYMM` (bounds at local midnight Europe/Istanbul of the 1st) for the whole
`db.retention_days` window (the window of the ingest vault re-load; at least one month back) up
to three months ahead. It runs in `arcilogger-db upgrade`, in `arcilogger-db partitions` and
(contract) daily in the worker. Rows of a month without a partition wait in the default
partition and are moved when the partition is created; afterwards the default partition only
holds outliers (e.g. a device clock in 2001). `arcilogger-db check` warns when next month's
partitions are missing.

The outcome is stored in the setting `db.timescale` (`storage`: `hypertables` or `partitions`)
and shown by `arcilogger-db check`. `timescaledb.telemetry_level = off` (TimescaleDB reports
telemetry by default; ARCILOGGER sends nothing anywhere): the installer sets it for the
database before `CREATE EXTENSION` and server-wide (`ALTER SYSTEM`) right after it, and
KURULUM 2.1 adds it to `postgresql.conf`. Tested on PostgreSQL 16.15 (plain, native partitions) and TimescaleDB 2.30.2 on
PostgreSQL 16.

## Retention

- Vault (legal store): ≥ 730 days, default 760 (`arcilogger-core retention`; off by default,
  `[retention] enabled = false`).
- Database: intended 400 days (setting `db.retention_days`, today only the re-ingest and lookup
  horizon). **No deletion job exists yet for the log tables** — nothing is dropped. The job is
  planned for a later milestone: chunks on TimescaleDB (`drop_chunks` / a retention policy),
  whole monthly partitions on plain PostgreSQL (`arcilogger_db.partitions.drop_before`, already
  written and tested but not called: drops every month that ends on or before the cutoff and
  deletes older rows of the default partitions; the month containing the cutoff is kept, so up to
  one extra month stays until the next run).
- Usage rollups (`usage_*`, `domain_categories`): `usage.retention_days` (400), deleted by whole
  days by the worker job `usage.retention` (daily after 03:30; `domain_categories` rows whose
  `last_day` is before the cutoff); they are derived and can be rebuilt from the logs while those
  are in the database. The same job deletes finished rebuild requests (`usage_rebuilds`, done or
  failed) 90 days after they finished.
- The licence tables (`license_installation`, `license_files`, `license_events`) are append-only
  and have no expiry.
- The search summary table (`search_counts`) follows the log tables: `partitions.drop_before`
  also deletes its rows with `hour` before the first day of the cutoff's month (the month
  `drop_before` keeps whole keeps its counts), so the counts and the rows stay in step once
  database retention runs; until then nothing is deleted (no job calls `drop_before`).
  Saved searches (`saved_searches`) are deleted with their user.
- `lookups`, `lookup_reports` (and the PDFs in `exports\reports\`), `audit_log`,
  `user_sessions`, the assignment history (`device_assignments`, `persons`) and the history of
  the browser administration (`admin_requests`) have no expiry yet; the open decisions are
  listed in
  [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md).
- Older data stays available in the vault; re-ingesting a sealed day on request ("arşivden yeniden
  yükle") is a later feature.

## Seed data

`arcilogger-db upgrade` seeds after every migration run (idempotent, `arcilogger_db.seed`):

- **default settings only** (`arcilogger_db.settings.DEFAULT_SETTINGS`), e.g.
  `nat.egresses = {}` (no internet exit entered: one implicit double-NAT exit until the site
  enters its lines), `lookup.tolerance_seconds = 60`, `lookup.max_session_seconds = 86400`,
  `lease.server_seconds = {}` (every DHCP server `lease.default_seconds`, 1 day, until the site
  enters other lease times), `lease.timeout_percent = 110` (spec 9.1 "× 1.1" as an integer),
  `auth.idle_minutes = 15`, `db.retention_days = 400`. All values are canonical JSON (no floats),
  so every change can be audited.
- **no network** (owner decision 2026-10-20, ASSUMPTIONS P4): ARCILOGGER ships no VLAN plan, no
  departments and no log sources — they are the site's own data. A new installation starts with
  empty `vlans` and `departments` tables; the administrator enters both on the Cihazlar ve
  Kişiler page (`/api/v1/vlans`, docs/MVP-CONTRACTS.md section 11). A VLAN without a linked
  department has **its Turkish name** as department (spec 2.2), applied in one place:
  `arcilogger_db.network.vlan_department` / `department_for_ip`.
- Rows an earlier version seeded (a VLAN plan and departments; marker setting
  `seed.network_version`) stay as they are. Revision `0013_site_data` hands a
  `lease.server_seconds` row the seed still owned to `:migration`, so such a site keeps its lease
  times instead of following the new empty default.
- The tests use a fictional example site explicitly (`arcilogger_db.testing.seed_example_network`,
  `EXAMPLE_VLANS`: a FortiGate in front of a MikroTik core router; static VLANs 10 for network
  devices and 20 for servers, three department VLANs 41, 42 and 43 and the staff Wi-Fi 60 with a
  DHCP server each, `dhcp<VLAN id>`, a guest Wi-Fi 61 marked `guest` with 1 h leases, a /30
  transit network without a VLAN id); the product never writes it.

A value an administrator changed is never overwritten; a row still owned by the seed follows a
changed default in a later version. Settings are changed with
`arcilogger-db settings set KEY JSON_VALUE --by NAME` — on the server
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe"`, the installer does not put `venv\Scripts` on `PATH` —
(validated, written with `updated_by = NAME`, audited as `settings.change`). The validation keeps
the spec 13 protections: `auth.totp_required_roles` must contain `admin` and `auditor`, and the
`auth.*` limits have bounds (`idle_minutes` 1–120, `absolute_hours` 1–24, `lockout_attempts`
1–20, `lockout_minutes` 1–1440, `password_min_length` 12–128; `INTEGER_RANGES`). A hand-written SQL `UPDATE` that leaves
`updated_by = ':seed'` is **reverted by the next `arcilogger-db upgrade`** and is not audited.

## Schema changes

The baseline revision is frozen. A change goes into a **new revision file** in
`arcilogger_db/migrations/versions/` together with the model change; the sync test fails if the
two disagree, and a test requires a single head (linear history).
