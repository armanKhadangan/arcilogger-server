"""5651 lookup engine (spec 2.3, 9.3-9.5; contract: docs/MVP-CONTRACTS.md section 5).

* :func:`attribute` — internal IP + moment → lease → device → person/department (9.4).
* :func:`run_lookup` — answers a request in one of four modes:

  ``exact``
      The public IP is one of the FortiGate's own addresses (``LookupSettings.exact_addresses``)
      and a port is given: ``transip = IP AND transport = port`` identifies the session. When
      the request names a destination, only the sessions on that NAT port that went to it are
      listed; the sessions whose logged destination IP/port differs are counted without being
      listed (``exact_other_destinations``). A **domain-only** request is different: a missing
      DNS/web/app log (name resolved by another resolver, DoH, a long-cached answer) does not
      show another destination. Sessions tied to the name are listed; when none is, the sessions
      on the NAT port (to the requested port, if one is given) are listed as
      ``domain_unverified`` with ``exact_domain_unverified``; when some are, the unverified ones
      are only counted (``exact_unverified_destinations``) — never a false "no session".
  ``candidates``
      Double NAT (2.3): the ISP router's translation is unknown, so the sessions in the window
      (± tolerance; only those to the requested destination when one is given — spec 2.3) are
      ranked by destination match and time distance (:mod:`.sessions`), at most
      ``lookup.max_candidates`` (``truncated`` + explanation beyond that). Also used for a
      destination-only request and for the FortiGate's address without a port.
  ``internal``
      The request names an internal IP and/or a MAC address: the attribution chain only.
  ``none``
      Nothing found; the explanations say why and what data would help.

**Data minimisation (spec 3.3), both NAT modes:** when the request names a destination (IP,
port or domain) and no session goes to it, the answer is ``none`` with
``destination_not_found``: unrelated clients are never listed. A request without destination
lists the whole window (candidates) or every session on the NAT port (exact) on purpose.

**Configured NAT addresses are checked against the logs.** Settings are not time-scoped: an
address in ``nat.public_ips`` / ``nat.wan_transip`` (e.g. entered after the ISP router was
switched to bridge mode) is used as ``transip`` only when the FortiGate logs show it as the
source-NAT address around the requested moment (± :data:`TRANSIP_PROBE_SECONDS`), or show no
other source-NAT address then — the FortiGate's *other* configured public addresses (dual WAN,
IP pool) do not count against it, they are not a sign of double NAT. Otherwise a requested
address is answered as double NAT (candidate search, ``public_ip_not_transip``), never with a
false "the FortiGate logged no session"; a ``nat.wan_transip`` that only narrows a double-NAT
search is dropped from it (``wan_transip_not_seen``). A search not narrowed to one ``transip``
leaves out the sessions translated to another configured public address: they did not leave
through the requested one.

**Address family** (defence in depth; the API refuses IPv6 lookups, ``ipv6_not_supported``:
there is no IPv6 identity evidence, no NDP or DHCPv6): only sessions that left with an address
(``transip``, else the source) of the requested public IP's family are candidates, so no IPv4
session — the FortiGate's IPv4 WAN address included — is ever a candidate for an IPv6 address.

Every match carries the attribution chain of its source IP at the session moment nearest to the
requested time; IP conflicts appear as chain alternatives (low confidence). A chain's person is
the device-person assignment **valid at that moment** (assignment history, ADR-0008) and the
person's current department (``person_department_current``); only a device without any
assignment history falls back to its owner at lookup time, and the outcome then says so
(``person_link_at_lookup_time``). Both functions
only read (JIT compilation is switched off for the caller's transaction, ``SET LOCAL``); the API
stores the lookup and writes the audit entry.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import all_, cast, exists, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY, INET
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import FgTraffic
from arcilogger_db.settings import get_setting, get_settings
from arcilogger_worker.correlation.watermark import get_watermark
from arcilogger_worker.lookup.attribution import Attributor
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
    time_points,
)
from arcilogger_worker.lookup.texts import explain
from arcilogger_worker.lookup.types import (
    AttributionChain,
    DataWindow,
    DestinationMatch,
    Explanation,
    LookupMode,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    MatchKind,
    NatMatch,
)
from arcilogger_worker.lookup.window import data_window

#: DNS answers this old still count for a requested domain (setting
#: ``lookup.dns_lookback_seconds``; resolvers cache answers, so the query precedes the session).
DEFAULT_DNS_LOOKBACK_SECONDS = 3600
#: A configured NAT address counts as the FortiGate's ``transip`` when the logs show it within
#: this many seconds of the requested moment (or show no source-NAT address at all then).
TRANSIP_PROBE_SECONDS = 900
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


def _ip_order(ip: str) -> tuple[int, int]:
    address = ipaddress.ip_address(ip)
    return address.version, int(address)


def attribute(session: Session, ip: str, at: datetime) -> AttributionChain | None:
    """Attribution chain of internal ``ip`` at ``at``; ``None`` when no lease covers it.

    When several MACs held ``ip`` at ``at`` (IP conflict, spec 9.1) the result is the most
    likely chain with the others in ``alternatives`` (low confidence, ``ip_conflict``).
    """
    settings = LookupSettings.from_settings(get_settings(session))
    request = LookupRequest(at=at, internal_ip=ip)
    return Attributor(session, settings).chain(str(request.internal_ip), request.at)


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
    in the database is in the sealed vault.
    """
    return _Lookup(session, request, settings, layout=layout, now=now or utc_now()).run()


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
        self.attributor = Attributor(session, settings)
        self.explanations: list[Explanation] = []
        self.truncated = False
        #: A configured NAT address was not the FortiGate's transip then: answered as double NAT.
        self.fallback = False
        self._seen: dict[str, bool] = {}

    # -- entry ------------------------------------------------------------------------------------

    def run(self) -> LookupOutcome:
        # The lookup queries are bounded index scans; the planner's cost estimate of the
        # correlated domain sub-queries crosses ``jit_above_cost`` and JIT compilation then
        # takes longer (~0.8 s) than the query itself. Off for this transaction only.
        self.session.execute(text("SET LOCAL jit = off"))
        window, notes = data_window(self.session, self.request.at, layout=self.layout, now=self.now)
        self.explanations.extend(notes)
        self._watermark_note()
        attribution: AttributionChain | None = None
        matches: list[NatMatch] = []
        mode: LookupMode
        request = self.request
        exact = self.settings.exact_addresses()
        if request.is_internal:
            attribution = self._internal()
            mode = "internal" if attribution is not None else "none"
        elif (
            request.public_ip in exact
            and request.public_port is not None
            and self._transip_seen(request.public_ip, requested=True)
        ):
            matches = self._exact()
            mode = "exact" if matches else "none"
        else:
            matches = self._candidates()
            mode = "candidates" if matches else "none"
        return self._outcome(mode, matches, attribution, window)

    def _outcome(
        self,
        mode: LookupMode,
        matches: list[NatMatch],
        attribution: AttributionChain | None,
        window: DataWindow,
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
        unique: dict[str, Explanation] = {}
        for item in self.explanations:
            unique.setdefault(item.key, item)
        return LookupOutcome(
            request=self.request,
            mode=mode,
            matches=tuple(matches),
            double_nat=self.settings.double_nat or self.fallback,
            attribution=attribution,
            explanations=tuple(unique.values()),
            data_window=window,
            tolerance_seconds=self.tolerance,
            generated_at=self.now,
            truncated=self.truncated,
        )

    def _watermark_note(self) -> None:
        watermark = get_watermark(self.session)
        if watermark is not None and self.window.low > watermark:
            self.explanations.append(
                explain("lease_timeline_behind", watermark=watermark.isoformat())
            )

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
                return None
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
            return None
        if mac is not None and request.internal_ip is not None:
            macs = {chain.lease.mac if chain.lease else None} | {
                a.lease.mac for a in chain.alternatives if a.lease is not None
            }
            if mac not in macs:
                self.explanations.append(explain("mac_not_at_ip", mac=mac, ip=ip))
        return chain

    # -- exact ------------------------------------------------------------------------------------

    def _exact(self) -> list[NatMatch]:
        request = self.request
        fetched = fetch_rows(
            self.session,
            self.window,
            transip=request.public_ip,
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
                explain("no_match_exact", address=request.public_ip, port=request.public_port)
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

    def _candidates(self) -> list[NatMatch]:
        request = self.request
        exact = self.settings.exact_addresses()
        transip = None
        if request.public_ip in exact and self._transip_seen(request.public_ip, requested=True):
            transip = request.public_ip
        elif (
            self.settings.wan_transip is not None
            and self.settings.wan_transip != request.public_ip
            and self._transip_seen(self.settings.wan_transip, requested=False)
        ):
            transip = self.settings.wan_transip
        common: dict[str, Any] = {
            "transip": transip,
            "proto": request.protocol,
            "srcips": self._source_filter(),
        }
        if transip is None:
            # Sessions that left through another of the FortiGate's own public addresses did
            # not leave through the requested one (dual WAN, IP pool).
            others = set(self.settings.public_ips) - {request.public_ip}
            if others:
                common["exclude_transips"] = others
        wants_destination = self._wants_destination()
        domain = self._domain_hits()
        if wants_destination:
            groups, capped = self._destination_groups(common, domain)
        else:
            fetched = fetch_rows(self.session, self.window, **common)
            groups, capped = group_sessions(fetched.rows), fetched.capped
        groups = self._same_family(groups)
        if not groups:
            self._explain_no_candidates(wants_destination)
            return []
        self.explanations.append(self._search_note(found=True))
        if not wants_destination:
            self.explanations.append(explain("candidates_without_destination"))
        port_hint = bool(get_setting(self.session, "lookup.port_hint", False)) and (
            request.public_port is not None and (request.public_ip not in exact or self.fallback)
        )
        if port_hint:
            self.explanations.append(explain("port_hint"))
        scored = []
        for group in groups:
            match, points = destination_match(group, request, domain)
            score = points + time_points(group.distance_ms(request.at), self.tolerance)
            if port_hint and group.first.transport == request.public_port:
                score += PORT_HINT_POINTS
            scored.append((group, match, min(score, CANDIDATE_MAX_SCORE)))
        return self._matches(scored, kind="candidate", capped=capped)

    def _destination_groups(
        self, common: dict[str, Any], domain: DomainHits | None
    ) -> tuple[list[SessionGroup], bool]:
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
        groups = [
            group
            for group in group_sessions(rows)
            if destination_match(group, request, domain)[0] != "none"
        ]
        return groups, capped

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

    def _search_note(self, *, found: bool) -> Explanation:
        """Why this is a candidate search (the spec 12.4 text only when candidates follow)."""
        request = self.request
        if request.public_ip is None:
            return explain("destination_search")
        if self.fallback and request.public_ip in self.settings.exact_addresses():
            return explain("double_nat" if found else "double_nat_no_candidates")
        if request.public_ip in self.settings.exact_addresses():
            return explain("port_missing")
        if self.settings.double_nat:
            return explain("double_nat" if found else "double_nat_no_candidates")
        return explain("public_ip_not_configured", address=request.public_ip)

    def _explain_no_candidates(self, wants_destination: bool) -> None:
        self.explanations.append(self._search_note(found=False))
        self.explanations.append(explain("no_match"))
        if wants_destination:
            self.explanations.append(
                explain("destination_not_found", destination=self._destination_text())
            )
        self.explanations.append(explain("no_match_candidates"))

    # -- shared -----------------------------------------------------------------------------------

    def _wants_destination(self) -> bool:
        request = self.request
        return (
            request.dest_ip is not None
            or request.dest_domain is not None
            or request.dest_port is not None
        )

    def _transip_seen(self, address: str, *, requested: bool) -> bool:
        """Is the configured ``address`` the FortiGate's source-NAT address around the moment?

        True when a ``fg_traffic`` row within ± :data:`TRANSIP_PROBE_SECONDS` has it as
        ``transip``, or when no row there has another source-NAT address (a quiet period does
        not contradict the setting; nor do the FortiGate's other configured public addresses —
        dual WAN or an IP pool is not double NAT). Otherwise the ISP router most likely still
        translated at that time (double NAT, e.g. a moment before the switch to bridge mode):
        for the ``requested`` public address the lookup falls back to a candidate search and
        says why (``public_ip_not_transip``); a ``nat.wan_transip`` that would only narrow a
        double-NAT search is not used (``wan_transip_not_seen``).
        """
        if address in self._seen:
            return self._seen[address]
        probe = timedelta(seconds=max(TRANSIP_PROBE_SECONDS, self.tolerance))
        low, high = self.request.at - probe, self.request.at + probe
        seen = bool(
            self.session.scalar(
                select(
                    exists().where(
                        FgTraffic.time >= low,
                        FgTraffic.time <= high,
                        FgTraffic.transip == address,
                    )
                )
            )
        )
        if not seen:
            own = sorted({address, *self.settings.public_ips})
            seen = not self.session.scalar(
                select(
                    exists().where(
                        FgTraffic.time >= low,
                        FgTraffic.time <= high,
                        FgTraffic.trandisp == "snat",
                        FgTraffic.transip.is_not(None),
                        FgTraffic.transip != all_(cast(literal(own, ARRAY(INET)), ARRAY(INET))),
                    )
                )
            )
        if not seen and requested:
            self.fallback = True
            self.explanations.append(explain("public_ip_not_transip", address=address))
        elif not seen:
            self.explanations.append(explain("wan_transip_not_seen", address=address))
        self._seen[address] = seen
        return seen

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
        matches = []
        for group, match, score in shown:
            matches.append(
                NatMatch(
                    kind=kind,
                    score=score,
                    time_distance_ms=group.distance_ms(at),
                    destination_match=match,
                    traffic=group.traffic(at),
                    chain=self.attributor.chain(group.first.srcip, group.moment(at)),
                )
            )
        return matches
