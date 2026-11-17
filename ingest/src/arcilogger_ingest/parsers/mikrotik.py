"""MikroTik RouterOS parser (spec 2.5, 7.2, 7.3; mapping tables in ``docs/PARSERS.md``).

**Header.** The remote-syslog wire format of RouterOS 7 is *not verified yet* (approximately
``<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp60 assigned …``: a BSD-style header
with an ISO-8601 time and the topics string). The parser therefore accepts, in this order and
each part optional: ``<PRI>``; an RFC 5424 header, an ISO-8601 timestamp (with or without
fraction and offset) or a BSD timestamp; a host name; a **topics** token (lower-case words
joined by ``,`` — ``;`` as in ``/log print`` is tolerated — with at least two words and an
optional trailing ``:``, e.g. ``dhcp,info``, ``system,info,account``, ``script,info:``); the
message.

A token is the host name when a topics token follows it, or — by BSD convention — when it
follows a timestamp (never when it ends with ``:``, which is a tag or a firewall chain). Such an
unconfirmed host name is ambiguous: ``<ts> DROP-FWD forward: in:…`` and ``<ts> CORE-R1 forward:
in:…`` have the same shape. With ``host_names`` (the device identities, constructor parameter)
the decision is deterministic. Without it the line is read with the host name and again with
the "host name" as the first message word; when neither a timestamp nor topics mark the header,
it is read as is and again with its first word as the host name (``<30>CORE-R1 dhcp60
assigned …``). The best reading wins: a clean match, then a match that skipped header residue
before its keyword, then a family failure — the earlier reading on a tie.

The evidence families (DHCP leases, ``ARP-LOG``, ``ARCI-HEARTBEAT``) are found anywhere in the
message, so header residue that was not recognised (``dhcp dhcp60 assigned …``, ``script:
ARP-LOG …``) does not hide them; the residue is kept in the row's ``extra["prefix"]``. A line
that looks like DHCP evidence (``assigned``/``deassigned`` plus topic ``dhcp`` or a MAC address)
but does not parse is a ``mikrotik.dhcp`` failure — never silently a plain log line.

**Time.** A time without offset is never guessed; one further than ``max_clock_skew`` (default
one day) from the receive time is a broken clock (``mikrotik.time``). Either way the receive
time is used and the header text is kept in ``messages.extra["device_time"]`` (contract).
**Two messages in one line** (a header such as ``<30>2026-…`` inside the message, a framing
error upstream) are cut at the second header: the first is parsed, the rest is kept in
``messages.extra["glued"]`` and the line is a ``mikrotik.glued`` failure.

**Message families** (compiled regular expressions with named groups):

============================================  ================  ===============================
Message                                       Table             ``messages.type`` / subtype
============================================  ================  ===============================
``<server> assigned|deassigned <ip> for       ``mt_dhcp``       ``dhcp`` / action
<mac> [<hostname…>]``
``<server> offering lease <ip> for <mac> …``  ``mt_event``      ``dhcp`` / ``offering``
``<server> failed to give out IP address …    ``mt_event``      ``dhcp`` / ``pool_empty``
pool <x> is empty``                           (``dhcp_pool_empty``)
``ARP-LOG <ip> <mac> <interface>``            ``mt_arp``        ``arp``
``[<prefix> ]<chain>: in:<if> out:<if>, …``   ``mt_fw``         ``fw`` / chain
``user <u> logged in|out from <ip> via <v>``  ``mt_account``    ``account`` / login|logout
``login failure for user <u> from <ip> via``  ``mt_account``    ``account`` / login_failure
``<if> link up|down [(<detail>)]``            ``mt_interface``  ``interface`` / up|down
``… changed|added|removed by <user> …``       ``mt_config``     ``config`` / action
``ARCI-HEARTBEAT <identity>``                 ``mt_event``      ``heartbeat``
``<mac>@<if>[:] connected|disconnected[, …]`` ``mt_event``      ``wifi`` / connected|…
anything else at level critical/error/warning ``mt_event``      ``log`` / level
anything else                                 —                 ``log`` / level
============================================  ================  ===============================

The **level** is the most severe of ``critical``/``error``/``warning``/``info``/``debug`` among
the topics, else derived from the PRI severity (see :func:`topic_level`).

The DHCP server name carries the VLAN (``dhcp60`` → 60) by the same rule as the database
setting ``dhcp.server_vlan_regex``; the configuration-change patterns are a constructor
parameter (defaults in :data:`DEFAULT_CONFIG_PATTERNS`, **verify on site**). MAC addresses are
stored as ``aa:bb:cc:dd:ee:ff``. Whatever a family regex does not cover (a firewall line's
unknown tail, the NAT part, ICMP type/code …) goes to the typed row's ``extra``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar, Final

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_ingest.parsers.base import (
    LinePosition,
    Parsed,
    ParsedRow,
    Parser,
    decode_message,
)
from arcilogger_ingest.parsers.syslog import (
    DEFAULT_MAX_CLOCK_SKEW,
    parse_time,
    plausible_time,
    split_pri,
    split_rfc5424,
    split_timestamp,
)
from arcilogger_ingest.parsers.values import INTEGER, to_int, to_ip, to_mac

#: ``dhcp60`` → VLAN 60: the database setting's shipped default (first group = VLAN id).
DEFAULT_SERVER_VLAN_REGEX: Final[str] = DEFAULT_SETTINGS["dhcp.server_vlan_regex"]
#: Configuration changes (spec 2.5: "``… changed by <user>`` / ``… added by …`` / ``… removed
#: by …`` in topics ``system,info`` (verify; make it a configurable regex list)"). Named groups:
#: ``target``, ``action``, ``user`` (→ ``user_name``), ``via``; any other group → ``extra``.
#: The user token is stored as sent — RouterOS may append session details to it (verify).
DEFAULT_CONFIG_PATTERNS: Final = (
    r"^(?P<target>.+?) (?P<action>changed|added|removed) by (?P<user>\S+)(?: (?P<detail>.+))?$",
)
#: Severity words among the topics, most severe first (``mt_event.level``).
LEVELS: Final = ("critical", "error", "warning", "info", "debug")
#: Levels that make an otherwise unrecognised line an ``mt_event`` (spec 7.3).
EVENT_LEVELS: Final = frozenset({"critical", "error", "warning"})

_TOPICS = re.compile(r"[a-z0-9][a-z0-9-]*(?:[,;][a-z0-9][a-z0-9-]*)+:?", re.ASCII)
_TOPIC_SPLIT = re.compile(r"[,;]")
_MAC_TEXT = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
#: A MAC address anywhere in a message (``:`` or ``-`` separated): marks DHCP evidence.
_MAC_ANYWHERE = re.compile(
    r"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}(?![0-9A-Fa-f:-])"
)
_ADDRESS = r"\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:.]+\]|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+"
#: A second syslog header inside a message: ``<PRI>`` + ISO, BSD or RFC 5424 timestamp.
_GLUED = re.compile(
    r"<\d{1,3}>(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:|[A-Z][a-z]{2} [ \d]\d \d{2}:|1 \d{4}-)", re.ASCII
)

# Searched, not anchored: header residue before the server name is tolerated (and kept). "for"
# is RouterOS 7 (format sample); "to" is tolerated for older wording (unverified). A ":" after
# the server name is tolerated too.
_DHCP = re.compile(
    r"(?:^|(?<= ))(?P<server>[^\s:]+):? (?P<action>assigned|deassigned) (?P<ip>\S+) (?:for|to) "
    r"(?P<mac>\S+)(?: (?P<host>.*))?\Z"
)
_OFFERING = re.compile(r"(?P<server>\S+) offering lease (?P<ip>\S+) for (?P<mac>\S+)(?P<tail>.*)")
_POOL = re.compile(r"(?P<server>\S+) failed to give out IP address(?P<tail>.*)")
_POOL_NAME = re.compile(r"pool <?(?P<pool>[^\s>]+)>? is empty")
#: Where our own scripts' keywords start (beginning of the message, or after residue).
_ARP_AT = re.compile(r"(?:^|(?<=[\s:]))ARP-LOG(?=\s|\Z)")
_ARP = re.compile(r"ARP-LOG (?P<ip>\S+) (?P<mac>\S+)(?: (?P<interface>\S+))?(?P<tail>.*)")
_HEARTBEAT_AT = re.compile(r"(?:^|(?<=[\s:]))ARCI-HEARTBEAT(?=\s|\Z)")
_HEARTBEAT = re.compile(r"ARCI-HEARTBEAT(?:\s+(?P<identity>.*))?")
# Parts in RouterOS order, each optional, the comma before a part tolerated missing (only the
# format sample's "connection-state:new src-mac …, proto UDP, a:p->b:q, len N" is verified).
_FIREWALL = re.compile(
    r"(?:(?P<prefix>.*?) )?(?P<chain>[^\s:]+): "
    r"in:(?P<in_if>\([^)]*\)|[^\s,]*) out:(?P<out_if>\([^)]*\)|[^\s,]*)"
    r"(?:,? connection-state:(?P<state>[^\s,]+(?:,[^\s,]+)*))?"
    rf"(?:,? src-mac (?P<src_mac>{_MAC_TEXT}))?"
    r"(?:,? proto (?P<proto>[^\s,(]+)(?: \((?P<proto_info>[^)]*)\))?)?"
    rf"(?:,? (?P<src>{_ADDRESS})(?::(?P<sport>\d+))?->(?P<dst>{_ADDRESS})(?::(?P<dport>\d+))?)?"
    r"(?:,? NAT (?P<nat>[^\s,]+))?"
    r"(?:,? len (?P<len>\d+))?"
    r"(?P<tail>.*)",
    re.ASCII,
)
_LOGGED = re.compile(
    r"user (?P<user>\S+) logged (?P<direction>in|out)"
    r"(?: from (?P<ip>\S+))?(?: via (?P<via>\S+))?(?P<tail>.*)"
)
_LOGIN_FAILURE = re.compile(
    r"login failure for user (?P<user>\S+)(?: from (?P<ip>\S+))?(?: via (?P<via>\S+))?(?P<tail>.*)"
)
_INTERFACE = re.compile(
    r"(?P<interface>\S+) link (?P<state>up|down)(?: \((?P<detail>[^)]*)\))?(?P<tail>.*)"
)
_WIFI = re.compile(
    rf"(?P<mac>{_MAC_TEXT})@(?P<interface>[^\s:,]+):? (?P<state>connected|disconnected)"
    r"(?:,? (?P<detail>.*))?"
)
_PORT: Final = (0, 65535)
_VLAN_ID: Final = (1, 4094)


@dataclass(frozen=True, slots=True)
class Header:
    """The MikroTik view of a syslog header."""

    facility: int | None
    severity: int | None
    time_text: str | None
    host: str | None
    #: ``True`` when a topics token or an RFC 5424 header confirmed the host name.
    host_certain: bool
    #: Topics string as sent (``None`` = no topics token).
    topics: str | None
    message: str


def topics_of(token: str | None) -> str | None:
    """The topics in ``token`` (``dhcp,info`` / ``dhcp,info:`` → ``dhcp,info``), else ``None``."""
    if not token or _TOPICS.fullmatch(token) is None:
        return None
    return token[:-1] if token.endswith(":") else token


def split_header(text: str) -> Header:
    """PRI, timestamp, host name, topics and message (see the module docstring)."""
    facility, severity, rest = split_pri(text)
    rest = rest.strip()
    rfc5424 = split_rfc5424(rest)
    if rfc5424 is not None:
        message = rfc5424.rest.strip()
        topics = topics_of(rfc5424.app)
        if topics is None:
            first, _, after = message.partition(" ")
            topics = topics_of(first)
            if topics is not None:
                message = after
        return Header(
            facility, severity, rfc5424.time_text, rfc5424.host, True, topics, message.strip()
        )
    time_text, rest = split_timestamp(rest)
    rest = rest.lstrip()
    first, _, after = rest.partition(" ")
    topics = topics_of(first)
    if topics is not None:
        return Header(facility, severity, time_text, None, False, topics, after.strip())
    after = after.lstrip()
    second, _, after_second = after.partition(" ")
    topics = topics_of(second)
    if topics is not None:
        return Header(facility, severity, time_text, first, True, topics, after_second.strip())
    if time_text is not None and first and not first.endswith(":"):
        # BSD convention: the word after the timestamp is the host — unless it is a tag or a
        # firewall chain ("forward:"). Unconfirmed; see MikroTikParser._readings.
        return Header(facility, severity, time_text, first, False, None, after.strip())
    return Header(facility, severity, time_text, None, False, None, rest)


#: Syslog severity → level, used only when the topics name none (e.g. no topics token).
_SEVERITY_LEVELS: Final = ("critical", "critical", "critical", "error", "warning")


def topic_level(topics: list[str], severity: int | None = None) -> str | None:
    """Most severe level word among the topics (``system,error,critical`` → ``critical``);
    without one, the PRI severity (0-2 critical, 3 error, 4 warning, 5-6 info, 7 debug)."""
    for level in LEVELS:
        if level in topics:
            return level
    if severity is None:
        return None
    if severity < len(_SEVERITY_LEVELS):
        return _SEVERITY_LEVELS[severity]
    return "debug" if severity == 7 else "info"


@dataclass(slots=True)
class _Result:
    """What one message family made of a message."""

    type: str
    subtype: str | None = None
    rows: list[ParsedRow] = field(default_factory=list)
    error: str | None = None
    detail: str | None = None
    #: Header residue was skipped before the family keyword (kept in ``extra["prefix"]``).
    residue: bool = False

    @property
    def rank(self) -> int:
        """0 = clean, 1 = clean after skipping residue, 2 = failure (lower is better)."""
        if self.rows or self.error is None:
            return 1 if self.residue else 0
        return 2


@dataclass(frozen=True, slots=True)
class _Context:
    topics: str | None
    topic_list: list[str]
    level: str | None


def _prefix(extra: dict[str, Any], message: str, start: int) -> bool:
    """Keep header residue before a family keyword (e.g. an unrecognised ``script:`` tag)."""
    residue = message[:start].strip() if start else ""
    if residue:
        extra["prefix"] = residue
    return bool(residue)


def _tail(extra: dict[str, Any], text: str | None) -> None:
    """Keep an unparsed remainder (spec 7.2: "keep unparsed remainder")."""
    if text:
        remainder = text.strip(" ,")
        if remainder:
            extra["tail"] = remainder


def _event(context: _Context, event: str, message: str, **values: Any) -> ParsedRow:
    """An ``mt_event`` row with every column (rows of one table always have the same keys)."""
    return ParsedRow(
        "mt_event",
        {
            "event": event,
            "topics": context.topics,
            "level": context.level,
            "identity": None,
            "interface": None,
            "mac": None,
            "ip": None,
            "text": message,
            "extra": {},
            **values,
        },
    )


class MikroTikParser(Parser):
    """RouterOS remote syslog → ``messages`` + ``mt_*`` rows (see the module docstring)."""

    name: ClassVar[str] = "mikrotik"
    tables: ClassVar[frozenset[str]] = frozenset(
        {"mt_dhcp", "mt_arp", "mt_fw", "mt_account", "mt_interface", "mt_config", "mt_event"}
    )

    def __init__(
        self,
        *,
        config_patterns: Iterable[str | re.Pattern[str]] = DEFAULT_CONFIG_PATTERNS,
        server_vlan_regex: str = DEFAULT_SERVER_VLAN_REGEX,
        host_names: Iterable[str] = (),
        max_clock_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW,
    ) -> None:
        """``config_patterns``: regular expressions for configuration-change messages (tried
        in order with ``search``); ``server_vlan_regex``: DHCP server name → VLAN id (first
        group), normally the setting ``dhcp.server_vlan_regex``; ``host_names``: the device
        identities (``/system identity``, compared case-insensitively) — when given, an
        unconfirmed header word is the host name exactly when it is one of them;
        ``max_clock_skew``: largest accepted distance between header time and receive time
        (``None`` = any). Invalid patterns raise ``re.error`` here, at start-up, not per line."""
        self.config_patterns = tuple(re.compile(pattern) for pattern in config_patterns)
        self.server_vlan = re.compile(server_vlan_regex)
        self.host_names = frozenset(name.casefold() for name in host_names)
        self.max_clock_skew = max_clock_skew
        self._vlans: dict[str, int | None] = {}
        self._families: tuple[Callable[[str, _Context], _Result | None], ...] = (
            self._arp,
            self._heartbeat,
            self._dhcp,
            self._firewall,
            self._account,
            self._interface,
            self._wifi,
            self._config,
        )

    # -- the line -------------------------------------------------------------------------------

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        header = split_header(decode_message(record.message))
        topic_list = _TOPIC_SPLIT.split(header.topics) if header.topics else []
        context = _Context(header.topics, topic_list, topic_level(topic_list, header.severity))
        extra: dict[str, Any] = {}
        message = header.message
        glued = _GLUED.search(message, 1) if "<" in message else None
        if glued is not None:
            extra["glued"] = message[glued.start() :]
            message = message[: glued.start()].rstrip()
        result, host, message = self._interpret(header, message, context)
        if result is None:
            result = _Result("log", context.level)
            if context.level in EVENT_LEVELS:
                result.rows.append(_event(context, "log", message))
        error, detail = result.error, result.detail
        if glued is not None:
            error, detail = "mikrotik.glued", "two messages in one line? (rest in extra.glued)"
        time = None
        if header.time_text is not None:
            device = parse_time(header.time_text)
            if device is not None:
                time = plausible_time(device, record.received, self.max_clock_skew)
                if time is None and error is None:
                    error, detail = "mikrotik.time", f"implausible device time {header.time_text}"
            if time is None:
                extra["device_time"] = header.time_text  # no offset or implausible: not used
        return Parsed(
            message={
                "type": result.type,
                "subtype": result.subtype,
                "severity": header.severity,
                "facility": header.facility,
                "program": header.topics,
                "host": host,
                "text": message,
                "extra": extra,
            },
            rows=tuple(result.rows),
            time=time,
            error=error,
            detail=detail[:400] if detail else detail,
        )

    def _readings(self, header: Header, message: str) -> list[tuple[str | None, str]]:
        """``(host, message)`` readings of a line, the preferred one first."""
        host = header.host
        if host is not None and not header.host_certain:
            joined = f"{host} {message}" if message else host
            if self.host_names:
                return [(host, message)] if host.casefold() in self.host_names else [(None, joined)]
            return [(host, message), (None, joined)]
        if host is None and header.time_text is None and header.topics is None:
            first, _, rest = message.partition(" ")
            rest = rest.lstrip()
            if rest and not first.endswith(":"):
                if self.host_names:
                    known = first.casefold() in self.host_names
                    return [(first, rest)] if known else [(None, message)]
                return [(None, message), (first, rest)]
        return [(host, message)]

    def _interpret(
        self, header: Header, message: str, context: _Context
    ) -> tuple[_Result | None, str | None, str]:
        """The reading with the best :attr:`_Result.rank` (the earlier one on a tie), else
        ``None`` with the preferred reading. ``<30>CORE-R1 dhcp41 assigned …`` matches as is
        (``CORE-R1`` as residue) and with ``CORE-R1`` as host name: the latter is clean."""
        readings = self._readings(header, message)
        best: tuple[_Result, str | None, str] | None = None
        for host, text in readings:
            result = self._match(text, context)
            if result is None:
                continue
            if result.rank == 0:
                return result, host, text
            if best is None or result.rank < best[0].rank:
                best = (result, host, text)
        if best is not None:
            return best
        host, text = readings[0]
        return None, host, text

    def _match(self, message: str, context: _Context) -> _Result | None:
        """The first family result with the best :attr:`_Result.rank`, else ``None``.

        Only a clean match returns at once: a keyword found after residue (``… for <mac>
        ARCI-HEARTBEAT-PC``) must not hide a clean match of a later family (the lease).
        """
        best: _Result | None = None
        for family in self._families:
            result = family(message, context)
            if result is None:
                continue
            if result.rank == 0:
                return result
            if best is None or result.rank < best.rank:
                best = result
        return best

    def vlan_of(self, server: str) -> int | None:
        """VLAN id from a DHCP server name (``dhcp60`` → 60), ``None`` when it has none."""
        if server in self._vlans:
            return self._vlans[server]
        match = self.server_vlan.search(server)
        group = match.group(1) if match is not None and match.re.groups else None
        vlan = to_int(group, _VLAN_ID) if group else None
        if len(self._vlans) < 4096:
            self._vlans[server] = vlan
        return vlan

    # -- families (each returns None when the message is not of its kind) -----------------------

    def _dhcp(self, message: str, context: _Context) -> _Result | None:
        if " assigned " in message or " deassigned " in message:
            match = _DHCP.search(message)
            if match is not None:
                ip, mac = to_ip(match["ip"]), to_mac(match["mac"])
                if ip is not None and mac is not None:
                    return self._lease(match, ip, mac)
                detail = "invalid ip" if ip is None else "invalid mac"
            else:
                detail = "unknown DHCP format"
            # Lease evidence that does not parse is counted, never silently a plain log line.
            if "dhcp" in context.topic_list or _MAC_ANYWHERE.search(message) is not None:
                action = match["action"] if match is not None else None
                return _Result("dhcp", action, error="mikrotik.dhcp", detail=detail)
            return None  # e.g. "ether1 assigned address to bridge"
        if " offering lease " in message:
            match = _OFFERING.fullmatch(message)
            if match is not None:
                server = match["server"]
                extra: dict[str, Any] = {"server": server}
                vlan = self.vlan_of(server)
                if vlan is not None:
                    extra["vlan"] = vlan
                ip, mac = to_ip(match["ip"]), to_mac(match["mac"])
                if ip is None:
                    extra["ip"] = match["ip"]
                if mac is None:
                    extra["mac"] = match["mac"]
                _tail(extra, match["tail"])
                row = _event(context, "dhcp_offering", message, ip=ip, mac=mac, extra=extra)
                return _Result("dhcp", "offering", [row])
        if " failed to give out IP address" in message:
            match = _POOL.fullmatch(message)
            if match is not None:
                server = match["server"]
                extra = {"server": server}
                vlan = self.vlan_of(server)
                if vlan is not None:
                    extra["vlan"] = vlan
                pool = _POOL_NAME.search(match["tail"])
                if pool is not None:
                    extra["pool"] = pool["pool"]
                event = "dhcp_pool_empty" if pool is not None else "dhcp_failed"
                subtype = "pool_empty" if pool is not None else "failed"
                return _Result("dhcp", subtype, [_event(context, event, message, extra=extra)])
        return None

    def _lease(self, match: re.Match[str], ip: str, mac: str) -> _Result:
        action = match["action"]
        server = match["server"]
        hostname = (match["host"] or "").strip() or None
        extra: dict[str, Any] = {}
        residue = _prefix(extra, match.string, match.start())
        row: dict[str, Any] = {
            "server": server,
            "vlan": self.vlan_of(server),
            "action": action,
            "ip": ip,
            "mac": mac,
            "hostname": hostname,
            "extra": extra,
        }
        return _Result("dhcp", action, [ParsedRow("mt_dhcp", row)], residue=residue)

    @staticmethod
    def _arp(message: str, context: _Context) -> _Result | None:
        if "ARP-LOG" not in message:
            return None
        found = _ARP_AT.search(message)
        if found is None:
            return None
        start = found.start()
        match = _ARP.fullmatch(message, start)
        ip = to_ip(match["ip"]) if match is not None else None
        mac = to_mac(match["mac"]) if match is not None else None
        if match is None or ip is None or mac is None:
            return _Result("arp", error="mikrotik.arp", detail="expected ARP-LOG <ip> <mac> <if>")
        extra: dict[str, Any] = {}
        residue = _prefix(extra, message, start)
        _tail(extra, match["tail"])
        row = {"ip": ip, "mac": mac, "interface": match["interface"], "extra": extra}
        return _Result("arp", None, [ParsedRow("mt_arp", row)], residue=residue)

    @staticmethod
    def _heartbeat(message: str, context: _Context) -> _Result | None:
        if "ARCI-HEARTBEAT" not in message:
            return None
        found = _HEARTBEAT_AT.search(message)
        match = _HEARTBEAT.fullmatch(message, found.start()) if found is not None else None
        if found is None or match is None:
            return None
        identity = (match["identity"] or "").strip() or None
        extra: dict[str, Any] = {}
        residue = _prefix(extra, message, found.start())
        row = _event(context, "heartbeat", message, identity=identity, extra=extra)
        return _Result("heartbeat", None, [row], residue=residue)

    @staticmethod
    def _firewall(message: str, context: _Context) -> _Result | None:
        if " in:" not in message or " out:" not in message:
            return None
        match = _FIREWALL.fullmatch(message)
        if match is None:
            if "firewall" in context.topic_list or ("proto " in message and "->" in message):
                return _Result("fw", error="mikrotik.firewall", detail="unknown firewall format")
            return None
        extra: dict[str, Any] = {}
        invalid: list[str] = []
        values: dict[str, Any] = {
            "prefix": match["prefix"] or None,
            "chain": match["chain"],
            "in_if": match["in_if"] or None,
            "out_if": match["out_if"] or None,
            "state": match["state"],
            "src_mac": to_mac(match["src_mac"]) if match["src_mac"] else None,
            "proto": match["proto"],
            "src": None,
            "sport": None,
            "dst": None,
            "dport": None,
            "len": None,
        }
        for column, kind in (("src", "ip"), ("dst", "ip"), ("sport", "port"), ("dport", "port")):
            raw = match[column]
            if raw is None:
                continue
            value = to_ip(raw.strip("[]")) if kind == "ip" else to_int(raw, _PORT)
            if value is None:
                extra[column] = raw
                invalid.append(column)
            values[column] = value
        if match["len"] is not None:
            values["len"] = to_int(match["len"], INTEGER)
            if values["len"] is None:
                extra["len"] = match["len"]
                invalid.append("len")
        if match["proto_info"] is not None:
            extra["proto_info"] = match["proto_info"]  # TCP flags or "type 3, code 3"
        if match["nat"] is not None:
            extra["nat"] = match["nat"]
        _tail(extra, match["tail"])
        values["extra"] = extra
        result = _Result("fw", match["chain"], [ParsedRow("mt_fw", values)])
        if invalid:
            result.error, result.detail = "mikrotik.firewall", "invalid " + ", ".join(invalid)
        return result

    @staticmethod
    def _account(message: str, context: _Context) -> _Result | None:
        if message.startswith("user "):
            match = _LOGGED.fullmatch(message)
            if match is None:
                return None
            action = "login" if match["direction"] == "in" else "logout"
        elif message.startswith("login failure for user "):
            match = _LOGIN_FAILURE.fullmatch(message)
            if match is None:
                return None
            action = "login_failure"
        else:
            return None
        extra: dict[str, Any] = {}
        ip = None
        if match["ip"] is not None:
            ip = to_ip(match["ip"])
            if ip is None:
                extra["from"] = match["ip"]  # e.g. a MAC address (WinBox over layer 2)
        _tail(extra, match["tail"])
        row = {
            "action": action,
            "user_name": match["user"],
            "ip": ip,
            "via": match["via"],
            "extra": extra,
        }
        return _Result("account", action, [ParsedRow("mt_account", row)])

    @staticmethod
    def _interface(message: str, context: _Context) -> _Result | None:
        if " link " not in message:
            return None
        match = _INTERFACE.fullmatch(message)
        if match is None:
            return None
        extra: dict[str, Any] = {}
        _tail(extra, match["tail"])
        row = {
            "interface": match["interface"],
            "state": match["state"],
            "detail": match["detail"],
            "extra": extra,
        }
        return _Result("interface", match["state"], [ParsedRow("mt_interface", row)])

    @staticmethod
    def _wifi(message: str, context: _Context) -> _Result | None:
        if "@" not in message or "connected" not in message:
            return None
        match = _WIFI.fullmatch(message)
        if match is None:
            return None
        state = match["state"]
        extra: dict[str, Any] = {}
        if match["detail"]:
            extra["detail"] = match["detail"]
        event = "wifi_connect" if state == "connected" else "wifi_disconnect"
        row = _event(
            context,
            event,
            message,
            interface=match["interface"],
            mac=to_mac(match["mac"]),
            extra=extra,
        )
        return _Result("wifi", state, [row])

    def _config(self, message: str, context: _Context) -> _Result | None:
        if " by " not in message:
            return None
        for pattern in self.config_patterns:
            match = pattern.search(message)
            if match is None:
                continue
            groups = match.groupdict()
            action = groups.pop("action", None)
            row = {
                "action": action,
                "user_name": groups.pop("user", None),
                "target": groups.pop("target", None),
                "via": groups.pop("via", None),
                "extra": {key: value for key, value in groups.items() if value is not None},
            }
            return _Result("config", action, [ParsedRow("mt_config", row)])
        return None
