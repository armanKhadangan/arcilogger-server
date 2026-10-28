"""FortiGate sessions behind a 5651 request: bounded queries, grouping, destination match, score.

**Matching rule** (docs/MVP-CONTRACTS.md section 5): a ``fg_traffic`` row's session interval is
``[time - duration, time]``; it matches when it overlaps ``[at - tolerance, at + tolerance]``.
Every query is bounded to ``time BETWEEN at - tolerance AND at + tolerance +
lookup.max_session_seconds`` so the index range stays small, whatever index the planner uses:
``(transip, transport, time)`` for exact lookups, ``(dstip, dstport, time)`` for a requested
destination, the primary key ``(time, id)`` for a window without destination.

**Grouping:** interim updates (``logid=0000000020``) and the final log of one session share
``sessionid`` and the 5-tuple + NAT pair; they become one :class:`SessionGroup` (one match), its
interval spanning all rows, its evidence citing the rows nearest to ``at``.

**Domains** (:func:`find_domain`, ``build_query(domain=…)``): DNS answers for the name (index
``(qname, time)``) and the addresses web filter / application control logs saw for it near the
moment give candidate destination addresses; a session whose *own* web/app log names the domain
is found inside the bounded ``fg_traffic`` scan by a correlated ``EXISTS`` on ``sessionid`` +
source IP within the session's time span — never by collecting session ids in Python, so a
popular domain (hundreds of thousands of web log rows a day) costs no more than the window
scan. Lists of values are passed as one array parameter (``= ANY(…)``), never as one bind
parameter per value (PostgreSQL accepts at most 65,535).

**Score** of a candidate (integers, 0-95; exact matches are 100 and need no score):

* time, 0-40: 40 when ``at`` is inside the session, falling linearly to 0 at the tolerance;
* destination, 0-50: requested IP and port 50; domain seen in this session's web/app log or in
  a DNS answer to this client 45 (+port 50, other port 40); only the address known for the
  domain (another client's DNS/web log — CDN addresses serve many names) 35 (+port 40); IP
  with no or another port 45/30; port only 20; nothing requested 25 (neutral); requested but
  not matched 0;
* port hint, 0 or 5: the exit whose search found the candidate is marked ``preserved`` (its ISP
  router keeps source ports; ``nat.egresses``, ADR-0016 D16) and the candidate's FortiGate NAT
  port equals the requested public port — only a ranking, never exact.

**NAT addresses searched** (``build_query``; the internet exits of ``nat.egresses``, ADR-0016):
``transip`` (exact mode), ``transip_in`` (an exit's NAT addresses, any ``trandisp``) and
``unclaimed`` (every private or shared source-NAT address no exit claims, with a ``trandisp`` of
``nat.snat_trandisp``), optionally narrowed to ``dstintfs``; a search without a NAT address (a
destination-only request) takes every session whose ``trandisp`` is listed.

The label (:data:`~arcilogger_worker.lookup.types.DestinationMatch`) keeps the weaker kinds
apart, so a report never presents them as plain matches: ``ip_other_port`` (requested IP,
another port), ``domain_other_port`` (the session is tied to the name, another port) and
``domain_address`` (only the address is known for the name, from another client's logs).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import IPv4Network
from typing import Any, Final

from sqlalchemy import Select, Text, and_, any_, cast, exists, func, literal, not_, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ClauseElement, ColumnElement, Executable

from arcilogger_db.egress import NOT_PUBLIC
from arcilogger_db.models import FgApp, FgDns, FgTraffic, FgWeb
from arcilogger_worker.lookup.refs import traffic_ref
from arcilogger_worker.lookup.types import (
    DestinationMatch,
    EvidenceRef,
    LookupRequest,
    TrafficInfo,
)

AnySelect = Select[*tuple[Any, ...]]
#: Rows read at most per query (a ±60 s window has a few thousand); more → truncated.
MAX_ROWS: Final = 50_000
#: Evidence rows cited per session (the ones nearest to the requested moment, first and last).
EVIDENCE_PER_SESSION: Final = 10
#: Destination points of the score (module docstring).
DESTINATION_POINTS: Final = {
    "ip_port": 50,
    "domain_port": 50,
    "domain": 45,
    "ip": 45,
    "domain_other_port": 40,
    "domain_address_port": 40,
    "domain_address": 35,
    "ip_other_port": 30,
    "port": 20,
    "not_requested": 25,
    "none": 0,
}
TIME_POINTS: Final = 40
PORT_HINT_POINTS: Final = 5
CANDIDATE_MAX_SCORE: Final = 95
#: A web/app log belongs to a session when written within its span ± this (log ordering).
LOG_SLACK: Final = timedelta(seconds=60)

_COLUMNS = (
    FgTraffic.id,
    FgTraffic.time,
    FgTraffic.logid,
    FgTraffic.sessionid,
    FgTraffic.proto,
    FgTraffic.srcip,
    FgTraffic.srcport,
    FgTraffic.dstip,
    FgTraffic.dstport,
    FgTraffic.transip,
    FgTraffic.transport,
    FgTraffic.trandisp,
    FgTraffic.duration,
    FgTraffic.is_interim,
    FgTraffic.source,
    FgTraffic.raw_day,
    FgTraffic.raw_line,
    FgTraffic.raw_offset,
    FgTraffic.srcintf,
    FgTraffic.dstintf,
    FgTraffic.srcmac,
)


@dataclass(frozen=True)
class TrafficRow:
    id: int
    time: datetime
    logid: str | None
    sessionid: int | None
    proto: int | None
    srcip: str
    srcport: int | None
    dstip: str | None
    dstport: int | None
    transip: str | None
    transport: int | None
    trandisp: str | None
    duration: int | None
    is_interim: bool
    source: str
    raw_day: Any
    raw_line: int
    raw_offset: int
    #: The session's own web filter / application control log names the requested domain.
    named: bool = False
    #: The FortiGate interfaces of the row and its srcmac (ADR-0016).
    srcintf: str | None = None
    dstintf: str | None = None
    srcmac: str | None = None

    @property
    def start(self) -> datetime:
        return self.time - timedelta(seconds=max(0, self.duration or 0))

    @property
    def key(self) -> tuple[Any, ...]:
        if self.sessionid is None:
            return ("row", self.id)
        return (
            self.sessionid,
            self.srcip,
            self.srcport,
            self.dstip,
            self.dstport,
            self.proto,
            self.transip,
            self.transport,
        )


@dataclass
class SessionGroup:
    rows: list[TrafficRow] = field(default_factory=list)

    @property
    def first(self) -> TrafficRow:
        return self.rows[0]

    @property
    def start(self) -> datetime:
        return min(row.start for row in self.rows)

    @property
    def end(self) -> datetime:
        return max(row.time for row in self.rows)

    def distance_ms(self, at: datetime) -> int:
        if self.start <= at <= self.end:
            return 0
        gap = self.start - at if at < self.start else at - self.end
        return gap // timedelta(milliseconds=1)

    def moment(self, at: datetime) -> datetime:
        """``at`` clamped into the session (the moment whose IP owner is attributed)."""
        return min(max(at, self.start), self.end)

    def traffic(self, at: datetime) -> TrafficInfo:
        rows = sorted(self.rows, key=lambda r: (abs((r.time - at).total_seconds()), r.time, r.id))
        cited = {r.id: r for r in rows[: EVIDENCE_PER_SESSION - 2]}
        cited.setdefault(self.rows[0].id, self.rows[0])
        cited.setdefault(self.rows[-1].id, self.rows[-1])
        evidence: tuple[EvidenceRef, ...] = tuple(
            traffic_ref(r) for r in sorted(cited.values(), key=lambda r: (r.time, r.id))
        )
        first = self.first
        nearest = self.nearest(at)
        return TrafficInfo(
            sessionid=first.sessionid,
            proto=first.proto,
            srcip=first.srcip,
            srcport=first.srcport,
            dstip=first.dstip,
            dstport=first.dstport,
            transip=first.transip,
            transport=first.transport,
            start=self.start,
            end=self.end,
            evidence=evidence,
            srcintf=nearest.srcintf,
            dstintf=nearest.dstintf,
        )

    def nearest(self, at: datetime) -> TrafficRow:
        """The row nearest to ``at`` (its exit and interfaces are the match's, ADR-0016 D32)."""
        return min(self.rows, key=lambda r: (abs((r.time - at).total_seconds()), r.time, r.id))


def group_sessions(rows: Iterable[TrafficRow]) -> list[SessionGroup]:
    """Rows → sessions (interim + final rows of one session together), rows in time order.

    A row read by several queries counts once (``named`` if any query saw its logs name the
    requested domain).
    """
    unique: dict[int, TrafficRow] = {}
    for row in rows:
        known = unique.get(row.id)
        if known is None or (row.named and not known.named):
            unique[row.id] = row
    groups: dict[tuple[Any, ...], SessionGroup] = {}
    for row in sorted(unique.values(), key=lambda r: (r.time, r.id)):
        groups.setdefault(row.key, SessionGroup()).rows.append(row)
    return list(groups.values())


# -- queries --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    at: datetime
    tolerance: int
    max_session: int

    @property
    def low(self) -> datetime:
        return self.at - timedelta(seconds=self.tolerance)

    @property
    def high(self) -> datetime:
        return self.at + timedelta(seconds=self.tolerance)

    @property
    def scan_end(self) -> datetime:
        return self.high + timedelta(seconds=self.max_session)


@dataclass
class Fetched:
    rows: list[TrafficRow]
    #: More rows than :data:`MAX_ROWS` matched (the rest was not read).
    capped: bool = False


def _session_start() -> ColumnElement[datetime]:
    return FgTraffic.time - func.make_interval(
        0, 0, 0, 0, 0, 0, func.coalesce(FgTraffic.duration, 0)
    )


def _inet_any(values: Collection[str]) -> ColumnElement[Any]:
    """``= ANY(CAST(:values AS inet[]))``: one parameter however many values."""
    return any_(cast(literal(sorted(values), ARRAY(INET)), ARRAY(INET)))


def _text_any(values: Collection[str]) -> ColumnElement[Any]:
    """``= ANY(CAST(:values AS text[]))``."""
    return any_(cast(literal(sorted(values), ARRAY(Text)), ARRAY(Text)))


def within(column: Any, networks: Sequence[IPv4Network]) -> ColumnElement[bool]:
    """``column`` is one of ``networks``: ``= ANY(inet[])`` when every entry is a single address
    (the ``(transip, transport, time)`` index applies), otherwise ``<<= ANY(cidr[])``."""
    clause: ColumnElement[bool]
    if all(n.prefixlen == n.max_prefixlen for n in networks):
        addresses = [str(n.network_address) for n in networks]
        clause = column == any_(cast(literal(addresses, ARRAY(INET)), ARRAY(INET)))
    else:
        texts = [str(n) for n in networks]
        clause = column.op("<<=")(any_(cast(literal(texts, ARRAY(CIDR)), ARRAY(CIDR))))
    return clause


def unclaimed_nat(
    claimed: Sequence[IPv4Network], snat_types: Collection[str]
) -> ColumnElement[bool]:
    """The rows whose ``transip`` is a private or shared source-NAT address no exit claims
    (``claimed``: every exit's NAT addresses and every direct exit's public addresses) with a
    listed ``trandisp``. A **public** NAT address no exit claims is the FortiGate's own address
    (an IP pool, a line nobody configured): its sessions never left through the requested one."""
    clauses = [
        FgTraffic.transip.is_not(None),
        within(FgTraffic.transip, NOT_PUBLIC),
        FgTraffic.trandisp == _text_any(snat_types),
    ]
    if claimed:
        clauses.append(not_(within(FgTraffic.transip, claimed)))
    return and_(*clauses)


def _named_by_logs(domain: str, window: Window) -> ColumnElement[bool]:
    """A web filter or application control log of this session names ``domain`` (correlated:
    same ``sessionid`` and source IP, written within the session's span)."""
    started = _session_start()
    clauses = []
    for model in (FgWeb, FgApp):
        clauses.append(
            exists().where(
                model.sessionid == FgTraffic.sessionid,
                model.srcip == FgTraffic.srcip,
                model.time >= started - LOG_SLACK,
                model.time <= FgTraffic.time + LOG_SLACK,
                # Constant bounds as well, for partition pruning: sessions longer than
                # ``max_session`` are outside the lookup anyway.
                model.time >= window.low - timedelta(seconds=window.max_session) - LOG_SLACK,
                model.time <= window.scan_end + LOG_SLACK,
                _host_matches(model.hostname, domain),
            )
        )
    return or_(*clauses)


def build_query(
    window: Window,
    *,
    transip: str | None = None,
    transport: int | None = None,
    proto: int | None = None,
    srcips: Collection[str] | None = None,
    dstips: Collection[str] | None = None,
    dstport: int | None = None,
    domain: str | None = None,
    domain_only: bool = True,
    transip_in: Sequence[IPv4Network] = (),
    unclaimed: Sequence[IPv4Network] | None = None,
    dstintfs: Collection[str] | None = None,
    snat_types: Collection[str] = ("snat",),
) -> AnySelect | None:
    """Rows whose session overlaps the window (bounded scan); ``None`` for an empty filter.

    ``domain``: only sessions whose own web/app log names it, or — with ``dstips`` — sessions to
    one of those addresses *or* named by their logs (one scan; ``TrafficRow.named`` tells which).
    ``domain_only=False`` only marks the rows (``named``) without filtering on the domain.

    NAT addresses: ``transip`` (exact); otherwise the rows translated to ``transip_in`` (an
    exit's NAT addresses) **or**, with ``unclaimed`` (the claimed networks), to a private or
    shared address no exit claims (:func:`unclaimed_nat`); with neither, every row whose
    ``trandisp`` is one of ``snat_types``. ``dstintfs``: only rows that left by one of these
    interfaces (an exit's ``interfaces``).
    """
    columns: list[Any] = list(_COLUMNS)
    query_filters: list[ColumnElement[bool]] = [
        FgTraffic.time >= window.low,
        FgTraffic.time <= window.scan_end,
        _session_start() <= window.high,
        FgTraffic.srcip.is_not(None),
    ]
    if transip is not None:
        query_filters.append(FgTraffic.transip == transip)
    elif transip_in or unclaimed is not None:
        parts = [within(FgTraffic.transip, transip_in)] if transip_in else []
        if unclaimed is not None:
            parts.append(unclaimed_nat(unclaimed, snat_types))
        query_filters.append(or_(*parts))
    else:
        query_filters.append(FgTraffic.trandisp == _text_any(snat_types))
    if transport is not None:
        query_filters.append(FgTraffic.transport == transport)
    if dstintfs is not None:
        if not dstintfs:
            return None
        query_filters.append(FgTraffic.dstintf == _text_any(dstintfs))
    if proto is not None:
        query_filters.append(FgTraffic.proto == proto)
    if srcips is not None:
        if not srcips:
            return None
        query_filters.append(FgTraffic.srcip == _inet_any(srcips))
    if dstport is not None:
        query_filters.append(FgTraffic.dstport == dstport)
    if domain is not None:
        named = _named_by_logs(domain, window)
        columns.append(named.label("named"))
    if domain is not None and domain_only:
        to_address = FgTraffic.dstip == _inet_any(dstips) if dstips else None
        query_filters.append(named if to_address is None else or_(to_address, named))
    elif dstips is not None:
        if not dstips:
            return None
        query_filters.append(FgTraffic.dstip == _inet_any(dstips))
    query = select(*columns).where(*query_filters)
    return query.order_by(FgTraffic.time, FgTraffic.id).limit(MAX_ROWS + 1)


def fetch_rows(session: Session, window: Window, **filters: Any) -> Fetched:
    """Execute :func:`build_query` (at most :data:`MAX_ROWS` rows)."""
    query = build_query(window, **filters)
    if query is None:
        return Fetched([])
    rows = [TrafficRow(**row._asdict()) for row in session.execute(query)]
    return Fetched(rows[:MAX_ROWS], capped=len(rows) > MAX_ROWS)


class Explain(Executable, ClauseElement):
    """``EXPLAIN [ANALYZE] <select>`` (SQLAlchemy's documented custom-construct recipe)."""

    inherit_cache = False

    def __init__(self, statement: AnySelect, *, analyze: bool = False) -> None:
        self.statement = statement
        self.analyze = analyze


@compiles(Explain, "postgresql")
def _compile_explain(element: Explain, compiler: Any, **kw: Any) -> str:
    options = "(ANALYZE, BUFFERS) " if element.analyze else ""
    return f"EXPLAIN {options}{compiler.process(element.statement, **kw)}"


def explain_rows(
    session: Session, window: Window, *, analyze: bool = False, **filters: Any
) -> list[str]:
    """The query plan of :func:`fetch_rows` (tests and the performance report)."""
    query = build_query(window, **filters)
    if query is None:
        return []
    return [str(line) for (line,) in session.execute(Explain(query, analyze=analyze))]


# -- destinations ---------------------------------------------------------------------------------


@dataclass
class DomainHits:
    """Where a requested domain was seen around the requested moment."""

    #: Destination addresses the domain resolved to (DNS answers) or was served from (web/app).
    addresses: set[str] = field(default_factory=set)
    #: ``(srcip, address)`` pairs from DNS answers to that client.
    dns_pairs: set[tuple[str, str]] = field(default_factory=set)

    def matches(self, row: TrafficRow) -> bool:
        """The session itself is tied to the name: its own web/app log names it, or the client
        resolved the name to this address."""
        if row.named:
            return True
        return row.dstip is not None and (row.srcip, row.dstip) in self.dns_pairs


def _host_matches(column: Any, domain: str) -> Any:
    lowered = func.lower(column)
    return or_(lowered == domain, lowered.endswith(f".{domain}", autoescape=True))


def find_domain(session: Session, domain: str, window: Window, dns_lookback: int) -> DomainHits:
    """Addresses of the name around the moment: DNS answers (index ``(qname, time)``) and the
    destinations of web/app logs with the host name, both from ``dns_lookback`` seconds before
    the window to its end (resolvers cache answers, so the query precedes the session; a CDN
    address seen for the name by another client counts less, see the score). Sessions named by
    their *own* web/app logs are found by the traffic query itself (``build_query(domain=…)``).
    """
    hits = DomainHits()
    low = window.low - timedelta(seconds=dns_lookback)
    names = [domain] if domain.startswith("www.") else [domain, f"www.{domain}"]
    dns = select(FgDns.srcip, FgDns.answers).where(
        FgDns.qname.in_(names), FgDns.time >= low, FgDns.time <= window.high
    )
    for srcip, answers in session.execute(dns):
        for address in answers or ():
            hits.addresses.add(address)
            if srcip is not None:
                hits.dns_pairs.add((srcip, address))
    for model in (FgWeb, FgApp):
        query = (
            select(model.dstip)
            .distinct()
            .where(
                model.time >= low,
                model.time <= window.high,
                model.dstip.is_not(None),
                _host_matches(model.hostname, domain),
            )
        )
        hits.addresses.update(str(address) for (address,) in session.execute(query))
    return hits


def destination_match(
    group: SessionGroup, request: LookupRequest, domain: DomainHits | None
) -> tuple[DestinationMatch, int]:
    """How the session matches the requested destination, and the score points for it."""
    row = group.first
    wants_port = request.dest_port is not None
    port_ok = wants_port and row.dstport == request.dest_port
    if request.dest_ip is not None and row.dstip == request.dest_ip:
        ip_label: DestinationMatch = (
            "ip_port" if port_ok else "ip_other_port" if wants_port else "ip"
        )
        return ip_label, DESTINATION_POINTS[ip_label]
    if domain is not None and request.dest_domain is not None:
        if any(domain.matches(r) for r in group.rows):
            key = "domain_port" if port_ok else "domain_other_port" if wants_port else "domain"
            label: DestinationMatch = (
                "domain_other_port" if key == "domain_other_port" else "domain"
            )
            return label, DESTINATION_POINTS[key]
        if row.dstip in domain.addresses:
            # Only the address is known for the name (another client's DNS/web log); a CDN
            # address serves many names, so this counts less and is labelled as such (the
            # port, matching or not, is visible in the session).
            key = "domain_address_port" if port_ok else "domain_address"
            return "domain_address", DESTINATION_POINTS[key]
    if request.dest_ip is None and request.dest_domain is None:
        if not wants_port:
            return "not_requested", DESTINATION_POINTS["not_requested"]
        if port_ok:
            return "port", DESTINATION_POINTS["port"]
    return "none", DESTINATION_POINTS["none"]


def time_points(distance_ms: int, tolerance: int) -> int:
    if distance_ms <= 0:
        return TIME_POINTS
    window_ms = tolerance * 1000
    if window_ms <= 0 or distance_ms >= window_ms:
        return 0
    return TIME_POINTS * (window_ms - distance_ms) // window_ms
