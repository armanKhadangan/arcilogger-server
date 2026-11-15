"""Network rules (W6-B, contract §2.2.1): R01 internal port scan, R02 external scan, R08 DNS
bypass, R10 proxy / VPN / Tor and R19 suspicious DNS (DGA).

Every rule groups the rows of its inputs by source address and counts them by **receive time**
(D8) over its window ``(end - window_seconds, end]``. Only addresses with at least one matching
row in the slice ``(start, end]`` are counted (§2.1.2), so one slice costs the slice's rows plus
the window's rows of those addresses (the ``(srcip, time)`` and ``(src, time)`` indexes). Device
time is bounded for partition pruning by :attr:`RuleContext.time_low` and
:attr:`RuleContext.time_high`.

Every parameter reaches SQL as a bound parameter (lists as arrays, networks as ``cidr[]`` with
``<<=``); nothing is formatted into the statement. The rules only read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_network
from typing import Any, Final

from sqlalchemy import (
    CTE,
    Integer,
    Select,
    Subquery,
    Text,
    any_,
    case,
    cast,
    distinct,
    false,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from arcilogger_db.models import FgApp, FgDns, FgTraffic, FgWeb, MtFw
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_worker.rules.model import (
    EvidenceRef,
    Hit,
    Param,
    RuleContext,
    RuleDef,
    RuleSettings,
)

#: Upper bound of a count threshold (a window holds at most this many rows of one address).
MAX_COUNT: Final = 1_000_000
HOUR: Final = 3600
EVIDENCE_MAX_KEY: Final = "rules.evidence_max"
#: Names a title lists (R10's ``what``).
NAMES_SHOWN: Final = 3

# -- SQL building blocks --------------------------------------------------------------------------


def _texts(values: Iterable[object]) -> ColumnElement[Any]:
    """``= ANY(CAST(:values AS text[]))``: one bound parameter however many values."""
    return any_(cast(literal(sorted({str(v) for v in values}), ARRAY(Text)), ARRAY(Text)))


def _ints(values: Iterable[object]) -> ColumnElement[Any]:
    """``= ANY(CAST(:values AS integer[]))``."""
    return any_(
        cast(literal(sorted({int(str(v)) for v in values}), ARRAY(Integer)), ARRAY(Integer))
    )


def networks(values: Iterable[object]) -> list[str]:
    """Settings hold networks as text or as ``ip_network`` objects: canonical CIDR text."""
    return sorted({str(ip_network(str(value), strict=False)) for value in values})


def inside(column: Any, values: Iterable[object]) -> ColumnElement[bool]:
    """``column <<= ANY(CAST(:networks AS cidr[]))`` (false for an empty list)."""
    listed = networks(values)
    if not listed:
        return false()
    return column.op("<<=", is_comparison=True)(  # type: ignore[no-any-return]
        any_(cast(literal(listed, ARRAY(CIDR)), ARRAY(CIDR)))
    )


def param(ctx: RuleContext, key: str) -> Any:
    return ctx.settings.params[key]


@dataclass(frozen=True)
class Input:
    """One table a rule reads: its address column (the group), its match and the values the
    rule aggregates (``measures``, the same names and types in every input of a rule)."""

    table: Any
    address: InstrumentedAttribute[Any]
    where: tuple[ColumnElement[bool], ...]
    measures: Mapping[str, Any]


def _received(table: Any, ctx: RuleContext, since: datetime) -> tuple[ColumnElement[bool], ...]:
    """Receive time in ``(since, end]``, device time within the pruning bounds."""
    return (
        table.time >= ctx.time_low,
        table.time <= ctx.time_high,
        table.time_received > since,
        table.time_received <= ctx.end,
    )


def _slice_addresses(inputs: Sequence[Input], ctx: RuleContext) -> CTE:
    """The addresses with at least one matching row in the slice."""
    parts = [
        select(i.address.label("ip")).where(*i.where, *_received(i.table, ctx, ctx.start))
        for i in inputs
    ]
    query = parts[0] if len(parts) == 1 else union_all(*parts)
    return query.cte("slice_addresses")


def _window_rows(
    inputs: Sequence[Input], ctx: RuleContext, addresses: ColumnElement[Any] | CTE
) -> Subquery:
    """The window's matching rows of ``addresses`` (the slice's addresses, or an ``inet[]`` of
    hit addresses), with their raw reference."""
    parts: list[Select[Any]] = []
    for i in inputs:
        chosen = (
            i.address.in_(select(addresses.c.ip))
            if isinstance(addresses, CTE)
            else i.address == any_(addresses)
        )
        parts.append(
            select(
                i.address.label("ip"),
                literal(i.table.__tablename__, Text).label("tbl"),
                i.table.id.label("row_id"),
                i.table.time.label("time"),
                i.table.time_received.label("time_received"),
                i.table.source.label("source"),
                i.table.raw_day.label("raw_day"),
                i.table.raw_line.label("raw_line"),
                i.table.raw_offset.label("raw_offset"),
                *(column.label(name) for name, column in i.measures.items()),
            ).where(*i.where, chosen, *_received(i.table, ctx, ctx.window_start))
        )
    query = parts[0] if len(parts) == 1 else union_all(*parts)
    return query.subquery("window_rows")


Aggregates = Callable[[Subquery], Mapping[str, ColumnElement[Any]]]


def evaluate(
    session: Session,
    ctx: RuleContext,
    inputs: Sequence[Input],
    *,
    aggregates: Aggregates,
    reached: Callable[[Mapping[str, ColumnElement[Any]]], ColumnElement[bool]],
) -> list[Hit]:
    """Count the window per address and return a hit for every address that ``reached`` its
    threshold, with up to ``evidence_max`` evidence rows, oldest first.

    ``aggregates`` builds the per-address values from the window rows (``count`` is always
    there); the hit's ``values`` are those aggregates plus ``window_seconds``."""
    if not inputs:
        return []
    rows = _window_rows(inputs, ctx, _slice_addresses(inputs, ctx))
    measured = {"count": func.count(), **aggregates(rows)}
    found = session.execute(
        select(
            rows.c.ip,
            func.max(rows.c.time_received).label("last"),
            *(column.label(name) for name, column in measured.items()),
        )
        .group_by(rows.c.ip)
        .having(reached(measured))
        .order_by(rows.c.ip)
    ).all()
    if not found:
        return []
    evidence = _evidence(session, ctx, inputs, [str(row.ip) for row in found])
    hits: list[Hit] = []
    for row in found:
        key = str(row.ip)
        values: dict[str, object] = {
            name: _value(row._mapping[name]) for name in measured if name != "count"
        }
        values["window_seconds"] = ctx.settings.window_seconds
        hits.append(
            Hit(
                key=key,
                time=row.last,
                count=int(row._mapping["count"]),
                values=values,
                evidence=evidence.get(key, ()),
            )
        )
    return hits


def _value(value: object) -> object:
    """A count as it is; a list of names (``array_agg``) as one sorted, comma-separated text of
    at most :data:`NAMES_SHOWN` names (a title stays short)."""
    if isinstance(value, list):
        names = sorted({str(item) for item in value if item})
        more = ", …" if len(names) > NAMES_SHOWN else ""
        return ", ".join(names[:NAMES_SHOWN]) + more
    return value


def _evidence_max(session: Session) -> int:
    """``rules.evidence_max`` (a hand-edited value outside 1-100 is brought into range)."""
    try:
        value = int(get_setting(session, EVIDENCE_MAX_KEY))
    except (TypeError, ValueError):
        value = DEFAULT_SETTINGS[EVIDENCE_MAX_KEY]
    return min(max(value, 1), 100)


def _evidence(
    session: Session, ctx: RuleContext, inputs: Sequence[Input], keys: Sequence[str]
) -> dict[str, list[EvidenceRef]]:
    """The oldest ``evidence_max`` window rows of each hit address (receive time, then table
    and row id, so the order is stable)."""
    listed = cast(literal(sorted(keys), ARRAY(INET)), ARRAY(INET))
    limit = _evidence_max(session)
    bare = [Input(i.table, i.address, i.where, {}) for i in inputs]
    window = _window_rows(bare, ctx, listed)
    ranked = select(
        window,
        func.row_number()
        .over(
            partition_by=window.c.ip,
            order_by=(window.c.time_received, window.c.tbl, window.c.row_id),
        )
        .label("n"),
    ).subquery()
    by_key: dict[str, list[EvidenceRef]] = {}
    for row in session.execute(
        select(ranked).where(ranked.c.n <= limit).order_by(ranked.c.ip, ranked.c.n)
    ):
        by_key.setdefault(str(row.ip), []).append(
            EvidenceRef(
                table=row.tbl,
                row_id=int(row.row_id),
                time=row.time,
                time_received=row.time_received,
                source=row.source,
                raw_day=row.raw_day,
                raw_line=int(row.raw_line),
                raw_offset=int(row.raw_offset),
            )
        )
    return by_key


def _internal(column: Any, ctx: RuleContext) -> ColumnElement[bool]:
    return inside(column, ctx.internal_networks)


def _threshold(key: str, default: int, maximum: int = MAX_COUNT) -> Param:
    return Param(
        key,
        "int",
        default,
        min=1,
        max=maximum,
        label_tr="Eşik (başlangıç değeri; ağınıza göre ayarlayın)",
        label_en="Threshold (a starting value; adjust it to your network)",
    )


def _deny_actions() -> Param:
    return Param(
        "deny_actions",
        "strings",
        ("deny",),
        min=1,
        label_tr="Engelleme sayılan FortiGate action değerleri",
        label_en="FortiGate action values counted as blocked",
    )


# -- R01 İçeriden port taraması / Internal port scan ----------------------------------------------


def evaluate_r01(session: Session, ctx: RuleContext) -> list[Hit]:
    """Blocked connections of an internal address to ``threshold_ports`` distinct ports or
    ``threshold_hosts`` distinct addresses within the window; MikroTik firewall lines count
    too when their log prefix is listed in ``mikrotik_prefixes``."""
    inputs = [
        Input(
            FgTraffic,
            FgTraffic.srcip,
            (
                FgTraffic.action == _texts(param(ctx, "deny_actions")),
                _internal(FgTraffic.srcip, ctx),
            ),
            {"port": FgTraffic.dstport, "host": FgTraffic.dstip},
        )
    ]
    prefixes = param(ctx, "mikrotik_prefixes")
    if prefixes:
        inputs.append(
            Input(
                MtFw,
                MtFw.src,
                (MtFw.prefix == _texts(prefixes), _internal(MtFw.src, ctx)),
                {"port": MtFw.dport, "host": MtFw.dst},
            )
        )
    ports, hosts = int(param(ctx, "threshold_ports")), int(param(ctx, "threshold_hosts"))
    return evaluate(
        session,
        ctx,
        inputs,
        aggregates=lambda rows: {
            "ports": func.count(distinct(rows.c.port)),
            "hosts": func.count(distinct(rows.c.host)),
        },
        reached=lambda m: (m["ports"] >= ports) | (m["hosts"] >= hosts),
    )


R01: Final = RuleDef(
    id="R01",
    kind="event",
    severity="high",
    window_seconds=60,
    cooldown_seconds=HOUR,
    params=(
        _threshold("threshold_ports", 30, 65535),
        _threshold("threshold_hosts", 50),
        _deny_actions(),
        Param(
            "mikrotik_prefixes",
            "strings",
            (),
            label_tr="Engelleme kuralı sayılan MikroTik log önekleri (boş: MikroTik okunmaz)",
            label_en="MikroTik log prefixes of blocking rules (empty: MikroTik is not read)",
        ),
    ),
    inputs=("fg_traffic", "mt_fw"),
    evaluate=evaluate_r01,
)


# -- R02 Dışarıdan tarama / External scan --------------------------------------------------------


def evaluate_r02(session: Session, ctx: RuleContext) -> list[Hit]:
    """Blocked connections from an external address, on the listed policies (none: any)."""
    where = [
        FgTraffic.action == _texts(param(ctx, "deny_actions")),
        ~_internal(FgTraffic.srcip, ctx),
    ]
    policies = param(ctx, "policy_names")
    if policies:
        where.append(FgTraffic.policyname == _texts(policies))
    threshold = int(param(ctx, "threshold"))
    return evaluate(
        session,
        ctx,
        [Input(FgTraffic, FgTraffic.srcip, tuple(where), {})],
        aggregates=lambda rows: {},
        reached=lambda m: m["count"] >= threshold,
    )


R02: Final = RuleDef(
    id="R02",
    kind="event",
    severity="medium",
    window_seconds=300,
    cooldown_seconds=HOUR,
    params=(
        _threshold("threshold", 100),
        _deny_actions(),
        Param(
            "policy_names",
            "strings",
            (),
            label_tr="Yalnızca bu FortiGate politikaları (boş: hepsi)",
            label_en="Only these FortiGate policies (empty: all)",
        ),
    ),
    inputs=("fg_traffic",),
    evaluate=evaluate_r02,
)


# -- R08 DNS atlatma / DNS bypass ----------------------------------------------------------------


def evaluate_r08(session: Session, ctx: RuleContext) -> list[Hit]:
    """DNS connections (``ports``) of an internal address to a server that is not one of the
    allowed DNS servers, whatever the FortiGate did with them."""
    threshold = int(param(ctx, "threshold"))
    inputs = [
        Input(
            FgTraffic,
            FgTraffic.srcip,
            (
                _internal(FgTraffic.srcip, ctx),
                FgTraffic.dstport == _ints(param(ctx, "ports")),
                ~inside(FgTraffic.dstip, param(ctx, "allowed_dns_servers")),
            ),
            {"server": FgTraffic.dstip},
        )
    ]
    return evaluate(
        session,
        ctx,
        inputs,
        aggregates=lambda rows: {"servers": func.count(distinct(rows.c.server))},
        reached=lambda m: m["count"] >= threshold,
    )


def r08_needs_setup(settings: RuleSettings) -> bool:
    """Idle until the allowed DNS servers are listed: without them every DNS query counts."""
    return not settings.param("allowed_dns_servers")


R08: Final = RuleDef(
    id="R08",
    kind="event",
    severity="medium",
    window_seconds=HOUR,
    cooldown_seconds=HOUR,
    params=(
        _threshold("threshold", 20),
        Param(
            "ports",
            "ints",
            (53, 853),
            min=1,
            label_tr="DNS portları",
            label_en="DNS ports",
        ),
        Param(
            "allowed_dns_servers",
            "networks",
            (),
            label_tr="İzin verilen DNS sunucuları (adres ya da ağ)",
            label_en="Allowed DNS servers (address or network)",
        ),
    ),
    inputs=("fg_traffic",),
    needs_setup=r08_needs_setup,
    evaluate=evaluate_r08,
)


# -- R10 Proxy / VPN / Tor -----------------------------------------------------------------------


def evaluate_r10(session: Session, ctx: RuleContext) -> list[Hit]:
    """Web or DNS filter rows in a listed FortiGuard category, application control rows of a
    listed application or application category. An empty list switches its part off."""
    inputs: list[Input] = []
    categories = param(ctx, "categories")
    if categories:
        for table in (FgWeb, FgDns):
            matched = table.catdesc == _texts(categories)
            inputs.append(Input(table, table.srcip, (matched,), {"what": table.catdesc}))
    apps, appcats = param(ctx, "apps"), param(ctx, "appcats")
    if apps or appcats:
        parts = []
        name: Any = FgApp.appcat
        if apps:
            parts.append(FgApp.app == _texts(apps))
            name = case((FgApp.app == _texts(apps), FgApp.app), else_=FgApp.appcat)
        if appcats:
            parts.append(FgApp.appcat == _texts(appcats))
        inputs.append(Input(FgApp, FgApp.srcip, (or_(*parts),), {"what": name}))
    threshold = int(param(ctx, "threshold"))
    return evaluate(
        session,
        ctx,
        inputs,
        aggregates=lambda rows: {"what": func.array_agg(distinct(rows.c.what))},
        reached=lambda m: m["count"] >= threshold,
    )


R10: Final = RuleDef(
    id="R10",
    kind="event",
    severity="medium",
    window_seconds=HOUR,
    cooldown_seconds=HOUR,
    params=(
        _threshold("threshold", 1),
        Param(
            "categories",
            "strings",
            ("Proxy Avoidance",),
            label_tr="FortiGuard kategorileri (catdesc; sahada doğrula)",
            label_en="FortiGuard categories (catdesc; verify on site)",
        ),
        Param(
            "apps",
            "strings",
            ("Tor",),
            label_tr="Uygulamalar (app; sahada doğrula)",
            label_en="Applications (app; verify on site)",
        ),
        Param(
            "appcats",
            "strings",
            ("Proxy",),
            label_tr="Uygulama kategorileri (appcat; sahada doğrula)",
            label_en="Application categories (appcat; verify on site)",
        ),
    ),
    inputs=("fg_web", "fg_dns", "fg_app"),
    evaluate=evaluate_r10,
)


# -- R19 Şüpheli DNS (DGA) / Suspicious DNS (DGA) ------------------------------------------------


def evaluate_r19(session: Session, ctx: RuleContext) -> list[Hit]:
    """More than ``threshold`` DNS answers "no such name" for one address within the window.

    The response code is not a column: FortiGate DNS logs keep it in ``extra`` (V6, sahada
    doğrula), under the field named by ``rcode_field``; the key is a bound parameter."""
    (field,) = param(ctx, "rcode_field")
    threshold = int(param(ctx, "threshold"))
    inputs = [
        Input(
            FgDns,
            FgDns.srcip,
            (FgDns.extra[str(field)].astext == _texts(param(ctx, "nxdomain_values")),),
            {"name": FgDns.qname},
        )
    ]
    return evaluate(
        session,
        ctx,
        inputs,
        aggregates=lambda rows: {"names": func.count(distinct(rows.c.name))},
        reached=lambda m: m["count"] > threshold,
    )


R19: Final = RuleDef(
    id="R19",
    kind="event",
    severity="medium",
    window_seconds=HOUR,
    cooldown_seconds=HOUR,
    params=(
        _threshold("threshold", 200),
        Param(
            "rcode_field",
            "strings",
            ("rcode",),
            min=1,
            max=1,
            label_tr="Yanıt kodunun alanı (sahada doğrula)",
            label_en="Field of the response code (verify on site)",
        ),
        Param(
            "nxdomain_values",
            "strings",
            ("NXDOMAIN", "3"),
            min=1,
            label_tr='"Böyle bir ad yok" yanıt kodları (sahada doğrula)',
            label_en='"No such name" response codes (verify on site)',
        ),
    ),
    inputs=("fg_dns",),
    format_verified=False,
    evaluate=evaluate_r19,
)

#: The rules of this module, in id order (``registry.py`` collects them).
RULES: Final[tuple[RuleDef, ...]] = (R01, R02, R08, R10, R19)
