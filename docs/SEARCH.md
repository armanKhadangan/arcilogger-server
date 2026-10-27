# Search (Arama) — query language, speed, statistics, export

> Türkçe: [ARAMA.md](ARAMA.md). API contract: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) section 17.
> Decision record: [adr/0015-search-live-sources.md](adr/0015-search-live-sources.md).
> Permissions: [roles.md](roles.md). Security: [SECURITY.md](SECURITY.md) section 15.
>
> **Licence:** Search and the live tail (Canlı Akış) belong to **5651 Core**; no licence state
> locks them ([LICENSING.md](LICENSING.md) section 2.2).

The Arama page searches the log records in the database (`messages` and the typed tables:
traffic, web filter, DNS, DHCP …) within a time range, shows the results page by page, counts the
timeline and the most frequent values, keeps saved searches, exports the results as CSV or Excel
and hands a search's raw lines over to an evidence package. **Raw log files are never
modified**; Search only reads, and every query runs in a read-only database transaction.

This page shows personal data; queries and opened raw lines are written to the audit log. Only
administrators and auditors who accepted the KVKK privacy notice can open it (section 11).

## 1. Query language

A query is a list of **conditions** separated by spaces; all of them must hold (AND).

| Written | Meaning | Example |
|---|---|---|
| `field:value` | the field equals this value | `srcip:172.20.41.105` |
| `field:a,b,c` | the field is one of these values (OR within one field) | `action:deny,blocked` |
| `field:*` / `-field:*` | the field is present / absent | `user:*` |
| `-condition` or `NOT condition` | the opposite of the condition | `-action:passthrough` |
| `word`, `"a phrase"` | free text (section 4) | `"login failure"` |
| `AND` | ignored (conditions hold together anyway) | `type:web AND action:blocked` |

- **No OR and no parentheses:** a bare `OR`, `(` and `)` are refused (`search_or_unsupported`);
  use commas between the values of one field.
- **Field names** ignore case, Turkish letters are folded (`Alanadı:` = `alanadi:`) and every
  field has Turkish aliases (section 3).
- **A word containing `:`** is read as a field; if there is no such field the query is refused
  (`search_unknown_field`), the word is not searched as text. Put a URL or an IPv6 address in
  quotes to search for it as text. If the whole word is a MAC address the hint is `mac:<value>`.
- **Inside quotes** only `\"` and `\\` are escapes.
- **Limits** (`search_too_complex`): at most 1,000 characters, 16 conditions, 20 values per
  condition, 5 free-text terms and 256 characters per value.
- **Time is not part of the query:** the time range is chosen next to it (section 5).

The query is checked every 250 ms while it is typed (`POST /search/parse`, no database work);
the faulty part is underlined. The `?` button opens the list of fields, examples and which
queries are fast.

Examples: `srcip:172.20.41.105 type:web action:blocked` · `domain:example.com -action:passthrough`
· `"login failure" source:core-r1` · `vlan:41 dstport:80,443` · `type:dhcp mac:00:00:5e:00:53:21`
· `error:fortigate.*`

## 2. Value forms

| Field kind | Accepted | Note |
|---|---|---|
| IP (`srcip`, `dstip`, `natip`, `ip`, `sender`) | `172.20.41.5`, `172.20.41.0/24`, `172.20.41.*`, `172.20.*` | IPv6 can be written; IPv6 networks are outside the supported scope |
| Port and number (`srcport`, `dstport`, `natport`, `port`, `sessionid`) | `443`, `1000-2000`, `>1024`, `>=1`, `<10`, `<=10`, comma lists | |
| Protocol (`proto`) | `tcp`, `udp`, `icmp` or the protocol number | |
| MAC (`mac`) | any separator and case (`00-00-5E-00-53-21`, `0000.5e00.5321`); vendor prefix `00:00:5e:*` | |
| Domain (`domain`) | `example.com` (the name and its subdomains), `*.example.com` (subdomains only); lowercased, international names in punycode | |
| DNS name (`qname`) | the exact name | |
| Token (`action`, `qtype`, `level`, `logid`) | the exact value, compared after lowercasing | |
| Text (`app`, `category`, `service`, `user`, `host` …) | exact value (case ignored for English letters), `foo*` prefix, `*foo` suffix, `*foo*` contains | `%` and `_` inside a value are searched as themselves |
| Kind (`type`) | the kinds of section 6, several with commas | |
| Severity (`severity`) | `emerg` … `debug` or 0–7, with comparisons (`<=err`) | |
| Parse error (`error`) | pattern name, `prefix*` or `*` alone | |
| VLAN (`vlan`) | VLAN ids (comma list) | the VLAN subnet of the client address (Devices and persons → VLANs) |
| Department (`department`) | department name or id | the default department of the VLAN, **not** a person's assignment (notice `department_by_vlan`) |

**Negation:** `-action:accept` finds "records that have an action other than accept"; a record
whose field is empty also satisfies a negated condition. Only the kinds of records that carry the
field are searched.

## 3. Fields

The list is the server's field catalogue (`GET /search/fields`; a test compares this table with
the catalogue). "Fast" names the tables where the field alone is read from an index.

| Field (aliases) | Kind | What | Fast |
|---|---|---|---|
| `source` (`kaynak`) | source | the log source's name, with `*` at its end if needed; a source defined as a network (e.g. `ap`) also finds its members (`ap-<ip>`) | `messages`, the MikroTik event tables |
| `sender` (`gonderen`) | IP | the address of the device that sent the line (unknown senders included) | — |
| `type` (`tur`, `tip`) | kind | the record kind (section 6) | `messages` |
| `subtype` (`alttur`) | text | the subtype the device sent | — |
| `host` (`cihaz`) | text | the host name of the syslog header or the FortiGate `devname` field | — |
| `program` (`konu`) | text | the syslog program or the MikroTik topics | — |
| `severity` (`onem`) | severity | the syslog severity | — |
| `error` (`hata`) | error | the pattern a line could not be parsed with | — |
| `srcip` (`kaynakip`, `istemci`) | IP | the client's address or network — the fastest field | traffic, web, DNS, application, IPS, virus, DHCP, ARP, MikroTik firewall, logins |
| `dstip` (`hedefip`) | IP | the address connected to | traffic, MikroTik firewall |
| `natip` (`transip`) | IP | the FortiGate's address after source NAT | traffic |
| `ip` (`adres`) | IP | any of the source, destination or NAT addresses | traffic, MikroTik firewall, DHCP, ARP, logins |
| `srcport` (`kaynakport`) | port | the client's port | — |
| `dstport` (`hedefport`) | port | the port connected to | traffic, together with `dstip:` |
| `natport` (`transport`) | port | the port after source NAT | traffic, together with `natip:` |
| `port` | port | the source or the destination port | — |
| `proto` (`protokol`) | protocol | tcp, udp, icmp or a number | — |
| `action` (`eylem`) | token | the device's action (deny, blocked, passthrough, assigned …) | — (statistics from the summary table) |
| `domain` (`alanadi`, `site`) | domain | the domain and its subdomains | web filter |
| `qname` | DNS name | the exact name asked in DNS | DNS |
| `url` | URL | text the URL contains | — |
| `app` (`uygulama`) | text | the FortiGate application | — |
| `appcat` | text | the FortiGate application category | — |
| `category` (`kategori`) | text | the FortiGuard category name (web and DNS filter) | — |
| `service` (`servis`) | text | the FortiGate service | — |
| `country` (`ulke`) | text | the destination's country | — |
| `policy` (`politika`) | policy | the FortiGate policy number or name | — |
| `sessionid` (`oturum`) | number | the FortiGate session id | traffic, web, application |
| `user` (`kullanici`) | text | the user who signed in to a device or changed it | — |
| `mac` | MAC | the device's MAC address or a vendor prefix | DHCP, ARP |
| `vlan` | VLAN | the VLAN of the client address | like `srcip` |
| `department` (`departman`) | department | the department of the client address's VLAN | like `srcip` |
| `interface` (`arayuz`) | text | the incoming or the outgoing interface | — |
| `qtype` | token | the type of the DNS query (A, AAAA, MX …) | — |
| `attack` (`saldiri`) | text | the name of the IPS signature | — |
| `virus` | text | the name of the virus found | — |
| `logid` | token | the FortiGate `logid` field | — |
| `level` | token | the event's level (FortiGate, MikroTik) | — |
| `text` (`metin`) | free text | the record's text (section 4); also searched without a field name | — |

## 4. Free text

- A word or a quoted phrase, **at least 3 characters** (`search_term_too_short`).
- Matched in the **first 2,000 characters** of the record's text (`messages.text`); the whole
  line is in the raw record (notice `text_truncated`). For FortiGate lines the text is the whole
  key=value body.
- **Case** is ignored for English letters only: İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç are matched as
  typed (notice `text_case`).
- Free text is not a field: `172.20.41.10` as text also finds `172.20.41.105`; use `srcip:` or
  `ip:` for an address.
- Free text uses no index; a query of free text alone is scanned in time slices (section 5).

## 5. Time, pages and the scan

- **The time range** is `[from, to)` on **device time** (`time`, the time the device reported
  for the line); the receive time is shown as well. At most `search.max_days` (31) days. Presets:
  the last 15 minutes, 1 hour, 24 hours, today (since midnight), yesterday, the last 7 and 30
  days.
- **Before the database:** when `from` lies before the oldest record in the database the notice
  `vault_only_before` is shown; lines before that day are in the sealed vault and are taken out
  with an evidence package.
- **The scan:** results come newest first. The server reads the range in time slices from its
  newest end: the first slice is 1 hour, a slice that answers quickly doubles (2, 4, 8 hours …).
  A request takes at most `search.request_budget_ms` (4 seconds). When the page fills or the
  start of the range is reached the scan is **complete** (`complete`). When the time runs out the
  page shows what was found so far and says "scanned up to …"; the page continues the scan on its
  own up to 5 times, then shows a "Devam et" (continue) button. If the first slice runs out of
  time it is halved (down to 5 minutes); if even 5 minutes do, the search ends with
  `search_timeout` (504) and is audited with `outcome: "timeout"`.
- **Pages:** a search gets a `search_id`; the next pages, the statistics, the export and the
  evidence hand-over are asked for with that id only. The id stays valid for 30 minutes after its
  last use (at most 20 searches are kept per user); when it has expired or belongs to another
  session the page reruns the search once on its own (a new `search.run` entry). At most
  `search.page_size_max` (500) rows per page; at most 2,000 rows in view.
- **Limits:** at most `search.max_concurrent` (3) search requests run at a time, 2 per user;
  if no slot frees within 2 seconds the answer is `search_busy` (429). Per user
  `search.runs_per_minute` (30) new searches a minute (`search_rate_limited`).

### 5.1 Which queries are fast?

Speed depends on whether the query can be read from an **index**. When one of the fields of the
"Fast" column of section 3 (e.g. `srcip:`, `domain:` + `type:web`, `qname:`, `mac:`, `dstip:` +
`dstport:`) is in the query as a positive condition, the server reads only the matching records,
and a 30-day range answers within seconds too. A query of free text alone, of fields without an
index such as `action:` or `user:`, or of negated conditions alone is scanned slice by slice; the
page says so with the notice `query_not_selective` ("Add a field such as srcip:, domain:, mac: or
qname: for faster results"). The measured times are in section 13.

## 6. Record kinds

The values of the `type:` field; the same names are used in the statistics and the export.

| `type:` | English | From |
|---|---|---|
| `traffic` | Traffic | FortiGate traffic |
| `web` | Web filter | FortiGate UTM webfilter |
| `dns` | DNS | FortiGate UTM DNS |
| `app` | Application and SSL | FortiGate UTM application control, SSL |
| `ips` | Intrusion prevention | FortiGate UTM IPS |
| `virus` | Antivirus | FortiGate UTM antivirus |
| `utm` | Other UTM | other FortiGate UTM subtypes |
| `event` | FortiGate event | FortiGate events |
| `dhcp` | DHCP | MikroTik DHCP (pool events included) |
| `arp` | ARP | MikroTik ARP |
| `fw` | MikroTik firewall | MikroTik firewall |
| `account` | Logins | MikroTik logins |
| `interface` | Interface | MikroTik interface |
| `config` | Configuration | MikroTik configuration changes |
| `wifi` | Wireless | MikroTik wireless connect/disconnect |
| `heartbeat` | Heartbeat | MikroTik heartbeat |
| `log` | MikroTik log | MikroTik log, warning and above |
| `syslog` | Generic syslog | generic syslog lines |
| `unknown` | Unrecognised | unrecognised lines |
| `other` | Other | every other kind |

## 7. Statistics

A search's timeline (histogram), total and most frequent values (source, kind, action, VLAN,
department; the top 10 values and "other") are asked for separately (`POST /search/stats`) and
counted in one of three ways:

| Way (`mode`) | When | How |
|---|---|---|
| `rollup` | the query has only `source`, `type`, `action`, `vlan`, `department` (and their negations), no free text, the VLANs are private IPv4 networks of /24 or larger, the range is longer than 12 hours and the summary counts are ready | from the hourly summary table (`search_counts`); instant even for 30 days |
| `hybrid` | the same, but the range does not start or end on the hour | full hours from the summary, the partial edge hours counted directly |
| `direct` | every other case | the records are counted directly, within `search.stats_budget_ms` (8 seconds) |

- **Bucket size:** 1 minute up to 2 hours, 5 minutes up to 12 hours, 1 hour up to 3 days,
  3 hours up to 14 days, 6 hours for longer ranges; buckets are aligned to site midnight.
- **When counting directly** the total and the histogram count every match; the most frequent
  values come from the newest `search.facet_sample_rows` (100,000) matches and are then labelled
  "son 100.000 eşleşmeye göre" (from the latest 100,000 matches, `sampled`). When the time runs
  out the count shows what it counted so far (`complete: false`, the uncounted part shaded, the
  total "≥ N").
- For a query without a kind (read from `messages` only) the action, VLAN and department cannot
  be counted ("Bir tür seçin (ör. type:web)" — choose a kind, e.g. type:web); source and kind
  are.
- **VLAN and department** come from the client address's /24; a /24 holding several smaller
  VLANs is shown as "Birden çok VLAN" (several VLANs). The department is the VLAN's default, not
  a person's assignment (usage per person: [USAGE.md](USAGE.md)).
- **While the summary counts are being prepared** (days loaded before the upgrade) the
  statistics are counted directly with the notice `counts_incomplete`; the job
  `search.counts_backfill` fills the summary table in the background.

## 8. Saved searches

A query and a time range (a preset or fixed dates) are saved under a name. Saved searches are
**personal**: every user sees only their own; another user's id answers "not found" (404). At
most `search.saved_max_per_user` (100) per user; names are unique ignoring case and cannot contain control or invisible format characters (such
as NUL or a direction override). Running a saved
search is an ordinary search (`search.run`, with `saved_id`); a preset is resolved at the time it
runs.

## 9. Export (CSV, Excel)

- Only an **administrator** (`search.export`) can export, with the privacy notice accepted and
  **after re-entering the password within the last 5 minutes**.
- The search's rows are read up to `search.export_max_rows` (100,000) rows or
  `search.export_budget_seconds` (120 seconds); a file that hits a limit is marked **cut**: its
  name ends in `-kismi`, the answer carries the header `X-Arci-Export-Truncated: rows|time`, and
  the Excel "Bilgi" (information) sheet gives the reason. The queries that fill in the rows'
  fields use the same time; when it runs out the file is cut there (`time`), and no row is
  written with empty columns.
- **CSV:** UTF-8 (BOM), `;` separator, CRLF line ends. **Excel:** the sheets "Sonuçlar"
  (results) and "Bilgi", every cell text. Columns: Zaman, Alınma zamanı, Kaynak, Gönderici, Tür,
  Alt tür, Kaynak IP, Kaynak port, Hedef IP, Hedef port, NAT IP, NAT port, Eylem, Alan adı, URL,
  Kullanıcı, Uygulama, Kategori, MAC, Metin (at most 2,000 characters), Ham kayıt
  (`source/day/line/offset`). In the CSV, values starting with `=`, `+`, `-`, `@`, a tab or a
  carriage return get a leading `'` so they never run as formulas; in Excel every cell is text.
  Control and invisible characters are written as visible escapes (`\u0007`) in both.
- **The file is not sealed.** The "Bilgi" sheet says so: "This file is not sealed; use an
  evidence package for integrity." Use an evidence package for an official answer (section 10).
- The file is built, its SHA-256 computed and written to the audit log **before it is sent**
  (`search.export`: rows, bytes, SHA-256, the reason it was cut); the browser checks the file
  against the `Repr-Digest` header.
- **Limits:** one export per user at a time (`search_export_running`), at most
  `search.export_max_per_hour` (10) an hour, counted from the audit log, so it holds across a
  restart of the server (`search_export_rate_limited`).
- An exported file holds personal data; keeping and sharing it is subject to the organisation's
  KVKK rules.

## 10. An evidence package from a search

"Kanıt paketine aktar" (add to an evidence package; only with `evidence.create`, administrators)
takes the search's raw line references (`POST /search/refs`, at most 10,000 lines, the line limit
of an evidence package), writes the count and the SHA-256 of the list to the audit log
(`search.refs`) and fills the evidence packages page with these lines. If the search did not
complete, hit the line limit, or some lines are on days not sealed yet, the page says so before
the package is made. The package itself is made as always ([KANIT-PAKETI.md](KANIT-PAKETI.md)).

## 11. Who sees what, what is audited

| | Administrator | Auditor | HR | Viewer |
|---|---|---|---|---|
| Search, pages, statistics, record details, saved searches, live tail | ✔ | ✔ | — | — |
| Export (re-authentication 5 min) | ✔ | — | — | — |
| Add to an evidence package | ✔ | — | — | — |

Search and the live tail show personal data: they do not open before the privacy notice is
accepted (`privacy_notice_required`). Written to the audit log:

| Entry | When | Content |
|---|---|---|
| `search.run` | every search (reruns included) | search id, normalised query, range, kinds, rows of the first page, complete or not, outcome (`ok`/`timeout`), time taken, saved search id |
| `search.event` | when a record's details are opened | table, id, time, raw reference |
| `search.refs` | hand-over to an evidence package | search id, query, range, line count, cut or not, SHA-256 |
| `search.export` | before the file is sent | search id, query, range, format, rows, reason it was cut, bytes, SHA-256 |
| `search.saved_create` / `search.saved_update` / `search.saved_delete` | when a saved search changes | id, name, query |
| `live.open` / `live.close` | when a live tail opens / closes | stream id, query, folders; on close the duration, the lines shown and not shown, the line ranges |
| `raw.view` | when a raw line is opened | raw reference |

Not audited: the next pages and the statistics (they can only be asked for with the id of an
audited search), the query check (`/search/parse`) and the field list. Entries hold no row
content; only inputs, counts and hashes.

## 12. Live tail (Canlı Akış)

The live tail shows new lines by reading the **raw spool folder**; it works while ingest lags
or is stopped and puts no load on the database. It is the quickest way to see whether a device
sends logs ([DEVICE-SETUP.md](DEVICE-SETUP.md)).

- The filter is the query language of Search (it may be empty). Parsed fields are labelled
  "önizleme" (preview; ingest's final record may differ in details); a line not in the
  collector's format is shown with the badge "biçim dışı" (malformed).
- **Limits:** at most `live.max_lines_per_second` (100) lines a second per stream;
  `live.max_streams_total` (4) streams on the server at a time, `live.max_streams_per_user` (2)
  per user (`live_stream_limit`). Lines that could not be shown are counted and reported with
  their reason; a connection that cannot keep up for 30 seconds (e.g. one that stopped reading)
  has its stream stopped (`slow_client`). The page keeps at most 2,000 lines (the oldest go
  first).
- **Pause / resume:** a paused stream waits 120 seconds and keeps its slot; "Sürdür" (resume)
  continues where it stopped. A stream stops after `live.max_minutes` (60) minutes (unattended
  screen).
- **Session:** every 30 seconds the stream checks the session, the permission and the privacy
  notice; an idle user is signed out after `auth.idle_minutes` as on every page. The stream's
  background requests do not reset the idle timer.
- **Reconnect:** after a network error it reconnects after 1, 2, 5, 10, then 30 seconds. If the
  lines in between could not be shown the page says "The lines in between could not be shown;
  you can open this interval in Search." with a link that opens it in Search.
- Opening and closing a stream are audited (`live.open`, `live.close`); a line's raw form opens
  in the usual raw line view (`raw.view`).

## 13. Performance

Measured by `api/tests/test_search_perf.py` (`poe test-perf`); the data and the budgets are in
MVP-CONTRACTS.md section 17. Data: the newest day 2,000,000 lines and each of the 29 days before
it 100,000 lines (4.9 million lines and their typed rows), about 150 clients, rare values to
find. Times through the API (no browser), the best of three after one warm-up run.

Measured (2026-10-26) on a 4-CPU Linux development machine shared with other work, PostgreSQL 16 with native monthly partitions (no TimescaleDB); 4,913,180 `messages` rows were written in 130 seconds, ready with the summary table and statistics in 689 seconds. The TimescaleDB CI job runs the same test with the chunks older than 7 days compressed.
After this measurement the data gained 4,320 MikroTik events a day (`mt_event`; Q6 now checks
the event index too); the numbers below are from the data before that addition, and the
TimescaleDB and Windows CI jobs' numbers are not recorded yet.

| # | Query | Range | Budget | First run | Best | Note |
|---|---|---|---|---|---|---|
| Q1 | empty query, first page | 1 day | 500 ms | 216 ms | 59 ms |  |
| Q1 | empty query, first page | 30 days | 500 ms | 67 ms | 53 ms |  |
| Q2 | `srcip:<needle client>` | 1 day | 2,000 ms | 211 ms | 65 ms |  |
| Q2 | `srcip:<needle client>` | 30 days | 10,000 ms | 87 ms | 66 ms |  |
| Q3 | `srcip:172.20.41.0/24` | 1 day | 2,000 ms | 70 ms | 70 ms | common value: primary key |
| Q4 | `srcip:<needle> type:web action:blocked` | 30 days | 10,000 ms | 72 ms | 57 ms |  |
| Q5 | `domain:<rare domain> type:web` | 30 days | 10,000 ms | 70 ms | 61 ms |  |
| Q6 | `type:dhcp mac:<rare MAC>` | 30 days | 10,000 ms | 72 ms | 73 ms |  |
| Q7 | `qname:<rare name>` | 30 days | 10,000 ms | 58 ms | 58 ms |  |
| Q8 | statistics `type:web action:blocked department:Finans` | 30 days | 500 ms | 24 ms | 20 ms | mode `rollup` |
| Q9 | statistics `srcip:<needle>` (direct) | 1 day | 2,000 ms | 66 ms | 50 ms |  |
| Q10 | export of 10,000 rows (Q2) | 30 days | 10,000 ms | 2,715 ms | 2,549 ms |  |
| S1 | rare free-text term | 1 day | 4,500 ms | 2,377 ms | 2,386 ms | complete, 10 rows |
| S2 | `dstport:3389 action:deny` | 1 day | 4,500 ms | 94 ms | 62 ms | complete, 100 rows |
| S3 | `ip:<needle>` | 30 days | 4,500 ms | 162 ms | 114 ms | complete, 100 rows |

The plan of every query is checked too: with indexed fields (`srcip:`, `domain:`, `mac:`,
`qname:`) no high-volume table is read sequentially.

## 14. Error codes

Every error carries `code`, `message_tr` and `message_en`; the faulty part of a refused query
comes in `details` with its character position.

| Code | HTTP | When |
|---|---|---|
| `search_syntax` | 422 | the query cannot be read (unclosed quote, empty value …) |
| `search_unknown_field` | 422 | there is no such field |
| `search_value_invalid` | 422 | the value does not fit the field |
| `search_term_too_short` | 422 | free text shorter than 3 characters |
| `search_too_complex` | 422 | the query limits are exceeded |
| `search_or_unsupported` | 422 | `OR` or parentheses |
| `search_fields_incompatible` | 422 | no record kind has all of the fields |
| `search_vlan_unknown` | 422 | the VLAN is not defined |
| `search_department_unknown` | 422 | no such department |
| `search_range_invalid` | 422 | invalid time range |
| `search_range_too_long` | 422 | the range is longer than `search.max_days` days |
| `search_cursor_invalid` | 422 | the page position does not belong to this search |
| `search_expired` | 409 | the search id has expired (the page reruns the search) |
| `search_timeout` | 504 | not even the shortest slice (5 minutes) fit the time budget |
| `search_busy` | 429 | too many searches are running |
| `search_rate_limited` | 429 | too many searches in a short time |
| `search_event_not_found` | 404 | the record is not in the database |
| `search_event_timeout` | 504 | the record could not be read in time (the database is busy; it may exist) |
| `search_export_running` | 409 | the previous export is still running |
| `search_export_rate_limited` | 429 | the hourly export limit |
| `saved_search_not_found` | 404 | no such saved search |
| `saved_search_name_taken` | 409 | a saved search with this name exists |
| `saved_search_limit` | 409 | the saved search limit |
