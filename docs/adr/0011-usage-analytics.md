# ADR-0011: Usage analytics — estimated time per person, site and category from connection logs

- Status: proposed (2026-10-20)
- Deciders: the product owner (detail decision of 2026-10-20); proposed with group 2, area U, build
  specification 10.1-10.4, 3.3 and 17 (M4)

## Context

Specification 10 asks for per person / device / department / day usage: online time, an
**estimated** active time, time per site and category, data volume, sessions, blocked attempts,
first/last activity, an hour-by-hour heat map, applications and off-hours use — with the reports
of area R built on the same numbers. The only inputs are connection logs: FortiGate traffic logs
(with interim logs `0000000020` and their `…delta` fields for long sessions), web filter, DNS
filter and application control logs, and the lease timeline / device assignment history that the
5651 lookup already uses (ADR-0008). Nothing measures what a person looked at: every duration is
an inference from bytes and connections.

The owner decided on 2026-10-20 that the reports are detailed and carry names (group 2 contract
section 1): this is the shipped default, and each organisation must establish its own legal basis
(e.g. consent or an information text; legal review recommended); pseudonyms remain an option.

## Decision

1. **The subject is the lookup's answer.** Usage belongs to the person the device was assigned to
   *at that moment*, else the device (the lease's MAC), else the bare IP address
   (`arcilogger_worker.usage.subjects`). The rollup does not have its own attribution rules: it
   asks the lookup engine's `Attributor` (lease → device → person at T through
   `device_assignments`, IP conflicts, infrastructure devices) at the midpoint of every interval in
   which the answer cannot change (cut at lease starts/ends — an open lease where it would time out
   — and at assignment starts/ends). A 1-minute bucket belongs to the subject of its first instant,
   so a device reassigned at noon splits a session across the noon minute.
2. **Sessions → 1-minute buckets** (`usage.engine`). Logs of a session (`srcip`, `sessionid`,
   `dstip`, `dstport`) are read in time order; an interim log with deltas adds its deltas over
   `[time - durationdelta, time]`, any other log adds what its cumulative counters exceed the
   increments so far over `[previous log, time]` ("final remainder"): interim and final bytes are
   never counted twice, and bytes of a lost interim log arrive with the final log; what the first
   interim log read holds beyond its deltas is added from the session start to its interval (a
   lost first interim log; the part of a long session before the day stays with the earlier
   day). Each increment is spread evenly over its interval. *Online*: every minute a session is
   open. *Active*: every minute with at least `usage.active_bytes_threshold` (2048)
   non-background bytes. The 60 s of an active minute are split between the domains by their
   non-background bytes, so the time per site adds up to the active time (the "raw" time — 60 s
   for every minute the domain had traffic at all — is kept for the drill-down). Days are site
   days (Europe/Istanbul); logs up to `usage.session_overlap_seconds` (30 min) after midnight
   still count for the day before.
3. **Destination naming** in the order of spec 10.2: web filter host name by `sessionid` (and
   `srcip`), application control / SSL host name by `sessionid`, the newest DNS answer of the same
   `srcip` containing `dstip` within `usage.dns_window_seconds` (600 s) before the session started,
   the FortiGuard ISDB name (`dstinetsvc`, a "service"), else the address. A session that started
   before the logs read for the day (`usage.session_overlap_seconds` before midnight) is named
   from the logs of its start, as on the earlier day. Spec 10.2 step 5's "cached reverse DNS" is
   **not** done: the server makes no DNS queries of its own (closed network; ASSUMPTIONS U3).
4. **Registrable domains from a bundled Public Suffix List.** `publicsuffixlist` (pinned
   `1.0.2.20261007`, MPL-2.0 for the code and the list, used unmodified as a separate library — the
   same file-level licence as for any MPL dependency) ships the list inside its wheel and reads it
   from the package directory; it never downloads (its updater is a separate script ARCILOGGER
   does not call). An administrator may place a newer `public_suffix_list.dat` on the server and
   name it in `usage.psl_file`. Only the ICANN section counts by default
   (`usage.psl_private_suffixes`): private suffixes such as `cloudfront.net` would split one CDN
   into thousands of "sites".
5. **Background traffic** (spec 10.3; settings with the spec's defaults): host name patterns,
   ports (DNS 53, NTP 123, DNS over TLS 853), sessions below 2 KB in total, and application
   categories (`Update`, `Network.Service`) **only for sessions without a host name**. The last
   restriction is a deviation from the spec: FortiOS 7.6 can log ordinary HTTPS browsing as
   `SSL_TLSv1.3.PQC` in `Network.Service`, and the literal rule would then make almost all browsing
   background (ASSUMPTIONS U4; verify on site).
6. **Rollups are plain tables of aggregates, rebuilt per day** (revision `0010_usage`):
   `usage_days` (bookkeeping), `usage_daily`, `usage_device_daily`, `usage_domain_daily` (with the
   host names as JSON), `usage_app_daily`, `usage_hourly`, `usage_blocked`, `usage_urls`,
   `domain_categories`, `usage_rebuilds`. Every row is split into work hours / off hours
   (`off_hours`), so the `hours` filter is a `WHERE` and "all" is a sum. Names, departments and
   categories are joined when read: renaming a person or changing a department needs no rebuild.
   Sizing for 150 people (≈ 250 subjects) and 400 days: `usage_domain_daily` ≈ 250 × 100 × 400 ≈
   10 M rows, `usage_hourly` ≈ 250 × 24 × 400 ≈ 2.4 M, `usage_urls` ≤ `usage.urls_per_day` (1000)
   per subject and day (with HTTPS certificate inspection mostly one URL per host name) — not log
   volumes, so **no hypertables or partitions**: indexes lead with `day` or `subject`, a day is
   replaced by `DELETE … WHERE day = :day` plus inserts in one transaction, and retention
   (`usage.retention_days`, 400) deletes whole days.
7. **Jobs** (`usage.jobs`): `usage.today` every 5 minutes recomputes today from what is loaded
   ("incremental" in the sense of spec 10.4: the current day is refreshed, earlier days are not
   touched; a full-day recompute is simpler and correct for sessions that span hours, and fits the
   budget below); `usage.daily` at 00:45 rebuilds yesterday as `final`; `usage.reattribute` every
   10 minutes rebuilds any of the last `usage.auto_rebuild_days` (7) days whose **inputs
   fingerprint** (engine version, `usage.*` settings, suffix list, ingest progress of the day's raw
   files, taken before the logs are read: a log loaded meanwhile changes it) or **attribution
   fingerprint** (who every source address of the day belonged to when, recomputed with the
   current leases and assignment history) changed, or that is missing or still partial — so
   linking a device to a person, or correcting the lease timeline, is applied to the recent days
   automatically; older days need `arcilogger-worker usage rebuild --from --to` or the web
   interface's rebuild (`POST /usage/rebuild`, run by the worker's `usage.requests`). Long jobs
   stop starting new days after 120 s and continue with the next run. A per-day advisory lock
   serialises the jobs, requests and the command line.
8. **API and privacy** (owner decision 2026-10-20): `/api/v1/usage` shows names, MAC and IP
   addresses to every role with `usage.person` or `usage.anonymous`; `dashboard.usage` alone sees
   only the company endpoint. Person-level endpoints keep `require_privacy_notice`; viewing one
   subject (`usage.view`), every export (`usage.export`), rebuild requests and the pseudonym mapping
   are audited with ids and counts, never names. Pseudonyms (`HMAC-SHA-256` with the secret
   `usage.pseudonym_key`) only when `usage.pseudonymize_anonymous_role` is on, for viewers with
   `usage.anonymous` but without `usage.person`. `persons.usage_excluded` removes a person from
   person-level views and reports (company and department totals still count the traffic).
9. **Report data** for area R (`usage.report_data.build_report_data`, schema 1) reads only the
   rollups; part-day windows (end-of-day report 00:00-18:30) use the hourly rows for times and
   volumes, the active-minute timelines for the first/last activity and whole days for sites,
   categories and sessions, and say so in `data_quality`.

## Consequences

- The figures are estimates and are labelled as such everywhere (`estimated: true`, the method note
  in the web interface, the reports and every Excel export). Known blind spots: QUIC/HTTP3 and other
  traffic the web filter does not log is named only by DNS or ISDB; DNS over HTTPS hides the names
  (address or ISDB label only); devices whose traffic does not pass the FortiGate (e.g. a guest
  Wi-Fi with another exit than the FortiGate, LAN-only traffic) are invisible; a tab left open with
  background polling may count as active; shared or NATed devices cannot be split between people.
- A session that runs over midnight without interim logs and ends more than 30 minutes after
  midnight loses its part before midnight on the final rollup of the earlier day (interim logs,
  `0000000020`, are a device setting, and so is their interval: verify per installation).
- Measured on the development machine (4 CPUs shared, PostgreSQL 16 without TimescaleDB):
  50 people, 100 user devices plus infrastructure, one Wednesday from the load generator
  (92,674 lines; 24,309 traffic, 16,185 web, 32,370 DNS, 15,866 application control rows):
  **1.7 s** for the full-day rollup (compute + write; 2.1 s for the same day again); five
  times the browsing intensity (450,182 lines, 123,097 traffic rows): **6.0 s**. Budget
  asserted by `worker/tests/test_usage_performance.py`: 30 s and 120 s. Linear extrapolation
  to the specification's busy day (5 million messages, ≈ 1.4 M traffic rows) gives about 70 s
  per rollup — the 5-minute refresh of today then costs up to ~25 % of one core late in the
  day.
- The re-attribution check recomputes the attribution of every source address of the last 7 days
  every 10 minutes (a few queries per day; no log table is scanned).
- `publicsuffixlist` and `openpyxl` (MIT; Excel exports) are new dependencies; both are pure
  Python. A newer suffix list arrives with a dependency upgrade or `usage.psl_file`.

## Alternatives considered

- **Incremental hour-by-hour rollups:** sessions span hours, distinct-session counts and
  per-domain splits are not additive per hour; correctness would need per-session state between
  runs. Rejected for the MVP; the full-day recompute meets the budget.
- **Hypertables / monthly partitions for the rollups:** they hold aggregates (≈ 10 M rows for 400
  days), not log lines; whole-day deletes on a leading `day` index are cheap. Can be added later
  without changing the readers.
- **`tldextract`:** also bundles a snapshot but fetches the list over HTTPS by default and needs
  explicit configuration not to; `publicsuffixlist` never does.
- **Reverse DNS for unnamed addresses:** would send queries for internal users' destinations to a
  resolver; not done (spec 0: nothing about the logs leaves the server).
