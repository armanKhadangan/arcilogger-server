"""Do the cited raw lines say what the report says? (spec 7: the raw log is the evidence)

A lookup's result comes from database rows (``fg_traffic``, ``mt_dhcp``, ``mt_arp``, the
``leases`` timeline); the database is **derived** data that ingest parsed from the raw spool
lines. The official report shows each cited raw line next to the result, so the two must agree.
This module re-reads every cited line with the **same parser ingest uses**
(:mod:`arcilogger_ingest.parsers`, shipped defaults like the ingest service) and compares it with
what the report presents:

* a FortiGate session of a result (``fg_traffic`` lines of :class:`NatMatch.traffic`): source
  and destination address and port, protocol, session number, NAT address and port;
* an IP-MAC record of a chain (its ``mt_dhcp`` / ``mt_arp`` / ``fg_traffic`` lines): the IP
  address, and the MAC address (``mt_arp``, and every ``mt_dhcp`` line except the one that ended
  a lease because the IP went to another device);
* every cited line: the record type (the line must parse into a row of the cited table) and the
  record time.

The check finds a database that changed after ingest (a manual repair, a defective rebuild, a
row edited by hand or by an intruder, a parser version that reads the line differently now). It
cannot find a defect of the parser itself — re-parsing repeats it — and the device → person link
stays an administrative assignment that no log line supports.

Results are plain JSON (stored with the report's inputs, so the PDF stays a pure function of
them): per cited line ``{"ok", "mismatches": [{"field", "report", "raw"}], "error"}``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from arcilogger_core.rawlog import RawLineError
from arcilogger_core.rawlog import parse_line as parse_raw
from arcilogger_ingest.parsers import PARSERS, LinePosition, SourceType
from arcilogger_worker.lookup import AttributionChain, EvidenceRef, LeaseInfo, LookupOutcome

#: Parser of each table a lookup cites (the table decides, not today's source configuration:
#: only the FortiGate parser writes ``fg_*`` rows and only the MikroTik parser ``mt_*`` rows).
PARSER_OF_TABLE: Final[Mapping[str, SourceType]] = {
    "fg_traffic": "fortigate",
    "fg_web": "fortigate",
    "fg_app": "fortigate",
    "fg_dns": "fortigate",
    "mt_dhcp": "mikrotik",
    "mt_arp": "mikrotik",
}
#: Session fields of a result compared with every cited ``fg_traffic`` line of that session.
SESSION_FIELDS: Final = (
    "srcip",
    "srcport",
    "dstip",
    "dstport",
    "proto",
    "sessionid",
    "transip",
    "transport",
)
_IP_FIELDS: Final = frozenset({"srcip", "dstip", "transip", "ip"})


@dataclass(frozen=True)
class Claim:
    """What the report states about one cited line: ``{column: value}`` of the cited row."""

    values: Mapping[str, Any]


def _normal(column: str, value: Any) -> Any:
    """Comparable form: IP addresses as ``ipaddress`` objects, MACs in lower case, times as
    instants; everything else as it is."""
    if value is None:
        return None
    if column in _IP_FIELDS:
        try:
            return ipaddress.ip_address(str(value))
        except ValueError:
            return str(value)
    if column == "mac":
        return str(value).lower()
    return value


def _shown(value: Any) -> Any:
    """JSON form of a value for the stored result (no floats, times as ISO strings)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _lease_claims(lease: LeaseInfo) -> dict[tuple[str, int | None, str], list[Claim]]:
    claims: dict[tuple[str, int | None, str], list[Claim]] = {}
    for ref in lease.evidence:
        if ref.table == "mt_arp":
            values: dict[str, Any] = {"ip": lease.ip, "mac": lease.mac}
        elif ref.table == "mt_dhcp":
            values = {"ip": lease.ip}
            closing = (
                lease.end_reason == "superseded" and lease.end is not None and ref.time >= lease.end
            )
            if not closing:  # the line that gave the IP to another device names that MAC
                values["mac"] = lease.mac
        elif ref.table == "fg_traffic":
            values = {"srcip": lease.ip}
        else:
            continue
        claims.setdefault(ref_key(ref), []).append(Claim(values))
    return claims


def _chain_claims(chain: AttributionChain) -> dict[tuple[str, int | None, str], list[Claim]]:
    claims: dict[tuple[str, int | None, str], list[Claim]] = {}
    if chain.lease is not None:
        _merge(claims, _lease_claims(chain.lease))
    for other in chain.alternatives:
        _merge(claims, _chain_claims(other))
    return claims


def _merge(
    into: dict[tuple[str, int | None, str], list[Claim]],
    more: Mapping[tuple[str, int | None, str], list[Claim]],
) -> None:
    for key, found in more.items():
        into.setdefault(key, []).extend(found)


def ref_key(ref: EvidenceRef) -> tuple[str, int | None, str]:
    """The key :func:`outcome_claims` files the claims about ``ref`` under."""
    raw = ref.raw
    where = f"{raw.source}/{raw.day.isoformat()}/{raw.line}" if raw is not None else "-"
    return (ref.table, ref.row_id, where)


def outcome_claims(outcome: LookupOutcome) -> dict[tuple[str, int | None, str], list[Claim]]:
    """What the report states about each cited line, by evidence reference."""
    claims: dict[tuple[str, int | None, str], list[Claim]] = {}
    for match in outcome.matches:
        traffic = match.traffic
        session = {name: getattr(traffic, name) for name in SESSION_FIELDS}
        for ref in traffic.evidence:
            if ref.table == "fg_traffic":
                claims.setdefault(ref_key(ref), []).append(Claim(session))
        if match.chain is not None:
            _merge(claims, _chain_claims(match.chain))
    if outcome.attribution is not None:
        _merge(claims, _chain_claims(outcome.attribution))
    return claims


def check_line(
    ref: EvidenceRef, data: bytes, offset: int, claims: Iterable[Claim]
) -> dict[str, Any]:
    """Parse cited line ``data`` (as stored, without ``\\n``) and compare it with ``ref`` and
    ``claims``: ``{"ok": bool, "mismatches": [...], "error": str | None}``."""
    result: dict[str, Any] = {"ok": False, "mismatches": [], "error": None}
    kind = PARSER_OF_TABLE.get(ref.table)
    raw = ref.raw
    if kind is None or raw is None:
        result["error"] = "not_checked"
        return result
    try:
        record = parse_raw(data)
        position = LinePosition(
            source=raw.source,
            source_type=kind,
            raw_day=raw.day,
            raw_line=raw.line,
            raw_offset=offset,
        )
        outcome = PARSERS[kind].parse(record, position)
    except (RawLineError, ValueError, OverflowError):
        result["error"] = "not_a_raw_line"
        return result
    rows = [row.values for row in outcome.rows if row.table == ref.table]
    if not rows:
        result["error"] = "other_record_type"
        return result
    values = rows[0]
    mismatches: list[dict[str, Any]] = []
    for claim in (Claim({"time": ref.time}), *claims):
        for column, value in claim.values.items():
            found = values.get(column)
            entry = {"field": column, "report": _shown(value), "raw": _shown(found)}
            if _normal(column, value) != _normal(column, found) and entry not in mismatches:
                mismatches.append(entry)
    result["mismatches"] = mismatches
    result["ok"] = not mismatches
    return result
