"""Parser registry: pick the parser for a raw line and run it.

Detection (spec 7.2): the configured source type decides; for an unknown sender
(``source_type is None``) the content decides — ``devname=`` or ``logid=`` means FortiGate,
anything else is parsed generically.

:data:`PARSERS` holds parsers with the shipped defaults. The ingest service builds its own set
from the settings with :func:`build_parsers` (interim logids, configuration-change patterns,
the DHCP server → VLAN rule, device identities, the accepted clock skew — the "verify on site"
knobs of docs/PARSERS.md) and passes it to :func:`parse_line` / :func:`get_parser`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Final

from arcilogger_core.rawlog import RawRecord
from arcilogger_ingest.parsers.base import (
    SOURCE_TYPES,
    LinePosition,
    Parsed,
    ParsedRow,
    ParseOutcome,
    Parser,
    SourceType,
    decode_message,
)
from arcilogger_ingest.parsers.fortigate import DEFAULT_INTERIM_LOGIDS, FortiGateParser
from arcilogger_ingest.parsers.generic import GenericParser
from arcilogger_ingest.parsers.mikrotik import (
    DEFAULT_CONFIG_PATTERNS,
    DEFAULT_SERVER_VLAN_REGEX,
    MikroTikParser,
)
from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW


def build_parsers(
    *,
    interim_logids: Iterable[str] = DEFAULT_INTERIM_LOGIDS,
    config_patterns: Iterable[str | re.Pattern[str]] = DEFAULT_CONFIG_PATTERNS,
    server_vlan_regex: str = DEFAULT_SERVER_VLAN_REGEX,
    host_names: Iterable[str] = (),
    max_clock_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW,
) -> Mapping[SourceType, Parser]:
    """One configured parser per source type (raises ``re.error`` for a bad pattern).

    ``interim_logids`` → FortiGate; ``config_patterns``, ``server_vlan_regex`` and
    ``host_names`` (MikroTik device identities) → MikroTik; ``max_clock_skew`` → all three.
    """
    return {
        "fortigate": FortiGateParser(interim_logids=interim_logids, max_clock_skew=max_clock_skew),
        "mikrotik": MikroTikParser(
            config_patterns=config_patterns,
            server_vlan_regex=server_vlan_regex,
            host_names=host_names,
            max_clock_skew=max_clock_skew,
        ),
        "generic": GenericParser(max_clock_skew=max_clock_skew),
    }


#: Parsers with the shipped defaults.
PARSERS: Final[Mapping[SourceType, Parser]] = build_parsers()
_FORTIGATE_MARKERS: Final = (b"devname=", b"logid=")


def detect_type(record: RawRecord, configured: SourceType | None) -> SourceType:
    """Configured type first; content only for unknown senders."""
    if configured is not None:
        return configured
    if any(marker in record.message for marker in _FORTIGATE_MARKERS):
        return "fortigate"
    return "generic"


def get_parser(source_type: SourceType, parsers: Mapping[SourceType, Parser] = PARSERS) -> Parser:
    return parsers[source_type]


def parse_line(
    record: RawRecord, position: LinePosition, parsers: Mapping[SourceType, Parser] = PARSERS
) -> ParseOutcome:
    """Detect the type and parse one raw line (never raises for bad input).

    ``parsers``: the set from :func:`build_parsers` (default: :data:`PARSERS`).
    """
    return parsers[detect_type(record, position.source_type)].parse(record, position)


__all__ = [
    "PARSERS",
    "SOURCE_TYPES",
    "LinePosition",
    "ParseOutcome",
    "Parsed",
    "ParsedRow",
    "Parser",
    "SourceType",
    "build_parsers",
    "decode_message",
    "detect_type",
    "get_parser",
    "parse_line",
]
