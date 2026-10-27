# ADR-0016: FortiGate DHCP as lease evidence, srcmac as partial evidence, per-exit NAT (dual WAN)

- Status: accepted (2026-10-27). Decided by the developer on the owner's delegation; the owner
  may change any part of it (open points at the end, with the default each one has now).
- Related: [ADR-0005](0005-database-and-mvp-contracts.md) (one table per parser, the MVP
  contracts), [ADR-0008](0008-device-assignment-history.md) (the person at the moment),
  [ADR-0010](0010-alerts-and-email.md) (R20 checks and their e-mail),
  [ADR-0013](0013-offline-licensing.md) (5651 Temel is never locked),
  [ADR-0014](0014-browser-administration.md) (the settings pages),
  [ADR-0015](0015-search-live-sources.md) (kinds, search fields, sources). The names this
  decision fixes are code: the settings and their refusal codes (`arcilogger_db.settings`,
  `arcilogger_db.settings_meta`, [CORRELATION.md](../CORRELATION.md) section 5), the internet
  exits (`arcilogger_db.egress`), the lookup outcome (`arcilogger_worker.lookup.types`), the
  explanation texts (`arcilogger_worker.lookup.texts`), the DHCP references
  (`arcilogger_worker.lookup.refs`) and the sender trust rules (`arcilogger_worker.lookup.trust`).
- Wording: nothing here claims legal compliance; ARCILOGGER is designed to support 5651
  obligations ("5651 yükümlülüklerini desteklemek için tasarlanmıştır"). Examples use the
  repository's fictional site only (Örnek A.Ş., `172.20.x`, `00:00:5e:00:53:xx`, RFC 5737
  public addresses, `100.64.12.9` / `100.64.13.9` as the FortiGate's address behind an ISP
  router).

## Context

A 5651 lookup turns a public IP, port and moment into a FortiGate session, its internal IP, the
lease of that IP (its MAC), the device and the person. Until now:

- **Lease evidence came from MikroTik only** (`mt_dhcp` and the ARP-LOG of `mt_arp`). On a site
  whose FortiGate is the DHCP server every lookup stopped at the internal IP (`no_lease`), and
  candidate and exact matches had no chain without saying why.
- **NAT was one global setting pair** (`nat.public_ips`, `nat.wan_transip`, `lookup.port_hint`).
  A site with two internet lines (one direct, one behind an ISP router doing a second NAT) could
  not say which line an address belongs to: a question about one line's IP listed the other
  line's sessions, and a quiet direct line fell back to every unclaimed session.
- **The FortiGate itself** documents its DHCP server events (the FortiOS 7.6 log reference: DHCP
  Ack 26001 with `ip`, `mac`, `dhcp_msg`, `lease`, `interface`, `hostname`; DHCP Release 26002
  without `lease`; no event for an expiry, a revoke or a decline) and the `srcmac` field of its
  session records (filled only with device identification on the source interface; behind a
  switch it may be the switch's MAC, behind a router always the router's). One community sample
  of a real 26001 line exists; RELEASE has none. The renewal behaviour, the RELEASE logid and the
  `dhcp_msg` values are therefore **sahada doğrula** (verified on site), each with a loud check.

The guiding rule of every choice below: a wrong person at high confidence is the worst outcome,
a false "nobody" the second worst. Where the logs cannot decide a case, the confidence is lowered
and the reason stated; of two equally safe designs the simpler one is taken.

## Decision

### Part A — FortiGate DHCP as lease evidence

- **D1 Scope.** FortiGate-only networks are supported when the FortiGate is the clients' DHCP
  server (server mode); its ACK and RELEASE records feed the same lease timeline as MikroTik DHCP,
  so the chain goes to the person. Statically addressed devices end at the internal IP, and the
  lookup says so (except through srcmac, D10). *Alternative:* "not supported" in the offer.
- **D2 Storage: a FortiGate-only time-series table `fg_dhcp`.** One table per parser keeps the
  report cross-check and the parser contract. *Alternatives:* rows in `mt_dhcp` (breaks "only the
  MikroTik parser writes `mt_*`"), a vendor-neutral table with a vendor column (breaks one parser
  per table), the untyped `fg_event.extra` (no index, MACs not normalised).
- **D3 Routing by the shape of the line, with constants only.** A FortiGate `type=event` line
  with `ip`, `mac` and `dhcp_msg` whose `ip` and `mac` convert becomes an `fg_dhcp` row; with the
  three keys but unconvertible values it stays an `fg_event` row and is the parse failure
  `fortigate.dhcp`. Ingest has no settings plumbing, and the report re-parses cited lines with the
  shipped parser. *Alternative:* configurable logid lists in the parser.
- **D4 The meaning is decided in the worker, from settings.** `dhcp.fortigate_logids` maps a logid
  to `assigned` or `deassigned` (default: the documented ACK 0100026001, the RELEASE 0100026002
  derived from the documented numbering); an unmapped logid is counted, never used, and raises
  `R20-lease:fortigate-refused`. A changed map and a lease rebuild reinterpret old rows without
  re-ingesting. *Alternative:* interpret the undocumented `dhcp_msg` or `msg` texts alone.
- **D5 Same timeline rules, one registry of DHCP sources.** An ACK opens a `high` interval like a
  MikroTik `assigned`; a repeated ACK of the pair **from the same table** continues it, an ACK from
  the other DHCP table supersedes it (counted `superseded_across_sources`); a RELEASE closes it.
  `DHCP_TABLES = ("mt_dhcp", "fg_dhcp")` and their tie-break rank are defined once
  (`lookup/refs.py`). A FortiGate lease cites each ACK with its logged lease, as in
  `vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14 (FortiGate DHCP, logid 0100026001,
  lease 604800)`; the action and the IP are read from the fixed `<action> <ip> for` part, and a
  space in a log-supplied interface name becomes `_`. *Alternative:* copy the MikroTik paths for
  FortiGate.
- **D6 The logged lease drives the timeout.** A logged lease of 300–8,640,000 s (the FortiOS 7.6
  CLI reference range of `lease-time`) ends the interval exactly `lease_seconds` after its last
  DHCP evidence, the FortiGate's own end, without the `lease.timeout_percent` margin; `0`
  (unlimited), a missing or out-of-range value use the configured times with the margin and are
  counted (`lease_fallback`, `lease_unlimited`, the alert `fortigate-lease-unlimited`). The
  lookup and the PDF print the lease time and the lease's own last DHCP record **at or before the
  requested moment** (never the line that ended a superseded lease, never a later renewal); more
  than half the lease after it, an `fg_dhcp` chain is `low` with `lease_renewal_not_seen`.
  *Alternative:* a new end reason `expired`.
- **D7 VLAN and server.** `leases.server` is the logged `interface` (so `lease.server_seconds`
  can key on it); the VLAN comes from the subnet (`find_vlan`), never from
  `dhcp.server_vlan_regex`. *Alternative:* a new interface-to-VLAN map.
- **D8 No backfill.** FortiGate DHCP lines loaded before the upgrade stay `fg_event` rows (stored,
  searchable, sealed). When no lease covers the moment, a probe names a covering, unconverted
  record (`fortigate_dhcp_not_converted`, cited only in internal mode and for exact matches); a
  DHCP chain with unconverted FortiGate DHCP lines of its IP or MAC after its lease's last record
  is lowered to `low` (`fortigate_dhcp_not_converted_since_lease`, citing nothing).
  *Alternative:* a one-off re-parse (that belongs to re-loading from the archive).
- **D9 Loud runtime checks.** Two R20 checks in the existing system job, each in its own
  transaction: `R20-lease:<cause>` and `R20-nat:<cause>`, 5651 Temel, never gated; the causes that
  lose chains or may name the wrong person are `high`. Every check counts **usable** evidence
  through one shared predicate (`trust.kept`), never raw row counts.
- **D10 srcmac: stored, never lease evidence, a labelled partial step of a lookup.** A typed
  `fg_traffic.srcmac`; a lookup uses it only on interfaces listed in `lookup.srcmac_interfaces`
  (default none), for a non-infrastructure MAC seen with at most `lookup.srcmac_max_ips` IPv4
  addresses within ±1 hour, holding no lease of another IP then, from a trusted source. A chain
  without a lease then gets the MAC, device and person at `low`, `mac_source = "srcmac"` and
  `srcmac_only`; a chain whose lease MAC differs from srcmac becomes a `low` conflict
  (`srcmac_mismatch`). **This departs from SPEC 9.1** ("FortiGate traffic only proves the IP was
  active, not the MAC"); the departure is limited to listed, directly connected interfaces,
  `low` confidence and a "kısmi kanıt" label. *Alternatives:* srcmac opens medium intervals; store
  it and never use it.
- **D11 Texts say plainly what is partial.** New `chain_missing`, the `srcmac_*` texts (what the
  field is, never that the MAC sent the session), `fortigate_format_unverified` /
  `fortigate_format_verified`, `lease_renewal_not_seen`, `dhcp_sources_overlap`,
  `session_from_unknown_source`, `source_configured_later`, `source_clock_offset`,
  `fortigate_dhcp_not_converted(_since_lease)`, `fortigate_dhcp_no_network` and the exit texts of
  Part B, in Turkish and English.
- **D24 Only configured FortiGate sources give FortiGate evidence.** An unconfigured sender
  (`unknown-<ip>`) is parsed by content, so any host sending syslog could otherwise create `high`
  leases. Untrusted is a source with `unknown = true` or a type other than `fortigate`, except an
  `unknown-<ip>` sender inside the address of an enabled, configured FortiGate source (a changed
  sender address configured afterwards). The set filters `fg_*` rows only; the session search
  keeps every source but marks a match with an untrusted row (`NatMatch.untrusted_sources`).
- **D25 `dhcp_msg` is checked per logid** (`dhcp.fortigate_dhcp_msgs`, default `Ack` for the ACK
  logid, case-insensitive); another value (a NAK, an INFORM reply) is refused and counted.
- **D26 Traffic does not keep a FortiGate lease alive by default**
  (`dhcp.fortigate_traffic_extends = false`): SPEC 9.1 lets traffic extend a lease because RouterOS
  logs no renewals, which most likely does not hold for FortiOS, and traffic shows no MAC, so a
  static device in the pool would keep the former holder named. Missing renewals are detected by
  their positive absence (`fortigate-renewals-missing`, high, or `fortigate-renewals-not-logged`,
  info, on a verified site that never logged one). **This departs from SPEC 9.1** for FortiGate
  leases. *Alternative:* the RouterOS rule for both tables.
- **D27 Two DHCP authorities are detected, not resolved.** One origin definition `(table, vd)`
  for the timeline, the alert and the lookup (the sender and `devid` are not compared, so an HA
  pair is one authority). Two origins in one configured network within the covering window make
  the chain `low` with `dhcp_sources_overlap`; `R20-lease:dhcp-sources-overlap:<net>` is high.
- **D28 Guards on FortiGate DHCP rows.** `dhcp.fortigate_interfaces_excluded` (DHCP over IPsec,
  whose leases end `ipsec-lease-hold` after the tunnel), zero, broadcast and multicast MACs, and
  rows without a usable device time are never lease evidence. A device clock minutes off passes
  them all, so `R20-lease:source-clock` measures each configured source's latest records, and
  every lookup measures the offset of its cited (or, for an empty result, searched) sources at the
  requested moment and lowers its chains with `source_clock_offset` beyond the limit.
- **D29 An unverified format is stated, not hidden.** `dhcp.fortigate_format_verified` (default
  `false`) is set by the administrator after the site check (DEVICE-SETUP.md section 2.4). While
  false, every `fg_dhcp` chain carries `fortigate_format_unverified`; once true,
  `fortigate_format_verified` names who marked it and when.
- **D30 Missing lease evidence is loud, on usable evidence.** One `none:<table>` cause per evidence
  table (`fg_dhcp`, `mt_arp` high; `mt_dhcp` medium and only while no ARP-LOG row arrives either),
  `absent` (info) for a site with no usable evidence in 30 days, one `fortigate-refused` cause with
  the count per reason.
- **D33 A FortiGate client holds one address per server interface and network.** A DHCPDECLINE
  has no documented log record, so an ACK of MAC m for IP y closes m's other open `fg_dhcp`
  interval of the same origin, interface and configured network (compared by the network's
  subnet, with or without a VLAN number), and m's open `mt_dhcp` interval on another IP of the same
  network (a migration), each after applying its own deadline first; nothing is closed outside
  every configured network, where an `fg_dhcp` chain is `low` with `fortigate_dhcp_no_network`.
  `R20-lease:fortigate-decline:<net>` names a static device inside the pool. A MikroTik ACK closes
  nothing this way. *Alternative:* close on any ACK of the MAC (cuts a host on two VLANs).

### Part B — per-exit NAT (dual WAN)

- **D12 One setting `nat.egresses`** replaces `nat.public_ips`, `nat.wan_transip` and
  `lookup.port_hint`: a JSON object keyed by exit name, default `{}` (one implicit double-NAT
  exit). *Alternative:* keep the old keys beside the new one.
- **D13 The exit model.** `mode` `direct` (the FortiGate holds the public address: exact),
  `isp_nat` (the ISP router translates once more: candidates), `not_logged` (the line bypasses
  the FortiGate) or `internal` (a source-NAT address towards a DMZ, another VLAN, a VPN or a
  branch: claimed, never searched); `public` (IPv4 addresses or networks of at least /24, never
  RFC 1918, RFC 6598 or reserved space — one explicit list, `NOT_PUBLIC`, because Python's
  `is_global` calls the RFC 5737 documentation ranges not global), `public_dynamic`, `transips`,
  `interfaces`, `port_preservation`, `label`. An `isp_nat` exit without `transips` is the
  catch-all; at most one.
- **D14 Which exit answers.** The requested public IP is matched against every exit's `public`,
  then its `transips`. An unclaimed address the logs show as the FortiGate's own `transip` near
  the moment is answered as observed (exact with a port); any other unclaimed address is searched
  only in the `isp_nat` exits whose public address is unknown or may change, always with a
  warning, else nobody is listed (`public_ip_no_egress`). A `not_logged` address is `none` unless
  the logs show it; an `internal` address is `none` without a search.
- **D15 The log check generalises Wave 2's.** A configured address counts as the FortiGate's NAT
  address around T when the logs show it as `transip` within ±15 minutes, or show no **private or
  shared** source-NAT address outside the exit's own set (on its interfaces when it declares any).
  A failed check, or a check seen "but not alone" whose search on P finds nothing, becomes a warned
  fallback over the exit's own address and the exits it falls into. A public unclaimed `transip`
  is the FortiGate's own address and never double-NAT evidence.
- **D16 Port preservation is per exit and only ever a ranking hint** (`preserved`: +5 points).
- **D17 `trandisp` values are a setting** (`nat.snat_trandisp`, default `["snat"]`, sahada
  doğrula); sessions with another value are counted and named (`nat_unknown_trandisp`).
- **D18 The migration keeps every answer, and never leaves an unparsable value.** The legacy keys
  are converted into exits by one pure function (a frozen copy in the migration), the result is
  validated, a failure writes `{}` with `R20-nat:conversion-failed`; the legacy rows are kept
  unread for one version so a rollback keeps its NAT values; a legacy row edited after the exits
  raises `R20-nat:legacy-changed`.
- **D19 The outcome and the PDF record the exit used** (`outcome.egress`: resolution, exits,
  addresses searched, last change of each exit; `NatMatch.egress` per match).
- **D20 The administrator reads the values instead of guessing them**: an admin-only endpoint
  lists the `(transip, dstintf, trandisp)` triples of the last 24 h and srcmac counts per
  interface; `R20-nat:unmapped-transip` names an unclaimed NAT address that keeps carrying
  sessions.
- **D31 Observed and dynamic public addresses.** An unclaimed address used as `transip` by a
  session overlapping T ± 15 min is the FortiGate's own (a dynamic direct line); an `isp_nat` exit
  may be `public_dynamic`, and every answer through its listed address says that only the ISP can
  confirm the address at T (`egress_public_dynamic`).
- **D32 A changed exit definition is named.** A status row `nat.egresses_changes` keeps, per exit,
  a fingerprint of the fields that decide its addresses, the time and the author of the last
  content change; a lookup whose exits changed after T carries `egress_changed_after_event` (the
  earlier definitions are in the audit log). Sessions whose records resolve to different exits
  take the exit of the row nearest T (`session_changed_exit`).

### Shared

- **D21 No licence gate, KVKK least privilege, no new permission or audit action.** Everything
  here is 5651 Temel. srcmac and FortiGate DHCP host names are personal data, shown under the same
  permissions as MikroTik leases; the observed endpoint and the lease-evidence card return counts
  and the firewall's own addresses only.
- **D22 Ingest stays within its gate** (at most 5 % slower on the throughput benchmark, the
  load-generator day and a FortiGate-only spool).
- **D23 One ADR**, this one.

## Revisions after review

The design was reviewed seven times before implementation; each round's findings that held were
folded in:

- **Round 3:** an ACK of the other DHCP table supersedes (D5); the FortiGate lease ends at the
  FortiGate's own end, without the margin (D6); a chain past half a lease without a renewal is
  `low`; the source-clock check (D28); the per-reason refusal alerts folded into one (D30).
- **Round 4:** only private or shared NAT addresses count as double-NAT evidence and as unclaimed
  candidates (D15); the exit mode `internal` (D13); the per-lookup clock check (D28); the MikroTik
  variant of D33.
- **Round 5:** a lease's last DHCP evidence is measured at the requested moment; every `isp_nat`
  exit without a public address leaves the D15 own set; an empty search measures its sources'
  clocks; unconverted FortiGate DHCP lines after a lease's last record lower its chain (D8).
- **Round 6:** D33 keyed on the configured network instead of its VLAN number, and `low` outside
  every configured network; the post-lease check of D8 for every DHCP chain by IP or MAC; the D15
  own set without exits on other declared interfaces; the fallback of an empty search on an
  address seen beside double-NAT evidence; FortiGate leases lowered to `low` for the previous
  version at a rollback and restored at the re-upgrade.
- **Round 7:** the post-lease check also reads converted ACKs of the chain's MAC for another
  address, for leases built before their network was entered.

## Consequences

- **Deviations from the specification**, also in ASSUMPTIONS: SPEC 9.1 on srcmac (D10, owner
  point O2) and on traffic extending a lease (D26, O14); the timeline's rules extended for
  FortiGate leases (a cross-table ACK supersedes, D5; a MAC's ACK for another address closes its
  other interval, D33; a logged lease ends without the margin, D6).
- New status rows (not settings, never editable): `nat.egresses_migration`,
  `nat.egresses_changes`, `leases.rollback_lowered`.
- `GET /settings` shows ten more settings in the group "5651 sorgu ve kira zaman çizelgesi"; the
  stored lookup outcome gains fields that older outcomes load without (their defaults).
- Every FortiGate format detail that is not confirmed by an official source or a real sample is
  either configurable with validation or covered by a loud runtime check, and a test pins each
  assumption so it fails when the assumption changes. The site checks are listed for the operator
  in KURULUM.md / INSTALL.md section 16.
- Residual risks (kept, documented): unverified formats on the syslog wire until the site check;
  long leases name a departed client until the lease ends (at `low` in its second half); renewals
  not logged make always-on clients' chains `low` from half their lease; srcmac is only as good as
  the interface setting; exits are not time-scoped (the change warning and the audit history
  remain); a public-looking NAT address behind an ISP router needs an `isp_nat` exit to be found;
  a MikroTik DECLINE is not detected.

## Sources

- FortiOS 7.6 log reference: log ID definitions and numbering; DHCP messages 26001 (DHCP Ack),
  26002 (DHCP Release) and 26003–26012; forward traffic log fields (`srcmac`, `mastersrcmac`,
  `srcmacvendor`, `trandisp`, `transip`, `transport`, `dstintf`) and the device-identification
  fields.
- FortiOS 7.6 CLI reference: `config system dhcp server` (`lease-time`, `server-type`,
  `ipsec-lease-hold`, `conflicted-ip-timeout`, `relay-agent`), `config system interface`
  (`device-identification`, `dhcp-relay-service`), `config log eventfilter`,
  `config log syslogd filter`, `config system ntp`.
- FortiOS 7.6 administration guide: "DHCP servers and relays", "DHCP monitor", device
  identification, central SNAT port preservation.
- Fortinet community technical tip 146254, "Email notification for DHCP IP lease" (the real 26001
  line); technical tip 98534, "How to display the source MAC addresses of 'devices' in logs in
  FortiAnalyzer"; technical tip 185298 (SD-WAN `dstintf`); support forum answers 111355 (26001 at
  every ACK), 155540 and 201683 (device identification behind switches and routers).

## Open points (owner decision)

Each has the default implemented above; changing one is a small follow-up.

| | Question | Default |
|---|---|---|
| O1 | FortiGate-only sites supported? | yes, with the FortiGate as DHCP server (D1) |
| O2 | May srcmac name the MAC in a chain? | yes, listed interfaces only, `low`, partial (D10) |
| O3 | Convert FortiGate DHCP lines loaded before the upgrade? | no; named and lowering (D8) |
| O4 | Address that no exit claims | observed if the logs show it; else exits with unknown or dynamic public address, warned; else nobody (D14, D31) |
| O5 | An `isp_nat` exit whose NAT addresses are not seen near T | widened to the unclaimed private or shared addresses, warned |
| O6 | Legacy NAT keys | converted; kept unread for one version (D18) |
| O7 | IPv6 in exits | refused until IPv6 lookups exist |
| O8 | Re-authentication for the new settings | no |
| O9 | Exits valid from/to a date | no; `egress_changed_after_event` (D32) |
| O10 | Logged FortiGate lease time | used (D6) |
| O11 | DEVICE-SETUP language | the new sections bilingual inside the Turkish file |
| O12 | Private WAN address in examples | `100.64.12.9` / `100.64.13.9` (RFC 6598) |
| O13 | DHCP servers other than MikroTik and FortiGate | not read; MikroTik ARP-LOG at `medium` |
| O14 | Does traffic keep a FortiGate lease alive? | no, loud when renewals seem missing (D26) |
| O15 | A FortiGate ACK of a MAC for another IP | closes its other interval of the same origin, interface and network (D33) |
| O16 | What makes two DHCP authorities | two origins `(table, vd)` (D27) |
| O17 | Fallback of an upgraded site without interfaces | converted exits keep Wave 2's narrowing (D15) |
| O18 | A FortiGate chain past half its lease without a renewal | `low` (D6) |
| O19 | End of a FortiGate lease with a logged lease time | the logged lease after the last DHCP record (D6) |
| O20 | MikroTik DHCP rows stopped | medium, only while no ARP-LOG row arrives either (D30) |
| O21 | An unclaimed public NAT address | the FortiGate's own address, never double-NAT evidence (D15) |
| O22 | A cited source's clock off at the requested moment | measured per lookup, chains `low` (D28) |
