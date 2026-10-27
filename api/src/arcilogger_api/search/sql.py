"""A plan as SQLAlchemy Core statements with bound parameters (ADR-0015, contract 2.5).

**Safe by construction** (contract 2.5.1): identifiers come only from the field catalogue
(``Base.metadata`` column objects; what the user typed only chose a key of it), and every value
is a bound parameter — ``literal(…)``, ``CAST(:p AS INET|CIDR|MACADDR)``, ``LIKE``/``ILIKE`` with
``\\``, ``%`` and ``_`` escaped here (``ESCAPE '\\'``). psycopg binds the values server-side; no
value is ever part of the SQL text, and this module calls no ``text()``
(``api/tests/test_search_sql_safety.py``).

The semantics are those of :mod:`arcilogger_api.search.matcher` (the oracle test keeps the two
equal): one clause matches when any value matches any of the field's columns in that table; a
negated clause is ``NOT coalesce(…, false)``, so a row whose column is empty matches it; text is
compared ignoring case for ASCII letters only (``lower``/``ILIKE`` in the installer's locale C).

Statements:

* :func:`page_statement` — one window of a page: each branch ``(SELECT <rank> AS b, time, id, …
  ORDER BY time DESC, id DESC LIMIT :n)``, several branches ``UNION ALL``\\ ed and ordered ``time
  DESC, b ASC, id DESC``; a typed branch with ``messages``-only conditions joins ``messages`` by
  the raw reference (one probe of ``ix_messages_source_time`` per candidate row). On TimescaleDB
  a window never holds a table's **compression horizon** (:func:`horizons_statement`; the window
  scan ends a window there), so a branch reads either uncompressed chunks or compressed ones.
* :func:`typed_rows`, :func:`message_rows` — the fields of a page's rows, one keyed query per
  table, the page's keys bound as arrays (``unnest``) and their time range as a constant.
* :func:`count_statement`, :func:`rollup_statement` — the statistics (``stats.py``): buckets
  (``date_bin`` aligned to site midnight) and the five facets in one ``GROUPING SETS`` query.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    ColumnElement,
    Date,
    FromClause,
    Integer,
    Interval,
    Select,
    Text,
    and_,
    case,
    cast,
    column,
    false,
    func,
    literal,
    not_,
    null,
    or_,
    select,
    table,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET, MACADDR, TIMESTAMP

from arcilogger_api.search.cursor import Bound
from arcilogger_api.search.fields import (
    FIELDS,
    ContainsValue,
    DepartmentValue,
    DomainValue,
    ErrorValue,
    Field,
    IntValue,
    IpNetwork,
    IpValue,
    MacValue,
    NameValue,
    PatternValue,
    Present,
    ProtoValue,
    SeverityValue,
    TokenValue,
    Value,
    VlanValue,
)
from arcilogger_api.search.planner import Branch, Condition, Plan, Rollup
from arcilogger_db.kinds import (
    ACTION_TABLES,
    CLIENT_IP,
    KINDS,
    MT_EVENT_KINDS,
    TABLE_KINDS,
    TYPED_TABLES,
    UTM_SUBTYPES,
    ascii_lower,
)
from arcilogger_db.models import TIME_SERIES_TABLES, Base, Message, SearchCount

TZ: Final = TIMESTAMP(timezone=True)
ESCAPE: Final = "\\"
#: Columns every branch of a page returns (the raw reference included).
COMMON: Final = (
    "time",
    "id",
    "time_received",
    "source",
    "sender",
    "raw_day",
    "raw_line",
    "raw_offset",
)
#: ``messages`` columns of a row (a typed page fills them in with :func:`message_rows`).
MESSAGE_COLUMNS: Final = ("type", "subtype", "text", "parse_error")
#: Origin of the histogram buckets: site midnight (Europe/Istanbul is +03:00 all year).
BUCKET_ORIGIN: Final = datetime.fromisoformat("2000-01-01T00:00:00+03:00")
#: The /24 of the client address in the statistics.
NET_PREFIX: Final = 24
#: TimescaleDB's view of the chunks (read on TimescaleDB only, :func:`horizons_statement`).
CHUNKS: Final = table(
    "chunks",
    column("hypertable_schema", Text),
    column("hypertable_name", Text),
    column("range_end", TZ),
    column("is_compressed", Boolean),
    schema="timescaledb_information",
)

Columns = Any  # a table's or an alias's ``.c``


def like_escape(text: str) -> str:
    """``text`` as a literal part of a ``LIKE`` pattern (``\\``, ``%``, ``_`` escaped)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _text(value: str) -> ColumnElement[str]:
    return literal(value, Text)


def _has_ascii_letter(text: str) -> bool:
    return any("a" <= char <= "z" or "A" <= char <= "Z" for char in text)


# =================================================================================================
# One value, one condition
# =================================================================================================


def _ip(column_: ColumnElement[Any], value: IpValue) -> ColumnElement[bool]:
    if value.exact:
        return column_ == cast(_text(value.render()), INET)
    return column_.op("<<=", is_comparison=True)(cast(_text(str(value.network)), CIDR))


def _in_networks(column_: ColumnElement[Any], networks: Sequence[IpNetwork]) -> ColumnElement[bool]:
    if not networks:
        return false()  # a department without VLANs matches nothing
    inside = column_.op("<<=", is_comparison=True)
    return or_(*(inside(cast(_text(str(network)), CIDR)) for network in networks))


def _between(column_: ColumnElement[Any], low: int | None, high: int | None) -> ColumnElement[bool]:
    if low is not None and low == high:
        return column_ == literal(low, BigInteger)
    parts = []
    if low is not None:
        parts.append(column_ >= literal(low, BigInteger))
    if high is not None:
        parts.append(column_ <= literal(high, BigInteger))
    return and_(*parts)


def _domain(column_: ColumnElement[Any], value: DomainValue, table: str) -> ColumnElement[bool]:
    if table == "fg_web":
        # Written exactly like the index ix_fg_web_rhost_time: reverse(lower(hostname)). The range
        # [r + '.', r + '/') holds the subdomains ('/' follows '.' in byte order, collation C);
        # the name itself is a separate arm (the single range [r, r + '/') would also hold
        # "my-example.com").
        reversed_ = func.reverse(func.lower(column_))
        name = value.name[::-1]
        subdomains = and_(reversed_ >= _text(name + "."), reversed_ < _text(name + "/"))
        if value.subdomains_only:
            return subdomains
        return or_(reversed_ == _text(name), subdomains)
    lowered = func.lower(column_)
    subdomains = lowered.like(_text("%." + like_escape(value.name)), escape=ESCAPE)
    if value.subdomains_only:
        return subdomains
    return or_(lowered == _text(value.name), subdomains)


def _pattern(column_: ColumnElement[Any], value: PatternValue) -> ColumnElement[bool]:
    if value.exact:
        return func.lower(column_) == _text(ascii_lower(value.core))
    pattern = ("%" if value.ends else "") + like_escape(value.core) + ("%" if value.starts else "")
    return column_.ilike(_text(pattern), escape=ESCAPE)


def _source(column_: ColumnElement[Any], value: PatternValue, member: bool) -> ColumnElement[bool]:
    """Exact (and ``name-%`` for a configured network source) or a prefix; case as stored."""
    if value.starts:
        return column_.like(_text(like_escape(value.core) + "%"), escape=ESCAPE)
    exact = column_ == _text(value.core)
    if member:
        return or_(exact, column_.like(_text(like_escape(value.core) + "-%"), escape=ESCAPE))
    return exact


def _contains(column_: ColumnElement[Any], value: ContainsValue) -> ColumnElement[bool]:
    pattern = _text("%" + like_escape(value.term) + "%")
    if _has_ascii_letter(value.term):
        return column_.ilike(pattern, escape=ESCAPE)
    return column_.like(pattern, escape=ESCAPE)  # no letter to fold: LIKE is cheaper


def value_condition(
    entry: Field,
    value: Value,
    column_: ColumnElement[Any],
    table: str,
    networks: Sequence[IpNetwork] = (),
    member: bool = False,
) -> ColumnElement[bool]:
    """``column_`` (of ``table``) matches one ``value`` of field ``entry``; ``networks``: the
    subnets of a ``vlan:``/``department:`` value; ``member``: a ``source:`` value names a
    configured network source."""
    if isinstance(value, Present):
        return column_.is_not(None)
    if isinstance(value, IpValue):
        return _ip(column_, value)
    if isinstance(value, IntValue):
        return _between(column_, value.low, value.high)
    if isinstance(value, ProtoValue):
        if table == "mt_fw":  # the name as text
            return func.upper(column_) == _text(value.name)
        return column_ == literal(value.number, Integer)
    if isinstance(value, MacValue):
        if value.low == value.high:
            return column_ == cast(_text(value.low), MACADDR)
        return column_.between(cast(_text(value.low), MACADDR), cast(_text(value.high), MACADDR))
    if isinstance(value, DomainValue):
        return _domain(column_, value, table)
    if isinstance(value, NameValue):
        return column_ == _text(value.name)
    if isinstance(value, TokenValue):
        return func.lower(column_) == _text(value.token)
    if isinstance(value, PatternValue):
        if entry.form == "source":
            return _source(column_, value, member)
        return _pattern(column_, value)
    if isinstance(value, ContainsValue):
        return _contains(column_, value)
    if isinstance(value, SeverityValue):
        return _between(column_, value.low, value.high)
    if isinstance(value, ErrorValue):
        if value.prefix:
            return column_.like(_text(like_escape(value.name) + "%"), escape=ESCAPE)
        return column_ == _text(value.name)
    if isinstance(value, VlanValue | DepartmentValue):
        return _in_networks(column_, networks)
    raise TypeError(f"no condition for {value!r}")  # pragma: no cover - type: is the plan's


def _column_names(entry: Field, value: Value, table: str) -> tuple[str, ...]:
    """The columns one value reads (``policy:``: the number or the name)."""
    names = entry.columns[table]
    if entry.form == "policy" and not isinstance(value, Present):
        return (names[0],) if isinstance(value, IntValue) else (names[1],)
    return names


def _combine(condition: Condition, parts: list[ColumnElement[bool]]) -> ColumnElement[bool]:
    expression = parts[0] if len(parts) == 1 else or_(*parts)
    if condition.negated:
        return not_(func.coalesce(expression, false()))
    return expression


def condition_sql(condition: Condition, columns: Columns, table: str) -> ColumnElement[bool]:
    """One clause on a row of ``table`` (``columns``: its ``.c`` or an alias's)."""
    entry = FIELDS[condition.field]
    parts: list[ColumnElement[bool]] = []
    for index, value in enumerate(condition.values):
        networks = condition.networks[index] if condition.networks is not None else ()
        member = condition.members[index] if condition.members is not None else False
        for name in _column_names(entry, value, table):
            parts.append(value_condition(entry, value, columns[name], table, networks, member))
    return _combine(condition, parts)


def kinds_condition(columns: Columns, kinds: Iterable[str]) -> ColumnElement[bool] | None:
    """The kinds as a condition on ``messages`` (``None``: every kind)."""
    wanted = [key for key in KINDS if key in set(kinds)]
    if len(wanted) == len(KINDS):
        return None
    excluded = [key for key in KINDS if key not in wanted]
    if len(wanted) <= len(excluded):
        if not wanted:
            return false()
        return or_(*(KINDS[key].messages_predicate(columns) for key in wanted))
    either = or_(*(KINDS[key].messages_predicate(columns) for key in excluded))
    return not_(func.coalesce(either, false()))


def kind_sql(columns: Columns) -> ColumnElement[str]:
    """``kinds.kind_of(type, subtype)`` of a ``messages`` row, in SQL."""
    utm = case(dict(UTM_SUBTYPES), value=columns.subtype, else_=_text("utm"))
    by_type = {
        kind.type: kind.key
        for kind in KINDS.values()
        if kind.type is not None and kind.type != "utm"
    }
    other = case(by_type, value=columns.type, else_=_text("other"))
    return case((columns.type == _text("utm"), utm), else_=other)


# =================================================================================================
# Pages
# =================================================================================================


def _table(name: str) -> Any:
    return Base.metadata.tables[name]


def _upper(columns: Columns, bound: Bound) -> ColumnElement[bool]:
    if bound.before_id is not None:
        return tuple_(columns.time, columns.id) < tuple_(
            literal(bound.time, TZ), literal(bound.before_id, BigInteger)
        )
    time: ColumnElement[datetime] = columns.time
    if bound.inclusive:
        return time <= literal(bound.time, TZ)
    return time < literal(bound.time, TZ)


def _join_messages(table: Any) -> tuple[FromClause, Any]:
    """``table`` joined with its lines' ``messages`` rows by the raw reference."""
    message = Message.__table__.alias("m")
    joined = table.join(
        message,
        and_(
            message.c.time == table.c.time,
            message.c.source == table.c.source,
            message.c.raw_day == table.c.raw_day,
            message.c.raw_line == table.c.raw_line,
        ),
    )
    return joined, message


def branch_where(
    plan: Plan, branch: Branch, lower: datetime, bound: Bound
) -> tuple[FromClause, Any, list[ColumnElement[bool]]]:
    """The branch's table (joined with ``messages`` where needed), its columns and its
    conditions inside ``[lower, bound)``."""
    table = _table(branch.table)
    columns = table.c
    where: list[ColumnElement[bool]] = [columns.time >= literal(lower, TZ), _upper(columns, bound)]
    source: FromClause = table
    if branch.table == "messages":
        kinds = kinds_condition(columns, plan.kinds)
        if kinds is not None:
            where.append(kinds)
    if branch.events is not None:
        where.append(columns.event.in_([_text(event) for event in branch.events]))
    where += [condition_sql(condition, columns, branch.table) for condition in branch.own]
    if branch.joined:
        source, message = _join_messages(table)
        where += [condition_sql(condition, message.c, "messages") for condition in branch.joined]
    return source, columns, where


def _branch_page(
    plan: Plan, branch: Branch, lower: datetime, bound: Bound, limit: int
) -> Select[Any]:
    source, columns, where = branch_where(plan, branch, lower, bound)
    selected: list[Any] = [literal(branch.rank, Integer).label("b")]
    selected += [columns[name] for name in COMMON]
    if branch.table == "messages":
        selected += [columns[name] for name in MESSAGE_COLUMNS]
    return (
        select(*selected)
        .select_from(source)
        .where(*where)
        .order_by(columns.time.desc(), columns.id.desc())
        .limit(literal(limit, Integer))
    )


def page_statement(
    plan: Plan, lower: datetime, bounds: Mapping[int, Bound], limit: int
) -> Select[Any]:
    """The newest ``limit`` rows of the plan in ``[lower, bound)`` (``bounds``: per rank)."""
    parts = [
        _branch_page(plan, branch, lower, bounds[branch.rank], limit) for branch in plan.branches
    ]
    if len(parts) == 1:
        return parts[0]
    union = union_all(*parts).subquery("page")
    return (
        select(union)
        .order_by(union.c.time.desc(), union.c.b.asc(), union.c.id.desc())
        .limit(literal(limit, Integer))
    )


def custom_plans_statement() -> Select[Any]:
    """``plan_cache_mode = force_custom_plan`` for the rest of the transaction (``set_config``
    with ``is_local``, as ``SET LOCAL``): every statement is planned with its values."""
    return select(
        func.set_config(_text("plan_cache_mode"), _text("force_custom_plan"), literal(True))
    )


def horizons_statement() -> Select[Any]:
    """Per time-series table with a compressed chunk, the end of its newest one (``tbl``,
    ``horizon``; TimescaleDB only).

    TimescaleDB estimates a compressed chunk at up to 1,000 rows a compressed batch, leaving out
    the conditions on its other columns: in a window that holds both kinds of chunk the page's
    ``LIMIT`` looks soon reached, and the uncompressed chunks are read backwards along the
    primary key instead of by the selective index. A window that ends at the horizon holds only
    uncompressed chunks of the table, planned with their statistics as a native partition is,
    and the compressed ones are read only when its rows did not fill the page (ADR-0015)."""
    return (
        select(CHUNKS.c.hypertable_name.label("tbl"), func.max(CHUNKS.c.range_end).label("horizon"))
        .where(
            CHUNKS.c.is_compressed,
            CHUNKS.c.hypertable_schema == func.current_schema(),
            CHUNKS.c.hypertable_name.in_([_text(name) for name in TIME_SERIES_TABLES]),
        )
        .group_by(CHUNKS.c.hypertable_name)
    )


def _array(values: Iterable[Any], type_: Any) -> Any:
    return literal(list(values), ARRAY(type_))


def _within(found: Any, times: Sequence[datetime]) -> ColumnElement[bool]:
    """``time`` between the keys' earliest and latest: implied by the keys, but a constant range,
    so only the partitions or chunks that hold them are planned (joined with the keys alone,
    TimescaleDB reads every chunk, the compressed ones decompressed)."""
    if not times:
        return false()
    time: ColumnElement[datetime] = found.c.time
    return time.between(literal(min(times), TZ), literal(max(times), TZ))


def typed_rows(table: str, keys: Sequence[tuple[datetime, int]]) -> Select[Any]:
    """The rows ``(time, id)`` of a typed table (a typed page)."""
    found = _table(table)
    wanted = (
        func.unnest(_array((k[0] for k in keys), TZ), _array((k[1] for k in keys), BigInteger))
        .table_valued(column("time", TZ), column("id", BigInteger))
        .render_derived(name="k")
    )
    return (
        select(found)
        .join(wanted, and_(found.c.time == wanted.c.time, found.c.id == wanted.c.id))
        .where(_within(found, [k[0] for k in keys]))
    )


RawKey = tuple[datetime, str, Any, int]


def _by_reference(found: Any, keys: Sequence[RawKey]) -> Any:
    wanted = (
        func.unnest(
            _array((k[0] for k in keys), TZ),
            _array((k[1] for k in keys), Text),
            _array((k[2] for k in keys), Date),
            _array((k[3] for k in keys), Integer),
        )
        .table_valued(
            column("time", TZ),
            column("source", Text),
            column("raw_day", Date),
            column("raw_line", Integer),
        )
        .render_derived(name="k")
    )
    return and_(
        found.c.time == wanted.c.time,
        found.c.source == wanted.c.source,
        found.c.raw_day == wanted.c.raw_day,
        found.c.raw_line == wanted.c.raw_line,
        _within(found, [k[0] for k in keys]),
    ), wanted


def rows_by_reference(table: str, keys: Sequence[RawKey]) -> Select[Any]:
    """The rows of ``table`` of the lines ``(time, source, raw_day, raw_line)``."""
    found = _table(table)
    on, wanted = _by_reference(found, keys)
    return select(found).join(wanted, on)


def message_rows(keys: Sequence[RawKey]) -> Select[Any]:
    """The ``messages`` rows of a typed page's lines."""
    return rows_by_reference("messages", keys)


# =================================================================================================
# Statistics
# =================================================================================================

#: The columns every branch of a count gives (``bucket`` from ``date_bin``; ``net`` the /24).
COUNT_COLUMNS: Final = ("bucket", "time", "b", "id", "source", "kind", "action", "net")
#: ``grouping(bucket, source, kind, action, net)`` of each grouping set.
GROUPING: Final = {
    "bucket": 0b01111,
    "source": 0b10111,
    "kind": 0b11011,
    "action": 0b11101,
    "net": 0b11110,
}


def _bucket(time: ColumnElement[Any], seconds: int) -> ColumnElement[Any]:
    return func.date_bin(
        literal(timedelta(seconds=seconds), Interval), time, literal(BUCKET_ORIGIN, TZ)
    )


def _net(address: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.network(func.set_masklen(address, literal(NET_PREFIX, Integer)))


def _kind_of_table(table: str, columns: Columns) -> ColumnElement[str]:
    if table == "mt_event":
        return case(dict(MT_EVENT_KINDS), value=columns.event, else_=null())
    return _text(TABLE_KINDS[table])


def _typed_keys(table: str, columns: Columns) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
    """``(lower(action), client address)`` of a typed table (``NULL`` where it has none)."""
    action = func.lower(columns.action) if table in ACTION_TABLES else cast(null(), Text)
    client = columns[CLIENT_IP[table]] if table in CLIENT_IP else cast(null(), INET)
    return action, client


def _typed_lines(lower: datetime, upper: datetime) -> FromClause:
    """``(time, source, raw_day, raw_line, action, client)`` of every typed row in a range."""
    parts = []
    for name in TYPED_TABLES:
        table = _table(name)
        action, client = _typed_keys(name, table.c)
        parts.append(
            select(
                table.c.time,
                table.c.source,
                table.c.raw_day,
                table.c.raw_line,
                action.label("action"),
                client.label("client"),
            ).where(table.c.time >= literal(lower, TZ), table.c.time < literal(upper, TZ))
        )
    return union_all(*parts).subquery("typed")


def _branch_counts(
    plan: Plan, branch: Branch, lower: datetime, upper: datetime, seconds: int, keys: bool
) -> Select[Any]:
    source, columns, where = branch_where(plan, branch, lower, Bound(upper))
    if branch.table == "messages":
        kind = kind_sql(columns)
        action: ColumnElement[Any] = cast(null(), Text)
        client: ColumnElement[Any] = cast(null(), INET)
        if keys:  # the typed row of each line, as search_counts has it (hybrid edges)
            typed = _typed_lines(lower, upper)
            source = source.outerjoin(
                typed,
                and_(
                    typed.c.time == columns.time,
                    typed.c.source == columns.source,
                    typed.c.raw_day == columns.raw_day,
                    typed.c.raw_line == columns.raw_line,
                ),
            )
            action, client = typed.c.action, typed.c.client
    else:
        kind = _kind_of_table(branch.table, columns)
        action, client = _typed_keys(branch.table, columns)
    return (
        select(
            _bucket(columns.time, seconds).label("bucket"),
            columns.time,
            literal(branch.rank, Integer).label("b"),
            columns.id,
            columns.source,
            kind.label("kind"),
            action.label("action"),
            _net(client).label("net"),
        )
        .select_from(source)
        .where(*where)
    )


def _grouped(rows: FromClause, count: ColumnElement[Any]) -> Select[Any]:
    sets = [rows.c[name] for name in ("bucket", "source", "kind", "action", "net")]
    return select(*sets, func.grouping(*sets).label("g"), count.label("n")).group_by(
        func.grouping_sets(*sets)
    )


def count_statement(
    plan: Plan,
    lower: datetime,
    upper: datetime,
    seconds: int,
    *,
    keys: bool = False,
    limit: int | None = None,
) -> Select[Any]:
    """Counts of the plan's rows in ``[lower, upper)`` per bucket and per facet value, one
    grouping set each (``GROUPING`` tells them apart); ``limit``: only the newest rows;
    ``keys``: a ``messages`` branch also gets each line's action and /24 from its typed row."""
    parts = [_branch_counts(plan, branch, lower, upper, seconds, keys) for branch in plan.branches]
    rows: FromClause = (union_all(*parts) if len(parts) > 1 else parts[0]).subquery("rows")
    if limit is not None:
        rows = (
            select(rows)
            .order_by(rows.c.time.desc(), rows.c.b.asc(), rows.c.id.desc())
            .limit(literal(limit, Integer))
            .subquery("newest")
        )
    return _grouped(rows, func.count())


def rollup_statement(rollup: Rollup, lower: datetime, upper: datetime, seconds: int) -> Select[Any]:
    """The same counts from ``search_counts`` for the full hours ``[lower, upper)``."""
    counts = SearchCount.__table__
    columns = counts.c
    where: list[ColumnElement[bool]] = [
        columns.hour >= literal(lower, TZ),
        columns.hour < literal(upper, TZ),
    ]
    if len(rollup.kinds) != len(KINDS):
        where.append(columns.kind.in_([_text(kind) for kind in rollup.kinds]))
    if rollup.tables is not None:
        where.append(columns.tbl.in_([_text(table) for table in rollup.tables]))
    targets = {"source": columns.source, "action": columns.action, "vlan": columns.net}
    for condition in rollup.conditions:
        entry = FIELDS[condition.field]
        target = targets.get(condition.field, columns.net)
        parts = []
        for index, value in enumerate(condition.values):
            networks = condition.networks[index] if condition.networks is not None else ()
            member = condition.members[index] if condition.members is not None else False
            parts.append(value_condition(entry, value, target, "search_counts", networks, member))
        where.append(_combine(condition, parts))
    rows = (
        select(
            _bucket(columns.hour, seconds).label("bucket"),
            columns.source,
            columns.kind,
            columns.action,
            columns.net,
            columns.n,
        )
        .where(*where)
        .subquery("counted")
    )
    return _grouped(rows, func.sum(rows.c.n))
