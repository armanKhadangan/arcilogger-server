# Wave 3 contract: FortiGate-only lease evidence, srcmac, and per-exit NAT (dual WAN)

> **In the repository (2026-10-28).** This is the binding design contract of Wave 3, copied from the
> author's working copy after seven review rounds. "SPEC" means the product's build specification,
> which the owner holds and which is not in the repository; the contract quotes every rule of it that
> it uses. Paths under `scratchpad/` are the author's working files (saved copies of the public
> Fortinet documentation pages named in §1.1, probe scripts) and are not in the repository; fetch the
> public pages again when you need them. The branch the contract calls "wave-3" is the shared
> integration branch on GitHub, and "main" is the release branch. Done before the team took over:
> day 0, WP-A0, WP-C1 and the first part of WP-E; WP-A and WP-B are being finished by the manager's
> agents. How the team works with this contract: `CLAUDE.md` and `CONTRIBUTING.md` at the repository
> root, and the GitHub issue of each work package.


- **Status:** final design for Wave 3 (roadmap 1.6 and the dual-WAN half of 2.6; SPEC 2.1, 2.3,
  9.1–9.5). The repository was only read; nothing in it was changed. The IPv6 half of 2.6 was
  shipped in Wave 0 (API 422 `ipv6_not_supported`, CORRELATION §7) and is kept as it is.
- **Base:** four research reports (lease timeline, NAT engine, documentation, Fortinet public
  documentation) and two scratch probes against PostgreSQL 16 at HEAD `9a9c6a2`
  (`w3lease/probe_tests/test_fg_only_probe.py`, `natprobe/test_dual_wan_probe.py`). Where the
  reports disagree or are wrong, section 0.3 says what was corrected.
- **Checked against the code on 2026-10-27** (section 1.2 lists the facts and where they are).
- **Revised after review (2026-10-27), four times.** A 45-finding review was checked against the
  code and the saved sources; every finding that held was folded in where it belongs (new
  decisions D24–D32, new settings, alert causes, texts, tests and package changes). A second
  45-finding round was checked the same way and folded in (D33, the reworked log check and
  fallback of D15, the usable-evidence alerts of D30, the per-exit change record of D32, the
  A0/day-0/C1 gate fixes of section 4 and the alert catalogue of 2.6.3). A third, 39-finding
  round was checked the same way. Its guiding rule: a wrong person at high confidence is the
  worst outcome and a false "nobody" the second worst; where the logs cannot decide a case,
  the confidence is lowered and the reason stated, and the simpler of two equally safe designs
  is taken. It changed the FortiGate lease end to the FortiGate's own end (D6), lowered chains
  past half a lease without a renewal to `low`, made cross-table ACKs supersede (D5), added a
  source-clock check (D28), folded the per-reason refusal alerts into one (D30), simplified D8,
  D27 and D32, fixed the resolution order and the interface rule of 2.8.3, the import and
  error paths of section 4, and several PDF and alert texts. A fourth, 37-finding round was
  checked the same way under the same rule. It made a lease's last DHCP evidence its own (never
  the line that ended it), kept traffic out of a FortiGate seed's deadline, counted only private
  or shared NAT addresses as double-NAT evidence and as unclaimed candidates, dropped an exit's
  interface condition only on positive evidence, added the exit mode `internal`, a per-lookup
  clock check and per-table `none` causes, reworked the D8 probe and the source-clock alert,
  let a FortiGate ACK end the MAC's MikroTik lease on another IP, removed the re-conversion
  after a rollback and the `fortigate-renewal-gaps` cause, and fixed several PDF, card and
  alert texts. A fifth round counted only blockers (a wrong person at medium or high confidence
  with no warning, a false "nobody" with no warning, evidence altered, data shown to the wrong
  role, or a contradiction that makes the contract unimplementable); it confirmed four, each
  fixed with the simplest safe change (a lease's last DHCP evidence is measured at the requested
  moment, every `isp_nat` exit without a public address leaves the D15 own set, an empty search
  measures its sources' clocks, unconverted FortiGate DHCP lines after a lease's last record lower
  its chain), and recorded its other findings as "Implementation notes — round 5", which every
  implementation agent reads. A sixth round, under the same blocker-only rule, confirmed five
  and fixed each the same way: D33 is keyed on the address's configured network instead of its
  VLAN number (a FortiGate chain outside every configured network is `low`), the post-lease check
  of unconverted FortiGate DHCP lines covers every DHCP chain by its IP or its MAC, the D15 own
  set also leaves out the addresses of exits on other declared interfaces, an empty search on P
  falls back to candidates when the probe saw both P and double-NAT evidence, and `-Rollback`
  lowers every FortiGate lease to `low` for the previous version (restored at the re-upgrade);
  its other findings are "Implementation notes — round 6". A seventh round reviewed only the
  round-5 and round-6 changes under the same rule, confirmed one blocker and fixed it the same
  way: the post-lease check also reads converted FortiGate ACKs of the chain's MAC for another
  address, so a lease built before its network was entered (D33 could not close it) is `low`,
  and the texts that ask for a network also ask for a lease rebuild; its other findings are
  "Implementation notes — round 7". The "Review notes" at the end list, per round, what was not
  taken over, or taken over in another form, and why.
- **Decisions** were taken by the developer on the owner's delegation. The defaults below are
  what gets implemented. The owner may change any of them; section 0.2 lists the ones that are
  real product choices, each with its alternative.
- **Language:** the contract is in English. Every user-facing text is given in Turkish first,
  with English next to it. Nothing here claims legal compliance; the only wording used is
  "5651 yükümlülüklerini desteklemek için tasarlanmıştır" / "designed to support 5651
  obligations".
- **Example world only:** Örnek A.Ş., `172.20.x` (`db/testing.py` `EXAMPLE_VLANS`: 10, 20, 41
  Finans, 42 Planlama, 43 İdari İşler, 60 Wi-Fi Personel, 61 Wi-Fi Misafir), MACs
  `00:00:5E:00:53:xx` (and `02:00:5e:00:53:xx` for randomised ones), public addresses from RFC
  5737 (`203.0.113.x`, `198.51.100.x`), domains under `example.com`. The repository's
  convention for the FortiGate's private WAN address behind an ISP router stays as it is:
  `100.64.12.9` (RFC 6598 shared space). The second line in this contract uses `100.64.13.9`.
  FortiGate interface names in examples (`vlan42`, `wan1`, `wan2`, `internal`) are fictional.
  Operator documents (`DEVICE-SETUP`, `ILK-ADIMLAR`/`FIRST-STEPS`, `AYARLAR`/`SETTINGS`,
  `LISANS`/`LICENSING`, `DENETCI-KILAVUZU`) use placeholders only. No document may name a device
  model or a firmware release more precise than "FortiOS x.y" (`tests/test_docs_consistency.py`
  `MODEL_OR_RELEASE`, which scans `README.md`, `CHANGELOG.md` and every `docs/**/*.md`,
  `docs/adr/` included). The Fortinet URLs and exact release numbers of section 1.1 stay in this
  contract only. ADR-0016 cites its sources as "FortiOS 7.6 log reference" / "FortiOS 7.6 CLI
  reference" and the community articles by title and number (major.minor only, never a third
  number), and the docs test gains an assertion on ADR-0016 (3.9).
- **"Sahada doğrula" (verify on site).** Every FortiGate format detail that is not confirmed by
  an official source or a real sample is either configurable (with validation) or protected by
  a runtime check that raises an alert. A test pins each assumption so it fails loudly when the
  assumption changes. Section 1.3 lists every such detail and where its loud check lives.

---

## 0. Decisions and resolution log

### 0.1 The decisions

**Part A — FortiGate DHCP as lease evidence**

**D1 — Scope.** FortiGate-only networks are supported when the FortiGate is the clients' DHCP
server in server mode. Its DHCP ACK/RELEASE event records feed the same lease timeline the 5651
lookup uses, so the chain goes all the way: internal IP → MAC → device → person. Devices with
static addresses have no lease evidence on such a network: their chain ends at the internal IP,
and the lookup says so. The only exception is the optional srcmac path of D10. Only rows of
configured FortiGate sources count (D24), and the format stays marked unverified until the site
check is done (D29).
*Alternative:* write "not supported" into the offer (roadmap open decision). *Why:* many small
sites have only a FortiGate. On those sites every lookup stops at `no_lease` today, and the
FortiOS reference documents the needed fields (section 1.1).

**D2 — Storage: a new FortiGate-only time-series table `fg_dhcp`.**
*Alternatives:*
- writing the rows into `mt_dhcp`, which breaks `test_parser_contract.py:123` and
  `consistency.PARSER_OF_TABLE` ("only the MikroTik parser writes `mt_*` rows"); and
  `mt_dhcp.server` is a MikroTik server name;
- a vendor-neutral `dhcp_events` with a `vendor` column, which breaks the one-parser-per-table
  rule that the report cross-check relies on;
- leaving the data in `fg_event.extra`, where it is untyped and unindexed, the MACs are not
  normalised, and the cross-check compares columns, so every ip/mac claim would mismatch.

*Why:* one table per parser keeps every existing contract. Vendor neutrality lives in the worker
instead (D5).

**D3 — Routing at ingest is decided by the shape of the line, using constants only.** A FortiGate
line becomes an `fg_dhcp` row instead of an `fg_event` row when all of these hold:
- `type="event"`;
- the keys `ip`, `mac` and `dhcp_msg` are all present;
- `ip` converts to an address and `mac` to a MAC.

When the three keys are present but the values do not convert, the row stays an `fg_event` row
and the line is the parse failure `fortigate.dhcp`; it is never silently an ordinary event.
*Alternative:* configurable logid lists in the parser (Fortinet report). *Why:*
- ingest has no settings plumbing: `prepare.py:147` uses the shipped parsers, and
  `build_parsers()` only has a contract test;
- the report cross-check re-parses each cited raw line with the shipped parser, so a parser that
  depends on settings would make old rows disagree with their own raw lines after a settings
  change;
- of the documented 26xxx messages, only 26001 and 26002 carry `ip`, `mac` and `dhcp_msg`
  together (section 1.1, F4).

**D4 — The meaning of each row is decided in the worker, per run, from settings.** The setting
`dhcp.fortigate_logids` maps a logid to `assigned` or `deassigned`. Its default is
`{"0100026001": "assigned", "0100026002": "deassigned"}`; the ACK logid is confirmed and the
RELEASE logid is derived (F5, F6). An `fg_dhcp` row whose logid is not in the map is counted in
the lease job and never used. The alert `R20-lease:fortigate-refused` (reason "unmapped", with
the logids seen) makes that loud.
Changing the map and rebuilding the leases reinterprets old rows without re-ingesting anything.
The logid decides the meaning; `dhcp_msg` is a second, configurable check that can only refuse a
row (D25).
*Alternative:* interpret `dhcp_msg` or `msg` texts alone. *Why:* those texts are not documented
(`dhcp_msg` is a generic "DHCP Message" field that 26008 DDNS and 26011 DHCPv6 carry too, and
RELEASE has no sample at all); the logid layout is documented.

**D5 — Same timeline rules, one registry of DHCP sources.**
- An ACK opens an interval with confidence `high`, like a MikroTik `assigned`, with
  `source_table = 'fg_dhcp'`.
- A repeated ACK of the same pair **from the same table** is a continuation; FortiOS likely logs
  every ACK, renewals included (F7). An ACK of the pair from the **other** DHCP table is not: it
  closes the open interval `superseded` (counted `superseded_across_sources`) and opens a new
  interval of its own table, exactly as an `assigned` already ends an ARP interval of its pair.
  Scenario: after a move from MikroTik to FortiGate DHCP a client usually keeps its address; if
  the FortiGate's ACK continued the `mt_dhcp` interval, that interval would keep the MikroTik
  lease time and stay open through traffic for good, so a printer set statically to the address
  later would be named as the former client at `high`, with no D26 or D33 check applying.
- A RELEASE closes the interval with `dhcp`.
- Traffic extends a MikroTik interval as today (SPEC 9.1: RouterOS logs no renewals). A FortiGate
  interval is **not** extended by traffic unless `dhcp.fortigate_traffic_extends` is on (D26).
- Superseding and ARP-after-release behave as today; a supersede across DHCP sources is counted,
  and two DHCP origins in a subnet lower the chain (D27). Conflicts behave as today, plus the
  srcmac conflict of D10.
- An ACK of a MAC for another IP closes that MAC's other open FortiGate interval of the same
  origin, interface and configured network, and its open MikroTik interval on another IP of the
  same network (D33).

`DHCP_TABLES = ("mt_dhcp", "fg_dhcp")` (the order is also the tie-break rank) and `TABLE_RANK`
are defined once, in `worker/lookup/refs.py` (day 0), next to `DHCP_TABLE`; the new
`worker/correlation/dhcp_sources.py` imports them, so `timeline.py` (which needs `TABLE_RANK`
for `evidence_key`) and `dhcp_sources.py` (which returns `timeline.DhcpEvent`) never import each
other. They replace every hard-coded `DHCP_TABLE == 'mt_dhcp'` in the correlation and lookup
code; section 2.6 lists them with file:line.
*Alternative:* copy the MikroTik paths for FortiGate. *Why:* the seed, look-ahead and catch-up
code is subtle (`leases.py:566-790`). One union loader keeps live, range and single-day rebuilds
equal for both sources. Without it, `_Open.from_interval` silently drops `fg_dhcp` refs from
seeds (`timeline.py:238`).

**D6 — The logged lease time drives the timeout of its interval.**
- A new nullable column `leases.lease_seconds` holds it.
- An open FortiGate interval with a logged lease times out **`lease_seconds` after its last DHCP
  evidence**: the end the FortiGate itself gives the lease, after which it may hand the address
  to someone else. No `timeout_percent` margin is added: the ACK time and the lease are both on
  the FortiGate's clock (watched by `R20-lease:source-clock`, D28), and a margin would only name
  the former client for up to 16.8 h after the FortiGate ended the lease (7-day default) and
  may have given the address to someone else, whenever the next holder's ACK was lost (UDP
  loss, a collector stop, a refused row). Its only gain, bridging one lost renewal line,
  costs a gap of seconds without it. A configured or fallback time (no usable logged lease)
  keeps today's rule, `× lease.timeout_percent / 100`, because it is only an estimate of the
  server's lease.
- The value is used only when it lies in 300–8,640,000 s, the FortiOS `lease-time` range (F8).
  A value of `0` (unlimited), a missing value or a value out of range falls back to
  `lease.server_seconds[<interface>]` or `lease.default_seconds`, and the job counts it
  (`lease_fallback`, and `lease_unlimited` for `0`).
- `0` is not silent: `R20-lease:fortigate-lease-unlimited` (high, D9) names it with its own text
  ("süresiz kira"). A client with an unlimited lease never renews, so with traffic not extending
  FortiGate intervals (D26) its chain ends after the fallback time; the alert tells the
  administrator to set a finite `lease-time` on the FortiGate. Out-of-range values stay
  `R20-lease:fortigate-lease` (low).
- The setting `dhcp.fortigate_use_logged_lease` (default `true`) switches this off.
- The lookup and the PDF print the lease time and the time of the last DHCP evidence: the
  lease's **own** last DHCP record **at or before the requested moment** (its opening ACK, a
  renewal or its own RELEASE), never the line that ended a `superseded` lease, which is another
  device's ACK or this MAC's ACK for another address, and never a renewal logged after the
  moment (2.7; round 5: a client that comes back inside its lease renews the same interval, and
  measured against that later ACK a missing renewal before the moment would never show). When
  the requested moment lies more than half the lease after that record of an `fg_dhcp`
  lease, the chain gets confidence **`low`** with `lease_renewal_not_seen` (2.7, 2.11): a client
  that still holds the address renews at half the lease (RFC 2131 T1), and FortiOS likely logs
  that ACK (F7), so a session from the address then is either the holder with an unlogged
  renewal (V3) or someone else, such as a static device in the pool; the logs cannot tell which.
  `low` is the level of the other "two explanations" cases (`srcmac_mismatch`,
  `dhcp_sources_overlap`).

*Alternative:* a new end reason `expired` at ACK time + lease (Fortinet report). *Why:* FortiOS
documents no expiry record (F4), so expiry is computed either way. The existing `timeout`
already covers it, so no check constraint changes and the timeline needs no new rule.

**D7 — VLAN and server of a FortiGate lease.**
- `leases.server` = the logged `interface`, so `lease.server_seconds` can key on it. The key
  space is shared with MikroTik server names, as documented.
- The VLAN comes from the subnet (`vlans` table, `find_vlan`). `dhcp.server_vlan_regex` is not
  applied to FortiGate interface names.

*Alternative:* a new map from interface to VLAN. *Why:* every client address lies in a
configured subnet. The MikroTik regex default `^dhcp(\d+)$` does not fit FortiGate names, and a
new map would only repeat the VLAN table.

**D8 — No backfill.** FortiGate DHCP lines loaded before the upgrade stay `fg_event` rows. They
remain stored and searchable (and sealed with their day), but they are not converted. When a
lookup ends without a lease, the probe of 2.8.3 (sized for FortiGate lease times) takes the
**newest** DHCP-shaped row of the IP before the moment from a trusted source, in `fg_event` or
`fg_dhcp`. Only when that row is an `fg_event` row of a logid mapped to `assigned` that would
have covered the moment does the lookup say so, with one text, `fortigate_dhcp_not_converted`,
which names the three possible reasons (loaded before the upgrade; fields that could not be
read, `fortigate.dhcp`; loaded by the previous version after a rollback) without choosing one,
so no status row records the upgrade moment. A newer RELEASE, a newer converted row or a row of
an unconfigured sender means nothing is said: the FortiGate may have given the address to
someone else or taken it back since. In internal mode and for an exact match the covering row is
cited as evidence ("kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez"),
so the PDF prints its raw line and seal data (2.12); for candidate matches the text names the IPs
without a citation, so a double-NAT report does not print other people's DHCP lines (KVKK). No
row after the moment is looked at, because another client's later ACK says nothing about the
moment and would put that client's MAC and host name into the report.
**A chain that does rest on a DHCP lease** (round 5 for `fg_dhcp`; round 6 for `mt_dhcp` too)
gets the same probe's `fg_event` query, for its IP **or its MAC**, over the time between that
lease's last DHCP evidence and the moment (at most the probe's window): unconverted FortiGate DHCP
lines there (loaded before the upgrade, loaded by the previous version after a rollback, or
fields that could not be read) may be a RELEASE, another client's ACK for the address, or the
MAC's ACK for another address that never entered the timeline, so the chain is lowered to `low`
with `fortigate_dhcp_not_converted_since_lease`, citing nothing (2.7). Without it, an `fg_dhcp`
lease open at a rollback keeps naming its holder at `high` through the rollback window although
sealed lines show that the address changed hands, and a MikroTik lease kept open by traffic
keeps naming a client whose move to another address a FortiGate DHCP line loaded before the
upgrade shows (the migration of 3.4 made while the previous version still ran).
*Alternative:* a one-off re-parse that moves rows from `fg_event` to `fg_dhcp`, recounts
`search_counts` and rebuilds the leases of those days. *Why:* the right general tool is roadmap
2.2 (re-load and re-parse from the archive). A special case would duplicate it: raw lines would
have to be read from the spool and the vault, and the worker cannot import the parsers. The
lookup stays honest about the gap.

**D9 — Loud runtime checks.** Two new R20 checks run in the existing `alerts.system` job every
5 minutes: `_lease_evidence` (`R20-lease:<cause>`) and `_nat_exits` (`R20-nat:<cause>`), each
in its own transaction, so a NAT problem never stops a lease alert (2.6.3). Both are 5651 Temel
and never gated. The causes that mean "5651 lookups lose their chain or may name the wrong
person" are `high`, so the default `alerts.email_min_severity = "high"` e-mails them, like R15
(2.6.3 has the full table). Every check counts **usable** evidence through one shared predicate
(`trust.kept`, the refusal rules of 2.6.1), never raw row counts (`search_counts` counts refused
and unknown-sender rows too):
- `none:<table>`, one cause per evidence table (`fg_dhcp`, `mt_arp`: high, because that
  evidence is periodic by design; `mt_dhcp`: medium, because RouterOS logs no renewals and a
  quiet period without new clients looks the same, and only while no `mt_arp` row arrives
  either): no usable row of that table while FortiGate traffic from private source addresses
  arrives, after usable rows of that table were seen in the 30 days before (a feed that
  stopped, also beside a working one: a mixed site whose FortiGate DHCP feed stops while
  MikroTik DHCP continues; D30);
- `absent` (info): no usable evidence of any table in 30 days (the set-ups KURULUM §0.1
  documents as ending at the internal IP; the card and §0.1 carry the limitation);
- `fortigate-refused` (medium when any row is refused, high when fewer than 10 % are usable):
  FortiGate DHCP rows refused by D4, D24, D25 or D28, one cause with the count, values and
  sources per reason in its details; rows of excluded interfaces and IPv6 rows are
  configuration, not refusals, and an empty `dhcp.fortigate_logids` (FortiGate evidence off)
  switches the cause off; it resolves as soon as the last 24 h, re-read with the current
  settings and sources, hold no refused row;
- `fortigate-unconverted` (high): a mapped logid that kept the `fg_event` shape, or
  `fortigate.dhcp` parse failures;
- `fortigate-renewals-missing` (high): active FortiGate clients' leases end without a renewal
  while no renewal ACK is seen at all, before the site check is done or after renewals were
  seen and stopped (D26); `fortigate-renewals-not-logged` (info) on a site whose check is done
  and whose renewals were never seen (the accepted set-up of DEVICE-SETUP 2.4 step 5);
- `fortigate-decline:<net>` (high): one address is left within minutes of its ACK again and
  again (a static device inside the pool, D33); `<net>` is the network's VLAN number, else its
  subnet (2.6.3);
- `dhcp-sources-overlap:<net>` (high): two DHCP authorities in one subnet (D27);
- `fortigate-lease-unlimited` (high): `lease=0`, so chains end after the fallback time (D6);
- `source-clock` (high): the device clock of a configured source whose rows feed lookups is
  more than 2 minutes off, measured on its latest records whatever their device time (D28);
- `fortigate-dhcp-mac` (medium): one MAC ACKed more than 3 distinct IPs of one origin,
  interface and configured network within 24 h (a shared or rewritten MAC, D28);
- `fortigate-lease`, `fortigate-dhcp-subnet` (low): lease out of range; an address in no
  configured network (whose FortiGate chains are `low`, D33; its details carry `first_day`, the
  day from which to rebuild the leases once the network is entered, round 7);
- `fortigate-unverified` (info): `fg_dhcp` rows exist and `dhcp.fortigate_format_verified` is
  still false (D29);
- `srcmac-shared:<interface>` (medium): a routed segment declared as L2;
- `R20-nat:conversion-failed` (high), `R20-nat:invalid` (high: the stored `nat.egresses` cannot
  be parsed), `R20-nat:legacy-changed` (medium: a legacy NAT row edited after the exits were
  last written, i.e. during a rollback, D18) and `R20-nat:unmapped-transip` (medium, D20).

E-mails and the Security page take a rule's name, action, runbook and e-mail facts from
`alerts/texts.py` by rule (`RULES`, `RUNBOOKS`, `DESCRIBERS`), not by dedup key, so WP-B defines
`R20-lease` and `R20-nat` there, and the per-cause line comes from `details.cause` through a
cause catalogue, as `R20-seal-config` does with `CONFIG_CAUSES` (2.6.3).

**D10 — srcmac: stored, never lease evidence, used only as a labelled partial step of a lookup.**
- Storage: a typed column `fg_traffic.srcmac MACADDR`, nullable, normalised by the parser, with
  no index. `mastersrcmac` and `srcmacvendor` stay in `extra`.
- srcmac never opens or extends a lease.
- **Recorded SPEC 9.1 deviation (tied to O2).** SPEC 9.1 says FortiGate traffic "only proves the
  IP was active, not the MAC". Using the srcmac field of a FortiGate session record to name a
  MAC, a device and a person in a lookup departs from that sentence. The departure is limited to
  listed interfaces, confidence `low` and a "kısmi kanıt" label, and is recorded in ADR-0016, in
  ASSUMPTIONS and under "Deviations" below.
- A lookup uses it only when all of these hold:
  1. the session row's `srcintf` is listed in `lookup.srcmac_interfaces` (default `[]`, which
     means off);
  2. the MAC is not an infrastructure MAC;
  3. it was seen with at most `lookup.srcmac_max_ips` (default **1**) distinct **IPv4** source
     IPs in [T − 1 h, T + 1 h] (router and switch guard; a dual-stack client's IPv6 and
     temporary IPv6 addresses carry the same srcmac and are not counted, lookups being IPv4
     only);
  4. no lease of that MAC on another IP covers any moment of that window (a router or switch
     with its own lease);
  5. the row comes from a trusted source (D24); rows of `unknown-<ip>` senders are never used
     for srcmac, nor counted by the guard.
- When those hold, a chain whose IP had no lease gets the MAC, device and person from that row,
  with confidence `low`, `mac_source = "srcmac"` and the explanation `srcmac_only`.
- A chain that has a lease gets `srcmac_agrees`, or, when the srcmac of a listed interface
  differs, it is treated as a conflict: the lease chain stays first, the srcmac chain is added as
  an alternative, both get confidence `low` and `srcmac_mismatch`, and the PDF prints both MACs.
  A static device using a pool address looks exactly like this; so does a DHCPDECLINE (no log
  record of a conflict is documented, F4, F20) in the seconds before the declining client's next
  ACK closes its interval (D33).
- Which interfaces may be listed (one wording in every text, F11): never a routed (L3) segment;
  an interface where the FortiGate is the VLAN's gateway and only layer-2 switches sit between
  it and the clients only after the two-client check of DEVICE-SETUP 2.5 (Fortinet: "The device
  must be connected directly. If there is a switch in between, the user may see the MAC address
  entry of the switch"; a Fortinet staff answer: device identification works properly "when the
  endpoints have layer-2 connectivity up to the FortiGate"; V8).

*Alternatives:*
- srcmac opens medium-confidence intervals (lease report D6; a larger SPEC 9.1 deviation);
- store srcmac and never use it.

*Why:* the FortiGate fills srcmac only with device detection on; with a switch in between the
switch's MAC "may" appear (F11), and behind a router or L3 switch it is the next hop's MAC for
everyone (F11, F12). A per-row, per-interface use cannot leak into the lease timeline, usage or
conflict alerts, and the report calls it partial.

**D11 — Texts say plainly what is partial.**
- `no_lease` becomes vendor neutral and names both DHCP sources.
- New `chain_missing`: candidate and exact matches without a chain are silent today (probe).
- New `fortigate_dhcp_not_converted` (D8), and the `srcmac_*` texts, which say what the srcmac
  field is (the FortiGate's device identification associated the source IP with a MAC) and never
  that the MAC sent the session.
- New `fortigate_format_unverified` while the site check of D29 is not done, and
  `fortigate_format_verified` (who marked it verified, and when) once it is;
  `lease_renewal_not_seen` (D6, the chain is `low`); `dhcp_sources_overlap` (D27);
  `session_from_unknown_source` for a match whose rows an unconfigured sender wrote, marked on
  the match itself (D24); `source_configured_later` for evidence of an `unknown-<ip>` sender
  whose address has since been configured as a FortiGate (D24); `source_clock_offset` when a
  cited source's clock was off at the moment, or, for a search that found nothing, a searched
  source's (D28, the chains are `low`); `fortigate_dhcp_not_converted_since_lease` for a DHCP
  chain with unconverted FortiGate DHCP lines of its IP or MAC after its lease's last DHCP record
  (D8, the chain is `low`), or with a converted FortiGate ACK of its MAC for another address that
  the lease build could not apply (a network entered or edited after the build; D33, round 7);
  `fortigate_dhcp_no_network` for an `fg_dhcp` chain whose IP lies in
  no configured network, where a declined address cannot be detected (D33, the chain is `low`);
  `egress_public_dynamic` for an answer through a listed address of an exit marked as changing
  (D31); `egress_internal` for an address that is not an internet exit (D13). No note singles out a lease
  that rests on FortiGate DHCP alone: a MikroTik DHCP lease without ARP-LOG is no better
  corroborated, and the real FortiGate caveat (format verified or not, by whom) has its own
  note.
- The PDF prints "Kısmi kanıt" for a srcmac step, the reason for a missing chain, the lease time
  and last DHCP evidence of a FortiGate lease, and one method paragraph per exit resolution
  whose double-NAT wording depends on what is configured and what is only assumed (2.12).

**D24 — Only configured FortiGate sources give FortiGate lease evidence.** An unknown sender
(`unknown-<ip>`) is parsed by content, and every line with `logid=` goes to the FortiGate parser
(`parsers/__init__.py:68-77`), so any host that sends UDP 514 could otherwise create `high`
lease evidence that supersedes the real holder and upserts devices.
- `dhcp_sources.load_rows` uses an `fg_dhcp` row only when its `source` is not *untrusted*.
  Untrusted is a source whose `sources` row has `unknown = true` or `type <> 'fortigate'`
  (`trust.untrusted_sources`, a leaf module, loaded once per run). The set filters **`fg_*`
  rows only** (`fg_dhcp`, `fg_traffic`, the probe's `fg_event`) and is never applied to `mt_*`
  rows, which only configured MikroTik sources write (`detect_type` never returns `mikrotik`),
  so a MikroTik source counts as trusted for its own rows, in the D27 origins and in the clock
  check (D28) as well. One source in the set is trusted after all: an
  `unknown-<ip>` source whose sender address lies in the `address` (an address or a network) of
  an enabled `sources` row with `type = 'fortigate'` and `unknown = false`: a FortiGate whose
  syslog sender address changed (another interface or `source-ip`, an HA member sending from its
  own address) and was configured afterwards. Its lines are trusted from the configured address
  on anyway, and anyone who can forge that address can forge the configured source, so trusting
  the earlier lines adds no path; refusing them would leave the leases of that window as
  `no_lease` for good although the typed rows hold the ACKs. A removed (disabled) source's
  address vouches for nothing. In production every source has a row (ingest writes it with the
  rows); rows of a source without one (test helpers only) count as trusted.
- The same rule removes untrusted sources' `fg_traffic` rows from the traffic that keeps lease
  intervals alive (`leases.load_traffic`, `_latest_traffic`), for both DHCP tables.
- In the lookup it removes them from every query that turns traffic rows into a claim about
  someone or about the firewall: the srcmac queries (`srcmac_sightings`, the MAC-only internal
  query, `srcmac_ip_counts`), the log probe of 2.8.3 (seen, unclaimed seen, the D15 check and the
  D31 observed check), the D8 probe (its `fg_event` and `fg_dhcp` rows) and the alerts that read
  traffic (`srcmac-shared`, `none`/`absent`, `fortigate-renewals-missing`,
  `R20-nat:unmapped-transip`). The clock checks of D28 take configured sources instead
  (`unknown = false`, any type), because they read `messages` of MikroTik sources too. The session search itself keeps
  every source, as recorded in Wave 2, but a match with a row of an untrusted source is marked
  **on the match** (`NatMatch.untrusted_sources`, the source names) and the outcome gets
  `session_from_unknown_source`; the PDF prints a "UYARI / Warning" row on that match and the web
  a badge, so a forged candidate never reaches a PDF unmarked, also in a list of 20.
  `untrusted_sources` is loaded once per lookup.
- Refused rows are counted (`fortigate_dhcp_untrusted_source`) and raise
  `R20-lease:fortigate-refused` with the reason "untrusted" and the source names; the existing
  "yeni log kaynağı" alert still names the sender. When the sender is then configured (the
  address rule above), the operator rebuilds the affected days, as for a logid map change (D4),
  and chains or matches that cite rows of such an `unknown-<ip>` source carry
  `source_configured_later` (info): "gönderici sonradan yapılandırıldı" / "sender configured
  later". A sender at another address stays refused (re-assigning its rows is roadmap 2.2).
- A removed source keeps its `type` and `unknown = false`, so the lines it sent while it was
  configured keep counting on a rebuild (the collector files a removed sender's new lines under
  `unknown-<ip>`, `ingest/sources.py`).
*Alternative:* the content rule as it is ("Nothing changes there"). *Why:* this is the first
path by which an unconfigured host could create high-confidence lease evidence; MikroTik rows
cannot come from unknown senders because `detect_type` never returns `mikrotik`.

**D25 — `dhcp_msg` is checked per logid.** The setting `dhcp.fortigate_dhcp_msgs` maps a logid to
the `dhcp_msg` values its rows may carry, compared case-insensitively. Default
`{"0100026001": ["Ack"]}` (F6); a logid without an entry (26002 today, no sample) is not
restricted. A row of a mapped logid whose `dhcp_msg` is not listed (a NAK, an INFORM reply or
anything else FortiOS might log under the same logid) is counted (`fortigate_dhcp_unexpected_msg`),
never used, and raises `R20-lease:fortigate-refused` (reason "unexpected `dhcp_msg`") with the
distinct values seen in rows of trusted sources (at most five, each cut to 32 characters, the
setting's own limit, with MAC- or IPv4-looking parts replaced by "…": the field is log-supplied
text). Sahada doğrula: V14.

**D26 — Traffic does not keep a FortiGate lease alive by default.** SPEC 9.1 lets traffic extend a
DHCP interval *because RouterOS logs no renewals*. For FortiOS a Fortinet staff member says
26001 is logged whenever it does a DHCP ACK, so renewals are probably logged (F7, likely; V3).
Traffic does not carry the client's MAC (srcmac only with device identification, D10), and on a
FortiGate-only site there is no ARP-LOG either. So a static device using a pool address, or the
device whose presence made a client send DHCPDECLINE, would keep the former holder's interval
open without bound, and the lookup would name that person at `high` without a warning. This
decision only stops traffic from *extending* such an interval; the ACK alone still keeps it open
for the logged lease (7 days with the FortiOS default; at `low` after half of it without a
renewal, D6), which D33 closes for the DECLINE case at the declining client's next ACK.
- New setting `dhcp.fortigate_traffic_extends` (default `false`, sahada doğrula V3). Off: traffic
  is kept as evidence of an `fg_dhcp` interval (latest traffic row, `last_traffic`) but never moves
  its deadline, so the interval times out at its last DHCP evidence + its alive window (D6).
  On: traffic extends `fg_dhcp` intervals as it does MikroTik ones.
- **Missing renewals are detected by their positive absence**, computed by the alert from the
  stored leases and `fg_traffic` (not from per-run counters: an incremental run seeds only open
  leases and cannot see an interval that timed out in an earlier run). A counted lease is a
  trusted `fg_dhcp` lease with a logged `lease_seconds` (so `lease=0` and fallback leases are not
  counted) that ended `timeout` in the last 24 h while its IP was in active use: a session of the
  IP *starting* (`time − duration`, never the end-log time, which for an idle departed client's
  session comes long after its last packet) between its last DHCP evidence + half the lease +
  15 min and its end, **and** one starting within 15 min after its end. A client that left, or
  came back later (a lunch break on a 1-h guest lease, a holiday on a 7-day lease), is never
  counted. The timeline counts `fg_renewal_ack`: a continuation ACK at least 40 % of the lease
  after the interval's previous DHCP evidence (a renewal, V3).
- `R20-lease:fortigate-renewals-missing` (high): at least 3 counted leases, and at least 5 % of
  the `fg_dhcp` leases open or ended in those 24 h, and no `fg_renewal_ack` in the lease runs of
  the last 7 days (renewals look unlogged), **while** `dhcp.fortigate_format_verified` is false
  (the site check, which includes the renewal check, is not done) **or** `fg_renewal_ack` was
  seen in the 30 days before those 7 (renewals stopped: a change). On a verified site whose
  renewals were never seen in 30 days, the same counted share gives
  `R20-lease:fortigate-renewals-not-logged` (info) instead: the accepted set-up of the renewal
  check's negative branch, named on the Security page without a permanent high alert that would
  teach operators to ignore the key (a separate key, because an open alert's severity never goes
  down, 1.2). A medium cause for gaps while renewals are seen is not kept: those chains are
  already `low` past half the lease (D6), a static device in the pool has its own high cause
  (`fortigate-decline`), and two logged DHCP servers have `dhcp-sources-overlap`. Neither text names
  `dhcp.fortigate_traffic_extends`: renewal ACKs that are refused (a `dhcp_msg`, logid or clock
  that differs from first ACKs) never become continuations and look exactly like unlogged
  renewals, so the high text first sends the administrator to `R20-lease:fortigate-refused`, then
  to the renewal check of DEVICE-SETUP 2.4 step 5. That check's negative branch (renewals not
  logged) is the only place that offers the setting, with its risk, as a decision for the
  organisation.
*Alternative:* the RouterOS rule for both tables (traffic always extends). *Why:* a missing chain
is visible and recoverable (`no_lease`, an alert, a setting); a wrong person at `high` is not.

**D27 — Two DHCP authorities are detected, not silently resolved.** An ACK for an IP closes
another MAC's open interval as `superseded` across tables, so MikroTik and FortiGate on one
subnet (a migration, a forgotten FortiGate DHCP server on a VLAN interface) or two VDOMs on one
range would make the lookup name whichever server logged last, at `high`.
- **One origin definition** for the timeline, the alert and the lookup: `DhcpRow.origin` =
  `(table, vd)` (`vd` from `fg_dhcp.extra`; `None` for MikroTik). Neither `devid` nor the sender
  (`source`) is compared: an HA failover changes `devid`, and HA members that send from their
  own addresses arrive as two sources (a network source files them as `fortigate-<ip>`), while
  the same DHCP service continues (V7). The timeline counts `superseded_across_sources` when the
  superseding event's origin differs from the closed interval's.
- `R20-lease:dhcp-sources-overlap:<net>` (high): within 24 h one configured network (a VLAN
  subnet, with or without a VLAN number) has usable rows of two origins.
- The lookup (2.7), one rule: when the chain's VLAN subnet has usable DHCP rows of two origins
  within W of the moment on either side (W = the covering lease's alive window, at least the
  longest configured DHCP window), the chain gets confidence `low` and `dhcp_sources_overlap`
  (the network and the origins, never another client's MAC). So the alert text "chains at those
  moments are shown with low confidence" holds for every chain of that subnet, the minutes
  after a cross-origin supersede included. A separate rule for the superseded predecessor (two
  more reads and its own text) is not kept: it would only add the case of a predecessor whose
  last DHCP row lies more than W before the moment (a MikroTik lease kept open by traffic alone),
  where the newer ACK is the only DHCP record near the moment and the chain rests on it.
- The topology stays unsupported (2.15); this only makes it visible.

**D28 — Guards on FortiGate DHCP rows (relay, IPsec, broken clocks, odd MACs).**
- `dhcp.fortigate_interfaces_excluded` (string list, default `[]`): rows of these `interface`
  names are never lease evidence (counted `fortigate_dhcp_excluded`). DEVICE-SETUP 2.4 tells the
  administrator to list the interfaces of `server-type ipsec` DHCP servers there: their leases
  expire `ipsec-lease-hold` (default 60 s) after the tunnel goes down (F20), so the logged lease
  is not the binding time, and the MAC such clients log is unknown.
- `00:00:00:00:00:00`, `ff:ff:ff:ff:ff:ff` and multicast MACs (least significant bit of the first
  octet set) are never lease evidence (`fortigate_dhcp_bad_mac`).
- A row whose time is the receive time, not the FortiGate's event time (`fg_dhcp.device_time =
  false`: no usable device time, or one beyond `max_clock_skew`, `fortigate.py:42-48, 540-565`),
  is never lease evidence (`fortigate_dhcp_receive_time`): a replayed or forwarded ACK would
  otherwise supersede the current holder at the wrong moment. Late lines that carry a usable
  device time are late evidence as today (`late.py`).
- **A device clock that is minutes off passes every check above** (`max_clock_skew` is a day),
  yet it shifts both the session search (an exact request at T searches [T − tolerance,
  T + tolerance]) and, on a FortiGate-only site, the lease chain by the same amount: the real
  session is missed and another client's session that reused the NAT port (port preservation,
  F15) is found as exact, with that client's lease. `R20-lease:source-clock` (high) checks every
  enabled, configured source (`unknown = false`, any type: a MikroTik DHCP router's clock shifts
  `mt_dhcp` events against FortiGate sessions just as well) whose spool files of today or
  yesterday hold rows of a table lookups read (`fg_traffic`, `fg_dhcp`, `mt_dhcp`, `mt_arp`,
  from `search_counts` by `raw_day`, the receive day, which a device clock cannot move), so the
  clock of an access point or a switch whose lines feed no lookup never pages. Per checked
  source it takes the source's **latest** records, not a device-time window: its newest 200
  `messages` rows by device time within [now − 1 h − `max_clock_skew`, now + 1 h +
  `max_clock_skew`] (`ix_messages_source_time`), of which those received in the last hour
  count. A device-time window of the last hour would miss exactly the clocks that harm most: a
  FortiGate set 3 h back writes no row with a device time in the last hour, while ingest
  accepts its times (they are within a day). Rows whose time is the receive time give exactly 0
  and are left out; the median ignores a minority of late lines. With at least 10 counted rows,
  a median beyond ±120 s (this contract's choice, two lookup tolerances) raises the cause. The
  text says that exact and candidate lookups are shifted by that offset and asks for NTP on the
  device (DEVICE-SETUP §2 gains the FortiGate's `config system ntp` / `set ntpsync enable`
  step, F21; for another device its own NTP client, which this contract does not describe). It
  resolves as soon as a check finds no checked source beyond the limit. Sahada doğrula V16.
- **The alert does not reach the auditor**, and 5651 requests come weeks after it resolved. Every
  row keeps both times for the retention period (`time`, `time_received`, 1.2), so the lookup
  measures the offset **at the requested moment** itself, over the rows of each cited source
  around T, and lowers the chains to `low` with `source_clock_offset` when it was beyond the
  limit (2.7): the offset is stated in the PDF instead of a "kesin eşleşme" that silently rests on
  a wrong clock. A search that found nothing cites no row, so it measures the sources whose rows
  it searched instead (round 5): an offset is exactly what makes an exact search miss the real
  session, and the PDF then states it next to the empty result.
- Alerts: `R20-lease:fortigate-refused` names receive-time rows (high when they are nearly all,
  e.g. a FortiGate clock more than a day off), bad MACs and the other refusal reasons;
  `R20-lease:fortigate-dhcp-mac` for one MAC that usable rows of the last 24 h ACK on more than 3
  distinct IPs of one origin, interface and configured network (clients behind a repeater that
  rewrites the MAC, DHCP over IPsec not excluded): counted on the rows, not on open leases,
  because D33 closes the previous lease of the MAC at each ACK, so only the latest client keeps a
  chain and the open-lease count would never see it; `R20-lease:fortigate-dhcp-subnet` for usable
  rows whose IP lies in no configured network (an unexpected scope such as a VPN pool, or a
  network not entered; the rows are still used, as MikroTik rows are, and their chains are `low`
  with `fortigate_dhcp_no_network`, D33).

**D29 — An unverified FortiGate format is stated, not hidden.** The parser constants and default
logids come from the reference and one community sample (V1). New setting
`dhcp.fortigate_format_verified` (boolean, default `false`), set by the administrator after the
DEVICE-SETUP 2.4 check (audited `settings.change` as every setting). While it is false and
`fg_dhcp` rows exist: the Kira kanıtı card says "kayıt biçimi sahada doğrulanmadı", every chain
whose lease is `fg_dhcp` carries `fortigate_format_unverified` (info) and the PDF method prints
it, and `R20-lease:fortigate-unverified` (info) stays open. Once it is true, the chain carries
`fortigate_format_verified` (info) with the setting row's `updated_at` and author, and the PDF
prints "kayıt biçimi {tarih} tarihinde {kullanıcı} tarafından sahada doğrulandı olarak
işaretlendi", so an auditor sees who marked it and when instead of a sentence that silently
disappeared. The check is defined by what ARCILOGGER shows (DEVICE-SETUP 2.4 steps 5–6: a
FortiGate lease appears on the Kira kanıtı card with no refused rows, and a trial 5651 Sorgu in
internal mode gives the test client's chain; the live tail and Arama show `fg_dhcp` and
`fg_event` rows under the same kind, so they cannot tell the two apart), with branches for a
missing RELEASE line (releases not logged: leases end by timeout, which is acceptable; the flag
is switched on for the ACK format) and for a RELEASE under another logid (added to
`dhcp.fortigate_logids`). The outcome of V2/V3 is written into KURULUM §16, and the check is
repeated after every FortiOS upgrade (F2 shows the field list changing between releases;
DEVICE-SETUP 2.4 and KURULUM §13 say so). The two constant tests of 3.1 are pin tests only; they
cannot detect a wrong assumption and are not described as if they could.

**D30 — Missing lease evidence is loud, measured on usable evidence.** Raw row counts do not
show it: `search_counts` counts every loaded row, refused and unknown-sender rows included, so a
feed whose every row is refused (a new `dhcp_msg` value, a renumbered logid, a FortiGate clock
more than a day off, a FortiGate that is not a configured source) looked healthy. And an open
MikroTik interval proves nothing about the feed, because traffic keeps it open.
- **Usable** evidence is a `mt_dhcp` row with action `assigned`/`deassigned`, a `mt_arp` row, or
  a `fg_dhcp` row the shared predicate `trust.kept` (the rules of 2.6.1) keeps.
- `R20-lease:none:<table>` (5651 Temel), **one cause per evidence table** (`fg_dhcp`, `mt_arp`,
  `mt_dhcp`), while trusted `fg_traffic` rows from private source addresses (RFC 1918) arrived
  in the last hour: no usable row of that table in its evidence window, and usable rows of that
  table in the 30 days before the window (a feed that stopped). A single aggregate condition
  would stay silent whenever any other table still delivers: on the supported mixed set-up
  (MikroTik DHCP for some networks, FortiGate DHCP for others) a FortiGate feed that stops while
  `mt_dhcp` rows keep arriving, or an ARP-LOG script that stops while DHCP continues, leaves new
  clients or static devices without chains for days. The windows: `fg_dhcp` 24 h, extended while
  open `fg_dhcp` leases exist to half of their longest `lease_seconds` plus 24 h (a FortiGate
  client renews at half its lease, so a working feed shows an ACK within that time; 4.5 days with
  the FortiOS default); `mt_arp` and `mt_dhcp` 24 h. Open MikroTik intervals do not count. A cause
  resolves as soon as a usable row of its table arrives. `fg_dhcp` and `mt_arp` are **high**:
  that evidence is periodic by design (renewals at half the lease; the hourly ARP-LOG script
  lists every host, static devices too). `mt_dhcp` is **medium**, and opens only while no usable
  `mt_arp` row arrives in its window either: RouterOS logs no renewals, so a weekend or a holiday
  without a new client, while servers and printers keep sending traffic, looks exactly like a
  stopped feed, and a weekly false high e-mail would teach operators to ignore the real one;
  beside a working ARP-LOG, which lists DHCP clients as well, a stopped MikroTik DHCP feed only
  lowers new chains to `medium`. When no table had usable rows in the 30 days, the condition is
  `R20-lease:absent` (info, one aggregate cause), whose text names the documented limitation
  (KURULUM §0.1: another DHCP server without ARP-LOG, a static-only FortiGate site) and the
  ARP-LOG option. A check opens a new alert after every dismissal (`alerts/store.py`), so any
  higher severity would be a permanent Security-page alert on a documented set-up; the card's
  `none` state and §0.1 carry the limitation.
- `R20-lease:fortigate-refused`: the `fg_dhcp` rows of the last 24 h that the predicate refuses
  (untrusted sender, unmapped logid, unexpected `dhcp_msg`, receive time, bad MAC). Rows of
  `dhcp.fortigate_interfaces_excluded` and IPv6 rows are configuration, not refusals: they are
  counted apart (`excluded`) and left out of both sides of the ratio, so a site that follows
  DEVICE-SETUP 2.4 step 2 (DHCP over IPsec excluded) is never alerted for it. With
  `dhcp.fortigate_logids = {}` (FortiGate evidence switched off) the cause is off. Medium when
  any row is refused; **high** when at least 10 rows were considered and fewer than 10 % of them
  are usable. One cause replaces the five per-reason causes of the second round: its details
  hold the count per reason (integers) and at most five values per reason (logids, `dhcp_msg`
  values, source names), which is as loud with four fewer texts, catalogue rows and tests. It
  resolves as soon as the check, re-reading the last 24 h with the current settings and sources,
  finds no refused row (as `none` does), not 24 h later: after the administrator corrects a
  logid map, a `dhcp_msg` list or a source definition and rebuilds, the alert and the card follow
  at once, so the site check of DEVICE-SETUP 2.4 step 5 can pass the same day. Receive-time and
  bad-MAC rows stay refused, and counted, until they leave the 24-h window.
- `R20-lease:fortigate-lease-unlimited` (high, split from `fortigate-lease`): `lease=0` rows,
  whose chains end after the fallback time.
- The Kira kanıtı card (2.12): `refused` while its own live count of refused rows (the same
  `trust.refusal` predicate and 24-h window as the alert) is above 0, which wins over `none`
  (records do arrive, so "make the server send records" would be the wrong fix); otherwise
  `none` while an `R20-lease:none:<table>` or `absent` cause is active, naming the tables;
  otherwise `ok`.

**D33 — A FortiGate client holds one address per server interface and subnet.** Without it a
DHCPDECLINE (no log record is documented, F4, F20) leaves the declining client attached to the
declined address at `high` until half its lease. Scenario: a printer is set statically to
172.20.42.57 inside the VLAN 42 pool; 2026-10-12 08:41:10 FortiOS ACKs .57 to
`00:00:5e:00:53:31` (an ACK, logged as 26001, F1, F6); the client's ARP probe finds the printer
and it declines (no documented log record); 08:41:14 FortiOS ACKs .58 to the same MAC. Today's
`_assigned` closes only other intervals of the *same IP*, so (.57, :31) stays open until
2026-10-19 08:41 and the printer's traffic at 10:12 names that client at `high`;
`lease_renewal_not_seen` lowers it only after half the lease, `fortigate-dhcp-mac` needs more than
3 IPs, and no conflict is seen. After `conflicted-ip-timeout` (1800 s) the next client that
declines .57 supersedes the last one, so the printer's traffic always goes to the latest innocent
client.
- **Rule:** an `assigned` of MAC m for IP y closes m's other open `fg_dhcp` intervals of the same
  origin (D27), the same interface (`server`) **and the same configured network**, at the new
  ACK, as `superseded`, counted `fg_mac_moved` (RFC 2131: a client holds one binding per server
  and interface; DECLINE is followed by a restart of the exchange). The **network** of an IP is
  the `vlans` row whose subnet contains it (`find_vlan`, most specific first), compared by its
  `subnet`, the table's key (round 6): `vlans.vlan_id` is nullable by design, a flat LAN or a
  relayed network entered without a VLAN id included (ILK-ADIMLAR/FIRST-STEPS §5), so a key on
  the VLAN number would close nothing there and bring back this decision's own failure. The
  network is part of the key because one FortiGate interface can carry several DHCP servers, one
  per relayed network behind a router (F22): a NAS with one MAC on tagged VLANs 41 and 43
  relayed to `internal` holds one address in each, and a key on the interface alone would cut
  one of its chains at every renewal. Before an interval of m on another IP is closed, its own
  deadline is applied first (`_expire_ip(other_ip, event.time)`): an interval already past its
  end times out at that end, exactly as a live run that expires every minute would have closed
  it, so live and rebuilt rows stay equal. Intervals of other interfaces, networks or origins (a
  laptop on two VLANs, a MAC on two VDOMs) are not touched, and nothing is closed when the IP
  lies in no configured network (network None: unrelated unconfigured scopes, such as two VPN
  pools or two relayed networks nobody entered, would otherwise count as one network;
  `fortigate-dhcp-subnet` names such rows). Because a declined address cannot be detected
  there, an `fg_dhcp` chain whose IP lies in no configured network is shown `low` (lease and
  chain) with `fortigate_dhcp_no_network` (2.7, 2.11): on a FortiGate-DHCP site every DHCP
  network is entered, with or without a VLAN id. **A network entered or edited later** (round 7)
  changes no stored lease: leases are rebuilt only on request (the hourly and nightly rebuilds
  cover today and yesterday, and a lease that ended before yesterday's midnight is final), so a
  lease built while either address lay in no configured network, or in a network entered
  narrower than the DHCP scope (a /24 for a /23 scope), keeps the close D33 missed once the
  network is entered. The lookup therefore does not rely on the build: an `fg_dhcp` or `mt_dhcp`
  chain still `high` or `medium` is lowered to `low` with
  `fortigate_dhcp_not_converted_since_lease` when a kept `assigned` `fg_dhcp` row of its MAC for
  another address, one D33 would close it on with today's networks, lies between the lease's
  last DHCP record and the moment (2.7); the `fortigate-dhcp-subnet` alert and both texts also
  ask for a rebuild of the leases (from the alert's `first_day`, or the lease's start day).
- **The MikroTik variant (a migration):** the same `fg_dhcp` `assigned` also closes m's open
  **`mt_dhcp`** interval on another IP of the same configured network (compared by subnet on
  both sides, whatever VLAN number the MikroTik row or `dhcp.server_vlan_regex` gave it), at the
  ACK, as `superseded` (counted `superseded_across_sources`), after applying its deadline first in
  the same way. After a move
  from MikroTik to FortiGate DHCP a client that the FortiGate gives a new address would otherwise
  keep its MikroTik lease on the old one, traffic would keep that lease open (D26 does not apply
  to MikroTik leases), and a printer set statically to the old address, outside the new pool, would
  be named as the client at `high` for good once the D27 window has passed, although the logs
  hold the FortiGate ACK that moved the MAC. A MikroTik `assigned` closes nothing this way:
  MikroTik-only sites keep today's rule (WP-B's "MikroTik behaviour identical"; the DECLINE gap
  there is a residual risk, 2.17).
- **Alert:** `R20-lease:fortigate-decline:<net>` (high): in 24 h one address had at least two
  `fg_dhcp` leases that were closed by D33 within 300 s of their opening ACK — a static device
  inside the pool. Its key and title name the network (`<net>`, `{network}`: the VLAN number,
  else the network's subnet and name, 2.6.3) and its e-mail the VLAN number only, never the IP;
  the Security page details add at most five declined addresses (the addresses where a static
  device answers, no MAC, no host name), because the remedy, to move the device out of the range
  or reserve its address, needs the address (2.16).
- Sahada doğrula V17: does FortiOS log a DECLINE, or check an address (ping or ARP) before it
  offers it? Either would make the decline visible; neither is documented (F4, F20).
- Golden (3.4): ACK(.57, :31) at 08:41:10, ACK(.58, :31) at 08:41:14, traffic from .57 at 10:12 →
  no `high` chain for :31, also on a network stored with `vlan_id` NULL; with no network entered
  for the address, :31's chain is `low` with `fortigate_dhcp_no_network`; with the network
  entered after the leases were built (no rebuild), or a /24 entered for a /23 scope, :31's .57
  chain is `low` with `fortigate_dhcp_not_converted_since_lease` (round 7); the migration golden
  covers the MikroTik variant.
*Alternative:* close on any ACK of the MAC, whatever the interface. *Why:* a MAC can hold one
address on each of two VLANs (one host on a trunk), and closing those would cut real chains.

**Part B — per-exit NAT (dual WAN)**

**D12 — One setting `nat.egresses` replaces `nat.public_ips`, `nat.wan_transip` and
`lookup.port_hint`.** It is a JSON object keyed by exit name; the shipped default is `{}`.
*Alternative:* keep the old keys as a legacy path next to the new one (NAT report D2). *Why:*
- two configurations of one fact confuse administrators and reports;
- the object type already works end to end in the settings API, audit, CLI and raw-JSON editor
  (precedent `lease.server_seconds`);
- a list of objects has no `SettingType` (`settings_meta.py:65-78`).

**D13 — The exit model (section 2.8.1).** Each exit has:
- `mode`: `direct` (the FortiGate holds the public address, so the lookup is exact), `isp_nat`
  (an ISP router translates once more, so the lookup gives candidates), `not_logged` (the line
  bypasses the FortiGate, so there are no records) or `internal` (not an internet exit: the
  FortiGate's source-NAT address towards a DMZ, another VLAN, a VPN or a branch, which FortiGate
  policies with NAT switched on write as `transip` like any other; it holds `transips` and
  `interfaces` only, its addresses count as claimed and it is never searched, so such an address
  stops counting as an unconfigured line in the alerts, the notes and the candidate searches);
- `public`: IPv4 addresses or networks of at least /24, never a private (RFC 1918), shared
  (RFC 6598) or reserved address (2.8.1 rule 5);
- `public_dynamic`: the line's public address may change (PPPoE, a dynamic ISP address;
  `isp_nat` only, default `false`, D31);
- `transips`: the FortiGate `transip` values of the exit (`isp_nat` and `internal` only);
- `interfaces`: optional `dstintf` names that only narrow the search;
- `port_preservation`: `unknown`, `preserved` or `not_preserved`;
- `label`: optional text.

An `isp_nat` exit with no `transips` is the catch-all: every private or shared source-NAT address that no other
exit claims. There is at most one. "No exits configured" behaves as one implicit catch-all,
which is today's pure double-NAT behaviour, except for an address the logs show as the
FortiGate's own `transip` near T: that one is answered as observed (D31 (a)) on every site,
with or without exits, so a bridge-mode site that never configured exits gets the exact answer
instead of other people's candidate sessions and a false "double NAT assumed" paragraph.

**D14 — Which exit answers (section 2.8.3).**
- The requested public IP is matched first against every exit's `public`, then against every
  exit's `transips`.
- An address that no exit claims but that the logs show as `transip` within ±15 minutes is the
  FortiGate's own address at that time: it is answered as an observed direct address
  (resolution `observed`, exact with a port) with the warning `egress_observed_not_configured`
  (D31).
- Any other unclaimed address is searched only in the `isp_nat` exits that have no `public`
  addresses or are marked `public_dynamic` ("genel adresi bilinmeyen ya da değişken"), always
  with the warning `egress_public_unknown`. When there is no such exit the answer is `none`
  (`public_ip_no_egress`).
- An address of a `not_logged` exit is answered `none` (`egress_not_logged`) unless the probe
  sees it as `transip` near T: then the line does pass the FortiGate after all (moved, or its mode
  set wrongly), and it is answered as an observed address (resolution `observed`, the exit
  named) with the warning `egress_not_logged_but_seen`, instead of a false "nobody was listed".
- An address of an `internal` exit is answered `none` without a search (`egress_internal`):
  it is not an internet address, so no 5651 request about the organisation's internet traffic
  can name it.
- Once each line's public address and interfaces are entered, a question about one line's IP
  never lists another line's sessions (golden tests 3.4 E-a, E-n2): the interface condition is
  kept in every widened search and dropped only on positive evidence that the names are wrong,
  i.e. the exit's own addresses seen near T on none of its interfaces (2.8.3). An idle line
  looks like no row on its interfaces, which is not such evidence.

*Alternative:* answer an unclaimed address with `none` always. *Why:* an upgraded single-line
site that only had `nat.wan_transip` would otherwise answer every request with `none` until its
administrator enters the ISP address. That is the reference site's configuration.

**D15 — The log check is generalised and applies to every public/transip resolution.**
- **The check.** A configured address P of exit E counts as the FortiGate's NAT address around T
  when the logs show it as `transip` within ±15 minutes (row time, as in Wave 2), or show no
  **private or shared** source-NAT address (the addresses rule 5 of 2.8.1 refuses as public:
  RFC 1918, RFC 6598 and the reserved ranges) outside E's **own set**, counting only rows on E's
  `interfaces` when E declares any. The own set is E's own addresses plus the `transips` and
  `direct` `public` addresses of every other exit X **that does not leave it**; X leaves it when
  X is an `isp_nat` exit without a `public` address (round 5), or when E and X both declare
  interfaces and those are disjoint (round 6; this replaces round 5's exception for such an
  `isp_nat` exit, and applies to every mode, `internal` included). The set is taken per address:
  an address that one exit staying in the set claims stays in it (a `transip` shared with an
  exit on E's own interface). The exits that leave include F, the exits E's fallback falls into.
  The check runs for a `direct` public address and for a `transip` resolution, **with or without
  a port** (today's `_candidates` runs `_transip_seen` first as well).
- **Seen, but not alone** (round 6). When the probe sees P within ±15 minutes **and** a private
  or shared address outside own(E) (on E's interfaces when E declares any), the check passes,
  but a search on P that finds no session (the exact search, or `transip = P` without a port)
  becomes the `fallback` below instead of `none`: the probe looks at row times only, so near a
  change of the line's mode (an ISP modem reset to router mode at 18:14, a switch to bridge mode
  at 18:24) its ±15 minutes hold rows of both sides, and the logs cannot tell on which side T
  lies. A search that finds a session stays exact. Both facts come from the one cached probe
  pass (2.8.3); Wave 2 answered `none` here (an E-j exception).
- **Why only private or shared addresses, and only on E's lines.** A second NAT by an ISP router
  leaves the FortiGate with an address behind that router, which is private or shared. A
  **public** `transip` that no exit claims is the FortiGate's own address (an IP pool, a second
  line nobody configured, a PPPoE line's new address; D31 (a) answers it as observed), so a
  session that left with it says nothing about P's line. Counting it (Wave 2's rule) made a quiet
  direct line fail the check whenever the mail server's unconfigured pool address was active:
  the answer became candidates over every unclaimed session, the mail server's included, with a
  PDF sentence that the ISP router "may have done a second NAT", where the exact search would
  have found the one session (E-o). For the same reason an SNAT towards a DMZ or a tunnel, which
  leaves on another interface, does not count against a line whose interfaces are declared (and
  can be declared `internal`, D13).
- **F (the fallback exits of E)** = the `isp_nat` exits without a `public` address that share an
  interface with E; or, when neither E nor that exit declares interfaces, only when both are
  **as converted** by v0018 (every field but `label` equals the exit of the same name in the
  status row `nat.egresses_migration.new`: legacy (b)'s `tanimsiz`, (d)'s `wan`). Otherwise F is
  empty. An administrator who edits such an exit's addresses, mode or interfaces turns it into
  an ordinary exit.
- **Why F leaves the own set.** In legacy (d) the `wan` exit's W is, for Wave 2, the same line as
  A before a switch to bridge mode, the case Wave 2 was built for (`engine.py:33-43`,
  `test_lookup_engine.py:278-281`: "instead of a false 'the FortiGate logged no session'"). With
  W in the own set, a question about A for a moment before the switch passes the check and
  answers `none`. What the fallback treats as the same line now never counts against it.
- **Why every `isp_nat` exit without a public address leaves it too** (round 5). Such an exit's
  line has no known public address, so nothing in the settings says it is not E's own line
  behind an ISP router: an upgraded `genel-1` whose line is still double-NATed, after the
  administrator saved that line's NAT address as a new exit from the observed panel (the
  public address is refused there, `genel-1` holds it), or after entering interfaces on the
  converted `wan` only (then no longer *as converted*, so outside F). Counted as another line's
  address, it made the check pass and the exact search answer a bare "the FortiGate logged no
  session" for every moment. Left out, the check fails and the answer is the warned `fallback`
  (`public_ip_not_transip`). Only declared, disjoint interfaces separate the two lines. The
  fallback search is not widened by it: it still covers F only, so another line's sessions are
  never listed (E-c2).
- **Why an exit on other declared interfaces leaves it** (round 6). When E declares interfaces,
  only rows on E's interfaces count, and a private NAT address is not unique across lines or over
  time: two modems of one ISP hand the FortiGate the same default address, and a `/24` of
  `transips` covers many. A row on E's interface whose address another line's exit claims is
  therefore E's line behind a router at T (line 1's modem reset to router mode, giving the
  FortiGate the address that line 2's modem gives it on `wan2`); kept in the own set, it made
  the check pass and the exact search answer a bare "no session". Rows of that exit's address on
  its own interfaces never count, so a real second line still never fails E's check (E-c2's
  last case, E-c4). The fallback search is unchanged (E's own addresses, F, the unclaimed
  predicate, on E's interfaces): the answer is the warned `fallback`, at worst with no
  candidate.
- **When the check fails**, or passes "seen, but not alone" and the search on P finds no session
  (resolution `fallback`, 2.8.3): candidates over F's `transips` (a
  catch-all F: the unclaimed predicate), or over the unclaimed predicate when F is empty,
  **always together with E's own addresses** (P, or E's `public`), every part narrowed to E's
  `interfaces` when E declares any (2.8.3; F without interfaces is only ever an *as converted*
  exit beside an E without interfaces). E's own addresses stay in the search because the probe
  looks at row times only: a 45-minute session through P that was logged once, at its end, an
  hour after T overlaps T but is invisible to it (Wave 2's fallback kept the requested address
  too). The search uses the unclaimed **predicate**, not the ≤ 5 addresses the probe saw (they
  are only named in the texts), so a sixth unconfigured private address is searched as well.
  The predicate covers private and shared addresses only (2.8.3): a session that left with an
  unclaimed public address left through the FortiGate's own address, not through P.
- **Effect.** A new configuration never gets another exit's claimed sessions for a double-NATed
  direct line (E-c, E-c2). An upgraded configuration keeps Wave 2's narrowing to W (E-j (d),
  (d2)) until the administrator enters interfaces or a public address for `wan` (the §13 note
  asks for both); it then behaves as a new configuration. Probe B (legacy (b), T2 unclaimed)
  therefore keeps its Wave 2 answer on an upgraded site, and probe C (legacy (d) whose W is in
  fact a second line) keeps it until interfaces or that line's public address are entered: a
  legacy configuration cannot tell the two readings of W apart without them.

**D16 — Port preservation is set per exit and is only ever a ranking hint.**
- `preserved` adds today's +5 points (`PORT_HINT_POINTS`) to that exit's candidates whose
  `transport` equals the requested port.
- Nothing ever becomes exact through port preservation, because the ISP router's table is not
  logged.
- The global `lookup.port_hint` is removed; the migration moves its value into the exits.

**D17 — `trandisp` values are a setting.** `nat.snat_trandisp` (default `["snat"]`, sahada
doğrula) replaces the hard-coded `trandisp = 'snat'` of searches that are not narrowed to an
exit's `transip` (`sessions.py:318-319`). Rows with a `transip` and an unlisted `trandisp` in
the window are counted and named (`nat_unknown_trandisp`), never dropped silently.

**D18 — Migration of the legacy keys keeps every answer the same, and never leaves an unparsable
value.** Migration `v0018` converts an administrator's `nat.public_ips`, `nat.wan_transip` and
`lookup.port_hint` with the pure function `legacy_egresses()` (section 2.8.2), so an upgraded
site gets the same modes and matches as before. The cases that differ are listed by name in
golden test 3.4 E-j: the new notes; the D31 (a) observed answer (an unclaimed address the
FortiGate itself used as `transip` near T is now exact, where Wave 2 gave candidates, legacy (a)
without exits included); the C2
`double_nat` fix; case (f) (other addresses of a bridge-mode site whose `nat.wan_transip` equals
its public IP); case (g) (503 before); case (h) (a private address entered in `nat.public_ips`);
a fallback that now also lists E's own sessions overlapping T (Wave 2's (d) left them out
when W was seen); an unclaimed **public** NAT address, which no longer fails the D15 check
and is no longer searched as an unclaimed candidate (D15, cases (a), (b), (e), (f) on a site that
has one); and an empty search on a P that the probe saw beside a private or shared address
outside its own set, now the `fallback` where Wave 2 answered `none` (D15 "seen, but not alone",
round 6; case (d) near a change of the line's mode).
- The converter puts every public IPv4 `nat.public_ips` entry into **one** direct exit
  (`genel-1`, at most 16 entries; `genel-2` … only past 16). It always adds a `wan` or `tanimsiz`
  exit when it keeps an address, so 8 exits hold at most 7 × 16 = **112** legacy public
  addresses; more fail validation. When `nat.wan_transip` is one of the public addresses
  (bridge mode), no `wan` exit is made: the direct exit already gives today's exact answer.
  Values that cannot be parsed (stored without validation, e.g. `"not an address"`) and IPv6
  entries are dropped and reported in `dropped`.
- An entry in RFC 1918 or RFC 6598 space (the FortiGate's own WAN address behind an ISP router,
  which the old help text "Güvenlik duvarının genel (WAN) adresleri" invited, e.g.
  `100.64.12.9`) is not a public address: it moves into the `wan` exit's `transips` (made if
  needed, together with `nat.wan_transip`). Wave 2 excluded every session translated to it from
  every candidate search, so every real public-IP request on such a site listed nobody (case (h)).
  A reserved entry (0/8, 127/8, 169.254/16, 224/4, 240/4) is dropped with reason `reserved`.
- The migration runs its result through a frozen copy of `validate_egresses`. If it still fails
  it writes `{}` (the implicit catch-all, today's pure double NAT), records
  `"conversion_failed": true`, and `R20-nat:conversion-failed` (high) tells the administrator to
  enter the exits. A site is never left with a value that `parse_egresses` refuses.
- No audit entry is written inside the migration (no revision does; the audit chain format is
  application code). v0018 writes a status row `nat.egresses_migration` with the legacy values,
  the result, `dropped` and `conversion_failed`; the seed, which runs after every upgrade with the
  application's own `append_audit` (precedent: `license.trial_start`), turns it into the
  `settings.change` entry once (target `setting:nat.egresses`, as `settings_admin.change_setting`
  writes it).
- **The legacy rows are kept, unread, for one version** (O6 alternative taken): an
  administrator-owned `nat.public_ips` / `nat.wan_transip` / `lookup.port_hint` row stays in
  `settings` as it was; the new code never reads, lists or writes it (the keys leave
  `DEFAULT_SETTINGS`, so `settings set` refuses them with `setting_unknown`), and the first
  revision after Wave 3 deletes them. `Install-Arcilogger.ps1 -Rollback` without
  `-RestoreDatabase` (KURULUM §13) then runs the previous version with its own NAT values
  instead of silently falling back to pure double NAT. Seed-owned legacy rows are deleted (the
  previous version's defaults are the same values).
- **Re-upgrade after a rollback: named, never converted again.** The database is already at
  `0018_nat_egresses`, so alembic does not run v0018 again, while the operator may have edited
  the legacy rows in the previous version. Nothing converts them automatically: two edits of one
  fact cannot be merged by a rule, and a second conversion path in the seed would need its own
  audit entry, change-record update and tests, while it still could not cover a site that had
  the shipped NAT defaults (v0018 writes no status row there, so there is no `from` to compare).
  One condition covers every site instead: `R20-nat:legacy-changed` (medium) is open while an
  administrator-owned legacy row (`nat.public_ips`, `nat.wan_transip`, `lookup.port_hint`) has an
  `updated_at` later than that of `nat.egresses`, and it names the keys (names only). It closes
  when an administrator writes `nat.egresses` (which moves its `updated_at` past the legacy
  rows'), the condition that opened it, so the alert cannot stay open after the exits are saved.
  The new code never writes a legacy row (the keys leave `DEFAULT_SETTINGS`), so only the
  previous version run after a rollback can make the condition true. Until the administrator
  acts, lookups use the exits as they are, and the alert says so.

**D19 — The outcome and the PDF record the exit used.**
- `outcome.egress` holds the resolution, the exits, the addresses actually searched and the last
  content change (time, author) of each exit used (D32).
- Every match carries the name of its exit, decided by the group's row nearest to T (D32); the
  PDF prints it with the row's `dstintf` on every match row (2.12).
- `double_nat` is true for every `isp_nat` answer, including an exact answer on an `isp_nat`
  exit's own `transip` (legacy case (c) keeps today's `true`), and for every `fallback`, which
  fixes probe C2: an unconfigured IP while `nat.public_ips` was set gave candidates with
  `double_nat=false`, and the PDF left out its method paragraph. What the PDF then says depends
  on the resolution (configured, assumed, or a direct line whose check failed; 2.12).

**D20 — The administrator reads the values instead of guessing them.** A new endpoint
`GET /settings/fortigate/observed` (admin, counts only) lists:
- the `(transip, dstintf, trandisp)` triples seen in the last 24 h with the exit that claims
  each;
- per `srcintf`, how many srcmac values and the most IPs seen with one MAC.

Ayarlar shows it under the exit editor. Lookups name unclaimed source-NAT addresses seen near T
(`egress_unmapped_seen`). Once exits are configured, an unclaimed source-NAT address that keeps
carrying sessions (a new ISP line, a WAN address that changed behind an ISP router) also raises
`R20-nat:unmapped-transip` (medium, 2.6.3) before anyone asks about that line's public IP and
gets "nobody was listed"; it resolves when an exit claims the address. A NAT address towards a
DMZ, another VLAN, a VPN or a branch is claimed by an `internal` exit (D13), so it stops raising
the alert and the notes without ever being searched.

**D31 — Observed and dynamic public addresses.** Once every exit has a public address (the
upgrade note asks for it), an unclaimed address would otherwise get `none`, even when it was
the organisation's: (a) a `direct` line with a dynamic public IP (PPPoE or bridge mode, common for
SME lines), whose new address the logs show as `transip`; (b) an `isp_nat` line whose ISP
router's public IP changed, which no log can show.
- (a) Before `public_unknown`/`no_egress`, the probe of the log check (2.8.3) asks whether P
  itself was the `transip` of a trusted session **overlapping** T ± 15 min (rows with
  `time >= T − 15 min`, `time − duration <= T + 15 min`, bounded like the session search by
  `time <= T + 15 min + max_session_seconds`), so an address used only by one long session that
  was logged once, at its end, is still observed. If so, P is answered as an observed direct
  address: exact on `transip = P AND transport = port` with a port, candidates `transip = P`
  without one, `double_nat` false, no exit name, warning `egress_observed_not_configured`.
- (b) An `isp_nat` exit may be marked `public_dynamic`: it still claims its listed addresses
  (attribution to that exit for those), and it is also searched for unclaimed addresses, with
  `egress_public_unknown`, like an exit without a public address. Every answer through one of
  its listed addresses carries `egress_public_dynamic` (warning) and a PDF method sentence: after
  the ISP re-dials the line, the listed address may belong to another customer, no log record
  can confirm the address of an `isp_nat` line (2.17), and the administrator may not have edited
  the exit yet, so the answer says that only the ISP can confirm the address at T.
- DEVICE-SETUP 2.6 asks for each line whether its public IP is static.
*Alternative:* a site-level "the public list is complete" switch (default off) that sends every
unclaimed P to all `isp_nat` exits. *Why:* per exit is narrower: a static line never receives
another address's question, and (a) needs no setting at all.

**D32 — An exit definition changed after the event, and sessions that change exit, are named.**
- 5651 requests are often weeks old; the lookup resolves P against today's exits, and for an
  `isp_nat` exit no log record confirms the public address. A warning on *every* change (a
  label edit, an unrelated new exit, the interfaces or public addresses §13 asks to enter) would
  sit on nearly every PDF for weeks and hide the case that matters, so the warning is per exit
  and per field that decides the addresses.
- **Change record.** A status row `nat.egresses_changes` (not in `DEFAULT_SETTINGS`, like
  `db.timescale`) holds per exit name `{"fingerprint": <SHA-256 of the canonical JSON of the
  exit's mode, public, transips and public_dynamic>, "changed_at", "changed_by", "deleted":
  bool}`. The fingerprint holds only the fields that decide which addresses an exit claims or is
  searched for: interfaces only narrow a candidate search and the logs guard them
  (`egress_interface_not_seen`, 2.8.3), they never apply to an exact answer, and port
  preservation only reorders candidates. With them in it, the interface step that §13 and
  DEVICE-SETUP 2.6 ask every upgraded site to do would put the warning on nearly every PDF of a
  moment before the edit for months, the failure this record exists to avoid.
  `settings_admin.change_setting` updates it in the same transaction as the `settings.change`
  audit entry whenever a value of `nat.egresses` changes an exit's fingerprint, adds or deletes
  an exit (API and CLI both go through it); a label-, interface- or port-preservation-only edit
  changes nothing. v0018 seeds it for every converted exit with
  the latest `updated_at` and the `updated_by` of the converted legacy rows (the content's last
  real change and its author). It keeps no earlier contents: the audit log holds every old
  value, and a per-exit history would have to be complete to be safe (a record of only the
  content before the latest change loses the address an exit held at T after two edits).
- `egress_changed_after_event` (warning) is given in two cases, which together need no history:
  - an exit in `outcome.egress.egresses` has `changed_at` later than T. This covers every
    **claimed** P: two exits never claim one address at once, so an exit that claims P now and
    did not claim it at T has changed since;
  - P is claimed by **no** exit now (resolutions `public_unknown`, `no_egress`,
    `unconfigured`) and any exit, deleted ones included, changed its content after T: the
    address may have belonged to that exit at T (renumbered, edited twice, or a catch-all or
    `public_unknown` exit that was deleted, as §13 asks). An `observed` answer is left out: it
    rests on the FortiGate's own record of P as its `transip` near T, which no exit definition
    changes (a `not_logged` exit that claims P is covered by the first case).

  The text names those exits with the time and author of each change and what each holds now
  (public and NAT addresses, or "silindi" / "deleted"), and says that the earlier definitions
  are in the organisation's audit log and can be requested from its administrator; the PDF
  method prints it. An exit without an entry (values written without `change_setting`, tests)
  falls back to the row's `updated_at`/`updated_by`, so the warning errs on the side of showing.
- Internal authors are shown in words: `:migration` as "yükseltme (eski NAT ayarlarından
  aktarıldı)" / "upgrade (converted from the old NAT settings)", `:seed` and `:system` as
  "varsayılan değer" / "shipped default"; never the raw `:migration`.
- Resolving the value in force at T from the `settings.change` history is the O9 alternative.
- Sessions are grouped by `TrafficRow.key` as in Wave 2 (`sessions.py:136-148`), which includes
  `transip` and `transport`: a session re-NATed to another address after a route change is two
  matches, each with its own exit, never one. Within one group the rows can still resolve to
  different exits through a shared `transip` whose rows carry different `dstintf` (an SD-WAN
  member change, F14), or a `dstintf` no exit lists: such a group takes its exit from its row
  nearest T and gets `session_changed_exit` (V9).

**Shared**

**D21 — No licence gate, KVKK least privilege, no new permission and no new audit action.**
Everything here is 5651 Temel (ASSUMPTIONS C-2).
- srcmac and FortiGate DHCP hostnames are personal data. They are shown under the same
  permissions as MikroTik leases: `lookup.run` for chains, `search.read` for `srcmac:`/`mac:`.
  `TrafficInfo.srcmac` (the typed field of a stored outcome) is filled only for rows of
  interfaces listed in `lookup.srcmac_interfaces`. That does **not** keep MACs out of PDFs: the
  report prints the raw line of every cited row (`reports/inputs.py` `cited_evidence`,
  `render.py` `evidence_item`, up to `RAW_DISPLAY_CHARS`), and a session record of an interface
  with device identification on carries `srcmac` and the fields below whatever the setting says.
- FortiGate device identification (DEVICE-SETUP 2.5) adds device name, OS, hardware vendor and
  version, device type and passively learned user names (`srcname`, `osname`, `devtype`,
  `srcfamily`, `srchwvendor`, `srchwversion`, `srcswversion`, `unauthuser`, `unauthusersource`;
  F19) to every session record of that interface. ARCILOGGER keeps them unchanged in
  `fg_traffic.extra` for the retention period (raw logs are evidence and never modified), shown in
  raw lines and Search, **and in the raw-line evidence of every 5651 report and evidence package
  that cites a session of that interface**, other people's candidate sessions of a double-NAT
  report included (up to `lookup.max_candidates`). Turning it on is the organisation's and its
  legal counsel's decision; DEVICE-SETUP 2.5 says exactly what it adds and where it appears, and
  advises enabling it only on interfaces that will be listed for srcmac; the legal drafts gain
  the rows (2.15, 2.16). A PDF test documents the behaviour (3.5).
- The observed endpoint and the lease-evidence card return counts and the firewall's own
  addresses only.
- Settings changes are audited as today.

**D22 — Ingest stays within its gate:** at most 5 % slower than the Wave 2 tree on
`bench_throughput.py --repeat 3`, on the load-generator day, and on a FortiGate-only spool
(section 3.8). The FortiGate-only spool is written **once** with the Wave 3 tool and loaded by
both trees through the same harness (`bench_throughput.py --spool <dir>`, added in Wave 3 and
copied unchanged into the Wave 2 worktree for the measurement), because the benchmark and its
mixes live in the tree being measured. The parser adds:
- one dictionary test per event line;
- one MAC normalisation per traffic line that carries srcmac, cached like `_ADDRESSES`;
- one boolean (`device_time`) per FortiGate DHCP line.

**D23 — One ADR** (`docs/adr/0016-fortigate-dhcp-and-internet-exits.md`) records D1–D33 with
their alternatives, the SPEC 9.1 deviation of D10, the round-3 changes (D5 cross-table ACKs,
the D6 lease end and the `low` past half a lease) and the round-4 changes (private or shared NAT
addresses only as double-NAT evidence and unclaimed candidates, the mode `internal`, the
per-lookup clock check, the MikroTik variant of D33) and the round-5 fixes (the last DHCP
evidence at the requested moment, the D15 own set without `isp_nat` exits that have no public
address, the clock measurement of an empty search, the lowering of an `fg_dhcp` chain after
unconverted FortiGate DHCP lines) and the round-6 fixes (D33 keyed on the configured network and
`low` outside every configured network, the post-lease check for every DHCP chain by IP or MAC,
the D15 own set without exits on other declared interfaces, the fallback of an empty search on a
P seen beside double-NAT evidence, and the lowering of FortiGate leases at a rollback) and the
round-7 fix (the post-lease check on converted ACKs of the chain's MAC for another address, for
leases built before their network was entered).

### 0.2 Owner decisions (the defaults below are implemented; changing one is a small follow-up)

| | Question | Default implemented | Alternative |
|---|---|---|---|
| O1 | Are FortiGate-only sites supported? | yes, with the FortiGate as DHCP server (D1); statics end at the internal IP | write "not supported" into the offer |
| O2 | May srcmac name the MAC in a chain? | yes, only on listed interfaces whose gateway is the FortiGate (never routed segments; with L2 switches in between only after the two-client check), `low`, labelled partial, never a lease; recorded as a SPEC 9.1 deviation (D10) | never; or open medium intervals (a larger SPEC 9.1 deviation) |
| O3 | Convert FortiGate DHCP lines loaded before the upgrade? | no; the lookup says they exist when no chain is built, and lowers a DHCP chain that such a line of its IP or MAC may contradict (D8) | a one-off re-parse (as part of roadmap 2.2) |
| O4 | Address that no exit claims | observed as `transip` near T: answered as the FortiGate's own address with a warning; otherwise searched in exits without a public address or marked `public_dynamic`, with a warning; `none` when there is none (D14, D31) | always `none`; or every unclaimed address goes to all `isp_nat` exits (as today's engine) |
| O5 | An `isp_nat` exit whose transips are not seen near T | widen: the exit's transips **and** the unclaimed private or shared source-NAT addresses (∪, as Wave 2's `wan_transip_not_seen`), on the exit's interfaces when it declares any, with `egress_transip_not_seen` | `none`; or the unclaimed addresses only |
| O6 | Legacy keys | converted; administrator rows kept unread for one version so a rollback without a database restore keeps its NAT values; deleted by the first revision after Wave 3 (D12, D18) | deleted at conversion (rollback then needs `-RestoreDatabase`) |
| O7 | IPv6 in exits | refused (`setting_ipv6_not_supported`) until roadmap 3.2 | accepted but unused |
| O8 | Re-authentication for `nat.*`, `dhcp.*`, `lookup.srcmac_*` | no (as today) | add them to `REAUTH_KEYS` |
| O9 | Exits valid from/to a date | no; each outcome records the setting's version and warns `egress_changed_after_event` when the exits changed after T (D32) | resolve the exits in force at T from the `settings.change` history; or `valid_from`/`valid_to` per exit |
| O10 | Logged FortiGate lease time | used (D6) | always the configured times |
| O11 | DEVICE-SETUP language | the new sections are bilingual inside the Turkish file | a separate English device guide |
| O12 | Private WAN address convention in examples | `100.64.12.9` / `100.64.13.9` (RFC 6598) | RFC 5737 addresses |
| O13 | DHCP servers other than MikroTik and FortiGate (e.g. Windows) | not read; leases only through MikroTik ARP-LOG at `medium`, as today (KURULUM §0.1); roadmap "MikroTik dışı DHCP" stays an open scope decision | a later roadmap item for another DHCP source; or "not supported" in the offer |
| O14 | Does traffic keep a FortiGate lease alive? | no (`dhcp.fortigate_traffic_extends = false`), loud when renewals seem missing (D26) | yes, the RouterOS rule for both tables |
| O15 | A FortiGate ACK of a MAC for another IP | closes that MAC's other open `fg_dhcp` interval of the same origin, interface and configured network (by subnet, with or without a VLAN id), and its open `mt_dhcp` interval on another IP of the same network (D33); nothing when the IP lies in no configured network, whose `fg_dhcp` chains are then `low`; a MikroTik ACK closes nothing this way | also for MikroTik ACKs (needs the RouterOS DECLINE behaviour checked on site) |
| O16 | What makes two DHCP authorities | two origins `(table, vd)`; the sender and `devid` are not compared, so an HA pair is one authority (D27) | also compare the sender (HA members sending from their own addresses then raise the alert) |
| O17 | Fallback of an upgraded site without interfaces | exits as converted by v0018 keep Wave 2's narrowing to `nat.wan_transip` (D15) | treat converted exits like new ones (probe C fixed at once, the bridge-switch case answers `none`) |
| O18 | Confidence of a FortiGate chain more than half a lease after its last DHCP evidence, no renewal seen | `low` with `lease_renewal_not_seen` (D6) | `medium` (reads as "probably the holder", although under D26's premise the missing renewal speaks against the holder); or `high` with a warning (round 2) |
| O19 | End of a FortiGate lease whose lease time is logged | the logged lease after the last DHCP evidence, the FortiGate's own end (D6) | lease × `lease.timeout_percent` (bridges one lost renewal line, but names the former client past the FortiGate's own end) |
| O20 | `R20-lease:none:mt_dhcp` (MikroTik DHCP rows stopped) | medium, and only while no ARP-LOG row arrives either (D30): a quiet period looks the same; `fg_dhcp` and `mt_arp` are high, each table on its own | high (an e-mail on most quiet weekends); or one aggregate cause (silent while another table still delivers) |
| O21 | An unclaimed **public** NAT address (an IP pool, an unconfigured second line) | the FortiGate's own address: answered as observed when asked, never double-NAT evidence for another exit and never an unclaimed candidate (D15, 2.8.3) | searched as an unclaimed candidate, as Wave 2 did (a quiet direct line then lists the pool's sessions as double-NAT candidates) |
| O22 | A cited source's clock off at the requested moment | measured per lookup from the stored times; the chains are `low` with `source_clock_offset` (2.7) | shift the search window by the measured offset (a correction that assumes the offset was constant over the window) |

### 0.3 Corrections to the research applied

1. **ACK logid.** The lease report rates `0100026001` "likely, never seen". A Fortinet community
   technical tip shows a real 26001 line, received through an automation e-mail, with
   `logid="0100026001"` (F6). The ACK logid is therefore confirmed. The RELEASE logid
   `0100026002` is still only derived from the documented layout.
2. **One typed row per line.** The Fortinet notes say a DHCP line "also stays in `messages` and
   `fg_event`". It stays in `messages` only: `fg_dhcp` replaces `fg_event` for that line
   (`fortigate.py:5`, the kinds contract).
3. **No end reason `expired`** (Fortinet notes). The existing `timeout` covers it (D6).
4. **`trandisp` and `transip` narrowing.** The NAT notes' "do not also require `trandisp='snat'`
   when filtering by exit transips" is already the code (`sessions.py:318`:
   `snat_only and transip is None`) and is kept.
5. **The migration needs a catch-all exit.** The NAT notes convert `nat.public_ips` only into
   `direct` exits. An upgraded site with `nat.public_ips` set and `nat.wan_transip` empty would
   then answer `none` for every other public IP, where it gave candidates before. The converter
   adds the catch-all exit (2.8.2).
6. **Fallback of a quiet `direct` exit.** The NAT notes' "fallback excludes all other exits"
   would change today's answer for `nat.public_ips` plus `nat.wan_transip`, which narrows the
   fallback to the WAN address. The first version of this contract sent the fallback to the
   exits without a public address first; review showed that this lists another line's sessions
   on a dual-WAN site (hat-2's `T2` for a question about a double-NATed hat-1). The fallback now
   searches the unclaimed source-NAT addresses together with the exit's own address, and enters
   an exit without a public address only when it shares an interface with the direct exit, or
   when neither declares interfaces and both are as converted by v0018, which keeps legacy case
   (d) (D15, 2.8.3, E-c2, E-j).
7. **The log check's "own" set** must include the `direct` exits' public addresses, not only
   every `transip` (NAT notes D4), and must leave out the `transips` of the exits the fallback
   falls into; otherwise legacy case (d) answers a false "no session" for a moment before a
   switch to bridge mode (D15). Round 5 widens the second part to every other `isp_nat` exit
   without a public address that declared, disjoint interfaces do not separate from the exit;
   round 6 also leaves out, for an exit that declares interfaces, every other exit whose declared
   interfaces are disjoint from its own (a row on the exit's interface is its line, whoever
   claims the address elsewhere).
8. **The baseline's table list is frozen.** `v0001_baseline.py:45` has its own literal
   `TIME_SERIES_TABLES`, so appending `FgDhcp` to the model list does not change the baseline.
   `v0017` creates its partition or hypertable itself and appends to the `db.timescale` status
   (`v0001:1413-1422`).
9. **Golden names.** The golden PC's DHCP host name is `PLN-PC14` (`lookup_testing.PC_HOSTNAME`)
   and the device name is `Planlama-PC-14` (`DEVICE_NAME`). Earlier text mixed the two.
10. **`detect_type`.** An unknown sender that sends FortiGate DHCP lines is detected as
    `fortigate`, because they contain `logid=`. `detect_type` stays as it is, but its rows are
    not lease evidence (D24): the first version's "Nothing changes there" missed that this
    opened a path for an unconfigured host to create `high` leases.
11. **Settings audit target.** `settings_admin.change_setting` writes `target = "setting:<key>"`
    with `details.old`/`details.new` (`settings_admin.py:83-90`); the v0018 audit entry uses the
    same form, not `target = "nat.egresses"`.
12. **F11 source.** The community article "How to display the source MAC addresses of 'devices'
    in logs in FortiAnalyzer" is topic 98534; it is saved as `fgres/cm/faz196890.html`. It says
    "The device must be connected directly. If there is a switch in between, the user may see the
    MAC address entry of the switch"; the help texts no longer say "same L2 network" as if that
    were enough (2.5).

---

## 1. Facts this contract rests on

### 1.1 FortiOS (public Fortinet documentation, read 2026-10-27; pages saved under `scratchpad/fgres/`)

| # | Fact | Source | Confidence |
|---|---|---|---|
| F1 | 26001 `LOG_ID_DHCP_ACK` ("DHCP Ack log", Event, system, Information). Fields: date, devid, `dhcp_msg` (string 4096), eventtime, `hostname` (string 128), `interface` (string 32), `ip` (ip 39), `lease` ("DHCP lease time", uint32), level, logdesc, logid (string 10), `mac` ("MAC Address", string 17), msg, subtype, time, type, tz, vd | docs.fortinet.com/document/fortigate/7.6.7/fortios-log-message-reference/26001/26001-log-id-dhcp-ack | confirmed |
| F2 | The 26001 field list is the same from 6.4.0 to 7.6.7; 6.2.0 has the same fields; 6.0.8 lacks `tz`; 8.0.2 adds `vendor` (string 64, no description) | the same page under 6.0.8, 6.2.0, 6.4.0, 7.0.0, 7.2.0, 7.4.8, 8.0.2 | confirmed |
| F3 | 26002 `LOG_ID_DHCP_RELEASE`: the fields of 26001 without `lease`. No message text, no `dhcp_msg` value and no real sample is known | …/7.6.7/…/26002/26002-log-id-dhcp-release | fields confirmed; values unconfirmed |
| F4 | Other DHCP messages: 26003 STAT (interface, used, total); 26004 client lease (no ip/mac); 26005/26006 lease usage; 26007 BLOCKED_MAC (mac, no ip); 26008–26010 DDNS (ip, no mac); 26011/26012 DHCPv6 (duid, iaid, ip, lease, no mac). There is no message for lease expiry or for an administrator's revoke | …/7.6.7/…/26003 … 26012; administration guide 38214 "DHCP monitor" | confirmed |
| F5 | The logid is 10 digits: two for the type (event `01`), two for the subtype (system `00`), six for the message ID. 26001 is therefore `0100026001` and 26002 `0100026002` | …/7.6.7/…/34314/log-id-numbers; …/656858/log-id-definitions | layout confirmed; `0100026002` derived (likely) |
| F6 | A real 26001 line (Fortinet community technical tip, delivered by an automation e-mail): `logid="0100026001" type="event" subtype="system" level="information" vd="root" logdesc="DHCP Ack log" interface="internal" dhcp_msg="Ack" mac="<upper case, colons>" ip=<unquoted> lease=604800 hostname="…" msg="DHCP server sends a DHCPACK"`. The MAC is upper case; `ip` and `lease` are unquoted. The field order on the syslog wire is not proven | community.fortinet.com/fortigate-3/technical-tip-email-notification-for-dhcp-ip-lease-146254 | confirmed as a sample |
| F7 | A Fortinet staff member: 26001 "is logged by FortiOS whenever it does a DHCP ACK". Renewals end with a DHCPACK, so they are probably logged too (unlike RouterOS) | community.fortinet.com/support-forum-92/…-111355 | likely |
| F8 | `config system dhcp server` has no logging switch. `lease-time` is in seconds, default 604800, range 300–8640000, `0` = unlimited. The server can run in relay mode and has `server-type regular\|ipsec` | FortiOS 7.6.7 CLI Reference PDF p.1472–1477 | confirmed |
| F9 | `config log eventfilter`: `event` and `system` default `enable`. `config log syslogd filter`: `severity` default `information`, with no toggle for event categories except free-style filters | CLI Reference pp.822–824, 917–919 | confirmed (that DHCP falls under `system` is inferred from "Category: system") |
| F10 | Forward traffic logs define `srcmac` ("MAC address associated with the Source IP", string 17), `mastersrcmac` and `srcmacvendor` (the last since 7.2.11/7.4.0, without description) | …/7.6.7/…/13/13-log-id-traffic-end-forward | confirmed |
| F11 | The source MAC is empty in logs by default and needs device detection on the interface. "The device must be connected directly. If there is a switch in between, the user may see the MAC address entry of the switch in the logs." Behind a router every client shows the router's MAC; staff call device-identification data "only cosmetic" (not used for policy decisions); an L3 switch hides the clients' MACs | community technical tip 98534 "How to display the source MAC addresses of 'devices' in logs in FortiAnalyzer" (saved as `cm/faz196890.html`); support forum 155540 (`cm/user155540.html`), 201683 (`cm/l3sw201683.html`) | confirmed (quotes); the switch case is "may", so it is verified on site (V8) |
| F12 | `config system interface` / `device-identification` default `disable`; device detection "is intended for devices directly connected to your LAN and DMZ ports" | CLI Reference p.1648; administration guide 103514 | confirmed |
| F13 | `trandisp` "NAT translation type" (string 16), `transip` "NAT Source IP", `transport` "NAT Source Protocol Port", `dstintf` "Destination Interface", `centralnatid` "central-snat-map id". The values of `trandisp` are not listed anywhere; only `snat` and `noop` have been seen | …/13-log-id-traffic-end-forward; administration guide 986892 (samples) | confirmed; value list unconfirmed |
| F14 | SD-WAN: `dstintf` is the member the session left by; `vwlid`/`vwlname` name the rule; `dstintf` is not updated when the reply comes back on another member | community technical tip 185298 | likely |
| F15 | Central SNAT: `port-preserve` is the default, but only source ports 5117–65533 are kept | administration guide 421028 | confirmed |
| F16 | The real site samples show `transport` equal to `srcport`, `dstintf="wan1"`, and `waninfo` naming `wan1` and `wan2` | scratchpad SPEC.md §2.5 | confirmed |
| F17 | 43667 wireless "DHCP ACK" has `stamac` but no `ip`, so it is not lease evidence | …/7.6.7/…/43667 | confirmed |
| F18 | DHCP relay is set on the interface: `config system interface` → `set dhcp-relay-service [disable\|enable]` (default `disable`, "allowing this interface to act as a DHCP relay"), with `dhcp-relay-ip` and `dhcp-relay-type`. The `config system dhcp server` table has `status`, `interface`, `server-type` and `relay-agent` | FortiOS 7.6.7 CLI Reference PDF p.1633, p.1650; p.1478 | confirmed |
| F19 | The forward traffic log defines `srcname`, `osname`, `devtype`, `srcfamily`, `srchwvendor`, `srchwversion`, `srcswversion`, `unauthuser`, `unauthusersource` besides `srcmac`, `mastersrcmac`, `srcmacvendor`. A Fortinet staff member: this is "information gathered via device-identification (looking at SMB/Kerberos/DHCP/etc traffic to identify device/version/user/etc.)" | `dl/t13-7.6.7.html.names` (field list of …/13-log-id-traffic-end-forward); support forum 155540 | field list confirmed; which fields a given unit fills is verified on site (V8) |
| F20 | `config system dhcp server`: `conflicted-ip-timeout` "Time in seconds to wait after a conflicted IP address is removed from the DHCP range before it can be reused" (default 1800); `ipsec-lease-hold` "DHCP over IPsec leases expire this many seconds after tunnel down (0 to disable forced-expiry)" (default 60); `server-type regular\|ipsec`. No log record of a conflict is documented (F4) | FortiOS 7.6.7 CLI Reference PDF p.1475–1478 | confirmed |
| F21 | `config system ntp`: `set ntpsync [enable\|disable]` "Enable/disable setting the FortiGate system time by synchronizing with an NTP Server" (disabled when unset, enabled after a factory reset), `set type [fortiguard\|custom]`, `config ntpserver` → `set server`, `syncinterval` 1–1440 min; `diagnose sys ntp status` "List NTP servers information." | FortiOS 7.6.7 CLI Reference PDF p.1888–1889, p.3464 | confirmed |
| F22 | "You can configure one or more DHCP servers on any FortiGate interface"; "If an interface is connected to multiple networks through routers, you can add a DHCP server for each network … The routers must be configured for DHCP relay"; the server table has `relay-agent` ("Relay agent IP") | administration guide 783526 "DHCP servers and relays" (`dl/ag767_783526_dhcp-servers-and-relays.html`); CLI Reference p.1478 | confirmed; what `interface` such a relayed client's 26001 line carries is verified on site (V6) |

### 1.2 Repository (HEAD `9a9c6a2`, Wave 2 merged)

| Fact | Where |
|---|---|
| The FortiGate parser maps `(event, *)` to `fg_event` (columns logid, subtype, level, logdesc, action, status, user_name, ui, srcip, dstip, cfg*, msg); every other key goes to `extra` as text; one line gives one `messages` row and at most one typed row | `ingest/parsers/fortigate.py:5-20, 200-236, 379-430` |
| `map_fields` kinds: text, integer (`smallint`/`integer`/`bigint`), inet; an empty value is NULL and kept in `extra`; a value that does not convert goes back to `extra` and is reported as `fortigate.value` | `fortigate.py:379-430` |
| `FortiGateParser.tables` is hard-coded; `_verdict` is the order glued, unterminated, unparsed, type, value, time | `fortigate.py:439-441, 638-655` |
| `to_mac` accepts `:` or `-` in either case and returns lower case with colons | `ingest/parsers/values.py:29, 62-70` |
| Ingest runs the shipped parsers (`parse_line` without parsers); `build_parsers()` only has a contract test | `ingest/prepare.py:39, 147`; `parsers/__init__.py:41-67` |
| An unknown sender is detected by content: `devname=`/`logid=` means fortigate | `parsers/__init__.py:68-77` |
| `mt_dhcp`: server NOT NULL, vlan, action, ip INET, mac MACADDR, hostname; indexes `(ip,time)`, `(mac,time)` | `db/models.py:459-477` |
| `leases`: `source_table` free text ("mt_dhcp or mt_arp"); no lease-time column; checks on end_reason, confidence, open iff no end | `db/models.py:827-864` |
| `TIME_SERIES_MODELS` drives `TIME_SERIES_TABLES`, kinds' branch order, the search window branches and `partitions.ensure`; the baseline keeps a frozen literal list | `db/models.py:2072-2089`; `v0001_baseline.py:45, 1364-1422`; `partitions.py:46, 109, 151` |
| Storage mode status is the `db.timescale` setting (author `:migration`) | `v0001_baseline.py:1413-1422` |
| Kinds: one kind per typed table; `fg_event` is kind `event`; `CLIENT_IP['fg_event']='srcip'`; the `dhcp` kind is MikroTik (`mt_dhcp` + dhcp `mt_event`) | `db/kinds.py:41-70, 120-326` |
| Search field `mac:` covers `mt_dhcp.mac`, `mt_arp.mac`, `mt_fw.src_mac`, `mt_event.mac` (only the first two indexed); `interface:` covers `fg_traffic` srcintf/dstintf etc. | `api/search/fields.py:872-930` |
| Lease loaders are MikroTik only: `_DHCP_COLUMNS`/`load_dhcp`, `first_evidence_time` (min of `mt_dhcp`, `mt_arp`), `_releases`, the seeds' raw DHCP and held queries, `_ref_key` (substring `" deassigned "`), `_row_key`, the `traffic_ips`/`dhcp_ips` sets | `worker/correlation/leases.py:232-301, 342-349, 527-563, 627-776, 865, 878, 961` |
| The timeline compares `source_table == DHCP_TABLE` in `_traffic`, `_deassigned` and `_assigned`; `_Open.from_interval` keeps continuations only for `DHCP_TABLE`; `evidence_key` orders equal times by row id without the table | `worker/correlation/timeline.py:101-118, 235-258, 443-524` |
| `TimelineRules.alive_window(source_table, server)` is per server only; `Context.load` refuses a timeout shorter than the 300-s traffic bucket | `timeline.py:147-178`; `leases.py:191-212` |
| Watermark feeding sources loop over `(MtDhcp, MtArp)`; the lookup data window reads `messages`, `fg_traffic`, `mt_dhcp`, `mt_arp` | `correlation/watermark.py:95-113`; `lookup/window.py:18-25` |
| `refs.DHCP_TABLE = "mt_dhcp"`; `dhcp_ref` summary `"<server> <action> <ip> for <mac>[ <host>]"` | `worker/lookup/refs.py:14-32` |
| `Attributor._open_limit` uses `lease_timeout_seconds(lease.server)`; ARP is detected by `source_table == 'mt_arp'`; `StoredLease.info()` has no `source_table` | `lookup/attribution.py:54-100, 212-246` |
| `LookupSettings`: `public_ips`, one `wan_transip`; `double_nat = not public_ips`; `exact_addresses()` = both | `lookup/types.py:671-734` |
| Engine: the mode choice, the candidate narrowing to `nat.wan_transip` with no note, `_transip_seen` whose own set is `{address} ∪ public_ips`, the port hint read with `get_setting("lookup.port_hint")`, `chain=None` without an explanation | `lookup/engine.py:198-211, 381-430, 501-547, 612-621` |
| `build_query` filters `trandisp = 'snat'` only when no `transip` is given; `exclude_transips` | `lookup/sessions.py:285-346` |
| Report cross-check: `PARSER_OF_TABLE` (fg_traffic/web/app/dns, mt_dhcp, mt_arp); `_lease_claims` skips other tables | `api/reports/consistency.py:40-48, 95-110` |
| Settings: defaults `db/settings.py:47-92`; `SettingValueError(key, code, text, **facts)` with `SETTING_ERRORS[code]` (messages never repeat a value); `validate_setting` key branches; `INTEGER_RANGES`; `REAUTH_KEYS`; authors `:seed`, `:system`, `:migration` | `db/settings.py:43-45, 287-450, 555-632` |
| `SettingType` is inferred from the default; an object is edited as raw JSON unless the key has its own editor (`ServerSecondsEditor`) | `db/settings_meta.py:24-78`; `web/src/pages/settings/setting-editors.tsx:485-498` |
| Docs test: every `DEFAULT_SETTINGS` key with prefix `nat.`, `lookup.`, `lease.`, `dhcp.`, `arp.`, `devices.`, `worker.`, `db.` must be named in `CORRELATION.md` | `tests/test_docs_consistency.py:715-723` |
| `append_audit(session, action=…, details=…)` works in any session (the seed uses it) | `db/audit.py:253-270`; `db/seed.py:31, 60-77` |
| Worker test helper `Evidence` writes the raw spool line and the row; `dhcp()` is MikroTik `core-r1`; there is no FortiGate DHCP helper; `traffic()` writes no `srcintf`/`dstintf` | `worker/testing.py:36-240` |
| Golden chain: WAN `100.64.12.9:51001` → `172.20.42.57` (`00:00:5e:00:53:57`, host `PLN-PC14`) → device `Planlama-PC-14` → Selin Kaya / Planlama | `api/lookup_testing.py:40-56` |
| Explanation tones: the web maps keys to `warning`/`danger`/`success`, otherwise `info` | `web/src/pages/lookup/chain-text.ts:11-31` |
| Ingest Wave 2 gate: `bench_throughput.py --repeat 3` and the load-generator day, ≤ 5 % (medians recorded) | `docs/INGEST.md:284-330` |
| The load generator has no topology option; its default bytes are pinned by a seed digest | `tools/loadgen/arcilogger_loadgen.py:1391-1457`; `tests/test_loadgen.py:63-86` |
| Probes: on a FortiGate-only site, rebuild gives 0 leases and 0 devices, internal mode `no_lease`, candidates have `chain=None` with only `double_nat`; two double-NAT WANs with `nat.wan_transip`=WAN-1 list only WAN-1 for ISP-2's IP; a quiet bridge WAN-1 next to an active WAN-2 falls back and lists WAN-2 | scratchpad `w3lease/probe_tests/test_fg_only_probe.py`, `natprobe/test_dual_wan_probe.py` |
| Sources: the collector files a sender that is not configured under `unknown-<ip>`; ingest picks its parser by content and flags it `unknown` ("yeni log kaynağı"); a removed source keeps its row, `enabled = false, expected = false`, and keeps its `type`; `feeding_sources` takes FortiGate sources with `type='fortigate'`, enabled, not unknown | `ingest/sources.py:1-79`; `ingest/service.py:428-470`; `ingest/loader.py:380-440`; `watermark.py:95-113` |
| The timeline's rule 3 ("RouterOS logs no renewals, so … traffic from the IP extends it") is the reason traffic extends DHCP intervals; `_traffic` extends every open `DHCP_TABLE` interval of the IP; an IP conflict is only seen when two intervals overlap | `timeline.py:22-26, 443-447`; `attribution.py:308-326` |
| `change_setting` audits `settings.change`, target `setting:<key>`, details `old`/`new`; `set_setting` does not validate (tests store `"not an address"`); `validate_setting` has no cross-key rule and no length limit for `nat.public_ips` | `db/settings_admin.py:63-90`; `db/settings.py:587-590`; `api/tests/test_lookup_api.py:550` |
| The seed runs after every `upgrade` and writes audit entries with `append_audit` (`license.trial_start`); no migration writes an audit entry | `db/seed.py:1-80`; `migrations/versions/*` |
| `LookupSettings.from_settings` parses the NAT keys and is called by the lease job, the usage subjects, the engine and the lookup router, so a bad NAT value stops the lease job too | `lookup/types.py:710-734`; `correlation/leases.py:194`; `usage/subjects.py:68`; `routers/lookup.py:640` |
| Services do not check the schema revision; `-Rollback` without `-RestoreDatabase` runs the previous version on the new schema; `get_setting` falls back to the shipped default for a missing row; per-revision upgrade notes live in KURULUM §13 | `db/migrate.py`; `docs/KURULUM.md` §13; `db/settings.py:252-259` |
| The PDF lease row prints MAC, host name, start, end/"açık", end reason, confidence and evidence only; `DOUBLE_NAT_TR` states "FortiGate'in WAN arayüzü ISS modeminin arkasındadır" as a fact and is printed for every `double_nat` answer in candidates/none mode, with the advice "Ayarlar → NAT: genel IP" | `reports/render.py:416-425, 568-580`; `reports/texts.py:63-105` |
| `test_parser_contract.py` compares every typed row's keys with the model's columns; `test_kinds.py` requires every typed table to be named by a kind and every table with an INET column to be in `CLIENT_IP` | `ingest/tests/test_parser_contract.py:194-214`; `db/tests/test_kinds.py:113-163` |
| `LookupOutcome` JSON carries every dataclass field; `api-samples.test.ts` rejects a sample key that the TypeScript type does not declare | `lookup/types.py:168-181`; `web/src/lib/api-samples.test.ts:99, 173-216` |
| The settings router, `GET /settings` included, requires `settings.manage`; there is no `settings.read` | `api/routers/settings.py:47` |
| `alerts.email_min_severity` defaults to `high`; R15 is `high` | `db/settings.py:123`; `alerts/r15.py:1` |
| `leases` has indexes `(ip, start)` and `(mac, start)` only; `fg_event` has `(subtype, time)` | `db/models.py:434, 836-837` |
| KURULUM §0.1 bullets "Tek yerleşke", "Kişi ve cihaz eşleştirmesi" (non-MikroTik DHCP + MikroTik ARP-LOG = `medium`), "NAT" (IPv6 sentence inside), "Kullanım analizi", "Başka üreticilerin cihazları"; KURULUM §16 "Sahada doğrulanacaklar"; DEVICE-SETUP §2 has no subsections and the placeholder `<firewall>`; texts cite "DEVICE-SETUP.md bölüm N" | `docs/KURULUM.md:73-119, 1109`; `docs/DEVICE-SETUP.md:13, 84-112`; `alerts/texts.py:214` |
| The privacy-notice draft names only "MikroTik yönlendirici (DHCP sunucusu)" as the source of access records and lists no MAC or device name for traffic records | `docs/legal/aydinlatma-metni-taslak.md:35-36` |
| The `dhcp` kind is labelled "DHCP" (MikroTik family) | `db/kinds.py:206-214` |
| The settings files that set the legacy NAT keys also include `db/tests/test_settings.py`, `web/scripts/live_stack.py` (live smoke), `web/src/pages/settings/setting-values.test.ts`, the `cli.py:45` docstring, `docs/DATA-MODEL.md:272`, `docs/MVP-CONTRACTS.md:174-188`, `docs/ILK-ADIMLAR.md:235-254`; `api/search/testing.py` maps every typed table (`MESSAGE_TYPES`, `REQUIRED`) | `grep -rl "nat.public_ips\|nat.wan_transip\|lookup.port_hint"`; `api/search/testing.py:28-67` |
| *(second review round)* `_assigned` closes only other open intervals of the **same IP**; a MAC's interval on another IP stays open | `timeline.py:492-524` |
| *(third review round)* `_assigned` continues any open DHCP interval of the pair (`current.source_table == DHCP_TABLE`) and keeps its `server`; `feed` expires only the event's IP (`_expire_ip(event.ip, …)`); `_closes` ends a seed at an `assigned` of its own MAC only when the seed is ARP | `timeline.py:332-346, 381-392, 492-504`; `leases.py:612-624` |
| *(third review round)* Sessions are grouped by `TrafficRow.key` = (sessionid, srcip, srcport, dstip, dstport, proto, **transip, transport**) | `lookup/sessions.py:136-148` |
| *(third review round)* The lookup router maps `(TypeError, ValueError)` to 503 only around `LookupSettings.from_settings`; `run_lookup` runs outside it and only `LookupTimeoutError`/`DBAPIError` are caught there, so any other error is a 500 | `api/routers/lookup.py:638-666` |
| *(third review round)* `timeline.py:54` imports `lookup.refs`, which runs `lookup/__init__`, which imports `engine` eagerly; `engine` imports `attribution`. A correlation module imported from `attribution` that imports `timeline` closes a cycle when `timeline` is imported first | `correlation/timeline.py:54`; `lookup/__init__.py:3`; `lookup/engine.py:75` |
| *(third review round)* `run_system_checks` runs each check in its own transaction and records an exception per check, so one exception loses every finding of that check | `alerts/system.py:902-925` |
| *(third review round)* `explain()` formats `text_tr` and `text_en` with the same params (`str.format` ignores unused names); no test compares their placeholders | `lookup/texts.py:265-273`; `worker/tests/test_devices_oui.py:128-148` |
| *(third review round)* The Security page summary is `facts()` of the describer before the title | `api/routers/alerts.py:233-240` |
| *(third review round)* The live tail's kind comes from the message's type/subtype, and Arama shows the kind, so `fg_dhcp` and `fg_event` rows look the same there; `LiveLine` has no table | `api/live/hub.py:437`; `web/src/pages/live/line-sheet.tsx:28-36`; `web/src/pages/search/results-table.tsx:79-84` |
| *(third review round)* `MODEL_OR_RELEASE` matches the word FortiGate followed by a space and a logid such as 0100026001 (`Forti(Gate…)\s+[A-Za-z]{0,4}-?\d{2,4}…`) | `tests/test_docs_consistency.py:1399-1404` |
| *(third review round)* A `none` outcome prints `MODE_DETAIL_TR['none']` "Ölçütlere uyan kayıt bulunamadı …" in section 4 and the same sentence in section 5 | `reports/texts.py:234`; `reports/render.py:409, 451-462` |
| *(third review round)* `NatMatch` carries no explanations | `lookup/types.py:561` |
| *(third review round)* An open alert's severity only goes up; a dismissed or resolved alert is never re-opened, the next occurrence is a new alert | `alerts/store.py:1-20, 133-216` |
| *(third review round)* `sources.address` is an `INET` (an address or a network); `unknown-<ip>` names the sender | `db/models.py:603-626`; `ingest/sources.py:1-79` |
| *(third review round)* ILK-ADIMLAR/FIRST-STEPS §8 is the trial lookup, with Makam "DENEME" | `docs/ILK-ADIMLAR.md:293-299` |
| Incremental lease runs seed only leases with `end IS NULL` and load traffic only for the IPs of open DHCP seeds and new ACKs; `_seeds` decides a seed's closing only from DHCP rows of its own IP | `leases.py:933-975, 612-625, 655-690` |
| `search_counts` is written by the ingest loader for every loaded row (refused and unknown-sender rows included); the lease job's refusals happen later, in the worker | `db/models.py:1887-1932`; `ingest/loader.py:443` |
| Alert e-mails and the Security page read `RULES`, `RUNBOOKS` (`runbook(rule)`) and `DESCRIBERS` by **rule**; an unknown rule gets `UNKNOWN_RULE`/`UNKNOWN_RUNBOOK`; titles are Turkish with `details.title_en` (`R20-parse`); `R20-seal-config` maps `details.cause` through `CONFIG_CAUSES`; e-mails show `details.source` | `alerts/texts.py:59-245, 250-600, 997-1034`; `alerts/system.py:576-605`; `routers/alerts.py:239-379` |
| `tests/test_docs_consistency.py::test_first_steps_explain_every_alert_code` collects every constant `R\d{2}-[a-z]+(-[a-z]+)*` of `*/src` and requires it in ILK-ADIMLAR/FIRST-STEPS §9; `ALERT_MAIL_IP_CASES` pins which describers may print an IP | `tests/test_docs_consistency.py:298-334, 968-1216` |
| Pinned Wave 2 counts: `HEAD = "0016_search"`; "15 tablo" and `tables == 15`; `mac:` covers exactly `mt_dhcp, mt_arp, mt_fw, mt_event`; `CONTRACT_FIELDS`/`CONTRACT_INDEXED` list every field and alias; the search guides list exactly the catalogue's fields (§3) and kind labels (§6) | `db/tests/test_migrations.py:33`; `db/tests/test_cli.py:104-149`; `db/tests/test_partitions.py:130`; `api/tests/test_search_planner.py:66`; `api/tests/test_search_fields.py:60-90, 220-226`; `tests/test_search_docs.py:74-95` |
| `ENGINE_EXPLANATIONS` are formatted eagerly (`text.format(**shown)`): a new placeholder without a caller passing it raises `KeyError`; `test_devices_oui.py` pins the `no_lease` wording "ana yönlendiriciden (MikroTik, DHCP sunucusu)" | `lookup/texts.py:265-273`; `engine.py:422`; `worker/tests/test_devices_oui.py:129-141` |
| The PDF prints the raw line of every cited row (traffic refs of every match, chain refs) up to `RAW_DISPLAY_CHARS` | `reports/inputs.py:120-130`; `reports/render.py:673-710` |
| `PATCH /settings` 422 details are `{key, code, message_tr, message_en}`, no field path | `routers/settings.py:211-226`; `db/settings.py:371-387` |
| `Kind` has key and labels only (no help text) | `db/kinds.py:83-97` |
| `logid:` covers `_FG_ALL` (the FortiGate tables without `fg_dhcp`); `level:` covers `fg_event`, `mt_event` | `api/search/fields.py:392, 979-990` |
| The settings page has no NAT page: the NAT keys are in the group "5651 sorgu ve kira zaman çizelgesi" of Uygulama ayarları; the 503 text still says "(Ayarlar → NAT)" | `db/settings_meta.py:36-45, 240-252`; `api/routers/lookup.py:106-107` |
| `-Rollback` runs no schema downgrade; `-RestoreDatabase` loads the pre-upgrade dump | `docs/KURULUM.md:985-996`; no `downgrade` call under `deploy/` |
| Wave 2's own set is `{address} ∪ nat.public_ips` (W is not in it), and its fallback without `nat.wan_transip` searches every `snat` session except the *other* public IPs, the requested one included | `engine.py:33-43, 381-405, 501-547`; `test_lookup_engine.py:275-284` |
| *(fourth review round)* A superseded interval keeps the line that ended it as evidence: `_assigned` calls `_close(other, event.time, "superseded", event.ref)` and `interval(…, closing)` appends it; the seed path does the same (`_closed_by`: `bounded(state, dhcp_ref(row))`); `_close` does not move `last`, so the stored `last_evidence` of a superseded lease is its own | `timeline.py:270-300, 403-415, 504`; `leases.py:759-776`; `reports/consistency.py:95-110` |
| *(fourth review round)* `_seeds` rebuilds a seed's state at midnight with `last = max(start, every stored ref, every found row)`, the stored latest traffic ref and the latest traffic row included | `leases.py:700-732` |
| *(fourth review round)* Every time-series row keeps `time` (device time) and `time_received` (collector receive time) for the retention period; `messages` has `ix_messages_source_time (source, time)`; ingest accepts a device time up to `max_clock_skew` (one day) from the receive time | `db/models.py:169-212`; `parsers/fortigate.py` docstring; `parsers/generic.py:31` |
| *(fourth review round)* `search_counts` is keyed by `(hour, source, raw_day, kind, tbl, …)` with `ix_search_counts_raw_day_source`; `raw_day` is the receive day of the spool file | `db/models.py:1885-1931` |
| *(fourth review round)* Wave 2's engine tests use the private `100.64.12.9` as the "other" transip of every double-NAT case; a configured public address never fails the check | `worker/tests/test_lookup_engine.py:275-360` |
| *(fourth review round)* The report is rendered and stored on its first download, so a Wave 2 outcome downloaded after the upgrade is rendered by the new renderer | `api/routers/lookup.py:18-20` |
| *(fourth review round)* The lease rebuild is in Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur, at most 31 days per request | `web/src/pages/settings/lease-rebuild.tsx`; `api/routers/admin_actions.py:20` |
| *(fourth review round)* The worker package depends on `arcilogger-core` and `arcilogger-db` only, so a worker test cannot use `api/lookup_testing.build_site` | `worker/pyproject.toml`; `api/src/arcilogger_api/lookup_testing.py:66` |

### 1.3 Verify on site (and the loud check that covers each item until then)

| # | Assumption | Where it is configurable or checked | Loud signal |
|---|---|---|---|
| V1 | The real 26001 line on the syslog wire: logid, field names `ip`/`mac`/`dhcp_msg`/`lease`/`interface`/`hostname`, quoting, MAC case and separator, `hostname` missing or `""` | parser constants; `dhcp.fortigate_logids` | `R20-lease:fortigate-unconverted` (high); `fortigate.dhcp` parse failures; `dhcp.fortigate_format_verified` false → `R20-lease:fortigate-unverified` (info), the card and the PDF say "kayıt biçimi sahada doğrulanmadı" (D29); the real-sample test (3.1) |
| V2 | The RELEASE logid `0100026002`, its `dhcp_msg`/`msg` values, and whether clients' releases are logged at all | `dhcp.fortigate_logids` | `R20-lease:fortigate-refused` with the reason "unmapped" and the logids seen (other logids with the DHCP shape) |
| V3 | Renewals produce 26001 (this decides whether traffic may keep a FortiGate lease alive) | `dhcp.fortigate_traffic_extends` (default `false`, D26) | leases that end without a renewal while their IP stays in use, and the `fg_renewal_ack` count → `R20-lease:fortigate-renewals-missing` (high, no renewal ACK in 7 days before the site check or after renewals stopped) or `fortigate-renewals-not-logged` (info, a verified site that never logged one); `lease_renewal_not_seen` in lookups (the chain is `low`); the DEVICE-SETUP 2.4 step 5 renewal check with its negative branch, its outcome written into KURULUM §16 |
| V4 | `lease` is in seconds; how `0` (unlimited) is written | `dhcp.fortigate_use_logged_lease`; bounds 300–8,640,000 | `R20-lease:fortigate-lease-unlimited` (high, `0`) and `R20-lease:fortigate-lease` (low, out of range); statistics `lease_fallback`, `lease_unlimited` |
| V5 | DHCP event lines reach syslog with the shipped DEVICE-SETUP configuration (severity `information`, no exclude filter) | DEVICE-SETUP 2.4 | `R20-lease:none:<table>` (high for FortiGate DHCP and ARP-LOG, medium for MikroTik DHCP without ARP-LOG; each table on its own) / `absent` (info) on usable evidence, `R20-lease:fortigate-refused` (high) when nearly every row is refused (D30); Kaynaklar "Kira kanıtı" card states `none`, `refused` |
| V6 | Relay mode gives no FortiGate lines; IPsec-type servers' leases end `ipsec-lease-hold` after tunnel down and their MACs are unknown; whether a reply to DHCPINFORM is logged as 26001; which `interface` the 26001 line of a client in a relayed network carries when the FortiGate serves several networks on one interface (F22) | `dhcp.fortigate_interfaces_excluded` (DEVICE-SETUP 2.4 lists IPsec servers' interfaces there); `dhcp.fortigate_dhcp_msgs`; D33 keys on the configured network as well | `R20-lease:none:fg_dhcp` / `absent` (relay: no evidence); `R20-lease:fortigate-dhcp-subnet` (address in no configured network, e.g. a VPN pool; such chains are `low`, `fortigate_dhcp_no_network`); `R20-lease:fortigate-refused` (zero/broadcast/multicast MAC); `R20-lease:fortigate-dhcp-mac` (one MAC ACKed on more than 3 IPs of one interface and network in 24 h) |
| V7 | `vd`/`devid` on HA pairs and multi-VDOM units (interface names) | kept in `extra`; `vd` is part of a row's origin (D27) | `R20-lease:dhcp-sources-overlap:<net>` (two `vd` values in one subnet); `devid` is not compared (HA failover) |
| V8 | srcmac appears only with device detection; it is the client's MAC for directly connected clients, and behind a layer-2 switch only if the two-client check shows it (Fortinet: "if there is a switch in between, the user may see the MAC address entry of the switch"; staff: device identification works properly with "layer-2 connectivity up to the FortiGate", F11); never behind a router or L3 switch; which device-identification fields a unit fills (F19) | `lookup.srcmac_interfaces` (default off), `lookup.srcmac_max_ips` (default 1); DEVICE-SETUP 2.5 two-client check | `R20-lease:srcmac-shared:<interface>`; `srcmac_shared`, `srcmac_other_lease` in lookups; `srcmac_mismatch` makes the chain a `low` conflict |
| V9 | Each WAN's `transip` and `dstintf` (SD-WAN member names); whether a session changes exit between its interim and final logs | `nat.egresses` (mode `internal` for NAT addresses that are not internet exits) | observed endpoint; `egress_unmapped_seen`; `egress_interface_not_seen`; `session_changed_exit`; `R20-nat:unmapped-transip` |
| V10 | `trandisp` values of outbound sessions (central NAT, IP pools) | `nat.snat_trandisp` | `nat_unknown_trandisp`; observed endpoint `unknown_trandisp` |
| V11 | Whether each ISP router keeps source ports | `port_preservation` per exit; DEVICE-SETUP 2.6 measurement | `port_hint` note says "sahada doğrulandı olarak işaretli" |
| V12 | Carrier-grade NAT at the ISP (a public IP shared with other customers) | mode `isp_nat` | the double-NAT method paragraph says the candidate list may not hold the right session (2.12) |
| V13 | `ALTER TABLE fg_traffic ADD COLUMN` on TimescaleDB with compressed chunks (CI image TimescaleDB 2.30.2, PostgreSQL 16) | `test_migration_0017.py` on the Linux TimescaleDB CI job | the migration test (WP-A0, day 1) |
| V14 | The `dhcp_msg` values FortiOS writes under each mapped logid (`Ack` seen once, F6; RELEASE unknown) | `dhcp.fortigate_dhcp_msgs` (D25) | `R20-lease:fortigate-refused` with the reason "unexpected `dhcp_msg`" and the distinct values (trusted rows, cut and masked); the real-sample test asserts them |
| V15 | Whether each line's public IP is static | `public_dynamic` per exit; DEVICE-SETUP 2.6 question | `egress_observed_not_configured`, `egress_public_unknown`, `egress_public_dynamic`, `public_ip_no_egress`; `egress_changed_after_event` |
| V16 | FortiGate DHCP lines carry a usable device time (clock, buffered or forwarded logs), and the device clock is right to within minutes (the FortiGate syncs with NTP, F21) | `fg_dhcp.device_time`; `max_clock_skew` (ingest); DEVICE-SETUP §2 NTP step | `R20-lease:source-clock` (high, a median offset beyond ±120 s on the source's latest records, whatever their device time, D28); `source_clock_offset` in a lookup whose cited source was off at the moment (the chains are `low`, 2.7); `R20-lease:fortigate-refused` with the reason "receive time" (high when nearly all rows are receive-time: a clock more than a day off); `fortigate.time` parse failures |
| V17 | Whether FortiOS logs a DHCPDECLINE, or checks an address (ping or ARP) before it offers it; that a client holds one binding per server interface and network (D33) | D33 rule (no setting) | `R20-lease:fortigate-decline:<net>` (high); `fg_mac_moved` in the lease job report; the DECLINE golden (3.4); `fortigate_dhcp_no_network` (the chain is `low`) outside every configured network |

V1–V6, V8, V11, V14–V17 are also added to the operator checklist KURULUM/INSTALL §16
"Sahada doğrulanacaklar" (WP-F2), not only to CORRELATION §8.

---

## 2. Implementation contract

### 2.1 Components and files (new unless marked)

| Area | Files |
|---|---|
| Data (db) | changed: `db/models.py` (`FgDhcp`, `FgTraffic.srcmac`, `Lease.lease_seconds`, two checks, one index), `kinds.py`, `settings.py`, `settings_meta.py`, `seed.py` (the v0018 audit entry from its status row; the re-upgrade's restore of FortiGate leases, 2.2.1), `settings_admin.py` (the `nat.egresses_changes` record of D32), `cli.py` (`leases prepare-rollback`, 2.2.1); new: `db/egress.py`, `db/lease_rollback.py` (the rollback step and the restore, 2.2.1), `migrations/versions/v0017_fortigate_dhcp.py`, `v0018_nat_egresses.py` |
| Ingest | changed: `ingest/parsers/fortigate.py`, `ingest/testing.py` (benchmark mix), `ingest/tests/bench_throughput.py` (`--spool`) |
| Fixtures | changed: `tests/fixtures/synthetic/fortigate-synthetic.log` (lines appended), `tests/fixtures/synthetic/README.md`, `tests/fixtures/format/README.md`; new: `tests/fixtures/egress-cases.json` (the exit validation cases both `db/tests/test_egress.py` and `web/.../egress-model.test.ts` read, as `format-vectors.json` is shared today) |
| Search | changed: `api/search/fields.py`, `api/search/testing.py` (`fg_dhcp` in `MESSAGE_TYPES`/`REQUIRED`) |
| Lease timeline (worker) | new: `worker/correlation/dhcp_sources.py`, `worker/lookup/trust.py` (leaf: `untrusted_sources`, `kept`, `refusal`, `usable_dhcp_origins`, `srcmac_ip_counts`; 2.6.1); changed: `correlation/timeline.py`, `leases.py`, `watermark.py`, `lookup/refs.py`, `lookup/window.py`, `alerts/system.py`, `alerts/texts.py`, `worker/testing.py` |
| Lookup (worker) | changed: `lookup/types.py`, `engine.py`, `sessions.py`, `attribution.py`, `texts.py` |
| API | changed: `reports/consistency.py`, `reports/texts.py`, `reports/render.py`, `routers/lookup.py` (schemas), `routers/settings.py` (observed endpoint), `sourcehealth.py`, `routers/sources.py`, `lookup_testing.py` |
| Web | new: `pages/settings/egress-editor.tsx`, `egress-model.ts`, `fortigate-observed-panel.tsx`, `components/network/supported-topologies.tsx`, `pages/sources/lease-evidence-card.tsx`, `locales/areas/network/{tr,en}.json`; changed: `setting-editors.tsx`, `settings-validation.ts`, `pages/settings/sources-page.tsx`, `pages/sources/*`, `pages/lookup/*`, `components/evidence/evidence-labels.ts`, `lib/lookup-types.ts`, `lib/sources-types.ts`, `lib/api-types.ts`, `locales/tr.json`, `locales/en.json`, e2e mocks and specs |
| Tools | changed: `tools/loadgen/arcilogger_loadgen.py`, `tools/loadgen/README.md` |
| Installer | changed: `deploy/windows/installer/Install-Arcilogger.ps1` (`Invoke-RollbackMode`: the step `leases.prepare-rollback` of 2.2.1), `tests/test_installer_scripts.py` |
| Docs | new: `docs/adr/0016-fortigate-dhcp-and-internet-exits.md`; changed: see 2.15 (the legal drafts `docs/legal/aydinlatma-metni-taslak.md` and `saklama-imha-politikasi-taslak.md` included) |

### 2.2 Tables, columns and migrations

#### 2.2.1 `v0017_fortigate_dhcp` (revision `0017_fortigate_dhcp`, down revision `0016_search`)

```sql
-- the shared TimeSeries columns exactly as models.TimeSeries declares them
CREATE TABLE fg_dhcp (
  id            bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
  time          timestamptz NOT NULL,
  time_received timestamptz NOT NULL,
  source        text NOT NULL,
  sender        inet NOT NULL,
  logid         text,
  subtype       text,
  level         text,
  logdesc       text,
  dhcp_msg      text,
  interface     text,
  ip            inet NOT NULL,
  mac           macaddr NOT NULL,
  hostname      text,
  lease         bigint,             -- uint32 as logged (seconds, verify on site); NULL when absent
  msg           text,
  device_time   boolean NOT NULL,   -- true: time is the FortiGate's event time; false: the
                                    -- receive time (no usable device time, fortigate.time)
  extra         jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_day       date NOT NULL,
  raw_line      integer NOT NULL,
  raw_offset    bigint NOT NULL,
  CONSTRAINT pk_fg_dhcp PRIMARY KEY (time, id));
CREATE INDEX ix_fg_dhcp_ip_time  ON fg_dhcp (ip, time);
CREATE INDEX ix_fg_dhcp_mac_time ON fg_dhcp (mac, time);
COMMENT ON TABLE fg_dhcp IS 'FortiGate DHCP server events (ACK/RELEASE by logid): which MAC got which IP when.';

ALTER TABLE fg_traffic ADD COLUMN srcmac macaddr;   -- nullable, no default, no index
ALTER TABLE leases ADD COLUMN lease_seconds integer;
ALTER TABLE leases ADD CONSTRAINT ck_leases_lease_seconds
      CHECK (lease_seconds IS NULL OR lease_seconds BETWEEN 300 AND 8640000);
ALTER TABLE leases ADD CONSTRAINT ck_leases_source_table
      CHECK (source_table IN ('mt_dhcp', 'mt_arp', 'fg_dhcp'));
-- open and recently ended leases per source (renewal, decline and dhcp-mac alerts, the card)
CREATE INDEX ix_leases_source_table_end ON leases (source_table, "end");
```

- **Storage mode** is decided as the baseline left it: `messages` is a hypertable means
  TimescaleDB, otherwise native partitions.
  - TimescaleDB: `create_hypertable` for `fg_dhcp` with the baseline's `CHUNK_INTERVAL` and the
    same API switch at 2.13; compression `segmentby source`, `orderby time DESC, id DESC`, and
    the policy `COMPRESS_AFTER`, inside a savepoint exactly like `_hypertables`.
  - Plain PostgreSQL: `PARTITION BY RANGE (time)` with `fg_dhcp_default`; monthly partitions come
    from `partitions.ensure` (automatic through `TIME_SERIES_MODELS`).
  - The migration appends `fg_dhcp` to the `db.timescale` status (`hypertables` or
    `partitioned`).
- **Models:** `FgDhcp` is appended at the **end** of `TIME_SERIES_MODELS`. Search branch indices
  and saved cursors are positional, so nothing existing moves. `FgTraffic.srcmac`,
  `Lease.lease_seconds` and the two checks are added. The model/migration sync test stays green.
- **TimescaleDB with compressed chunks:** `ADD COLUMN` on a nullable column without a default is
  expected to work on TimescaleDB 2.30.2 (V13). WP-A proves it on day 1 on the Linux CI job. If
  it is refused, the fallback is to keep srcmac in `extra` (the parser keeps today's behaviour
  for that key), read it in the worker as `extra->>'srcmac'` through a normalising Python step,
  and record the change as a deviation. The contract interfaces (`TrafficRow.srcmac`, the
  explanations) stay the same.
- **Downgrade:** drops `fg_dhcp` (its rows are lost; the raw lines stay in the spool and the
  vault), the column, the checks, the index and `lease_seconds`, and removes `fg_dhcp` from the
  `db.timescale` status. `leases` rows with
  `source_table = 'fg_dhcp'` are deleted first, because the old code cannot read them.
- **A site rollback runs no downgrade, and one lease step** (2.2.2 step 5; round 6). After
  `-Rollback` without `-RestoreDatabase` on a FortiGate-DHCP site the schema stays at 0018. The
  previous version's attribution treats every `source_table` but `mt_arp` as DHCP and takes a
  lease's stored confidence into the chain (`weakest`), and it cannot read a FortiGate DHCP line
  (its parser files them under `fg_event` again). Left as they are, it keeps an `fg_dhcp` lease
  open for its own configured time (its `alive_window` ignores `lease_seconds`), never ends it at
  the RELEASE or the next client's ACK, re-opens one that this version ended whenever its rebuild
  of today, yesterday or a late day re-derives the lease from its stored refs, and names the
  former holder at `high`, in lookups, PDFs and evidence packages. So `-Rollback`, after
  `services.stop` and before `program.swap`, while this version's program is still in place,
  runs **`arcilogger-db leases prepare-rollback`** (4.3 WP-B), one transaction on derived rows
  only:
  - `UPDATE leases SET confidence = 'low', updated_at = :at WHERE source_table = 'fg_dhcp' AND
    confidence <> 'low'`, `:at` one timestamp for the whole statement: every FortiGate lease,
    ended ones too, because the previous version may rebuild any day;
  - upsert the status row `leases.rollback_lowered` = `{"at": :at, "count": <rows>}` (author
    `:system`, not in `DEFAULT_SETTINGS`, like `db.timescale`);
  - print the count, which the installer writes to its log.

  Raw and sealed data are not touched. With `-RestoreDatabase` the step is skipped (the dump
  replaces the database). A failure of the command does not stop the rollback; the installer
  then prints, in Turkish and English, that FortiGate chains in the previous version may name a
  former holder and that FortiGate-DHCP lookups of moments after the rollback must be made after
  the re-upgrade. The previous version keeps a stored confidence through all its jobs
  (`interval_from_row`, `_Open.from_interval`, the `replace` of `_seeds`, `_write`), so its
  chains on these leases are `low`; the `fg_dhcp` leases that started on a day it rebuilds are
  deleted (it cannot re-derive them), which gives no chain; it creates no FortiGate lease.
  Its usage analysis does not read confidence, as for every chain (usage is not a 5651 output).
  MikroTik leases follow the previous version's own rules, as before the upgrade.
- **Re-upgrade** (round 6). The seed, which `arcilogger-db upgrade` runs after alembic, finds
  `leases.rollback_lowered` and, in one transaction, sets `confidence = 'high'` (the only
  confidence an `fg_dhcp` interval opens with) on the `fg_dhcp` leases at `low` whose
  `updated_at` equals the row's `at` (the previous version did not rewrite them, so they are this
  version's rows of the rollback moment), deletes the status row, and prints both counts,
  restored and still `low`, with the rebuild range of §13. The rows the previous version
  rewrote (closed early by its timeout, re-derived by a rebuild) stay `low` until a rebuild
  re-derives them from the `fg_dhcp` rows (2.6.2: a rebuilt `fg_dhcp` seed is `high`); this
  version's hourly and nightly rebuilds do that for the leases alive at today's and yesterday's
  midnight, the §13 rebuild for the rest. The lines the previous version loaded stay `fg_event`
  rows (D8): a lookup without a chain names a covering one with `fortigate_dhcp_not_converted`,
  whose text covers "the previous version loaded them after a rollback", and a DHCP chain whose
  lease's last DHCP evidence before the moment is older than such a line of its IP or its MAC is
  lowered to `low` with `fortigate_dhcp_not_converted_since_lease` (2.7), whether or not the
  rebuild below was run. The `fg_dhcp` leases the previous version closed early or deleted stay so
  until a rebuild (the incremental run seeds open leases only, and the D8 probe stops at their
  converted rows), so moments between that early end and the FortiGate's own end would answer a
  plain `no_lease`; the §13 paragraph therefore asks for a lease rebuild from the rollback day
  minus the longest lease to the re-upgrade day (2.15); the rebuilt leases' chains in the
  rollback window are then subject to the same lowering.
- **Owner:** WP-A, in its first merge A0 (4.3), together with the model lines and their minimal
  consumers, so every gate stays green (4.2).

#### 2.2.2 `v0018_nat_egresses` (revision `0018_nat_egresses`, down revision `0017_fortigate_dhcp`)

Settings only; it runs the same way on both storage modes.

1. Read the rows `nat.public_ips`, `nat.wan_transip`, `lookup.port_hint` and `nat.egresses`
   (raw SQL on `op.get_bind()`; no ORM model, no application import).
2. If any legacy row has `updated_by` other than `:seed`, and `nat.egresses` is missing or owned
   by `:seed`:
   - compute `value, dropped = legacy_egresses(...)` (2.8.2) with a **frozen copy** of the
     function inside the migration (migrations do not import application code);
   - run `value` through a **frozen copy of `validate_egresses`** (2.8.1). If it fails, `value`
     becomes `{}` and `conversion_failed = true` (the alembic log names the rule, never a value);
   - upsert `nat.egresses` with `updated_by = ':migration'` and `updated_at` = the latest
     `updated_at` of the converted legacy rows (D32);
   - upsert the status row `nat.egresses_migration` (author `:migration`, not in
     `DEFAULT_SETTINGS`): `{"migration": "0018_nat_egresses", "from": {<the three legacy
     values>}, "new": value, "dropped": [{"value": "<text>", "reason": "ipv6" | "invalid" |
     "reserved"}], "conversion_failed": bool, "audited": false}`. Its `new` is also what D15
     compares an exit with to tell whether it is still *as converted*;
   - upsert the status row `nat.egresses_changes` (D32; author `:migration`): one entry per
     converted exit with its fingerprint, `changed_at` = the latest `updated_at` and
     `changed_by` = the `updated_by` of the converted legacy rows, `deleted: false`.
3. **Legacy rows:** rows owned by `:seed` are deleted; administrator-owned rows are kept as they
   are, unread (D18, O6), and the first revision after Wave 3 deletes them.
4. **Audit, after the migration** (WP-C1, `db/seed.py`): the seed, which `upgrade` runs after
   alembic, finds `nat.egresses_migration` with `audited: false`, writes
   `append_audit(session, action="settings.change", target="setting:nat.egresses",
   details={"old": null, "new": value, "migration": "0018_nat_egresses", "from": …, "dropped":
   …, "conversion_failed": …})` and sets `audited: true` in the same transaction. A crash between
   the two leaves `audited: false`, so the next upgrade or seed writes the entry once. The seed
   converts nothing else: legacy rows edited during a rollback are named by
   `R20-nat:legacy-changed` after the re-upgrade (D18) and entered by hand.
5. **Downgrade:**
   - when `nat.egresses` is still owned by `:migration`, it is deleted and the kept legacy rows
     are what the previous version reads;
   - when an administrator changed it after the upgrade, the legacy rows are rebuilt from the
     exits where they allow it (overwriting the kept rows): `direct` exits' single-address
     `public` entries become `nat.public_ips`; the one `isp_nat` exit with exactly one /32
     `transip` and no `interfaces` becomes `nat.wan_transip`; any `preserved` exit sets
     `lookup.port_hint = true`; everything else is dropped with a warning in the alembic log
     (no audit entry: the previous version's seed does not know the status row);
   - `nat.egresses`, `nat.egresses_migration` and `nat.egresses_changes` are deleted.
   - **Scope:** `Install-Arcilogger.ps1 -Rollback` runs no schema downgrade (nothing under
     `deploy/` calls one; services do not check the revision, 1.2), and the CLI has no downgrade
     command, so this downgrade is what the migration tests (and a developer's `alembic
     downgrade`) run, not what a site's rollback does. The rollback's only database step is the
     lease step of 2.2.1 (round 6: derived `leases` rows and one status row). On a rollback
     the previous version reads the kept legacy rows, i.e. the **pre-upgrade** NAT values; the
     §13 paragraph (2.15) tells the operator to re-enter by hand, in the previous version, any
     exit change made after the upgrade (the audit log holds it), never to restore the database
     for that; legacy edits made during the rollback are named after the next upgrade by
     `R20-nat:legacy-changed` and entered in the exits by hand (D18).
6. `db/tests/test_migration_0018.py` (WP-C1) runs the case matrix of 2.8.2 in both directions,
   asserts that every converter output passes `arcilogger_db.egress.validate_egresses` and that
   the migration's frozen converter and validator give the same results as the application's,
   and checks the seed's single audit entry (`verify` of the audit chain passes) and the
   re-upgrade path (rollback → legacy edit → re-upgrade: `nat.egresses` and the audit log
   unchanged and `R20-nat:legacy-changed`'s condition true, on a converted site and on a site
   that had the shipped NAT defaults, where v0018 wrote no status row).

### 2.3 Parser (`ingest/parsers/fortigate.py`, WP-A)

- **Spec:**
  ```
  FG_DHCP: Spec = (("logid","logid","text"), ("subtype","subtype","text"), ("level","level","text"),
                   ("logdesc","logdesc","text"), ("dhcp_msg","dhcp_msg","text"),
                   ("interface","interface","text"), ("ip","ip","inet"), ("mac","mac","mac"),
                   ("hostname","hostname","text"), ("lease","lease","bigint"), ("msg","msg","text"))
  ```
  `SPECS["fg_dhcp"] = FG_DHCP`; `tables` gains `fg_dhcp`. `fg_dhcp` is not in `SUBTYPE_IMPLIED`
  (the subtype is a column).
- **`device_time`** (`_table_specific` for `fg_dhcp`, like `is_interim`): `true` when the event-time
  step returned a usable device time, `false` when the receive time was used (no device time,
  or one beyond `max_clock_skew`: `fortigate.time`). The parser is deterministic, so the
  cross-check's re-parse gives the same value. The worker never uses a `false` row as lease
  evidence (D28).
- **New field kind `mac`:**
  - `compile_spec` gains `macs: tuple[(column, key)]`, converted with `values.to_mac` through a
    bounded cache `_MACS` (65,536 entries, cleared when full, like `_ADDRESSES`);
  - an empty value is NULL and is kept in `extra`;
  - a value that does not convert goes back to `extra` and is reported in `issues.invalid`
    (`fortigate.value`).
- **Routing** (in `parse_record`, after `table = table_for(...)`):
  ```
  if table == "fg_event" and "ip" in fields and "mac" in fields and "dhcp_msg" in fields:
      if _address(fields["ip"]) is not None and to_mac(fields["mac"]) is not None:
          table = "fg_dhcp"
      else:
          issues.dhcp = True        # verdict fortigate.dhcp, the row stays fg_event
  ```
  The check runs only for `event` lines, which are about 1 % of the volume.
- **Verdict:** `fortigate.dhcp` is placed after `fortigate.type` and before `fortigate.value`.
  The detail is `"DHCP event without a usable ip/mac: ip=<valid|invalid|empty>, mac=<…>"`;
  only the validity is given, never the value.
- **`FG_TRAFFIC`** gains `("srcmac", "srcmac", "mac")`. `mastersrcmac`, `srcmacvendor` and
  8.0's `vendor` stay in `extra`, as does everything else ("nothing is lost").
- An IPv6 `ip` with a MAC becomes an `fg_dhcp` row (INET accepts it); the worker ignores it
  (2.6).
- The module docstring table gains the row
  `event/* with ip+mac+dhcp_msg | fg_dhcp | FortiGate DHCP server (ACK/RELEASE; meaning by logid in the worker)`.
- **Not changed:** `detect_type`; the parsers stay independent of settings; the cross-check's
  re-parse gives the same row.

**Synthetic fixture lines.** They are appended (never inserted; `test_fortigate.py` indexes by
position) to `tests/fixtures/synthetic/fortigate-synthetic.log`. They are built from F1–F4 and
the shape of F6, with fictional values, and `devname="FIREWALL-01"`
`devid="FGT0000000000000"`. There are 13 lines:

1. ACK, `interface="vlan42"`, `mac="00:00:5E:00:53:57"` (upper case as in F6),
   `ip=172.20.42.57`, `lease=86400`, `hostname="PLN-PC14"`.
2. The same pair as a renewal ACK 12 h later.
3. RELEASE `logid="0100026002"`, `logdesc="DHCP Release log"`, `dhcp_msg="Release"`,
   `msg="sentetik: DHCP release"` (placeholder texts, marked as such).
4. ACK with a lower-case MAC and no `hostname`.
5. ACK with `hostname=""`.
6. ACK with the 8.0 field `vendor="sentetik"`.
7. 26003 DHCP_STAT (`interface`, `used`, `total`): stays `fg_event`.
8. 26011 DHCPv6 reply (`duid`, `iaid`, `ip=2001:db8::57`, `lease`): stays `fg_event`.
9. Forward traffic with `srcintf="vlan42"`, `srcmac="00:00:5e:00:53:57"`, `mastersrcmac`,
   `srcmacvendor`.
10. Forward traffic with `srcmac=""`.
11. 26001 with `dhcp_msg="Nak"` (placeholder; it parses to `fg_dhcp` and the worker refuses it,
    D25).
12. 26001 with `mac="00:00:00:00:00:00"` (parses; the worker refuses it, D28).
13. 26001 whose `eventtime` and `date`/`time` lie two days before the receive time (`device_time`
    false, `fortigate.time`).

The README row says: "built from the FortiOS 7.6 field list and one community sample; logid of
RELEASE derived; `dhcp_msg`/`msg` of RELEASE and every value are placeholders; sahada doğrula".
`tests/fixtures/format/README.md` names `fortigate-dhcp-sample.log` as awaited, in these words:
"İlk cihaz kaydından **biçimi** alınarak eklenir: alan adları, sıraları, tırnaklama ve
ayırıcılar aynen korunur; her değer örnek dünyanın değeriyle değiştirilir (MAC
`00:00:5E:00:53:xx`, IP `172.20.x.x`, `devname="FIREWALL-01"`, `devid="FGT0000000000000"`, ana
bilgisayar adı ör. `PLN-PC14`). Gerçek bir MAC, IP, ana bilgisayar adı, seri numarası ya da cihaz
adı depoya girmez." / "Added from the **shape** of the first device capture: field names, order,
quoting and separators are kept exactly; every value is replaced by the example world's (…). No
real MAC, IP, host name, serial number or device name enters the repository." The file's
SHA256SUMS line is added with it.

### 2.4 Kinds, search fields, live tail (WP-A)

- `kinds.py`: `KINDS["event"]` gains `KindTable("fg_dhcp")`, so `TABLE_KINDS["fg_dhcp"] ==
  "event"` (the typed row's kind equals its `messages` row's kind `event/system`).
  `CLIENT_IP["fg_dhcp"] = "ip"`; `fg_dhcp` is not in `ACTION_TABLES` (no `action` column). The
  `dhcp` kind is relabelled "DHCP (MikroTik)" / "DHCP (MikroTik)" (the pattern of
  `evidenceTables`), so an administrator checking DHCP arrival on a FortiGate-only site is not
  sent to an empty chip. Kinds have no help text (`Kind` holds a key and labels only, and the
  web has no per-kind help), so the pointer lives only in ARAMA/SEARCH §6, in the rows of the
  `dhcp` and `event` kinds: "FortiGate DHCP kayıtları `type:event` altındadır; `type:event
  mac:<MAC>`, `ip:` ya da `logid:0100026001` onları bulur" / "FortiGate DHCP records are under
  `type:event`; `type:event mac:<MAC>`, `ip:` or `logid:0100026001` finds them".
- `api/search/testing.py` (WP-A0): `MESSAGE_TYPES["fg_dhcp"] = ("event", "system")` and
  `REQUIRED["fg_dhcp"] = {"ip": "172.20.42.57", "mac": "00:00:5e:00:53:57", "device_time": True}`
  (the NOT NULL columns), so tests that write `fg_dhcp` through `Lines` work.
- `fields.py`:
  - `mac:` gains `fg_dhcp.mac`, which is indexed and therefore added to the indexed set;
  - `interface:` gains `fg_dhcp.interface`;
  - a new field `srcmac` (aliases `kaynakmac`) on `fg_traffic.srcmac`, kind `mac`, typed, **not
    indexed**, with help TR "FortiGate oturum kaydındaki kaynak MAC adresi (yalnızca aygıt
    tanıma açık arayüzlerde dolu; dizinsiz, zaman dilimleriyle taranır)." / EN "Source MAC of
    the FortiGate session record (filled only on interfaces with device detection; not indexed,
    scanned in time slices).";
  - `mac:` deliberately does not include `fg_traffic.srcmac`, so a `mac:` search never scans
    the largest table;
  - the `ip:` claim per table follows R27: `fg_dhcp.ip` is indexed;
  - `logid:` gains `fg_dhcp.logid` and `level:` gains `fg_dhcp.level`, with no index claims
    (`_FG_ALL` itself is not changed, because it also drives `dstip:` and `action:`, which
    `fg_dhcp` lacks). Without them `logid:0100026001`, which found these lines in `fg_event` in
    Wave 2 and which the alert texts and DEVICE-SETUP 2.4 step 5 use to compare a raw line,
    would find no new DHCP row.
- The summary table and the live tail need no code: `count_key` reads `TABLE_KINDS` and
  `CLIENT_IP`; `prepare_line` gives the new rows to the matcher.
- Tests (all in A0, because each pins the Wave 2 registry, 1.2): `test_kinds.py`;
  `test_search_fields.py` (`CONTRACT_FIELDS` gains `srcmac` with alias `kaynakmac`,
  `CONTRACT_INDEXED` gains `fg_dhcp` under `mac` and `ip`, the `mac:` table tuple gains
  `fg_dhcp`, `logid`/`level` gain it); `test_search_planner.py` (every expected `mac:` table
  tuple whose kinds include `event` gains `fg_dhcp`, last; `type:dhcp mac:` is unchanged);
  `test_search_oracle.py` (the appended fixture lines flow in, A rest);
  `tests/test_search_docs.py` (ARAMA/SEARCH §3 lists the `srcmac` field and §6 the label "DHCP
  (MikroTik)", both edited in A0).

### 2.5 Settings (day 0; group `lookup` "5651 sorgu ve kira zaman çizelgesi")

| Key | Default | Type, bounds, validation (error code) | Label TR / EN |
|---|---|---|---|
| `dhcp.fortigate_logids` | `{"0100026001": "assigned", "0100026002": "deassigned"}` | object, ≤ 16 entries; keys `^\d{10}$`, values `assigned`\|`deassigned` (`setting_logid_invalid`); `{}` allowed (FortiGate DHCP evidence off) | FortiGate DHCP kayıt kimlikleri (logid) / FortiGate DHCP log IDs (logid) |
| `dhcp.fortigate_dhcp_msgs` | `{"0100026001": ["Ack"]}` | object, ≤ 16 entries; keys `^\d{10}$`; values lists of 1–8 strings of 1–32 characters without control characters, de-duplicated case-insensitively (`setting_dhcp_msg_invalid`); a logid without an entry is not restricted | FortiGate DHCP ileti türleri (dhcp_msg) / FortiGate DHCP message types (dhcp_msg) |
| `dhcp.fortigate_use_logged_lease` | `true` | boolean | FortiGate kira süresini kayıttan al / Use the lease time in the FortiGate record |
| `dhcp.fortigate_traffic_extends` | `false` | boolean | Trafik FortiGate kirasını uzatır / Traffic extends a FortiGate lease |
| `dhcp.fortigate_interfaces_excluded` | `[]` | string list, ≤ 32 entries, the interface rule (`setting_interface_invalid`, `setting_too_many`) | Kira kanıtı sayılmayan FortiGate DHCP arayüzleri / FortiGate DHCP interfaces that are not lease evidence |
| `dhcp.fortigate_format_verified` | `false` | boolean | FortiGate DHCP kayıt biçimi sahada doğrulandı / FortiGate DHCP record format verified on site |
| `lookup.srcmac_interfaces` | `[]` | string list, ≤ 32 entries; each 1–35 characters, no control characters and no leading or trailing space, de-duplicated (`setting_interface_invalid`, `setting_too_many`) | srcmac kullanılan FortiGate arayüzleri / FortiGate interfaces whose srcmac is used |
| `lookup.srcmac_max_ips` | `1` | integer 1–16 (`INTEGER_RANGES`) | Bir srcmac için en çok IP adresi / Most IP addresses per srcmac |
| `nat.egresses` | `{}` | object, `arcilogger_db.egress.validate_egresses` (2.8.1) | İnternet çıkışları (NAT) / Internet exits (NAT) |
| `nat.snat_trandisp` | `["snat"]` | string list, 1–8 entries, each `^[a-z][a-z0-9+_-]{0,15}$`, not empty (`setting_trandisp_invalid`) | Kaynak NAT türleri (trandisp) / Source NAT types (trandisp) |
| removed by WP-C1 | `nat.public_ips`, `nat.wan_transip`, `lookup.port_hint` | converted by `v0018`; administrator rows kept unread for one version (D18) | — |
| status rows (no default, never editable, like `db.timescale`) | `nat.egresses_migration` (v0018; the seed only sets its `audited` flag), `nat.egresses_changes` (seeded by v0018, kept by `change_setting`), `leases.rollback_lowered` (round 6: written by `arcilogger-db leases prepare-rollback` at a rollback, read and deleted by the seed of the re-upgrade, 2.2.1) | written by v0018, the seed, `settings_admin.change_setting` and the rollback step; read by `run_lookup` itself (D15, D32; never through `LookupSettings.from_settings`), the seed and the `R20-nat:conversion-failed` alert | — |

**Help texts (TR / EN):**

- `dhcp.fortigate_logids`:
  - TR: "FortiGate DHCP sunucusu olaylarından kira kanıtına çevrilenlerin logid değeri ve
    anlamı: assigned (adres verildi ya da yenilendi), deassigned (adres bırakıldı).
    Varsayılanlar FortiOS günlük başvuru belgesinden alınmıştır (DHCP Ack 0100026001; DHCP
    Release 0100026002 belgedeki numaralama kuralından türetildi); ilk kayıtlar gelince sahada
    doğrulayın. Değişiklik geçmiş günlere kiralar yeniden oluşturulunca uygulanır. Boş: FortiGate
    DHCP kayıtları kullanılmaz."
  - EN: "The logid of each FortiGate DHCP server event that becomes lease evidence, and its
    meaning: assigned (address given or renewed), deassigned (address released). The defaults
    come from the FortiOS log reference (DHCP Ack 0100026001; DHCP Release 0100026002 derived
    from its numbering rule); verify them on site once the first records arrive. A change
    applies to past days when the leases are rebuilt. Empty: FortiGate DHCP records are not
    used."
- `dhcp.fortigate_use_logged_lease`:
  - TR: "Açık: FortiGate DHCP Ack kaydındaki lease değeri (saniye; sahada doğrulayın) o kiranın
    süresidir ve kira son DHCP kaydından bu süre sonra, FortiGate'in kendisinin bitirdiği anda
    kapanır. 300 saniyeden kısa, 100 günden uzun ya da 0 (süresiz) değerlerde DHCP sunucusu
    süreleri (lease.server_seconds, lease.default_seconds; lease.timeout_percent payıyla)
    kullanılır. Kapalı: her zaman o süreler."
  - EN: "On: the lease value of the FortiGate DHCP Ack record (seconds; verify on site) is that
    lease's time, and the lease closes that long after its last DHCP record, when the FortiGate
    itself ends it. Values below 300 seconds, above 100 days or 0 (unlimited) use the DHCP server
    times (lease.server_seconds, lease.default_seconds, with the lease.timeout_percent margin).
    Off: always those times."
- `dhcp.fortigate_dhcp_msgs`:
  - TR: "Her logid için kira kanıtı sayılan dhcp_msg değerleri (büyük/küçük harf ayrımı yok).
    Varsayılan: DHCP Ack kaydında yalnızca Ack (topluluk örneğindeki değer). Listede olmayan bir
    değerle gelen kayıt (ör. Nak) kullanılmaz ve uyarı verir. Listede olmayan logid
    sınırlanmaz. Sahada doğrulayın."
  - EN: "Per logid, the dhcp_msg values that count as lease evidence (case-insensitive). Default:
    only Ack for the DHCP Ack record (the value of the community sample). A record with any
    other value (e.g. Nak) is not used and raises an alert. A logid without an entry is not
    restricted. Verify on site."
- `dhcp.fortigate_traffic_extends`:
  - TR: "Kapalı (önerilen): FortiGate kirası yalnızca DHCP kayıtlarıyla (Ack, yenileme) açık
    kalır; trafik kanıt olarak eklenir ama süreyi uzatmaz. FortiOS büyük olasılıkla yenilemeleri
    de kaydeder (sahada doğrulayın: DEVICE-SETUP.md bölüm 2.4 adım 5); trafik MAC adresini
    göstermez ve aynı adresi kullanan başka bir cihaz kirayı eski sahibine bağlı tutabilir.
    Açık: MikroTik kiralarında olduğu gibi trafik kirayı uzatır; yalnızca yenilemelerin
    kaydedilmediği sahada doğrulanırsa ve kurum bu yanlış kişi riskini kabul ederse açın (uyarı
    R20-lease:fortigate-renewals-missing)."
  - EN: "Off (recommended): a FortiGate lease stays open through DHCP records only (Ack,
    renewal); traffic is added as evidence but does not extend it. FortiOS most likely logs
    renewals too (verify on site: DEVICE-SETUP.md section 2.4 step 5); traffic does not show the
    MAC, and another device using the same address could keep the lease tied to its former
    holder. On: traffic extends the lease as it does MikroTik leases; switch it on only when it
    is verified on site that renewals are not logged and the organisation accepts this
    wrong-person risk (alert R20-lease:fortigate-renewals-missing)."
- `dhcp.fortigate_interfaces_excluded`:
  - TR: "Bu FortiGate arayüzlerinin DHCP kayıtları kira kanıtı sayılmaz. IPsec üzerinden DHCP
    veren sunucuların (server-type ipsec) arayüzlerini buraya ekleyin: bu kiralar tünel
    kapandıktan ipsec-lease-hold saniye sonra (varsayılan 60) biter; kayıttaki kira süresi bu
    bitişi göstermez."
  - EN: "DHCP records of these FortiGate interfaces are not lease evidence. Add the interfaces of
    DHCP-over-IPsec servers (server-type ipsec): those leases end ipsec-lease-hold seconds after
    the tunnel goes down (default 60); the lease time in the record does not show that end."
- `dhcp.fortigate_format_verified`:
  - TR: "FortiGate DHCP kayıtlarının biçimi DEVICE-SETUP.md bölüm 2.4'teki adımlarla sahada
    doğrulandıysa açın; her FortiOS yükseltmesinden sonra doğrulamayı yineleyin. Kapalıyken
    raporlar ve Kira kanıtı kutusu 'kayıt biçimi sahada doğrulanmadı' der; açıkken raporlar
    kimin ve ne zaman doğrulandı olarak işaretlediğini yazar."
  - EN: "Switch on once the FortiGate DHCP record format has been verified on site with the steps
    of DEVICE-SETUP.md section 2.4; repeat the check after every FortiOS upgrade. While off,
    reports and the Lease evidence card say 'record format not verified on site'; while on,
    reports say who marked it verified and when."
- `lookup.srcmac_interfaces`:
  - TR: "Aygıt tanımanın açık olduğu ve FortiGate'in VLAN'ın ağ geçidi olduğu arayüzlerin
    srcintf adları. Yönlendirilmiş (L3) bir ağa bakan arayüzü hiçbir zaman eklemeyin: orada srcmac
    yönlendiricinin ya da L3 anahtarın adresidir. Arada yalnızca katman 2 anahtarlar varsa
    arayüzü ancak iki bilinen istemciyle sahada doğruladıktan sonra ekleyin (Cihaz kurulumu
    bölüm 2.5): Fortinet'e göre arada bir anahtar varsa kayıtta anahtarın MAC adresi
    görülebilir. Kira kaydı olmayan bir oturumda MAC adresi, cihaz ve cihaza atanmış kişi bu
    alandan düşük güvenle ve kısmi kanıt olarak gösterilir; kira kaydı hiç oluşturulmaz. Boş:
    srcmac kullanılmaz."
  - EN: "The srcintf names of interfaces where device detection is on and the FortiGate is the
    VLAN's gateway. Never add an interface that faces a routed (L3) network: there srcmac is the
    router's or L3 switch's address. When only layer-2 switches sit in between, add the interface
    only after checking it on site with two known clients (DEVICE-SETUP.md section 2.5):
    according to Fortinet, with a switch in between the switch's MAC address may appear in the
    record. For a session without a lease, the MAC address, the device and the person assigned to
    it are shown from this field with low confidence, as partial evidence; no lease is ever
    created. Empty: srcmac is not used."
- `lookup.srcmac_max_ips`:
  - TR: "Bir srcmac istenen anın ±1 saat içinde bundan çok farklı IPv4 adresiyle görülürse bir
    yönlendiricinin, anahtarın ya da ağ cihazının adresi sayılır ve kullanılmaz. IPv6 adresleri
    sayılmaz: çift yığınlı bir istemci aynı MAC adresiyle IPv6 ve geçici IPv6 adresleri de
    kullanır."
  - EN: "A srcmac seen with more distinct IPv4 addresses within ±1 hour of the requested moment
    counts as a router's, switch's or network device's address and is not used. IPv6 addresses
    are not counted: a dual-stack client also uses IPv6 and temporary IPv6 addresses with the
    same MAC address."
- `nat.egresses`:
  - TR: "Kurumun her internet hattı: servis sağlayıcının verdiği genel IP adresleri (ya da ağı)
    ve bu adresin değişip değişemeyeceği, FortiGate'in o hattaki kaynak NAT adresi (transip),
    NAT türü (doğrudan / servis sağlayıcı NAT'ı / FortiGate'ten geçmiyor) ve servis
    sağlayıcının kaynak portu koruyup korumadığı. FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya
    da şubeye yaptığı kaynak NAT'ın adresleri 'İnternet çıkışı değil' türüyle girilir; bu
    adreslerde oturum aranmaz. Genel adres özel (RFC 1918) ya da paylaşılan
    (RFC 6598, ör. 100.64.x.x) bir adres olamaz: FortiGate'in servis sağlayıcı yönlendiricisinin
    arkasındaki adresi, servis sağlayıcı NAT'ı çıkışının NAT adresidir. Birden çok hat varsa
    her hattın arayüzlerini (dstintf) girin. Boş: tek bir çift NAT çıkışı varsayılır. Kurulum
    yerinde girilir, tahmin edilmez; değerleri 'Kayıtlarda görülen NAT adresleri' tablosundan
    okuyabilirsiniz."
  - EN: "Each internet line of the organisation: the public IP addresses (or network) the ISP
    gave and whether that address may change, the FortiGate's source-NAT address on that line
    (transip), the NAT type (direct / ISP NAT / does not pass the FortiGate) and whether the ISP
    keeps source ports. The FortiGate's source-NAT addresses towards a DMZ, another VLAN, a VPN or
    a branch are entered with the type 'Not an internet exit'; no session is searched on them. A public address cannot be a private (RFC 1918) or shared (RFC 6598, e.g.
    100.64.x.x) address: the FortiGate's address behind the ISP router is the NAT address of an
    ISP NAT exit. With several lines, enter each line's interfaces (dstintf). Empty: one
    double-NAT exit is assumed. Entered on site, never assumed; read the values from the 'NAT
    addresses seen in the logs' table."
- `nat.snat_trandisp`:
  - TR: "Hiçbir çıkışa tanımlı olmayan adreslerde aday aramasına alınan FortiGate trandisp
    değerleri. FortiOS belgeleri değerlerin listesini vermez; kayıtlarda yalnızca snat görüldü.
    Sahada doğrulayın: başka değerler 'Kayıtlarda görülen NAT adresleri' tablosunda işaretlenir."
  - EN: "The FortiGate trandisp values a candidate search takes for addresses no exit claims.
    The FortiOS documents do not list the values; only snat was seen in logs. Verify on site:
    other values are flagged in the 'NAT addresses seen in the logs' table."

**Existing keys whose help changes (day 0):**
- `lease.server_seconds`: "DHCP sunucusu adı (MikroTik, ör. dhcp61) ya da FortiGate DHCP
  arayüzü adı (ör. vlan61)" / "DHCP server name (MikroTik, e.g. dhcp61) or FortiGate DHCP
  interface name (e.g. vlan61)".
- `dhcp.server_vlan_regex`: "Yalnızca MikroTik DHCP sunucusu adları; FortiGate kiralarının VLAN'ı
  alt ağdan bulunur" / "MikroTik DHCP server names only; FortiGate leases take the VLAN from the
  subnet".

**New `SETTING_ERRORS` codes (TR / EN; they never repeat a submitted value):**

| Code | Türkçe | English |
|---|---|---|
| `setting_logid_invalid` | logid 10 rakamdan oluşmalı ve anlamı assigned ya da deassigned olmalı (en çok 16 kayıt). | A logid has 10 digits and its meaning is assigned or deassigned (at most 16 entries). |
| `setting_dhcp_msg_invalid` | Her logid 10 rakamdan oluşmalı; değeri 1–8 ileti türünden (her biri 1–32 karakter, kontrol karakteri yok) oluşan bir liste olmalı. | Each logid has 10 digits; its value is a list of 1–8 message types (each 1–32 characters, no control characters). |
| `setting_interface_invalid` | Arayüz adı 1–35 karakter olmalı; kontrol karakteri ve baş/son boşluk içeremez. | An interface name has 1–35 characters, without control characters or leading/trailing spaces. |
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

`setting_too_many` (existing) covers more than 8 exits and more than 16 addresses or 8
interfaces per exit. `REAUTH_KEYS` and `LOCKED_KEYS` are unchanged (O8). `web/src/lib/
settings-types.ts` needs no change: the group exists and object is a known type.

### 2.6 Lease timeline (WP-B)

#### 2.6.1 The DHCP source registry (`worker/correlation/dhcp_sources.py`, new)

**The trust and usable-row rules live in a leaf module, `worker/lookup/trust.py`** (day 0
writes `untrusted_sources` complete and the signatures of the other four functions; WP-B fills
them after A0). It
imports only `arcilogger_db.models` and `lookup.types`, so every user can import it: the timeline
side (`dhcp_sources`, `leases`), the lookup (`engine`, `attribution`), `alerts/system.py` and the
API's `sourcehealth`. A lookup module that imported `dhcp_sources` instead would close an import
cycle (`timeline` → `lookup.refs` → `lookup/__init__` → `engine` → `attribution` →
`dhcp_sources` → `timeline`, 1.2), and querying `MtDhcp` directly in `attribution.py` would break
the grep rule of 2.6.2.

```python
# worker/lookup/trust.py (leaf)
def untrusted_sources(session) -> frozenset[str]    # D24: unknown or type<>'fortigate', except an
                                                    # unknown-<ip> whose <ip> lies in an enabled,
                                                    # configured fortigate row's address
def refusal(settings, untrusted) -> ColumnElement[str | None]  # the code of the first rule of
                                                    # 1-7 below that applies to an fg_dhcp row, as
                                                    # a CASE; NULL for a usable row. The only
                                                    # implementation of the rules: load_rows
                                                    # selects it as a column and counts from it,
                                                    # the alerts and the card group by it
def kept(settings, untrusted) -> ColumnElement[bool]  # refusal(...) IS NULL
def usable_dhcp_origins(session, settings, untrusted, subnets, lo, hi
                        ) -> dict[str, set[tuple[str, str | None]]]  # per subnet, the origins
                                                    # (table, vd) of usable rows (D27, 2.7):
                                                    # mt_dhcp rows with action assigned/deassigned
                                                    # (never filtered by `untrusted`, D24) and
                                                    # fg_dhcp rows that `kept` accepts
def srcmac_ip_counts(session, low, high, interfaces, untrusted, macs=None
                     ) -> dict[tuple[str, str], int]  # per (srcintf, srcmac) the distinct IPv4
                                                    # srcip of trusted fg_traffic rows (D10): the
                                                    # one query of the lookup guard (2.9), the
                                                    # srcmac-shared alert and the observed endpoint
```

```python
# worker/correlation/dhcp_sources.py
from arcilogger_worker.lookup.refs import DHCP_TABLES, TABLE_RANK   # defined once, in refs.py
from arcilogger_worker.lookup.trust import kept, untrusted_sources

@dataclass(frozen=True, slots=True)
class DhcpRow:                       # one row of either table, already interpreted
    table: str; id: int; time: datetime; action: Literal["assigned", "deassigned"]
    ip: str; mac: str; server: str | None; vlan: int | None; hostname: str | None
    lease_seconds: int | None; ref: EvidenceRef
    origin: tuple[str, str | None]        # (table, vd); vd None for mt_dhcp (D27)
    network: str | None                   # subnet of the configured network holding ip
                                          # (ctx.network_for, find_vlan), else None (D33)

def load_rows(ctx, lo, hi, ips: Collection[str] | None = None,
              macs: Collection[str] | None = None) -> list[DhcpRow]
def first_time(session) -> datetime | None          # min over mt_dhcp, trusted usable fg_dhcp
def events(rows) -> list[DhcpEvent]                 # for the timeline
```

`load_rows` runs one query per table and merges them in Python by
`(time, TABLE_RANK[table], id)`. `ctx.untrusted` is `untrusted_sources(session)`, loaded once per
run (D24).

- **`Context.network_for(ip)`** (new, cached beside `vlan_for`): the `subnet` of the `vlans` row
  `find_vlan(ip)` returns, else `None`. Both tables use it for `network` (round 6): the VLAN
  number of a MikroTik row may come from the row or `dhcp.server_vlan_regex`, and a network may
  have no VLAN number at all, so D33 compares networks, never VLAN numbers.
- **`mt_dhcp`** keeps today's columns and rules: actions `assigned`/`deassigned`,
  `vlan = ctx.vlan_for(ip, server, vlan)`, `lease_seconds = None`, `ref = dhcp_ref(row)`,
  `origin = ("mt_dhcp", None)`, `network = ctx.network_for(ip)`.
- **`fg_dhcp`** reads `id, time, logid, dhcp_msg, interface, ip, mac, hostname, lease,
  device_time`, `extra->>'vd'`, the raw reference columns and `source`, and selects
  `trust.refusal(settings, ctx.untrusted)` as one more column, so the rules exist once (the
  alerts and the card use the same expression). Each row is checked in
  this order and counted under the first rule that applies; a row any rule applies to is never
  lease evidence. Rules 2 and 3 are **configuration** (the administrator left the row out on
  purpose, or it is out of scope): they are counted apart and never count as refusals in an
  alert or on the card. Rules 1 and 4–7 are **refusals**:
  1. `source IN ctx.untrusted` → `fortigate_dhcp_untrusted_source` (D24);
  2. `interface IN settings.fortigate_interfaces_excluded` → `fortigate_dhcp_excluded` (D28);
  3. an IPv6 `ip` → `ipv6_ignored`;
  4. no mapping in `settings.fortigate_logids` → `fortigate_dhcp_unmapped` (D4);
  5. `settings.fortigate_dhcp_msgs` lists the logid and `lower(dhcp_msg)` is not among its
     lower-cased values (a NULL `dhcp_msg` included) → `fortigate_dhcp_unexpected_msg` (D25);
  6. `device_time = false` → `fortigate_dhcp_receive_time` (D28);
  7. zero, broadcast or multicast MAC → `fortigate_dhcp_bad_mac` (D28).
  For a kept row:
  - `action = settings.fortigate_logids[logid]`;
  - `server = interface`, `vlan = ctx.vlan_for(ip)` (server `None`: the subnet only, D7),
    `network = ctx.network_for(ip)`; a row whose IP lies in no configured network is still used
    and counted `fortigate_dhcp_no_network`;
  - `lease_seconds = lease` when `use_logged_lease` and `300 <= lease <= 8_640_000`. Otherwise
    the value is `None`; a non-NULL lease is then counted as `lease_fallback`, and `lease = 0`
    also as `lease_unlimited`;
  - `ref = fg_dhcp_ref(row, action)`; `origin = ("fg_dhcp", vd)`.
- Counts are deduplicated by `(table, id)` within a run (seeds and look-ahead load rows again).
- **Traffic** (`leases.load_traffic`, `_latest_traffic`) leaves out `fg_traffic` rows whose
  `source` is in `ctx.untrusted` (both DHCP tables; D24).

`refs.py` (day 0) adds the following.

- `FG_DHCP_TABLE = "fg_dhcp"`, `DHCP_TABLES = (DHCP_TABLE, FG_DHCP_TABLE)` and `TABLE_RANK =
  {table: rank for rank, table in enumerate(DHCP_TABLES)}` (the index is the tie-break rank),
  the only definitions; `timeline.py`, `leases.py`, `dhcp_sources.py`, `trust.py` and the lookup
  import them from here. (`refs.py` and `trust.py` import nothing from `correlation/`, so the
  lookup never needs `dhcp_sources`.)
- `fg_dhcp_ref(row, action)` with the summary
  `"<interface or -> <action> <ip> for <mac>[ <hostname>] (FortiGate DHCP, logid <logid>, lease
  <n|->)"`. The second token is the action, as in the MikroTik summary; the lease is the logged
  value (or `-`), so a stored interval's evidence keeps the lease of each ACK it cites. The
  words "FortiGate DHCP," before the logid keep the summary, quoted anywhere in the docs, out of
  `MODEL_OR_RELEASE` (the word FortiGate directly followed by a logid or message number matches
  its model pattern, 1.2); the docs write the message as "FortiGate DHCP 26001", never with the
  number right after the device name.
- `dhcp_action(ref) -> str | None`, which returns `ref.summary.split()[1]` for a ref of a DHCP
  table. It replaces the substring test in `_ref_key`, because a hostname can contain the word
  "deassigned". `dhcp_ip(ref) -> str | None` returns the third token (the IP) the same way; the
  report cross-check uses it for the line that ended a lease (2.12).
- `last_dhcp_evidence(refs: Sequence[EvidenceRef], end: datetime | None, end_reason: str |
  None, at: datetime | None = None) -> datetime | None` (a lease's `evidence`, `end` and
  `end_reason`, so a `StoredLease` and a timeline interval both pass their fields; round 5): the
  newest time among the DHCP-table refs with `time <= at` (no bound when `at` is None; for a
  lease ended `dhcp`, its own release included), leaving out the line that ended a `superseded`
  lease (a ref with `time >= end`), which is another device's ACK or, after D33, this MAC's ACK
  for another address; the same test `_lease_claims` uses. The stored refs keep the opening ACK
  and the last `CONTINUATIONS_KEPT` (6) continuations only, so for an `fg_dhcp` lease the
  attributor takes the per-moment value from the rows instead (2.7) and uses this function only
  as its fallback; for the other tables it is the value. One value per lookup moment for
  `LeaseInfo.last_dhcp_evidence`, `lease_renewal_not_seen` and the PDF lease row (2.7, 2.12).

#### 2.6.2 Every plug-in point

| File:line (today) | Change |
|---|---|
| `timeline.py:68-78` `DhcpEvent` | + `table: str = DHCP_TABLE`, `lease_seconds: int \| None = None`, `origin: tuple \| None = None`, `network: str \| None = None` (the row's `network`, 2.6.1) |
| `timeline.py:115-118` `evidence_key` | `(time, rank, TABLE_RANK.get(table, 0), row_id, ip, mac)` with the ref's table; equal-time ordering stays deterministic across tables (rows of two tables can share an id) |
| `timeline.py:125-177` `TimelineRules` | + `fortigate_traffic_extends: bool`; `alive_window(source_table, server, lease_seconds=None)`: ARP → `arp_grace`; a DHCP table with `lease_seconds` (only `fg_dhcp` rows carry one) → `lease_seconds`, the server's own end (D6); otherwise `timeout(server)` (configured time × `timeout_percent`, as today); `longest_window` also covers the largest `lease_seconds` of the seeds. The same formula is `LookupSettings.lease_timeout_seconds(server, lease_seconds=None)`, and a test asserts that the two agree |
| `timeline.py:180-203`, `218-232` `LeaseInterval`, `_Open` | + `lease_seconds: int \| None = None`, `origin: tuple \| None = None` (not stored; rebuilt from the newest DHCP ref of a seed), `network: str \| None = None` (not stored; a new interval takes the event's, a seed's is `ctx.network_for(seed.ip)`, set by the lease job for every seed, `run_incremental`'s included) |
| `timeline.py:238` `from_interval` | continuations: `ref.table in DHCP_TABLES` (fixes the dropped `fg_dhcp` refs of seeds) |
| `timeline.py:376-379` `_deadline` | passes `item.lease_seconds` |
| `timeline.py:443-447` `_traffic` | `mt_dhcp` items: `touch` as today. `fg_dhcp` items: with `fortigate_traffic_extends` as today; without it the traffic ref and `last_traffic` are kept but `last` is not moved (D26). No per-run "traffic after the lease" counter: an incremental run cannot see intervals that timed out in an earlier run, so the renewal alerts compute from stored leases (2.6.3) |
| `timeline.py:478-490` `_deassigned` | `item.source_table in DHCP_TABLES` |
| `timeline.py:492-524` `_assigned` | a continuation only when the open item of the pair has `source_table == event.table` (D5); the continuation takes the event's `lease_seconds` when it has one. An open item of the pair from the other DHCP table (or ARP, as today) is closed `superseded` at the event like every other open interval of the IP. A new interval gets `source_table = event.table`, `lease_seconds = event.lease_seconds`, `origin = event.origin`; each open DHCP interval it closes as `superseded` whose `origin` differs counts `superseded_across_sources` (D27), the same pair's included. **D33:** an `fg_dhcp` `assigned` of MAC m (new interval or continuation) also closes, as `superseded` at the event time with the event as closing ref, every open `fg_dhcp` interval of m on another IP with the same `origin`, `server` (interface) and `network`, counted `fg_mac_moved`, and m's open `mt_dhcp` interval on another IP with the same `network` (the MikroTik variant, counted `superseded_across_sources`), each after first applying that IP's deadline (`self._expire_ip(other_ip, event.time)`, so an interval already past its end times out there, as a live run would have closed it); nothing is closed when the event's `network` is None (an address in no configured network; a network without a VLAN number has a `network` and closes like any other, round 6); the engine keeps a second index `_dhcp_by_mac: dict[mac, set[ip]]` of the open DHCP intervals of both tables for it. A MikroTik `assigned` never closes an interval of another IP |
| timeline stats | + `fg_renewal_ack` (a continuation `fg_dhcp` ACK at least 40 % of the lease after the interval's previous DHCP evidence: a renewal, V3), `fg_mac_moved` (D33), `superseded_across_sources` |
| `leases.py:232-301` | `_DHCP_COLUMNS`/`load_dhcp` → `dhcp_sources.load_rows` + `events` |
| `leases.py:342-349` `first_evidence_time` | `min(dhcp_sources.first_time, min(mt_arp.time))` |
| `leases.py:355-391` `interval_from_row`, `_values` | `lease_seconds` |
| `leases.py:475-489` `_devices` | unchanged code; FortiGate `DhcpEvent`s now arrive (host names from ACKs) |
| `leases.py:527-532` `_releases` | through the union loader |
| `leases.py:550-563` `_RANK`, `_ref_key`, `_row_key` | keys `(time, rank, TABLE_RANK, id)`; the release rank through `dhcp_action(ref)`; `_row_key(row: DhcpRow)` |
| `leases.py:732` `_seeds` `last` | for an `fg_dhcp` seed while `fortigate_traffic_extends` is off, `last` = the maximum of `seed.start` and the times of the seed's DHCP and ARP refs and rows; traffic refs and the latest traffic row only set `last_traffic` and stay evidence, as `_traffic` does in the timeline (D26). `ended_by.time <= last + window` and the timeout use that `last`. Without it every day rebuild (`_catch_up`, `run_late_evidence`, the rebuild that D4 and D24 ask for) would move the FortiGate deadline to the last traffic before midnight plus the lease, reopen the seed at every midnight, and name the former client past the FortiGate's own lease end; `mt_dhcp` seeds keep today's rule |
| `leases.py:644, 742` seed windows | `alive_window(seed.source_table, seed.server, lease)` with the seed's lease **at midnight** (next row). For an `fg_dhcp` seed the raw-window lower bound is `seed.start` (FortiGate writes about two rows per lease time per address, so this stays small) |
| `leases.py:612-625` `_closes`, `655-690` seeds' raw DHCP and held queries | `load_rows(ctx, low, day_start, ips, macs=<the MACs of the DHCP seeds>)` (the MAC part reads `fg_dhcp` only); `Lease.source_table.in_(DHCP_TABLES)`; `_Held` gains `table`, and "continues a lease its MAC already held" means the same MAC **and** the same table; with `row.mac == seed.mac`, `_closes` returns `seed.source_table != row.table` (an ARP seed, as today, and now a seed of the other DHCP table, D5); `_closes` also ends an `fg_dhcp` seed at a kept `fg_dhcp` `assigned` of the seed's MAC for **another IP** with the same origin, interface and `network` (D33), and an `mt_dhcp` seed at a kept `fg_dhcp` `assigned` of the seed's MAC for another IP of the same `network` (the MikroTik variant; the seed's `network` from its IP, compared by subnet), never when the row's `network` is None, only when that row's time is within the seed's alive window (`ended_by.time <= last + window`, otherwise the seed timed out first), exactly as the timeline would, so live, range and single-day rebuilds stay equal; the look-ahead of `rebuild_day` loads DHCP rows for the pending IPs **and** `fg_dhcp` rows for the MACs of the pending DHCP intervals of both tables. The `lease_seconds` of an `fg_dhcp` seed at midnight is that of the latest kept `assigned` `DhcpRow` of its pair before midnight; the stored value only when there is none. A later day's rebuild may already have changed the stored row to a newer ACK's lease, so the stored value is never trusted for midnight when a row exists |
| `leases.py:690-726` seed closing and continuation refs | `DhcpRow` and its own `ref`; `seed.source_table in DHCP_TABLES`; the seed's `origin` from its newest DHCP row |
| `leases.py:720-732` `_seeds` state (round 6) | an `fg_dhcp` seed's state gets `confidence = "high"`, the confidence every `fg_dhcp` interval opens with: a stored `low` can only be the rollback step's (2.2.1), and the rebuild has just re-derived the seed from its `fg_dhcp` rows (its raw window starts at `seed.start`), so it undoes the step for a lease the previous version rewrote. Other seeds, and every seed of `run_incremental` (stored rows, no re-derivation), keep their stored confidence, as today |
| `leases.py:759-776` `_closed_by` | `in DHCP_TABLES` |
| `leases.py:865, 878, 961` traffic and DHCP IP sets | `in DHCP_TABLES`; traffic of untrusted sources left out (D24) |
| `leases.py` job report | + `fortigate_dhcp_unmapped`, `fortigate_dhcp_untrusted_source`, `fortigate_dhcp_unexpected_msg`, `fortigate_dhcp_excluded`, `fortigate_dhcp_receive_time`, `fortigate_dhcp_bad_mac`, `fortigate_dhcp_no_network`, `ipv6_ignored`, `lease_fallback`, `lease_unlimited`, `fg_renewal_ack`, `fg_mac_moved`, `superseded_across_sources`, and `kept_fg_dhcp`/`refused_fg_dhcp`/`excluded_fg_dhcp` in `details` (refused: rules 1 and 4–7 of 2.6.1; excluded: rules 2–3) (integers, for the job page; the alerts of 2.6.3 read the tables through the shared `kept` predicate, and only `fg_renewal_ack` from the lease runs of 7 days, because "a renewal was ever seen" is all they need) |
| `Context.load` (`leases.py:191-212`) | builds `LookupSettings` **without** parsing `nat.egresses` (2.8.1, lazy), so a bad NAT value can never stop the lease job; the same for `usage/subjects.py:68` |
| `watermark.py:107` | `for model in (MtDhcp, FgDhcp, MtArp)` |
| `window.py:25` `_TABLES` | + `FgDhcp` |
| `late.py`, `rebuild.py`, `admin_jobs.py`, `service.py` | no change (they work per source and day) |

Grep rule (done-when): no `== DHCP_TABLE`, `"mt_dhcp"` or `MtDhcp` remains in
`worker/src/arcilogger_worker/correlation/` and `worker/src/arcilogger_worker/lookup/` outside
`dhcp_sources.py`, `refs.py`, `trust.py`, `watermark.py` and `window.py`. (`worker/testing.py` writes
`MtDhcp` rows for tests by design and is outside the rule.)

#### 2.6.3 Lease and NAT alerts `R20-lease`, `R20-nat` (`alerts/system.py`, `alerts/texts.py`)

Two new checks run in `alerts.system` (every 5 minutes, 5651 Temel), each in its own
transaction: `run_system_checks` records an exception per check and loses that check's findings
(1.2), so the two must not share one. `_lease_evidence` owns the dedup keys `R20-lease:*` and
never parses `nat.egresses`. `_nat_exits` owns `R20-nat:*`; it calls `parse_egresses` and turns a
parse failure into the finding `R20-nat:invalid` instead of raising, so a bad NAT value never
stops a lease alert (the coupling 2.8.1 removes from the lease job). Each check resolves its
alerts after 24 h without the problem (unless the table says otherwise) and costs less than 1 s
at 1 M lines a day (3.8). **One filter for every query:** "usable" `fg_dhcp` rows are those
`trust.kept(settings, untrusted)` accepts (2.6.1, trusted sources only); "trusted traffic" is
`fg_traffic` of sources outside `untrusted_sources`; `mt_*` rows are never filtered by that set
(D24). No evidence is counted from `search_counts` (it counts refused and unknown-sender rows
too); only `source-clock` reads it, to pick the sources whose files hold lookup rows. Every query
except those of `fortigate-refused`, `fortigate-unconverted` and `source-clock` reads usable rows
only, so a row of an excluded interface never raises a cause whose remedy is to exclude it.

| Key | Problem (query) | Severity | Resolves |
|---|---|---|---|
| `R20-lease:none:<table>` | one cause per evidence table `fg_dhcp`, `mt_arp`, `mt_dhcp`: trusted `fg_traffic` of the last hour whose `srcip` is in 10/8, 172.16/12 or 192.168/16 (first match through the time key), **no usable row of that table** (`mt_dhcp` `assigned`/`deassigned`, `mt_arp`, usable `fg_dhcp`) in its evidence window (`fg_dhcp`: 24 h, extended while open `fg_dhcp` leases exist to half their longest `lease_seconds` + 24 h; `mt_arp`, `mt_dhcp`: 24 h; open MikroTik intervals never count, traffic keeps them open), **and** usable rows of that table in the 30 days before its window (a feed that stopped; D30); `mt_dhcp` only while no usable `mt_arp` row arrived in its window either; details: `table` | **high** for `fg_dhcp` and `mt_arp` (periodic by design); **medium** for `mt_dhcp` (RouterOS logs no renewals: a quiet period looks the same) | as soon as a usable row of the table arrives |
| `R20-lease:absent` | the same traffic, and no usable row of any evidence table in the 24 h nor in the 30 days before (the set-ups KURULUM §0.1 documents as ending at the internal IP) | info | as soon as a usable row arrives |
| `R20-lease:fortigate-refused` | while `dhcp.fortigate_logids` is not empty: the `fg_dhcp` rows of the last 24 h outside rules 2–3 of 2.6.1 (excluded interfaces and IPv6 rows are configuration and are left out of both sides; their count goes into the details as `excluded`), of which rules 1 and 4–7 refuse some; details: the count per reason (`untrusted`, `unmapped`, `unexpected_msg`, `receive_time`, `bad_mac`, integers) and at most five values per reason: logids matching `^\d{10}$` (unmapped), `dhcp_msg` values (unexpected; cut to 32 characters, MAC- or IPv4-looking parts replaced by "…"), source names under `sources` (untrusted), and `first_day`, the oldest site day with a refused row within the last 100 days (the same predicate over `fg_dhcp`, a small table, primary-key range), so the operator knows from which day to rebuild | **medium** when at least one row is refused; **high** when at least 10 rows were considered and fewer than 10 % are usable | as soon as a check, re-reading the last 24 h with the current settings and sources, finds no refused row |
| `R20-lease:fortigate-unconverted` | (a) `fg_event` rows of the last 24 h, `subtype='system'`, `logid` a key of the map, and NOT `extra ?& array['ip','mac','dhcp_msg']`; (b) `parse_failures` of the last two days with pattern `fortigate.dhcp` | high | 24 h clean |
| `R20-lease:fortigate-renewals-missing` | **counted leases** (D26): `fg_dhcp` leases of trusted sources with `lease_seconds` NOT NULL that ended `timeout` in the last 24 h (`ix_leases_source_table_end`) and whose IP has trusted sessions *starting* (`time − duration`) both in [last DHCP evidence (the lease's newest own record, `refs.last_dhcp_evidence` without `at`) + `lease_seconds`/2 + 15 min, end] and in [end, end + 15 min] (`ix_fg_traffic_srcip_time`, each range bounded by `max_session_seconds`); at least 3 of them and at least 5 % of the `fg_dhcp` leases open or ended in those 24 h, **and** no `fg_renewal_ack` in the `leases.*` runs of the last 7 days (`ix_job_runs_job_started_at`), **and** either `dhcp.fortigate_format_verified` is false or `fg_renewal_ack` was seen in the 30 days before those 7 | high | 24 h clean |
| `R20-lease:fortigate-renewals-not-logged` | the same counted share, no `fg_renewal_ack` in the 30 days, and `dhcp.fortigate_format_verified` true (the accepted set-up of DEVICE-SETUP 2.4 step 5's negative branch, recorded in KURULUM §16) | info | 24 h clean, or when a renewal ACK is seen |
| `R20-lease:fortigate-decline:<net>` | an IP with at least two `fg_dhcp` leases in the last 24 h that ended `superseded` within 300 s of their start where the same MAC's `fg_dhcp` lease on another IP of the same configured network starts at that end (D33; `ix_leases_source_table_end`, `ix_leases_mac_start`); key and title name the network of the IP (`<net>`, `{network}`) and the count, the e-mail its VLAN number when it has one, never the IP; the details add at most five such addresses (`addresses`, shown on the Security page only, 2.16) | high | 24 h clean |
| `R20-lease:dhcp-sources-overlap:<net>` | within 24 h one configured network (subnet) has usable DHCP rows of two origins `(table, vd)` (D27); details: the origins, each rendered "MikroTik DHCP" or "FortiGate DHCP (VDOM <vd>)" ("FortiGate DHCP" without `vd`), `vd` cut to 32 characters and masked like every log-supplied value | high | 24 h clean |
| `R20-lease:fortigate-lease-unlimited` | usable `fg_dhcp` rows of the last 24 h with `lease = 0` (D6) | high | 24 h clean |
| `R20-lease:source-clock` | the checked sources: enabled, `unknown = false` (any type), with rows of `fg_traffic`, `fg_dhcp`, `mt_dhcp` or `mt_arp` in their spool files of today or yesterday (`search_counts` by `raw_day`, `ix_search_counts_raw_day_source`); per checked source its newest 200 `messages` rows by device time within [now − 1 h − `max_clock_skew`, now + 1 h + `max_clock_skew`] (`ix_messages_source_time`), of which those with `time_received` in the last hour and `time <> time_received` count; with at least 10 counted rows, their median of `time − time_received` beyond ±120 s (D28); details: at most five source names under `sources` and their offsets in whole seconds | high | as soon as a check finds no checked source beyond the limit |
| `R20-lease:fortigate-dhcp-mac` | usable `fg_dhcp` `assigned` rows of the last 24 h: a MAC with more than 3 distinct IPs within one origin, interface and configured network (D28, the key of D33; the rows, not open leases, because D33 closes the previous lease at every ACK); details: the count of such MACs | medium | 24 h clean |
| `R20-lease:srcmac-shared:<interface>` | for each `lookup.srcmac_interfaces` entry, the last hour of trusted traffic: some `srcmac` with more than `lookup.srcmac_max_ips` distinct **IPv4** `srcip` (`family(srcip) = 4`) | medium | 24 h clean |
| `R20-lease:fortigate-lease` | usable `fg_dhcp` rows of the last 24 h with `lease NOT NULL AND (lease < 300 OR lease > 8640000) AND lease <> 0` | low | 24 h clean |
| `R20-lease:fortigate-dhcp-subnet` | usable `fg_dhcp` rows of the last 24 h whose `ip` lies in no `vlans.subnet` (D28); details: at most five interface names and `first_day`, the oldest site day within the last 100 days with a usable row in no `vlans.subnet` (the same predicate over `fg_dhcp`, a small table, primary-key range, as `fortigate-refused` computes its own; round 7), so the operator knows from which day to rebuild once the network is entered | low | 24 h clean |
| `R20-lease:fortigate-unverified` | `fg_dhcp` rows exist (last 7 days) and `dhcp.fortigate_format_verified` is false (D29) | info | when the setting is true |
| `R20-nat:conversion-failed` | the status row `nat.egresses_migration` has `conversion_failed: true` and `nat.egresses` is still owned by `:migration` (D18) | high | when an administrator writes `nat.egresses` |
| `R20-nat:invalid` | the stored `nat.egresses` fails `parse_egresses` (a value written without validation, or one a later version's stricter rules refuse); details: the rule's error code, never the value | high | when the value parses |
| `R20-nat:legacy-changed` | an administrator-owned legacy row (`nat.public_ips`, `nat.wan_transip`, `lookup.port_hint`) whose `updated_at` is later than that of `nat.egresses` (written by the previous version during a rollback, D18; on a site with the shipped NAT defaults as well, where no status row exists); details: the key names | medium | when the condition no longer holds: an administrator writes `nat.egresses` |
| `R20-nat:unmapped-transip` | exits are configured (and parse) and, in the last hour of trusted `fg_traffic` (primary-key range), a `transip` that no exit claims, with a listed `trandisp`, carried at least 50 sessions (D20; an `internal` exit claims the addresses of NAT towards a DMZ, VLAN, VPN or branch, D13); details: the count and at most five such addresses (the firewall's own addresses), each marked private/shared (a line behind an ISP router that no exit describes, whose public IP lookups list nobody, or an internal NAT to declare as `internal`) or public (answered as observed, D31) | medium | when an exit claims every such address, or 24 h clean |

**Networks in keys and titles** (round 6). `<net>` in a dedup key is the network's VLAN number when
it has one, else its subnet in canonical text with `/` written `_` (`172.20.42.0_24`); `{network}`
in a title or text is "VLAN 42" / "VLAN 42", else the network's name and subnet ("Ofis
(172.20.42.0/24)" / "Ofis (172.20.42.0/24)"), both site configuration, not client data; the
describers' `vlan` fact is set only for a network with a VLAN number, so an e-mail never carries
a subnet or a network name.

Pre-upgrade `fg_event` DHCP rows carry all three keys and never trigger `fortigate-unconverted`
(a). Details hold what 2.16 allows: counts, a `cause` code, a `table` code (`fg_dhcp`, `mt_dhcp`,
`mt_arp`), VLAN numbers (else the network's subnet and name), offsets in seconds, a `first_day` and at most five logids, `dhcp_msg`
values, interface names, VDOM names, source names (under `sources`) or, for
`R20-nat:unmapped-transip`, the firewall's own NAT addresses, and for `fortigate-decline` the
declined pool addresses (the addresses where a static device answers; no MAC, no host name);
never a client MAC and no other client IP. Everything beyond counts, the cause, the table code
and VLAN numbers is shown on the Security page only, never in an e-mail.
Every log-supplied text in the details comes from rows of trusted sources only (an untrusted
sender's row is counted under `untrusted`, rule 1, before any other rule reads it), is cut to 32
characters and has MAC- or IPv4-looking parts replaced by "…". Every query is bounded by time
and an index (3.8: each under 1 s; `R20-nat:unmapped-transip` and `source-clock` under 0.2 s and
0.5 s).

**Catalogue (`alerts/texts.py`, WP-B).** E-mails and the Security page key their texts by rule
(1.2), so WP-B adds:
- `RULES["R20-lease"]` = RuleText("Kira kanıtı sorunu", "Lease evidence problem", "Uyarıdaki
  nedeni okuyun ve Kaynaklar → Kira kanıtı kutusunu açın (DEVICE-SETUP.md bölüm 2.4).", "Read
  the cause in the alert and open Sources → Lease evidence (DEVICE-SETUP.md section 2.4).");
  `RULES["R20-nat"]` = RuleText("İnternet çıkışı tanımı sorunu", "Internet exit definition
  problem", "Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet
  çıkışları (NAT) bölümünü denetleyin.", "Check Settings → Application settings → 5651 lookup
  and lease timeline → Internet exits (NAT).").
- Numbered runbooks per rule (`_STEPS["R20-lease"]`, `_STEPS["R20-nat"]`, TR/EN): 1. read the
  cause in the details (its own what-to-do text is shown there); 2. open Kaynaklar → Kira kanıtı
  (or the exit editor and its "Kayıtlarda görülen NAT adresleri" table); 3. follow DEVICE-SETUP
  §2 and 2.4–2.6 for the cause; a rebuild is done in Ayarlar → Uygulama ayarları → 5651 sorgu ve
  kira zaman çizelgesi → Kiraları yeniden oluştur (at most 31 days per request, from the day the
  alert's details name); 4. raw logs are not affected and are sealed; the alert closes by itself
  24 h after the problem ends (the `none` causes when a usable row arrives, the refused cause as
  soon as no refused row is left in the last 24 h, the clock cause at the next good measurement,
  the unverified and NAT causes when the setting is written).
- `LEASE_CAUSES` / `NAT_CAUSES`: per cause code the fixed one-line action (TR/EN) of the table
  below, without placeholders, used by the describers, as `CONFIG_CAUSES` is for
  `R20-seal-config`. It is the e-mail line and the Security page summary (`routers/alerts.py`
  puts `facts()` before the title, 1.2); WP-B, UYARILAR/ALERTS and the mail tests share it.
- Describers `_lease` and `_nat`: whitelisted facts are `cause` (one of the codes of the table
  below, else no facts), integers (`rows`, `count`, `kept`, `shared`, `seconds`), `vlan` (an
  integer 1–4094) and `table` (one of the three codes, rendered "FortiGate DHCP", "MikroTik DHCP",
  "MikroTik ARP-LOG"); they produce "<cause action> (<count> kayıt)" / "<cause action>
  (<count> records)" when a count is present, "<cause action> (<seconds> sn)" / "(<seconds>
  s)" for `source-clock`, and the table's name after the action for `none`. They never read
  `sources`, interface names, VDOM names, logids, `dhcp_msg` values, `first_day` or addresses, and
  call no `_ip`, so no e-mail carries them (2.16).
- Each finding's `title` is the cause's Turkish title of the texts below with its counts,
  `details.title_en` the English title, and `details.action_tr`/`action_en` the cause's
  what-to-do text, filled when the check runs and shown on the Security page (never in an
  e-mail).

**One-line actions (`LEASE_CAUSES`, `NAT_CAUSES`; fixed texts, no placeholders):**

| Cause | Türkçe | English |
|---|---|---|
| `none` | Kira kanıtı kesildi: bu kaynağın olay kayıtlarını syslog'a gönderdiğini denetleyin. | Lease evidence stopped: check that this source sends its event records to syslog. |
| `absent` | Kira kanıtı yok: 5651 sorguları iç IP adresinde biter (Kurulum kılavuzu bölüm 0.1). | No lease evidence: 5651 lookups end at the internal IP (installation guide section 0.1). |
| `fortigate-refused` | FortiGate DHCP kayıtları kullanılmıyor: uyarıdaki nedene göre ayarı düzeltip kiraları yeniden oluşturun. | FortiGate DHCP records are not used: fix what the alert names and rebuild the leases. |
| `fortigate-unconverted` | FortiGate DHCP kayıtları kira kanıtına çevrilemedi: kayıt biçimini sahada doğrulayın. | FortiGate DHCP records could not become lease evidence: verify the record format on site. |
| `fortigate-renewals-missing` | FortiGate DHCP yenilemeleri görünmüyor: önce kullanılmayan kayıtlara, sonra yenileme denetimine bakın. | FortiGate DHCP renewals do not appear: check refused records first, then do the renewal check. |
| `fortigate-renewals-not-logged` | FortiGate DHCP yenilemeleri bu kurulumda kaydedilmiyor: zincirler kira bitiminde biter (Kurulum kılavuzu bölüm 16). | FortiGate DHCP renewals are not logged at this installation: chains end when the lease ends (installation guide section 16). |
| `fortigate-decline` | DHCP havuzunda statik adresli bir cihaz olabilir: cihazı havuzun dışına taşıyın ya da adresini ayırın. | A static device may sit in the DHCP pool: move it out of the pool or reserve its address. |
| `dhcp-sources-overlap` | Aynı ağda iki DHCP kaynağı: fazla DHCP sunucusunu kapatın ya da ağları ayırın. | Two DHCP sources on one network: switch off the extra DHCP server or separate the networks. |
| `fortigate-lease-unlimited` | FortiGate süresiz kira veriyor: DHCP sunucusunda sınırlı bir lease-time ayarlayın. | The FortiGate gives unlimited leases: set a finite lease-time on its DHCP server. |
| `source-clock` | Bir log kaynağının saati kayık: cihazda NTP eşitlemesini açın. | A log source's clock is off: switch on NTP synchronisation on the device. |
| `fortigate-dhcp-mac` | Bir MAC adresi tek bir ağda çok sayıda FortiGate adresi aldı: arayüz yapısını denetleyin. | One MAC address got many FortiGate addresses on one network: check the interface setup. |
| `srcmac-shared` | srcmac arayüzünde paylaşılan MAC adresi: arayüzü srcmac listesinden çıkarın. | Shared MAC address on a srcmac interface: remove the interface from the srcmac list. |
| `fortigate-lease` | FortiGate kira süresi beklenmeyen bir değerde: lease alanının birimini sahada doğrulayın. | Unexpected FortiGate lease time: verify the unit of the lease field on site. |
| `fortigate-dhcp-subnet` | FortiGate DHCP kaydı tanımlı bir VLAN'a ait değil: ağı (VLAN numarası olmasa da) ekleyip kiraları yeniden oluşturun ya da arayüzü hariç tutun. | FortiGate DHCP record outside the configured VLANs: add the network (even without a VLAN number) and rebuild the leases, or exclude the interface. |
| `fortigate-unverified` | FortiGate DHCP kayıt biçimi sahada doğrulanmadı: DEVICE-SETUP.md bölüm 2.4'teki doğrulamayı yapın. | FortiGate DHCP record format not verified on site: do the check of DEVICE-SETUP.md section 2.4. |
| `conversion-failed` | Eski NAT ayarları aktarılamadı: internet çıkışlarını tanımlayın. | The old NAT settings could not be converted: define the internet exits. |
| `invalid` | İnternet çıkışları ayarı okunamıyor: genel IP ile 5651 sorguları çalışmaz; ayarı düzeltin. | The internet exits setting cannot be read: 5651 lookups by public IP fail; fix the setting. |
| `legacy-changed` | Geri almada değiştirilen eski NAT ayarları çıkışlara aktarılmadı: değişikliği çıkışlara elle işleyin. | Old NAT settings changed during a rollback were not carried over: enter the change in the exits by hand. |
| `unmapped-transip` | Hiçbir çıkışa tanımlı olmayan NAT adresi kullanılıyor: hattı çıkış olarak tanımlayın. | A NAT address no exit defines is in use: define the line as an exit. |

**Texts (title; what to do):**

- `fortigate-unconverted`:
  - TR: "FortiGate DHCP olay kaydı kira kanıtına çevrilemedi"; "Son 24 saatte {rows} kayıt DHCP
    logid'iyle geldi ama beklenen alanlar (ip, mac, dhcp_msg) yok ya da okunamadı ({failures}
    ayrıştırma hatası). Bu kayıtlardaki adresler için 5651 sorguları iç IP adresinde durabilir.
    Kayıt biçimi bu FortiOS sürümünde farklı olabilir: ham satırı Canlı Akış ya da Arama'da açın
    ve sahada doğrulama adımını izleyin (DEVICE-SETUP.md bölüm 2.4)."
  - EN: "A FortiGate DHCP event could not be turned into lease evidence"; "In the last 24 hours
    {rows} records came with a DHCP logid but without the expected fields (ip, mac, dhcp_msg),
    or with values that could not be read ({failures} parse failures). 5651 lookups for these
    addresses may stop at the internal IP. The record format may differ in this FortiOS
    version: open the raw line in Live tail or Search and follow the on-site check
    (DEVICE-SETUP.md section 2.4)."
- `none` (`{evidence}` is "FortiGate DHCP", "MikroTik DHCP" or "MikroTik ARP-LOG" in both
  languages; each language's text is filled when the check runs):
  - TR: "Kira kanıtı kesildi: {evidence} kayıtları gelmiyor"; "Son {hours} saatte kullanılabilir
    hiçbir {evidence} kaydı gelmedi (daha önce geliyordu), ancak FortiGate iç ağdan trafik
    kaydediyor. Bu kayıtlara dayanan 5651 zincirleri bu sürede kurulamayabilir: FortiGate
    DHCP'de yeni ve yenilenen kiralar, ARP-LOG'da statik IP'li cihazlar, MikroTik DHCP'de yeni
    istemciler. Başka bir kaynağın kayıtları gelmeye devam etse de bu kaynak kesilmiş olabilir.
    Kaynağın olay kayıtlarını syslog'a gönderdiğini denetleyin (FortiGate DHCP: DEVICE-SETUP.md
    bölüm 2.4, event/system kayıtları ve syslog filtresi; MikroTik: bölüm 3, DHCP konusu ve
    ARP-LOG betiği); kayıtlar geliyor ama kullanılmıyorsa ayrıca R20-lease:fortigate-refused
    uyarısına bakın. MikroTik DHCP için uyarı orta önemdedir ve yalnızca ARP-LOG kaydı da
    gelmiyorsa açılır: RouterOS yenilemeleri kaydetmez, yeni istemcinin gelmediği sessiz bir
    dönem de böyle görünür."
  - EN: "Lease evidence stopped: no {evidence} records"; "In the last {hours} hours no usable
    {evidence} record arrived (they did before), while the FortiGate logs traffic from the
    internal network. 5651 chains that rest on these records may not be built meanwhile: new and
    renewed leases for FortiGate DHCP, static-IP devices for ARP-LOG, new clients for MikroTik
    DHCP. This source may have stopped although another source's records keep arriving. Check
    that the source sends its event records to syslog (FortiGate DHCP: DEVICE-SETUP.md section
    2.4, the event/system records and the syslog filter; MikroTik: section 3, the DHCP topic and
    the ARP-LOG script); if records arrive but are not used, see the alert
    R20-lease:fortigate-refused as well. For MikroTik DHCP the alert is of medium severity and
    opens only while no ARP-LOG record arrives either: RouterOS logs no renewals, and a quiet
    period without new clients looks the same."
- `absent`:
  - TR: "Kira kanıtı yok: 5651 sorguları iç IP adresinde biter"; "Son 30 günde kullanılabilir
    hiçbir DHCP ya da ARP-LOG kaydı gelmedi, ancak FortiGate iç ağdan trafik kaydediyor. Bu,
    Kurulum kılavuzu bölüm 0.1'de anlatılan bir yapı olabilir (MikroTik ARP-LOG olmadan başka
    bir DHCP sunucusu, yalnızca statik adresli bir FortiGate ağı): 5651 sorguları FortiGate
    oturumunda ve iç IP adresinde biter. Kira kanıtı isteniyorsa FortiGate ya da MikroTik DHCP
    kayıtlarını (DEVICE-SETUP.md bölüm 2.4 ve 3) ya da ana MikroTik yönlendiricide ARP-LOG
    betiğini açın."
  - EN: "No lease evidence: 5651 lookups end at the internal IP"; "In the last 30 days no usable
    DHCP or ARP-LOG record arrived, while the FortiGate logs traffic from the internal network.
    This may be a set-up described in the installation guide section 0.1 (another DHCP server
    without MikroTik ARP-LOG, a FortiGate network with static addresses only): 5651 lookups end
    at the FortiGate session and the internal IP address. For lease evidence, switch on the
    FortiGate or MikroTik DHCP records (DEVICE-SETUP.md sections 2.4 and 3) or the ARP-LOG script
    on the main MikroTik router."
- `fortigate-refused`:
  - TR: "FortiGate DHCP kayıtlarının bir kısmı kullanılmıyor" (high: "FortiGate DHCP kayıtları
    geliyor ama kullanılmıyor"); "Son 24 saatte gelen {rows} FortiGate DHCP kaydının {refused}
    tanesi kira kanıtı sayılmadı ({kept} tanesi sayıldı; hariç tutulan arayüzlerin {excluded}
    kaydı bu sayılara girmez). Nedenler (kayıt sayısı ve görülen değerler): {reasons}. Bu
    kayıtlardaki adresler için açık kiralar süreleri dolunca kapanır ve 5651 sorguları iç IP
    adresinde durabilir. Nedene göre: tanınmayan logid için dhcp.fortigate_logids, beklenmeyen
    dhcp_msg değeri için dhcp.fortigate_dhcp_msgs (anlamını sahada doğrulayın), cihaz saati için
    FortiGate'in saati (NTP, DEVICE-SETUP.md bölüm 2), kullanılamayan MAC adresi için arayüz
    yapısı, tanımsız gönderici için log kaynağı tanımı (DEVICE-SETUP.md bölüm 4; gönderici kurumun
    FortiGate'iyse onu ekleyin, değilse kaynağını araştırın); sonra {first_day} gününden bugüne
    kiraları Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden
    oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün). Uyarı, son 24 saatte
    kullanılmayan kayıt kalmayınca kapanır; cihaz saati ve kullanılamayan MAC adresi nedeniyle
    kullanılmayan kayıtlar 24 saat dolana kadar sayılır."
  - EN: "Some FortiGate DHCP records are not used" (high: "FortiGate DHCP records arrive but are
    not used"); "Of the {rows} FortiGate DHCP records of the last 24 hours {refused} were not
    used as lease evidence ({kept} were; the {excluded} records of excluded interfaces are not in
    these numbers). Reasons (records and values seen): {reasons}. For the addresses of these
    records open leases close when their time runs out and 5651 lookups may stop at the internal
    IP. By reason: dhcp.fortigate_logids for an unknown logid, dhcp.fortigate_dhcp_msgs for an
    unexpected dhcp_msg value (verify its meaning on site), the FortiGate's clock for the device
    time (NTP, DEVICE-SETUP.md section 2), the interface setup for an unusable MAC address, the
    log source definition for an unconfigured sender (DEVICE-SETUP.md section 4; if it is the
    organisation's FortiGate add it, otherwise find out where the records come from); then
    rebuild the leases from {first_day} to today in Settings → Application settings → 5651
    lookup and lease timeline → Rebuild leases (at most 31 days per request). The alert closes
    once no record of the last 24 hours is refused; records refused for the device time or an
    unusable MAC address stay counted until 24 hours have passed."
- `fortigate-renewals-missing`:
  - TR: "FortiGate DHCP yenilemeleri kayıtlarda görünmüyor"; "Son 24 saatte {count} FortiGate
    kirası, IP adresi kira süresinin ikinci yarısında ve bitiminden hemen sonra da kullanıldığı
    hâlde yenileme kaydı olmadan kapandı; son 7 günde hiçbir yenileme (kira süresinin
    yarısında gelen DHCP Ack) kaydı görülmedi. Bu istemcilerin 5651 zincirleri kira bitiminde
    kesiliyor. Önce R20-lease:fortigate-refused uyarısına bakın: yenileme kayıtları,
    kullanılmayan kayıtlar arasında olabilir (ör. farklı bir dhcp_msg değeri ya da cihaz saati).
    Öyle değilse FortiGate yenilemeleri kaydetmiyor olabilir: DEVICE-SETUP.md bölüm 2.4
    adım 5'teki yenileme denetimini yapın; denetimin sonucu ne yapılacağını anlatır."
  - EN: "FortiGate DHCP renewals do not appear in the logs"; "In the last 24 hours {count}
    FortiGate leases closed without a renewal record although their IP address was in use in
    the second half of the lease and right after its end; no renewal (a DHCP Ack at half the
    lease) was seen in the last 7 days. These clients' 5651 chains end when the lease ends.
    First see the alert R20-lease:fortigate-refused: the renewal records may be among the
    records that are not used (e.g. another dhcp_msg value or the device time). If not, the
    FortiGate may not log renewals: do the renewal check of DEVICE-SETUP.md section 2.4 step 5;
    its outcome says what to do."
- `fortigate-renewals-not-logged`:
  - TR: "FortiGate DHCP yenilemeleri bu kurulumda kaydedilmiyor"; "Son 24 saatte {count} FortiGate
    kirası, IP adresi kira süresinin ikinci yarısında ve bitiminden hemen sonra da kullanıldığı
    hâlde yenileme kaydı olmadan kapandı; son 30 günde hiçbir yenileme (kira süresinin yarısında
    gelen DHCP Ack) kaydı görülmedi ve kayıt biçimi sahada doğrulandı olarak işaretli. Bu,
    DEVICE-SETUP.md bölüm 2.4 adım 5'teki yenileme denetiminin olumsuz sonucu olabilir: sürekli
    açık istemcilerin 5651 zincirleri kira süresinin yarısından sonra düşük güvenle gösterilir ve
    kira bitiminde kesilir. Denetimin sonucunu ve kurumun seçimini Kurulum kılavuzu bölüm 16'da
    denetleyin; yenilemeler kaydediliyorsa önce R20-lease:fortigate-refused uyarısına bakın."
  - EN: "FortiGate DHCP renewals are not logged at this installation"; "In the last 24 hours
    {count} FortiGate leases closed without a renewal record although their IP address was in
    use in the second half of the lease and right after its end; no renewal (a DHCP Ack at half
    the lease) was seen in the last 30 days, and the record format is marked as verified on site.
    This may be the negative outcome of the renewal check of DEVICE-SETUP.md section 2.4 step 5:
    always-on clients' 5651 chains are shown with low confidence after half the lease and end when
    the lease ends. Check the recorded outcome and the organisation's choice in the installation
    guide section 16; if renewals are logged, see the alert R20-lease:fortigate-refused first."
- `fortigate-decline`:
  - TR: "{network} DHCP havuzunda statik adresli bir cihaz olabilir"; "Son 24 saatte
    {network} alt ağında {count} kez bir istemci FortiGate'ten aldığı adresi birkaç dakika içinde
    (en çok 5 dakika) bırakıp başka bir adres aldı; aynı adreste bu birden çok kez oldu. Bu,
    adresin ağda başka bir cihazca (havuz içinde statik adres) kullanıldığını gösterebilir
    (DHCPDECLINE; FortiOS'un bunu kaydettiği belgelenmemiştir). O cihaz açık değilken adresi alan
    bir istemci olursa iki cihaz aynı adresi
    kullanır ve sorgu yanlış kişiyi gösterebilir. Statik cihazı havuzun dışına taşıyın ya da
    adresini FortiGate'te ayırın (Güvenlik sayfasında uyarının ayrıntıları ilgili adresleri, en
    çok beşini, gösterir)."
  - EN: "A static device may sit in the DHCP pool of {network}"; "In the last 24 hours, in the
    subnet of {network}, a client {count} times left an address the FortiGate had given it
    within minutes (at most 5) and took another; this happened more than once on one address.
    Another device may use that address (a static address inside the pool; DHCPDECLINE, which
    FortiOS is not documented to log). If a client gets the address while that device is off, two
    devices use one address and
    a lookup may show the wrong person. Move the static device out of the pool or reserve its
    address on the FortiGate (on the Security page the alert's details show the addresses
    concerned, at most five)."
- `dhcp-sources-overlap`:
  - TR: "Aynı ağda iki DHCP kaynağı: {network}"; "Son 24 saatte {network} alt ağında {sources}
    kaynaklarından DHCP kaydı geldi. Aynı ağa iki DHCP sunucusu desteklenmez: bir sunucunun
    verdiği adres ötekinin kirasını kapatır ve sorgu son kaydı yazan sunucuya göre yanıt verir.
    Bu anlardaki zincirler düşük güvenle gösterilir. Fazla DHCP sunucusunu kapatın ya da
    ağları ayırın; geçiş sırasında bu uyarı beklenir."
  - EN: "Two DHCP sources on one network: {network}"; "In the last 24 hours DHCP records for
    the subnet of {network} came from {sources}. Two DHCP servers on one network are not
    supported: an address one server gives closes the other's lease, and the lookup answers by
    whichever server logged last. Chains at those moments are shown with low confidence.
    Switch off the extra DHCP server or separate the networks; during a migration this alert is
    expected."
- `fortigate-dhcp-mac`:
  - TR: "Bir MAC adresi tek bir ağda çok sayıda FortiGate adresi aldı"; "Son 24 saatte {shared}
    MAC adresine aynı arayüz ve ağda 3'ten fazla farklı IP adresi verildi. Bu, istemcinin MAC
    adresini göstermeyen bir yapı (ör. IPsec üzerinden DHCP, MAC adresini değiştiren bir
    tekrarlayıcı) ya da paylaşılan veya sanal bir MAC adresi olabilir; böyle bir MAC adresinin
    zinciri yalnızca son adres verilen istemciye gider ve yanlış cihazı gösterebilir. IPsec
    üzerinden DHCP veren arayüzü dhcp.fortigate_interfaces_excluded ayarına ekleyin; bir ağ
    cihazının MAC adresiyse onu altyapı cihazı olarak işaretleyin; değilse arayüz ve VLAN
    yapısını denetleyin."
  - EN: "One MAC address got many FortiGate addresses on one network"; "In the last 24 hours
    {shared} MAC addresses were given more than 3 distinct IP addresses on one interface and
    network. This may be a setup that does not show the client's MAC address (e.g. DHCP over IPsec,
    a repeater that rewrites MAC addresses) or a shared or virtual MAC address; the chain of such
    a MAC address only follows the client that got the latest address and may show the wrong
    device. Add an interface that serves DHCP over IPsec to dhcp.fortigate_interfaces_excluded;
    mark the MAC address as an infrastructure device if it is a network device's; otherwise check
    the interface and VLAN setup."
- `fortigate-lease`:
  - TR: "FortiGate DHCP kira süresi beklenmeyen bir değerde"; "{rows} kayıtta lease değeri 300
    saniye ile 100 gün arasında değil; bu kiralarda ayarlardaki süreler kullanıldı. lease
    alanının birimini sahada doğrulayın."
  - EN: "Unexpected FortiGate DHCP lease time"; "In {rows} records the lease value is not
    between 300 seconds and 100 days; those leases used the configured times. Verify the unit
    of the lease field on site."
- `fortigate-lease-unlimited`:
  - TR: "FortiGate DHCP süresiz kira veriyor: zincirler kesiliyor"; "{rows} kayıtta süresiz kira
    (lease=0) var: bu istemciler adreslerini yenilemez ve kira kaydı ayarlardaki süre dolunca
    kapanır; sonrasında 5651 zinciri kurulamaz. FortiGate DHCP sunucusunda sınırlı bir
    lease-time ayarlayın."
  - EN: "The FortiGate DHCP server gives unlimited leases: chains are cut"; "{rows} records carry
    an unlimited lease (lease=0): these clients never renew and their lease closes when the
    configured time runs out; after that no 5651 chain can be built. Set a finite lease-time on
    the FortiGate DHCP server."
- `fortigate-dhcp-subnet`:
  - TR: "FortiGate DHCP kaydı tanımlı bir VLAN'a ait değil"; "Son 24 saatte {rows} FortiGate DHCP
    kaydının IP adresi hiçbir VLAN alt ağında değil (arayüzler: {interfaces}). Bu adreslere
    dayanan 5651 zincirleri düşük güvenle gösterilir: bir istemcinin adresi reddedip başka bir
    adres alması yalnızca tanımlı ağlarda anlaşılır. Bir VPN havuzu, girilmemiş bir ağ ya da yeni
    bir DHCP kapsamı olabilir: ağı, FortiGate'teki DHCP kapsamının tamamıyla ve VLAN numarası
    olmasa da, ekleyin (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar → VLAN ekle); sonra
    {first_day} gününden bugüne kiraları Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman
    çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün):
    eklenen ya da değiştirilen bir ağ geçmiş FortiGate kiralarına ancak yeniden oluşturmadan sonra
    uygulanır. IPsec arayüzüyse onu dhcp.fortigate_interfaces_excluded ayarına ekleyin."
  - EN: "FortiGate DHCP record outside the configured VLANs"; "In the last 24 hours the IP
    address of {rows} FortiGate DHCP records lies in no VLAN subnet (interfaces: {interfaces}).
    5651 chains on these addresses are shown with low confidence: a client that declines an
    address and takes another is only detected in configured networks. It may be a VPN pool, a
    network not entered or a new DHCP scope: add the network, with the FortiGate's whole DHCP
    scope and even without a VLAN number (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar → VLAN
    ekle); then rebuild the leases from {first_day} to today in Settings → Application settings →
    5651 lookup and lease timeline → Rebuild leases (at most 31 days per request): a network added
    or changed applies to past FortiGate leases only after a rebuild. For an IPsec interface, add
    it to dhcp.fortigate_interfaces_excluded."
- `fortigate-unverified`:
  - TR: "FortiGate DHCP kayıt biçimi sahada doğrulanmadı"; "FortiGate DHCP kayıtları kira kanıtı
    olarak kullanılıyor, ancak kayıt biçimi yalnızca FortiOS belgelerine ve tek bir topluluk
    örneğine dayanıyor. DEVICE-SETUP.md bölüm 2.4'teki doğrulamayı yapıp
    dhcp.fortigate_format_verified ayarını açın; o zamana kadar raporlar bunu belirtir."
  - EN: "FortiGate DHCP record format not verified on site"; "FortiGate DHCP records are used as
    lease evidence, but their format rests only on the FortiOS documentation and one community
    sample. Do the check of DEVICE-SETUP.md section 2.4 and switch on
    dhcp.fortigate_format_verified; until then reports say so."
- `source-clock`:
  - TR: "Bir log kaynağının saati kayık"; "{sources} kaynaklarının son kayıtlarının zamanları,
    ARCILOGGER'ın alma zamanından ortanca olarak {seconds} saniye farklı (sınır 120 saniye). Bu kaynağın kayıtlarıyla yapılan kesin ve aday 5651 sorguları bu kadar kayar: doğru
    oturum bulunmayabilir ve aynı portu sonra kullanan başka bir oturum bulunabilir; FortiGate
    DHCP kiraları da aynı kadar kayar. Cihazda NTP eşitlemesini açın (FortiGate: config system
    ntp → set ntpsync enable, DEVICE-SETUP.md bölüm 2; başka bir cihazda kendi NTP istemcisi).
    Bu kaynağın kayıtlarına dayanan sorgular kaymayı kendileri ölçer ve zincirleri düşük
    güvenle, nedeniyle birlikte gösterir."
  - EN: "A log source's clock is off"; "The times of the latest records of {sources} differ
    from ARCILOGGER's receive time by a median of {seconds} seconds (limit 120 seconds). Exact
    and candidate 5651 lookups on these records are shifted by that much: the right session may
    be missed and another session that used the same port later may be found; FortiGate DHCP
    leases are shifted by the same amount. Switch on NTP synchronisation on the device
    (FortiGate: config system ntp → set ntpsync enable, DEVICE-SETUP.md section 2; another device:
    its own NTP client). Lookups that rest on this source's records measure the offset themselves
    and show their chains with low confidence, stating why."
- `R20-nat:conversion-failed`:
  - TR: "Eski NAT ayarları internet çıkışlarına aktarılamadı"; "Yükseltmede eski NAT ayarları
    (nat.public_ips, nat.wan_transip, lookup.port_hint) geçerli bir internet çıkışı tanımına
    çevrilemedi; çıkışlar boş bırakıldı ve sorgular şimdi tek bir çift NAT çıkışı varsayıyor.
    Eski değerler denetim kaydındadır (settings.change). Hatlarınızı Ayarlar → Uygulama ayarları
    → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde tanımlayın."
  - EN: "The old NAT settings could not be converted to internet exits"; "During the upgrade the
    old NAT settings (nat.public_ips, nat.wan_transip, lookup.port_hint) could not be turned
    into a valid internet exit definition; the exits were left empty and lookups now assume one
    double-NAT exit. The old values are in the audit log (settings.change). Define your lines in
    Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT)."
- `R20-nat:invalid`:
  - TR: "İnternet çıkışları ayarı okunamıyor"; "Kayıtlı nat.egresses değeri bu sürümün
    kurallarıyla okunamıyor ({code}); genel IP adresiyle yapılan 5651 sorguları 503 hatasıyla
    durur ve NAT uyarıları denetlenemez. Ayarı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira
    zaman çizelgesi → İnternet çıkışları (NAT) bölümünde düzeltin; eski değer denetim kaydındadır
    (settings.change)."
  - EN: "The internet exits setting cannot be read"; "The stored nat.egresses value cannot be
    read by this version's rules ({code}); 5651 lookups by public IP stop with error 503 and the
    NAT alerts cannot be checked. Fix the setting in Settings → Application settings → 5651 lookup
    and lease timeline → Internet exits (NAT); the old value is in the audit log
    (settings.change)."
- `R20-nat:legacy-changed`:
  - TR: "Geri almada değiştirilen eski NAT ayarları çıkışlara aktarılmadı"; "Önceki sürüme geri
    dönüldüğü sırada eski NAT ayarları ({keys}) internet çıkışlarının son kaydından sonra
    değiştirildi. Yükseltme eski ayarları yeniden çevirmez; sorgular şimdi çıkışların bugünkü
    tanımını kullanıyor. Eski ayarlardaki değişikliği (denetim kaydında settings.change) Ayarlar
    → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)
    bölümüne elle işleyin; uyarı çıkışlar kaydedilince kapanır."
  - EN: "Old NAT settings changed during a rollback were not carried over"; "While the previous
    version was running after a rollback, the old NAT settings ({keys}) were changed after the
    internet exits were last saved. The upgrade does not convert old settings again; lookups now
    use the exits as they are defined today. Enter the change of the old settings (in the audit
    log as settings.change) by hand in Settings → Application settings → 5651 lookup and lease
    timeline → Internet exits (NAT); the alert closes once the exits are saved."
- `srcmac-shared`:
  - TR: "srcmac arayüzünde paylaşılan MAC adresi: {interface}"; "Son bir saatte {interface}
    arayüzünde {macs} MAC adresi birden çok IPv4 adresiyle görüldü (bir MAC adresinde en çok
    {count} farklı IPv4 adresi; sınır {limit}); bu arayüz yönlendirilmiş bir ağa bakıyor olabilir.
    Arayüzü lookup.srcmac_interfaces ayarından çıkarın ya da ağ yapısını kontrol edin. Bu MAC
    adresleri sorgularda kullanılmıyor."
  - EN: "Shared MAC address on a srcmac interface: {interface}"; "In the last hour {macs} MAC
    addresses were seen with more than one IPv4 address on interface {interface} (at most
    {count} distinct IPv4 addresses for one MAC address; limit {limit}); the interface may face a
    routed network. Remove it from lookup.srcmac_interfaces or check the network. These MAC
    addresses are not used in lookups."
- `R20-nat:unmapped-transip`:
  - TR: "Hiçbir internet çıkışına tanımlı olmayan NAT adresi kullanılıyor"; "Son bir saatte
    FortiGate {count} oturumu hiçbir çıkışın tanımlamadığı kaynak NAT adresleriyle çıkardı:
    {addresses}. Özel ya da paylaşılan bir adres, servis sağlayıcı yönlendiricisinin arkasındaki
    tanımsız bir hat olabilir: o hattın genel IP'si sorulduğunda kimse listelenmez; ya da
    FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e veya şubeye yaptığı NAT'ın adresi olabilir:
    onu 'İnternet çıkışı değil' türüyle tanımlayın. Genel bir adres FortiGate'in kendi adresidir
    ve sorgularda uyarıyla 'kayıtlarda görülen adres' olarak yanıtlanır. Adresi Ayarlar →
    Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde
    tanımlayın ('Kayıtlarda görülen NAT adresleri' → 'Çıkış olarak ekle')."
  - EN: "A NAT address that no internet exit defines is in use"; "In the last hour the FortiGate
    sent {count} sessions out with source-NAT addresses that no exit defines: {addresses}. A
    private or shared address may be an undefined line behind an ISP router, so that a lookup for
    that line's public IP lists nobody; or the address of the FortiGate's NAT towards a DMZ,
    another VLAN, a VPN or a branch: define it with the type 'Not an internet exit'. A public
    address is the FortiGate's own address and is answered in lookups as an 'address seen in the
    logs', with a warning. Define the address in Settings → Application settings → 5651 lookup
    and lease timeline → Internet exits (NAT) ('NAT addresses seen in the logs' → 'Add as
    exit')."

`UYARILAR.md`/`ALERTS.md` §2 gain every row of the table (5651 Temel, with severity).
`ILK-ADIMLAR.md`/`FIRST-STEPS.md` §9 gain `R20-lease` and `R20-nat` with one line each, in
WP-B's merge (the docs test collects every rule constant of `*/src`, 1.2, so the merge that adds
the constants must document them; one hand-over of those two files to WP-B, 4.4). The alerts
page shows the backend texts; if the web has a per-rule label map, it gains `R20-lease` and
`R20-nat` (WP-E).

### 2.7 Attribution (WP-C)

- `StoredLease` gains `lease_seconds` and `source_table`, which it already has, and passes both
  into `LeaseInfo`, with `last_dhcp_evidence` **for the lookup moment `at`** (round 5): the
  lease's own newest DHCP record at or before `at`, its own release included, **never the line
  that ended a `superseded` lease** and never a record after `at`.
  - **`fg_dhcp` leases:** the attributor (`_single` has `at`) reads it with one `LIMIT 1` query
    per chain on `ix_fg_dhcp_ip_time`: `fg_dhcp` rows with `ip` and `mac` of the lease, `time`
    in `[lease.start, at]` and `trust.kept(settings, untrusted)` (so an assigned or deassigned
    logid of a trusted source), `ORDER BY time DESC`. The stored refs cannot be used for it:
    they keep the opening ACK and the last 6 continuations (`CONTINUATIONS_KEPT`), so the record
    just before `at` may be gone, and the newest ones may lie after `at`. Rows of `[lease.start,
    at]` of the pair are this lease's own (a new interval of the pair starts only after this one
    closed; the line that ended a superseded lease is another MAC's, another IP's or the other
    table's, and lies at `end >= at`). When the query finds no row (rows refused under settings
    changed since the build), `refs.last_dhcp_evidence(lease.evidence, lease.end,
    lease.end_reason, at)` is the fallback.
  - **Other tables:** `refs.last_dhcp_evidence(lease.evidence, lease.end, lease.end_reason, at)`.
  - **Why per moment.** A daily client that leaves without a RELEASE and comes back inside its
    lease (Mon 08:00 ACK, lease 86400, gone at 17:30, back Tue 07:50) gets a continuation ACK of
    the same interval; measured against that later ACK, a lookup of Mon 22:15, when only a static
    or squatting device could have used the address, would stay `high` (and the PDF would print a
    "last DHCP evidence" after the moment). The stored evidence of a superseded lease holds the
    line that ended it (1.2): another client's ACK, or after D33 this MAC's ACK for another IP,
    always at the lease's end. Counted as the lease's own, it would make `lease_renewal_not_seen`
    impossible exactly when the FortiGate gave the address away before the lease ended (a phone
    gone without a logged RELEASE at 10:00, the next ACK of the address to another device the next
    morning: a lookup at 23:00 would stay `high`), and the PDF would print the other client's ACK
    time as the holder's last DHCP evidence.
- `_open_limit` becomes `last + settings.lease_timeout_seconds(lease.server, lease.lease_seconds)`.
- `LookupSettings.lease_timeout_seconds(server, lease_seconds=None)` (day 0) is the one formula
  shared with `TimelineRules`: `lease_seconds` itself when given (a logged FortiGate lease, the
  FortiGate's own end, D6), otherwise the configured server time × `lease.timeout_percent` / 100
  as today. A test asserts that the two agree for every source.
- `covers()` keeps `<=` for every DHCP table and `<` for ARP. `arp_only` is unchanged.
- **FortiGate notes on a chain** whose lease is `fg_dhcp`:
  - `fortigate_format_unverified` (info) while `dhcp.fortigate_format_verified` is false, and
    `fortigate_format_verified` (info, `{at}`, `{by}` from that setting row, which `run_lookup`
    reads itself, 2.10) once it is true (D29);
  - `lease_renewal_not_seen` (warning) when `at > last_dhcp_evidence + lease / 2`, with
    `last_dhcp_evidence` the per-moment value above and `lease` that record's logged lease when it
    is usable (300–8,640,000 s with `use_logged_lease`), else the lease's `lease_seconds`, else
    its configured server time: the lease **and** the chain get confidence `low` (D6), the same
    pattern as the `low` of D10 and D27.
  - `fortigate_dhcp_no_network` (warning; D33, round 6) when the chain's IP lies in no configured
    network (`find_vlan` gives None; `_single` reads it already): D33 cannot close a declined
    address there, so the lease **and** the chain get confidence `low`.
- **Unconverted FortiGate DHCP lines after the lease** (`fortigate_dhcp_not_converted_since_lease`,
  warning; D8, round 5 for `fg_dhcp`, round 6 for every DHCP chain), after the lease steps above
  and D27, for each chain or alternative whose lease is `mt_dhcp` or `fg_dhcp` and that is still
  `high` or `medium`: a mapped FortiGate DHCP line in `fg_event` (the probe's branch of 2.8.3:
  `subtype = 'system'`, `logid = ANY(keys of dhcp.fortigate_logids)`, either action, sources
  outside `untrusted_sources`, `ix_fg_event_subtype_time`) with `time` in `(last_dhcp_evidence,
  at]`, the lower end raised to `at` − the D8 probe window (8,640,000 s) for a MikroTik lease
  kept open by traffic, that names **the chain's IP** (`extra->>'ip'`) **or the chain's MAC**
  (`lower(replace(extra->>'mac', '-', ':'))`, the parser's MAC form; the field's format is V1,
  sahada doğrula). A row matched by its MAC counts unless the chain's IP and the row's `ip` both
  lie in configured networks and those differ (D33's key: a laptop's ACK on another VLAN says
  nothing about this one; an unreadable `ip`, or an address outside every configured network on
  either side, counts). **One query per lookup** serves every such
  chain, with no cap on their number: `extra->>'ip' = ANY(:ips) OR <normalised mac> = ANY(:macs)`
  over the union of the chains' windows, returning the matching rows' `ip`, normalised `mac` and
  `time`, which are matched to each chain's own window in memory (a healthy site returns none).
  A match is a FortiGate DHCP line that did not enter the lease timeline (loaded before the
  upgrade, loaded by the previous version after a rollback, or fields that could not be read): a
  RELEASE, another client's ACK for the address, or the MAC's ACK for another address (a move
  the timeline could not apply: D5 and D33's MikroTik variant), so the chain and its alternatives
  get confidence `low` and the text. Nothing is cited, and no value of the row (MAC, host name)
  enters the outcome (it is matched in the query and in memory only): the row may be another
  client's (KVKK, 2.16). For an `fg_dhcp` chain the window is at most half a lease (past it,
  `lease_renewal_not_seen` has lowered the chain already). After a normal upgrade such rows
  exist only before the upgrade, in a rollback window or as unconverted lines, which
  `R20-lease:fortigate-unconverted` names.
- **A MAC move the lease build could not apply** (round 7; D33; the same key and text): the
  same check also reads **converted** rows, because a lease built while either address lay in no
  configured network, or in a network entered narrower than the DHCP scope, kept the close D33
  missed, and entering the network later rebuilds nothing (D33). For each chain or alternative
  whose lease is `fg_dhcp` or `mt_dhcp` and that is still `high` or `medium` at that point (after
  D27 and the `fg_event` branch), an `fg_dhcp` row that `trust.kept(settings, untrusted)` accepts,
  with a logid mapped to
  `assigned` in `dhcp.fortigate_logids`, of **the chain's MAC for another IP**, with `time` in
  `(last_dhcp_evidence, at)` (the lower end as above, raised to `at` − 8,640,000 s for a MikroTik
  lease, and `lease.start` when the lease has no DHCP record at or before `at`; the upper end is
  open because a row exactly at `at` that the timeline applied ended the lease at `at`), lowers
  the chain and its alternatives to `low` with `fortigate_dhcp_not_converted_since_lease`. For an
  `fg_dhcp` chain the row must also carry the lease's interface (`lease.server`) and, when the
  per-moment query found the lease's own newest row (it then also returns that row's
  `extra->>'vd'`), its VDOM: D33's origin and interface. For an `mt_dhcp` chain any interface
  and VDOM counts (the MikroTik variant has no such key). The row counts under the MAC rule
  above: unless both addresses lie in configured networks today and those differ (a laptop or a
  NAS on two configured networks stays `high`). Built with today's networks and settings, D33
  closes such a lease at that row, so a healthy timeline never shows one, and a lease still open
  past it is exactly the stale build. **One query per lookup** on `ix_fg_dhcp_mac_time` (`mac =
  ANY(:macs)` over the union of the chains' windows; `fg_dhcp` is small) returns the rows' `ip`,
  `interface`, `vd` and `time`, matched to each chain in memory; nothing is cited and no value of
  the row enters the outcome (KVKK, 2.16). It covers a network entered after the build, a
  network that does not cover the whole DHCP scope, a skipped rebuild and gaps longer than the
  rebuild's 31 days alike.
- **Two DHCP origins** (D27), one rule: `trust.usable_dhcp_origins` runs **one query per lookup**
  for the VLAN subnets of all its chains, over `[T − W, T + W]` with W the largest alive window
  of those chains' covering leases, at least the longest configured DHCP window
  (`lease_timeout_seconds` over the configured servers); `mt_dhcp` and `fg_dhcp` primary-key time
  ranges, `ip <<= ANY(subnets)`, usable rows only (`kept` applies to `fg_dhcp` rows only; an
  `mt_dhcp` row is usable with action `assigned`/`deassigned`, whatever `untrusted` holds, D24;
  DHCP tables are small; 3.8 guards it). A chain
  whose subnet shows two or more origins `(table, vd)` gets confidence `low` and
  `dhcp_sources_overlap` (the network and the origins rendered as in 2.6.3, "MikroTik DHCP" or
  "FortiGate DHCP (VDOM <vd>)", never a MAC of another client).
- **Sender configured later** (D24): a chain whose cited DHCP rows come from an `unknown-<ip>`
  source that `untrusted_sources` now trusts gets `source_configured_later` (info, the source
  names).
- **srcmac conflict** (D10): `Attributor.chain(..., srcmac=(mac, ref, interface))` with a usable
  srcmac of a listed interface that differs from the lease MAC returns the lease chain with
  confidence `low`, `srcmac_mismatch`, and the srcmac chain as an alternative (also `low`), as
  `ip_conflict` does.
- New `Attributor.srcmac_chain(ip, at, mac, ref, interface) -> AttributionChain` (2.9).
- **Source clock at the requested moment** (D28, V16, O22): `run_lookup` measures, for each
  source of the outcome's cited rows (the matches' session rows and the chains' lease evidence;
  at most 5 sources), the median of `time − time_received` over that source's `messages` rows
  with a device time in [T − 1 h, T + 1 h] and `time <> time_received`
  (`ix_messages_source_time`, at most the first 5,000 rows of the range, one query per source,
  cached per lookup). The search and the lease timeline used the device's times, so those rows
  are exactly the ones the lookup relied on, and their receive times show the real moment. With
  at least 10 such rows and a median beyond max(120 s, the lookup tolerance), the outcome gets
  `source_clock_offset` (warning: the sources and their offsets) and every chain of the outcome,
  alternatives included, gets confidence `low`: the session found may not be the one the
  requested moment means, and the lease may not cover it. A match keeps its kind (the record did
  match by `transip` and `transport`); the PDF prints the warning in the method section (2.12).
  Nothing is said when a source has fewer than 10 rows there (a quiet DHCP-only router), or when
  its rows carry the receive time (a clock more than `max_clock_skew` off, which ingest replaced
  by the receive time).
  **A search that found nothing** (round 5) cites no row, yet a clock that is off is exactly what
  makes an exact or internal search miss the real session or lease. So when a search ran and
  the outcome cites no row of the table it searched, the same measurement also covers the
  sources whose rows that search read: `fg_traffic` when a public-IP request found no match
  (`none`, or candidates with an empty list; not `no_egress`, `not_logged` or `internal`, which
  searched nothing), and `mt_dhcp`, `fg_dhcp` and `mt_arp` when an internal lookup or a match has
  no chain. The sources are chosen as `R20-lease:source-clock` chooses them: enabled, configured
  (`unknown = false`) sources whose spool files of the receive days of `[T − 1 h, T + 1 h]` hold
  rows of that table (`search_counts` by `raw_day`, `ix_search_counts_raw_day_source`; a
  device clock cannot move the receive day), at most 5 sources in all with the cited ones.
  Beyond the limit, the outcome gets `source_clock_offset` as well, any chain it has is lowered as
  above, and it keeps its mode (an empty result has no chain to lower). Its text (2.11) says that
  the right session or lease may lie in the shifted window and that the lookup can be run again
  with the tolerance widened by the offset.

### 2.8 Internet exits (`db/egress.py`, `lookup/engine.py`, `lookup/sessions.py`; WP-C)

#### 2.8.1 Model and validation

```json
{
  "hat-1": {"label": "Ana hat", "mode": "direct", "public": ["203.0.113.200"],
            "public_dynamic": false, "transips": [], "interfaces": ["wan1"],
            "port_preservation": "unknown"},
  "hat-2": {"label": "Yedek hat", "mode": "isp_nat", "public": ["198.51.100.77"],
            "public_dynamic": true, "transips": ["100.64.13.9"], "interfaces": ["wan2"],
            "port_preservation": "unknown"},
  "misafir": {"label": "Misafir hattı (FortiGate dışı)", "mode": "not_logged",
              "public": ["198.51.100.90"], "public_dynamic": false, "transips": [],
              "interfaces": [], "port_preservation": "unknown"},
  "dmz": {"label": "DMZ'ye NAT", "mode": "internal", "public": [], "public_dynamic": false,
          "transips": ["172.20.99.1"], "interfaces": ["dmz"], "port_preservation": "unknown"}
}
```

```python
EgressMode = Literal["direct", "isp_nat", "not_logged", "internal"]
PortPreservation = Literal["unknown", "preserved", "not_preserved"]

@dataclass(frozen=True)
class Egress:
    name: str; label: str; mode: EgressMode
    public: tuple[IPv4Network, ...]; public_dynamic: bool; transips: tuple[IPv4Network, ...]
    interfaces: tuple[str, ...]; port_preservation: PortPreservation
    @property
    def catch_all(self) -> bool: return self.mode == "isp_nat" and not self.transips
    @property
    def public_unknown(self) -> bool:   # searched for unclaimed addresses (D14, D31)
        return self.mode == "isp_nat" and (not self.public or self.public_dynamic)
    def claims_public(self, ip: str) -> bool
    def claims_transip(self, ip: str) -> bool

class EgressValueError(ValueError):            # code + facts; settings.py wraps it into
    code: str; facts: Mapping[str, Any]        # SettingValueError (no import cycle)

def validate_egresses(value: Any) -> dict[str, dict[str, Any]]      # normalised, all fields filled
def parse_egresses(value: Any) -> tuple[Egress, ...]                # sorted by name; ValueError
def legacy_egresses(public_ips, wan_transip, port_hint) -> tuple[dict, list[str]]
```

Validation rules, in order. Each rule fails with its own code from 2.5.

1. An object with at most 8 exits.
2. The name matches `^[a-z0-9][a-z0-9-]{0,31}$`.
3. Each exit is an object with only the known fields; `mode` is required.
4. `label`: at most 64 characters, no control characters (default `""`).
5. `public` and `transips`:
   - lists of at most 16 entries;
   - each entry an IPv4 address or IPv4 network with a prefix of at least /24 and zero host
     bits, stored in canonical text (`/32` written as the bare address);
   - IPv6 fails with `setting_ipv6_not_supported`;
   - a `public` entry (of any mode) that lies in or overlaps 10/8, 172.16/12, 192.168/16
     (RFC 1918), 100.64/10 (RFC 6598), 0/8, 127/8, 169.254/16, 224/4 or 240/4 fails with
     `setting_egress_public_private`. The check uses this explicit list, never Python's
     `ipaddress` `is_private`/`is_global`, which classify the RFC 5737 documentation ranges (the
     example world's public addresses) as not global. `transips` take any IPv4 address (a
     public `transip` of an `isp_nat` exit is only warned about in the editor, 2.13).
6. `interfaces`: at most 8 entries, each following the interface rule (`setting_interface_invalid`).
7. `port_preservation` is one of the three values (default `unknown`); `public_dynamic` is a
   boolean (default `false`; `setting_egress_field` for another type).
8. Per mode:
   - `direct`: `public` is not empty, `transips` is empty, `port_preservation` is `unknown`,
     `public_dynamic` is false (`setting_egress_dynamic`; a dynamic direct line is found by the
     observed check, D31);
   - `not_logged`: `public` is not empty, every other list is empty, `public_dynamic` is false;
   - `internal`: `transips` is not empty, `public` is empty, `public_dynamic` is false and
     `port_preservation` is `unknown` (`setting_egress_internal`); `interfaces` optional;
   - `isp_nat`: free.
9. Across exits:
   - no two exits' `public` overlap;
   - no `public` of any exit overlaps any exit's `transips` (`setting_egress_overlap`); an
     `isp_nat` exit whose own `public` and `transips` overlap fails with
     `setting_egress_isp_nat_direct` instead (the FortiGate holds the public address itself, so
     the line is direct: typing the line's public address into a converted `wan` exit whose NAT
     address is that same address would otherwise turn its exact answers into candidates);
   - two exits' `transips` may overlap only when both have non-empty, disjoint `interfaces`;
   - at most one catch-all.
10. Normalisation: lists are de-duplicated in the order given and every field is present.
    Canonical JSON sorts the keys.

`settings.validate_setting` (day 0) dispatches `nat.egresses` to `validate_egresses` and wraps
`EgressValueError`.

**NAT is parsed only where it is used.** `LookupSettings` keeps the raw `nat.egresses` value;
`LookupSettings.egresses()` calls `parse_egresses` on first use and caches it. Four places call
it, each with its own handling of a value that cannot be parsed:
- the API's lookup router, **inside its existing `try`** that maps `(TypeError, ValueError)` to
  503 `lookup_settings_invalid` (one listed C1 edit of `routers/lookup.py`, 4.1): `run_lookup`
  runs outside that `try`, so a parse failure inside it would be a 500 (1.2);
- the lookup engine, which then finds the parsed value cached;
- the NAT alert check `_nat_exits`, which turns the failure into `R20-nat:invalid` (2.6.3);
- the observed endpoint, which answers with `egress: null` on every row and `exits_invalid:
  true` (2.12).

The lease job (`Context.load`, `TimelineRules.from_settings`), the lease alerts
(`_lease_evidence`) and the usage subjects never call it, so a bad NAT value cannot stop the
lease timeline or a lease alert, both 5651 Temel (test in 3.3). Today's coupling (an invalid
`nat.public_ips` also stops the lease job) disappears with it.

#### 2.8.2 Legacy conversion (`legacy_egresses`; frozen copy in `v0018`)

```python
_NOT_PUBLIC = tuple(
    ipaddress.ip_network(n)
    for n in (  # rule 5 of 2.8.1, explicit list
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    )
)  # RFC 1918, RFC 6598
_RESERVED = tuple(
    ipaddress.ip_network(n)
    for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)


def _v4(text, dropped):
    """The canonical IPv4 text of a legacy value, or None (reported in ``dropped``)."""
    try:
        address = ipaddress.ip_address(str(text).strip())
    except ValueError:
        dropped.append({"value": str(text)[:64], "reason": "invalid"})  # stored unvalidated
        return None
    if address.version != 4:
        dropped.append({"value": str(address), "reason": "ipv6"})  # storable before
        return None
    if any(address in net for net in _RESERVED):
        dropped.append({"value": str(address), "reason": "reserved"})
        return None
    return str(address)


def legacy_egresses(public_ips, wan_transip, port_hint):
    dropped = []
    items = public_ips if isinstance(public_ips, list) else [public_ips]
    found = list(dict.fromkeys(a for a in (_v4(x, dropped) for x in items) if a))
    private = [a for a in found if any(ipaddress.ip_address(a) in n for n in _NOT_PUBLIC)]
    keep = [a for a in found if a not in private]  # real public addresses
    wan = _v4(wan_transip, dropped) if str(wan_transip or "").strip() else None
    pp = "preserved" if port_hint is True else "unknown"
    out = {}
    for i in range(0, len(keep), 16):  # one direct exit; split past 16
        out[f"genel-{i // 16 + 1}"] = {
            "label": "Genel IP (yükseltmede aktarıldı)",
            "mode": "direct",
            "public": keep[i : i + 16],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": "unknown",
        }
    transips = list(
        dict.fromkeys([*([wan] if wan is not None and wan not in keep else []), *private])
    )
    if transips:  # > 16 fails validation -> {}
        out["wan"] = {
            "label": "FortiGate WAN adresi (yükseltmede aktarıldı)",
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": transips,
            "interfaces": [],
            "port_preservation": pp,
        }
    elif keep or port_hint is True:
        out["tanimsiz"] = {
            "label": "Genel adresi tanımlanmamış hatlar",
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": pp,
        }
    return out, dropped
```

The labels are printed in official PDFs (2.12), so they are neutral and say nothing about "old
behaviour". The migration then validates the result with its frozen `validate_egresses`
(2.2.2): more than 8 exits (more than 112 public legacy addresses: 7 direct exits of 16 plus the
`wan` or `tanimsiz` exit that is always added), more than 16 `transips` in `wan`, or any other
rule failure gives `{}` and `conversion_failed`.

Equivalence cases. E-j's expectations are the **existing test assertions** of the Wave 2 tests
and goldens recorded from the Wave 2 tree before C1's switch (C1 removes today's engine in the
same merge, so "today's engine" cannot be run beside the new one).

| Legacy | Exits | Old answer = new answer |
|---|---|---|
| (a) all defaults | none (implicit catch-all) | candidates over every `snat` session with a private or shared NAT address; **differs** for a P the logs show as the FortiGate's own `transip` near T: now the observed answer (exact with a port, D31 (a)), where Wave 2 gave candidates; and sessions with a public NAT address (a bridge-mode site's own address) are no longer candidates for another P (O21). Listed in E-j |
| (b) `public_ips=[A]` | `genel-1` direct A + `tanimsiz` | A+port exact; other P → candidates without A's sessions; **differs** where an unclaimed public NAT address (an IP pool) is active: it no longer fails A's check and its sessions are no candidates (O21). Listed in E-j |
| (b2) `public_ips=[A1 … A12]` | `genel-1` direct A1–A12 + `tanimsiz` | each Ai+port exact; other P → candidates without the Ai sessions |
| (b3) 20 addresses | `genel-1` (16) + `genel-2` (4) + `tanimsiz` | as (b2) |
| (c) `wan_transip=W` | `wan` (isp_nat, public unknown, W) | every P → narrowed to W; P = W + port → exact on W with `double_nat` true (as today) |
| (d) A and W | `genel-1` + `wan` | A exact; other P → W; A quiet with an unclaimed NAT address X → W (fallback: both exits as converted and neither declares interfaces, D15) |
| (d2) A and W, A quiet, only W active (a moment before the line was switched to bridge mode) | `genel-1` + `wan` | the D15 check of A leaves W (F's transip) out of the own set, so it fails → fallback → candidates over W with `public_ip_not_transip` (Wave 2's answer; without the F rule it would be a false `none`) |
| (e) `port_hint` only | `tanimsiz` preserved | candidates with the +5 hint (the O21 exception of (a)) |
| (f) `public_ips=[A]`, `wan_transip=A` (bridge mode) | `genel-1` direct A + `tanimsiz`; no `wan` | A+port exact, `double_nat` false (same); **other P differs**: today narrowed to A's own sessions, now the catch-all's unclaimed private or shared sessions, usually none on a bridge-mode site (A's sessions did not leave through P). Listed in E-j |
| (g) an invalid stored value (`"not an address"`), IPv6 or a reserved address | the valid rest; the bad value in `dropped` | today such a site answers 503 `lookup_settings_invalid` (invalid, IPv6); now it answers with the rest, and the audit entry names what was dropped |
| (h) `public_ips=[100.64.12.9]` (the FortiGate's WAN address behind an ISP router, entered as a public address) | `wan` (isp_nat, transips [100.64.12.9], plus `nat.wan_transip` if set) | **differs**: Wave 2 excluded every session translated to 100.64.12.9 from every candidate search, so each real public-IP request listed nobody; now it gives candidates narrowed to 100.64.12.9 with `egress_public_unknown`. Listed in E-j |

The converted exits keep Wave 2's fallback (D15) only while they stay **as converted**: once an
administrator gives `wan` interfaces or a public address, or edits `genel-1`'s addresses, the
fallback follows the rules of a new configuration (E-c, E-c2).

#### 2.8.3 Resolution (`engine._resolve_egress`)

```
exits = settings.egresses()     # parsed (and checked) by the router before run_lookup (2.8.1)
probe = log_probe(P)   # cached per lookup, trusted sources only (D24): seen(P) by row time
                       # ±15 min (the D15 check) and, computed as well, the private or shared
                       # transips outside own(E) there (≤ 5, the D15 evidence; round 6),
                       # seen_overlap(P) by session overlap (D31 (a)),
                       # the unclaimed addresses seen (≤ 5, each marked public or private/
                       # shared, named in texts only), and per exit with interfaces whether its
                       # own addresses have rows in ±15 min and whether any of those rows
                       # carries one of its interfaces
E = first exit (by name) with E.claims_public(P):
    if E.mode == "not_logged" and probe.seen_overlap(P):
                                                -> Resolution("observed", [E])       # D14
                                                -> Resolution("public", [E])
E = first exit with E.claims_transip(P)           -> Resolution("transip", [E])   # an
                                                   # internal E: no search, egress_internal
if probe.seen_overlap(P):                       -> Resolution("observed", [])        # D31 (a),
                                                   # with or without exits (D13)
if not exits:                                   -> Resolution("unconfigured", [IMPLICIT_CATCH_ALL])
U = [E for E in exits if E.public_unknown]      # isp_nat without public, or public_dynamic
if U:                                           -> Resolution("public_unknown", U)
                                                -> Resolution("no_egress", exits)    # every
                                                   # configured exit: the list P was checked
                                                   # against, printed in the PDF (2.12)
# a "public, direct" or "transip" resolution whose log check (D15) fails becomes
                                                   Resolution("fallback", [E, *F])
#   F = fallback_exits(E): the isp_nat exits without a public address that share an
#       interface with E, or, when neither E nor the exit declares interfaces, only when both
#       are as converted by v0018 (legacy (b), (d)); otherwise F = []
#   it also becomes Resolution("fallback", [E, *F]) when the check passed with seen(P) while
#   the probe also saw a private or shared transip outside own(E) and the search on P
#   (exact, or transip = P without a port) finds no session (D15 "seen, but not alone")
#   the check's own set own(E) = E's own addresses ∪ the addresses claimed by the exits
#       X != E outside G(E), per address (one claimant outside G(E) keeps an address in)
#   G(E) = every isp_nat exit X != E without a public address (round 5; F ⊆ G(E)), and,
#          when E and X both declare interfaces, every X whose interfaces are disjoint from
#          E's, of any mode, internal included (round 6)
```

Then, with **claimed** = every exit's `transips` (the `internal` exits' included) ∪ every
`direct` exit's `public`, and **unclaimed** = `transip IS NOT NULL AND NOT (transip <<=
ANY(claimed)) AND transip <<= ANY(NOT_PUBLIC) AND trandisp = ANY(nat.snat_trandisp)`, with
`NOT_PUBLIC` the ranges rule 5 of 2.8.1 refuses as public (RFC 1918, RFC 6598, reserved; one
constant shared with the validator) (always the predicate over the whole bounded search window,
never only the ≤ 5 addresses the probe saw). An unclaimed **public** `transip` is the FortiGate's
own address (D15): it is answered as observed when it is P, named in `egress_unmapped_seen` and
`R20-nat:unmapped-transip`, and never searched as a candidate for another address (O21):

| Resolution, mode | Port given | Search | Mode | Notes | `double_nat` |
|---|---|---|---|---|---|
| public, `direct` | yes, and the log check (D15) passes for P | exact `transip = P AND transport = port` | exact / none (`fallback` instead of none when the pass was "seen, but not alone") | `exact_nat` (as today) | false |
| public, `direct` | no, and the check passes | candidates `transip = P` | candidates (`fallback` instead of an empty list when the pass was "seen, but not alone") | `port_missing` | false |
| public, `isp_nat` | any | candidates `transip <<= ANY(E.transips)` (catch-all: unclaimed), `dstintf = ANY(E.interfaces)` when set; O5: `E.transips ∪ unclaimed`, on E's interfaces as well | candidates | `egress_search`, `double_nat` text; `egress_public_dynamic` when E is `public_dynamic` (D31 (b)) | true |
| public, `not_logged` | any, P not seen | none | none | `egress_not_logged` | false |
| observed, `not_logged` exit (P seen as a session's `transip` overlapping T ± 15 min) | yes / no | as `observed` below | exact / none / candidates | `egress_not_logged_but_seen`, `exact_nat` or `port_missing` | false |
| transip (P is an exit's NAT address) | yes, the check passes for P | exact on P | exact / none (`fallback` instead of none when the pass was "seen, but not alone") | `exact_nat`, `egress_search` | true (the exit is `isp_nat`; legacy (c) keeps today's `true`) |
| transip | no, the check passes | candidates `transip = P` | candidates (`fallback` instead of an empty list when the pass was "seen, but not alone") | `port_missing`, `egress_search` | true |
| transip, `internal` exit (P is a NAT address that is not an internet exit, D13) | any | none (no check, no search) | none | `egress_internal` | false |
| **fallback** (public `direct` or transip, the check fails, or it passes "seen, but not alone" and the search on P finds no session; port or not) | any | candidates over **E's own addresses** (P, or E's `public`) ∪ F's `transips` (a catch-all F, or F empty: ∪ the unclaimed predicate); `dstintf = ANY(E.interfaces)` on every part when E declares interfaces (the interface rule below); F's transips not seen near T: O5 (∪ unclaimed) | candidates | `public_ip_not_transip` (+ `egress_transip_not_seen` when O5 applies to F; never `egress_search`: the PDF's fallback addition names F's exits, 2.12) | true |
| observed (unclaimed P seen as a session's `transip` overlapping T ± 15 min) | yes | exact `transip = P AND transport = port` | exact / none | `egress_observed_not_configured`, `exact_nat` | false |
| observed | no | candidates `transip = P` | candidates | `egress_observed_not_configured`, `port_missing` | false |
| public_unknown | any | candidates over ∪ U's transips (catch-all in U: unclaimed) | candidates | `egress_public_unknown`, `double_nat` | true |
| no_egress | any | none | none | `public_ip_no_egress` | false |
| unconfigured | any | candidates over unclaimed (every listed `trandisp` with a private or shared NAT address) | candidates | `double_nat`, `egress_unconfigured` | true |

`outcome.egress.resolution` is exactly the resolution of this table: `public`, `transip`,
`fallback`, `observed`, `public_unknown`, `unconfigured` or `no_egress` (2.10). For `observed`,
`egresses` holds the `not_logged` exit that claims P, if any, and is otherwise empty; for
`no_egress` it holds every configured exit (none of which claims P).

- **Destination rules stay as they are** (data minimisation): a requested destination that no
  candidate reaches gives `none` with `destination_not_found`.
- **Not-seen widening (O5):** an `isp_nat` exit E whose `transips` have no row within ±15 min
  of T, while unclaimed rows exist there, is searched over its `transips` **and** the unclaimed
  set (∪, as Wave 2's `wan_transip_not_seen` did: a long session through E's address that was
  logged after T + 15 min still overlaps T), both parts on E's interfaces when E declares any
  (the interface rule below). The explanation is `egress_transip_not_seen` with at most 5 seen
  addresses. A quiet window keeps the narrowing.
- **Interfaces:**
  - when E declares `interfaces`, the condition `dstintf = ANY(E.interfaces)` applies to **every
    part** of E's search: its own addresses, F's, and the unclaimed part of a fallback (F empty)
    or of the O5 widening. Keying the exception on rows of E's transips would drop it exactly
    there, where E's transips are not seen by definition (O5) or do not exist (a direct exit), and
    a quiet line's question would list another line's sessions;
  - it is dropped only on **positive evidence that the names are wrong** (renamed or mistyped):
    rows of E's own addresses (its `transips`; its `public` for a `direct` exit) within ±15 min
    of T exist and none of them carries one of E's interfaces: `egress_interface_not_seen`.
    When E's own addresses have no row near T, the condition is **kept**: an idle line (SD-WAN
    sends everything over the other member at night) looks exactly like that, and dropping the
    condition there listed the other line's sessions for a line that carried nothing; the
    narrowed search then finds E's own long sessions or nobody, and `egress_transip_not_seen`
    says that E's addresses were not seen;
  - the exception is an exit that shares a transip with another exit: rows of the shared transip
    on the other exit's interfaces are no evidence, so it keeps the condition even then
    (`egress_interface_not_seen_kept`);
  - interfaces never apply to exact mode.
- **Unclaimed addresses seen:** when the ±15-minute probe sees unclaimed source-NAT addresses
  (public ones included; an `internal` exit's addresses are claimed) and exits are configured,
  the lookup adds `egress_unmapped_seen` with at most 5 addresses. This works in every mode, so
  an incomplete configuration always shows.
- **Unknown `trandisp`:** when the search uses the unclaimed set, rows in the window with a
  `transip` and a `trandisp` not in the setting are counted and named
  (`nat_unknown_trandisp`, at most 5 values).
- **Port hint:** a candidate whose exit has `preserved` and whose `transport` equals the
  requested port gets +5. The explanation is `port_hint` with the exits' names. Exact mode never
  uses it.
- **Every match** gets `egress` from the group's row nearest to T: the exit that claims that row's
  `transip`; when several exits claim it (a shared transip), the first exit by name whose
  `interfaces` contain the row's `dstintf`; otherwise, or for an unclaimed row, `null`. When the
  group's rows resolve to different exits (or one claimed and one unclaimed), the match gets
  `session_changed_exit` naming them (D32).
- **Exits changed after the event:** from the change record `nat.egresses_changes` (which
  `run_lookup` reads itself, 2.10): when an exit of `outcome.egress.egresses` changed its content
  after T, or, for a P that no exit claims now (`public_unknown`, `no_egress`, `unconfigured`;
  not `observed`, which rests on the FortiGate's own record), when any exit, deleted ones
  included, changed its content after T, the outcome gets `egress_changed_after_event` naming
  those exits with the time and author of each change and what each holds now (D32). "Content"
  is the fingerprint of D32 (mode, public, transips, public_dynamic): a label-, interface- or
  port-preservation-only edit gives no warning, nor does a change of another exit while P is
  claimed.
- **Untrusted sources** (D24): every probe query leaves out the rows of `untrusted_sources`; a
  match with a row of such a source carries the source names in `NatMatch.untrusted_sources`, and
  the outcome gets `session_from_unknown_source` (the count and the names). A match whose rows
  come from an `unknown-<ip>` source that is trusted because its address was configured later
  gets `source_configured_later` instead (info).
- **The log check** (`_transip_seen`, generalised): one probe pass per lookup (a few indexed
  queries, cached) returns, within `±max(900 s, tolerance)` by row time, `exists(transip =
  address)` for the requested P (and, for `transip`/`direct` resolutions, the claimed address);
  for the D15 check of E, the distinct private or shared `transips` outside own(E) (at most 5),
  on E's interfaces when E declares any, computed whether or not P was seen (no early return, as
  HEAD's `_transip_seen` has: "seen, but not alone" needs both, round 6; a `fallback` stores them
  in `searched.outside_seen`, 2.10, the `{seen}` of the fallback paragraph, 2.12); the distinct
  unclaimed addresses (at most 5, each marked
  public or private/shared); and per exit with `interfaces` whether its own addresses have rows
  there and whether one of those rows carries one of its interfaces (the interface rule above);
  a second, bounded query answers `seen_overlap(P)` for the observed check (D31 (a),
  D14): `transip = P AND time >= T − probe AND time <= T + probe + max_session_seconds AND time −
  duration <= T + probe` (`ix_fg_traffic_transip_transport_time`; an address no exit claims has
  few rows, so the scan stays short; 3.8). The results are cached per lookup and serve D15, the
  observed check, the texts and `egress_unmapped_seen`; the searches themselves use the
  predicates of the table above.
- **IPv4 only:** the API refusal (422 `ipv6_not_supported`) and `_same_family` stay.

**`sessions.build_query` changes:**
- `transip: str` stays for exact mode;
- new `transip_in: Sequence[IPv4Network]` (`= ANY(inet[])` when every entry is a /32, otherwise
  `<<= ANY(cidr[])`);
- `unclaimed: Sequence[IPv4Network] | None` (the unclaimed predicate);
- `dstintfs: Collection[str] | None`;
- `snat_types: Collection[str]` replaces the literal `'snat'`;
- `exclude_transips` is removed (the unclaimed set replaces it);
- `_COLUMNS` and `TrafficRow` gain `srcintf`, `dstintf`, `srcmac`.

**FortiGate DHCP rows that did not become lease evidence (D8 probe).** When a chain cannot be
built for an IP (internal mode `no_lease`, or a match with `chain_missing`), one probe per lookup
(for at most 20 IPs) takes, per IP, the **newest DHCP-shaped row before T from a trusted source**
(D24: rows of `untrusted_sources` are left out, so a forged line never reaches the outcome),
across both tables that can hold one:

```sql
-- per IP, LIMIT 1 each; the newer of the two rows is "the newest row"
SELECT 'fg_event' AS tbl, id, time, logid, extra->>'lease' AS lease, extra->>'interface' AS interface,
       source, raw_day, raw_line, raw_offset FROM fg_event
 WHERE subtype = 'system'                                -- ix_fg_event_subtype_time
   AND time >= :at - :window AND time <= :at             -- window: see below
   AND logid = ANY(:mapped_logids)                       -- keys of dhcp.fortigate_logids
   AND extra->>'ip' = :ip AND NOT (source = ANY(:untrusted))
 ORDER BY time DESC LIMIT 1;
SELECT 'fg_dhcp' AS tbl, id, time FROM fg_dhcp            -- ix_fg_dhcp_ip_time
 WHERE ip = :ip AND time >= :at - :window AND time <= :at
   AND NOT (source = ANY(:untrusted))
 ORDER BY time DESC LIMIT 1;
```

- **Window.** `TimelineRules.longest_window` alone (about 95,040 s with the defaults) is too
  short for FortiGate leases: with FortiOS's default `lease-time` 604,800 s the client's last ACK
  before T often lies 3–7 days back. The window is `max(longest_window, 8,640,000 s)` (100
  days, the FortiOS maximum `lease-time`, F8); each query is a backward index scan that stops at
  its first row.
- **The newest row decides.** Only when it is an `fg_event` row whose logid
  `dhcp.fortigate_logids` maps to **`assigned`** does it count, and then only when it would
  have covered T, by the same formula as a lease (`lease_timeout_seconds`, D6): `T <= row.time
  + its logged lease` when that lies in 300–8,640,000 s, else `T <= row.time + the configured
  time of its interface × timeout_percent / 100`. Anything else means nothing is said: a newer
  RELEASE (a mapped `deassigned` logid; it has no `lease` field, F3, and is the record that the
  client left), a newer converted `fg_dhcp` row (the timeline saw that event; when the chain is
  missing all the same, the FortiGate gave the address to another device or took it back after
  the old ACK), or a row of a 1-day lease from 100 days ago.
- No row after T is looked for: another client's later ACK says nothing about T, and citing it
  would print that client's MAC and host name in the report.
- **A chain that rests on a DHCP lease** (round 5 for `fg_dhcp`, round 6 for `mt_dhcp` too; D8)
  runs the `fg_event` branch above for its IP **or its MAC**, with the window
  `(last_dhcp_evidence, at]` (at most the probe's) instead of the probe's, batched into one query
  per lookup (2.7): any mapped row there, of either action, lowers the chain to `low` with
  `fortigate_dhcp_not_converted_since_lease`; nothing is cited, in any mode.
- A counting row gives one text, `fortigate_dhcp_not_converted` with `{ips}`, whose wording names
  the three possible reasons (D8). **In internal mode and for an exact match** that row is also
  cited as an `EvidenceRef` (table `fg_event`, summary "FortiGate DHCP kaydı (kira zaman
  çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)") in
  `LookupOutcome.unconverted_dhcp` (at most 1 per IP), so the PDF prints its raw line and seal
  data with that label (2.12; the cross-check marks `fg_event` lines `not_checked`, as today),
  never as an attribution. For candidate matches nothing is cited: a double-NAT report would
  otherwise print up to 20 other people's DHCP lines, with their MACs and host names, for
  sessions that are only candidates (KVKK data minimisation, 2.16).
- Cost (3.8): < 0.5 s for 20 IPs on the 100-day window, the worst case an IP that has no DHCP
  row at all (each scan reads the whole window). If that guard fails, the window falls back to
  `max(longest_window, 604,800 s)` (the FortiOS default lease), recorded as a deviation and in
  CORRELATION §7.

### 2.9 srcmac in the lookup (WP-C)

- **Allowed** when `srcintf ∈ settings.srcmac_interfaces`, `srcmac IS NOT NULL`, the MAC is not
  an infrastructure MAC (`devices.infrastructure_macs` or the device flag), the MAC is not
  shared, and no lease of the MAC on another IP covers any moment of `[T − 1 h, T + 1 h]`
  (`Attributor.leases_of_mac`; refusal `srcmac_other_lease`), and the row's source is trusted
  (D24). **Shared** means more than `srcmac_max_ips` (default 1) distinct **IPv4** `srcip`
  (`family(srcip) = 4`; a dual-stack client's IPv6 and temporary IPv6 addresses carry the same
  srcmac) for that `srcmac` on that `srcintf` within `[T − 1 h, T + 1 h]`, rows of trusted
  sources only. One batched query per lookup counts it:
  `trust.srcmac_ip_counts(session, low, high, interfaces, untrusted, macs)` (2.6.1, WP-B); the
  observed endpoint's `max_ips_per_mac` and the `srcmac-shared` alert call the same function
  (without `macs`), so the three cannot count differently.
- **`TrafficInfo.srcmac`** is filled only for rows whose `srcintf` is listed; for every other row
  it stays `null`, so no other person's MAC reaches the typed fields of a stored outcome while the
  feature is off (test in 3.4). The cited raw lines still carry it on interfaces with device
  identification on (D21, 2.16).
- **Candidate and exact matches:**
  - the group's row nearest to T that carries a usable srcmac is used;
  - `chain is None`: `Attributor.srcmac_chain(srcip, moment, srcmac, traffic_ref(row),
    srcintf)` gives a chain with `lease=None`, a `DeviceInfo` (mac, name, host names and vendor
    from `devices` when known; `confidence="low"`; `evidence=(traffic_ref,)`), the person valid
    at that moment as today, the VLAN from the subnet, `confidence="low"`,
    `mac_source="srcmac"` and the explanation `srcmac_only`;
  - a chain with a lease adds `srcmac_agrees` (info) when the MACs are equal; when they differ it
    becomes the conflict of D10 (lease chain first, srcmac chain as alternative, both `low`,
    `srcmac_mismatch`).
- **Internal mode, IP given, no lease:**
  - `sessions.srcmac_sightings(srcips={ip}, window, interfaces, untrusted, limit=50)` reads the
    trusted rows of the IP whose session overlaps the window, through `(srcip, time)`;
  - one usable MAC gives a srcmac chain; the mode is `internal`, and the outcome keeps
    `no_lease` and adds `srcmac_only`;
  - several MACs give `srcmac_ambiguous` (their count and at most 3 of them) and no chain.
- **Internal mode, MAC only, no lease:** rows with `srcmac = mac` within `[T − tolerance,
  T + tolerance]`, a primary-key time range on the listed interfaces. One IP gives a chain;
  several give `mac_multiple_ips` (an existing key).
- **Refusals add explanations:** a shared MAC gives `srcmac_shared`; an infrastructure MAC gives
  `srcmac_infrastructure`; a MAC with a lease on another IP gives `srcmac_other_lease`.
- **srcmac never reaches** `leases`, `devices` (no upsert from srcmac), conflicts or usage. A
  test asserts that `leases` and `devices` are unchanged after lookups with srcmac on.

### 2.10 Lookup outcome changes (`lookup/types.py`, day 0; JSON is what the API returns)

| Type | New field | Default (old stored outcomes) |
|---|---|---|
| `LeaseInfo` | `source_table: str \| None` (`mt_dhcp`/`fg_dhcp`/`mt_arp`), `lease_seconds: int \| None`, `last_dhcp_evidence: datetime \| None` (the lease's own newest DHCP record at or before the chain's moment, never the line that ended it and never a later renewal; for `fg_dhcp` from the rows, otherwise `refs.last_dhcp_evidence`, 2.6.1, 2.7) | `None`, `None`, `None` |
| `AttributionChain` | `mac_source: Literal["lease", "srcmac"]` | `"lease"` |
| `TrafficInfo` | `srcintf`, `dstintf`, `srcmac: str \| None` (`srcmac` only for listed interfaces, 2.9) | `None` |
| `NatMatch` | `egress: str \| None`; `untrusted_sources: list[str]` (the sources of its rows that `untrusted_sources` refuses, D24; the PDF prints a warning row and the web a badge on that match) | `None`, `[]` |
| `LookupOutcome` | `egress: LookupEgress \| None` (`None` without a public IP in the request); `unconverted_dhcp: list[EvidenceRef]` (D8 probe rows, at most 1 per IP, internal mode and exact matches only) | `None`, `[]` |
| `LookupSettings` | raw `nat.egresses` with the lazy `egresses()` (2.8.1), `snat_trandisp`, `srcmac_interfaces`, `srcmac_max_ips`, `fortigate_logids`, `fortigate_dhcp_msgs`, `use_logged_lease`, `fortigate_traffic_extends`, `fortigate_interfaces_excluded`, `fortigate_format_verified`; `lease_timeout_seconds(server, lease_seconds=None)` (the D6 formula); `public_ips`/`wan_transip` removed by WP-C1. `from_settings(values)` keeps its **values-only** signature for every caller. What needs a row's author or time is read by `run_lookup` itself at its start, one `select` of three `settings` rows into a small engine-internal `StatusRows` (not part of `LookupSettings`): `nat.egresses_changes` (D32), `nat.egresses_migration` (`new` gives the *as converted* exits, D15) and the `dhcp.fortigate_format_verified` row's `updated_at`/`updated_by` (D29). The engine is the only reader, so no caller has to pass rows and the router needs no change for it | shipped defaults |

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

`changes` lists, for each exit of `egresses`, and, when no exit claims P now, for each exit
(deleted ones included, `"deleted": true`) whose content changed after T (D32), its last content
change from `nat.egresses_changes` (`changed_by` as stored; the texts and the PDF render
`:migration`, `:seed` and `:system` in words). `changed_after_event` is true when any entry has
`after_event`.

`resolution` is the value of the 2.8.3 table. For `fallback`, `egresses` holds the direct (or
transip) exit whose check failed followed by the exits it fell into; for `no_egress` it holds
every configured exit (the list P was checked against, which the PDF prints, 2.12), and for
`observed` it holds the `not_logged` exit that claims P, if any (D14); a request for an
`internal` exit's address gives `transip` with that exit (mode `internal`, nothing searched);
`searched.unclaimed_seen` lists the unclaimed addresses the probe saw (at most 5, public ones
included), while `searched.unclaimed` true means the private or shared unclaimed predicate was
searched. `searched.outside_seen` (round 6) lists, for a `fallback` only, the private or shared
addresses outside own(E) that the probe saw, on E's interfaces when E declares any (the D15
evidence, at most 5; the `{seen}` of the two `fallback` paragraphs, 2.12), and is `[]` for every
other resolution and in older outcomes. A `mode` value is one of the four of 2.8.1.

`from_dict` accepts every older outcome (fields missing means the defaults above); there is a
round-trip test for every new field.

### 2.11 Explanation texts (`lookup/texts.py`, day 0; tone in `chain-text.ts`)

Placeholders are filled by `texts.py` as today (times in `12.10.2026 18:19:07` form). Lists are
comma-joined; `-` means none. `explain()` formats the Turkish and the English text with **one**
params dict (1.2), so a value that differs by language is never one placeholder: the settings
path is written out in each text ("Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman
çizelgesi → İnternet çıkışları (NAT)" / "Settings → Application settings → 5651 lookup and
lease timeline → Internet exits (NAT)"; there is no NAT page, the setting lives in that group,
`settings_meta.GROUPS`), and the changed exits are two params, `{changes_tr}` in the Turkish
text and `{changes_en}` in the English one (`str.format` ignores the other), each rendered by
`texts.py` from the language-neutral `outcome.egress.changes`: "hat-2 (20.10.2026 08:12:00,
yonetici; şimdi: genel 198.51.100.77, NAT 100.64.13.9)" / "hat-2 (20.10.2026 08:12:00, yonetici;
now: public 198.51.100.77, NAT 100.64.13.9)", "silindi" / "deleted" for a deleted exit, and
internal authors in words (D32). Day 0 adds only the **new** keys; a rewritten existing text
lands in the merge that changes its caller (marked in the table), because the texts are
formatted eagerly and existing tests pin some of them (1.2).

| Key (tone) | Türkçe | English |
|---|---|---|
| `no_lease` (info, **text rewritten in C2**, same params; day 0 keeps today's text, which `test_devices_oui.py` pins) | {ip} adresi için {at} anında geçerli bir kira kaydı yok. En yakın kayıtlar: önce {before}, sonra {after}. Kira kanıtı DHCP sunucusunun kayıtlarından (MikroTik DHCP ya da FortiGate DHCP olayları) ve MikroTik ARP-LOG kayıtlarından gelir: DHCP sunucusunun kayıt gönderdiğini Kaynaklar sayfasındaki "Kira kanıtı" kutusunda kontrol edin. Statik IP'li cihazlar yalnızca ARP-LOG ile (ya da srcmac ile kısmi kanıt olarak) görünür. | No lease covers {ip} at {at}. Nearest leases: before {before}, after {after}. Lease evidence comes from the DHCP server's records (MikroTik DHCP or FortiGate DHCP events) and from MikroTik ARP-LOG lines: check on the Sources page ("Lease evidence") that the DHCP server sends them. Static-IP devices only appear through ARP-LOG (or, as partial evidence, through srcmac). |
| `no_lease_for_mac` (info, rewritten in C2) | {mac} için {at} anında geçerli bir kira kaydı (DHCP ya da ARP-LOG) yok. | No lease (DHCP or ARP-LOG) of {mac} covers {at}. |
| `chain_missing` (warning) | {count} eşleşmede iç IP adresinden cihaza zincir kurulamadı: o anda bu adresler için kira kaydı yok. Bu eşleşmeler FortiGate oturumunda ve iç IP adresinde biter. | For {count} match(es) no chain from the internal IP to a device could be built: no lease covered those addresses at that moment. These matches end at the FortiGate session and the internal IP address. |
| `fortigate_dhcp_not_converted` (warning) | {ips} için o anı kapsayan FortiGate DHCP kayıtları var, ancak kira kanıtına çevrilmediler, bu nedenle zincir kurulmadı. Olası nedenler: kayıtlar bu sürüme yükseltmeden önce yüklendi; ip ya da mac alanları okunamadı (ayrıştırma hatası fortigate.dhcp, uyarı R20-lease:fortigate-unconverted); ya da kayıtları bir geri almadan sonra önceki sürüm yükledi. İç IP sorgusunda ve kesin eşleşmede kayıt, kanıt bölümünde "kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez" notuyla gösterilir (aday eşleşmelerde gösterilmez); bir eşleştirme değildir. Ham kayıtlar saklanır ve gece mühürlenir; Arama'da type:event ile görülebilir. | There are FortiGate DHCP records for {ips} that cover that moment, but they did not become lease evidence, so no chain was built. Possible reasons: they were loaded before the upgrade to this version; their ip or mac fields could not be read (parse failure fortigate.dhcp, alert R20-lease:fortigate-unconverted); or the previous version loaded them after a rollback. In an internal-IP lookup and for an exact match the record is shown in the evidence section marked "not in the lease timeline; no chain built; no confidence given" (not for candidate matches); it is not an attribution. The raw lines are kept and sealed every night; they can be seen in Search with type:event. |
| `srcmac_only` (warning) | Bu zincirde kira kaydı yok. MAC adresi FortiGate oturum kaydının srcmac alanından alındı ({interface} arayüzü): FortiGate'in aygıt tanıma bilgisine göre kaynak IP adresi o anda bu MAC adresiyle ilişkilendirilmişti. Bu kısmi bir kanıttır; MAC adresinin bu oturumu gönderdiğini ya da adresin bir DHCP kirasıyla verildiğini kanıtlamaz. Cihaz ve kişi bu MAC adresine göre düşük güvenle gösterilir. | This chain has no lease. The MAC address comes from the srcmac field of the FortiGate session record (interface {interface}): the FortiGate's device identification associated the source IP with this MAC address at that moment. This is partial evidence; it does not prove that this MAC sent the session or that a DHCP lease gave it the address. The device and the person are shown for this MAC address with low confidence. |
| `srcmac_agrees` (info) | FortiGate oturum kaydındaki srcmac ({mac}) kira kaydındaki MAC adresiyle aynı. | The srcmac of the FortiGate session record ({mac}) is the MAC address of the lease. |
| `srcmac_mismatch` (warning) | FortiGate oturum kaydındaki srcmac ({srcmac}) kira kaydındaki MAC adresinden ({mac}) farklı: o anda bu IP adresini iki cihaz kullanmış olabilir (IP çakışması, statik adresli bir cihaz ya da kayıtlarda görünmeyen bir adres çakışması). Zincir kira kaydına göre kuruldu, srcmac'e göre olası zincir de gösterildi; ikisi de düşük güvenlidir. Arayüz ayarını (lookup.srcmac_interfaces) da kontrol edin. | The srcmac of the FortiGate session record ({srcmac}) differs from the lease's MAC address ({mac}): two devices may have used this IP address at that moment (an IP conflict, a device with a static address, or an address conflict that does not appear in the logs). The chain follows the lease and the possible chain by srcmac is shown too; both have low confidence. Also check the interface setting (lookup.srcmac_interfaces). |
| `srcmac_ambiguous` (warning) | {ip} adresinden ±{seconds} saniyede {count} farklı srcmac görüldü (ilk üçü: {macs}); MAC adresi belirlenemedi. | {count} different srcmac values were seen from {ip} within ±{seconds} s (the first three: {macs}); the MAC address could not be determined. |
| `srcmac_shared` (warning) | srcmac {mac}, {at} anının ±1 saat içinde {count} farklı IP adresiyle görüldü: bir yönlendiricinin, anahtarın ya da ağ cihazının adresi olabilir; kullanılmadı. | srcmac {mac} was seen with {count} distinct IP addresses within ±1 hour of {at}: it may belong to a router, switch or network device; it was not used. |
| `srcmac_other_lease` (warning) | srcmac {mac}, {at} anının ±1 saat içinde başka bir IP adresinin ({other_ip}) kirasında görülüyor: bir yönlendiricinin ya da anahtarın adresi olabilir; kullanılmadı. | srcmac {mac} holds a lease of another IP address ({other_ip}) within ±1 hour of {at}: it may belong to a router or switch; it was not used. |
| `srcmac_infrastructure` (info) | srcmac {mac} bir altyapı cihazının adresi; kullanılmadı. | srcmac {mac} belongs to an infrastructure device; it was not used. |
| `egress_search` (info) | Arama {egress} çıkışıyla sınırlandı: bu çıkış servis sağlayıcı NAT'ı olarak tanımlı (ayarlara göre servis sağlayıcının yönlendiricisi bu hatta ikinci bir NAT yapıyor). FortiGate'in bu hattaki NAT adresleri: {transips}; arayüzler: {interfaces}. | The search was limited to exit {egress}: this exit is defined as ISP NAT (by the settings, the ISP router does a second NAT on this line). The FortiGate's NAT addresses on this line: {transips}; interfaces: {interfaces}. |
| `egress_public_unknown` (warning) | {address} hiçbir çıkışın genel adresi olarak tanımlı değil; genel adresi tanımlanmamış ya da değişebilen çıkışlarda arandı: {egresses}. Adres tanımlı bir hatta ait değilse liste yanlış hattın kullanıcılarını gösterebilir; adresin ait olduğu hattın genel adresini Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde ekleyin. | {address} is not the public address of any exit; it was searched in the exits whose public address is not defined or may change: {egresses}. If the address does not belong to such a line, the list may show the users of the wrong line; add the public address of the line it belongs to in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT). |
| `public_ip_no_egress` (warning) | {address} hiçbir internet çıkışına tanımlı değil; kimse listelenmedi. Adres kurumun bir hattına aitse Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde tanımlayıp sorguyu yeniden çalıştırın. | {address} belongs to no internet exit; nobody was listed. If it is one of the organisation's lines, define it in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT) and run the lookup again. |
| `egress_not_logged` (warning) | {address}, {egress} çıkışına ait; bu çıkış FortiGate üzerinden geçmiyor. FortiGate'te bu hattın oturum kaydı yok ve kimse listelenmedi; bu hattın kaydı ancak hattı yöneten cihazdan alınabilir. | {address} belongs to exit {egress}, which does not pass the FortiGate. The FortiGate has no session records of this line and nobody was listed; records of this line can only come from the device that runs it. |
| `egress_transip_not_seen` (warning) | {egress} çıkışının NAT adresleri ({transips}) {at} çevresinde (±15 dakika) kayıtlarda görülmedi; arama hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT adreslerine genişletildi ({at} çevresinde görülenler: {seen}); arayüz koşulu: {interfaces}. | The NAT addresses of exit {egress} ({transips}) do not appear in the logs around {at} (±15 minutes); the search was widened to every private or shared source-NAT address that no exit claims (seen around {at}: {seen}); interface condition: {interfaces}. |
| `egress_interface_not_seen` (warning) | {at} çevresinde {egress} çıkışının adresleri kayıtlarda yalnızca bu çıkışın arayüzleri ({interfaces}) dışındaki arayüzlerde görünüyor: arayüz adları değişmiş ya da yanlış girilmiş olabilir; arama arayüz koşulu olmadan yapıldı. | Around {at} the logs show the addresses of exit {egress} only on interfaces other than its own ({interfaces}): the interface names may have changed or been entered wrongly; the search was made without the interface condition. |
| `egress_interface_not_seen_kept` (warning) | {at} çevresinde {egress} çıkışının NAT adresi kayıtlarda bu çıkışın arayüzlerinde ({interfaces}) görünmüyor; bu NAT adresi başka bir çıkışla paylaşıldığı için arama arayüz koşuluyla sınırlı kaldı ve sonuç boş olabilir. | Around {at} the logs do not show the NAT address of exit {egress} on its interfaces ({interfaces}); because the address is shared with another exit the search kept the interface condition and may find nothing. |
| `egress_unmapped_seen` (warning) | {at} çevresinde hiçbir çıkışa tanımlı olmayan kaynak NAT adresleri de görüldü: {addresses}. Çıkış tanımları eksik olabilir (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)). | Around {at} the logs also show source-NAT addresses that no exit claims: {addresses}. The exit definitions may be incomplete (Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT)). |
| `egress_unconfigured` (info) | İnternet çıkışları tanımlanmamış: servis sağlayıcının yönlendiricisinin ikinci bir NAT yaptığı varsayıldı ve özel ya da paylaşılan bir NAT adresiyle (RFC 1918, RFC 6598) çıkan bütün kaynak NAT oturumları arandı. Genel bir NAT adresiyle çıkan oturumlar FortiGate'in kendi adresinden çıktığı için aranmadı; o adres sorulursa kayıtlarda görülen adres olarak yanıtlanır. Hatlarınızı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde tanımlayın. | No internet exits are defined: a second NAT by the ISP router was assumed and every source-NAT session with a private or shared NAT address (RFC 1918, RFC 6598) was searched. Sessions that left with a public NAT address left through the FortiGate's own address and were not searched; that address, when asked about, is answered as an address seen in the logs. Define your lines in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT). |
| `port_hint` (warning, **text rewritten in C1**, + `{egresses}`: texts are formatted eagerly, so the new placeholder lands with the engine that passes it, 1.2) | {egresses} çıkışında servis sağlayıcının kaynak portu koruduğu sahada doğrulanmış olarak işaretli: FortiGate NAT portu istenen genel porta eşit adaylar öne alındı. Bu yalnızca sıralamadır; kesin eşleşme değildir. | Exit(s) {egresses} are marked as verified on site to keep source ports at the ISP router: candidates whose FortiGate NAT port equals the requested public port are ranked first. This is only a ranking, not an exact match. |
| `nat_unknown_trandisp` (warning) | Bu aralıkta {count} oturum ayarlarda tanımlı olmayan bir NAT türüyle ({values}) kaydedildi ve aday aramasına alınmadı. nat.snat_trandisp ayarını sahada doğrulayın. | In this window {count} sessions were logged with a NAT type that is not in the settings ({values}) and were not searched. Verify the nat.snat_trandisp setting on site. |
| `public_ip_not_transip` (warning, **text changed in C1**: "Ayarlar → NAT" → "Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)"; round 6, D15 "seen, but not alone": "görünmüyor" → "görünmüyor ya da yalnızca bir kısmında görünüyor", "başka bir çıkış adresi var" → "başka bir çıkış adresi de var") | unchanged otherwise | unchanged otherwise ("Settings → NAT" → the English path; "does not appear" → "does not appear, or only part of the time,", "show another address" → "also show another address") |
| `fortigate_format_unverified` (info) | FortiGate DHCP kayıt biçimi bu kurulumda henüz sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4); kira kaydı FortiOS belgelerindeki biçime göre okundu. | The FortiGate DHCP record format has not yet been verified on site at this installation (DEVICE-SETUP.md section 2.4); the lease was read by the format of the FortiOS documentation. |
| `lease_renewal_not_seen` (warning) | Bu kiranın istenen andan önceki son DHCP kaydı {last} anında (kira süresi {lease} sn). İstenen an bundan kira süresinin yarısından fazla sonra ve arada bir yenileme kaydı yok. Adresi hâlâ tutan bir istemci kira süresinin yarısında yeniler; bu yenileme kayıtlarda yoksa ya yenileme kaydedilmedi ya da adresi o anda başka bir cihaz (ör. havuzda statik adresli bir cihaz) kullanıyordu. Kayıtlar hangisi olduğunu gösteremez; zincir bu nedenle düşük güvenle gösterildi. | The last DHCP record of this lease before the requested moment is at {last} (lease time {lease} s). The requested moment is more than half the lease time later, with no renewal record in between. A client that still holds the address renews at half the lease; if that renewal is not in the logs, either it was not logged or another device (e.g. a static device in the pool) used the address at that moment. The records cannot show which, so the chain is shown with low confidence. |
| `fortigate_dhcp_not_converted_since_lease` (warning; round 5, D8; worded for every DHCP chain in round 6; converted rows of a MAC move the build missed in round 7, D33) | Bu kiranın {last} anındaki son DHCP kaydından sonra, istenen andan önce, {ip} adresi ya da bu kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı FortiGate DHCP kayıtları var (ör. bu sürüme yükseltmeden önce yüklendi, bir geri almadan sonra önceki sürüm yükledi, alanları okunamadı ya da kiralar bir ağ tanımlanmadan veya değiştirilmeden önce oluşturuldu). Bu kayıtlar adresin bırakıldığını, başka bir cihaza verildiğini ya da bu MAC adresinin başka bir adres aldığını gösteriyor olabilir; zincir bu nedenle düşük güvenle gösterildi. Kayıtlar başka bir istemciye ait olabileceği için gösterilmez; ham kayıtlar saklanır ve gece mühürlenir (Arama'da type:event). Ağlar (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar) sonradan tanımlandı ya da değiştirildiyse kiraları bu kiranın başladığı günden bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün). | After this lease's last DHCP record at {last} and before the requested moment there are FortiGate DHCP records for {ip} or for this lease's MAC address that the lease timeline did not apply to this lease (e.g. loaded before the upgrade to this version, loaded by the previous version after a rollback, fields that could not be read, or leases built before a network was entered or changed). They may show that the address was released or given to another device, or that this MAC address took another address, so the chain is shown with low confidence. The records are not shown, because they may belong to another client; the raw lines are kept and sealed every night (Search, type:event). If networks (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar) were entered or changed later, rebuild the leases from this lease's start day to today in Settings → Application settings → 5651 lookup and lease timeline → Rebuild leases (at most 31 days per request). |
| `fortigate_dhcp_no_network` (warning; round 6, D33) | {ip} adresi tanımlı hiçbir ağda değil (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar). Bir istemcinin FortiGate'ten aldığı adresi reddedip (DHCPDECLINE, ör. adresi havuzda statik adresli bir cihaz kullanıyorsa) başka bir adres aldığı yalnızca tanımlı ağlarda anlaşılır; bu kira o anda adresi kullanan cihazı göstermiyor olabilir, zincir bu nedenle düşük güvenle gösterildi. Ağı VLAN numarası olmasa da tanımlayın; sonra kiraları bu kiranın başladığı günden bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün): bir ağ geçmiş kiralara ancak yeniden oluşturmadan sonra uygulanır. | {ip} lies in no configured network (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar). A client that declines an address the FortiGate gave it (DHCPDECLINE, e.g. because a static device in the pool uses it) and takes another is only detected in configured networks; this lease may not show the device that used the address at that moment, so the chain is shown with low confidence. Define the network, even without a VLAN number, then rebuild the leases from this lease's start day to today in Settings → Application settings → 5651 lookup and lease timeline → Rebuild leases (at most 31 days per request): a network applies to past leases only after a rebuild. |
| `egress_observed_not_configured` (warning) | {address} hiçbir internet çıkışında tanımlı değil, ancak {at} çevresinde (±15 dakika) FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor: FortiGate bu adresi o sırada kendisi kullanıyordu (ör. değişken genel IP'li bir hat). Eşleşme FortiGate kaydında yapıldı. Adresin o tarihte kuruma ait olduğunu servis sağlayıcıdan doğrulayın ve hattı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde tanımlayın. | {address} is not defined in any internet exit, but around {at} (±15 minutes) the FortiGate logs show it as a source-NAT address: the FortiGate itself used this address at that time (e.g. a line with a changing public IP). The match was made on the FortiGate record. Confirm with the ISP that the address belonged to the organisation on that date, and define the line in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT). |
| `egress_changed_after_event` (warning) | {at} anından sonra şu internet çıkışlarının tanımı değişti: {changes_tr}. Sorgu bugünkü tanımlarla yapıldı; bu çıkışların o tarihteki adresleri farklıysa sonuç farklı olabilir. Önceki tanımlar kurumun denetim kaydındadır (settings.change) ve sistem yöneticisinden istenebilir. | These internet exits were changed after {at}: {changes_en}. The lookup used today's definitions; if these exits had other addresses on that date, the result may differ. The earlier definitions are in the organisation's audit log (settings.change) and can be requested from its administrator. |
| `session_changed_exit` (warning) | {count} oturumun kayıtları farklı çıkışlarda görünüyor ({egresses}); her eşleşmenin çıkışı istenen ana en yakın kayda göre verildi. | The records of {count} session(s) appear on different exits ({egresses}); each match's exit is taken from its record nearest to the requested moment. |
| `egress_not_logged_but_seen` (warning) | {address}, {egress} çıkışına FortiGate'ten geçmeyen bir hat olarak tanımlı, ancak {at} çevresinde (±15 dakika) FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor: hat FortiGate'e taşınmış ya da türü yanlış girilmiş olabilir. Eşleşme FortiGate kaydında yapıldı. Çıkışın türünü Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde düzeltin. | {address} is defined in exit {egress} as a line that does not pass the FortiGate, but around {at} (±15 minutes) the FortiGate logs show it as a source-NAT address: the line may have been moved behind the FortiGate, or its type entered wrongly. The match was made on the FortiGate record. Correct the exit's type in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT). |
| `dhcp_sources_overlap` (warning; `{sources}` as "MikroTik DHCP", "FortiGate DHCP (VDOM <vd>)", and `{network}` as "VLAN 42" or the network's name and subnet, 2.6.3) | {network} alt ağında {at} çevresinde iki DHCP kaynağından kayıt var ({sources}): aynı ağda iki DHCP sunucusu olabilir. Zincir düşük güvenle gösterildi (uyarı R20-lease:dhcp-sources-overlap). | Around {at} the subnet of {network} has records of two DHCP sources ({sources}): there may be two DHCP servers on one network. The chain is shown with low confidence (alert R20-lease:dhcp-sources-overlap). |
| `fortigate_format_verified` (info) | FortiGate DHCP kayıt biçimi bu kurulumda {at} tarihinde {by} tarafından sahada doğrulandı olarak işaretlendi (DEVICE-SETUP.md bölüm 2.4). | The FortiGate DHCP record format was marked as verified on site at this installation on {at} by {by} (DEVICE-SETUP.md section 2.4). |
| `session_from_unknown_source` (warning) | {count} eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi ({sources}); bu eşleşmeler ayrıca kendi satırlarında işaretlidir. Bu kayıtlar kurumun FortiGate'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır (uyarı "yeni log kaynağı"). | The records of {count} match(es) came from a sender that is not configured ({sources}); those matches are also marked on their own rows. Do not use them as evidence until it is confirmed that they came from the organisation's FortiGate (alert "new log source"). |
| `source_configured_later` (info) | Bu sonuçtaki kayıtların bir kısmı {sources} kaynağından: gönderici, kayıtlar alındığında yapılandırmada tanımlı değildi ve adresi sonradan bir FortiGate kaynağı olarak tanımlandı (gönderici sonradan yapılandırıldı). Kayıtlar bu yüzden kanıt sayıldı. | Some records of this result come from {sources}: the sender was not configured when they arrived, and its address was configured as a FortiGate source later (sender configured later). The records were therefore used as evidence. |
| `egress_public_dynamic` (warning) | {egress} hattının genel adresi değişebilir olarak işaretli; {address} adresinin {at} anında bu hatta ait olduğu yalnızca servis sağlayıcıdan doğrulanabilir. | The public address of {egress} is marked as changing; that {address} belonged to this line at {at} can only be confirmed by the ISP. |
| `egress_internal` (warning) | {address}, {egress} tanımına göre FortiGate'in iç ağlara (DMZ, başka bir VLAN, VPN ya da şube) yaptığı kaynak NAT'ın adresidir, bir internet çıkışı değildir; oturum aranmadı ve kimse listelenmedi. Adres bir internet hattına aitse tanımı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde düzeltin. | {address} is, by the definition {egress}, the address of the FortiGate's source NAT towards internal networks (a DMZ, another VLAN, a VPN or a branch), not an internet exit; no session was searched and nobody was listed. If the address belongs to an internet line, correct the definition in Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT). |
| `source_clock_offset` (warning) | {at} çevresinde şu kaynakların kayıt zamanları ARCILOGGER'ın alma zamanından ortanca olarak saniye cinsinden şu kadar farklıydı: {offsets} (sınır {limit} saniye). Arama ve kira zaman çizelgesi cihazın zamanlarını kullandı: doğru oturum ya da kira bu fark kadar kaymış bir zaman aralığında olabilir; doğru oturum bulunmamış ve başka bir oturum bulunmuş olabilir, kira da o anı kapsamıyor olabilir. Bu sonuçta zincir varsa düşük güvenle gösterildi; sonuç boşsa toleransı bu fark kadar genişletip sorguyu yeniden çalıştırın. | Around {at} the record times of these sources differed from ARCILOGGER's receive time by a median of (in seconds): {offsets} (limit {limit} seconds). The search and the lease timeline used the device's times: the right session or lease may lie in a window shifted by that offset; the right session may have been missed and another found, and the lease may not cover the moment. Any chain of this result is therefore shown with low confidence; if the result is empty, widen the tolerance by the offset and run the lookup again. |

**Retired keys:** `public_ip_not_configured` and `wan_transip_not_seen` stay in the catalogue
and keep their tones, so stored lookups still render, but they are never emitted again; a test
asserts that. The web tone map gains `warning` for every key marked warning above. A test
checks that every key the engine can emit has a tone or is `info` by design.

### 2.12 API (WP-D)

- **Lookup schemas** (`routers/lookup.py` and its pydantic models): the fields of 2.10. The
  `lookup.run` audit `details` gain `egress: {resolution, names}`, in machine form. The 503
  `lookup_settings_invalid` text (`routers/lookup.py:106-107`, which still names a NAT page
  that does not exist) becomes "5651 sorgu ayarları geçersiz: İnternet çıkışları (nat.egresses)
  değeri okunamadı (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi →
  İnternet çıkışları (NAT)); sistem yöneticisi düzeltmelidir." / "The 5651 lookup settings are
  invalid: the internet exits value (nat.egresses) cannot be read (Settings → Application
  settings → 5651 lookup and lease timeline → Internet exits (NAT)); an administrator must fix
  it.". One more line, written by WP-C1 as a listed exception (4.1): `settings.egresses()` is
  called inside the existing `try` that maps `(TypeError, ValueError)` to that 503, right after
  `from_settings` (2.8.1). Without it a stored bad `nat.egresses` would raise inside `run_lookup`,
  which runs outside that `try`, and the API would answer 500 (1.2). Nothing else in the router
  changes: the status rows are read by `run_lookup` itself (2.10).
- **Report cross-check** (`reports/consistency.py`):
  - `PARSER_OF_TABLE["fg_dhcp"] = "fortigate"`;
  - `_lease_claims` treats `fg_dhcp` refs like `mt_dhcp` refs (`ip` and `mac`), except **the line
    that ended a `superseded` lease** (a ref of either DHCP table with `time >= end`), which claims
    only the field it shares with the lease: `ip` when it names the lease's IP (another device
    took the address, or the other table's ACK of the same pair, D5), `mac` when it names another
    IP (the MAC moved: D33 and its MikroTik variant). The IP it names is read with
    `refs.dhcp_ip(ref)`. The mt_dhcp rule of today ("`mac` unless it is the superseding line")
    would claim `ip` = .57 on a D33 closing line that holds .58, and every report citing such a
    lease would print a false "ham satırda … uyuşmuyor" warning on a correct chain;
  - new `_srcmac_claims(chain)`: for `mac_source == "srcmac"`, on the cited `fg_traffic` ref,
    `{srcip: chain.internal_ip, srcmac: chain.device.mac}`;
  - `fg_dhcp` lines are no longer `not_checked`.
- **Report texts** (`reports/texts.py`, `render.py`):
  - `TABLES["fg_dhcp"]` = "FortiGate DHCP kaydı" / "FortiGate DHCP record";
  - method item (2) becomes "(2) DHCP sunucusunun kayıtlarından (MikroTik DHCP, FortiGate DHCP
    olayları) ve saatlik ARP-LOG kayıtlarından kurulan IP–MAC zaman çizelgesi …" / "(2) the
    IP–MAC timeline built from the DHCP server's records (MikroTik DHCP, FortiGate DHCP events)
    and hourly ARP-LOG lines …";
  - a chain without a lease prints "Zincir kurulamadı: o anda bu iç IP adresi için kira kaydı
    yok." / "No chain: no lease covered this internal IP address at that moment.";
  - a srcmac step prints the row "MAC adresi (kısmi kanıt): FortiGate oturum kaydının srcmac
    alanı, {interface} arayüzü; FortiGate'in aygıt tanıma bilgisine göre kaynak IP adresi o anda
    bu MAC adresiyle ilişkilendirilmişti; kira kaydı yok" / "MAC address (partial evidence):
    srcmac field of the FortiGate session record, interface {interface}; the FortiGate's device
    identification associated the source IP with this MAC address at that moment; no lease"
    (one placeholder name, `{interface}`, in both languages);
  - a srcmac conflict (D10) prints the lease chain and the srcmac alternative with both MACs, as
    `ip_conflict` alternatives are printed today, but under its own header "Olası diğer zincir
    (srcmac, kısmi kanıt)" / "Possible other chain (srcmac, partial evidence)" for an alternative
    with `mac_source = "srcmac"` (the `ip_conflict` header stays for the others);
  - **section 4 gains one row** for a public-IP request, "Çıkış / Exit: {resolution label} —
    {name} ({label}), {mode}" (the exits of `outcome.egress.egresses`; no name for
    `unconfigured` and `no_egress`), from **one TR/EN label table** shared with the web header
    line (2.13) and the reading table (2.15): `public` "Genel IP" / "Public IP"; `transip` "NAT
    adresi" / "NAT address"; `fallback` of a `direct` exit "Doğrudan hat doğrulanamadı" /
    "Direct line not confirmed"; `fallback` of a `transip` resolution "NAT adresi
    doğrulanamadı" / "NAT address not confirmed"; `observed` "Kayıtlarda görülen adres" /
    "Address seen in the logs"; `public_unknown` "Genel adresi bilinmeyen hatlar" / "Lines with
    unknown public address"; `unconfigured` "Çıkış tanımlı değil" / "No exits defined";
    `no_egress` "Çıkış yok" / "No exit". Mode names: "Doğrudan" / "Direct", "Servis sağlayıcı
    NAT'ı" / "ISP NAT", "FortiGate'ten geçmiyor" / "Does not pass the FortiGate", "İnternet
    çıkışı değil" / "Not an internet exit". So the auditor finds in the PDF the label the
    reading table explains;
  - **every match row** (`render.py` `match()`) gains "Çıkış / Exit: {name} ({label}) · dstintf
    {dstintf}" from `NatMatch.egress` and the cited row's `dstintf` ("-" when unclaimed), and the
    `session_changed_exit` match names both exits, so an auditor sees which line each candidate
    of a `public_unknown`, `unconfigured` or `fallback` answer left by;
  - **an outcome stored before the upgrade** (`egress` None, `TrafficInfo.dstintf` None,
    `LeaseInfo.source_table` None; reports are rendered on their first download, 1.2) prints no
    section-4 exit row, no exit row on its matches, no per-resolution method paragraph and no
    FortiGate lease additions; a `double_nat` answer prints the "Çift NAT varsayımı" variant with
    the CGNAT sentence (never the old sentence that states the ISP modem as a fact);
  - **unconverted FortiGate DHCP rows** (`LookupOutcome.unconverted_dhcp`, D8: at most the one
    covering row before the moment per IP, internal mode and exact matches only) are printed in
    section 6 like every cited row (raw line, seal facts), each marked "kira zaman çizelgesine
    girmedi; zincir kurulmadı, güven düzeyi verilmez" / "not in the lease timeline; no chain
    built; no confidence given", so it is never read as an attribution; a candidate report
    carries the `fortigate_dhcp_not_converted` text with the IPs and no such line;
  - **a match with rows of an untrusted sender** (`NatMatch.untrusted_sources`, D24) gets a
    "UYARI / Warning" row on that match, as a contradicted match does today: "Bu eşleşmenin
    kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi: {sources}; kurumun
    FortiGate'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır." / "The records of this
    match came from a sender that is not configured: {sources}; do not use them as evidence until
    it is confirmed that they came from the organisation's FortiGate.";
  - **`no_egress` and `not_logged` searched no session**, so the generic `none` texts ("Ölçütlere
    uyan kayıt bulunamadı", `MODE_DETAIL_TR['none']` in section 4 and the section 5 line, 1.2),
    which read as a log-based "nobody", are replaced for these two resolutions by "Oturum
    aranmadı: istenen adres {address} tanımlı internet çıkışlarından hiçbirine ait değil (bölüm
    4)." / "No session was searched: the requested address {address} belongs to none of the
    defined internet exits (section 4)." and "Oturum aranmadı: istenen adres {address}
    FortiGate'ten geçmeyen {exit} çıkışına ait (bölüm 4)." / "No session was searched: the
    requested address {address} belongs to exit {exit}, which does not pass the FortiGate
    (section 4).";
  - **the lease row** of an `fg_dhcp` lease adds "kira süresi (FortiGate kaydı): {lease} sn · son
    DHCP kanıtı: {last}" / "lease time (FortiGate record): {lease} s · last DHCP evidence:
    {last}" (`LeaseInfo.lease_seconds`, `last_dhcp_evidence`, the lease's own last DHCP record
    at or before the chain's moment, never the line that ended it and never a later renewal,
    2.7; "ayarlardaki süre" / "configured time" when `lease_seconds` is null), and the source
    through `TABLES`;
  - **method notes for FortiGate leases**, derived from the stored outcome only, never from the
    setting at render time (a report is rendered on its first download, possibly after the
    setting changed; round 2 note 15): when a chain carries `fortigate_format_unverified`, the
    method section prints "FortiGate DHCP kayıt biçimi bu kurulumda sahada doğrulanmadı; kira
    kayıtları FortiOS belgelerindeki biçime göre okundu." / "The FortiGate DHCP record format has
    not been verified on site at this installation; the leases were read by the format of the
    FortiOS documentation."; when a chain carries `fortigate_format_verified`, its text (who
    marked it, and when, as stored; 2.11) instead; a chain at `low` through
    `lease_renewal_not_seen`, `fortigate_dhcp_not_converted_since_lease` or
    `fortigate_dhcp_no_network` prints that text beside its lease row (the second with no cited
    `fg_event` line);
  - `source_clock_offset`, `egress_public_dynamic` and `egress_internal`, when present, are
    printed in the method section with their texts (2.11);
  - `egress_changed_after_event`, when present, is printed in the method section as well, with
    what each changed exit holds now (or "silindi" / "deleted"), so it can be read without the
    audit log;
  - **Yöntem / Method: one paragraph per (resolution, mode) row below**, eleven in all: `public`
    direct, `public`/`transip` isp_nat, `not_logged`, `transip` internal, `fallback` direct,
    `fallback` transip, the `fallback` addition for searched addresses of another exit,
    `observed`, `public_unknown`, `unconfigured`, `no_egress`. `{exit}` is the exit's name and label, `{exits}` the
    comma-joined "name (label)" of the exits searched, `{other}` the same for F's exits,
    `{address}` the requested address, `{transips}`/`{interfaces}` the exit's values, `{seen}` the
    unclaimed addresses seen, except in the two `fallback` paragraphs, where it is the private or
    shared addresses outside own(E) that the probe saw, on E's interfaces when E declares any (at
    most 5; the D15 evidence, `searched.outside_seen`, round 6), `{searched}` the addresses actually searched
    (`egress.searched`);
    when `searched.unclaimed` is true, `{searched}` reads "… ve hiçbir çıkışa tanımlı olmayan
    bütün özel ya da paylaşılan kaynak NAT adresleri (T çevresinde görülenler: …)" / "… and
    every private or shared source-NAT address no exit claims (seen near T: …)", so a candidate
    through a sixth unclaimed address is inside the stated search; `{configured}` lists every configured exit as "name (label): mode, public
    addresses" (the firewall's own addresses, not personal data).
    Texts (TR / EN):
    - `public`, `direct`, exact or port missing: "{exit} çıkışı doğrudan tanımlı: genel IP adresi
      {address} FortiGate'in kendisindedir ve oturum kayıtlarında kaynak NAT adresi (transip)
      olarak görünür. Talepteki genel port FortiGate'in NAT portuyla (transport) karşılaştırıldı
      (port yoksa o adresle çıkan oturumlar aday olarak sıralandı)." / "Exit {exit} is defined as
      direct: the public IP address {address} is on the FortiGate itself and appears in its
      session records as the source-NAT address (transip). The requested public port was
      compared with the FortiGate's NAT port (transport) (without a port, the sessions that left
      through that address were ranked as candidates)."
    - `public` or `transip`, `isp_nat`: "{exit} çıkışı servis sağlayıcı NAT'ı olarak tanımlı: bu
      tanıma göre servis sağlayıcının yönlendiricisi FortiGate'in bu hattaki NAT adresini
      ({transips}) ikinci kez çevirir ve bu dönüşümün kaydı ARCILOGGER'da yoktur. Arama bu çıkışın NAT
      adresleriyle{ ve arayüzleriyle ({interfaces})} sınırlandı. Port koruma: {kullanılmadı |
      yalnızca sıralamada kullanıldı (sahada doğrulandı olarak işaretli)}." / "Exit {exit} is
      defined as ISP NAT: by this definition the ISP router translates the FortiGate's NAT
      address on this line ({transips}) a second time and ARCILOGGER has no record of that
      translation. The search was
      limited to this exit's NAT addresses{ and interfaces ({interfaces})}. Port preservation:
      {not used | used for ranking only (marked as verified on site)}." For `transip` it adds:
      "İstenen adres bu çıkışta FortiGate'in kendi NAT adresidir; eşleşme FortiGate kaydında
      transip ve transport ile yapıldı." / "The requested address is the FortiGate's own NAT
      address on this exit; the match was made on the FortiGate record by transip and transport."
      For a `public` answer of an exit marked `public_dynamic` it adds the text of
      `egress_public_dynamic` (2.11): only the ISP can confirm that the address was the line's at T.
    - `fallback`, the failed exit is `direct`: "{exit} doğrudan olarak tanımlı, ancak {address} o
      anın çevresinde FortiGate kayıtlarında NAT adresi olarak görünmüyor ya da yalnızca bir
      kısmında görünüyor; kayıtlar o sırada bu hatta özel ya da paylaşılan başka kaynak NAT
      adresleri ({seen}) de gösteriyor. Bu nedenle kesin eşleşme yapılamadı ve {searched}
      adresleriyle çıkan oturumlar aday olarak sıralandı: bu hatta o sırada servis sağlayıcının
      yönlendiricisi ikinci bir NAT yapmış olabilir." / "{exit} is defined as direct, but around
      that moment {address} does not appear as a NAT address in the FortiGate logs, or only part
      of the time; the logs also show other, private or shared source-NAT addresses on this line
      then ({seen}). So no exact match was possible, and the sessions that left through
      {searched} were ranked as candidates: the ISP router may have done a second NAT on this line
      at that time."
    - `fallback`, the failed resolution is `transip` (an `isp_nat` exit's NAT address, E-k2): "{exit}
      çıkışı servis sağlayıcı NAT'ı olarak tanımlı; NAT adresi {address} o anın çevresinde
      FortiGate kayıtlarında görünmüyor ya da yalnızca bir kısmında görünüyor; kayıtlar o sırada
      özel ya da paylaşılan başka kaynak NAT adresleri ({seen}) de gösteriyor.
      Bu nedenle kesin eşleşme yapılamadı ve {searched} adresleriyle çıkan oturumlar aday olarak
      sıralandı." / "Exit {exit} is defined as ISP NAT; around that moment its NAT address
      {address} does not appear in the FortiGate logs, or only part of the time; the logs also show
      other, private or shared source-NAT addresses then ({seen}). So no exact match was possible,
      and the sessions that left through {searched} were ranked as candidates." No bridge-mode sentence and no "define as Direct" advice.
    - `fallback` addition when F is not empty (the search entered another exit's addresses,
      D15): "Aranan adreslerin bir kısmı {other} çıkışına aittir: bu çıkış yükseltmede eski
      ayarlardan aktarıldı ve arayüz tanımlanmadığı için aynı hat sayıldı. Liste o hattın
      kullanıcılarını da içerebilir; her hattın arayüzleri girilince bu olmaz." / "Some of the
      searched addresses belong to exit {other}: it was converted from the old settings at the
      upgrade and, with no interfaces defined, counted as the same line. The list may contain
      that line's users too; once each line's interfaces are entered this no longer happens."
    - `observed`: the text of `egress_observed_not_configured` (2.11), or of
      `egress_not_logged_but_seen` when a `not_logged` exit claims the address.
    - `public_unknown`: "{address} hiçbir çıkışın genel adresi olarak tanımlı değil. Arama, genel
      adresi tanımlanmamış ya da değişebilen çıkışlarda yapıldı: {exits}; bu çıkışlarda servis
      sağlayıcının ikinci bir NAT yaptığı varsayıldı. Adres bu hatlardan birine ait değilse liste
      ilgisiz kullanıcıları gösterebilir." / "{address} is not the public address of any exit. The
      search was made in the exits whose public address is not defined or may change: {exits};
      a second NAT by the ISP router was assumed for them. If the address belongs to none of
      these lines, the list may show unrelated users."
    - `unconfigured`: "İnternet çıkışları tanımlanmamış: servis sağlayıcının ikinci bir NAT
      yaptığı varsayıldı ve FortiGate'in özel ya da paylaşılan bir NAT adresiyle çıkan bütün
      kaynak NAT oturumları arandı; genel bir NAT adresiyle çıkan oturumlar FortiGate'in kendi
      adresinden çıktığı için aranmadı." / "No internet exits are defined: a second NAT by the ISP
      router was assumed and every source-NAT session of the FortiGate with a private or shared
      NAT address was searched; sessions that left with a public NAT address left through the
      FortiGate's own address and were not searched."
    - `not_logged`: "{address}, {exit} çıkışına ait; bu çıkış FortiGate'ten geçmez ve
      ARCILOGGER'da bu hattın oturum kaydı yoktur. Bu nedenle rapor bir sonuç içermez; kayıt ancak
      hattı yöneten cihazdan alınabilir." / "{address} belongs to exit {exit}, which does not pass
      the FortiGate; ARCILOGGER has no session record of this line. The report therefore contains
      no result; records can only come from the device that runs the line."
    - `transip`, `internal`: "{address}, {exit} tanımına göre FortiGate'in iç ağlara (DMZ, başka
      bir VLAN, VPN ya da şube) yaptığı kaynak NAT'ın adresidir; bir internet çıkışı değildir ve
      oturum aranmadı. Bu nedenle rapor bir sonuç içermez." / "By the definition {exit},
      {address} is the address of the FortiGate's source NAT towards internal networks (a DMZ,
      another VLAN, a VPN or a branch); it is not an internet exit and no session was searched.
      The report therefore contains no result." Sections 4 and 5 print "Oturum aranmadı: istenen
      adres {address} internet çıkışı olmayan {exit} tanımına ait (bölüm 4)." / "No session was
      searched: the requested address {address} belongs to {exit}, which is not an internet exit
      (section 4).", as for `not_logged`.
    - `no_egress`: "{address} kurumun tanımlı internet çıkışlarından hiçbirine ait değil ve o
      sırada FortiGate kayıtlarında da görünmüyor; bu nedenle oturum aranmadı. Adres kurumun bir
      hattına aitse çıkış tanımlanıp sorgu yeniden çalıştırılmalıdır." / "{address} belongs to
      none of the organisation's defined internet exits and does not appear in the FortiGate logs
      at that time either, so no session was searched. If the address belongs to one of the
      organisation's lines, the exit must be defined and the lookup run again." Then, so that the
      reader can check the claim: "Sorgu anında tanımlı çıkışlar: {configured}." / "Exits defined
      at the time of the lookup: {configured}."
  - **the double-NAT paragraph is conditional** (`render.py:416`, `texts.DOUBLE_NAT_*` replaced by
    three variants; printed for candidates/none answers with `double_nat` true, as today,
    **except for resolution `transip`**: there the request names the FortiGate's own NAT
    address, the match is made by `transip` and `transport`, and the `transip` sentence of the
    method paragraph stands; "the public port cannot be mapped exactly" beside it would
    contradict it):
    - configured `isp_nat` (resolution `public`): "Çift NAT: {exit} çıkışı servis
      sağlayıcı NAT'ı olarak tanımlı; servis sağlayıcının yönlendiricisi FortiGate'in adresini
      ikinci kez çevirir. ARCILOGGER'da bu ikinci dönüşümün kaydı yoktur; bu nedenle talepteki
      genel port FortiGate'in çıkış portuna (transport) kesin olarak eşlenemez." / "Double NAT:
      exit {exit} is defined as ISP NAT; the ISP router translates the FortiGate's address a
      second time. ARCILOGGER has no record of that second translation, so the public port of
      the request cannot be mapped exactly to the FortiGate's outgoing port (transport).";
    - assumed (`unconfigured`, `public_unknown`): "Çift NAT varsayımı: bu adres için servis
      sağlayıcının ikinci bir NAT yaptığı varsayıldı; ARCILOGGER'da böyle bir dönüşümün kaydı
      yoktur ve talepteki genel port FortiGate'in çıkış portuna kesin olarak eşlenemez." /
      "Double NAT assumed: a second NAT by the ISP was assumed for this address; ARCILOGGER has no
      record of such a translation, and the public port of the request cannot be mapped exactly
      to the FortiGate's outgoing port.";
    - `fallback`: the `fallback` method paragraph above stands in for it (it never says the line
      sits behind an ISP router as a fact; the `transip` variant never mentions bridge mode);
    - every variant except `fallback` then adds the CGNAT sentence: "Genel adres başka
      müşterilerle paylaşılıyorsa (CGNAT) aday listesi doğru oturumu içermeyebilir; kesin yanıt
      için servis sağlayıcının NAT kaydı gerekir." / "If the public address is shared with other
      customers (CGNAT), the candidate list may not contain the right session; an exact answer
      needs the ISP's NAT record.";
    - then the result sentence (unchanged `DOUBLE_NAT_RESULT_*`) and the advice: "Kesin eşleşme
      için şunlardan biri gerekir: (a) servis sağlayıcının ilgili zamana ait NAT kaydı (genel port
      → FortiGate çıkış portu); (b) servis sağlayıcı yönlendiricisinin köprü (bridge) moduna
      alınması ve FortiGate'in genel IP adresini doğrudan tutması (Ayarlar → Uygulama ayarları →
      5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde bu hattı
      'Doğrudan' olarak tanımlayın)." / "An exact answer needs either (a) the ISP's NAT record
      for that time (public port → FortiGate port) or (b) the ISP router in bridge mode with the
      public address on the FortiGate (define this line as 'Direct' in Settings → Application
      settings → 5651 lookup and lease timeline → Internet exits (NAT))." For `fallback` only (a)
      is printed, with — for a failed `direct` exit only — "ya da hattın o tarihte köprü modunda
      olduğunun servis sağlayıcıdan doğrulanması" / "or confirmation from the ISP that the line
      was in bridge mode on that date"; the "define as Direct" advice is never printed for an exit
      that is already direct, nor for a `transip` fallback;
  - **raw lines with device identification**: a cited session line of an interface with device
    identification on prints `srcmac` and the F19 fields as they are, also for other people's
    candidate sessions and with `lookup.srcmac_interfaces` empty (D21; raw lines are never
    modified); a PDF test pins it (3.5);
  - `resolution = fallback` is emitted exactly in the rows of 2.8.3 marked **fallback**;
  - the PDF is printed through the existing `pdftext` checks.
- **`GET /settings/fortigate/observed?hours=24`**:
  - `hours` 1–72; requires `settings.manage` (admin), the permission of the whole settings
    router and of `GET /settings` (`routers/settings.py:47`); `docs/roles.md` and
    `web/e2e/permissions.spec.ts` say so;
  - not audited (counts and the firewall's own addresses only);
  - cached 5 minutes per `hours`; `statement_deadline` 5 s, otherwise 504
    `fortigate_observed_timeout` "FortiGate kayıtları {seconds} saniyede özetlenemedi; biraz
    sonra yeniden deneyin." / "The FortiGate records could not be summarised within {seconds} s;
    try again a little later.".

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

  NAT part: `GROUP BY transip, dstintf, trandisp` over trusted `fg_traffic` with `transip IS NOT
  NULL`, `ORDER BY count DESC LIMIT 100`. When the stored `nat.egresses` cannot be parsed, every
  row has `egress: null` and the `nat` object carries `"exits_invalid": true` (the panel says so
  and points to `R20-nat:invalid`), instead of a 500. srcmac part: the last hour, trusted rows with `srcmac
  IS NOT NULL` grouped by `srcintf` (the most distinct **IPv4** `srcip` per MAC through a
  subquery, as the D10 guard counts), `LIMIT 50`.
- **`GET /sources`** gains `lease_evidence` (the Sources overview, `sources.read`):

  ```json
  {"hours": 24,
   "mt_dhcp": {"rows": 1234, "last_hour": "…", "leases_current": 310},
   "fg_dhcp": {"rows": 0, "last_hour": null, "leases_current": 0, "refused_rows": 0,
               "excluded_rows": 0, "format_verified": false},
   "mt_arp": {"rows": 560, "last_hour": "…", "leases_current": 42},
   "srcmac": {"interfaces": [], "rows_last_hour": null},
   "state": "ok | none | refused", "none_tables": []}
  ```

  - `rows` counts **usable** rows of the last 24 h (`mt_dhcp` `assigned`/`deassigned`, `mt_arp`,
    `fg_dhcp` through `trust.kept`; primary-key time ranges on these small tables), not
    `search_counts`, which counts refused and unknown-sender rows too.
  - `fg_dhcp.refused_rows` counts the `fg_dhcp` rows of the last 24 h that rules 1 and 4–7 of
    2.6.1 refuse; `excluded_rows` those of rules 2–3 (excluded interfaces, IPv6), configuration
    shown as an info line ("hariç tutulan arayüzler: N kayıt" / "excluded interfaces: N
    records"), never as refused; `format_verified` is `dhcp.fortigate_format_verified` (D29).
  - `leases_current` per `source_table`: leases open or ended in the last 24 h
    (`ix_leases_source_table_end`), shown for information.
  - `state`, one value, in this order: **`refused`** while `fg_dhcp.refused_rows > 0` (the live
    count, through the same `trust.refusal` predicate and 24-h window as the alert, so it drops
    to 0 as soon as the administrator corrects the setting or the source and rebuilds); it
    **wins over `none`**: a feed whose rows are all refused opens both causes, and the `none`
    text "make the DHCP server send its records" would send the administrator to the wrong fix,
    since the records do arrive. Otherwise **`none`** while an `R20-lease:none:<table>` or
    `R20-lease:absent` alert is active, **read from the active alerts** (one implementation of
    the evidence windows and the 30-day lookback; up to 5 minutes of lag), with `none_tables`
    naming the tables of the active `none` causes (empty for `absent`); otherwise `ok`. (A
    healthy FortiGate-only site with 604,800-s leases that logged no DHCP row for a day therefore
    stays `ok`, as its alert does.) The `fortigate-refused` alert adds its severity and reasons
    on the Security page.
  - `rows_last_hour` counts `fg_traffic` rows with srcmac in the last hour on the configured
    interfaces; it is only computed when interfaces are configured, cached 60 s, with a 2-s
    deadline (`null` on timeout).
- **`lookup_testing.build_site`** gains `dhcp: Literal["mikrotik", "fortigate"] = "mikrotik"`,
  `srcmac: bool = False` and `wan2: bool = False`:
  - `fortigate`: the same golden chain through `Evidence.fortigate_dhcp` (`interface="vlan42"`,
    lease 86400, host `PLN-PC14`, renewal ACKs every 12 h so the chain stays covered without
    traffic extension, D26), for WP-D's API and PDF tests; the worker's own goldens build the same
    site with `Evidence.fortigate_dhcp` directly (the worker does not depend on the API, 1.2);
  - `srcmac`: the traffic rows carry `srcintf="vlan42"` and `srcmac`;
  - `wan2`: one more session of the phone through `100.64.13.9`, `dstintf="wan2"`.

  The defaults leave today's rows and raw lines byte-identical.
- **Samples** (`capture_api_samples.py`; regenerate, then `--check`):
  - `settings.json` with the new keys and a 422 egress error;
  - `lookups.json` with a FortiGate-only internal and candidate chain, a chain at `low` through
    `lease_renewal_not_seen`, a srcmac-only chain, a srcmac conflict, a `hat-2` candidate list, a
    quiet `direct` → `none`, a `fallback` (direct and transip), an `observed` address (with and
    without exits), `not_logged` and `not_logged` seen, `public_ip_no_egress` (its `egresses`
    listing the configured exits), `egress_public_unknown`, `egress_public_dynamic`,
    `egress_internal`, `egress_changed_after_event`, `source_clock_offset`, an
    `unconverted_dhcp` citation, a match with `untrusted_sources`;
  - `sources.json` with `lease_evidence` (states `ok`, `none` with `none_tables`, `refused`);
  - `settings-observed.json`;
  - `api-schemas.json` exported.
- **Error codes:** `setting_invalid` 422 with the detail codes of 2.5 (existing envelope
  `{key, code, message_tr, message_en}`, no field path: the exit editor shows a server error at
  the editor level, and the field-level hints come from its client-side mirror of the same
  rules, 2.13); `lookup_settings_invalid` 503 (existing, text above); `fortigate_observed_timeout`
  504 (new); `ipv6_not_supported` 422 (unchanged).

### 2.13 Web (WP-E)

- **Ayarlar → Uygulama ayarları, group lookup:**
  - `nat.egresses` gets `EgressEditor` (dispatch by key in `setting-editors.tsx`, like
    `ServerSecondsEditor`):
    - one row per exit with name, label, mode (select: "Doğrudan (genel IP FortiGate'te)" /
      "Direct (public IP on the FortiGate)", "Servis sağlayıcı NAT'ı (çift NAT)" / "ISP NAT
      (double NAT)", "FortiGate'ten geçmiyor" / "Does not pass the FortiGate", "İnternet çıkışı
      değil (DMZ, VLAN, VPN ya da şubeye NAT)" / "Not an internet exit (NAT towards a DMZ, VLAN,
      VPN or branch)"), public addresses (not for the last), NAT addresses (only for ISP NAT and
      the last), interfaces, port preservation (only for ISP
      NAT: "Bilinmiyor" / "Unknown", "Korunuyor (sahada doğrulandı)" / "Kept (verified on
      site)", "Korunmuyor" / "Not kept"), and for ISP NAT the check box "Genel IP değişebilir
      (dinamik)" / "Public IP may change (dynamic)" (`public_dynamic`);
    - inline warnings: an ISP NAT exit without a public address: "Genel adres girilmedi: bu
      çıkış, hiçbir çıkışa tanımlı olmayan her genel IP için aranır." / "No public address:
      this exit is searched for every public IP that no exit claims."; an ISP NAT exit marked
      dynamic: "Genel IP değişebilir: bu çıkış, tanımlı adreslerinin yanında hiçbir çıkışa
      tanımlı olmayan genel IP'ler için de aranır." / "Public IP may change: besides its listed
      addresses, this exit is also searched for public IPs that no exit claims."; a catch-all:
      "Tanımsız tüm NAT adreslerini kapsar." / "Covers every unclaimed NAT address."; two or more
      exits of mode Direct or ISP NAT while any of them has no interfaces: "Her hattın
      arayüzlerini (dstintf) girin; girilmezse sorgu bir çıkışın oturumunu hangi hattan çıktığına
      göre ayıramaz ve bir hattın doğrulanamadığı anlarda arama yalnızca tanımsız adreslere
      yönelir." / "Enter each line's interfaces (dstintf); without them a lookup cannot tell by
      which line a session left, and at moments when a line cannot be confirmed the search falls
      back to unclaimed addresses only."; an ISP NAT exit whose NAT address is not in RFC 1918 or
      RFC 6598 space: "NAT adresi genel bir adres: FortiGate bu adresi kendisi tutuyorsa hat
      doğrudandır, türünü Doğrudan yapın." / "The NAT address is a public address: if the
      FortiGate holds it itself, the line is direct; set its type to Direct."; a converted exit
      (as converted by v0018, D15) without interfaces, shown only while another exit beside it is
      still as converted without interfaces (the pairing of D15's F; round 5: once the partner is
      edited, or a later exit claims the line's NAT address, the old behaviour has ended):
      "Yükseltmede aktarıldı: aktarılan çıkışlar arayüzsüz ve değiştirilmeden kaldıkça sorgular
      onları eski NAT ayarlarındaki gibi tek hat sayar; sonradan eklenen bir çıkışın NAT adresleri
      bunun dışındadır. Her hattın genel adresini ve arayüzlerini girin (KURULUM.md bölüm 13)." /
      "Converted at the upgrade: while the converted exits stay without interfaces and unchanged,
      lookups treat them as one line, as the old NAT settings did; the NAT addresses of an exit
      added later are outside that. Enter each line's public address and interfaces (INSTALL.md
      section 13).";
  - `egress-model.ts` mirrors the server rules and codes of 2.8.1 (the private/shared/reserved
    `public` rule and `setting_egress_isp_nat_direct` included) and shows them on the field; the
    server stays authoritative, and a server 422 (whose details carry no field path, 2.12) is
    shown at the editor level with its code and message. Both test suites read one case list,
    `tests/fixtures/egress-cases.json` (value, expected code or normalised value), as
    `format-vectors.json` is shared today;
  - `FortiGateObservedPanel` sits under the editor: "Kayıtlarda görülen NAT adresleri (son 24
    saat)" / "NAT addresses seen in the logs (last 24 hours)", with the table of 2.12 (with
    `exits_invalid` it says "Kayıtlı çıkış tanımı okunamıyor; çıkış sütunu boş." / "The stored
    exit definition cannot be read; the exit column is empty."); an
    unmapped address has an "Çıkış olarak ekle" / "Add as exit" button that prefills a row by a
    fixed rule: a `transip` outside RFC 1918/6598 prefills **Direct** with `public = [transip]`
    and the row's `dstintf` as interface; an RFC 1918 or RFC 6598 `transip` prefills **ISP NAT**
    with `transips = [transip]`, the `dstintf`, an empty public list and the "Genel adres
    girilmedi" warning (the administrator enters the ISP's public address), and offers beside it
    "İnternet çıkışı değil olarak ekle" / "Add as not an internet exit" (mode `internal` with the
    same `transips` and `dstintf`), with the hint "FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e
    ya da şubeye yaptığı NAT'ın adresiyse bunu seçin; bu adreslerde oturum aranmaz." / "Choose
    this for the address of the FortiGate's NAT towards a DMZ, another VLAN, a VPN or a branch;
    no session is searched on such addresses." (an ISP NAT exit without a public address saved
    for a DMZ address would be searched for every unclaimed public IP); an unknown
    `trandisp` is flagged; the srcmac interfaces table has "srcmac için ekle" / "Use for
    srcmac", disabled when `max_ips_per_mac > srcmac_max_ips`;
  - `dhcp.fortigate_logids` and `dhcp.fortigate_dhcp_msgs` use the generic JSON editor with
    server validation; the help text says that a rebuild applies them to past days;
  - `dhcp.fortigate_traffic_extends` shows its help text as a warning when switched on;
  - the `ServerSecondsEditor` label mentions the FortiGate interface name.
- **Ayarlar → Kaynaklar** (`settings/sources-page.tsx`):
  - the `SupportedTopologies` note (collapsible);
  - for FortiGate sources the hint "FortiGate'in DHCP sunucusunu kullanıyorsanız sistem olay
    kayıtları syslog'a gönderilmelidir (DEVICE-SETUP.md bölüm 2.4)." / "If you use the
    FortiGate's DHCP server, its system event records must go to syslog (DEVICE-SETUP.md section
    2.4).".
- **Kaynaklar** (`pages/sources/`):
  - `LeaseEvidenceCard` "Kira kanıtı" / "Lease evidence" with three states, `refused` winning
    over `none` (2.12): ok (usable counts and current leases per source); refused: "FortiGate
    DHCP kayıtları geliyor ama son 24 saatte {refused} tanesi kullanılmadı; nedenler uyarıda."
    / "FortiGate DHCP records arrive but {refused} of the last 24 hours were not used; the
    reasons are in the alert.", with a link to the alert and the settings (the count follows the
    current settings and sources, so it drops as soon as a correction is saved and the leases
    are rebuilt); none, one line per table of `none_tables`: for `fg_dhcp` and `mt_arp`
    "Kullanılabilir {evidence} kaydı gelmiyor (daha önce geliyordu): bu kayıtlara dayanan 5651
    sorguları iç IP adresinde durabilir. Kaynağın kayıt göndermesini sağlayın (DEVICE-SETUP.md
    bölüm 2.4 ve 3)." / "No usable {evidence} record arrives (it did before): 5651 lookups that
    rest on these records may stop at the internal IP. Make the source send its records
    (DEVICE-SETUP.md sections 2.4 and 3)."; for `mt_dhcp` (the medium cause, a quiet period
    looks the same) "Son 24 saatte yeni MikroTik DHCP kaydı gelmedi; mevcut MikroTik kiraları
    trafikle sürer, yeni istemciler zincirsiz kalabilir; kayıtların geldiğini denetleyin
    (DEVICE-SETUP.md bölüm 3)." / "No new MikroTik DHCP record in the last 24 hours; existing
    MikroTik leases continue with traffic, new clients may have no chain; check that the records
    arrive (DEVICE-SETUP.md section 3)."; for `absent` (`none_tables` empty) "Kullanılabilir DHCP
    ya da ARP-LOG kaydı gelmiyor: 5651 sorguları iç IP adresinde durur (Kurulum kılavuzu bölüm
    0.1). Kira kanıtı isteniyorsa DHCP sunucusunun kayıt göndermesini sağlayın (DEVICE-SETUP.md
    bölüm 2.4 ve 3)." / "No usable DHCP or ARP-LOG record arrives: 5651 lookups stop at the
    internal IP (installation guide section 0.1). For lease evidence, make the DHCP server send
    its records (DEVICE-SETUP.md sections 2.4 and 3)." `{evidence}` is "FortiGate DHCP" or
    "MikroTik ARP-LOG". With `fg_dhcp` rows and `format_verified` false it also shows
    "FortiGate DHCP kayıt biçimi sahada doğrulanmadı (DEVICE-SETUP.md bölüm 2.4)." / "FortiGate
    DHCP record format not verified on site (DEVICE-SETUP.md section 2.4)."; refused rows are
    shown as a count with a link to the alerts, and `excluded_rows` as an info line "Hariç
    tutulan arayüzler: {excluded} kayıt (kira kanıtı sayılmaz)" / "Excluded interfaces:
    {excluded} records (not lease evidence)", never as refused;
  - the same `SupportedTopologies` note.
- **5651 Sorgu result view:**
  - an "Çıkış" / "Exit" line in the header (resolution label, name, label, mode) for public-IP
    requests, from the one label table of 2.12 that the PDF's section-4 row uses too: eight
    labels for the seven resolutions, `fallback` split by the failed exit ("Genel IP", "NAT
    adresi", "Doğrudan hat doğrulanamadı", "NAT adresi doğrulanamadı", "Kayıtlarda görülen
    adres", "Genel adresi bilinmeyen hatlar", "Çıkış tanımlı değil", "Çıkış yok" / "Public IP",
    "NAT address", "Direct line not confirmed", "NAT address not confirmed", "Address seen in
    the logs", "Lines with unknown public address", "No exits defined", "No exit");
  - each match shows its exit and `dstintf`, as the PDF does (2.12), and a match with
    `untrusted_sources` a badge "Tanımsız gönderici" / "Unconfigured sender" with the source
    names;
  - a chain at `low` through `lease_renewal_not_seen`,
    `fortigate_dhcp_not_converted_since_lease` or `fortigate_dhcp_no_network` shows that text
    beside its lease step;
  - a srcmac step shows the badge "Kısmi kanıt: srcmac" / "Partial evidence: srcmac" in place
    of the lease step; a srcmac conflict shows both chains as alternatives do today;
  - a FortiGate lease step shows its lease time and last DHCP evidence (2.12);
  - `lookup.match.noChain` becomes "Zincir kurulamadı: o anda bu iç IP için kira kaydı yok" /
    "No chain: no lease for this internal IP at that moment";
  - the `LeaseInfo` source is shown through `evidenceTables`.
- **Evidence labels:** `EVIDENCE_TABLES` gains `fg_dhcp`; `evidenceTables.fg_dhcp` = "DHCP kaydı
  (FortiGate)" / "DHCP record (FortiGate)".
- **VLAN dialog hint:** "DHCP sunucusunun adı (MikroTik: ör. dhcp20; FortiGate: arayüz adı)" /
  "Name of the DHCP server (MikroTik: e.g. dhcp20; FortiGate: interface name)".
- **Locales:**
  - new keys go into `locales/areas/network/{tr,en}.json` (the area mechanism of W2);
  - the four changed keys (`lookup.match.noChain`, the VLAN hint, the `ServerSecondsEditor`
    label, `evidenceTables.fg_dhcp`, which is added there because the evidence label list
    lives there) are edited in `tr.json`/`en.json` by WP-E only;
  - `locales.test.ts` must pass (parity, no empties, no claims, no milestones).
- **Mocks and e2e:**
  - `mock-admin.ts` mirrors the new codes and drops the IPv4-only `nat.public_ips` divergence
    (that key is gone);
  - `mock-sources.ts` serves `lease_evidence`;
  - specs: `settings-application.spec.ts` (exit editor, server errors, observed panel
    prefill), `lookups.spec.ts` (FortiGate-only, srcmac, `hat-2`, `none` cases),
    `sources.spec.ts` (card states), and axe on the new panels.

**Supported topologies (UI text, `network.topology.*`):**

- Title: "Desteklenen ağ yapıları" / "Supported network setups".
- Items:
  1. "Tek yerleşke ve internete çıkışta bir FortiGate." / "One site with one FortiGate at the
     internet edge."
  2. "Kira kanıtı: MikroTik DHCP ve ARP-LOG kayıtları, FortiGate DHCP olay kayıtları (sahada
     doğrulanmalı) ya da ikisi (farklı ağlarda)." / "Lease evidence: MikroTik DHCP and ARP-LOG
     records, FortiGate DHCP event records (to be verified on site), or both (on different
     networks)."
  3. "Yalnız-FortiGate ağlarda statik IP'li cihazların kira kaydı olmaz; zincir iç IP adresinde
     biter. İsteğe bağlı srcmac yalnızca ağ geçidi FortiGate olan ve sahada denetlenmiş ağlarda
     kısmi kanıt verir." / "On FortiGate-only networks static-IP devices have no lease; the
     chain ends at the internal IP. Optional srcmac gives partial evidence only on networks whose
     gateway is the FortiGate, after a site check."
  4. "Başka bir DHCP sunucusu (ör. Windows): kira kaydı yalnızca MikroTik ARP-LOG ile, orta
     güvenle." / "Another DHCP server (e.g. Windows): leases only through MikroTik ARP-LOG, with
     medium confidence."
  5. "Birden çok internet hattı: her hat, arayüzleriyle birlikte, Ayarlar → Uygulama ayarları →
     5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde tanımlanır." /
     "Several internet lines: each line is defined, with its interfaces, in Settings →
     Application settings → 5651 lookup and lease timeline → Internet exits (NAT)."
  6. "Servis sağlayıcı ikinci bir NAT yapıyorsa sorgu aday listesi verir; genel IP başka
     müşterilerle paylaşılıyorsa (CGNAT) liste doğru oturumu içermeyebilir." / "When the ISP does
     a second NAT, lookups give a candidate list; when the public IP is shared with other
     customers (CGNAT), the list may not contain the right session."
  7. "Desteklenmeyenler: IPv6 sorguları; aynı ağa iki DHCP sunucusu; FortiGate'in DHCP
     aktarıcı (relay) olduğu ağlarda FortiGate kira kaydı; aynı adres aralığını kullanan birden
     çok şube; FortiGate'ten geçmeyen hatlarda sorgu; bir yönlendiricinin ya da L3 anahtarın
     arkasındaki ağlarda srcmac." / "Not supported: IPv6 lookups; two DHCP servers on one network;
     FortiGate leases where the FortiGate is a DHCP relay; several branches with the same
     address range; lookups for lines that do not pass the FortiGate; srcmac behind a router or
     an L3 switch."
- The list is the docs statement of 2.15 in short: the same cases in the same order, nothing that
  the docs do not say (checked by hand at WP-F2's merge).
- Link: "Ayrıntılar: Kurulum kılavuzu bölüm 0.1" / "Details: installation guide section 0.1".

### 2.14 Load generator (WP-F1)

All new options default to today's behaviour. The default output stays byte-identical, so the
seed digest test is unchanged.

- **`--dhcp-server mikrotik|fortigate|both`** (default `mikrotik`). With `fortigate`:
  - DHCP churn is rendered as FortiGate DHCP Ack (26001) lines on assignment and on renewal at half the
    lease (a simulation assumption, sahada doğrula: F7), with `logid="0100026001"`,
    `logdesc="DHCP Ack log"`, `interface="vlan<VLAN>"`, `dhcp_msg="Ack"`, `lease=<seconds>`,
    `hostname` and `msg="DHCP server sends a DHCPACK"`;
  - 26002 lines are written only when a guest leaves; every other lease expires unlogged. This
    is a simulation choice, not a measured behaviour;
  - no `core-r1`/`switch-1` lines and no ARP-LOG;
  - client traffic has `srcintf="vlan<VLAN>"` (the FortiGate is the gateway).

  `both` keeps MikroTik for VLAN 41–43 and FortiGate for 60/61.
- **`--srcmac`:** traffic lines carry `srcmac` (lower case). In `fortigate` mode it is the
  client's MAC; in `mikrotik` mode it is the core router's MAC for every client, which exercises
  the shared guard.
- **`--egress NAME:TRANSIP:DSTINTF:SHARE`** (repeatable; shares sum to 100): sessions are
  assigned to exits by `rng(seed, "egress", session)`; `waninfo` lists the interfaces. Without
  the option there is one `--transip` and `dstintf="wan1"` as today.
- **Truth:**
  - leases gain `"evidence": "mt_dhcp" | "fg_dhcp" | "none"` (statics in `fortigate` mode have
    `none`);
  - sessions gain `"egress"`;
  - `_expected_table` in `tests/test_loadgen.py` gains `fg_dhcp`.
- The README (TR/EN) documents the options and moves "FortiGate DHCP" out of "not covered yet".

### 2.15 Docs (Turkish and English)

| Document | Change | Owner |
|---|---|---|
| `README.md` "Desteklenen ağ" | a short summary of the supported setups (one site, one FortiGate; MikroTik or FortiGate DHCP; internet exits; IPv6 not supported) with the link to KURULUM §0.1; not the full statement | WP-F2 |
| `docs/KURULUM.md`, `docs/INSTALL.md` §0.1 | the bullets "Kişi ve cihaz eşleştirmesi" and "NAT" are **replaced** by the two bullets below; "Tek yerleşke" keeps its text except "adresleri bir ana MikroTik yönlendiricinin (DHCP sunucusu) dağıttığı" → "adresleri bir MikroTik yönlendiricinin ya da FortiGate'in DHCP sunucusunun dağıttığı"; "Kullanım analizi" and "Başka üreticilerin cihazları" stay as they are; the "Desteklenmeyenler" paragraph is new | WP-F2 |
| `docs/KURULUM.md`, `docs/INSTALL.md` §13 | new paragraph "İnternet çıkışlı sürüme yükseltme (şema revizyonları `0017_fortigate_dhcp`, `0018_nat_egresses`)" / "Upgrading to the version with internet exits (…)", text below | WP-F2 |
| `docs/KURULUM.md`, `docs/INSTALL.md` §16 | V1–V6, V8, V11, V14–V17 of 1.3 as checklist rows, with a column for the recorded outcome (V2: RELEASE logged or not, and its logid; V3: renewals logged or not, and, if not, the organisation's choice of DEVICE-SETUP 2.4 step 5; V6: relayed networks served on one interface; V16: the FortiGate syncs with NTP, checked with `diagnose sys ntp status`, which lists the NTP servers' information, F21) and the date of the check, repeated after each FortiOS upgrade | WP-F2 |
| `docs/DEVICE-SETUP.md` | §2 numbered 2.1–2.6: the existing content becomes 2.1–2.3 unchanged in substance, plus one time step in 2.1 (`config system ntp` → `set ntpsync enable`, with `set type fortiguard` or `set type custom` and `config ntpserver` → `edit 1` → `set server "<ntp>"`, F21; "the FortiGate's clock dates every session and DHCP record; an offset of minutes shifts exact lookups", `R20-lease:source-clock`), new 2.4 FortiGate DHCP records, 2.5 srcmac and device identification (optional), 2.6 several internet lines; the existing placeholder `<firewall>` is reused, plus `<arayüz>` (interface) and `<istemci-mac>` in the placeholder table; each new section Turkish then an English block (O11); "Doğrulayın" marks; texts and alerts cite "DEVICE-SETUP.md bölüm 2.4" / "section 2.4"; 2.4 defines the check by what ARCILOGGER shows, with the branches of D29 and a re-check after each FortiOS upgrade; 2.5 says where the device-identification fields appear (raw lines of every report and evidence package that cites a session of the interface) and to enable it only on interfaces that will be listed; 2.6 asks for each line's `dstintf`, required with two or more lines, and for the internal NAT addresses as `internal` exits (outline below); no statement about the CPU cost of device identification (no source gives one) | WP-F2 |
| `docs/CORRELATION.md` | §5 rows for the new keys (day 0); TR summary; §1 evidence table (`fg_dhcp`, srcmac "never lease evidence", traffic does not extend FortiGate leases by default, a logged FortiGate lease ends at `lease_seconds` without the margin, a continuation only within one DHCP table, the "Deviations" of the contract); §2 confidence and the new explanations; §3 "Çıkışlar / Exits" (2.8.3 table); §7 limits (one mapping limit removed; IPv6 kept; statics on FortiGate-only sites; two DHCP authorities detected, not supported; D33 for FortiGate only, keyed on the configured network, the MikroTik DECLINE gap kept, FortiGate chains outside every configured network `low`; a network added or edited applies to past leases only after a rebuild, and until then the lookup lowers the chains a missed D33 close leaves open (2.7, round 7); the D8 probe window; the rollback's lease step and its restore, 2.2.1); §8 verify on site (1.3) | day 0, then WP-F2 |
| `docs/PARSERS.md` | §2.3 routing rule, §2.4 `fg_dhcp`/`srcmac`/`device_time` mapping, §6 fixtures, §7 verify list, §8 corrected ("ingest uses the shipped parser defaults; settings never change a parsed row") | WP-A |
| `docs/DATA-MODEL.md` | `fg_dhcp`, `fg_traffic.srcmac`, `leases.lease_seconds`, the `source_table` values and check, `ix_leases_source_table_end`, the status rows `nat.egresses_migration`, `nat.egresses_changes` and `leases.rollback_lowered` (round 6); line 272's legacy NAT defaults replaced by `nat.egresses = {}` | WP-A (A0 the schema rows; A the NAT line, after C1) |
| `docs/ARAMA.md`, `docs/SEARCH.md` | §3 `srcmac:` row; `mac:`/`interface:`/`ip:`/`logid:`/`level:` mention `fg_dhcp`; §6 the `dhcp` type is "DHCP (MikroTik)" and the `dhcp` and `event` rows say FortiGate DHCP is under `type:event` (2.4) | WP-A (§3 and §6 rows in **A0**, the docs test pins them; the rest in A) |
| `docs/INGEST.md` | the Wave 3 gate measurements | WP-A |
| `docs/UYARILAR.md`, `docs/ALERTS.md` §2 | every `R20-lease:*` row and the four `R20-nat` causes (`conversion-failed`, `invalid`, `legacy-changed`, `unmapped-transip`), with severities, the cause's one-line action of 2.6.3 and its what-to-do; §5/§7 (what an e-mail carries) unchanged, since the describers carry no address (2.16) | WP-B |
| `docs/MVP-CONTRACTS.md` | §5 lookup (egress, `mac_source`, `LeaseInfo` fields), lines 174–188 (the legacy NAT keys) rewritten for `nat.egresses`, a new §18 for this wave's endpoints and codes | WP-D |
| `docs/RAPOR.md` | the section-4 exit row and its labels, the method paragraph per (resolution, mode), the conditional double-NAT text (not for `transip`), the partial srcmac row and its alternative header, the exit/dstintf row of every match and its unconfigured-sender warning row, the clock-offset, changing-public-address and internal-NAT sentences, the "Oturum aranmadı" lines of `no_egress`/`not_logged` with the listed exits, the FortiGate lease row and notes (unverified / verified by whom; `low` past half a lease without a renewal, after unconverted FortiGate DHCP lines of the address or its MAC, or outside every configured network), the cited unconverted DHCP row ("zincir kurulmadı; güven düzeyi verilmez"), and the reading table below | WP-D |
| `docs/roles.md` | `GET /settings/fortigate/observed` row (`settings.manage`) | WP-D |
| `docs/ILK-ADIMLAR.md`, `docs/FIRST-STEPS.md` | §5 VLAN DHCP server field, and the sentence (round 6) "FortiGate DHCP kullanılıyorsa FortiGate'in DHCP verdiği her ağı, VLAN numarası olmasa da, ekleyin: tanımlı bir ağda olmayan adreslerin FortiGate zincirleri düşük güvenle gösterilir (`fortigate_dhcp_no_network`). Bir ağı sonradan ekler ya da değiştirirseniz kiraları o ağın ilk FortiGate DHCP kaydının gününden (`R20-lease:fortigate-dhcp-subnet` uyarısının ayrıntılarındaki ilk gün) bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün): bir ağ geçmiş kiralara ancak yeniden oluşturmadan sonra uygulanır; o zamana kadar etkilenen zincirler düşük güvenle gösterilir." / "With FortiGate DHCP, add every network the FortiGate serves by DHCP, even without a VLAN number: FortiGate chains of addresses outside the entered networks are shown with low confidence (`fortigate_dhcp_no_network`). If you add or change a network later, rebuild the leases from the day of that network's first FortiGate DHCP record (the first day in the details of the `R20-lease:fortigate-dhcp-subnet` alert) to today in Settings → Application settings → 5651 lookup and lease timeline → Rebuild leases (at most 31 days per request): a network applies to past leases only after a rebuild; until then the chains concerned are shown with low confidence." (round 7); §6: the example `settings get/set nat.wan_transip` replaced by `& $db settings get nat.egresses` and a piped JSON example `'{"hat-1": {"mode": "direct", "public": ["<genel IP>"]}}' \| & $db settings set nat.egresses - --by <kullanıcı>`, pointing to the exit editor (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)) as the normal way; the settings table (internet exits, srcmac interfaces, the new `dhcp.fortigate_*` keys); the sentence "Değiştirilen bir değeri yükseltme ya da başlangıç verisi geri almaz" gains "Bir yükseltme eski bir ayarı yenisine çevirebilir; bu da denetim kaydına yazılır (KURULUM.md bölüm 13)." / "An upgrade may convert an old setting into a new one; that is written to the audit log too (INSTALL.md section 13)."; §9: `R20-lease` and `R20-nat`, one line each (the docs test requires every rule constant there) | WP-B (§9 lines, in its merge), then WP-F2 |
| `docs/AYARLAR.md`, `docs/SETTINGS.md` | the new keys, the exit editor, the observed panel | WP-F2 |
| `docs/KULLANIM.md`, `docs/USAGE.md`, `docs/LISANS.md`, `docs/LICENSING.md` | "MikroTik DHCP sunucusunun kiraları" → "DHCP sunucusunun (MikroTik ya da FortiGate) kiraları"; srcmac is not used for usage | WP-F2 |
| `docs/ARCHITECTURE.md`, `docs/DENETCI-KILAVUZU.md`, `docs/KANIT-PAKETI.md` | lease sources; DENETCI-KILAVUZU gets the reading table below (TR, EN in the same file's English part); KANIT-PAKETI notes that raw lines of cited sessions may carry device-identification fields; any "MikroTik DHCP" claim that is now incomplete | WP-F2 |
| `docs/legal/aydinlatma-metni-taslak.md` | row "Ağ erişim kayıtları": source "MikroTik yönlendirici ya da FortiGate güvenlik duvarı (DHCP sunucusu)", data also "FortiGate DHCP kayıtlarındaki ana bilgisayar adı"; row "İnternet trafiği kayıtları": "aygıt tanıma açık arayüzlerde ayrıca kaynak MAC adresi, cihaz adı, işletim sistemi, donanım üreticisi ve sürümü, cihaz türü ve FortiGate'in pasif olarak öğrendiği kullanıcı adı"; a sentence that srcmac may be shown in 5651 results as partial evidence; in the "5651 talepleri" row and the traffic row: on interfaces with device identification these fields appear in the raw-line evidence of every 5651 report and evidence package that cites a session of such an interface, other people's candidate sessions included | WP-F2 |
| `docs/legal/saklama-imha-politikasi-taslak.md` | the parsed-records row names FortiGate DHCP records and the device-identification fields of session records (same retention, raw lines unchanged), and that they travel with the raw lines into 5651 reports and evidence packages | WP-F2 |
| `tools/loadgen/README.md`, `tests/fixtures/synthetic/README.md`, `tests/fixtures/format/README.md` | 2.14, 2.3 | WP-F1, WP-A |
| `docs/adr/0016-fortigate-dhcp-and-internet-exits.md`, `docs/adr/README.md` | D1–D33 and the SPEC 9.1 deviation of D10; sources cited as "FortiOS 7.6 log reference / CLI reference" and community articles by title and number, never a third version number (`MODEL_OR_RELEASE`) | integrator (day 0) |
| `docs/ASSUMPTIONS.md` | G8 rewritten; new rows for FortiGate DHCP (logids, `dhcp_msg`, renewals and traffic, lease unit, device time), srcmac (and the SPEC 9.1 deviation), exits (`public_dynamic`, observed addresses, the mode `internal`, unclaimed public NAT addresses), `trandisp`; O1–O22; the "verify on site" list gains 1.3 | integrator |
| `CHANGELOG.md` | Wave 3 entry including the settings migration and the rollback note | integrator |

Done-when for WP-F2: `grep -rn "MikroTik DHCP\|nat.wan_transip\|nat.public_ips\|lookup.port_hint"
docs README.md` leaves only historical CHANGELOG lines, the §13 upgrade paragraph and statements
that are still correct in context.

**Supported topologies: the two replacement bullets and the new paragraph (KURULUM §0.1; INSTALL
§0.1 is the English text).** The §0.1 introduction stays ("Kurmadan önce ağınızın … belirler").

TR:
> - **Kişi ve cihaz eşleştirmesi (kira kanıtı):** iç IP adresinden MAC adresine, cihaza ve
>   kişiye giden adım DHCP sunucusunun kira kayıtlarına dayanır:
>   - **DHCP sunucusu MikroTik:** ana MikroTik yönlendiricinin kira kayıtları (`assigned` /
>     `deassigned`) kullanılır; ana yönlendiricideki saatlik `ARP-LOG` betiği (isteğe bağlı,
>     sahada yazılır; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 3) statik IP'li cihazları ve kira
>     kayıtları arasındaki boşlukları **orta güvenle** ekler.
>   - **DHCP sunucusu FortiGate (yalnız-FortiGate ağlar):** FortiGate DHCP sunucusunun olay
>     kayıtları (DHCP Ack / Release) kira zaman çizelgesine girer. Kayıt biçimi FortiOS
>     belgelerinden ve tek bir topluluk örneğinden alınmıştır; ilk kurulumda sahada doğrulanır
>     ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) ve doğrulanana kadar raporlar bunu
>     belirtir. Yalnızca yapılandırmada FortiGate olarak tanımlı kaynakların kayıtları
>     kullanılır. FortiGate'in DHCP verdiği her ağ, VLAN numarası olmasa da, Cihazlar ve Kişiler
>     sayfasında tanımlanır: tanımlı bir ağda olmayan adreslerin zincirleri düşük güvenle
>     gösterilir. Sonradan eklenen ya da değiştirilen bir ağ geçmiş kiralara ancak kiralar
>     yeniden oluşturulunca uygulanır ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5). Statik IP'li cihazların kira kaydı yoktur: zincir iç IP adresinde biter; ağ
>     geçidi FortiGate olan arayüzlerde, iki istemciyle sahada denetlendikten sonra, isteğe
>     bağlı olarak FortiGate oturum kaydının srcmac alanı MAC adresini **kısmi kanıt** olarak,
>     düşük güvenle verir ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.5).
>   - **İkisi birlikte:** iki DHCP sunucusu farklı ağlara hizmet ediyorsa ikisinin kayıtları da
>     kullanılır.
>   - **Başka bir DHCP sunucusu (ör. Windows):** bu sürüm onun kayıtlarını okumaz; kira kaydı
>     yalnızca MikroTik `ARP-LOG` ile, orta güvenle oluşur. `ARP-LOG` yoksa 5651 sorgusu
>     FortiGate oturumunda ve iç IP adresinde biter (MAC, cihaz ve kişi yok), kullanım analizi de
>     IP adresine göre sayar.
> - **İnternet çıkışları (NAT):** FortiGate'in kaynak NAT'ı desteklenir. Her internet hattı
>   Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)
>   bölümünde tanımlanır: genel IP (ve değişip değişemeyeceği), FortiGate'in o hattaki NAT
>   adresi, arayüzleri, NAT türü ve port koruma. Genel adres
>   FortiGate'in kendisindeyse (doğrudan) genel IP + port oturumu **kesin** belirler. Servis
>   sağlayıcının yönlendiricisi ikinci bir NAT yapıyorsa o hattın sorguları **aday** oturumları
>   sıralar ([CORRELATION.md](CORRELATION.md) bölüm 3); genel IP başka müşterilerle
>   paylaşılıyorsa (CGNAT) aday listesi doğru oturumu içermeyebilir, kesin yanıt için servis
>   sağlayıcının NAT kaydı gerekir. IPv6 adresleriyle 5651 sorgusu desteklenmez: kira kayıtları
>   (DHCP, `ARP-LOG`) yalnızca IPv4 adreslerini kapsar.
>
> **Desteklenmeyenler:** IPv6 sorguları; aynı ağa iki DHCP sunucusu (ARCILOGGER fark ederse
> uyarır ve o anlardaki zincirleri düşük güvenle verir); FortiGate'in DHCP aktarıcı (relay)
> olarak çalıştığı ağlarda FortiGate kira kaydı (kira kanıtı asıl DHCP sunucusundan gelmelidir);
> aynı özel adres aralığını kullanan birden çok şube; FortiGate'ten geçmeyen hatlarda sorgu (hat
> yalnızca "FortiGate'ten geçmiyor" olarak işaretlenir, sorgu kimseyi listelemez); bir
> yönlendiricinin ya da L3 anahtarın arkasındaki ağlarda srcmac (orada o cihazın MAC adresidir;
> arada yalnızca katman 2 anahtarlar varsa arayüz ancak iki istemciyle sahada denetlenince
> kullanılır).

EN:
> - **Person and device matching (lease evidence):** the step from the internal IP address to
>   the MAC address, the device and the person rests on the DHCP server's lease records:
>   - **MikroTik as DHCP server:** the main MikroTik router's lease records (`assigned` /
>     `deassigned`) are used; the hourly `ARP-LOG` script on the main router (optional, written
>     on site; [DEVICE-SETUP.md](DEVICE-SETUP.md) section 3) adds static-IP devices and the gaps
>     between lease records with **medium confidence**.
>   - **FortiGate as DHCP server (FortiGate-only networks):** the FortiGate DHCP server's event
>     records (DHCP Ack / Release) feed the lease timeline. Their format comes from the FortiOS
>     documentation and one community sample; it is verified on site at the first installation
>     ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4), and until then reports say so. Only
>     records of sources configured as a FortiGate are used. Every network the FortiGate serves
>     by DHCP is entered on the Cihazlar ve Kişiler page, even without a VLAN number: chains of
>     addresses outside the entered networks are shown with low confidence. A network added or
>     changed later applies to past leases only once the leases are rebuilt
>     ([FIRST-STEPS.md](FIRST-STEPS.md) section 5). Static-IP devices
>     have no lease: the
>     chain ends at the internal IP; on interfaces whose gateway is the FortiGate, after a site
>     check with two clients, the srcmac field of the FortiGate session record can optionally
>     give the MAC address as **partial evidence**, with low confidence
>     ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5).
>   - **Both together:** when the two DHCP servers serve different networks, both are used.
>   - **Another DHCP server (e.g. Windows):** this version does not read its records; leases come
>     only from MikroTik `ARP-LOG`, with medium confidence. Without `ARP-LOG` a 5651 lookup ends
>     at the FortiGate session and the internal IP address (no MAC, device or person), and usage
>     analysis counts by IP address.
> - **Internet exits (NAT):** the FortiGate's source NAT is supported. Each internet line is
>   defined in Settings → Application settings → 5651 lookup and lease timeline → Internet exits
>   (NAT): the public IP (and whether it may change), the FortiGate's NAT address on that line,
>   its interfaces, the NAT type and port preservation.
>   When the public address is on the FortiGate itself (direct), public IP + port identify the
>   session **exactly**. When the ISP router does a second NAT, lookups for that line rank
>   **candidate** sessions ([CORRELATION.md](CORRELATION.md) section 3); when the public IP is
>   shared with other customers (CGNAT), the candidate list may not contain the right session,
>   and an exact answer needs the ISP's NAT record. 5651 lookups by IPv6 address are not
>   supported: lease records (DHCP, `ARP-LOG`) cover IPv4 addresses only.
>
> **Not supported:** IPv6 lookups; two DHCP servers on one network (ARCILOGGER warns when it
> notices and gives the chains of those moments with low confidence); FortiGate leases where the
> FortiGate is a DHCP relay (lease evidence must come from the actual DHCP server); several
> branches using the same private address range; lookups for lines that do not pass the
> FortiGate (they are only marked "does not pass the FortiGate", and a lookup lists nobody);
> srcmac behind a router or an L3 switch (there it is that device's MAC address; with only
> layer-2 switches in between an interface is used only after a site check with two clients).

**KURULUM §13 paragraph (INSTALL §13 in English, the same content).**

> **İnternet çıkışlı sürüme yükseltme (şema revizyonları `0017_fortigate_dhcp`,
> `0018_nat_egresses`).** Şema yükseltmesi FortiGate DHCP kayıtları için yeni bir tablo ve
> `fg_traffic` tablosuna bir sütun ekler (büyük tablolarda bile hızlıdır: sütun boş eklenir).
> Eski NAT ayarları tek bir yeni ayara, **İnternet çıkışları**na çevrilir: `nat.public_ips`
> adresleri "Genel IP (yükseltmede aktarıldı)" adlı doğrudan bir çıkış, `nat.wan_transip`
> "FortiGate WAN adresi (yükseltmede aktarıldı)" adlı bir servis sağlayıcı NAT'ı çıkışı olur
> (adres genel IP'lerden biriyse ayrı çıkış yapılmaz; `nat.public_ips` içinde özel ya da
> paylaşılan bir adres, ör. 100.64.x.x, varsa o da bu çıkışın NAT adresi olur), genel adresi
> olmayan hatlar için "Genel adresi tanımlanmamış hatlar" adlı bir çıkış eklenir,
> `lookup.port_hint` o çıkışın port koruma değerine geçer. Dönüşüm denetim kaydına
> `settings.change` olarak yazılır (`details.migration = "0018_nat_egresses"`; atılan geçersiz,
> IPv6 ya da ayrılmış değerler `details.dropped` içinde). Dönüşüm geçerli bir tanım veremezse
> çıkışlar boş kalır ve `R20-nat:conversion-failed` uyarısı açılır.
>
> **Yükseltmeden sonra:**
> - Her hattın genel adresini ve arayüzlerini (dstintf) Ayarlar → Uygulama ayarları → 5651
>   sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde girin. Aktarılan
>   "FortiGate WAN adresi" ya da "Genel adresi tanımlanmamış hatlar" çıkışı olan her kurulumda,
>   bu girilene kadar hiçbir çıkışa tanımlı olmayan bir adres için yapılan her sorgu ve rapor
>   `egress_public_unknown` uyarısını taşır; aktarılan çıkışlar arayüz girilene ya da
>   değiştirilene kadar eski NAT davranışını korur, ancak sonradan eklenen bir çıkışın NAT
>   adresleri bunun dışındadır. Hat hâlâ servis sağlayıcı yönlendiricisinin arkasındaysa ve
>   FortiGate'in o hattaki NAT adresini (ör. 100.64.x.x) yeni bir çıkış olarak eklerseniz,
>   hattın genel adresini doğrudan çıkıştan bu çıkışa taşıyın ve arayüzlerini girin; aksi hâlde
>   doğrudan çıkışın sorguları "Doğrudan hat doğrulanamadı" uyarısıyla yanıtlanabilir.
> - Aktarılan "FortiGate WAN adresi" çıkışının NAT adresi hattın genel adresiyse (FortiGate genel
>   adresi kendisi tutuyorsa) çıkışın türünü **Doğrudan** yapın; editör bunu uyarır.
> - Bütün hatlar tanımlandıktan sonra "Genel adresi tanımlanmamış hatlar" çıkışını silin: o
>   kaldıkça hiçbir hatta ait olmayan bir genel IP sorulduğunda ilgisiz oturumlar aday olarak
>   listelenir.
> - Bir değişiklik: hiçbir çıkışa tanımlı olmayan bir adres o anda FortiGate kayıtlarında kaynak
>   NAT adresi olarak görünüyorsa sorgu artık aday listesi yerine o adresle kesin eşleşme yapar
>   ve bunu uyarıyla belirtir (`egress_observed_not_configured`).
> - FortiGate DHCP kullanılıyorsa DEVICE-SETUP.md bölüm 2.4'teki doğrulamayı yapın ve sonucu
>   bölüm 16'ya yazın; her FortiOS yükseltmesinden sonra doğrulamayı yineleyin. Yükseltmeden
>   önceki anlar için 5651 sorguları iç IP adresinde durur: o zamana ait FortiGate DHCP kayıtları
>   kira zaman çizelgesine dönüştürülmez, sorgu bunu belirtir ve iç IP sorgusunda ya da kesin
>   eşleşmede kaydı kanıt olarak gösterir (`fortigate_dhcp_not_converted`). Daha eski bir kiraya
>   (MikroTik ya da FortiGate) dayanan bir zincirde, kiranın son DHCP kaydından sonra o adres ya
>   da o kiranın MAC adresi için böyle bir kayıt varsa (ör. yükseltmeden önce MikroTik'ten
>   FortiGate DHCP'ye geçen bir istemci başka bir adres aldıysa) zincir düşük güvenle ve
>   nedeniyle gösterilir (`fortigate_dhcp_not_converted_since_lease`). **Yükseltmeden sonra
>   da** her istemci için yeni sürümde ilk DHCP Ack kaydı gelene kadar zincir kurulmaz: bu,
>   kira süresinin yarısına kadar sürebilir (FortiOS varsayılanı 7 günlük kirada 3,5 gün;
>   istemci yenilemeyi kirasının yarısında yapar). Yükseltmeyi bu boşluğu bilerek planlayın;
>   FortiGate DHCP kullanmayan kurulumlar bundan etkilenmez.
> - Genel bir NAT adresi hiçbir çıkışa tanımlı değilse (ör. bir IP havuzu) artık çift NAT
>   belirtisi sayılmaz ve aday aramasına alınmaz: FortiGate'in kendi adresidir, sorulursa
>   kayıtlarda görülen adres olarak yanıtlanır. FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da
>   şubeye yaptığı NAT'ın adreslerini "İnternet çıkışı değil" türüyle girin.
>
> **Geri alma:** eski NAT satırları bu sürümde okunmadan saklanır; `-Rollback` şema
> geri dönüşü yapmaz, önceki sürüm bu satırları, yani **yükseltme öncesi** NAT değerlerini okur.
> Yükseltmeden sonra İnternet çıkışlarında bir değişiklik yaptıysanız, geri döndükten sonra
> önceki sürümün Ayarlar → Uygulama ayarları bölümündeki `nat.public_ips` ve `nat.wan_transip`
> değerlerini bu sürümde yaptığınız değişiklikle karşılaştırıp elle girin (değişiklikler denetim
> kaydında `settings.change` olarak durur); bunun için veritabanını geri yüklemeyin:
> `-RestoreDatabase` yükseltmeden sonraki bütün kayıtları (denetim, sorgu, rapor, uyarı, cihaz ve
> kişi atamaları) siler. FortiGate DHCP kullanan bir kurulumda önceki sürüm FortiGate DHCP
> kayıtlarını okuyamaz: bir FortiGate kirasını, adres bırakıldıktan ya da başka bir istemciye
> verildikten sonra da açık tutabilir. Bu yüzden `-Rollback`, program değiştirilmeden önce,
> bütün FortiGate kiralarını **düşük güvene** indirir; kaç kira olduğunu yazar ve kurulum
> günlüğüne kaydeder (yalnızca türetilmiş kira satırları değişir; ham kayıtlara ve mühürlere
> dokunulmaz; `-RestoreDatabase` ile bu adım yapılmaz). Önceki sürümde FortiGate DHCP'ye dayanan
> bütün zincirler bu nedenle düşük güvenle görünür. Önceki sürüm mevcut FortiGate kiralarını
> kendi ayarlardaki sürelerine göre (kayıttaki kira süresine bakmadan, kiranın son DHCP
> kaydından yaklaşık 26 saat sonra; bu an geçmişte de olabilir) kapatır, geri dönüş günü ya da
> önceki gün başlayanları kendi yeniden oluşturmasında siler ve yenisini oluşturmaz; MikroTik
> kiraları önceki sürümün kendi kurallarıyla, yükseltmeden önceki gibi sürer. Geri dönüşten
> sonraki anlar için FortiGate DHCP adreslerinin 5651 sorgularını yeniden yükseltmeden sonra
> yapın. Yeniden yükseltildiğinde aradaki günlerin FortiGate DHCP kayıtları kira zaman
> çizelgesine girmez ve sorgular bunu belirtir: zincir kurulamayan bir anda, o anı kapsayan en
> yeni DHCP kaydı böyle bir kayıtsa `fortigate_dhcp_not_converted` ile; bir DHCP kirasına
> dayanan zincirde, kiranın son DHCP kaydından sonra o adres ya da o kiranın MAC adresi için
> böyle bir kayıt varsa zincir düşük güvenle ve nedeniyle gösterilir
> (`fortigate_dhcp_not_converted_since_lease`).
>
> **Yeniden yükseltme:** veritabanı şeması geri alınmadığı için dönüşüm yeniden çalışmaz ve eski
> NAT ayarları yeniden çevrilmez. Geri dönüş sırasında önceki sürümde `nat.public_ips`,
> `nat.wan_transip` ya da `lookup.port_hint` değiştirildiyse `R20-nat:legacy-changed` uyarısı
> değişen ayarları adlarıyla gösterir: değişikliği İnternet çıkışları bölümüne elle işleyin;
> uyarı çıkışlar kaydedilince kapanır. Yeniden yükseltme, önceki sürümün değiştirmediği FortiGate
> kiralarının güven düzeyini geri getirir ve kaç kiranın geri geldiğini, kaçının düşük kaldığını
> yazar; düşük kalanlar önceki sürümün kapattığı ya da yeniden hesapladığı kiralardır. FortiGate
> DHCP kullanan bir kurulumda yeniden
> yükseltmeden sonra, geri dönüş gününden en uzun kira süresi kadar önceki günden (7 günlük
> kirada 7 gün önce) yeniden yükseltme gününe kadar kiraları Ayarlar → Uygulama ayarları → 5651
> sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir
> istekte en çok 31 gün): önceki sürümün erken kapattığı, sildiği ya da düşük güvende bıraktığı
> FortiGate kiraları ancak böyle FortiGate'in kendi bitişine ve güven düzeyine göre yeniden
> kurulur; yapılmazsa o aralıktaki anlar için sorgu nedensiz bir "kira yok" verebilir ya da
> zinciri düşük güvenle gösterir. Yeniden kurulan bu kiralar geri dönüş aralığında, kiranın son
> DHCP kaydından sonra önceki sürümün o adres ya da MAC adresi için yüklediği bir FortiGate DHCP
> kaydı varsa düşük güvenle ve nedeniyle gösterilir (`fortigate_dhcp_not_converted_since_lease`):
> adres o arada bırakılmış, başka bir cihaza verilmiş ya da MAC adresi başka bir adres almış
> olabilir.

**DEVICE-SETUP 2.4–2.6 outline** (placeholders `<firewall>`, `<arayüz>`, `<istemci-mac>`).
Every FortiGate command below is from the CLI reference (F8, F9, F12, F18, F20); everything else
is checked in ARCILOGGER.

- 2.4 "FortiGate DHCP sunucusu kayıtları (yalnız-FortiGate ağlar)":
  1. Doğrulayın: the interface is not a relay and a DHCP server serves it:
     `config system interface` → `edit <arayüz>` → `get` shows `dhcp-relay-service disable`;
     `config system dhcp server` → `show` has an entry with `set interface <arayüz>` and
     `set status enable` (F18).
  2. List the interfaces of `server-type ipsec` DHCP servers (DHCP over IPsec, VPN pools) in
     Ayarlar `dhcp.fortigate_interfaces_excluded` (F20, D28).
  3. `config log eventfilter`: `event` and `system` `enable` (the defaults).
  4. `config log syslogd filter`: `set severity information` (the default); no free-style filter
     that excludes event logs.
  5. Check, on one test client: `ipconfig /release`, then `ipconfig /renew` (or disconnect and
     reconnect the client), and note the time. The check **passes** when ARCILOGGER shows all
     two of: (a) Kaynaklar → Kira kanıtı shows a FortiGate lease (state `ok`, FortiGate DHCP
     current leases > 0, refused rows 0, excluded interfaces aside; the count follows the
     current settings at once, while rows refused for the receive time or an unusable MAC stay
     counted until they leave the 24-h window); (b) a trial 5651 Sorgu in
     internal mode, entered as ILK-ADIMLAR.md bölüm 8 describes (Makam "DENEME", Gerekçe
     "FortiGate DHCP sahada doğrulama"), with the client's IP and the noted time gives the
     client's MAC and device (with the note "biçim sahada doğrulanmadı"). Together they prove the
     format end to end. (Canlı Akış and Arama show a FortiGate DHCP record and a plain FortiGate
     event under the same kind, so they cannot tell the two apart and are not part of the
     check.)
     Branches:
     - no release line appears: releases are not logged (V2); leases then end by timeout, which
       is acceptable; record it in KURULUM §16 and go on with the ACK format;
     - a release line appears under another logid: add that logid with `deassigned` to Ayarlar
       `dhcp.fortigate_logids` and rebuild the leases of the day (Ayarlar → Uygulama ayarları →
       5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur); record it in §16;
     - the card shows no FortiGate lease, or refused rows: open the raw line in Arama
       (`logid:<logid>`), compare its logid with `dhcp.fortigate_logids` and its `dhcp_msg` with
       `dhcp.fortigate_dhcp_msgs`, look at `R20-lease:fortigate-unconverted` and
       `R20-lease:fortigate-refused`, correct the setting, rebuild from the day the alert names
       (`first_day`; Kiraları yeniden oluştur, at most 31 days per request), and repeat the check.
     Doğrulayın also that a renewal while the client stays connected (wait for half the lease
     time, or set a short `lease-time` on a test scope) gives a second ACK line (V3), and record
     the outcome in §16:
     - a second ACK appears: renewals are logged; nothing else to do;
     - no second ACK appears: renewals are not logged. Then every always-on client's chain ends
       at its lease end (`no_lease`) and is `low` from half the lease on. The organisation (with
       its legal counsel) chooses between keeping that, which never names a wrong person, and
       switching on `dhcp.fortigate_traffic_extends`, whose help text states the risk: traffic
       then keeps a lease open, so another device using the same address (a static device in
       the pool) is named as the former holder. Record the choice and its date in §16.
  6. When the check passes, switch on `dhcp.fortigate_format_verified` (reports then name who
     marked it and when). Repeat steps 5–6 after **every FortiOS upgrade** (the field list
     changes between releases, F2): switch the setting off, check, switch it on again. If a line
     has to be shared outside the organisation (e.g. for support), mask the MAC address, host
     name, IP address, `devid` and `devname` first; never send it unmasked.
  7. `lease-time` (default 604,800 s; `0` = unlimited, which gives a high alert) is used as each
     lease's time (`dhcp.fortigate_use_logged_lease`). Traffic does not extend FortiGate leases
     unless `dhcp.fortigate_traffic_extends` is switched on (D26).
  8. Keep static devices out of the DHCP range (or reserve their addresses on the FortiGate): a
     static device inside the pool makes clients decline the address (`R20-lease:fortigate-
     decline`, D33) and can put two devices on one address.
  9. Doğrulayın, if the FortiGate serves networks behind a router through DHCP relay (several
     DHCP servers on one interface, F22): the 26001 line of a relayed client names an
     `interface` and an address of its own network (V6); D33 then keeps one binding per network,
     not per interface. Record it in §16.
- 2.5 "srcmac ve aygıt tanıma (isteğe bağlı; ağ geçidi FortiGate olan ağlar)":
  - `config system interface` → `edit <arayüz>` → `set device-identification enable`, only on
    interfaces that will be listed in `lookup.srcmac_interfaces`: interfaces where the FortiGate
    is the VLAN's gateway, never a routed (L3) segment (there srcmac is the router's or L3
    switch's MAC for everyone). With layer-2 switches in between, Fortinet says "if there is a
    switch in between, the user may see the MAC address entry of the switch" and a Fortinet staff
    answer says device identification works properly "when the endpoints have layer-2
    connectivity up to the FortiGate" (F11): such an interface is listed only after the
    two-client check below;
  - **what it adds** (F19): every session record of that interface then carries the client's
    MAC address (`srcmac`, `mastersrcmac`, `srcmacvendor`) and, as the FortiGate detects them,
    the device name (`srcname`), operating system (`osname`, `srcfamily`, `srcswversion`),
    hardware vendor and version (`srchwvendor`, `srchwversion`), device type (`devtype`) and
    passively learned user names (`unauthuser`, `unauthusersource`). ARCILOGGER stores them
    unchanged with the session record for the whole retention period; they appear in raw lines
    and Search (`search.read`), for listed interfaces in 5651 results as partial evidence, and
    — whatever `lookup.srcmac_interfaces` says — in the **raw-line evidence of every 5651
    report and evidence package** that cites a session of that interface, the candidate sessions
    of other people in a double-NAT report included (D21);
  - turning it on is the organisation's decision, with its legal counsel, as for guest networks
    (section 5); the privacy notice draft has the matching rows; "Doğrulayın": after enabling it,
    check the unit's load in its own monitoring (no saved Fortinet page states its cost, so the
    guide states none);
  - Doğrulayın before listing an interface in Ayarlar `lookup.srcmac_interfaces`: with two known
    clients on it, the observed panel shows `max_ips_per_mac` 1 (IPv4 addresses; a dual-stack
    client's IPv6 addresses are not counted) and the two MACs in Arama (`srcmac:`) are the
    clients' own (not a switch's or router's);
  - routed segments must never be listed.
- 2.6 "Birden çok internet hattı":
  - read each line's `transip` and `dstintf` in Ayarlar → "Kayıtlarda görülen NAT adresleri", or
    in Arama with `natip:` and `interface:`;
  - enter each line's `dstintf` names as the exit's interfaces: **required with two or more
    lines** (without them a lookup cannot tell by which line a session left, and the editor
    warns); a FortiGate address behind an ISP router (RFC 1918 or 100.64.x.x) is the NAT address
    of an ISP NAT exit, never a public address;
  - ask the ISP for each line whether its public IP is static; mark a line whose address may
    change as `public_dynamic` (ISP NAT) — a direct line with a changing address is found by the
    logs (D31);
  - enter the exits;
  - port preservation per line only by a measurement, otherwise `unknown`: from a client, open a
    connection to a server the organisation controls on that line (e.g. `curl` to its own web
    server) and compare the source port that server logs with the session's `transport` in
    Arama (`natip:<transip>`); equal on several tries → `preserved` (sahada doğrulandı);
  - mark guest lines that bypass the FortiGate as `not_logged`;
  - enter the FortiGate's source-NAT addresses towards a DMZ, another VLAN, a VPN or a branch
    (policies with NAT switched on; they appear in the observed table with the `dstintf` of that
    DMZ, VLAN or tunnel) as exits of the type "İnternet çıkışı değil" (`internal`): until then they count as
    an unconfigured line (`R20-nat:unmapped-transip`, `egress_unmapped_seen`) and as unclaimed
    candidates.

**DENETCI-KILAVUZU / RAPOR reading table** (the same table in both, TR; the English parts carry
the EN column). Auditors read the PDFs, so this table is what guarantees they can tell what is
logged, what is configured and what is assumed. Its first column uses the PDF's own labels (the
one label table of 2.12 for the exit row) and names in backticks the explanation keys behind
each row; a docs test (3.9) checks that every key the PDF prints in its method section or beside
a chain (`reports/texts.py` `READING_TABLE_KEYS`) and every exit label has a row in both
documents:

| PDF'te görülen / What the PDF shows | Kesin olan (kayıtta) / Exact (in the records) | Ayarlara dayanan / Configured | Varsayılan / Assumed |
|---|---|---|---|
| Çıkış "Genel IP", doğrudan, kesin eşleşme / exit "Public IP", direct, exact | FortiGate oturum kaydı: transip ve transport / the FortiGate session record: transip and transport | genel IP'nin bu hatta ait olduğu / that the public IP belongs to this line | — |
| Çıkış "Genel IP" ya da "NAT adresi", servis sağlayıcı NAT'ı / ISP NAT | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın ikinci NAT yaptığı, genel adresi / that the line does a second NAT, its public address | genel port → FortiGate portu eşlemesi yoktur; liste adaydır / no public port → FortiGate port mapping; the list is candidates |
| "Genel adresi değişebilir" uyarısı / "public address may change" (`egress_public_dynamic`) | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın genel adresi ve değişebilir işaretlendiği / the line's public address and that it is marked as changing | adresin o anda bu hatta ait olduğu; yalnızca servis sağlayıcı doğrulayabilir / that the address was this line's at that moment; only the ISP can confirm it |
| "Doğrudan hat doğrulanamadı" (fallback) | o anın çevresinde istenen adresin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü ve o hatta özel ya da paylaşılan başka bir NAT adresinin de göründüğü / that around the moment the requested address did not appear in the records, or only part of the time, and another, private or shared NAT address did on that line | hattın tanımı / the line's definition | o anda ikinci NAT yapılmış olabileceği; liste başka bir hattın kullanıcılarını içerebilir (paragraf söyler) / that a second NAT may have happened; the list may hold another line's users (the paragraph says so) |
| "NAT adresi doğrulanamadı" (fallback of an ISP NAT address) | o anın çevresinde istenen NAT adresinin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü / that around the moment the requested NAT address did not appear in the records, or only part of the time | hattın tanımı ve NAT adresi / the line's definition and NAT address | listenin bu hattın o anki oturumlarını içerdiği; başka adreslerle çıkan oturumlar da aday olabilir / that the list holds this line's sessions of that moment; sessions that left with other addresses may be candidates too |
| "Kayıtlarda görülen adres" (observed) | FortiGate'in o adresi kaynak NAT adresi olarak kullandığı / that the FortiGate used the address as source NAT | — | adresin o tarihte kuruma ait olduğu (servis sağlayıcıdan doğrulanır) / that the address belonged to the organisation then (confirmed with the ISP) |
| "Genel adresi bilinmeyen hatlar", "Çıkış tanımlı değil" | özel ya da paylaşılan NAT adresiyle çıkan FortiGate oturumları / the FortiGate sessions that left with a private or shared NAT address | — | ikinci NAT; liste ilgisiz kullanıcılar içerebilir / a second NAT; the list may hold unrelated users |
| "Kısmi kanıt: srcmac" (`srcmac_only`) | FortiGate'in aygıt tanımasının kaynak IP'yi bu MAC ile ilişkilendirdiği / that the FortiGate's device identification associated the source IP with this MAC | arayüzün listelendiği / that the interface is listed | MAC'in oturumu gönderdiği kanıtlanmaz; güven düşük / not proof that the MAC sent the session; low confidence |
| Düşük güvenli srcmac çakışması / low srcmac conflict (`srcmac_mismatch`) | iki farklı MAC kaydı / two different MAC records | — | o anda iki olası cihaz vardır; ikisi de gösterilir / there are two possible devices; both are shown |
| "Kayıt biçimi sahada doğrulanmadı" / "verified by … on …" (`fortigate_format_unverified`, `fortigate_format_verified`) | FortiGate DHCP kaydı / the FortiGate DHCP record | biçimin doğru okunduğu (doğrulandıysa kim, ne zaman; sorgu anındaki durum) / that the format is read correctly (if verified, by whom and when; as at the time of the lookup) | doğrulanmadıysa biçim FortiOS belgelerine dayanır / if not verified, the format rests on the FortiOS documents |
| "Kira zaman çizelgesine girmedi; zincir kurulmadı" (`fortigate_dhcp_not_converted`) | ham FortiGate DHCP kaydı ve mührü (iç IP sorgusu ve kesin eşleşme) / the raw FortiGate DHCP line and its seal (internal lookup and exact match) | — | zincir kurulmadı ve güven düzeyi verilmez; kayıt yalnızca o anı kapsayan bir kaydın var olduğunu gösterir, bir eşleştirme değildir / no chain was built and no confidence is given; the line only shows that a record covering the moment exists, it is not an attribution |
| "Kira yenilemesi görülmedi" (güven düşük) / no renewal seen (low) (`lease_renewal_not_seen`) | FortiGate DHCP Ack kaydı ve zamanı (kiranın istenen andan önceki kendi son kaydı) / the FortiGate DHCP Ack and its time (the lease's own last record before the moment) | kira süresi (kayıttan ya da ayardan) / the lease time (logged or configured) | Ack sahibinin adresi istenen anda hâlâ kullandığı; kayıtlar bunu gösteremez, güven bu yüzden düşük / that the Ack's holder still used the address at the requested moment; the records cannot show it, hence low confidence |
| "Kiradan sonra uygulanmamış DHCP kaydı" (güven düşük) / DHCP record after the lease not applied to it (low) (`fortigate_dhcp_not_converted_since_lease`) | DHCP kira kaydı (kiranın istenen andan önceki kendi son kaydı) ve ondan sonra o adres ya da kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı bir FortiGate DHCP kaydının var olduğu (kayıt gösterilmez) / the DHCP lease record (the lease's own last record before the moment) and that a FortiGate DHCP record of that address or of the lease's MAC address after it was not applied to this lease by the lease timeline (the record is not shown) | kira süresi / the lease time | kira sahibinin adresi istenen anda hâlâ kullandığı; sonraki kayıt bir bırakma, başka bir cihaza verilme ya da MAC adresinin başka bir adres alması olabilir, güven bu yüzden düşük / that the holder still used the address at the moment; the later record may be a release, a hand-over to another device or the MAC taking another address, hence low confidence |
| "Tanımlı bir ağda değil" (güven düşük) / not in a configured network (low) (`fortigate_dhcp_no_network`) | FortiGate DHCP kira kaydı / the FortiGate DHCP lease record | kurumun tanımlı ağları (adres hiçbirinde değil) / the organisation's configured networks (the address is in none) | kira sahibinin adresi o anda hâlâ kullandığı; adresin reddedilip başka bir istemciye geçtiği yalnızca tanımlı ağlarda anlaşılır, güven bu yüzden düşük / that the holder still used the address then; a declined address is only detected in configured networks, hence low confidence |
| "Aynı ağda iki DHCP kaynağı" (güven düşük) / two DHCP sources on one network (low) (`dhcp_sources_overlap`) | o ağda o anın çevresinde iki DHCP kaynağının kayıtları / the records of two DHCP sources in that network around the moment | ağın (VLAN alt ağının) tanımı / the network's (VLAN subnet's) definition | zincirin dayandığı kaydı yazan sunucunun o anda adresi gerçekten verdiği; kayıtlar iki sunucudan hangisinin geçerli olduğunu gösteremez / that the server whose record the chain rests on really gave the address then; the records cannot show which of the two applied |
| "Gönderici sonradan yapılandırıldı" / sender configured later (`source_configured_later`) | kayıtların o göndericiden geldiği / that the records came from that sender | göndericinin adresinin sonradan bir FortiGate kaynağı olarak tanımlandığı / that the sender's address was configured as a FortiGate source later | kayıtların alındıkları sırada da kurumun FortiGate'inden geldiği / that the records came from the organisation's FortiGate when they arrived as well |
| "Kaynak saati kayık" (güven düşük) / source clock off (low) (`source_clock_offset`) | kayıtlardaki zaman ile alma zamanı arasındaki ölçülen fark / the offset measured between the record times and the receive times | sınır (120 saniye ya da sorgu toleransı) / the limit (120 seconds or the lookup tolerance) | doğru oturumun kaymış zaman aralığında olduğu; bulunan oturum başka biri olabilir / that the right session lies in the shifted window; the session found may be someone else's |
| "Oturum aranmadı" (Çıkış yok / no exit) | — | sorgu anındaki çıkış listesi (PDF'te yazılı) ve hatların türü / the exit list at the time of the lookup (printed in the PDF) and the lines' types | çıkış listesinin olay tarihinde de tam olduğu / that the exit list was complete on the date of the event too |
| "Oturum aranmadı" (FortiGate'ten geçmeyen hat / line not passing the FortiGate) | — | hattın FortiGate'ten geçmediği / that the line does not pass the FortiGate | hattın olay tarihinde de FortiGate'ten geçmediği / that it did not pass it on the date of the event either |
| "Oturum aranmadı" (internet çıkışı olmayan NAT adresi / not an internet exit) (`egress_internal`) | — | adresin FortiGate'in iç ağlara yaptığı NAT'ın adresi olarak tanımlandığı / that the address is defined as the FortiGate's NAT towards internal networks | adresin olay tarihinde de bir internet çıkışı olmadığı / that it was not an internet exit on the date of the event either |
| "Tanımsız gönderici" uyarılı eşleşme / match with the unconfigured-sender warning (`session_from_unknown_source`) | yapılandırılmamış bir göndericinin oturum kaydı / a session record of an unconfigured sender | — | kaydın kurumun FortiGate'inden geldiği doğrulanmadı; kanıt olarak kullanılmamalıdır / not confirmed that the record came from the organisation's FortiGate; not to be used as evidence |
| "Çıkış tanımı olaydan sonra değişti" / exit changed after the event (`egress_changed_after_event`) | — | bugünkü tanım (PDF'te yazılı); önceki tanımlar kurumun denetim kaydında / today's definition (printed in the PDF); earlier definitions in the organisation's audit log | o tarihteki tanımın aynı olduğu / that the definition was the same on that date |

### 2.16 Audit, KVKK and licence

- **No new audit action.** `settings.change` covers PATCH of every new key and the v0018
  conversion (written by the seed, target `setting:nat.egresses`, `details.migration`, `from`,
  `dropped`, `conversion_failed`). `lookup.run` details gain `egress`. `audit-labels.ts`
  `KNOWN_ACTIONS` is unchanged. The change record `nat.egresses_changes` (D32) is a status row
  written beside the `settings.change` entry, not an audit action; the audit log stays the
  record of every old value.
- **Personal data:**
  - FortiGate DHCP host names and MACs are in `fg_dhcp` and `leases`, read under the existing
    permissions (`lookup.run`, `search.read`, devices pages); they are new personal data for a
    FortiGate-only site, and the privacy-notice draft gains them (2.15);
  - **device identification** (DEVICE-SETUP 2.5) puts the client's MAC, device name, OS,
    hardware vendor and version, device type and passively learned user names into every
    session record of that interface (F19). ARCILOGGER keeps them unchanged in `fg_traffic`
    (`srcmac` as a column, the rest in `extra`) for the retention period; they are visible in
    raw lines and Search under the existing permissions. So exposure **does grow** on a site
    that follows the guide; the guide says exactly what is added, the decision is the
    organisation's and its legal counsel's, and the privacy-notice and retention drafts gain the
    rows (2.15). ARCILOGGER does not strip fields from evidence (raw logs are never modified);
  - lookups add the srcmac exposure only through `srcmac:` (`search.read`) and srcmac chains
    (`lookup.run`); `TrafficInfo.srcmac` is filled only for listed interfaces (2.9), so the
    typed fields of stored outcomes carry no other person's MAC while the feature is off. The
    **raw lines** that a PDF or an evidence package prints do: on an interface with device
    identification on, every cited session line holds `srcmac` and the F19 fields, other
    people's candidate sessions included (D21). DEVICE-SETUP 2.5 and both legal drafts say so
    (2.15), and a PDF test pins it (3.5);
  - `srcmac_ambiguous` names at most 3 MACs (2.11);
  - the observed endpoint and the lease-evidence card return counts and the firewall's own
    addresses only;
  - **alert details** hold counts, VLAN numbers, a cause code, a table code, a day, clock
    offsets in seconds and at most five logids, `dhcp_msg` values, interface names, VDOM names,
    source names (an unconfigured sender's name carries its address, as the existing "yeni log
    kaynağı" alert does) or the firewall's own NAT addresses (`R20-nat:unmapped-transip`); never a
    client MAC. The one IP of a pool address they may hold is `fortigate-decline`'s: at most five
    declined pool addresses, the addresses where a static device answers, without a MAC or host
    name, because the remedy (reserve the address or move the device) cannot be carried out
    without it; they are shown on the Security page (administrators) and never in an e-mail.
    Every log-supplied text value (a VDOM name included) comes from rows of trusted sources
    only, is cut to 32 characters and has MAC- or IPv4-looking parts replaced by "…" (a
    `dhcp_msg` is a free 4096-character field, F1);
  - **alert e-mails** carry only the rule name, its action, the cause's one-line action and the
    whitelisted counts and VLAN numbers of the `R20-lease`/`R20-nat` describer (2.6.3); source
    names go under `sources` (a list), not under `source`, so the e-mail's source line never
    shows them, and the describer calls no `_ip`, so `ALERT_MAIL_IP_CASES` stays as it is.
- **Licence:** no licence check in any new path (parser, lease job, lookup, alerts, endpoints);
  the docs guides need no new rows (no new worker job, no new API area). The usage module
  benefits from FortiGate leases automatically; srcmac never feeds usage.

### 2.17 Residual risks

- **FortiGate formats are unverified on the syslog wire** (V1–V5, V14, V16). Mitigations:
  defaults from the official reference, a configurable logid and `dhcp_msg` map, the loud checks
  of 2.6.3, the "not verified on site" state shown on the card and in every PDF of an `fg_dhcp`
  chain until the site check is done (D29), and the real-sample test that turns on with the
  first (masked) capture.
- **Long leases** (604,800 s by default): an interval stays open up to `last DHCP evidence +
  lease` (7 days after the last ACK) when no RELEASE arrives; traffic does not extend it by
  default (D26). That is the end the FortiGate itself gives the lease, on its own clock (D6,
  `R20-lease:source-clock`); the PDF prints the lease time and the last DHCP evidence, and past
  half the lease without a renewal the chain is `low` with `lease_renewal_not_seen`. A client
  that left without a RELEASE still holds the chain until then, at `low` in the second half.
- **Renewals not logged** (V3 negative): every always-on client's chain is `low` from half its
  lease and ends at the lease end (`no_lease`), unless the organisation switches on
  `dhcp.fortigate_traffic_extends` and accepts its wrong-person risk (DEVICE-SETUP 2.4 step 5).
  A false "nobody" there is visible (`fortigate-renewals-missing`, the PDF's reason); a wrong
  person would not be.
- **A multi-day holiday on a FortiGate-only site** opens `R20-lease:none:fg_dhcp` (high) once no client
  renews for half the longest lease + 24 h (4.5 days with 7-day leases); it resolves with the
  first ACK after the holiday. Telling an empty office from a stopped feed would need another
  rule; the contract keeps the simpler one.
- **srcmac is only as good as the interface setting.** The shared guard (default 1 IP), the
  other-lease refusal and the alert catch a routed segment or a switch declared as directly
  connected. An L2 segment with a single client behind a NAT device is not caught: it is
  documented as not supported, and DEVICE-SETUP 2.5 asks for a two-client check.
- **Exits are not time-scoped (O9).** An exit changed after the event is protected by the log
  check (for `direct` lines), the observed check (D31) and `egress_changed_after_event`; for an
  `isp_nat` line whose public address changed, nothing in the logs can confirm the old address,
  so the warning and the audit history are what the operator has.
- **Unknown senders' traffic in lookups.** D24 keeps unknown senders out of lease evidence,
  lease extension, every srcmac query, the log probe and the traffic-based alerts. The session
  search of a lookup still reads `fg_traffic` of every source, as in Wave 2; an unknown sender's
  forged session lines could appear as candidates, but each such match is marked on its own row
  (`NatMatch.untrusted_sources`, a warning row in the PDF, a badge in the web) and the outcome
  carries `session_from_unknown_source` (and the "yeni log kaynağı" alert names the sender).
- **DECLINE on MikroTik.** D33 is triggered by FortiGate ACKs only (they also end the MAC's
  MikroTik lease on another address of the VLAN, the migration case). A MikroTik client that declines
  an address keeps its interval until a RouterOS record or the timeout closes it (and traffic
  extends MikroTik intervals); whether RouterOS logs anything for a DECLINE is not known. O15
  records the option of applying D33 there once it is checked on site.
- **A client that declines and gets no new ACK** (gives up, self-assigns 169.254/16) keeps the
  declined address at `high` until half its lease, then at `low` until the lease ends; D33 needs
  the next ACK. The decline alert catches the repeated case, `srcmac_mismatch` the case with
  srcmac on.
- **A device clock that drifts by less than 120 s** is not alerted (`source-clock`) nor lowers a
  chain (`source_clock_offset`, unless the lookup tolerance is lower); exact lookups have a ±60-s
  tolerance by default, so a drift of one to two minutes can still move a session out of the
  window. The threshold is this contract's choice. The per-lookup measurement needs at least 10
  rows of the source around T; a source that logged less (a quiet DHCP-only router) is not
  measured there. On an empty result the searched sources' offset is stated
  (`source_clock_offset`), not corrected: the answer stays `none` or `no_lease` until the lookup
  is run again with the tolerance widened by the offset (O22).
- **A public-looking NAT address behind an ISP router** (an ISP that numbers the FortiGate's WAN
  side from address space outside RFC 1918 and RFC 6598) counts as the FortiGate's own address
  (D15, O21): such a line fails no D15 check and its sessions are not unclaimed candidates, so
  until the administrator defines it as an `isp_nat` exit with that `transips` (validation
  accepts a public `transip` there, the editor warns), lookups of its public IP find nobody.
  `egress_unmapped_seen` and `R20-nat:unmapped-transip` name the address meanwhile.
- **Renamed interfaces and a double NAT at once.** With interfaces declared, the D15 check
  counts only rows on E's interfaces; when the names are wrong and the line is double-NATed at
  T, the check passes and the exact search finds nobody. `egress_unmapped_seen` names the other
  address; the same wrong names make the candidate searches drop their condition (2.8.3).
- **HA pairs.** `devid` changes on an HA failover and is not compared (D27); two units that log
  the same DHCP event twice are continuations of one interval.
- **One MAC shared by several clients on one interface** (a repeater that rewrites MAC
  addresses, DHCP over IPsec that is not excluded): D33 keeps only the latest-ACKed address's
  chain, and that chain names the device of the shared MAC at its usual confidence until the
  administrator acts on `R20-lease:fortigate-dhcp-mac` (exclude the interface, mark the MAC as an
  infrastructure device). Lowering such chains per lookup would need its own query; the contract
  keeps the alert.
- **`<<= ANY(cidr[])` candidate scans** are time-bounded; the perf guard covers them.
- **The pre-existing gap** that ingest ignores `dhcp.server_vlan_regex` (`prepare.py:147`) is
  recorded, not fixed. The worker falls back to it only for rows with NULL VLAN; PARSERS §8 is
  corrected to say so.

---

## 3. Test plan

### 3.1 Ingest and parser (WP-A)

- `ingest/tests/test_fortigate_dhcp.py` (new):
  - each synthetic DHCP line gives the expected `fg_dhcp` row: columns, `extra` = {devid, vd,
    vendor …}, the MAC in lower case with colons from upper case, lower case and `-`,
    `hostname` absent or `""` → NULL (and `""` in `extra`), `lease` absent → NULL, `lease="abc"`
    → NULL plus `fortigate.value`;
  - `ip="n/a"` or `mac=""` with `dhcp_msg` gives an `fg_event` row plus `fortigate.dhcp` with no
    value in the detail;
  - 26003, 26007-like (mac without ip), DDNS-like (ip without mac) and DHCPv6 shapes stay
    `fg_event` with no failure;
  - an IPv6 `ip` with a MAC gives an `fg_dhcp` row;
  - `device_time` is true for lines with a usable `eventtime` or `date`/`time`/`tz`, false for
    fixture line 13 (two days off) and for a line without any time field;
  - fixture lines 11 (`Nak`) and 12 (zero MAC) parse to `fg_dhcp` (refusing them is the
    worker's job, 3.3);
  - exactly one typed row per line;
  - "nothing is lost": every field is in a column or in `extra`.
- srcmac:
  - traffic with srcmac in each MAC form gives the normalised column; `srcmac="n/a"` stays in
    `extra` with `fortigate.value`; `srcmac=""` gives NULL;
  - `mastersrcmac` and `srcmacvendor` are in `extra`;
  - every `fg_traffic` row has the same column set (template), with or without srcmac.
- Properties (`test_fortigate_properties.py`): any order and quoting of the DHCP keys gives the
  same row, and the MAC cache never changes a result (cache full or empty).
- Contract (`test_parser_contract.py:114-123, 185-215`): the FortiGate table set includes
  `fg_dhcp`; every table is emitted by some fixture line; rows of one table have the same
  columns.
- **Pin tests (they keep the defaults, the fixtures and the documented field list together;
  they cannot detect a wrong assumption, and their docstrings say so):**
  - `test_fortigate_dhcp_logid_defaults_match_the_fixtures`: the default
    `dhcp.fortigate_logids` equals `{"0100026001": "assigned", "0100026002": "deassigned"}`, the
    default `dhcp.fortigate_dhcp_msgs` equals `{"0100026001": ["Ack"]}`, and the synthetic
    ACK/RELEASE logids are keys of the map. The docstring says to change them only after a real
    capture;
  - `test_routing_shape_is_the_documented_field_list`: the routing keys equal
    `{"ip", "mac", "dhcp_msg"}` ⊆ the 26001/26002 field list of F1/F3, written as a literal in
    the test.
- **The assumption checks that can fail** are runtime ones: the R20-lease causes of 2.6.3 and
  the "not verified on site" state of D29 (3.3, 3.5). In CI the one assumption test is:
  - `test_real_fortigate_dhcp_sample`: skipped with "sahada doğrula: gerçek FortiGate DHCP
    satırı henüz yok" while `tests/fixtures/format/fortigate-dhcp-sample.log` is missing. Once
    the (masked, 2.3) file exists it asserts that each ACK/RELEASE line routes to `fg_dhcp` with
    `device_time` true, that its logid is a key of the default map, that its `dhcp_msg` is
    allowed by the default `dhcp.fortigate_dhcp_msgs` (and records the RELEASE value seen), and
    that `lease` is an integer in range.
- Kinds (`db/tests/test_kinds.py`): `TABLE_KINDS["fg_dhcp"] == "event"`, `CLIENT_IP`; every
  fixture line maps to a named kind; a typed row's kind equals its `messages` row's kind; the
  `dhcp` kind's labels are "DHCP (MikroTik)".
- Search (A0, 2.4): `test_search_fields.py` (`CONTRACT_FIELDS`/`CONTRACT_INDEXED`, `srcmac:`
  not indexed, `mac:` without `fg_traffic`, `logid:`/`level:` with `fg_dhcp`),
  `test_search_planner.py` (the `mac:` table tuples), `tests/test_search_docs.py` (ARAMA/SEARCH
  §3 and §6); `test_search_oracle.py` (matcher = SQL over the new lines, A rest).

### 3.2 Database and migrations (both storage modes)

- `db/tests/test_migration_0017.py` (WP-A0), on plain PostgreSQL and on the Linux TimescaleDB CI
  job (it also checks `ix_leases_source_table_end` up and down, and that v0017 writes no
  settings row):
  - up and down;
  - `fg_dhcp` is a hypertable with a compression policy, or a partitioned table with
    `fg_dhcp_default`, and `partitions.ensure` creates its monthly partitions;
  - the `db.timescale` status lists it;
  - **ADD COLUMN with a compressed `fg_traffic` chunk present:** insert old rows, compress the
    chunk, upgrade, read `srcmac` NULL from the compressed chunk, insert a new row with srcmac,
    query both;
  - the checks refuse `source_table='x'` and `lease_seconds=10`;
  - the downgrade deletes `fg_dhcp` leases and keeps every other row;
  - the model/migration sync test.
- `db/tests/test_migration_0018.py` (WP-C1): the case matrix (a)–(g) of 2.8.2, including
  (b2) 12 addresses, (b3) 20 addresses, (f) `public_ips=[A], wan_transip=A`, (g) a stored
  `"not an address"`, IPv6 and a reserved address (dropped with their reason), (h) a private
  `100.64.12.9` in `nat.public_ips` (moved into `wan`); 112 public addresses → 7 direct exits +
  `tanimsiz`, valid; **113** addresses → `nat.egresses = {}` and the status row's
  `conversion_failed: true` (the alert itself is WP-B's test, 3.3); `nat.egresses_changes`
  seeded per converted exit with the legacy author and time; **every converter output passes
  `validate_egresses`** and
  `parse_egresses`; the frozen converter and validator equal the application's on every case;
  seed-owned legacy rows deleted, administrator rows kept unchanged and unread; `updated_at` of
  `nat.egresses` = the latest legacy `updated_at`; an already-set `nat.egresses` left alone; the
  seed writes exactly one `settings.change` entry (target `setting:nat.egresses`) from the status
  row and none on a second run, with the audit chain intact (`verify` passes); the downgrade
  restores the kept rows when `nat.egresses` is unchanged, rebuilds them (lossy, logged) after an
  administrator change; idempotent.
- `db/tests/test_egress.py` (WP-C1): every rule and code of 2.8.1 (`public_dynamic`, the
  private/shared/reserved `public` rule with RFC 5737 addresses accepted, and
  `setting_egress_isp_nat_direct` and the mode `internal` with `setting_egress_internal`
  included), normalisation, limits, overlap with and without interfaces (an `internal` exit's
  `transips` against another exit's), catch-all, `public_unknown`, `parse_egresses` ordering, the
  one `NOT_PUBLIC` constant shared by the validator and the unclaimed predicate, and `legacy_egresses`
  against the table of 2.8.2; the cases live in `tests/fixtures/egress-cases.json`, which the web
  mirror's test reads too (3.6).
- `db/tests/test_settings_admin.py` (WP-C1): `change_setting` of `nat.egresses` updates
  `nat.egresses_changes` (fingerprint, time, author, deleted exits; no earlier contents) in the
  same transaction as its audit entry; a label-, interface- or port-preservation-only edit
  leaves it unchanged, an edit of `mode`, `public`, `transips` or `public_dynamic` changes it
  (D32). The seed writes the one v0018 audit entry and converts nothing else (D18).
- Settings (day 0): every new key and code in `test_settings_admin.py`/`test_cli.py`;
  `test_settings_meta.py` (every key has TR/EN text; automatic).

### 3.3 Lease timeline (WP-B)

- `test_timeline.py` additions (pure):
  - an FG ACK opens `high`, `fg_dhcp`, `server=interface`;
  - a renewal ACK is a continuation and updates `lease_seconds`;
  - a RELEASE closes with `dhcp`;
  - a logged lease of 604800 times out at `last + 604800 s` (the FortiGate's own end, D6); a
    fallback lease (`None`) at the server/default time × `timeout_percent`, as today;
  - **traffic never extends an FG interval by default**: ACK(A) at t0 with lease 86400, no
    renewal, traffic from the IP every 5 minutes for two days → the interval closes `timeout` at
    `t0 + 86400 s`; with `fortigate_traffic_extends` the same feed keeps it open (the RouterOS
    rule); traffic still extends MikroTik intervals and never an ARP interval;
  - **cross-table ACK (D5, a migration)**: (172.20.42.57, `00:00:5e:00:53:57`) open as `mt_dhcp`;
    on 2026-10-01 09:20 an `fg_dhcp` ACK of the same pair (`vlan42`, lease 86400) closes the
    `mt_dhcp` interval `superseded` (`superseded_across_sources` = 1) and opens an `fg_dhcp`
    interval with `lease_seconds = 86400`; traffic from .57 every 5 minutes for three more days
    with no further ACK (a printer set statically to .57) does not keep it open: it closes
    `timeout` at 09:20 + 86400 s. The reverse (an `mt_dhcp` `assigned` of a pair whose open
    interval is `fg_dhcp`) opens an `mt_dhcp` interval with no `lease_seconds`;
  - **D33**: ACK(.57, :31) at 08:41:10 then ACK(.58, :31) at 08:41:14 on the same interface,
    origin and VLAN → (.57, :31) closes `superseded` at 08:41:14, `fg_mac_moved` = 1; the same
    MAC's ACK on another interface, of another `vd`, or for an address of another VLAN on the same
    interface (two relayed networks, F22) closes nothing, nor does an ACK for an address in no
    configured network (`network` None); the same DECLINE shape on 172.20.42.0/24 stored with
    `vlan_id` NULL (a flat LAN entered without a VLAN number) closes (.57, :31) like any other
    (round 6); a MikroTik `assigned` of the same MAC on another IP closes nothing (MikroTik
    unchanged);
  - **D33, MikroTik variant (a migration)**: (172.20.42.57, `00:00:5e:00:53:57`) open as
    `mt_dhcp`, an `fg_dhcp` ACK of :57 for 172.20.42.120 (same network) at 09:20 → the `mt_dhcp`
    interval closes `superseded` at 09:20 (`superseded_across_sources` = 1), and traffic from .57
    afterwards (a printer set statically to .57) reopens nothing, also when the MikroTik row's
    VLAN number came from `dhcp.server_vlan_regex` and differs from the subnet's (both sides are
    compared by subnet); an `fg_dhcp` ACK of :57 for an address of another network closes
    nothing;
  - **D33 after the deadline**: ACK(172.20.61.20, :21) at 09:00 with lease 3600 on `vlan61`, no
    further event for .20, ACK(172.20.61.35, :21) at 14:00 → (.20, :21) ends `timeout` at 10:00,
    not `superseded` at 14:00, whether the day is fed at once (`rebuild_day`) or minute by minute
    (`finish` every minute), and a MAC-only lookup for :21 at 12:00 gives no lease of .20;
  - ARP within the grace after an FG release is ignored;
  - MikroTik and FortiGate rows with equal time and equal id order deterministically
    (`TABLE_RANK`);
  - an `fg_dhcp` ACK that supersedes an open `mt_dhcp` interval of another MAC (and one with
    another `vd`) counts `superseded_across_sources`; the same server's reassignment does not;
  - `fg_renewal_ack` counts a continuation ACK at ≥ 40 % of the lease, not one a minute later.
- A **property** test (hypothesis) over random interleavings of both tables, **with the lease
  varying per ACK**, cross-table ACKs of one pair, and a MAC moving to another IP after its
  first interval's deadline: feeding in random chunks (with `expire` between them) equals the
  one-shot build; `bounded()` of a seed keeps its `fg_dhcp` refs; and `refs.last_dhcp_evidence`
  of every closed interval, without `at` and for any `at` inside it, is a time of its own DHCP
  events at or before `at` (never the event that superseded it).
- `test_leases_fortigate.py` (new, DB):
  - a FortiGate-only rebuild writes leases and **devices** (host names from ACKs);
    `first_evidence_time` comes from `fg_dhcp` only; the catch-up starts there;
  - a seed across midnight keeps its `fg_dhcp` evidence and continuations; an `fg_dhcp` seed
    whose MAC gets an ACK for another IP before midnight is closed by it (D33), equal to the
    live and range builds;
  - an unmapped logid is counted and not used, and becomes used after the map is changed and the
    day rebuilt;
  - IPv6 is ignored and counted; `lease=0` and `lease=10**7` fall back and are counted
    (`lease_unlimited` for 0); `use_logged_lease=false` uses the settings;
  - the VLAN comes from the subnet even when the interface matches `^dhcp(\d+)$`;
  - **refusals** (each counted, none opens or closes a lease, none upserts a device): an ACK in
    an `unknown-<ip>` spool (source row `unknown = true`) and one from a configured MikroTik
    source; `dhcp_msg="Nak"` under 26001 (fixture line 11) and a NULL `dhcp_msg`; `dhcp_msg="ack"`
    in lower case **is** used (case-insensitive); a zero MAC (line 12) and a multicast MAC;
    `device_time = false` (line 13); traffic of an `unknown-<ip>` source does not extend a
    MikroTik interval; an excluded interface's row and an IPv6 row are counted as configuration
    (`excluded`), not as refused;
  - **a sender configured later** (D24): ACKs in an `unknown-172.20.10.1` spool are refused;
    after a `fortigate` source with address 172.20.10.1 is configured and the day rebuilt, they
    open leases, and a chain citing them carries `source_configured_later`; a disabled
    (removed) source's address does not vouch;
  - an unparsable `nat.egresses` (stored without validation) does not stop `rebuild_day` or
    `run_incremental` (2.8.1).
- `test_leases_consistency.py`: for a FortiGate-only site and a mixed site (MikroTik for VLAN 41,
  FortiGate for 60), live incremental, range rebuilds and single-day rebuilds in any order give
  the same rows (the existing harness); the FortiGate ACKs change their `lease` between ACKs
  (86400, then 43200), so a seed's lease at midnight is exercised; the feed includes the D33
  "move after the deadline" shape of `test_timeline.py`, a cross-table ACK of one pair, the
  MikroTik variant of D33, and **a FortiGate lease whose end falls after midnight while traffic
  from its IP continues past that end** (ACK at 08:00 with lease 86400, no renewal, a printer's
  traffic every 5 minutes for three days): every build closes it `timeout` at 08:00 the next day,
  never at the last traffic before a midnight plus the lease (the `_seeds` `last` of 2.6.2).
- `test_late_evidence.py`: late `fg_dhcp` lines trigger the rebuild of their day.
- Watermark: a FortiGate source with `fg_dhcp` rows feeds; the data window sees an
  `fg_dhcp`-only day.
- Alerts (`R20-lease`, `R20-nat`; DB-level, through `alerts.system` after real lease runs):
  - each cause of 2.6.3 opens with its severity and the details 2.16 allows (no client MAC, no
    client IP); pre-upgrade `fg_event` DHCP rows with the full shape do not trigger
    `fortigate-unconverted`;
  - `none:fg_dhcp` opens **high** on a FortiGate-only site whose usable ACKs stopped, stays
    closed on a FortiGate-only site whose last ACK was 3 days ago under 604,800-s leases, and
    resolves as soon as a usable ACK returns; **on a mixed site whose FortiGate DHCP rows (VLAN 60)
    stop while MikroTik DHCP rows (VLAN 41) keep arriving**, `none:fg_dhcp` opens high within the
    `fg_dhcp` window and the card shows `none` naming FortiGate DHCP; **on a MikroTik site whose
    ARP-LOG script stops while DHCP continues**, `none:mt_arp` opens high after 24 h; **a MikroTik
    site without ARP-LOG whose dhcp topic stops while servers keep sending traffic** opens
    `none:mt_dhcp` at **medium** (a quiet weekend looks the same; open MikroTik intervals do not
    count), and the same site with a working ARP-LOG stays without `none:mt_dhcp`; a site with no
    usable evidence of any table in 30 days gets `absent` (**info**), never `none`;
  - `fortigate-refused` (and the card state `refused`) opens **high** when every ACK carries
    `dhcp_msg="DHCPACK"`, when every row is receive-time (`device_time=false`), and when every row
    comes from an `unknown-<ip>` sender, while `search_counts` shows the rows arriving; it opens
    **medium** for one refused row among usable ones, with the reason counts and at most five
    values (a `dhcp_msg` of 40 characters is cut to 32, a value that contains a MAC or an IPv4
    address is masked, a value from an untrusted row never appears); it **stays closed** on a
    site where an IPsec interface listed in `dhcp.fortigate_interfaces_excluded` gives 90 % of
    the rows (the card shows them as `excluded_rows`, `refused_rows = 0`), and with
    `dhcp.fortigate_logids = {}`; `fortigate-dhcp-subnet` and `fortigate-lease` ignore excluded
    rows, and `fortigate-dhcp-subnet`'s details carry `first_day`, the oldest day within 100 days
    with a usable row in no configured network, which its TR and EN texts print (round 7); **after the setting is corrected** (the `dhcp_msg` list gains the value, or the sender
    is configured) the next check resolves it and the card leaves `refused` at once, while
    receive-time rows keep it open until they are 24 h old; its details carry `first_day`, the
    oldest day with a refused row; when every row is refused **and** the feed's usable rows
    stopped, both `fortigate-refused` and `none:fg_dhcp` are active and the card shows
    `refused`;
  - `source-clock` opens for a FortiGate source whose clock is 8 minutes behind, for one 3 hours
    behind and for one 3 hours ahead (no row of the last two has a device time in the last hour),
    and for a MikroTik DHCP source 8 minutes behind; names the source and the offset (never in the
    e-mail); stays closed for a source with a few late lines among on-time ones, for receive-time
    rows, and for a switch or access point 10 minutes off whose files hold no lookup rows (no
    `fg_traffic`, `fg_dhcp`, `mt_dhcp` or `mt_arp` in `search_counts`); and resolves at the next
    check that finds no checked source beyond the limit;
  - `fortigate-renewals-missing` opens on a zero-renewal FortiGate-only site whose active clients'
    leases time out across several `run_incremental` runs (the alert reads the stored leases, not
    run counters); its text sends the administrator to `fortigate-refused` first and never names
    `dhcp.fortigate_traffic_extends`; it stays silent for a guest VLAN with 1-h leases and phones
    that leave and come back after the timeout, and for Monday returns under 2-day leases; with
    renewal ACKs logged elsewhere the same gaps raise nothing (no gaps cause); on a site with
    `dhcp.fortigate_format_verified` true and no renewal ACK in 30 days it gives
    `fortigate-renewals-not-logged` (**info**) and not the high cause, while on such a site whose
    renewal ACKs stopped after being seen in the 30 days the high cause opens; `lease=0` leases are
    never counted;
  - `fortigate-decline:<net>` opens for two quick moves off one address in 24 h, names VLAN 42 and
    not the IP in its key, title and e-mail, holds the address in its Security-page details, and
    stays closed for a single move and for moves to an address of another network; on a network
    without a VLAN number its key carries the subnet (`fortigate-decline:172.20.42.0_24`), its
    title the network's name and subnet, and its e-mail no VLAN, subnet or name;
  - `dhcp-sources-overlap` opens for MikroTik + FortiGate rows in one subnet and for two `vd`
    values (rendered "MikroTik DHCP", "FortiGate DHCP (VDOM root)"; a 40-character `vd` is cut to
    32), not for two `devid` values or two senders of one HA pair;
  - `fortigate-dhcp-mac` opens for one MAC ACKed on four addresses of one interface and network in
    24 h (each ACK closing the previous lease, D33), and stays closed for a laptop with one address
    on each of two VLANs;
  - `fortigate-lease-unlimited` (high) for `lease=0`, `fortigate-lease` (low) for 10**7;
  - `fortigate-unverified` (info) closes when the setting is true;
  - `srcmac-shared` is per interface and stays closed for one MAC with one IPv4 and two IPv6
    addresses;
  - `R20-nat:conversion-failed` opens after a failed v0018 conversion (moved here from C1's
    migration test) and closes when an administrator writes `nat.egresses`;
    `R20-nat:unmapped-transip` opens for an unclaimed `100.64.13.9` with 50 sessions in the last
    hour and closes once an exit claims it;
  - **a stored unparsable `nat.egresses`** opens `R20-nat:invalid` (high) and leaves every
    `R20-lease` cause working (`none`, `fortigate-refused` and `fortigate-unconverted` still open
    and resolve in the same run: the two checks have their own transactions);
    `R20-nat:legacy-changed` opens after rollback → legacy edit → re-upgrade, on a converted site
    and on a site that had the shipped NAT defaults (`nat.egresses` owned by `:seed`, no status
    row), and closes at the next check after an administrator writes `nat.egresses`, although
    the legacy rows are still there;
  - high causes are e-mailed with the default `alerts.email_min_severity`, and the e-mail holds
    the rule name, the cause's line and counts only (no source name, interface, logid or address;
    the describer test of 3.9 keeps `ALERT_MAIL_IP_CASES` unchanged);
  - the others resolve after 24 h clean.
- Grep test (2.6.2): no `mt_dhcp` special case remains in `correlation/` and `lookup/` outside
  the registry files.
- **Rollback step and restore (2.2.1, 2.6.2; round 6; `db/tests/test_lease_rollback.py`, new, and
  a `test_leases_fortigate.py` case):** `arcilogger-db leases prepare-rollback` sets every
  `fg_dhcp` lease to `low` (ended ones too) with one `updated_at`, leaves `mt_dhcp` and `mt_arp`
  leases and every evidence row unchanged, writes `leases.rollback_lowered` with that time and
  the count, and prints the count; then one lowered lease is rewritten as the previous version
  would (`end` set, `updated_at` later) and another left alone: `arcilogger-db upgrade` (the
  seed) restores only the untouched one to `high`, keeps the rewritten one `low`, deletes the
  status row and prints both counts; a second run of the seed changes nothing; `rebuild_day`
  of a day whose seed is the rewritten `low` lease writes it `high` (re-derived from its
  `fg_dhcp` rows), while `run_incremental` keeps it `low`; a lease already `low` from an earlier
  rollback, rewritten then, is not restored by the next re-upgrade (its `updated_at` is not the
  step's time).

### 3.4 Lookup goldens (WP-C)

**FortiGate-only golden** (`worker/tests/test_lookup_fortigate_only.py`). The site is built with
`Evidence.fortigate_dhcp` plus traffic plus `rebuild_day` (day by day, so seeds at midnight are
exercised), using the example chain; the worker never imports `api/lookup_testing` (1.2):
- internal mode `172.20.42.57` at `AT` gives MAC `00:00:5e:00:53:57`, device `Planlama-PC-14`,
  person Selin Kaya, department Planlama, lease `high`, `source_table="fg_dhcp"`,
  `lease_seconds=86400`, and evidence `fg_dhcp` with the raw reference of spool `fortigate`; no
  `no_lease`;
- candidate mode (no exits) and exact mode (exit `wan`, `100.64.12.9:51001`) give the same chain
  per match;
- with `dhcp.fortigate_format_verified` false the chain carries `fortigate_format_unverified`
  (and no note that singles out FortiGate DHCP, 2.7); `LeaseInfo.lease_seconds = 86400` and
  `last_dhcp_evidence` = the newest ACK at or before `AT`;
- a static client gives a match with `chain=None` and `chain_missing` (count 1);
- an `fg_event` DHCP row that covers T gives `fortigate_dhcp_not_converted` for its IP, with its
  three possible reasons; **a pre-upgrade ACK 4 days before T with `lease=604800`** is found (the
  probe window covers FortiGate lease times) and cited in `unconverted_dhcp` (one row, labelled
  "zincir kurulmadı; güven düzeyi verilmez"), while one 100 days before T with `lease=86400` is
  not (coverage rule); an `fg_event` DHCP row of **another MAC** for the IP an hour after T is
  never cited, and its MAC and host name appear nowhere in the outcome; **a pre-upgrade ACK
  followed by its RELEASE before T** (a mapped `deassigned` logid) gives no
  `fortigate_dhcp_not_converted` and no citation; **a pre-upgrade ACK of :31 (lease 604800) at
  U − 1 d, then a converted ACK of :77 for the same IP at U + 2 h and its RELEASE at U + 3 h**, and
  a lookup at U + 5 h: no chain, no text, no citation (the newest row before T is the converted
  RELEASE); the same pre-upgrade ACK written by an `unknown-<ip>` sender is never cited; in a
  candidate lookup with 5 chainless candidates whose IPs have covering pre-upgrade ACKs, the
  outcome carries `fortigate_dhcp_not_converted` with the IPs and `unconverted_dhcp` is empty;
- **DECLINE (D33)**: ACK(172.20.42.57, `00:00:5e:00:53:31`) at 2026-10-12 08:41:10, ACK(.58, :31)
  at 08:41:14, traffic from .57 at 10:12 (a static printer) → no `high` chain for :31 (the .57
  lookup gives `no_lease`); the .58 lookup gives :31's chain; **the same on 172.20.42.0/24 stored
  with `vlan_id` NULL** (a FortiGate-only flat LAN on `internal`, entered without a VLAN number;
  round 6): no `high` or `medium` chain for :31 at .57; **with no network entered for
  172.20.42.x**: the .57 and .58 lookups give :31's chain at `low` (lease and chain) with
  `fortigate_dhcp_no_network`, never `medium` or `high`; **the network entered after the build,
  no rebuild** (round 7): the leases of 10-12 built with no network for 172.20.42.x, then
  172.20.42.0/24 'Ofis' entered with no VLAN id on 10-21 (the 10-12 leases are final by then):
  the .57 lookup at 10-12 10:12 gives :31's chain at `low` (lease and chain) with
  `fortigate_dhcp_not_converted_since_lease` (matched by the converted 08:41:14 ACK of .58),
  never `medium` or `high`, without `fortigate_dhcp_no_network`, citing nothing; after the
  leases are rebuilt from 10-12 it gives `no_lease`; **a /24 entered for a /23 scope** (round
  7): 172.20.42.0/24 entered, the FortiGate serving 172.20.42.0/23 on `internal`,
  ACK(172.20.42.57, :31) at 08:41:10 and ACK(172.20.43.10, :31) at 08:41:14: the build closes
  nothing (the event's network is None), the 172.20.42.57 lookup at 10:12 gives :31's chain at
  `low` with `fortigate_dhcp_not_converted_since_lease` (the other address lies in no configured
  network), never `medium` or `high`, and the 172.20.43.10 lookup gives it at `low` with
  `fortigate_dhcp_no_network`;
- **relayed networks (D33 key)**: a NAS `00:00:5e:00:53:44` with ACKs for 172.20.41.44 and
  172.20.43.44, both with `interface="internal"` (two DHCP servers on one interface, F22), renewed
  alternately: both addresses keep their chains at every renewal (no `fg_mac_moved`), and
  lookups of either address between renewals stay `high` (the round-7 converted-row check skips
  the MAC's row in the other configured network, 2.7);
- **migration keeps the IP (D5)**: (172.20.42.57, `00:00:5e:00:53:57`) open as `mt_dhcp`, then a
  FortiGate ACK of the same pair, then no FortiGate renewal and a printer's traffic from .57: a
  lookup two days after the ACK gives no chain for Selin Kaya (`no_lease`), never `high`;
- **migration moves the IP (D33, MikroTik variant)**: 2026-10-01, :57 holds .57 through MikroTik
  (pool .50–.199); the FortiGate (pool .100–.199) ACKs :57 → 172.20.42.120 at 09:20; a printer set
  statically to .57 sends traffic for a week: every .57 lookup after 09:20 gives `no_lease`,
  never Selin Kaya, also once the D27 window has passed; the .120 lookup gives her chain; **the
  network entered after the build** (round 7): the same rows, the leases built while no network
  held 172.20.42.x, then 172.20.42.0/24 entered without a rebuild (and the same with no network
  entered at all): every .57 lookup after 09:20 gives Selin Kaya's chain at `low`, never `medium`
  or `high`: with `fortigate_dhcp_not_converted_since_lease` (the converted 09:20 ACK of :57 for
  .120), or with `dhcp_sources_overlap` while D27's window still holds both origins of the entered
  network;
- **migration moves the IP before the upgrade (D8, round 6)**: the same site, but the 10-02 09:20
  ACK of :57 → .120 is an `fg_event` row with the full DHCP shape (a line the previous version
  loaded), VLAN 42 has no ARP-LOG, the printer's traffic from .57 keeps the `mt_dhcp` interval
  (.57, :57) open, and :57's first converted ACK (a renewal of .120) comes at 10-05 21:20: a .57
  lookup at 10-04 14:00 gives Selin Kaya's chain at `low` with
  `fortigate_dhcp_not_converted_since_lease` (matched by her MAC), never `medium` or `high`, and
  cites nothing; **client B variant**: the `fg_event` row ACKs .57 to another MAC instead (matched
  by the IP): the same `low`, and B's MAC and host name appear nowhere in the outcome; **another
  network**: the `fg_event` row ACKs :57 an address of another configured network (a laptop on
  two VLANs): the .57 chain is not lowered by it; the same rows from an `unknown-<ip>` sender
  lower nothing;
- with `dhcp.fortigate_format_verified` true the chain carries `fortigate_format_verified` with
  the setting row's time and author;
- **no renewal, traffic only**: ACK(A) for the IP at t0 (lease 86400), no further ACK, traffic
  from the IP until t0 + 2 days: a lookup at t0 + 1.5 days gives no chain for A (`no_lease`; the
  lease ended at t0 + 24 h); a lookup at t0 + 15 h gives A's chain at **`low`** (lease and chain)
  with `lease_renewal_not_seen`; a lookup at t0 + 11 h gives it at `high`; with renewals every
  12 h (written with `Evidence.fortigate_dhcp`, as `build_site(dhcp="fortigate")` does for WP-D)
  the chain stays `high`; t0 is 08:00 and the days are built one by one with `rebuild_day`, so the
  lease's end falls after a midnight while traffic continues past it (the `_seeds` `last`, 2.6.2);
- **superseded early, no renewal (D6, 2.7)**: ACK(A) for .142 at t0 (lease 604800), no renewal,
  no logged RELEASE, ACK(B) for .142 at t0 + 5 d; a lookup at t0 + 4 d gives A's chain at **`low`**
  with `lease_renewal_not_seen`, `LeaseInfo.last_dhcp_evidence` = t0 (never B's ACK time), and
  the PDF lease row prints t0 (WP-D); the same with lease 86400, ACK(B) at t0 + 22.5 h and a
  lookup at t0 + 14 h: `low`;
- **renewed after the moment (D6, 2.7; round 5)**: ACK(A) for .57 at t0 = 08:00 (lease 86400), no
  ACK until A's same-pair ACK at t0 + 23 h 50 min (a continuation of the same interval: A came
  back inside its lease), traffic from .57 at t0 + 14 h (a static device): a lookup at t0 + 14 h
  gives A's chain at **`low`** with `lease_renewal_not_seen` and `last_dhcp_evidence` = t0
  (never the later ACK); a lookup at t0 + 23 h 55 min gives `high` with `last_dhcp_evidence` =
  t0 + 23 h 50 min. **Dropped refs**: lease 3600 with a renewal ACK every 30 min from t0 to
  t0 + 10 h (more than `CONTINUATIONS_KEPT` continuations), a lookup at t0 + 2 h 10 min stays
  `high` with `last_dhcp_evidence` = t0 + 2 h, which the stored refs no longer hold (the row
  query, 2.7);
- **source clock (D28, 2.7)**: the FortiGate's rows around T are 8 minutes behind their receive
  time (a clock set back), an exact request at T: the outcome carries `source_clock_offset`
  naming `fortigate` with about −480 s, every chain is `low`, the match keeps kind `exact`; with
  an offset of 30 s nothing is added; with fewer than 10 rows of the source around T nothing is
  added; **empty result (round 5)**: the same clock, the real session logged at device time
  T − 8 min and no session in the device-time window: the exact request gives `none` with
  `source_clock_offset` naming `fortigate` at about −480 s (the source chosen through
  `search_counts`, whose rows the test writes as the ingest loader would; no row cited), and an
  internal lookup without a lease at T on that
  FortiGate-only site gives `no_lease` with the same text; the same exact request with the
  tolerance widened to 600 s finds the session; a `no_egress` answer measures nothing;
- **rollback window (D8, 2.2.1; round 5)**: an `fg_dhcp` ACK of (.57, :31) at t0 (lease 604800),
  then a trusted `fg_event` row with the full DHCP shape and logid 0100026001 for .57 and :44 at
  t0 + 26 h (a line the previous version loaded after a rollback): a lookup at t0 + 27.5 h gives
  :31's chain at **`low`** with `fortigate_dhcp_not_converted_since_lease`, nothing in
  `unconverted_dhcp`, and :44's MAC and host name nowhere in the outcome; a lookup at t0 + 25 h
  stays `high`; the same `fg_event` row from an `unknown-<ip>` sender lowers nothing;
- **srcmac contradicts the lease**: ACK(A), then a session of the IP on a listed interface whose
  srcmac is B: the chain is A's with confidence `low`, `srcmac_mismatch`, and B's srcmac chain as
  the alternative (also `low`);
- **two DHCP origins**: an `mt_dhcp` lease of MAC C for the IP, then an `fg_dhcp` ACK of A for
  the same IP an hour later: a lookup shortly after gives A's chain at `low` with
  `dhcp_sources_overlap` (C's row lies within W); a lookup while MikroTik rows of that VLAN
  still arrive within W gives A at `low` with `dhcp_sources_overlap`; once only one origin is
  left within W, A at `high`; two FortiGate senders of one `vd` (an HA pair) never lower a
  chain; one `usable_dhcp_origins` query serves every chain of the lookup;
- an ACK from an `unknown-<ip>` source creates no chain and no device.

**srcmac** (`test_lookup_srcmac.py`):
- off by default (srcmac present, no chain);
- interface listed: srcmac chain, `low`, `mac_source="srcmac"`, `srcmac_only`, device and
  person;
- two IPv4 addresses with one MAC within ±1 h give `srcmac_shared` and no chain (default
  `srcmac_max_ips = 1`); one IPv4 and two IPv6 addresses with one MAC (a dual-stack client) do
  not;
- a srcmac row of an `unknown-<ip>` source is never used and not counted by the guard;
- a srcmac that holds a lease of another IP in the window gives `srcmac_other_lease`;
- an infrastructure MAC gives `srcmac_infrastructure`;
- a lease with an equal srcmac gives `srcmac_agrees`; a different one gives the `low` conflict
  of D10 (lease chain first, srcmac alternative, `srcmac_mismatch`);
- internal-mode ambiguity with four MACs names the count and three MACs; MAC-only internal
  lookup;
- an unlisted interface is never used, and with `lookup.srcmac_interfaces = []` no outcome
  (stored JSON included) contains any `srcmac` value although the rows carry one;
- `leases` and `devices` are unchanged after lookups.

**Dual WAN** (`test_lookup_egress.py`). Values: T1 `100.64.12.9`, T2 `100.64.13.9`, P1
`203.0.113.200`, P2 `198.51.100.77`, clients `172.20.42.57` (WAN-1) and `172.20.60.142`
(WAN-2), destination `203.0.113.10:443`. The first group fails on today's code.
- **E-a** Two `isp_nat` exits with public addresses: a P2 question lists only the T2 session,
  and `outcome.egress` names `hat-2` with resolution `public`.
- **E-b** P1 lists only T1.
- **E-c** `direct` P1 quiet while T2 is active: a P1 + port question is `none` (exact, no
  session), never the T2 session; no `public_ip_not_transip`.
- **E-c2** hat-1 `direct` P1 on `wan1`, hat-2 `isp_nat` T2 on `wan2` **without** a public
  address, line 1 double-NATed to an unclaimed private X (`100.64.12.9`) at T (X rows on `wan1`,
  T2 rows active): a P1 +
  port question gives resolution `fallback`, candidates over P1 ∪ X on `wan1` only, never a T2
  session, `public_ip_not_transip`, `double_nat` true (the PDF assertions, the `fallback`
  paragraph without the "define as Direct" advice, are WP-D's, 3.5). The same **without any
  declared interfaces** (a new configuration): F is empty, so candidates over P1 ∪ X, still never
  a T2 session, and the editor warns about the missing interfaces. Only exits *as converted* by
  v0018 fall into each other without interfaces (E-j (d), (d2)). The fallback carries
  `public_ip_not_transip` and **never `egress_search`** (its text says the exit is defined as
  ISP NAT, which hat-1 is not). With X on `wan2` instead (another line's private address) and
  hat-1's interfaces declared, the check passes (rows off E's interfaces do not count) and P1 +
  port is exact.
- **E-c3** the no-port form of E-c2 (P1 without a port) also runs the log check and gives
  `fallback`, not `transip = P1` candidates.
- **E-c4 (the own set leaves out `isp_nat` exits without a public address; round 5)** an upgraded
  single-line site whose ISP router still translates, only `100.64.12.9` (on `wan1`) active near
  T, P1 + port asked:
  - legacy (b) (`genel-1` direct P1 + `tanimsiz`, both as converted) plus `hat-2` (`isp_nat`,
    `transips [100.64.12.9]`, `interfaces [wan1]`, no public) saved from the observed panel:
    the D15 check of `genel-1` fails (hat-2's transips are not in its own set: hat-2 has no
    public address and `genel-1` declares no interfaces), so the answer is `fallback` with
    `public_ip_not_transip`, never `none` with `no_match_exact` alone; the fallback search
    covers P1 ∪ the unclaimed predicate (F = [`tanimsiz`]) and lists no `100.64.12.9` session;
  - legacy (d) (`genel-1` + `wan` with W = `100.64.12.9`) after `wan1` is entered on `wan` only
    (no longer as converted, F empty): the same `fallback` with `public_ip_not_transip`;
  - with `wan1` declared on `genel-1`, `wan2` on `hat-2` and `100.64.12.9`'s rows on `wan2` (a
    real second line), hat-2's transips leave the own set (disjoint declared interfaces, round
    6) but rows off `wan1` do not count: P1 + port is exact, as in E-c2's last case;
  - **the same address on both lines (round 6)**: hat-1 `direct` P1 (`203.0.113.200`) on `wan1`,
    hat-2 `isp_nat` with `transips [100.64.12.9]` on `wan2`, once with and once without a public
    P2; line 1's modem was reset to router mode, so near T `100.64.12.9` rows lie on `wan1` (and
    on `wan2`), and P1 has no row: P1 + port gives `fallback` with `public_ip_not_transip`, never
    `none` with `no_match_exact` alone, and lists no session that left by `wan2`.
- **E-c5 (seen, but not alone; D15, round 6)** legacy (d) as converted (`genel-1` direct
  `203.0.113.200`, `wan` with W = `100.64.12.9`, no interfaces), line 1 in bridge mode until its
  modem is reset to router mode at 18:14:00: `.200` rows 18:04–18:14, `100.64.12.9` rows from
  18:14, the requester's session at 18:19 through `100.64.12.9`; a request for `.200:51001` at
  18:19:07 (tolerance 60 s): the probe sees `.200` and `100.64.12.9`, the exact search finds
  nothing, so the answer is `fallback` over `.200` ∪ `100.64.12.9` listing the requester's
  session, `public_ip_not_transip` with the "not consistently" wording, and
  `searched.outside_seen` = `["100.64.12.9"]`, which the fallback paragraph's `{seen}` names;
  **mirror**: W rows until 18:24 and `.200` rows from
  18:24 (a switch to bridge mode), a request at 18:19: the same `fallback`; **a found match
  stays exact**: the requester's session through `.200:51001` at 18:12 gives `exact`; **a new
  configuration** in which `100.64.12.9` was saved as an `isp_nat` exit on `wan1` during an
  earlier router-mode episode (it shares `wan1`, so it is in G(E)) behaves the same. E-o, E-c2's
  last case and E-c4's third case keep their exact answers (their outside addresses are public or
  lie off E's declared interfaces).
- **E-d** `not_logged` lists nobody (`egress_not_logged`).
- **E-d2** a `not_logged` exit's address that is the `transip` of sessions overlapping T: answered
  as `observed` (exact with a port), the exit in `egress.egresses`, `egress_not_logged_but_seen`.
- **E-e** An unclaimed P with exits configured:
  - with a public-unknown exit, only that exit is searched and `egress_public_unknown` is given;
  - with an `isp_nat` exit marked `public_dynamic` (and a public address), that exit is searched
    with `egress_public_unknown`; a request for that exit's **listed** address is resolution
    `public` and carries `egress_public_dynamic` (and its PDF method sentence, WP-D), while the
    same request to an exit not marked dynamic carries none;
  - without either, and P not seen as a transip, `none` with `public_ip_no_egress`.
- **E-e2 (observed)** every exit has a public address; P (unclaimed) is the `transip` of
  sessions within ±15 min (a direct line whose address changed): P + port gives `exact` on
  `transip = P` with `egress_observed_not_configured`, `double_nat` false, `NatMatch.egress`
  null; without a port, candidates `transip = P`.
- **E-e3 (observed without exits)** `nat.egresses = {}` (a bridge-mode site that never defined
  exits) and P the `transip` of sessions overlapping T: P + port gives `exact` with
  `egress_observed_not_configured`, resolution `observed`, never `unconfigured`, no other
  person's session, and the PDF has no "Çift NAT varsayımı" paragraph (3.5).
- **E-f** Every `isp_nat` or `transip` narrowing carries `egress_search`; a `direct` exit's
  narrowing, its fallback included, never does; every match carries `egress`.
- **E-g** The port hint applies only to a `preserved` exit's candidates (+5), never in exact
  mode.
- **E-h** `outcome.egress`, `NatMatch.egress`, `mac_source`, `TrafficInfo` and `LeaseInfo`
  fields round-trip, and old stored outcomes load.
- **E-i** IPv6: API 422 unchanged; an IPv6 exit is refused at validation.
- **E-j (legacy equivalence)** For every NAT test of the Wave 2 `test_lookup_engine.py`, and for
  each case (a)–(g) of 2.8.2 with and without a port, the config `legacy_egresses(<its old
  settings>)` gives the same mode, the same matched session ids and the same `double_nat` as
  the **expectation recorded from the Wave 2 tree** (the existing assertions, plus a golden file
  `worker/tests/goldens/legacy_nat.json` that C1 records on the Wave 2 tree before switching).
  The exceptions are listed by name in the test: the D31 (a) observed answer (an unclaimed P that
  the FortiGate itself used as `transip` near T is exact where Wave 2 gave candidates, in (a)–(f),
  (a) without exits included);
  C2 `double_nat`; case (f) other P; case (g) (503 before); case (h) (a private address in
  `nat.public_ips`); a fallback that now also lists E's own sessions overlapping T (in (d)
  Wave 2 left them out when W was seen); **an unclaimed public NAT address** (an IP pool
  next to legacy (a), (b), (e), (f)): it no longer fails A's check, so A + port is exact where
  Wave 2 fell back to candidates, and its sessions are no longer candidates for another P; and
  **an empty search on a P seen beside a private or shared address outside its own set** (E-c5,
  round 6): `fallback` where Wave 2 answered `none`. The
  recorded Wave 2 assertions themselves use the private `100.64.12.9` as the other address
  (1.2), so they stay unchanged. Probe B (legacy (b)) and probe C (legacy (d)) keep their
  Wave 2 answers on upgraded sites and are **not** exceptions; their fix applies once interfaces
  or the second line's public address are entered (E-c, E-c2). Case (c) exact on W keeps
  `double_nat` true.
- **E-j (d2)** legacy (d), A quiet, only W active, no interfaces: resolution `fallback`,
  candidates over W, `public_ip_not_transip` (Wave 2's answer; the D15 own set leaves W out).
- **E-k** Interfaces: two exits sharing T1 with `wan1`/`wan2` split correctly; an exit declaring
  `wan2` whose own transip has rows near T only on `wan3` (no exit lists it) gives
  `egress_interface_not_seen` (condition dropped), the same for a shared transip gives `…_kept`
  (rows on the other exit's `wan1` are no evidence either); an exit declaring `wan2` whose
  addresses have **no** row near T keeps the condition without either note; a row of the shared
  T1 whose `dstintf` is `wan3` gets `NatMatch.egress = null`.
- **E-k2 (transip + port, check fails)** P = T2 (an `isp_nat` exit's NAT address) with a port
  while T2 has no row near T and an unclaimed private address (`100.64.12.77`) is active:
  resolution `fallback`, candidates over T2 ∪ unclaimed, `public_ip_not_transip`, `double_nat`
  true (its PDF paragraph is the `transip` variant, 3.5); with only an unclaimed public address
  active instead, the check passes and the answer is exact on T2.
- **E-k3 (session changes exit)** two exits share T1 with `wan1`/`wan2`; one session whose
  interim row has T1/`wan1` and final row T1/`wan2` (same `transip` and `transport`, so one group,
  `TrafficRow.key`): the match's exit is that of the row nearest T, with `session_changed_exit`.
  A session whose interim row has T1 and final row T2 is two groups, i.e. two matches, each with
  its own exit and no `session_changed_exit` (E-j keeps Wave 2's grouping).
- **E-k4 (exits changed after the event)** a lookup for a moment before a content change of the
  exit it used carries `egress_changed_after_event` naming that exit, the change's time, author
  (`:migration` shown in words) and current addresses; a label-only, interface-only or
  port-preservation-only edit (the interface step §13 asks for), or a change of another exit
  while P is claimed, gives none; an edit of the exit's `public`, `transips`, `mode` or
  `public_dynamic` gives it; an `observed` answer gives none whatever changed; a lookup after the
  change gives none. For a P that
  no exit claims now: (1) `tanimsiz` (a catch-all) deleted after T → `no_egress` **with** the
  warning naming `tanimsiz` as deleted; (2) hat-2's public address edited twice after T
  (A → B → C), a request for A at T → the warning names hat-2 with its current addresses; (3)
  hat-2 renumbered A → B and its interfaces added later, a request for A → the warning names
  hat-2. The PDF assertions are WP-D's, 3.5.
- **E-l** Unknown `trandisp`: a row with an unlisted `trandisp` and the transip of a configured
  exit is found by the exit filter; on the unclaimed path it is counted (`nat_unknown_trandisp`),
  not dropped silently.
- **E-m** `egress_unmapped_seen` appears when an unclaimed source-NAT address is active near T.
- **E-n** `isp_nat` not seen: widened to its transips ∪ unclaimed with `egress_transip_not_seen`;
  a quiet window keeps the narrowing.
- **E-n2 (widening keeps the interfaces), two fixtures.** The E-o site plus hat-2 (`isp_nat`,
  public P2, T2, interfaces `wan2`), and a private unclaimed address `100.64.12.77` active on
  `wan1` (line 1 double-NATed by a forgotten router):
  - **idle line:** at 02:10 SD-WAN sends everything over `wan1`, so `wan2` carries no session
    and T2 has no row; a P2 question widens (O5) to T2 ∪ unclaimed **on `wan2` only** (the
    condition is kept, no `egress_interface_not_seen`), lists none of line 1's sessions, finds
    line 2's own long session overlapping T if there is one and nobody otherwise, with
    `egress_transip_not_seen`; the same with hat-1's fallback (E-c2) stays on `wan1`;
  - **renamed interface:** T2's rows near T carry `port2`, which no exit lists: the condition is
    dropped with `egress_interface_not_seen` and T2's sessions are found.
- **E-o (long session; second and fourth reviews)** hat-1 direct `203.0.113.200` on `wan1`; a
  mail server's SNAT uses the unconfigured **public** `203.0.113.201`, active every minute;
  `172.20.42.57:51001 → 198.51.100.10:443` via `.200:51001` runs 01:55–02:40 and is logged once, at
  02:40. A request at 02:10 for `.200:51001`: the D15 check **passes** (no `.200` row in ±15 min,
  but `.201` is public, so it is no double-NAT evidence) → resolution `public`, **exact** on
  `transip = .200 AND transport = 51001` over the overlap-aware window, which lists that one
  session, `double_nat` false, none of the mail server's sessions, no "may have done a second
  NAT" sentence, and `egress_unmapped_seen` naming `.201`. Without the port: candidates
  `transip = .200` with `port_missing`, the same session among them. A request for `.201` at
  02:10 is `observed`. **The fallback path** keeps its own fixture: a private unclaimed
  `100.64.12.77` on `wan1` instead of `.201` makes the check fail → `fallback`, the session is
  a candidate (E's own address stays in the search), and a sixth unconfigured private address
  is searched too (the predicate, not the ≤ 5 seen), while `.201` sessions never are.
- **E-q (internal NAT, D13)** hat-1 direct P1 with no interfaces declared and an `internal` exit `dmz` with
  `transips = [172.20.99.1]`, `interfaces = [dmz]`, while LAN→DMZ sessions with NAT leave through
  172.20.99.1 every minute: a quiet P1 + port passes the check (172.20.99.1 is claimed), no
  lookup carries `egress_unmapped_seen` for it, a `public_unknown` or `unconfigured` search never
  lists a DMZ session, `R20-nat:unmapped-transip` stays closed for it (WP-B), and a request for
  172.20.99.1 itself gives `none` with `egress_internal` and no search. Without the `dmz` exit
  the same DMZ address is named by `egress_unmapped_seen` and searched as an unclaimed private
  candidate in the catch-all.
- **E-p1 (untrusted sender, probe; C1)** a forged session line from an `unknown-<ip>` source
  never makes an unclaimed address `observed` and never fails or passes a D15 check (it needs
  only `trust.untrusted_sources`, day 0).
- **E-p2 (untrusted sender, match; C2)** the same forged line matching the request is listed with
  `NatMatch.untrusted_sources = ["unknown-…"]` and the outcome carries
  `session_from_unknown_source`; among 20 candidates only that match is marked (the PDF
  assertion is WP-D's, 3.5).

### 3.5 API and report (WP-D)

- `test_report_consistency.py`:
  - `fg_dhcp` claims pass on the golden FortiGate site and mismatch when the row is altered;
  - **a lease closed by D33** (ACK .57 for :31, then ACK .58 for :31) cited in a report: the
    closing line (.58) claims `mac` and passes, no "uyuşmuyor" warning; a lease superseded by
    another MAC's ACK claims `ip` on the closing line and passes; the MikroTik lease closed by a
    FortiGate ACK of its MAC for another IP passes the same way; an altered closing row still
    mismatches;
  - a srcmac chain's claims (`srcip`, `srcmac`) pass and mismatch alike;
  - `fg_dhcp` is no longer `not_checked`.
- `test_report_pdf.py`:
  - a FortiGate-only report names "FortiGate DHCP kaydı" and never says MikroTik for it; its
    lease row prints the lease time and the last DHCP evidence (for the superseded-early golden
    of 3.4, the holder's own ACK time, never the next client's); while the outcome carries
    `fortigate_format_unverified` the method prints the "sahada doğrulanmadı" sentence, and for
    an outcome carrying `fortigate_format_verified` the "{tarih} tarihinde {kullanıcı}
    tarafından sahada doğrulandı olarak işaretlendi" sentence instead; **a lookup made while
    unverified, then the setting switched on, then the first download**: the PDF still says
    "sahada doğrulanmadı" (the note comes from the stored outcome, never from the setting);
  - an internal lookup with `unconverted_dhcp` prints that raw line in section 6 marked "kira
    zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez", and no other client's
    later DHCP line; a candidate report with 5 chainless candidates prints the
    `fortigate_dhcp_not_converted` text and cites no `fg_event` line;
  - section 4 prints the exit row "Çıkış / Exit: <label> — <name (label)>, <mode>" with the label
    of the one table of 2.12 for each resolution, "Doğrudan hat doğrulanamadı" for E-c2's
    fallback and "NAT adresi doğrulanamadı" for E-k2's; `transip` of an `internal` exit (E-q)
    prints "Oturum aranmadı: … internet çıkışı olmayan …" in sections 4 and 5;
  - **a Wave 2 outcome** (a stored sample without `egress`, `dstintf` or `source_table`) renders
    without an exit row in section 4 or on its matches, without a per-resolution method
    paragraph, and, with `double_nat` true, with the "Çift NAT varsayımı" variant and the CGNAT
    sentence, never the old "WAN arayüzü ISS modeminin arkasındadır" sentence;
  - `source_clock_offset` (the 3.3/3.4 golden), `egress_public_dynamic` (E-e) and
    `egress_internal` (E-q) are printed in the method section with their texts;
  - a match with `untrusted_sources` prints its "UYARI / Warning" row with the source name, and
    the other 19 candidates of the same report print none (E-p2);
  - `no_egress` and `not_logged` print "Oturum aranmadı: …" in sections 4 and 5, never
    "Ölçütlere uyan kayıt bulunamadı"; `no_egress` lists the configured exits in its method
    paragraph;
  - a chain at `low` through `lease_renewal_not_seen` prints "güven: düşük" and the text beside
    its lease row, with the last DHCP evidence before the moment (the renewed-after-the-moment
    golden of 3.4 prints t0, never the later ACK); a chain at `low` through
    `fortigate_dhcp_not_converted_since_lease` (the rollback-window golden, and the round-6
    "migration moves the IP before the upgrade" golden for a MikroTik lease) prints its text
    beside the lease row and no `fg_event` line in section 6; a chain at `low` through
    `fortigate_dhcp_no_network` (the DECLINE golden without a network) prints its text beside the
    lease row;
  - E-e3's PDF (observed, no exits) has no "Çift NAT varsayımı" paragraph and no other person's
    session;
  - a srcmac step prints "kısmi kanıt" and the device-identification wording, never "geldiğini
    gösterir"; a srcmac conflict prints both MACs, the alternative under "Olası diğer zincir
    (srcmac, kısmi kanıt)";
  - every match row prints its exit and `dstintf`;
  - **device identification in raw lines**: a candidate report whose cited session lines carry
    `srcmac`, `srcname` and `unauthuser` prints them in the raw lines with
    `lookup.srcmac_interfaces = []` (documents D21; raw lines are never modified), while no typed
    field of the outcome carries the MAC;
  - a missing chain prints its reason;
  - one method paragraph per (resolution, mode) row of 2.12, each with its own text (eleven in
    all, the `transip` internal paragraph, the `transip` fallback variant and the "searched
    addresses of another exit" addition included), `{exits}` filled with the names and labels;
  - the double-NAT paragraph is present for every `isp_nat` answer with resolution `public`
    (C2 fixed), says "varsayıldı" for `unconfigured`/`public_unknown`, carries the CGNAT
    sentence, and is **absent for resolution `transip`**, with a port and no session as well
    (the transip sentence stands); for a `fallback` the PDF never says that the line sits behind
    an ISP router as a fact and never advises "define as Direct";
  - **E-k2's PDF** (transip fallback) prints the `transip` variant, without bridge mode and
    without "define as Direct"; E-c2's PDF prints the direct variant without "define as Direct";
    E-c5's PDF prints the direct variant with "ya da yalnızca bir kısmında görünüyor" and names
    `100.64.12.9` as `{seen}`;
    legacy (d)'s fallback into `wan` prints the "searched addresses of another exit" addition;
  - `egress_changed_after_event` is printed in the method section (E-k4) with the exit, time, an
    internal author in words and what the exit holds now ("silindi" for the deleted `tanimsiz`),
    in the Turkish text and the English text each in its own language (`{changes_tr}`,
    `{changes_en}`);
  - a `fallback` or O5 search over the unclaimed predicate prints "… ve hiçbir çıkışa tanımlı
    olmayan bütün özel ya da paylaşılan kaynak NAT adresleri (T çevresinde görülenler: …)";
  - no compliance claim (existing `pdftext` checks).
- `test_lookup_api.py`: the `egress` block and the new fields in responses; 503
  `lookup_settings_invalid` (never 500) with a stored bad exit value, because the router calls
  `settings.egresses()` inside its `try` (C1's listed edit), and the new text (no "Ayarlar →
  NAT"); IPv6 422 unchanged; `lookup.run` audit details hold `egress`.
- Settings API:
  - PATCH `nat.egresses` returns each code of 2.5 in 422 `details`;
  - `GET /settings/fortigate/observed`: permission `settings.manage` (admin yes, auditor no),
    counts only, `unmapped_transips`, `unknown_trandisp`, cache, 504 on deadline; with a stored
    unparsable `nat.egresses` a 200 with `exits_invalid: true` and `egress: null` rows.
- `test_sources_api.py`: the `lease_evidence` state `none` follows the active
  `R20-lease:none:<table>`/`absent` alerts (read, never recomputed: an alert opened by the check
  gives the state and `none_tables`, a resolved one clears it); `refused` follows the live
  `refused_rows > 0` and **wins over `none`** when both hold (every row refused while the feed's
  usable rows stopped); after the setting is corrected `refused_rows` is 0 and the state leaves
  `refused` without waiting for the alert; `rows` counts usable
  rows only (refused and unknown-sender rows are not in it although `search_counts` has them);
  `refused_rows` leaves out excluded-interface rows, which `excluded_rows` counts;
  `format_verified` follows the setting; srcmac counts only with interfaces configured.

### 3.6 Web (WP-E)

- Vitest:
  - `egress-model.test.ts` mirrors every server rule with the same codes, reading the case list
    `tests/fixtures/egress-cases.json` that `db/tests/test_egress.py` reads too (as
    `format.test.ts` reads `format-vectors.json`); a server 422 is shown at the editor level;
  - the editor warnings: two FortiGate exits without interfaces, an ISP NAT exit with a public
    NAT address, a converted exit;
  - the observed panel prefill: a public `transip` prefills Direct with `public = [transip]`, a
    `100.64.13.9` prefills ISP NAT with `transips = [100.64.13.9]`, an empty public list and the
    warning, and offers "İnternet çıkışı değil olarak ekle" (mode `internal`) beside it; the mode
    select offers `internal` and hides the public addresses for it;
  - lease-evidence card states `ok`, `none` (one line per table of `none_tables`, the hedged
    MikroTik DHCP text, the `absent` text), `refused` winning over `none` (with
    `leases_current`, the unverified-format line, refused rows, the excluded-interfaces info
    line);
  - lookup result rendering (exit line with the eight labels of the seven resolutions, per-match exit and
    `dstintf`, the unconfigured-sender badge, the srcmac badge, the srcmac conflict, the
    FortiGate lease time and last evidence, a `low` chain with `lease_renewal_not_seen`, a `low`
    chain with `fortigate_dhcp_no_network`, the no-chain text);
  - the exit editor's `public_dynamic` box (ISP NAT only) and its warning;
  - tones for every new key;
  - evidence labels;
  - `api-types.test.ts` against the generated schemas;
  - `api-samples.test.ts` for the new samples;
  - `locales.test.ts`.
- Playwright against the captured samples with strict bodies: `settings-application.spec.ts`,
  `lookups.spec.ts`, `sources.spec.ts`; axe on the exit editor, the observed panel and the
  card.

### 3.7 Load generator and end to end (WP-F)

- `tests/test_loadgen.py`:
  - the default digest is unchanged;
  - each new option is deterministic (same seed, same bytes);
  - in `fortigate` mode there are no `core-r1`/`switch-1` lines, every DHCP line parses to
    `fg_dhcp`, the ACK logids are in the default map, and renewals happen at half the lease;
  - `--srcmac` in `mikrotik` mode puts one MAC on many IPs;
  - `--egress` shares are within ±2 % over a day, `dstintf` follows the exit, and the truth
    names the exit;
  - `_expected_table` includes `fg_dhcp`.
- `tests/test_installer_scripts.py` (integrator, round 6): the dry-run rollback plan without
  `-RestoreDatabase` has `leases.prepare-rollback` after `services.stop` and before
  `program.swap`; with `-RestoreDatabase` it has none; a failing command gives the Turkish and
  English warning of 2.2.1 and the rollback goes on.
- `tests/test_mvp_end_to_end.py`:
  - **`test_fortigate_only_site`:** a loadgen day with `--dhcp-server fortigate --srcmac
    --users 20`, then ingest, then the worker; 20 sampled truth sessions of DHCP clients are
    looked up and the chain's MAC and person equal the truth (assignments created from the
    truth); statics give `chain_missing` with srcmac off and `srcmac_only` with their
    interfaces listed; the only `R20-lease` alert that opens is `fortigate-unverified` (info),
    and it closes once the test sets `dhcp.fortigate_format_verified`;
  - **`test_dual_wan_site`:** `--egress hat-1:100.64.12.9:wan1:60 --egress
    hat-2:100.64.13.9:wan2:40` and exits with P1/P2; for 10 sampled truth sessions per exit, a
    lookup by that exit's public IP and the session's destination lists only that exit's
    sessions and includes the truth device; an unclaimed public IP that is not a `transip` gives
    `none`.

### 3.8 Performance guards (`poe test-perf`, both CI jobs)

- **Ingest gate (WP-A, ≤ 5 % slower than the Wave 2 tree,** `bench_throughput.py --repeat 3`,
  12 runs per tree alternating, medians recorded in `INGEST.md`):
  1. the default mix;
  2. a FortiGate-only spool (70 % traffic lines with srcmac, 30 % FortiGate DHCP lines),
     **written once** with the Wave 3 tool (`ingest/testing.py` mix `fortigate-only`) into a
     directory, then loaded by both trees with `bench_throughput.py --spool <dir>`: the option is
     added in Wave 3 and the same harness file is copied unchanged into the Wave 2 worktree for
     the measurement (the Wave 2 tree stores the lines in `fg_event`/`extra`);
  3. the load-generator day (default options) and a `--dhcp-server fortigate --srcmac` day,
     each written once and loaded by both trees through the same harness, **at least 16 runs per
     tree alternating** (one 8-run campaign on this VM ranged from −0.2 % to −6.7 %, INGEST.md),
     measured by the integrator after F1 merges.
- **Lookup** (`test_lookup_performance.py`, tier `1` data, ±60 s, < 5 s as today):
  - a two-exit `= ANY` candidate search;
  - an unclaimed search with `<<= ANY` exclusion;
  - an exit with a /29 public network;
  - the srcmac guard query over 2 h at 1 M rows a day (< 1 s);
  - internal-mode srcmac sightings (< 0.5 s);
  - the `fortigate_dhcp_not_converted` probe of 2.8.3 (the newest row per IP in `fg_event` and
    `fg_dhcp`) for 20 IPs over 400 days of `fg_event` at 1 M lines a day, on its 100-day window
    (8,640,000 s), worst case IPs with no DHCP row at all (< 0.5 s; on failure the 2.8.3 fallback
    window applies); the round-5 lease checks of 2.7 for 20 `fg_dhcp` chains (the per-moment
    `fg_dhcp` row and the post-lease `fg_event` query over half a 7-day lease, < 0.5 s together);
    the round-6 post-lease query of 2.7 as one query for 100 chains (`lookup.max_candidates`),
    `mt_dhcp` chains whose windows reach the 100-day cap included, by IP or MAC, over 400 days of
    `fg_event` at 1 M lines a day (< 0.5 s; on failure the cap falls back to 604,800 s, as the
    probe's window does, recorded as a deviation); the round-7 converted-row query of 2.7 for the
    MACs of 100 chains on `ix_fg_dhcp_mac_time` over the 100-day cap, at the load generator's
    FortiGate DHCP rate over 400 days (< 0.2 s);
  - the per-lookup clock measurement of 2.7 for two sources over [T − 1 h, T + 1 h] of `messages`
    at 1 M lines a day (< 0.2 s), the `search_counts` choice of the searched sources for an empty
    result included;
  - the overlap-aware `seen_overlap(P)` probe for an unclaimed P with no rows and for a busy
    unclaimed P (< 0.2 s);
  - `usable_dhcp_origins` for 20 chains in 3 VLANs over ±7 days (W of a 7-day lease) at 1 M lines
    a day, one query (< 0.2 s), and the srcmac other-lease check (< 0.1 s).
- **Endpoints and checks:**
  - observed endpoint at 1 M rows a day over 24 h: < 2 s (deadline 5 s);
  - `lease_evidence`: < 0.5 s (usable-row counts on the DHCP tables through `kept`) and srcmac
    count < 1 s;
  - each `R20-lease` query: < 1 s (`none`/`absent` with a 4.5-day evidence window and the 30-day
    evidence-type lookup, per table, `refused` with its `first_day` over 100 days of `fg_dhcp`,
    `renewals-missing` with its two session-start ranges per counted lease, `decline`,
    `fortigate-dhcp-mac`); `source-clock` (the source list from `search_counts`, then the newest
    200 `messages` rows per source in its skew-bounded range): < 0.5 s, also for a source whose
    clock is 3 h ahead; `R20-nat:unmapped-transip` over the last hour: < 0.2 s;
  - `EXPLAIN` shows no sequential scan of an uncompressed `fg_traffic` partition outside the
    requested time range.

### 3.9 Docs tests

The existing `tests/test_docs_consistency.py` and `tests/test_search_docs.py` stay green at every
merge:
- `CORRELATION.md` names every new `nat.`/`dhcp.`/`lookup.` key (day 0 adds the rows);
- `DATA-MODEL.md` names `fg_dhcp` (A0);
- ARAMA/SEARCH §3 field tables equal the registry and §6 the kind labels (A0, 2.4);
- `test_first_steps_explain_every_alert_code`: ILK-ADIMLAR/FIRST-STEPS §9 name `R20-lease` and
  `R20-nat` (WP-B's merge, the one that adds the constants);
- the alert-mail cases (`ALERT_MAIL_IP_CASES`) stay as they are: the `R20-lease`/`R20-nat`
  describers read no address and no `source` (2.16);
- UYARILAR/ALERTS;
- operator documents use placeholders only;
- no model or firmware string (ADR-0016 included; a **new** assertion checks that it cites
  "FortiOS 7.6" and no `FortiOS \d+\.\d+\.\d+`; written by the integrator on day 0, with the
  ADR);
- no compliance claim;
- the ADR index lists ADR-0016;
- a **new** assertion that `UYARILAR`/`ALERTS` list every `R20-lease` cause and the four
  `R20-nat` causes of 2.6.3, each with its one-line action (written by WP-B in its merge, with the
  docs rows);
- `MODEL_OR_RELEASE` stays green with the evidence summaries quoted in CORRELATION, RAPOR and
  ADR-0016 ("FortiGate DHCP, logid 0100026001", "FortiGate DHCP 26001"; never the number right
  after the device name, 2.6.1);
- a **new** assertion that the reading table of 2.15 in `RAPOR.md` and `DENETCI-KILAVUZU.md`
  has a row for every key of `reports/texts.py` `READING_TABLE_KEYS` (named in backticks in the
  row's first column) and for every exit label of the 2.12 label table (Turkish label; written
  in WP-F2's merge, the one that brings `DENETCI-KILAVUZU.md`, by the integrator as an append-only
  line; `RAPOR.md` is WP-D's and merges before it).

`tests/test_docs_consistency.py` is owned by the integrator; the three new assertions are its
listed append-only lines (4.4).

---

## 4. Work packages

### 4.1 Branch and gate rules

- All work lands on **`wave-3`**, branched from `main` after Wave 2. Packages branch from
  `wave-3` and merge back in the order of 4.5. `main` receives `wave-3` once, fully green:
  Linux with TimescaleDB, Windows with plain PostgreSQL, and the Windows installer end-to-end
  job.
- **Every merge keeps every gate green:** `uv run poe lint`, `uv run poe test`, `npm run check`,
  Playwright, `export_api_contract.py --check`, `capture_api_samples.py --check`, gitleaks.
  Whoever merges regenerates `web/src/generated/api-schemas.json` and `web/e2e/samples/*.json`.
  Generated files are never merged by hand.
- **Fixed by this contract:**
  - the `fg_dhcp` schema and the routing rule;
  - every setting key, default, bound and error code;
  - the exit JSON (the four modes included) and its resolution table (the seven resolution
    values and the eight exit labels of 2.12 included);
  - explanation keys and texts, and the report texts of 2.12;
  - every JSON shape of 2.10 and 2.12;
  - the R20-lease and R20-nat keys, severities, texts and their catalogue entries (2.6.3);
  - the status rows `nat.egresses_migration`, `nat.egresses_changes` and `leases.rollback_lowered`
    (v0017 writes none), and the rollback step of 2.2.1 with its restore at the re-upgrade;
  - the leaf module `lookup/trust.py` and its five functions (`untrusted_sources`, `refusal`,
    `kept`, `usable_dhcp_origins`, `srcmac_ip_counts`; 2.6.1).

  A package that has to deviate writes it into its merge description and into "Deviations"
  below; the integrator folds it into `MVP-CONTRACTS.md`.
- **One owner per file at any time.** A file handed over after day 0 is edited only by its new
  owner. The listed exceptions: WP-C1's mechanical edits (4.3), made inside C1's merge, which
  include the `HEAD` line of `db/tests/test_migrations.py` (WP-A owns the file; A0 sets
  `0017_fortigate_dhcp`, C1 sets `0018_nat_egresses`); the rewritten explanation texts and the
  tests that pin them, edited in the merge that changes their caller (4.2); WP-B's one edit of
  ILK-ADIMLAR/FIRST-STEPS §9 and its assertion in `tests/test_docs_consistency.py` (4.4); the
  TypeScript mirror types (4.2), kept in step by the server-side merge that changes the shape
  until WP-D has merged; and **one line of `api/src/arcilogger_api/routers/lookup.py`** (WP-D's
  file): C1 calls `settings.egresses()` inside the existing `(TypeError, ValueError)` → 503
  `try`, so C1's own update of `test_lookup_api.py` (a stored bad `nat.egresses` → 503) is green
  at C1's merge (2.8.1, 2.12).

### 4.2 Day 0 (integrator, about one day)

Day 0 writes only what keeps every gate green on its own: settings, types, texts, refs, the egress
stubs and the matching TypeScript mirror of 2.10. **The schema (models, v0017) is not a day-0
file**: adding `FgDhcp` to `TIME_SERIES_MODELS` and `FgTraffic.srcmac` without the parser, kinds
and search lines breaks `test_kinds.py::test_kinds_name_every_typed_table_and_mt_event_event`,
`test_client_ip_and_action_columns_exist` and `test_parser_contract.py::
test_rows_of_one_table_always_have_the_same_columns` (1.2), so the schema and its minimal
consumers merge together as WP-A0 on day 1.

The integrator writes these complete, then hands them over:

| File | Day 0 content | Then owned by |
|---|---|---|
| `db/src/arcilogger_db/settings.py`, `settings_meta.py` | every key, meta text, bound, simple validator and error code of 2.5; the `nat.egresses` dispatch to `egress.validate_egresses`; the changed help texts. The legacy keys stay | frozen; WP-C1 removes the three legacy keys and their two validation branches |
| `db/src/arcilogger_db/egress.py` | the types (`EgressMode` with `internal`), the `NOT_PUBLIC` constant, `EgressValueError` and signatures of 2.8.1; `validate_egresses` checks only the outer type; `parse_egresses` returns `()` for `{}` | WP-C1 |
| `worker/src/arcilogger_worker/lookup/types.py` | every field of 2.10 with `from_dict` defaults (`LookupOutcome.unconverted_dhcp`, `LookupEgress.changes`, `NatMatch.untrusted_sources` included); `LookupSettings` new value fields with their defaults, the raw `nat.egresses` and the lazy `egresses()`; `from_settings(values)` keeps its values-only signature (the status rows are read by `run_lookup`, C1/C2); `lease_timeout_seconds(server, lease_seconds=None)` with the D6 formula (every caller passes `None` until WP-B, so nothing changes on day 0) | WP-C |
| `worker/src/arcilogger_worker/lookup/trust.py` (new, leaf: imports only `arcilogger_db.models` and `lookup.types`) | `untrusted_sources(session)` complete, the D24 address exception included (it reads only `sources`, which exists); `refusal`, `kept`, `usable_dhcp_origins` and `srcmac_ip_counts` as signatures that raise `NotImplementedError` (they need A0's `FgDhcp` and `FgTraffic.srcmac`); a unit test of `untrusted_sources` | WP-B (fills the other four after A0; C1, C2 and D only call them) |
| `worker/src/arcilogger_worker/lookup/texts.py` | every **new** key and text of 2.11, nothing else: the rewrites of `port_hint` (new `{egresses}` placeholder; texts are formatted eagerly and `engine.py:422` passes no params, so it lands with C1's engine), of `public_ip_not_transip` (C1) and of `no_lease`/`no_lease_for_mac` (C2, which also updates `worker/tests/test_devices_oui.py`, pinning today's wording) are not day-0 work | WP-C |
| `worker/src/arcilogger_worker/lookup/refs.py` | `FG_DHCP_TABLE`, `DHCP_TABLES`, `TABLE_RANK` (their only definitions, 2.6.1), `fg_dhcp_ref`, `dhcp_action`, `dhcp_ip`, `last_dhcp_evidence` (pure functions on refs, with unit tests) | WP-B |
| `worker/src/arcilogger_worker/testing.py` | `traffic(..., srcintf=None, dstintf=None, srcmac=None)`, where the defaults leave every existing raw line byte-identical (the `srcmac` argument is written to the raw line only; the row column arrives with A0) | WP-B, who after A0 writes `srcmac` (normalised) to the `FgTraffic` row as well, and adds `Evidence.fortigate_dhcp` |
| `web/src/lib/lookup-types.ts` | the TypeScript mirror of every 2.10 shape (`LeaseInfo`, `AttributionChain.mac_source`, `TrafficInfo`, `NatMatch.egress` and `untrusted_sources`, `LookupEgress` with the seven resolutions and the four modes), so the regenerated `lookups.json` passes `api-samples.test.ts` | the server-side merges in order (C1, C2, D) keep it in step; WP-E after D |
| `web/e2e/samples/*.json`, `web/src/generated/api-schemas.json` | regenerated (the new outcome fields with their defaults, the new settings) | whoever merges |
| `docs/CORRELATION.md` | §5 rows for the ten new keys | WP-F2 |
| `docs/adr/0016-fortigate-dhcp-and-internet-exits.md`, `docs/adr/README.md` | D1–D33 (sources cited as "FortiOS 7.6 …", 3.9) | integrator |
| `tests/test_docs_consistency.py` | the new ADR-0016 assertion of 3.9 | integrator (WP-B appends its UYARILAR assertion, 4.4) |
| tests | settings validation and meta, types round-trip, texts completeness (new keys) | as the files |

Done when:
- [ ] `wave-3` exists and every gate is green on it (`npm run check` and
      `capture_api_samples.py --check` included);
- [ ] `PATCH /settings` accepts and refuses the new simple keys with the codes of 2.5.

**TypeScript mirror types.** `web/src/lib/lookup-types.ts`, `sources-types.ts` and the observed
types in `api-types.ts` mirror server shapes, and the web gates compare them with the generated
samples and schema. They are therefore updated **in the same merge as the server shape**: day 0
for 2.10, WP-D for `GET /sources` `lease_evidence` and the observed endpoint (as Wave 2's WP5
sync did). WP-E consumes them and owns them only after WP-D merges; before that it works on hand
mocks.

### 4.3 The packages

#### WP-A — Ingest and data (backend, about 3 days; two merges)

- **A0 (day 1, before C1):** the schema with its minimal consumers, so every gate stays green:
  - `db/src/arcilogger_db/models.py` (`FgDhcp` appended to `TIME_SERIES_MODELS`,
    `FgTraffic.srcmac`, `Lease.lease_seconds`, both checks, `ix_leases_source_table_end`),
    `migrations/versions/v0017_fortigate_dhcp.py` (2.2.1, both modes), `db/tests/
    test_migration_0017.py` with the TimescaleDB `ADD COLUMN` test on a compressed chunk (V13;
    report the result in the merge description; take the fallback of 2.2.1 if it fails);
  - `db/src/arcilogger_db/kinds.py` (`KINDS["event"]` += `KindTable("fg_dhcp")`,
    `CLIENT_IP["fg_dhcp"] = "ip"`, the `dhcp` label), `db/tests/test_kinds.py`;
  - `ingest/src/arcilogger_ingest/parsers/fortigate.py`: the `mac` field kind, `FG_DHCP` with
    `device_time`, the routing rule, `FG_TRAFFIC` `srcmac` (2.3), with the synthetic fixture lines
    and `test_parser_contract.py` additions;
  - `api/src/arcilogger_api/search/fields.py` claims for `fg_dhcp` (`mac:`, `interface:`, `ip:`,
    `logid:`, `level:`) and `srcmac:`, and `api/src/arcilogger_api/search/testing.py`
    (`MESSAGE_TYPES`, `REQUIRED` for `fg_dhcp`);
  - **every test that pins the Wave 2 schema or search registry** (1.2), so A0 leaves every gate
    green: `db/tests/test_migrations.py` (`HEAD = "0017_fortigate_dhcp"`), `db/tests/test_cli.py`
    (the "15 tablo" assertions become 16) and `db/tests/test_partitions.py` (`tables == 16`),
    `api/tests/test_search_planner.py` (the `mac:` table tuples gain `fg_dhcp`),
    `api/tests/test_search_fields.py` (`CONTRACT_FIELDS` with `srcmac`/`kaynakmac`,
    `CONTRACT_INDEXED` with `fg_dhcp` under `mac` and `ip`, `logid`/`level`);
  - `docs/ARAMA.md`/`docs/SEARCH.md` §3 (`srcmac:` row) and §6 (the label "DHCP (MikroTik)" and
    the `type:event` pointer), which `tests/test_search_docs.py` compares with the catalogue;
  - `docs/DATA-MODEL.md` rows of 2.2.1.
- **A (rest, by day 3):**
  - parser and benchmark: the rest of `fortigate.py`, `parsers/values.py`,
    `ingest/src/arcilogger_ingest/testing.py` (benchmark mix `fortigate-only`),
    `ingest/tests/bench_throughput.py` (`--spool`), `ingest/tests/test_fortigate_dhcp.py` (new),
    and additions to `test_fortigate.py`, `test_fortigate_properties.py`;
  - fixtures: `tests/fixtures/synthetic/fortigate-synthetic.log` (append only), its
    `README.md`, `tests/fixtures/format/README.md` (2.3 wording);
  - db: further additions to `db/tests/test_migrations.py` (the `HEAD` line excepted, which C1
    sets to `0018_nat_egresses`, 4.1);
  - search: further `api/tests/test_search_fields.py` cases, additions to
    `api/tests/test_search_oracle.py`;
  - docs: `docs/PARSERS.md`, `docs/DATA-MODEL.md`, the rest of `docs/ARAMA.md`/`docs/SEARCH.md`,
    `docs/INGEST.md`.
- **Done when:**
  - [ ] A0: every gate green with the schema in, the pinned tests of the A0 list updated
        (`test_migrations` HEAD, `test_cli`/`test_partitions` 16 tables, `test_search_planner`,
        `test_search_fields`, `test_search_docs` with ARAMA/SEARCH §3 and §6); the v0017 upgrade
        and downgrade pass on plain PostgreSQL and on the Linux TimescaleDB job;
  - [ ] 3.1 and the WP-A part of 3.2 pass on both CI jobs;
  - [ ] the synthetic lines are appended with the README row;
  - [ ] the format-sample guard is skipped with its reason;
  - [ ] gates 1–2 of 3.8 hold (≤ 5 %), recorded in `INGEST.md`;
  - [ ] docs tests are green.

#### WP-B — Lease timeline and lease alerts (worker, about 3.5 days)

- **Owns:**
  - code: `worker/src/arcilogger_worker/correlation/dhcp_sources.py` (new, `load_rows` counting
    from the `trust.refusal` column), `lookup/trust.py` (after day 0: `refusal`, `kept`,
    `usable_dhcp_origins`, `srcmac_ip_counts`), `timeline.py` (D5 cross-table ACKs, D33 with its
    key on the configured network, its deadline, its MikroTik variant and nothing for an address
    in no configured network), `leases.py` (`Context.network_for`, the `_seeds` `last` of an
    `fg_dhcp` seed without traffic, the D33 variants in `_closes` and the look-ahead, the
    `high` of a rebuilt `fg_dhcp` seed, round 6), `watermark.py`, `late.py` (if needed), `lookup/refs.py` and `testing.py` (after
    day 0), `lookup/window.py`, `alerts/system.py` (the two checks `_lease_evidence` and
    `_nat_exits` in their own transactions: every `R20-lease` cause, the per-table `none`
    causes, `fortigate-renewals-not-logged` and `source-clock` (sources from `search_counts`,
    latest rows per source) included, and `R20-nat:conversion-failed` (which reads the status row
    C1's migration writes), `invalid`, `legacy-changed` (the `updated_at` condition, no status
    row needed) and `unmapped-transip`),
    `alerts/texts.py` (`RULES`, `_STEPS`, `LEASE_CAUSES`/`NAT_CAUSES` with the fixed one-line
    actions, the `_lease`/`_nat` describers, 2.6.3);
  - `worker/testing.py` after A0: `traffic()` writes `srcmac` (normalised) to the `FgTraffic` row
    as well as to the raw line (C2's srcmac goldens and the re-parse test need it);
    `Evidence.fortigate_dhcp(time, ip, mac, *, logid="0100026001", interface="vlan42",
    hostname=None, lease=86400, dhcp_msg="Ack", source="fortigate", device_time=True) -> int`
    (raw line in the spool in the fixture format, plus the row the parser would write), with the
    **Evidence re-parse test**: the line re-parsed by the shipped parser gives the written row,
    `fg_traffic.srcmac` included (it needs WP-A0's routing rule, so it belongs here);
  - tests: `worker/tests/test_timeline.py`, a timeline property test (new or extended),
    `test_leases_db.py`, `test_leases_fortigate.py` (new), `test_leases_consistency.py`,
    `test_late_evidence.py`, the watermark and window tests, the system-alert tests (the
    `R20-nat:conversion-failed` assertion moved here from C1's migration test), the
    alert-text and e-mail describer tests;
  - **the rollback step and its restore (2.2.1; round 6):** `db/src/arcilogger_db/lease_rollback.py`
    (new: `prepare_rollback(session, now)` and `restore_after_rollback(session)`), the `leases
    prepare-rollback` command of `db/src/arcilogger_db/cli.py` and the call in
    `db/src/arcilogger_db/seed.py` (both files handed over by WP-C1 after its merge, with
    `db/tests/test_cli.py`), `db/tests/test_lease_rollback.py` (new); the installer step is the
    integrator's;
  - docs: `docs/UYARILAR.md`, `docs/ALERTS.md`; **one edit** of `docs/ILK-ADIMLAR.md` and
    `docs/FIRST-STEPS.md` §9 (`R20-lease`, `R20-nat`, one line each) and the UYARILAR/ALERTS
    assertion appended to `tests/test_docs_consistency.py` (4.4), in the same merge as the
    constants.
- **Done when:**
  - [ ] every row of 2.6.2 is done (D33 in the timeline, the seeds and the look-ahead), and the
        grep test passes;
  - [ ] 3.3 passes, including live, range and single-day agreement for the FortiGate-only and
        mixed sites, with the lease varying between ACKs, a D33 move across midnight and a
        FortiGate lease ending after midnight under continuing traffic;
  - [ ] the job report carries every counter of 2.6.2;
  - [ ] every `R20-lease` cause and the four `R20-nat` causes work with their texts, severity,
        resolution and catalogue entries: an e-mail of each high cause names the rule
        ("Kira kanıtı sorunu"), the cause's fixed one-line action and counts, never
        `UNKNOWN_RULE`; the Security page shows the numbered runbook and the cause's what-to-do;
        a stored bad `nat.egresses` leaves every lease cause working;
  - [ ] the docs tests are green with ILK-ADIMLAR/FIRST-STEPS §9 and UYARILAR/ALERTS updated;
  - [ ] no existing lease test changed its expectations (MikroTik-only behaviour is identical);
  - [ ] the rollback-step tests of 3.3 pass (lowering, the restore of untouched rows only, the
        `high` of a rebuilt `fg_dhcp` seed).

#### WP-C — Lookup engine and internet exits (worker and db, about 4 days; two merges)

- **C1 (merge by the end of day 2, right after A0; it depends only on day 0 and A0's v0017):**
  - `db/src/arcilogger_db/egress.py` complete (the private/shared/reserved `public` rule, the
    mode `internal` and `setting_egress_isp_nat_direct` included), `db/tests/test_egress.py` (new) and the shared case
    list `tests/fixtures/egress-cases.json` (new);
  - `v0018_nat_egresses.py` (frozen converter and validator, the status rows
    `nat.egresses_migration` and `nat.egresses_changes`, legacy rows kept),
    `db/src/arcilogger_db/seed.py` (the one audit entry from the status row; no re-conversion,
    D18), `db/src/arcilogger_db/settings_admin.py` (`change_setting` maintains
    `nat.egresses_changes` with the fingerprint of mode, public, transips and public_dynamic,
    D32), `db/tests/test_migration_0018.py` (new; the status row's `conversion_failed` flag, not
    the alert);
  - the `HEAD` line of `db/tests/test_migrations.py` → `0018_nat_egresses` (a listed exception,
    4.1);
  - the removal of `nat.public_ips`, `nat.wan_transip` and `lookup.port_hint` (three
    `DEFAULT_SETTINGS` entries, two `validate_setting` branches, three meta entries);
  - `lookup/types.py` (`from_settings(values)` keeps the raw exits, `egresses()` parses them
    lazily; `public_ips`/`wan_transip` removed); `run_lookup` reads the two NAT status rows itself
    (2.10);
  - **the one listed line of `api/src/arcilogger_api/routers/lookup.py`** (4.1): `settings.
    egresses()` inside the existing 503 `try`;
  - a unit test in `test_lookup_types.py`: `from_settings` with a stored bad `nat.egresses`
    succeeds and only `egresses()` raises (the lease job and the usage subjects therefore need no
    change; WP-B's 3.3 test runs `rebuild_day` on such a value);
  - `engine.py` and `sessions.py`: resolution, filters, the generalised log check (the F rule
    and the own set of D15, private or shared addresses only and on E's interfaces, the
    overlap-aware `seen_overlap`), the unclaimed predicate over private or shared addresses only
    (O21), the fallback and O5 searches as ∪, the resolution order of 2.8.3 (the observed check
    before `unconfigured`; an `internal` exit's address answered without a search), the
    interface rule (dropped only on positive evidence: the exit's own addresses seen near T on
    none of its interfaces), `no_egress` listing every exit, the per-exit change warning (no
    history; every changed exit for an unclaimed P, `observed` left out),
    `egress_public_dynamic`, `egress_internal`, the own set of D15 (round 5: every other
    `isp_nat` exit without a public address left out; round 6: for an E with declared interfaces,
    every exit on disjoint declared interfaces left out too, per address; E-c4), the "seen, but
    not alone" fallback with its probe computing both facts and the reworded fallback texts
    (E-c5, round 6), and every exit note of 2.8.3; the rewritten `port_hint` and
    `public_ip_not_transip` texts in `lookup/texts.py`, with `test_lookup_engine.py`'s port-hint
    test;
  - `worker/tests/test_lookup_egress.py` (new): the **engine** parts of the E-cases of 3.4
    (E-a … E-o, E-n2 (both fixtures), E-e3, E-p1, E-q, E-j; E-p2 is C2's); their PDF assertions are WP-D's, in
    `test_report_pdf.py`;
  - **mechanical updates of every test and sample that set the legacy keys**, using
    `legacy_egresses`: `worker/tests/test_lookup_engine.py`, `test_lookup_types.py`,
    `test_lookup_assignments.py`, `test_lookup_performance.py`;
    `db/tests/test_settings_admin.py`, `test_cli.py`, `test_seed.py`, **`test_settings.py`**;
    the `db/src/arcilogger_db/cli.py` docstring example (`'{"hat-1": …}' | arcilogger-db settings
    set nat.egresses - --by tolga`);
    `api/tests/test_lookup_api.py` (its `"not an address"` case becomes a stored bad
    `nat.egresses`), `test_report_pdf.py`; `tests/test_mvp_end_to_end.py` (the NAT step only);
    `web/scripts/capture_api_samples.py` (the NAT settings only) and the regenerated samples;
    **`web/scripts/live_stack.py`** (sets `nat.egresses = legacy_egresses([], WAN, False)[0]`
    instead of `nat.wan_transip`, so the live smoke's exact WAN lookup stays exact);
    `web/e2e/mock-admin.ts` and `web/e2e/settings-application.spec.ts` (NAT cases replaced by one
    `nat.egresses` error case through the generic editor);
    **`web/src/pages/settings/setting-values.test.ts`** (its two legacy-key fixtures renamed to
    current keys of the same types); `web/src/lib/lookup-types.ts` (the `nat.public_ips` comment).
    The docs that explain the legacy keys (DATA-MODEL line 272 by WP-A after C1, KURULUM/INSTALL,
    ILK-ADIMLAR/FIRST-STEPS, CORRELATION, ASSUMPTIONS, MVP-CONTRACTS) are rewritten by their
    owners (2.15); no docs test requires a named key to exist meanwhile.
- **C2:**
  - `attribution.py` (`lease_seconds`, `last_dhcp_evidence` per lookup moment (the `fg_dhcp` row
    query, `refs.last_dhcp_evidence` as its fallback and for the other tables; never the line
    that ended the lease, never a later renewal), the post-lease `fg_event` check with
    `fortigate_dhcp_not_converted_since_lease` for every DHCP chain by IP or MAC in one batched
    query (2.7, D8; round 6) and its converted-row branch (`fg_dhcp` rows of the chain's MAC for
    another address, one query on `ix_fg_dhcp_mac_time`; D33, round 7), `fortigate_dhcp_no_network`
    (D33, round 6), `srcmac_chain`, the srcmac
    conflict, the one D27 rule through `trust.usable_dhcp_origins` with `dhcp_sources_overlap`,
    `lease_renewal_not_seen` lowering lease and chain to `low`, the format notes with
    `fortigate_format_verified` from the setting row `run_lookup` reads, `source_configured_later`),
    the srcmac paths in `engine.py` and `sessions.py` (IPv4-only guard, untrusted sources left
    out; the guard through `trust.srcmac_ip_counts`), `NatMatch.untrusted_sources` with
    `session_from_unknown_source` (E-p2), `chain_missing`, the D8 probe (FortiGate-sized window,
    the newest trusted DHCP-shaped row before T across `fg_event` and `fg_dhcp`, counting only
    an `assigned` `fg_event` row that covers T, one `unconverted_dhcp` citation per IP in
    internal mode and for exact matches only) and its one text, and the per-lookup clock
    measurement with `source_clock_offset` lowering every chain to `low`, measured for the
    searched sources when a search found nothing (2.7);
  - the rewritten `no_lease` and `no_lease_for_mac` texts, with `worker/tests/test_devices_oui.py`
    (which pins today's `no_lease` wording) updated in the same merge;
  - `types.py` and `texts.py` finalised;
  - tests: `worker/tests/test_lookup_fortigate_only.py` (the DECLINE, superseded-early,
    renewed-after-the-moment, clock (empty result included), rollback-window and D8-probe goldens
    included; every site written with `Evidence.fortigate_dhcp`, never with
    `api/lookup_testing.build_site`, which the worker cannot import), `test_lookup_srcmac.py`,
    additions to `test_lookup_egress.py` and `test_lookup_performance.py`.
- **Done when:**
  - [ ] C1: 3.2 (C part), the **engine parts** of the E-cases of 3.4, and E-j legacy
        equivalence against the expectations recorded from the Wave 2 tree pass; every gate is
        green with the legacy keys gone; samples are regenerated;
  - [ ] C2: 3.4 passes in full (engine and outcome; the PDF assertions follow in WP-D);
  - [ ] the perf guards of 3.8 for the lookup pass on both CI jobs;
  - [ ] every engine explanation has a test that emits it.

#### WP-D — API, report and samples (backend, about 2.5 days; after C2)

- **Owns:**
  - code: `api/src/arcilogger_api/reports/consistency.py` (the closing-line claims of 2.12),
    `reports/texts.py` (the exit label table, `READING_TABLE_KEYS`), `reports/render.py` (the
    section-4 exit row, method notes from the stored outcome only, the rendering of outcomes
    stored before the upgrade), `routers/lookup.py` (schemas, audit details and the 503 text
    only; C1's one line inside the `try` is its listed exception), `routers/settings.py`
    (observed endpoint through `trust.srcmac_ip_counts`, `exits_invalid`), `sourcehealth.py`
    (`lease_evidence`: `refused` from the live count, winning over `none`, which is read from
    the active alerts with `none_tables`), `routers/sources.py`, `lookup_testing.py`;
  - samples: `web/scripts/capture_api_samples.py` (after C1), `samples_*` helpers it calls,
    `web/e2e/samples/*.json`, `web/src/generated/api-schemas.json`;
  - tests: `api/tests/test_report_consistency.py`, `test_report_pdf.py` (after C1; the PDF
    assertions of the E-cases, E-c2, E-k2, E-k4 and legacy (d), and the device-identification
    raw-line test), `test_lookup_api.py` (after C1), the settings API tests,
    `test_sources_api.py`;
  - docs: `docs/MVP-CONTRACTS.md`, `docs/RAPOR.md` (with the reading table of 2.15),
    `docs/roles.md`.
- **Done when:**
  - [ ] 2.12 is complete and 3.5 passes (every method paragraph variant, no double-NAT paragraph
        for `transip`, the section-4 exit row with the shared labels, the match exit row and its
        unconfigured-sender warning, the "Oturum aranmadı" lines with the listed exits, the
        srcmac alternative header, the cited unconverted row with its "no chain" label and no
        citation in candidate reports, the verified-by sentence from the outcome, the D33
        closing-line claims, a Wave 2 outcome rendered without exit rows);
  - [ ] the observed and `lease_evidence` perf guards pass;
  - [ ] the samples of 2.12 are captured and `--check` is clean;
  - [ ] MVP-CONTRACTS §5 and §18 document every field, endpoint and code of this contract.

#### WP-E — Web (about 3 days; from day 1 on hand mocks written from 2.10–2.13; merges after D)

- **Owns:**
  - settings pages: `web/src/pages/settings/setting-editors.tsx`, `egress-editor.tsx`,
    `egress-model.ts`, `fortigate-observed-panel.tsx` (new, with tests),
    `settings-validation.ts`, `pages/settings/sources-page.tsx`;
  - Kaynaklar: `web/src/pages/sources/*` (`lease-evidence-card.tsx` new);
  - lookup and shared components: `web/src/pages/lookup/*`,
    `web/src/components/network/supported-topologies.tsx` (new),
    `web/src/components/evidence/evidence-labels.ts`;
  - types: `web/src/lib/lookup-types.ts`, `sources-types.ts`, `api-types.ts` (observed types)
    **only after WP-D merges** (4.2: until then the server-side merges keep them in step);
  - locales: `web/src/locales/areas/network/*` (new), and the four keys in
    `web/src/locales/tr.json`/`en.json`;
  - mocks and specs: `web/e2e/mock-admin.ts` (after C1), `mock-sources.ts`,
    `settings-application.spec.ts` (after C1), `lookups.spec.ts`, `sources.spec.ts`;
  - the alert-rule label map, if one exists.
- **Done when:**
  - [ ] every element of 2.13 works against the captured samples with strict bodies, the editor
        warnings, the mode `internal`, the prefill rule with its "not an internet exit" offer,
        the eight exit labels, the card's per-table `none` lines and `refused` precedence, the
        topology list in the order of KURULUM §0.1, the unconfigured-sender badge, the `low`
        lease step and the excluded-interfaces line included;
  - [ ] `egress-model.test.ts` passes on `tests/fixtures/egress-cases.json` (C1's file);
  - [ ] the vitest and Playwright specs of 3.6 pass, including axe;
  - [ ] `npm run check` is green.

#### WP-F — Load generator (F1), docs and end to end (F2) (about 3 days)

- **F1 (after A):** `tools/loadgen/arcilogger_loadgen.py`, `tools/loadgen/README.md`,
  `tests/test_loadgen.py`.
- **F2 (after E):**
  - tests: `tests/test_mvp_end_to_end.py` (after C1);
  - docs: `README.md`, `docs/KURULUM.md`, `docs/INSTALL.md`, `docs/DEVICE-SETUP.md`,
    `docs/CORRELATION.md` (after day 0), `docs/ILK-ADIMLAR.md`, `docs/FIRST-STEPS.md`,
    `docs/AYARLAR.md`, `docs/SETTINGS.md`, `docs/KULLANIM.md`, `docs/USAGE.md`,
    `docs/LISANS.md`, `docs/LICENSING.md`, `docs/ARCHITECTURE.md`,
    `docs/DENETCI-KILAVUZU.md`, `docs/KANIT-PAKETI.md`, `docs/legal/aydinlatma-metni-taslak.md`,
    `docs/legal/saklama-imha-politikasi-taslak.md`.
- **Done when:**
  - [ ] F1: 2.14 and the loadgen part of 3.7 pass, and the default digest is unchanged;
  - [ ] F2: both end-to-end tests of 3.7 pass on Linux;
  - [ ] the two replacement bullets and the "Desteklenmeyenler" paragraph are in KURULUM and
        INSTALL §0.1 word for word as in 2.15, the kept bullets unchanged; README has the short
        summary with the link; the §13 paragraph and the §16 rows are in;
  - [ ] DEVICE-SETUP §2 is numbered 2.1–2.6, with the NTP step in 2.1 (F21), 2.4–2.6 written
        with placeholders (`<firewall>`, `<ntp>` reused) and "Doğrulayın" marks, with the D29
        check by visible results (card and trial lookup, no Canlı Akış step) and its branches,
        the renewal check with its negative branch, the relayed-networks step, the re-check after
        FortiOS upgrades, the 2.5 statement of where device-identification fields appear, and the
        2.6 interface step; every text that cites them says "bölüm 2.4" / "section 2.4";
  - [ ] the §13 paragraph is the one of 2.15 (no `-RestoreDatabase` advice for exit changes; the
        post-upgrade gap of FortiGate-DHCP sites, the public-NAT-address and internal-NAT
        sentences, the re-upgrade paragraph with `R20-nat:legacy-changed` and the lease rebuild
        range included);
  - [ ] the legal drafts have the rows of 2.15, the device-identification-in-reports sentences
        included;
  - [ ] DENETCI-KILAVUZU has the reading table of 2.15 (TR and EN), and the reading-table docs
        test of 3.9 is green on it and on RAPOR;
  - [ ] CORRELATION §1, §2, §3, §7 and §8 are updated;
  - [ ] the grep rule of 2.15 holds;
  - [ ] docs tests are green.

#### Integrator (day 0 and about 1.5 days of integration)

- **Owns:** the day-0 files of 4.2 that it keeps, `docs/ASSUMPTIONS.md`, `CHANGELOG.md`,
  `tests/test_docs_consistency.py` (4.4), the consistency of the generated files, `web/e2e/permissions.spec.ts` (the observed endpoint:
  admin only), `web/e2e-live/smoke.spec.ts`, the "Deviations" folding, and (round 6) the
  rollback step `leases.prepare-rollback` of `deploy/windows/installer/Install-Arcilogger.ps1`
  (after WP-B's merge, which brings the command) with its `tests/test_installer_scripts.py`
  case (3.7).
- **Integration steps:**
  1. Check that every merge regenerated the contract and samples.
  2. Run gate 3 of 3.8 (the load-generator days) and record the numbers in `INGEST.md`. WP-A
     hands the file over for this one edit.
  3. Run every gate on Linux with TimescaleDB and on Windows: `poe lint`, `poe test`,
     `poe test-perf`, `npm run check`, full Playwright, both `--check` scripts, gitleaks, and
     the Windows installer job (the upgrade runs `v0017` and `v0018` on a database with legacy
     NAT values; round 6: before it, the integrator adds the rollback step
     `leases.prepare-rollback` to `Invoke-RollbackMode` with its test, and the job's rollback run
     shows the lowered count).
  4. Walk the paths by hand on a real stack fed by the load generator:
     - a FortiGate-only day: Kaynaklar card, then 5651 Sorgu internal and candidates, then PDF,
       then the evidence package verifies;
     - turn srcmac on: a static device shows "Kısmi kanıt";
     - a dual-WAN day: the observed panel, "Çıkış olarak ekle", a P2 lookup lists only `hat-2`,
       then PDF;
     - an upgrade with `nat.wan_transip` set: the exit `wan` appears, the audit entry exists
       once, the old row is kept unread, `nat.egresses_changes` holds the legacy author, and
       lookups give the same modes and sessions (the named E-j exceptions apart), now with
       `egress_public_unknown` on every lookup of an address no exit claims until the public
       address is entered (the §13 note says so); a moment before a switch to bridge mode (A
       quiet, only W active) still gives candidates over W;
     - a `-Rollback` without `-RestoreDatabase` after that upgrade: the installer prints and logs
       the count of FortiGate leases lowered (step `leases.prepare-rollback`, before
       `program.swap`); the previous version answers with its pre-upgrade NAT values (the kept
       rows), not as pure double NAT; on a FortiGate-DHCP day every FortiGate lease is `low`, a
       previous-version lookup of a FortiGate-DHCP address after a RELEASE and another client's
       ACK it cannot read gives the former holder at `low`, never `medium` or `high` (also after
       its hourly rebuild of today), and it closes the existing FortiGate leases by its configured
       times (2.2.1); an edit of `nat.wan_transip` in the previous version, then the re-upgrade:
       the upgrade output names the FortiGate leases restored to `high` and those still `low`,
       `nat.egresses` is unchanged, `R20-nat:legacy-changed` opens and closes once the exits are
       saved; the lease rebuild of the §13 range (rollback day minus the longest lease to the
       re-upgrade day) restores the FortiGate leases the previous version closed early or left
       `low`, and a lookup in the rollback window after a FortiGate DHCP line the previous
       version loaded for that address or its MAC (its RELEASE or another client's ACK) gives
       that lease's chain at `low` with `fortigate_dhcp_not_converted_since_lease`, with or
       without the rebuild;
     - a FortiGate source whose clock is set 8 minutes back, then 3 hours back:
       `R20-lease:source-clock` opens both times, a lookup in that time carries
       `source_clock_offset` with `low` chains, and the alert closes after NTP is switched on.
  5. Write ASSUMPTIONS (O1–O22, the 1.3 list, the SPEC 9.1 deviation of D10) and the CHANGELOG
     Wave 3 entry; no version bump.
  6. Merge `wave-3` into `main`.

### 4.4 Shared, append-only lines

- `docs/CORRELATION.md` §5 setting rows: day 0 adds them; WP-F2 owns the file afterwards.
- `docs/INGEST.md` Wave 3 table: WP-A writes the rows for gates 1–2; the integrator adds the row
  for gate 3.
- `tests/test_docs_consistency.py` (integrator): day 0 appends the ADR-0016 assertion; WP-B
  appends the UYARILAR/ALERTS assertion in its merge; the reading-table assertion is appended in
  WP-F2's merge, once both `RAPOR.md` (WP-D) and `DENETCI-KILAVUZU.md` (WP-F2) hold the table
  (3.9).
- `docs/ILK-ADIMLAR.md`, `docs/FIRST-STEPS.md` §9: WP-B adds the `R20-lease` and `R20-nat` lines
  in the merge that adds the constants (the docs test collects every rule constant); WP-F2 owns
  the files afterwards.
- `db/tests/test_migrations.py` `HEAD`: A0 sets `0017_fortigate_dhcp`, C1 `0018_nat_egresses`
  (4.1).

### 4.5 Merge order

```
day 0 ─> WP-A0 ─> WP-C1 ─> WP-A ──┬─> WP-B ─> WP-C2 ─> WP-D ─> WP-E ─> WP-F2 ─> integration ─> main
 (d0)    (d1)     (end d2)  (d3)   └─> WP-F1 (any time after A, before F2)
WP-A, WP-B, WP-C, WP-E and WP-F start on day 1 on the day-0 interfaces (B and C2 on A0's schema
as soon as it merges; E on hand mocks).
```

1. Day 0: settings, types, the **new** texts (no rewrite of an existing one), refs, the leaf
   `lookup/trust.py` (`untrusted_sources` complete), the egress stubs, `traffic()` arguments, the
   ADR with its docs-test assertion and the 2.10 TypeScript mirror; every gate green.
2. WP-A0 (day 1): the schema with its minimal consumers (models, v0017, kinds, the FortiGate spec
   and routing, search claims, `search/testing.py`) and every test and docs row that pins the
   Wave 2 schema or registry (`test_migrations` HEAD, the 16-table counts, the planner and field
   tests, ARAMA/SEARCH §3 and §6); every gate green. C1's v0018 builds on its v0017.
3. WP-C1 (by the end of day 2): the breaking settings switch with every mechanical update, the
   `HEAD` line for 0018, the one line of `routers/lookup.py` (503 for a bad `nat.egresses`), the
   `port_hint`/`public_ip_not_transip` rewrites with their engine, and the engine parts of the
   E-cases (E-p1; E-p2 waits for C2). It depends only on day 0 and A0, so it merges before the
   rest of A, and B, C2, D and E rebase once onto it.
4. WP-A (rest, about day 3): parser details, fixtures, search docs, the migration matrix and
   the throughput gate. The cross-check and the loadgen tests need the parser.
5. WP-B: the lease timeline (D33 on the configured network and its MikroTik variant included,
   the seeds' `last` and the `high` of a rebuilt `fg_dhcp` seed), the trust functions, the
   alerts with their catalogue, UYARILAR and the ILK-ADIMLAR/FIRST-STEPS §9 lines, and the
   rollback command with its restore in the seed (`db/cli.py`, `db/seed.py` handed over by C1).
   WP-C2's goldens need FortiGate leases.
6. WP-C2: srcmac, `chain_missing`, the D8 probe, the per-lookup clock measurement, the
   `no_lease` rewrites with `test_devices_oui.py`, all goldens.
7. WP-D: the PDF parts of the E-cases, regenerates every sample and brings
   `sources-types.ts`/`api-types.ts` in step.
8. WP-E: switches the mocks to the samples.
9. WP-F2: docs and end to end. F1 merges in between, at any point after A.
10. Integration (the installer's rollback step included, round 6), then `wave-3` → `main`.

**Manager decision, 2026-10-28 (shorter critical path):**
- **Start early and merge in order.** WP-D, WP-E2 and WP-F2 start now on the branch of the package they wait for. Their pull requests into `wave-3` merge in the order above.
- **F2 no longer waits for E2.** It merges right after WP-D, in either order with WP-E2: the two touch disjoint files. A doc that quotes a §2.13 web text follows the text as merged; if E2 deviates later, a one-line follow-up is acceptable.
- **The integration step** (#11 part 2) waits for WP-E2 and WP-F2 both.

---

## Deviations

Each package appends a section `## Deviations — <package>` here. In it, it records every
departure from this contract, with the reason, the tests that show it, and what the integrator
must fold into `MVP-CONTRACTS.md`, ADR-0016 or ASSUMPTIONS. Owner decisions taken differently
from section 0.2 are recorded here too, with the date and who decided.

## Deviations — contract (recorded at the 2026-10-27 revision)

- **SPEC 9.1, "FortiGate traffic … only proves the IP was active, not the MAC" (D10, O2).** The
  srcmac field of a FortiGate session record may name the MAC, device and person of a chain on
  listed, directly connected interfaces, at confidence `low` and labelled "kısmi kanıt"; it
  never opens or extends a lease. Fold into ADR-0016 and ASSUMPTIONS. Tests: 3.4 srcmac.
- **SPEC 9.1, traffic extends an open interval (D26, O14).** Kept for MikroTik leases; not
  applied to FortiGate leases by default, because SPEC 9.1 justifies it by RouterOS logging no
  renewals, which probably does not hold for FortiOS (F7, verified on site as V3). Fold into
  ADR-0016 and CORRELATION §1. Tests:
  3.3 "traffic never extends an FG interval by default".
- **Timeline rule 1 (CORRELATION §1, `timeline.py` docstring), extended for FortiGate (D33,
  O15).** An `assigned` closes every other open interval of its IP, as today, and for `fg_dhcp`
  also its MAC's open `fg_dhcp` interval on another IP of the same origin, interface and
  configured network (after applying that interval's own deadline; round 6: the network by
  subnet, with or without a VLAN number). Fold into ADR-0016, CORRELATION §1 and the
  timeline docstring. Tests: 3.3 "D33", "D33 after the deadline", 3.4 "DECLINE", "relayed
  networks".
- **Timeline rule 2 (continuation), per table (D5).** An `assigned` of a pair continues its open
  interval only when both come from the same DHCP table; from the other table it supersedes, as
  an `assigned` already ends an ARP interval of its pair. Fold into ADR-0016 and CORRELATION §1.
  Tests: 3.3 "cross-table ACK", 3.4 "migration keeps the IP".
- **Timeout rule (CORRELATION §1 table), for logged FortiGate leases (D6, O19).** An `fg_dhcp`
  interval with a logged lease times out `lease_seconds` after its last DHCP evidence, without
  the `lease.timeout_percent` margin that configured times keep. Fold into ADR-0016 and
  CORRELATION §1. Tests: 3.3 "a logged lease of 604800".

## Deviations — day0 (2026-10-27)

- **En dash in the new `SETTING_ERRORS` texts.** The texts of `setting_dhcp_msg_invalid` and
  `setting_interface_invalid` write "1-8", "1-32", "1-35" with a hyphen instead of the en dash of
  2.5: the repository's lint (ruff RUF001) refuses the ambiguous character in Python strings, and
  the existing texts write ranges with a hyphen ("1-65535"). Wording otherwise unchanged. Tests:
  `db/tests/test_settings_admin.py`. Nothing to fold.
- **English menu path in two new explanation texts (implementation note round 7 (b)).** The
  English texts of `fortigate_dhcp_not_converted_since_lease` and `fortigate_dhcp_no_network` name
  the networks page "Devices and people → Departments and VLANs" (the labels of
  `web/src/locales/en.json`) instead of the Turkish path, and the rebuild section "→ Rebuild the
  leases" (`en.json` `settings.leaseRebuild.title`, as `docs/SETTINGS.md`) instead of 2.11's
  "→ Rebuild leases" (review fix); the Turkish texts are unchanged. Tests:
  `worker/tests/test_lookup_texts.py::test_the_settings_path_is_the_one_the_web_interface_shows`,
  which takes every label of both paths from `web/src/locales/{tr,en}.json`. WP-B should use the
  same English labels in the `fortigate-dhcp-subnet` alert text (2.6.3 writes "Rebuild leases"
  twice), and WP-F2 in FIRST-STEPS (2.15). Fold into the 2.11 table.
- **Reading of 2.5 for `dhcp.fortigate_dhcp_msgs` (no change of a fixed value, recorded for
  review).** More than 16 logids is refused with `setting_dhcp_msg_invalid` (the one code the 2.5
  row names), although that code's text does not mention the 16-entry limit; the console text
  does. `nat.snat_trandisp` is de-duplicated before its 1-8 count; the interface lists are
  de-duplicated before their 32-entry count (`setting_too_many`).
- **Day-0 stubs that later packages replace** (as 4.2 describes; recorded so nobody reads them as
  final): `egress.validate_egresses` checks only the outer type; `egress.parse_egresses` returns
  `()` for `{}`, `ValueError` for a value that is no object, `NotImplementedError` for any exit;
  `egress.legacy_egresses` and the four row functions of `lookup/trust.py` raise
  `NotImplementedError`.
  `db/tests/test_settings_admin.py::test_every_code_of_the_catalogue_is_a_refusal` takes the exit
  rules' codes from `egress.ERROR_CODES` until WP-C1's `db/tests/test_egress.py` produces each of
  them; WP-C1 keeps `ERROR_CODES` equal to the codes its validator raises.
- **Names the contract leaves open** (JSON shapes unchanged): the raw value is
  `LookupSettings.nat_egresses`; the 2.10 parts are the dataclasses `EgressEntry` (with
  `from_egress`), `EgressSearched`, `EgressChange` and `LookupEgress`; `egress.IMPLICIT_CATCH_ALL`
  is the implicit exit (name `""`, mode `isp_nat`, round-5 note); `egress.PRIVATE_OR_SHARED` and
  `egress.RESERVED` are the two halves of `NOT_PUBLIC`; `egress.interface_name_ok` is the interface
  rule shared by the settings and the exit rules; `trust.REFUSAL_RULES` and `trust.CONFIGURATION`
  name the refusal codes of 2.6.1 in their order.
- **`legacy_egresses` returns `dropped` as `list[{value, reason}]`** (review fix): the shape of
  2.8.2's code (`reason` one of `invalid`, `ipv6`, `reserved`), superseding 2.8.1's signature
  `tuple[dict, list[str]]`. The day-0 stub is typed `tuple[dict[str, dict[str, Any]],
  list[dict[str, str]]]`; WP-C1's converter, v0018's frozen copy and the audit entry that names
  dropped values use this shape. Fold into 2.8.1.
- **The ADR-0016 members of `web/src/lib/lookup-types.ts` are optional** (review fix; option (b)
  of the review): `GET /lookup/{id}` returns `row.outcome` as stored (`LookupOut.of`), so a
  lookup stored before the upgrade has none of `LookupOutcome.egress`/`unconverted_dhcp`,
  `NatMatch.egress`/`untrusted_sources`, `AttributionChain.mac_source`,
  `TrafficInfo.srcintf`/`dstintf`/`srcmac` and `LeaseInfo.source_table`/`lease_seconds`/
  `last_dhcp_evidence` (only the PDF and the evidence package read the outcome through
  `LookupOutcome.from_dict`). The JSON the server writes is unchanged (2.10). **WP-E** reads an
  absent member as its 2.10 default (`?? null`, `?? []`, `?? 'lease'`) and tests the result page
  with such an outcome; **WP-D** keeps the router returning the outcome as stored (no
  `from_dict` round trip of a stored lookup); if WP-D chooses to normalise in `LookupOut.of`
  instead, it makes the members required again in the same merge. Test:
  `web/src/lib/api-samples.test.ts` "describes a lookup stored before ADR-0016". Fold into 2.10.
- **`LookupSettings.from_settings` reads the lookup-only values leniently** (review fix): a stored
  `nat.snat_trandisp`, `lookup.srcmac_interfaces` or `lookup.srcmac_max_ips` of another shape
  (written around the validator, CORRELATION §5) gives its shipped default instead of
  `ValueError`, because the lease job (`Context.load`) and the usage rollups call
  `from_settings` too (2.8.1: a bad NAT value never stops the lease timeline). The timeline's own
  `dhcp.fortigate_*` values stay strict. Tests: `worker/tests/test_lookup_types.py::
  test_the_lookup_only_settings_never_stop_the_lease_timeline`. Nothing to fold.

## Deviations — a0 (2026-10-27)

- **One assertion in a WP-E file** (`web/e2e/search.spec.ts`, "completes field names, then
  values the catalogue knows"): the completions of `kay` now include `kaynakmac:` (the alias of
  the new field `srcmac:`, 2.4), in catalogue order after `kaynakport:`. The spec pins the Wave 2
  field catalogue through the captured samples, so A0 had to change it to keep Playwright green
  (4.1 "every merge keeps every gate green"); nothing else in the web was touched. WP-E keeps
  the line when it rebases. Nothing to fold.
- **Compression of `fg_dhcp` follows the baseline's outcome** (2.2.1 "inside a savepoint exactly
  like `_hypertables`"): v0017 compresses `fg_dhcp` only when the `db.timescale` status says the
  baseline compressed (`compression: true`; an edition without compression compressed nothing,
  so `fg_dhcp` is not tried either). A refusal is recorded as `compression_reason` = `"fg_dhcp:
  <reason>"` in the status (raised in `require` mode), as the baseline records its own; the other
  keys of the status are unchanged. The downgrade drops that reason with `fg_dhcp`, and an
  upgrade that compresses `fg_dhcp` drops one left behind (a reason without the `fg_dhcp:`
  prefix is the baseline's and stays; a0 review fix). The storage itself is decided by
  `messages` being a hypertable (2.2.1), not by the status. Tests:
  `db/tests/test_migration_0017.py::test_a_refused_compression_of_fg_dhcp_on_timescaledb`
  (the policy statement is made to fail: `require` raises and stays at 0016, `auto` records the
  reason and leaves `fg_dhcp` uncompressed). Nothing to fold.
- **The status update** (round-6 implementation note on 3.2): v0017 updates the existing
  `db.timescale` row only (value, `updated_by = ':migration'`, `updated_at`); it never creates
  one, and the downgrade writes back the lists without `fg_dhcp`. The test asserts that the
  migration alone writes no settings row but this one and changes no key of the status but
  `hypertables`/`partitioned`. Nothing to fold.
- **Names the contract leaves open:** `models.LEASE_SOURCE_TABLES` (`mt_dhcp`, `mt_arp`,
  `fg_dhcp`; the check `ck_leases_source_table`) and `models.LEASE_SECONDS_RANGE` (300,
  8,640,000; the check `ck_leases_lease_seconds`), for WP-B's timeline to share;
  `fortigate.DHCP_KEYS` (the three routing keys; the 3.1 pin test can compare it with F1/F3);
  `fortigate._dhcp_table` and the verdict detail exactly as 2.3 writes it
  (`"DHCP event without a usable ip/mac: ip=<valid|invalid|empty>, mac=<valid|invalid|empty>"`).
  The 13 synthetic lines are appended after one `#` comment line naming ADR-0016 (every reader
  skips `#` lines, so positions of the earlier lines are unchanged); the fixture README row
  (an A-rest item) is written with them. Nothing to fold.
- **V13 result** (TimescaleDB 2.30.2, PostgreSQL 16.15, the 5433 test cluster, 2026-10-27):
  `ALTER TABLE fg_traffic ADD COLUMN srcmac macaddr` is accepted with a compressed chunk in
  place, the chunk stays compressed, its rows read `srcmac` NULL, a new row and a late row into
  the compressed chunk carry it, and the downgrade's `DROP COLUMN` works with the chunk still
  compressed. The 2.2.1 fallback (srcmac in `extra`) is not needed.
- **Round-7 implementation note "3.8 guard for the round-6 post-lease query" [WP-A, WP-C2]:**
  A0 adds no expression index on `fg_event` (`extra->>'ip'`, the normalised `extra->>'mac'`):
  the A0 schema is the fixed 2.2.1 list, and such an index would be built over every stored
  `fg_event` row inside the upgrade. WP-A's answer to WP-C2 is the note's other option, a UNION
  of the two branches (each on `ix_fg_event_subtype_time` with its own `LIMIT`); should the 3.8
  guard still fail with it, the indexes go into a new revision agreed with WP-A then, or the
  guard's expected cost is recorded in 3.8. Nothing to fold.

## Deviations — e1 (WP-E, first part, 2026-10-28)

- **"As converted" is judged by the converter's shape in the editor** (2.13, the converted-exit
  warning). `GET /settings` does not list the status row `nat.egresses_migration` (status rows
  are never listed, like `db.timescale`), so the browser cannot compare an exit with
  `migration.new`. `egress-model.ts` treats an exit as converted when it has the name and the
  fields `legacy_egresses` gives (`genel-<n>` direct without interfaces; `wan` with NAT
  addresses and `tanimsiz` without, both ISP NAT without public addresses or interfaces), and
  shows the warning only while a converted partner of the other kind is beside it. A new exit
  that happens to look exactly like a converted one gets the (advisory) warning too; an edited
  address with the shape kept does not end it. Alternative for WP-D: expose the converted exits
  (e.g. one read-only field of the `nat.egresses` setting) and let the editor compare. Tests:
  `web/src/pages/settings/egress-model.test.ts`, `egress-editor.test.tsx`. Fold into 2.13.
- **Where the exit editor's E2E runs.** `web/e2e/mock-admin.ts` and
  `settings-application.spec.ts` are WP-E's only after C1 (4.3), so the editor, the observed
  panel and their axe runs are in a new `web/e2e/w3-network.spec.ts` with its own
  `web/e2e/mock-network.ts` (the observed endpoint; one `PATCH /settings` refusal; every other
  change falls through to `mock-admin.ts`, which checks the body against the API's model). The
  contract's `lookups.spec.ts` is the repository's `lookup.spec.ts`. The second step may fold the
  network spec into `settings-application.spec.ts`. Nothing to fold.
- **Interim types until WP-D** (4.2): `lease_evidence` is declared in
  `web/src/pages/sources/lease-evidence-model.ts` (read through `leaseEvidenceOf`, so a server
  without the field shows no card) and the observed answer in
  `web/src/pages/settings/egress-model.ts`, exactly as 2.12 gives the JSON;
  `lib/sources-types.ts` and `lib/api-types.ts` were not edited. Both modules use relative
  imports because the E2E mocks import them. `web/src/lib/api-types.test.ts` lists
  `GET /settings/fortigate/observed` in `PENDING_ENDPOINTS` (the page calls it before the API has
  it); that test fails once the endpoint is in the OpenAPI document, so the entry goes with
  WP-D's merge. WP-E's second step moves the types and deletes the hand mocks
  (`web/src/test/wave3-mocks.ts`). Nothing to fold.
- **Hand case list for the exit rules** (`web/src/test/egress-cases.hand.json`, shape
  `{"cases": [{"name", "value", "code" | "normalised"}]}`) until WP-C1 writes
  `tests/fixtures/egress-cases.json`. Where 2.8.1 names no code the mirror chose: a field of the
  wrong JSON type (`public`, `transips`, `interfaces` not a list, `public_dynamic` not a boolean,
  an exit that is no object) `setting_egress_field`; a list entry that is no text
  `setting_egress_address`; `public_dynamic` true on `direct` `setting_egress_dynamic`, on
  `not_logged` `setting_egress_not_logged`, on `internal` `setting_egress_internal` (the code
  each mode's rule names); list limits counted after de-duplication (the lenient reading: a
  value the server refuses is shown at the editor level, never blocked in the browser while the
  server would take it); a prefix written with leading zeros (`/024`) read as `ipaddress` reads
  it; no netmask notation. WP-C1's fixture decides; the second step runs the mirror on it and
  aligns. Fold the chosen codes into 2.8.1 if C1 takes them.
- **`lease_evidence.<table>.last_hour`** (2.12 gives only `"…"`) is read as the time of the
  newest usable row within the last hour, `null` when none arrived in it ("Son kayıt {time}" /
  "Son bir saatte kayıt yok"). WP-D states its meaning in MVP-CONTRACTS §5.
- **Small additions:** the browser-side code `egress_duplicate_name` (two rows with one name, which
  JSON cannot express); the label `evidenceTables.fg_event` ("FortiGate olay kaydı") for the
  cited unconverted record (in the `network` locale area, beside the four changed keys); the
  "Kira kanıtı" line "sahada doğrulanmadı" shows while FortiGate DHCP rows arrive at all (used,
  refused or excluded); the FortiGate hint of Ayarlar → Kaynaklar is shown in the source dialog
  for the type FortiGate and under the table while a FortiGate source is configured; a match
  shows its exit row also when only `dstintf` or `egress` is set (a lookup without a public IP).
  Nothing to fold.
- **WP-E edits outside the 4.3 list** (e1 review): `web/src/pages/settings/settings-group-form.tsx`
  (the server's refusal codes by key, which the exits' editor shows; a key counts as changed only
  when its draft differs from the one the stored value opens with),
  `web/src/pages/settings/setting-values.ts` (the `toDraft`/`fromDraft` dispatch of
  `nat.egresses`, `Parsed.params`, the exported list splitter `stringListItems`) and
  `web/src/lib/api-types.test.ts` (`PENDING_ENDPOINTS`, the interim-types note above) are WP-E
  edits, as the `ServerSecondsEditor` dispatch is; no other package owns them. Fold: list the
  three files under WP-E in 4.3.
- **A stored exit is never read into something else** (e1 review, 2.13): an exit of the stored
  `nat.egresses` that the rules of one exit refuse (an unknown mode or field, a value of the wrong
  type, a name the rules refuse), or whose row would not give it back unchanged, is kept as stored
  (`EgressRow.stored`): shown read-only with the rule it breaks and its stored value ("Bu çıkış
  kaydedildiği biçimiyle düzenleyicide gösterilemiyor; …" / "This exit cannot be shown in the
  editor as it is stored; …", `network.egress.kept.*`), sent as stored in every save of the exits
  (so the mirror and the server refuse it there) until the administrator removes it, and left out
  of the warnings, the prefill and the observed panel's claims. A stored value that is no object
  is named above the rows (`network.egress.storedUnreadable`). Labels are sent as typed (rule 4
  does not trim) and interface names are entered one per line (a FortiGate interface name may hold
  a comma). The E2E observed mock answers `exits_invalid` whenever the exit rules refuse the stored
  value, as `parse_egresses` does. Tests: `egress-model.test.ts`, `egress-editor.test.tsx`,
  `w3-network.spec.ts`. Fold into 2.13.

## Deviations — c1 (WP-C1, 2026-10-28)

- **"Seen, but not alone" falls back only when nothing went through P at T** (D15, round-7 note
  "the trigger's scope", first alternative; restated by the c1 review fix). A pass "seen, but
  not alone" becomes the fallback only when no session of a trusted sender through P (any port,
  any destination, the requested protocol) **spans T itself** (`time − duration <= T <= time`)
  and the search on P would find no session (with a port: `transip = P AND transport = port`
  in the window; without one: `transip = P` in the window; both before any destination filter);
  otherwise the search on P runs and an empty answer stays `none` with `no_match_exact`. A
  session through P that ended before T or began after it (inside the tolerance of a change of
  the line's mode) no longer keeps the bare `none`; a P:port session inside the tolerance still
  gives the exact answer. The abandoned search adds no explanation (the plan is replaced before
  it runs). E-c5's main, mirror and found-match cases keep their answers. Tests:
  `test_e_c5_seen_but_not_alone_falls_back`, `test_e_c5_mirror_and_a_new_configuration`,
  `test_e_c5_a_line_that_carried_the_address_stays_exact`,
  `test_e_c5_a_moment_within_the_tolerance_of_the_switch`. Fold into D15/D18 and the round-6
  owner point ("near a change of a line's mode").
- **`public_unknown` keeps each exit's interface rule and O5.** The table row says "∪ U's
  transips"; the engine searches each exit of U on its own interfaces (the interface rule) and
  widens an exit whose NAT addresses are not seen near T to the unclaimed ones (O5), as it does
  for a `public` `isp_nat` exit. Tests: `test_e_e_the_exits_of_unknown_public_address_keep_their_interfaces_and_widen`, `test_e_k3_a_session_that_changes_exit`.
  Fold into the 2.8.3 table.
- **`double_nat` of a request without a public IP** (an internal-IP, MAC or destination-only
  lookup) is "no `direct` exit", Wave 2's "no public IP configured"; an unreadable `nat.egresses`
  counts as true (the router answers 503 only for public-IP lookups). Fold into 2.10.
- **An IPv6 public IP now lists no session at all, NAT66 included** (an E-j difference the
  API's 422 `ipv6_not_supported` makes unreachable): the unclaimed predicate covers IPv4 private
  and shared space only, so the NAT66 session (`fd00:0:0:42::57` → `2001:db8::44`) that Wave 2
  listed for `2001:db8::45` is no candidate any more; the Wave 2 family filter still keeps every
  IPv4 session out. `test_no_ipv4_session_is_a_candidate_for_an_ipv6_address` asserts `none`.
  Fold into the E-j exception list of 3.4 (line for the integrator: "an IPv6 public IP (refused
  by the API, 422): no session, NAT66 included, where Wave 2 listed NAT66 sessions") or into
  2.17.
- **`egress_unmapped_seen` leaves the requested address out only when it was answered as
  `observed`** (its own warning names it) and is given only while exits are configured. An
  `isp_nat` exit's public P that the FortiGate itself used near T (a line switched to bridge
  mode, the exit not edited) is not claimed, so the warning names it beside the candidate search
  over the exit's NAT addresses (c1 review fix; before it, such a lookup answered "nobody" with
  no warning). Tests: `test_e_m_an_unclaimed_nat_address_near_the_moment_is_named`,
  `test_e_m_an_isp_nat_lines_public_address_the_fortigate_used_is_named`. Fold into 2.11.
  **Open (owner, round-5 note "isp_nat public seen as transip"):** answering such a P as
  `observed` (exact with a port) with its own warning ("defined as ISP NAT, but the FortiGate
  itself used this address") needs a new explanation key (2.11) and `observed` with an
  `isp_nat` exit in `egresses` (2.10); not done in C1, both fixed interfaces.
- **`egress_changed_after_event`**: the changed exits are joined with "; " in `{changes_tr}` /
  `{changes_en}`; `:system` and `:seed` read "varsayılan değer" / "shipped default",
  `:migration` "yükseltme (eski NAT ayarlarından aktarıldı)" / "upgrade (converted from the old
  NAT settings)". An exit with no entry in `nat.egresses_changes` falls back to the `nat.egresses`
  row's time and author; for an address no exit claims, every current exit is considered beside
  the change record's names, so an exit written around `change_setting` gets that fallback too
  (c1 review fix; E-k4 case 4). `observed` is not an "unclaimed" resolution for the change list (round-5
  note, taken): an `observed` answer for an unclaimed P names nothing; one through a `not_logged`
  exit names that exit when it changed (D32 first case). Tests: `test_e_k4_…`. Fold into 2.10,
  2.11.
- **Status rows.** `nat.egresses_changes` is written by `:system` (`SYSTEM_AUTHOR`), one entry per
  exit `{fingerprint, changed_at, changed_by, deleted}`, `changed_at` the audit entry's UTC time.
  v0018 writes one entry per converted exit with `changed_at` = the newest administrator-written
  legacy row's `updated_at` (UTC ISO) and `changed_by` = that row's author; the migration status
  row keeps `{migration, from, new, dropped, conversion_failed, audited}`; `from` holds a legacy
  number canonical JSON refuses (a float, an integer beyond ±(2^53 − 1)) as its text, so the
  seed's audit entry never fails (c1 review fix; fixture row "g: numbers stored without
  validation", `test_numbers_canonical_json_refuses_never_stop_the_upgrade`). The downgrade rebuilds
  the legacy keys with author `:migration` (direct single addresses → `nat.public_ips`; one ISP NAT
  exit with one /32 NAT address and no interfaces → `nat.wan_transip`; a preserved exit →
  `lookup.port_hint`) and warns what it cannot carry (networks, other modes, public addresses of
  ISP NAT exits). Tests: `db/tests/test_migration_0018.py`, `db/tests/test_settings_admin.py`.
  Fold into 2.8.2/§13.
- **Validator codes where 2.8.1 names none** (`tests/fixtures/egress-cases.json`, 94 cases): a
  field of the wrong JSON type, `null`, an unknown field, a missing mode or an exit that is not an
  object → `setting_egress_field`; an address list entry that is not a text, netmask notation,
  host bits, an empty or /33 prefix, an octet with a leading zero, a network wider than /24 →
  `setting_egress_address` (a prefix with a leading zero, `/024`, is read as `ipaddress` reads
  it); an interface entry that is not a text → `setting_interface_invalid`; `port_preservation`
  `null` or unknown → `setting_egress_port_preservation`; a value that is not an object →
  `setting_type`; `public_dynamic` on `direct` → `setting_egress_dynamic`, on `not_logged` →
  `setting_egress_not_logged`, on `internal` → `setting_egress_internal`; a `not_logged` exit
  takes no port preservation either (only public addresses and a label); limits count after
  de-duplication. These match WP-E's hand list except interface entries (WP-E:
  `setting_egress_address`); WP-E's second step aligns on the fixture. Fold the codes into 2.8.1.
- **`setting_ip_invalid` is gone** with `nat.public_ips`/`nat.wan_transip` (its only users); the
  settings mock and the refusal spec use `setting_egress_public_private`. Fold into §5.
- **Explanation order:** the plan's notes (`egress_search`, `egress_public_unknown`,
  `egress_transip_not_seen`, `public_ip_not_transip`, …) follow the search notes (`double_nat`,
  `candidates_without_destination`, `port_hint`), as Wave 2 put its NAT notes. Nothing to fold.
- **E-q's "an `unconfigured` search never lists a DMZ session" is not exercised**: 2.8.3 is kept
  as written (an `internal` exit counts as an exit, round-5 note "resolution order on a site with
  `internal` exits only", second alternative); the E-q test says so. Fold: drop the clause.
- **Samples backdate the exits.** `web/scripts/capture_api_samples.py` enters `nat.egresses` with
  `updated_at` 2026-10-01 09:00 (site time), so the samples carry no wall-clock change warning and
  `--check` is stable. Nothing to fold.
- **Edits outside the C1 list, mechanical:** `db/tests/test_migration_0017.py` (WP-A) upgrades
  to its own revision (`revision=REVISION`, as `test_migration_0016.py` does) now that 0018 is
  the head; five calls, three of them TimescaleDB-only. Passed on PostgreSQL 16 and on the 5433
  TimescaleDB cluster. Nothing to fold.
- **Handover:** after this merge `db/src/arcilogger_db/cli.py` and `db/src/arcilogger_db/seed.py`
  go to WP-B (4.3).

## Deviations — b (WP-B, 2026-10-28)

- **ARP never moves the deadline of an `fg_dhcp` interval or seed (round-5 note "D6 / D26 / 2.6.2
  `_arp`", first option).** Without `dhcp.fortigate_traffic_extends`, `_arp` keeps the ARP ref and
  the sighting as evidence but does not move `last` of an `fg_dhcp` interval, as `_traffic` does
  (D26); the `_seeds` `last` of an `fg_dhcp` seed is therefore the maximum of `seed.start` and its
  **DHCP** refs and rows only (2.6.2 wrote "DHCP and ARP refs and rows"). So the deadline is "the
  FortiGate's own end" of D6 on a site whose ARP-LOG also lists a FortiGate-served VLAN, and live,
  range and single-day rebuilds agree. Tests:
  `test_timeline.py::test_traffic_never_extends_a_fortigate_lease_by_default` (its ARP part),
  `test_leases_consistency.py`. Fold into CORRELATION §1 (WP-F2) and the D6 text of ADR-0016.
- **Counters are counted once, in the run's primary window.** `load_rows(…, count=True)` adds to
  `ctx.stats` only for the window the run builds; the seeds and the look-ahead, which read rows of
  other windows again, call it with `count=False`. Every counter of 2.6.2 lands in the job report's
  `details.stats` (the existing counter map), `kept_fg_dhcp`, `refused_fg_dhcp` and
  `excluded_fg_dhcp` included. Tests: `test_leases_fortigate.py`, `test_dhcp_sources.py`.
- **Raw window of an `fg_dhcp` seed (round-5 performance note, option "bound per seed").** The lower
  bound is `max(seed.start, midnight − W)` with W the longest window the seed can have (the
  configured alive window, its stored `lease_seconds` and every lease its stored refs cite), instead
  of `seed.start`. A row older than that cannot touch the seed at midnight. Tests: the consistency
  feeds (lease varying between ACKs, a lease ending after midnight).
- **Lease of an `fg_dhcp` seed at midnight (round-5 and round-6 notes).** The usable lease of the
  latest kept `assigned` of its pair that logs one, from its stored refs (the lease in the ref
  summary) and the raw rows before the closing row, else before midnight; the stored value only when
  the seed has no such ref or row at all. When refs or rows exist but none logs a usable lease, the
  configured time applies (`None`), as the live interval had it, instead of round 5's "else the
  stored value": a later day's rebuild may have stored a newer ACK's lease. Tests:
  `test_leases_consistency.py`.
- **Origin of a seed (round-5 note "`run_incremental` seed origin").** Read for `rebuild_day` and
  `run_incremental` alike from the `vd` of the `fg_dhcp` row of the seed's newest own ref (by
  primary key, never the line that superseded it); `mt_dhcp` seeds have `(mt_dhcp, None)`. Tests:
  the DECLINE shape across a live cut in `test_leases_consistency.py`.
- **D33 network granularity (round-7 note).** `Context.network_for` keeps the most specific
  configured network holding the address (the existing `find_vlan` rule); no 3.3 case pins the least
  specific choice. The docs side is WP-F2's.
- **`lookup/trust.py` imports the table names from `lookup/refs.py`** (`DHCP_TABLE`,
  `FG_DHCP_TABLE`) for `usable_dhcp_origins`, so the grep rule's registry files share one spelling.
- **Check names.** The two checks are `check_lease_evidence` and `check_nat_exits` (registered in
  `CHECKS` as `lease_evidence` and `nat_exits`, each in its own transaction like every check), after
  the module's `check_*` convention; 2.6.3 calls them `_lease_evidence` / `_nat_exits`.
- **Describer facts.** `_lease`/`_nat` read only `cause`, `count`, `vlan`, `table` and `seconds`;
  each finding puts its main number into `details.count` (`rows`, `kept` and `shared` stay in the
  details for the Security page but are not printed). Tests: `test_alerts_lease.py`
  (`test_no_e_mail_or_report_line_carries_an_address_a_name_or_a_log_value`, the e-mail test).
- **`source-clock` (round-5 note).** `time <> time_received` is in the WHERE clause before the
  `LIMIT 200`, and the bound is the worker's `MAX_CLOCK_SKEW` (1 day), pinned equal to
  `arcilogger_ingest.parsers.syslog.DEFAULT_MAX_CLOCK_SKEW` by a test in `test_alerts_lease.py`.
- **`R20-nat:unmapped-transip`.** "Claims" means the public or a NAT address of any exit (`internal`
  exits included); only IPv4 `transip` values are counted. **`legacy-changed`** is also true when no
  `nat.egresses` row exists (a site with the shipped NAT defaults).
- **`fortigate-unverified`** looks at any `fg_dhcp` row of the last 7 days (2.6.3: "rows exist"),
  not only usable rows.
- **`trust.srcmac_ip_counts`** reads the `fg_traffic.srcmac` column only (a coalesce with `extra` is
  WP-C2's); `interfaces=None` counts every interface (round-5 note (d)).
- **Rollback step (2.2.1).** `prepare_rollback` sets `lock_timeout = 30s` and `statement_timeout =
  10min` (`SET LOCAL`, review note "2.2.1 rollback step … timeouts"); the command exits 0 with the
  count, 1 on a database error (a timeout included), 3 when the database cannot be reached. A step
  run again before the re-upgrade (a rollback retried after a failure, this version running in
  between) keeps the recorded `at`, lowers the leases still `high` with it and adds to the count, so
  the restore covers both runs (an upsert with a new `at` would leave every lease of the first run
  `low`). The restore gives the restored rows a fresh `updated_at`; a status row whose `at` cannot
  be read restores nothing and is deleted (the rows stay `low` until a rebuild). The rebuild range
  is `[site day of (at − max(lease_seconds of the fg_dhcp leases, else 604,800 s)), site day of
  now]`. `arcilogger-db upgrade` prints two Turkish lines with the English in brackets, as
  `search-counts` does; the first line of the upgrade output is unchanged. Tests:
  `db/tests/test_lease_rollback.py`; the rebuilt seed's `high` in `test_leases_fortigate.py`.
- **Docs.** UYARILAR/ALERTS §2 have one row per cause, keyed `` `R20-lease:<cause>` `` (with
  `:<tablo>`/`:<table>`, `:<ağ>`/`:<net>` or `:<arayüz>`/`:<interface>` where the dedup key has
  one), whose "what to do" cell starts with the cause's one-line action; the §2 opening names both
  rules. The assertion appended to `tests/test_docs_consistency.py` needed its import line widened
  (`LEASE_CAUSES`, `NAT_CAUSES`, `RULES`), not only an append.
- **Left to other packages, as the contract assigns them:** the "Kira kanıtı" card and its
  `source_configured_later` wording (WP-C2/WP-D), the web's per-rule labels (WP-E), the installer
  step `leases.prepare-rollback` (integrator), CORRELATION/KURULUM texts (WP-F2).

## Deviations — c2 (WP-C2, 2026-10-28)

- **A RELEASE mapped after the build ends the lease in the lookup** (2.7, round-6 note "a release
  mapped after the build", its second option). When the newest kept `fg_dhcp` row of a lease's
  pair in `[lease.start, at]` (the per-moment query) has a logid mapped to `deassigned`, the
  lookup treats the lease as not covering `at` (`Attributor.covers`): no chain from it, so an
  internal lookup answers `no_lease` and a match `chain_missing`, as after the rebuild. No new
  explanation key and no lowered chain with a text. The stored lease is unchanged; the usage
  rollups (no per-moment query) still read the stored lease until the rebuild. Tests:
  `worker/tests/test_lookup_fortigate_only.py::test_a_release_mapped_after_the_build_ends_the_lease`.
  Fold into 2.7.
- **D27 for a chain outside every configured network: the address stands for its network**
  (2.7, round-6 note "a network not entered on a mixed site", its suggested handling). The one
  `usable_dhcp_origins` query of a lookup also covers, for each chain whose IP lies in no
  configured network, that IP itself (as a /32 subnet), so a forgotten FortiGate DHCP server
  beside MikroTik DHCP on a subnet nobody entered lowers the MikroTik chain to `low` with
  `dhcp_sources_overlap` instead of leaving it `medium`. No new key and no extra query; only the
  `{network}` parameter differs from 2.11 there: it is the address (`172.20.44.30`), since no
  configured network has a name. Tests:
  `worker/tests/test_lookup_fortigate_only.py::test_two_origins_on_a_subnet_nobody_entered`.
  Fold into 2.7 and the 2.11 parameter cell.
- **The D8 probe also runs for a MAC-only internal lookup, keyed on the MAC** (2.8.3, round-6
  note "MAC-only internal lookups", its first option). When a request names only a MAC and no
  lease of it covers the moment (`no_lease_for_mac`), the probe looks for the newest DHCP-shaped
  row of that MAC before the moment instead of an IP's: `fg_event` rows by
  `lower(replace(extra->>'mac', '-', ':'))` (the form 2.7 uses) and kept `fg_dhcp` rows by `mac`,
  with the same window, sender filter and coverage rule. A counting row gives the same
  `fortigate_dhcp_not_converted`, `{ips}` = the address that row names (the requested MAC's own
  record; the MAC itself when the row's `ip` cannot be read), and is cited in `unconverted_dhcp`
  (internal mode). No new key, no text change, no extra query for any other lookup. Tests:
  `worker/tests/test_lookup_fortigate_only.py::test_a_mac_only_lookup_finds_the_line_by_its_mac`.
  Fold into 2.8.3.
- **The per-lookup clock check: the rows nearest T on each side, and the fixed limit for lease
  searches** (2.7 "Source clock at the requested moment", round-5 note "2.7 clock sample" and
  round-6 note "`source_clock_offset` of an internal lookup", their suggested handling). A
  source's sample is its `messages` rows with `time <> time_received` nearest T on each side
  within [T − 1 h, T + 1 h], at most 2,500 before and 2,500 from T (one `UNION ALL` query on
  `ix_messages_source_time`, median by `percentile_cont`), not the first 5,000 of the range. The
  limit is 120 s, not max(120 s, tolerance), for every source of an internal lookup and for a
  source with a cited `mt_dhcp`, `fg_dhcp` or `mt_arp` row, or searched for one of those tables:
  the lease search uses the moment, not the tolerance; a source measured only for the session
  search keeps max(120 s, tolerance). The offset compared with the limit is the whole number of
  seconds printed (the median rounded), so a named offset always exceeds `{limit}`. `{limit}`
  prints the smallest limit of the named sources. The 2.11 text is unchanged (its remedy "widen
  the tolerance by the offset" is not reworded for internal lookups). Tests:
  `worker/tests/test_lookup_fortigate_only.py::test_the_clock_is_measured_on_the_rows_nearest_the_moment`,
  `::test_the_clock_of_a_session_search_alone_may_reach_the_tolerance`,
  `::test_an_internal_lookup_without_a_lease_names_the_clock`,
  `::test_the_tolerance_widened_by_the_offset_finds_the_session`,
  `::test_a_cited_source_whose_clock_was_off_lowers_every_chain` (120.4 s and 120.6 s). Fold
  into 2.7, 2.15's reading table and 2.17.
- **The srcmac router guard counts a used row outside the hour by its own address** (2.9, the
  slice-4 review: a long session logged at its end, or a sighting up to the scan end, lies
  outside [T − 1 h, T + 1 h], so its srcmac was used uncounted). The one
  `trust.srcmac_ip_counts` call of a lookup and its `Attributor.leases_of_macs` call (the
  other-lease check) cover `[T − 1 h, T + 1 h]` exactly, for every MAC. A considered row (a
  srcmac row on a listed interface from a trusted sender: the matches' session rows, the
  internal sightings, the MAC-only rows) whose time lies outside that hour adds its own address
  to the count of its `(srcintf, srcmac)` unless the hour shows that address with that MAC
  already; one more query per lookup decides it (`sessions.srcmac_addresses_within`, through
  `ix_fg_traffic_srcip_time`, only when such a row exists). Every considered row belongs to a
  session overlapping the lookup window (tolerance ≤ 3600 s), so the address it adds was in use
  within ±1 h of `{at}`, and the `srcmac_shared` text stays true; no new key. No row widens the
  hour of another MAC: an earlier form of this fix widened the one window to every considered
  row, so one candidate's long session counted another MAC's ordinary address change hours
  later and dropped its D10 conflict (the lease chain kept its confidence); that form was
  withdrawn. The other-lease check is not widened for a row outside the hour: a lease covering
  a moment of the hour around T is what 2.9 asks. Refusing a row outside the hour was not
  chosen: such a row is the usual record of a long session, and refusing it would drop its D10
  conflict and its srcmac chain. Tests:
  `worker/tests/test_lookup_srcmac.py::test_a_shared_mac_logged_after_the_guard_window_is_still_counted`,
  `::test_another_long_session_does_not_widen_the_guard_of_a_mac`,
  `::test_an_address_the_hour_shows_already_is_counted_once`,
  `::test_the_other_lease_check_of_a_row_after_the_window_reads_the_hour`. Fold into 2.9.
- **The D8 probe window and the MikroTik cap of the `fg_event` post-lease check fall back to
  604,800 s** (2.8.3 "Cost", 2.7, 3.8; round-5 note "2.8.3 D8 probe cost", round-7 notes "3.8
  guard for the round-6 post-lease query" and "2.7 post-lease check, the MikroTik window cap and
  an empty `last_dhcp_evidence`"). `fg_event` has no index on a line's address (A0 adds none),
  so the probe and the round-6 post-lease query read every `system` line of their window.
  Measured by the 3.8 guards (`test_lookup_performance.py`, 50,000 `system` lines a day: a
  twentieth of 1 M FortiGate lines, the share the alert guard writes) on the 100-day window: the
  probe for 20 addresses without a DHCP row 2.1 s in its round-5 form and 0.64 s with the key
  read once per row (an `IN` before the per-key bounds, the round-5 note's form), the post-lease
  query for 100 chains 0.93 s; both miss 0.5 s. So the probe's window is `max(longest_window,
  604,800 s)` and the `mt_dhcp` cap of the `fg_event` post-lease query 604,800 s
  (`attribution.UNCONVERTED_WINDOW`), as 2.8.3 and 3.8 provide: 0.08 s and 0.09 s. The round-7
  converted-row query is not capped so: 3.8 gives it no fallback, and it keeps `at` −
  8,640,000 s for an `mt_dhcp` chain (`attribution.MISSED_MOVE_WINDOW`). On those 100 days it
  missed its 0.2 s too (0.89 s in its first form, 0.30-0.33 s with the rows grouped per MAC,
  because it fetched about 45,000 rows for 100 MACs at the load generator's FortiGate DHCP rate
  and matched them in memory), so it now matches each chain's own window in the query: one
  join of the chains' `(mac, ip, since, at)` (an `unnest` of bound arrays) with `fg_dhcp`, still
  `mac = ANY(:macs)` over the union of the windows on `ix_fg_dhcp_mac_time`, returning each
  other address, interface and VDOM once per chain; interface, VDOM and D33's key are checked
  in memory as 2.7 writes (0.08 s; the same answers). What the fallback costs: a pre-upgrade
  ACK more than 7 days before the moment is not named (`fortigate_dhcp_not_converted`); a
  MikroTik lease kept open by traffic is checked against the unconverted FortiGate DHCP lines
  of the last 7 days only. Two wrong-person paths follow, each named at the chain's stored
  confidence (`high` or `medium`) **without a warning**: (1) a client moved to another address
  by an unconverted `fg_event` ACK (before the upgrade, or in a rollback window) **that then
  left the site** logs nothing more, and its old MikroTik lease, kept open by another device's
  traffic, names it in every lookup more than 7 days after that line (under the contract, more
  than 100 days after; the round-7 note's residual, so a recorded residual of 2.17); a client
  moved by a **converted** ACK keeps the contract's 100 days through the round-7 query; (2) a
  FortiGate lease-time above the FortiOS default can leave such a chain without a line in the
  7 days even while the client stays on the site; with lease-times up to 7 days a client that
  stays logs a line at least every half lease (if FortiOS logs renewals, F7, sahada doğrula),
  so the window holds one, but a client that left logs none (path 1). An `fg_dhcp` chain's own
  window `(last_dhcp_evidence, at]` is not capped (at most half its lease while the chain is
  not `low`). The cost grows with the `system` lines of the window (about 0.13-0.19 s per
  million here): the 100-day window would pass below about 25,000 lines a day, and the fallback
  misses 0.5 s above about 400,000; expression indexes on `(extra->>'ip', time)` and on the
  normalised `extra->>'mac'` for `subtype = 'system'` (a WP-A revision) would restore the 100
  days and remove both paths' 7-day limit (a question to the manager in the pull request).
  Tests: `worker/tests/test_lookup_performance.py::test_the_d8_probe_of_twenty_addresses_is_fast`,
  `::test_the_post_lease_query_of_a_hundred_chains_is_fast`,
  `::test_the_converted_row_query_of_a_hundred_chains_is_fast` (100 days),
  `worker/tests/test_lookup_fortigate_only.py::test_a_mikrotik_lease_kept_open_reads_back_one_probe_window`
  (an `fg_event` line 8 days back not counted, 6 days back counted; a converted row 8 and 99
  days back counted, 101 days back not), `::test_an_ack_older_than_its_lease_is_not_named`
  (the 3.4 golden's 100 days, and 6 days inside the window by the coverage rule). Fold into
  2.7, 2.8.3, 2.17 (the residual of path 1), 3.8 and CORRELATION §7 (WP-F2: the window of the
  probe and of the `fg_event` post-lease check is 7 days; the converted-row check reads 100).

---

## Implementation notes — round 5

**Every implementation agent must read this section, check each note against the code it
writes, and handle the note when that is cheap and safe, or record in its package report why
not.** These are round-5 findings that are not blockers (no wrong person at medium or high
confidence without a warning, no unwarned false "nobody", no evidence change, no data shown to
the wrong role, no contradiction that makes the contract unimplementable), so they did not change
the contract. Each bullet gives the section, the problem and the suggested handling; the package
in brackets owns the code concerned.

- **2.6.2 `_seeds` lease at midnight vs `_assigned` [WP-B].** Live, a continuation keeps the
  previous `lease_seconds` when the new ACK has none (e.g. a renewal with `lease=0`), but the
  seed rule takes the latest kept `assigned` row of the pair, which then has none, so live and
  rebuilt deadlines differ. Take "the latest such row that carries a usable `lease_seconds`;
  else the stored value", and add that shape to the consistency feed.
- **D6 / D26 / 2.6.2 `_arp` on an `fg_dhcp` interval [WP-B].** `_arp` touches the pair's
  `fg_dhcp` interval (`timeline.py:449-454`), so on a site whose MikroTik ARP-LOG also lists a
  FortiGate-served VLAN the deadline moves to the last ARP sighting + `lease_seconds`, past "the
  FortiGate's own end" that D6 and the help text state. Keep a DHCP-only `last` for the
  `fg_dhcp` deadline (ARP stays evidence, as traffic does), or state in D6 and CORRELATION §1
  that ARP extends it; the `_seeds` `last` row must follow the same choice.
- **D33 MikroTik variant, VLAN comparison [WP-B].** `mt_dhcp` rows take their VLAN from the row,
  then `dhcp.server_vlan_regex`, then the subnet, while `fg_dhcp` rows take the subnet only; a
  regex VLAN that differs from the subnet's never matches, so the migration close never fires.
  Compare the subnet's VLAN on both sides for D33. *(Settled in the contract by round 6 blocker
  1: D33 compares the configured network's subnet on both sides, 2.6.1, 2.6.2.)*
- **2.7 clock sample [WP-C2].** "The first 5,000 rows" of [T − 1 h, T + 1 h] by device time cover
  only minutes after T − 1 h on a busy FortiGate, not the rows the search used, and a clock
  stepped inside the hour is then measured on the wrong side. Take rows on both sides of T (e.g.
  2,500 each) or [T − 10 min, T + 10 min]; the round-5 empty-result measurement uses the same
  query.
- **2.6.2 `_seeds` raw window [WP-B, performance only].** One `low = min(bounds)` for every seed
  IP (`leases.py:649-667`) with the `fg_dhcp` bound at `seed.start` reads every renewal of every
  seed IP since the oldest open seed's start, at each midnight and per day of a 31-day rebuild.
  Bound per seed by midnight − the largest lease seen, or query per seed bound.
- **D24 / D8 probe, refused unconfigured sender [WP-C2, optional].** Lookups in a window whose
  ACKs came from an unconfigured sender (HA member, changed `source-ip`) answer `no_lease` or
  `chain_missing` with nothing about it once the refused alert has resolved. Optional: an info
  text with the sender name and a count only (no MAC, no line).
- **D33 / 2.6.2 `run_incremental` seed origin [WP-B].** The `LeaseInterval`/`_Open` row of 2.6.2
  rebuilds a seed's `origin` "from the newest DHCP ref of a seed", but the table has no row for
  `run_incremental`, and the ref summary carries no VDOM. Read the `vd` of each open `fg_dhcp`
  seed's newest `fg_dhcp` ref
  row by its primary key `(time, id)` and pass it through `LeaseInterval.origin`; add to the
  `test_leases_consistency` feed a same-origin fg→fg move 4 s apart that straddles a live cut
  (the DECLINE case), so a missing origin goes red.
- **2.8.3 D8 probe after a settings fix without a rebuild [WP-C2 / WP-D].** A row refused when the
  leases were built and usable under today's settings is a converted `fg_dhcp` row, so the probe
  says nothing; `fortigate-refused` resolves on the settings fix alone, so once the operator skips
  the rebuild the alert and the card name it nowhere, and the lookup prints only `chain_missing`
  or `no_lease`. Cheapest safe options: keep a "rebuild the leases from {first_day}" line on the
  Kira kanıtı card until a rebuild covering `first_day` has run; or let the probe count a
  newest `fg_dhcp` row that `kept` accepts now, mapped `assigned`, covering T and inside no
  stored lease of its pair, with a fourth reason "the leases were not rebuilt after a settings
  or source change".
- **2.8.3 resolution, `isp_nat` public seen as `transip` [WP-C1].** Only a `not_logged` exit gets
  the `seen_overlap(P)` test in the `claims_public` branch; when an ISP switches a line to bridge
  mode and the `isp_nat` exit is not edited, P's exact session is logged while the answer is
  candidates over T2 (with `egress_transip_not_seen`, `egress_unmapped_seen` naming P). Apply
  the same test to `isp_nat` publics: observed, exact with a port, with a warning worded "defined
  as ISP NAT, but the FortiGate itself used this address".
- **D32 / 2.8.3 change warning [WP-C1].** "Nor does a change of another exit while P is claimed"
  ignores that other exits' `transips` decide own(E) of the D15 check (more so after round 5,
  where every `isp_nat` exit without a public address counts) and the unclaimed predicate of
  fallback, O5, catch-all and `unconfigured`. When the resolution ran the D15 check or searched
  the unclaimed predicate, also name every exit whose `mode` or `transips` changed after T.
- **2.12 fallback paragraph `{seen}` [WP-D].** `{seen}` is the unclaimed addresses seen, so when
  the failing address is claimed (legacy (d2)'s W; after round 5 also the NAT address of an
  `isp_nat` exit without a public address, E-c4) the PDF prints "(-)", or names a public address
  as private. For the fallback paragraph, set `{seen}` to the private or shared addresses outside
  own(E) that made the check fail; the probe already computes them. *(Settled in the contract by
  round 6 blocker 4: 2.8.3's probe returns those addresses, a `fallback` stores them in
  `searched.outside_seen` (2.10), and 2.12 defines `{seen}` so.)*
- **2.12 fallback addition text [WP-D].** "Converted at the upgrade and, with no interfaces
  defined, counted as the same line" is false for F's shared-interface branch. Use two variants:
  converted without interfaces, and shares interface {interfaces}.
- **2.12 `isp_nat` paragraph and `egress_search` under O5 [WP-C1 / WP-D].** "The search was
  limited to this exit's NAT addresses" and `egress_search` contradict `egress_transip_not_seen`
  and the candidates shown once O5 widens the search (without interfaces, other lines' users).
  Print `{searched}` in the paragraph and drop `egress_search` when O5 applies.
- **2.8.3 port hint vs match exit [WP-C1].** "A candidate whose exit has `preserved`" read as
  `NatMatch.egress` is null for unclaimed rows, so legacy (e) (`tanimsiz` catch-all, preserved)
  never gets +5 and E-j (e) goes red. Define the exit as the one whose search found the candidate
  (E, F or U), not `NatMatch.egress`.
- **D15 / 2.8.3 fallback search, E's own addresses [WP-C1].** "(P, or E's `public`)" can be read
  as all of E's public; the fallback for A1 of legacy (b2) would then list sessions logged as
  leaving via A5, which Wave 2 excluded. State that the search covers P, the requested address,
  never E's other public addresses.
- **2.8.3 / `sessions.build_query`, destination-only requests [WP-C1].** A request with a
  destination and no public IP (`types.py:235-238`) has no defined transip predicate after
  `exclude_transips` and the W narrowing are gone. Specify Wave 2's behaviour without the W
  narrowing: every session with a listed `trandisp`, `outcome.egress` None.
- **2.8.3 / 2.10 `IMPLICIT_CATCH_ALL` and F [WP-C1, day-0 types].** The implicit exit has no
  defined name, label, mode, `changes` entry or TypeScript form, and `fallback_exits(E)` could
  include E itself. Serialise it as name `""`, mode `isp_nat`, no change entry, and state that F
  excludes E.
- **2.12 `no_egress` text after an HA failover [WP-D].** An unclaimed dynamic P that only an
  unconfigured sender's rows show gives `no_egress`, and the PDF says P "does not appear in the
  FortiGate logs at that time either". Say "in the records of the configured sources", and when
  untrusted rows show P add a note in the `session_from_unknown_source` style.
- **2.13 observed panel "Çıkış olarak ekle" [WP-E].** For a new NAT address on an existing exit's
  `dstintf` (hat-2's modem now gives the FortiGate 100.64.13.10), a separate exit makes the
  address claimed, so hat-2's O5 widening never searches it and P2 lookups list nobody, with only
  `egress_transip_not_seen`. When the row's `dstintf` is an existing exit's interface, offer "add
  to that exit's NAT addresses" first.
- **2.12 Kira kanıtı card vs `dhcp.fortigate_logids = {}` [WP-D].** `refused_rows` counts rule
  4, so with FortiGate evidence switched off every row is refused and the card shows `refused`
  ("reasons are in the alert") while the alert is off, hiding a `none:mt_*` state. Count refused
  rows, and the card's `refused` state, only while the map is not empty (the alert's gate), and
  move the card assertions of 3.3 to 3.5 (the card is WP-D's).
- **2.17 vs 2.7 clock limit [WP-F2 docs].** 2.17 says a drift under 120 s does not lower a chain
  "unless the lookup tolerance is lower", which reads as a min(); 2.7 uses max(120 s, tolerance),
  under which it never does. Reword 2.17 (and CORRELATION) to max().
- **3.4 E-k4 and 2.10 vs D32 [WP-C1].** D32 warns an `observed` answer through a `not_logged`
  exit that changed after T (first case), while E-k4 says "an `observed` answer gives none
  whatever changed", and 2.10's `changes` lists changed exits for every unclaimed P, `observed`
  included. Restrict E-k4's clause to the observed answer for an unclaimed P and leave `observed`
  out of 2.10's "no exit claims P now" list.
- **D28 / 2.6.3 `source-clock`: `max_clock_skew` and the 200-row sample [WP-B].** The bound lives
  only in `arcilogger_ingest.parsers.syslog.DEFAULT_MAX_CLOCK_SKEW`, which the worker cannot
  import (it depends on core and db only); and receive-time rows have newer `time` than a lagging
  clock's rows, so the newest 200 by device time can all be receive-time rows, dropped, leaving
  fewer than 10. Move the constant to core (or define it in the worker with a root test pinning
  both equal) and put `time <> time_received` in the WHERE clause before `LIMIT 200`.
- **2.8.3 interface rule for a catch-all [WP-C1].** "E's own addresses" are its `transips`, which a
  catch-all lacks, so its interface condition is never dropped on a renamed interface and its
  searches find nobody (only `egress_unmapped_seen` hints). For a catch-all, take the unclaimed
  predicate as its own addresses in that rule.
- **2.8.3 D8 probe cost [WP-C2, performance].** Per-IP `LIMIT 1` scans make an IP with no DHCP row
  scan every `system` `fg_event` row of 100 days, 20 times; the round-5 post-lease query (2.7)
  uses the same branch over at most half a lease *(round 6: the post-lease query is now one
  batched query per lookup, 2.7, guarded in 3.8; this note stays for the no-chain probe)*. If the 3.8 guard is tight, use one query with
  `extra->>'ip' = ANY(:ips)` and `DISTINCT ON (extra->>'ip')`, or an expression index on
  `(extra->>'ip', time)` (a migration, so agree it with WP-A first).
- **Minor wording and leftovers.** (a) E-q says "searched … in the catch-all", but its site has
  none; the address is searched in the fallback's unclaimed part [WP-C1]. (b) 4.2 "§5 rows for the
  ten new keys" is stale; write "the new keys" [integrator]. (c) The clock check lowers chains but
  not `LeaseInfo.confidence`, so the PDF lease row can say `high` next to a `low` chain; lower both,
  as `lease_renewal_not_seen` does [WP-C2]. (d) `trust.srcmac_ip_counts` takes `interfaces`, but
  the observed endpoint counts every interface: type it `Collection[str] | None` [WP-B].
- **2.8.3 resolution order on a site with `internal` exits only [WP-C1, demoted class-5 claim].**
  `if not exits` counts `internal` exits, so a site that saves only a DMZ exit turns its implicit
  catch-all answers into a warned `no_egress`, and E-q's "an `unconfigured` search never lists a
  DMZ session" can never be exercised. It is consistent as written (warnings are printed, and
  `R20-nat:unmapped-transip` opens); the simpler reading is `internet = [E for E in exits if
  E.mode != "internal"]` for the `unconfigured` test, `egress_unmapped_seen` and the alert. Take
  it only together with an E-q line for a DMZ-only site, or keep 2.8.3 and drop that E-q clause.
- **2.15 / 3.9 `READING_TABLE_KEYS` [WP-D, demoted class-5 claim].** Its definition ("every key the
  PDF prints in its method section or beside a chain") read literally covers every explanation
  key. Define it as the explicit tuple of the backticked keys of the 2.15 table's first column
  (14 after round 5: `egress_public_dynamic`, `srcmac_only`, `srcmac_mismatch`,
  `fortigate_format_unverified`, `fortigate_format_verified`, `fortigate_dhcp_not_converted`,
  `lease_renewal_not_seen`, `fortigate_dhcp_not_converted_since_lease`, `dhcp_sources_overlap`,
  `source_configured_later`, `source_clock_offset`, `egress_internal`,
  `session_from_unknown_source`, `egress_changed_after_event`; 15 after round 6, with
  `fortigate_dhcp_no_network`), and let the 3.9 test check both directions (every tuple key has
  a row; every backticked key is in the tuple and in the texts).
- **D10 / 2.2.1 / 2.9 srcmac of rows loaded before the upgrade or in a rollback window [WP-C2].**
  Those `fg_traffic` rows keep srcmac in `extra` only (column NULL), so the srcmac queries see
  nothing and such moments answer `no_lease`/`chain_missing` (warned, never a wrong person). Read
  `coalesce(srcmac, <normalised extra->>'srcmac'>)` (the TimescaleDB-fallback normaliser of 2.2.1)
  in `srcmac_sightings`, the MAC-only query and `srcmac_ip_counts`, with the same guards; or say in
  §13 and the `no_lease`/`chain_missing` texts that srcmac of such rows is not used.
- **2.8.1 "NAT is parsed only where it is used" [WP-C1].** The router calls `egresses()` right
  after `from_settings` for every request, so with an unparsable stored value internal-IP and MAC
  lookups, which need no NAT, also answer 503, while the alert says only public-IP lookups stop.
  Call it inside the existing `try` only when `lookup_request.public_ip` is set.
- **2.2.1 and §13 "Geri alma" wording [WP-F2 docs].** The previous version closes an `fg_dhcp`
  lease at the stored last evidence + its configured window, which can lie in the past at its
  first run (not "in about 26 hours"), and its hourly and nightly rebuilds delete `fg_dhcp`
  leases that started today or yesterday. Reword: closed "about 26 h after the lease's last DHCP
  record, possibly already in the past", and deleted for leases that started on the rollback day
  or the day before; keep the rebuild instruction. *(Folded into the round-6 rewrite of 2.2.1
  and §13, blocker 5.)*
- **2.8.3 / 2.11 `nat_unknown_trandisp` values [WP-C1].** `{values}` prints log-supplied
  `trandisp` texts in lookups and the PDF from every source (the session search keeps untrusted
  ones), uncut and unmasked. Count trusted rows only, and print a value only when it matches the
  setting's pattern `^[a-z][a-z0-9+_-]{0,15}$`, otherwise "…".
- **§13 "Geri alma", reports rendered on first download [WP-F2 docs].** A Wave 3 lookup first
  downloaded under the previous version is rendered by the old renderer, which drops the
  untrusted-sender "UYARI" row, the "Kısmi kanıt" label and the exit and FortiGate lease rows (the
  stored explanation texts and `low` confidences still print). Add one sentence: download the
  reports of lookups made in the new version before running `-Rollback`.
- **2.16 scheduled reports [WP-B].** Scheduled HR and usage reports embed alerts through the same
  `describe()` (`reports/content.py:82-110`), so the `R20-lease`/`R20-nat` whitelist protects them,
  but nothing states or tests it. State it in 2.16 and add one describer-test assertion: a
  scheduled report's alert list carries no address, source name or log value for these rules.

Not listed here: the finding that an empty search is never clock-measured (2.7) is blocker 3 of
round 5 and is fixed in the contract; the finding that `refs.last_dhcp_evidence` has no
parameter types for mypy is fixed by blocker 1's typed signature (2.6.1).

## Implementation notes — round 6

**Every implementation agent must read this section, check each note against the code it
writes, and handle the note when that is cheap and safe, or record in its package report why
not.** These are round-6 findings that are not blockers (none names a wrong person or device at
medium or high confidence without a warning, answers a false "nobody" without a warning, changes
evidence, shows data to the wrong role, or makes the contract unimplementable), so they did not
change the contract. Each bullet gives the section, the problem and the suggested handling; the
package in brackets owns the code concerned. Two findings on the same token rule are merged into
one bullet (7); none was dropped.

- **D6 / D8 probe / 2.11, `lease=0` [WP-C2].** An always-connected client whose FortiGate lease was
  logged with `lease=0` loses its chain after the fallback time, and an internal lookup then
  prints only `no_lease` (info), whose advice (check that the DHCP server sends records) is wrong;
  the probe reads that converted row and says nothing (match lookups do print `chain_missing`, and
  `fortigate-lease-unlimited` is high). When the newest row is a kept `assigned` `fg_dhcp` row with
  `lease = 0` or out of range whose lease ended by the fallback time, add a short
  `fortigate_lease_unlimited` text naming that time, citing nothing.
- **2.8.3 D8 probe, refused `fg_dhcp` rows [WP-C2].** The probe's `fg_dhcp` branch filters
  `untrusted` only, so a refused row (a 26001 NAK if V14 shows FortiOS logs them, a bad MAC, a
  receive-time row) counts as "a newer converted row the timeline saw" and silences
  `fortigate_dhcp_not_converted` for a covering pre-upgrade ACK. Apply `trust.kept(settings,
  untrusted)` in that branch, or skip refused rows when choosing the newest row.
- **2.8.3 D8 probe, MAC-only internal lookups [WP-C2].** The probe runs per IP, so a MAC-only
  lookup for a moment whose ACK exists only as an `fg_event` line returns `no_lease_for_mac`
  (`engine.py:260-268`) with nothing about it, and O3's "the lookup says they exist" does not hold
  there. Add the same `fg_event` query keyed on the normalised `extra->>'mac'` (the form 2.7 uses),
  or a fixed sentence in `no_lease_for_mac` that FortiGate DHCP lines loaded before the upgrade
  are not searched by MAC.
- **D27 / 2.7 / 2.6.3, a network not entered on a mixed site [WP-C2, WP-B].** The lookup check and
  `dhcp-sources-overlap` work per configured network, so a forgotten FortiGate DHCP server beside
  MikroTik DHCP on a subnet nobody entered is never seen, and the last-logged server's client is
  named at `medium` with only the low `fortigate-dhcp-subnet` alert (round 6 lowers `fg_dhcp`
  chains there, not `mt_dhcp` ones; the topology is unsupported). For a chain IP outside every
  configured network, compare the origins of the usable DHCP rows of that IP itself within W
  (`(ip, time)` indexes on both tables).
- **2.7 per-moment `last_dhcp_evidence`, a release mapped after the build [WP-C2].** When the
  administrator maps 0100026002 after `fortigate-refused` named it and skips the rebuild, the open
  lease was built without the client's RELEASE, but the per-moment query (kept rows of either
  action) takes that RELEASE as the last evidence, and the chain stays `high` inside a lease that
  has ended under the current settings. When the newest kept row of the pair in `[start, at]` is
  `deassigned`, lower the chain with a short text (or treat the lease as ended there); otherwise
  read `assigned` rows only.
- **2.6.2 `_seeds` lease at midnight vs the closing check [WP-B].** "The latest kept `assigned` row
  of its pair before midnight" can belong to a new interval: seed (x, m) with lease 3600 and last
  ACK 20:00, n's ACK for x at 22:30 closes it, m's new ACK for x at 23:00 (lease 86400) opens
  another; the 86,400-s window makes the seed end `superseded` at 22:30 where the live run timed
  it out at 21:00 (`lease_renewal_not_seen` keeps the lookup `low`). Take the seed's lease from the
  latest kept `assigned` row before the closing row when there is one, else before midnight (and,
  with round 5's first note, one that carries a usable lease); add the shape to the consistency
  feed.
- **2.6.1 `refs.dhcp_action` / `dhcp_ip`, whitespace in the first token [day 0 `refs.py`, WP-B,
  WP-D] (two findings merged).** `summary.split()[1]` and `[2]` read the wrong tokens when the
  first token holds a space: a RouterOS DHCP server named with one (a quoted name such as "dhcp
  42"), or a FortiGate `interface`, a log-supplied string of up to 32 characters (F1), such as
  "port 1". `_ref_key` then loses the release rank, and the report cross-check reads "assigned"
  as the IP of a closing line and prints a false "ham satırda … uyuşmuyor" on a correct chain.
  Parse from the fixed `<action> <ip> for <mac>` part (the first match of ` (assigned|deassigned)
  <ip> for `), or keep the action and the IP as structured fields of the ref; when building
  `fg_dhcp_ref`, replace whitespace in the interface with `_`; add a test with a spaced interface,
  marked sahada doğrula.
- **D15 / 2.8.3 fallback of an E with declared interfaces [WP-C1] (with the optional part of round
  6 blocker 3).** The fallback searches E's own addresses ∪ F ∪ the unclaimed predicate, so the
  claimed addresses that made the check fail on E's own interfaces are never searched: legacy (d)
  with `wan1` entered on `genel-1` only (W is in G(E) and claimed by `wan`), and the blocker-3
  fixture (`100.64.12.9` claimed by `hat-2`), give a warned `fallback` with no candidate while
  `wan1`'s rows hold them (Wave 2 listed them in the first case). When E declares interfaces, let
  the fallback's unclaimed part be "the private or shared transips outside own(E)", on E's
  interfaces (the set the check used); narrowed to E's interfaces, it cannot list another line's
  sessions. The resolution table is fixed (4.1), so a package that takes it records a deviation.
- **3.4 E-k4 vs the round-5 note "D32 / 2.8.3 change warning" [WP-C1].** The note asks to name
  every exit whose `mode` or `transips` changed after T whenever the resolution ran the D15 check
  or the unclaimed predicate; E-k4 asserts that "a change of another exit while P is claimed gives
  none", so taking the note turns E-k4 red. If the note is taken, narrow E-k4's clause in the same
  merge to resolutions that ran neither the D15 check nor the unclaimed predicate (round 6's own
  set makes the note's case more common); otherwise keep both as written.
- **2.8.3 log check, `trandisp` of the D15 evidence [WP-C1].** Wave 2's evidence query filtered
  `trandisp = 'snat'` (`engine.py:535`); the probe's "private or shared transip outside own(E)"
  states no filter, while the unclaimed predicate uses `nat.snat_trandisp`, so two implementations
  can pass or fail the same check differently. Use `trandisp = ANY(nat.snat_trandisp)` there too
  (for the "seen, but not alone" evidence as well) and extend E-l to the check.
- **2.7 post-lease check, per-chain windows [WP-C2].** Round 5 asked for "one query per distinct
  IP" with the probe's newest-row `LIMIT 1`, but each chain has its own window
  `(last_dhcp_evidence, at]`, so the newest row over the union can miss an earlier chain's row.
  Settled by round 6 blocker 2 (one batched query returns the matching rows' times, and each
  chain is tested against its own window); check that the implementation does so.
- **2.7 / 2.8.3 post-lease check, the 20-IP cap [WP-C2].** "At most 20 IPs, as the probe" did not
  say which chains were checked, and chains beyond the cap (candidate lookups have up to
  `lookup.max_candidates` = 100) stayed `high` or `medium` with no text. Settled by round 6
  blocker 2 (one query, no cap); the 20-IP cap stays for the no-chain probe only.
- **2.7 / 2.11 `source_clock_offset` of an internal lookup [WP-C2].** The empty-result advice "widen
  the tolerance by the offset and run the lookup again" does not help an internal lookup, which
  uses `at` only (`engine._internal`), and with the limit max(120 s, tolerance) the re-run drops
  the warning and still answers `no_lease` (the 3.4 golden expects the same text there). Use the
  fixed limit for internal and lease-table measurements, independent of the request tolerance,
  and give the internal remedy as "run the lookup again at the moment shifted by the offset"
  (the 120-s value is an owner point and is not re-raised).
- **2.7 / 2.12 lease row confidence [WP-C2].** `fortigate_dhcp_not_converted_since_lease` (now for
  every DHCP chain) lowers only the chain, while `lease_renewal_not_seen` lowers the lease and the
  chain, so the PDF and web lease step can show the lease `high` beside a `low` chain (round-5
  note (c) for the clock check). Lower `LeaseInfo.confidence` too.
- **2.7 / 2.10 / 2.12 the lease time shown [WP-C2, WP-D].** The half-lease test and the `{lease}` of
  `lease_renewal_not_seen` use the logged lease of the record at the moment, while the PDF and web
  lease row print `LeaseInfo.lease_seconds`, the stored value of the latest ACK, possibly logged
  after the moment (3.3 varies the lease between ACKs); on the refs fallback path the record's
  lease sits only in the ref summary (`lease <n|->`). Fill `LeaseInfo.lease_seconds` from the same
  per-moment record (the summary on the fallback path), or state in 2.12 that the row shows the
  latest logged lease.
- **4.3 C2 / usage, cost of the per-moment queries [WP-C2, performance].** The per-moment `fg_dhcp`
  row query and the post-lease `fg_event` query live in `attribution.py`, and `usage/subjects.py`
  calls `Attributor.chain` for every piece of every IP of a day while ignoring confidence, so the
  rollup would pay both per chain (round 6 runs the post-lease query for `mt_dhcp` chains as well;
  `test_usage_performance` uses MikroTik DHCP, so no gate goes red). Run both only for lookups (an
  `Attributor` flag, or a pass in `run_lookup` next to D27).
- **3.4 E-c fixture [WP-C1].** After round 5 an `isp_nat` exit without a public address leaves
  own(hat-1), so E-c built with hat-2 lacking a public address (as E-c2 has it) gives `fallback`
  with `public_ip_not_transip` where E-c expects `none` and no `public_ip_not_transip`. State in
  E-c that hat-2 is `isp_nat` with public P2.
- **3.6 web tests [WP-E].** 2.13 requires the `fortigate_dhcp_not_converted_since_lease` text beside
  the lease step of a `low` chain, but 3.6's "lookup result rendering" item lists only the
  `lease_renewal_not_seen` rendering (round 6 added `fortigate_dhcp_no_network`; "tones for every
  new key" covers the tone only). Add the post-lease key to that item.
- **2.2.1 / §13 / 2.8.3 D8 probe after a re-upgrade without the rebuild [WP-C2, WP-F2].** A lease
  the previous version closed early or deleted leaves the moments up to the FortiGate's own end
  without a lease; when the newest DHCP line of the IP before T is the converted ACK (the client
  logged nothing during the rollback), the probe says nothing and the lookup gives a bare
  `no_lease`. The round-6 §13 text no longer claims otherwise ("Geri alma" names
  `fortigate_dhcp_not_converted` only for an unconverted covering line). Preferably apply round 5's
  suggested probe extension: count a kept, mapped `assigned` `fg_dhcp` row that covers T and lies
  in no stored lease of its pair, with a fourth reason "the leases were not rebuilt after a
  rollback or a settings change", so a skipped rebuild is never silent.
- **3.2 `test_migration_0017` "writes no settings row" [WP-A0].** 2.2.1 has v0017 append `fg_dhcp`
  to the `db.timescale` status, a settings row the baseline upserts with author `:migration`
  (`v0001_baseline.py:1413-1422`), so a literal before/after comparison of `settings` fails. Word
  the assertion "v0017 writes no settings row other than the `db.timescale` status update
  (`fg_dhcp` added; removed again on downgrade)".

## Implementation notes — round 7

**Every implementation agent must read this section, check each note against the code it
writes, and handle the note when that is cheap and safe, or record in its package report why
not.** These are round-7 findings on the round-5 and round-6 edits that are not blockers (none
names a wrong person or device at medium or high confidence without a warning, answers a false
"nobody" without a warning, changes evidence or blocks a 5651 function, shows data to the wrong
role, or makes the contract unimplementable), so they did not change the contract. Each bullet
gives the section, the problem and the suggested handling; the package in brackets owns the code
concerned. Line numbers refer to the round-6 text. Of 22 findings, three on the rollback step
are merged into one bullet, two on E-c5 into one, and two on the fallback wording into one; none
was dropped (18 bullets).

- **2.7 post-lease check, the MikroTik window cap and an empty `last_dhcp_evidence` [WP-C2].**
  The `fg_event` branch caps the window at `at` − 8,640,000 s (604,800 s under the 3.8 fallback)
  "for a MikroTik lease kept open by traffic", which is not defined, and gives no lower end when
  `last_dhcp_evidence` is None (`{last}` would print None). A client moved by a pre-upgrade
  `fg_event` ACK that then left the site (no converted ACK, so round 7's branch does not fire)
  is named at `medium` again by lookups more than 100 (or 7) days later. Apply the cap to every
  `mt_dhcp` chain, take `lease.start` as the lower end when there is no record (as round 7's
  branch does) and print the lease start for `{last}`, and record the residual in 2.17 (or keep
  the 100-day cap and drop the 7-day fallback for this query).
- **2.8.3 D8 probe bullet vs 2.7 [WP-C2].** The 2.8.3 bullet says any mapped row there "of either
  action" lowers the chain, while 2.7 exempts a MAC-matched row whose address lies in another
  configured network (the laptop on two VLANs). The error is in the safe direction. Add the D33-key
  exception to the 2.8.3 bullet, or point it to 2.7.
- **D33 key granularity: the most specific `vlans` row [WP-B, WP-F2].** The key follows the
  entered networks, not the DHCP scope. (a) A department /25 entered inside the FortiGate's /24
  scope makes D33 and the MAC rule treat a decline from the /25 to the /24 as two networks:
  nothing is closed and nothing is warned (round 7's branch skips it too, both networks being
  configured and different). (b) A single supernet 172.20.0.0/16 with no VLAN id over relayed /24s
  served on `internal` makes D33 cut a multi-homed NAS's chains at every renewal and fires the
  misleading `fortigate-decline`. Mostly pre-existing; round 6 extends it to networks without a
  VLAN number. Write "enter each DHCP scope as exactly one network" in ILK-ADIMLAR/FIRST-STEPS §5
  and DEVICE-SETUP 2.4; optionally compare the least specific containing network for D33, or add
  a 3.3 case that pins the choice.
- **2.2.1 rollback step: when it runs, failure paths, timeouts [WP-F2, installer; three findings
  merged].** (a) The step is unconditional: a later rollback between two versions that both read
  `fg_dhcp` (a Wave 3 patch rolled back to 3.0, a Wave 4 installer rolled back to Wave 3) lowers
  every FortiGate chain with no reason of its own, and nothing restores it there (the seed runs
  only in `migrate.upgrade`, `db/migrate.py:83-110`). (b) A rollback that fails after the step
  (the existing `rollback-swap` simulation: `Rename-ArciFolder` refuses; or any step before the
  swap) goes through `Complete-FailedChange -Operation rollback` (`Install-Arcilogger.ps1:2943-2989,
  3123-3138`), which restarts this version through `Restore-ArciRuntime` and prints "önceki
  durum geri getirildi [previous state restored]", while every FortiGate lease stays `low` and
  `leases.rollback_lowered` stays until the next `arcilogger-db upgrade`; this version's
  `run_incremental` keeps the stored `low` and may rewrite rows that are then never restored. (c)
  No lock or statement timeout is set while the collector is stopped. All are false lows only.
  Run the step only when the previous program cannot read FortiGate DHCP (its `arcilogger-db` has
  no `leases` command, or `<InstallDir>.previous\VERSION.json` predates the first Wave 3 version);
  on a failed rollback that keeps this version, undo it (a `$script:LeasesLowered` flag and, in
  `Complete-FailedChange` for `rollback`, `arcilogger-db upgrade` for the seed restore or a
  `leases restore` action), or at least print that `-Repair` restores them; set `lock_timeout`
  and `statement_timeout` on the command; add 3.7 assertions for the condition and for
  `rollback-swap`.
- **2.2.1 re-upgrade / §13, MikroTik leases a FortiGate ACK ended [WP-C2].** After a same-day
  rollback the previous version's recheck re-opens an `mt_dhcp` lease that D33's MikroTik variant
  had closed at a converted `fg_dhcp` ACK (HEAD's `_seeds` re-derives seeds spanning midnight from
  `mt_dhcp` rows only), and §13's "without the rebuild" sentence names FortiGate leases only; the
  round-6 post-lease check read `fg_event` only. Covered by round 7's converted-row branch (review
  note 1 of round 7): the converted ACK of the chain's MAC for another address of the same network
  lowers the re-opened chain to `low` with its text. Add this shape to the round-7 migration
  golden if cheap; no other change.
- **2.7 per-moment query and the empty-search clock, details [WP-C2].** (a) The `trust.kept(settings,
  untrusted)` query lives in the Attributor, but the contract does not say how the Attributor
  receives `untrusted` on the lookup and usage paths; (b) "at most 5 sources in all with the cited
  ones" does not say which sources win when there are more; (c) the restore compares `updated_at`
  with a timestamp kept in JSON. Pass `untrusted` into the Attributor constructor; rank cited
  sources first, then searched ones by row count; store `at` as an ISO string with microseconds
  and time zone and compare it exactly.
- **D15 "Seen, but not alone": the trigger's scope [WP-C1; see the round-7 owner point].** The
  trigger (P seen within ±15 min and an outside private or shared address seen) also holds when
  the second address is busy all day, not only near a change of the line's mode. On an upgraded
  legacy (d) site read as probe C (genel-1 direct 203.0.113.200 as line 1, `wan` `isp_nat`
  W = 100.64.13.9 as line 2, both busy, no interfaces), a request for `.200:51001` at 11:23:41
  with no such session (say the requester's clock is an hour off) becomes a fallback over `.200`
  ∪ 100.64.13.9 that lists every line-1 session through `.200` on other ports and line-2 users;
  the same on legacy (b) and on a new configuration without interfaces next to an `isp_nat` exit
  without a public address. While the FortiGate holds P at T, the other-port sessions through P
  cannot be the requested one. Every such answer is warned (`public_ip_not_transip`, and for F
  the "may contain that line's users" addition), but D18 and the round-6 owner point ("near a
  change of a line's mode") understate the scope. Fall back only when, in addition, no session
  through P overlaps T (the no-port `transip = P` overlap search is also empty, the condition that
  already makes the no-port form sound), otherwise keep exact `none` (E-c5's main, mirror and
  found-match cases still pass); or require that P's probe rows and the outside rows do not
  interleave in time. Either way restate D18 and the owner point to match.
- **D15 "finds no session" and the abandoned search's explanations [WP-C1].** "A search on P that
  finds no session" is not defined relative to the destination filter. On legacy (d) or (b), a
  request `.200:51001 → 203.0.113.10:443` at T, where the `.200:51001` session at T went to
  198.51.100.120 (port reuse, HEAD's `_port_reuse` fixture): HEAD's `_exact` finds the group and
  `_exact_to_destination` returns [] with `no_match`, `destination_not_found` and
  `exact_only_other_destinations`; read as "no session", it becomes a fallback to the destination
  over `.200` ∪ F/unclaimed, and a line-2 user who reached 203.0.113.10:443 is listed as a warned
  candidate. Nor is it said what happens to the explanations the abandoned search produced
  (`no_match`, `no_match_exact`, `no_match_candidates`, `exact_only_other_destinations`,
  `egress_search`; `engine.py:300-328` appends `no_match`/`no_match_exact` before returning [];
  `test_nothing_found_never_promises_a_list` guards the converse). Define "finds no session" as no
  group on `transip = P AND transport = port` (or `transip = P` without a port) before the
  destination filter, so a P:port session to another destination keeps `none` with
  `destination_not_found`; and state that a search turned into a fallback discards its own
  explanations (at most an info line that the exact search on P:port found nothing).
- **3.4 E-c5 "a new configuration" [WP-C1; two findings merged].** The golden explains the
  result by membership in G(E) ("it shares wan1, so it is in G(E)"), but G(E) only decides the
  check and F decides what is listed. Built with `genel-1`/`hat-1` direct `.200` and no
  interfaces plus an `isp_nat` exit [100.64.12.9] on `wan1` without a public address (legacy
  `wan` replaced), that exit is in G(E) but not in F (no shared interface, not as converted) and
  claims 100.64.12.9, so the fallback lists no candidate and "behaves the same" goes red. Write
  "hat-1 direct `.200` on `wan1` and hat-2 `isp_nat` `[100.64.12.9]` on `wan1`, no public; hat-2
  shares `wan1`, so it is in F ⊆ G(E), and the fallback lists the requester's session", and say
  whether legacy `wan` is kept.
- **D15 round-6 own-set rule, one side without interfaces [WP-C1].** (Pre-existing.) The round-6
  fix holds only when both exits declare disjoint interfaces: with hat-1 direct 203.0.113.200 on
  `[wan1]` and hat-2 `isp_nat` with public 198.51.100.77, transips `[100.64.12.9]` and no
  interfaces, line 1's modem reset to router mode hands the FortiGate 100.64.12.9 on `wan1`, which
  stays in own(hat-1) (hat-2 has a public address and declares no interfaces), so P1 quiet passes
  the check and P1 + port gives a bare `none`; the editor's missing-interfaces warning is not in
  the report. Optionally, when E declares interfaces, count a claimed private address on E's
  interfaces as evidence unless its claimant shares one of E's interfaces; otherwise state the
  limitation in 2.17 next to "Renamed interfaces and a double NAT at once".
- **D32 change fingerprint / 2.8.3 "Exits changed after the event" / E-k4 [WP-C1].** Since round
  5 (another exit's public address present or absent) and round 6 (the other exit's interfaces),
  own(E) depends on other exits' fields that D32's fingerprint and E-k4 ignore: after the §13
  interface step (hat-1 gets `[wan1]`, hat-2 `[wan2]`), a re-run lookup of a moment when hat-2
  declared none drops T2 from own(hat-1) and can change the D15 result with no
  `egress_changed_after_event`. This widens round 5's D32 note. If that note is taken, include
  the inputs of G(E) (the other exits' interfaces and public presence) and narrow E-k4 in the
  same merge; otherwise state the dependency in 2.17.
- **Wording of E-c5, E-c4, 2.10, 2.12 and the round-5 notes [WP-C1, WP-D; two findings
  merged].** (a) E-c5 asks for `public_ip_not_transip` "with the 'not consistently' wording",
  but 2.11 and 2.12 define "ya da yalnızca bir kısmında görünüyor" / "or only part of the time":
  use that. (b) 2.10 says a fallback's `egresses` holds the exit "whose check failed": write
  "whose check failed or passed seen-but-not-alone". (c) Both 2.12 fallback paragraphs say "So no
  exact match was possible" and, for the direct variant, that `{seen}` was seen "bu hatta / on
  this line"; after a seen-but-not-alone pass an exact search did run, and on a site without
  interfaces `{seen}` (`searched.outside_seen`) may be another line's NAT address (E-c2's
  no-interface variant puts hat-2's T2 there). Add a seen-but-not-alone variant ("the exact
  search on {address}:{port} found no session"), and say "bu hatta / on this line" only when E
  declares interfaces, else "FortiGate kayıtlarında / in the FortiGate records" (or leave other
  exits' claimed addresses out of `outside_seen` when E declares none; the check is unchanged).
  (d) Drop E-c4's stale reason "and genel-1 declares no interfaces". (e) Mark round-5 review note
  2 "(corrected in round 6)", as note 4 is.
- **2.8.2 equivalence rows (b) and (d) vs E-j [WP-C1].** Legacy (d) as converted, A + port asked,
  A and W both seen within ±15 min, no A session: Wave 2 answered `none`, round 6 gives
  `fallback` (E-c5); E-j lists it as an exception, but the 2.8.2 rows, against which E-j's
  recorded expectations are read, still say "(d) … A exact" with no "**differs**" marker; the
  same for (b) whenever a private transip is seen beside A. Add "**differs**: an empty A(+port)
  search while a private or shared address outside own(E) is also seen gives `fallback` (E-c5);
  listed in E-j" to rows (b) and (d).
- **3.4 "migration moves the IP before the upgrade" golden [WP-C2].** The expected
  `fortigate_dhcp_not_converted_since_lease` depends on D27 not firing first (the check runs only
  on chains still `high` or `medium`): with the default lease settings W = 86400 × 110 % = 95,040 s,
  so for T = 10-04 14:00 [T − W, T + W] ends at 10-05 16:24, before the first converted ACK at
  10-05 21:20; a configured MikroTik window of 31.3 h or more gives `dhcp_sources_overlap`
  instead. The golden also says "10-02 09:20" while the base golden has the FortiGate ACK on
  10-01 at 09:20. State in the golden that the site keeps the default lease settings, and align
  the date with the base golden.
- **2.2.1 re-upgrade counts and range [WP-B, WP-F2].** The seed "prints both counts, restored
  and still `low`, with the rebuild range of §13", but which "longest lease" the range uses is
  not defined, and `SeedReport` has no field for the counts or the range (`cli.py:159-185` prints
  settings, TimescaleDB and partitions only). Define `start = (at − max(lease_seconds of the
  fg_dhcp leases, else 604,800 s)).date()` and `end = today`; add `rollback_restored`,
  `rollback_still_low` and `rebuild_from`/`rebuild_to` to `SeedReport`, printed by `cmd_upgrade`
  when present.
- **3.8 guard for the round-6 post-lease query [WP-A, WP-C2].** One query for 100 chains,
  `extra->>'ip' = ANY(:ips) OR lower(replace(extra->>'mac','-',':')) = ANY(:macs)` over up to 100
  days of `fg_event` at 1 M lines a day, can use only `ix_fg_event_subtype_time`
  (`models.py:434`), so every `system` row in the window is scanned and even the 604,800-s
  fallback may miss 0.5 s (round 5's D8 probe-cost note with one more OR branch). Agree with WP-A
  before C2 on expression indexes `(extra->>'ip', time)` and `(lower(replace(extra->>'mac', '-',
  ':')), time)` for `subtype = 'system'` (a migration), or a UNION of the two branches; otherwise
  record the guard's expected cost in 3.8. Round 7's converted-row branch reads `fg_dhcp` on
  `ix_fg_dhcp_mac_time` and is not affected, but round 6's note "4.3 C2 / usage, cost of the
  per-moment queries" applies to it as well: run it for lookups only, like the other two.
- **2.6.3 / 2.11 / §13 texts [WP-B, WP-C2, WP-F2].** (a) The `fortigate-dhcp-subnet` one-line
  action said "VLAN'ı ekleyin / add the VLAN": reworded in round 7 (the network, even without a
  VLAN number, and the rebuild); the title still says VLAN, which may stay. (b) The EN texts of
  `fortigate-dhcp-subnet`, `fortigate_dhcp_no_network` and, since round 7,
  `fortigate_dhcp_not_converted_since_lease` give the Turkish menu path "Cihazlar ve Kişiler →
  Departmanlar ve VLAN'lar (→ VLAN ekle)", while other EN texts use English labels: use the
  labels of `web/src/locales/en.json`. (c) §13 says "yaklaşık 26 saat" without "varsayılanlarla"
  (26.4 h holds only for 86400 × 110 %) and names deletions for the rollback day and the day
  before only, while 2.2.1 also names late-evidence rebuilds: restore "varsayılanlarla / with the
  defaults" and add those rebuilds.
- **2.15 DATA-MODEL row ownership; 2.12 after a re-upgrade without the rebuild [WP-B, WP-F2,
  WP-D].** (a) WP-A (merge step 4) owns the DATA-MODEL.md line for `leases.rollback_lowered`, a
  status row WP-B (step 5) creates; no gate fails, but give the line to WP-B or WP-F2. (b) After
  a re-upgrade without the §13 rebuild, a chain on a lease the previous version rewrote stays
  `low`, and the PDF and web show "güven: düşük" with no explanation key or reading-table row.
  Optionally add a short info key (e.g. `lease_lowered_at_rollback`, set when an `fg_dhcp` lease
  is `low` and its row predates the restore), or say in 2.12 that such chains print the §13
  rebuild advice.

---

## Review notes — first round (2026-10-27)

Findings of the review that were not taken over as proposed, or only in part, and why. Every
other finding was verified against the code and the saved sources and folded in.

1. **F11 citation (srcmac texts finding).** Not taken: KB 98534 *is* among the saved pages; it is
   `fgres/cm/faz196890.html` (topic id 98534 in the page). The rest of that finding (texts no
   longer say "same L2 network", `srcmac_max_ips` default 1, the other-lease refusal, the
   two-client site check) is taken over; F11 now names the file.
2. **"Drop W from the direct exit when it equals `wan_transip`" (blocker on >8 exits).** Not
   taken; the other blocker's rule is used instead: no `wan` exit when `wan_transip` is one of
   the public IPs. Dropping W from the direct exit would leave no direct exit for a bridge-mode
   site and turn its exact answers into `isp_nat` answers with `double_nat` true and a
   double-NAT paragraph that is false for it. The one-direct-exit conversion, the frozen
   validator, `{}` on failure (named `conversion_failed` and alerted rather than `lossy`) and the
   decoupling of the lease job from NAT are taken over.
3. **`devid` in the two-authorities check.** Not taken: an HA failover changes `devid` while the
   same DHCP service continues, so comparing it would raise false alerts and lower good chains.
   `vd` and the source table/sender are compared (D27, V7).
4. **"Resolve the exits in force at T from the audit history" (exits not time-scoped).** Not made
   the default: it would also replay an administrator's corrected mistakes onto old events, which
   is a product choice; it is the O9 alternative. The minimum (`egress_changed_after_event` in the
   outcome and the PDF, with a test) is taken, using the setting row's `updated_at` (v0018 carries
   the legacy rows' last change) instead of scanning the audit log.
5. **Site-level "public list is complete" switch (dynamic public IPs).** Not taken; the per-exit
   `public_dynamic` flag is narrower (a static line never receives another address's question).
   The observed-transip answer for direct lines is taken over without any setting (D31).
6. **Fallback of a failed direct check searching "the unclaimed set" and the public-unknown
   exits together.** Taken in a narrower form: when a qualifying public-unknown exit exists
   (shared interface, or no interfaces declared anywhere) only that exit is searched, otherwise
   only the unclaimed addresses seen. Searching both would change legacy case (d), which the
   finding asked to keep equivalent.
7. **"Never use the logged lease on IPsec interfaces".** FortiGate DHCP log lines do not say
   whether a server is `server-type ipsec`, so this cannot be automatic: the administrator lists
   such interfaces in `dhcp.fortigate_interfaces_excluded` (DEVICE-SETUP 2.4), and their rows are
   not used at all. "One MAC with more than N IPs at once" raises an alert (more than 3 open
   leases) but does not refuse rows, since the threshold is not confirmed by any source.
8. **`lease = 0` treated as "until superseded or released".** Not taken: it would keep a
   departed client's chain open without bound, the problem of the traffic-extension finding. The
   alert variant (own text "süresiz kira") is taken.
9. **Traffic capped at "last DHCP evidence + lease_seconds".** Taken as "traffic never moves the
   deadline of an FG interval" (default), and applied to FortiGate intervals with a fallback
   lease time as well, not only a known one. *(Corrected in round 3: this note called it
   equivalent because the DHCP deadline, lease × `timeout_percent`, is never earlier than the
   server's lease end. It was later than that end by the margin, so it was not equivalent; a
   logged lease now ends exactly at `lease_seconds`, D6, round 3 note 2.)*
10. **Placeholders.** `<firewall>` is reused instead of `<fortigate>`/`<fortigate-arayüz>`; the
    new `<arayüz>` (an interface name) is kept, because it is not the firewall's address and
    DEVICE-SETUP has no interface placeholder today.
11. **DEVICE-SETUP step "send a line to the support/verification list".** Dropped rather than
    naming a channel: no such channel exists in the documents. The step now says to mask MAC,
    host name, IP, `devid` and `devname` before any line leaves the organisation.
12. **Day-0 breakage options.** Neither option was taken literally: the schema moves out of day 0
    into WP-A's first merge A0 together with its minimal consumers (kinds, spec and routing,
    search claims, `search/testing.py`), and the Evidence re-parse test moves to WP-B. For the web
    gates, day 0 writes `lookup-types.ts`, and WP-D keeps `sources-types.ts`/`api-types.ts` in step
    (a mix of the two options).
13. **Duplicates.** The ADR release-string findings, the `settings.read` findings, the
    `fortigate_dhcp_not_converted` findings and the two C1 mechanical-list findings were merged.

## Review notes — second round (2026-10-27)

All 45 findings were checked against the code at HEAD and the saved Fortinet pages
(`fgres/cm/faz196890.html`, `user155540.html`, `l3sw201683.html`); every one described a real
gap. Most were taken over as proposed (section 1.2 lists the facts they rest on). The ones below
were taken in another form, in part, or merged, and why.

1. **D28 "accept receive-time rows from a source whose lines are all receive-time with a stable
   offset"** (in the refused-evidence finding). Not taken: a replay or a forwarding relay also
   produces a stable offset, the receive time carries collector and queue delays, and a lease
   event at the wrong moment supersedes the right holder at `high`. The case is made loud instead:
   `R20-lease:fortigate-refused` (high) opens when nearly every row is refused, and its text names
   the FortiGate clock. The rest of that finding (usable evidence, a `refused` card state,
   `lease=0` high) is taken.
2. **One origin definition for two DHCP authorities.** Taken, but as `(table, vd)`, not
   `(table, source, vd)`: HA members that send from their own addresses arrive as two sources (a
   network source files them as `fortigate-<ip>`), so comparing the sender would raise the alert
   and lower good chains on every failover, the reason `devid` was already left out (O16). The
   lookup-wide `low` while the overlap holds is taken (`dhcp_sources_overlap`).
3. **Overlap-aware log probe.** Taken for the observed check (D31 (a), the `not_logged` case),
   where P is an address no exit claims and has few rows. Not taken for the D15 check of an
   exit's own, busy address: an overlap scan over T + `max_session_seconds` can read a day of that
   address's rows at a quiet moment. The finding's second option is taken there: the fallback and
   the O5 widening always search E's own addresses ∪ the unclaimed predicate over the bounded
   search window, so a long session is still found.
4. **Field path in the 422 detail.** Not taken: it changes the shape of every `PATCH /settings`
   error and its TypeScript mirror for one editor. The server error is shown at the editor level,
   field hints come from the client-side mirror, and both suites read one JSON case list
   (`tests/fixtures/egress-cases.json`), the finding's other option.
5. **Help text per kind.** Not added to `Kind` (an API and mirror change for one sentence); the
   pointer lives in ARAMA/SEARCH §6 only, the finding's first option.
6. **The no-interface fallback only for converted exits.** Taken with the status row as the
   marker: an exit counts as *as converted* while every field but its label equals the exit of
   the same name in `nat.egresses_migration.new`. A stored `legacy` flag would be a new exit field
   an administrator could set by hand; the comparison needs no field and ends by itself once the
   exit is edited. The editor warning and the DEVICE-SETUP 2.6 step are taken; validation does not
   *refuse* exits without interfaces (a site enters the exits before its first logs show the
   `dstintf` names), it warns. The "searched addresses of another exit" paragraph is taken.
7. **D15 own set (bridge-switch case).** Taken as proposed (F's transips leave the own set). It
   means probe B and probe C keep their Wave 2 answers on upgraded sites until interfaces or the
   second line's public address are entered; that is now stated (D15, E-j) instead of claimed as
   fixed. The other reading (converted exits behave like new ones at once) is O17.
8. **DECLINE (D33).** Taken for `fg_dhcp` only, with the high alert naming the VLAN. Not applied to
   MikroTik: WP-B's rule that MikroTik behaviour stays identical, and RouterOS's DECLINE logging is
   unknown (O15, 2.17). The 300-s threshold and "at least two" are this contract's choice.
9. **Renewal alerts** (three findings merged: normal returns counted, end-log time, incremental
   runs blind to timed-out intervals). Taken as one rework: the per-run counters
   `fg_traffic_after_lease_end`/`fg_reopened_after_timeout` are dropped rather than repaired, and
   the alert computes from stored leases and session starts; high only when no renewal ACK was
   seen in 7 days. The thresholds (3 leases and 5 %) are this contract's choice.
10. **`R20-lease:none`** (four findings merged: refused rows, MikroTik feed stop, `lease=0`,
    documented set-ups). Taken. Not taken: the proposed coverage alert "share of active RFC 1918
    source IPs without a covering lease in the last hour": on a site with static devices (whose
    chains end at the internal IP by design) it would fire permanently, and it joins traffic with
    leases every 5 minutes; the stopped and refused feeds it targets are covered by `none` and
    `fortigate-refused`.
11. **`R20-nat:unmapped-transip` severity.** Medium, not low: with exits configured, a private
    unclaimed address means a line whose public-IP lookups list nobody.
12. **A configured `isp_nat` exit with a public transip.** The refusal of an exit whose own public
    address and NAT address overlap, the editor warning, the §13 sentence, the `egress_search`
    rewording, no double-NAT paragraph for `transip` and the `transip` fallback variant are taken
    (the separate isp_nat-fallback-text finding is merged here). Not taken: converting a global
    `nat.wan_transip` into a direct exit automatically, which would change upgraded answers
    without the administrator.
13. **Private addresses in `public`.** Taken as a refusal (new code), with an explicit list:
    Python's `ipaddress.is_private`/`is_global` classify the RFC 5737 ranges (the example world's
    public addresses) as not global and must not be used. The converter moves such legacy entries
    into `wan` (new case (h), an E-j exception). The prefill rule of the observed panel is merged
    here.
14. **Exit change warning.** Taken with a per-exit change record maintained by `change_setting`;
    "an exit that claimed P before its change" is covered by `addresses_before`, so a removed
    address still warns. *(Replaced in round 3: `addresses_before` kept only the content before
    the latest change; the record now holds no contents and every exit changed after T is named
    for an unclaimed P, round 3 note 11.)*
15. **"Verified by whom" in the PDF.** Taken through an explanation of the outcome
    (`fortigate_format_verified` with time and author) rather than read from the setting when the
    PDF is rendered: a stored report must not change when the setting changes later.
16. **Rollback with FortiGate leases.** The finding offered "have the rollback delete or ignore
    `fg_dhcp` leases". Not taken: the installer runs no SQL on a rollback and no schema downgrade.
    What happens is now stated (2.2.1, §13), and `fortigate_dhcp_unconverted_recent` names the
    rollback case. *(Round 3: one text, `fortigate_dhcp_not_converted`, names every reason.
    Corrected in round 6: stating it left the previous version naming the former holder at
    `high` after a release or reassignment it cannot read, so `-Rollback` now runs one Wave 3
    step on derived rows that lowers every FortiGate lease to `low`, and the re-upgrade restores
    the untouched ones; round 6 note 5.)*
17. **Not-logged address seen in the logs.** Taken as resolution `observed` with the exit listed,
    not as an eighth resolution value.
18. **Duplicates merged:** the refused-evidence findings (three); the renewal findings (three);
    the private-public and prefill findings; the isp_nat fallback text into the public-transip
    finding; the e-mail facts into 2.16.

## Review notes — round 3 (2026-10-27)

All 39 findings were checked against the code at HEAD (section 1.2 gains the facts they rest
on, marked "third review round") and the saved Fortinet pages (F21 and F22 are new: `config
system ntp` and `diagnose sys ntp status` in the CLI Reference, several DHCP servers on one
interface for relayed networks in administration guide 783526). Every finding described a
real gap. The guiding rule of this round (a wrong person at high confidence is the worst
outcome, a false "nobody" the second worst; lower the confidence and say why rather than add
machinery; take the simpler of two equally safe designs) decided the open choices. Taken as
proposed, by finding number: 1 (cross-table ACKs supersede, D5), 2 (a logged lease ends at
`lease_seconds`, D6; round 1 note 9 corrected), 4 (D33 applies the other interval's deadline
first), 5 (D33 keys on the VLAN too), 6 (an `unknown-<ip>` sender at a configured FortiGate's
address is trusted, D24), 8 (E-k3 rewritten for a shared transip; grouping unchanged), 9 (the
help and alert texts hedged, sahada doğrula), 10 (the interface condition is dropped only when
no row near T carries the exit's interfaces), 11 (the change record keeps no contents), 12
(past half a lease without a renewal the chain is `low`), 13 (one listed C1 line in
`routers/lookup.py`; `run_lookup` reads the status rows; `from_settings` stays values-only), 15
(two checks, `R20-nat:invalid`), 16 and 29 (excluded and IPv6 rows are configuration; one
`fortigate-refused` cause), 17 (`{changes_tr}`/`{changes_en}`, the path written out), 18 (fixed
one-line actions), 19 (step 5(a) dropped), 20 (`FortiGate DHCP, logid …`), 22 (the card state
read from the alerts), 23 (one `fortigate_dhcp_not_converted` text, no `dhcp.fortigate_since`),
28 (`NatMatch.untrusted_sources`), 30 (the observed check before `unconfigured`), 33 (the seed
converts again after a rollback, or `R20-nat:legacy-changed`), 35 (`absent` is info), 36
(minutes, at most 5), 37 (`fortigate_dhcp_only` dropped), 38 (log-supplied alert values from
trusted rows, cut and masked) and 39 (the negative branch of the renewal check). The ones below
were taken in another form, in part, or merged, and why.

1. **Finding 3, source clock.** Taken as one generic cause, `R20-lease:source-clock`, over the
   `messages` rows of every trusted source rather than FortiGate sources only: a MikroTik clock
   that is minutes off shifts DHCP events against FortiGate sessions in the same way, and the
   query is the same. The threshold (±120 s) is this contract's choice. Not taken: the optional
   per-lookup warning "the offset at T exceeded the tolerance", which needs a stored history of
   offsets per source and moment; the alert history already shows when a source was off.
   *(Corrected in round 4: every row keeps `time` and `time_received`, so the offset at T is
   measured from the stored rows and lowers the chains, 2.7; the alert also missed hour-scale
   offsets and MikroTik sources, D28. Round 4 notes 1 and 6.)*
2. **Finding 7, renewal alert vs refusals.** Merged with finding 39 into one simpler change:
   neither renewal text names `dhcp.fortigate_traffic_extends` any more; the high text sends the
   administrator to `fortigate-refused` first (renewal ACKs may be among the refused rows), then
   to the renewal check of DEVICE-SETUP 2.4 step 5, whose negative branch is the only place that
   offers the setting, as the organisation's decision. The alert's condition is unchanged, so it
   stays high when renewals are refused (chains of active clients are cut either way); a
   condition that suppressed it while refusals exist would have lowered a real false "nobody" to
   the medium `fortigate-refused`.
3. **Finding 14, import cycle.** Taken (`lookup/trust.py`, a leaf, day 0). E-p is **split**
   rather than moved whole to C2: its probe half (never `observed`, never a D15 pass or fail)
   needs only `untrusted_sources`, which day 0 now writes, so it stays in C1 (E-p1), and the
   match marking (`session_from_unknown_source`, `NatMatch.untrusted_sources`) is C2's (E-p2).
4. **Finding 21, one D27 rule.** Taken, with the window written as `[T − W, T + W]` (W = the
   covering lease's alive window, at least the longest configured DHCP window) and one
   `usable_dhcp_origins` query per lookup for all its chains' subnets, instead of one query per
   chain over `[T − longest_window, T + covering window]`. It lowers the same chains or more in
   every case the contract describes; the one case the dropped rule alone would have lowered (a
   MikroTik predecessor whose last DHCP row lies more than W back, kept open by traffic) rests on
   the newer ACK, the only DHCP record near T (D27).
5. **Finding 24, `medium` past half a lease.** Not taken as `medium`; finding 12's `low` is
   taken instead. Under D26's own premise (FortiOS logs renewals) a missing renewal speaks
   *against* the holder, and `medium` reads as "probably the holder"; `low` is the level of the
   other cases where the logs leave two explanations (`srcmac_mismatch`,
   `dhcp_sources_overlap`). The finding's DENETCI/RAPOR reading-table row is taken. The
   alternative is O18.
6. **Finding 25, silent change warnings.** Covered by finding 11's simpler rule instead of the
   proposed per-exit "searched for unclaimed addresses" flags and a capped union of previous
   addresses: for a P that no exit claims now, every exit changed after T is named, deleted ones
   included, which covers a deleted catch-all and an exit edited twice; a claimed P is covered by
   the `egresses` check, because two exits never claim one address at once. E-k4 gains both
   cases.
7. **Finding 26, `no_egress`/`not_logged` in the PDF.** Taken, but the snapshot of the exits is
   `outcome.egress.egresses`, which for `no_egress` now holds every configured exit, instead of
   a new outcome field; `not_logged` already holds its exit. The reading-table rows are taken.
8. **Finding 27, the D8 row after T.** Taken in a simpler form: no row after T is looked for or
   cited at all (one query, not two), not "only when it has the same MAC": a later ACK of the
   same MAC says nothing about T that the covering row before T does not already say, and the
   second query, its coverage rule and its test go away.
9. **Finding 31, step 5(a).** Merged with finding 19: (a) is dropped rather than redefined as the
   difference between `type:event mac:` and `logid:` searches in Arama. That difference rests on
   which tables `mac:` covers, a search-registry detail that may change; the card (no refused
   rows) and the trial lookup already prove the format end to end. Adding a `table` field to
   `LiveLine` was not taken either (an API and mirror change for one check). The trial lookup is
   marked as ILK-ADIMLAR §8 says (Makam "DENEME").
10. **Finding 32, `none` on quiet sites.** Taken for MikroTik-only evidence (`medium`). The
    FortiGate holiday case the finding mentions is not refined: telling an empty office from a
    stopped feed would need a further rule (traffic from IPs that hold FortiGate leases), so it
    is recorded as a residual risk (2.17) that opens a high alert after half the longest lease +
    24 h of a long holiday and resolves with the first ACK.
11. **Finding 34, "önce / şimdi" in the PDF.** Taken in part: each named exit prints what it
    holds now (or "silindi"), the time and the author, and the text says the earlier definitions
    are in the organisation's audit log and can be requested from its administrator. The
    "before" values are not printed: after finding 11 the change record holds no contents, and
    printing the value in force at T safely needs the complete history (one previous content is
    wrong after two edits, round 3 note 6), which is the machinery this round removed.
12. **Finding 6, trust of an `unknown-<ip>` sender.** Taken with two narrowings: the vouching
    `sources` row must be enabled (a removed source's address vouches for nothing, so lines it
    sent after its removal stay refused), and the address may be a configured network, as
    network sources are. The explanation is a new info text, `source_configured_later`.
13. **Finding 15, `R20-nat:invalid`.** Taken. It is not dead machinery although every write path
    validates: a value valid under Wave 3's rules can be refused by a later version's stricter
    rules after an upgrade, and the lookup's 503 would otherwise be the first sign.
14. **Not raised by the findings, fixed on the way:** the `fortigate-dhcp-mac` text no longer
    names "a relay" as a set-up that hides the client's MAC (relayed DHCP carries the client's
    hardware address; nothing saved says otherwise); the `egress_transip_not_seen` text no
    longer carries an optional segment, which `str.format` would have read as a placeholder; the
    loadgen wording with the message number right after the device name became "FortiGate DHCP
    Ack (26001)".

**Open points for the owner (round 3).** O18 (`low` past half a lease), O19 (a logged FortiGate
lease ends without the margin) and O20 (`none` medium on MikroTik-only evidence) in 0.2; the
±120-s `source-clock` threshold; the re-conversion after a rollback (D18), which could instead
only alert *(decided in round 4: it only alerts)*; trusting an `unknown-<ip>` sender once its address is configured (D24), which a
stricter posture would leave to roadmap 2.2; and, once V14 is answered on site, whether FortiOS
logs NAKs under 26001: if it does, `fortigate-refused` would stay open at medium on every site
with roaming laptops, and the NAK value would need its own "not evidence, not a refusal" entry.

## Review notes — round 4 (2026-10-27)

All 37 findings were checked against the code at HEAD `1680d28` (three test-only commits after
`9a9c6a2`; section 1.2 gains the facts they rest on, marked "fourth review round") and the saved
Fortinet pages. Every finding described a real gap; findings 10, 13 and 15 repeat 1, 5 and 6 and
were merged with them. The guiding rule of round 3 decided the open choices again. Taken as
proposed, by finding number: 1/10 (a lease's last DHCP evidence is its own, never the line that
ended it; `refs.last_dhcp_evidence`), 2 (an `fg_dhcp` seed's `last` ignores traffic), 4 (the
interface condition is dropped only on positive evidence), 6/15 (the closing line claims the
field it shares with the lease), 8 (the MikroTik variant of D33), 9 (`egress_public_dynamic`), 11
(`untrusted_sources` filters `fg_*` rows only), 12 (the section-4 exit row and one label table,
the fallback label split), 14 (no re-conversion after a rollback; one `updated_at` condition), 16
(worker goldens through `Evidence.fortigate_dhcp`), 17 (one implementation of the refusal rules,
five trust functions, `srcmac_ip_counts` in `trust.py`), 19 (`refused` wins over `none`), 20 (the
topology list in the order of §0.1), 25 (`fortigate-refused` resolves when the last 24 h re-read
with current settings are clean; the card follows its live count), 26 (the fingerprint holds
mode, public, transips and public_dynamic; `observed` left out), 27 (method notes from the stored
outcome), 31 (D8 citations only in internal mode and for exact matches), 32 (outcomes stored
before the upgrade), 33 (reading-table rows and their docs test), 34 (no CPU statement; the IPsec
help follows F20), 35 (E-f reworded) and 36 (origins rendered, VDOM names cut and masked). The
ones below were taken in another form, in part, or merged, and why.

1. **Finding 21 (blocker), source clock.** Taken, combining its two options: the newest 200
   `messages` rows of the source by device time inside a range of ±(1 h + `max_clock_skew`)
   around now, counted when received in the last hour. That finds any offset ingest accepts and
   stays an index scan that stops early. The checked sources are not chosen by "rows in the
   window", which is again a device-time window and would miss the clocks that harm most; they
   come from `search_counts` by `raw_day`, the receive day: configured, enabled sources whose
   files of today or yesterday hold `fg_traffic`, `fg_dhcp`, `mt_dhcp` or `mt_arp` rows, so a
   MikroTik DHCP router is checked and a switch or access point never pages. The sentence "nothing
   reads `search_counts`" is narrowed accordingly (no evidence is counted from it). Merged with
   the clock part of finding 11.
2. **Finding 3, double-NAT evidence and unclaimed candidates.** Taken, with one list for
   "private or shared": the ranges rule 5 of 2.8.1 refuses as public (RFC 1918, RFC 6598 and the
   reserved ranges), one constant shared with the validator. The restriction applies to the D15
   check (only rows on E's interfaces when E declares any) and to every unclaimed candidate set
   (fallback, O5, catch-all, `public_unknown`, `unconfigured`), as the finding proposed; public
   unclaimed addresses stay in `observed`, `egress_unmapped_seen` and `R20-nat:unmapped-transip`.
   This changes Wave 2 answers only where an unclaimed public NAT address exists; E-j names them
   for (a), (b), (e) and (f), and the recorded Wave 2 assertions use the private `100.64.12.9`
   and stay unchanged (1.2). The cost, an ISP that numbers the FortiGate's WAN side from
   public-looking space, is a residual risk (2.17) and owner decision O21.
3. **Finding 24, internal NAT addresses.** Taken in a minimal form: a fourth mode `internal`
   (transips and interfaces only; claimed, never searched, never a fallback or catch-all). A
   request for such an address is answered by the existing resolution `transip` with no search
   and `egress_internal`, not by an eighth resolution value, which would change the fixed
   resolution list, its TypeScript mirror and every label table. A separate setting for such
   addresses was considered and not taken: the mode keeps one editor, one validation and the
   observed panel's "add" button.
4. **Finding 23, `none` per table.** Taken, with one narrowing: `none:mt_dhcp` (medium) opens
   only while no usable `mt_arp` row arrives either. ARP-LOG lists DHCP clients as well, so a
   stopped MikroTik DHCP feed beside a working ARP-LOG only lowers new chains to `medium`, while
   a per-table medium would open on most quiet weekends of every ARP-LOG site (O20). `absent`
   stays one aggregate cause.
5. **Findings 5, 13 and 31, the D8 probe.** Merged into one rule instead of the finding's list of
   exceptions: the newest trusted DHCP-shaped row of the IP before T, across `fg_event` and
   `fg_dhcp`, decides; only an `fg_event` row of an `assigned` logid that covers T counts. This
   covers the RELEASE, the converted-later and the forged-line cases at once, with two
   `LIMIT 1` index scans per IP. Citations are kept for internal mode and exact matches (the
   requester asked about that IP or that one session) and dropped for candidates (finding 31).
   Dropping the citation everywhere was considered (simpler) and not taken: in the weeks after an
   upgrade the covering ACK is the only record an internal lookup can show.
6. **Finding 22, per-lookup clock warning.** Taken: round 3 note 1's premise was wrong (the stored
   rows keep both times). "Lower the match" is done by lowering every chain to `low`: `NatMatch`
   has no confidence field, and the match did match the record by `transip` and `transport`, so
   it keeps its kind while the PDF states the offset. Shifting the search by the measured offset
   (a correction rather than a warning) is the O22 alternative.
7. **Findings 18, 28 and 30, renewal and decline alerts.** `fortigate-renewal-gaps` is cut
   (finding 18's simpler option): the affected chains are already `low` past half the lease, a
   static device in the pool has `fortigate-decline`, two logged servers have
   `dhcp-sources-overlap`. Finding 28 is taken as a separate info key,
   `fortigate-renewals-not-logged`, because an open alert's severity never goes down (1.2), so one
   key could not fall from high to info after the site check. Finding 30: the decline addresses
   are taken (Security page only; 2.16 names this one exception to "no client IP", since the
   address is where a static device answers and the remedy needs it), `first_day` of the refused
   rows is taken with a 100-day bound, every rebuild instruction names the Ayarlar path; the
   renewal-gaps VLAN numbers are moot.
8. **Finding 7, shared MACs.** The alert is recomputed from ACKed rows per origin, interface and
   VLAN, and D33 closes nothing for `vlan` None, as proposed. *(Corrected in round 6: D33 and the
   alert key on the configured network, compared by subnet, because `vlans.vlan_id` is nullable
   by design; nothing is closed only for an address in no configured network, whose `fg_dhcp`
   chains are `low` with `fortigate_dhcp_no_network`; round 6 note 1.)* The optional `low` for chains of a
   flagged MAC is not taken: it needs another query per lookup for a rare set-up whose remedy
   (exclude the interface, mark the MAC as infrastructure) the alert already names; the case is
   a residual risk (2.17).
9. **Finding 29, the upgrade text.** Both points are taken. The suggestion to shorten the
   FortiGate `lease-time` before an upgrade is not written: it would be new operational advice
   resting on client behaviour, and stating the gap (up to half the lease, renewal at half the
   lease, RFC 2131) is enough to plan around it.
10. **Finding 33, the reading-table test.** Taken as a check on backticked explanation keys in the
    table's first column against `reports/texts.py` `READING_TABLE_KEYS`, plus the eight exit
    labels; it is appended in WP-F2's merge, the first one in which both documents hold the
    table.

**Open points for the owner (round 4).** O21 (an unclaimed public NAT address is the FortiGate's
own: never double-NAT evidence, never an unclaimed candidate; the cost is the public-looking WAN
space behind an ISP router, 2.17); O22 (a source clock off at T lowers the chains rather than
shifting the search); the exit mode `internal` as the way to declare DMZ, VLAN, VPN and branch
NAT addresses; `fortigate-renewals-not-logged` as a permanent info alert on a verified site whose
renewals are not logged (the alternative is no alert, leaving §16 as the only record); the
declined pool addresses shown on the Security page (the one IP in alert details; a KVKK reading
for the organisation's counsel); D8 citations kept for internal and exact lookups only (or none
at all); `none:mt_dhcp` silent while ARP-LOG works; the chains of a shared MAC kept at their
confidence while `fortigate-dhcp-mac` is open; and, carried from round 3, the ±120-s clock limit,
trusting an `unknown-<ip>` sender once its address is configured, and how NAKs under 26001 are
treated once V14 is answered on site.

## Review notes — round 5 (2026-10-27)

This round changed the contract only for **blockers**: a concrete scenario in which an
implementation that follows the contract exactly names a wrong person or device at medium or high
confidence with no warning, answers "nobody" while the stored logs hold the answer with no
warning, alters or deletes evidence, shows person-level data to the wrong role, or meets a
contradiction that makes it unimplementable. Four blockers survived three independent refuters
each and are fixed below with the simplest safe change; every other finding is an implementation
note (the section before the review notes), not a contract change. Two class-5 claims (the
`run_incremental` seed origin and `READING_TABLE_KEYS`) and two class-2 claims (a settings fix
without a rebuild; a site with `internal` exits only) were demoted by their refuters and are notes.
The open points already deferred to the owner (rounds 3 and 4) were not re-raised.

1. **A lease's last DHCP evidence is measured at the requested moment (class 1; D6, 2.6.1, 2.7,
   2.10, 2.11, 2.12, 2.15, 3.3, 3.4, 3.5, 3.8, 4.3).** `refs.last_dhcp_evidence` was the lease's
   newest own DHCP record overall, so a client that left without a RELEASE and came back inside
   its lease (a continuation of the same interval) hid the missing T1 renewal before the moment:
   a static device using the address that evening was named as the holder at `high` with no
   warning, and the PDF printed a "last DHCP evidence" after the moment. Taken: the value is the
   newest own record **at or before** the moment, with that record's usable logged lease for the
   half-lease test (else the lease's `lease_seconds`, else the configured time). Because stored
   refs keep only the opening ACK and the last 6 continuations, an `fg_dhcp` chain reads it with
   one `LIMIT 1` query on `ix_fg_dhcp_ip_time` (rows of the pair in `[lease.start, at]` that
   `trust.kept` accepts); the pure function, now typed `(refs, end, end_reason, at=None)`, is the
   fallback and the value for the other tables. The superseded-line exclusion holds by
   construction (that line lies at the lease's end, at or after the moment, and is another
   MAC's, IP's or table's). New golden "renewed after the moment", with a dropped-refs case that
   only the query passes. Not taken: storing every continuation in the refs (a schema-size change
   for one per-chain read).
2. **Every `isp_nat` exit without a public address leaves the D15 own set (class 2; D15, 0.3 #7,
   2.8.3, 2.13, §13 text of 2.15, 3.4 E-c4, 4.3 C1).** The own set counted every exit's `transips`
   as another line's address, including those of an `isp_nat` exit whose public address is
   unknown and whose interfaces do not separate it from E. On an upgraded single-line site still
   behind its ISP router, an administrator who followed `R20-nat:unmapped-transip` and saved the
   line's NAT address as a new exit (its public address is refused there, `genel-1` holds it), or
   who entered interfaces on the converted `wan` only, made the check pass, and every lookup of
   the site's only public IP, past moments included, answered a bare "the FortiGate logged no
   session" with no warning (the address being claimed, `egress_unmapped_seen` and the alert went
   quiet). Taken, the check only: own(E) leaves out the `transips` of every other `isp_nat` exit
   without a public address unless both declare interfaces and they are disjoint (F is a subset).
   The check then fails and the answer is the warned `fallback` (`public_ip_not_transip`, the
   fallback paragraph), at worst with no candidate; the fallback search is unchanged (F only), so
   another line's sessions are never listed (E-c2) and E-a, E-b, E-c, E-c2, E-j, E-k2, E-o, E-q
   keep their answers. The 2.13 editor text that said the old behaviour continues "until
   interfaces are entered" is shown only while a converted partner remains and no longer
   promises that; the §13 sentence says the same. Not taken: the optional rule that keeps a
   converted exit in F while its interfaces overlap E's (it would restore Wave 2's candidates
   for the second trigger, but adds a second "as converted" test for a warned case), and
   searching the left-out exits' `transips` in the fallback (it would list another line's
   sessions whenever interfaces are missing). The fallback paragraph's `{seen}` then often reads
   "(-)": an implementation note.
3. **A search that found nothing measures its sources' clocks (class 2; D11, D28, 2.7, 2.11,
   2.17, 3.4, 3.8, 4.3 C2).** The per-lookup clock check measured only the sources of cited rows,
   so it was blind exactly when an offset made an exact or internal search miss the real
   session: the outcome was `none` with the generic `no_match_exact` text while the stored rows
   held both the session and the offset. Taken: when a search ran and its outcome cites no row of
   the table it searched, the same median query also measures the configured sources whose spool
   files of the receive days around T hold rows of that table (`search_counts` by `raw_day`, the
   choice `R20-lease:source-clock` makes; at most 5 sources in all), and beyond the limit adds
   `source_clock_offset`. The text of that key (new in this wave, day 0) now fits both cases: any
   chain is `low`, and an empty result may be found by widening the tolerance by the offset.
   Not taken: a separate key for the empty case (one more row in the texts, tones, reading table
   and PDF for one sentence), and shifting the search by the offset (the O22 alternative).
4. **Unconverted FortiGate DHCP lines after a lease's last record lower its chain (class 1; D8,
   D11, 2.2.1, 2.7, 2.8.3, 2.11, 2.12, 2.13, 2.15 reading table and §13, 3.4, 3.5, 3.8, 4.3).**
   After `-Rollback` without `-RestoreDatabase` and a re-upgrade, which the contract supports,
   the previous version files every FortiGate DHCP line under `fg_event`, so an `fg_dhcp` lease
   open at the rollback is never ended by the RELEASE or by the next client's ACK logged in that
   window; the instructed rebuild rebuilds the same stale lease. It named the former holder at
   `high` for up to half a lease with no warning, and the D8 probe ran only when no chain was
   built, so 2.2.1's and §13's claim "the lookup names them" was false here. Taken, the simplest
   safe form: for each chain or alternative that rests on an `fg_dhcp` lease and is still `high`
   or `medium`, the existing `fg_event` branch of the D8 probe runs for its IP over
   `(last_dhcp_evidence, T]` (at most half a lease, `LIMIT 1`, trusted sources, the mapped logids
   of either action); a row there lowers the chain and its alternatives to `low` with the new
   warning `fortigate_dhcp_not_converted_since_lease`, citing nothing (the row may hold another
   client's MAC and host name, KVKK). The per-IP filter keeps unconverted lines elsewhere on the
   site from lowering unrelated chains; on a healthy site such rows exist only as unconverted
   lines, which `fortigate-unconverted` already names. 2.2.1, §13 "Geri alma" and "Yeniden
   yükseltme" and the 4.3 rollback walk now say what lookups in the rollback window show, with
   or without the rebuild; round 2 note 16's statement is corrected accordingly. Not taken:
   changing the installer's rollback (round 2 note 16 still holds: it runs no SQL), and a new
   alert for rollback windows (the lowered chain states the reason where it matters).
   *(Corrected in round 6: the check also covers `mt_dhcp` chains and matches the MAC, round 6
   note 2; the installer's rollback now runs the lease step of 2.2.1, round 6 note 5, because
   this fix only protected lookups made after the re-upgrade.)*

**Open points for the owner (round 5).** None new. The consequences of the fixes are within
decisions already taken: an empty search's clock offset is stated, not corrected (O22); a quiet
`direct` line beside an `isp_nat` exit without a public address and without separating
interfaces now gets the warned `fallback` instead of an exact "no session", which the editor's
"enter each line's interfaces" warning already covers.

## Review notes — round 6 (2026-10-27)

This round changed the contract only for **blockers**, under the five classes of round 5 (a
wrong person or device at medium or high confidence with no warning; a false "nobody" with no
warning; evidence altered or a 5651 function blocked; person-level data to the wrong role or out
of the server; a contradiction that makes the contract unimplementable). Five blockers survived
three independent refuters each and are fixed below with the simplest safe change; the other 21
findings are "Implementation notes — round 6" (20 bullets, two merged; none dropped). Line
numbers in the findings refer to the round-5 text. The open points already deferred to the owner
were not re-raised.

1. **D33 is keyed on the configured network, not the VLAN number (class 1; D5, D9, D11, D23,
   D27, D28, D33, O15, 1.3, 2.6.1, 2.6.2, 2.6.3, 2.7, 2.11, 2.12, 2.13, 2.15 §0.1, ILK-ADIMLAR
   row and reading table, 3.3, 3.4, 3.5, 3.6, 4.3, 4.5, Deviations; corrects round 4 note 8).**
   For an `fg_dhcp` row
   `ctx.vlan_for(ip)` is `find_vlan(...).vlan_id`, which is NULL for a network entered without a
   VLAN id (`vlans` is keyed by subnet, `models.py:691-705`; FIRST-STEPS asks for an empty id on
   such a network), and D33 closed nothing for `vlan` None. On a FortiGate-only flat LAN the
   DECLINE scenario of D33 itself then named the declining client for the static printer's
   traffic at `medium` (lease and device `high`, person `medium`) with no warning, and the same
   on a site that entered no network. Taken: the D33 key (timeline, seeds, MikroTik variant) is
   the configured network, the `vlans` row `find_vlan` returns, compared by its `subnet` on both
   sides (`DhcpRow.network`, `DhcpEvent.network`, `LeaseInterval.network`,
   `Context.network_for`; this settles round 5's note on MikroTik regex VLANs); nothing is closed
   only for an address in no configured network, and an `fg_dhcp` chain there is `low` (lease and
   chain) with the new `fortigate_dhcp_no_network`, the lookup-side signal the low
   `fortigate-dhcp-subnet` alert never gave; `fortigate-decline` and `dhcp-sources-overlap` name a
   network by its VLAN number, else its subnet in the key and its name and subnet in the title
   (`<net>`, `{network}`; e-mails carry the VLAN number only); `fortigate-dhcp-mac` counts per
   network; KURULUM §0.1 and ILK-ADIMLAR §5 tell FortiGate-DHCP sites to enter every DHCP network,
   with or without a VLAN id. Not taken: keying on (origin, interface) alone for an address in no
   configured network (two relayed networks or VPN pools nobody entered would cut each other's
   chains at every renewal, a false "nobody" with no warning), and lowering `mt_dhcp` chains
   outside every network (D27's gap there is implementation note 4).
2. **The post-lease check covers every DHCP chain, by IP or MAC (class 1; D8, D11, O3, 2.2.1,
   2.7, 2.8.3, 2.11, 2.15 reading table and §13, 3.4, 3.5, 3.8, 4.3).** In the "migration moves
   the IP" golden made while the previous version still ran, the FortiGate ACK that moved
   Selin Kaya's MAC to .120 was filed as `fg_event`, the printer's traffic kept her `mt_dhcp`
   interval on .57 open (D26 does not apply to MikroTik), D33's MikroTik variant and D27 need
   converted rows, the D8 probe runs only without a chain, and the round-5 check covered `fg_dhcp`
   chains and their IP only: the PDF named her at `medium` with no warning while sealed lines
   showed the move (the same for another client's ACK of .57, and for an `fg_dhcp` chain whose MAC
   moved inside a rollback window). Taken: for every chain or alternative whose lease is `mt_dhcp`
   or `fg_dhcp` and still `high` or `medium`, a mapped FortiGate DHCP line in `fg_event` of the
   chain's IP or MAC in `(last_dhcp_evidence, at]` (capped at the D8 window) lowers it to `low`
   with the reworded `fortigate_dhcp_not_converted_since_lease`, citing nothing; a MAC match
   counts unless both addresses lie in configured networks and those differ (the D33 key, so a
   laptop on two VLANs is not lowered; the verifiers' precise variant, erring towards `low`); one batched query per
   lookup with no cap, matched to each chain's own window (which settles implementation notes 11
   and 12), guarded in 3.8 with the probe's fallback window. New golden "migration moves the IP
   before the upgrade" with its client-B and other-network variants; §13 and the reading table say
   so. Not taken: leaving out lines of the chain's own pair (an unread same-pair FortiGate ACK is
   exactly the D5 migration the timeline could not apply), and reading any value of the line into
   the outcome.
3. **The D15 own set leaves out exits on other declared interfaces (class 2; D15, 0.3 #7, D23,
   2.8.3, 3.4 E-c4, 4.3 C1).** With E declaring interfaces, a private `transip` on E's interface
   that another exit claims was kept in own(E) when that exit declared disjoint interfaces (round
   5's exception); two modems of one ISP hand the FortiGate the same default address, so line 1
   reset to router mode made the check pass and the exact search answer a bare "no session" for
   the sessions that left `wan1` through `100.64.12.9` (Wave 2 failed the check there). Taken, per
   address: an exit X leaves own(E) when X is an `isp_nat` exit without a public address (round 5)
   or when E and X both declare interfaces and they are disjoint, of any mode; an address one
   remaining exit claims stays in. Rows of X's address on X's own interfaces still never count, so
   E-c2's last case, E-c4's third case, E-k, E-k2, E-k3 and E-n2 keep their answers; the new E-c4
   case gives the warned `fallback`, at worst with no candidate. Not taken: the optional widening
   of the fallback to the transips outside own(E) on E's interfaces (implementation note 8, with
   legacy (d)'s case), and making round 5's D32 note binding instead (note 9).
4. **"Seen, but not alone" falls back (class 2; D15, D18, D23, 2.8.3, 2.10, 2.11, 2.12, 2.15
   reading table, 3.4 E-c5 and E-j, 3.5, 4.3 C1).** The first branch of the check accepted P seen anywhere
   in ±15 minutes, so near a change of the line's mode (legacy (d) as converted, the modem reset
   to router mode at 18:14, a request for `.200:51001` at 18:19:07) the check passed on the rows
   before the reset, the exact search found nothing, and the answer was a bare `none` although the
   same probe window held `100.64.12.9`, whose sessions contain the requester's. Taken: when the
   probe saw P and the D15 evidence for E, a search on P that finds no session becomes the
   `fallback` (P ∪ F or the unclaimed predicate, `public_ip_not_transip` reworded "or only part of
   the time"); a found match stays exact; the probe computes both facts (no early return) and the
   distinct evidence addresses, which a `fallback` stores in the new `searched.outside_seen`
   (2.10, so the PDF, which renders the stored outcome, has them) and which become the fallback
   paragraph's `{seen}` (settling round 5's note on it); an E-j exception (Wave 2 answered
   `none`). E-o, E-c2's last case and E-c4's
   third case are unaffected (their outside addresses are public or off E's interfaces). Not
   taken: keeping `none` with a warning that names the addresses (the verifiers' weaker
   alternative: a warned "nobody" where the fallback lists the session, for one more text), and an
   overlap-aware probe (round 2 note 3's cost).
5. **FortiGate leases are lowered at a rollback and restored at the re-upgrade (class 1; 2.1,
   2.2.1, 2.2.2, 2.5, 2.6.2, 2.15 §13 and DATA-MODEL row, 3.3, 3.7, 4.1, 4.3, 4.5; corrects round
   2 note 16 and round 5 note 4).** After `-Rollback` without
   `-RestoreDatabase`, as §13 advises, the previous version (HEAD) loads every `leases` row, gives
   an open `fg_dhcp` lease its own timeout (about 26 h after the last record), cannot read the
   RELEASE or the next client's ACK (its parser files them under `fg_event`), and named the
   former holder at `high` in lookups and stored PDFs; round 5's lowering helped only lookups made
   after the re-upgrade. Checked against HEAD: its hourly `leases.recheck_today` and nightly
   rebuilds also re-derive every lease that spans a rebuilt midnight from its stored refs
   (`_seed_candidates`, `_seeds`, `leases.py:566-757`), so they re-open an `fg_dhcp` lease this
   version had ended (superseded at 08:00, re-opened at the 10:00 recheck at `high`) and delete
   the `fg_dhcp` leases that started on a rebuilt day; and they keep a stored confidence
   (`interval_from_row`, `_Open.from_interval`, `replace`), which the chain takes (`weakest`).
   Taken: the rollback, before the program swap, runs `arcilogger-db leases prepare-rollback`,
   which lowers **every** `fg_dhcp` lease to `low` with one `updated_at` and records it in the
   status row `leases.rollback_lowered` (derived rows only; the count printed and logged; a
   failure only warns); the previous version then shows every FortiGate chain `low`. The
   re-upgrade's seed restores `high` on the rows whose `updated_at` is still the step's time
   (untouched by the previous version), deletes the status row and prints both counts; a rebuilt
   `fg_dhcp` seed is `high` (2.6.2), so this version's own rebuilds undo the step for the rows the
   previous version rewrote, once they re-derive them from the `fg_dhcp` rows; `run_incremental`
   keeps a stored `low`. Not taken: closing the open leases at the rollback moment (the verifiers'
   first and third form: the previous version's hourly recheck re-opens a lease that spans
   midnight within the hour); lowering only open leases (the second form: a lease this version
   ended today or yesterday is re-opened at `high` by the same rebuilds); a §13 sentence alone
   (nothing printed in the previous version's PDF); and lowering MikroTik leases that a FortiGate
   ACK ended (the previous version re-derives them from its own evidence at `high` when they start
   on a rebuilt day, so a lowering would not hold, and otherwise they follow its own rules as
   before the upgrade, as §13 now says). Its usage analysis, which reads no confidence, is not a
   5651 output.

**Open points for the owner (round 6).**
- `-Rollback` now runs one Wave 3 step on derived rows (every FortiGate lease to `low`, one status
  row), reversing round 2 note 16's "the installer runs no SQL on a rollback"; in the previous
  version every FortiGate chain is `low` without a reason of its own, and FortiGate-DHCP lookups
  of moments after the rollback are to be made after the re-upgrade. The alternative, a §13
  sentence only, leaves blocker 5 open.
- On a FortiGate-DHCP site, `fg_dhcp` chains of addresses outside every configured network are
  `low` (`fortigate_dhcp_no_network`), so "lookups work without VLANs" no longer holds for them
  and such sites must enter their DHCP networks. The alternative, keying D33 on (origin,
  interface) there, can cut the chains of relayed networks nobody entered.
- "Seen, but not alone" turns some Wave 2 `none` answers into candidate lists near a change of a
  line's mode (an E-j exception). The alternative is `none` with a warning naming the addresses.

## Review notes — round 7 (2026-10-27)

This round reviewed only what rounds 5 and 6 changed (the diff from the round-4 text), under the
five blocker classes of round 5, and changed the contract only for **blockers**. One blocker
survived three independent refuters and is fixed below with the simplest safe change; the other
22 findings are "Implementation notes — round 7" (18 bullets, three merges; none dropped). Line
numbers in the finding refer to the round-6 text. The open points already deferred to the owner
were not re-raised.

1. **A network entered after the leases were built keeps D33's missed close, without a warning
   (class 1; D33, 0, 2.6.3, 2.7, 2.11, 2.15 KURULUM/INSTALL §0.1, ILK-ADIMLAR row, CORRELATION
   row and reading table, 3.3, 3.4, 3.8, 4.3 C2; completes round 6 note 1).** D33 decides at
   build time from both addresses' networks, while round 6's lookup-side signal
   (`fortigate_dhcp_no_network`) tests only the chain's IP, at lookup time. Nothing rebuilds
   leases when a network is added or edited (`api/routers/vlans.py`: a 5651 lookup reads `vlans`
   when it runs), the hourly and nightly rebuilds cover today and yesterday
   (`worker/service.py:8-11`), and a lease that ended before midnight is final
   (`leases.py:574`). On a FortiGate-only flat LAN whose 172.20.42.0/24 was not entered, D33's
   own DECLINE (ACK .57 at 08:41:10, ACK .58 at 08:41:14 for :31, a static printer on .57)
   closed nothing; once the administrator entered 'Ofis' as the alert and the texts say, a
   lookup weeks later of .57 at 10:12 found a network (no `fortigate_dhcp_no_network`), no
   `lease_renewal_not_seen` (1.5 h into a 7-day lease), no `fg_event` line and one origin: :31's
   device at `high` and its person at `medium`, with no warning. The same with a /24 entered for
   a /23 scope, the client declining .42.57 for .43.10 (that address's network is None at the
   build, and the .42.57 chain lies in a configured network). Taken: the 2.7 post-lease check
   also reads **converted** rows. For an `fg_dhcp` or `mt_dhcp` chain still `high` or `medium`, a
   kept `assigned` `fg_dhcp` row of the chain's MAC for another IP in `(last_dhcp_evidence, at)`
   (for an `fg_dhcp` chain on the lease's interface and VDOM, D33's key) lowers lease and chain
   to `low` with `fortigate_dhcp_not_converted_since_lease`, under round 6's MAC rule (it counts
   unless both addresses lie in configured networks today and those differ). It is one query per
   lookup on `ix_fg_dhcp_mac_time`, cites nothing and reads no value of the row into the outcome.
   Built with today's networks and settings, D33 closes such a lease at that row, so a healthy
   timeline never shows one, and a laptop or NAS on two configured networks stays `high`. The
   text and the reading-table row are reworded ("not applied to this lease") and ask for the
   rebuild. `R20-lease:fortigate-dhcp-subnet` gains `first_day` in its details (computed as
   `fortigate-refused` computes its own). Its texts, its one-line action, `fortigate_dhcp_no_network`,
   ILK-ADIMLAR/FIRST-STEPS §5, KURULUM/INSTALL §0.1 and CORRELATION §7 now say that a network
   added or changed applies to past leases only after a rebuild from that day. Goldens: the
   DECLINE golden gains "the network entered after the build, no rebuild" (`low`, then
   `no_lease` after the rebuild) and "a /24 entered for a /23 scope" (`low`, and the .43.10 chain
   `low` with `fortigate_dhcp_no_network`); the relayed-network golden pins `high` for the NAS;
   the migration golden gains the same shape for D33's MikroTik variant (which also covers round
   7's implementation note on a MikroTik lease re-opened after a same-day rollback); 3.8 guards
   the query. Not taken: the texts alone (the /23 case, an administrator who enters or edits
   networks unprompted, a gap longer than the rebuild's 31 days per request and a skipped rebuild
   stay open); storing the build-time network on the lease (one more v0017 column for the same
   signal, and still blind to a network edited later); a rebuild started from the VLAN form (a
   WP-E change; the warned `low` holds until the rebuild); and matching converted rows by IP
   (another MAC's ACK of the same IP always closes the interval at the build, whatever the
   network). This does not reverse round 6's "not lowering `mt_dhcp` chains outside every
   network": an `mt_dhcp` chain is lowered only on a FortiGate ACK of its own MAC for another
   address that D33's MikroTik variant would act on.

**Open points for the owner (round 7).**
- Adding or editing a network on a FortiGate-DHCP or migration site now asks for a lease rebuild
  from the `fortigate-dhcp-subnet` alert's `first_day`; until it runs, the chains whose D33 close
  was missed are `low` with a reason, never `medium` or `high`. A rebuild offered by the VLAN
  create and edit form itself would be a web change, not made here.
- For reading round 6's third open point: an implementation note of this round shows that
  "seen, but not alone" turns an exact miss into a candidate list whenever P and an outside
  private or shared address are both seen within ±15 minutes, not only near a change of a line's
  mode (every such answer is warned); the note proposes narrowing the trigger to "no session
  through P overlaps T".
