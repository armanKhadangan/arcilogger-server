# Korelasyon ve 5651 sorgusu / Correlation and the 5651 lookup

> Durum: M3 (iş paketi B3). Kod: `worker/src/arcilogger_worker/correlation/` (IP–MAC zaman
> çizelgesi, cihazlar, IP çakışmaları), `worker/src/arcilogger_worker/lookup/` (5651 sorgu
> motoru), `worker/src/arcilogger_worker/service.py` (worker hizmeti). Bağlayıcı arayüz:
> `docs/MVP-CONTRACTS.md` bölüm 5. ARCILOGGER **5651 yükümlülüklerini desteklemek için
> tasarlanmıştır**; sonuçların resmi bir yanıtta nasıl kullanılacağı için hukuki görüş alınmalıdır.

## Özet (TR)

- **Kira zaman çizelgesi (`leases`)**: hangi MAC adresinin hangi iç IP adresini ne zaman
  kullandığı. Kaynaklar: MikroTik DHCP `assigned`/`deassigned` kayıtları, saatlik `ARP-LOG`
  kayıtları ve FortiGate trafik kayıtları (trafik yalnızca açık bir kaydı uzatır, asla yeni kayıt
  açmaz). RouterOS kira yenilemelerini loglamaz; bu yüzden açık bir kayıt, son kanıttan sonra
  "kira süresi × %110" boyunca kanıt gelmezse zaman aşımıyla kapanır (kira süresi varsayılan 1
  gün; farklı olan sunucular, ör. misafir ağınınki, `lease.server_seconds` ile girilir).
- Statik IP'li cihazlar (yazıcı, sunucu) yalnızca ARP-LOG ile görünür: `[ilk görülme, son
  görülme + 1 saat]`, **orta güven**.
- Aynı IP aynı anda iki MAC adresinde görünürse **IP çakışması** uyarısı üretilir (altyapı
  cihazları hariç) ve sorgu sonucu tek bir kişiyi kesinmiş gibi göstermez: tüm olası cihazlar
  "düşük güven" ile listelenir.
- **5651 sorgusu** dört kipte çalışır: **kesin** (genel IP FortiGate'in kendi adresi ve port
  verilmiş), **adaylar** (çift NAT: ISS modeminin NAT kaydı olmadığı için zaman penceresindeki
  oturumlar hedef ve zaman yakınlığına göre sıralanır), **iç** (iç IP veya MAC adresi verilmiş)
  ve **sonuç yok** (nedeni ve hangi verinin yardımcı olacağı açıklanır).
- Her eşleşme tam atıf zincirini taşır: oturum → kira (MAC) → cihaz → kişi/departman; her adım
  ham log satırına (`kaynak`, `gün`, `satır`, `bayt konumu`) işaret eder.
- Talepte hedef (IP/port/alan adı) varsa yalnızca o hedefe giden oturumlar aday olur; o hedefe
  giden oturum yoksa sonuç "bulunamadı" olur ve ilgisiz kişiler listelenmez.
- **IPv6 desteklenmez:** IPv6 adreslerini cihazlara bağlayan kayıtlar (NDP, DHCPv6) toplanmaz.
  Genel veya iç adresi IPv6 olan bir sorgu sonuç yerine açık bir retle yanıtlanır
  (`ipv6_not_supported`); böyle bir talep başka yollarla yanıtlanmalıdır. Hiçbir IPv4 oturumu bir
  IPv6 adresinin adayı olamaz.
- Geç gelen kayıtlar (ingest kesintisi sonrası birikmiş günler) otomatik olarak yeniden işlenir.
- Ölçüm: günde 3.000.000 trafik satırında (iki ardışık gün) ±60 sn aday araması **1,2–1,3 sn**,
  alan adıyla **1,1–1,5 sn**, hedef IP + port ile **0,04 sn** (hedef < 5 sn).

## Summary (EN)

The worker turns DHCP, ARP and FortiGate traffic records into an IP ↔ MAC timeline
(`leases`), keeps a device inventory, raises IP-conflict alerts and answers 5651 requests:
exact matches when the request names the FortiGate's own address, ranked candidates in the
double-NAT case, the attribution chain for an internal IP or MAC, or a clear "nothing found"
with what would help. Every step cites the raw spool line it rests on. IPv6 lookups are not
supported (no NDP or DHCPv6 evidence): they are refused, never answered with IPv4 candidates.

---

## 1. Lease timeline (spec 9.1)

Evidence (`correlation/timeline.py`, a pure function — `build_timeline(events, rules, horizon=…,
seeds=…)` — tested without a database):

| Evidence | Effect |
|---|---|
| `mt_dhcp` **assigned** ip, mac | Opens an interval (ip, mac, hostname, vlan, server), confidence **high**. Every *other* open interval of that IP closes: **superseded** (at that moment). Same ip + mac already open (DHCP): continuation — evidence kept, host name updated. |
| `mt_dhcp` **deassigned** ip, mac | Closes the open interval of exactly that ip + mac: **dhcp**. Without one: counted as `orphan_deassign`, ignored. |
| `mt_arp` `ARP-LOG ip mac` | Extends the open interval of that ip + mac (DHCP or ARP). Without one: opens an **ARP interval** (confidence **medium**) — except within `arp.ignore_after_release_seconds` (300 s) after a `deassigned` of that pair (a stale ARP table entry; counted as `ignored_arp_after_release`). |
| `fg_traffic` from `srcip` | Extends the open **DHCP** intervals of that IP. Never creates an interval, never extends an ARP interval (traffic proves the address was used, not by which MAC). |
| nothing | A DHCP interval times out `lease time × lease.timeout_percent / 100` after its **last evidence** (default 1 day × 110 % = 26 h 24 min; a server given 1 h in `lease.server_seconds`: 1 h × 110 % = 1 h 6 min): **timeout**, ending at that moment. An ARP interval ends `arp.interval_seconds` (1 h) after the last sighting: **arp**. |

Details and decisions:

- **Renewals are silent** in RouterOS: an open interval stays open as long as evidence keeps
  arriving within the timeout (hourly ARP-LOG, traffic).
- **ARP intervals** `[first_seen, last_seen + 1 h]`; sightings at most `interval × timeout_percent
  / 100` apart (66 min, scheduler jitter) are merged. An ARP interval closed later by an
  assignment **or a release** (inside the 66-minute grace but after `last_seen + 1 h`) never
  claims more than `last_seen + 1 h` (reason `arp`).
- **Stale ARP entries after a release**: the router keeps a released client in its ARP table for
  a while; an `ARP-LOG` of the same ip + mac within `arp.ignore_after_release_seconds` (default
  300 s, *verify on site*) after its `deassigned` opens nothing. A later sighting does. Other
  MACs are never ignored (that would hide an IP conflict). The rule carries across midnight and
  incremental runs (the releases just before the start are passed in), so it never depends on
  where a run was cut.
- **Deviation from the literal spec text** ("ARP intervals for IPs *without* DHCP"): an ARP
  interval is opened for **any** ip + mac that has no open interval, not only for IPs never seen
  in DHCP. Reason: DHCP clients whose lease was assigned before ARCILOGGER started (go-live, an
  always-on desktop that renews silently for weeks) would otherwise be invisible until their next
  `assigned`. For IPs never in DHCP (static devices) the behaviour is exactly the spec's. A later
  `assigned` of the same pair replaces the ARP interval by a DHCP interval (`superseded`;
  zero-length remainders are dropped). Side effects the owner should know: an `ARP-LOG` of a
  departed client more than 300 s after its release (a long-lived stale ARP entry) re-opens a
  medium-confidence interval for it until the next assignment or for 1 h; an ARP sighting of
  another MAC during an open DHCP lease is an overlapping interval and an IP conflict.
  *Owner to confirm.*
- **Equal timestamps** are ordered traffic → ARP → `deassigned` → `assigned` (then row id), so
  "release and re-assign in the same second" is `dhcp` + new interval, not `superseded`.
- **VLAN** of an interval: the `mt_dhcp.vlan` column, else the DHCP server name
  (`dhcp.server_vlan_regex`, `dhcp60` → 60), else the subnet in `vlans`.
- **Evidence stored per lease** (`leases.evidence`, contract format `EvidenceRef.to_dict()`): the
  opening row, up to 6 continuation `assigned` rows, the latest ARP row, the latest traffic row and
  the closing row — bounded, so a lease alive for months stays small. Each points at its raw line
  (`source`, `raw_day`, `raw_line`, `raw_offset`).

### Storage, rebuild and incremental mode (`correlation/leases.py`)

- A `leases` row belongs to the **site day of its start** (Europe/Istanbul).
- **`rebuild_day(day)`** — one transaction: recompute the day's intervals from the raw tables
  and write the **difference**: a recomputed interval takes the `id` of the stored row with the
  same (ip, mac, start) and is written only when something changed; rows that no longer follow
  from the evidence are deleted. **Lease ids are stable** across rebuilds, so `LeaseInfo.lease_id`
  in stored lookups and the lease ids in alert details keep pointing at their row (an id
  disappears only when its interval disappears). Idempotent: a second rebuild writes nothing.
- **Seeds** are the intervals of earlier days that were **alive at midnight**: stored open, or
  ending at/after midnight (their chain of evidence spans midnight), plus ARP intervals whose
  end is only decided after midnight (`last + 1 h` before, `last + 66 min` after midnight).
  A row that ended before midnight is final — its end rests only on evidence before midnight —
  and is never re-opened by a later sighting of the same pair or by another client's traffic on
  the address. A seed's state at midnight is its stored evidence before midnight plus the raw
  evidence of the last lease-timeout window (for a seed alive since its start, every sighting
  and `assigned` of its pair and every traffic row of its IP in that window belongs to it). The
  raw DHCP events of the window are also checked for a release or a new assignment that ended
  the seed (a continuation `assigned` of a MAC that already held a lease ends nothing): the seed
  is then closed exactly as a continuous run would close it. Its tail (end, last evidence,
  evidence) is rewritten by the rebuild.
- Intervals still open at the end of the day are followed into the next days (**look-ahead**,
  only for those IPs, one day per query) until they close: an interval crossing midnight is
  **one row with its real end**.
- **`rebuild_days(first, last)`** — rebuilds in order; only the last day looks ahead (the next
  day's seeds carry the state; intervals open at an intermediate midnight are written open and
  closed again by the next day).
- **Consistency (tested, `worker/tests/test_leases_consistency.py`)**: live incremental runs,
  `rebuild_days` and single-day rebuilds in any order (each day after the others, only the
  middle day, …) give the same rows with the same ids and the same alerts — the hand-computed
  ones, for a scenario with releases, timeouts across midnight, a static device with gaps,
  guest leases, a stale ARP entry right before midnight, an ARP interval decided after midnight
  and an IP conflict with a continuation `assigned`. Single days rely on the earlier days' rows:
  on an empty `leases` table rebuild **in order** (`rebuild --day <first> --to <last>`), which
  is what the catch-up does.
- **Incremental mode** — `run_incremental` every `[worker] interval_seconds` (60 s): evidence
  between the watermark (setting `worker.leases_watermark`, internal, author `:system`) and
  `min(now, ingest progress) − worker.leases_lag_seconds` (default 120 s) is fed into the open
  intervals; new rows are inserted, changed ones updated, the watermark advanced. **Ingest
  progress** (`correlation/watermark.py`) is the device time up to which the sources that feed
  the timeline (enabled FortiGate sources, every source with DHCP/ARP lines in the day before
  the watermark or `now`, whichever is earlier — so an ingest outage longer than a day still
  holds it) are loaded — the oldest of their newest `messages.time`. So the watermark never
  passes evidence that is not in the database yet: during an ingest outage or backlog it waits,
  and a device clock that runs behind moves it back by the same amount. A source stops holding
  the others back only when it is **silent and fully loaded**, judged by receive time, never by
  device time (a clock that runs behind is not silence): with the collector's status file
  (`state/collector-status.json`, written within the last 5 minutes) a source is active while the
  collector received from it within 30 minutes and has a backlog while the collector received
  later than its newest loaded line; without the file (collector down) a source whose newest
  loaded line was received 30 minutes before the newest loaded line of any other source counts
  as silent (device off; "log source silent" is its own alert). Until then lookups of the newest
  moments carry `lease_timeline_behind`. Remedy for a source that is gone for good: disable it
  (`sources.enabled`). Limit: after a backlog of several days ingest loads the newest day
  first, so the horizon may pass older days that are still loading; the late-evidence job
  (below) rebuilds them once they are loaded. Without a watermark (first start) or
  more than a day behind (worker stopped), it **catches up** with day rebuilds from the first
  day with DHCP/ARP data.
- Rebuilds never process evidence past the watermark — the two modes never apply the same row
  twice. A transaction-level advisory lock serialises all lease jobs (service and manual repair).
- **Late evidence** (ingest backlog after an outage, `correlation/late.py`): the incremental job
  has already passed it. Today's and yesterday's are picked up by the hourly re-check of today
  and the nightly rebuild of yesterday + today. Older days: `ingest_offsets.line_number` (per
  source and **raw day**) grows in the same transaction as the rows it loads (contract), so
  every rebuild of a complete day records the line count it started from (internal setting
  `worker.leases_ingest_seen`, `{"YYYY-MM-DD": lines}`), and the job `leases.late_evidence`
  (every 10 minutes) rebuilds — from the day before the first grown raw day (device clocks
  around midnight) up to today, in order — whenever a raw day has more lines than at its last
  rebuild. Days never recorded count as late once they are older than yesterday (yesterday
  belongs to the nightly rebuild). The first run on a database without the setting only records
  the current counts. Raw days older than `db.retention_days` are ignored. Tested end to end:
  the worker runs through a weekend without ingest, Saturday's rows arrive on Monday, the lease
  appears within one job interval and the lookup answers. A future "re-ingest from the vault"
  must advance `ingest_offsets` (or rebuild the days itself) to be noticed.
- **Traffic volume**: per source IP only the first and last row of every 5-minute bucket are read
  (exact because the bucket is shorter than the shortest lease timeout; checked at start). The
  rebuild of a day with 1,000,000 traffic rows and 300 devices took **2.4 s** (section 6).
- **Manual repair**: `arcilogger-worker rebuild --day 2026-10-12 [--to 2026-10-20]` (one
  transaction and one `job_runs` row per day). Run it for the affected range, **in order**, after
  changing lease settings (`lease.*`, `arp.*`, `dhcp.server_vlan_regex`): stored rows were
  computed with the old rules and are the next day's seeds.

### Devices (`correlation/devices.py`, spec 9.2)

Updated from DHCP and ARP evidence by every lease job: `first_seen` (earliest), `last_seen`
(latest), `last_ip` / `last_vlan` (of the latest sighting — rebuilding an old day never moves a
device back), `hostnames` (DHCP host names, first seen first, at most 16), `randomized`
(locally administered unicast MAC: second hex digit 2, 6, A or E — e.g. iPhone private
addresses). **Never overwritten**: `name`, `type`, `person_id`, `notes`, `tags`,
`infrastructure` (only initialised for a new device from `devices.infrastructure_macs`) and
`vendor` once set. `person_id` is the current owner of the assignment history
(`device_assignments`, ADR-0008) and is written only by `arcilogger_db.assignments`; the upsert
writes device rows in MAC order, the order in which the assignment writers lock them.

**Vendor (optional OUI file).** ARCILOGGER does not ship or download the IEEE registry. The
administrator may download the IEEE Registration Authority's public **MA-L** listing (CSV or
text; find the current link on the IEEE RA website — *verify*), copy it to the server as
**`C:\ProgramData\ARCILOGGER\oui.csv`** and set the setting `devices.oui_file` to that path
(`arcilogger-db settings set devices.oui_file C:\ProgramData\ARCILOGGER\oui.csv --by NAME`; a text
value needs no JSON quotes, docs/ILK-ADIMLAR.md section 6). Put it there and nowhere under the
data root: the installer gives `NT SERVICE\ArciWorker` inherited read rights in
`C:\ProgramData\ARCILOGGER` (ADR-0007 section 4), while `D:\ARCILOGGER` is protected and grants
the services only "list this folder" — a file in a folder created there later (e.g.
`D:\ARCILOGGER\config`) is unreadable for the worker and the vendor stays empty. Check with
`icacls C:\ProgramData\ARCILOGGER\oui.csv`: it must list `NT SERVICE\ArciWorker:(I)(RX)`. Accepted:
IEEE CSV (`Registry,Assignment,Organization Name,…`, MA-L/MA-M/MA-S), the IEEE text listing
(`00-00-0C   (hex)   Cisco …`) and a simple `AABBCC<TAB>Vendor` file; the longest prefix wins;
unrecognised lines are skipped and counted. The file is re-read when it changes. Without it the
vendor stays empty; randomised MACs never get a vendor.

### IP conflicts (`correlation/conflicts.py`)

Two intervals of the same IP with different MACs that overlap for a positive time (`[start, end)` —
touching is not overlapping). Infrastructure MACs (setting `devices.infrastructure_macs` or the
device flag) are excluded (the core router answers on many VLANs, proxy ARP). Alert `ip_conflict`,
severity `medium`, deduplicated per IP, MAC pair and site day of the overlap start
(`ip_conflict:<ip>:<mac>,<mac>:<day>`): a rebuild never raises it twice, also not after an
administrator dismissed it. A rebuild of a day sees all of that day's evidence: an **active** alert
of that day (open, or acknowledged — İnceleniyor — by a person looking into it) whose conflict it no
longer finds (late evidence closed one of the leases earlier) is set to `resolved` with
`details.auto_resolved = {at, reason: "lease_rebuild"}` and `resolved_at`; if a later rebuild finds
the conflict again, that alert is re-opened (`details.reopened_at`). Both changes are `alert_events`
rows by `system` (reasons `lease_rebuild`, `conflict_found_again`; docs/ALERTS.md section 8). Alerts
a person resolved or dismissed are never changed. The incremental job only raises alerts.

---

## 2. Attribution chain (spec 9.4, `lookup/attribution.py`)

`attribute(session, ip, at)` → lease → device → person / department, or `None`.

- A closed lease covers `start ≤ at < end`. An **open** lease covers `at` only until it would
  time out without further evidence (`last_evidence + lease timeout`; ARP: `last_evidence + 1 h`),
  so a stale open row never answers for a later moment.
- **Several MACs** at `at` = IP conflict: the most likely chain (non-infrastructure, then DHCP
  before ARP, then the newest assignment; a MAC named in the request first) with the others in
  `alternatives`; that chain **and every alternative** have confidence **low** and the
  explanation `ip_conflict` (no device may read as more reliable; step confidences stay).
- **Confidence**: lease (`high` DHCP, `medium` ARP) · device (`high`, `medium` for a randomised
  MAC) · person (`medium`: an administrative link, not log evidence); the chain has the weakest.
- **Department**: the linked person's department, else the VLAN's
  (`arcilogger_db.network.vlan_department` — the same rule as the usage reports).
- **Person at T** (assignment history, ADR-0008, `arcilogger_db.assignments`): the device's
  `device_assignments` row covering `at` (`valid_from ≤ at < valid_to`, open rows to infinity)
  names the person — `PersonInfo.link = "history"` with `assigned_from` / `assigned_to`; a later
  reassignment changes nothing for an earlier moment. A device **with** history but no row at
  `at` (assigned only later, or unassigned then) names **nobody**: `person_unassigned_at_time`
  (department from the VLAN), never the owner of another time. Only a device without any history
  row but with `devices.person_id` (written outside the assignment module) falls back to that
  current owner, `link = "current"`, and the outcome carries `person_link_at_lookup_time`. A row
  the migration backfilled from such a link (`backfilled`, start = the device's first sighting)
  adds `person_link_backfilled`. One person may hold several MACs (PC, phone, the phone's next
  randomised MAC): each device has its own history.
- **Department of a person**: always the person's **current** department — no department
  history is kept; for history links the outcome says so (`person_department_current`).
- Explanations added to a chain: `guest_vlan` (a VLAN marked `guest`, e.g. a guest Wi-Fi with
  another exit than the FortiGate: no FortiGate records), `arp_only`, `randomized_mac`,
  `infrastructure_device`, `no_person`, `person_unassigned_at_time`, `person_link_backfilled`.

## 3. 5651 lookup (spec 9.3, 9.5, `lookup/engine.py`)

`run_lookup(session, request, settings, *, layout=None, now=None)` — only reads; the API stores
the outcome (`lookups.outcome`) and writes the audit entry. `layout` (the data directory) lets
the data window say whether a day is in the sealed vault.

| Mode | When | What |
|---|---|---|
| `exact` | public IP ∈ `settings.exact_addresses()` (`nat.public_ips`, `nat.wan_transip`) **and** a port, and the logs show that address as the FortiGate's `transip` around T (± 15 min; or no other source-NAT address — the FortiGate's other configured public addresses, dual WAN or an IP pool, do not count against it) | `transip = IP AND transport = port` (index `(transip, transport, time)`), score 100, explanation `exact_nat`. With a requested destination only the sessions on that port that went to it; sessions whose logged destination IP/port differs are counted, never listed (`exact_other_destinations`, or `exact_only_other_destinations` when none is kept); none → `none` + `destination_not_found`. A **domain-only** request is not filtered by a missing log: a missing DNS/web/app log for the name (another resolver, encrypted DNS, a cached answer) does not show another destination. Sessions tied to the name are listed; if none is, the sessions on the port (to the requested port, if given) are listed as `domain_unverified` with `exact_domain_unverified` ("hedef doğrulanmadı"); if some are, the untied ones are counted neutrally (`exact_unverified_destinations`). Without destination every session on the port is listed (`multiple_exact`) |
| `candidates` | double NAT (public IP not configured), a requested configured address that the logs do not show as `transip` then while they show another, non-configured source-NAT address (`public_ip_not_transip`: the ISP router still translated, e.g. a moment before the switch to bridge mode — answered as double NAT), or the FortiGate's address without port, or only a destination | sessions in the window, ranked (below); explanation `double_nat` (spec 12.4 wording, only together with candidates), `port_missing`, `public_ip_not_configured` or `destination_search` |
| `internal` | internal IP and/or MAC, no public IP | the attribution chain (`attribution`), no matches |
| `none` | nothing found | why only a candidate search was possible (`double_nat_no_candidates` — the 12.4 first clause without "listed below" —, `port_missing`, `public_ip_not_configured`, `destination_search`), `no_match`, `destination_not_found` when a destination was requested, and what would help: `no_match_exact`, `no_match_candidates`, `no_lease` (with the nearest leases before/after), `no_lease_for_mac`. No text in this mode promises a list. |

**Session matching** (contract): a row's interval is `[time − duration, time]`; it matches when
it overlaps `[at − tolerance, at + tolerance]`. Every query is bounded to `time BETWEEN at −
tolerance AND at + tolerance + lookup.max_session_seconds`. Interim updates (`0000000020`) and
the final row of one session (same `sessionid`, 5-tuple and NAT pair) are **one** match; its
interval spans all its rows and its evidence cites up to 10 of them (nearest to `at`, first,
last). Rows before `at − tolerance` are outside the scan; with cumulative `duration` in interim
rows (*verify*) the session start is still exact.

**Candidates.** Only source-NAT sessions (`transip = nat.wan_transip` when configured and seen
in the logs around T, else `trandisp = 'snat'` — a configured `nat.wan_transip` that the logs do
not show then is not used and says so, `wan_transip_not_seen`), optionally of the requested
protocol. A search not narrowed to one `transip` leaves out sessions translated to another
address in `nat.public_ips`: they did not leave through the requested address. With a destination the search is
restricted to it: `dest_ip` (index `(dstip, dstport, time)`), `dest_domain`, or only
`dest_port`. **When nothing goes to the requested destination the answer is `none`** with
`destination_not_found` (spec 2.3: candidates "to the given destination … if the request
contains one"; listing every client of the window would put unrelated persons into an official
answer — data minimisation, spec 3.3). The deliberate way to see the whole window is a request
without destination (`candidates_without_destination`); the explanation says so. An internal IP
or MAC given together with a public IP restricts the candidates to that client. Only sessions
that left with an address (`transip`, else the source) of the requested public IP's family are
candidates: no IPv4 session — the FortiGate's IPv4 WAN address included — is ever a candidate
for an IPv6 address (defence in depth behind the API's refusal, section 7).

**Domains.** Addresses of the name around the moment: DNS answers for the name and `www.` +
name (`fg_dns`, index `(qname, time)`) and the destinations of web filter / application control
logs with that host name or a sub-domain, both from `lookup.dns_lookback_seconds` (default 3600)
before the window to its end. A session whose **own** web/app log names the domain (same
`sessionid` and source IP, written within the session's span ± 60 s) is found inside the
bounded `fg_traffic` scan by a correlated `EXISTS` (index `(sessionid)` of `fg_web`/`fg_app`) —
never by collecting session ids, so a popular domain costs no more than the window scan (the
earlier version failed with more than 65,535 bind parameters). Every list of values is passed as
one array parameter (`= ANY(…)`). The lookup switches PostgreSQL JIT compilation off for its
transaction (`SET LOCAL jit = off`): the planner's cost estimate of the correlated sub-queries
crosses `jit_above_cost` and compiling took longer (0.8 s) than the query.

**Score** (integers; exact = 100, candidates at most 95):

| Part | Points |
|---|---|
| Time | 40 when `at` is inside the session, falling linearly to 0 at the tolerance |
| Destination | IP + port 50 · domain + port 50 · domain 45 · IP (no port asked) 45 · domain, other port 40 · only the domain's address known (another client's DNS/web log; CDN addresses serve many names) 35, with port 40 · IP, other port 30 · port only 20 · nothing requested 25 (neutral) · requested, not matched 0. The weaker kinds keep their own `destination_match` label and report text: `ip_other_port`, `domain_other_port`, `domain_address`; `domain_unverified` (exact mode only, see above) |
| Port hint | +5 when `lookup.port_hint` is `true` and the FortiGate port equals the requested public port (off by default, see "verify") |

Order: score ↓, destination match, time distance ↑, session start, source IP, source port,
session id, row id — deterministic. At most `lookup.max_candidates` (100) matches; beyond that
`truncated = true` with the explanation `truncated` (`total`, `shown`).

**Data window** (`lookup/window.py`): first/last day in the database, whether the requested day
has rows, whether its sealed manifest exists. Explanations: `not_in_database` /
`only_in_vault` / `no_data_for_day`, `older_than_db_retention` (`db.retention_days`),
`future_time`, and `lease_timeline_behind` when the requested window is after the lease
watermark. Re-ingesting a sealed day ("arşivden yeniden yükle") is a later feature.

All explanation texts (Turkish and English, no legal claims) are in `lookup/texts.py` and
`lookup/types.py` (`EXPLANATIONS`); `params` keep machine values (UTC ISO times, integers).

## 4. Worker service (`service.py`, `cli.py`)

| Job | When |
|---|---|
| `partitions.ensure` | daily after 00:15 and at start-up — `arcilogger_db.partitions.ensure_partitions` (no-op with TimescaleDB) |
| `license.check` | every 5 minutes — the licence state recomputed (`arcilogger_db.licensing.load_status`, never cached); a state change and every limit crossing since the last events become `license_events` rows and audit entries (`license.state`, `license.limit_exceeded`, `license.limit_ok`), each change once; the licence alerts opened and resolved in their own transaction (`R20-license`, `R20-license-limit`, `R20-license-maintenance`: docs/ALERTS.md; a failure there is only logged and reported as `alerts.error`, the job does not fail); `state/license-status.json` rewritten for the installer. Details `{"status", "enabled", "recorded", "alerts"}`. If the licence code cannot be loaded the job fails like any job (retries, `R20-job`); the other jobs run on (docs/LICENSING.md) |
| `leases.incremental` | every `[worker] interval_seconds` (default 60 s) |
| `leases.nightly` | daily after 00:05 — rebuild yesterday and today |
| `leases.recheck_today` | hourly — rebuild today (late evidence of today) |
| `leases.late_evidence` | every 10 minutes — rebuild older days whose evidence arrived after their rebuild (section 1) |
| `evidence.purge` | daily after 03:00 — deletes expired evidence packages (`evidence.keep_days`, 30 days; `arcilogger_worker.evidence_jobs.purge_evidence`, audited as `evidence.purge`) |
| `audit.import` | every 15 minutes — copies the results of the database-free scheduled sealing and retention runs into the audit log, each event once (`seal.run`, `retention.run`; docs/KANIT-PAKETI.md) |
| `admin.requests` | every 15 seconds — the browser administration's test e-mails and lease rebuilds (`admin_requests` rows of `channel = 'worker'`, claimed with `FOR UPDATE SKIP LOCKED`; a lease rebuild runs day by day as `leases.rebuild` job runs with the request's id; audited as `smtp.test` / `leases.rebuild`; ADR-0014, `arcilogger_worker.admin_jobs`) |
| `alerts.import` | every minute — new `state/alerts.jsonl` lines into `alerts`, each line once (byte offset in the internal setting `worker.alerts_import`, written with the alerts; docs/ALERTS.md) |
| `alerts.r15` | every minute — R15 log source silent (`sources.last_seen`, the collector status, `[[sources]] heartbeat_minutes`; `alerts.r15_*` settings) |
| `alerts.system` | every 5 minutes — the R20 checks: disk, sealing, verification, credit, replica, parse failures, failing jobs, database backup, clock (`alerts.*` settings) |
| `alerts.notify` | every minute — alert e-mail through `[smtp]` to `alerts.email_recipients` (`alert_deliveries`; skipped while `[smtp] enabled = false`; ADR-0010) |
| `usage.requests` | every 30 seconds — usage rebuild requests of the web interface (`usage_rebuilds`, `POST /api/v1/usage/rebuild`; ADR-0011, docs/USAGE.md) |
| `usage.daily` | daily after 00:45 — yesterday's usage rollup rebuilt as `final` |
| `usage.today` | every 5 minutes — today's usage rollup from the logs loaded so far (`partial`) |
| `usage.reattribute` | every 10 minutes — any of the last `usage.auto_rebuild_days` days whose device owners or leases (who an address belonged to when), late logs, `usage.*` settings or engine changed, or that is missing or still partial, is rebuilt; uses the attribution chain of section 2 at every moment the owner can change |
| `usage.retention` | daily after 03:30 — usage rollups older than `usage.retention_days` (400) |
| `reports.run_due` | every minute — scheduled usage reports: due schedules queued (one run for the newest occurrence after downtime, `reports.catch_up`), queued runs rendered (HTML, PDF, Excel into `exports/usage-reports/`) and e-mailed one per recipient (`report_runs`; ADR-0012, docs/USAGE-REPORTS.md) |
| `reports.purge` | daily after 03:20 — deletes the files of report runs older than `reports.keep_days` (400 days); the run rows and the hashes stay |

**Licence gates** (ADR-0013, docs/LICENSING.md section 2.3): the jobs of a paid module —
`usage.requests`, `usage.daily`, `usage.today`, `usage.reattribute` (Kullanım analizi,
`usage_analytics`) and `reports.run_due` (Zamanlanmış kullanım raporları, `usage_reports`) — ask
the licence before their `running` row is written. While the module is off the job does not run:
its first skip of a site day is one `skipped` row with the details `{"license": {"module",
"status"}}` (for a daily job the run that counts as done), later ones only move its next due time;
one INFO line per job when its gate changes (`job usage.today skipped: module usage_analytics not
licensed (trial_expired)` / `job usage.today runs: module usage_analytics licensed`). When
`reports.run_due` is locked an active `R20-report` is resolved with the note "Zamanlanmış raporlar
lisansta kapalı / Scheduled reports are off in the licence". The status of the gates is kept 60 s
per worker process; a failed licence evaluation is never cached and locks only the paid jobs.
Every other job (the 5651 jobs, `license.check`, and the data-minimisation jobs `usage.retention`
and `reports.purge`) never asks and runs in every licence state; skipped runs do not count for
`R20-job`.

Daily jobs run once per site day as soon as their time has passed, also right after a start-up
(the last successful run is read from `job_runs`). Every run is a `job_runs` row (`running` →
`ok` / `failed` / `skipped`, JSON details with counts and `duration_ms`). A failing job never
stops the loop and is **retried with a growing delay**: daily jobs after 15 min, 30 min, 1 h,
2 h, then every 4 h; interval jobs after their interval, doubling up to 30 min (never sooner
than their interval). A success resets it. While the database is unreachable a job is retried
after 1 minute. Status for the dashboard: `state/worker-status.json` (state, database
reachability, per job last start/finish/status/error/next due, `failures`, `retry_at`). Stop:
Ctrl+C, service stop, SIGTERM (Windows also Ctrl+Break); the loop checks every second.

Commands: `arcilogger-worker run`, `arcilogger-worker rebuild --day YYYY-MM-DD [--to
YYYY-MM-DD]`, `arcilogger-worker usage rebuild --from YYYY-MM-DD [--to YYYY-MM-DD]` (usage
rollups, one `usage.rebuild` job run; refused with exit code 1 and the `module_not_licensed`
message while Kullanım analizi is off — the only gated command), `arcilogger-worker version`; exit codes 0 ok, 1 failed, 2 configuration/usage
(also lease settings the timeline cannot work with, e.g. a lease timeout shorter than the 300 s
traffic bucket), 3 database unreachable. A failed day is recorded as a `failed`
`leases.rebuild` run with the error type and message, never left `running`.

## 5. Settings used

Shipped (`arcilogger_db.settings.DEFAULT_SETTINGS`): `nat.public_ips`, `nat.wan_transip`,
`lookup.tolerance_seconds`, `lookup.max_candidates`, `lookup.max_session_seconds`,
`lookup.port_hint` (`false`), `lookup.dns_lookback_seconds` (`3600`), `lease.default_seconds`,
`lease.server_seconds`, `lease.timeout_percent`, `dhcp.server_vlan_regex`,
`arp.interval_seconds`, `arp.ignore_after_release_seconds` (`300`; 0 switches the rule off; at
most `arp.interval_seconds`), `devices.infrastructure_macs`, `devices.oui_file` (`""`),
`worker.leases_lag_seconds` (`120`), `db.retention_days`. Internal, written by the worker
(author `:system`; not settings, not for editing): `worker.leases_watermark`,
`worker.leases_ingest_seen`.

**Changing a setting** (until the settings page, M6): `arcilogger-db settings set KEY JSON_VALUE --by NAME` on the
server (`list`, `get KEY` show the values). The installer does not put `venv\Scripts` on `PATH`,
so in an administrator PowerShell call it with its full path:
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set KEY JSON_VALUE --by NAME`
(the configuration is read from `C:\ProgramData\ARCILOGGER\arcilogger.toml`). It validates the key and the value, writes
`updated_by = NAME` — so `arcilogger-db upgrade` never puts the default back — and appends
`settings.change` with the old and new value to the audit log. A hand-written SQL change is not
validated, not audited, and reverted by the next upgrade while the row still says `:seed`.

An unreadable `devices.oui_file` (a folder, no permission, missing) is logged once and treated
as no file: the vendor stays empty and the lease jobs carry on. On the Windows server the usual
cause is the file's location — see "Vendor" in section 1.

Also shipped, read by the API's 5651 lookup and report: `lookup.timeout_seconds` and
`lookup.report_timestamp` (`false`; docs/RAPOR.md). The alert jobs read the `alerts.*` settings
(recipients and policy of the e-mail, the R15 grace periods, the R20 thresholds); they are listed
with their ranges in docs/ALERTS.md section 3. The usage jobs read the `usage.*` settings
(thresholds, background rules, work hours, holidays, re-attribution window, retention; docs/USAGE.md
section 4); their subject step is the attribution chain of section 2 (`Attributor`, extended with
`covered_until`, `leases_overlapping` and `assignments` for the usage rollups). Internal, written
by `alerts.import` (author `:system`): `worker.alerts_import`. Operator steps and PowerShell quoting:
docs/ILK-ADIMLAR.md section 6. Settings are read from the database when used, so no service
restart is needed.

## 6. Performance (spec 14: lookup < 5 s for ±60 s)

`worker/tests/test_lookup_performance.py`, PostgreSQL 16 with native monthly partitions,
developer machine, warm cache (`VACUUM ANALYZE` after loading). The synthetic traffic is
**continuous** — it does not stop at midnight — so the scan tail of
`lookup.max_session_seconds` (24 h after the window) is full: 300 clients, sessions of 0–96 s
and one 2-hour session in 500, web filter logs for every fourth session (a fifth of them
`*.google.com`, the rest one of 5,000 rare sites), DNS answers for `www.google.com`, DHCP and
hourly ARP-LOG for 300 devices.

| Step | Default (`ARCILOGGER_PERF` unset): 1 M rows/day, 1.1 days | `ARCILOGGER_PERF=large`: 3 M rows/day, 2 days (6 M rows, 1.5 M web rows) |
|---|---|---|
| Loading the rows (`INSERT … SELECT`, all indexes) | 18.5 s | 125–129 s |
| `rebuild_day` of the first day (with look-ahead into the next) | 2.5 s | 7.3–7.5 s |
| Candidates, ±60 s, **no destination** (100 shown, chains included) | 0.47 s | 1.24–1.34 s |
| Candidates, popular domain (`google.com`) | 0.37 s | 1.11–1.52 s |
| Candidates, rare domain | 0.35 s | 1.08 s |
| Candidates to a destination IP + port | 0.02 s | 0.04 s |
| Exact (`transip`, `transport`) | 0.02 s | 0.02 s |

The default runs with the test suite (about 27 s; `ARCILOGGER_PERF=0` skips it); `large` takes
about 2.5 minutes and should be run before releases. `EXPLAIN` (asserted): no sequential scan;
the exact lookup uses `…_transip_transport_time_idx` with `transip`, `transport` and both time
bounds in the index condition; the destination search a bitmap scan of
`…_dstip_dstport_time_idx`; the window without destination and the domain search an index scan
of the partition's primary key `(time, id)` limited to `[at − 60 s, at + 60 s + 86,400 s]`, the
domain's correlated sub-queries an index scan of `fg_web`/`fg_app` `(sessionid)`.

**Scaling.** The search without a specific destination IP reads every `fg_traffic` row in that
24-hour range (about `rows per day × max_session_seconds / 86,400` index entries plus their heap
pages) and keeps the few thousand whose session started before the window: 1.0 M rows read →
0.33 s, 3.0 M → 1.05 s of execution — linear. At 5 M rows a day expect about 1.8 s warm; a cold
cache (after a restart) reads those heap pages from disk and was **not measured** — measure on
the production server. If it gets close to 5 s: lower `lookup.max_session_seconds` (verify the
FortiGate session TTLs; 4 h cuts the read by 6×), or add a partial index on `fg_traffic (time)
WHERE duration >= 300` and split the scan into "logged within 5 minutes of the window" plus
"long sessions logged later" (schema change, later item), or materialise the NAT index of spec
9.3.

## 7. Limits

- **IPv6 is not supported.** ARCILOGGER collects no evidence that ties an IPv6 address to a
  device (no NDP neighbour tables, no DHCPv6 logs; DHCP and `ARP-LOG` are IPv4 only). A lookup
  whose public or internal address is IPv6 — also an IPv4 address written as IPv6,
  `::ffff:a.b.c.d` — is refused before it runs (API 422 `ipv6_not_supported`, audited as
  `lookup.run_failed`; the web form says so before sending): this version cannot answer such a
  request, it must be answered by other means. A destination may be IPv6 (it only filters).
- The public port of a double-NAT request cannot be mapped (no ISP router logs): candidates only.
  The fix is the ISP router in bridge mode with the public IP in `nat.public_ips` (network admin).
- The FortiGate's own outbound sessions (`subtype="local"`, `trandisp="noop"`) are not candidates.
- A guest network that does not leave through the FortiGate (a separate line or a VPN tunnel; a
  configuration option, DEVICE-SETUP §5) has no FortiGate records: when its VLAN is marked
  `guest` (`vlans.guest`), guests never appear as candidates; their DHCP/ARP leases are still
  attributed (`guest_vlan`).
- A domain is matched only through FortiGate web filter / application control / DNS logs; a
  session whose DNS query is older than `lookup.dns_lookback_seconds` is matched by IP only.
  DNS logs are searched for the exact name and `www.` + name (index `(qname, time)`); other
  sub-domains (`cdn.example.com`) are found through web filter / application control logs.
- Lookups read the database only; a day that is only in the sealed vault must be re-ingested
  first (later feature).
- A requested destination that no session reached gives `none`, never the whole window, in
  both NAT modes; the whole window (or every session on an exact NAT port) is listed only for a
  request without destination. A domain is matched only through FortiGate DNS, web filter and
  application control logs.
- The person of a chain is the assignment valid at T; its department is the person's current
  one (no department history, section 2). Assignments can only be appended to the history,
  not corrected afterwards (ADR-0008).
- Late evidence older than `db.retention_days`, or loaded without advancing `ingest_offsets`,
  is not detected (manual `rebuild`).

## 8. Verify on site

- **FortiGate interim logs** carry the cumulative `duration` (session start = `time − duration`)
  and how often they come; long sessions without interim logs are only found up to
  `lookup.max_session_seconds` after their start.
- **MikroTik DHCP wire format** and that `assigned` is logged on every (re)binding, `deassigned`
  on expiry/release; RouterOS logs no renewals (the timeline relies on it).
- **ARP-LOG script interval** (`arp.interval_seconds`, 1 h) and its jitter (merge grace 66 min).
- **Lease times per DHCP server** (`lease.server_seconds`, empty by default: every server
  `lease.default_seconds`, 1 day; enter servers with other lease times, e.g. a guest network's).
- **ISP router port preservation** before switching on `lookup.port_hint`: compare the source
  port an external server sees with the FortiGate `transport` of the same connection.
- **`nat.wan_transip`** (the `transip` of the FortiGate's traffic logs) and **`nat.public_ips`**:
  enter them once confirmed (`arcilogger-db settings set KEY JSON_VALUE --by NAME`; `nat.public_ips`
  only after the ISP router is in bridge mode — moments before that are answered as double NAT
  automatically, `public_ip_not_transip`).
- **Infrastructure MACs** (core router, FortiGate, switches): enter them in
  `devices.infrastructure_macs` with the same command (or flag the devices) so they never raise
  IP conflicts.
- **OUI file**: download location and format of the IEEE public listing.
- **Stale ARP entries after a release**: how long RouterOS keeps a released DHCP client in the
  ARP table that `ARP-LOG` prints (`arp.ignore_after_release_seconds`, 300 s; the DHCP server's
  `add-arp` option changes this).
- **Cold-cache lookup time** at the real daily volume on the production disk (section 6).
- **Owner decisions**: ARP intervals also for DHCP IPs without an open lease, with its side
  effects (section 1); `none` instead of the whole window when the requested destination has no
  session (section 3).
