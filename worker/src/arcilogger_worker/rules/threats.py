"""Threat and health rules (W6-D, contract §2.2.3): R05, R06, R07, R17, R18.

- **R05 IPS saldırısı:** ``fg_ips`` rows of a high severity open an alert each; rows of a medium
  severity open one when ``threshold_medium`` of them arrive within the window. Key
  ``<srcip>:<attackid>``.
- **R06 Botnet / tehdit IP:** ``fg_ips`` rows of a botnet event type (V7), and optionally
  ``fg_traffic`` rows of the site's threat policies. Key ``srcip``.
- **R07 Zararlı yazılım:** every ``fg_virus`` row except the ignored actions. Key
  ``<srcip>:<virusid>``. The title and details carry the virus name and the source address
  only; the file name and the URL stay in the evidence (the raw line).
- **R17 Güvenlik duvarı sağlığı** (the only ``condition`` rule, D9): the FortiGate's performance
  statistics (V3) show CPU or memory above the limit in every sample for ``sustain_seconds``,
  or a conserve-mode event arrives (V4). Key ``source``. The engine resolves the alert after
  ``clear_seconds`` without a hit.
- **R18 DHCP havuzu doldu:** MikroTik ``dhcp_pool_empty`` events and FortiGate events whose
  ``logdesc`` contains one of the configured phrases (V5). Key ``<source>:<server or
  interface>``.

Every query bounds the device time to the pruning bounds and the receive time to the window
(D8), looks only at keys with a row in the slice, and passes every parameter as a bound
parameter: lists as arrays, phrases matched with ``strpos`` (a ``%`` or ``_`` in a phrase is a
plain character, never a pattern). Nothing here writes a table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Numeric,
    SQLColumnExpression,
    Text,
    and_,
    any_,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    not_,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import InstrumentedAttribute, Session

from arcilogger_db.models import FgEvent, FgIps, FgTraffic, FgVirus, MtEvent
from arcilogger_worker.rules.model import EvidenceRef, Hit, Param, RuleContext, RuleDef

#: A JSON text value that is a plain decimal number (``extra`` values are strings; anything
#: else is not a sample and must not abort the slice with a cast error).
_NUMBER: Final = r"^\s*[0-9]+(\.[0-9]+)?\s*$"
#: A column of a model or an SQL expression.
_Column = SQLColumnExpression[Any] | InstrumentedAttribute[Any]
#: The FortiGate's performance-statistics event (V3, sahada doğrula).
PERF_LOGIDS: Final = ("0100040704",)


# =================================================================================================
# Query helpers
# =================================================================================================


def _texts(values: Iterable[object]) -> ColumnElement[Any]:
    """A bound ``text[]`` (never a literal in the SQL string)."""
    return cast(literal(sorted({str(value) for value in values}), ARRAY(Text)), ARRAY(Text))


def _in(column: _Column, values: Sequence[object]) -> ColumnElement[bool]:
    """``column = ANY(:values)``; an empty list matches nothing."""
    if not values:
        return false()
    return column == any_(_texts(values))


def _not_in(column: _Column, values: Sequence[object]) -> ColumnElement[bool]:
    """``column`` NULL or not in ``values``; an empty list excludes nothing."""
    if not values:
        return literal(True, Boolean)
    return or_(column.is_(None), not_(column == any_(_texts(values))))


def _contains_any(column: _Column, phrases: Sequence[object]) -> ColumnElement[bool]:
    """``column`` contains one of ``phrases`` (case-insensitive, no pattern characters)."""
    if not phrases:
        return false()
    words = (
        func.unnest(_texts(str(phrase).lower() for phrase in phrases))
        .table_valued("w")
        .render_derived()
    )
    return exists(
        select(literal(1)).select_from(words).where(func.strpos(func.lower(column), words.c.w) > 0)
    )


def _number(json_text: ColumnElement[Any]) -> ColumnElement[Any]:
    """The JSON text as ``numeric`` when it is a plain number, else NULL."""
    return case((json_text.op("~")(_NUMBER), cast(func.trim(json_text), Numeric)), else_=None)


def _first_field(extra: _Column, names: Sequence[str]) -> ColumnElement[Any]:
    """The first of the ``extra`` fields ``names`` that is present (names are bound)."""
    return func.coalesce(*(extra.op("->>")(name) for name in names), None)


def _host(column: _Column) -> ColumnElement[str]:
    """An address column as text without the mask; ``-`` when the row has none."""
    return func.coalesce(func.host(column), "-")


def _bounds(model: Any, ctx: RuleContext, since: datetime) -> ColumnElement[bool]:
    """Device time within the pruning bounds, receive time within ``(since, end]`` (D8)."""
    return and_(
        model.time >= ctx.device_start,
        model.time <= ctx.device_end,
        model.time_received > since,
        model.time_received <= ctx.end,
    )


@dataclass
class _Group:
    """The rows of one key: count, receive-time span, evidence (oldest first), newest values."""

    key: str
    count: int = 0
    first: datetime | None = None
    last: datetime | None = None
    evidence: list[tuple[datetime, int, EvidenceRef]] = field(default_factory=list)
    newest: dict[str, Any] = field(default_factory=dict)
    newest_at: datetime | None = None

    def add(self, other: _Group) -> None:
        self.count += other.count
        if other.first is not None and (self.first is None or other.first < self.first):
            self.first = other.first
        if other.last is not None and (self.last is None or other.last > self.last):
            self.last = other.last
        self.evidence.extend(other.evidence)
        if other.newest_at is not None and (
            self.newest_at is None or other.newest_at > self.newest_at
        ):
            self.newest, self.newest_at = other.newest, other.newest_at

    def refs(self, limit: int) -> list[EvidenceRef]:
        self.evidence.sort(key=lambda item: (item[0], item[1]))
        return [ref for _, _, ref in self.evidence[:limit]]


def _groups(
    session: Session,
    ctx: RuleContext,
    model: Any,
    key: _Column,
    match: ColumnElement[bool],
    values: Mapping[str, _Column],
    *,
    since: datetime,
    minimum: int = 1,
) -> dict[str, _Group]:
    """The rows of ``model`` matching ``match`` with a receive time in ``(since, end]``, grouped
    by ``key``, for the keys with at least one such row in the slice and at least ``minimum``
    rows: the count, the first and last receive time, the oldest ``evidence_max`` rows as
    evidence and the ``values`` of the newest row."""
    in_slice = select(key).where(match, _bounds(model, ctx, ctx.start)).distinct().scalar_subquery()
    order = (model.time_received, model.id)
    rows = (
        select(
            key.label("key"),
            model.id.label("row_id"),
            model.time.label("time"),
            model.time_received.label("time_received"),
            model.source.label("source"),
            model.raw_day.label("raw_day"),
            model.raw_line.label("raw_line"),
            model.raw_offset.label("raw_offset"),
            *(column.label(f"v_{name}") for name, column in values.items()),
            func.row_number().over(partition_by=key, order_by=order).label("rn"),
            func.row_number()
            .over(partition_by=key, order_by=[column.desc() for column in order])
            .label("rn_desc"),
            func.count().over(partition_by=key).label("n"),
            func.min(model.time_received).over(partition_by=key).label("first_at"),
            func.max(model.time_received).over(partition_by=key).label("last_at"),
        )
        .where(match, _bounds(model, ctx, since), key.in_(in_slice))
        .subquery("r")
    )
    query = (
        select(rows)
        .where(rows.c.n >= minimum, or_(rows.c.rn <= ctx.evidence_max, rows.c.rn_desc == 1))
        .order_by(rows.c.key, rows.c.rn)
    )
    table = model.__tablename__
    groups: dict[str, _Group] = {}
    for row in session.execute(query).mappings():
        group = groups.get(row["key"])
        if group is None:
            group = groups[row["key"]] = _Group(
                row["key"], count=row["n"], first=row["first_at"], last=row["last_at"]
            )
        if row["rn"] <= ctx.evidence_max:
            ref = EvidenceRef(
                tbl=table,
                row_id=row["row_id"],
                time=row["time"],
                time_received=row["time_received"],
                source=row["source"],
                raw_day=row["raw_day"],
                raw_line=row["raw_line"],
                raw_offset=row["raw_offset"],
            )
            group.evidence.append((row["time_received"], row["row_id"], ref))
        if row["rn_desc"] == 1:
            group.newest = {name: row[f"v_{name}"] for name in values}
            group.newest_at = row["time_received"]
    return groups


def _merge(*parts: Mapping[str, _Group]) -> dict[str, _Group]:
    merged: dict[str, _Group] = {}
    for part in parts:
        for key, group in part.items():
            if key in merged:
                merged[key].add(group)
            else:
                merged[key] = group
    return merged


def _hit(ctx: RuleContext, group: _Group, values: Mapping[str, object]) -> Hit:
    last = group.last or group.newest_at
    if last is None:  # pragma: no cover - a group always has a row
        raise ValueError(f"empty group {group.key}")
    return Hit(
        key=group.key,
        time=last,
        count=group.count,
        values=dict(values),
        evidence=group.refs(ctx.evidence_max),
    )


def _strings(ctx: RuleContext, name: str) -> list[str]:
    value = ctx.settings.params[name]
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name}: a list is expected")
    return [str(item) for item in value]


def _int(ctx: RuleContext, name: str) -> int:
    value = ctx.settings.params[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name}: an integer is expected")
    return value


# =================================================================================================
# R05 — IPS saldırısı
# =================================================================================================


def evaluate_r05(session: Session, ctx: RuleContext) -> list[Hit]:
    """High-severity IPS rows each (rows of the slice); medium ones from ``threshold_medium``
    rows within the window. One hit per ``<srcip>:<attackid>``."""
    key = func.concat(_host(FgIps.srcip), ":", func.coalesce(cast(FgIps.attackid, Text), "-"))
    values = {
        "srcip": _host(FgIps.srcip),
        "attackid": FgIps.attackid,
        "attack": FgIps.attack,
        "severity": FgIps.severity,
    }
    high = _groups(
        session,
        ctx,
        FgIps,
        key,
        _in(FgIps.severity, _strings(ctx, "high_severities")),
        values,
        since=ctx.start,
    )
    medium = _groups(
        session,
        ctx,
        FgIps,
        key,
        _in(FgIps.severity, _strings(ctx, "medium_severities")),
        values,
        since=ctx.window_start,
        minimum=_int(ctx, "threshold_medium"),
    )
    hits = []
    for group in _merge(high, medium).values():
        hits.append(
            _hit(
                ctx,
                group,
                {
                    **group.newest,
                    "count": group.count,
                    "window_seconds": ctx.settings.window_seconds,
                },
            )
        )
    return hits


# =================================================================================================
# R06 — Botnet / tehdit IP
# =================================================================================================


def evaluate_r06(session: Session, ctx: RuleContext) -> list[Hit]:
    """Every botnet IPS row (V7) and every traffic row of a threat policy, per ``srcip``."""
    ips = _groups(
        session,
        ctx,
        FgIps,
        _host(FgIps.srcip),
        _in(FgIps.eventtype, _strings(ctx, "ips_eventtypes")),
        {"dstip": _host(FgIps.dstip), "detail": FgIps.attack},
        since=ctx.start,
    )
    policies = _strings(ctx, "threat_policy_names")
    traffic: dict[str, _Group] = {}
    if policies:
        traffic = _groups(
            session,
            ctx,
            FgTraffic,
            _host(FgTraffic.srcip),
            _in(FgTraffic.policyname, policies),
            {"dstip": _host(FgTraffic.dstip), "detail": FgTraffic.policyname},
            since=ctx.start,
        )
    return [
        _hit(ctx, group, {**group.newest, "srcip": group.key, "count": group.count})
        for group in _merge(ips, traffic).values()
    ]


# =================================================================================================
# R07 — Zararlı yazılım
# =================================================================================================


def evaluate_r07(session: Session, ctx: RuleContext) -> list[Hit]:
    """Every ``fg_virus`` row except the ignored actions, per ``<srcip>:<virusid>``.

    Only the virus name, its id and the source address reach the values (the title and the
    details); ``filename`` and ``url`` are never selected."""
    key = func.concat(_host(FgVirus.srcip), ":", func.coalesce(cast(FgVirus.virusid, Text), "-"))
    groups = _groups(
        session,
        ctx,
        FgVirus,
        key,
        _not_in(FgVirus.action, _strings(ctx, "ignore_actions")),
        {"srcip": _host(FgVirus.srcip), "virus": FgVirus.virus, "virusid": FgVirus.virusid},
        since=ctx.start,
    )
    return [
        _hit(
            ctx,
            group,
            {**group.newest, "virus": group.newest.get("virus") or "-", "count": group.count},
        )
        for group in groups.values()
    ]


# =================================================================================================
# R17 — Güvenlik duvarı sağlığı (condition)
# =================================================================================================


def _r17_samples(session: Session, ctx: RuleContext) -> dict[str, _Group]:
    """Per source with a performance sample in the slice: the newest unbroken run of samples
    whose CPU or memory is above its limit, when that run is still going (the newest sample is
    above a limit) and spans at least ``sustain_seconds``.

    CPU and memory are judged separately: the run of the measure that has been high for longer
    counts ("CPU > cpu_percent **or** memory > mem_percent in every sample")."""
    sustain = _int(ctx, "sustain_seconds")
    since = min(ctx.window_start, ctx.end - timedelta(seconds=2 * sustain)) - (ctx.end - ctx.start)
    match = _in(FgEvent.logid, _strings(ctx, "perf_logids"))
    cpu = _number(_first_field(FgEvent.extra, _strings(ctx, "cpu_field")))
    mem = _number(_first_field(FgEvent.extra, _strings(ctx, "mem_field")))
    cpu_high = cpu > _int(ctx, "cpu_percent")
    mem_high = mem > _int(ctx, "mem_percent")
    in_slice = (
        select(FgEvent.source)
        .where(match, _bounds(FgEvent, ctx, ctx.start), or_(cpu.is_not(None), mem.is_not(None)))
        .distinct()
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            FgEvent.source,
            FgEvent.id,
            FgEvent.time,
            FgEvent.time_received,
            FgEvent.raw_day,
            FgEvent.raw_line,
            FgEvent.raw_offset,
            cpu.label("cpu"),
            mem.label("mem"),
            func.coalesce(cpu_high, False).label("cpu_high"),
            func.coalesce(mem_high, False).label("mem_high"),
        )
        .where(
            match,
            _bounds(FgEvent, ctx, since),
            or_(cpu.is_not(None), mem.is_not(None)),
            FgEvent.source.in_(in_slice),
        )
        .order_by(FgEvent.source, FgEvent.time_received, FgEvent.id)
    ).all()
    by_source: dict[str, list[Any]] = {}
    for row in rows:
        by_source.setdefault(row.source, []).append(row)
    groups: dict[str, _Group] = {}
    for source, samples in by_source.items():
        newest = samples[-1]
        if newest.time_received <= ctx.start:
            continue
        best: list[Any] = []
        for flag in ("cpu_high", "mem_high"):
            run: list[Any] = []
            for sample in reversed(samples):
                if not getattr(sample, flag):
                    break
                run.append(sample)
            run.reverse()
            if run and run[-1] is newest and len(run) > len(best):
                best = run
        if not best or (best[-1].time_received - best[0].time_received).total_seconds() < sustain:
            continue
        group = _Group(
            source, count=len(best), first=best[0].time_received, last=newest.time_received
        )
        for sample in best:
            ref = EvidenceRef(
                tbl="fg_event",
                row_id=sample.id,
                time=sample.time,
                time_received=sample.time_received,
                source=source,
                raw_day=sample.raw_day,
                raw_line=sample.raw_line,
                raw_offset=sample.raw_offset,
            )
            group.evidence.append((sample.time_received, sample.id, ref))
        group.newest = {
            "cpu": _plain(newest.cpu),
            "mem": _plain(newest.mem),
            "conserve": False,
        }
        group.newest_at = newest.time_received
        groups[source] = group
    return groups


def _plain(value: object) -> object:
    """A ``Decimal`` sample as ``int`` when whole, else ``float`` (JSON-friendly)."""
    if value is None:
        return None
    number = float(str(value))
    return int(number) if number.is_integer() else number


def evaluate_r17(session: Session, ctx: RuleContext) -> list[Hit]:
    """Sustained high CPU or memory (V3) or a conserve-mode event (V4), per source."""
    samples = _r17_samples(session, ctx)
    conserve = _groups(
        session,
        ctx,
        FgEvent,
        FgEvent.source,
        _contains_any(FgEvent.logdesc, _strings(ctx, "conserve_phrases")),
        {"logdesc": FgEvent.logdesc},
        since=ctx.start,
    )
    for group in conserve.values():
        group.newest = {"cpu": None, "mem": None, "conserve": True}
    hits = []
    for source, group in _merge(samples, conserve).items():
        values = {
            "source": source,
            "cpu": group.newest.get("cpu"),
            "mem": group.newest.get("mem"),
            "conserve": source in conserve,
            "cpu_percent": _int(ctx, "cpu_percent"),
            "mem_percent": _int(ctx, "mem_percent"),
            "sustain_seconds": _int(ctx, "sustain_seconds"),
        }
        if source in samples and source in conserve:
            values["cpu"] = samples[source].newest.get("cpu")
            values["mem"] = samples[source].newest.get("mem")
        hits.append(_hit(ctx, group, values))
    return hits


# =================================================================================================
# R18 — DHCP havuzu doldu
# =================================================================================================


def evaluate_r18(session: Session, ctx: RuleContext) -> list[Hit]:
    """MikroTik ``dhcp_pool_empty`` events and FortiGate events with a pool-exhaustion phrase
    (V5), per ``<source>:<DHCP server or interface>``."""
    mt_server = func.coalesce(
        MtEvent.extra.op("->>")("server"), MtEvent.interface, literal("-", Text)
    )
    mikrotik = _groups(
        session,
        ctx,
        MtEvent,
        func.concat(MtEvent.source, ":", mt_server),
        MtEvent.event == "dhcp_pool_empty",
        {"source": MtEvent.source, "server": mt_server, "pool": MtEvent.extra.op("->>")("pool")},
        since=ctx.start,
    )
    fg_server = func.coalesce(
        FgEvent.extra.op("->>")("interface"),
        FgEvent.extra.op("->>")("server"),
        literal("-", Text),
    )
    fortigate = _groups(
        session,
        ctx,
        FgEvent,
        func.concat(FgEvent.source, ":", fg_server),
        _contains_any(FgEvent.logdesc, _strings(ctx, "fortigate_phrases")),
        {"source": FgEvent.source, "server": fg_server, "pool": literal(None, Text)},
        since=ctx.start,
    )
    return [
        _hit(ctx, group, {**group.newest, "count": group.count})
        for group in _merge(mikrotik, fortigate).values()
    ]


# =================================================================================================
# Registry entries
# =================================================================================================

#: Longest list a rule parameter of this module accepts (the global limit, §2.1.4).
_LIST_MAX: Final = 200

RULES: Final[tuple[RuleDef, ...]] = (
    RuleDef(
        id="R05",
        kind="event",
        severity="high",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "high_severities",
                "strings",
                ["high", "critical"],
                min=0,
                max=_LIST_MAX,
                label_tr="Her biri uyarı açan IPS önem dereceleri",
                label_en="IPS severities that open an alert each",
            ),
            Param(
                "medium_severities",
                "strings",
                ["medium"],
                min=0,
                max=_LIST_MAX,
                label_tr="Eşikle sayılan IPS önem dereceleri",
                label_en="IPS severities counted against the threshold",
            ),
            Param(
                "threshold_medium",
                "int",
                10,
                min=1,
                max=100_000,
                label_tr="Pencere içinde orta önemli saldırı sayısı",
                label_en="Medium-severity attacks within the window",
            ),
        ),
        inputs=("fg_ips",),
        evaluate=evaluate_r05,
    ),
    RuleDef(
        id="R06",
        kind="event",
        severity="critical",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "ips_eventtypes",
                "strings",
                ["botnet"],
                min=0,
                max=_LIST_MAX,
                label_tr="Botnet sayılan IPS olay türleri (sahada doğrula)",
                label_en="IPS event types counted as botnet (verify on site)",
            ),
            Param(
                "threat_policy_names",
                "strings",
                [],
                min=0,
                max=200,
                label_tr="Tehdit IP politikası adları (boş = kapalı)",
                label_en="Threat IP policy names (empty = off)",
            ),
        ),
        inputs=("fg_ips", "fg_traffic"),
        evaluate=evaluate_r06,
        format_verified=False,
    ),
    RuleDef(
        id="R07",
        kind="event",
        severity="high",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "ignore_actions",
                "strings",
                [],
                min=0,
                max=_LIST_MAX,
                label_tr="Yok sayılan eylemler",
                label_en="Ignored actions",
            ),
        ),
        inputs=("fg_virus",),
        evaluate=evaluate_r07,
    ),
    RuleDef(
        id="R17",
        kind="condition",
        severity="high",
        window_seconds=1200,
        cooldown_seconds=3600,
        params=(
            Param(
                "cpu_percent",
                "percent",
                80,
                min=1,
                max=100,
                label_tr="CPU sınırı (%)",
                label_en="CPU limit (%)",
            ),
            Param(
                "mem_percent",
                "percent",
                85,
                min=1,
                max=100,
                label_tr="Bellek sınırı (%)",
                label_en="Memory limit (%)",
            ),
            Param(
                "sustain_seconds",
                "seconds",
                600,
                min=60,
                max=86_400,
                label_tr="Sınırın aşılması gereken süre",
                label_en="How long the limit must be exceeded",
            ),
            Param(
                "perf_logids",
                "strings",
                list(PERF_LOGIDS),
                min=1,
                max=_LIST_MAX,
                label_tr="Performans istatistiği logid'leri (sahada doğrula)",
                label_en="Performance statistics logids (verify on site)",
            ),
            Param(
                "cpu_field",
                "strings",
                ["cpu"],
                min=1,
                max=5,
                label_tr="CPU alanı (sahada doğrula)",
                label_en="CPU field (verify on site)",
            ),
            Param(
                "mem_field",
                "strings",
                ["mem"],
                min=1,
                max=5,
                label_tr="Bellek alanı (sahada doğrula)",
                label_en="Memory field (verify on site)",
            ),
            Param(
                "conserve_phrases",
                "strings",
                ["conserve"],
                min=0,
                max=_LIST_MAX,
                label_tr="Conserve mode olay ifadeleri (sahada doğrula)",
                label_en="Conserve mode event phrases (verify on site)",
            ),
        ),
        inputs=("fg_event",),
        evaluate=evaluate_r17,
        format_verified=False,
        clear_seconds=600,
    ),
    RuleDef(
        id="R18",
        kind="event",
        severity="high",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(
            Param(
                "fortigate_phrases",
                "strings",
                ["ran out of"],
                min=0,
                max=_LIST_MAX,
                label_tr="FortiGate havuz doldu ifadeleri (sahada doğrula)",
                label_en="FortiGate pool exhausted phrases (verify on site)",
            ),
        ),
        inputs=("mt_event", "fg_event"),
        evaluate=evaluate_r18,
    ),
)
