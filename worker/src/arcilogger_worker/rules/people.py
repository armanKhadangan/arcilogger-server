"""The people and usage rules (Wave 6 contract §2.2.4, package W6-E): R09 (repeated blocked
categories), R13 (large upload) and R14 (night activity). All three are ``person_level``.

**The person** of a row is the owner of its source address at the row's **device** time, asked
of the usage reports' subject timelines (:func:`arcilogger_worker.usage.subjects.build_timelines`,
which ask the lookup engine's :class:`~arcilogger_worker.lookup.attribution.Attributor`): no
attribution code of its own (§5). As in the usage reports, a minute belongs to the subject of
its first instant. The key is ``person:<id>``, or ``ip:<srcip>`` when nobody is attributed (no
lease, or a device nobody is assigned to). ``Hit.person_id`` carries the person; the values, the
title and the evidence name the address only, never a person (KVKK, §0.1 rule 5).

**Bytes** (R13 sent, R14 sent + received) are counted per session as the usage rollups count
them (:mod:`arcilogger_worker.usage.engine`): forward sessions only, refused connections
(:data:`~arcilogger_worker.usage.engine.REFUSED_ACTIONS`) are none; an interim log adds its
``sentdelta``/``rcvddelta``, any other log what its cumulative counters exceed the session's
previous log ("final remainder"), so interim and final logs are never counted twice. A log's
increment counts at the log's receive time (D8); the session's earlier logs are read back
``usage.session_overlap_seconds`` before the window to find the previous counters. Unlike the
rollups the increment is not spread over its interval: a rule asks "how much was sent in this
hour", and a final log that closes a long session counts at its time.

Every query bounds the device time to the pruning bounds and the receive time to the window,
reads only addresses with a matching row in the slice, and binds every parameter.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from ipaddress import ip_network
from typing import Any, Final, Literal

from sqlalchemy import (
    SQLColumnExpression,
    Text,
    and_,
    any_,
    case,
    cast,
    false,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import Label

from arcilogger_db.models import FgDns, FgTraffic, FgWeb
from arcilogger_db.settings import INTEGER_RANGES, get_setting
from arcilogger_worker.rules.model import (
    EvidenceRef,
    Hit,
    Network,
    Param,
    RuleContext,
    RuleDef,
    RuleSettings,
)
from arcilogger_worker.usage.engine import REFUSED_ACTIONS
from arcilogger_worker.usage.subjects import IpTimeline, build_timelines

_Column = SQLColumnExpression[Any] | InstrumentedAttribute[Any]

#: Evidence rows a hit carries at most: the largest ``rules.evidence_max`` (the engine keeps the
#: configured number of them, oldest first).
EVIDENCE_LIMIT: Final = INTEGER_RANGES["rules.evidence_max"][1] or 100
MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB
#: Default of ``usage.session_overlap_seconds`` (how far back a session's earlier logs are read).
_OVERLAP_DEFAULT: Final = 1800
_MINUTE: Final = 60


# =================================================================================================
# Query helpers
# =================================================================================================


def _texts(values: Iterable[object]) -> SQLColumnExpression[Any]:
    """A bound ``text[]`` (never a literal in the SQL string)."""
    return cast(literal(sorted({str(value) for value in values}), ARRAY(Text)), ARRAY(Text))


def _in(column: _Column, values: Sequence[object]) -> SQLColumnExpression[bool]:
    if not values:
        return false()
    return column == any_(_texts(values))


def _within(column: _Column, networks: Sequence[Network]) -> SQLColumnExpression[bool]:
    """``column <<= ANY(:networks::cidr[])``; no networks match nothing."""
    if not networks:
        return false()
    nets = cast(literal(sorted(str(net) for net in networks), ARRAY(CIDR)), ARRAY(CIDR))
    return column.op("<<=", is_comparison=True)(any_(nets))


def _strings(ctx: RuleContext, name: str) -> list[str]:
    value = ctx.settings.param(name)
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name}: a list is expected")
    return [str(item) for item in value]


def _networks(settings: RuleSettings, name: str) -> list[Network]:
    value = settings.param(name)
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name}: a list is expected")
    return list(value)


def _int(ctx: RuleContext, name: str) -> int:
    value = ctx.settings.param(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name}: an integer is expected")
    return value


def _minute(column: _Column) -> SQLColumnExpression[datetime]:
    return func.date_trunc("minute", column)


# =================================================================================================
# Buckets → subjects
# =================================================================================================


@dataclass(frozen=True)
class _Bucket:
    """The matching rows of one address in one minute of device time."""

    srcip: str
    minute: datetime
    count: int
    amount: int
    first: datetime
    last: datetime
    in_slice: bool


@dataclass
class _Subject:
    """Everything one key gathered: rows, amount, receive-time span, its newest address."""

    key: str
    person_id: int | None
    count: int = 0
    amount: int = 0
    first: datetime | None = None
    last: datetime | None = None
    in_slice: bool = False
    srcip: str = ""
    ips: set[str] = field(default_factory=set)

    def add(self, bucket: _Bucket) -> None:
        self.count += bucket.count
        self.amount += bucket.amount
        self.in_slice = self.in_slice or bucket.in_slice
        self.ips.add(bucket.srcip)
        if self.first is None or bucket.first < self.first:
            self.first = bucket.first
        if self.last is None or bucket.last >= self.last:
            self.last = bucket.last
            self.srcip = bucket.srcip


def _key(timeline: IpTimeline | None, ip: str, at: datetime) -> tuple[str, int | None]:
    """``person:<id>`` of the address at ``at``, else ``ip:<address>``."""
    if timeline is not None:
        subject = timeline.at(at.timestamp())
        if subject.person_id is not None:
            return f"person:{subject.person_id}", subject.person_id
    return f"ip:{ip}", None


def _timelines(session: Session, buckets: Sequence[_Bucket]) -> dict[str, IpTimeline]:
    if not buckets:
        return {}
    low = min(bucket.minute for bucket in buckets)
    high = max(bucket.minute for bucket in buckets) + timedelta(seconds=_MINUTE)
    return build_timelines(session, {bucket.srcip for bucket in buckets}, low, high)


def _subjects(
    buckets: Sequence[_Bucket], timelines: Mapping[str, IpTimeline]
) -> dict[str, _Subject]:
    """The buckets added up per key (a minute is its first instant's subject's)."""
    subjects: dict[str, _Subject] = {}
    for bucket in buckets:
        key, person_id = _key(timelines.get(bucket.srcip), bucket.srcip, bucket.minute)
        subject = subjects.get(key)
        if subject is None:
            subject = subjects[key] = _Subject(key, person_id)
        subject.add(bucket)
    return subjects


def _evidence(
    session: Session,
    model: Any,
    rows: Any,
    keys: Mapping[str, _Subject],
    timelines: Mapping[str, IpTimeline],
) -> dict[str, list[EvidenceRef]]:
    """The oldest :data:`EVIDENCE_LIMIT` rows of every address in ``rows`` (a subquery with the
    row columns, ``srcip`` as text and ``wanted``), given to the key of the address at each row's
    device time, oldest first per key."""
    numbered = (
        select(
            rows,
            func.row_number()
            .over(partition_by=rows.c.srcip, order_by=(rows.c.time_received, rows.c.id))
            .label("rn"),
        )
        .where(rows.c.wanted)
        .subquery("numbered")
    )
    found: dict[str, list[EvidenceRef]] = defaultdict(list)
    query = (
        select(numbered)
        .where(numbered.c.rn <= EVIDENCE_LIMIT)
        .order_by(numbered.c.time_received, numbered.c.id)
    )
    table = model.__tablename__
    for row in session.execute(query):
        key, _ = _key(timelines.get(row.srcip), row.srcip, row.time)
        if key in keys and len(found[key]) < EVIDENCE_LIMIT:
            found[key].append(
                EvidenceRef(
                    table=table,
                    row_id=row.id,
                    time=row.time,
                    time_received=row.time_received,
                    source=row.source,
                    raw_day=row.raw_day,
                    raw_line=row.raw_line,
                    raw_offset=row.raw_offset,
                )
            )
    return found


def _merge_evidence(*parts: Mapping[str, list[EvidenceRef]]) -> dict[str, list[EvidenceRef]]:
    merged: dict[str, list[EvidenceRef]] = defaultdict(list)
    for part in parts:
        for key, refs in part.items():
            merged[key].extend(refs)
    for refs in merged.values():
        refs.sort(key=lambda ref: (ref.time_received, ref.table, ref.row_id))
        del refs[EVIDENCE_LIMIT:]
    return merged


def _row_columns(model: Any) -> tuple[SQLColumnExpression[Any], ...]:
    return (
        model.id.label("id"),
        model.time.label("time"),
        model.time_received.label("time_received"),
        model.source.label("source"),
        model.raw_day.label("raw_day"),
        model.raw_line.label("raw_line"),
        model.raw_offset.label("raw_offset"),
        func.host(model.srcip).label("srcip"),
    )


# =================================================================================================
# R09 — Engelli kategori ısrarı
# =================================================================================================


def _blocked_rows(ctx: RuleContext, model: Any) -> Any:
    """The blocked rows of ``model`` in the window, of the addresses with one in the slice."""
    match = and_(
        _in(model.action, _strings(ctx, "blocked_actions")),
        model.srcip.is_not(None),
        model.time >= ctx.time_low,
        model.time <= ctx.time_high,
    )
    in_slice = (
        select(model.srcip)
        .where(match, model.time_received > ctx.start, model.time_received <= ctx.end)
        .distinct()
        .scalar_subquery()
    )
    return (
        select(*_row_columns(model), literal(True).label("wanted"), literal(1).label("amount"))
        .where(
            match,
            model.time_received > ctx.window_start,
            model.time_received <= ctx.end,
            model.srcip.in_(in_slice),
        )
        .subquery(model.__tablename__)
    )


def _buckets(session: Session, ctx: RuleContext, rows: Any) -> list[_Bucket]:
    minute: Label[datetime] = _minute(rows.c.time).label("minute")
    query = (
        select(
            rows.c.srcip,
            minute,
            func.count().label("rows"),
            func.coalesce(func.sum(rows.c.amount), 0).label("amount"),
            func.min(rows.c.time_received).label("first"),
            func.max(rows.c.time_received).label("last"),
        )
        .where(rows.c.wanted)
        .group_by(rows.c.srcip, minute)
    )
    return [
        _Bucket(
            row.srcip,
            row.minute,
            int(row.rows),
            int(row.amount),
            row.first,
            row.last,
            row.last > ctx.start,
        )
        for row in session.execute(query)
    ]


def evaluate_r09(session: Session, ctx: RuleContext) -> list[Hit]:
    """Blocked web and DNS requests (``action`` in ``blocked_actions``) of one person — or one
    address nobody is attributed to — reaching ``threshold`` within the window."""
    sources = [(model, _blocked_rows(ctx, model)) for model in (FgWeb, FgDns)]
    buckets = [bucket for _, rows in sources for bucket in _buckets(session, ctx, rows)]
    timelines = _timelines(session, buckets)
    threshold = _int(ctx, "threshold")
    subjects = {
        key: subject
        for key, subject in _subjects(buckets, timelines).items()
        if subject.in_slice and subject.count >= threshold
    }
    evidence = _merge_evidence(
        *(_evidence(session, model, rows, subjects, timelines) for model, rows in sources)
    )
    return [
        _hit(
            subject,
            {
                "srcip": subject.srcip,
                "count": subject.count,
                "window_seconds": ctx.settings.window_seconds,
            },
            evidence.get(key, []),
        )
        for key, subject in subjects.items()
    ]


# =================================================================================================
# Byte counting of R13 and R14
# =================================================================================================


def _overlap(session: Session) -> timedelta:
    value = get_setting(session, "usage.session_overlap_seconds", _OVERLAP_DEFAULT)
    return timedelta(seconds=value if isinstance(value, int) else _OVERLAP_DEFAULT)


def _traffic_match(
    networks: Sequence[Network], low: datetime, high: datetime
) -> SQLColumnExpression[bool]:
    """Forward traffic of the given networks, refused connections left out, device time in
    ``[low, high]``."""
    return and_(
        FgTraffic.subtype == "forward",
        or_(FgTraffic.action.is_(None), ~(FgTraffic.action == any_(_texts(REFUSED_ACTIONS)))),
        FgTraffic.srcip.is_not(None),
        _within(FgTraffic.srcip, networks),
        FgTraffic.time >= low,
        FgTraffic.time <= high,
    )


def _traffic_rows(
    session: Session,
    ctx: RuleContext,
    since: datetime,
    *,
    sent_only: bool,
    networks: Sequence[Network],
    count_from: datetime | None = None,
    scope: Literal["slice", "all"] | Sequence[str] = "slice",
    period: bool = False,
) -> Any:
    """Forward traffic of the given networks, with each log's byte increment (``amount``,
    usage accounting); ``wanted``: received in ``(since, end]`` with an increment, and at or
    after ``count_from`` (site time windows). ``scope``: the addresses with a log in the slice,
    all of them (a period's totals rebuilt), or the given ones (a period's evidence).
    ``period``: the device-time bounds of a period's totals (:func:`_period_bounds`)."""
    lookback = since - _overlap(session)
    low = (
        lookback - ctx.max_device_skew
        if period
        else min(ctx.time_low, lookback - ctx.max_device_skew)
    )
    match = _traffic_match(networks, low, ctx.time_high)
    if scope == "slice":
        in_slice: Any = (
            select(FgTraffic.srcip)
            .where(match, FgTraffic.time_received > ctx.start, FgTraffic.time_received <= ctx.end)
            .distinct()
            .scalar_subquery()
        )
        by_address: SQLColumnExpression[bool] = FgTraffic.srcip.in_(in_slice)
    elif scope == "all":
        by_address = literal(True)
    else:
        by_address = func.host(FgTraffic.srcip) == any_(_texts(scope))
    session_key = (FgTraffic.srcip, FgTraffic.sessionid, FgTraffic.dstip, FgTraffic.dstport)
    order = (FgTraffic.time, FgTraffic.id)

    def increment(delta: _Column, total: _Column) -> SQLColumnExpression[Any]:
        previous = func.lag(total).over(partition_by=session_key, order_by=order)
        return case(
            (
                and_(FgTraffic.is_interim, delta.is_not(None)),
                func.greatest(delta, 0),
            ),
            else_=func.greatest(func.coalesce(total, 0) - func.coalesce(previous, 0), 0),
        )

    amount = increment(FgTraffic.sentdelta, FgTraffic.sentbyte)
    if not sent_only:
        amount = amount + increment(FgTraffic.rcvddelta, FgTraffic.rcvdbyte)
    rows = (
        select(*_row_columns(FgTraffic), amount.label("amount"))
        .where(
            match,
            FgTraffic.time_received > lookback,
            FgTraffic.time_received <= ctx.end,
            by_address,
        )
        .subquery("increments")
    )
    wanted = and_(rows.c.time_received > since, rows.c.amount > 0)
    if count_from is not None:
        wanted = and_(wanted, rows.c.time_received >= count_from)
    return select(rows, wanted.label("wanted")).subquery("traffic")


# =================================================================================================
# Period totals (R13's site day, R14's nights; #280)
# =================================================================================================
#
# A period's totals per address and minute of device time are kept from one slice to the next,
# so a run reads its slice's rows and the other logs of the sessions they belong to, not the
# whole period. A log's increment depends on the session's previous log in device time, so a
# late log can change the increment of a log counted before: each touched session's buckets are
# computed on its logs as of the slice start and as of the slice end, and the difference is
# added. The totals equal one read of the period (the rebuild) at every slice end.


@dataclass(frozen=True)
class _Period:
    """What a period's totals depend on (any change rebuilds them)."""

    rule: str
    since: datetime
    count_from: datetime | None
    sent_only: bool
    networks: tuple[str, ...]
    overlap: timedelta
    skew: timedelta


@dataclass
class _Totals:
    """A period's totals as of ``filled_until`` (the end of the last slice counted): address →
    minute of device time → [rows, bytes, first received, last received]."""

    filled_until: datetime
    buckets: dict[str, dict[datetime, list[Any]]] = field(default_factory=dict)


#: The live runs' totals, by database and period (the worker's process; rebuilt after a
#: restart). A dry run keeps its own in its read-only session (``session.info``).
_LIVE: dict[tuple[str, _Period], _Totals] = {}
#: Periods older than this before the newest one of their rule are dropped.
_KEEP: Final = timedelta(days=2)


def clear_period_totals() -> None:
    """Forget the live totals (tests; the next run rebuilds them)."""
    _LIVE.clear()


def _store(session: Session) -> tuple[dict[Any, _Totals], Any]:
    """Where this session's totals live, and their key prefix."""
    if session.execute(text("SHOW transaction_read_only")).scalar_one() == "on":
        return session.info.setdefault("rules.period_totals", {}), None
    return _LIVE, str(session.get_bind().engine.url)


def _period_buckets(session: Session, ctx: RuleContext, period: _Period) -> list[_Bucket]:
    """The period's buckets of the addresses with a log in the slice, as of the slice end."""
    store, prefix = _store(session)
    totals = store.get((prefix, period))
    addresses = _slice_addresses(session, ctx, period)
    if totals is None or totals.filled_until != ctx.start:
        totals = _rebuild(session, ctx, period)
    else:
        _advance(session, ctx, period, totals)
    totals.filled_until = ctx.end
    store[(prefix, period)] = totals
    for key in [
        key
        for key in store
        if key[0] == prefix and key[1].rule == period.rule and key[1].since < period.since - _KEEP
    ]:
        del store[key]
    return [
        _Bucket(ip, minute, cell[0], cell[1], cell[2], cell[3], cell[3] > ctx.start)
        for ip in sorted(addresses)
        for minute, cell in sorted(totals.buckets.get(ip, {}).items())
    ]


def _period_bounds(ctx: RuleContext, period: _Period) -> tuple[datetime, datetime, datetime]:
    """The lookback (sessions' earlier logs) and the device-time bounds of the period's rows."""
    lookback = period.since - period.overlap
    return lookback, lookback - period.skew, ctx.end + period.skew


def _slice_addresses(session: Session, ctx: RuleContext, period: _Period) -> set[str]:
    _, low, high = _period_bounds(ctx, period)
    query = (
        select(func.host(FgTraffic.srcip))
        .where(
            _traffic_match(_period_networks(period), low, high),
            FgTraffic.time_received > ctx.start,
            FgTraffic.time_received <= ctx.end,
        )
        .distinct()
    )
    return {str(ip) for ip in session.scalars(query)}


def _period_networks(period: _Period) -> list[Network]:
    return [ip_network(net) for net in period.networks]


def _rebuild(session: Session, ctx: RuleContext, period: _Period) -> _Totals:
    """The period's totals from one read of it (every address)."""
    rows = _traffic_rows(
        session,
        ctx,
        period.since,
        sent_only=period.sent_only,
        networks=_period_networks(period),
        count_from=period.count_from,
        scope="all",
        period=True,
    )
    totals = _Totals(filled_until=ctx.end)
    for bucket in _buckets(session, ctx, rows):
        cells = totals.buckets.setdefault(bucket.srcip, {})
        cells[bucket.minute] = [bucket.count, bucket.amount, bucket.first, bucket.last]
    return totals


_SESSION_COLUMNS: Final = (
    FgTraffic.id,
    func.host(FgTraffic.srcip).label("srcip"),
    FgTraffic.sessionid,
    FgTraffic.dstip,
    FgTraffic.dstport,
    FgTraffic.time,
    FgTraffic.time_received,
    FgTraffic.is_interim,
    FgTraffic.sentbyte,
    FgTraffic.rcvdbyte,
    FgTraffic.sentdelta,
    FgTraffic.rcvddelta,
)


def _advance(session: Session, ctx: RuleContext, period: _Period, totals: _Totals) -> None:
    """Add what the slice changes: the sessions with a log received in it, or with an earlier
    log that the slice's later device-time bound now reaches, counted again."""
    lookback, low, high = _period_bounds(ctx, period)
    match = _traffic_match(_period_networks(period), low, high)
    reached = and_(
        FgTraffic.time_received > lookback,
        FgTraffic.time_received <= ctx.start,
        FgTraffic.time > ctx.start + period.skew,
    )
    touched = session.execute(
        select(func.host(FgTraffic.srcip), FgTraffic.sessionid, FgTraffic.dstip, FgTraffic.dstport)
        .where(
            match,
            or_(
                and_(FgTraffic.time_received > ctx.start, FgTraffic.time_received <= ctx.end),
                reached,
            ),
        )
        .distinct()
    ).all()
    if not touched:
        return
    keys = {(str(ip), sessionid, dstip, dstport) for ip, sessionid, dstip, dstport in touched}
    ids = sorted({key[1] for key in keys if key[1] is not None})
    ips = sorted({key[0] for key in keys})
    by_session = or_(
        FgTraffic.sessionid == any_(cast(literal(ids), ARRAY(FgTraffic.sessionid.type))),
        FgTraffic.sessionid.is_(None),
    )
    rows = [
        row
        for row in session.execute(
            select(*_SESSION_COLUMNS).where(
                match,
                FgTraffic.time_received > lookback,
                FgTraffic.time_received <= ctx.end,
                func.host(FgTraffic.srcip) == any_(_texts(ips)),
                by_session,
            )
        )
        if (row.srcip, row.sessionid, row.dstip, row.dstport) in keys
    ]
    before_received, before_time = ctx.start, ctx.start + period.skew
    old = _increments(
        [r for r in rows if r.time_received <= before_received and r.time <= before_time], period
    )
    new = _increments(rows, period)
    for ip in old.keys() | new.keys():
        cells = totals.buckets.setdefault(ip, {})
        for minute in old.get(ip, {}).keys() | new.get(ip, {}).keys():
            was = old.get(ip, {}).get(minute, [0, 0, None, None])
            now = new.get(ip, {}).get(minute, [0, 0, None, None])
            cell = cells.get(minute)
            if cell is None:
                cell = cells[minute] = [0, 0, now[2], now[3]]
            cell[0] += now[0] - was[0]
            cell[1] += now[1] - was[1]
            if now[2] is not None and (cell[2] is None or now[2] < cell[2]):
                cell[2] = now[2]
            if now[3] is not None and (cell[3] is None or now[3] > cell[3]):
                cell[3] = now[3]
            if cell[0] <= 0 and cell[1] <= 0:
                del cells[minute]
        if not cells:
            del totals.buckets[ip]


def _increments(rows: Sequence[Any], period: _Period) -> dict[str, dict[datetime, list[Any]]]:
    """The buckets of the given sessions' logs, as the SQL of :func:`_traffic_rows` counts them:
    per session (address, session id, destination, port) in device-time order, an interim log
    its delta, any other log its counters less the previous log's."""
    sessions: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        sessions[(row.srcip, row.sessionid, row.dstip, row.dstport)].append(row)
    buckets: dict[str, dict[datetime, list[Any]]] = defaultdict(dict)
    for logs in sessions.values():
        logs.sort(key=lambda row: (row.time, row.id))
        sent = rcvd = None
        for row in logs:
            amount = _increment(row.is_interim, row.sentdelta, row.sentbyte, sent)
            if not period.sent_only:
                amount += _increment(row.is_interim, row.rcvddelta, row.rcvdbyte, rcvd)
            sent, rcvd = row.sentbyte, row.rcvdbyte
            if (
                row.time_received <= period.since
                or amount <= 0
                or (period.count_from is not None and row.time_received < period.count_from)
            ):
                continue
            minute = row.time.replace(second=0, microsecond=0)
            cell = buckets[row.srcip].get(minute)
            if cell is None:
                buckets[row.srcip][minute] = [1, amount, row.time_received, row.time_received]
                continue
            cell[0] += 1
            cell[1] += amount
            cell[2] = min(cell[2], row.time_received)
            cell[3] = max(cell[3], row.time_received)
    return buckets


def _increment(
    interim: bool | None, delta: int | None, total: int | None, previous: int | None
) -> int:
    if interim and delta is not None:
        return max(delta, 0)
    return max((total or 0) - (previous or 0), 0)


def _hit(subject: _Subject, values: Mapping[str, object], evidence: Sequence[EvidenceRef]) -> Hit:
    if subject.last is None:  # pragma: no cover - a subject always has a bucket
        raise ValueError(f"empty subject {subject.key}")
    return Hit(
        key=subject.key,
        time=subject.last,
        count=subject.count,
        values=dict(values),
        evidence=tuple(evidence),
        person_id=subject.person_id,
    )


def _mib(amount: int) -> float:
    return round(amount / MIB, 1)


# =================================================================================================
# R13 — Büyük veri gönderimi
# =================================================================================================


def _period(
    session: Session,
    ctx: RuleContext,
    rule: str,
    since: datetime,
    count_from: datetime | None,
    sent_only: bool,
    networks: Sequence[Network],
) -> _Period:
    return _Period(
        rule,
        since,
        count_from,
        sent_only,
        tuple(sorted(str(net) for net in networks)),
        _overlap(session),
        ctx.max_device_skew,
    )


def _site_day_start(ctx: RuleContext) -> datetime:
    local = ctx.end.astimezone(ctx.zone)
    return datetime.combine(local.date(), time(0), tzinfo=ctx.zone)


def evaluate_r13(session: Session, ctx: RuleContext) -> list[Hit]:
    """Bytes sent by one person (else one internal address) above ``hour_bytes`` within the
    window, or above ``day_bytes`` since the start of the site day."""
    networks = list(ctx.internal_networks)
    hour_rows = _traffic_rows(session, ctx, ctx.window_start, sent_only=True, networks=networks)
    day_start = _site_day_start(ctx)
    hour_buckets = _buckets(session, ctx, hour_rows)
    day_buckets = _period_buckets(
        session, ctx, _period(session, ctx, "R13", day_start, None, True, networks)
    )
    timelines = _timelines(session, [*hour_buckets, *day_buckets])
    hour = _subjects(hour_buckets, timelines)
    day = _subjects(day_buckets, timelines)
    hour_limit, day_limit = _int(ctx, "hour_bytes"), _int(ctx, "day_bytes")
    hits: list[Hit] = []
    picked: dict[str, tuple[_Subject, str]] = {}
    for key, subject in hour.items():
        if subject.in_slice and subject.amount > hour_limit:
            picked[key] = (subject, "hour")
    for key, subject in day.items():
        if key not in picked and subject.in_slice and subject.amount > day_limit:
            picked[key] = (subject, "day")
    if not picked:
        return hits
    evidence = _evidence(
        session,
        FgTraffic,
        hour_rows,
        {key: subject for key, (subject, period) in picked.items() if period == "hour"},
        timelines,
    )
    by_day = {key: subject for key, (subject, period) in picked.items() if period == "day"}
    if by_day:
        # Only now the day's logs, of the addresses picked (their evidence).
        day_rows = _traffic_rows(
            session,
            ctx,
            day_start,
            sent_only=True,
            networks=networks,
            scope=sorted({ip for subject in by_day.values() for ip in subject.ips}),
            period=True,
        )
        evidence.update(_evidence(session, FgTraffic, day_rows, by_day, timelines))
    for key, (subject, period) in picked.items():
        values = {
            "srcip": subject.srcip,
            "bytes": subject.amount,
            "mib": _mib(subject.amount),
            "period": period,
            "hour_bytes": hour[key].amount if key in hour else 0,
            "day_bytes": day[key].amount if key in day else 0,
        }
        hits.append(_hit(subject, values, evidence.get(key, [])))
    return hits


# =================================================================================================
# R14 — Gece etkinliği
# =================================================================================================


def _nights(ctx: RuleContext) -> list[tuple[datetime, datetime]]:
    """The nights (``[night_start, night_end)`` in site time, across midnight when the end is
    not after the start) that overlap the slice."""
    begin = timedelta(seconds=_int(ctx, "night_start"))
    end = timedelta(seconds=_int(ctx, "night_end"))
    length = end - begin if end > begin else end + timedelta(days=1) - begin
    first = (ctx.start.astimezone(ctx.zone) - timedelta(days=1)).date()
    nights = []
    for offset in range(3):
        midnight = datetime.combine(first + timedelta(days=offset), time(0), tzinfo=ctx.zone)
        low = midnight + begin
        high = low + length
        if low < ctx.end and high > ctx.start:
            nights.append((low, high))
    return nights


def _needs_networks(settings: RuleSettings) -> bool:
    return not _networks(settings, "user_networks")


def evaluate_r14(session: Session, ctx: RuleContext) -> list[Hit]:
    """Traffic (sent and received) of the ``user_networks`` within one night, per person (else
    address), from ``min_bytes`` on. The night is counted from its start to the slice end."""
    networks = _networks(ctx.settings, "user_networks")
    if not networks:
        return []
    minimum = _int(ctx, "min_bytes")
    hits: list[Hit] = []
    for low, high in _nights(ctx):
        period = _period(session, ctx, "R14", low, low, False, networks)
        buckets = [
            bucket for bucket in _period_buckets(session, ctx, period) if bucket.first < high
        ]
        timelines = _timelines(session, buckets)
        subjects = {
            key: subject
            for key, subject in _subjects(buckets, timelines).items()
            if subject.in_slice and subject.amount >= minimum
        }
        if not subjects:
            continue
        rows = _traffic_rows(
            session,
            ctx,
            low,
            sent_only=False,
            networks=networks,
            count_from=low,
            scope=sorted({ip for subject in subjects.values() for ip in subject.ips}),
            period=True,
        )
        evidence = _evidence(session, FgTraffic, rows, subjects, timelines)
        night = low.astimezone(ctx.zone).date().isoformat()
        for key, subject in subjects.items():
            values = {
                "srcip": subject.srcip,
                "bytes": subject.amount,
                "mib": _mib(subject.amount),
                "night": night,
            }
            hits.append(_hit(subject, values, evidence.get(key, [])))
    return hits


# =================================================================================================
# Registry entries
# =================================================================================================

RULES: Final[tuple[RuleDef, ...]] = (
    RuleDef(
        id="R09",
        kind="event",
        severity="low",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "blocked_actions",
                "strings",
                ("blocked",),
                min=1,
                max=200,
                label_tr="Engellendi sayılan eylemler",
                label_en="Actions counted as blocked",
            ),
            Param(
                "threshold",
                "int",
                20,
                min=1,
                max=1_000_000,
                label_tr="Pencere içinde engellenen istek sayısı",
                label_en="Blocked requests within the window",
            ),
        ),
        inputs=("fg_web", "fg_dns"),
        evaluate=evaluate_r09,
        person_level=True,
    ),
    RuleDef(
        id="R13",
        kind="event",
        severity="medium",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "hour_bytes",
                "bytes",
                GIB,
                min=MIB,
                max=1024 * GIB,
                label_tr="Pencere içinde gönderilen veri sınırı",
                label_en="Data sent within the window, limit",
            ),
            Param(
                "day_bytes",
                "bytes",
                5 * GIB,
                min=MIB,
                max=10 * 1024 * GIB,
                label_tr="Bir günde (yerel saat) gönderilen veri sınırı",
                label_en="Data sent in one site day, limit",
            ),
        ),
        inputs=("fg_traffic",),
        evaluate=evaluate_r13,
        person_level=True,
    ),
    RuleDef(
        id="R14",
        kind="event",
        severity="low",
        window_seconds=21_600,
        cooldown_seconds=21_600,
        params=(
            Param(
                "user_networks",
                "networks",
                (),
                min=0,
                max=200,
                label_tr="Kullanıcı ağları (boş = kural kapalı)",
                label_en="User networks (empty = rule off)",
            ),
            Param(
                "night_start",
                "seconds",
                0,
                min=0,
                max=86_399,
                label_tr="Gece başlangıcı (yerel saat, gece yarısından saniye)",
                label_en="Night start (site time, seconds after midnight)",
            ),
            Param(
                "night_end",
                "seconds",
                6 * 3600,
                min=0,
                max=86_399,
                label_tr="Gece bitişi (yerel saat, gece yarısından saniye)",
                label_en="Night end (site time, seconds after midnight)",
            ),
            Param(
                "min_bytes",
                "bytes",
                10 * MIB,
                min=1,
                max=1024 * GIB,
                label_tr="Bir gecede en az veri",
                label_en="Data in one night, at least",
            ),
        ),
        inputs=("fg_traffic",),
        evaluate=evaluate_r14,
        person_level=True,
        needs_setup=_needs_networks,
    ),
)
