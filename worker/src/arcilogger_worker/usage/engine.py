"""Usage rollup of one site day (spec 10.2-10.3): sessions → 1-minute buckets → metrics.

Everything here is an **estimate** computed from connection logs (FortiGate traffic, web
filter, application control, DNS); docs/KULLANIM.md explains every step and its blind spots.

**Input** (one site day ``[lo, hi)`` in Europe/Istanbul, read per group of source addresses):

* ``fg_traffic`` forward sessions from internal addresses (RFC 1918 / ULA) with a time in
  ``[lo - overlap, hi + overlap)`` (``usage.session_overlap_seconds``: sessions running over
  midnight), except refused connections (``action`` ``deny`` / ``ip-conn``);
* ``fg_web`` / ``fg_app`` rows of the same addresses (host names by ``sessionid``, URLs,
  categories, blocked attempts) and ``fg_dns`` rows (answers, blocked queries); for a session
  that started before ``lo - overlap``, its host name rows back to its start
  (:func:`_names_before`).

**Sessions.** Logs of one session (``srcip``, ``sessionid``, ``dstip``, ``dstport``) are taken
in time order. Each log adds an increment: an interim log (``is_interim``) with deltas adds
``sentdelta`` / ``rcvddelta`` over ``[time - durationdelta, time]``; any other log adds what its
cumulative ``sentbyte`` / ``rcvdbyte`` exceed the increments so far ("final remainder") over
``[previous log, time]`` (the first log: ``[time - duration, time]``). So interim and final logs
are never counted twice, and bytes of lost interim logs still arrive with the final log. When
the first log read is an interim log, its counters minus its deltas (the bytes before its
interval: earlier logs of a session that started before the loaded window, or a lost first
interim log) are an increment over ``[min(time - duration), time - durationdelta]``. Every
increment is spread evenly over its interval; only the part inside the day is kept, so the part
of a long session before the day stays with the earlier day. The session is open (online) over
``[min(time - duration), last log]``.

**Destination** (spec 10.2, in this order): ``fg_web.hostname`` with the same ``sessionid``
(and ``srcip``); ``fg_app.hostname`` (application control and SSL ``sni``) with the same
``sessionid``; the newest ``fg_dns`` answer of the same ``srcip`` that contained ``dstip``, at
most ``usage.dns_window_seconds`` before the session started; ``dstinetsvc`` (FortiGuard
service, kind ``service``); else the address (kind ``ip``). Host names are reduced to their
registrable domain (:mod:`.domains`); the full host name is kept for the drill-down.

**Buckets** (1 minute, local time): a subject (:mod:`.subjects`) is *online* in every minute a
session of it is open, *active* in every minute in which its non-background bytes reach
``usage.active_bytes_threshold``. The 60 seconds of an active minute are split between the
domains (host names, applications) by their non-background bytes in that minute, so the time per
site adds up to the active time ("raw" time: 60 s for every minute with any traffic of the
domain). A session counts once for each subject, in the part (work hours / off hours) of its
first minute. Device rows (per MAC) apply the same rules to that device's traffic alone, and
count every blocked attempt of the device (``usage.blocked_per_day`` limits only the stored
attempts), so the devices of a person add up to the person's count. A subject's VLAN and address
are those of its most online minutes, else (only blocked attempts or URLs that day) those of its
filter events.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import (
    BackgroundRules,
    DomainNamer,
    is_ip_literal,
    normalize_hostname,
)
from arcilogger_worker.usage.subjects import (
    IpTimeline,
    Subject,
    build_timelines,
    timelines_hash,
)

#: Raised whenever the meaning of a stored rollup changes: the recent days are rebuilt.
ENGINE_VERSION: Final = 3
BUCKET: Final = 60
#: Source addresses read per query.
IP_GROUP: Final = 64
#: A DNS answer logged this many seconds after the session start still names it (the session
#: start is derived from whole-second durations).
DNS_TOLERANCE: Final = 2.0
#: A filter log this many seconds before the derived session start still belongs to the session.
START_SLACK: Final = 2.0
#: Refused connections are no sessions (``0000000011`` "ip-conn", policy deny).
REFUSED_ACTIONS: Final = ("deny", "ip-conn")
#: Blocked attempts (spec 10.3). Application control ``reset`` refuses the session too.
WEB_BLOCKED: Final = frozenset({"blocked", "block"})
DNS_BLOCKED: Final = frozenset({"block", "blocked"})
APP_BLOCKED: Final = frozenset({"block", "blocked", "reset"})
#: Internal source addresses (the usage of the LAN, not inbound traffic): RFC 1918 and ULA.
PRIVATE: Final = (
    "(srcip << inet '10.0.0.0/8' OR srcip << inet '172.16.0.0/12' "
    "OR srcip << inet '192.168.0.0/16' OR srcip << inet 'fc00::/7')"
)
#: Most top domains stored per device and day.
DEVICE_TOP: Final = 20


# -- results --------------------------------------------------------------------------------------


@dataclass
class DayResult:
    day: date
    lo: datetime
    hi: datetime
    ips: list[str]
    attribution_hash: str
    #: Newest traffic log time inside the day (``None`` without traffic).
    computed_until: datetime | None
    daily: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)
    domains: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, Any]] = field(default_factory=list)
    hourly: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    urls: list[dict[str, Any]] = field(default_factory=list)
    #: Registrable domain → (category, cat, source, hits).
    categories: dict[str, tuple[str, int | None, str, int]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


# -- input rows -----------------------------------------------------------------------------------


@dataclass(slots=True)
class TrafficLog:
    time: float
    duration: int | None
    sentbyte: int | None
    rcvdbyte: int | None
    sentdelta: int | None
    rcvddelta: int | None
    durationdelta: int | None
    is_interim: bool


@dataclass(frozen=True, slots=True)
class Flow:
    """What one session's bytes are attributed to (per device)."""

    mac: str | None
    kind: str
    domain: str
    host: str
    app: str | None
    appcat: str | None
    background: bool


def _ts(value: datetime) -> float:
    return value.timestamp()


def _chunks(items: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def source_ips(session: Session, lo: datetime, hi: datetime, horizon: datetime) -> list[str]:
    """Internal source addresses with traffic (``[lo, horizon)``) or filter events (day)."""
    rows = session.execute(
        text(
            f"SELECT srcip FROM fg_traffic WHERE time >= :lo AND time < :horizon "  # noqa: S608
            f"AND subtype = 'forward' AND {PRIVATE} "
            f"UNION SELECT srcip FROM fg_web WHERE time >= :lo AND time < :hi AND {PRIVATE} "
            f"UNION SELECT srcip FROM fg_dns WHERE time >= :lo AND time < :hi "
            f"AND action = ANY(:dns) AND {PRIVATE} "
            f"UNION SELECT srcip FROM fg_app WHERE time >= :lo AND time < :hi "
            f"AND action = ANY(:app) AND {PRIVATE}"
        ),
        {
            "lo": lo,
            "hi": hi,
            "horizon": horizon,
            "dns": sorted(DNS_BLOCKED),
            "app": sorted(APP_BLOCKED),
        },
    ).scalars()
    return sorted(str(ip) for ip in rows if ip is not None)


# -- the day --------------------------------------------------------------------------------------


@dataclass
class _Metrics:
    online: int = 0
    active: int = 0
    down: float = 0.0
    up: float = 0.0
    first: int | None = None
    last: int | None = None


class DayBuilder:
    """Accumulates one day; :meth:`finish` turns the buckets into table rows."""

    def __init__(
        self,
        day: date,
        settings: UsageSettings,
        namer: DomainNamer,
        rules: BackgroundRules,
    ) -> None:
        self.day = day
        self.settings = settings
        self.namer = namer
        self.rules = rules
        lo, hi = day_window(day)
        self.lo, self.hi = lo.astimezone(UTC), hi.astimezone(UTC)
        self.lo_ts, self.hi_ts = _ts(self.lo), _ts(self.hi)
        self.buckets = int((self.hi_ts - self.lo_ts) // BUCKET)
        self.off = settings.off_hours_minutes(day)
        self.flows: dict[Flow, int] = {}
        self.flow_list: list[Flow] = []
        self.subjects: dict[str, Subject] = {}
        #: subject → bucket → flow id → [non-background bytes, received, sent]
        self.cells: dict[str, dict[int, dict[int, list[float]]]] = defaultdict(dict)
        self.online: dict[str, set[int]] = defaultdict(set)
        #: subject → MAC → online minutes
        self.online_device: dict[str, dict[str, set[int]]] = defaultdict(dict)
        self.device_ips: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.vlans: dict[str, Counter[int]] = defaultdict(Counter)
        self.device_vlans: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
        self.subject_ips: dict[str, Counter[str]] = defaultdict(Counter)
        #: VLANs and addresses of the filter events (blocked attempts, URLs): those of a subject
        #: (or of one of its devices) without an online minute that day
        self.event_vlans: dict[str, Counter[int]] = defaultdict(Counter)
        self.event_ips: dict[str, Counter[str]] = defaultdict(Counter)
        self.event_device_vlans: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
        self.event_device_ips: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.sessions: Counter[tuple[str, bool]] = Counter()
        #: subject → (part, flow id) → sessions
        self.flow_sessions: dict[str, Counter[tuple[bool, int]]] = defaultdict(Counter)
        self.device_sessions: Counter[tuple[str, str, bool]] = Counter()
        self.blocked_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.blocked_counts: Counter[tuple[str, bool]] = Counter()
        #: subject → (MAC, part) → blocked attempts (every one; ``usage_blocked`` keeps the newest)
        self.device_blocked: dict[str, Counter[tuple[str, bool]]] = defaultdict(Counter)
        #: subject → (part, url, host) → [hits, first, last, category, blocked]
        self.urls: dict[str, dict[tuple[bool, str, str | None], list[Any]]] = defaultdict(dict)
        self.categories: dict[str, Counter[tuple[str, int | None, str]]] = defaultdict(Counter)
        self.newest: float | None = None
        self.counts: Counter[str] = Counter()

    # -- helpers ----------------------------------------------------------------------------------

    def bucket_of(self, moment: float) -> int:
        return int((moment - self.lo_ts) // BUCKET)

    def part(self, moment: float) -> bool:
        index = self.bucket_of(moment)
        return self.off[min(max(index, 0), self.buckets - 1)]

    def _flow(self, flow: Flow) -> int:
        known = self.flows.get(flow)
        if known is None:
            known = len(self.flow_list)
            self.flows[flow] = known
            self.flow_list.append(flow)
        return known

    def _subject(self, subject: Subject) -> str:
        self.subjects.setdefault(subject.key, subject)
        return subject.key

    # -- one address ------------------------------------------------------------------------------

    def add_ip(
        self,
        ip: str,
        timeline: IpTimeline,
        traffic: Sequence[tuple[Any, ...]],
        web: Sequence[tuple[Any, ...]],
        app: Sequence[tuple[Any, ...]],
        dns: Sequence[tuple[Any, ...]],
    ) -> None:
        web_hosts = self._web(ip, timeline, web)
        app_hosts = self._app(ip, timeline, app)
        answers = self._dns(ip, timeline, dns)
        group: list[tuple[Any, ...]] = []
        key: tuple[Any, ...] | None = None
        for row in traffic:
            # (sessionid, dstip, dstport, time, …); rows without a session id stand alone
            row_key = (row[0], row[1], row[2]) if row[0] is not None else (None, id(row))
            if row_key != key and group:
                self._session(ip, timeline, group, web_hosts, app_hosts, answers)
                group = []
            key = row_key
            group.append(row)
        if group:
            self._session(ip, timeline, group, web_hosts, app_hosts, answers)

    def _event_subject(self, ip: str, timeline: IpTimeline, moment: float) -> Subject:
        """The subject of a filter event (blocked attempt, URL); counts its VLAN and address."""
        subject = timeline.at(moment)
        key = self._subject(subject)
        if subject.vlan is not None:
            self.event_vlans[key][subject.vlan] += 1
        self.event_ips[key][ip] += 1
        if subject.mac is not None:
            pair = (key, subject.mac)
            if subject.vlan is not None:
                self.event_device_vlans[pair][subject.vlan] += 1
            self.event_device_ips[pair][ip] += 1
        return subject

    def _blocked(
        self,
        subject: Subject,
        moment: float,
        source: str,
        action: str,
        host: str | None,
        category: str | None,
        url: str | None,
        app: str | None,
    ) -> None:
        key = subject.key
        part = self.part(moment)
        self.blocked_counts[(key, part)] += 1
        if subject.mac is not None:
            self.device_blocked[key][(subject.mac, part)] += 1
        domain = self.namer.registrable(host) if host else None
        self.blocked_events[key].append(
            {
                "time": moment,
                "off_hours": part,
                "mac": subject.mac,
                "source": source,
                "action": action,
                "domain": domain,
                "hostname": host,
                "category": category,
                "url": url,
                "app": app,
            }
        )

    def _in_day(self, moment: float) -> bool:
        return self.lo_ts <= moment < self.hi_ts

    def _web(
        self, ip: str, timeline: IpTimeline, rows: Sequence[tuple[Any, ...]]
    ) -> dict[int, str]:
        hosts: dict[int, str] = {}
        for sessionid, moment_dt, hostname, url, cat, catdesc, action in rows:
            moment = _ts(moment_dt)
            host = normalize_hostname(hostname)
            if sessionid is not None and host is not None and sessionid not in hosts:
                hosts[sessionid] = host
            if not self._in_day(moment):
                continue
            self.counts["web_rows"] += 1
            if host is not None and catdesc and not is_ip_literal(host):
                self.categories[self.namer.registrable(host)][("web", cat, catdesc)] += 1
            blocked = (action or "").lower() in WEB_BLOCKED
            if not (blocked or url):
                continue
            subject = self._event_subject(ip, timeline, moment)
            if blocked:
                self._blocked(subject, moment, "web", action, host, catdesc, url, None)
            if url:
                urls = self.urls[subject.key]
                url_key = (self.part(moment), url, host)
                entry = urls.get(url_key)
                if entry is None:
                    urls[url_key] = [1, moment, moment, catdesc, blocked]
                else:
                    entry[0] += 1
                    entry[1] = min(entry[1], moment)
                    if moment >= entry[2]:
                        entry[2] = moment
                        entry[3] = catdesc or entry[3]
                    entry[4] = entry[4] or blocked
        return hosts

    def _app(
        self, ip: str, timeline: IpTimeline, rows: Sequence[tuple[Any, ...]]
    ) -> dict[int, str]:
        hosts: dict[int, str] = {}
        for sessionid, moment_dt, hostname, app, appcat, action, subtype in rows:
            moment = _ts(moment_dt)
            host = normalize_hostname(hostname)
            if sessionid is not None and host is not None and sessionid not in hosts:
                hosts[sessionid] = host
            if subtype == "ssl" or not self._in_day(moment):
                continue
            if (action or "").lower() in APP_BLOCKED:
                subject = self._event_subject(ip, timeline, moment)
                self._blocked(subject, moment, "app", action, host, appcat, None, app)
        return hosts

    def _dns(
        self, ip: str, timeline: IpTimeline, rows: Sequence[tuple[Any, ...]]
    ) -> dict[str, tuple[list[float], list[str]]]:
        answers: dict[str, tuple[list[float], list[str]]] = {}
        for moment_dt, qname, addresses, cat, catdesc, action in rows:
            moment = _ts(moment_dt)
            host = normalize_hostname(qname)
            if host is not None and addresses:
                for address in addresses:
                    times, names = answers.setdefault(str(address), ([], []))
                    times.append(moment)
                    names.append(host)
            if not self._in_day(moment):
                continue
            if host is not None and catdesc and not is_ip_literal(host):
                self.categories[self.namer.registrable(host)][("dns", cat, catdesc)] += 1
            if (action or "").lower() in DNS_BLOCKED:
                subject = self._event_subject(ip, timeline, moment)
                self._blocked(subject, moment, "dns", action, host, catdesc, None, None)
        return answers

    # -- one session ------------------------------------------------------------------------------

    def _destination(
        self,
        sessionid: int | None,
        dstip: str | None,
        start: float,
        inetsvc: str | None,
        web_hosts: dict[int, str],
        app_hosts: dict[int, str],
        answers: dict[str, tuple[list[float], list[str]]],
    ) -> tuple[str, str, str | None]:
        """(kind, domain, host name) of the session (spec 10.2)."""
        host = None
        if sessionid is not None:
            host = web_hosts.get(sessionid) or app_hosts.get(sessionid)
        if host is None and dstip is not None:
            found = answers.get(dstip)
            if found is not None:
                times, names = found
                index = bisect.bisect_right(times, start + DNS_TOLERANCE) - 1
                if index >= 0 and times[index] >= start - self.settings.dns_window_seconds:
                    host = names[index]
        if host is not None:
            if is_ip_literal(host):
                return "ip", host, host
            return "domain", self.namer.registrable(host), host
        if inetsvc:
            return "service", inetsvc, None
        return "ip", dstip or "?", None

    def _session(
        self,
        ip: str,
        timeline: IpTimeline,
        rows: list[tuple[Any, ...]],
        web_hosts: dict[int, str],
        app_hosts: dict[int, str],
        answers: dict[str, tuple[list[float], list[str]]],
    ) -> None:
        logs = [
            TrafficLog(_ts(r[3]), r[4], r[5], r[6], r[7], r[8], r[9], bool(r[10])) for r in rows
        ]
        start = min(log.time - (log.duration or 0) for log in logs)
        end = max(log.time for log in logs)
        if end < self.lo_ts or start >= self.hi_ts:
            return
        self.counts["sessions_read"] += 1
        sessionid, dstip, dstport = rows[0][0], rows[0][1], rows[0][2]
        app = next((r[11] for r in rows if r[11]), None)
        appcat = next((r[12] for r in rows if r[12]), None)
        inetsvc = next((r[13] for r in rows if r[13]), None)
        increments: list[tuple[float, float, int, int]] = []
        total_rcvd = total_sent = 0
        first_log = logs[0]
        if first_log.is_interim and (
            first_log.rcvddelta is not None or first_log.sentdelta is not None
        ):
            # The bytes before this log's interval came between the session start and that
            # interval: in earlier logs before the loaded window (a long session over midnight,
            # only the part inside the day counts) or in interim logs that were lost.
            total_rcvd = max(0, (first_log.rcvdbyte or 0) - max(0, first_log.rcvddelta or 0))
            total_sent = max(0, (first_log.sentbyte or 0) - max(0, first_log.sentdelta or 0))
            if total_rcvd or total_sent:
                until = first_log.time - (first_log.durationdelta or 0)
                increments.append((start, max(start, until), total_rcvd, total_sent))
        previous: float | None = None
        for log in logs:
            if log.is_interim and (log.rcvddelta is not None or log.sentdelta is not None):
                rcvd, sent = max(0, log.rcvddelta or 0), max(0, log.sentdelta or 0)
                if log.durationdelta is not None:
                    begin = log.time - log.durationdelta
                elif previous is not None:
                    begin = previous
                else:
                    begin = log.time - (log.duration or 0)
            else:
                rcvd = max(0, (log.rcvdbyte or 0) - total_rcvd)
                sent = max(0, (log.sentbyte or 0) - total_sent)
                begin = previous if previous is not None else log.time - (log.duration or 0)
            total_rcvd += rcvd
            total_sent += sent
            if rcvd or sent:
                increments.append((max(begin, start), log.time, rcvd, sent))
            previous = log.time
            if self._in_day(log.time) and (self.newest is None or log.time > self.newest):
                self.newest = log.time
        kind, domain, host = self._destination(
            sessionid, dstip, start, inetsvc, web_hosts, app_hosts, answers
        )
        background = (
            total_rcvd + total_sent < self.settings.background_session_bytes
            or self.rules.port(dstport)
            or (host is not None and kind == "domain" and self.rules.host(host))
            or (kind != "domain" and self.rules.appcat(appcat))
        )
        label = host if host is not None else domain
        flows: dict[str | None, int] = {}

        def flow_for(subject: Subject) -> int:
            known = flows.get(subject.mac)
            if known is None:
                known = self._flow(Flow(subject.mac, kind, domain, label, app, appcat, background))
                flows[subject.mac] = known
            return known

        # Online minutes and the session count (per subject, in the part of its first minute).
        first = max(self.bucket_of(max(start, self.lo_ts)), 0)
        if end > start:
            last = min(math.ceil((min(end, self.hi_ts) - self.lo_ts) / BUCKET), self.buckets) - 1
        else:
            last = first
        seen: set[str] = set()
        for index in range(first, last + 1):
            subject = timeline.at(self.lo_ts + index * BUCKET)
            key = self._subject(subject)
            self.online[key].add(index)
            if subject.vlan is not None:
                self.vlans[key][subject.vlan] += 1
            self.subject_ips[key][ip] += 1
            if subject.mac is not None:
                pair = (key, subject.mac)
                self.online_device[key].setdefault(subject.mac, set()).add(index)
                self.device_ips[pair][ip] += 1
                if subject.vlan is not None:
                    self.device_vlans[pair][subject.vlan] += 1
            if key not in seen:
                seen.add(key)
                part = self.off[index]
                self.sessions[(key, part)] += 1
                self.flow_sessions[key][(part, flow_for(subject))] += 1
                if subject.mac is not None:
                    self.device_sessions[(key, subject.mac, part)] += 1
        # Bytes, spread evenly over each increment's interval.
        for begin, finish, rcvd, sent in increments:
            for index, share in self._spread(begin, finish):
                subject = timeline.at(self.lo_ts + index * BUCKET)
                key = self._subject(subject)
                cell = self.cells[key].setdefault(index, {})
                flow = flow_for(subject)
                entry = cell.get(flow)
                if entry is None:
                    entry = cell[flow] = [0.0, 0.0, 0.0]
                r, s = rcvd * share, sent * share
                if not background:
                    entry[0] += r + s
                entry[1] += r
                entry[2] += s

    def _spread(self, begin: float, finish: float) -> Iterable[tuple[int, float]]:
        """``(bucket, share)`` of the interval ``[begin, finish]`` inside the day."""
        if finish <= begin:
            index = self.bucket_of(finish)
            if 0 <= index < self.buckets:
                yield index, 1.0
            return
        low, high = max(begin, self.lo_ts), min(finish, self.hi_ts)
        if high <= low:
            return
        length = finish - begin
        first = self.bucket_of(low)
        last = min(math.ceil((high - self.lo_ts) / BUCKET), self.buckets) - 1
        for index in range(first, last + 1):
            left = max(low, self.lo_ts + index * BUCKET)
            right = min(high, self.lo_ts + (index + 1) * BUCKET)
            if right > left:
                yield index, (right - left) / length

    # -- rows -------------------------------------------------------------------------------------

    def finish(self, result: DayResult) -> DayResult:
        keys = sorted(set(self.online) | set(self.cells) | set(self.blocked_events))
        keys += sorted(set(self.urls) - set(keys))
        for key in keys:
            self._finish_subject(key, result)
        self._finish_urls(result)
        for domain, counter in sorted(self.categories.items()):
            ranked = sorted(counter.items(), key=lambda item: (item[0][0] != "web", -item[1]))
            (source, cat, category), hits = ranked[0]
            result.categories[domain] = (category, cat, source, hits)
        result.computed_until = (
            None if self.newest is None else datetime.fromtimestamp(self.newest, UTC)
        )
        result.stats = {
            "subjects": len(keys),
            "flows": len(self.flow_list),
            **dict(sorted(self.counts.items())),
        }
        return result

    def _moment(self, index: int) -> datetime:
        return datetime.fromtimestamp(self.lo_ts + index * BUCKET, UTC)

    def _finish_subject(self, key: str, result: DayResult) -> None:
        subject = self.subjects[key]
        cells = self.cells.get(key, {})
        online = self.online.get(key, set())
        threshold = self.settings.active_bytes_threshold
        metrics = {False: _Metrics(), True: _Metrics()}
        for index in online:
            metrics[self.off[index]].online += 1
        domains: dict[tuple[bool, str], dict[str, Any]] = {}
        apps: dict[tuple[bool, str], list[Any]] = {}
        hourly: dict[tuple[int, bool], list[Any]] = {}
        runs: dict[bool, list[list[Any]]] = {False: [], True: []}
        devices: dict[tuple[str, bool], _Metrics] = {}
        device_domains: dict[tuple[str, bool], dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for index in online:
            hour = hourly.setdefault((index // 60, self.off[index]), [0, 0, 0.0, 0.0, Counter()])
            hour[0] += 1
        for mac, minutes in self.online_device.get(key, {}).items():
            for index in minutes:
                devices.setdefault((mac, self.off[index]), _Metrics()).online += 1
        device_blocked = self.device_blocked.get(key, Counter())
        for device_part in device_blocked:  # a device with blocked attempts only has a row too
            devices.setdefault(device_part, _Metrics())
        for index in sorted(cells):
            flows = cells[index]
            part = self.off[index]
            total = metrics[part]
            hour = hourly.setdefault((index // 60, part), [0, 0, 0.0, 0.0, Counter()])
            nonbg = 0.0
            device_nonbg: dict[str, float] = defaultdict(float)
            present: set[str] = set()
            for flow_id, (weight, rcvd, sent) in flows.items():
                flow = self.flow_list[flow_id]
                nonbg += weight
                total.down += rcvd
                total.up += sent
                hour[2] += rcvd
                hour[3] += sent
                row = domains.get((part, flow.domain))
                if row is None:
                    row = domains[(part, flow.domain)] = {
                        "kind": flow.kind,
                        "seconds": 0.0,
                        "raw": 0,
                        "down": 0.0,
                        "up": 0.0,
                        "sessions": 0,
                        "background": True,
                        "hosts": {},
                    }
                row["down"] += rcvd
                row["up"] += sent
                if not flow.background:
                    row["background"] = False
                host = row["hosts"].setdefault(flow.host, [0.0, 0.0, 0])
                host[1] += rcvd + sent
                if rcvd + sent > 0 and flow.domain not in present:
                    present.add(flow.domain)
                    row["raw"] += BUCKET
                if flow.app:
                    app = apps.setdefault((part, flow.app), [flow.appcat, 0.0, 0.0, 0])
                    app[2] += rcvd + sent
                if flow.mac is not None:
                    device = devices.setdefault((flow.mac, part), _Metrics())
                    device.down += rcvd
                    device.up += sent
                    device_nonbg[flow.mac] += weight
            if nonbg >= threshold:
                total.active += 1
                hour[1] += 1
                total.first = index if total.first is None else total.first
                total.last = index
                minute_domains: dict[str, float] = defaultdict(float)
                for flow_id, (weight, _rcvd, _sent) in flows.items():
                    if weight <= 0:
                        continue
                    flow = self.flow_list[flow_id]
                    seconds = BUCKET * weight / nonbg
                    domains[(part, flow.domain)]["seconds"] += seconds
                    domains[(part, flow.domain)]["hosts"][flow.host][0] += seconds
                    minute_domains[flow.domain] += seconds
                    hour[4][flow.domain] += seconds
                    if flow.app:
                        apps[(part, flow.app)][1] += seconds
                dominant = min(minute_domains.items(), key=lambda item: (-item[1], item[0]))[0]
                run = runs[part]
                minute = index
                if run and run[-1][1] == minute and run[-1][2] == dominant:
                    run[-1][1] = minute + 1
                else:
                    run.append([minute, minute + 1, dominant])
            for mac, weight in device_nonbg.items():
                if weight < threshold:
                    continue
                device = devices[(mac, part)]
                device.active += 1
                device.first = index if device.first is None else device.first
                device.last = index
                for flow_id, (flow_weight, _rcvd, _sent) in flows.items():
                    flow = self.flow_list[flow_id]
                    if flow.mac == mac and flow_weight > 0:
                        device_domains[(mac, part)][flow.domain] += BUCKET * flow_weight / weight
        for (part, flow_id), count in self.flow_sessions.get(key, Counter()).items():
            flow = self.flow_list[flow_id]
            row = domains.get((part, flow.domain))
            if row is None:  # a session without bytes inside the day
                row = domains[(part, flow.domain)] = {
                    "kind": flow.kind,
                    "seconds": 0.0,
                    "raw": 0,
                    "down": 0.0,
                    "up": 0.0,
                    "sessions": 0,
                    "background": flow.background,
                    "hosts": {},
                }
            row["sessions"] += count
            row["hosts"].setdefault(flow.host, [0.0, 0.0, 0])[2] += count
            if flow.app:
                apps.setdefault((part, flow.app), [flow.appcat, 0.0, 0.0, 0])[3] += count
        # The VLAN and address of the most online minutes; without any online minute (only
        # blocked attempts or URLs that day) those of the filter events.
        vlan = _most(self.vlans.get(key) or self.event_vlans.get(key))
        main_ip = _most(self.subject_ips.get(key) or self.event_ips.get(key))
        for part in (False, True):
            values = metrics[part]
            blocked = self.blocked_counts.get((key, part), 0)
            sessions = self.sessions.get((key, part), 0)
            has_urls = any(p == part for p, _, _ in self.urls.get(key, {}))
            if not (values.online or values.down or values.up or blocked or sessions or has_urls):
                continue
            result.daily.append(
                {
                    "day": self.day,
                    "subject": key,
                    "off_hours": part,
                    "kind": subject.kind,
                    "person_id": subject.person_id,
                    "mac": subject.mac if subject.kind == "device" else None,
                    "ip": key[3:] if subject.kind == "ip" else main_ip,
                    "vlan": vlan,
                    "online_seconds": values.online * BUCKET,
                    "active_seconds": values.active * BUCKET,
                    "download_bytes": round(values.down),
                    "upload_bytes": round(values.up),
                    "sessions": sessions,
                    "blocked": blocked,
                    "first_activity": None if values.first is None else self._moment(values.first),
                    "last_activity": None if values.last is None else self._moment(values.last + 1),
                    "timeline": runs[part],
                }
            )
        for (part, domain), row in domains.items():
            result.domains.append(
                {
                    "day": self.day,
                    "subject": key,
                    "domain": domain,
                    "off_hours": part,
                    "kind": row["kind"],
                    "seconds": row["seconds"],
                    "raw_seconds": row["raw"],
                    "download_bytes": round(row["down"]),
                    "upload_bytes": round(row["up"]),
                    "sessions": row["sessions"],
                    "background": row["background"],
                    "hosts": {
                        host: [round(values[0], 3), round(values[1]), values[2]]
                        for host, values in sorted(row["hosts"].items())
                    },
                }
            )
        for (part, app_name), (appcat, seconds, volume, sessions) in apps.items():
            result.apps.append(
                {
                    "day": self.day,
                    "subject": key,
                    "app": app_name,
                    "off_hours": part,
                    "appcat": appcat,
                    "seconds": seconds,
                    "bytes": round(volume),
                    "sessions": sessions,
                }
            )
        for (hour_index, part), (online_minutes, active, down, up, top) in sorted(hourly.items()):
            result.hourly.append(
                {
                    "hour": datetime.fromtimestamp(self.lo_ts + hour_index * 3600, UTC),
                    "subject": key,
                    "off_hours": part,
                    "day": self.day,
                    "online_seconds": online_minutes * BUCKET,
                    "active_seconds": active * BUCKET,
                    "download_bytes": round(down),
                    "upload_bytes": round(up),
                    "top_domain": min(top.items(), key=lambda item: (-item[1], item[0]))[0]
                    if top
                    else None,
                }
            )
        for (mac, part), values in sorted(devices.items()):
            pair = (key, mac)
            top = sorted(device_domains.get((mac, part), {}).items(), key=lambda i: (-i[1], i[0]))
            # As for the subject: without an online minute, those of the filter events.
            device_ip = _most(self.device_ips.get(pair) or self.event_device_ips.get(pair))
            device_vlan = _most(self.device_vlans.get(pair) or self.event_device_vlans.get(pair))
            result.devices.append(
                {
                    "day": self.day,
                    "mac": mac,
                    "subject": key,
                    "off_hours": part,
                    "ip": device_ip,
                    "vlan": device_vlan,
                    "online_seconds": values.online * BUCKET,
                    "active_seconds": values.active * BUCKET,
                    "download_bytes": round(values.down),
                    "upload_bytes": round(values.up),
                    "sessions": self.device_sessions.get((key, mac, part), 0),
                    "blocked": device_blocked.get((mac, part), 0),
                    "first_activity": None if values.first is None else self._moment(values.first),
                    "last_activity": None if values.last is None else self._moment(values.last + 1),
                    "top_domains": [[d, round(s, 3)] for d, s in top[:DEVICE_TOP]],
                }
            )
        events = sorted(self.blocked_events.get(key, ()), key=lambda e: e["time"], reverse=True)
        for event in events[: self.settings.blocked_per_day]:
            result.blocked.append(
                {
                    **event,
                    "day": self.day,
                    "subject": key,
                    "time": datetime.fromtimestamp(event["time"], UTC),
                }
            )

    def _finish_urls(self, result: DayResult) -> None:
        for key, urls in sorted(self.urls.items()):
            items = sorted(
                urls.items(), key=lambda item: (-item[1][2], item[0][1], item[0][2] or "")
            )
            for (part, url, host), (hits, first, last, category, blocked) in items[
                : self.settings.urls_per_day
            ]:
                result.urls.append(
                    {
                        "day": self.day,
                        "subject": key,
                        "off_hours": part,
                        "url": url,
                        "hostname": host,
                        "domain": self.namer.registrable(host) if host else None,
                        "hits": hits,
                        "first_seen": datetime.fromtimestamp(first, UTC),
                        "last_seen": datetime.fromtimestamp(last, UTC),
                        "category": category,
                        "blocked": blocked,
                    }
                )


def _most(counter: Counter[Any] | None) -> Any:
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], str(item[0])))[0]


# -- loading --------------------------------------------------------------------------------------

_TRAFFIC = text(
    "SELECT srcip, sessionid, dstip, dstport, time, duration, sentbyte, rcvdbyte, sentdelta, "
    "rcvddelta, durationdelta, is_interim, app, appcat, dstinetsvc "
    "FROM fg_traffic WHERE srcip = ANY(CAST(:ips AS inet[])) AND time >= :lo AND time < :hi "
    "AND subtype = 'forward' AND (action IS NULL OR action <> ALL(:refused)) "
    "ORDER BY srcip, sessionid, dstip, dstport, time, id"
)
_WEB = text(
    "SELECT srcip, sessionid, time, hostname, url, cat, catdesc, action FROM fg_web "
    "WHERE srcip = ANY(CAST(:ips AS inet[])) AND time >= :lo AND time < :hi "
    "ORDER BY srcip, time, id"
)
_APP = text(
    "SELECT srcip, sessionid, time, hostname, app, appcat, action, extra ->> 'subtype' "
    "FROM fg_app WHERE srcip = ANY(CAST(:ips AS inet[])) AND time >= :lo AND time < :hi "
    "ORDER BY srcip, time, id"
)
_DNS = text(
    "SELECT srcip, time, qname, answers, cat, catdesc, action FROM fg_dns "
    "WHERE srcip = ANY(CAST(:ips AS inet[])) AND time >= :lo AND time < :hi "
    "ORDER BY srcip, time, id"
)


#: Filter logs before the loaded window naming the sessions that started earlier
#: (:func:`_names_before`).
_WEB_BEFORE = text(
    "SELECT srcip, sessionid, time, hostname, url, cat, catdesc, action FROM fg_web "
    "WHERE srcip = ANY(CAST(:ips AS inet[])) AND sessionid = ANY(CAST(:sessions AS bigint[])) "
    "AND time >= :lo AND time < :hi ORDER BY srcip, time, id"
)
_APP_BEFORE = text(
    "SELECT srcip, sessionid, time, hostname, app, appcat, action, extra ->> 'subtype' "
    "FROM fg_app WHERE srcip = ANY(CAST(:ips AS inet[])) "
    "AND sessionid = ANY(CAST(:sessions AS bigint[])) AND time >= :lo AND time < :hi "
    "ORDER BY srcip, time, id"
)
_DNS_BEFORE = text(
    "SELECT d.srcip, d.time, d.qname, d.answers, d.cat, d.catdesc, d.action FROM fg_dns d "
    "JOIN unnest(CAST(:ips AS inet[]), CAST(:los AS timestamptz[]), "
    "CAST(:his AS timestamptz[])) AS w(ip, lo, hi) "
    "ON d.srcip = w.ip AND d.time >= w.lo AND d.time < w.hi ORDER BY d.srcip, d.time, d.id"
)


#: Rows per source address (the address column left out).
_Rows = dict[str, list[tuple[Any, ...]]]


def _by_ip(rows: Iterable[Sequence[Any]]) -> _Rows:
    found: _Rows = defaultdict(list)
    for row in rows:
        found[str(row[0])].append(tuple(row[1:]))
    return found


def _merged(spans: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Overlapping time ranges joined (every row is read once)."""
    merged: list[tuple[datetime, datetime]] = []
    for low, high in sorted(spans):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _names_before(
    session: Session,
    traffic: Mapping[str, Sequence[tuple[Any, ...]]],
    lo: datetime,
    read_from: datetime,
    dns_window: timedelta,
) -> tuple[_Rows, _Rows, _Rows]:
    """(web, application control, DNS) rows before ``read_from`` naming the day's sessions that
    started before it.

    The filter logs are read from ``read_from`` on (``usage.session_overlap_seconds`` before the
    day), but a session that started earlier and runs into the day (a long session over
    midnight) was named at its start. For these sessions only, the names are read back to their
    start: web filter and application control rows by ``sessionid``, DNS answers of the source
    address within ``usage.dns_window_seconds`` before the start — as the earlier day read them,
    so the session keeps its name, kind and background rule.
    """
    slack = timedelta(seconds=START_SLACK)
    dns_from = read_from - dns_window
    starts: dict[tuple[str, int], datetime] = {}
    spans: dict[str, set[tuple[datetime, datetime]]] = defaultdict(set)
    for ip, rows in traffic.items():
        for row in rows:
            # (sessionid, dstip, dstport, time, duration, …): a session reaching into the day
            # has a log inside it, and every log of a session gives the same start.
            if row[3] < lo:
                continue
            start = row[3] - timedelta(seconds=row[4] or 0)
            if start - slack >= read_from:
                continue
            if row[0] is not None:
                key = (ip, int(row[0]))
                starts[key] = min(start, starts.get(key, start))
            if start < read_from:
                # DNS answers from dns_window before the start to a minute after it (the lookup
                # takes the newest one up to DNS_TOLERANCE after the start).
                spans[ip].add(
                    (start - dns_window, min(start + timedelta(seconds=BUCKET), dns_from))
                )
    web: _Rows = defaultdict(list)
    app: _Rows = defaultdict(list)
    if starts:
        params = {
            "ips": sorted({ip for ip, _ in starts}),
            "sessions": sorted({sessionid for _, sessionid in starts}),
            "lo": min(starts.values()) - slack,
            "hi": read_from,
        }
        for query, found in ((_WEB_BEFORE, web), (_APP_BEFORE, app)):
            for row in session.execute(query, params):
                ip = str(row[0])
                start = starts.get((ip, row[1]))
                if start is not None and row[2] >= start - slack:  # not an earlier session
                    found[ip].append(tuple(row[1:]))
    lookups = [
        (ip, low, high) for ip, ranges in sorted(spans.items()) for low, high in _merged(ranges)
    ]
    dns: _Rows = {}
    if lookups:
        ips, los, his = (list(column) for column in zip(*lookups, strict=True))
        dns = _by_ip(session.execute(_DNS_BEFORE, {"ips": ips, "los": los, "his": his}))
    return web, app, dns


def compute_day(
    session: Session,
    day: date,
    *,
    settings: UsageSettings | None = None,
    namer: DomainNamer | None = None,
) -> DayResult:
    """Read the logs of ``day`` and compute its rollup (nothing is written)."""
    settings = settings or UsageSettings.load(session)
    namer = namer or DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    rules = BackgroundRules.create(
        settings.background_domains, settings.background_appcats, settings.background_ports
    )
    builder = DayBuilder(day, settings, namer, rules)
    lo, hi = builder.lo, builder.hi
    overlap = timedelta(seconds=settings.session_overlap_seconds)
    window = timedelta(seconds=settings.dns_window_seconds)
    ips = source_ips(session, lo, hi, hi + overlap)
    timelines = build_timelines(session, ips, lo, hi)
    for group in _chunks(ips, IP_GROUP):
        bounds = {"ips": group, "lo": lo - overlap, "hi": hi + overlap}
        traffic = _by_ip(session.execute(_TRAFFIC, {**bounds, "refused": list(REFUSED_ACTIONS)}))
        web = _by_ip(session.execute(_WEB, bounds))
        app = _by_ip(session.execute(_APP, bounds))
        dns = _by_ip(session.execute(_DNS, {**bounds, "lo": lo - overlap - window}))
        web_before, app_before, dns_before = _names_before(
            session, traffic, lo, lo - overlap, window
        )
        for ip in group:
            builder.counts["traffic_rows"] += len(traffic.get(ip, ()))
            builder.add_ip(
                ip,
                timelines[ip],
                traffic.get(ip, ()),
                [*web_before.get(ip, ()), *web.get(ip, ())],
                [*app_before.get(ip, ()), *app.get(ip, ())],
                [*dns_before.get(ip, ()), *dns.get(ip, ())],
            )
    result = DayResult(
        day=day,
        lo=lo,
        hi=hi,
        ips=ips,
        attribution_hash=timelines_hash(timelines),
        computed_until=None,
    )
    builder.finish(result)
    result.stats["psl"] = namer.source
    if namer.warning:
        result.stats["psl_warning"] = namer.warning
    return result
