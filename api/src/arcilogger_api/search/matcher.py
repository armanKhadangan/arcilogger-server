"""A parsed query as a Python predicate over one record (ADR-0015, contract 2.14.3).

Canlı Akış filters the lines it reads from the spool with it, and the search tests use it as the
oracle the SQL must agree with. A **record** is one loaded line: its ``messages`` row and, when
the line has one, its typed row and table (:class:`Record`) — what ``prepare_line`` makes of a
spool line, or what the database holds for it.

The semantics are those of the SQL the planner compiles:

* A query with a typed field (``srcip``, ``domain``, ``mac``, ``action`` …) matches records whose
  typed table has every typed field of the query and a kind ``type:`` allows
  (:func:`~arcilogger_api.search.fields.branch_tables`; for ``mt_event`` the kind of its
  ``event``); its ``messages``-only conditions (free text, ``host``, ``severity``, ``error``,
  ``subtype``) are checked on the line's ``messages`` row. A line without a typed row (or with
  one in another table) never matches it: ``-action:accept`` finds records that **have** an
  action other than accept.
* A query without a typed field matches ``messages`` rows: ``type:`` by
  :func:`arcilogger_db.kinds.kind_of`, ``source``, ``sender`` and the ``messages``-only fields.
* One clause matches when any of its values matches any of the field's columns in that table;
  a negated clause is ``NOT coalesce(…, false)``: a record whose columns are empty matches it.
* Text is compared ignoring case for ASCII letters only (``kinds.ascii_lower``, PostgreSQL's
  ``lower()``/``ILIKE`` in locale C); ``qname:`` and ``source:`` compare exactly, as stored.
* ``vlan:``/``department:`` are bound once, when the matcher is built, to the subnets of the
  site's VLANs (:class:`~arcilogger_api.search.fields.SiteNetwork`; unknown ones are refused);
  ``source:ap`` of a configured network source ``ap`` also matches its members ``ap-<ip>``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

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
    KindValue,
    MacValue,
    NameValue,
    PatternValue,
    Present,
    ProtoValue,
    SeverityValue,
    SiteNetwork,
    TokenValue,
    Value,
    VlanValue,
    allowed_kinds,
    branch_tables,
    mac_text,
)
from arcilogger_api.search.grammar import Clause, Query
from arcilogger_db.kinds import ascii_lower, kind_of, kind_of_row

#: Fields whose value a spool line has before it is parsed (the folder and the sender).
UNPARSED_FIELDS: Final = frozenset({"source", "sender"})

ValueTest = Callable[[Any], bool]
RecordTest = Callable[["Record"], bool]


@dataclass(frozen=True, slots=True)
class Record:
    """One line: its ``messages`` row and its typed row (``table``, ``row``), if any."""

    message: Mapping[str, Any]
    table: str | None = None
    row: Mapping[str, Any] | None = None


def _interface(value: Any) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface | None:
    try:
        return ipaddress.ip_interface(str(value))
    except ValueError:
        return None


def _in_networks(networks: tuple[IpNetwork, ...]) -> ValueTest:
    """``column <<= network`` for any of ``networks``."""

    def test(value: Any) -> bool:
        address = _interface(value)
        if address is None:
            return False
        return any(
            address.version == net.version and address.network.subnet_of(net)  # type: ignore[arg-type]
            for net in networks
        )

    return test


def _ip_test(value: IpValue) -> ValueTest:
    if not value.exact:
        return _in_networks((value.network,))
    wanted = value.network.network_address

    def test(column: Any) -> bool:
        address = _interface(column)
        return (
            address is not None
            and address.ip == wanted
            and address.network.prefixlen == address.max_prefixlen
        )

    return test


def _between(low: int | None, high: int | None) -> ValueTest:
    def test(column: Any) -> bool:
        if not isinstance(column, int) or isinstance(column, bool):
            return False
        return (low is None or column >= low) and (high is None or column <= high)

    return test


def _proto_test(value: ProtoValue, table: str) -> ValueTest:
    if table == "mt_fw":  # the name as text, compared like upper(proto) = 'TCP'
        name = ascii_lower(value.name)
        return lambda column: ascii_lower(str(column)) == name
    return lambda column: column == value.number


def _mac_test(value: MacValue) -> ValueTest:
    def test(column: Any) -> bool:
        mac = mac_text(str(column))
        return mac is not None and value.low <= mac <= value.high

    return test


def _pattern_test(value: PatternValue) -> ValueTest:
    return lambda column: value.matches(str(column))


def _source_test(value: PatternValue, site: SiteNetwork) -> ValueTest:
    """``source = :p`` (and ``LIKE ':p-%'`` for a network source) or ``LIKE ':p%'``: exact."""
    core = value.core
    if value.starts:
        return lambda column: str(column).startswith(core)
    if site.is_network_source(core):
        member = core + "-"
        return lambda column: str(column) == core or str(column).startswith(member)
    return lambda column: str(column) == core


def _value_test(
    entry: Field, value: Value, table: str, site: SiteNetwork, clause: Clause
) -> ValueTest:
    """How one value of ``entry`` compares with a non-empty column of ``table``."""
    if isinstance(value, Present):
        return lambda column: True
    if isinstance(value, IpValue):
        return _ip_test(value)
    if isinstance(value, IntValue):
        return _between(value.low, value.high)
    if isinstance(value, ProtoValue):
        return _proto_test(value, table)
    if isinstance(value, MacValue):
        return _mac_test(value)
    if isinstance(value, DomainValue):
        return lambda column: value.matches(str(column))
    if isinstance(value, NameValue):
        return lambda column: str(column) == value.name
    if isinstance(value, TokenValue):
        return lambda column: ascii_lower(str(column)) == value.token
    if isinstance(value, PatternValue):
        if entry.form == "source":
            return _source_test(value, site)
        return _pattern_test(value)
    if isinstance(value, ContainsValue):
        return lambda column: value.matches(str(column))
    if isinstance(value, SeverityValue):
        return _between(value.low, value.high)
    if isinstance(value, ErrorValue):
        if value.prefix:
            return lambda column: str(column).startswith(value.name)
        return lambda column: str(column) == value.name
    if isinstance(value, VlanValue | DepartmentValue):
        return _in_networks(site.networks(value, clause.start, clause.end))
    raise TypeError(f"no test for {value!r}")  # pragma: no cover - KindValue is handled apart


def _columns(entry: Field, value: Value, table: str) -> tuple[str, ...]:
    """The columns one value reads (``policy:``: the number or the name)."""
    columns = entry.columns[table]
    if entry.form == "policy" and not isinstance(value, Present):
        return (columns[0],) if isinstance(value, IntValue) else (columns[1],)
    return columns


def _clause_test(
    clause: Clause, tables: tuple[str, ...], site: SiteNetwork
) -> Callable[[Mapping[str, Any], str], bool]:
    """The positive clause on a row of one of ``tables``: ``test(row, table)``."""
    entry = FIELDS[clause.field]
    checks: dict[str, list[tuple[tuple[str, ...], ValueTest]]] = {}
    for table in tables:
        checks[table] = [
            (_columns(entry, value, table), _value_test(entry, value, table, site, clause))
            for value in clause.values
        ]

    def test(row: Mapping[str, Any], table: str) -> bool:
        for columns, check in checks[table]:
            for column in columns:
                value = row.get(column)
                if value is not None and check(value):
                    return True
        return False

    return test


def _negatable(test: RecordTest, negated: bool) -> RecordTest:
    return (lambda record: not test(record)) if negated else test


@dataclass(frozen=True)
class Matcher:
    """A compiled query: ``matcher(record)`` tells whether the record matches."""

    query: Query
    #: The typed tables that drive the query; ``None``: ``messages`` drives it.
    tables: tuple[str, ...] | None
    #: The kinds ``type:`` allows.
    kinds: frozenset[str]
    _tests: tuple[RecordTest, ...] = field(repr=False)
    _source_tests: tuple[Callable[[str], bool], ...] = field(repr=False)

    def __call__(self, record: Record) -> bool:
        if self.tables is not None:
            if record.table not in self.tables or record.row is None:
                return False
            if kind_of_row(record.table, record.row.get("event")) not in self.kinds:
                return False
        return all(test(record) for test in self._tests)

    @property
    def typed(self) -> bool:
        """Driven by typed tables: only records with a typed row can match."""
        return self.tables is not None

    @property
    def needs_parse(self) -> bool:
        """Some clause reads more than the folder and the sender of a spool line."""
        return any(clause.field not in UNPARSED_FIELDS for clause in self.query.clauses)

    @property
    def terms(self) -> tuple[str, ...]:
        """Free-text terms every match contains (a cheap byte search may skip other lines)."""
        return self.query.terms

    def wants_source(self, name: str) -> bool:
        """Whether a record of source ``name`` can match (the ``source:`` clauses only)."""
        return all(test(name) for test in self._source_tests)


def compile_matcher(query: Query, site: SiteNetwork | None = None) -> Matcher:
    """The predicate of ``query`` (``site``: the VLANs, departments and network sources it may
    name). :class:`~arcilogger_api.search.messages.SearchQueryError` when no table has every
    typed field, or for an unknown VLAN or department."""
    site = site or SiteNetwork()
    clauses = query.clauses
    tables = branch_tables(clauses)
    kinds = allowed_kinds(clauses)
    tests: list[RecordTest] = []
    source_tests: list[Callable[[str], bool]] = []
    for clause in clauses:
        entry = FIELDS[clause.field]
        if entry.name == "type":
            if tables is None:
                named = frozenset(v.kind for v in clause.values if isinstance(v, KindValue))
                tests.append(_negatable(_kind_test(named), clause.negated))
            continue  # a typed query keeps its kinds through the tables (Matcher.__call__)
        if entry.scope == "typed":
            # A typed field makes the query typed: branch_tables named its tables.
            typed_test = _clause_test(clause, tables or (), site)
            tests.append(_negatable(_typed_record_test(typed_test), clause.negated))
            continue
        message_test = _clause_test(clause, ("messages",), site)
        tests.append(_negatable(_message_record_test(message_test), clause.negated))
        if entry.name == "source":
            source_tests.append(_negatable_name(message_test, clause.negated))
    return Matcher(query, tables, kinds, tuple(tests), tuple(source_tests))


def _kind_test(named: frozenset[str]) -> RecordTest:
    def test(record: Record) -> bool:
        message = record.message
        return kind_of(str(message.get("type")), message.get("subtype")) in named

    return test


def _typed_record_test(test: Callable[[Mapping[str, Any], str], bool]) -> RecordTest:
    def record_test(record: Record) -> bool:
        if record.row is None or record.table is None:  # refused by Matcher first
            return False
        return test(record.row, record.table)

    return record_test


def _message_record_test(test: Callable[[Mapping[str, Any], str], bool]) -> RecordTest:
    return lambda record: test(record.message, "messages")


def _negatable_name(
    test: Callable[[Mapping[str, Any], str], bool], negated: bool
) -> Callable[[str], bool]:
    def name_test(name: str) -> bool:
        found = test({"source": name}, "messages")
        return not found if negated else found

    return name_test
