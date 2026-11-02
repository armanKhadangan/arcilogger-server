"""Kinds of log records: what ``type:`` means in a search, a summary count and the live tail.

A **kind** names the records of one kind of log, whatever table holds them (ADR-0015, contract
2.3). The parsers write ``messages.type``/``subtype`` for every line and at most one typed row
(``fg_*``, ``mt_*``); a kind ties the two together, so the search planner, the matcher of the
live tail, the summary table ``search_counts`` (ingest and its rebuild) and the Kaynaklar page all
mean the same records by ``type:web``:

* :func:`kind_of` — the kind of a ``messages`` row from its ``type`` and ``subtype``;
* :data:`KINDS` — every kind with its Turkish and English label, its ``messages`` predicate
  (:meth:`Kind.messages_predicate`) and its typed tables (:attr:`Kind.tables`; ``mt_event``
  holds several kinds, told apart by ``mt_event.event``: :meth:`Kind.table_predicate`);
* :func:`kind_of_row` — the kind of a typed row from its table (and ``mt_event.event``);
* :data:`CLIENT_IP` — the client address column of each typed table: the column ``srcip:``,
  ``vlan:``, ``department:`` and the summary table's /24 all read, so filters and facets agree.

A line whose ``(type, subtype)`` is none of the known ones is of kind ``other``; a test runs every
format fixture through the parsers and fails when one maps to ``other`` (a new parser type must
be added here) or when a typed row's table disagrees with its ``messages`` row.

``type`` and ``subtype`` are compared exactly as the parsers write them (the FortiGate ``type``
field as sent, the MikroTik families in lower case).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, and_, false, func, not_

from arcilogger_db.models import TIME_SERIES_TABLES

Family = Literal["fortigate", "mikrotik", "generic"]

#: Typed tables in branch-rank order (``models.TIME_SERIES_TABLES`` without ``messages``).
TYPED_TABLES: Final[tuple[str, ...]] = TIME_SERIES_TABLES[1:]

#: The client address column of each typed table (``mt_interface``, ``mt_config``: none).
CLIENT_IP: Final[Mapping[str, str]] = {
    "fg_traffic": "srcip",
    "fg_web": "srcip",
    "fg_dns": "srcip",
    "fg_app": "srcip",
    "fg_ips": "srcip",
    "fg_virus": "srcip",
    "fg_event": "srcip",
    "mt_dhcp": "ip",
    "mt_arp": "ip",
    "mt_fw": "src",
    "mt_account": "ip",
    "mt_event": "ip",
    "fg_dhcp": "ip",
}

#: Typed tables with an ``action`` column (the summary table's ``action``).
ACTION_TABLES: Final[frozenset[str]] = frozenset(
    {
        "fg_traffic",
        "fg_web",
        "fg_dns",
        "fg_app",
        "fg_ips",
        "fg_virus",
        "fg_event",
        "mt_dhcp",
        "mt_account",
        "mt_config",
    }
)


@dataclass(frozen=True, slots=True)
class KindTable:
    """A typed table holding records of a kind."""

    table: str
    #: The ``mt_event.event`` values of the kind; ``None``: every row of the table.
    events: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Kind:
    """One kind of record (``type:<key>``)."""

    key: str
    label_tr: str
    label_en: str
    #: ``messages.type`` of the kind; ``None`` for ``other`` (none of the known types).
    type: str | None
    #: ``messages.subtype`` values of the kind; ``None``: any subtype (``utm``: any subtype not
    #: taken by one of the FortiGate UTM kinds, ``NULL`` included).
    subtypes: tuple[str, ...] | None = None
    tables: tuple[KindTable, ...] = ()
    #: The kind of device that writes it (Kaynaklar suggests a source type from it).
    family: Family | None = None

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(entry.table for entry in self.tables)

    def messages_predicate(self, columns: Any) -> ColumnElement[bool]:
        """The kind as a condition on ``messages`` (``columns``: its ``.c``, or an alias's)."""
        type_: ColumnElement[str] = columns.type
        subtype: ColumnElement[str | None] = columns.subtype
        if self.type is None:
            return not_(type_.in_(sorted(KNOWN_TYPES)))
        condition = type_ == self.type
        if self.subtypes is not None:
            return and_(condition, subtype.in_(self.subtypes))
        if self.type == "utm":
            taken = subtype.in_(sorted(UTM_SUBTYPES))
            return and_(condition, not_(func.coalesce(taken, false())))
        return condition

    def table_predicate(self, table: str, columns: Any) -> ColumnElement[bool] | None:
        """The kind's condition on its typed ``table`` (``None``: every row); ``ValueError`` for
        a table that holds no records of the kind."""
        for entry in self.tables:
            if entry.table == table:
                return None if entry.events is None else columns.event.in_(entry.events)
        raise ValueError(f"kind {self.key!r} has no rows in {table!r}")


def _kind(
    key: str,
    label_tr: str,
    label_en: str,
    type_: str | None,
    *tables: KindTable,
    subtypes: tuple[str, ...] | None = None,
    family: Family | None = None,
) -> Kind:
    return Kind(key, label_tr, label_en, type_, subtypes, tables, family)


#: ``mt_event.event`` → kind (the other typed tables hold one kind each: :data:`TABLE_KINDS`).
MT_EVENT_KINDS: Final[Mapping[str, str]] = {
    "dhcp_offering": "dhcp",
    "dhcp_pool_empty": "dhcp",
    "dhcp_failed": "dhcp",
    "wifi_connect": "wifi",
    "wifi_disconnect": "wifi",
    "heartbeat": "heartbeat",
    "log": "log",
}


def _events(kind: str) -> tuple[str, ...]:
    return tuple(event for event, owner in MT_EVENT_KINDS.items() if owner == kind)


#: Every kind, in the order the web interface lists them (contract 2.3).
KINDS: Final[Mapping[str, Kind]] = {
    kind.key: kind
    for kind in (
        _kind(
            "traffic", "Trafik", "Traffic", "traffic", KindTable("fg_traffic"), family="fortigate"
        ),
        _kind(
            "web",
            "Web filtresi",
            "Web filter",
            "utm",
            KindTable("fg_web"),
            subtypes=("webfilter",),
            family="fortigate",
        ),
        _kind(
            "dns", "DNS", "DNS", "utm", KindTable("fg_dns"), subtypes=("dns",), family="fortigate"
        ),
        _kind(
            "app",
            "Uygulama ve SSL",
            "Application and SSL",
            "utm",
            KindTable("fg_app"),
            subtypes=("app-ctrl", "ssl"),
            family="fortigate",
        ),
        _kind(
            "ips",
            "Saldırı önleme",
            "Intrusion prevention",
            "utm",
            KindTable("fg_ips"),
            subtypes=("ips",),
            family="fortigate",
        ),
        _kind(
            "virus",
            "Virüs",
            "Antivirus",
            "utm",
            KindTable("fg_virus"),
            subtypes=("virus",),
            family="fortigate",
        ),
        _kind("utm", "Diğer UTM", "Other UTM", "utm", family="fortigate"),
        # FortiGate DHCP server lines are events (messages type event/system): their typed
        # table fg_dhcp belongs here, not to the MikroTik kind dhcp (ADR-0016, contract 2.4)
        _kind(
            "event",
            "FortiGate olayı",
            "FortiGate event",
            "event",
            KindTable("fg_event"),
            KindTable("fg_dhcp"),
            family="fortigate",
        ),
        _kind(
            "dhcp",
            "DHCP (MikroTik)",
            "DHCP (MikroTik)",
            "dhcp",
            KindTable("mt_dhcp"),
            KindTable("mt_event", _events("dhcp")),
            family="mikrotik",
        ),
        _kind("arp", "ARP", "ARP", "arp", KindTable("mt_arp"), family="mikrotik"),
        _kind(
            "fw",
            "MikroTik güvenlik duvarı",
            "MikroTik firewall",
            "fw",
            KindTable("mt_fw"),
            family="mikrotik",
        ),
        _kind(
            "account",
            "Oturum açma",
            "Logins",
            "account",
            KindTable("mt_account"),
            family="mikrotik",
        ),
        _kind(
            "interface",
            "Arayüz",
            "Interface",
            "interface",
            KindTable("mt_interface"),
            family="mikrotik",
        ),
        _kind(
            "config",
            "Yapılandırma",
            "Configuration",
            "config",
            KindTable("mt_config"),
            family="mikrotik",
        ),
        _kind(
            "wifi",
            "Kablosuz",
            "Wireless",
            "wifi",
            KindTable("mt_event", _events("wifi")),
            family="mikrotik",
        ),
        _kind(
            "heartbeat",
            "Heartbeat",
            "Heartbeat",
            "heartbeat",
            KindTable("mt_event", _events("heartbeat")),
            family="mikrotik",
        ),
        _kind(
            "log",
            "MikroTik günlüğü",
            "MikroTik log",
            "log",
            KindTable("mt_event", _events("log")),
            family="mikrotik",
        ),
        _kind("syslog", "Genel syslog", "Generic syslog", "syslog", family="generic"),
        _kind("unknown", "Tanınmayan", "Unrecognised", "unknown"),
        _kind("other", "Diğer", "Other", None),
    )
}

#: The ``messages.type`` values of the named kinds (``other`` is every other type).
KNOWN_TYPES: Final[frozenset[str]] = frozenset(
    kind.type for kind in KINDS.values() if kind.type is not None
)
#: FortiGate UTM subtypes that have a kind (and a typed table) of their own.
UTM_SUBTYPES: Final[Mapping[str, str]] = {
    subtype: kind.key
    for kind in KINDS.values()
    if kind.type == "utm" and kind.subtypes is not None
    for subtype in kind.subtypes
}
_BY_TYPE: Final[Mapping[str, str]] = {
    kind.type: kind.key for kind in KINDS.values() if kind.type is not None and kind.type != "utm"
}

#: Typed tables that hold records of exactly one kind (``mt_event``: :data:`MT_EVENT_KINDS`).
TABLE_KINDS: Final[Mapping[str, str]] = {
    entry.table: kind.key
    for kind in KINDS.values()
    for entry in kind.tables
    if entry.events is None
}


_ASCII_LOWER: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def ascii_lower(text: str) -> str:
    """``text`` lower-cased the way PostgreSQL's ``lower()`` does in the installer's locale C:
    ASCII letters only (``İ``, ``I`` with dot, ``Ş`` … stay as they are), so a Python comparison
    and its SQL twin agree."""
    return text.translate(_ASCII_LOWER)


def kind_of(type_: str, subtype: str | None) -> str:
    """The kind of a ``messages`` row (``other`` for an unknown ``type``)."""
    if type_ == "utm":
        return UTM_SUBTYPES.get(subtype or "", "utm")
    return _BY_TYPE.get(type_, "other")


def kind_of_row(table: str, event: str | None = None) -> str | None:
    """The kind of a typed row of ``table`` (``event``: its ``mt_event.event``); ``None`` for an
    ``mt_event`` row whose event no kind names."""
    if table == "mt_event":
        return MT_EVENT_KINDS.get(event or "")
    return TABLE_KINDS.get(table)


def tables_of(kinds: Iterable[str]) -> tuple[str, ...]:
    """The typed tables holding records of ``kinds``, in branch-rank order."""
    wanted = {entry.table for key in kinds for entry in KINDS[key].tables}
    return tuple(table for table in TYPED_TABLES if table in wanted)


def mt_event_events(kinds: Iterable[str]) -> tuple[str, ...]:
    """The ``mt_event.event`` values of ``kinds`` (in :data:`MT_EVENT_KINDS` order)."""
    wanted = set(kinds)
    return tuple(event for event, kind in MT_EVENT_KINDS.items() if kind in wanted)


def family_of(kinds: Iterable[str]) -> Family | None:
    """The one device family of ``kinds``; ``None`` when they are of none or of several."""
    families = {KINDS[key].family for key in kinds} - {None}
    return families.pop() if len(families) == 1 else None
