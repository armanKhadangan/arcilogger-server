"""The field catalogue of the query language: the one list of what can be searched, and how.

Every field of ``q`` (``srcip:``, ``domain:`` …, contract 2.4) is a :class:`Field` of
:data:`FIELDS`: its canonical name and aliases (Turkish ones in ASCII; the parser folds case and
Turkish letters, so ``Kaynak:``, ``KAYNAKİP:`` and ``kaynakıp:`` work), its kind of value
(:data:`FORMS`), the columns it reads in each table (built from ``Base.metadata``; a test checks
every one exists), the tables where it alone is index-backed (a test checks each claim against
the indexes of ``arcilogger_db.models``) and its Turkish and English label, help and examples
(``GET /search/fields``). Where a field is searched follows from its **scope**:

* ``typed`` — a column of the typed tables (``fg_*``, ``mt_*``): a query with one of them is
  driven by the typed tables that have **every** typed field of it (:func:`branch_tables`);
* ``messages`` — a column of ``messages`` only (free text, ``host``, ``severity`` …): joined by
  the raw reference in a typed query;
* ``every`` — ``source``, ``sender`` (a column of every table) and ``type`` (the kinds of
  :mod:`arcilogger_db.kinds`, which choose tables and the ``messages`` rows).

This module also parses one value of a field into its value object (:func:`parse_value`; the
parser calls it, so a value is checked where it is typed) and binds the site's own data a value
names: the VLANs and departments of ``vlan:``/``department:`` and the configured network
sources of ``source:`` (:class:`SiteNetwork`). Values are compared with ``kinds.ascii_lower``
where the SQL uses ``lower()``/``ILIKE`` (locale C: ASCII letters only).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from sqlalchemy import Column, select

from arcilogger_api.search.messages import SearchQueryError
from arcilogger_api.textfold import fold
from arcilogger_db.kinds import CLIENT_IP, KINDS, TYPED_TABLES, ascii_lower, tables_of
from arcilogger_db.models import TIME_SERIES_TABLES, Base, Department, Vlan

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from arcilogger_core.config import Config

Scope = Literal["typed", "messages", "every"]
#: How a field's values are written and compared (``search/messages.py`` ``FORMATS`` explains
#: each to the user).
Form = Literal[
    "ip",
    "int",
    "port",
    "proto",
    "mac",
    "domain",
    "name",
    "token",
    "text",
    "url",
    "source",
    "kind",
    "severity",
    "error",
    "policy",
    "vlan",
    "department",
    "freetext",
]
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Smallest free-text term, and the largest value (characters).
MIN_TERM_CHARS: Final = 3
MAX_VALUE_CHARS: Final = 256
MAX_PORT: Final = 65535
MAX_BIGINT: Final = 2**63 - 1
#: FortiGate stores the protocol number; MikroTik ``mt_fw.proto`` the name in capitals.
PROTOCOLS: Final[Mapping[str, int]] = {"icmp": 1, "tcp": 6, "udp": 17}
PROTOCOL_NAMES: Final[Mapping[int, str]] = {number: name for name, number in PROTOCOLS.items()}
#: Syslog severities (RFC 5424) by name; lower numbers are more severe.
SEVERITIES: Final[Mapping[str, int]] = {
    "emerg": 0,
    "emergency": 0,
    "alert": 1,
    "crit": 2,
    "critical": 2,
    "err": 3,
    "error": 3,
    "warning": 4,
    "warn": 4,
    "notice": 5,
    "info": 6,
    "informational": 6,
    "debug": 7,
}
SEVERITY_NAMES: Final = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


# =================================================================================================
# Values
# =================================================================================================

#: A bare value may hold these only inside quotes.
_NEEDS_QUOTES: Final = re.compile(r'[\s",()]')


def quote(text: str) -> str:
    """``text`` as a quoted value (``\\`` and ``"`` escaped)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bare_or_quoted(text: str, *, literal_star: bool = False) -> str:
    """``text`` as written in a normalised query: bare when it can be read back unchanged."""
    if not text or _NEEDS_QUOTES.search(text) or (literal_star and "*" in text):
        return quote(text)
    return text


@dataclass(frozen=True, slots=True)
class Present:
    """``field:*``: the field has a value (``IS NOT NULL``)."""

    def render(self) -> str:
        return "*"


@dataclass(frozen=True, slots=True)
class IpValue:
    """An address (``exact``) or a network the column must lie in (``<<=``)."""

    network: IpNetwork
    exact: bool

    def render(self) -> str:
        return str(self.network.network_address) if self.exact else str(self.network)


@dataclass(frozen=True, slots=True)
class IntValue:
    """``low <= value <= high`` (``None``: open)."""

    low: int | None
    high: int | None

    def render(self) -> str:
        if self.low is not None and self.low == self.high:
            return str(self.low)
        if self.high is None:
            return f">={self.low}"
        if self.low is None:
            return f"<={self.high}"
        return f"{self.low}-{self.high}"


@dataclass(frozen=True, slots=True)
class ProtoValue:
    number: int

    @property
    def name(self) -> str:
        """The MikroTik spelling (``TCP``), the number for another protocol."""
        return PROTOCOL_NAMES.get(self.number, str(self.number)).upper()

    def render(self) -> str:
        return PROTOCOL_NAMES.get(self.number, str(self.number))


@dataclass(frozen=True, slots=True)
class MacValue:
    """``low <= mac <= high``: one address, or every address of a vendor prefix."""

    low: str
    high: str
    #: Octets given for a prefix (``00:00:5e:*``: 3); 6 for one address.
    octets: int = 6

    def render(self) -> str:
        if self.octets == 6:
            return self.low
        return self.low[: 3 * self.octets - 1] + ":*"


@dataclass(frozen=True, slots=True)
class DomainValue:
    """``example.com``: the name and its subdomains; ``*.example.com``: subdomains only."""

    name: str
    subdomains_only: bool = False

    def render(self) -> str:
        return f"*.{self.name}" if self.subdomains_only else self.name

    def matches(self, value: str) -> bool:
        host = ascii_lower(value)
        return host.endswith("." + self.name) or (not self.subdomains_only and host == self.name)


@dataclass(frozen=True, slots=True)
class NameValue:
    """An exact DNS name (``qname:``, compared as stored: index-backed)."""

    name: str

    def render(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class TokenValue:
    """One value compared exactly after lower-casing both sides (``action``, ``qtype`` …)."""

    token: str

    def render(self) -> str:
        return bare_or_quoted(self.token, literal_star=True)


@dataclass(frozen=True, slots=True)
class PatternValue:
    """Text compared ignoring ASCII case: exact, ``foo*`` (starts), ``*foo`` (ends) or ``*foo*``
    (contains). A quoted value is literal (``"a*"`` is the text ``a*``)."""

    core: str
    starts: bool = False
    ends: bool = False
    #: A ``policy:`` name: one of digits only is written quoted (bare digits are the number).
    quote_digits: bool = field(default=False, compare=False)

    @property
    def exact(self) -> bool:
        return not (self.starts or self.ends)

    def render(self) -> str:
        if self.exact:
            if self.quote_digits and self.core.isascii() and self.core.isdigit():
                return quote(self.core)
            return bare_or_quoted(self.core, literal_star=True)
        # A wildcard value was bare: it holds nothing that needs quotes and no other "*".
        return ("*" if self.ends else "") + self.core + ("*" if self.starts else "")

    def matches(self, value: str) -> bool:
        text, core = ascii_lower(value), ascii_lower(self.core)
        if self.starts and self.ends:
            return core in text
        if self.starts:
            return text.startswith(core)
        if self.ends:
            return text.endswith(core)
        return text == core


@dataclass(frozen=True, slots=True)
class ContainsValue:
    """Text the column contains, ignoring ASCII case (``url:``, free text)."""

    term: str

    def render(self) -> str:
        return bare_or_quoted(self.term, literal_star=True)

    def matches(self, value: str) -> bool:
        return ascii_lower(self.term) in ascii_lower(value)


@dataclass(frozen=True, slots=True)
class KindValue:
    kind: str

    def render(self) -> str:
        return self.kind


@dataclass(frozen=True, slots=True)
class SeverityValue:
    """``low <= severity <= high`` on the syslog scale 0 (emerg) … 7 (debug)."""

    low: int
    high: int

    def render(self) -> str:
        if self.low == self.high:
            return SEVERITY_NAMES[self.low]
        if self.low == 0:
            return f"<={SEVERITY_NAMES[self.high]}"
        if self.high == 7:
            return f">={SEVERITY_NAMES[self.low]}"
        return f"{self.low}-{self.high}"


@dataclass(frozen=True, slots=True)
class ErrorValue:
    """A parse error pattern name, or (``prefix``) every name that starts with it."""

    name: str
    prefix: bool = False

    def render(self) -> str:
        return self.name + ("*" if self.prefix else "")


@dataclass(frozen=True, slots=True)
class VlanValue:
    vlan_id: int

    def render(self) -> str:
        return str(self.vlan_id)


@dataclass(frozen=True, slots=True)
class DepartmentValue:
    """A department by name (compared folded, :func:`arcilogger_api.textfold.fold`) or id."""

    text: str

    @property
    def department_id(self) -> int | None:
        return int(self.text) if self.text.isascii() and self.text.isdigit() else None

    def render(self) -> str:
        return bare_or_quoted(self.text, literal_star=True)


Value = (
    Present
    | IpValue
    | IntValue
    | ProtoValue
    | MacValue
    | DomainValue
    | NameValue
    | TokenValue
    | PatternValue
    | ContainsValue
    | KindValue
    | SeverityValue
    | ErrorValue
    | VlanValue
    | DepartmentValue
)


# =================================================================================================
# The catalogue
# =================================================================================================


@dataclass(frozen=True, slots=True)
class Field:
    """One field of the query language (contract 2.4)."""

    name: str
    aliases: tuple[str, ...]
    form: Form
    scope: Scope
    #: Table → the columns the field reads there (OR of them); ``type`` reads no column of most
    #: typed tables (the table itself is the kind) and ``mt_event.event``.
    columns: Mapping[str, tuple[str, ...]]
    #: Tables where the field alone is index-backed (every column of it leads an index).
    indexed: tuple[str, ...]
    label_tr: str
    label_en: str
    help_tr: str
    help_en: str
    examples: tuple[str, ...] = ()
    #: Inclusive bounds of an integer field.
    int_bounds: tuple[int, int] = (0, MAX_BIGINT)

    @property
    def tables(self) -> tuple[str, ...]:
        """The tables the field reads, in branch-rank order (``messages`` first)."""
        return tuple(table for table in TIME_SERIES_TABLES if table in self.columns)

    def column_objects(self, table: str) -> tuple[Column[Any], ...]:
        """The field's columns in ``table`` as SQLAlchemy column objects."""
        found = Base.metadata.tables[table]
        return tuple(found.c[name] for name in self.columns[table])

    def to_dict(self) -> dict[str, Any]:
        """The entry of ``GET /search/fields``."""
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "kind": self.form,
            "label_tr": self.label_tr,
            "label_en": self.label_en,
            "help_tr": self.help_tr,
            "help_en": self.help_en,
            "tables": list(self.tables),
            "indexed": list(self.indexed),
            "examples": list(self.examples),
        }


_FG_ALL: Final = ("fg_traffic", "fg_web", "fg_dns", "fg_app", "fg_ips", "fg_virus", "fg_event")
#: FortiGate tables with source and destination ports, protocol-less ones included.
_FG_PORTS: Final = ("fg_traffic", "fg_web", "fg_dns", "fg_app", "fg_ips", "fg_virus")


def _on(tables: Iterable[str], *columns: str) -> dict[str, tuple[str, ...]]:
    return dict.fromkeys(tables, columns)


def _address_columns() -> dict[str, tuple[str, ...]]:
    """``ip:``: per table its client, destination and NAT address columns."""
    columns: dict[str, tuple[str, ...]] = {}
    for table in TYPED_TABLES:
        found = Base.metadata.tables[table].c
        names = [CLIENT_IP[table]] if table in CLIENT_IP else []
        names += [name for name in ("dstip", "dst", "transip") if name in found]
        if names:
            columns[table] = tuple(names)
    return columns


_CLIENT: Final = {table: (column,) for table, column in CLIENT_IP.items()}
_CLIENT_INDEXED: Final = tuple(t for t in CLIENT_IP if t not in ("fg_event", "mt_event"))


def _field(
    name: str,
    aliases: tuple[str, ...],
    form: Form,
    scope: Scope,
    columns: Mapping[str, tuple[str, ...]],
    indexed: tuple[str, ...],
    texts: tuple[str, str, str, str],
    examples: tuple[str, ...] = (),
    int_bounds: tuple[int, int] = (0, MAX_BIGINT),
) -> Field:
    return Field(name, aliases, form, scope, columns, indexed, *texts, examples, int_bounds)


_PORT: Final = (0, MAX_PORT)

#: Every field, in the order of the syntax help.
FIELDS: Final[Mapping[str, Field]] = {
    entry.name: entry
    for entry in (
        _field(
            "source",
            ("kaynak",),
            "source",
            "every",
            _on(TIME_SERIES_TABLES, "source"),
            ("messages", "mt_interface", "mt_config", "mt_event"),
            (
                "Kaynak",
                "Source",
                "Log kaynağının adı (spool klasörü); sonunda * olabilir. Ağ olarak tanımlı bir "
                "kaynak (ör. ap) üyelerini de bulur (ap-<ip>).",
                "The log source's name (spool folder), with * at its end if needed. A source "
                "defined as a network (e.g. ap) also finds its members (ap-<ip>).",
            ),
            ("source:fortigate", "source:core-*"),
        ),
        _field(
            "sender",
            ("gonderen",),
            "ip",
            "every",
            _on(TIME_SERIES_TABLES, "sender"),
            (),
            (
                "Gönderici IP",
                "Sender IP",
                "Satırı gönderen cihazın adresi (bilinmeyen göndericiler dahil).",
                "The address of the device that sent the line (unknown senders included).",
            ),
            ("sender:172.20.10.2",),
        ),
        _field(
            "type",
            ("tur", "tip"),
            "kind",
            "every",
            {"messages": ("type",), **_on(TYPED_TABLES), "mt_event": ("event",)},
            ("messages", "mt_event"),
            (
                "Kayıt türü",
                "Record type",
                "Kaydın türü (trafik, web filtresi, DHCP …); türler alan listesindedir.",
                "The kind of record (traffic, web filter, DHCP …); the types are in the field "
                "list.",
            ),
            ("type:web", "type:dhcp,arp", "-type:heartbeat"),
        ),
        _field(
            "subtype",
            ("alttur",),
            "text",
            "messages",
            {"messages": ("subtype",)},
            (),
            (
                "Alt tür",
                "Subtype",
                "Cihazın gönderdiği alt tür (ör. FortiGate forward, local; MikroTik login).",
                "The subtype the device sent (e.g. FortiGate forward, local; MikroTik login).",
            ),
            ("subtype:forward",),
        ),
        _field(
            "host",
            ("cihaz",),
            "text",
            "messages",
            {"messages": ("host",)},
            (),
            (
                "Cihaz adı",
                "Host name",
                "Syslog başlığındaki ya da FortiGate devname alanındaki cihaz adı.",
                "The host name of the syslog header or the FortiGate devname field.",
            ),
            ("host:core-r1",),
        ),
        _field(
            "program",
            ("konu",),
            "text",
            "messages",
            {"messages": ("program",)},
            (),
            (
                "Program / konu",
                "Program / topics",
                "Syslog programı ya da MikroTik konuları (ör. dhcp,info).",
                "The syslog program or the MikroTik topics (e.g. dhcp,info).",
            ),
            ("program:dhcp*",),
        ),
        _field(
            "severity",
            ("onem",),
            "severity",
            "messages",
            {"messages": ("severity",)},
            (),
            (
                "Önem",
                "Severity",
                "Syslog önemi: emerg … debug ya da 0-7; karşılaştırma yazılabilir (<=err: hata "
                "ve daha önemli).",
                "The syslog severity: emerg … debug or 0-7; comparisons work (<=err: errors and "
                "more severe).",
            ),
            ("severity:<=err", "severity:warning"),
        ),
        _field(
            "error",
            ("hata",),
            "error",
            "messages",
            {"messages": ("parse_error",)},
            (),
            (
                "Ayrıştırma hatası",
                "Parse error",
                "Satırın ayrıştırılamayan kalıbı; sonunda * olabilir, * tek başına her hatayı "
                "bulur.",
                "The pattern a line could not be parsed with, with * at its end if needed; * "
                "alone finds every error.",
            ),
            ("error:fortigate.*", "error:*"),
        ),
        _field(
            "srcip",
            ("kaynakip", "istemci"),
            "ip",
            "typed",
            _CLIENT,
            _CLIENT_INDEXED,
            (
                "Kaynak IP (istemci)",
                "Source IP (client)",
                "İstemcinin adresi ya da ağı; en hızlı alan budur.",
                "The client's address or network; the fastest field.",
            ),
            ("srcip:172.20.41.105", "srcip:172.20.41.0/24", "srcip:172.20.*"),
        ),
        _field(
            "dstip",
            ("hedefip",),
            "ip",
            "typed",
            {**_on(_FG_ALL, "dstip"), "mt_fw": ("dst",)},
            ("fg_traffic", "mt_fw"),
            (
                "Hedef IP",
                "Destination IP",
                "Bağlanılan adres ya da ağ.",
                "The address or network connected to.",
            ),
            ("dstip:203.0.113.10",),
        ),
        _field(
            "natip",
            ("transip",),
            "ip",
            "typed",
            {"fg_traffic": ("transip",)},
            ("fg_traffic",),
            (
                "NAT IP",
                "NAT IP",
                "FortiGate'in kaynak NAT sonrası adresi (transip).",
                "The FortiGate's address after source NAT (transip).",
            ),
            ("natip:198.51.100.7",),
        ),
        _field(
            "ip",
            ("adres",),
            "ip",
            "typed",
            _address_columns(),
            ("fg_traffic", "mt_fw", "mt_dhcp", "mt_arp", "mt_account", "fg_dhcp"),
            (
                "Herhangi bir IP",
                "Any IP",
                "Kaynak, hedef ya da NAT adreslerinden biri; UTM kayıtlarında taranır, istemci "
                "için srcip: daha hızlıdır.",
                "Any of the source, destination or NAT addresses; scanned in UTM records, srcip: "
                "is faster for a client.",
            ),
            ("ip:203.0.113.10",),
        ),
        _field(
            "srcport",
            ("kaynakport",),
            "port",
            "typed",
            {**_on(_FG_PORTS, "srcport"), "mt_fw": ("sport",)},
            (),
            ("Kaynak port", "Source port", "İstemcinin portu.", "The client's port."),
            ("srcport:>=49152",),
            _PORT,
        ),
        _field(
            "dstport",
            ("hedefport",),
            "port",
            "typed",
            {**_on(_FG_PORTS, "dstport"), "mt_fw": ("dport",)},
            (),
            (
                "Hedef port",
                "Destination port",
                "Bağlanılan port; dstip: ile birlikte indeksten okunur.",
                "The port connected to; read from an index together with dstip:.",
            ),
            ("dstport:80,443", "dstport:1000-2000"),
            _PORT,
        ),
        _field(
            "natport",
            ("transport",),
            "port",
            "typed",
            {"fg_traffic": ("transport",)},
            (),
            (
                "NAT port",
                "NAT port",
                "Kaynak NAT sonrası port; natip: ile birlikte indeksten okunur.",
                "The port after source NAT; read from an index together with natip:.",
            ),
            ("natport:40123",),
            _PORT,
        ),
        _field(
            "port",
            (),
            "port",
            "typed",
            {**_on(_FG_PORTS, "srcport", "dstport"), "mt_fw": ("sport", "dport")},
            (),
            (
                "Herhangi bir port",
                "Any port",
                "Kaynak ya da hedef port.",
                "The source or the destination port.",
            ),
            ("port:3389",),
            _PORT,
        ),
        _field(
            "proto",
            ("protokol",),
            "proto",
            "typed",
            {**_on(("fg_traffic", "fg_app", "fg_ips", "fg_virus"), "proto"), "mt_fw": ("proto",)},
            (),
            (
                "Protokol",
                "Protocol",
                "tcp, udp, icmp ya da protokol numarası.",
                "tcp, udp, icmp or the protocol number.",
            ),
            ("proto:udp",),
            (0, 255),
        ),
        _field(
            "action",
            ("eylem",),
            "token",
            "typed",
            {
                **_on(_FG_ALL, "action"),
                **_on(("mt_dhcp", "mt_account", "mt_config"), "action"),
            },
            (),
            (
                "Eylem",
                "Action",
                "Cihazın eylemi (deny, blocked, passthrough, assigned, login …). -action:accept "
                "eylemi accept olmayan kayıtları bulur (eylemi boş olanlar dahil).",
                "The device's action (deny, blocked, passthrough, assigned, login …). "
                "-action:accept finds records whose action is not accept, including those "
                "without one.",
            ),
            ("action:deny,blocked", "-action:passthrough"),
        ),
        _field(
            "domain",
            ("alanadi", "site"),
            "domain",
            "typed",
            {
                "fg_web": ("hostname",),
                "fg_dns": ("qname",),
                "fg_app": ("hostname",),
                "fg_virus": ("hostname",),
            },
            ("fg_web",),
            (
                "Alan adı",
                "Domain",
                "Alan adı ve alt alan adları; *.example.com yalnızca alt alan adlarını bulur.",
                "The domain and its subdomains; *.example.com finds only the subdomains.",
            ),
            ("domain:example.com", "domain:*.example.com"),
        ),
        _field(
            "qname",
            (),
            "name",
            "typed",
            {"fg_dns": ("qname",)},
            ("fg_dns",),
            (
                "DNS sorgu adı",
                "DNS query name",
                "DNS'te sorulan tam ad (indeksten okunur).",
                "The exact name asked in DNS (read from an index).",
            ),
            ("qname:www.example.com",),
        ),
        _field(
            "url",
            (),
            "url",
            "typed",
            _on(("fg_web", "fg_app", "fg_virus"), "url"),
            (),
            ("URL", "URL", "URL'nin içerdiği metin.", "Text the URL contains."),
            ("url:login",),
        ),
        _field(
            "app",
            ("uygulama",),
            "text",
            "typed",
            _on(("fg_traffic", "fg_app"), "app"),
            (),
            ("Uygulama", "Application", "FortiGate uygulama adı.", "The FortiGate application."),
            ("app:YouTube",),
        ),
        _field(
            "appcat",
            (),
            "text",
            "typed",
            _on(("fg_traffic", "fg_app"), "appcat"),
            (),
            (
                "Uygulama kategorisi",
                "Application category",
                "FortiGate uygulama kategorisi.",
                "The FortiGate application category.",
            ),
            ("appcat:Video/Audio",),
        ),
        _field(
            "category",
            ("kategori",),
            "text",
            "typed",
            _on(("fg_web", "fg_dns"), "catdesc"),
            (),
            (
                "Web kategorisi",
                "Web category",
                "FortiGuard kategori adı (web ve DNS filtresi).",
                "The FortiGuard category name (web and DNS filter).",
            ),
            ("category:*Social*",),
        ),
        _field(
            "service",
            ("servis",),
            "text",
            "typed",
            _on(("fg_traffic", "fg_ips", "fg_virus"), "service"),
            (),
            ("Servis", "Service", "FortiGate servis adı (HTTPS, DNS …).", "The FortiGate service."),
            ("service:HTTPS",),
        ),
        _field(
            "country",
            ("ulke",),
            "text",
            "typed",
            {"fg_traffic": ("dstcountry",)},
            (),
            ("Hedef ülke", "Destination country", "Hedefin ülkesi.", "The destination's country."),
            ("country:Reserved",),
        ),
        _field(
            "policy",
            ("politika",),
            "policy",
            "typed",
            {"fg_traffic": ("policyid", "policyname")},
            (),
            (
                "Politika",
                "Policy",
                "FortiGate politika numarası ya da adı.",
                "The FortiGate policy number or name.",
            ),
            ("policy:12", "policy:Internet*"),
        ),
        _field(
            "sessionid",
            ("oturum",),
            "int",
            "typed",
            _on(_FG_PORTS, "sessionid"),
            ("fg_traffic", "fg_web", "fg_app"),
            (
                "Oturum numarası",
                "Session id",
                "FortiGate oturum numarası.",
                "The FortiGate session id.",
            ),
            ("sessionid:123456",),
        ),
        _field(
            "user",
            ("kullanici",),
            "text",
            "typed",
            _on(("fg_event", "mt_account", "mt_config"), "user_name"),
            (),
            (
                "Kullanıcı adı",
                "User name",
                "Cihaza giriş yapan ya da değişiklik yapan kullanıcı.",
                "The user who signed in to a device or changed it.",
            ),
            ("user:admin",),
        ),
        _field(
            "mac",
            (),
            "mac",
            "typed",
            # fg_traffic.srcmac is deliberately not here: a mac: search never scans the largest
            # table (the field srcmac: does, ADR-0016)
            {
                "mt_dhcp": ("mac",),
                "mt_arp": ("mac",),
                "mt_fw": ("src_mac",),
                "mt_event": ("mac",),
                "fg_dhcp": ("mac",),
            },
            ("mt_dhcp", "mt_arp", "fg_dhcp"),
            (
                "MAC adresi",
                "MAC address",
                "Cihazın MAC adresi (her ayraçla) ya da üretici öneki.",
                "The device's MAC address (any separator) or a vendor prefix.",
            ),
            ("mac:00:00:5e:00:53:21", "mac:00:00:5e:*"),
        ),
        _field(
            "srcmac",
            ("kaynakmac",),
            "mac",
            "typed",
            {"fg_traffic": ("srcmac",)},
            (),
            (
                "Kaynak MAC (FortiGate oturumu)",
                "Source MAC (FortiGate session)",
                "FortiGate oturum kaydındaki kaynak MAC adresi (yalnızca aygıt tanıma açık "
                "arayüzlerde dolu; dizinsiz, zaman dilimleriyle taranır).",
                "Source MAC of the FortiGate session record (filled only on interfaces with "
                "device detection; not indexed, scanned in time slices).",
            ),
            ("srcmac:00:00:5e:00:53:57",),
        ),
        _field(
            "vlan",
            (),
            "vlan",
            "typed",
            _CLIENT,
            _CLIENT_INDEXED,
            (
                "VLAN",
                "VLAN",
                "İstemci adresinin VLAN'ı (Cihazlar ve Kişiler → VLAN'lar).",
                "The VLAN of the client address (Devices and persons → VLANs).",
            ),
            ("vlan:41", "vlan:41,42"),
        ),
        _field(
            "department",
            ("departman",),
            "department",
            "typed",
            _CLIENT,
            _CLIENT_INDEXED,
            (
                "Departman (VLAN'a göre)",
                "Department (by VLAN)",
                "İstemci adresinin VLAN'ına tanımlı varsayılan departman; kişi ataması değildir.",
                "The default department of the client address's VLAN, not the person's assignment.",
            ),
            ("department:Finans",),
        ),
        _field(
            "interface",
            ("arayuz",),
            "text",
            "typed",
            {
                "fg_traffic": ("srcintf", "dstintf"),
                "mt_arp": ("interface",),
                "mt_fw": ("in_if", "out_if"),
                "mt_interface": ("interface",),
                "mt_event": ("interface",),
                "fg_dhcp": ("interface",),
            },
            (),
            (
                "Arayüz",
                "Interface",
                "Gelen ya da giden arayüz.",
                "The incoming or the outgoing interface.",
            ),
            ("interface:ether1",),
        ),
        _field(
            "qtype",
            (),
            "token",
            "typed",
            {"fg_dns": ("qtype",)},
            (),
            (
                "DNS sorgu tipi",
                "DNS query type",
                "DNS sorgusunun tipi (A, AAAA, MX …).",
                "The type of the DNS query (A, AAAA, MX …).",
            ),
            ("qtype:aaaa",),
        ),
        _field(
            "attack",
            ("saldiri",),
            "text",
            "typed",
            {"fg_ips": ("attack",)},
            (),
            ("Saldırı adı", "Attack name", "IPS imzasının adı.", "The name of the IPS signature."),
            ("attack:*Shell*",),
        ),
        _field(
            "virus",
            (),
            "text",
            "typed",
            {"fg_virus": ("virus",)},
            (),
            ("Virüs adı", "Virus name", "Bulunan virüsün adı.", "The name of the virus found."),
            ("virus:EICAR*",),
        ),
        _field(
            "logid",
            (),
            "token",
            "typed",
            # fg_dhcp is not in _FG_ALL, which drives dstip: and action: too (ADR-0016)
            {**_on(_FG_ALL, "logid"), "fg_dhcp": ("logid",)},
            (),
            (
                "FortiGate log numarası",
                "FortiGate log id",
                "FortiGate logid alanı.",
                "The FortiGate logid field.",
            ),
            ("logid:0000000013",),
        ),
        _field(
            "level",
            (),
            "token",
            "typed",
            _on(("fg_event", "mt_event", "fg_dhcp"), "level"),
            (),
            (
                "Seviye",
                "Level",
                "Olayın seviyesi (FortiGate level, MikroTik konu seviyesi).",
                "The event's level (FortiGate level, MikroTik topic level).",
            ),
            ("level:warning",),
        ),
        _field(
            "text",
            ("metin",),
            "freetext",
            "messages",
            {"messages": ("text",)},
            (),
            (
                "Serbest metin",
                "Free text",
                "Kaydın ilk 2.000 karakterinde geçen metin (en az 3 karakter); alan adı "
                "yazmadan da aranır.",
                "Text in the first 2,000 characters of a record (at least 3 characters); also "
                "searched without a field name.",
            ),
            ('"login failure"', "text:youtube"),
        ),
    )
}

#: Folded alias or name → field name.
ALIASES: Final[Mapping[str, str]] = {
    key: entry.name for entry in FIELDS.values() for key in (entry.name, *entry.aliases)
}
_TURKISH_ASCII: Final = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def field_key(name: str) -> str:
    """A field name as typed, folded for lookup (``KAYNAKİP`` → ``kaynakip``, ``tür`` → ``tur``)."""
    return fold(name).translate(_TURKISH_ASCII)


def lookup_field(name: str) -> Field | None:
    """The field ``name`` (any case, Turkish letters folded, an alias) names, or ``None``."""
    found = ALIASES.get(field_key(name))
    return None if found is None else FIELDS[found]


def catalogue_fields() -> list[dict[str, Any]]:
    """``FieldCatalogue.fields``."""
    return [entry.to_dict() for entry in FIELDS.values()]


def catalogue_kinds() -> list[dict[str, str]]:
    """``FieldCatalogue.kinds``."""
    return [{"key": k.key, "label_tr": k.label_tr, "label_en": k.label_en} for k in KINDS.values()]


# =================================================================================================
# Parsing one value
# =================================================================================================

_IPV4_WILDCARD: Final = re.compile(r"^([0-9]{1,3}(?:\.[0-9]{1,3}){0,2})\.\*$")
_INT: Final = re.compile(
    r"^(?:([0-9]{1,19})|([0-9]{1,19})-([0-9]{1,19})|(>=|<=|>|<)([0-9]{1,19}))$"
)
_MAC_DIGITS: Final = re.compile(r"^[0-9a-f]{12}$")
_MAC_PREFIX: Final = re.compile(r"^([0-9a-f]{2}(?:[:-][0-9a-f]{2}){0,4})[:-]\*$")
_LABEL: Final = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
_ERROR_NAME: Final = re.compile(r"^[a-z0-9._-]+$")
_SEVERITY: Final = re.compile(r"^(>=|<=|>|<)?([a-z]+|[0-7])$")
#: Forms without ``field:*``: ``type`` names kinds, not a column; ``vlan:*``/``department:*``
#: would only test the client address a VLAN is looked up by, not that it lies in one.
_NOT_PRESENT: Final = frozenset({"kind", "vlan", "department"})


@dataclass(frozen=True, slots=True)
class RawValue:
    """One value as typed: its text (unescaped), whether it was quoted, and its span."""

    text: str
    quoted: bool
    start: int
    end: int


def _invalid(entry: Field, raw: RawValue, form: str | None = None) -> SearchQueryError:
    return SearchQueryError(
        "search_value_invalid",
        raw.start,
        raw.end,
        field=entry.name,
        value=raw.text,
        format=form or entry.form,
    )


def _ip(entry: Field, raw: RawValue) -> IpValue:
    text = raw.text.strip()
    wildcard = _IPV4_WILDCARD.match(text)
    try:
        if wildcard is not None:
            octets = wildcard.group(1).split(".")
            address = ".".join(octets + ["0"] * (4 - len(octets)))
            network = ipaddress.ip_network(f"{address}/{8 * len(octets)}", strict=True)
            return IpValue(network, exact=False)
        if "%" in text:
            raise ValueError("zone id")
        if "/" in text:
            network = ipaddress.ip_network(text, strict=False)
            return IpValue(network, exact=network.prefixlen == network.max_prefixlen)
        address_value = ipaddress.ip_address(text)
        return IpValue(ipaddress.ip_network(address_value), exact=True)
    except ValueError:
        raise _invalid(entry, raw) from None


def _int(entry: Field, raw: RawValue) -> IntValue:
    match = _INT.match(raw.text.strip())
    if match is None:
        raise _invalid(entry, raw)
    exact, low_text, high_text, operator, bound = match.groups()
    low: int | None
    high: int | None
    if exact is not None:
        low = high = int(exact)
    elif operator is not None:
        number = int(bound)
        low, high = {
            ">": (number + 1, None),
            ">=": (number, None),
            "<": (None, number - 1),
            "<=": (None, number),
        }[operator]
    else:
        low, high = int(low_text), int(high_text)
    floor, ceiling = entry.int_bounds
    lowest = floor if low is None else low
    highest = ceiling if high is None else high
    if lowest > highest or lowest < floor or highest > ceiling:
        raise _invalid(entry, raw)
    return IntValue(low, high)


def _proto(entry: Field, raw: RawValue) -> ProtoValue:
    text = ascii_lower(raw.text.strip())
    if text in PROTOCOLS:
        return ProtoValue(PROTOCOLS[text])
    if text.isascii() and text.isdigit() and int(text) <= 255:
        return ProtoValue(int(text))
    raise _invalid(entry, raw)


def mac_text(text: str) -> str | None:
    """``00-00-5E-00-53-21`` / ``0000.5e00.5321`` → ``00:00:5e:00:53:21``; ``None`` for no MAC."""
    digits = re.sub(r"[:.\-]", "", ascii_lower(text.strip()))
    if not _MAC_DIGITS.match(digits):
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def _mac(entry: Field, raw: RawValue) -> MacValue:
    prefix = _MAC_PREFIX.match(ascii_lower(raw.text.strip()))
    if prefix is not None:
        octets = re.split(r"[:-]", prefix.group(1))
        count = len(octets)
        low = ":".join(octets + ["00"] * (6 - count))
        high = ":".join(octets + ["ff"] * (6 - count))
        return MacValue(low, high, count)
    mac = mac_text(raw.text)
    if mac is None:
        raise _invalid(entry, raw)
    return MacValue(mac, mac)


def dns_name(text: str) -> str | None:
    """A host name as stored by the devices: lower case, outer dots removed, an international
    name in punycode (``idna`` codec); ``None`` when it is no host name."""
    name = text.strip().strip(".")
    if not name:
        return None
    if not name.isascii():
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    name = ascii_lower(name)
    if len(name) > 253 or not all(_LABEL.match(label) for label in name.split(".")):
        return None
    return name


def _domain(entry: Field, raw: RawValue) -> DomainValue:
    text = raw.text.strip()
    subdomains = text.startswith("*.")
    name = dns_name(text[2:] if subdomains else text)
    if name is None:
        raise _invalid(entry, raw)
    return DomainValue(name, subdomains)


def _name(entry: Field, raw: RawValue) -> NameValue:
    name = dns_name(raw.text)
    if name is None:
        raise _invalid(entry, raw)
    return NameValue(name)


def _pattern(entry: Field, raw: RawValue, *, ends_allowed: bool = True) -> PatternValue:
    text = raw.text
    if raw.quoted:
        return PatternValue(text)
    ends = text.startswith("*")
    starts = text.endswith("*") and len(text) > 1
    core = text[1 if ends else 0 : len(text) - 1 if starts else len(text)]
    if not core or "*" in core or (ends and not ends_allowed):
        raise _invalid(entry, raw)
    return PatternValue(core, starts=starts, ends=ends)


def _source(entry: Field, raw: RawValue) -> PatternValue:
    value = _pattern(entry, raw, ends_allowed=False)
    return PatternValue(ascii_lower(value.core), starts=value.starts)


def _contains(entry: Field, raw: RawValue) -> ContainsValue:
    text = raw.text if raw.quoted else raw.text.strip("*")
    if entry.form == "freetext":
        if len(text) < MIN_TERM_CHARS:
            raise SearchQueryError("search_term_too_short", raw.start, raw.end)
    elif not text:
        raise _invalid(entry, raw)
    return ContainsValue(text)


def _kind(entry: Field, raw: RawValue) -> KindValue:
    key = ascii_lower(raw.text.strip())
    if key not in KINDS:
        raise _invalid(entry, raw)
    return KindValue(key)


def _severity_number(text: str) -> int | None:
    if text in ("0", "1", "2", "3", "4", "5", "6", "7"):
        return int(text)
    return SEVERITIES.get(text)


def _severity(entry: Field, raw: RawValue) -> SeverityValue:
    text = ascii_lower(raw.text.strip())
    if "-" in text:
        low_text, _, high_text = text.partition("-")
        low, high = _severity_number(low_text), _severity_number(high_text)
        if low is None or high is None or low > high:
            raise _invalid(entry, raw)
        return SeverityValue(low, high)
    match = _SEVERITY.match(text)
    number = None if match is None else _severity_number(match.group(2))
    if match is None or number is None:
        raise _invalid(entry, raw)
    low, high = {
        None: (number, number),
        ">=": (number, 7),
        "<=": (0, number),
        ">": (number + 1, 7),
        "<": (0, number - 1),
    }[match.group(1)]
    if low > high:
        raise _invalid(entry, raw)
    return SeverityValue(low, high)


def _error(entry: Field, raw: RawValue) -> ErrorValue:
    text = ascii_lower(raw.text.strip())
    prefix = text.endswith("*") and not raw.quoted
    name = text[:-1] if prefix else text
    if not _ERROR_NAME.match(name):
        raise _invalid(entry, raw)
    return ErrorValue(name, prefix)


def _policy(entry: Field, raw: RawValue) -> IntValue | PatternValue:
    text = raw.text.strip()
    if not raw.quoted and text.isascii() and text.isdigit():
        return _int(entry, raw)
    name = _pattern(entry, raw)
    return PatternValue(name.core, starts=name.starts, ends=name.ends, quote_digits=True)


def _vlan(entry: Field, raw: RawValue) -> VlanValue:
    text = raw.text.strip()
    if not (text.isascii() and text.isdigit()) or not 1 <= int(text) <= 4094:
        raise _invalid(entry, raw)
    return VlanValue(int(text))


def _department(entry: Field, raw: RawValue) -> DepartmentValue:
    text = raw.text.strip()
    if not text:
        raise _invalid(entry, raw)
    return DepartmentValue(text)


def _token(entry: Field, raw: RawValue) -> TokenValue:
    text = ascii_lower(raw.text.strip())
    if not text:
        raise _invalid(entry, raw)
    return TokenValue(text)


def parse_value(entry: Field, raw: RawValue) -> Value:
    """The value object of ``raw`` for ``entry``; :class:`SearchQueryError` when the field does
    not accept it (``search_value_invalid``, ``search_term_too_short``)."""
    form = entry.form
    if raw.text == "*" and not raw.quoted and form != "freetext":
        if form in _NOT_PRESENT:
            raise _invalid(entry, raw)
        return Present()
    if form == "ip":
        return _ip(entry, raw)
    if form in ("int", "port"):
        return _int(entry, raw)
    if form == "proto":
        return _proto(entry, raw)
    if form == "mac":
        return _mac(entry, raw)
    if form == "domain":
        return _domain(entry, raw)
    if form == "name":
        return _name(entry, raw)
    if form == "token":
        return _token(entry, raw)
    if form == "text":
        return _pattern(entry, raw)
    if form == "source":
        return _source(entry, raw)
    if form in ("url", "freetext"):
        return _contains(entry, raw)
    if form == "kind":
        return _kind(entry, raw)
    if form == "severity":
        return _severity(entry, raw)
    if form == "error":
        return _error(entry, raw)
    if form == "policy":
        return _policy(entry, raw)
    if form == "vlan":
        return _vlan(entry, raw)
    return _department(entry, raw)


# =================================================================================================
# Which tables a query reads
# =================================================================================================


class ClauseLike(Protocol):
    """What :func:`allowed_kinds` and :func:`branch_tables` read of a ``grammar.Clause``."""

    @property
    def field(self) -> str: ...

    @property
    def values(self) -> tuple[Value, ...]: ...

    @property
    def negated(self) -> bool: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


def allowed_kinds(clauses: Sequence[ClauseLike]) -> frozenset[str]:
    """The kinds every ``type:`` clause allows (a negated one removes its kinds)."""
    kinds = set(KINDS)
    for clause in clauses:
        if clause.field != "type" or any(isinstance(v, Present) for v in clause.values):
            continue
        named = {value.kind for value in clause.values if isinstance(value, KindValue)}
        kinds = kinds - named if clause.negated else kinds & named
    return frozenset(kinds)


def branch_tables(clauses: Sequence[ClauseLike]) -> tuple[str, ...] | None:
    """The typed tables a query is driven by, in branch-rank order; ``None`` when it has no typed
    field (``messages`` drives it). :class:`SearchQueryError` ``search_fields_incompatible`` when
    no table has every typed field (and a kind ``type:`` allows)."""
    typed = [clause for clause in clauses if FIELDS[clause.field].scope == "typed"]
    if not typed:
        return None
    tables = set(tables_of(allowed_kinds(clauses)))
    for clause in typed:
        tables &= set(FIELDS[clause.field].columns)
    if not tables:
        involved = [c for c in clauses if c.field == "type" or FIELDS[c.field].scope == "typed"]
        names = ", ".join(dict.fromkeys(clause.field for clause in involved))
        raise SearchQueryError(
            "search_fields_incompatible",
            min(clause.start for clause in involved),
            max(clause.end for clause in involved),
            fields=names,
        )
    return tuple(table for table in TYPED_TABLES if table in tables)


# =================================================================================================
# The site's own data a query names
# =================================================================================================


@dataclass(frozen=True, slots=True)
class DepartmentEntry:
    id: int
    name: str
    #: VLAN ids whose default department this is.
    vlans: tuple[int, ...]


@dataclass(frozen=True)
class SiteNetwork:
    """The VLANs and departments ``vlan:``/``department:`` name and the configured network
    sources ``source:`` expands (``ap`` also finds ``ap-<ip>``)."""

    #: VLAN id → its one subnet (``vlans.subnet``; ``vlan_id`` is unique).
    vlans: Mapping[int, IpNetwork] = field(default_factory=dict)
    departments: tuple[DepartmentEntry, ...] = ()
    network_sources: frozenset[str] = frozenset()

    @classmethod
    def load(cls, session: Session, config: Config | None = None) -> SiteNetwork:
        """Read the VLANs and departments (and the network sources of ``config``)."""
        vlans: dict[int, IpNetwork] = {}
        members: dict[int, list[int]] = {}
        for subnet, vlan_id, department_id in session.execute(
            select(Vlan.subnet, Vlan.vlan_id, Vlan.department_id)
        ):
            if vlan_id is None:
                continue
            vlans[vlan_id] = ipaddress.ip_network(str(subnet))
            if department_id is not None:
                members.setdefault(department_id, []).append(vlan_id)
        departments = tuple(
            DepartmentEntry(id_, name, tuple(sorted(members.get(id_, ()))))
            for id_, name in session.execute(
                select(Department.id, Department.name).order_by(Department.id)
            )
        )
        sources = frozenset(
            source.name for source in (config.sources if config else ()) if source.is_network
        )
        return cls(vlans, departments, sources)

    def vlan_networks(self, value: VlanValue, start: int, end: int) -> tuple[IpNetwork, ...]:
        """The subnet of a VLAN; ``search_vlan_unknown`` when no VLAN has that id."""
        subnet = self.vlans.get(value.vlan_id)
        if subnet is None:
            raise SearchQueryError("search_vlan_unknown", start, end, vlan=value.vlan_id)
        return (subnet,)

    def department_networks(
        self, value: DepartmentValue, start: int, end: int
    ) -> tuple[IpNetwork, ...]:
        """The subnets of a department's VLANs (none: it matches nothing);
        ``search_department_unknown`` when no department has that name or id."""
        wanted_id = value.department_id
        key = fold(value.text)
        for department in self.departments:
            if department.id == wanted_id or fold(department.name) == key:
                return tuple(self.vlans[vlan] for vlan in department.vlans if vlan in self.vlans)
        raise SearchQueryError("search_department_unknown", start, end, department=value.text)

    def networks(self, value: Value, start: int, end: int) -> tuple[IpNetwork, ...]:
        """The subnets a ``vlan:`` or ``department:`` value stands for."""
        if isinstance(value, VlanValue):
            return self.vlan_networks(value, start, end)
        if isinstance(value, DepartmentValue):
            return self.department_networks(value, start, end)
        raise TypeError(f"no networks for {value!r}")  # pragma: no cover

    def is_network_source(self, name: str) -> bool:
        return name in self.network_sources
