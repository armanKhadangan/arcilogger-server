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
from typing import Any, Final

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


def _traffic_rows(
    session: Session,
    ctx: RuleContext,
    since: datetime,
    *,
    sent_only: bool,
    networks: Sequence[Network],
    count_from: datetime | None = None,
) -> Any:
    """Forward traffic of the given networks, of the addresses with a log in the slice, with
    each log's byte increment (``amount``, usage accounting); ``wanted``: received in
    ``(since, end]`` with an increment, and at or after ``count_from`` (site time windows)."""
    lookback = since - _overlap(session)
    match = and_(
        FgTraffic.subtype == "forward",
        or_(FgTraffic.action.is_(None), ~(FgTraffic.action == any_(_texts(REFUSED_ACTIONS)))),
        FgTraffic.srcip.is_not(None),
        _within(FgTraffic.srcip, networks),
        FgTraffic.time >= min(ctx.time_low, lookback - ctx.max_device_skew),
        FgTraffic.time <= ctx.time_high,
    )
    in_slice = (
        select(FgTraffic.srcip)
        .where(match, FgTraffic.time_received > ctx.start, FgTraffic.time_received <= ctx.end)
        .distinct()
        .scalar_subquery()
    )
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
            FgTraffic.srcip.in_(in_slice),
        )
        .subquery("increments")
    )
    wanted = and_(rows.c.time_received > since, rows.c.amount > 0)
    if count_from is not None:
        wanted = and_(wanted, rows.c.time_received >= count_from)
    return select(rows, wanted.label("wanted")).subquery("traffic")


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


def _site_day_start(ctx: RuleContext) -> datetime:
    local = ctx.end.astimezone(ctx.zone)
    return datetime.combine(local.date(), time(0), tzinfo=ctx.zone)


def evaluate_r13(session: Session, ctx: RuleContext) -> list[Hit]:
    """Bytes sent by one person (else one internal address) above ``hour_bytes`` within the
    window, or above ``day_bytes`` since the start of the site day."""
    networks = list(ctx.internal_networks)
    hour_rows = _traffic_rows(session, ctx, ctx.window_start, sent_only=True, networks=networks)
    day_start = _site_day_start(ctx)
    day_rows = _traffic_rows(session, ctx, day_start, sent_only=True, networks=networks)
    hour_buckets = _buckets(session, ctx, hour_rows)
    day_buckets = _buckets(session, ctx, day_rows)
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
    evidence.update(
        _evidence(
            session,
            FgTraffic,
            day_rows,
            {key: subject for key, (subject, period) in picked.items() if period == "day"},
            timelines,
        )
    )
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
        rows = _traffic_rows(
            session,
            ctx,
            low,
            sent_only=False,
            networks=networks,
            count_from=low,
        )
        buckets = [bucket for bucket in _buckets(session, ctx, rows) if bucket.first < high]
        timelines = _timelines(session, buckets)
        subjects = {
            key: subject
            for key, subject in _subjects(buckets, timelines).items()
            if subject.in_slice and subject.amount >= minimum
        }
        if not subjects:
            continue
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
