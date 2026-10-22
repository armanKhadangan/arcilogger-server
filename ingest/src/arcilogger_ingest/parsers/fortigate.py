"""FortiGate parser: FortiOS ``format default`` (``key=value`` pairs after the syslog PRI).

Spec 2.5 (format samples and notes), 7.2, 7.3; field mapping tables in ``docs/PARSERS.md``.

One line gives one ``messages`` row and at most one typed row:

==========================  ==============  ================================================
FortiGate ``type/subtype``  Table           Notes
==========================  ==============  ================================================
``traffic/*``               ``fg_traffic``  ``is_interim`` for the interim logids (default
                                            ``0000000020``); deltas and source NAT as columns
``utm/webfilter``           ``fg_web``      ``url`` ≤ 2,000 characters
``utm/dns``                 ``fg_dns``      ``ipaddr`` → ``answers`` (``INET[]``)
``utm/app-ctrl``            ``fg_app``
``utm/ssl``                 ``fg_app``      ``hostname`` (else ``sni``); ``extra.subtype="ssl"``
``utm/ips``                 ``fg_ips``
``utm/virus``               ``fg_virus``
``event/*``                 ``fg_event``    admin logins, configuration changes, perf, VPN …
anything else               —               ``messages`` only; fields in ``extra["fields"]``
==========================  ==============  ================================================

SSL inspection logs go to ``fg_app``, not ``fg_web``: spec 10.2 ranks ``fg_web.hostname`` first
(web filter verdict, categories, blocked counts) and puts the SSL ``sni``/``hostname`` in the
same, second tier as ``fg_app.hostname`` — the usage engine joins both by ``sessionid``. SSL
rows have no ``app``/``appid``/``appcat`` and carry ``extra.subtype = "ssl"``.

**Nothing is lost.** Every field of the line is a typed column, or in the typed row's ``extra``
under its own key, or represented by the ``messages`` row / ``time``: ``type`` (the table),
``devname`` (``messages.host``), and ``eventtime``/``date``/``time``/``tz`` *when the event
time was derived from them*. ``subtype`` is a column of ``fg_traffic``/``fg_event``, implied by
the table for ``fg_web``/``fg_dns``/``fg_ips``/``fg_virus`` and kept in ``extra`` for ``fg_app``
(which holds two subtypes). A value that does not fit its column (e.g. ``srcip="n/a"``) stays in
``extra`` as text and the line is reported as ``fortigate.value``; an empty value (``key=``,
``key=""``) is ``NULL`` in every column kind — text included — and kept in ``extra`` as ``""``.
A key that occurs twice keeps its first value in the column and **all** values as a JSON list in
``extra[key]`` (extra values are otherwise always strings, so a list always means "duplicate
key"). Findings about the line go to ``messages.extra``: ``prefix`` (text before the first
``key=value``), ``unparsed`` (stray tokens), ``unterminated`` (key of a value without closing
quote), ``backslashes="literal"`` (see below), ``truncated`` (columns cut to 2,000 characters),
``device_time`` (a device time that was not used), and ``fields`` when there is no typed row.

**Event time** (``time``): ``eventtime`` (UTC since the epoch; nanoseconds on FortiOS 7.6,
microseconds or seconds on other versions — detected by magnitude, milliseconds accepted too),
else ``date`` + ``time`` + ``tz``. A time without an offset is never guessed; one further than
``max_clock_skew`` (default one day) from the receive time is a broken clock, not an event time
(``fortigate.time``). Without a usable time the receive time is used and the device text is kept
in ``messages.extra["device_time"]``. An ``eventtime`` or ``date``/``time`` that is not a time at
all is a ``fortigate.value`` failure.

**Damaged lines.** Two messages glued into one line (a framing error upstream) repeat their
envelope keys (``date``, ``time``, ``devname``, ``logid`` …): the first message's row is kept and
the line is a ``fortigate.glued`` failure. Whether FortiOS escapes ``\\`` inside quoted values is
not verified: when the escape-aware reading leaves a quote open or stray tokens but a literal
reading of every backslash parses cleanly (a Windows path ending in ``\\``), the literal reading
is used and ``messages.extra["backslashes"] = "literal"`` records it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar, Final, Literal

from arcilogger_core.rawlog import RawRecord
from arcilogger_ingest.parsers.base import (
    URL_MAX,
    LinePosition,
    Parsed,
    ParsedRow,
    Parser,
    decode_message,
)
from arcilogger_ingest.parsers.generic import parse_generic
from arcilogger_ingest.parsers.kv import Tokens, tokenize, tokenize_reference
from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW, plausible_time, split_pri
from arcilogger_ingest.parsers.values import BIGINT, INTEGER, SMALLINT, to_int, to_ip

#: Logids of interim traffic logs (spec 2.5: ``0000000020`` = interim update of a long session).
DEFAULT_INTERIM_LOGIDS: Final = frozenset({"0000000020"})
#: Keys every FortiGate message carries once: two or more of them repeated = two glued messages.
ENVELOPE_KEYS: Final = frozenset({"date", "time", "devname", "devid", "logid", "type", "eventtime"})

#: FortiOS ``level`` names → syslog severity (used only when the line has no PRI).
LEVELS: Final = {
    "emergency": 0,
    "alert": 1,
    "critical": 2,
    "error": 3,
    "warning": 4,
    "notice": 5,
    "notification": 5,
    "information": 6,
    "debug": 7,
}

Kind = Literal["text", "smallint", "integer", "bigint", "inet"]
#: (column, FortiGate key, kind) — the readable form of a table mapping.
Spec = tuple[tuple[str, str, Kind], ...]

_SESSION: Final[Spec] = (
    ("sessionid", "sessionid", "bigint"),
    ("srcip", "srcip", "inet"),
    ("srcport", "srcport", "integer"),
    ("dstip", "dstip", "inet"),
    ("dstport", "dstport", "integer"),
)

FG_TRAFFIC: Final[Spec] = (
    ("logid", "logid", "text"),
    ("subtype", "subtype", "text"),
    ("action", "action", "text"),
    ("policyid", "policyid", "bigint"),
    ("policyname", "policyname", "text"),
    ("proto", "proto", "smallint"),
    *_SESSION,
    ("srcintf", "srcintf", "text"),
    ("dstintf", "dstintf", "text"),
    ("transip", "transip", "inet"),
    ("transport", "transport", "integer"),
    ("trandisp", "trandisp", "text"),
    ("service", "service", "text"),
    ("dstinetsvc", "dstinetsvc", "text"),
    ("app", "app", "text"),
    ("appid", "appid", "bigint"),
    ("appcat", "appcat", "text"),
    ("apprisk", "apprisk", "text"),
    ("dstcountry", "dstcountry", "text"),
    ("duration", "duration", "bigint"),
    ("sentbyte", "sentbyte", "bigint"),
    ("rcvdbyte", "rcvdbyte", "bigint"),
    ("sentpkt", "sentpkt", "bigint"),
    ("rcvdpkt", "rcvdpkt", "bigint"),
    ("sentdelta", "sentdelta", "bigint"),
    ("rcvddelta", "rcvddelta", "bigint"),
    ("durationdelta", "durationdelta", "bigint"),
    ("utmaction", "utmaction", "text"),
    ("crscore", "crscore", "integer"),
)
FG_WEB: Final[Spec] = (
    ("logid", "logid", "text"),
    *_SESSION,
    ("hostname", "hostname", "text"),
    ("url", "url", "text"),
    ("cat", "cat", "integer"),
    ("catdesc", "catdesc", "text"),
    ("action", "action", "text"),
    ("eventtype", "eventtype", "text"),
    ("profile", "profile", "text"),
)
FG_DNS: Final[Spec] = (
    ("logid", "logid", "text"),
    *_SESSION,
    ("qname", "qname", "text"),
    ("qtype", "qtype", "text"),
    ("cat", "cat", "integer"),
    ("catdesc", "catdesc", "text"),
    ("action", "action", "text"),
    ("eventtype", "eventtype", "text"),
)
FG_APP: Final[Spec] = (
    ("logid", "logid", "text"),
    ("proto", "proto", "smallint"),
    *_SESSION,
    ("app", "app", "text"),
    ("appid", "appid", "bigint"),
    ("appcat", "appcat", "text"),
    ("apprisk", "apprisk", "text"),
    ("action", "action", "text"),
    ("hostname", "hostname", "text"),
    ("url", "url", "text"),
)
FG_IPS: Final[Spec] = (
    ("logid", "logid", "text"),
    ("eventtype", "eventtype", "text"),
    ("proto", "proto", "smallint"),
    *_SESSION,
    ("service", "service", "text"),
    ("attack", "attack", "text"),
    ("attackid", "attackid", "bigint"),
    ("severity", "severity", "text"),
    ("action", "action", "text"),
    ("msg", "msg", "text"),
)
FG_VIRUS: Final[Spec] = (
    ("logid", "logid", "text"),
    ("eventtype", "eventtype", "text"),
    ("proto", "proto", "smallint"),
    *_SESSION,
    ("service", "service", "text"),
    ("virus", "virus", "text"),
    ("virusid", "virusid", "bigint"),
    ("dtype", "dtype", "text"),
    ("filename", "filename", "text"),
    ("url", "url", "text"),
    ("hostname", "hostname", "text"),
    ("action", "action", "text"),
    ("msg", "msg", "text"),
)
FG_EVENT: Final[Spec] = (
    ("logid", "logid", "text"),
    ("subtype", "subtype", "text"),
    ("level", "level", "text"),
    ("logdesc", "logdesc", "text"),
    ("action", "action", "text"),
    ("status", "status", "text"),
    ("user_name", "user", "text"),
    ("ui", "ui", "text"),
    ("srcip", "srcip", "inet"),
    ("dstip", "dstip", "inet"),
    ("cfgpath", "cfgpath", "text"),
    ("cfgobj", "cfgobj", "text"),
    ("cfgattr", "cfgattr", "text"),
    ("msg", "msg", "text"),
)
SPECS: Final[dict[str, Spec]] = {
    "fg_traffic": FG_TRAFFIC,
    "fg_web": FG_WEB,
    "fg_dns": FG_DNS,
    "fg_app": FG_APP,
    "fg_ips": FG_IPS,
    "fg_virus": FG_VIRUS,
    "fg_event": FG_EVENT,
}

#: ``(type, subtype)`` → table (``subtype`` ``None`` = every subtype of the type).
TABLES: Final[dict[tuple[str, str | None], str]] = {
    ("traffic", None): "fg_traffic",
    ("event", None): "fg_event",
    ("utm", "webfilter"): "fg_web",
    ("utm", "dns"): "fg_dns",
    ("utm", "app-ctrl"): "fg_app",
    ("utm", "ssl"): "fg_app",
    ("utm", "ips"): "fg_ips",
    ("utm", "virus"): "fg_virus",
}
#: Tables whose rows imply the subtype (``subtype`` is then not repeated in ``extra``).
SUBTYPE_IMPLIED: Final = frozenset({"fg_web", "fg_dns", "fg_ips", "fg_virus"})
#: Separators between ``ipaddr`` answers (verify the exact list format on site).
_ANSWER_SEPARATORS: Final = str.maketrans({",": " ", ";": " "})

_BOUNDS: Final = {"smallint": SMALLINT, "integer": INTEGER, "bigint": BIGINT}
#: Longest digit string that always fits the type (no range check needed).
_SAFE_DIGITS: Final = {"smallint": 4, "integer": 9, "bigint": 18}
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
#: Validated addresses (srcip/transip/dstip repeat constantly); bounded, cleared when full.
_ADDRESSES: dict[str, str] = {}
_ADDRESSES_MAX: Final = 65536
_TIMEZONES: dict[str, timezone | None] = {}


#: Columns a table has besides its :data:`Spec` columns, with their value when absent.
_DERIVED: Final[dict[str, dict[str, Any]]] = {
    "fg_traffic": {"is_interim": False},
    "fg_dns": {"answers": None},
}


@dataclass(frozen=True, slots=True)
class _Compiled:
    """A :data:`Spec` split by kind for the mapping loop (see :func:`map_fields`)."""

    #: Every column with its "absent" value: rows of one table always have the same keys
    #: (``arcilogger_db.bulk.copy_rows`` takes its column list from the first row).
    template: dict[str, Any]
    text: tuple[tuple[str, str], ...]
    integers: tuple[tuple[str, str, int, tuple[int, int]], ...]
    addresses: tuple[tuple[str, str], ...]


def compile_spec(table: str, spec: Spec) -> _Compiled:
    return _Compiled(
        template={**dict.fromkeys(column for column, _, _ in spec), **_DERIVED.get(table, {})},
        text=tuple((column, key) for column, key, kind in spec if kind == "text"),
        integers=tuple(
            (column, key, _SAFE_DIGITS[kind], _BOUNDS[kind])
            for column, key, kind in spec
            if kind in _BOUNDS
        ),
        addresses=tuple((column, key) for column, key, kind in spec if kind == "inet"),
    )


_COMPILED: Final = {table: compile_spec(table, spec) for table, spec in SPECS.items()}


def table_for(log_type: str | None, subtype: str | None) -> str | None:
    """Typed table for a FortiGate ``type``/``subtype`` (``None`` = ``messages`` only)."""
    if log_type is None:
        return None
    return TABLES.get((log_type, None)) or TABLES.get((log_type, subtype or ""))


def eventtime_utc(text: str) -> datetime | None:
    """``eventtime`` → aware UTC time; the unit is detected by magnitude.

    =====================  ============  ==============================================
    value                  unit          seen on
    =====================  ============  ==============================================
    10⁹ ≤ v < 10¹¹         seconds       older FortiOS versions (spec 7.2)
    10¹¹ ≤ v < 10¹⁴        milliseconds  not known from FortiOS; accepted anyway
    10¹⁴ ≤ v < 10¹⁷        microseconds  older FortiOS versions (spec 7.2)
    10¹⁷ ≤ v < 10²⁰        nanoseconds   FortiOS 7.6 (format samples)
    =====================  ============  ==============================================

    The ranges cannot overlap for any date after 2001-09-09 (10⁹ s). Anything else (not a
    plain decimal, before 2001, too large) is ``None``: the caller falls back to ``date``/
    ``time``/``tz``.
    """
    value = to_int(text, (0, 10**20 - 1))
    if value is None or value < 10**9:
        return None
    if value < 10**11:
        micros = value * 1_000_000
    elif value < 10**14:
        micros = value * 1_000
    elif value < 10**17:
        micros = value
    else:
        micros = value // 1_000
    return _EPOCH + timedelta(microseconds=micros)


def offset_of(text: str) -> timezone | None:
    """FortiOS ``tz`` (``+0300``; ``+03:00`` and ``Z`` tolerated) → fixed offset, else ``None``."""
    if text in _TIMEZONES:
        return _TIMEZONES[text]
    result: timezone | None = None
    candidate = text.replace(":", "")
    if candidate in ("Z", "z", "+0000", "-0000"):
        result = UTC
    elif (
        len(candidate) == 5
        and candidate[0] in "+-"
        and candidate[1:].isascii()
        and candidate[1:].isdigit()
    ):
        hours, minutes = int(candidate[1:3]), int(candidate[3:5])
        if hours <= 23 and minutes <= 59:
            sign = -1 if candidate[0] == "-" else 1
            result = timezone(sign * timedelta(hours=hours, minutes=minutes))
    if len(_TIMEZONES) < 64:  # a handful of offsets in practice; never an unbounded cache
        _TIMEZONES[text] = result
    return result


def local_time(date_text: str, time_text: str, tz_text: str | None) -> datetime | None:
    """``date`` + ``time`` + ``tz`` → aware time; ``None`` without a usable offset."""
    zone = offset_of(tz_text) if tz_text is not None else None
    if zone is None:
        return None
    try:
        value = datetime.fromisoformat(f"{date_text}T{time_text}")
    except ValueError:
        return None
    if value.tzinfo is not None:
        return None  # an offset inside "time" is not FortiOS; do not guess which one wins
    return value.replace(tzinfo=zone)


def _address(text: str) -> str | None:
    address = _ADDRESSES.get(text)
    if address is None:
        address = to_ip(text)
        if address is not None:
            if len(_ADDRESSES) >= _ADDRESSES_MAX:
                _ADDRESSES.clear()
            _ADDRESSES[text] = address
    return address


def answers_of(text: str) -> tuple[list[str] | None, bool]:
    """``ipaddr`` → (valid answer addresses or ``None``, every token was valid)."""
    tokens = text.translate(_ANSWER_SEPARATORS).split()
    answers = [address for token in tokens if (address := to_ip(token)) is not None]
    return (answers or None), len(answers) == len(tokens)


def map_fields(table: str, fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Move the mapped keys of ``table`` out of ``fields`` into columns.

    Returns ``(values, invalid keys)``; ``values`` has every column of the table (``None`` when
    absent). A value that does not convert goes back into ``fields`` (→ ``extra``) as sent; an
    empty value — of any kind, text included — is "absent" (``NULL``), kept in ``fields`` too,
    and not an error (an empty ``hostname`` must not win the usage join over the DNS fallback).
    """
    compiled = _COMPILED[table]
    pop = fields.pop
    values = compiled.template.copy()
    for column, key in compiled.text:
        raw = pop(key, None)
        if raw:
            values[column] = raw
        elif raw is not None:
            fields[key] = raw
    invalid: list[str] = []
    for column, key, safe_digits, bounds in compiled.integers:
        raw = pop(key, None)
        if raw is None:
            continue
        if len(raw) <= safe_digits and raw.isdigit() and raw.isascii():
            values[column] = int(raw)
        elif (number := to_int(raw, bounds)) is not None:
            values[column] = number
        else:
            fields[key] = raw
            if raw:
                invalid.append(key)
    for column, key in compiled.addresses:
        raw = pop(key, None)
        if raw is None:
            continue
        address = _address(raw)
        if address is not None:
            values[column] = address
        else:
            fields[key] = raw
            if raw:
                invalid.append(key)
    return values, invalid


@dataclass(slots=True)
class _Issues:
    """What did not fit while mapping one line (turned into one pattern by :func:`_verdict`)."""

    #: Keys whose value does not fit its column (also ``eventtime`` and ``date/time``).
    invalid: list[str] = field(default_factory=list)
    #: Device time text that was rejected as implausible (``fortigate.time``).
    implausible: str | None = None
    #: Envelope keys that occur more than once (two messages glued into one line).
    glued: frozenset[str] = frozenset()


class FortiGateParser(Parser):
    """``key=value`` lines → ``messages`` + ``fg_*`` rows (see the module docstring)."""

    name: ClassVar[str] = "fortigate"
    tables: ClassVar[frozenset[str]] = frozenset(
        {"fg_traffic", "fg_web", "fg_dns", "fg_app", "fg_ips", "fg_virus", "fg_event"}
    )

    def __init__(
        self,
        *,
        interim_logids: Iterable[str] = DEFAULT_INTERIM_LOGIDS,
        max_clock_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW,
    ) -> None:
        #: Traffic logids that are interim updates (``fg_traffic.is_interim``).
        self.interim_logids = frozenset(interim_logids)
        #: Largest accepted distance between device time and receive time (``None`` = any).
        self.max_clock_skew = max_clock_skew

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        facility, severity, rest = split_pri(decode_message(record.message))
        body = rest.strip()
        start = _first_pair(body)
        meta: dict[str, Any] = {}
        tokens = _tokenize(body[start:] if start else body, meta)
        fields: dict[str, Any] = tokens.fields
        if not fields:
            generic = parse_generic(record, self.max_clock_skew)
            return Parsed(
                message=generic.message,
                time=generic.time,
                error="fortigate.kv",
                detail="no key=value pairs",
            )
        if start:
            meta["prefix"] = body[:start].rstrip()
        if tokens.strays:
            meta["unparsed"] = list(tokens.strays)
        if tokens.unterminated is not None:
            meta["unterminated"] = tokens.unterminated
        issues = _Issues()
        if tokens.duplicates:  # empty for every ordinary line: no cost on the hot path
            repeated = ENVELOPE_KEYS.intersection(tokens.duplicates)
            if len(repeated) >= 2:
                issues.glued = repeated

        log_type = fields.get("type") or None
        if log_type is not None:
            del fields["type"]  # the table / messages.type (an empty one stays in the fields)
        subtype = fields.get("subtype") or None
        host = fields.pop("devname", None)
        if host == "":
            fields["devname"] = host  # empty: not a host name, kept as sent
            host = None
        time = self._event_time(fields, meta, record.received, issues)
        if severity is None:
            severity = LEVELS.get(fields.get("level", ""))
        message: dict[str, Any] = {
            "type": log_type or "unknown",
            "subtype": subtype,
            "severity": severity,
            "facility": facility,
            "program": None,
            "host": host,
            "text": body,
            "extra": meta,
        }
        table = table_for(log_type, subtype)
        rows: tuple[ParsedRow, ...] = ()
        if table is None:
            if subtype is not None:
                del fields["subtype"]  # in messages.subtype (an empty one stays in the fields)
            fields.update(tokens.duplicates)
            if fields:
                meta["fields"] = fields
        else:
            if table in SUBTYPE_IMPLIED and subtype is not None:
                del fields["subtype"]
            values, invalid = map_fields(table, fields)
            issues.invalid.extend(invalid)
            self._table_specific(table, values, fields, issues.invalid, meta)
            fields.update(tokens.duplicates)
            values["extra"] = fields
            rows = (ParsedRow(table, values),)
        error, detail = _verdict(tokens, log_type, issues)
        return Parsed(message=message, rows=rows, time=time, error=error, detail=detail)

    def _event_time(
        self,
        fields: dict[str, Any],
        meta: dict[str, Any],
        received: datetime,
        issues: _Issues,
    ) -> datetime | None:
        """Event time; consumes the time fields it was derived from (module docstring).

        ``eventtime`` first, then ``date``/``time``/``tz``. A value that is not a time goes to
        ``issues.invalid``; a time further than ``max_clock_skew`` from the receive time is not
        used (the next source is tried) and goes to ``issues.implausible``. ``None`` = use the
        receive time; the device text is then kept in ``meta["device_time"]``.
        """
        rejected: str | None = None
        eventtime = fields.get("eventtime")
        if eventtime:
            value = eventtime_utc(eventtime)
            if value is None:
                issues.invalid.append("eventtime")
            elif (usable := plausible_time(value, received, self.max_clock_skew)) is not None:
                for key in ("eventtime", "date", "time", "tz"):
                    fields.pop(key, None)
                return usable
            else:
                rejected = eventtime
        date_text, time_text = fields.get("date"), fields.get("time")
        if date_text and time_text:
            tz_text = fields.get("tz")
            local = local_time(date_text, time_text, tz_text)
            if local is None:
                if tz_text is not None and offset_of(tz_text) is not None:
                    issues.invalid.append("date/time")  # an offset, but no valid date and time
                meta["device_time"] = f"{date_text} {time_text}"  # never guessed
            elif (usable := plausible_time(local, received, self.max_clock_skew)) is not None:
                for key in ("date", "time", "tz"):
                    fields.pop(key, None)
                if rejected is not None:
                    issues.implausible = f"eventtime={rejected}"  # date/time used instead
                return usable
            else:
                rejected = f"{date_text} {time_text} {tz_text}"
                meta["device_time"] = rejected
        if rejected is not None:
            meta.setdefault("device_time", rejected)
            issues.implausible = rejected
        return None

    def _table_specific(
        self,
        table: str,
        values: dict[str, Any],
        fields: dict[str, Any],
        invalid: list[str],
        meta: dict[str, Any],
    ) -> None:
        if table == "fg_traffic":
            values["is_interim"] = values.get("logid") in self.interim_logids
            return
        if table == "fg_dns":
            ipaddr = fields.pop("ipaddr", None)
            if ipaddr is not None:
                answers, complete = answers_of(ipaddr)
                values["answers"] = answers
                if answers is None or not complete:
                    fields["ipaddr"] = ipaddr  # kept as sent
                    if not complete:
                        invalid.append("ipaddr")
        elif table == "fg_app" and values["hostname"] is None and fields.get("sni"):
            # SSL log without (or with an empty) host name: the SNI, which stays in extra too.
            values["hostname"] = fields["sni"]
        url = values.get("url")
        if url is not None and len(url) > URL_MAX:
            values["url"] = url[:URL_MAX]  # spec 7.3; the raw line keeps the full URL
            meta["truncated"] = ["url"]


def _tokenize(text: str, meta: dict[str, Any]) -> Tokens:
    """:func:`tokenize`, retried with literal backslashes when the line does not parse cleanly.

    FortiOS may or may not escape ``\\`` inside quoted values (verify on site). If it does not,
    a value ending in a backslash (``filename="C:\\Temp\\"``) turns its closing quote into an
    escaped one: at the end of the line the value swallows the rest (unterminated), inside the
    line the quotes pair up wrongly and the next quoted value becomes a stray token. The literal
    reading is used only when it closes every quote and leaves no stray token;
    ``meta["backslashes"]`` records that it was.
    """
    tokens = tokenize(text)
    if (tokens.unterminated is not None or tokens.strays) and "\\" in text:
        literal = tokenize_reference(text, escapes=False)
        if literal.unterminated is None and not literal.strays:
            meta["backslashes"] = "literal"
            return literal
    return tokens


def _is_pair(token: str) -> bool:
    equals = token.find("=")
    return equals > 0 and '"' not in token[:equals]


def _first_pair(body: str) -> int:
    """Index of the first ``key=value`` token: 0 for ordinary lines; spec 7.2 says to tolerate
    anything before ``date=`` (e.g. a relay's BSD header), which becomes ``extra["prefix"]``.
    """
    space = body.find(" ")
    if _is_pair(body if space < 0 else body[:space]):
        return 0
    while space >= 0:
        position = space + 1
        space = body.find(" ", position)
        if _is_pair(body[position:] if space < 0 else body[position:space]):
            return position
    return 0


def _verdict(
    tokens: Tokens, log_type: str | None, issues: _Issues
) -> tuple[str | None, str | None]:
    """The one parse-failure pattern of a line (most severe first) and a short detail."""
    if issues.glued:
        repeated = ", ".join(sorted(issues.glued))
        return "fortigate.glued", f"two messages in one line? repeated keys: {repeated}"
    if tokens.unterminated is not None:
        return "fortigate.unterminated", f"no closing quote after {tokens.unterminated}="
    if tokens.strays:
        return "fortigate.unparsed", f"{len(tokens.strays)} token(s) without key=value"
    if log_type is None:
        return "fortigate.type", "no type= field"
    if issues.invalid:
        return "fortigate.value", ("invalid value: " + ", ".join(issues.invalid))[:400]
    if issues.implausible is not None:
        return "fortigate.time", f"implausible device time: {issues.implausible}"[:400]
    return None, None
