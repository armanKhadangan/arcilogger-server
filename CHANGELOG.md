# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/),
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### FortiGate DHCP, srcmac and internet exits (Wave 3)

A 5651 lookup now follows the lease on sites whose FortiGate is the clients' DHCP server, may use
the `srcmac` of a FortiGate session record as labelled partial evidence, and resolves a public IP
to the internet exit (line) that holds it, so a site with two lines (one direct, one behind an ISP
router) lists only that line's sessions
([ADR-0016](docs/adr/0016-fortigate-dhcp-and-internet-exits.md); docs/CORRELATION.md; MVP-CONTRACTS
§18). Decided by the developer on the owner's delegation; the owner may change it (the ADR lists
the open points O1–O22, [ASSUMPTIONS](docs/ASSUMPTIONS.md) the implemented defaults, not yet
confirmed). The rule behind every choice: a wrong person at high confidence is the worst outcome, a
false "nobody" the second worst; where the logs cannot decide, the confidence is lowered and the
reason given. Every FortiGate format detail that no official source or real sample confirms is a
setting or has a loud check (sahada doğrula; KURULUM/INSTALL section 16 is the operator's list).

- **FortiGate DHCP as lease evidence:** a FortiGate `event` line with `ip`, `mac` and `dhcp_msg` is
  stored in the new table `fg_dhcp` by its shape alone (a value that does not convert keeps it an
  `fg_event` row and the parse failure `fortigate.dhcp`). What a row means is the worker's setting
  `dhcp.fortigate_logids`: the DHCP Ack `0100026001` assigns, the Release `0100026002` (derived
  from the documented numbering, sahada doğrula) releases; `dhcp.fortigate_dhcp_msgs` allows `Ack`
  under the Ack logid. Usable rows feed the same lease timeline as MikroTik DHCP: an Ack opens a
  `high` lease, a Release ends it, an Ack from the other DHCP table supersedes it, and a FortiGate
  Ack of a MAC for another address closes that MAC's other lease of the same origin, interface and
  configured network. A logged lease of 300–8,640,000 s ends the lease exactly that long after its
  last DHCP record, the FortiGate's own end; `0`, a missing or an out-of-range value use the
  configured lease times. Traffic does not keep a FortiGate lease alive
  (`dhcp.fortigate_traffic_extends`, off). A changed map applies to older rows after a lease
  rebuild, without loading anything again.
- **Only usable evidence counts:** FortiGate DHCP rows of an unconfigured sender (an
  `unknown-<ip>` sender only inside the address of an enabled, configured FortiGate source), of an
  interface in `dhcp.fortigate_interfaces_excluded` (DHCP over IPsec), without the FortiGate's own
  time, with a zero, broadcast or multicast MAC, an unmapped logid or another `dhcp_msg` are never
  lease evidence and are counted by reason; the lease job, the lookup, the alerts and the Kaynaklar
  card read one rule for it (`lookup/trust.py`).
- **5651 Sorgu:** a FortiGate lease prints its logged lease time and its last DHCP record at or
  before the requested moment. A chain is `low`, with the reason, more than half a lease after that
  record with no renewal seen (`lease_renewal_not_seen`), when two DHCP origins served its network
  (`dhcp_sources_overlap`), for an address in no configured network (`fortigate_dhcp_no_network`),
  when a cited source's clock was more than 120 s off at the moment (`source_clock_offset`), and
  when an unconverted FortiGate DHCP line (loaded before the upgrade, or during a rollback) of its
  IP or MAC follows its lease's last record (`fortigate_dhcp_not_converted_since_lease`). Such
  lines are not converted (O3): when no chain is built the lookup names a covering one
  (`fortigate_dhcp_not_converted`). Until `dhcp.fortigate_format_verified` is set after the site
  check (DEVICE-SETUP section 2.4) every FortiGate chain says that the record format is not
  verified on site. A match without a chain says so (`chain_missing`), and a match with rows of an
  unconfigured sender is marked (`session_from_unknown_source`).
- **srcmac, partial evidence:** the new column `fg_traffic.srcmac` keeps the session record's source
  MAC (FortiOS fills it only with device identification on the source interface). A lookup uses it
  only on interfaces listed in `lookup.srcmac_interfaces` (default none, so off), from a trusted
  sender, for a MAC that is no infrastructure MAC, seen with at most `lookup.srcmac_max_ips` (1)
  IPv4 addresses on that interface within an hour of the moment and holding no lease of another
  address then. A chain without a lease then gets the MAC, device and person at `low`, labelled
  **Kısmi kanıt: srcmac** (`srcmac_only`); a lease of another MAC makes both chains a `low`
  conflict, listed side by side (`srcmac_mismatch`). srcmac never opens or extends a lease. This
  departs from SPEC 9.1, as does traffic not extending a FortiGate lease (ASSUMPTIONS, "Deviations
  from the specification").
- **Internet exits:** one setting, `nat.egresses` (at most 8 exits by name), replaces
  `nat.public_ips`, `nat.wan_transip` and `lookup.port_hint`. An exit is `direct` (the FortiGate
  holds the public address: exact answers), `isp_nat` (an ISP router translates once more:
  candidates), `not_logged` (the line bypasses the FortiGate) or `internal` (a source-NAT address
  towards a DMZ, another VLAN, a VPN or a branch: claimed, never searched), with its public
  addresses (never private, shared or reserved space; IPv6 refused), NAT addresses (`transips`),
  interfaces (`dstintf`), port preservation (a ranking hint only) and `public_dynamic`. A lookup
  matches the public IP against the exits, checks that the logs show the exit's NAT address within
  ±15 minutes of the moment (a failed check is a warned fallback, e.g. "Doğrudan hat
  doğrulanamadı") and searches that exit's sessions only. An address no exit claims is the
  FortiGate's own when the logs show it as its NAT address near the moment; otherwise it is
  searched only in the exits whose public address is unknown or dynamic, with a warning, else
  nobody is listed (`public_ip_no_egress`). Only private or shared NAT addresses count as double-NAT
  evidence; candidate searches over unclaimed addresses take only the `trandisp` values of
  `nat.snat_trandisp` (`snat`) and name the others. Every answer records the exit it used and
  warns when an exit changed after the moment (`egress_changed_after_event`; the earlier
  definitions are in the audit log).
- **Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi:** ten new settings (the six
  `dhcp.fortigate_*`, `lookup.srcmac_interfaces`, `lookup.srcmac_max_ips`, `nat.egresses`,
  `nat.snat_trandisp`) with Turkish and English validation, no re-authentication (O8).
  **İnternet çıkışları (NAT)** is an editor of its own (a form per exit, the server's rules checked
  in the browser, a stored exit it cannot give back never rewritten); under it, **Kayıtlarda
  görülen NAT adresleri (son 24 saat)** lists the `transip`, `dstintf` and `trandisp` groups of
  the trusted session records with the exit that claims each (**Çıkış olarak ekle**, **İnternet
  çıkışı değil olarak ekle**) and the interfaces whose records carry srcmac (**srcmac için ekle**).
- **Kaynaklar:** a **Kira kanıtı** card: per evidence table (MikroTik DHCP, FortiGate DHCP,
  ARP-LOG) the usable rows of the last 24 hours, the newest of the last hour and the leases open
  or ended in that time, the FortiGate DHCP rows not used and those of excluded interfaces,
  whether the record format is verified, the srcmac interfaces with their rows of the last hour,
  and a state (lease evidence arrives, none arrives, or it is refused). Counts only, no client
  address. A **Desteklenen ağ yapıları** box (also on Ayarlar → Kaynaklar) says which network
  set-ups are supported and which are not (KURULUM/INSTALL section 0.1).
- **Alerts** (docs/UYARILAR.md / ALERTS.md): `R20-lease:<cause>` and `R20-nat:<cause>`, one alert
  per cause, in the existing system job. Lease evidence stopped per table (`none:fg_dhcp`,
  `none:mt_arp` high; `none:mt_dhcp` medium and only while no ARP-LOG row arrives either) or
  absent (info); FortiGate DHCP records refused, not converted, not verified on site, without
  renewals, with unlimited or unexpected lease times, outside the configured networks, many
  addresses for one MAC; a static device in the DHCP pool (`fortigate-decline:<net>`); two DHCP
  sources on one network; a source clock beyond ±120 s (`source-clock`); a shared MAC on a srcmac
  interface; for the exits a failed conversion, an unreadable value, an old NAT row changed during
  a rollback and a NAT address no exit defines that keeps carrying sessions (`unmapped-transip`).
  An e-mail carries only the cause's fixed line, counts, a VLAN number, an evidence table and a
  clock offset; addresses, MACs and source names show on the Güvenlik page only.
- **Arama:** `fg_dhcp` rows belong to the kind **FortiGate olayı** (the MikroTik kind is now
  **DHCP (MikroTik)**) and answer `srcip:`, `ip:`, `mac:`, `interface:`, `logid:` and `level:`;
  the new field `srcmac:` (`kaynakmac:`) searches the session records' source MAC (no index,
  scanned in time slices; `mac:` never reads the session records).
- **Report (PDF):** section 4 names the exit (**Çıkış**) with the method paragraph of its
  resolution and mode (double NAT, a shared public address, what would make the answer exact), and
  each candidate's exit and `dstintf`; the chain shows a FortiGate lease's logged time and last
  DHCP record, the srcmac step as partial evidence with its session line, and the
  unconfigured-sender warning; a method note says whether the FortiGate DHCP record format was
  verified (by whom and when). The cross-check re-parses FortiGate DHCP and srcmac lines with the
  shipped parser; FortiGate DHCP lines outside the lease timeline are shown, not compared.
- **Who sees what** (docs/roles.md): `GET /settings/fortigate/observed` administrators only
  (`settings.manage`; counts and the firewall's own addresses, not audited); the Kira kanıtı card
  with `GET /sources` (`sources.read`). srcmac and FortiGate DHCP host names are personal data,
  shown under the same permissions as MikroTik leases. No new permission key and no new audit
  action; `lookup.run` also records the exit's resolution and names (never a person, MAC or raw
  line), and the upgrade writes one `settings.change` entry for the conversion of the old NAT keys.
- **5651 Temel:** FortiGate DHCP, srcmac, the exits and both alert rules are basic functions; no
  licence state blocks them.
- **Database:** revision `0017_fortigate_dhcp` (the time-series table `fg_dhcp` with indexes on
  `(ip, time)` and `(mac, time)`, a hypertable or monthly partitions like the other log tables;
  `fg_traffic.srcmac`, added empty, also on TimescaleDB with compressed chunks;
  `leases.lease_seconds`) and `0018_nat_egresses` (settings only: an administrator's old NAT values
  become exits through a frozen copy of the conversion, a result that does not validate becomes
  `{}` with `R20-nat:conversion-failed`, the old rows are kept unread for one version; status rows
  `nat.egresses_migration` and `nat.egresses_changes`). FortiGate DHCP lines loaded before the
  upgrade stay `fg_event` rows (stored, searchable, sealed).
- **API:** `GET /settings/fortigate/observed?hours=` (1–72; 504 `fortigate_observed_timeout` after
  5 s), `lease_evidence` on `GET /sources`, and in the lookup outcome `egress` (resolution, exits,
  addresses searched, each exit's last change), each match's exit and unconfigured senders,
  `unconverted_dhcp` and a chain's `mac_source`; 503 `lookup_settings_invalid` when a stored
  lookup setting cannot be read; refusal codes `setting_egress_*`, `setting_ipv6_not_supported`,
  `setting_logid_invalid`, `setting_dhcp_msg_invalid`, `setting_interface_invalid`,
  `setting_trandisp_invalid` (MVP-CONTRACTS §18). A lookup's transaction plans every statement
  with its values, as the search does.
- **Rollback:** `Install-Arcilogger.ps1 -Rollback` without `-RestoreDatabase` runs the step
  `leases.prepare-rollback` after the services stop and before the program is swapped:
  `arcilogger-db leases prepare-rollback` sets every FortiGate lease to `low` (derived rows only,
  one transaction) and the count is printed and logged; a failure or `-SkipDatabase` is a warning
  that says what it means for the lookups, never a silent skip. The next `arcilogger-db upgrade`
  sets the leases the previous version left as they were back to `high`, prints how many it
  restored and how many stay `low`, and names the days to rebuild. The step runs on every such
  rollback, so **a rollback between two versions that both read FortiGate DHCP shows FortiGate-DHCP
  chains `low` until the next `arcilogger-db upgrade`** (false lows only, never a wrong person;
  ASSUMPTIONS RB1).
- **Upgrade:** the old NAT values become exits named "Genel IP (yükseltmede aktarıldı)", "FortiGate
  WAN adresi (yükseltmede aktarıldı)" and, where needed, "Genel adresi tanımlanmamış hatlar";
  until each line's public address and interfaces are entered, lookups of an address no exit
  claims carry `egress_public_unknown`. On a FortiGate-DHCP site no chain is built for a client
  until its first Ack in the new version (up to half its lease); do the record check of
  DEVICE-SETUP section 2.4 and set `dhcp.fortigate_format_verified` (KURULUM/INSTALL section 13).
- **Load generator** (tools/loadgen/README.md): site plans `--dhcp-server mikrotik|fortigate|both`,
  `--srcmac` and `--egress NAME:TRANSIP:DSTINTF:SHARE` (dual WAN); the truth file names each
  lease's evidence and each session's exit; the default output is unchanged, byte for byte. Its
  FortiGate DHCP lines are synthetic (the Release logid derived, renewal at half the lease a
  simulation assumption).
- **Performance:** ingest with `fg_dhcp` and `srcmac` stayed within the 5 % gate on the development
  machine: the default mix +0.8 % in-process and +0.7 % with 2 workers, a FortiGate-only spool
  −3.5 % and +1.7 % (docs/INGEST.md). The lookup's probe of an address no exit claims reads the NAT
  address index in its own order, the probe window first (a busy address in about 0.01 s); a busy
  unclaimed address with no session over the moment takes about 0.5 s at a million lines a day,
  under a bound of 1 s of its own, the whole lookup staying at about 1 s (contract, "Deviations —
  c3").
- **Docs:** DEVICE-SETUP section 2.1–2.6 (NTP; FortiGate DHCP server records with the site check;
  srcmac and device identification; several internet lines), KURULUM/INSTALL sections 0.1, 13 and
  16, CORRELATION, RAPOR and DENETCI-KILAVUZU (how to read the new report rows), AYARLAR/SETTINGS,
  ILK-ADIMLAR/FIRST-STEPS, and the legal drafts (FortiGate DHCP host names and device-identification
  fields as personal data, for the organisation's counsel).

### Fixes and smaller additions (Wave 3)

- **A 5651 lookup by domain on compressed TimescaleDB chunks** took 6.0–7.2 s at 3 million rows a
  day, over its 5 s: the web and application sub-query's time bound used `COALESCE`, which
  TimescaleDB does not carry into the compressed batches. It is now two plain sub-queries (rows
  with and without a duration) with the same matches: 1.45–1.56 s.
- **Arama** opened with a hand-over's range (from the Panel or Canlı Akış) searched again on the
  default last 24 hours (the Panel's "today" search showed 24 hours and was audited twice); a
  letter typed right after taking a completion could land before the caret; a search whose
  budget ran out while its first window was still being halved returned an empty page that
  repeated the scan, now 504 `search_timeout`.
- **Dialogs** of the devices, reports and users pages and the evidence wizard show their form from
  the first frame (an edit no longer opens on an empty form for one frame).
- **Accessible names** contain the text on screen (WCAG 2.5.3, checked by axe-core 4.14) in both
  languages: user menu, Panel parse failures, settings history, report runs, usage period, Arama's
  raw line, the syntax help's examples.
- **Collector:** a status write refused on Windows (the file open in another process) is tried
  again at the next round, so a new configuration's report no longer waits for the next status
  interval.
- **Worker:** the search-counts backfill ends its run when its budget is used up also on a coarse
  clock (Windows).
- **Dependencies:** the Python, web runtime and web tooling groups and the CI actions updated
  (Dependabot).
- **Development:** `CLAUDE.md`, `CONTRIBUTING.md` and a pull request template for the team; CI
  runs on pull requests and on the `wave-*` integration branches, each test once, the Windows job
  in three parts; `tools/dev/cloud-setup.sh` prepares PostgreSQL 16 for a cloud session.

### Search, live tail and sources (Wave 2)

Three of the menu's placeholder pages are real: **Arama**, **Canlı Akış** and **Kaynaklar**, plus
a parse-failure card on the Panel ([ADR-0015](docs/adr/0015-search-live-sources.md);
docs/ARAMA.md / SEARCH.md; MVP-CONTRACTS §17). Decided by the developer on the owner's delegation;
the owner may change it (the ADR lists the open points D1–D10, ASSUMPTIONS AR1–AR10 the
implemented defaults, not yet confirmed).

- **Arama:** one query language (`srcip:172.20.41.105 type:web action:blocked`; a comma is OR
  inside a field, `-`/`NOT` negates, free text; no OR across fields and no parentheses, refused
  with a hint), checked as you type with the faulty part underlined, Turkish field aliases and a
  `?` help that says which fields are fast. The top-bar search box turns an address into `ip:`, a
  MAC into `mac:` and a host name into `domain:`; the time range picker (up to `search.max_days`,
  31) shows on Arama. Rows come newest first with the device and the receive time; a record
  drawer shows every field and the raw reference, and its **Ham satır** opens the exact raw line
  with its SHA-256. Statistics: total, histogram (drag to narrow the range) and the top values by
  source, kind, action, VLAN and the VLAN's default department; a value adds or excludes a clause.
  Saved searches (at most 100 a user), CSV/XLSX export (at most 100,000 rows or 120 s, formula
  characters neutralised) and **Kanıt paketine aktar** into the evidence wizard (up to 10,000
  lines; the unsealed days' lines can be left out). A partial answer says how far it got; older
  days than the database holds are reached through an evidence package (the page says so).
- **Canlı Akış:** watch lines arrive, filtered with the same query language, read from the raw
  spool, so it works while ingest lags or is stopped and a new `unknown-<ip>` sender appears within
  seconds (the **Bilinmeyen göndericiler** filter). A paused stream keeps its place for 120 s and
  resumes with the lines it missed while the server's buffer still holds them; otherwise a `gap`
  notice offers the interval in Arama. Lines not shown (rate, queue, parse budget) are counted with
  their reason. Highlight rules are kept only in the tab's session storage and cleared at sign-out;
  a line opens in Arama (±5 minutes).
- **Kaynaklar:** every source's state (active, silent, never sent, switched off, not configured, or
  configured since as a source), messages a minute loaded into the database with a 60-minute
  sparkline, lines today, bytes not yet loaded, parse failures, and per source 48 hours, 14 days,
  the gaps of 30 days and the newest 50 lines of today's (else yesterday's) spool file, each
  opening as stored with its SHA-256, with the range's SHA-256. **Kaynak olarak tanımla** (for an
  unknown sender) opens Ayarlar → Kaynaklar with the add dialog filled in; Kaynaklar itself never
  changes the configuration.
- **Panel:** a parse-failure card (today's failures per 1,000 lines, the last 7 days, today's three
  commonest patterns with their source, an example line and **Aramada göster**); card titles wrap
  instead of being cut off at 1280–1920 px.
- **Who sees what** (docs/roles.md): Arama and Canlı Akış administrators and auditors
  (`search.read` and the accepted privacy notice); export administrators (`search.export` and a
  re-authentication within 5 minutes); Kanıt paketine aktar `evidence.create`; Kaynaklar
  administrators (`sources.read`; its newest lines also `search.read` or `lookup.run` and the
  notice); the Panel card also with `dashboard.view`. No new permission key.
- **How:** typed tables (`fg_*`, `mt_*`) drive any query with a typed field, `messages` the rest;
  all SQL from input is bound parameters (an injection property test). A search page scans time
  windows newest first within `search.request_budget_ms` (4 s) and answers what it found with a
  cursor (504 `search_timeout` only when not even 5 minutes fit); direct statistics within
  `search.stats_budget_ms` (8 s). An exact hourly summary table `search_counts`, written by the
  ingest loader in the same transaction as the rows (exactly-once kept) and recounted for older
  files by the worker job `search.counts_backfill`, gives totals and facets of a broad 30-day query
  in well under a second (`arcilogger-db search-counts status | rebuild | verify`). One new index on
  the log tables, `ix_fg_web_rhost_time` on `fg_web (reverse(lower(hostname)), time)`, makes
  `domain:` a prefix range; no trigram index (it would cost ingest most of its time). Limits:
  3 searches at once (2 a user), 30 new searches a minute, 10 exports an hour. The live tail has one
  spool reader per API process and a two-step stream (`POST /live/streams`, then a `GET` that holds
  no database connection); limits 4 streams (2 a user), 100 lines a second, 60 minutes. Caddy does
  not compress it and the API waits at most 5 s for open streams on shutdown. Ingest with the counts
  step met the 5 % throughput gate on average on the development machine (−1.9 % in-process,
  −3.8 % with 2 workers on a load-generator day), without margin; it is re-measured on site
  (docs/INGEST.md).
- **TimescaleDB:** a search window ends at a table's newest compressed chunk, so the uncompressed
  days are planned on their own statistics and use the selective indexes (`domain:`,
  `type:dhcp mac:`); the compressed days are read only while the page is not full. A page's fill-in
  queries are bounded by its rows' time range, and the search's read-only transaction plans every
  statement with its values (`plan_cache_mode`, transaction-local). Measured on a tenth and on half
  of the tier-1 data; the full tier-1 run on TimescaleDB is the Linux CI job's (ADR-0015, ARAMA.md /
  SEARCH.md section 13).
- **KVKK and audit:** every search (`search.run`), opened record (`search.event`) and raw line
  (`raw.view`), evidence hand-over (`search.refs`), export (`search.export`), saved-search change
  (`search.saved_create` / `_update` / `_delete`), live stream (`live.open`, `live.close` with the
  delivered ranges) and newest-lines read (`raw.tail`) is audited; search, hand-over and export
  before the data leaves the API. Inputs, counts and hashes, never row content (docs/DENETCI-KILAVUZU.md
  section 9). Follow-up pages and statistics serve only an audited `search_id` (30 minutes) and are
  not audited again. No query in a URL or the browser history (the address holds only the range and
  a saved search's id); hand-overs between pages stay in memory.
- **5651 Temel:** search, live tail and sources are basic functions; no licence state blocks them
  (LISANS/LICENSING).
- **Database:** revision `0016_search` (`search_counts`, `saved_searches`,
  `ingest_offsets.counts_from_line`, the `fg_web` index; up and down on native partitions and on
  TimescaleDB with compressed chunks); on native partitions `drop_before` also deletes the
  `search_counts` of the months it drops; the settings group `search` ("Arama ve canlı akış",
  docs/AYARLAR.md / SETTINGS.md).
- **API:** `/api/v1/search*`, `POST /live/streams`, `GET`/`DELETE /live/streams/{id}`,
  `/live/status`, `/sources`, `/sources/{name}`, `/sources/{name}/tail`, the `parse_failures` box of
  `/dashboard/summary`; `GET /raw` also opens an `unknown-<ip>` spool folder (MVP-CONTRACTS §17,
  docs/roles.md).
- **Upgrade:** ingest, API and worker stay stopped while `0016_search` builds the `fg_web` index
  (about 15 s per 10 million rows on the development machine with the table in memory, about
  2 minutes for 400 days at a million lines a day; longer from disk); the collector keeps writing
  the spool, so ingest catches up afterwards, and the worker recounts older files in the background
  (docs/INSTALL.md / KURULUM.md, which also advise `[database] pool_size` 10 when several people
  search). DEVICE-SETUP section 4 checks a new device in Canlı Akış.

### Browser administration (Wave 1)

Most of what an administrator configured on the server console is now a page in **Ayarlar**
([ADR-0014](docs/adr/0014-browser-administration.md); docs/AYARLAR.md / SETTINGS.md). Decided by
the developer on the owner's delegation; the owner may change it (the ADR lists the open points).

- **Pages:** Genel (organisation name, time sources, clock alert threshold), Uygulama ayarları
  (every database setting by group with Turkish and English validation, work hours and holidays,
  lease rebuild), Kamu SM (wizard: Java, Zamane client upload, root certificates and legal root
  fingerprints, customer number and password tried with a credit query, stamp test, test ↔
  production, credit threshold), Kaynaklar (add, edit, switch off, remove a log source with its
  firewall opening), E-posta (SMTP, password, CA file, alert e-mail settings, trial and test
  e-mails), Replika, Saklama (≥ 730 days, the days to delete shown and typed to confirm), Yedekleme
  and Değişiklik geçmişi (requests, cancel, revert, configuration versions, a diagnostics ZIP
  without secrets). **Şimdi mühürle** (202 instead of 501) and **Kontör sorgula** on the sealing
  page. Secret fields are never filled in: "Kayıtlı ✓" and the time of the last change.
- **How:** a new service **ArciAdmin** (`NT SERVICE\ArciAdmin`, no listener, no administrator
  rights) is the only writer of `arcilogger.toml` and `secrets.json`; the API never gains write
  access to the configuration, `secret.key` or the vault. The API hands it Ed25519-signed requests
  through a protected inbox (secrets as Fernet tokens), with one allow-list of rules checked by
  the API (422 at once) and again by the agent; the agent applies atomically (write-ahead record,
  version store, replace, verify, rollback, crash recovery), probes credentials before saving and
  reports each step. The input-free SYSTEM task `\ARCILOGGER\ApplySystem` derives the firewall's
  syslog addresses and Windows Time peers from the validated file and widens a syslog opening only
  after the collector has loaded the new source. Seal now runs in the Seal task (LOCAL SERVICE)
  through a request the agent signs; credit query, stamp test and replica test run in the new
  task `\ARCILOGGER\Requests`; test e-mail and lease rebuild are worker jobs (`admin_requests`,
  revision `0015`). Executables are never free values from the browser and every task re-checks
  them at run time (`execpolicy`); an unknown Zamane release needs `approve-jar` on the console
  after `jar_hold_hours`.
- **No restart:** collector and ingest reload `[[sources]]`, worker and API the rest of the
  configuration within seconds, keep the old one on an error and report the SHA-256 they loaded;
  the 5651 pipeline never stops for a settings change. A switched-off source is still recorded
  (raw logs are never dropped) but no longer expected by R15 or the sealer's gap check.
- **Safety:** credential, retention, replica, clock and source switch-off changes need a recent
  re-authentication; the 730-day floor is enforced by the API, the agent and the retention job;
  nothing the browser changes can move `data_dir` or `secrets_dir` (checked against the
  installer's `data-root.txt` by every service and task); `-DisableBrowserAdministration` switches
  it all off. Every request and outcome is audited (`admin.request` in the API transaction, the
  agent's journal imported into the hash chain, Windows Event Log source `ARCILOGGER-Admin`); new
  alerts `R20-admin` and `R20-config-change`. No licence state affects any of it.
- **API:** `/api/v1/admin/config/*`, `/admin/status`, `/admin/requests`, `/admin/diagnostics`,
  `GET/PATCH /settings`, `POST /seals/{day}/seal`, `POST /credits/query` (docs/MVP-CONTRACTS.md
  §16, docs/roles.md).
- **Installer:** ArciAdmin, the Requests and ApplySystem tasks, the administration keys,
  `admin\policy.json`, the Event Log source, `data-root.txt`, trusted executables recorded on
  install and upgrade, `Set-ArciFirewall.ps1 -SyncFromConfig` updating rules in place;
  `-SourceNetwork`, `-DisableBrowserAdministration`; rollback to a version without ArciAdmin
  removes it. The Windows end-to-end job covers seal now through Caddy, a site name change, a new
  source reaching both firewall rules, the credit query and the rollback. A dry run whose plan
  trips over a problem its preflight already reported returns 2 like the real run (was 3).
- **Stays on the console:** installation and upgrade, PostgreSQL, `[database]`, `[api]`,
  `[collector]`, `[ingest]`, `[worker]`, `[seal]`, paths and executables, removing a root or a
  legal fingerprint, `admin\policy.json`, the firewall's administrator addresses, the database
  password, the pseudonym key, key rotation, `approve-jar`, `create-admin` (SETTINGS §5).

### Fixes and smaller additions (Wave 0)

- **Usage analytics counted no 192.168.0.0/16 network.** The private-source filter of the usage
  engine listed an example range instead of `192.168.0.0/16` (a leftover of the removal of the
  first installation's data). It now lists the three RFC 1918 blocks and IPv6 ULA; the engine
  version went from 2 to 3, so the recent days are rebuilt. 5651 lookups were never affected.
- **IPv6 lookups are refused instead of guessed.** `POST /lookup/5651` answers 422
  `ipv6_not_supported` (TR/EN) for an IPv6 public or internal address (also `::ffff:a.b.c.d`),
  audited as `lookup.run_failed`; ARCILOGGER collects no IPv6 identity evidence (NDP, DHCPv6). The
  engine keeps only sessions of the requested address family, so no IPv4 session can be a candidate
  for an IPv6 address. The 5651 Sorgu form refuses it before sending (docs/CORRELATION.md §7).
- **Exclude a person from usage reports in the web interface.** `usage_excluded` in the person API
  (administrators, audited with old and new value), the persons CSV export and import (optional
  `usage_excluded` column), the person form ("Kullanım raporlarından hariç tut"), a badge in the
  list and the drawer. 5651 lookups still name the person.
- **Licence alerts** (`R20-license`, `R20-license-limit`, `R20-license-maintenance`): the trial
  ends within 3 days, a time-limited licence ends within 14 days, paid modules are off (trial
  ended, expired, version not covered; invalid or wrong server as high), a users or log sources
  limit is crossed, maintenance ends within 30 days or has ended (info). Decided on the trusted
  time, auto-resolved, one alert per occurrence (a dismissed one stays closed until the situation
  changes), e-mail facts limited to state, dates, module names and counts. The `license.check` job
  never fails because of them and no 5651 function depends on them (docs/ALERTS.md, UYARILAR.md).
- **Web interface without developer traces.** The component kit (`/ui-kit`) and its texts are only
  in the development and test builds (`npm run build:e2e`), never in the shipped `npm run build`;
  the build fails if a source map or a kit module would ship. Placeholder pages and the settings
  page no longer show milestone codes ("Bu sayfa sonraki bir sürümde eklenecek.").
- **Change your own password** from the user menu ("Parolayı değiştir"), with the current password;
  other sessions end, wrong attempts count towards the lockout.
- **Supported network setups** stated before installing (KURULUM / INSTALL §0.1, LISANS /
  LICENSING §2, README): person attribution needs MikroTik DHCP lease evidence, one WAN mapping,
  one site per installation, no IPv6 lookups, usage analytics only from FortiGate traffic, web
  filter, DNS filter and application control logs of private source addresses; other vendors'
  logs are collected, sealed and stored as plain lines.

### Offline licensing

ARCILOGGER is sold to many organisations under a **perpetual licence plus annual maintenance**
(owner decisions of 2026-10-20, [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) "Licensing", L1–L8 and
C-1–C-16; [ADR-0013](docs/adr/0013-offline-licensing.md); customer guide
[docs/LISANS.md](docs/LISANS.md) / [docs/LICENSING.md](docs/LICENSING.md)). The licence switches
only paid modules; it never stops a 5651 function and never changes evidence. ARCILOGGER remains
*designed to support* 5651 obligations; nothing here is a claim of legal compliance.

- **Modules:** `core_5651` (5651 Temel: collection, sealing, retention, 5651 lookups, evidence
  packages, devices and persons, alerts, audit log — on in every licence and every state),
  `usage_analytics` (Kullanım analizi), `usage_reports` (Zamanlanmış kullanım raporları, needs
  usage analytics) and `bulk_import` (Toplu içe aktarma (CSV); manual entry always works). Limits:
  log sources (a configured device or any sender seen in the last 30 days; soft: logs over the
  limit are still collected and sealed) and active users (creating or re-enabling a user is
  refused at the limit; 3 without a valid licence, none during the trial).
- **Trial:** 14 days with every module and a "Deneme sürümü — N gün kaldı" banner. The start
  survives a reinstallation with the old data folder (the oldest vault and spool day, the audit
  log and the licence history pull it back); a trial start ahead of the trusted time makes the
  trial unavailable (`trial_clock_inconsistent`).
- **Offline activation, no network call:** a request code (`ARCIREQ1-…`: server id from the
  Windows MachineGuid and SMBIOS UUID, installation id, version, organisation name, checksum) is
  shown on the new licence page (Ayarlar → Lisans), by `arcilogger-db license request-code` and at
  the end of the installation; the licensor (Arman Khadangan, arman@khadangan.com) returns a
  signed `.lic` (Ed25519 over the RFC 8785 canonical payload, key ids for rotation), installed in
  the web interface, with `arcilogger-db license install` or the installer's `-LicenseFile`.
  ARCILOGGER never contacts the licensor; no telemetry.
- **States and banners:** `trial`, `trial_expired` (Lisanssız), `licensed`, `invalid`,
  `wrong_server`, `expired` (a time-limited `valid_until` demo licence), `version_not_covered` (a
  version released after `maintenance_until`); all but `trial` and `licensed` leave 5651 Temel
  only. Trusted time: the latest of the system clock, the newest verified Kamu SM time stamp, the
  end of the newest vault day, the version's release day and the licence's issue time, so setting
  the clock back extends nothing while there are sealed days.
- **API:** `GET/POST/DELETE /api/v1/license`; the routers `/usage`, `/reports`, `/imports` answer
  403 `module_not_licensed` while their module is off; `POST /users` and re-enabling a user 409
  `license_limit` at the users limit; every licence install, removal, refusal, state change and
  limit crossing is audited (`license.*`). `arcilogger-api create-admin` respects the users limit
  except for true recovery or with `--deactivate USERNAME`.
- **Worker:** job `license.check` (every 5 minutes; records state changes and limit crossings,
  writes `state\license-status.json` for the installer); the paid jobs (`usage.*`,
  `reports.run_due`) are skipped while their module is off (one `skipped` row a day), an open
  `R20-report` is resolved when scheduled reports go off; `arcilogger-worker usage rebuild` is the
  only gated command. Data minimisation jobs and every 5651 job always run.
- **Database:** revision `0014_license` with the append-only tables `license_installation`,
  `license_files`, `license_events` (docs/DATA-MODEL.md); the licence is the signed file, verified
  again on every evaluation.
- **Installer:** `-LicenseFile` (installed right after the schema migration; a refusal is a
  warning, exit code 1), the maintenance rule before any change (a bundle whose `release_date` is
  after the licence's maintenance date stops with exit code 2; `-AllowUncoveredUpgrade` installs it
  with 5651 Temel only, so a 5651 fix can always be installed), a warning before upgrading an
  installation without a licence whose vault holds older sealed days, and step 6 of the next
  steps with the request code. `bundle-manifest.json` and `VERSION.json` carry `release_date`.
  The services and scheduled tasks run Python isolated (`python.exe -I -X utf8 -u -m …`, tasks
  `-I -m …`).
- **No secrets:** the production key set is empty until the owner commits the first public key,
  so every installation runs in trial or unlicensed mode until then; the issuer tool
  (`tools/license-issuer`, owner only) is never part of the bundle, and the test helpers are
  excluded from the wheels.
- **Lisans Yöneticisi (licence manager, owner only):** the issuer as a Tk window for the owner's
  offline Windows PC (settings, key generation, issue with the earlier-licence choice, registry
  with renewal, verify), one implementation with the command line, shipped as one zip built by
  `.github/workflows/license-manager.yml` (`tools/license-issuer/README.md`); never in the bundle.
- **Threat model, stated plainly** (ADR-0013, docs/SECURITY.md section 13, LICENSING section 11):
  identity spoofing on virtual machines, a full fresh install, a database owner disabling triggers,
  reading already computed usage tables with SQL and a determined administrator patching the
  checker cannot be prevented offline; the remedies are contractual. Hardening (compiled licence
  module, signed file manifest, Authenticode) is phase 2.
- **Existing installations** upgrade into `trial_expired` (their sealed days are older than 14
  days) unless a licence is installed with or before the upgrade (docs/LISANS.md section 8).
- **Setup .exe and releases:** `ARCILOGGER-Setup-<version>.exe /LICENSEFILE="<file>"` and
  `/ALLOWUNCOVEREDUPGRADE` pass `-LicenseFile` and `-AllowUncoveredUpgrade` to the installer (C-6:
  also possible with only the setup .exe); the upgrade warning for an installation without a
  licence now sends the operator to Ayarlar → Lisans after the upgrade instead of to
  `-LicenseFile`, and names `usage.auto_rebuild_days` (default 7) and **Yeniden hesapla** for
  the usage rollups; the release job
  stops a tag whose `RELEASE_DATE` was not bumped (`tools/release/check_release_date.py`).

### Product without customer data

ARCILOGGER is a product for many organisations (owner decision 2026-10-20,
[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) P1–P8). It remains *designed to support* 5651
obligations; nothing here is a claim of legal compliance.

- **No data of a particular organisation:** the code, the tests, the documents and the defaults
  name no organisation, its e-mail addresses, devices, firmware versions, addresses, network plan
  or guest-network design. Tests and examples use a fictional organisation and network
  (`Örnek A.Ş.`, `ornek.example`, private and documentation address ranges).
- **Organisation name:** `site_name` is empty by default (one line, at most 200 characters, no
  control characters). The installer asks for it (`-SiteName`, required on a first non-interactive
  installation, kept on an upgrade); `arcilogger-core config-check [--json]` reports what a
  production configuration still lacks (no organisation name: error, exit code 1; no local time
  source: warning). Without a name the 5651 report leaves it out of its opening sentence and
  footer (a dash in the Kurum field), the usage reports out of the mail subject, the summary, the
  HTML header, the PDF cover and footer and the workbook's "Site" row; evidence packages and the
  password checks cope with it. `GET /api/v1/auth/me` returns `site_name`; the web sidebar shows it
  under the product name and nothing when it is empty (the locale key `app.site` is gone).
- **Time source:** `[clock] ntp_server` is empty by default; the installer asks for it
  (`-NtpServer`). Without it the hourly clock check asks only `[clock] fallback_server` and records
  a warning with every sample, `Set-ArciTime.ps1` sets the fallback as the only source and the
  manifest records the source the server follows; no time server at all is reported and alerted
  instead of querying an empty host.
- **Admin address:** `-AdminAddress` of the installer and of `Set-ArciFirewall.ps1` has no default:
  given, remembered, or typed at the console; otherwise a clear Turkish/English stop with
  `10.0.0.10` / `10.0.0.0/24` examples. Given on an upgrade, `-SiteName` / `-NtpServer` rewrite only
  that line of the existing `arcilogger.toml` (the previous file is kept as
  `arcilogger.toml.before-<time>`). The setup's publisher is `ARCILOGGER`; the example
  configuration has placeholders and lists the fictional example network's `[[sources]]` as
  comments only (ADR-0007 amendment 2026-10-20).
- **No log source on a first installation:** the `arcilogger.toml` a first installation makes
  from the example configures no device (before, the example network's devices: silent-source
  alerts for devices that never send, and their parser for a sender at one of their addresses).
  The commented example sources stay in the file (remove the `#` to use them; `tools/loadgen`
  sends from them). No source is a valid state: `Set-ArciFirewall.ps1`
  makes no syslog rule then (514 stays closed) and warns instead of stopping; the installer's
  preflight warns instead of failing (`preflight.sources`, also with `-SkipFirewall`, where every
  sender becomes `unknown-<ip>`, and on a new installation over the `arcilogger.toml` a removal
  kept, read without Python then; an invalid address still stops it before the firewall step), its
  next steps say to add the `[[sources]]` of the firewall and the core router, and the
  installation ends with warnings (exit code 1). `arcilogger-core config-check` warns
  (`sources`); the Panel's "Log alımı" box says "İzlenen kaynak yok" with what to add instead of
  "0/0 kaynak aktif" and a green badge. An existing `arcilogger.toml` is never replaced, so an
  upgrade keeps its sources. DEVICE-SETUP §4, KURULUM / INSTALL §7.1 and ILK-ADIMLAR /
  FIRST-STEPS §5 describe the step.
- **Licence:** `LICENSE` names the rights holder, Arman Khadangan; every organisation uses the
  software under its own licence agreement with the rights holder.
- **No network plan shipped:** the production seed writes only the default settings — no VLANs,
  no departments, no sources (`lease.server_seconds` now `{}`); `arcilogger-db upgrade` no longer
  reports VLAN and department counts. Revision `0013_site_data` hands a `lease.server_seconds`
  row an earlier seed still owned to `:migration`, so an existing site keeps its lease times;
  VLANs and departments seeded earlier stay. The tests' example site lives in
  `arcilogger_db.testing` (`seed_example_network`, `EXAMPLE_VLANS`; the test template database has
  it unless `create_template(example_network=False)`).
- **Texts name device roles, not host names:** a missing lease points at the main router
  (MikroTik, the DHCP server), the evidence records read "DHCP kaydı (MikroTik)", the guest-network
  explanation describes the configuration option (another exit than the FortiGate, e.g. a separate
  line or a VPN tunnel), the replica alert names `[replica] target`; the fallback link of the alert
  and report e-mails is `https://arcilogger.internal` (the installer's default server name).
- **Log format samples** (`tests/fixtures/format/`: `fortigate-sample.log`,
  `mikrotik-log-print-sample.txt`): synthetic, modelled on FortiOS 7.6 / RouterOS 7 output with
  their field names, order, quoting and separators; all values fictional, `SHA256SUMS`
  regenerated. Tests, the load generator and the documents refer to them.
- **A small example:** the example configuration's commented `[[sources]]` are a firewall, the
  core router (the DHCP server) and one switch; `tools/loadgen` sends from exactly these. The
  tests' example network (`arcilogger_db.testing`) is a small fictional plan with generic
  purposes: static VLANs for network devices and servers, three department VLANs, one staff
  Wi-Fi, a guest Wi-Fi with 1 h leases and a /30 transit link. The load generator simulates it
  with documentation MAC addresses (RFC 7042) that follow each device's address, guests that
  browse through the firewall like everyone else and neutral policy and log-prefix names. The UI
  kit page shows only placeholders and documentation addresses.
- **Usage background domains:** the shipped `usage.background_domains` list the operating systems'
  services, certificate checks and the update hosts of the two supported log sources (FortiGate,
  MikroTik); administrators add the update and cloud hosts of their own products (IP phone system,
  antivirus, …).
- **Documents:** they speak of the organisation; the device guide uses placeholders
  (`<arcilogger>`, `<firewall>`, `<router>` …) and describes a guest network that bypasses the
  FortiGate as a configuration option (DEVICE-SETUP §5); KURULUM / INSTALL §7.1 describe the
  organisation's own values; the legal drafts are templates (`[Şirket unvanı]`, `[adres]`,
  `[VERBİS kayıt no]`, still "Taslak — hukuk müşaviri onayı gerekir"); the ADRs, ASSUMPTIONS and
  this changelog keep their decision history without naming an organisation, and ASSUMPTIONS
  lists the installation-time settings (G1–G10) every organisation enters.
- **Upgrading an existing installation** changes nothing it relies on: `arcilogger.toml` (with its
  `site_name` and `[clock] ntp_server`), the remembered admin addresses, the VLANs, departments and
  lease times in the database are kept.

### VLAN management

- **The organisation enters its network on the web:** the **Departmanlar ve VLAN'lar** tab of
  Cihazlar ve Kişiler adds, edits and deletes VLANs and subnets (`devices.manage`; other roles
  with `devices.read` see the list only). A new installation shows "Henüz VLAN yok: ağınızı
  ekleyin"; the form checks the network (prefix length, host bits zero — the message names the
  network meant —, no IPv4 network written as IPv6), the VLAN id (1–4094) and the DHCP server
  name before sending, the server's refusals are shown in the form, and a deletion asks for a
  confirmation that says what it changes. After a save the focus goes back to the row's menu
  button, also to the new row of a VLAN moved to another network. The department select of each
  row stays.
- **API** (`/api/v1/vlans`, docs/MVP-CONTRACTS.md section 11): `POST` (201), `PATCH` with only the
  fields sent — the old body `{subnet, department_id}` still changes only the department —,
  `new_subnet` to correct a VLAN's network (name, department and flags stay) and
  `DELETE ?subnet=` (204). An IPv4 network written as IPv6 (`::ffff:10.0.20.0/120`) is refused
  with the IPv4 network meant: no IPv4 address falls into it. Subnet, VLAN id and DHCP server are
  unique (409 `vlan_exists`, `vlan_id_taken`, `dhcp_server_taken`, Turkish and English texts
  naming the network that holds them); nested subnets are allowed, the most specific wins as
  before. `VlanOut` gains `dhcp_server` and `notes`. Audited as `vlan.create`, `vlan.update` (now
  with `changed`), `vlan.delete`: subnets, VLAN ids, department ids and the guest flag, the texts
  only as field names (`vlan.create` lists the fields the request sent with a value).
- **Changes take effect like a department change:** the device list, the department list and
  the next 5651 lookup use the table at once (stored lookups keep their result); the usage pages
  read the VLAN's department by VLAN number when they are shown (a deleted VLAN's department goes
  at once), and a changed VLAN number of an address (a renumbered VLAN, a new one or one with a
  new network; for addresses leaving a deleted or moved VLAN only the hours without a lease, the
  leased hours keep the lease's number) is rebuilt by `usage.reattribute` for the last
  `usage.auto_rebuild_days` days (tested in `worker/tests/test_usage_jobs.py`).
- **Texts:** the departments' empty-list hint no longer says the installer creates departments;
  the first-steps guides (ILK-ADIMLAR / FIRST-STEPS sections 5 and 12), KURULUM / INSTALL,
  roles.md, DATA-MODEL, ARCHITECTURE and ASSUMPTIONS (P4, G6, V1–V4) describe entering the VLANs;
  the "VLAN list cannot be entered" limitation is gone.

### Usage reports

- **Scheduled usage reports** ([ADR-0012](docs/adr/0012-scheduled-usage-reports.md),
  [docs/KULLANIM-RAPORLARI.md](docs/KULLANIM-RAPORLARI.md) /
  [docs/USAGE-REPORTS.md](docs/USAGE-REPORTS.md)): the worker job `reports.run_due` (every
  minute) runs the schedules of `report_schedules` (revision `0011_reports`) — Gün sonu raporu
  18:30 (the day so far), Tam gün raporu 07:00 (the previous day), weekly (Monday 07:15, the
  previous ISO week) and monthly (day 1 07:30, the previous month), shipped disabled until
  recipients are set, person-level with names (owner decision 2026-10-20). Europe/Istanbul local
  times; after downtime a schedule runs once for its newest occurrence (`reports.catch_up`).
- **Three renderings of one document:** an HTML e-mail made for Outlook and Gmail (tables and
  inline styles, no images, scripts or external resources, the ARCILOGGER address the only link,
  Turkish first with an English summary line), a PDF (cover, one section per department with its
  persons, per-person detail with the top 20 sites, categories and blocked attempts, "Sayfa n /
  N", generation time) and an Excel workbook (Özet, Departmanlar, Kişiler, Kişi-Site, Kişi-Saat,
  Engellenenler, Cihazlar; durations as time values, bytes as numbers, frozen and filtered
  headers, no formulas). Every rendering prints the method note: usage times are estimates. The
  content: company totals, departments, every person, notable changes vs. the 7-day average, the
  period's security alerts (catalogue texts only, never a log line) and the previous day's
  sealing status.
- **Delivery** through the alert e-mail's SMTP client: one e-mail per recipient, PDF and workbook
  attached up to `reports.max_attachment_mb` (15 MB, else a download link), retries with the
  alert e-mail's backoff, per-recipient state in `report_runs`, the local alert `R20-report` on
  giving up. Files in `exports/usage-reports/<run>/` with SHA-256, never in the vault, deleted
  after `reports.keep_days` (400 days) by `reports.purge`.
- **API** `/api/v1/reports` (`reports.manage`): schedules (validated recipients, `HH:MM`,
  weekday / day of month), "run now", run history with deliveries, downloads checked against
  the SHA-256, preview; every change, run, download and preview audited (addresses only as a
  count); the KVKK notice for person-level content.
- The DejaVu fonts, their registration, the PDF styles and the PDF text extraction moved from
  the API into `arcilogger_worker.pdf` (re-exported under the old names); the official 5651
  report renders the same bytes. New worker dependencies: reportlab, openpyxl (MIT).
- Review fixes: an end-of-day schedule at 00:00 (an empty period) is refused by the API (422
  `time`) and the Raporlar form, and one stored before no longer stops the other schedules; a
  control character in a host name is written as `\xNN` in the workbook instead of failing the
  whole report; the PDF matches a person's sites and blocked attempts by the subject, so persons
  (or a person and a device) with the same name stay apart; reports over several days date the
  first and last activity; a list of blocked attempts cut at 50 000 says so (workbook, PDF, data
  quality); a day over `usage.blocked_per_day` gets a data quality note (a whole-day report lists
  only its stored attempts, a part-day report such as the end-of-day one can count only those);
  the group without a department is "Bilinmiyor" in every part of a report; editing a
  schedule's name, recipients or formats keeps an occurrence the worker has not run yet;
  pseudonyms without the secret `usage.pseudonym_key` are refused with 409
  `pseudonym_key_missing` (preview, schedule, "run now") and the worker uses its own secret
  store; the docs explain that `reports.max_attachment_mb` counts the attachments before base64
  encoding.

### Backups

- **Scheduled database backup** ([ADR-0009](docs/adr/0009-database-backup.md),
  [docs/YEDEKLEME.md](docs/YEDEKLEME.md) / [docs/BACKUP.md](docs/BACKUP.md)): the new task
  `\ARCILOGGER\Backup` (daily 01:40, LOCAL SERVICE) runs `arcilogger-db backup run` — `pg_dump -Fc`
  as the ARCILOGGER role into `D:\ARCILOGGER\backups\scheduled\`, the password only in a temporary
  password file, written under a temporary name and checked with `pg_restore --list` before it is
  renamed, a sidecar with SHA-256 and versions, `state\backup\last.json` after every attempt and an
  `R20-backup` alert on a failure; refused below `[backup] min_free_gb` plus the newest dump's size.
- Retention: the newest dump of each of the last 14 days, 8 weeks and 12 months
  (`arcilogger-db backup prune`), deleting only ARCILOGGER's dump names in that folder (never the
  installer's `backups\db`) and never the dump a run has just made (a clock set back:
  `R20-backup:clock`, resolved by a later good backup); `backup list` and `backup verify
  [--deep]`. A failed retention has its own alert key (`R20-backup:prune`, resolved an hour after
  a later good backup that no longer reports it); a dump whose `last.json` could not be written
  raises `R20-backup`, resolved only by a backup that succeeds after it.
- **Restore** only into a new database (`arcilogger-db backup restore --file F --target-db NAME
  [--sha256 HEX]`, the superuser password read from stdin, locale `C` like the installer's
  database, one transaction per statement — TimescaleDB's chunks outgrow the lock table of a
  single transaction —, TimescaleDB pre/post restore) with the steps to switch ARCILOGGER to it;
  the live database is never dropped or overwritten. The sidecar only detects accidental damage
  (any LOCAL SERVICE process can replace a dump with its sidecar): the runbook records each
  copy's SHA-256 off the server and restores with `--sha256`.
- `[backup]` configuration section; NTFS rights: `backups\scheduled` is written by the scheduled
  tasks only and readable by no ARCILOGGER service.

### Alerts

- **Alerts in the database** (`arcilogger-worker`, ADR-0010): `alerts.import` copies every
  `state/alerts.jsonl` line into `alerts` exactly once (byte offset stored with the alerts, torn
  last lines wait, replaced files are read again; after a power cut `raise_alert` starts on a
  fresh line and the torn fragment is skipped); one active alert per dedup key, resolved
  automatically when the problem is gone (`resolved_at`, `details.auto_resolved`).
- **R15 log source silent** (`alerts.r15`, every minute): enabled, expected, configured sources
  silent longer than their `[[sources]] heartbeat_minutes` (example configuration: firewall 10, core
  router 15), judged by what ingest loaded and what the collector received; start-up and never-seen
  grace periods.
- **R20 system checks** (`alerts.system`, every 5 minutes): disk (data and system drive),
  sealing overdue in the morning, verification failed, Kamu SM credit, a sealed day without a
  replica, parse failure rate, failing worker jobs, database backup (`state/backup/last.json`;
  retention failures apart; nothing while `[backup] enabled = false`), clock recovery.
  Thresholds are `alerts.*` database settings with validated ranges.
- **Sealing configuration errors alert:** no Kamu SM credentials, no pinned roots, no Zamane
  client or an unusable secret store raise `R20-seal-config` (high, Turkish and English) once per
  day and cause instead of only exit code 1 and `logs\core.log`.
- **Alert e-mail** (`alerts.notify`): SMTP from the standard library (`[smtp]` in
  `arcilogger.toml`, password `arcilogger-core secret set smtp.password`, certificate always
  verified), policy in the database (`alerts.email_*`, `alerts.web_base_url`); alerts whose
  problem was seen in the last 24 hours (a check that still finds it records so at most hourly in
  `details.seen_at`, apart from the rule's own facts such as R15's last message); digest, hourly
  limit with a summary, retries with backoff, one local `R20-email` alert on giving up, at most
  one repeated e-mail after a crash (`alert_deliveries`, revision `0009_alert_delivery`). The
  e-mail is built from a fixed Turkish/English catalogue and shape-checked facts only: never a log
  line, a person's name or a secret. `arcilogger-worker alerts test-email --to ADDRESS` and
  `alerts list [--open]`.
- Docs: docs/UYARILAR.md / docs/ALERTS.md (every rule, its setting and what to do, SMTP set-up),
  ADR-0010; ILK-ADIMLAR / FIRST-STEPS sections 3, 7 and 9.
- Docs: every IP address an alert e-mail can carry is listed (UYARILAR / ALERTS sections 5 and 7,
  ASSUMPTIONS E5, ADR-0010, the information text draft section 6): besides a new sender's and the
  conflicting one of an IP conflict, the sender address of a message refused for its transport
  (`wrong-transport`, high, so e-mailed by default) and a source name that holds the sender's
  address (`ap-<address>`, `unknown-<address>`, on the source line and in the subject). One
  IP address per alert, and one e-mail can list several alerts: the texts no longer say "one IP
  address". A docs test fails when a rule's sentence in `alerts/texts.py` can show an address from
  a detail the docs do not name (every detail is tried with an address while the others hold
  numbers, flags, days or the words the rule compares with).

### Alerts web API and dashboard

- **Alert state workflow** (ADR-0010 amendment 2026-10-20): Yeni (`open`), İnceleniyor
  (`acknowledged`), Kapandı (`resolved`), Yanlış alarm (`dismissed`) with a fixed transition
  table; re-opening is refused (409 `alert_key_active`) while another active alert holds the
  same dedup key — a check that still sees the problem has opened it. Every change of state and
  every note is an `alert_events` row (revision `0012_alert_events`, who and when), written only
  by `arcilogger_worker.alerts.store`; the checks' automatic resolution and the IP-conflict
  rebuild's re-opening are `system` events with a reason code (the rebuild now also sets
  `resolved_at`, and also closes an İnceleniyor conflict alert it no longer finds, like every
  check).
- **Audit:** `alert.state` (from/to, rule) and `alert.note` with the note's **length** only — a
  note never enters the sealed audit log or an e-mail.
- **Alerts API** (`alerts.read` / `alerts.manage`, administrators): `GET /api/v1/alerts`
  (state, severity, rule, text search over title, rule and source, time range, sorting, paging),
  `GET /alerts/{id}` (sanitised details, events, step-by-step runbook TR/EN from the catalogue,
  e-mail deliveries with masked recipients; e-mail addresses are masked also inside texts and in
  every title and summary), `POST /alerts/{id}/state`, `POST /alerts/{id}/notes`
  (1–2000 characters), `GET /alerts/summary` (open per severity, acknowledged, the five newest,
  mean time to close over 30 days).
- **Dashboard API** `GET /api/v1/dashboard/summary` (`dashboard.view`, every box with its own
  permission, `null` without it): log intake (sources active/silent, messages per minute and a
  60-minute sparkline from `messages`), yesterday's seal and the 30-day seal calendar, Kamu SM
  credit with the days it lasts, clock offset, open alerts. The message count reads the
  primary key of the month's partition (no new index; a `perf` test keeps it under 1 s on
  300,000 messages in the hour).
- Runbooks for every rule in `arcilogger_worker.alerts.texts.RUNBOOKS`; web contract and
  captured samples `web/e2e/samples/alerts.json` and `dashboard.json`.
- Docs: UYARILAR / ALERTS section 8 (states, notes, who changes them, closing while a problem
  persists), ILK-ADIMLAR / FIRST-STEPS sections 7 and 9, MVP-CONTRACTS section 14, roles.md,
  DATA-MODEL.md, ADR-0010 amendment.

### Installer

- **Desktop Windows 11 Pro** (KURULUM.md / INSTALL.md section 17, ADR-0007 amendment
  2026-10-19): the setup `.exe` installs on a PC without a data disk (`C:\ARCILOGGER` with a
  warning when there is no fixed NTFS `D:`); removable, optical, network and RAM drives are
  refused and a USB disk is warned about; on client Windows `Set-ArciPower.ps1` switches sleep,
  hibernation, Fast Startup and network adapter power saving off and `-Uninstall` restores the
  recorded values (`-SkipPower`).
- **Collector address:** a new `arcilogger.toml` gets the server address as `[collector] bind`;
  the preflight stops, with the fix, when the bind is not an address of the computer.
- **Fewer manual steps:** `-ServerAddress` and `-ServerName` are remembered (`install\state.json`)
  or detected from the only adapter with a default gateway (a DHCP address is warned about); at
  the end of a first installation the setup token is shown on the console only (never in the log
  or the plan; the token line bypasses PowerShell's output streams, so a transcript or redirection
  started outside the installer does not record it either) and the Kamu SM credentials can be
  stored on stdin; over a kept database with an administrator the installer says so instead of
  asking to fix a failure (`arcilogger-api setup-token` now exits with 4, not 1, when an
  administrator exists); the setup `.exe` window waits for Enter; the install log points to `%TEMP%` until the data folder exists.
- **Operating system policy:** Windows Server 2022/2025 and Windows 11 Pro/Enterprise/Education
  (25H2 and later) are supported; older Windows 11 builds and ARM64 warn; Windows 10 and Home stop
  unless `-AllowUnsupportedWindows` (the message states the product policy, no Microsoft support
  date: those differ by edition, e.g. LTSC); client Windows gets a licence note.
- **Database:** new databases are created with `LOCALE 'C'`; the docs ask for the locale "C" in
  the EDB installer (Turkish locale rename, PostgreSQL BUG #18196).
- **Release build:** a `v*` tag builds `ARCILOGGER-Setup-<version>.exe`, the bundle zip,
  `SHA256SUMS.txt` and the SBOM on GitHub Actions (artifact, and a draft GitHub release); the admin
  PC can download instead of building (KURULUM.md section 4).
- **Smaller fixes:** the auditpol fallback message works on every edition; `Set-ArciVaultAcl.ps1`
  takes `data_dir` of the configuration; strict outbound mode keeps the SMTP rule of `[smtp]`
  and remembers `-SmtpServer`.
- **Folder swaps retry:** upgrade, rollback and the automatic swap-back after a failed upgrade
  retry renaming the program folders for up to 60 s while Windows still holds a file in them (a
  virus scanner right after the files were written); before, the swap-back could stop half-way
  with "Access to the path is denied".
- Manual acceptance test for Windows 11 Pro 25H2: [docs/KABUL-TESTI-WINDOWS11.md](docs/KABUL-TESTI-WINDOWS11.md).

### Devices and persons

- **Assignment history** (`device_assignments`, revision `0008_device_assignments`, ADR-0008):
  who held a device when; at most one current assignment per MAC and no overlapping intervals,
  enforced by the database (`btree_gist` exclusion constraint, a trigger where the extension
  cannot be created); history is appended to, never rewritten; existing links are backfilled
  (`backfilled`, start = first sighting); `devices.person_id` stays as the current owner.
- **5651 person step at T:** a lookup names the person assigned at the requested moment
  (`PersonInfo.link` `history`, `assigned_from`, `assigned_to` in the outcome and the lookup API);
  a device unassigned then names nobody; only devices without history fall back to the current
  owner (`person_link_at_lookup_time`). The official report prints the assignment interval and
  says the department is the person's current one. Stored outcomes and reports stay readable.
- **API** for the "Cihazlar ve Kişiler" page: `/devices` (search by MAC/host name/IP/name,
  "unknown devices", filters, sort, pages; detail with history and recent leases; edit; bulk
  assign/unassign), `/persons` (create, edit, deactivate, detail), `/departments`, `/vlans`
  (department link), `/imports/{persons|assignments}` (CSV preview and all-or-nothing apply with
  per-line Turkish/English errors, templates) and CSV exports (Turkish Excel, formula injection
  neutralised). Admin only, KVKK notice on person data, every change audited without names
  (MVP-CONTRACTS.md section 11, roles.md).
- **Rules in detail:** department changes are audited with ids and field names only (no
  department names); a persons export imports back as updates through its `id` column, also for persons
  without an employee number; a start up to two minutes ahead of the server clock is stored as
  now; an unassign refuses a note instead of dropping it (the dialog no longer offers one); an
  applied assignment import locks the persons it names (`FOR SHARE`, before the devices) and
  checks again that they are active.

### Usage analytics

- **Usage rollups** (`arcilogger-worker`, [ADR-0011](docs/adr/0011-usage-analytics.md),
  [docs/KULLANIM.md](docs/KULLANIM.md) / [docs/USAGE.md](docs/USAGE.md)): per person, device and
  bare address and site day — online time, **estimated** active time, time and raw time per site
  (registrable domain, FortiGuard service or address) with host names, categories, applications,
  download/upload, sessions, blocked attempts (web, DNS, application control), first/last activity,
  hourly rows, active-minute timeline, URLs the web filter logged, per-device breakdowns (each
  device's blocked attempts counted in full, `usage_device_daily.blocked`, so the devices add up to
  the person beyond `usage.blocked_per_day`); every row split into work hours and off hours. The
  subject is the person the device was assigned to **at that moment** (the 5651 attribution chain),
  else the device, else the address. Interim deltas and final remainders are spread over 1-minute
  buckets and never counted twice; destinations by web filter, application control/SSL, DNS answer
  (600 s), ISDB name, else address; registrable domains from the Public Suffix List bundled with
  `publicsuffixlist` (MPL-2.0, pinned, never downloaded; optional newer file `usage.psl_file`);
  background traffic per spec 10.3 (editable patterns, ports, application categories, < 2 KB
  sessions).
- Settings `usage.*` (thresholds, background rules, work hours, holidays — only the fixed-date
  national holidays are shipped, the religious holidays are entered every year —, re-attribution
  window, retention, URL and blocked limits, optional pseudonyms, report change thresholds), all
  validated. Tables of revision `0010_usage`: `usage_days`, `usage_daily`, `usage_device_daily`,
  `usage_domain_daily`, `usage_app_daily`, `usage_hourly`, `usage_blocked`, `usage_urls`,
  `domain_categories`, `usage_rebuilds`; `persons.usage_excluded`.
- Jobs `usage.today` (every 5 minutes), `usage.daily` (00:45, final), `usage.reattribute` (the
  last 7 days are rebuilt automatically when device assignments, leases, late logs or settings
  change), `usage.requests`, `usage.retention`; `arcilogger-worker usage rebuild --from --to`.
- **Rules in detail:** a day's inputs fingerprint is taken before its logs are read, so a log the
  ingest loads while the day is computed makes `usage.reattribute` rebuild it; what the first
  interim log read holds beyond its deltas is spread from the session start (a lost first interim
  log keeps its bytes); a session that started before the logs read for the day keeps its host
  name, kind and background rule after midnight; a device or address with only blocked attempts
  or URLs that day gets the VLAN (so the department) and address of those events, and so does a
  device row with only blocked attempts (a person's device breakdown, the report's Cihazlar sheet:
  every device with traffic or with blocked attempts). With engine version 2 the last
  `usage.auto_rebuild_days` days are rebuilt once.
- **API** `/api/v1/usage` (MVP-CONTRACTS.md section 12): company, subjects (persons, devices,
  departments), one subject (timeline, sites, categories, applications, host names, URLs, blocked
  attempts, hourly table, per-device breakdown, 30-day heat map), sites, categories, a department
  filter, CSV and Excel exports (openpyxl, MIT; text cells never formulas), rebuild requests and
  the pseudonym mapping. A whole list comes in one answer (`limit` up to 100,000: the period is
  aggregated once, not once per page). The users of a site or category carry only the figures kept
  per site (no online time of a category — its sites' raw times added up would count a minute once
  per site —, no blocked attempts, no first/last activity), aggregated in the database; the detail
  answers show the 200 with the most time with `users_total` (a category's 100 sites with
  `sites_total`) and the exports every one, with their own columns. Owner decision 2026-10-20: names
  for every role that may see usage; pseudonyms (HMAC-SHA-256, secret `usage.pseudonym_key`) only
  with `usage.pseudonymize_anonymous_role`. Viewing a subject, exports, rebuilds and the mapping are
  audited without names; person-level endpoints need the KVKK notice.
- **Report data** for the scheduled reports: `arcilogger_worker.usage.report_data.build_report_data`
  (schema 1, deterministic, with the detail lists of the Excel workbook) and
  `sample_report_data()`. In a part-day window (the end-of-day report, also when it runs late)
  the first/last activity are the active minutes inside the window, never the evening's
  activity or the window's end.
- Golden usage datasets (spec 17 M4) with every expected number computed by hand
  (`worker/tests/test_usage_golden.py`); API, report-data, job and performance tests; captured API
  answers `web/e2e/samples/usage.json`. Measured: 1.7 s for a full day of 100 devices (load
  generator), 6.0 s at five times the intensity.
- **The report viewer accepts the KVKK notice too:** it sees names since the owner decision, so
  `usage.anonymous` joined the permissions that require the notice — all four roles now accept it
  once per version, the viewer's sign-in asks for it and its person-level usage requests answer
  403 `privacy_notice_required` until then (the company totals do not). The permission's label
  says what it shows: "Kullanım raporları (adlarla; takma ad isteğe bağlı)".
- `from` without `to` now runs to today (it was one day), as the contract says; `to` alone stays
  one day. `POST /usage/rebuild` refuses a range that starts before the oldest log in the database
  (422 `usage_rebuild_no_logs`, naming the earliest day) instead of replacing those days' rollups
  with empty ones. The audit entries `usage.view` and one-subject `usage.export` name the real
  subject (`usage:person:12`) also for pseudonymous viewers, so a search by person finds them.
  `GET /audit` shows these targets to a reader who sees pseudonyms (the auditor while
  `usage.pseudonymize_anonymous_role` is on) as the subject's pseudonym token (`usage:p:…`, new
  field `target_masked`; `usage:masked` without the key) — its own views no longer tie a
  pseudonym to a MAC, an IP or a person id. The stored entries are unchanged. As an entry's hash
  covers the real target and would let a guess be tested, such a reader gets no hashes
  (`prev_hash`, `hash` and the `last_hash` of `/audit/verify` are `null`; the server still
  verifies the chain), and evidence packages with the audit log are hidden from it (404). The
  Denetim Kaydı drawer says why it cannot recompute the hash. The docs say that pseudonyms stay
  weak for the auditor (`search.read`, `lookup.run`, the hash in the day's seal manifest).
- Legal drafts (`docs/legal/`): the information text lists the usage data, the scheduled reports
  that leave the server by e-mail and who sees names; the retention draft lists the usage rollups
  and their nightly deletion after `usage.retention_days`.
- Legal drafts and roles.md corrected: the retention draft's measures (section 5) no longer say
  that nothing leaves the server — e-mail is the exception (named usage reports and alert e-mails
  through the configured SMTP server), as in the information text, and counsel is asked about the
  mail provider; on the devices page only administrators see the person registry (e-mail, staff
  number, title, notes), the device list and the assignment history, while `audit.read` (admin,
  auditor) sees every assignment change made there in the audit log (`device.assign`: MAC,
  previous and new person number, start, who made it), every usage role sees each person's
  devices of the period (name, MAC, IP) on the usage page, report recipients get the devices with
  their persons and a 5651 result shows the assignment of that moment. ASSUMPTIONS U13 states the
  users of a site or category as answered: a category's online time is 0, no blocked attempts or
  first/last activity, 200 in the detail, all in the export.

### Web: devices and persons

- **Cihazlar ve Kişiler** page (`/devices`; `devices.read`, changes `devices.manage`) on the
  devices and persons API: tabs Cihazlar, Kişiler, Departmanlar ve VLAN'lar, İçe/Dışa aktar
  (`?tab=`, keyboard accessible). The device table (MAC with the randomised flag, name with host
  names and vendor, last IP/VLAN, type, owner, first/last seen) is searched, filtered, sorted and
  paged by the server; the filters, the "Bilinmeyen cihazlar" quick filter and the sort order are
  kept in the address, the search text never is. **CSV indir** on the device and person tabs
  exports every page of the filtered list (the API's export filters).
- Row selection with **Kişiye ata** / **Atamayı kaldır**: a person picker that searches active
  persons on the server, an optional start of validity (site time; refused in the browser when it
  is in the future or before a selected device's current assignment) and a note (≤ 500
  characters). The API's refusals (`assignment_overlap`, `assignment_in_future`,
  `person_inactive`, `device_not_found`) are shown in the dialog with what to change; devices the
  person already held are reported as unchanged. The device drawer edits name, type,
  infrastructure flag, notes and tags (the API's limits checked first) and shows the append-only
  assignment history and the newest leases; the department falls back to the VLAN's and says when
  the VLAN has none; randomised MAC addresses carry a warning that one phone may appear as
  several devices (spec 9.2).
- Persons: create, edit, deactivate (no delete; an inactive person cannot be given devices, the
  history is kept), search by name, e-mail, employee number or job title, a drawer with the
  person's devices and assignment history. Departments: create, rename; the VLAN → department
  mapping is changed in place.
- CSV import of persons or assignments: template, dry run with counts, refused lines in the
  interface language (the server's cut-off after 200 shown as "200+") and a preview of the API's
  preview rows (actions, changed fields, previous owner, start), then apply after a confirmation.
  An apply the data no longer allows (all or nothing) returns to the preview with the new errors.
- Person-level lists wait for the accepted KVKK notice instead of asking the API; without
  `devices.manage` the page is read-only.
- **5651 Sorgu**: the person step shows the assignment behind it ("Atama: 01.10.2026 09:00:00
  itibarıyla", or both ends); lookups stored before the assignment history show nothing new.
- `DataTable` supports server-side sorting and a custom row counter; the API client sends a
  `FormData` body as multipart (CSV upload).
- Tests: the generated contract and the captured answers of the real API
  (`e2e/samples/devices.json`) drive a stateful devices mock (`e2e/mock-devices.ts`, the API's
  rules and message tables, strict request bodies); unit tests for the filters, the edit and
  assignment checks, the import state machine, the filtered export and the permission gating;
  E2E specs and axe scans in light and dark mode.

### Web: dashboard, security, reports

- **Panel** (`/`; `dashboard.view` or `dashboard.usage`) on `GET /dashboard/summary` and today's
  `GET /usage/company` (spec 12.3 item 2): Log alımı (active sources, messages per minute over the
  last hour, silent sources), Dünkü mühür (state, time, serial, verification), Kontör (left, how
  long it lasts, below-threshold badge), Saat sapması (offset, NTP server; "Bilinmiyor" when the
  newest sample is older than 3 hours, `ok: null`); Bugünkü kullanım (active users, active time,
  downloaded and uploaded with an hourly stacked area and its table view), Açık güvenlik
  uyarıları (open counts per severity, the newest five, each a link to the alert), En çok vakit
  geçirilen siteler, Kategoriler (donut: the six largest with the API's `share`, "Diğer" the rest
  of the day's active time — also the categories beyond the ten the API sends —, the active time
  in the middle) and the last 30 days of sealing. A block the API leaves out (`null`) or the user
  may not see is not shown — no error; the HR role sees only the usage blocks and never asks the
  summary. Each block links to its page when the user may open it. Live blocks refresh every
  30 s, usage every 5 minutes, as background requests (`X-Arci-Background`), and not while the
  tab is hidden. Usage figures carry the "tahmini" method note.
- **Güvenlik** (`/security`; `alerts.read`, changes `alerts.manage`): "Güvenlik Özeti" (open
  alerts per severity, open and being investigated, mean time to close over 30 days) and the alert
  list (state, severity chips, rule, time range and text search; server-side sorting and paging;
  the filters and the open alert are in the address, the search text never is). The detail drawer
  shows the summary, the detector's details, the runbook in the interface language, the history
  (state changes and notes, `system` for automatic ones) and the e-mails with masked recipients.
  Spec 11's state names (Yeni, İnceleniyor, Kapandı, Yanlış alarm); only the changes the API
  allows from the current state are offered, with an optional note (its own history entry), and a
  hint next to closing that a persisting problem opens a new alert. `alert_state_unchanged` /
  `alert_transition_not_allowed` are explained and the alert reloaded; a re-open refused with
  `alert_key_active` links to the active alert; `system` events show their reason code as text
  (the raw code when unknown). Sorting on time, severity, state and rule. No rule editor yet (a
  later milestone; the page says so).
- **Raporlar** (`/reports`; `reports.manage`): the scheduled reports with kind and time,
  recipients, formats, a person-level badge, an on/off switch (refused without recipients), next
  run and last run state; a create/edit dialog with the kind's own fields (time, weekday, month
  day), an e-mail chip input (typed, pasted lists, duplicates and invalid addresses explained),
  format choices, person-level detail on and pseudonyms off by default (owner decision
  2026-10-20) and a live preview of the next run and the period it covers; delete with a
  confirmation (runs and files are kept); "Şimdi çalıştır" over the kind's own period as the API
  computes it, or a chosen one of at most 92 days (a `custom` run), with or without e-mail;
  "Önizle" (the PDF in a new tab, the e-mail HTML in a sandboxed frame); the run history (queued
  time, state — a run that e-mailed nobody is "Tamamlandı (e-posta yok)" —, the deliveries tried
  so far, a failed one marked for retry while the run is running) and the files (downloaded and
  checked against their SHA-256; 410 `report_expired`, 409 `report_missing` / `report_damaged` in
  the API's words). `/reports?run=<id>` opens that run (the report e-mail links there). E-mail
  switched off on the server (409 `report_smtp_disabled`) points to the SMTP setup;
  `report_recipients_required` and a missing usage engine (503 `usage_engine_unavailable`, a run
  failed for it) are said in words.
- `usePollingQuery` also keeps refreshing a failed answer in the background (before, a widget
  whose endpoint kept failing sent its retries as user activity and kept the session alive). The
  seal calendar's day colours are shared with the dashboard's 30-day strip; a small `Switch`
  (role `switch`) joins the UI kit primitives.
- Types for the alert, dashboard, report and company-usage schemas of the generated contract
  (`api-types.ts`, a `Float` alias for fractional numbers checked by the sample test); strict
  request bodies in the E2E mock (`AlertStateRequest`, `AlertNoteRequest`, `ScheduleCreate`,
  `ScheduleUpdate`, `RunRequest`); the captured samples (`e2e/samples/alerts.json`,
  `dashboard.json`, `reports.json`, `usage.json` → `company`) drive stateful mocks
  (`e2e/mock-alerts.ts`, `e2e/mock-reports.ts`) that follow the API's workflow and refusals.
- Tests: block visibility by permission, the 30 s / 5 min polling (background, paused while
  hidden), the alert workflow and the page address, the schedule form checks with the next-run
  text, periods and the recipients chip input; E2E specs (dashboard for administrator, auditor,
  viewer and HR; acknowledge → close with a note, a change made meanwhile, a refused re-open
  linking to the active alert, a check's reason, notes, read-only; create a schedule, run it now,
  see the run, `?run=`, an expired file, e-mail switched off, no usage engine, preview, edit,
  switch off, delete; forbidden without `reports.manage`) and axe scans of the three pages in
  light and dark mode.

### Web: usage

- **Kullanım** page (`/usage`; `usage.person` or `usage.anonymous`) on the usage API of group
  contract 2 section 2: tabs Şirket, Departman, Kişi, Cihaz, Site, Kategori; the period (Bugün,
  Dün, Bu hafta, Geçen hafta, Bu ay, Geçen ay or a custom range of site days, Europe/Istanbul,
  weeks from Monday; previous/next period), the hours filter (Tüm saatler / Mesai içi / Mesai
  dışı) and the selected department, person, device, site or category are kept in the address
  (a drill-down is a history entry); a table's search text never is.
- Şirket: KPI tiles (active users, online and active time, off hours, downloaded, uploaded,
  sessions, blocked), hourly download/upload (stacked area) and active users (line), top sites,
  categories (donut; "Diğer" and the middle from the period's active time, not from the ten
  categories sent), applications, departments ("Bilinmiyor" for subjects without one).
  Departman: a department's persons, its devices assigned to nobody and bare IP addresses (the
  VLAN's department) with every metric, its sites and categories (`department=` on the API).
  Kişi: every person; Cihaz: devices assigned to nobody and IP addresses without a MAC (an
  assigned device's traffic is its person's); both searched like the API's `q` (name, MAC, IP,
  host name; a pseudonym only) and sorted by any column. The subject view: header card (MAC, IP
  and host name of a device), the day timeline (active spans coloured by category, hover with
  time and top domain), per-day table with totals for a period, hourly charts and table, the
  30-day hourly heat map (fixed 60-minute scale), sites with background and service/IP badges
  (drawer with host names and URLs), the searchable URL list (blocked ones marked), categories,
  applications, every blocked attempt (domain, host name and path, application), the person's
  devices (the header's device names open this breakdown). Site: host names with sessions and
  who used it for how long (their figures on that site: time, off hours, volumes, sessions);
  Kategori: its sites and users; a capped list says how many there are and that the export holds
  them all.
- Every duration carries the "tahmini" info icon with the method note (`usage.method_note`);
  "Veriler 14:35'e kadar işlendi" or "Veriler 12.10.2026 sonuna kadar işlendi" from
  `computed_until`; empty, loading and error states (a 404 for a period without data is an
  empty state with the API's sentence). Pseudonyms (only with
  `usage.pseudonymize_anonymous_role`) carry an "anonim" badge; names are shown by default
  (owner decision 2026-10-20); a missing pseudonym key is reported with the API's instruction
  (409 `pseudonym_key_missing`). Every tab but Şirket waits for the KVKK notice.
- Export (`/usage/export.csv|xlsx?view=…`, audited by the API): the CSV holds a view's main
  table, the Excel file every section on its own sheet and the sheet "Yöntem"; a table that is
  not its view's main table offers only the Excel file, and Şirket exports `view=company` (no
  notice needed). "Yeniden hesapla" for `settings.manage` (`POST /usage/rebuild`, the job polled
  with its requester and time, every usage view refreshed when done).
- Names go back to the API as it sent them: a site keeps its spelling in the address and the
  requests (a FortiGuard service such as `Microsoft-Azure` is matched exactly), the unknown
  category is `Bilinmiyor` in every UI language (only the text shown is translated: "Unknown" in
  English in the site and category tables, the Sites filter, the category view, Şirket's
  categories, the charts and the Panel's Kategoriler card), and a department's persons and devices are the API's scoped lists
  without a second filter in the browser (the department "Bilinmiyor" lists its subjects, whose
  own department is empty).
- Lists are read in full in one request (`limit=100000`); views whose period includes today refresh
  every 5 minutes in the background (`X-Arci-Background`). New shared pieces: a line chart,
  `DataTable` `actions`, `KpiCard` with its own estimate text, timeline segments with a detail.
- Tests: the 22 remaining usage interfaces complete `api-types.ts` (the type test covers every
  schema again) and `api-samples.test.ts` types every captured usage answer; unit tests for the
  address state, the period presets, the Turkish dative after a time, the API's search folding,
  the per-day table, the timeline layout and the heat-map scale. The usage mock
  (`e2e/mock-usage.ts`) serves the answers captured from the real API for 12.10.2026
  (`e2e/samples/usage.json`) and derives what the capture lacks from its subjects' details
  (checked to reproduce every captured list, detail and CSV file); other periods come from a
  generated dataset of the same company in the real shapes. E2E specs for the drill-down,
  period and hours, exports, rebuild, pseudonyms (captured tokens, a real key refused, the
  missing key), the KVKK wait and the forbidden page; axe scans of every tab and detail in light
  and dark mode.

### Added — MVP, version 0.1.0 (M2/M3 essentials, authentication and audit, Windows installer)

ARCILOGGER is *designed to support* Law 5651 obligations ("5651 yükümlülüklerini desteklemek için
tasarlanmıştır"); nothing below is a claim of legal compliance.

- **Database package** `arcilogger-db`: SQLAlchemy 2 models, Alembic migrations run
  programmatically (`upgrade`, `current`, `check`, `partitions`; revisions `0001_baseline`,
  `0006_lookup_reports`, `0007_evidence_seals`), PostgreSQL 16 with **optional TimescaleDB** and
  native monthly partitions otherwise, seed data (VLANs, departments, default settings),
  canonical-JSON settings, bulk `COPY` loading behind one module (ADR-0005, DATA-MODEL.md).
- **Parsers and ingest**: FortiGate (traffic incl. interim logs, web filter, DNS, application,
  IPS/antivirus, events) and MikroTik (DHCP, ARP-LOG, firewall, account, interface, config)
  parsers with real fixtures; `arcilogger-ingest` tails the raw spool (or the sealed vault copy)
  into PostgreSQL with exactly-once offsets, 2 parse workers by default (INGEST.md, PARSERS.md).
- **Correlation and 5651 lookup** (`arcilogger-worker`): lease timeline from DHCP and ARP-LOG,
  devices and IP conflicts, late-evidence rebuilds, the lookup engine (exact, double-NAT
  candidates, internal) and the worker job loop with `rebuild` (CORRELATION.md).
- **API** (`arcilogger-api`): sign-in with Argon2id passwords, mandatory TOTP for administrators
  and auditors, lockout and rate limits, CSRF, re-authentication for sensitive actions, KVKK
  notice enforced on person-level endpoints, users and roles, hash-chained audit log sealed
  daily, first administrator via `setup-token` / recovery via `create-admin`; 5651 lookup with
  the official PDF report (rendered once, stored, optionally time-stamped), raw-line view,
  evidence packages built in the background with limits, sealing calendar and day detail with
  on-demand verification, Kamu SM credit history (SECURITY.md, roles.md, RAPOR.md, KANIT-PAKETI.md).
- **Evidence package and standalone verifier**: self-verifying ZIP with sealed days, extract,
  roots, the lookup's report and `verify.py` (Python standard library only), plus the OpenSSL
  method without `verify.py` (ADR-0006, DENETCI-KILAVUZU.md).
- **Web interface**: sign-in and first-run setup, Kullanıcılar ve Roller, Denetim Kaydı, 5651
  Sorgu, Kanıt Paketleri, Mühürleme, Cihazlar ve Kişiler. Panel, Kaynaklar, Canlı Akış, Arama,
  Kullanım, Güvenlik, Raporlar, Uyum Durumu and Sistem are placeholders naming their milestone.
- **Windows installer**: offline bundle with SHA-256-checked components and wheels, WinSW services
  under virtual accounts, least-privilege NTFS rights, Caddy with an internal CA, firewall,
  scheduled tasks, database role, upgrade with rollback, uninstall that never deletes evidence
  (ADR-0007, KURULUM.md / INSTALL.md).
- **Load generator** (`tools/loadgen`) and the end-to-end tests (`tests/test_mvp_end_to_end.py`).
- **Docs**: first steps after the installation (ILK-ADIMLAR.md / FIRST-STEPS.md), draft employee
  information text and draft retention/deletion policy for legal review (`docs/legal/`), auditor
  guide for web-created packages and the report checks. The offline bundle carries the whole
  `docs` folder (ADRs and legal drafts included), and the installer's next steps put the Kamu SM
  roots before the connection test and point to the first-steps guide.

### Known limitations of the MVP

- No department history: a 5651 result names the person assigned at the moment of the event,
  with that person's current department; past assignments cannot be corrected in the interface
  (ADR-0008).
- No settings page: database settings are changed with `arcilogger-db settings` until M6.
- Usage analytics are estimates from connection logs (docs/USAGE.md section 3: QUIC/HTTP3 without
  web logs, DNS over HTTPS, devices not passing the FortiGate); no rule engine, rule editor or
  webhooks (M5; the R15/R20 checks, the alert e-mail, the alert workflow and the Güvenlik page
  exist, docs/ALERTS.md), no compliance and system pages (M6), no "Seal now" from the web.
- Database dumps stay on the server's data disk; copying them off the server (external disk, a NAS)
  is manual (docs/YEDEKLEME.md section 4).
- Alerts have no assignee yet (spec 11); only administrators see and change them.
- No deletion job for the database's log tables, the stored 5651 reports or the lookups yet (the
  usage rollups are deleted after `usage.retention_days` by the worker job `usage.retention`,
  docs/DATA-MODEL.md "Retention"); vault retention is off by default
  (docs/legal/saklama-imha-politikasi-taslak.md).
- Verify on site: MikroTik wire format, FortiGate interim-log behaviour, Kamu SM `.zd` format and
  root URLs (kamusm.md K1–K10), TimescaleDB on Windows, the WORM share of the replica target
  (MVP-CONTRACTS.md §8, KURULUM.md §16).
- Decisions: [ADR-0001 … ADR-0007](docs/adr/README.md) and the owner decisions in
  [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

### Added — M1 (collection and sealing)

- Syslog collector (`arcilogger-collector run`): UDP and TCP 514 (RFC 6587 octet counting and LF
  framing), sender allow-list incl. networks for DHCP access points, unknown senders written to
  `unknown-<ip>` with a one-time alert (or dropped), reversible raw-line format, receive-day files
  switched at Istanbul midnight, fsync every second, status file.
- Daily sealing (`arcilogger-core seal`): deterministic gzip, SHA-256 of compressed and raw bytes,
  line counts, gaps per expected source, audit log, clock samples, RFC 8785 canonical manifest
  chained to the previous day, time stamp, independent verification, read-only lock (NTFS deny ACL
  on Windows), spool cleanup; strict day order, retry schedule, R20 alerts, late-stamp marking,
  crash recovery without a second stamp.
- Time-stamp providers: Kamu SM Zamane console client, generic RFC 3161 (development), mock TSA
  with its own test CA and failure injection; independent RFC 3161/CMS verifier (cross-checked with
  `openssl ts -verify`).
- Chain verification (`verify --day/--last/--all [--deep]`), replica to a second location without
  overwriting, retention with a sealed deletion record and chain anchor (off by default, minimum
  730 days), SNTP clock probe, Kamu SM credit check, encrypted secret store, `tsa-test`, `status`.
- Windows Task Scheduler definitions for all evidence jobs (LOCAL SERVICE, run-after-missed-start),
  `Register-ArciTasks.ps1`, `Set-ArciTime.ps1`, example configuration (since 2026-10-20 a fictional
  example network).
- Docs: `kamusm.md` (facts, open questions, connection test, test-user e-mail),
  `DEVICE-SETUP.md` (FortiGate/MikroTik commands, network flows).

### Added — M0 (repository skeleton)

- Decision: ARCILOGGER runs on a Windows server, users connect only through a browser (ADR-0001);
  own syslog collector (ADR-0002); integrity on Windows (ADR-0003); web tooling (ADR-0004).
- uv workspace with five Python packages (`core`, `collector`, `ingest`, `api`, `worker`), ruff,
  mypy `--strict`, pytest + hypothesis, `poe` task runner.
- `arcilogger-core` CLI (`version`, `info`), time rules (UTC inside, Europe/Istanbul for people,
  receive-day partitions) and Turkish/English display formatting.
- API health endpoint `/api/v1/health`.
- Web interface skeleton: design tokens (light/dark), Inter and JetBrains Mono bundled, Turkish and
  English, application shell, 20+ components and the `/ui-kit` page.
- Real FortiGate and MikroTik log samples as pinned fixtures; shared display-format vectors tested by
  both Python and TypeScript.
- CI on GitHub Actions: Python on Windows and Linux, web lint/types/tests/build, Playwright E2E with
  axe accessibility checks in both themes, gitleaks, SBOM (syft) and vulnerability scan (grype).
