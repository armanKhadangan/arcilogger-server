"""5651 lookup engine (spec 2.3, 9.3-9.5; contract: docs/MVP-CONTRACTS.md section 5; internet
exits: ADR-0016, Wave 3 contract 2.8).

* :func:`attribute` — internal IP + moment → lease → device → person/department (9.4).
* :func:`run_lookup` — answers a request in one of four modes:

  ``exact``
      The public IP is a ``direct`` exit's public address, an exit's FortiGate NAT address or an
      address the logs show as the FortiGate's own NAT address near the moment, and a port is
      given: ``transip = IP AND transport = port`` identifies the session. When the request names
      a destination, only the sessions on that NAT port that went to it are listed; the sessions
      whose logged destination IP/port differs are counted without being listed
      (``exact_other_destinations``). A **domain-only** request is different: a missing
      DNS/web/app log (name resolved by another resolver, DoH, a long-cached answer) does not
      show another destination. Sessions tied to the name are listed; when none is, the sessions
      on the NAT port (to the requested port, if one is given) are listed as
      ``domain_unverified`` with ``exact_domain_unverified``; when some are, the unverified ones
      are only counted (``exact_unverified_destinations``) — never a false "no session".
  ``candidates``
      Double NAT (2.3): the ISP router's translation is unknown, so the sessions in the window
      (± tolerance; only those to the requested destination when one is given — spec 2.3) that
      left through the exit the public IP resolved to are ranked by destination match and time
      distance (:mod:`.sessions`), at most ``lookup.max_candidates`` (``truncated`` +
      explanation beyond that). Also used for an exact address without a port and for a
      destination-only request.
  ``internal``
      The request names an internal IP and/or a MAC address: the attribution chain only.
  ``none``
      Nothing found; the explanations say why and what data would help.

**Internet exits** (``nat.egresses``, :mod:`arcilogger_db.egress`). The requested public IP is
resolved to the exit that answers it (:meth:`_Lookup._resolve_egress`, recorded in
``outcome.egress``): an exit's ``public`` address (``direct``: exact; ``isp_nat``: candidates over
its NAT addresses; ``not_logged``: nobody), an exit's NAT address (``transip``; an ``internal``
exit's address is no internet address and is never searched), an address no exit claims that
the logs show as the FortiGate's own NAT address of a session overlapping the moment
(``observed``, D31), the exits whose public address is unknown or may change
(``public_unknown``), no exits configured (``unconfigured``: one implicit double-NAT exit) or
nobody (``no_egress``). Settings are not time-scoped, so an exact address is checked against the
logs first (D15): it is the FortiGate's NAT address around the moment when the logs show it
there, or show no private or shared NAT address outside the exit's own set; otherwise, or when
it is seen beside such an address while no session through it spans the moment and its search
finds none, the answer is the warned ``fallback`` over the address and the exits the line may
share (``public_ip_not_transip``), never a false "the FortiGate logged no session". Only
private or shared NAT addresses count as double-NAT evidence or as unclaimed candidates: a
public NAT address no exit claims is the FortiGate's own (an IP pool, an unconfigured line).
Every probe reads trusted senders only (D24). Every match names its exit (the row nearest the
moment).

**Data minimisation (spec 3.3), both NAT modes:** when the request names a destination (IP,
port or domain) and no session goes to it, the answer is ``none`` with
``destination_not_found``: unrelated clients are never listed. A request without destination
lists the whole window (candidates) or every session on the NAT port (exact) on purpose.

**Address family** (defence in depth; the API refuses IPv6 lookups, ``ipv6_not_supported``:
there is no IPv6 identity evidence, no NDP or DHCPv6): only sessions that left with an address
(``transip``, else the source) of the requested public IP's family are candidates, so no IPv4
session — the FortiGate's IPv4 WAN address included — is ever a candidate for an IPv6 address.

Every match carries the attribution chain of its source IP at the session moment nearest to the
requested time; IP conflicts appear as chain alternatives (low confidence). A chain's person is
the device-person assignment **valid at that moment** (assignment history, ADR-0008) and the
person's current department (``person_department_current``); only a device without any
assignment history falls back to its owner at lookup time, and the outcome then says so
(``person_link_at_lookup_time``). A match without a chain ends at the session and the internal IP
(``chain_missing``). When no lease chain could be built (an internal lookup without a lease, a
match without a chain or with a srcmac chain only), the newest FortiGate DHCP row of a trusted
sender before the moment is looked up for the address (at most 20; the MAC of a MAC-only
request): a covering ACK that stayed an ``fg_event`` row (loaded before the upgrade, unreadable
fields, loaded by the previous version after a rollback) is named
(``fortigate_dhcp_not_converted``) and, in internal mode and for an exact match, cited in
``unconverted_dhcp``, never as an attribution (ADR-0016 D8). A match with rows of an
unconfigured sender is marked (``NatMatch.untrusted_sources``,
``session_from_unknown_source``; D24). **srcmac** (D10, contract 2.9; off while
``lookup.srcmac_interfaces`` is empty): the srcmac of a session record on a listed interface and
from a trusted sender, whose MAC passes the router guard (no infrastructure MAC, at most
``lookup.srcmac_max_ips`` IPv4 addresses within an hour of the moment, no lease of another
address there), gives a match without a lease, and an internal lookup without one, a ``low``
chain by that MAC (``srcmac_only``); beside a lease it agrees or makes the ``low`` conflict of
D10. It is never lease evidence and is never written anywhere. The clocks of the sources whose
rows the outcome cites (and, when a search found nothing, of the sources whose rows it read) are
measured at the moment from the stored device and receive times; an offset beyond the limit
gives ``source_clock_offset`` and every chain ``low`` (D28). Both functions
only read (JIT compilation is switched off for the caller's transaction, ``SET LOCAL``); the API
stores the lookup and writes the audit entry.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from ipaddress import IPv4Network
from typing import Any

from sqlalchemy import (
    ColumnElement,
    and_,
    any_,
    case,
    cast,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
    text,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, distinct_on
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.egress import (
    CHANGES_KEY,
    IMPLICIT_CATCH_ALL,
    MIGRATION_KEY,
    NOT_PUBLIC,
    Egress,
)
from arcilogger_db.models import (
    FgDhcp,
    FgEvent,
    FgTraffic,
    Message,
    SearchCount,
    Setting,
    Source,
)
from arcilogger_db.settings import get_setting, get_settings
from arcilogger_worker.correlation.watermark import get_watermark
from arcilogger_worker.lookup.attribution import (
    UNCONVERTED_WINDOW,
    Attributor,
    Srcmac,
    StoredLease,
    event_kept,
    usable_lease,
)
from arcilogger_worker.lookup.refs import ARP_TABLE, DHCP_TABLES, traffic_ref
from arcilogger_worker.lookup.sessions import (
    CANDIDATE_MAX_SCORE,
    PORT_HINT_POINTS,
    DomainHits,
    SessionGroup,
    TrafficRow,
    Window,
    destination_match,
    fetch_rows,
    find_domain,
    group_sessions,
    srcmac_addresses_within,
    srcmac_rows_of_mac,
    srcmac_sightings,
    time_points,
    within,
)
from arcilogger_worker.lookup.texts import exit_changes, explain
from arcilogger_worker.lookup.trust import (
    UNKNOWN_PREFIX,
    kept,
    srcmac_ip_counts,
    untrusted_sources,
)
from arcilogger_worker.lookup.types import (
    AttributionChain,
    DataWindow,
    DestinationMatch,
    EgressChange,
    EgressEntry,
    EgressResolution,
    EgressSearched,
    EvidenceRef,
    Explanation,
    LookupEgress,
    LookupMode,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    MatchKind,
    NatMatch,
    RawRef,
)
from arcilogger_worker.lookup.window import data_window

#: DNS answers this old still count for a requested domain (setting
#: ``lookup.dns_lookback_seconds``; resolvers cache answers, so the query precedes the session).
DEFAULT_DNS_LOOKBACK_SECONDS = 3600
#: The log check of an exit's address looks this many seconds around the requested moment
#: (D15, D31), or the tolerance when that is wider.
TRANSIP_PROBE_SECONDS = 900
#: Addresses the probe names (texts, ``outcome.egress.searched``).
PROBE_NAMES = 5
#: A ``trandisp`` value printed as logged only in the setting's own form (log-supplied text).
_TRANDISP = re.compile(r"^[a-z][a-z0-9+_-]{0,15}$")
_DESTINATION_ORDER = {
    "ip_port": 0,
    "domain": 1,
    "ip": 2,
    "domain_other_port": 3,
    "domain_address": 4,
    "ip_other_port": 5,
    "port": 6,
    "domain_unverified": 7,
    "not_requested": 8,
    "none": 9,
}
#: Resolutions whose address no exit claims now (D32: every exit changed after the moment is
#: named; ``observed`` rests on the FortiGate's own record and is left out).
_UNCLAIMED_RESOLUTIONS = frozenset({"public_unknown", "no_egress", "unconfigured"})
#: The D8 probe (2.8.3) looks at the IPs of this many matches without a lease chain of one
#: lookup at most.
PROBE_IPS = 20
#: The router guard of srcmac counts the addresses of a MAC this far around the moment (and
#: the address of a row it uses outside that) and looks for its leases on other addresses
#: there (2.9, D10).
SRCMAC_GUARD = timedelta(hours=1)
#: Rows an internal lookup without a lease reads for the srcmac of an address or a MAC (2.9).
SRCMAC_ROWS = 50
#: srcmac values ``srcmac_ambiguous`` names.
SRCMAC_NAMED = 3
#: The source clock at the requested moment (2.7, D28): a source's ``messages`` rows within this
#: span of the moment by device time, at most :data:`CLOCK_SIDE_ROWS` on each side of it (the
#: rows the search and the lease timeline used, on both sides of a clock step; round 5), of which
#: :data:`CLOCK_MIN_ROWS` make a measurement; a median offset beyond :data:`CLOCK_LIMIT_SECONDS`
#: (a session search: or beyond the tolerance) lowers the chains. The minimum and the limit are
#: those of ``R20-lease:source-clock`` (a test pins them equal). At most :data:`CLOCK_SOURCES`
#: sources are measured, the cited ones first.
CLOCK_SPAN = timedelta(hours=1)
CLOCK_SIDE_ROWS = 2500
CLOCK_MIN_ROWS = 10
CLOCK_LIMIT_SECONDS = 120
CLOCK_SOURCES = 5
#: The tables of the lease timeline: a source measured for one of them has the fixed limit, as
#: every source of an internal lookup has (the lease search uses the moment, not the tolerance;
#: round 6).
LEASE_TABLES = frozenset((*DHCP_TABLES, ARP_TABLE))
#: How the D8 probe cites a FortiGate DHCP record that never entered the lease timeline.
UNCONVERTED_SUMMARY = (
    "FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)"
)


def _ip_order(ip: str) -> tuple[int, int]:
    address = ipaddress.ip_address(ip)
    return address.version, int(address)


def _host(address: str) -> IPv4Network:
    return IPv4Network(f"{address}/32")


def _texts(networks: Sequence[IPv4Network]) -> list[str]:
    return [str(n.network_address) if n.prefixlen == n.max_prefixlen else str(n) for n in networks]


def _text_order(value: str) -> tuple[int, int, str]:
    """IP addresses in address order, anything else (a MAC) after them."""
    try:
        return (*_ip_order(value), "")
    except ValueError:
        return 9, 0, value


def _joined(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "-"


def _whole_seconds(seconds: float) -> int:
    """``seconds`` rounded up to the next whole second, away from zero (120.4 s prints 121): a
    clock offset beyond its limit prints beyond it."""
    whole = math.ceil(abs(seconds))
    return -whole if seconds < 0 else whole


def _lowered(chain: AttributionChain) -> AttributionChain:
    """``chain`` and its alternatives at ``low``, the lease step too (a source's clock was off
    at the moment, D28)."""
    lease = None if chain.lease is None else replace(chain.lease, confidence="low")
    return replace(
        chain,
        confidence="low",
        lease=lease,
        alternatives=tuple(_lowered(other) for other in chain.alternatives),
    )


def attribute(session: Session, ip: str, at: datetime) -> AttributionChain | None:
    """Attribution chain of internal ``ip`` at ``at``; ``None`` when no lease covers it.

    When several MACs held ``ip`` at ``at`` (IP conflict, spec 9.1) the result is the most
    likely chain with the others in ``alternatives`` (low confidence, ``ip_conflict``).
    """
    settings = LookupSettings.from_settings(get_settings(session))
    request = LookupRequest(at=at, internal_ip=ip)
    attributor = StatusRows.load(session).attributor(session, settings, untrusted_sources(session))
    chain = attributor.chain(str(request.internal_ip), request.at)
    return None if chain is None else attributor.checked([chain])[0]


def run_lookup(
    session: Session,
    request: LookupRequest,
    settings: LookupSettings,
    *,
    layout: Layout | None = None,
    now: datetime | None = None,
) -> LookupOutcome:
    """Answer a 5651 request (exact, candidates, internal or none).

    ``layout`` (the server's data directory) lets the data window say whether a day that is not
    in the database is in the sealed vault. The exits are ``settings.egresses()``: the API's
    router parses them before (a value that cannot be parsed is its 503), so a public-IP lookup
    here finds them parsed.
    """
    return _Lookup(session, request, settings, layout=layout, now=now or utc_now()).run()


# -- the rows the lookup reads itself -------------------------------------------------------------


@dataclass(frozen=True)
class StatusRows:
    """The settings rows whose author or time a lookup needs, read by :func:`run_lookup` itself
    in one ``select`` (never through :meth:`LookupSettings.from_settings`): the per-exit change
    record (D32), the exits as v0018 converted them (D15 *as converted*), the ``nat.egresses``
    row (an exit without a change entry falls back to it) and the FortiGate DHCP format check
    (D29)."""

    changes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    converted: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    egresses_at: datetime | None = None
    egresses_by: str | None = None
    format_verified_at: datetime | None = None
    format_verified_by: str | None = None

    @classmethod
    def load(cls, session: Session) -> StatusRows:
        keys = (CHANGES_KEY, MIGRATION_KEY, "nat.egresses", "dhcp.fortigate_format_verified")
        rows = {
            row.key: row
            for row in session.execute(
                select(Setting.key, Setting.value, Setting.updated_at, Setting.updated_by).where(
                    Setting.key.in_(keys)
                )
            )
        }
        changes = rows[CHANGES_KEY].value if CHANGES_KEY in rows else {}
        migration = rows[MIGRATION_KEY].value if MIGRATION_KEY in rows else {}
        converted = migration.get("new") if isinstance(migration, Mapping) else None
        egresses = rows.get("nat.egresses")
        verified = rows.get("dhcp.fortigate_format_verified")
        return cls(
            changes=changes if isinstance(changes, Mapping) else {},
            converted=converted if isinstance(converted, Mapping) else {},
            egresses_at=None if egresses is None else egresses.updated_at,
            egresses_by=None if egresses is None else egresses.updated_by,
            format_verified_at=None if verified is None else verified.updated_at,
            format_verified_by=None if verified is None else verified.updated_by,
        )

    def attributor(
        self, session: Session, settings: LookupSettings, untrusted: frozenset[str]
    ) -> Attributor:
        """The attributor of a lookup: lease facts read for the moment (2.7), the format note
        with this row's time and author (D29)."""
        return Attributor(
            session,
            settings,
            untrusted,
            lookup=True,
            format_verified=(self.format_verified_at, self.format_verified_by),
        )

    def last_change(self, name: str) -> tuple[datetime, str, bool] | None:
        """The last content change of exit ``name``: (time, author, deleted)."""
        entry = self.changes.get(name)
        if isinstance(entry, Mapping):
            try:
                at = datetime.fromisoformat(str(entry["changed_at"]))
                return at, str(entry["changed_by"]), bool(entry.get("deleted", False))
            except (KeyError, ValueError):
                pass
        if self.egresses_at is None or self.egresses_by is None:
            return None
        return self.egresses_at, self.egresses_by, False

    def as_converted(self, egress: Egress) -> bool:
        """Every field but the label is the exit of the same name v0018 converted."""
        stored = self.converted.get(egress.name)
        if not isinstance(stored, Mapping):
            return False
        entry = EgressEntry.from_egress(egress)
        return (
            stored.get("mode") == entry.mode
            and list(stored.get("public") or ()) == list(entry.public)
            and bool(stored.get("public_dynamic")) == entry.public_dynamic
            and list(stored.get("transips") or ()) == list(entry.transips)
            and list(stored.get("interfaces") or ()) == list(entry.interfaces)
            and (stored.get("port_preservation") or "unknown") == entry.port_preservation
        )


# -- the log probe (D15, D31) ---------------------------------------------------------------------


class _Probe:
    """The cached log checks of one lookup: ``fg_traffic`` rows of trusted senders within ±
    :data:`TRANSIP_PROBE_SECONDS` (or the tolerance) of the moment, by row time, except the
    overlap check of an address no exit claims (D31 (a)), which reads sessions overlapping that
    window. A few indexed queries, each run once."""

    def __init__(
        self,
        session: Session,
        settings: LookupSettings,
        exits: Sequence[Egress],
        at: datetime,
        tolerance: int,
        untrusted: frozenset[str],
    ) -> None:
        self.session = session
        self.settings = settings
        self.exits = exits
        seconds = max(TRANSIP_PROBE_SECONDS, tolerance)
        self.low = at - timedelta(seconds=seconds)
        self.high = at + timedelta(seconds=seconds)
        self.untrusted = untrusted
        self.claimed = claimed_networks(exits)
        self._cache: dict[Any, Any] = {}

    def _where(self) -> list[ColumnElement[bool]]:
        clauses = [FgTraffic.time >= self.low, FgTraffic.time <= self.high]
        if self.untrusted:
            clauses.append(_not_in(FgTraffic.source, self.untrusted))
        return clauses

    def _cached(self, key: Any, compute: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = compute()
        return self._cache[key]

    def seen(self, address: str) -> bool:
        """``address`` is the ``transip`` of a row near the moment."""
        return bool(
            self._cached(
                ("seen", address),
                lambda: self.session.scalar(
                    select(exists().where(*self._where(), FgTraffic.transip == address))
                ),
            )
        )

    def seen_any(self, networks: Sequence[IPv4Network]) -> bool:
        """One of ``networks`` (an exit's NAT addresses) is the ``transip`` of a row near it."""
        if not networks:
            return False
        return bool(
            self._cached(
                ("seen_any", tuple(networks)),
                lambda: self.session.scalar(
                    select(exists().where(*self._where(), within(FgTraffic.transip, networks)))
                ),
            )
        )

    def seen_overlap(self, address: str, max_session: int) -> bool:
        """``address`` is the ``transip`` of a session overlapping the probe window, also one
        logged once, at its end, after it (bounded like the session search).

        The first such row in the order of ``ix_fg_traffic_transip_transport_time``, not
        ``EXISTS``: for a busy address the planner expects an early match and scanned the
        partition sequentially, reading its rows before the window too (3.8 guard; 0.9 s on a
        partition of 10 days at 1 M rows a day). In time order it read every address's rows of
        the range when none of the address's own overlaps (0.29 s at 1 M a day for an address
        with 1 % of them). In the index's order the scan reads the address's own rows only.

        Asked in two steps, the probe window first, then the tail after it: over daily chunks
        the ordered scan merges the chunks and reads each one to its first overlapping row or
        to its end, so a busy address had its whole next day read (0.18 s, 471,352 rows
        removed). An address with no overlapping session has all its rows of the tail read
        (``duration`` is in no index)."""

        def first(*clauses: ColumnElement[bool]) -> bool:
            started = FgTraffic.time - func.make_interval(
                0, 0, 0, 0, 0, 0, func.coalesce(FgTraffic.duration, 0)
            )
            where = [FgTraffic.transip == address, *clauses, started <= self.high]
            if self.untrusted:
                where.append(_not_in(FgTraffic.source, self.untrusted))
            query = (
                select(FgTraffic.time)
                .where(*where)
                .order_by(FgTraffic.transip, FgTraffic.transport, FgTraffic.time)
                .limit(1)
            )
            return self.session.scalar(query) is not None

        def compute() -> bool:
            end = self.high + timedelta(seconds=max_session)
            return first(FgTraffic.time >= self.low, FgTraffic.time <= self.high) or first(
                FgTraffic.time > self.high, FgTraffic.time <= end
            )

        return bool(self._cached(("overlap", address), compute))

    def _distinct(self, *clauses: ColumnElement[bool]) -> list[str]:
        query = (
            select(FgTraffic.transip)
            .where(*self._where(), FgTraffic.transip.is_not(None), *clauses)
            .group_by(FgTraffic.transip)
            .order_by(FgTraffic.transip)
            .limit(PROBE_NAMES)
        )
        return [str(address) for address in self.session.scalars(query)]

    def unclaimed(self) -> tuple[list[str], list[str]]:
        """The NAT addresses no exit claims near the moment: (private or shared, public), at
        most :data:`PROBE_NAMES` each, with a listed ``trandisp``."""

        def compute() -> tuple[list[str], list[str]]:
            listed = FgTraffic.trandisp == _any_text(self.settings.snat_trandisp)
            free = [not_(within(FgTraffic.transip, self.claimed))] if self.claimed else []
            private = self._distinct(listed, within(FgTraffic.transip, NOT_PUBLIC), *free)
            public = self._distinct(listed, not_(within(FgTraffic.transip, NOT_PUBLIC)), *free)
            return private, public

        result: tuple[list[str], list[str]] = self._cached(("unclaimed",), compute)
        return result

    def outside(self, egress: Egress) -> list[str]:
        """The D15 evidence against ``egress``: private or shared NAT addresses outside its own
        set (:func:`own_networks`), with a listed ``trandisp``, on its interfaces when it declares
        any; at most :data:`PROBE_NAMES`."""

        def compute() -> list[str]:
            own = own_networks(egress, self.exits)
            clauses = [
                FgTraffic.trandisp == _any_text(self.settings.snat_trandisp),
                within(FgTraffic.transip, NOT_PUBLIC),
            ]
            if own:
                clauses.append(not_(within(FgTraffic.transip, own)))
            if egress.interfaces:
                clauses.append(FgTraffic.dstintf == _any_text(egress.interfaces))
            return self._distinct(*clauses)

        result: list[str] = self._cached(("outside", egress.name), compute)
        return result

    def interface_evidence(self, egress: Egress, own: Sequence[IPv4Network]) -> tuple[bool, bool]:
        """Rows of ``own`` (the exit's own addresses) near the moment: whether there are any,
        and whether one of them left by one of the exit's interfaces."""

        def compute() -> tuple[bool, bool]:
            mine = within(FgTraffic.transip, own)
            there = exists().where(*self._where(), mine)
            on_mine = exists().where(
                *self._where(), mine, FgTraffic.dstintf == _any_text(egress.interfaces)
            )
            row = self.session.execute(select(there, on_mine)).one()
            return bool(row[0]), bool(row[1])

        if not own:
            return False, False
        result: tuple[bool, bool] = self._cached(("interfaces", egress.name), compute)
        return result


def _any_text(values: Sequence[str]) -> Any:
    return any_(cast(literal(list(values), ARRAY(Text)), ARRAY(Text)))


def _not_in(column: Any, values: frozenset[str]) -> ColumnElement[bool]:
    clause: ColumnElement[bool] = not_(column == _any_text(sorted(values)))
    return clause


def claimed_networks(exits: Sequence[Egress]) -> tuple[IPv4Network, ...]:
    """What the exits claim: every exit's NAT addresses (an ``internal`` exit's included) and
    every ``direct`` exit's public addresses; the rest is *unclaimed*."""
    found: list[IPv4Network] = []
    for egress in exits:
        found += egress.transips
        if egress.mode == "direct":
            found += egress.public
    return tuple(dict.fromkeys(found))


def leaves_own_set(egress: Egress, other: Egress) -> bool:
    """``other`` is in G(E): an ``isp_nat`` exit without a public address (nothing says it is
    not E's own line behind an ISP router, round 5), or, when both declare interfaces, an exit
    of any mode on interfaces disjoint from E's (round 6)."""
    if other.mode == "isp_nat" and not other.public:
        return True
    return bool(
        egress.interfaces
        and other.interfaces
        and not set(egress.interfaces) & set(other.interfaces)
    )


def own_networks(egress: Egress, exits: Sequence[Egress]) -> tuple[IPv4Network, ...]:
    """own(E) of the D15 check: E's own addresses and the addresses claimed by every other exit
    that does not leave the set (:func:`leaves_own_set`); taken per address, so an address one
    staying exit claims stays in."""
    found: list[IPv4Network] = [*egress.public, *egress.transips]
    for other in exits:
        if other.name != egress.name and not leaves_own_set(egress, other):
            found += claimed_networks([other])
    return tuple(dict.fromkeys(found))


# -- srcmac (2.9, D10) ----------------------------------------------------------------------------


def _nearest_first(at: datetime) -> Any:
    return lambda row: (abs((row.time - at).total_seconds()), row.time, row.id)


class _SrcmacGuard:
    """When a session record's srcmac may name a MAC in a lookup (2.9, D10), the rows being of a
    listed interface and a trusted sender already: the MAC is no infrastructure MAC
    (``srcmac_infrastructure``), shows at most ``lookup.srcmac_max_ips`` IPv4 addresses on that
    interface within an hour of the moment (``srcmac_shared``; one
    :func:`.trust.srcmac_ip_counts` query per lookup, the one the alert and the observed
    endpoint count with), and holds no lease of another address there
    (``srcmac_other_lease``). A considered row outside that hour (a session overlapping the
    lookup window, logged at its end) adds its own address to its MAC's count unless the hour
    shows it already, so a row used is always counted; no row widens the hour of another MAC.
    A refusal adds its explanation to ``notes``."""

    def __init__(
        self,
        session: Session,
        settings: LookupSettings,
        attributor: Attributor,
        at: datetime,
        untrusted: frozenset[str],
        notes: list[Explanation],
    ) -> None:
        self.session = session
        self.settings = settings
        self.attributor = attributor
        self.at = at
        self.low, self.high = at - SRCMAC_GUARD, at + SRCMAC_GUARD
        self.untrusted = untrusted
        self.notes = notes
        self._counts: dict[tuple[str, str], int] = {}
        self._leases: dict[str, list[StoredLease]] = {}
        self._decided: dict[tuple[str, str, str], bool] = {}

    def load(self, rows: Sequence[TrafficRow]) -> None:
        """Read what the guard needs for every row the lookup considers, once."""
        if not rows:
            return
        macs = {str(row.srcmac) for row in rows}
        interfaces = self.settings.srcmac_interfaces
        self._counts = srcmac_ip_counts(
            self.session, self.low, self.high, interfaces, self.untrusted, macs
        )
        outside = {
            (str(row.srcintf), str(row.srcmac), row.srcip)
            for row in rows
            if not self.low <= row.time <= self.high
        }
        within = srcmac_addresses_within(
            self.session, keys=outside, low=self.low, high=self.high, untrusted=self.untrusted
        )
        for interface, mac, _ip in outside - within:
            self._counts[interface, mac] = self._counts.get((interface, mac), 0) + 1
        self._leases = self.attributor.leases_of_macs(macs, self.low, self.high)
        self.attributor.prefetch_devices(macs)

    def usable(self, row: TrafficRow) -> Srcmac | None:
        """The srcmac of ``row`` (a listed interface, a trusted sender) when it may be used."""
        mac, interface = str(row.srcmac), str(row.srcintf)
        key = (mac, interface, row.srcip)
        if key not in self._decided:
            self._decided[key] = self._allowed(mac, interface, row.srcip)
        return (mac, traffic_ref(row), interface) if self._decided[key] else None

    def _allowed(self, mac: str, interface: str, ip: str) -> bool:
        at = self.at.isoformat()
        if self.attributor.is_infrastructure(mac):
            self.notes.append(explain("srcmac_infrastructure", mac=mac))
            return False
        count = self._counts.get((interface, mac), 0)
        if count > self.settings.srcmac_max_ips:
            self.notes.append(explain("srcmac_shared", mac=mac, at=at, count=count))
            return False
        other = next(
            (
                lease
                for lease in self._leases.get(mac, ())
                if lease.ip != ip
                and lease.start <= self.high
                and self.attributor.covered_until(lease) >= self.low
            ),
            None,
        )
        if other is not None:
            self.notes.append(explain("srcmac_other_lease", mac=mac, at=at, other_ip=other.ip))
            return False
        return True


# -- the resolution and its search ----------------------------------------------------------------


@dataclass(frozen=True)
class _Part:
    """One part of a candidate search: the exit whose search it is (its port preservation
    ranks the candidates it finds), its NAT addresses, the unclaimed predicate, and the
    interface condition (``None``: none)."""

    egress: Egress | None
    transips: tuple[IPv4Network, ...] = ()
    unclaimed: bool = False
    dstintfs: tuple[str, ...] | None = None


@dataclass(frozen=True)
class _DhcpLine:
    """A FortiGate DHCP line that stayed an ``fg_event`` row (D8): the probe's key (the IP, or
    the MAC of a MAC-only request), the row and the DHCP fields it needs (log-supplied text)."""

    key: str
    id: int
    time: datetime
    logid: str | None
    ip: str | None
    lease: str | None
    interface: str | None
    source: str
    raw_day: Any
    raw_line: int
    raw_offset: int

    def ref(self) -> EvidenceRef:
        """The row as the outcome cites it: never an attribution (2.8.3)."""
        raw = RawRef(self.source, self.raw_day, self.raw_line, self.raw_offset)
        return EvidenceRef("fg_event", self.id, self.time, raw, UNCONVERTED_SUMMARY)


@dataclass
class _Plan:
    """How a public IP is answered (2.8.3): the resolution, the exits recorded, the search
    (``exact`` on the address with the requested port, else candidate ``parts``) and the
    notes."""

    resolution: EgressResolution
    exits: list[Egress]
    double_nat: bool
    #: Search ``transip = address`` (with the port when one is given), else the parts.
    address: str | None = None
    parts: list[_Part] = field(default_factory=list)
    notes: list[Explanation] = field(default_factory=list)
    #: No search at all (``none``): ``not_logged``, ``internal``, ``no_egress``.
    nobody: bool = False
    #: The D15 check passed "seen, but not alone": the exit and its outside evidence, for the
    #: fallback when nothing went through the address (D15).
    seen_beside: tuple[Egress, list[str]] | None = None
    outside_seen: list[str] = field(default_factory=list)


class _Lookup:
    def __init__(
        self,
        session: Session,
        request: LookupRequest,
        settings: LookupSettings,
        *,
        layout: Layout | None,
        now: datetime,
    ) -> None:
        self.session = session
        self.request = request
        self.settings = settings
        self.layout = layout
        self.now = now
        self.tolerance = (
            request.tolerance_seconds
            if request.tolerance_seconds is not None
            else settings.tolerance_seconds
        )
        self.window = Window(request.at, self.tolerance, settings.max_session_seconds)
        #: Made in :meth:`run` from the status rows (the format note needs one of them).
        self.attributor: Attributor
        self.explanations: list[Explanation] = []
        self.truncated = False
        self.status = StatusRows()
        self._exits: tuple[Egress, ...] | None = None
        self._probe: _Probe | None = None
        self._srcmac: _SrcmacGuard | None = None
        self._untrusted: frozenset[str] | None = None
        #: What an internal lookup could not build a chain for: ``("ip", address, at)``, or
        #: ``("mac", mac, at)`` for a MAC-only request (the D8 probe, 2.8.3).
        self._unbuilt: tuple[str, str, datetime] | None = None
        #: The clock measurement per source (2.7): (median offset in seconds, rows) or ``None``.
        self._clocks: dict[str, tuple[float, int] | None] = {}

    # -- entry ------------------------------------------------------------------------------------

    def run(self) -> LookupOutcome:
        # The lookup queries are bounded index scans; the planner's cost estimate of the
        # correlated domain sub-queries crosses ``jit_above_cost`` and JIT compilation then
        # takes longer (~0.8 s) than the query itself. Off for this transaction only.
        self.session.execute(text("SET LOCAL jit = off"))
        self.status = StatusRows.load(self.session)
        self.attributor = self.status.attributor(self.session, self.settings, self.untrusted)
        window, notes = data_window(self.session, self.request.at, layout=self.layout, now=self.now)
        self.explanations.extend(notes)
        self._watermark_note()
        attribution: AttributionChain | None = None
        matches: list[NatMatch] = []
        mode: LookupMode
        plan: _Plan | None = None
        request = self.request
        if request.is_internal:
            attribution = self._internal()
            mode = "internal" if attribution is not None else "none"
        elif request.public_ip is None:
            matches = self._candidates(None)
            mode = "candidates" if matches else "none"
        else:
            plan = self._resolve_egress()
            mode, matches, plan = self._answer(plan)
        attribution, matches = self._across_chains(attribution, matches)
        missing = sum(1 for match in matches if match.chain is None)
        if missing:
            self.explanations.append(explain("chain_missing", count=missing))
        unconverted = self._unconverted(matches)
        attribution, matches = self._source_clocks(attribution, matches, plan)
        return self._outcome(mode, matches, attribution, window, plan, unconverted)

    def _across_chains(
        self, attribution: AttributionChain | None, matches: list[NatMatch]
    ) -> tuple[AttributionChain | None, list[NatMatch]]:
        """Every chain of the outcome after the checks of 2.7 that read across them (D27, the
        post-lease check): one pass, one query per check for the whole lookup."""
        chains = [m.chain for m in matches if m.chain is not None]
        if attribution is not None:
            chains.append(attribution)
        checked = iter(self.attributor.checked(chains))
        matches = [m if m.chain is None else replace(m, chain=next(checked)) for m in matches]
        return (None if attribution is None else next(checked)), matches

    def _outcome(
        self,
        mode: LookupMode,
        matches: list[NatMatch],
        attribution: AttributionChain | None,
        window: DataWindow,
        plan: _Plan | None,
        unconverted: tuple[EvidenceRef, ...] = (),
    ) -> LookupOutcome:
        chains = [m.chain for m in matches if m.chain is not None]
        if attribution is not None:
            chains.append(attribution)
        every = [chain for c in chains for chain in (c, *c.alternatives)]
        if any(c.person is not None and c.person.link == "current" for c in every):
            self.explanations.append(explain("person_link_at_lookup_time"))
        if any(
            c.person is not None and c.person.link == "history" and c.department_source == "person"
            for c in every
        ):
            self.explanations.append(explain("person_department_current"))
        egress = None if plan is None else self._egress_record(plan)
        unique: dict[str, Explanation] = {}
        for item in self.explanations:
            unique.setdefault(item.key, item)
        return LookupOutcome(
            request=self.request,
            mode=mode,
            matches=tuple(matches),
            double_nat=self._no_direct_exit() if plan is None else plan.double_nat,
            attribution=attribution,
            explanations=tuple(unique.values()),
            data_window=window,
            tolerance_seconds=self.tolerance,
            generated_at=self.now,
            truncated=self.truncated,
            egress=egress,
            unconverted_dhcp=unconverted,
        )

    def _watermark_note(self) -> None:
        watermark = get_watermark(self.session)
        if watermark is not None and self.window.low > watermark:
            self.explanations.append(
                explain("lease_timeline_behind", watermark=watermark.isoformat())
            )

    # -- the exits --------------------------------------------------------------------------------

    @property
    def exits(self) -> tuple[Egress, ...]:
        if self._exits is None:
            self._exits = self.settings.egresses()
        return self._exits

    @property
    def untrusted(self) -> frozenset[str]:
        if self._untrusted is None:
            self._untrusted = untrusted_sources(self.session)
        return self._untrusted

    @property
    def probe(self) -> _Probe:
        if self._probe is None:
            self._probe = _Probe(
                self.session,
                self.settings,
                self.exits,
                self.request.at,
                self.tolerance,
                self.untrusted,
            )
        return self._probe

    @property
    def srcmac(self) -> _SrcmacGuard:
        if self._srcmac is None:
            self._srcmac = _SrcmacGuard(
                self.session,
                self.settings,
                self.attributor,
                self.request.at,
                self.untrusted,
                self.explanations,
            )
        return self._srcmac

    def _no_direct_exit(self) -> bool:
        """``double_nat`` of a request without a public IP: no line on which the FortiGate holds
        the public address itself (Wave 2's "no public IP configured"). An unreadable
        ``nat.egresses`` (the router refuses public-IP lookups then) counts as double NAT."""
        try:
            return not any(egress.mode == "direct" for egress in self.exits)
        except ValueError:
            return True

    def _resolve_egress(self) -> _Plan:
        """Which exit answers the requested public IP (2.8.3), in this order: an exit's public
        address, an exit's NAT address, an address the logs show as the FortiGate's own NAT
        address (D31 (a), with or without exits), no exits, the exits whose public address is
        unknown or may change, nobody."""
        address = str(self.request.public_ip)
        exits = self.exits
        probe = self.probe
        public = next((e for e in exits if e.claims_public(address)), None)
        if public is not None:
            if public.mode == "not_logged":
                if probe.seen_overlap(address, self.settings.max_session_seconds):
                    return self._observed([public])
                return _Plan(
                    "public",
                    [public],
                    double_nat=False,
                    nobody=True,
                    notes=[explain("egress_not_logged", address=address, egress=public.name)],
                )
            if public.mode == "direct":
                return self._checked("public", public, double_nat=False)
            return self._isp_nat(public)
        nat = next((e for e in exits if e.claims_transip(address)), None)
        if nat is not None:
            if nat.mode == "internal":
                return _Plan(
                    "transip",
                    [nat],
                    double_nat=False,
                    nobody=True,
                    notes=[explain("egress_internal", address=address, egress=nat.name)],
                )
            plan = self._checked("transip", nat, double_nat=True)
            if plan.resolution == "transip":
                plan.notes.append(self._egress_search(nat))
            return plan
        if probe.seen_overlap(address, self.settings.max_session_seconds):
            return self._observed([])
        if not exits:
            return _Plan(
                "unconfigured",
                [IMPLICIT_CATCH_ALL],
                double_nat=True,
                parts=[_Part(IMPLICIT_CATCH_ALL, unclaimed=True)],
                notes=[explain("egress_unconfigured")],
            )
        unknown = [e for e in exits if e.public_unknown]
        if unknown:
            notes = [
                explain(
                    "egress_public_unknown",
                    address=address,
                    egresses=_joined([e.name for e in unknown]),
                )
            ]
            parts = []
            for egress in unknown:
                interfaces = self._interfaces(egress)
                if egress.catch_all:
                    parts.append(_Part(egress, unclaimed=True, dstintfs=interfaces))
                    continue
                parts.append(_Part(egress, egress.transips, dstintfs=interfaces))
                widened = self._not_seen(egress, interfaces)
                if widened is not None:
                    parts.append(widened[0])
                    notes.append(widened[1])
            return _Plan("public_unknown", unknown, double_nat=True, parts=parts, notes=notes)
        return _Plan(
            "no_egress",
            list(exits),
            double_nat=False,
            nobody=True,
            notes=[explain("public_ip_no_egress", address=address)],
        )

    def _observed(self, exits: list[Egress]) -> _Plan:
        """The FortiGate itself used the address near the moment (D31 (a), D14): exact with a
        port, candidates ``transip = address`` without one."""
        address = str(self.request.public_ip)
        at = self.request.at.isoformat()
        if exits:
            note = explain(
                "egress_not_logged_but_seen", address=address, egress=exits[0].name, at=at
            )
        else:
            note = explain("egress_observed_not_configured", address=address, at=at)
        return _Plan("observed", exits, double_nat=False, address=address, notes=[note])

    def _checked(self, resolution: EgressResolution, egress: Egress, *, double_nat: bool) -> _Plan:
        """A ``direct`` public address or an exit's NAT address, checked against the logs (D15):
        it passes when the logs show it near the moment, or show no private or shared NAT
        address outside the exit's own set; otherwise the answer is the fallback."""
        address = str(self.request.public_ip)
        seen = self.probe.seen(address)
        outside = self.probe.outside(egress)
        if not seen and outside:
            return self._fallback(egress, outside)
        plan = _Plan(resolution, [egress], double_nat=double_nat, address=address)
        if seen and outside:
            plan.seen_beside = (egress, outside)
        return plan

    def _egress_search(self, egress: Egress) -> Explanation:
        return explain(
            "egress_search",
            egress=egress.name,
            transips=_joined(_texts(egress.transips)),
            interfaces=_joined(list(egress.interfaces)),
        )

    def _isp_nat(self, egress: Egress) -> _Plan:
        """An ``isp_nat`` exit's public address: candidates over its NAT addresses (a catch-all:
        the unclaimed ones), widened to the unclaimed addresses when its own are not seen near
        the moment while unclaimed ones are (O5)."""
        address = str(self.request.public_ip)
        interfaces = self._interfaces(egress)
        plan = _Plan("public", [egress], double_nat=True)
        if egress.catch_all:
            plan.parts = [_Part(egress, unclaimed=True, dstintfs=interfaces)]
            plan.notes.append(self._egress_search(egress))
        else:
            plan.parts = [_Part(egress, egress.transips, dstintfs=interfaces)]
            widened = self._not_seen(egress, interfaces)
            if widened is not None:
                plan.parts.append(widened[0])
                plan.notes.append(widened[1])
            else:
                plan.notes.append(self._egress_search(egress))
        if egress.public_dynamic:
            plan.notes.append(
                explain(
                    "egress_public_dynamic",
                    egress=egress.name,
                    address=address,
                    at=self.request.at.isoformat(),
                )
            )
        return plan

    def _not_seen(
        self, egress: Egress, interfaces: tuple[str, ...] | None
    ) -> tuple[_Part, Explanation] | None:
        """O5: the exit's NAT addresses have no row near the moment while unclaimed private or
        shared ones have: its search also covers the unclaimed ones (a long session through its
        address logged after the window still overlaps it), on its interfaces as well."""
        private, _public = self.probe.unclaimed()
        if self.probe.seen_any(egress.transips) or not private:
            return None
        note = explain(
            "egress_transip_not_seen",
            egress=egress.name,
            transips=_joined(_texts(egress.transips)),
            at=self.request.at.isoformat(),
            seen=_joined(private),
            interfaces=_joined(list(interfaces or ())),
        )
        return _Part(egress, unclaimed=True, dstintfs=interfaces), note

    def _fallback_exits(self, egress: Egress) -> list[Egress]:
        """F: the ``isp_nat`` exits without a public address that share an interface with E,
        or, when neither declares interfaces, only when both are as converted by v0018 (legacy
        (b), (d)); never E itself."""
        found = []
        for other in self.exits:
            if other.name == egress.name or other.mode != "isp_nat" or other.public:
                continue
            if egress.interfaces and other.interfaces:
                shares = bool(set(egress.interfaces) & set(other.interfaces))
            else:
                shares = (
                    not egress.interfaces
                    and not other.interfaces
                    and self.status.as_converted(egress)
                    and self.status.as_converted(other)
                )
            if shares:
                found.append(other)
        return found

    def _fallback(self, egress: Egress, outside: list[str]) -> _Plan:
        """The check failed, or passed "seen, but not alone" and nothing went through the
        address: candidates over the requested address (never E's other public addresses) and
        F's NAT addresses (a catch-all F, or no F: the unclaimed ones), on E's interfaces when
        it declares any; F's addresses not seen near the moment: O5."""
        address = str(self.request.public_ip)
        interfaces = self._interfaces(egress)
        fallen = self._fallback_exits(egress)
        parts = [_Part(egress, (_host(address),), dstintfs=interfaces)]
        notes = [explain("public_ip_not_transip", address=address)]
        for other in fallen:
            if other.catch_all:
                parts.append(_Part(other, unclaimed=True, dstintfs=interfaces))
                continue
            parts.append(_Part(other, other.transips, dstintfs=interfaces))
            widened = self._not_seen(other, interfaces)
            if widened is not None:
                parts.append(widened[0])
                notes.append(widened[1])
        if not fallen:
            parts.append(_Part(None, unclaimed=True, dstintfs=interfaces))
        return _Plan(
            "fallback",
            [egress, *fallen],
            double_nat=True,
            parts=parts,
            notes=notes,
            outside_seen=outside[:PROBE_NAMES],
        )

    def _interfaces(self, egress: Egress) -> tuple[str, ...] | None:
        """The interface condition of an exit's search: its ``interfaces``, dropped only on
        positive evidence that the names are wrong — rows of its own addresses near the moment
        exist and none left by one of them (``egress_interface_not_seen``). An exit that shares
        a NAT address with another keeps it (``egress_interface_not_seen_kept``), and so does a
        catch-all, whose unclaimed addresses may be another line's. An idle line (no row of its
        addresses) keeps it too."""
        if not egress.interfaces:
            return None
        own = list(egress.transips) + (list(egress.public) if egress.mode == "direct" else [])
        rows, on_interfaces = self.probe.interface_evidence(egress, own)
        if not rows or on_interfaces:
            return egress.interfaces
        params = {
            "egress": egress.name,
            "interfaces": _joined(list(egress.interfaces)),
            "at": self.request.at.isoformat(),
        }
        shared = any(
            other.name != egress.name
            and any(a.overlaps(b) for a in egress.transips for b in other.transips)
            for other in self.exits
        )
        if shared:
            self.explanations.append(explain("egress_interface_not_seen_kept", **params))
            return egress.interfaces
        self.explanations.append(explain("egress_interface_not_seen", **params))
        return None

    def _answer(self, plan: _Plan) -> tuple[LookupMode, list[NatMatch], _Plan]:
        """Run the plan's search (a "seen, but not alone" pass whose address carried nothing at
        the moment and whose search would find no session becomes the fallback, its own notes
        discarded)."""
        request = self.request
        if plan.nobody:
            self.explanations.extend(plan.notes)
            self._unmapped_note(plan)
            return "none", [], plan
        if plan.seen_beside is not None and not self._carried(str(plan.address)):
            egress, outside = plan.seen_beside
            plan = self._fallback(egress, outside)
        if plan.address is not None and request.public_port is not None:
            matches = self._exact(plan.address)
            self.explanations.extend(plan.notes)
            self._unmapped_note(plan)
            return ("exact" if matches else "none"), matches, plan
        matches = self._candidates(plan)
        self._unmapped_note(plan)
        return ("candidates" if matches else "none"), matches, plan

    def _carried(self, address: str) -> bool:
        """The search on ``address`` keeps its answer (D15 "seen, but not alone", round 7): a
        session of a trusted sender through it spans the moment itself, on any port and to any
        destination (the FortiGate held the address then, so the line was not double-NATed), or
        the search would find a session through it in the window (with the requested port when
        one is given), before any destination filter. A session that ended before the moment
        or began after it shows neither: within the tolerance of a change of the line's mode it
        belongs to the other mode."""
        request = self.request
        started = FgTraffic.time - func.make_interval(
            0, 0, 0, 0, 0, 0, func.coalesce(FgTraffic.duration, 0)
        )
        common = [FgTraffic.transip == address]
        if request.protocol is not None:
            common.append(FgTraffic.proto == request.protocol)
        if self.untrusted:
            common.append(_not_in(FgTraffic.source, self.untrusted))
        spans = [
            FgTraffic.time >= request.at,
            FgTraffic.time <= request.at + timedelta(seconds=self.settings.max_session_seconds),
            started <= request.at,
        ]
        found = [
            FgTraffic.time >= self.window.low,
            FgTraffic.time <= self.window.scan_end,
            started <= self.window.high,
        ]
        if request.public_port is not None:
            found.append(FgTraffic.transport == request.public_port)
        query = select(or_(exists().where(*common, *spans), exists().where(*common, *found)))
        return bool(self.session.scalar(query))

    def _unmapped_note(self, plan: _Plan) -> None:
        """``egress_unmapped_seen``: NAT addresses no exit claims near the moment (public ones
        included), while exits are configured: an incomplete configuration always shows. The
        requested address is left out only when it was answered as observed (its own warning
        names it); an ``isp_nat`` exit's public address the FortiGate itself used (a line
        switched to bridge mode) is named."""
        if not self.exits:
            return
        private, public = self.probe.unclaimed()
        observed = self.request.public_ip if plan.resolution == "observed" else None
        seen = [a for a in [*private, *public] if a != observed][:PROBE_NAMES]
        if seen:
            self.explanations.append(
                explain(
                    "egress_unmapped_seen", at=self.request.at.isoformat(), addresses=_joined(seen)
                )
            )

    def _egress_record(self, plan: _Plan) -> LookupEgress:
        """``outcome.egress`` (2.10): the resolution, the exits, what was searched and the exits'
        last content changes (D32), with ``egress_changed_after_event`` when one lies after the
        moment."""
        transips: list[str] = []
        interfaces: list[str] = []
        if plan.address is not None:
            transips.append(plan.address)
        for part in plan.parts:
            transips += [a for a in _texts(part.transips) if a not in transips]
            interfaces += [i for i in part.dstintfs or () if i not in interfaces]
        private, public = self.probe.unclaimed()
        searched = EgressSearched(
            transips=tuple(transips),
            unclaimed=any(part.unclaimed for part in plan.parts),
            unclaimed_seen=tuple([*private, *public][:PROBE_NAMES]),
            outside_seen=tuple(plan.outside_seen) if plan.resolution == "fallback" else (),
            interfaces=tuple(interfaces),
            interface_filter=any(part.dstintfs is not None for part in plan.parts),
        )
        changes = self._changes(plan)
        after = [change for change in changes if change.after_event]
        if after:
            text_tr, text_en = exit_changes(after, self.exits)
            self.explanations.append(
                explain(
                    "egress_changed_after_event",
                    at=self.request.at.isoformat(),
                    changes_tr=text_tr,
                    changes_en=text_en,
                )
            )
        return LookupEgress(
            resolution=plan.resolution,
            egresses=tuple(EgressEntry.from_egress(e) for e in plan.exits),
            searched=searched,
            changes=tuple(changes),
            changed_after_event=bool(after),
        )

    def _changes(self, plan: _Plan) -> list[EgressChange]:
        """The last content change of each exit used and, for an address no exit claims now,
        of every exit (deleted ones included) changed after the moment (D32). A current exit
        without an entry in the change record (a value written around ``change_setting``) falls
        back to the ``nat.egresses`` row's time and author, so the warning errs on showing."""
        at = self.request.at
        found: dict[str, EgressChange] = {}
        for egress in plan.exits:
            last = self.status.last_change(egress.name) if egress.name else None
            if last is not None:
                changed_at, by, deleted = last
                found[egress.name] = EgressChange(
                    egress.name, changed_at, by, deleted, after_event=changed_at > at
                )
        if plan.resolution in _UNCLAIMED_RESOLUTIONS:
            for name in sorted({*self.status.changes, *(egress.name for egress in self.exits)}):
                last = self.status.last_change(name)
                if name not in found and last is not None and last[0] > at:
                    found[name] = EgressChange(name, last[0], last[1], last[2], after_event=True)
        return list(found.values())

    # -- internal ---------------------------------------------------------------------------------

    def _internal(self) -> AttributionChain | None:
        request = self.request
        at = request.at
        ip, mac = request.internal_ip, request.mac
        if ip is None and mac is not None:
            leases = [
                lease
                for lease in self.attributor.leases_of_mac(mac, at, at)
                if self.attributor.covers(lease, at)
            ]
            if not leases:
                self.explanations.append(explain("no_lease_for_mac", mac=mac, at=at.isoformat()))
                self._unbuilt = ("mac", mac, at)
                return self._srcmac_of_mac(mac)
            ips = sorted({lease.ip for lease in leases})
            if len(ips) > 1:
                self.explanations.append(explain("mac_multiple_ips", mac=mac, ips=", ".join(ips)))
            latest = max(leases, key=lambda lease: (lease.start, lease.id))
            ip = latest.ip
        if ip is None:  # pragma: no cover - LookupRequest guarantees ip or mac
            return None
        chain = self.attributor.chain(ip, at, prefer_mac=mac)
        if chain is None:
            before, after = self.attributor.nearest(ip, at)
            self.explanations.append(
                explain(
                    "no_lease",
                    ip=ip,
                    at=at.isoformat(),
                    before=None if before is None else before.isoformat(),
                    after=None if after is None else after.isoformat(),
                )
            )
            self._unbuilt = ("ip", ip, at)
            chain = self._srcmac_of_ip(ip)
            if chain is None:
                return None
        if mac is not None and request.internal_ip is not None:
            macs = {c.device.mac for c in (chain, *chain.alternatives) if c.device is not None}
            if mac not in macs:
                self.explanations.append(explain("mac_not_at_ip", mac=mac, ip=ip))
        return chain

    def _srcmac_of_ip(self, ip: str) -> AttributionChain | None:
        """An internal lookup of ``ip`` without a lease (2.9): the srcmac of the address's
        sessions overlapping the window on listed interfaces. One usable MAC gives the srcmac
        chain (the outcome keeps ``no_lease``); several give ``srcmac_ambiguous`` and no
        chain."""
        interfaces = self.settings.srcmac_interfaces
        if not interfaces:
            return None
        rows = srcmac_sightings(
            self.session,
            srcips={ip},
            window=self.window,
            interfaces=interfaces,
            untrusted=self.untrusted,
            limit=SRCMAC_ROWS,
        )
        self.srcmac.load(rows)
        usable = self._usable_rows(rows, key=lambda row: str(row.srcmac))
        if len(usable) > 1:
            self.explanations.append(
                explain(
                    "srcmac_ambiguous",
                    ip=ip,
                    seconds=self.tolerance,
                    count=len(usable),
                    macs=", ".join(list(usable)[:SRCMAC_NAMED]),
                )
            )
            return None
        if not usable:
            return None
        ((srcmac, _row),) = usable.values()
        return self.attributor.srcmac_chain(ip, self.request.at, *srcmac)

    def _srcmac_of_mac(self, mac: str) -> AttributionChain | None:
        """A MAC-only internal lookup without a lease (2.9): the rows whose srcmac is ``mac``
        within the tolerance of the moment on listed interfaces. One address gives the srcmac
        chain (the outcome keeps ``no_lease_for_mac``); several give ``mac_multiple_ips`` and
        no chain."""
        interfaces = self.settings.srcmac_interfaces
        if not interfaces:
            return None
        rows = srcmac_rows_of_mac(
            self.session,
            mac=mac,
            at=self.request.at,
            low=self.window.low,
            high=self.window.high,
            interfaces=interfaces,
            untrusted=self.untrusted,
            limit=SRCMAC_ROWS,
        )
        self.srcmac.load(rows)
        usable = self._usable_rows(rows, key=lambda row: row.srcip)
        if len(usable) > 1:
            ips = ", ".join(sorted(usable, key=_ip_order))
            self.explanations.append(explain("mac_multiple_ips", mac=mac, ips=ips))
            return None
        if not usable:
            return None
        ((ip, (srcmac, _row)),) = usable.items()
        return self.attributor.srcmac_chain(ip, self.request.at, *srcmac)

    def _usable_rows(
        self, rows: Sequence[TrafficRow], *, key: Any
    ) -> dict[str, tuple[Srcmac, TrafficRow]]:
        """Per ``key`` (the MAC or the address), in the order first seen, the usable srcmac of
        its row nearest to the moment."""
        nearest = _nearest_first(self.request.at)
        found: dict[str, tuple[Srcmac, TrafficRow]] = {}
        for row in rows:
            usable = self.srcmac.usable(row)
            if usable is None:
                continue
            known = found.get(key(row))
            if known is None or nearest(row) < nearest(known[1]):
                found[key(row)] = (usable, row)
        return found

    # -- FortiGate DHCP rows that did not become lease evidence (D8 probe) -------------------------

    def _unconverted(self, matches: Sequence[NatMatch]) -> tuple[EvidenceRef, ...]:
        """The D8 probe (2.8.3) for what no lease chain could be built for: the internal
        lookup's address (its MAC for a MAC-only request), else the IPs of the first
        :data:`PROBE_IPS` matches without one (no chain, or a srcmac chain: one rule for both
        paths), each at its chain's moment. Per IP the newest DHCP-shaped row before
        the moment from a trusted sender decides, in ``fg_event`` or ``fg_dhcp``: only an
        ``fg_event`` row of a logid mapped to ``assigned`` that would have covered the moment
        counts (``fortigate_dhcp_not_converted``). In internal mode and for an exact match the
        row is cited (never as an attribution); for a candidate nothing is (KVKK). No row after
        the requested moment is read, also for a session that began after it: another client's
        later ACK says nothing about it."""
        targets: dict[str, datetime] = {}
        cited: set[str] = set()
        by_mac = False
        if self._unbuilt is not None:
            kind, value, at = self._unbuilt
            by_mac = kind == "mac"
            targets[value] = at
            cited.add(value)
        for match in matches:
            ip = match.traffic.srcip
            if (match.chain is not None and match.chain.lease is not None) or ip in targets:
                continue
            if len(targets) == PROBE_IPS:
                break
            targets[ip] = min(max(self.request.at, match.traffic.start), match.traffic.end)
            if match.kind == "exact":
                cited.add(ip)
        if not targets or not self.settings.fortigate_logids:
            return ()
        newest = self._newest_dhcp(targets, by_mac=by_mac)
        counted = [
            newest[key]
            for key, moment in targets.items()
            if key in newest
            and self._would_cover(newest[key], moment)
            and (not by_mac or self._newest_of_its_address(newest[key], moment))
        ]
        if not counted:
            return ()
        ips = [self._named(line) if by_mac else line.key for line in counted]
        self.explanations.append(
            explain("fortigate_dhcp_not_converted", ips=", ".join(sorted(ips, key=_text_order)))
        )
        return tuple(
            line.ref()
            for line in sorted(counted, key=lambda line: _text_order(line.key))
            if line.key in cited
        )

    def _newest_dhcp(
        self, targets: Mapping[str, datetime], *, by_mac: bool
    ) -> dict[str, _DhcpLine]:
        """Per target key the ``fg_event`` DHCP line of the probe when it is the newest
        DHCP-shaped row of trusted senders in ``[moment - window, min(moment, T)]``: one
        ``DISTINCT ON`` query per table (``ix_fg_event_subtype_time``, ``ix_fg_dhcp_ip_time``;
        round 5's cost note), each key bounded by its own moment, and never past the requested
        moment T (2.8.3: a session that began after T is probed up to T). A line D25 or D28
        refuses (:func:`.attribution.event_kept`) is no such row, as a refused ``fg_dhcp`` row
        is none; a converted row (``trust.kept``) as new or newer means the timeline saw that
        event: nothing is said."""
        # The lease timeline imports this package (its refs), so its rules are imported here.
        from arcilogger_worker.correlation.timeline import TimelineRules

        # The configured ARP grace, at most the ARP interval (the rules refuse a longer one), as
        # the lease job builds them; the window does not depend on it.
        settings = self.settings
        grace = min(settings.arp_release_grace_seconds, settings.arp_interval_seconds)
        rules = TimelineRules.from_settings(settings, release_grace=grace)
        longest = timedelta(seconds=rules.longest_window)
        width = max(longest, UNCONVERTED_WINDOW)
        until = {key: min(at, self.request.at) for key, at in targets.items()}
        low, high = min(targets.values()) - width, max(until.values())
        logids = self.settings.fortigate_logids
        released = sorted(logid for logid, meaning in logids.items() if meaning == "deassigned")
        event_key = (
            func.lower(func.replace(FgEvent.extra["mac"].astext, "-", ":"))
            if by_mac
            else FgEvent.extra["ip"].astext
        )
        events = (
            select(
                event_key,
                FgEvent.id,
                FgEvent.time,
                FgEvent.logid,
                FgEvent.extra["ip"].astext,
                FgEvent.extra["lease"].astext,
                FgEvent.extra["interface"].astext,
                FgEvent.source,
                FgEvent.raw_day,
                FgEvent.raw_line,
                FgEvent.raw_offset,
            )
            .where(
                FgEvent.subtype == "system",
                FgEvent.time >= low,
                FgEvent.time <= high,
                FgEvent.logid.in_(sorted(logids)),
                event_kept(self.settings),
                # Every row of the window is read (no index holds the key); the key is read once
                # per row here, before the per-key bounds (3.8).
                event_key.in_(sorted(targets)),
                or_(
                    *(
                        and_(
                            event_key == key,
                            FgEvent.time >= at - width,
                            FgEvent.time <= until[key],
                        )
                        for key, at in targets.items()
                    )
                ),
            )
            .ext(distinct_on(event_key))
            # At equal times an assignment is the newer row, as in the timeline's feed order.
            .order_by(
                event_key,
                FgEvent.time.desc(),
                case((FgEvent.logid.in_(released), 1), else_=0),
                FgEvent.id.desc(),
            )
        )
        if self.untrusted:
            events = events.where(_not_in(FgEvent.source, self.untrusted))
        lines = [_DhcpLine(*row) for row in self.session.execute(events)]
        if not lines:
            return {}
        dhcp_key = FgDhcp.mac if by_mac else FgDhcp.ip
        converted = (
            select(dhcp_key, FgDhcp.time)
            .where(
                FgDhcp.time >= low,
                FgDhcp.time <= high,
                or_(
                    *(
                        and_(
                            dhcp_key == line.key,
                            FgDhcp.time >= targets[line.key] - width,
                            FgDhcp.time <= until[line.key],
                        )
                        for line in lines
                    )
                ),
                kept(self.settings, self.untrusted),
            )
            .ext(distinct_on(dhcp_key))
            .order_by(dhcp_key, FgDhcp.time.desc())
        )
        seen = {str(key): time for key, time in self.session.execute(converted)}
        return {
            line.key: line for line in lines if line.key not in seen or seen[line.key] < line.time
        }

    def _newest_of_its_address(self, line: _DhcpLine, moment: datetime) -> bool:
        """The address rule for a MAC's line (the c2 deviation): it counts only while it is
        also the newest DHCP-shaped row of the address it names, by the IP probe at the same
        moment; a newer row of that address, converted or not, says the address went on (to
        another client, or to the MAC again in a row the timeline saw). A line whose ``ip`` is
        no plain IPv4 address has no address to read (it never reaches PostgreSQL's ``inet``)
        and counts as found."""
        address = line.ip or ""
        try:
            ipaddress.IPv4Address(address)
        except ValueError:
            return True
        same = self._newest_dhcp({address: moment}, by_mac=False).get(address)
        return same is not None and same.id == line.id

    def _would_cover(self, line: _DhcpLine, moment: datetime) -> bool:
        """An ACK (a logid mapped to ``assigned``) whose lease would have covered ``moment``: its
        logged lease when usable, else its interface's configured time x
        ``lease.timeout_percent`` (the lease formula, D6)."""
        if self.settings.fortigate_logids.get(line.logid or "") != "assigned":
            return False
        try:
            logged = None if line.lease is None else int(line.lease)
        except ValueError:
            logged = None
        lease = usable_lease(self.settings, logged)
        timeout = self.settings.lease_timeout_seconds(line.interface, lease)
        return moment <= line.time + timedelta(seconds=timeout)

    @staticmethod
    def _named(line: _DhcpLine) -> str:
        """The address a MAC's line names in the text (the requested MAC's own record), the MAC
        when its ``ip`` cannot be read."""
        try:
            return str(ipaddress.ip_address(line.ip or ""))
        except ValueError:
            return line.key

    # -- the source clock at the requested moment (D28) ------------------------------------------

    def _source_clocks(
        self, attribution: AttributionChain | None, matches: list[NatMatch], plan: _Plan | None
    ) -> tuple[AttributionChain | None, list[NatMatch]]:
        """2.7 "Source clock at the requested moment": the sources of :meth:`_clock_sources`
        whose median offset at the moment is beyond their limit are named
        (``source_clock_offset``) and every chain of the outcome, alternatives included, is
        ``low``, its lease step too; a match keeps its kind. The search and the lease timeline
        used the device's times, so a session or lease found may not be the one the moment
        means, and an empty search may have missed it."""
        sources = self._clock_sources(attribution, matches, plan)
        tolerance = max(CLOCK_LIMIT_SECONDS, self.tolerance)
        offsets: dict[str, int] = {}
        limits: list[int] = []
        for name, lease in sources.items():
            limit = CLOCK_LIMIT_SECONDS if lease or self.request.is_internal else tolerance
            measured = self._clock(name)
            # The unrounded median is compared, as R20-lease:source-clock compares it; the
            # offset printed is rounded up, so a named offset still exceeds its limit.
            if measured is not None and abs(measured[0]) > limit:
                offsets[name] = _whole_seconds(measured[0])
                limits.append(limit)
        if not offsets:
            return attribution, matches
        self.explanations.append(
            explain(
                "source_clock_offset",
                at=self.request.at.isoformat(),
                offsets=", ".join(f"{name}: {offset:+d}" for name, offset in offsets.items()),
                limit=min(limits),
            )
        )
        matches = [m if m.chain is None else replace(m, chain=_lowered(m.chain)) for m in matches]
        return (None if attribution is None else _lowered(attribution)), matches

    def _clock_sources(
        self, attribution: AttributionChain | None, matches: Sequence[NatMatch], plan: _Plan | None
    ) -> dict[str, bool]:
        """The sources to measure, at most :data:`CLOCK_SOURCES`, each with whether it was
        measured for a lease table (the fixed limit): the sources of the cited rows (the
        matches' session rows, the chains' lease and device evidence), most cited first; then,
        for a search whose outcome cites no row of the tables it read (round 5), the sources
        ``R20-lease:source-clock`` would check for those tables, by their rows in the files of
        the receive days around the moment (``search_counts``; a device clock cannot move the
        receive day). A search that ran and found no match read ``fg_traffic``; an internal
        lookup without a lease and a match without a lease chain read the lease tables. An
        answer that searched nothing (``no_egress``, ``not_logged``, ``internal``) adds
        none."""
        cited: Counter[str] = Counter()
        lease_cited: set[str] = set()
        chains = [m.chain for m in matches if m.chain is not None]
        if attribution is not None:
            chains.append(attribution)
        refs = [ref for m in matches for ref in m.traffic.evidence]
        for chain in (c for top in chains for c in (top, *top.alternatives)):
            refs += chain.lease.evidence if chain.lease is not None else ()
            refs += chain.device.evidence if chain.device is not None else ()
        for ref in refs:
            if ref.raw is not None:
                cited[ref.raw.source] += 1
                if ref.table in LEASE_TABLES:
                    lease_cited.add(ref.raw.source)
        tables: set[str] = set()
        searched = not self.request.is_internal and (plan is None or not plan.nobody)
        if searched and not matches:
            tables.add("fg_traffic")
        if self._unbuilt is not None or any(
            m.chain is None or m.chain.lease is None for m in matches
        ):
            tables |= LEASE_TABLES
        ranked = sorted(cited, key=lambda name: (-cited[name], name))
        sources = {name: name in lease_cited for name in ranked}
        if tables:
            lease = bool(tables & LEASE_TABLES)
            for name in self._searched_sources(tables):
                sources[name] = sources.get(name, False) or lease
        return dict(list(sources.items())[:CLOCK_SOURCES])

    def _searched_sources(self, tables: set[str]) -> list[str]:
        """The enabled, configured sources whose spool files of the receive days of the clock
        span hold rows of ``tables``, most rows first (``ix_search_counts_raw_day_source``)."""
        at = self.request.at
        days = sorted({site_day(at - CLOCK_SPAN), site_day(at + CLOCK_SPAN)})
        configured = select(Source.name).where(Source.enabled.is_(True), Source.unknown.is_(False))
        rows = func.sum(SearchCount.n)
        query = (
            select(SearchCount.source)
            .where(
                SearchCount.raw_day.in_(days),
                SearchCount.tbl.in_(sorted(tables)),
                SearchCount.source.in_(configured),
            )
            .group_by(SearchCount.source)
            .order_by(rows.desc(), SearchCount.source)
        )
        return list(self.session.scalars(query))

    def _clock(self, source: str) -> tuple[float, int] | None:
        """The median of ``time - time_received`` (seconds) over ``source``'s ``messages`` rows
        nearest the moment on each side within :data:`CLOCK_SPAN` whose device time is not the
        receive time (ingest's stand-in for a clock beyond ``max_clock_skew`` says nothing),
        and their number; ``None`` with fewer than :data:`CLOCK_MIN_ROWS`. One query on
        ``ix_messages_source_time``, cached per lookup."""
        if source not in self._clocks:
            at = self.request.at

            def side(*clauses: ColumnElement[bool], order: Any) -> Any:
                return (
                    select(Message.time, Message.time_received)
                    .where(
                        Message.source == source, Message.time != Message.time_received, *clauses
                    )
                    .order_by(order)
                    .limit(CLOCK_SIDE_ROWS)
                )

            before = side(
                Message.time >= at - CLOCK_SPAN, Message.time < at, order=Message.time.desc()
            )
            after = side(Message.time >= at, Message.time <= at + CLOCK_SPAN, order=Message.time)
            rows = union_all(before, after).subquery("clock")
            seconds = func.extract("epoch", rows.c.time - rows.c.time_received)
            count, median = self.session.execute(
                select(func.count(), func.percentile_cont(0.5).within_group(seconds))
            ).one()
            found = count >= CLOCK_MIN_ROWS and median is not None
            self._clocks[source] = (float(median), int(count)) if found else None
        return self._clocks[source]

    # -- exact ------------------------------------------------------------------------------------

    def _exact(self, address: str) -> list[NatMatch]:
        request = self.request
        fetched = fetch_rows(
            self.session,
            self.window,
            transip=address,
            transport=request.public_port,
            proto=request.protocol,
            srcips=self._source_filter(),
            domain=request.dest_domain,
            domain_only=False,  # the destination is reported, it does not filter
        )
        groups = group_sessions(fetched.rows)
        if not groups:
            self.explanations.append(explain("no_match"))
            self.explanations.append(
                explain("no_match_exact", address=address, port=request.public_port)
            )
            return []
        domain = self._domain_hits()
        scored = [(group, destination_match(group, request, domain)[0], 100) for group in groups]
        if self._wants_destination():
            scored = self._exact_to_destination(scored)
            if not scored:
                return []
        self.explanations.append(explain("exact_nat"))
        if len(scored) > 1:
            self.explanations.append(explain("multiple_exact", count=len(scored)))
        return self._matches(scored, kind="exact", capped=fetched.capped)

    def _exact_to_destination(
        self, scored: list[tuple[SessionGroup, DestinationMatch, int]]
    ) -> list[tuple[SessionGroup, DestinationMatch, int]]:
        """The sessions on the NAT port that answer the requested destination.

        The NAT port identifies sessions, the destination the one asked about: sessions on the
        same port whose logged destination IP/port differs (port reuse inside the window) are
        counted, never listed (spec 3.3). For a domain-only request the absence of a DNS/web/app
        log for the name proves nothing, so those sessions are kept as ``domain_unverified``
        unless a session tied to the name exists.
        """
        request = self.request
        domain_only = request.dest_ip is None and request.dest_domain is not None
        verified: list[tuple[SessionGroup, DestinationMatch, int]] = []
        unverified: list[tuple[SessionGroup, DestinationMatch, int]] = []
        differs = 0
        for group, match, score in scored:
            if match != "none":
                verified.append((group, match, score))
            elif domain_only and request.dest_port in (None, group.first.dstport):
                unverified.append((group, "domain_unverified", score))
            else:
                differs += 1
        kept = verified or unverified
        params: dict[str, str | int | None] = {
            "address": request.public_ip,
            "port": request.public_port,
        }
        if not kept:
            self.explanations.append(explain("no_match"))
            self.explanations.append(
                explain("destination_not_found", destination=self._destination_text())
            )
        elif not verified:
            self.explanations.append(explain("exact_domain_unverified", domain=request.dest_domain))
        if differs:
            key = "exact_other_destinations" if kept else "exact_only_other_destinations"
            self.explanations.append(explain(key, count=differs, **params))
        if verified and unverified:
            self.explanations.append(
                explain(
                    "exact_unverified_destinations",
                    count=len(unverified),
                    domain=request.dest_domain,
                    **params,
                )
            )
        return kept

    # -- candidates -------------------------------------------------------------------------------

    def _searches(self, plan: _Plan | None) -> list[dict[str, Any]]:
        """The NAT filters of the candidate search (one per interface condition)."""
        common: dict[str, Any] = {
            "proto": self.request.protocol,
            "srcips": self._source_filter(),
            "snat_types": self.settings.snat_trandisp,
        }
        if plan is None:  # a destination-only request: every listed trandisp
            return [common]
        if plan.address is not None:
            return [{**common, "transip": plan.address}]
        searches: dict[tuple[str, ...] | None, dict[str, Any]] = {}
        claimed = claimed_networks(self.exits)
        for part in plan.parts:
            search = searches.setdefault(
                part.dstintfs, {**common, "transip_in": (), "dstintfs": part.dstintfs}
            )
            search["transip_in"] = tuple(dict.fromkeys([*search["transip_in"], *part.transips]))
            if part.unclaimed:
                search["unclaimed"] = claimed
        return list(searches.values())

    def _candidates(self, plan: _Plan | None) -> list[NatMatch]:
        request = self.request
        searches = self._searches(plan)
        wants_destination = self._wants_destination()
        domain = self._domain_hits()
        rows: list[TrafficRow] = []
        capped = False
        for search in searches:
            if wants_destination:
                found, more = self._destination_rows(search, domain)
            else:
                fetched = fetch_rows(self.session, self.window, **search)
                found, more = fetched.rows, fetched.capped
            rows += found
            capped |= more
        groups = self._same_family(group_sessions(rows))
        if wants_destination:
            groups = [g for g in groups if destination_match(g, request, domain)[0] != "none"]
        if not groups:
            self._explain_no_candidates(plan, wants_destination)
            self._trandisp_note(plan)
            return []
        self.explanations.append(self._search_note(plan, found=True))
        if not wants_destination:
            self.explanations.append(explain("candidates_without_destination"))
        hinted = self._port_hint(plan)
        if hinted:
            self.explanations.append(explain("port_hint", egresses=_joined(sorted(hinted))))
        if plan is not None:
            self.explanations.extend(plan.notes)
        self._trandisp_note(plan)
        scored = []
        for group in groups:
            match, points = destination_match(group, request, domain)
            score = points + time_points(group.distance_ms(request.at), self.tolerance)
            part = None if plan is None else self._part_of(plan, group.first)
            if (
                hinted
                and part is not None
                and part.egress is not None
                and part.egress.name in hinted
                and group.first.transport == request.public_port
            ):
                score += PORT_HINT_POINTS
            scored.append((group, match, min(score, CANDIDATE_MAX_SCORE)))
        return self._matches(scored, kind="candidate", capped=capped)

    def _port_hint(self, plan: _Plan | None) -> set[str]:
        """The ``preserved`` exits whose search runs, when a port was requested (D16)."""
        if plan is None or self.request.public_port is None or plan.address is not None:
            return set()
        return {
            part.egress.name
            for part in plan.parts
            if part.egress is not None and part.egress.port_preservation == "preserved"
        }

    def _part_of(self, plan: _Plan, row: TrafficRow) -> _Part | None:
        """The part of the search that found ``row``: its NAT addresses, else the unclaimed
        predicate (the exit whose search found the candidate ranks it, D16)."""
        if row.transip is None:
            return None
        address = ipaddress.ip_address(row.transip)
        for part in plan.parts:
            if part.dstintfs is not None and row.dstintf not in part.dstintfs:
                continue
            if any(address in network for network in part.transips):
                return part
        for part in plan.parts:
            if part.unclaimed and (part.dstintfs is None or row.dstintf in part.dstintfs):
                return part
        return None

    def _destination_rows(
        self, common: dict[str, Any], domain: DomainHits | None
    ) -> tuple[list[TrafficRow], bool]:
        """Sessions to the requested destination only (spec 2.3)."""
        request = self.request
        rows: list[TrafficRow] = []
        capped = False
        searches: list[dict[str, Any]] = []
        if request.dest_ip is not None:
            searches.append({"dstips": {request.dest_ip}})
        if domain is not None and request.dest_domain is not None:
            searches.append({"domain": request.dest_domain, "dstips": domain.addresses})
        if request.dest_ip is None and request.dest_domain is None:
            searches.append({"dstport": request.dest_port})
        for filters in searches:
            fetched = fetch_rows(self.session, self.window, **filters, **common)
            rows += fetched.rows
            capped |= fetched.capped
        return rows, capped

    def _same_family(self, groups: list[SessionGroup]) -> list[SessionGroup]:
        """The sessions that left with an address of the requested public IP's family (module
        docstring); all of them when no public IP was requested."""
        if self.request.public_ip is None:
            return groups
        version = ipaddress.ip_address(self.request.public_ip).version
        return [
            group
            for group in groups
            if ipaddress.ip_address(group.first.transip or group.first.srcip).version == version
        ]

    def _search_note(self, plan: _Plan | None, *, found: bool) -> Explanation:
        """Why this is a candidate search (the spec 12.4 text only when candidates follow)."""
        if plan is None:
            return explain("destination_search")
        if plan.address is not None:  # an exact address without a port
            return explain("port_missing")
        return explain("double_nat" if found else "double_nat_no_candidates")

    def _explain_no_candidates(self, plan: _Plan | None, wants_destination: bool) -> None:
        self.explanations.append(self._search_note(plan, found=False))
        self.explanations.append(explain("no_match"))
        if wants_destination:
            self.explanations.append(
                explain("destination_not_found", destination=self._destination_text())
            )
        self.explanations.append(explain("no_match_candidates"))
        if plan is not None:
            self.explanations.extend(plan.notes)

    def _trandisp_note(self, plan: _Plan | None) -> None:
        """``nat_unknown_trandisp``: when the unclaimed addresses were searched, the rows of the
        window that an unlisted ``trandisp`` kept out, counted and named (trusted senders; a
        value printed only in the setting's own form)."""
        if plan is None or not any(part.unclaimed for part in plan.parts):
            return
        listed = list(self.settings.snat_trandisp)
        claimed = claimed_networks(self.exits)
        clauses: list[ColumnElement[bool]] = [
            FgTraffic.time >= self.window.low,
            FgTraffic.time <= self.window.scan_end,
            FgTraffic.transip.is_not(None),
            within(FgTraffic.transip, NOT_PUBLIC),
            or_(FgTraffic.trandisp.is_(None), not_(FgTraffic.trandisp == _any_text(listed))),
        ]
        if claimed:
            clauses.append(not_(within(FgTraffic.transip, claimed)))
        if self.untrusted:
            clauses.append(_not_in(FgTraffic.source, self.untrusted))
        rows = self.session.execute(
            select(FgTraffic.trandisp, func.count())
            .where(and_(*clauses))
            .group_by(FgTraffic.trandisp)
            .order_by(FgTraffic.trandisp)
        ).all()
        count = sum(number for _value, number in rows)
        if not count:
            return
        values = [
            value if value is not None and _TRANDISP.match(value) else "…" for value, _ in rows
        ]
        self.explanations.append(
            explain(
                "nat_unknown_trandisp",
                count=count,
                values=_joined(list(dict.fromkeys(values))[:PROBE_NAMES]),
            )
        )

    # -- shared -----------------------------------------------------------------------------------

    def _wants_destination(self) -> bool:
        request = self.request
        return (
            request.dest_ip is not None
            or request.dest_domain is not None
            or request.dest_port is not None
        )

    def _source_filter(self) -> set[str] | None:
        """Internal IP / MAC given together with a public IP: only that client's sessions."""
        request = self.request
        sources: set[str] = set()
        if request.internal_ip is not None:
            sources.add(request.internal_ip)
        if request.mac is not None:
            low = self.window.low - timedelta(seconds=self.settings.max_session_seconds)
            sources |= {
                lease.ip
                for lease in self.attributor.leases_of_mac(request.mac, low, self.window.high)
            }
        if request.internal_ip is None and request.mac is None:
            return None
        return sources

    def _domain_hits(self) -> DomainHits | None:
        if self.request.dest_domain is None:
            return None
        lookback = int(
            get_setting(self.session, "lookup.dns_lookback_seconds", DEFAULT_DNS_LOOKBACK_SECONDS)
        )
        return find_domain(self.session, self.request.dest_domain, self.window, lookback)

    def _destination_text(self) -> str:
        request = self.request
        parts = [request.dest_domain or "", request.dest_ip or ""]
        text = " / ".join(part for part in parts if part)
        if request.dest_port is not None:
            text = f"{text}:{request.dest_port}" if text else f"port {request.dest_port}"
        return text

    def _exit_of(self, row: TrafficRow) -> str | None:
        """The exit of a session row (D32): the exit that claims its ``transip`` as a NAT
        address (a shared one: the first by name whose interfaces hold the row's ``dstintf``),
        else the exit whose public address it is; ``None`` for an unclaimed row."""
        if row.transip is None or self.request.public_ip is None:
            return None
        try:
            exits = self.exits
        except ValueError:  # pragma: no cover - the router refuses such lookups
            return None
        address = ipaddress.ip_address(row.transip)
        nat = [e for e in exits if any(address in n for n in e.transips)]
        if len(nat) == 1:
            return nat[0].name
        if nat:
            return next((e.name for e in nat if row.dstintf in e.interfaces), None)
        return next((e.name for e in exits if any(address in n for n in e.public)), None)

    def _matches(
        self,
        scored: list[tuple[SessionGroup, DestinationMatch, int]],
        *,
        kind: MatchKind,
        capped: bool,
    ) -> list[NatMatch]:
        at = self.request.at

        def order(item: tuple[SessionGroup, DestinationMatch, int]) -> tuple[Any, ...]:
            group, match, score = item
            first = group.first
            return (
                -score,
                _DESTINATION_ORDER.get(match, 10),
                group.distance_ms(at),
                group.start,
                _ip_order(first.srcip),
                first.srcport or 0,
                first.sessionid or 0,
                first.id,
            )

        scored.sort(key=order)
        limit = self.settings.max_candidates
        total = len(scored)
        if total > limit or capped:
            self.truncated = True
            self.explanations.append(explain("truncated", total=total, shown=min(total, limit)))
        shown = scored[:limit]
        self.attributor.prefetch((g.first.srcip, g.moment(at)) for g, _, _ in shown)
        srcmacs = self._session_srcmacs([group for group, _, _ in shown])
        interfaces = self.settings.srcmac_interfaces
        matches = []
        changed: list[str] = []
        changed_count = 0
        later: set[str] = set()
        for (group, match, score), srcmac in zip(shown, srcmacs, strict=True):
            egress = self._exit_of(group.nearest(at))
            exits = list(dict.fromkeys(self._exit_of(row) for row in group.rows))
            if len(exits) > 1:
                changed_count += 1
                changed += [name or "-" for name in exits if (name or "-") not in changed]
            sources = {row.source for row in group.rows}
            later |= {name for name in sources - self.untrusted if name.startswith(UNKNOWN_PREFIX)}
            matches.append(
                NatMatch(
                    kind=kind,
                    score=score,
                    time_distance_ms=group.distance_ms(at),
                    destination_match=match,
                    traffic=group.traffic(at, interfaces, self.untrusted),
                    chain=self.attributor.chain(group.first.srcip, group.moment(at), srcmac=srcmac),
                    egress=egress,
                    untrusted_sources=tuple(sorted(sources & self.untrusted)),
                )
            )
        if changed_count:
            self.explanations.append(
                explain("session_changed_exit", count=changed_count, egresses=_joined(changed))
            )
        self._sender_notes(matches, later)
        return matches

    def _session_srcmacs(self, groups: Sequence[SessionGroup]) -> list[Srcmac | None]:
        """Per session, the srcmac of its row nearest to the moment that carries a usable one
        (2.9): a listed interface, a trusted sender and the guard; ``None`` while
        ``lookup.srcmac_interfaces`` is empty (off)."""
        interfaces = self.settings.srcmac_interfaces
        if not interfaces:
            return [None] * len(groups)
        at = self.request.at
        rows = [group.srcmac_rows(at, interfaces, self.untrusted) for group in groups]
        self.srcmac.load([row for found in rows for row in found])
        used: list[Srcmac | None] = []
        for found in rows:
            usable = (self.srcmac.usable(row) for row in found)
            used.append(next((srcmac for srcmac in usable if srcmac is not None), None))
        return used

    def _sender_notes(self, matches: Sequence[NatMatch], later: set[str]) -> None:
        """D24: the session search keeps every sender, so a match with rows of an unconfigured
        one is marked on the match and named here (``session_from_unknown_source``); rows of an
        ``unknown-<ip>`` sender that a source configured later vouches for are named as such
        (``source_configured_later``, information)."""
        marked = [match for match in matches if match.untrusted_sources]
        if marked:
            names = sorted({name for match in marked for name in match.untrusted_sources})
            self.explanations.append(
                explain("session_from_unknown_source", count=len(marked), sources=_joined(names))
            )
        if later:
            self.explanations.append(
                explain("source_configured_later", sources=", ".join(sorted(later)))
            )
